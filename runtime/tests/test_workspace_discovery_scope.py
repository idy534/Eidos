from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.tools.workspace import ToolExecutor, WorkspacePathError  # noqa: E402
from eidos_runtime.workspace.discovery_scope import (  # noqa: E402
    DiscoveryScopeError,
    MAX_IGNORE_FILE_BYTES,
    WorkspaceDiscoveryScope,
)


class WorkspaceDiscoveryScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-discovery-")
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _load(self) -> WorkspaceDiscoveryScope:
        descriptor = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY)
        try:
            return WorkspaceDiscoveryScope.load(descriptor)
        finally:
            os.close(descriptor)

    def test_root_gitignore_supports_comments_blanks_wildcards_directories_and_anchors(self) -> None:
        (self.workspace / ".gitignore").write_text(
            "# comment\n\n*.log\ncache/\n/root-only.txt\n",
            encoding="utf-8",
        )
        scope = self._load()

        self.assertTrue(scope.is_ignored("trace.log", is_directory=False))
        self.assertTrue(scope.is_ignored("nested/trace.log", is_directory=False))
        self.assertTrue(scope.is_ignored("cache/item.txt", is_directory=False))
        self.assertTrue(scope.is_ignored("root-only.txt", is_directory=False))
        self.assertFalse(scope.is_ignored("nested/root-only.txt", is_directory=False))
        self.assertFalse(scope.is_ignored("keep.txt", is_directory=False))

    def test_eidosignore_is_later_and_can_reinclude_ordinary_path(self) -> None:
        (self.workspace / ".gitignore").write_text("fixtures/\n", encoding="utf-8")
        (self.workspace / ".eidosignore").write_text(
            "!fixtures/agent-test.json\n", encoding="utf-8"
        )
        scope = self._load()

        self.assertTrue(scope.is_ignored("fixtures/other.json", is_directory=False))
        self.assertFalse(scope.is_ignored("fixtures/agent-test.json", is_directory=False))

    def test_eidosignore_adds_an_exclusion_after_gitignore(self) -> None:
        (self.workspace / ".gitignore").write_text("*.log\n", encoding="utf-8")
        (self.workspace / ".eidosignore").write_text("*.snapshot\n", encoding="utf-8")
        scope = self._load()

        self.assertTrue(scope.is_ignored("trace.log", is_directory=False))
        self.assertTrue(scope.is_ignored("result.snapshot", is_directory=False))

    def test_symlinked_ignore_file_is_not_followed(self) -> None:
        outside = self.workspace.parent / f"{self.workspace.name}-outside-ignore"
        outside.write_text("*.txt\n", encoding="utf-8")
        (self.workspace / ".gitignore").symlink_to(outside)

        with self.assertRaisesRegex(DiscoveryScopeError, "ignore_file_invalid"):
            self._load()

    def test_oversized_and_invalid_utf8_ignore_files_fail_closed(self) -> None:
        (self.workspace / ".eidosignore").write_bytes(
            b"a" * (MAX_IGNORE_FILE_BYTES + 1)
        )
        with self.assertRaisesRegex(DiscoveryScopeError, "ignore_file_too_large"):
            self._load()

        (self.workspace / ".eidosignore").write_bytes(b"\xff")
        with self.assertRaisesRegex(DiscoveryScopeError, "ignore_file_invalid_utf8"):
            self._load()


class ToolExecutorDiscoveryScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-discovery-tools-")
        self.workspace = Path(self.temporary.name)
        self.executor = ToolExecutor(self.workspace)

    def tearDown(self) -> None:
        self.executor.close()
        self.temporary.cleanup()

    def _list(self, arguments: dict[str, object] | None = None) -> dict[str, object]:
        return self.executor.execute(
            "list_files", arguments or {}, threading.Event()
        )

    def _search(
        self,
        query: str,
        **arguments: object,
    ) -> dict[str, object]:
        return self.executor.execute(
            "search_text", {"query": query, **arguments}, threading.Event()
        )

    def test_list_files_supports_scoped_depth_and_entry_limits(self) -> None:
        source = self.workspace / "src"
        nested = source / "nested"
        nested.mkdir(parents=True)
        (source / "main.py").write_text("main\n", encoding="utf-8")
        (nested / "module.py").write_text("module\n", encoding="utf-8")
        (self.workspace / "outside.py").write_text("outside\n", encoding="utf-8")

        scoped = self._list({"path": "src", "maxDepth": 1})

        self.assertEqual(scoped["outcome"], "success")
        self.assertEqual(scoped["data"]["paths"], ["src/main.py", "src/nested/"])
        self.assertNotIn("outside.py", scoped["data"]["paths"])

        limited = self._list({"path": "src", "maxEntries": 1})
        self.assertEqual(limited["data"]["paths"], ["src/main.py"])
        self.assertTrue(limited["data"]["truncated"])

    def test_discovery_paths_resolve_workspace_absolute_and_reject_outside(self) -> None:
        (self.workspace / "visible.txt").write_text("needle\n", encoding="utf-8")
        workspace_path = str(self.workspace.resolve())

        listed = self._list({"path": workspace_path})
        searched = self._search("needle", path=workspace_path)

        self.assertEqual(listed["outcome"], "success")
        self.assertIn("visible.txt", listed["data"]["paths"])
        self.assertEqual(searched["outcome"], "success")
        self.assertEqual(
            [match["path"] for match in searched["data"]["matches"]],
            ["visible.txt"],
        )

        outside = str(self.workspace.parent / "not-authorized")
        for tool_name, arguments in (
            ("list_files", {"path": outside}),
            ("search_text", {"query": "needle", "path": outside}),
        ):
            with self.subTest(tool_name=tool_name):
                result = self.executor.execute(tool_name, arguments, threading.Event())
                self.assertEqual(result["outcome"], "error")
                self.assertEqual(result["code"], "path_outside_authorized_roots")

        for tool_name, arguments in (
            ("list_files", {"path": "../outside"}),
            ("search_text", {"query": "needle", "path": "../outside"}),
        ):
            with self.subTest(tool_name=tool_name):
                result = self.executor.execute(tool_name, arguments, threading.Event())
                self.assertEqual(result["outcome"], "error")
                self.assertEqual(result["code"], "invalid_arguments")

    def test_read_tools_resolve_workspace_absolute_paths(self) -> None:
        source = self.workspace / "source.txt"
        source.write_text("one\ntwo\nthree\n", encoding="utf-8")
        absolute = str(source.resolve())

        read = self.executor.execute(
            "read_file", {"path": absolute}, threading.Event()
        )
        line_range = self.executor.execute(
            "read_file_range",
            {"path": absolute, "startLine": 2, "endLine": 2},
            threading.Event(),
        )

        self.assertEqual(read["outcome"], "success")
        self.assertEqual(read["data"]["path"], "source.txt")
        self.assertEqual(line_range["outcome"], "success")
        self.assertEqual(line_range["data"]["path"], "source.txt")
        self.assertEqual(line_range["data"]["content"], "two\n")

    def test_active_skill_absolute_paths_are_read_only_authorized(self) -> None:
        skill_temporary = tempfile.TemporaryDirectory(prefix="eidos-active-skill-")
        self.addCleanup(skill_temporary.cleanup)
        skill = Path(skill_temporary.name)
        (skill / "SKILL.md").write_text("skill needle\n", encoding="utf-8")
        (skill / "scripts").mkdir()
        (skill / "scripts" / "review.py").write_text(
            "print('skill needle')\n", encoding="utf-8"
        )
        skill_root = skill.resolve()
        self.executor.set_active_skill_roots(lambda: (skill_root,))

        read = self.executor.execute(
            "read_file", {"path": str(skill_root / "SKILL.md")}, threading.Event()
        )
        listed = self.executor.execute(
            "list_files", {"path": str(skill_root)}, threading.Event()
        )
        searched = self.executor.execute(
            "search_text",
            {"query": "skill needle", "path": str(skill_root)},
            threading.Event(),
        )

        self.assertEqual(read["outcome"], "success")
        self.assertEqual(read["data"]["path"], str(skill_root / "SKILL.md"))
        self.assertEqual(
            set(listed["data"]["paths"]),
            {
                str(skill_root / "SKILL.md"),
                str(skill_root / "scripts") + "/",
                str(skill_root / "scripts" / "review.py"),
            },
        )
        listed_read = self.executor.execute(
            "read_file", {"path": listed["data"]["paths"][0]}, threading.Event()
        )
        self.assertEqual(listed_read["outcome"], "success")
        self.assertEqual(
            listed_read["data"]["path"], listed["data"]["paths"][0]
        )
        self.assertEqual(
            {match["path"] for match in searched["data"]["matches"]},
            {
                str(skill_root / "SKILL.md"),
                str(skill_root / "scripts" / "review.py"),
            },
        )
        review_path = str(skill_root / "scripts" / "review.py")
        searched_range = self.executor.execute(
            "read_file_range",
            {"path": review_path, "startLine": 1, "endLine": 1},
            threading.Event(),
        )
        self.assertEqual(searched_range["outcome"], "success")
        self.assertEqual(searched_range["data"]["path"], review_path)

    def test_inactive_skill_absolute_path_is_an_ordinary_tool_error(self) -> None:
        skill_temporary = tempfile.TemporaryDirectory(prefix="eidos-inactive-skill-")
        self.addCleanup(skill_temporary.cleanup)
        skill = Path(skill_temporary.name)
        (skill / "SKILL.md").write_text("inactive\n", encoding="utf-8")

        result = self.executor.execute(
            "read_file", {"path": str(skill / "SKILL.md")}, threading.Event()
        )

        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["code"], "path_outside_authorized_roots")
        self.assertIn("authorized workspace", result["summary"])
        self.assertFalse(result["sideEffectsMayExist"])
        self.assertFalse(result["reconciliationRequired"])

    def test_list_and_search_apply_root_ignore_and_refresh_each_call(self) -> None:
        (self.workspace / "ignored.log").write_text("needle\n", encoding="utf-8")
        (self.workspace / "visible.txt").write_text("needle\n", encoding="utf-8")
        (self.workspace / ".gitignore").write_text("*.log\n", encoding="utf-8")

        listed = self._list()
        searched = self._search("needle")

        self.assertNotIn("ignored.log", listed["data"]["paths"])
        self.assertNotIn("ignored.log", [item["path"] for item in searched["data"]["matches"]])
        self.assertIn("visible.txt", listed["data"]["paths"])

        (self.workspace / ".gitignore").write_text("", encoding="utf-8")
        self.assertIn("ignored.log", self._list()["data"]["paths"])

    def test_ignored_directory_is_traversed_for_later_negation(self) -> None:
        fixtures = self.workspace / "fixtures"
        fixtures.mkdir()
        (fixtures / "hidden.txt").write_text("needle\n", encoding="utf-8")
        (fixtures / "agent-test.json").write_text("needle\n", encoding="utf-8")
        (self.workspace / ".gitignore").write_text("fixtures/\n", encoding="utf-8")
        (self.workspace / ".eidosignore").write_text(
            "!fixtures/agent-test.json\n", encoding="utf-8"
        )

        listed = self._list()
        searched = self._search("needle")

        self.assertNotIn("fixtures/", listed["data"]["paths"])
        self.assertNotIn("fixtures/hidden.txt", listed["data"]["paths"])
        self.assertIn("fixtures/agent-test.json", listed["data"]["paths"])
        self.assertEqual(
            [item["path"] for item in searched["data"]["matches"]],
            ["fixtures/agent-test.json"],
        )

    def test_eidosignore_negation_cannot_reinclude_hard_or_sensitive_paths(self) -> None:
        git_file = self.workspace / ".git" / "visible.txt"
        eidos_file = self.workspace / ".eidos" / "visible.txt"
        sensitive_file = self.workspace / ".ssh" / "visible.txt"
        for path in (git_file, eidos_file, sensitive_file):
            path.parent.mkdir()
            path.write_text("needle\n", encoding="utf-8")
        (self.workspace / ".eidosignore").write_text(
            "!.git/visible.txt\n!.eidos/visible.txt\n!.ssh/visible.txt\n",
            encoding="utf-8",
        )

        listed = self._list()
        searched = self._search("needle")

        self.assertNotIn(".git/visible.txt", listed["data"]["paths"])
        self.assertNotIn(".eidos/visible.txt", listed["data"]["paths"])
        self.assertNotIn(".ssh/visible.txt", listed["data"]["paths"])
        self.assertEqual(searched["data"]["matches"], [])

    def test_ignored_file_remains_available_to_explicit_operations(self) -> None:
        ignored = self.workspace / "ignored.txt"
        ignored.write_text("before\n", encoding="utf-8")
        (self.workspace / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")

        read = self.executor.execute(
            "read_file", {"path": "ignored.txt"}, threading.Event()
        )
        prepared = self.executor.prepare_file_change(
            "write_file", {"path": "ignored.txt", "content": "after\n"}, threading.Event()
        )
        patched = self.executor.prepare_file_change(
            "apply_patch",
            {"changes": [{
                "type": "update",
                "path": "ignored.txt",
                "chunks": [{"oldLines": ["before"], "newLines": ["after"]}],
            }]},
            threading.Event(),
        )
        deleted = self.executor.prepare_file_change(
            "delete_file", {"path": "ignored.txt"}, threading.Event()
        )

        self.assertEqual(read["outcome"], "success")
        self.assertNotIsInstance(prepared, dict)
        self.assertNotIsInstance(patched, dict)
        self.assertNotIsInstance(deleted, dict)

    def test_shell_launch_does_not_scan_gitignored_sensitive_and_unsafe_entries(self) -> None:
        ignored = self.workspace / "ignored"
        ignored.mkdir()
        (self.workspace / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        (ignored / "credentials.json").write_text("secret\n", encoding="utf-8")

        identity = self.executor.prepare_shell(".", threading.Event())

        self.assertEqual(identity.path, self.workspace.resolve())
        with self.assertRaisesRegex(WorkspacePathError, "sensitive_workspace_content"):
            self.executor.refresh_workspace_index(threading.Event())

        (ignored / "credentials.json").unlink()
        target = ignored / "target.txt"
        target.write_text("x\n", encoding="utf-8")
        os.link(target, ignored / "alias.txt")
        with self.assertRaisesRegex(WorkspacePathError, "unsupported_workspace_hardlink"):
            self.executor.refresh_workspace_index(threading.Event())

    def test_shell_launch_does_not_scan_gitignored_special_files(self) -> None:
        ignored = self.workspace / "ignored"
        ignored.mkdir()
        (self.workspace / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        fifo = ignored / "stream"
        os.mkfifo(fifo)
        try:
            identity = self.executor.prepare_shell(".", threading.Event())
            self.assertEqual(identity.path, self.workspace.resolve())
            with self.assertRaisesRegex(WorkspacePathError, "unsupported_workspace_entry"):
                self.executor.refresh_workspace_index(threading.Event())
        finally:
            fifo.unlink(missing_ok=True)

    def test_invalid_ignore_file_returns_stable_discovery_error(self) -> None:
        (self.workspace / ".eidosignore").write_bytes(b"\xff")

        result = self._list()

        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["code"], "ignore_file_invalid_utf8")

    def test_ignored_directory_does_not_bypass_cancellation(self) -> None:
        ignored = self.workspace / "ignored"
        ignored.mkdir()
        (ignored / "file.txt").write_text("needle\n", encoding="utf-8")
        (self.workspace / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        cancel = threading.Event()
        cancel.set()

        result = self.executor.execute("search_text", {"query": "needle"}, cancel)

        self.assertEqual(result["code"], "canceled")


if __name__ == "__main__":
    unittest.main()
