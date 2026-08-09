from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


from eidos_runtime.sandbox.seatbelt import (
    PROFILE_PATH,
    runtime_python_executable,
    secure_workspace_move,
)


class RuntimeDistributionSeatbeltTests(unittest.TestCase):
    def test_python_runtime_policy_is_read_only(self) -> None:
        policy = PROFILE_PATH.read_text(encoding="utf-8")
        self.assertIn("(allow file-read* file-test-existence", policy)
        self.assertIn("(allow file-map-executable", policy)
        self.assertIn(
            '(deny file-write*\n  (subpath (param "PYTHON_RUNTIME_ROOT"))',
            policy,
        )
        self.assertNotIn("/Library/Developer/CommandLineTools/usr/bin/python3", policy)

    def test_secure_workspace_move_uses_the_running_python_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "candidate"
            target = workspace / "target"
            source.write_text("candidate", encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0)
            with (
                patch("eidos_runtime.sandbox.seatbelt.is_seatbelt_ready", return_value=True),
                patch("eidos_runtime.sandbox.seatbelt.os.access", return_value=True),
                patch(
                    "eidos_runtime.sandbox.seatbelt.subprocess.run",
                    return_value=completed,
                ) as run,
            ):
                self.assertEqual(
                    secure_workspace_move(workspace, source, target, None),
                    "committed",
                )

            command = run.call_args.args[0]
            self.assertIn(str(runtime_python_executable()), command)
            self.assertNotIn("/Library/Developer/CommandLineTools/usr/bin/python3", command)
            self.assertIn(str(Path(sys.prefix).resolve()), " ".join(command))


if __name__ == "__main__":
    unittest.main()
