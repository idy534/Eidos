from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path


def suggest_commit_message(
    paths: Sequence[str],
    *,
    stat_summary: str,
    untracked_paths: Collection[str],
) -> str:
    """Build a deterministic Conventional Commit message from changed Git paths."""

    untracked_set = set(untracked_paths)
    lowered = [path.lower() for path in paths]
    if all(
        path.endswith((".md", ".mdx", ".rst", ".txt")) or path.startswith("docs/")
        for path in lowered
    ):
        kind = "docs"
    elif all(
        "test" in path or path.endswith(("_test.py", ".test.ts", ".test.tsx", ".spec.ts"))
        for path in lowered
    ):
        kind = "test"
    elif all(path.endswith((".css", ".scss")) for path in lowered):
        kind = "style"
    elif len(paths) > 0 and all(path in untracked_set for path in paths):
        kind = "feat"
    elif any("fix" in path for path in lowered):
        kind = "fix"
    elif any("refactor" in path for path in lowered):
        kind = "refactor"
    else:
        kind = (
            "chore"
            if all(
                Path(p).name in {"package.json", "pnpm-lock.yaml", "pyproject.toml", "uv.lock"}
                for p in lowered
            )
            else "feat"
        )

    top_dirs = [Path(p).parts[0] for p in paths if len(Path(p).parts) > 1]
    scope = top_dirs[0] if len(set(top_dirs)) == 1 and top_dirs[0] not in {".", ""} else None
    header_prefix = f"{kind}({scope})" if scope else kind

    if len(paths) == 1:
        name = Path(paths[0]).name
        action = "add" if paths[0] in untracked_set else "update"
        subject = f"{header_prefix}: {action} {name}"
    else:
        basenames = [Path(p).name for p in paths[:2]]
        suffix = f" and {len(paths) - 2} more" if len(paths) > 2 else ""
        subject = f"{header_prefix}: update {', '.join(basenames)}{suffix}"
    if len(subject) > 72:
        subject = subject[:69].rstrip() + "..."
    if len(paths) <= 1 and not stat_summary.strip():
        return subject
    bullets = "\n".join(f"- {path}" for path in paths[:6])
    extra = f"\n- ... and {len(paths) - 6} more files" if len(paths) > 6 else ""
    return f"{subject}\n\n{bullets}{extra}"
