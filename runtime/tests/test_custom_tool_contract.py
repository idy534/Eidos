from __future__ import annotations

import asyncio
from pathlib import Path
import json
import threading
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from eidos_runtime.model.client import (
    CustomToolDefinition,
    CustomToolFormat,
    CustomToolPayload,
    FunctionToolDefinition,
    FunctionToolPayload,
    ModelProfileSnapshot,
    ModelResponse,
    ModelToolCall,
)
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.tools.registry import (
    ToolProvenance,
    ToolRegistry,
    ToolRegistryEntry,
    ToolSpec,
)
from eidos_runtime.tools.workspace import ToolExecutor
from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.config import ModelProfileSpec
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.loop_guard import tool_call_fingerprint
from eidos_runtime.workspace.codex_patch import MAX_PATCH_BYTES, patch_grammar


RESULT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class EchoAdapter:
    def execute(
        self, arguments: dict[str, object], _cancel: threading.Event
    ) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "echo",
            "outcome": "success",
            "code": "ok",
            "summary": "ok",
            "data": arguments,
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }


def _provenance() -> ToolProvenance:
    return ToolProvenance.model_validate({
        "kind": "builtin",
        "sourceId": "test",
        "sourceVersion": "1",
        "contentHash": "a" * 64,
    })


def _custom_entry() -> ToolRegistryEntry:
    return ToolRegistryEntry(
        ToolSpec.model_validate({
            "name": "echo",
            "description": "Accept raw text.",
            "sideEffect": "none",
            "approvalRequired": False,
            "timeoutSeconds": 5,
            "batchPolicy": "single",
            "visibility": "direct",
            "inputKind": "custom",
            "inputFormat": {
                "type": "grammar",
                "syntax": "lark",
                "definition": "start: /.+/",
            },
            "resultSchema": RESULT_SCHEMA,
        }),
        _provenance(),
        EchoAdapter(),
    )


