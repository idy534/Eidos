from __future__ import annotations

import hashlib
from pathlib import Path

from eidos_runtime.runtime.resolution import (
    RuleResolutionSnapshot,
    RuleResolutionWarning,
    RuleSourceSnapshot,
    ShadowedRuleCandidate,
)


PROJECT_RULE_BUDGET_BYTES = 32 * 1024
PROJECT_RULE_CANDIDATES = (
    "EIDOS.override.md",
    "EIDOS.md",
    "AGENTS.override.md",
    "AGENTS.md",
    "CLAUDE.md",
)


class ProjectRuleResolver:
    def __init__(self, budget_bytes: int = PROJECT_RULE_BUDGET_BYTES) -> None:
        if budget_bytes < 0:
            raise ValueError("rule budget must not be negative")
        self.budget_bytes = budget_bytes

    def resolve(self, workspace_root: Path, cwd: Path) -> RuleResolutionSnapshot:
        root = workspace_root.resolve(strict=True)
        current = cwd.resolve(strict=True)
        if current != root and root not in current.parents:
            raise ValueError("cwd is outside workspace root")

        rules: list[RuleSourceSnapshot] = []
        shadowed: list[ShadowedRuleCandidate] = []
        warnings: list[RuleResolutionWarning] = []
        remaining = self.budget_bytes

        relative_parts = current.relative_to(root).parts
        directories = [root]
        for index in range(len(relative_parts)):
            directories.append(root.joinpath(*relative_parts[: index + 1]))

        for level, directory in enumerate(directories):
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

                included = _utf8_prefix(data, remaining)
                content = included.decode("utf-8")
                truncated = len(included) < len(data)
                rule = RuleSourceSnapshot(
                    absolute_path=str(candidate.resolve()),
                    relative_path=candidate.relative_to(root).as_posix(),
                    filename=filename,
                    content=content,
                    content_hash=hashlib.sha256(data).hexdigest(),
                    byte_count=len(data),
                    included_byte_count=len(included),
                    directory_level=level,
                    selection_reason=(
                        "eidos_override"
                        if candidate_index == 0
                        else "eidos_native"
                        if candidate_index == 1
                        else "compatibility_fallback"
                    ),
                    truncated=truncated,
                )
                rules.append(rule)
                remaining -= len(included)
                selected_index = candidate_index
                if truncated:
                    warnings.append(_warning(
                        "RULE_BUDGET_TRUNCATED",
                        candidate,
                        "Project rule content exceeded the remaining 32 KiB budget",
                    ))
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

        return RuleResolutionSnapshot.create(
            workspace_root=str(root),
            cwd=str(current),
            budget_bytes=self.budget_bytes,
            used_bytes=self.budget_bytes - remaining,
            rules=tuple(rules),
            shadowed=tuple(shadowed),
            warnings=tuple(warnings),
        )


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
