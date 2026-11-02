from __future__ import annotations

from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart
import pytest

from eidos_runtime.model.native_custom import encode_responses_context
from eidos_runtime.model.pydantic_ai_client import encode_context


@pytest.mark.parametrize("legacy", [False, True])
def test_source_context_uses_compatible_provider_roles_without_becoming_system_policy(legacy):
    contexts = tuple({
        "type": "user" if legacy else "context", "sectionId": section,
        "source": source, "content": content,
        **({"memoryEpochs": {"private-scope": 3}} if section == "memory-evidence" else {}),
    } for section, source, content in (
        ("project-rule:AGENTS.md", "AGENTS.md", "Keep changes focused."),
        ("selected-skill:user:review", "user:review", "Read the checklist."),
        ("memory-evidence", "memory", "An earlier user preference."),
        ("workspace-environment", "runtime", "Current workspace."),
    )) + ({"type": "user", "content": "Translate the following text."},)
    messages = encode_context(contexts)
    parts = [part for message in messages if isinstance(message, ModelRequest) for part in message.parts]
    assert not any(isinstance(part, SystemPromptPart) for part in parts)
    assert [part.content for part in parts if isinstance(part, UserPromptPart)] == [item["content"] for item in contexts]
    assert "private-scope" not in str(messages)
    native = encode_responses_context(contexts)
    assert [item["content"] for item in native] == [item["content"] for item in contexts]
    assert all(item["role"] == "user" for item in native)
    assert "private-scope" not in str(native)


def test_recovery_keeps_scoped_guidance_and_the_current_user_goal():
    from eidos_runtime.model.context_projection import project_context, recovery_context

    guidance = {"type": "context", "contextCategory": "memory", "memorySource": "project", "scope": "/project", "sectionId": "project-rule:AGENTS.md", "source": "AGENTS.md", "content": "Keep the approved scope.\n" * 100}
    goal = {"type": "user", "sourceItemId": "current-goal", "content": "Translate the supplied paragraph."}
    context = (guidance, {"type": "context", "content": "environment"}, goal) + tuple(
        {"type": "assistant", "content": f"Earlier progress {index}"} for index in range(20)
    )
    assert project_context(context, text_bytes=256)[0] == guidance
    recovered = recovery_context(context, 1024, current_goal_id="current-goal")
    assert guidance in recovered
    assert goal in recovered


def test_recovery_keeps_memory_source_and_scope_with_revocation_guard():
    from eidos_runtime.model.context_projection import recovery_context

    memory = {"type": "context", "sectionId": "memory-evidence", "contextCategory": "memory",
              "memorySource": "long_term", "scope": "global", "source": "memory",
              "content": "A saved reference. " * 100, "memoryEpochs": {"scope-id": 2}}
    recovered = recovery_context((memory, {"type": "user", "content": "Continue."}), 1024)
    projected = next(item for item in recovered if item.get("sectionId") == "memory-evidence")
    assert projected["contextCategory"] == "memory"
    assert projected["memorySource"] == "long_term"
    assert projected["scope"] == "global"
    assert projected["memoryEpochs"] == {"scope-id": 2}
