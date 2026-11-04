from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.model.config import ModelProfileSpec
from eidos_runtime.model.native_custom import OpenAIResponsesModelClient
from eidos_runtime.model.reasoning_summary import emit_reasoning_summary, reasoning_summary_sink
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.thinking_notifier import (
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    ThinkingSummaryNotifier,
    split_summary,
)
from eidos_runtime.sandbox.sensitive import default_scanner


def test_split_summary_waits_for_a_complete_title_then_separates_body():
    assert split_summary("**Running the prep", final=False) is None
    assert split_summary("Plain first line", final=False) is None
    assert split_summary("**Check skills**\n\nList the directory first.", final=False) == (
        "Check skills",
        "List the directory first.",
    )
    assert split_summary("Plain title\nbody text", final=False) == ("Plain title", "body text")
    # A provider that never closes the title still surfaces at the end.
    assert split_summary("**Unclosed", final=True) == ("Unclosed", "")


def test_split_summary_bounds_title_and_body():
    title, body = split_summary(f"**{'t' * 500}**\n\n{'b' * 5000}", final=True)
    assert len(title) <= MAX_TITLE_CHARS and title.endswith("…")
    assert len(body) <= MAX_BODY_CHARS and body.endswith("…")


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _notifier(clock: _Clock | None = None):
    sent: list[dict[str, object]] = []
    events = RuntimeEvents(sent.append)
    clock = clock or _Clock()
    return ThinkingSummaryNotifier(events, default_scanner(), "s1", "r1", clock), sent, clock


def test_notifier_publishes_a_live_only_notification_without_an_event_id():
    notifier, sent, _ = _notifier()
    notifier.receive("rs_1:0", "**Check skills**\n\nList first.", False)
    assert sent == [{
        "jsonrpc": "2.0",
        "method": "run/thinking",
        "params": {"sessionId": "s1", "runId": "r1", "summaryId": "rs_1:0", "title": "Check skills", "body": "List first."},
    }]


def test_notifier_throttles_growth_of_one_part_but_never_drops_the_final_text_or_a_new_part():
    notifier, sent, clock = _notifier()
    notifier.receive("rs_1:0", "**Title**\n\nOne", False)
    clock.now += 0.05
    notifier.receive("rs_1:0", "**Title**\n\nOne two", False)
    assert len(sent) == 1
    notifier.receive("rs_1:0", "**Title**\n\nOne two three", True)
    assert sent[-1]["params"]["body"] == "One two three"
    clock.now += 0.01
    notifier.receive("rs_1:1", "**Next**", False)
    assert sent[-1]["params"]["summaryId"] == "rs_1:1"
    count = len(sent)
    notifier.receive("rs_1:1", "**Next**", True)
    assert len(sent) == count


def test_notifier_redacts_credentials_and_survives_a_broken_output():
    notifier, sent, _ = _notifier()
    notifier.receive("rs_1:0", "**Use token**\n\nAuthorization: Bearer abcdefghijklmnop1234567890", True)
    assert "abcdefghijklmnop1234567890" not in str(sent)

    def broken(_: dict[str, object]) -> None:
        raise BrokenPipeError

    ThinkingSummaryNotifier(RuntimeEvents(broken), default_scanner(), "s", "r").receive("x:0", "**T**", True)


def test_sink_is_scoped_and_absent_sinks_are_ignored():
    seen: list[tuple[str, str, bool]] = []
    emit_reasoning_summary("a", "ignored")
    with reasoning_summary_sink(lambda *args: seen.append(args)):
        emit_reasoning_summary("a", "kept", final=True)
    emit_reasoning_summary("a", "ignored")
    assert seen == [("a", "kept", True, False)]


def _client(create, effort: str | None):
    kernel = RuntimeAsyncKernel()
    kernel.start()
    kwargs = dict(
        provider_id="custom", model_id="fixture", wire_api="openai_responses",
        context_window_tokens=4096, max_output_tokens=512, request_timeout_seconds=5,
    )
    client = OpenAIResponsesModelClient(
        ModelProfileSpec(**kwargs),
        openai_client=SimpleNamespace(responses=SimpleNamespace(create=create)),
        retry_transport=None,
        profile_snapshot=ModelProfileSnapshot(
            **kwargs, supports_tools=True, supports_json_schema_output=False, supports_reasoning=True,
        ),
        async_kernel=kernel,
        reasoning_effort=effort,
    )
    return client, kernel


@pytest.mark.parametrize(("effort", "expected"), [
    ("medium", {"effort": "medium", "summary": "auto"}),
    ("none", {"effort": "none"}),
    (None, None),
])
def test_responses_request_asks_for_a_summary_only_when_reasoning_is_on(effort, expected):
    captured: list[dict[str, object]] = []

    def create(**kwargs):
        captured.append(kwargs)
        return {"status": "completed", "model": "fixture", "output": []}

    client, kernel = _client(create, effort)
    try:
        client.complete(({"type": "user", "content": "hi"},), threading.Event(), lambda _: None,
                        instructions="x", allow_tools=False, max_output_tokens=64)
    finally:
        client.close()
        kernel.close()
    assert captured[0].get("reasoning") == expected