def test_function_and_custom_model_contracts_are_closed_and_frozen() -> None:
    function = FunctionToolDefinition(
        name="read_file",
        description="Read a file.",
        parameters_json_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    custom = CustomToolDefinition(
        name="apply_patch",
        description="Use raw patch text.",
        format=CustomToolFormat(
            type="grammar", syntax="lark", definition="start: /.+/"
        ),
    )
    function_payload = FunctionToolPayload(arguments={"path": "a.txt"})
    custom_payload = CustomToolPayload(input='*** Begin Patch\n"\\界\n')

    assert function.kind == "function"
    assert custom.kind == "custom"
    assert function_payload.kind == "function"
    assert custom_payload.kind == "custom"
    assert ModelToolCall("c1", "apply_patch", custom_payload).payload == custom_payload
    with pytest.raises((ValidationError, TypeError)):
        custom.name = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        CustomToolDefinition.model_validate({
            **custom.model_dump(),
            "unexpected": True,
        })
    with pytest.raises(AttributeError):
        _ = ModelToolCall("c1", "apply_patch", custom_payload).arguments


def test_bare_dict_with_custom_shape_is_still_a_function_payload() -> None:
    call = ModelToolCall(
        "c1",
        "apply_patch",
        {"kind": "custom", "input": "hello"},
    )

    assert isinstance(call.payload, FunctionToolPayload)
    assert call.payload_kind == "function"
    assert call.arguments == {"kind": "custom", "input": "hello"}


def test_tool_spec_enforces_function_and_custom_input_invariants() -> None:
    with pytest.raises(ValidationError):
        ToolSpec.model_validate({
            "name": "bad",
            "description": "bad",
            "sideEffect": "none",
            "approvalRequired": False,
            "timeoutSeconds": 5,
            "resultSchema": RESULT_SCHEMA,
        })
    with pytest.raises(ValidationError):
        ToolSpec.model_validate({
            "name": "bad",
            "description": "bad",
            "sideEffect": "none",
            "approvalRequired": False,
            "timeoutSeconds": 5,
            "inputKind": "custom",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "resultSchema": RESULT_SCHEMA,
        })


def test_custom_registry_definition_fingerprint_and_budget_include_grammar() -> None:
    first = _custom_entry()
    second = ToolRegistryEntry(
        first.spec.model_copy(update={
            "input_format": first.spec.input_format.model_copy(
                update={"definition": "start: /.+/ /x/"}
            )
        }),
        first.provenance,
        first.adapter,
    )
    assert first.contract_fingerprint != second.contract_fingerprint
    registry = ToolRegistry((first,))
    definition = registry.model_definitions()[0]
    assert isinstance(definition, CustomToolDefinition)
    assert definition.format is not None
    assert definition.format.definition == "start: /.+/"

    oversized = ToolRegistryEntry(
        first.spec.model_copy(update={
            "input_format": first.spec.input_format.model_copy(
                update={"definition": "x" * (32 * 1024)}
            )
        }),
        first.provenance,
        first.adapter,
    )
    with pytest.raises(ValueError, match="tool_definition_too_large"):
        ToolRegistry((oversized,))


def test_dispatcher_keeps_custom_input_raw_and_rejects_function_payload() -> None:
    entry = _custom_entry()
    dispatcher = ToolDispatcher(ToolRegistry((entry,)))
    raw = 'line 1 "quoted" \\ 界\nline 2'
    response = dispatcher.validate(ModelResponse(
        tool_calls=(ModelToolCall(
            "c1", "echo", CustomToolPayload(input=raw)
        ),),
    ))

    assert response.error_code is None
    assert response.tool_calls[0].payload == CustomToolPayload(input=raw)
    invalid = dispatcher.validate(ModelResponse(
        tool_calls=(ModelToolCall(
            "c2", "echo", FunctionToolPayload(arguments={"input": raw})
        ),),
    ))
    assert invalid.error_code == "TOOL_ARGUMENT_CONTRACT_VIOLATION"

    oversized = dispatcher.validate(ModelResponse(
        tool_calls=(ModelToolCall(
            "c3", "echo", CustomToolPayload(input="x" * (MAX_PATCH_BYTES + 1))
        ),),
    ))
    assert oversized.error_code is None
    assert oversized.protocol_diagnostic is None
    assert oversized.tool_calls[0].payload == CustomToolPayload(
        input="x" * (MAX_PATCH_BYTES + 1)
    )
    plan = dispatcher.plan(response.tool_calls[0])
    assert dispatcher.validate_execution(response.tool_calls[0], plan)


def test_native_apply_patch_preparation_does_not_encode_raw_patch(tmp_path: Path) -> None:
    raw = (
        "*** Begin Patch\n"
        "*** Add File: quoted.txt\n"
        "+quoted \"value\" \\\n"
        "+\u754c\n"
        "*** End Patch\n"
    )
    with ToolExecutor(
        tmp_path,
        supports_custom_tools=True,
        supports_tool_grammar=True,
    ) as executor:
        with patch(
            "eidos_runtime.tools.workspace.encode_patch",
            side_effect=AssertionError("native custom path encoded a patch"),
        ):
            prepared = executor.prepare_file_change(
                "apply_patch", raw, threading.Event()
            )
        assert not isinstance(prepared, dict), prepared
        result, _ = executor.commit_patch(
            "apply_patch", prepared, threading.Event()
        )

    assert result["outcome"] == "success"
    assert (tmp_path / "quoted.txt").read_text() == 'quoted "value" \\\n界\n'


def test_apply_patch_capability_routing_keeps_legacy_function_fallback(
    tmp_path: Path,
) -> None:
    with ToolExecutor(tmp_path) as legacy:
        legacy_spec = legacy.registry.get("apply_patch")
        assert legacy_spec is not None
        assert legacy_spec.spec.input_kind == "function"
        assert legacy_spec.spec.input_schema is not None
    with ToolExecutor(
        tmp_path,
        supports_custom_tools=True,
        supports_tool_grammar=True,
    ) as native:
        native_spec = native.registry.get("apply_patch")
        assert native_spec is not None
        assert native_spec.spec.input_kind == "custom"
        assert native_spec.spec.input_schema is None
        assert native_spec.spec.input_format is not None
        assert native_spec.spec.input_format.definition == patch_grammar()


def test_responses_custom_wire_keeps_grammar_and_raw_input() -> None:
    from eidos_runtime.model_gateway.native_custom import (
        encode_responses_context,
        encode_responses_tool_definition,
        map_responses_response,
    )

    definition = CustomToolDefinition(
        name="apply_patch",
        description="This is a FREEFORM tool.",
        format=CustomToolFormat(
            type="grammar", syntax="lark", definition="start: /.+/"
        ),
    )
    wire = encode_responses_tool_definition(definition)
    raw = '*** Begin Patch\n+quoted "\\界"\n*** End Patch'
    response = SimpleNamespace(
        output=(SimpleNamespace(
            type="custom_tool_call",
            call_id="call-1",
            name="apply_patch",
            input=raw,
        ),),
        output_text="",
        id="resp-1",
        model="gpt-test",
        status="completed",
        usage=None,
    )

    assert wire == {
        "type": "custom",
        "name": "apply_patch",
        "description": "This is a FREEFORM tool.",
        "format": {
            "type": "grammar",
            "syntax": "lark",
            "definition": "start: /.+/",
        },
    }
    mapped = map_responses_response(response)
    assert mapped.tool_calls[0].payload == CustomToolPayload(input=raw)
    assert mapped.tool_calls[0].payload.input == raw
    assert encode_responses_context((
        {
            "type": "tool_call",
            "callId": "call-1",
            "name": "apply_patch",
            "payloadKind": "custom",
            "input": raw,
        },
        {
            "type": "tool_result",
            "callId": "call-1",
            "name": "apply_patch",
            "payloadKind": "custom",
            "result": "{\"outcome\":\"success\"}",
        },
    )) == [
        {
            "type": "custom_tool_call",
            "call_id": "call-1",
            "name": "apply_patch",
            "input": raw,
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call-1",
            "output": "{\"outcome\":\"success\"}",
        },
    ]


def test_responses_wire_keeps_function_compatibility_without_custom_capability() -> None:
    from eidos_runtime.model_gateway.native_custom import OpenAIResponsesModelClient

    class FakeResponses:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        async def create(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            return SimpleNamespace(
                output=(), output_text="", id="resp-1", model="gpt-test",
                status="completed", usage=None,
            )

    class FakeClient:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    kernel = RuntimeAsyncKernel()
    kernel.start()
    client = FakeClient()
    profile = ModelProfileSpec(
        provider_id="openai",
        model_id="gpt-test",
        wire_api="openai_responses",
        context_window_tokens=8_192,
        max_output_tokens=512,
        request_timeout_seconds=5.0,
    )
    snapshot = ModelProfileSnapshot(
        provider_id="openai",
        model_id="gpt-test",
        wire_api="openai_responses",
        context_window_tokens=8_192,
        max_output_tokens=512,
        request_timeout_seconds=5.0,
        supports_tools=True,
        supports_json_schema_output=False,
        supports_reasoning=False,
    )
    model = OpenAIResponsesModelClient(
        profile,
        openai_client=client,
        retry_transport=None,
        profile_snapshot=snapshot,
        async_kernel=kernel,
    )
    try:
        model.complete(
            ({"type": "user", "content": "read"},),
            threading.Event(),
            lambda _delta: None,
            instructions="instructions",
            tool_definitions=(FunctionToolDefinition(
                name="read_file",
                description="Read a file.",
                parameters_json_schema={"type": "object"},
            ),),
        )
    finally:
        model.close()
        kernel.close()

    assert client.responses.kwargs is not None
    sent_tools = client.responses.kwargs["tools"]
    assert isinstance(sent_tools, list)
    assert sent_tools[0]["type"] == "function"
    assert client.responses.kwargs["store"] is False


def test_pydantic_ai_boundary_does_not_downgrade_custom_definition() -> None:
    from eidos_runtime.model.pydantic_ai_client import encode_tool_definition

    with pytest.raises(ValueError, match="custom_tools_require_native_transport"):
        encode_tool_definition(CustomToolDefinition(
            name="apply_patch",
            description="FREEFORM",
            format=CustomToolFormat(
                type="grammar", syntax="lark", definition="start: /.+/"
            ),
        ))


def test_responses_client_sends_native_custom_tool_to_mock_provider() -> None:
    from eidos_runtime.model_gateway.native_custom import OpenAIResponsesModelClient

    raw = '*** Begin Patch\n+quoted "\\界"\n*** End Patch'

    class FakeResponses:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        async def create(self, **kwargs: object) -> object:
            self.kwargs = kwargs
            return SimpleNamespace(
                output=(SimpleNamespace(
                    type="custom_tool_call",
                    call_id="call-1",
                    name="apply_patch",
                    input=raw,
                ),),
                output_text="",
                id="resp-1",
                model="gpt-test",
                status="completed",
                usage=None,
            )

    class FakeClient:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    kernel = RuntimeAsyncKernel()
    kernel.start()
    client = FakeClient()
    profile = ModelProfileSpec(
        provider_id="openai",
        model_id="gpt-test",
        wire_api="openai_responses",
        context_window_tokens=8_192,
        max_output_tokens=512,
        request_timeout_seconds=5.0,
        supports_custom_tools=True,
        supports_tool_grammar=True,
    )
    snapshot = ModelProfileSnapshot(
        provider_id="openai",
        model_id="gpt-test",
        wire_api="openai_responses",
        context_window_tokens=8_192,
        max_output_tokens=512,
        request_timeout_seconds=5.0,
        supports_tools=True,
        supports_json_schema_output=False,
        supports_reasoning=False,
        supports_custom_tools=True,
        supports_tool_grammar=True,
    )
    model = OpenAIResponsesModelClient(
        profile,
        openai_client=client,
        retry_transport=None,
        profile_snapshot=snapshot,
        async_kernel=kernel,
    )
    try:
        response = model.complete(
            ({"type": "user", "content": "edit"},),
            threading.Event(),
            lambda _delta: None,
            instructions="instructions",
            tool_definitions=(CustomToolDefinition(
                name="apply_patch",
                description="This is a FREEFORM tool.",
                format=CustomToolFormat(
                    type="grammar", syntax="lark", definition="start: /.+/"
                ),
            ),),
        )
    finally:
        model.close()
        kernel.close()

    assert client.responses.kwargs is not None
    sent_tools = client.responses.kwargs["tools"]
    assert isinstance(sent_tools, list)
    assert sent_tools[0]["type"] == "custom"
    assert sent_tools[0]["format"]["syntax"] == "lark"
    assert response.tool_calls[0].payload == CustomToolPayload(input=raw)


def test_responses_stream_reassembles_custom_input_without_json_encoding() -> None:
    from eidos_runtime.model_gateway.native_custom import (
        OpenAIResponsesModelClient,
        map_responses_response,
    )

    raw = '*** Begin Patch\n+quoted "\\界"\n*** End Patch'

    class FakeStream:
        def __init__(self) -> None:
            self.events = (
                SimpleNamespace(
                    type="response.custom_tool_call_input.delta",
                    item_id="item-1",
                    delta=raw[:17],
                ),
                SimpleNamespace(
                    type="response.custom_tool_call_input.delta",
                    item_id="item-1",
                    delta=raw[17:],
                ),
                SimpleNamespace(
                    type="response.output_item.done",
                    item=SimpleNamespace(
                        type="custom_tool_call",
                        id="item-1",
                        call_id="call-1",
                        name="apply_patch",
                        input="",
                    ),
                ),
                SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        output=(SimpleNamespace(
                            type="custom_tool_call",
                            id="item-1",
                            call_id="call-1",
                            name="apply_patch",
                            input="",
                        ),),
                        output_text="",
                        id="resp-1",
                        model="gpt-test",
                        status="completed",
                        usage=None,
                    ),
                ),
            )

        def __aiter__(self):
            self.index = 0
            return self

        async def __anext__(self):
            if self.index >= len(self.events):
                raise StopAsyncIteration
            event = self.events[self.index]
            self.index += 1
            return event

    class FakeResponses:
        async def create(self, **_kwargs: object) -> object:
            return FakeStream()

    class FakeClient:
        responses = FakeResponses()

    kernel = RuntimeAsyncKernel()
    kernel.start()
    profile = ModelProfileSpec(
        provider_id="openai",
        model_id="gpt-test",
        wire_api="openai_responses",
        context_window_tokens=8_192,
        max_output_tokens=512,
        request_timeout_seconds=5.0,
        supports_custom_tools=True,
        supports_tool_grammar=True,
    )
    snapshot = ModelProfileSnapshot(
        provider_id="openai",
        model_id="gpt-test",
        wire_api="openai_responses",
        context_window_tokens=8_192,
        max_output_tokens=512,
        request_timeout_seconds=5.0,
        supports_tools=True,
        supports_json_schema_output=False,
        supports_reasoning=False,
        supports_custom_tools=True,
        supports_tool_grammar=True,
    )
    model = OpenAIResponsesModelClient(
        profile,
        openai_client=FakeClient(),
        retry_transport=None,
        profile_snapshot=snapshot,
        async_kernel=kernel,
    )
    try:
        response = model.complete(
            (), threading.Event(), lambda _delta: None,
            instructions="instructions",
        )
    finally:
        model.close()
        kernel.close()

    assert response.tool_calls[0].payload == CustomToolPayload(input=raw)
    empty = map_responses_response(SimpleNamespace(
        output=(SimpleNamespace(
            type="custom_tool_call",
            call_id="call-empty",
            name="custom",
            input="",
        ),),
        output_text="",
        id="resp-empty",
        model="gpt-test",
        status="completed",
        usage=None,
    ))
    assert empty.tool_calls[0].payload == CustomToolPayload(input="")


