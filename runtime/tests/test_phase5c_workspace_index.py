from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.sandbox.workspace_manifest import (  # noqa: E402
    attach_workspace_diff,
    diff_workspace_manifests,
)
from eidos_runtime.tools.workspace import (  # noqa: E402
    ToolExecutor,
    WorkspacePathError,
)


class WorkspaceIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="eidos-phase5c-index-"
        )
        self.workspace = Path(self.temporary.name)
        self.executor = ToolExecutor(self.workspace)

    def tearDown(self) -> None:
        self.executor.close()
        self.temporary.cleanup()

    def _refresh(self):
        self.executor.refresh_workspace_index(threading.Event())
        return self.executor.workspace_index.snapshot

    def test_workspace_index_incremental_refresh(self) -> None:
        for index in range(100):
            (self.workspace / f"{index}.txt").write_text(
                "content", encoding="utf-8"
            )
        reads = 0
        original_read = os.read

        def counted(*args, **kwargs):
            nonlocal reads
            reads += 1
            return original_read(*args, **kwargs)

        with patch(
            "eidos_runtime.sandbox.workspace_index.os.read",
            side_effect=counted,
        ):
            first = self._refresh()
            first_reads = reads
            second = self._refresh()

        self.assertGreater(first_reads, 0)
        self.assertEqual(reads, first_reads)
        self.assertEqual(second.generation, first.generation)

    def test_git_objects_are_not_recursively_scanned(self) -> None:
        objects = self.workspace / ".git" / "objects" / "aa"
        objects.mkdir(parents=True)
        (objects / "secret.key").write_text("ignored", encoding="utf-8")

        snapshot = self.executor.prepare_shell(".", threading.Event())

        self.assertEqual(snapshot.path, self.workspace.resolve())
        self.assertEqual(
            self.executor.workspace_index.snapshot.entry_count, 1
        )

    def test_sensitive_file_added_after_initial_scan_is_detected(self) -> None:
        self._refresh()
        (self.workspace / "new-token.txt").write_text(
            "sensitive", encoding="utf-8"
        )

        with self.assertRaisesRegex(
            WorkspacePathError, "sensitive_workspace_content"
        ):
            self._refresh()

    def test_index_invalidates_on_workspace_identity_change(self) -> None:
        original = self.workspace.resolve()
        moved = original.with_name(f"{original.name}-moved")
        original.rename(moved)
        original.mkdir()
        try:
            with self.assertRaisesRegex(
                WorkspacePathError, "workspace_identity_changed"
            ):
                self._refresh()
        finally:
            original.rmdir()
            moved.rename(original)

    def test_manifest_generation_is_stable(self) -> None:
        path = self.workspace / "state.txt"
        path.write_text("one", encoding="utf-8")
        before = self._refresh()
        unchanged = self._refresh()
        time.sleep(0.001)
        path.write_text("two", encoding="utf-8")
        changed = self._refresh()

        self.assertEqual(unchanged.generation, before.generation)
        self.assertEqual(changed.generation, before.generation + 1)

    def test_large_workspace_does_not_force_reconciliation(self) -> None:
        for index in range(20_001):
            (self.workspace / f"f-{index}").touch()
        before = self._refresh()
        before_manifest = self.executor.workspace_index.manifest()
        self._refresh()
        after_manifest = self.executor.workspace_index.manifest()
        diff = diff_workspace_manifests(
            before_manifest, after_manifest
        )
        result = attach_workspace_diff(
            {"outcome": "success", "data": {}}, diff
        )

        self.assertTrue(before.complete)
        self.assertTrue(diff.complete)
        self.assertFalse(result["reconciliationRequired"])


if __name__ == "__main__":
    unittest.main()
