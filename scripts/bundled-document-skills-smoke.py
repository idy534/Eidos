"""Exercise bundled document Skills through real, offline Seatbelt Shells.

Run with the bundled Python and PYTHONPATH=<bundle>/app. No host document
libraries, Python, Node, Office installation, or Homebrew PATH are used.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import sys
import tempfile
import threading
from unittest.mock import patch
import zipfile
from urllib.parse import urlsplit
import httpx2 as httpx

from eidos_runtime.db.storage import SessionStore, WorkspaceIdentity
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog, deploy_system_skills
from eidos_runtime.extensions.skill_access import SkillAccess
from eidos_runtime.infrastructure.runtime_dependencies import RuntimeDependencyCatalog
from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, load_resource_index
from eidos_runtime.infrastructure.managed_resource_catalog import ManagedResourceCatalog
from eidos_runtime.application.managed_resources import installation_operation
from eidos_runtime.runtime.runtime_dependencies import RuntimeDependencyCoordinator
from eidos_runtime.sandbox.host_shell import HostShell, ShellEnvironmentSnapshot
from eidos_runtime.sandbox.permissions import (
    SandboxAttempt, SandboxType, base_permission_profile_for_workspace,
    materialize_effective_profile,
)
from eidos_runtime.sandbox.shell import run_shell


def main() -> None:
    bundle = Path(sys.executable).resolve().parents[2]
    base_catalog = RuntimeDependencyCatalog.from_manifest(bundle / "runtime.json")
    with tempfile.TemporaryDirectory(prefix="eidos-document-smoke-") as directory:
        root = Path(directory).resolve()
        workspace, home, temporary = (root / name for name in ("workspace with spaces", "home with spaces", "tmp"))
        for path in (workspace, home, temporary):
            path.mkdir()
        store = SessionStore(root / "data")
        store.initialize()
        try:
            if (bundle / "resource-index.json").is_file():
                class FileStream(httpx.SyncByteStream):
                    def __init__(self, path):
                        self.path = path
                    def __iter__(self):
                        with self.path.open("rb") as stream:
                            while chunk := stream.read(1024 * 1024):
                                yield chunk

                archives = Path(__file__).resolve().parents[1] / "build/resource-packages"
                requests = []
                def transport(request):
                    requests.append(str(request.url))
                    file = archives / Path(urlsplit(str(request.url)).path).name
                    assert file.is_file(), file
                    return httpx.Response(200, stream=FileStream(file))
                index = load_resource_index(bundle / "resource-index.json")
                for pack in index.packs:
                    assert all("libreoffice-kit-wasm" not in file.path and "/react-icons/" not in file.path
                               and "/tests/" not in file.path for file in pack.manifest.files)
                manager = ManagedResourceManager(
                    index, root=home / "Eidos/Resource",
                    client_factory=lambda: httpx.Client(transport=httpx.MockTransport(transport)),
                )
                manager.installation_operation = installation_operation(store, manager)
                dependency_catalog = ManagedResourceCatalog(base_catalog, manager)
                assert not manager.is_ready("document-python")
            else:
                dependency_catalog = base_catalog
            deploy_system_skills(store.data_directory)
            catalog = SkillCatalog(PluginCatalog(store))
            snapshot = catalog.catalog_snapshot(catalog.extension_snapshot())
            access = SkillAccess.from_snapshot(snapshot)
            session = store.create_session(str(workspace))
            run, _ = store.create_run(session["id"], "bundled document smoke")
            coordinator = RuntimeDependencyCoordinator(
                run["id"], dependency_catalog, store=store, skills=catalog,
                skill_snapshot=snapshot, forbidden_roots=(store.data_directory,),
            )
            metadata = workspace.stat()
            identity = WorkspaceIdentity(path=workspace, device=metadata.st_dev, inode=metadata.st_ino, owner=metadata.st_uid)
            shell = HostShell(Path("/bin/sh"), "sh")
            shell_snapshot = ShellEnvironmentSnapshot(
                shell=shell, environment={
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home),
                    "TMPDIR": str(temporary), "LANG": "en_US.UTF-8", "SHELL": "/bin/sh",
                }, captured_at=datetime.now(timezone.utc), source="captured", diagnostic="captured",
            )

            def invoke(name, arguments, *, resource=None, timeout=90, success=True):
                skill_root = store.data_directory / "skills/.system" / name
                if resource is not None:
                    arguments = ["--resource", resource, *arguments]
                command = '"$RUNTIME_PYTHON" ' + shlex.join([str(skill_root / "scripts/run.py"), *arguments])
                invocation = access.activate_implicit(command, workspace)
                assert invocation and invocation.qualified_id == f"system:{name}"
                profile = coordinator.prepare_skill_resources(
                    invocation.qualified_id, command, workspace, invocation.script_path, threading.Event(),
                )
                binding = coordinator.binding_for_skill(invocation.qualified_id, profile=profile)
                assert binding and binding.ready, tuple((d.name, d.reason) for d in binding.diagnostics if d.status != "ready")
                coordinator.verify_binding(binding, qualified_skill_id=invocation.qualified_id)
                base = base_permission_profile_for_workspace(workspace, store.data_directory)
                base = coordinator.permission_profile(base, binding).model_copy(update={"active_skill_roots": (str(skill_root),)})
                effective = materialize_effective_profile(base)
                assert not effective.network_enabled and not effective.full_access
                attempt = SandboxAttempt(
                    ordinal=0, sandbox=SandboxType.MACOS_SEATBELT, sandboxRequested=True,
                    permissions=effective, sandboxCwd=str(workspace), workspaceRoots=(str(workspace),),
                )
                with (
                    patch("eidos_runtime.sandbox.shell.HOST_SHELL_RESOLVER.resolve", return_value=shell),
                    patch("eidos_runtime.sandbox.shell.SHELL_ENVIRONMENT_PROVIDER.get", return_value=shell_snapshot),
                ):
                    result = run_shell(
                        identity, command, identity, timeout, threading.Event(), lambda _: None,
                        attempt=attempt, active_skill_roots=(skill_root,), skill_invocation=invocation,
                        dependency_environment=coordinator.shell_environment(binding, resource_profile=profile),
                    )
                if success:
                    assert result["outcome"] == "success", result
                return result

            def helper(name, filename, *args, **kwargs):
                script = store.data_directory / "skills/.system" / name / "scripts" / filename
                profile = {"office/convert.py": "office", "recalc.py": "office", "thumbnail.py": "thumbnail", "render.py": "render", "ocr.cjs": "ocr-eng"}.get(filename)
                kwargs.setdefault("resource", profile)
                return invoke(name, ["node" if script.suffix == ".cjs" else "python", str(script), *args], **kwargs)

            (workspace / "node_modules/docx").mkdir(parents=True)
            (workspace / "node_modules/docx/package.json").write_text('{"name":"docx","version":"0.0.0","main":"index.cjs"}')
            (workspace / "node_modules/docx/index.cjs").write_text("throw Error('workspace packages must not override managed document libraries');")
            (workspace / "docx.py").write_text("raise RuntimeError('workspace module must not shadow the managed library')")
            (workspace / "word.cjs").write_text("""const { Document, Packer, Paragraph } = require('docx');