def test_responses_stream_requires_a_completed_terminal_event() -> None:
    from eidos_runtime.model.client import ModelRequestError
    from eidos_runtime.model_gateway.native_custom import (
        OpenAIResponsesModelClient,
    )

    class FakeStream:
        def __init__(self, events: tuple[object, ...]) -> None:
            self.events = events
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.index >= len(self.events):
                raise StopAsyncIteration
            event = self.events[self.index]
            self.index += 1
            return event

    client = OpenAIResponsesModelClient.__new__(OpenAIResponsesModelClient)
    output_item_done = SimpleNamespace(
        type="response.output_item.done", item=SimpleNamespace()
    )
    cases = (
        (output_item_done, SimpleNamespace(type="response.failed")),
        (output_item_done, SimpleNamespace(type="response.incomplete")),
        (
            SimpleNamespace(type="response.output_text.delta", delta="partial"),
            SimpleNamespace(type="error"),
        ),
        (SimpleNamespace(type="response.output_item.done", item=SimpleNamespace()),),
    )
    for events in cases:
        with pytest.raises(ModelRequestError) as raised:
            asyncio.run(client._consume_stream(
                FakeStream(events), threading.Event(), lambda _delta: None
            ))
        assert raised.value.failure.code == "protocol_error"


def test_responses_stream_cancel_before_request_is_not_sent() -> None:
    from eidos_runtime.model.client import ModelRequestError
    from eidos_runtime.model_gateway.native_custom import OpenAIResponsesModelClient

    class FakeResponses:
        calls = 0

        async def create(self, **_kwargs: object) -> object:
            self.calls += 1
            raise AssertionError("canceled request was sent")

    class FakeClient:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    kernel = RuntimeAsyncKernel()
    kernel.start()
    client = FakeClient()
    profile = ModelProfileSpec(
        provider_id="openai",
        model_id="gpt-test",
        wire_api="openai_responses",
        context_window_tokens=8_192,
        max_output_tokens=512,
        request_timeout_seconds=5.0,
    )
    snapshot = ModelProfileSnapshot(
        provider_id="openai",
        model_id="gpt-test",
        wire_api="openai_responses",
        context_window_tokens=8_192,
        max_output_tokens=512,
        request_timeout_seconds=5.0,
        supports_tools=True,
        supports_json_schema_output=False,
        supports_reasoning=False,
    )
    model = OpenAIResponsesModelClient(
        profile,
        openai_client=client,
        retry_transport=None,
        profile_snapshot=snapshot,
        async_kernel=kernel,
    )
    cancel = threading.Event()
    cancel.set()
    try:
        with pytest.raises(ModelRequestError) as raised:
            model.complete(
                (), cancel, lambda _delta: None, instructions="instructions"
            )
    finally:
        model.close()
        kernel.close()
    assert raised.value.failure.code == "sampling_canceled"
    assert client.responses.calls == 0