def test_responses_stream_forwards_only_summary_events_to_the_sink():
    events = [
        SimpleNamespace(type="response.reasoning_summary_part.added", item_id="rs_1", summary_index=0),
        SimpleNamespace(type="response.reasoning_summary_text.delta", item_id="rs_1", summary_index=0, delta="**Check "),
        SimpleNamespace(type="response.reasoning_summary_text.delta", item_id="rs_1", summary_index=0, delta="skills**"),
        SimpleNamespace(type="response.reasoning_summary_text.done", item_id="rs_1", summary_index=0, text="**Check skills**\n\nList first."),
        # Raw reasoning text is never a summary and must not reach the sink.
        SimpleNamespace(type="response.reasoning_text.delta", item_id="rs_1", content_index=0, delta="private thought"),
        SimpleNamespace(type="response.output_text.delta", delta="answer"),
        SimpleNamespace(type="response.completed", response={"status": "completed", "model": "fixture", "output": []}),
    ]

    async def stream():
        for event in events:
            yield event

    seen: list[tuple[str, str, bool]] = []
    text: list[str] = []
    client, kernel = _client(lambda **_: stream(), "high")
    try:
        with reasoning_summary_sink(lambda *args: seen.append(args)):
            client.complete(({"type": "user", "content": "hi"},), threading.Event(), text.append,
                            instructions="x", allow_tools=False, max_output_tokens=64)
    finally:
        client.close()
        kernel.close()
    assert seen == [
        ("rs_1:0", "**Check ", False, False),
        ("rs_1:0", "**Check skills**", False, False),
        ("rs_1:0", "**Check skills**\n\nList first.", True, False),
    ]
    assert "".join(text) == "answer"


def test_engine_publishes_summary_live_and_persists_nothing(tmp_path):
    from eidos_runtime.db.storage import SessionStore
    from eidos_runtime.model.client import ModelResponse, ScriptedModel
    from eidos_runtime.runtime.engine import RuntimeEngine

    class SummarisingModel(ScriptedModel):
        def complete(self, context, cancel, on_text_delta, **kwargs):
            emit_reasoning_summary("rs_1:0", "**Check skills**\n\nList first.", final=True)
            return super().complete(context, cancel, on_text_delta, **kwargs)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    notifications: list[dict[str, object]] = []
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "hello")
        RuntimeEngine(store, SummarisingModel([ModelResponse(text="done")]), notifications.append).run(
            run["id"], threading.Event()
        )
        thinking = [n for n in notifications if n.get("method") == "run/thinking"]
        assert [n["params"] for n in thinking] == [{
            "sessionId": session["id"], "runId": run["id"], "summaryId": "rs_1:0",
            "title": "Check skills", "body": "List first.",
        }]
        fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/run-thinking-v1.json").read_text())
        assert set(thinking[0]["params"]) == set(fixture["notification"]["params"])
        # Not an Event: no stored row mentions the summary.
        dump = repr(store.connection.execute("SELECT * FROM events").fetchall())
        assert "Check skills" not in dump
        assert "Check skills" not in repr(store.read_session_snapshot(session["id"]))
    finally:
        store.close()


def test_raw_reasoning_uses_its_first_sentence_as_the_title():
    assert split_summary("先看看技能目录。再对比", final=False, raw=True) == ("先看看技能目录", "再对比")
    assert split_summary("Look at a.ts first. Then compare", final=False, raw=True) == ("Look at a.ts first", "Then compare")
    # No sentence end yet: wait, but never wait past the title limit.
    assert split_summary("先看看技能", final=False, raw=True) is None
    assert split_summary("先看看技能", final=True, raw=True) == ("先看看技能", "")
    title, body = split_summary("x" * 200, final=False, raw=True)
    assert len(title) <= MAX_TITLE_CHARS and body


def test_chat_completions_reasoning_reaches_the_sink_as_raw_text_and_stays_out_of_the_response():
    from pydantic_ai.messages import (
        PartDeltaEvent, PartStartEvent, TextPart, ThinkingPart, ThinkingPartDelta,
    )
    from eidos_runtime.model.pydantic_ai_client import _RawThinkingForwarder

    forwarder = _RawThinkingForwarder()
    first = forwarder.observe(PartStartEvent(index=0, part=ThinkingPart("先看目录。")))
    assert first and first[0][1] == "先看目录。"
    # Within the interval nothing is offered, but the text keeps accumulating.
    assert forwarder.observe(PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="再比较"))) == []
    assert forwarder.observe(PartStartEvent(index=1, part=TextPart("answer"))) == []
    final = forwarder.finish()
    assert [text for _, text in final] == ["先看目录。再比较"]
    seen: list[tuple[str, str, bool, bool]] = []
    with reasoning_summary_sink(lambda *args: seen.append(args)):
        forwarder.emit(final, True)
    assert seen == [(final[0][0], "先看目录。再比较", True, True)]
