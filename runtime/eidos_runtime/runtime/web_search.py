from __future__ import annotations

from concurrent.futures import CancelledError
import threading
from typing import TYPE_CHECKING, cast

from eidos_runtime.infrastructure.web_search import PROVIDER_HOSTS
from eidos_runtime.runtime.contracts import RuntimeCancelled
from eidos_runtime.runtime.async_kernel import RuntimeAsyncTask
from eidos_runtime.runtime.tool_execution import PreparedToolExecution, HandlerOutcome
from eidos_runtime.tools.web_search import (
    WebSearchAdapter,
    WebSearchToolRuntime,
    search_error,
)

if TYPE_CHECKING:
    from eidos_runtime.model.client import ModelToolCall
    from eidos_runtime.runtime.tool_runtime import ToolCallRuntime


def invoke_web_search(
    context: ToolCallRuntime,
    runtime: WebSearchToolRuntime,
    run_id: str,
    item: dict[str, object],
    call: ModelToolCall,
    cancel: threading.Event,
) -> HandlerOutcome:
    adapter = cast(WebSearchAdapter, runtime.implementation)
    snapshot = adapter.snapshot
    host = PROVIDER_HOSTS[snapshot.provider]
    count = (
        call.arguments.get("count")
        or snapshot.config.options(snapshot.provider).default_count
    )
    provider_name = (
        "DeepSeek Search" if snapshot.provider == "deepseek" else "LangSearch"
    )
    prepared = runtime.prepare(context, call.arguments, cancel)
    execution = PreparedToolExecution(
        approval_description={
            "kind": "network_access",
            "summary": f"本次搜索使用 {provider_name}，最多请求 {count} 条结果。Eidos 会将查询发送给该服务，服务可能计费。",
            "toolName": "web_search",
            "hosts": [host],
            "target": call.arguments.get("query"),
        },
        intent_preconditions={
            "provider": snapshot.provider,
            "host": host,
            "query": call.arguments.get("query"),
            "count": count,
        },
        transition_reason="web_search_network_approval",
    )
    approval = context.controller.authorize_side_effect(
        run_id=run_id, item=item, prepared=execution, cancel=cancel
    )
    if approval.decision != "approve":
        return HandlerOutcome(
            search_error(
                "user_rejected_network", approval.feedback or "用户拒绝了本次网页搜索。"
            ),
            "declined",
        )
    if context.async_kernel is None:
        return HandlerOutcome(
            search_error("web_search_unavailable", "搜索执行环境尚未就绪。"),
            "completed",
        )

    def start() -> RuntimeAsyncTask[dict[str, object]]:
        if cancel.is_set():
            raise RuntimeCancelled
        context.controller.mark_execution_started()
        return context.async_kernel.start_task(
            adapter.search, call.arguments, owner_id=run_id
        )

    # Only commit/start uses the existing Run side-effect gate. Waiting for
    # network IO never holds the gate or grants network access to Shell.
    task = context.controller.run_exclusive_side_effect(cancel, start)
    try:
        while not task.wait(0.05):
            if cancel.is_set():
                task.cancel()
                task.wait(5)
                raise RuntimeCancelled
        if cancel.is_set():
            raise RuntimeCancelled
        raw = task.result()
        verified = runtime.verify(context, prepared, raw, cancel)
        return HandlerOutcome(verified.result, "completed")
    except CancelledError:
        raise RuntimeCancelled from None
    finally:
        if not task.done():
            task.cancel()
        runtime.cleanup(context, "completed" if task.done() else "canceled")
