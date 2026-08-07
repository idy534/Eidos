from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.sandbox.seatbelt import (  # noqa: E402
    SYSTEM_PYTHON,
    is_seatbelt_usable,
    secure_workspace_move,
)


class SeatbeltFileCommitCacheTests(unittest.TestCase):
    def test_secure_workspace_move_disables_python_bytecode_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            source = workspace / ".candidate.tmp"
            target = workspace / "readme"
            source.write_bytes(b"")

            with (
                patch("eidos_runtime.sandbox.seatbelt.sys.platform", "darwin"),
                patch(
                    "eidos_runtime.sandbox.seatbelt.is_seatbelt_ready",
                    return_value=True,
                ),
                patch("eidos_runtime.sandbox.seatbelt.os.access", return_value=True),
                patch("eidos_runtime.sandbox.seatbelt.subprocess.run") as run,
            ):
                run.return_value.returncode = 0
                status = secure_workspace_move(workspace, source, target, None)

            self.assertEqual(status, "committed")
            command = run.call_args.args[0]
            python_index = command.index(SYSTEM_PYTHON)
            self.assertEqual(command[python_index + 1], "-B")

    @unittest.skipUnless(
        sys.platform == "darwin" and is_seatbelt_usable(),
        "Seatbelt is macOS-only and requires usable sandbox-exec",
    )
    def test_secure_workspace_move_does_not_pollute_workspace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-secure-move-") as temporary:
            workspace = Path(temporary)
            source = workspace / ".candidate.tmp"
            target = workspace / "readme"
            source.write_bytes(b"")

            status = secure_workspace_move(workspace, source, target, None)

            self.assertEqual(status, "committed")
            self.assertTrue(target.is_file())
            self.assertEqual(
                sorted(path.name for path in workspace.iterdir()),
                ["readme"],
            )


if __name__ == "__main__":
    unittest.main()
