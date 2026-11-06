"""One event loop per owned worker; each request uses a fresh SDK session."""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import signal
import sys
import tempfile

import anyio
from pydantic import ValidationError

from qoder_agent_sdk import (
    AssistantMessage, HookMatcher, PermissionResultDeny, QoderAgentOptions,
    QoderSDKClient, ResultMessage, StreamEvent, create_sdk_mcp_server, qodercli_auth, tool,
)

from .protocol import (
    CatalogModel, MAX_FRAME_BYTES, MAX_TEXT_BYTES, WorkerCall,
    WorkerCatalog, WorkerError, WorkerEvent, WorkerLogin, WorkerRequest,
    WorkerResult, WorkerText,
)


class BoundaryError(Exception):
    pass


def emit(request: WorkerRequest, payload) -> None:
    data = WorkerEvent(request_id=request.request_id, payload=payload).model_dump_json().encode() + b"\n"
    if len(data) > MAX_FRAME_BYTES:
        raise BoundaryError("worker_frame_too_large")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def error_payload(error: Exception) -> WorkerError:
    # Third-party messages can contain credentials or response bodies. Only a
    # stable classification is allowed across the process boundary.
    if isinstance(error, ValidationError):
        return WorkerError(code="protocol_error", reason="qoder_contract_invalid")
    code = str(getattr(error, "code", "")).lower()
    text = (type(error).__name__ + " " + str(error)).lower()[:8192]
    if "quota" in text or "credit" in text or "402" in text:
        return WorkerError(code="invalid_request", reason="insufficient_quota")
    if "auth" in text or "login" in text or "401" in text or "403" in text:
        return WorkerError(code="authentication_failed", reason="qoder_login_required")
    if "429" in text or "rate_limit" in code:
        return WorkerError(code="rate_limited", reason="qoder_rate_limited")
    if "context" in text and any(x in text for x in ("exceed", "length", "window", "compact")):
        return WorkerError(code="context_exceeded", reason="qoder_context_exceeded")
    if isinstance(error, TimeoutError):
        return WorkerError(code="provider_timeout", reason="qoder_timeout")
    if isinstance(error, BoundaryError):
        return WorkerError(code="protocol_error", reason=str(error)[:128])
    return WorkerError(code="provider_unavailable", reason="qoder_sdk_failed")


async def settle_sdk_tasks(children: list[asyncio.Task]) -> bool:
    """Retrieve SDK child exceptions before the next request starts.

    SDK 1.0.17 cancels process-mode callback tasks during disconnect without
    joining them. This worker owns the loop, so it can join all newly created
    children after disconnect. It never suppresses an exception as success.
    """
    children = [child for child in children if child is not asyncio.current_task()]
    for child in children:
        child.cancel()
    if not children:
        return True
    results = await asyncio.gather(*children, return_exceptions=True)
    return all(not isinstance(result, Exception) for result in results)


def catalog_entry(model):
    context = model.get("context_config") or {}
    windows = tuple(v["token_count"] for v in context.values() if isinstance(v.get("token_count"), int) and 8192 < v["token_count"] <= 100000000)
    windows = windows or tuple(v for v in model.get("availableContextWindows", []) if isinstance(v, int) and 8192 < v <= 100000000)
    default = model.get("defaultContextWindow") or next((v.get("token_count") for v in context.values() if v.get("is_default")), None)
    thinking = model.get("thinking_config") or {}
    enabled = thinking.get("enabled") or {}
    efforts = enabled.get("efforts") or {v: {} for v in model.get("efforts", [])}
    selections = tuple(v for v in efforts if v in {"none", "thinking", "low", "medium", "high", "xhigh", "max"})
    if "disabled" in thinking and "none" not in selections:
        selections = ("none", *selections)
    preferred = model.get("defaultEffort") or next((k for k, v in efforts.items() if v.get("is_default")), None)
    if "disabled" in thinking and not enabled.get("is_default"):
        preferred = "none"
    raw = model.get("serverModel") or {}
    images = model.get("isVl", raw.get("is_vl"))
    output = model.get("maxOutputTokens", raw.get("max_output_tokens"))
    return CatalogModel(id=model["value"], name=model.get("displayName") or model["value"], enabled=model.get("isEnabled", True),
        context_windows=windows, default_context_window=default if default in windows else windows[0] if windows else None,
        max_output_tokens=output if type(output) is int and 0 < output <= 131072 else None,
        supports_images=images if isinstance(images, bool) else None, reasoning_selections=selections,
        default_reasoning=preferred if preferred in selections else None)