const fs = require('node:fs');
Packer.toBuffer(new Document({sections:[{children:[new Paragraph('Eidos 文档 smoke')]}]})).then(b=>fs.writeFileSync('word.docx',b));
""")
            invoke("docx", ["node", "word.cjs"])
            (workspace / "relative.mjs").write_text("export const label = 'managed ESM';\n")
            (workspace / "esm.mjs").write_text("import {Paragraph} from 'docx'; import {label} from './relative.mjs'; if(!new Paragraph(label)) throw Error('ESM failed');\n")
            invoke("docx", ["node", "esm.mjs"])
            assert "Eidos" in helper("docx", "read.py", "word.docx")["data"]["stdout"]
            helper("docx", "office/convert.py", "word.docx", "word-render/word.pdf")
            helper("docx", "render.py", "word-render/word.pdf", "word-pages")
            assert list((workspace / "word-pages").glob("*.png"))
            print("Passed bundled Word creation, read and PDF rendering", flush=True)

            (workspace / "deck.cjs").write_text("""const pptxgen = require('pptxgenjs');
const sharp = require('sharp');
const p = new pptxgen(); p.layout='LAYOUT_WIDE';
const slide = p.addSlide(); slide.addText('Eidos 幻灯片 smoke', {x:1,y:1,w:10,h:1,fontFace:'Noto Sans SC'});
sharp(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><path d="M20 128L100 210L230 30" fill="none" stroke="green" stroke-width="20"/></svg>')).png().toBuffer().then(b=>{
slide.addImage({data:'image/png;base64,'+b.toString('base64'),x:1,y:3,w:1,h:1}); return p.writeFile({fileName:'deck.pptx'});});
""")
            invoke("pptx", ["node", "deck.cjs"], resource="images")
            assert "Eidos" in helper("pptx", "read.py", "deck.pptx")["data"]["stdout"]
            helper("pptx", "thumbnail.py", "deck.pptx", "deck-thumbs")
            assert (workspace / "deck-thumbs.jpg").is_file()
            print("Passed bundled PowerPoint creation, image packages and thumbnail rendering", flush=True)

            (workspace / "sheet.py").write_text("""from openpyxl import Workbook
