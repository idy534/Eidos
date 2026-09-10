from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from eidos_runtime.sandbox import file_commit_helper as helper


class FileCommitHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name).resolve()
        self.source = root / "candidate"
        self.target = root / "target"
        self.source.write_bytes(b"replacement\n")
        self.target.write_bytes(b"original contents\n")
        self.expected = hashlib.sha256(self.target.read_bytes()).hexdigest()

    def commit(self, *, delete: bool = False) -> int:
        with patch.object(
            helper.sys, "argv", ["file_commit_helper", str(self.source), str(self.target), self.expected] + (["delete"] if delete else [])
        ):
            return helper.main()

    def test_existing_file_keeps_inode_and_mode(self) -> None:
        self.target.chmod(0o640)
        before = self.target.stat()
        self.assertEqual(self.commit(), 0)
        after = self.target.stat()
        self.assertEqual(self.target.read_bytes(), b"replacement\n")
        self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
        self.assertEqual(after.st_mode, before.st_mode)

    def test_version_conflict_leaves_external_contents_untouched(self) -> None:
        self.target.write_bytes(b"external edit\n")
        self.assertEqual(self.commit(), helper.EXIT_CONFLICT)
        self.assertEqual(self.target.read_bytes(), b"external edit\n")

    def test_hard_link_is_rejected_before_truncation(self) -> None:
        alias = self.target.with_name("alias")
        os.link(self.target, alias)
        self.assertIn(self.commit(), (helper.EXIT_CONFLICT, helper.EXIT_FAILED))
        self.assertEqual(alias.read_bytes(), b"original contents\n")

    def test_symlink_is_rejected_before_truncation(self) -> None:
        original = self.target.with_name("original")
        self.target.rename(original)
        self.target.symlink_to(original)
        self.assertIn(self.commit(), (helper.EXIT_CONFLICT, helper.EXIT_FAILED))
        self.assertEqual(original.read_bytes(), b"original contents\n")

    def test_directory_is_rejected(self) -> None:
        self.target.unlink()
        self.target.mkdir()
        self.assertIn(self.commit(), (helper.EXIT_CONFLICT, helper.EXIT_FAILED))
        self.assertTrue(self.target.is_dir())

    def test_foreign_owner_is_rejected_before_truncation(self) -> None:
        with patch.object(helper.os, "getuid", return_value=os.getuid() + 1):
            self.assertIn(self.commit(), (helper.EXIT_CONFLICT, helper.EXIT_FAILED))
        self.assertEqual(self.target.read_bytes(), b"original contents\n")

    def test_partial_writes_are_completed(self) -> None:
        real_write = os.write

        def short_write(descriptor: int, data: bytes) -> int:
            return real_write(descriptor, data[:2])

        with patch.object(helper.os, "write", side_effect=short_write):
            self.assertEqual(self.commit(), 0)
        self.assertEqual(self.target.read_bytes(), b"replacement\n")

    def test_write_failure_reports_uncertain_after_truncation(self) -> None:
        with patch.object(helper.os, "write", side_effect=OSError("disk full")):
            self.assertEqual(self.commit(), helper.EXIT_UNCERTAIN)
        self.assertEqual(self.target.read_bytes(), b"")

    def test_failure_after_partial_write_reports_uncertain(self) -> None:
        real_write = os.write
        writes = 0

        def failing_write(descriptor: int, data: bytes) -> int:
            nonlocal writes
            writes += 1
            if writes > 1:
                raise OSError("disk full")
            return real_write(descriptor, data[:2])

        with patch.object(helper.os, "write", side_effect=failing_write):
            self.assertEqual(self.commit(), helper.EXIT_UNCERTAIN)
        self.assertEqual(self.target.read_bytes(), b"re")

    def test_fsync_failure_reports_uncertain_with_written_contents(self) -> None:
        with patch.object(helper.os, "fsync", side_effect=OSError("flush failed")):
            self.assertEqual(self.commit(), helper.EXIT_UNCERTAIN)
        self.assertEqual(self.target.read_bytes(), b"replacement\n")

    def test_delete_removes_verified_target(self) -> None:
        self.assertEqual(self.commit(delete=True), 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.source.read_bytes(), b"replacement\n")

    def test_delete_version_conflict_preserves_external_edit(self) -> None:
        self.target.write_bytes(b"external edit\n")
        self.assertEqual(self.commit(delete=True), helper.EXIT_CONFLICT)
        self.assertEqual(self.target.read_bytes(), b"external edit\n")

    def test_delete_fsync_failure_reports_uncertain(self) -> None:
        with patch.object(helper.os, "fsync", side_effect=OSError("flush failed")):
            self.assertEqual(self.commit(delete=True), helper.EXIT_UNCERTAIN)
        self.assertFalse(self.target.exists())

    def test_delete_symlink_does_not_remove_or_modify_referent(self) -> None:
        original = self.target.with_name("original")
        self.target.rename(original)
        self.target.symlink_to(original)
        self.assertIn(self.commit(delete=True), (helper.EXIT_FAILED, helper.EXIT_CONFLICT))
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(original.read_bytes(), b"original contents\n")

    def test_changed_candidate_is_rejected_before_truncation(self) -> None:
        expected_candidate = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.source.write_bytes(b"changed candidate\n")
        with patch.object(helper.sys, "argv", [
            "file_commit_helper", str(self.source), str(self.target), self.expected,
            "write", expected_candidate,
        ]):
            self.assertEqual(helper.main(), helper.EXIT_FAILED)
        self.assertEqual(self.target.read_bytes(), b"original contents\n")

    def test_verified_candidate_is_written(self) -> None:
        expected_candidate = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with patch.object(helper.sys, "argv", [
            "file_commit_helper", str(self.source), str(self.target), self.expected,
            "write", expected_candidate,
        ]):
            self.assertEqual(helper.main(), 0)
        self.assertEqual(self.target.read_bytes(), b"replacement\n")

    def commit_stdin(self, contents: bytes, candidate_hash: str | None = None) -> int:
        expected_candidate = candidate_hash or hashlib.sha256(contents).hexdigest()
        with (
            patch.object(helper.sys, "argv", [
                "file_commit_helper", "-", str(self.target), self.expected,
                "write", expected_candidate,
            ]),
            patch.object(helper.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(contents))),
        ):
            return helper.main()

    def test_stdin_candidate_completes_short_writes(self) -> None:
        real_write = os.write
        with patch.object(helper.os, "write", side_effect=lambda fd, data: real_write(fd, data[:2])):
            self.assertEqual(self.commit_stdin(b"stdin replacement\n"), 0)
        self.assertEqual(self.target.read_bytes(), b"stdin replacement\n")

    def test_oversized_stdin_is_rejected_before_truncation(self) -> None:
        self.assertEqual(self.commit_stdin(b"x" * (helper.MAX_FILE_BYTES + 1)), helper.EXIT_FAILED)
        self.assertEqual(self.target.read_bytes(), b"original contents\n")

    def test_stdin_candidate_hash_mismatch_is_rejected_before_truncation(self) -> None:
        expected_candidate = hashlib.sha256(b"approved content\n").hexdigest()
        self.assertEqual(self.commit_stdin(b"changed content\n", expected_candidate), helper.EXIT_FAILED)
        self.assertEqual(self.target.read_bytes(), b"original contents\n")
