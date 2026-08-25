from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.extensions.skill_invocation import (  # noqa: E402
    parse_skill_script_invocation,
)


class SkillInvocationTests(unittest.TestCase):
    def test_every_supported_runner_and_script_extension_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-invocation-") as directory:
            cwd = Path(directory)
            scripts = cwd / "scripts"
            scripts.mkdir()
            commands = {
                "python scripts/run.py": "run.py",
                "python3 scripts/run.py": "run.py",
                "bash scripts/run.sh": "run.sh",
                "sh scripts/run.sh": "run.sh",
                "zsh scripts/run.sh": "run.sh",
                "node scripts/run.js": "run.js",
                "deno run scripts/run.ts": "run.ts",
                "ruby scripts/run.rb": "run.rb",
                "perl scripts/run.pl": "run.pl",
                "pwsh scripts/run.ps1": "run.ps1",
            }
            for name in set(commands.values()):
                (scripts / name).write_text("fixture\n", encoding="utf-8")

            for command, name in commands.items():
                with self.subTest(command=command):
                    invocation = parse_skill_script_invocation(command, cwd)
                    self.assertIsNotNone(invocation)
                    assert invocation is not None
                    self.assertEqual(invocation.script_path, (scripts / name).resolve())

    def test_best_effort_runner_parser_supports_supported_languages_and_options(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-invocation-") as directory:
            cwd = Path(directory)
            script = cwd / "scripts" / "run.ts"
            script.parent.mkdir()
            script.write_text("console.log('ok')\n", encoding="utf-8")

            for command in (
                "python -u scripts/run.ts",
                "bash -- scripts/run.ts",
                "deno run --allow-read scripts/run.ts",
                "node scripts/run.ts --flag",
                "pwsh -File scripts/run.ts",
            ):
                with self.subTest(command=command):
                    invocation = parse_skill_script_invocation(command, cwd)
                    self.assertIsNotNone(invocation)
                    assert invocation is not None
                    self.assertEqual(invocation.script_path, script.resolve())

    def test_parser_does_not_treat_inline_code_or_malformed_shell_as_a_script(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-invocation-") as directory:
            cwd = Path(directory)
            self.assertIsNone(
                parse_skill_script_invocation("python3 -c 'print(1)'", cwd)
            )
            self.assertIsNone(
                parse_skill_script_invocation("node -e 'run.js'", cwd)
            )
            self.assertIsNone(
                parse_skill_script_invocation("python3 'scripts/run.py", cwd)
            )

    def test_first_non_option_script_must_have_a_supported_extension(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-invocation-") as directory:
            cwd = Path(directory)
            self.assertIsNone(
                parse_skill_script_invocation("python -m package.module", cwd)
            )
            self.assertIsNone(
                parse_skill_script_invocation("python scripts/run.txt", cwd)
            )


if __name__ == "__main__":
    unittest.main()
