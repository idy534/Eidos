"""One text_v1 projection shared by Qoder requests and context accounting."""
from __future__ import annotations

import base64
import json
import re
from typing import Literal, TypedDict

from pydantic_ai.messages import BinaryContent
from eidos_runtime.model.tool_content import encode_tool_content

from eidos_runtime.model.client import FunctionToolDefinition, ModelContextItem, ModelToolDefinitionLike
from eidos_runtime.model.qoder_protocol import WorkerTool

# Transport facts only: task, tool and progress rules live in the shared instructions.
FORMAT_GUIDE = """The next user message is a JSON context envelope (text_v1) that Eidos builds for this transport.
In it, Eidos context arrives as currentGuidance records instead of <system-reminder> blocks.
Records keep their original order and each has a category: currentUserInput is what the user
wrote or pasted for the current task; currentReference is a file or text the user attached to it;
currentGuidance is context Eidos adds, such as saved instructions, memory and environment details;
history is earlier conversation and progress on this task.
requestState.phase says where the task stands: initial means no progress yet; after_tools means
Eidos returned results for your latest tool requests, listed in latestToolResultOrders; continuing
means there is earlier progress but no new result; standalone means there is no current input, so
follow the task in the context.
Each request starts a new SDK session. Continue the task from history instead of restarting its
plan or opening remarks. Tools you request run after this response, and their results arrive in
the next envelope.
"""


class QoderRequestState(TypedDict):
    phase: Literal["initial", "after_tools", "continuing", "standalone"]
    currentInputOrder: int | None
    latestAssistantOrder: int | None
    latestToolResultOrders: list[int]


_MODEL_FIELDS = frozenset({
    "type", "content", "phase", "name", "arguments", "input", "payloadKind", "callId", "result",
    "code", "toolName", "toolCallIndex", "toolCallCount", "validationPath", "validationCode",
    "toolDeclared", "stopReason", "toolsAllowed",
})


# The SDK exposes each tool as mcp__eidos__<name>; MCP names allow these characters
# within 64 bytes. Names with "__" would nest another MCP prefix, so they get aliases.
_REAL_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,52}")


def _tool_aliases(names: tuple[str, ...]) -> dict[str, str]:
    """Use real tool names where the SDK accepts them; opaque aliases confused tool choice."""
    usable = {name for name in names if _REAL_TOOL_NAME.fullmatch(name) and "__" not in name}
    aliases: dict[str, str] = {}
    counter = 0
    for name in names:
        if name in usable:
            aliases[name] = name
            continue
        while f"t{counter:03d}" in usable or f"t{counter:03d}" in aliases:
            counter += 1
        aliases[f"t{counter:03d}"] = name
        counter += 1
    return aliases


def _request_state(context: tuple[ModelContextItem, ...]) -> QoderRequestState:
    current = next((index for index in range(len(context) - 1, -1, -1)
                    if context[index].get("isCurrentInput") is True), None)
    if current is None:
        return {"phase": "standalone", "currentInputOrder": None,
                "latestAssistantOrder": None, "latestToolResultOrders": []}
    assistant = next((index for index in range(len(context) - 1, current, -1)
                      if context[index].get("type") == "assistant"), None)
    start = (assistant if assistant is not None else current) + 1
    latest_call = next((item for item in reversed(context[start:])
                        if item.get("type") == "tool_call"), None)
    response_id = latest_call.get("responseId") if latest_call is not None else None
    call_ids = {item.get("callId") for item in context[start:]
                if item.get("type") == "tool_call" and item.get("responseId") == response_id
                and isinstance(item.get("callId"), str)} if isinstance(response_id, str) else None
    results = [index for index in range(start, len(context))
               if context[index].get("type") == "tool_result"]
    if call_ids is not None:
        results = [index for index in results if context[index].get("callId") in call_ids]
    progressed = any(item.get("type") in {"assistant", "tool_call", "tool_result"}
                     for item in context[current + 1:])
    phase: Literal["initial", "after_tools", "continuing", "standalone"] = (
        "after_tools" if results else "continuing" if progressed else "initial"
    )
    return {"phase": phase, "currentInputOrder": current,
            "latestAssistantOrder": assistant, "latestToolResultOrders": results}


