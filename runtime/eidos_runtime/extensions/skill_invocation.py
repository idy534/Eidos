"""Best-effort detection of scripts belonging to a known Skill.

The parser in this module only identifies a possible script token.  It does
not grant access to the path.  ``SkillAccess`` performs the trusted catalog
lookup and the final path checks before a sandbox policy is changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex


SUPPORTED_RUNNERS = frozenset({
    "python",
    "python3",
    "bash",
    "zsh",
    "sh",
    "node",
    "deno",
    "ruby",
    "perl",
    "pwsh",
})
SUPPORTED_SCRIPT_EXTENSIONS = frozenset({
    ".py",
    ".sh",
    ".js",
    ".ts",
    ".rb",
    ".pl",
    ".ps1",
})

_OPTIONS_WITH_ARGUMENT = frozenset({
    "-c",
    "--command",
    "-e",
    "--eval",
    "--execute",
    "--print",
    "-m",
    "--module",
    "-W",
    "--warning",
    "--config",
    "--cwd",
    "-f",
})
_DENO_SUBCOMMANDS = frozenset({
    "cache",
    "check",
    "compile",
    "eval",
    "fmt",
    "install",
    "lint",
    "repl",
    "run",
    "task",
    "test",
    "upgrade",
})


@dataclass(frozen=True, slots=True)
class SkillScriptInvocation:
    """A possible script invocation found in a shell command."""

    runner: str
    script_path: Path

    @property
    def invocation_type(self) -> str:
        return "implicit"


def parse_skill_script_invocation(
    command: str,
    cwd: Path,
) -> SkillScriptInvocation | None:
    """Return the first supported script token in ``command``.

    Shell syntax is intentionally not interpreted here.  ``shlex`` provides
    a conservative tokenization for the common cases, and malformed quoting
    is treated as no match.  The returned path is only a candidate; callers
    must verify it against a trusted Skill snapshot.
    """

    if not command or not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None

    runner = Path(tokens[0]).name.removesuffix(".exe").lower()
    if runner not in SUPPORTED_RUNNERS:
        return None

    index = 1
    if runner == "deno":
        while index < len(tokens) and tokens[index] in _DENO_SUBCOMMANDS:
            index += 1

    while index < len(tokens):
        token = tokens[index]
        if token == "--" or token.startswith("-"):
            if token in _OPTIONS_WITH_ARGUMENT:
                index += 2
            else:
                index += 1
            continue
        candidate = Path(token)
        if candidate.suffix.lower() not in SUPPORTED_SCRIPT_EXTENSIONS:
            return None
        return SkillScriptInvocation(
            runner=runner,
            script_path=(cwd / candidate).resolve(strict=False)
            if not candidate.is_absolute()
            else candidate.resolve(strict=False),
        )
    return None


# These aliases make the boundary easy to discover for future callers while
# keeping one implementation and one parser contract.
parse_script_invocation = parse_skill_script_invocation
detect_skill_script_invocation = parse_skill_script_invocation