def test_responses_stream_cancel_closes_and_cleans_blocking_anext() -> None:
    from eidos_runtime.model.client import ModelRequestError
    from eidos_runtime.model_gateway.native_custom import OpenAIResponsesModelClient

    async def run() -> None:
        class BlockingStream:
            def __init__(self, first: object | None = None) -> None:
                self.first = first
                self.calls = 0
                self.closed = asyncio.Event()

            def __aiter__(self):
                return self

            async def __anext__(self):
                self.calls += 1
                if self.calls == 1 and self.first is not None:
                    return self.first
                await asyncio.Future()
                raise AssertionError("unreachable")

            async def aclose(self) -> None:
                self.closed.set()

        client = OpenAIResponsesModelClient.__new__(OpenAIResponsesModelClient)
        for first in (
            None,
            SimpleNamespace(type="response.output_text.delta", delta="partial"),
        ):
            stream = BlockingStream(first)
            cancel = threading.Event()
            task = asyncio.create_task(client._consume_stream(
                stream, cancel, lambda _delta: None
            ))
            await asyncio.sleep(0.02)
            cancel.set()
            with pytest.raises(ModelRequestError) as raised:
                await asyncio.wait_for(task, timeout=1.0)
            assert raised.value.failure.code == "sampling_canceled"
            assert stream.closed.is_set()
            assert task.done()
            assert not any(
                pending is not asyncio.current_task()
                and not pending.done()
                for pending in asyncio.all_tasks()
            )

    asyncio.run(run())


