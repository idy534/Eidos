"""Eidos context compaction and recoverable tool-output clearing policy.

Every threshold derives from the usable input budget of the Run's frozen model
profile (window minus output reserve and safety margin), so a model with a
larger window keeps more working detail and a model with a large output
reserve still compacts before its input budget runs out.
"""

from __future__ import annotations

from eidos_runtime.models import EidosFrozenStrictModel

AUTO_COMPACTION_KEEP_ITEMS = 16
CONTINUITY_SECTION_ID = "continuity-evidence"
CONTINUITY_CONTEXT_MAX_BYTES = 32 * 1024

# One working set is the recent evidence kept verbatim through clearing and
# early compaction. It is also the room left for the next step, which may add
# a model response and a batch of bounded tool results.
WORKING_SET_DIVISOR = 8
WORKING_SET_MIN_TOKENS = 1_024
WORKING_SET_MAX_TOKENS = 32 * 1024
# Old tool bodies stay readable through read_tool_output, so they are released
# before any summary is requested. A small window keeps them for as long as
# three quarters of its budget allows. A larger window releases them at half
# of its budget: every request re-sends the whole context, and old recoverable
# bodies are the cheapest part to give back. The floor keeps mid-size windows
# from clearing earlier than they did with the former fixed threshold; the
# ceiling bounds the per-request cost of very large windows.
CLEARING_TRIGGER_FLOOR_TOKENS = 64 * 1024
CLEARING_TRIGGER_CEILING_TOKENS = 256 * 1024
# Old bodies are released a whole batch at a time; the Provider prefix cache
# after the batch boundary is lost each time it moves. A batch holds about the
# quarter of the trigger that one clearing gives back (four bytes per reference
# token), so a larger window moves the boundary as rarely as a smaller one.
CLEARING_BATCH_MIN_BYTES = 64 * 1024
CLEARING_BATCH_BYTES_PER_TOKEN = 4


class ContextThresholds(EidosFrozenStrictModel):
    """Reference-token boundaries for one usable input budget."""

    working_set_tokens: int
    clearing_trigger_tokens: int
    clearing_target_tokens: int
    clearing_batch_bytes: int
    compaction_trigger_tokens: int


def context_thresholds(usable_input_budget: int) -> ContextThresholds:
    usable = max(1, usable_input_budget)
    working_set = min(
        WORKING_SET_MAX_TOKENS,
        max(WORKING_SET_MIN_TOKENS, usable // WORKING_SET_DIVISOR),
    )
    clearing_trigger = max(1, min(
        usable * 3 // 4,
        max(
            CLEARING_TRIGGER_FLOOR_TOKENS,
            min(CLEARING_TRIGGER_CEILING_TOKENS, usable // 2),
        ),
    ))
    clearing_target = max(1, clearing_trigger * 3 // 4)
    return ContextThresholds(
        working_set_tokens=working_set,
        clearing_trigger_tokens=clearing_trigger,
        # Release a quarter of the trigger at once, so the cleared boundary and
        # the Provider prefix cache behind it move rarely.
        clearing_target_tokens=clearing_target,
        clearing_batch_bytes=max(
            CLEARING_BATCH_MIN_BYTES,
            (clearing_trigger - clearing_target) * CLEARING_BATCH_BYTES_PER_TOKEN,
        ),
        # Early compaction leaves one working set of room. A budget too small
        # for that still uses three quarters of itself first.
        compaction_trigger_tokens=max(usable * 3 // 4, usable - working_set, 1),
    )
