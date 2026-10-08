import pytest

from eidos_runtime.memory.contracts import MemoryProposal
from eidos_runtime.memory.fact_review import MemoryFactAssessment, require_supported_fact
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.tools.memory import MemoryResultData


def test_historical_fact_assessment_still_decodes_in_tool_results():
    result = MemoryResultData.model_validate({"fact_assessment": {
        "supported": True, "atomic": True, "action": "reuse",
        "targetEntryId": "existing-entry", "expectedRevision": 2,
        "reason": "Same fact", "scopeId": "scope", "baseGeneration": 3,
        "inputTokens": 100, "outputTokens": 20,
    }})
    assert isinstance(result.fact_assessment, MemoryFactAssessment)
    assert result.fact_assessment.target_entry_id == "existing-entry"
    assert result.fact_assessment.input_tokens == 100
    assert MemoryResultData().fact_assessment is None


@pytest.mark.parametrize(("supported", "atomic", "code"), [
    (False, True, "memory_claim_unsupported"),
    (True, False, "memory_claim_not_atomic"),
])
def test_historical_consolidation_still_requires_supported_independent_facts(supported, atomic, code):
    with pytest.raises(MemoryRejected, match=code):
        require_supported_fact(supported, atomic)


def test_old_historical_proposal_does_not_implicitly_assert_grounding():
    proposal = MemoryProposal(action="create", candidate_index=0)
    assert not proposal.grounded and not proposal.atomic
