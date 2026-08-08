from __future__ import annotations

from eidos_runtime.db.database import now_ms as _now_ms
from eidos_runtime.db.repositories.execution import (
    ExecutionRepository as BaseExecutionRepository,
)
from eidos_runtime.db.transitions import transition_segments
from eidos_runtime.runtime.resolution import (
    RuleResolutionSnapshot,
    StepResolutionSnapshot,
)
from eidos_runtime.runtime.state_machine import SegmentStatus


SEGMENT_STEP_QUANTUM = 20
SEGMENT_EFFECTIVE_MS_QUANTUM = 1_800_000


class ExecutionRepository(BaseExecutionRepository):
    """Execution persistence with non-terminal Segment rollover semantics.

    A Segment is a bounded execution slice. Exhausting its local step/time quota
    must not terminate an otherwise healthy Run; the next Step starts in a fresh
    Segment while Run-level hard budgets and LoopGuard remain authoritative.
    """

    def increment_model_step(
        self,
        run_id: str,
        *,
        tool_snapshot: dict[str, object] | None = None,
        rule_resolution_snapshot: RuleResolutionSnapshot | None = None,
        resolution_snapshot: StepResolutionSnapshot | None = None,
    ) -> int:
        self._rollover_exhausted_segment(run_id)
        return super().increment_model_step(
            run_id,
            tool_snapshot=tool_snapshot,
            rule_resolution_snapshot=rule_resolution_snapshot,
            resolution_snapshot=resolution_snapshot,
        )

    def _rollover_exhausted_segment(self, run_id: str) -> None:
        with self.lock, self._connection() as connection:
            segment = connection.execute(
                """
                SELECT step_count, effective_ms FROM execution_segments
                WHERE run_id = ? AND status = 'running'
                ORDER BY ordinal DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if segment is None:
                return
            if (
                int(segment["step_count"]) < SEGMENT_STEP_QUANTUM
                and int(segment["effective_ms"]) < SEGMENT_EFFECTIVE_MS_QUANTUM
            ):
                return
            running_step = connection.execute(
                """
                SELECT id FROM steps
                WHERE run_id = ? AND status = 'running'
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if running_step is not None:
                return
            transition_segments(
                connection,
                run_id,
                frozenset({SegmentStatus.RUNNING}),
                SegmentStatus.COMPLETED,
                _now_ms(),
                "segment_quantum_exhausted",
            )
