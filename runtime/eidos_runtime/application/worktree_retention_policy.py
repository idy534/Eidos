from __future__ import annotations

from eidos_runtime.models import EidosFrozenStrictModel


class RetentionCandidate(EidosFrozenStrictModel):
    """Facts supplied by the application layer to the pure retention policy."""

    worktree_id: str
    managed: bool
    active: bool
    safe: bool
    idle: bool
    last_used_at: int
    created_at: int = 0
    protection_reason: str | None = None


class RetentionSkipped(EidosFrozenStrictModel):
    worktree_id: str
    reason: str


class RetentionDecision(EidosFrozenStrictModel):
    keep: tuple[str, ...] = ()
    cleanup: tuple[str, ...] = ()
    skipped: tuple[RetentionSkipped, ...] = ()


class WorktreeRetentionPolicy:
    """Select retention candidates without reading Git, SQLite, or disk."""

    def select(
        self,
        candidates: tuple[RetentionCandidate, ...],
        *,
        limit: int,
    ) -> RetentionDecision:
        if limit < 0:
            raise ValueError("retention limit must be non-negative")
        ordered = sorted(
            candidates,
            key=lambda value: (value.last_used_at, value.created_at, value.worktree_id),
            reverse=True,
        )
        keep: list[str] = [candidate.worktree_id for candidate in ordered[:limit]]
        skipped: list[RetentionSkipped] = []
        cleanup: list[str] = []
        for candidate in ordered[limit:]:
            if not candidate.managed:
                skipped.append(
                    RetentionSkipped(
                        worktree_id=candidate.worktree_id,
                        reason=candidate.protection_reason or "not_managed",
                    )
                )
            elif candidate.active:
                keep.append(candidate.worktree_id)
                skipped.append(
                    RetentionSkipped(
                        worktree_id=candidate.worktree_id,
                        reason=candidate.protection_reason or "active",
                    )
                )
            elif not candidate.safe:
                keep.append(candidate.worktree_id)
                skipped.append(
                    RetentionSkipped(
                        worktree_id=candidate.worktree_id,
                        reason=candidate.protection_reason or "unsafe",
                    )
                )
            elif not candidate.idle:
                keep.append(candidate.worktree_id)
                skipped.append(
                    RetentionSkipped(
                        worktree_id=candidate.worktree_id,
                        reason=candidate.protection_reason or "not_idle",
                    )
                )
            else:
                cleanup.append(candidate.worktree_id)
        cleanup.reverse()
        return RetentionDecision(
            keep=tuple(keep),
            cleanup=tuple(cleanup),
            skipped=tuple(skipped),
        )


__all__ = [
    "RetentionCandidate",
    "RetentionDecision",
    "RetentionSkipped",
    "WorktreeRetentionPolicy",
]