async def empty_messages():
    for message in ():
        yield message


async def sdk_request(request: WorkerRequest, cli: str, config: Path):
    loop = asyncio.get_running_loop()
    previous_factory = loop.get_task_factory()
    children: list[asyncio.Task] = []

    def factory(loop, coroutine, context=None):
        child = (previous_factory(loop, coroutine, context=context) if previous_factory is not None
                 else asyncio.Task(coroutine, loop=loop, context=context))
        children.append(child)
        return child

    loop.set_task_factory(factory)
    violation = False
    generations = 0
    intercepted: set[str] = set()
    calls: list[WorkerCall] = []
    call_ids: set[str] = set()
    result = None
    streamed = ""
    assistant_text = ""
    allowed = {"mcp__eidos__" + definition.name for definition in request.tools}

    async def forbidden_handler(arguments):
        nonlocal violation
        violation = True
        raise BoundaryError("sdk_tool_execution_blocked")

    async def before_tool(data, call_id, context):
        nonlocal violation
        if data.get("tool_name") not in allowed or not call_id:
            violation = True
        else:
            intercepted.add(call_id)
        return {"continue_": False, "stopReason": "Return tool requests to the Eidos outer loop"}

    async def deny_tool(name, arguments, context):
        nonlocal violation
        violation = True
        return PermissionResultDeny(message="Eidos owns all tool execution", interrupt=True)

    async def resolve(context):
        nonlocal violation, generations
        generations += 1
        if context.get("purpose") != "main" or generations != 1 or request.operation != "sample":
            violation = True
            raise BoundaryError("secondary_generation_blocked")
        available = context.get("availableModels", [])
        if not any(m.get("value") == request.model_id and m.get("isEnabled", True) for m in available):
            violation = True
            raise BoundaryError("selected_model_unavailable")
        parameters = {"contextWindow": request.context_window_tokens}
        if request.reasoning_effort is not None:
            parameters["reasoningEffort"] = request.reasoning_effort
        return {"model": request.model_id, "parameters": parameters}

    definitions = [tool(t.name, t.description, t.schema_)(forbidden_handler) for t in request.tools]
    with tempfile.TemporaryDirectory(prefix="request-", dir=config / "work") as cwd:
        options = QoderAgentOptions(
            auth=qodercli_auth(), cli_path=cli, cwd=cwd,
            env={"QODER_CONFIG_DIR": str(config), "HOME": str(config / "home")},
            system_prompt=request.instructions, tools=[], setting_sources=[], skills=[], plugins=[], memory={},
            strict_mcp_config=True,
            settings={"mcp": {"lazyLoad": False}, "general": {"fileCheckpointing": {"enabled": False}}},
            max_turns=1, include_partial_messages=True, control_request_timeout_ms=20000,
            extra_args={"max-output-tokens": str(request.max_output_tokens), "no-session-persistence": None},
            resolve_model=resolve, resolve_model_timeout_ms=1500, can_use_tool=deny_tool,
            hooks={"PreToolUse": [HookMatcher(hooks=[before_tool])]},
            mcp_servers={"eidos": create_sdk_mcp_server("eidos", tools=definitions)} if definitions else {},
            allowed_tools=sorted(allowed), stderr=lambda _line: None,
        )
        client = QoderSDKClient(options=options)
        try:
            await client.connect()
            if request.operation == "catalog":
                models = await client.get_available_models()
                catalog = WorkerCatalog(models=tuple(catalog_entry(m) for m in models))
            if request.operation != "catalog":
                if request.images:
                    async def multimodal():
                        yield {"type": "user", "message": {"role": "user", "content": [
                            {"type": "text", "text": request.prompt},
                            *(block for image in request.images for block in ({"type": "text", "text": image.get("source", "Attached image")}, {"type": "image", "source": {"type": "base64", "media_type": image["mime"], "data": image["data"]}})),
                        ]}}
                    await client.query(multimodal())
                else:
                    await client.query(request.prompt)
            async for message in client.receive_response() if request.operation != "catalog" else empty_messages():
                if isinstance(message, StreamEvent):
                    event = message.event
                    delta = event.get("delta", {})
                    if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if not isinstance(text, str):
                            raise BoundaryError("invalid_text_delta")
                        streamed += text
                        if len(streamed.encode()) > MAX_TEXT_BYTES:
                            raise BoundaryError("model_output_too_large")
                        for offset in range(0, len(text), 32768):
                            emit(request, WorkerText(text=text[offset:offset + 32768]))
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if type(block).__name__ == "TextBlock":
                            assistant_text += block.text
                            if len(assistant_text.encode()) > MAX_TEXT_BYTES:
                                raise BoundaryError("model_output_too_large")
                        elif type(block).__name__ == "ToolUseBlock":
                            call = WorkerCall(id=block.id, name=block.name, arguments=block.input)
                            if call.id in call_ids or call.name not in allowed:
                                raise BoundaryError("invalid_tool_batch")
                            if len(json.dumps(call.arguments, ensure_ascii=False).encode()) > 65536:
                                raise BoundaryError("tool_arguments_too_large")
                            call_ids.add(call.id)
                            calls.append(call)
                            if len(calls) > 256:
                                raise BoundaryError("tool_batch_too_large")
                elif isinstance(message, ResultMessage):
                    result = message
            # The result is provisional until SDK disconnect and callbacks settle.
        finally:
            try:
                await client.disconnect()
            finally:
                loop.set_task_factory(previous_factory)
                if not await settle_sdk_tasks(children):
                    violation = True
    if request.operation == "catalog" and not violation:
        return catalog
    reported = result.total_credits if result is not None else None
    credits = (reported if isinstance(reported, (int, float)) and math.isfinite(reported) and reported >= 0 else None)
    if violation or generations != 1 or result is None:
        return WorkerError(code="protocol_error", reason="sdk_boundary_violation", credits=credits)
    if result.is_error:
        payload = error_payload(RuntimeError(" ".join(result.errors or [result.subtype])))
        return payload.model_copy(update={"credits": credits})
    if result.num_turns > 1:
        return WorkerError(code="protocol_error", reason="secondary_generation_blocked", credits=credits)
    if calls:
        if result.terminal_reason != "hook_stopped" or result.stop_reason != "tool_use" or call_ids != intercepted:
            return WorkerError(code="protocol_error", reason="unverified_tool_stop", credits=credits)
    elif result.stop_reason != "end_turn" or result.terminal_reason == "hook_stopped":
        return WorkerError(code="protocol_error", reason="incomplete_response", credits=credits)
    if streamed and streamed != assistant_text:
        return WorkerError(code="protocol_error", reason="inconsistent_text_stream", credits=credits)
    if not streamed:
        for offset in range(0, len(assistant_text), 32768):
            emit(request, WorkerText(text=assistant_text[offset:offset + 32768]))
    usage = result.usage or {}
    if reported is not None and credits is None:
        raise BoundaryError("invalid_credits_usage")
    return WorkerResult(calls=tuple(calls), finish_reason="tool_use" if calls else "end_turn", credits=credits,
                        input_tokens=usage.get("input_tokens") or None, output_tokens=usage.get("output_tokens") or None,
                        response_id=result.uuid or result.session_id)


