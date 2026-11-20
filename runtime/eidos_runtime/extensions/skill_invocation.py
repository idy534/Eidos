"""Best-effort detection of scripts belonging to a known Skill.

The parser in this module only identifies a possible script token.  It does
not grant access to the path.  ``SkillAccess`` performs the trusted catalog
lookup and the final path checks before a sandbox policy is changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import sys


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
_POWERSHELL_WORD = re.compile(r'''\s*(?:'((?:[^']|'')*)'|"([^"]*)"|([&;]+|[^\s'"&;]+))(?=\s|[&;]|$)''')


@dataclass(frozen=True, slots=True)
class SkillScriptInvocation:
    """A possible script invocation found in a shell command."""

    runner: str
    script_path: Path


def skill_command_tokens(command: str) -> list[str]:
    if sys.platform != "win32":
        return shlex.split(command)
    # Read the literal words in the documented PowerShell launcher. Backslashes
    # are path separators and two single quotes represent a literal apostrophe.
    # Expressions and interpolation are left to PowerShell.
    tokens: list[str] = []
    position = 0
    command = command.rstrip()
    while position < len(command):
        match = _POWERSHELL_WORD.match(command, position)
        if match is None:
            raise ValueError("invalid literal PowerShell command")
        single, double, bare = match.groups()
        if single is not None:
            tokens.append(single.replace("''", "'"))
        else:
            tokens.append(double if double is not None else bare)
        position = match.end()
    return tokens[1:] if tokens[:1] == ["&"] else tokens


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
        tokens = skill_command_tokens(command)
    except ValueError:
        return None
    if not tokens:
        return None

    runtime_runner = {
        "$RUNTIME_PYTHON": "python3", "${RUNTIME_PYTHON}": "python3",
        "$RUNTIME_NODE": "node", "${RUNTIME_NODE}": "node",
    }.get(tokens[0])
    if sys.platform == "win32":
        runtime_runner = runtime_runner or {
            "$env:runtime_python": "python3", "${env:runtime_python}": "python3",
            "$env:runtime_node": "node", "${env:runtime_node}": "node",
        }.get(tokens[0].lower())
    runner = runtime_runner or Path(tokens[0]).name.removesuffix(".exe").lower()
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
