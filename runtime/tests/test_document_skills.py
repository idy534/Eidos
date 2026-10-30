import subprocess
import sys
from unittest.mock import patch
import json

import pytest
from semantic_version import NpmSpec, Version

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skill_management import SkillManagement
from eidos_runtime.extensions.skills import (
    BUNDLED_SYSTEM_SKILLS, SkillCatalog, SkillReadError, deploy_system_skills,
)
from eidos_runtime.extensions.skill_manifest import load_skill_agent_metadata
from eidos_runtime.models.skill_runtime import NodePackageRequirement

pytestmark = pytest.mark.integration
NAMES = ("docx", "pptx", "xlsx", "pdf")


@pytest.fixture
def management(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    deploy_system_skills(store.data_directory)
    manager = SkillManagement(SkillCatalog(PluginCatalog(store)))
    try:
        yield manager
    finally:
        store.close()


def install_personal(manager, name="docx"):
    root = manager.store.data_directory / "skills" / name
    root.mkdir(mode=0o700)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Personal document workflow.\n---\nMy custom instructions.\n"
    )
    return root


def test_fresh_install_discovers_enabled_builtins_with_valid_requirements(management):
    listed = {skill.qualified_id: skill for skill in management.list()}
    for name in NAMES:
        skill = listed[f"system:{name}"]
        assert skill.enabled and skill.source_kind == "system"
        metadata = load_skill_agent_metadata(BUNDLED_SYSTEM_SKILLS / name)
        assert metadata.runtime_dependency_error is None
        assert metadata.runtime_dependencies is not None
        versions = json.loads((BUNDLED_SYSTEM_SKILLS.parents[4] / "resources/runtime-dependencies/node/package.json").read_text())["dependencies"]
        for requirement in metadata.runtime_dependencies.dependencies:
            if isinstance(requirement, NodePackageRequirement):
                assert NpmSpec(requirement.version).match(Version(versions[requirement.name]))
        with pytest.raises(SkillReadError, match="system_skill_protected"):
            management.remove(skill.qualified_id)


def test_upgrade_preserves_personal_files_and_frozen_catalog(management):
    root = install_personal(management)
    original = (root / "SKILL.md").read_bytes()
    snapshot = management.catalog.extension_snapshot()
    management.migrate_builtin_documents()
    states = {state.qualified_id: state for state in management.store.skill_states()}
    assert states["system:docx"].enabled
    assert not states["user:docx"].enabled
    assert (root / "SKILL.md").read_bytes() == original
    assert "My custom instructions." in management.catalog.read_skill(snapshot, "user:docx")["content"]
    ids = management.catalog.extension_snapshot()["skillCatalogIds"]
    assert "system:docx" in ids and "user:docx" not in ids
    # A user's later explicit choice survives subsequent startup migration.
    management.set_enabled("user:docx", True)
    management.set_enabled("system:docx", False)
    management.migrate_builtin_documents()
    listed = {skill.qualified_id: skill for skill in management.list()}
    assert listed["user:docx"].enabled and not listed["system:docx"].enabled


def test_upgrade_preserves_disabled_preference(management):
    install_personal(management, "xlsx")
    management.set_enabled("user:xlsx", False)
    management.migrate_builtin_documents()
    listed = {skill.qualified_id: skill for skill in management.list()}
    assert not listed["system:xlsx"].enabled


def test_upgrade_failure_rolls_back_both_states_and_can_retry(management):
    install_personal(management)
    from eidos_runtime.db.repositories import extensions
    real_append = extensions.append_event
    calls = 0

    def append(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected transaction failure")
        return real_append(*args, **kwargs)

    with patch.object(extensions, "append_event", side_effect=append):
        with pytest.raises(RuntimeError, match="injected"):
            management.migrate_builtin_documents()
    assert not management.store.skill_states()
    management.migrate_builtin_documents()
    assert len(management.store.skill_states()) == 2


def test_script_entry_fails_when_no_verified_environment(tmp_path):
    result = subprocess.run(
        [sys.executable, str(BUNDLED_SYSTEM_SKILLS / "pdf/scripts/run.py"), "python", "missing.py"],
        cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "RUNTIME_PYTHON is unavailable" in result.stderr


def test_document_tree_deployment_is_idempotent(management):
    root = management.store.data_directory / "skills/.system/docx"
    before = root.stat().st_ino
    deploy_system_skills(management.store.data_directory)
    assert root.stat().st_ino == before
