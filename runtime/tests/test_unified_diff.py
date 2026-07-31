from __future__ import annotations

import sys
from pathlib import Path
import unittest

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.tools.contracts import ApplyPatchInput  # noqa: E402
from eidos_runtime.workspace.unified_diff import (  # noqa: E402
    PatchApplyError,
    apply_strict_single_file_patch,
)


class UnifiedDiffTests(unittest.TestCase):
    def apply(self, original: str, patch: str, path: str = "notes.txt") -> str:
        return apply_strict_single_file_patch(
            path=path, original=original, patch_text=patch
        )

    def assert_error(
        self, code: str, original: str, patch: str, path: str = "notes.txt"
    ) -> None:
        with self.assertRaisesRegex(PatchApplyError, f"^{code}$"):
            self.apply(original, patch, path)

    def test_applies_one_hunk_with_context(self) -> None:
        self.assertEqual(
            self.apply(
                "before\nold\nafter\n",
                "--- a/notes.txt\n+++ b/notes.txt\n@@ -1,3 +1,3 @@ section\n before\n-old\n+new\n after\n",
            ),
            "before\nnew\nafter\n",
        )

    def test_applies_multiple_ordered_hunks(self) -> None:
        self.assertEqual(
            self.apply(
                "one\ntwo\nthree\nfour\n",
                "--- notes.txt\n+++ notes.txt\n@@ -1 +1 @@\n-one\n+ONE\n@@ -4 +4 @@\n-four\n+FOUR\n",
            ),
            "ONE\ntwo\nthree\nFOUR\n",
        )

    def test_addition_at_beginning(self) -> None:
        self.assertEqual(
            self.apply(
                "old\n",
                "--- a/notes.txt\n+++ b/notes.txt\n@@ -0,0 +1 @@\n+new\n",
            ),
            "new\nold\n",
        )

    def test_addition_at_end(self) -> None:
        self.assertEqual(
            self.apply(
                "old\n",
                "--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1,2 @@\n old\n+new\n",
            ),
            "old\nnew\n",
        )

    def test_deletion_and_empty_and_unicode_lines(self) -> None:
        self.assertEqual(
            self.apply(
                "alpha\n\n你好\nomit\nomega\n",
                "--- a/notes.txt\n+++ b/notes.txt\n@@ -1,5 +1,4 @@\n alpha\n \n 你好\n-omit\n omega\n",
            ),
            "alpha\n\n你好\nomega\n",
        )

    def test_rejects_crlf_patch_as_current_parser_does(self) -> None:
        self.assert_error(
            "invalid_patch",
            "first\r\nold\r\nlast\r\n",
            "--- a/notes.txt\r\n+++ b/notes.txt\r\n@@ -1,3 +1,3 @@\r\n first\r\n-old\r\n+new\r\n last\r\n",
        )

    def test_preserves_crlf_content_line_endings_when_patch_uses_lf_syntax(self) -> None:
        self.assertEqual(
            self.apply(
                "first\r\nold\r\nlast\r\n",
                "--- a/notes.txt\n+++ b/notes.txt\n@@ -1,3 +1,3 @@\n first\r\n-old\r\n+new\r\n last\r\n",
            ),
            "first\r\nnew\r\nlast\r\n",
        )

    def test_rejects_malformed_or_mismatched_headers(self) -> None:
        patch = "--- other.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-old\n+new\n"
        self.assert_error("invalid_patch", "old\n", patch)
        self.assert_error(
            "invalid_patch",
            "old\n",
            "--- a/notes.txt\n+++ b/other.txt\n@@ -1 +1 @@\n-old\n+new\n",
        )

    def test_rejects_missing_hunks_and_invalid_hunk_syntax(self) -> None:
        self.assert_error("invalid_patch", "old\n", "--- a/notes.txt\n+++ b/notes.txt\n")
        self.assert_error(
            "invalid_patch",
            "old\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -one +1 @@\n-old\n+new\n",
        )

    def test_rejects_incorrect_hunk_counts(self) -> None:
        self.assert_error(
            "invalid_patch",
            "old\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -1,2 +1 @@\n-old\n+new\n",
        )

    def test_rejects_out_of_order_overlap_or_out_of_range_hunks(self) -> None:
        self.assert_error(
            "patch_context_mismatch",
            "one\ntwo\nthree\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -2 +2 @@\n-two\n+TWO\n@@ -1 +1 @@\n-one\n+ONE\n",
        )
        self.assert_error(
            "patch_context_mismatch",
            "one\ntwo\nthree\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -2 +2 @@\n-two\n+TWO\n@@ -2 +2 @@\n-two\n+again\n",
        )
        self.assert_error(
            "patch_context_mismatch",
            "one\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -2 +2 @@\n-two\n+TWO\n",
        )

    def test_rejects_context_removed_and_source_exhaustion_mismatches(self) -> None:
        self.assert_error(
            "patch_context_mismatch",
            "old\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n other\n",
        )
        self.assert_error(
            "patch_context_mismatch",
            "old\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-other\n+new\n",
        )
        self.assert_error(
            "patch_context_mismatch",
            "old\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -1,2 +1 @@\n old\n-missing\n+new\n",
        )

    def test_rejects_multiple_files_and_patch_metadata_without_hunks(self) -> None:
        self.assert_error(
            "invalid_patch",
            "old\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-old\n+new\n"
            "--- a/other.txt\n+++ b/other.txt\n@@ -1 +1 @@\n-old\n+new\n",
        )
        self.assert_error(
            "invalid_patch",
            "old\n",
            "diff --git a/notes.txt b/notes.txt\nindex abc..def 100644\n"
            "--- a/notes.txt\n+++ b/notes.txt\n",
        )

    def test_rejects_unsupported_file_forms_and_git_metadata(self) -> None:
        unsupported = (
            "--- /dev/null\n+++ b/notes.txt\n@@ -0,0 +1 @@\n+new\n",
            "--- a/notes.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
            "diff --git a/notes.txt b/notes.txt\nsimilarity index 100%\n"
            "rename from notes.txt\nrename to moved.txt\n",
            "diff --git a/notes.txt b/notes.txt\nsimilarity index 100%\n"
            "copy from notes.txt\ncopy to copied.txt\n",
            "diff --git a/notes.txt b/notes.txt\nold mode 100644\nnew mode 100755\n"
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-old\n+new\n",
            "diff --git a/notes.txt b/notes.txt\nnew file mode 120000\n"
            "--- /dev/null\n+++ b/notes.txt\n@@ -0,0 +1 @@\n+target\n",
            "Binary files a/notes.txt and b/notes.txt differ\n",
            "GIT binary patch\nliteral 1\na\n",
            "diff --cc notes.txt\nindex abc,def..123\n--- a/notes.txt\n+++ b/notes.txt\n",
        )
        for patch in unsupported:
            with self.subTest(patch=patch.splitlines()[0]):
                self.assert_error("invalid_patch", "old\n", patch)

    def test_rejects_no_newline_marker_nul_and_control_structure(self) -> None:
        self.assert_error(
            "invalid_patch",
            "old",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-old\n+new\n"
            "\\ No newline at end of file\n",
        )
        self.assert_error(
            "invalid_patch",
            "old\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-old\x00\n+new\n",
        )

    def test_rejects_trailing_unsupported_structure(self) -> None:
        self.assert_error(
            "invalid_patch",
            "old\n",
            "--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-old\n+new\n"
            "unsupported trailing data\n",
        )

    def test_apply_patch_input_retains_its_existing_size_bound(self) -> None:
        with self.assertRaises(ValidationError):
            ApplyPatchInput(path="notes.txt", patch="界" * 174_763)


if __name__ == "__main__":
    unittest.main()
