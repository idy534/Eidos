from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT / "runtime" / "tests")]

from eidos_runtime.sandbox.seatbelt import run_seatbelt_self_test  # noqa: E402


TESTS = (
    "test_seatbelt.SeatbeltSmokeTests.test_workspace_write_profile_passes_fail_closed_self_test",
    "test_seatbelt.SeatbeltSmokeTests.test_git_worktree_pointer_is_supported_but_not_writable",
    "test_seatbelt.SeatbeltSmokeTests.test_dynamic_profile_grants_only_approved_external_paths",
    "test_seatbelt.SeatbeltSmokeTests.test_dynamic_profile_keeps_runtime_write_and_git_denies",
    "test_seatbelt.SeatbeltSmokeTests.test_managed_workspace_inside_data_keeps_data_state_denied",
    "test_seatbelt.SeatbeltSmokeTests.test_dynamic_network_grant_reaches_only_when_enabled",
    "test_shell.ShellProcessGroupTests",
)


def main() -> int:
    if sys.platform != "darwin":
        print("Native Seatbelt tests require macOS.", file=sys.stderr)
        return 1
    self_test = run_seatbelt_self_test()
    if not self_test.available or self_test.failures:
        print(
            f"Seatbelt Self-Test unavailable: {', '.join(self_test.failures)}",
            file=sys.stderr,
        )
        return 1
    suite = unittest.TestLoader().loadTestsFromNames(TESTS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print(f"Native Seatbelt tests skipped: {result.skipped}", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
