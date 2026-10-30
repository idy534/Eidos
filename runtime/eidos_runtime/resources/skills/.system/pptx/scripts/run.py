"""Run document code inside the environment selected by Eidos run_shell."""
import os
from pathlib import Path
import sys


def main() -> None:
    arguments = sys.argv[1:]
    profile = None
    if arguments[:1] == ["--resource"]:
        if len(arguments) < 4:
            raise SystemExit("Usage: run.py [--resource PROFILE] python|node SCRIPT [ARG ...]")
        profile, arguments = arguments[1], arguments[2:]
    if len(arguments) < 2 or arguments[0] not in {"python", "node"}:
        raise SystemExit("Usage: run.py [--resource PROFILE] python|node SCRIPT [ARG ...]")
    kind, args = arguments[0], arguments[1:]
    requested = profile or kind
    bound = os.environ.get("EIDOS_RESOURCE_PROFILE")
    if bound is not None and requested != bound:
        raise SystemExit("Resource profile does not match the Eidos binding; use a direct launcher command")
    key = "RUNTIME_PYTHON" if kind == "python" else "RUNTIME_NODE"
    executable = os.environ.get(key)
    if not executable or not Path(executable).is_absolute():
        raise SystemExit(f"{key} is unavailable; use this Skill through Eidos run_shell")
    if kind == "python" and not args[0].startswith("-"):
        script = Path(args[0]).resolve()
        skill = Path(__file__).resolve().parents[1]
        if script.is_relative_to(skill):
            # PYTHONSAFEPATH stays enabled. Only trusted, read-only Skill helper
            # directories are added for the helpers' existing local imports.
            roots = (str(script.parent), str(skill / "scripts"))
            existing = os.environ.get("PYTHONPATH", "")
            os.environ["PYTHONPATH"] = os.pathsep.join((*roots, existing))
    # Older all-in-one bundles remain usable during migration. New resource
    # bindings provide the verified font path directly; never guess versions.
    if not os.environ.get("EIDOS_DOCUMENT_FONTS"):
        fonts = Path(os.environ["RUNTIME_PYTHON"]).resolve().parents[2] / "dependencies/fonts"
        if fonts.is_dir():
            os.environ["EIDOS_DOCUMENT_FONTS"] = str(fonts)
    if kind == "node":
        args = ["--preserve-symlinks", "--preserve-symlinks-main", *args]
    os.execv(executable, [executable, *args])


if __name__ == "__main__":
    main()
