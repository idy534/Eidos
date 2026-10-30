"""Read only the managed launcher's documented resource selector."""
from pathlib import Path
import shlex


def resource_profile(command: str, cwd: Path, script_path: Path) -> str:
    if script_path.name != "run.py":
        return "python"
    tokens = shlex.split(command)
    for index, token in enumerate(tokens):
        if token.startswith("$"):
            continue
        if (cwd / token).resolve(strict=False) == script_path:
            arguments = tokens[index + 1:]
            explicit = None
            if arguments[:1] == ["--resource"]:
                if len(arguments) < 3:
                    raise ValueError("resource profile requires a value and runner")
                explicit, arguments = arguments[1], arguments[2:]
            if not arguments or arguments[0] not in {"python", "node"}:
                raise ValueError("managed launcher requires python or node")
            return explicit or arguments[0]
    raise ValueError("managed launcher was not found in the command")
