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
    "availableNames": ["read_file"],
    "directNames": ["read_file"],
    "deferredNames": [],
    "activatedNames": [],
    "specHashes": {"read_file": "1" * 64},
    "definitionsHash": "2" * 64,
    "toolSetHash": "3" * 64,
}
PROVENANCE = {
    "kind": "builtin",
    "sourceId": "eidos",
    "sourceVersion": "1",
    "contentHash": "4" * 64,
}


class ExtensionStorageTests(unittest.TestCase):
    def test_shared_extension_vectors_match_closed_runtime_dtos(self) -> None:
        fixture = json.loads(
            (Path(__file__).resolve().parents[2] / "protocol" / "fixtures" / "extensions-v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            PluginRecordDto.model_validate(fixture["plugin"]).to_json_value(),
            fixture["plugin"],
        )
        self.assertEqual(
            SkillMetadataDto.model_validate(fixture["skill"]).to_json_value(),
            fixture["skill"],
        )
        self.assertEqual(
            McpServerRecordDto.model_validate(fixture["mcpServer"]).to_json_value(),
            fixture["mcpServer"],
        )
        self.assertEqual(
            RunExtensionSnapshotDto.model_validate(
                fixture["runExtensionSnapshot"]
            ).to_json_value(),
            fixture["runExtensionSnapshot"],
        )
        self.assertEqual(
            StepToolSnapshotDto.model_validate(
                fixture["stepToolSnapshot"]
            ).to_json_value(),
            fixture["stepToolSnapshot"],
        )
        self.assertEqual(
            ToolProvenance.model_validate(fixture["toolProvenance"]).model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            fixture["toolProvenance"],
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-p3-storage-")
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

        self.assertEqual(SCHEMA_REVISION, 9)
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
        item_id = item["id"]
        run_id = run["id"]
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()

        self.assertEqual(
            self.store.read_run(run_id)["extensionSnapshot"], EXTENSION_SNAPSHOT
        )
        self.assertEqual(
            self.store.read_step_tool_snapshot(run_id, step_index), TOOL_SNAPSHOT
        )
        restored = self.store.read_item(item_id)["toolCall"]
        self.assertEqual(restored["provenance"], PROVENANCE)
        self.assertEqual(restored["toolSetHash"], TOOL_SNAPSHOT["toolSetHash"])


if __name__ == "__main__":
    unittest.main()
