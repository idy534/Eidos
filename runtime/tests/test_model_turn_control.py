from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse as PAIModelResponse, TextPart

from eidos_runtime.model.client import AssistantMessagePhase, ModelRequestError
from eidos_runtime.model.native_custom import encode_responses_context, map_responses_response
from eidos_runtime.model.pydantic_ai_client import encode_context, map_model_response


@pytest.mark.parametrize("end_turn", [None, False, True])
def test_responses_preserves_native_message_phase_and_turn_control(end_turn: bool | None) -> None:
    response = SimpleNamespace(
        id="response", model="fixture", status="completed", usage=None,
        end_turn=end_turn,
        output=[SimpleNamespace(
            type="message", role="assistant", phase="commentary",
            content=[SimpleNamespace(type="output_text", text="Checking the document.")],
        )],
    )
    result = map_responses_response(response)
    assert result.phase is AssistantMessagePhase.COMMENTARY
    assert result.phase_source == "provider"
    assert result.end_turn is end_turn


def test_pydantic_response_preserves_text_part_phase() -> None:
    result = map_model_response(PAIModelResponse(
        parts=[TextPart("Finished.", provider_name="openai", provider_details={"phase": "final_answer"})],
        provider_name="openai", finish_reason="stop",
    ))
    assert result.phase is AssistantMessagePhase.FINAL_ANSWER
    assert result.phase_source == "provider"


@pytest.mark.parametrize("value", ["false", 0, {}, []])
def test_invalid_native_turn_control_is_rejected(value: object) -> None:
    with pytest.raises(ModelRequestError):
        map_responses_response({"id": "response", "status": "completed", "output": [], "end_turn": value})


def test_mixed_native_message_phases_are_not_fabricated_as_final() -> None:
    result = map_responses_response({
        "status": "completed", "output": [
            {"type": "message", "role": "assistant", "phase": phase,
             "content": [{"type": "output_text", "text": text}]}
            for phase, text in [("commentary", "Checking."), ("final_answer", "Done.")]
        ],
    })
    assert result.phase is AssistantMessagePhase.UNKNOWN


def test_both_history_encoders_preserve_known_assistant_phase() -> None:
    context = ({"type": "assistant", "content": "Checking.", "phase": "commentary"},)
    assert encode_responses_context(context)[0]["phase"] == "commentary"
    part = encode_context(context)[0].parts[0]
    assert part.provider_details == {"phase": "commentary"}
    assert part.provider_name == "openai"


def test_unknown_history_phase_does_not_create_a_provider_final_label() -> None:
    context = ({"type": "assistant", "content": "Answer.", "phase": "unknown"},)
    assert "phase" not in encode_responses_context(context)[0]
    assert encode_context(context)[0].parts[0].provider_details is None
