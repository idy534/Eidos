"""Build fixed release archives, then remove document payloads from the App.

Run after the existing locked Node/Python/font builders. This script never
resolves packages or installs dependencies on a target machine.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile

from eidos_runtime.models.managed_resources import ResourceIndex, ResourcePack, resource_archive_name
from eidos_runtime.infrastructure.browser_runtime import CONNECT_HELPER_EXECUTABLE
from eidos_runtime.infrastructure.chrome_extension import WINDOWS_CONNECT_HELPER
from eidos_runtime.infrastructure import local_files
from eidos_runtime.model.qoder_protocol import RESOURCE_VERSION
from eidos_runtime.models.runtime_dependencies import (
    RuntimeDependencyExecutable, RuntimeDependencyInventoryFile,
    RuntimeDependencyManifest, RuntimeDependencyNodePackage, RuntimeDependencyPythonPackage, RuntimeDependencyTarget,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / "resources/runtime-dependencies/resource-release.json").read_text())
VERSION = RELEASE["version"]
NODE_VERSION = json.loads((ROOT / "resources/runtime-dependencies/node/node-release.json").read_text())["version"]
PYTHON_GROUPS = {
    "pandas": "data", "numpy": "data", "python-dateutil": "data", "six": "data", "tzdata": "data",
    "reportlab": "pdf", "pdfminer-six": "pdf", "pdfplumber": "pdf", "pypdfium2": "pdf",
}
TITLES = {
    "document-python": "基础文档处理", "node": "文档 Node 解释器", "document-node": "文档 JavaScript 库",
    "office": "Office 转换与重算", "images": "图片转换", "pdf": "PDF 生成与渲染",
    "data": "表格数据分析", "ocr": "OCR 引擎", "ocr-eng": "英文 OCR", "ocr-chi-sim": "中文 OCR",
    "fonts": "文档字体", "browser": "浏览器自动化", "qoder": "Qoder 模型 SDK",
}
REQUIRES = {
    "document-node": ("node",), "office": ("document-node", "fonts"),
    "images": ("document-node",), "pdf": ("document-python",), "data": ("document-python",),
    "ocr": ("document-node",), "ocr-eng": ("ocr",), "ocr-chi-sim": ("ocr",),
    "browser": ("node",),
}
# Playwright MCP drives the user's installed Chrome over CDP; it ships no browser.
BROWSER_NODE_PACKAGES = frozenset({"@playwright", "playwright", "playwright-core"})


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def python_packages(bundle: Path, staging: Path, core: Path) -> tuple[dict[str, list[RuntimeDependencyPythonPackage]], list[RuntimeDependencyPythonPackage]]:
    source = bundle / "dependencies/python"
    declared = {"tzdata": "tzdata"}  # pandas selects this only on Windows.
    for line in (ROOT / "resources/runtime-dependencies/python/top_level.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            package, name = line.split(": ")
            declared[package.split("==")[0]] = name
    distributions = list(importlib.metadata.distributions(path=[str(source)]))
    core_versions = {dist.metadata["Name"].lower().replace("_", "-"): dist.version
                     for dist in importlib.metadata.distributions(path=[str(core)])}
    groups: dict[str, list[RuntimeDependencyPythonPackage]] = {}
    shared = []
    for dist in distributions:
        name = dist.metadata["Name"].lower().replace("_", "-").replace(".", "-")
        descriptor = RuntimeDependencyPythonPackage(name=name, import_name=declared[name], version=dist.version)
        if core_versions.get(name) == dist.version:
            shared.append(descriptor)
            continue
        group = PYTHON_GROUPS.get(name, "document-python")
        groups.setdefault(group, []).append(descriptor)
        record = dist.read_text("RECORD")
        if record is None:
            raise ValueError(f"No wheel RECORD for {name}")
        for row in csv.reader(record.splitlines()):
            relative = Path(row[0])
            if (relative.is_absolute() or ".." in relative.parts or
                    any(part in {"tests", "test", "__pycache__"} for part in relative.parts)):
                continue
            file = source / relative
            if file.is_file() and file.suffix != ".pyc":
                copy_file(file, staging / group / "lib" / relative)
    return groups, shared


def node_group(relative: Path, target: RuntimeDependencyTarget) -> str | None:
    parts = relative.parts
    if parts[0].startswith("."):
        return None
    windows_office = target == "win32-x64" and parts[:2] == ("@deepseek-ai", "libreoffice-kit-win32-x64")
    if windows_office:
        return "office"  # Keep the Windows engine, resources and notices intact.
    if any(p in {"test", "tests", "examples", ".gitkeep", "__pycache__"} for p in parts):
        return None
    if parts[0] == "@deepseek-ai":
        if parts[1].startswith("libreoffice-kit-"):
            return "office" if parts[1] == f"libreoffice-kit-{target}" else None
        if "frame-forwarding" in parts:
            return None
    if parts[0] in {"react", "react-dom", "react-icons", "scheduler"}:
        return None
    if parts[0] in BROWSER_NODE_PACKAGES:
        return "browser"
    if parts[0] == "@img":
        return "images"
    if parts[0] == "tesseract.js-core":
        # Tesseract.js 7.0.0 passes a boolean to Node getCore(), whose OEM
        # comparison selects the full core even for LSTM_ONLY. Keep both
        # variants until the locked upstream adapter is compatible.
        return "ocr"
    if parts[0] == "@tesseract.js-data":
        if "4.0.0_best_int" in parts:
            return None
        return "ocr-eng" if parts[1] == "eng" else "ocr-chi-sim"
    return "document-node"


def node_packages(bundle: Path, staging: Path, target: RuntimeDependencyTarget) -> dict[str, list[RuntimeDependencyNodePackage]]:
    source = bundle / "dependencies/node/node_modules"
    for file in sorted(source.rglob("*")):
        relative = file.relative_to(source)
        if relative.parts[0].startswith("."):
            continue
        metadata = file.lstat()
        if local_files.is_link(metadata):
            raise ValueError("Node source must already be flattened")
        if stat.S_ISREG(metadata.st_mode):
            group = node_group(relative, target)
            if group:
                copy_file(file, staging / group / "node_modules" / relative)
    groups = {}
    for group in staging.iterdir():
        modules = group / "node_modules"
        if not modules.exists():
            continue
        descriptors = []
        roots = [p for p in modules.iterdir() if not p.name.startswith(".")]
        roots = [child for p in roots for child in (list(p.iterdir()) if p.name.startswith("@") else [p])]
        for package in roots:
            metadata = package / "package.json"
            if metadata.is_file():
                value = json.loads(metadata.read_text())
                descriptors.append(RuntimeDependencyNodePackage(name=value["name"], version=value["version"]))
        groups[group.name] = descriptors
    shutil.copytree(bundle / "dependencies/node/bin", staging / "node/bin")
    copy_file(bundle / "dependencies/node/LICENSE", staging / "node/LICENSE")
    if target == "win32-x64":
        copy_file(bundle / "dependencies/browser" / WINDOWS_CONNECT_HELPER, staging / "browser" / WINDOWS_CONNECT_HELPER)
    return groups


def pack_group(group: Path, output: Path, target: RuntimeDependencyTarget,
               python: list[RuntimeDependencyPythonPackage], node: list[RuntimeDependencyNodePackage]) -> ResourcePack:
    pack_id = group.name
    version = RESOURCE_VERSION if pack_id == "qoder" else NODE_VERSION if pack_id == "node" else VERSION
    files = [p for p in sorted(group.rglob("*")) if p.is_file()]
    inventory = tuple(RuntimeDependencyInventoryFile(path=p.relative_to(group).as_posix(), sha256=digest(p)) for p in files)
    binary = None
    if pack_id == "node":
        binary = ("node", NODE_VERSION, "bin/node.exe" if target == "win32-x64" else "bin/node")
    if pack_id == "qoder":
        binary = ("qodercli", "1.1.79", "lib/qoder_agent_sdk/_bundled/qodercli")
    if pack_id == "office":
        program = "dsoffice.exe" if target == "win32-x64" else "dsoffice"
        binary = ("office", "0.2.3", f"node_modules/@deepseek-ai/libreoffice-kit-{target}/bin/{program}")
    if pack_id == "browser" and target == "win32-x64":
        binary = (CONNECT_HELPER_EXECUTABLE, VERSION, WINDOWS_CONNECT_HELPER)
    executables = ()
    if binary is not None:
        name, binary_version, relative = binary
        executables = (RuntimeDependencyExecutable(name=name, version=binary_version, path=relative, sha256=digest(group / relative)),)
    manifest = RuntimeDependencyManifest(
        schema_version=1, bundle_id=f"com.idy.eidos.resources.{pack_id}", bundle_version=version, target=target,
        executables=executables, python_path=("lib",) if python else (), python_packages=tuple(python),
        node_modules="node_modules" if node else None, node_packages=tuple(node), files=inventory,
    )
    filename = resource_archive_name(pack_id, version, target)
    archive_path = output / filename
    with archive_path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for file in files:
                entry = archive.gettarinfo(str(file), file.relative_to(group).as_posix())
                entry.uid = entry.gid = entry.mtime = 0
                entry.uname = entry.gname = ""
                entry.mode = 0o755 if file.stat().st_mode & 0o111 else 0o644
                with file.open("rb") as stream:
                    archive.addfile(entry, stream)
    if pack_id == "node":
        location = f"runtimes/node/{version}/{target}"
    elif python:
        location = f"packages/python/{pack_id}/{version}/cp312-{target}"
    elif pack_id == "office":
        location = f"native/office/{version}/{target}"
    elif pack_id.startswith("ocr-"):
        location = f"models/ocr/{pack_id.removeprefix('ocr-')}/{version}"
    elif pack_id == "fonts":
        location = f"fonts/document/{version}"
    else:
        location = f"packages/node/{pack_id}/{version}"
    if target == "win32-x64" and not (pack_id in {"node", "office"} or python):
        location += f"/{target}"
    pack = ResourcePack(
        id=pack_id, title=TITLES[pack_id], version=version, location=location,
        url=(f"https://github.com/{RELEASE['repository']}/releases/download/{RELEASE['tag']}/{filename}"
             if target == "darwin-arm64" else None),
        archive_sha256=digest(archive_path), archive_bytes=archive_path.stat().st_size,
        expanded_bytes=sum(p.stat().st_size for p in files), requires=REQUIRES.get(pack_id, ()),
        font_directory="fonts" if pack_id == "fonts" else None, manifest=manifest,
        python_abi="cp312" if python else None, isolated=pack_id == "qoder",
    )
    print(f"Built {pack.title}: {pack.archive_bytes / 1024**2:.1f} MiB download, {pack.expanded_bytes / 1024**2:.1f} MiB installed", flush=True)
    return pack


def qoder_packages(bundle: Path, staging: Path) -> list[RuntimeDependencyPythonPackage]:
    source = bundle / "dependencies/qoder"
    if not source.is_dir():
        raise ValueError("build the isolated Qoder payload before resource packs")
    shutil.copytree(source, staging / "qoder/lib", ignore=shutil.ignore_patterns("tests", "test", "__pycache__", "*.pyc"))
    packages = []
    for dist in importlib.metadata.distributions(path=[str(source)]):
        name = dist.metadata["Name"]
        declared = (dist.read_text("top_level.txt") or "").splitlines()
        roots = {str(f).split("/")[0].removesuffix(".py") for f in dist.files or ()}
        candidates = [*declared, *sorted(roots)]
        # RECORD may include console-script bin/ and a native _cffi_backend.
        # Declare an actual import package/module supported by the catalog.
        import_name = next((v for v in candidates if v.isidentifier()
                            and ((source / v / "__init__.py").is_file() or (source / (v + ".py")).is_file())), None)
        if import_name is None:
            raise ValueError(f"No import root for {name}")
        packages.append(RuntimeDependencyPythonPackage(name=name, import_name=import_name, version=dist.version))
    return packages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "build/macos-runtime")
    parser.add_argument("--output", type=Path, default=ROOT / "build/resource-packages")
    parser.add_argument("--target", choices=("darwin-arm64", "win32-x64"), default="darwin-arm64")
    parser.add_argument("--keep-payloads", action="store_true")
    args = parser.parse_args()
    bundle, output = args.bundle.resolve(), args.output.resolve()
    controlled = (ROOT / "build").resolve()
    if not bundle.is_relative_to(controlled) or not output.is_relative_to(controlled):
        raise ValueError("resource build paths must be inside the repository build directory")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="resource-packs-", dir=output) as folder:
        stage = Path(folder)
        core = bundle / ("python/Lib/site-packages" if args.target == "win32-x64" else "app")
        python, shared = python_packages(bundle, stage, core)
        node = node_packages(bundle, stage, args.target)
        if args.target == "darwin-arm64":
            python["qoder"] = qoder_packages(bundle, stage)
        shutil.copytree(bundle / "dependencies/fonts", stage / "fonts/fonts")
        packs = tuple(pack_group(group, output, args.target, python.get(group.name, []), node.get(group.name, []))
                      for group in sorted(stage.iterdir()) if group.is_dir())
    index = ResourceIndex(version=VERSION, target=args.target, packs=packs)
    encoded = index.model_dump_json(by_alias=True).encode()
    (bundle / "resource-index.json").write_bytes(encoded)
    (output / "resource-index.json").write_bytes(encoded)
    (output / "shared-python.json").write_text(json.dumps([p.model_dump(by_alias=True) for p in shared]))
    loader = bundle / "app/eidos_runtime/resources/runtime-loader.mjs"
    copy_file(ROOT / "resources/runtime-dependencies/node/runtime-loader.mjs", loader)
    if not args.keep_payloads:
        shutil.rmtree(bundle / "dependencies")


if __name__ == "__main__":
    main()
