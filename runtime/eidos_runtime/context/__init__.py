"""Model-visible context projection and budgeting."""
from eidos_runtime.context.budget import ContextBudget, ContextUsageSnapshot
from eidos_runtime.context.plan import (
    ContextPlan,
    ContextPlanner,
    ContextSnapshot,
)
from eidos_runtime.context.verified_compaction import (
    CompactionVerificationError,
    ContextCompactionVerifier,
    VerifiedCompactSummary,
)

__all__ = [
    "CompactionVerificationError",
    "ContextBudget",
    "ContextCompactionVerifier",
    "ContextPlan",
    "ContextPlanner",
    "ContextSnapshot",
    "ContextUsageSnapshot",
    "VerifiedCompactSummary",
]
