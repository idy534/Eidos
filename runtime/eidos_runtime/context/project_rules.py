from __future__ import annotations

from collections.abc import Iterable
import hashlib
from pathlib import Path

from eidos_runtime.runtime.resolution import (
    RuleResolutionSnapshot,
    RuleResolutionWarning,
    RuleSourceSnapshot,
    ShadowedRuleCandidate,
)


PROJECT_RULE_BUDGET_BYTES = 64 * 1024
PROJECT_RULE_CANDIDATES = (
    "EIDOS.override.md",
    "EIDOS.md",
    "AGENTS.override.md",
    "AGENTS.md",
    "CLAUDE.md",
)
# Room kept for the line that says where a truncated rule continues.
_CONTINUATION_RESERVE_BYTES = 256
# Rule files loaded because the Run read or patched files below them.
TOUCHED_RULE_LIMIT = 8


class ProjectRuleResolver:
    def __init__(self, budget_bytes: int = PROJECT_RULE_BUDGET_BYTES) -> None:
        if budget_bytes < 0:
            raise ValueError("rule budget must not be negative")
        self.budget_bytes = budget_bytes

    def resolve(
        self, workspace_root: Path, cwd: Path, touched: Iterable[str] = ()
    ) -> RuleResolutionSnapshot:
        """Resolve rules from the root to cwd, then below files the Run touched.

        ``touched`` holds file paths from this Run's file tools, in first-touch
        order. Rule files in their directories load after the cwd chain, up to
        ``TOUCHED_RULE_LIMIT`` files, so a Run picks up the rules of a package
        once it starts working there.
        """
        root = workspace_root.resolve(strict=True)
        current = cwd.resolve(strict=True)
        if current != root and root not in current.parents:
            raise ValueError("cwd is outside workspace root")

        shadowed: list[ShadowedRuleCandidate] = []
        warnings: list[RuleResolutionWarning] = []
        # (level, candidate index, path, filename, data, from the cwd chain)
        selected: list[tuple[int, int, Path, str, bytes, bool]] = []

        directories = [(directory, True) for directory in _chain(root, current)]
        seen = {directory for directory, _ in directories}
        for path in touched:
            directory = _touched_directory(root, path)
            if directory is None:
                continue
            for value in _chain(root, directory):
                if value not in seen:
                    seen.add(value)
                    directories.append((value, False))

        for directory, from_cwd in directories:
            level = len(directory.relative_to(root).parts)
            if not from_cwd and sum(not entry[5] for entry in selected) >= TOUCHED_RULE_LIMIT:
                break
            selected_index: int | None = None
            for candidate_index, filename in enumerate(PROJECT_RULE_CANDIDATES):
                candidate = directory / filename
                if not candidate.exists():
                    continue
                try:
                    resolved = candidate.resolve(strict=True)
                    if resolved != root and root not in resolved.parents:
                        warnings.append(_warning(
                            "RULE_PATH_OUTSIDE_WORKSPACE",
                            candidate,
                            "Rule candidate resolves outside the workspace",
                        ))
                        continue
                    data = candidate.read_bytes()
                    text = data.decode("utf-8")
                except (OSError, UnicodeError):
                    warnings.append(_warning(
                        "RULE_READ_ERROR",
                        candidate,
                        "Rule candidate could not be read as UTF-8",
                    ))
                    continue
                if not text.strip():
                    continue
                selected.append((level, candidate_index, candidate, filename, data, from_cwd))
                selected_index = candidate_index
                break

            if selected_index is None:
                continue
            for filename in PROJECT_RULE_CANDIDATES[selected_index + 1 :]:
                candidate = directory / filename
                if candidate.exists():
                    shadowed.append(ShadowedRuleCandidate(
                        absolute_path=str(candidate.absolute()),
                        relative_path=candidate.relative_to(root).as_posix(),
                        filename=filename,
                        directory_level=level,
                        reason="higher_precedence_candidate_selected",
                    ))

        # The most specific rules take precedence, so the budget is spent from
        # the deepest directory of the cwd chain up; the root file is the one
        # cut first. Rules loaded for touched files come after, in first-touch
        # order. The output keeps root-first order for the cwd chain.
        remaining = self.budget_bytes
        fitted: dict[Path, tuple[str, int, int, bool]] = {}
        in_cwd = [entry for entry in selected if entry[5]]
        later = [entry for entry in selected if not entry[5]]
        for _, _, candidate, _, data, _ in [*reversed(in_cwd), *later]:
            relative = candidate.relative_to(root).as_posix()
            content, included, used, truncated = _fit_rule(data, remaining, relative)
            fitted[candidate] = content, included, used, truncated
            remaining -= used
            if truncated:
                warnings.append(_warning(
                    "RULE_BUDGET_TRUNCATED",
                    candidate,
                    f"Project rule content exceeded the remaining {self.budget_bytes // 1024} KiB budget",
                ))

        rules = []
        for level, candidate_index, candidate, filename, data, _ in selected:
            content, included, _, truncated = fitted[candidate]
            rules.append(RuleSourceSnapshot(
                absolute_path=str(candidate.resolve()),
                relative_path=candidate.relative_to(root).as_posix(),
                filename=filename,
                content=content,
                content_hash=hashlib.sha256(data).hexdigest(),
                byte_count=len(data),
                included_byte_count=included,
                directory_level=level,
                selection_reason=(
                    "eidos_override"
                    if candidate_index == 0
                    else "eidos_native"
                    if candidate_index == 1
                    else "compatibility_fallback"
                ),
                truncated=truncated,
            ))

        return RuleResolutionSnapshot.create(
            workspace_root=str(root),
            cwd=str(current),
            budget_bytes=self.budget_bytes,
            used_bytes=self.budget_bytes - remaining,
            rules=tuple(rules),
            shadowed=tuple(shadowed),
            warnings=tuple(warnings),
        )


