from __future__ import annotations

import hashlib
import io
from pathlib import Path
import stat
import sys
import tempfile
import threading
from unittest.mock import patch
import zipfile

import pytest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skill_invocation import (  # noqa: E402
    parse_skill_script_invocation,
)
from eidos_runtime.extensions.skill_manifest import (  # noqa: E402
    load_skill_agent_metadata,
    parse_skill_manifest,
)
from eidos_runtime.extensions.skills import (  # noqa: E402
    SkillCatalog,
    SkillCreation,
    SkillReadError,
    _commit_skill_tree,
    _download_github_skill,
    _frontmatter,
)
from eidos_runtime.tools.view_image import (  # noqa: E402
    ViewImageRootAuthority,
    read_authorized_image,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "anthropic-skills"
SKILL_NAMES = ("docx", "pptx", "pdf", "xlsx")


class _ArchiveResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.stream: io.BytesIO | None = None

    def __enter__(self) -> _ArchiveResponse:
        self.stream = io.BytesIO(self.payload)
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def geturl(self) -> str:
        return "https://codeload.github.com/anthropics/skills/zip/main"

    def read(self, size: int) -> bytes:
        assert self.stream is not None
        return self.stream.read(size)


def _fixture_path(skill_name: str) -> Path:
    return FIXTURE_ROOT / skill_name


def _fixture_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _fixture_archive(root: Path, skill_name: str) -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        archive_root = Path("anthropic-skills-main") / "skills" / skill_name
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            entry = zipfile.ZipInfo((archive_root / relative).as_posix())
            entry.create_system = 3
            mode = stat.S_IMODE(path.stat().st_mode)
            entry.external_attr = (stat.S_IFREG | mode) << 16
            bundle.writestr(entry, path.read_bytes())
    return archive.getvalue()


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_anthropic_skill_frontmatter_uses_the_shared_parser(skill_name: str) -> None:
    root = _fixture_path(skill_name)
    document = (root / "SKILL.md").read_text(encoding="utf-8")

    manifest = parse_skill_manifest(document, root.name)
    frontmatter = _frontmatter(document, default_name=root.name)
    metadata = load_skill_agent_metadata(root)

    assert manifest.name == skill_name
    assert manifest.description
    assert manifest.short_description == "Use existing local tools."
    assert frontmatter == (manifest.name, manifest.description)
    for field in ("license:", "compatibility:", "metadata:", "allowed-tools:"):
        assert field in document
    assert metadata.interface is not None
    assert metadata.dependencies is not None
    assert metadata.dependencies.tools[0].type == "mcp"
    assert metadata.policy is not None
    assert metadata.policy.allow_implicit_invocation is True


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_anthropic_skill_fixture_installs_complete_tree_and_preserves_assets(
    skill_name: str,
) -> None:
    fixture = _fixture_path(skill_name)
    expected_files = _fixture_files(fixture)
    archive = _fixture_archive(fixture, skill_name)

    with patch(
        "urllib.request.OpenerDirector.open",
        return_value=_ArchiveResponse(archive),
    ):
        name, files = _download_github_skill(
            f"https://github.com/anthropics/skills/tree/main/skills/{skill_name}",
            threading.Event(),
        )

    assert name == skill_name
    assert set(files) == expected_files
    assert "scripts/render.py" in files.executable_paths

    with tempfile.TemporaryDirectory(prefix=f"eidos-{skill_name}-compatibility-") as directory:
        root = Path(directory).resolve()
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir(mode=0o700)
        store = SessionStore(data)
        store.initialize()
        try:
            skills = SkillCatalog(PluginCatalog(store))
            creation = SkillCreation(
                name=name,
                path=f"~/.eidos/skills/{name}",
                files=files,
                content_hash=hashlib.sha256(b"fixture").hexdigest(),
                diff="fixture",
                executable_paths=files.executable_paths,
            )
            result = _commit_skill_tree(
                skills, creation, threading.Event(), "skill_install"
            )
            assert result["code"] == "ok"

            installed = data / "skills" / skill_name
            assert {
                path.relative_to(installed).as_posix()
                for path in installed.rglob("*")
                if path.is_file()
            } == expected_files
            assert (installed / "LICENSE").is_file()
            assert (installed / "scripts" / "render.py").stat().st_mode & stat.S_IXUSR
            assert (installed / "references" / "format-guide.md").is_file()
            assert (installed / "assets" / "preview.png").read_bytes() == (
                fixture / "assets" / "preview.png"
            ).read_bytes()
            assert (installed / "assets" / f"template.{skill_name}").read_bytes() == (
                fixture / "assets" / f"template.{skill_name}"
            ).read_bytes()
            assert (installed / "SKILL.md").stat().st_mode & 0o777 == 0o600
            assert (installed / "scripts" / "render.py").stat().st_mode & 0o777 == 0o700

            snapshot = skills.catalog_snapshot(skills.extension_snapshot())
            qualified_id = f"user:{skill_name}"
            skill = skills.read_skill(snapshot, qualified_id)
            resource = skills.read_resource(
                snapshot, qualified_id, "references/format-guide.md"
            )
            metadata = skills.metadata(snapshot, qualified_id)
            assert skill["content"] == (installed / "SKILL.md").read_text(
                encoding="utf-8"
            )
            assert resource["content"] == (
                installed / "references" / "format-guide.md"
            ).read_text(encoding="utf-8")
            assert metadata.dependencies is not None
            assert metadata.dependencies.tools[0].type == "mcp"
            with pytest.raises(SkillReadError, match="skill_resource_not_text"):
                skills.read_resource(
                    snapshot,
                    qualified_id,
                    f"assets/template.{skill_name}",
                )

            invocation = parse_skill_script_invocation(
                "python3 scripts/render.py", installed
            )
            assert invocation is not None
            assert invocation.script_path == (
                installed / "scripts" / "render.py"
            ).resolve()

            image = read_authorized_image(
                "assets/preview.png",
                ViewImageRootAuthority(
                    workspace_root=workspace,
                    active_skill_roots=(installed,),
                ),
            )
            assert image.mime == "image/png"
            assert image.data == (installed / "assets" / "preview.png").read_bytes()
        finally:
            store.close()