w=Workbook(); s=w.active; s['A1']=3; s['A2']=4; s['A3']='=SUM(A1:A2)'; w.save('sheet.xlsx')
s['A3']='=1/0'; w.save('bad.xlsx')
""")
            invoke("xlsx", ["python", "sheet.py"])
            assert json.loads(helper("xlsx", "recalc.py", "sheet.xlsx")["data"]["stdout"])["status"] == "success"
            (workspace / "verify.py").write_text("from openpyxl import load_workbook\nassert load_workbook('sheet.xlsx',data_only=True).active['A3'].value == 7\n")
            invoke("xlsx", ["python", "verify.py"])
            assert json.loads(helper("xlsx", "recalc.py", "bad.xlsx")["data"]["stdout"])["status"] == "errors_found"
            print("Passed bundled Excel formula values and error reporting", flush=True)
            (workspace / "data.py").write_text("import pandas as pd\nimport numpy as np\nassert pd.DataFrame({'a':[3,4]}).a.sum()==7\nassert np.array([3,4]).sum()==7\n")
            invoke("xlsx", ["python", "data.py"], resource="data")
            print("Passed managed pandas/NumPy analysis", flush=True)

            (workspace / "pdf.py").write_text("""import os
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter
pdfmetrics.registerFont(TTFont('EidosCJK',str(Path(os.environ['EIDOS_DOCUMENT_FONTS'])/'NotoSansSC.ttf')))
c=canvas.Canvas('source.pdf'); c.setFont('Helvetica',44); c.drawString(30,700,'HELLO EIDOS')
c.setFont('EidosCJK',24); c.drawString(30,600,'中文文档验证')
c.acroForm.textfield(name='customer',x=30,y=500,width=200,height=24); c.save()
r=PdfReader('source.pdf'); assert '中文文档验证' in r.pages[0].extract_text()
w=PdfWriter(); w.append(r); w.append(r); w.write('merged.pdf'); assert len(PdfReader('merged.pdf').pages)==2
""")
            invoke("pdf", ["python", "pdf.py"], resource="pdf")
            fields = workspace / "fields.json"
            fields.write_text(json.dumps([{"field_id": "customer", "page": 1, "value": "Eidos"}]))
            helper("pdf", "fill_fillable_fields.py", "source.pdf", "fields.json", "filled.pdf")
            (workspace / "verify-form.py").write_text("from pypdf import PdfReader\nassert PdfReader('filled.pdf').get_fields()['customer']['/V'] == 'Eidos'\n")
            invoke("pdf", ["python", "verify-form.py"])
            helper("pdf", "render.py", "source.pdf", "pdf-pages", "--dpi", "100")
            ocr = helper("pdf", "ocr.cjs", "pdf-pages/page-1.png", "eng", "searchable.pdf")
            assert "HELLO EIDOS" in ocr["data"]["stdout"], ocr
            bilingual = helper("pdf", "ocr.cjs", "pdf-pages/page-1.png", "eng+chi_sim", "bilingual.pdf", resource="ocr-bilingual")
            assert (workspace / "bilingual.pdf").read_bytes().startswith(b"%PDF"), bilingual
            with zipfile.ZipFile(workspace / "word.docx") as archive:
                assert "word/document.xml" in archive.namelist()
            assert (workspace / "searchable.pdf").read_bytes().startswith(b"%PDF")
            # Real network and protected writes remain denied in the bound environment.
            (workspace / "boundary.py").write_text("""import socket
from pathlib import Path
try:
    socket.create_connection(('1.1.1.1',443),timeout=1)
except OSError: pass
else: raise AssertionError('network unexpectedly available')
try:
    Path(__import__('os').environ['RUNTIME_NODE']).write_bytes(b'bad')
except OSError: pass
else: raise AssertionError('bundle unexpectedly writable')
""")
            invoke("pdf", ["python", "boundary.py"], resource="ocr-eng")
            (workspace / "sleep.py").write_text("import time\ntime.sleep(120)\n")
            timed = invoke("pdf", ["python", "sleep.py"], timeout=1, success=False)
            assert timed["outcome"] == "error", timed
            print("Passed bundled PDF, Chinese fonts, offline OCR, protected paths and timeout", flush=True)
            if isinstance(dependency_catalog, ManagedResourceCatalog):
                before = len(requests)
                manager.ensure(("office", "ocr-eng", "pdf"), threading.Event())
                assert len(requests) == before, "ready resources unexpectedly downloaded again"
                print("Passed clean managed resource installation, CJS/ESM, shared Python and reuse", flush=True)
        finally:
            store.close()


if __name__ == "__main__":
    main()
