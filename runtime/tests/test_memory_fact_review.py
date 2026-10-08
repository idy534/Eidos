import json
import threading

import pytest

from eidos_runtime.memory.fact_review import review_fact
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel


def output(**changes):
    return {"supported": True, "atomic": True, "action": "create", "reason": "Supported source", **changes}


def test_fact_review_isolated_from_agent_context_and_preserves_usage_metadata():
    from eidos_runtime.model.client import ModelUsage
    from eidos_runtime.model.config import default_profile_snapshot

    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("fact", "submit_memory_fact_assessment", output()),), usage=ModelUsage(input_tokens=100, output_tokens=20))])
    model.profile_snapshot = default_profile_snapshot("deepseek-flash")
    payload = json.dumps({"original_sources": [{"content": "Always reply in Chinese"}], "related_entries": [{"content": "User is Eddy"}]})
    result = review_fact(model, payload, threading.Event())
    assert result.input_tokens == 100 and result.output_tokens == 20
    assert result.elapsed_ms >= 0 and result.model_id
    assert model.contexts == [({"type": "user", "content": payload},)]
    assert len(model.tool_definitions_history[0]) == 1
    assert "matching context, never evidence" in model.instructions_history[0]


@pytest.mark.parametrize("response", [
    ModelResponse(text="not json"),
    ModelResponse(text=json.dumps(output()), response_state="incomplete"),
    ModelResponse(text=json.dumps(output()), finish_reason="length"),
    ModelResponse(tool_calls=(ModelToolCall("wrong", "memory_record", output()),)),
    ModelResponse(text=json.dumps(output(action="reuse", targetEntryId="invented"))),
    ModelResponse(text="x" * 8193),
])
def test_invalid_review_cannot_authorize_publication(response):
    with pytest.raises(MemoryRejected, match="memory_assessment_failed"):
        review_fact(ScriptedModel([response]), "{}", threading.Event())


def test_late_review_is_discarded_after_deadline(monkeypatch):
    from eidos_runtime.memory import fact_review

    times = iter([0, 46])
    monkeypatch.setattr(fact_review.time, "monotonic", lambda: next(times))
    with pytest.raises(MemoryRejected, match="memory_assessment_failed"):
        review_fact(ScriptedModel([ModelResponse(text=json.dumps(output()))]), "{}", threading.Event())


def test_old_historical_proposal_does_not_implicitly_assert_grounding():
    from eidos_runtime.memory.contracts import MemoryProposal

    proposal = MemoryProposal(action="create", candidate_index=0)
    assert not proposal.grounded and not proposal.atomic


def test_oversized_evidence_is_rejected_before_model_request():
    model = ScriptedModel([])
    with pytest.raises(MemoryRejected, match="memory_assessment_input_limit"):
        review_fact(model, "x" * (48 * 1024 + 1), threading.Event())
    assert model.contexts == []