async def login(cli: str, config: Path) -> WorkerLogin:
    process = await asyncio.create_subprocess_exec(cli, "login", env={**os.environ, "QODER_CONFIG_DIR": str(config),
        "HOME": str(config / "home")}, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, cwd=config / "work")
    try:
        with anyio.fail_after(180):
            await process.wait()
        return WorkerLogin(succeeded=process.returncode == 0)
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()


async def main() -> None:
    cli = sys.argv[1]
    config = Path(sys.argv[2])
    config.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(config, 0o700)
    for name in ("work", "home"):
        directory = config / name
        if directory.is_symlink():
            raise BoundaryError("qoder_config_directory_unsafe")
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.stat().st_uid != os.getuid():
            raise BoundaryError("qoder_config_directory_unsafe")
        os.chmod(directory, 0o700)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_FRAME_BYTES)
    transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    current: asyncio.Task | None = None

    def stop() -> None:
        if current is not None:
            current.cancel()
        transport.close()
    loop.add_signal_handler(signal.SIGTERM, stop)
    try:
        while True:
            line = await reader.readline()
            if not line:
                return
            if len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
                raise BoundaryError("worker_frame_too_large")
            request = WorkerRequest.model_validate_json(line)
            current = asyncio.create_task(login(cli, config) if request.operation == "login" else sdk_request(request, cli, config))
            try:
                payload = await current
            except asyncio.CancelledError:
                return
            except Exception as error:
                payload = error_payload(error)
            finally:
                current = None
            emit(request, payload)
    finally:
        transport.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        # Invalid envelopes and startup failures close the channel. Never print
        # a third-party exception, token or user prompt to protocol stdout.
        sys.exit(1)