def encode_qoder_context(
    context: tuple[ModelContextItem, ...], instructions: str,
    tools: tuple[ModelToolDefinitionLike, ...], *, required_tool: str | None = None,
) -> tuple[str, str, tuple[WorkerTool, ...], dict[str, str]]:
    definitions = tools if required_tool is None else tuple(t for t in tools if t.name == required_tool)
    if any(not isinstance(t, FunctionToolDefinition) for t in definitions):
        raise ValueError("qoder_custom_tools_unsupported")
    if len({t.name for t in definitions}) != len(definitions):
        raise ValueError("qoder_duplicate_tool_names")
    aliases = _tool_aliases(tuple(tool.name for tool in definitions))
    declarations = tuple(WorkerTool(
        name=alias, description=tool.description if alias == tool.name else f"Eidos tool: {tool.name}\n{tool.description}",
        schema=tool.parameters_json_schema,
    ) for alias, tool in zip(aliases, definitions, strict=True))
    # History names the same callable names the model sees in this request.
    callable_names = {name: alias for alias, name in aliases.items()}
    current = next((index for index in range(len(context) - 1, -1, -1)
                    if context[index].get("isCurrentInput") is True), None)
    current_source = context[current].get("sourceItemId") if current is not None else None
    records = []
    for index, item in enumerate(context):
        # Only model-facing fields: replay state, memory revocation records, hashes
        # and internal identifiers stay out of the prompt, as in the chat adapters.
        value = {k: v for k, v in item.items() if k in _MODEL_FIELDS}
        if item.get("type") in {"tool_call", "tool_result"} and value.get("name") in callable_names:
            value["name"] = callable_names[value["name"]]
        if item.get("toolContent"):
            parts = encode_tool_content(item, "", supports_images=True)
            value["savedTextContent"] = [part for part in parts if isinstance(part, str)]
            value["savedImageCount"] = sum(isinstance(part, BinaryContent) for part in parts)
        if item.get("inputImage"):
            value["imageAttached"] = True
        section = str(item.get("sectionId", ""))
        reference = section.startswith("input-reference:")
        guidance = item.get("type") in {"context", "protocol_error"} or bool(section and not reference)
        kind = (
            "currentUserInput" if index == current
            else "currentReference" if reference and current_source is not None and item.get("sourceItemId") == current_source
            else "currentGuidance" if guidance else "history"
        )
        records.append({"order": index, "category": kind, "item": value})
    prompt = json.dumps({"encoding": "text_v1", "records": records,
                         "requestState": _request_state(context)}, ensure_ascii=False, separators=(",", ":"))
    system = instructions + "\n\n" + FORMAT_GUIDE
    if required_tool is not None:
        if len(declarations) != 1:
            raise ValueError("model_output_tool_invalid")
        system += f"\nReturn exactly one call to mcp__eidos__{declarations[0].name}. This is the required structured output port.\n"
    return system, prompt, declarations, aliases


def qoder_budget_payload(context: tuple[ModelContextItem, ...], instructions: str,
                         tools: tuple[ModelToolDefinitionLike, ...], *, required_tool: str | None = None) -> dict[str, object]:
    system, prompt, declarations, _ = encode_qoder_context(context, instructions, tools, required_tool=required_tool)
    return {"system_prompt": system, "prompt": prompt,
            "tools": [t.model_dump(mode="json", by_alias=True) for t in declarations]}


def qoder_images(context: tuple[ModelContextItem, ...], *, supports_images: bool = True) -> tuple[dict[str, str], ...]:
    images = []
    for index, item in enumerate(context):
        source = f"Image from context record {index}; source item {item.get('sourceItemId', '')}; tool call {item.get('callId', '')}"
        if item.get("inputImage"):
            images.append({"mime": str(item.get("mime")), "data": str(item["inputImage"]), "source": source})
        if item.get("toolContent"):
            for part in encode_tool_content(item, "", supports_images=supports_images):
                if isinstance(part, BinaryContent):
                    images.append({"mime": part.media_type, "data": base64.b64encode(part.data).decode("ascii"), "source": source})
    return tuple(images)
