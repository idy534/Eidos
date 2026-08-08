from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SCHEMA_REVISION, SessionStore  # noqa: E402
from eidos_runtime.protocol.schemas import (  # noqa: E402
    McpServerRecordDto,
    PluginRecordDto,
    RunExtensionSnapshotDto,
    SkillMetadataDto,
    StepResolutionReviewDto,
    StepToolSnapshotDto,
)
from eidos_runtime.tools.registry import ToolProvenance  # noqa: E402


EXTENSION_SNAPSHOT = {
    "schemaVersion": 1,
    "extensionContractVersion": 1,
    "plugins": [],
    "skillCatalogHash": "0" * 64,
    "mcpConfigHash": "0" * 64,
}
TOOL_SNAPSHOT = {
    "schemaVersion": 1,
    "toolSetHash": "1" * 64,
    "tools": [],
}
PROVENANCE = {
    "kind": "builtin",
    "sourceId": "eidos",
    "sourceVersion": "1",
    "contentHash": "2" * 64,
}


class ExtensionStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-extension-storage-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_revision_contains_extension_snapshot_and_provenance_columns(self) -> None:
        connection = self.store.connection
        assert connection is not None

        self.assertEqual(SCHEMA_REVISION, 13)
        self.assertIn(
            "extension_snapshot_json",
            {row[1] for row in connection.execute("PRAGMA table_info(runs)")},
        )
        self.assertIn(
            "tool_snapshot_json",
            {row[1] for row in connection.execute("PRAGMA table_info(steps)")},
        )
        self.assertIn(
            "provenance_json",
            {row[1] for row in connection.execute("PRAGMA table_info(tool_calls)")},
        )

    def test_run_step_and_tool_provenance_survive_restart(self) -> None:
        run, _ = self.store.create_run(
            self.session["id"], "inspect", extension_snapshot=EXTENSION_SNAPSHOT
        )
        step_index = self.store.increment_model_step(
            run["id"], tool_snapshot=TOOL_SNAPSHOT
        )
        item = self.store.create_tool_item(
            run["id"], step_index, 0, "call-1", "read_file",
            json.dumps({"path": "README.md"}),
            provenance=PROVENANCE,
            tool_set_hash=TOOL_SNAPSHOT["toolSetHash"],
        )
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()

        stored_run = self.store.read_run(run["id"])
        self.assertEqual(stored_run["extensionSnapshot"], EXTENSION_SNAPSHOT)
        stored_step = self.store.read_step_tool_snapshot(run["id"], step_index)
        self.assertEqual(stored_step, TOOL_SNAPSHOT)
        stored_item = self.store.read_item(item["id"])
        self.assertEqual(stored_item["toolCall"]["provenance"], PROVENANCE)
        self.assertEqual(
            stored_item["toolCall"]["toolSetHash"], TOOL_SNAPSHOT["toolSetHash"]
        )

    def test_extension_and_resolution_wire_models_are_strict(self) -> None:
        models = (
            (PluginRecordDto, {
                "schemaVersion": 1,
                "id": "plugin-a",
                "name": "Plugin A",
                "enabled": True,
                "contentHash": "0" * 64,
                "manifest": {},
            }),
            (McpServerRecordDto, {
                "schemaVersion": 1,
                "pluginId": "plugin-a",
                "serverId": "server-a",
                "enabled": True,
                "config": {},
            }),
            (SkillMetadataDto, {
                "schemaVersion": 1,
                "qualifiedId": "builtin:test",
                "name": "test",
                "description": "test skill",
                "scope": "builtin",
                "kind": "builtin",
                "contentHash": "0" * 64,
                "rootPath": "/tmp/test",
            }),
            (RunExtensionSnapshotDto, EXTENSION_SNAPSHOT),
            (StepToolSnapshotDto, TOOL_SNAPSHOT),
            (StepResolutionReviewDto, {
                "schemaVersion": 1,
                "stepIndex": 1,
                "systemPromptHash": "0" * 64,
                "resolvedInstructionsHash": "1" * 64,
                "workspaceRules": [],
                "skillInstructions": [],
                "activatedToolNames": [],
                "effectiveCwd": ".",
                "toolSetHash": "2" * 64,
                "ruleResolutionSnapshotId": "snapshot",
            }),
        )
        for model, payload in models:
            with self.subTest(model=model.__name__):
                model.model_validate(payload)
                with self.assertRaises(ValueError):
                    model.model_validate({**payload, "unknown": True})

        ToolProvenance.model_validate(PROVENANCE)


if __name__ == "__main__":
    unittest.main()