def test_responses_direct_noncompleted_status_is_not_executable() -> None:
    from eidos_runtime.model.client import ModelRequestError
    from eidos_runtime.model_gateway.native_custom import map_responses_response

    for status in ("failed", "incomplete"):
        with pytest.raises(ModelRequestError) as raised:
            map_responses_response(SimpleNamespace(
                output=(SimpleNamespace(
                    type="function_call",
                    call_id="call-1",
                    name="apply_patch",
                    arguments="{}",
                ),),
                output_text="",
                id="resp-1",
                model="gpt-test",
                status=status,
                usage=None,
            ))
        assert raised.value.failure.code == "protocol_error"


def test_custom_tool_call_fingerprint_hashes_raw_utf8_input() -> None:
    first = ModelToolCall("c1", "apply_patch", CustomToolPayload(input="界"))
    second = ModelToolCall("c1", "apply_patch", CustomToolPayload(input="界\n"))

    assert tool_call_fingerprint((first,)) != tool_call_fingerprint((second,))


def test_context_replay_preserves_custom_kind_call_id_and_raw_input() -> None:
    with tempfile.TemporaryDirectory(prefix="eidos-custom-context-") as root:
        root_path = Path(root)
        data = root_path / "data"
        workspace = root_path / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        store = SessionStore(data)
        store.initialize()
        try:
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"],
                "edit",
                model_id="test",
                model_profile=ModelProfileSnapshot(
                    provider_id="test",
                    model_id="test",
                    context_window_tokens=8_192,
                    max_output_tokens=512,
                    request_timeout_seconds=5.0,
                    supports_tools=True,
                    supports_json_schema_output=False,
                    supports_reasoning=False,
                    supports_custom_tools=True,
                    supports_tool_grammar=True,
                ),
            )
            raw = '*** Begin Patch\n+quoted "\\界"\n*** End Patch'
            mutation = store.create_tool_item_committed(
                run["id"],
                0,
                0,
                "call-raw",
                "apply_patch",
                json.dumps({"kind": "custom", "input": raw}, ensure_ascii=False),
                payload_kind="custom",
            )
            assert mutation.value["toolCall"]["payloadKind"] == "custom"
            store.complete_tool_item(
                mutation.value["id"],
                json.dumps({"outcome": "success", "code": "ok"}),
            )

            context = ContextBuilder(store).build(
                run["id"], projectless=True
            ).model_context
        finally:
            store.close()

    call = next(item for item in context if item.get("type") == "tool_call")
    result = next(item for item in context if item.get("type") == "tool_result")
    assert call == {
        "type": "tool_call",
        "callId": "call-raw",
        "name": "apply_patch",
        "payloadKind": "custom",
        "input": raw,
    }
    assert result["payloadKind"] == "custom"