def _chain(root: Path, directory: Path) -> list[Path]:
    parts = directory.relative_to(root).parts
    return [root, *(root.joinpath(*parts[: index + 1]) for index in range(len(parts)))]


def _touched_directory(root: Path, path: str) -> Path | None:
    """The existing workspace directory holding a touched file, or None."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        directory = candidate.parent.resolve(strict=True)
    except OSError:
        return None
    if directory != root and root not in directory.parents:
        return None
    return directory


def _fit_rule(data: bytes, limit: int, relative_path: str) -> tuple[str, int, int, bool]:
    """Return the included text, file bytes included, budget used and truncation.

    A cut ends at the last complete line that fits, and a final line tells
    the model which line to read next. The line counts toward the budget.
    """
    if len(data) <= limit:
        return data.decode("utf-8"), len(data), len(data), False
    room = limit - _CONTINUATION_RESERVE_BYTES - len(relative_path.encode())
    if room <= 0:
        return "", 0, 0, True
    prefix = _utf8_prefix(data, room)
    newline = prefix.rfind(b"\n")
    if newline >= 0:
        prefix = prefix[: newline + 1]
    lines = prefix.count(b"\n")
    note = (
        f"[Truncated by Eidos: the first {lines} lines of {relative_path} are shown "
        f"({len(prefix):,} of {len(data):,} bytes). Read the file from line {lines + 1} for the rest.]"
    )
    separator = "" if not prefix or prefix.endswith(b"\n") else "\n"
    content = prefix.decode("utf-8") + separator + note
    return content, len(prefix), len(content.encode()), True


def _utf8_prefix(data: bytes, limit: int) -> bytes:
    prefix = data[:limit]
    while prefix:
        try:
            prefix.decode("utf-8")
            return prefix
        except UnicodeDecodeError as error:
            prefix = prefix[: error.start]
    return b""


def _warning(
    code: str,
    path: Path,
    message: str,
) -> RuleResolutionWarning:
    return RuleResolutionWarning.model_validate({
        "code": code,
        "path": str(path.absolute()),
        "message": message,
    })
