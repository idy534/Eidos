from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import threading
from typing import ClassVar, Literal, TYPE_CHECKING

from pydantic import Field

from eidos_runtime.memory.contracts import (
    MemoryActionResult,
    MemoryDocument,
    MemoryEdit,
    MemoryEditRequest,
    MemoryListRequest,
    MemoryReadRequest,
    MemoryReadResult,
)
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.runtime.errors import tool_result
from eidos_runtime.tools.contracts import StrictToolModel, result_model
from eidos_runtime.tools.registry import (
    AdapterToolRuntime,
    ToolProvenance,
    ToolRegistryEntry,
    ToolSpec,
)

if TYPE_CHECKING:
    from eidos_runtime.model.client import ModelToolCall
    from eidos_runtime.runtime.tool_runtime import ToolCallRuntime
    from eidos_runtime.runtime.tool_execution import HandlerOutcome

logger = logging.getLogger("eidos.runtime.memory")


class MemorySearch(EidosFrozenStrictModel):
    query: str = Field(min_length=1, max_length=512)
    scope: Literal["current", "global", "allowed"] = "allowed"
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=8, ge=1, le=16)


class MemoryRead(EidosFrozenStrictModel):
    path: str = Field(default="MEMORY.md", max_length=200)
    scope: Literal["current", "global"] = "current"
    revision: int | None = Field(
        default=None,
        ge=0,
        description="Keep the returned revision while reading subsequent pages.",
    )
    offset: int = Field(
        default=0, ge=0, description="Use nextOffset to continue a truncated note."
    )
    limit: int = Field(default=8192, ge=1, le=16384)


class MemoryToolEdit(MemoryEdit):
    user_requested: bool = Field(
        default=False,
        description="True only when the user explicitly asks to save, correct or delete this note; an enduring future-behavior instruction also counts. Delete requires this flag. Otherwise writes require automatic memory for the current scope.",
    )


class MemoryResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ()
    documents: list[MemoryDocument] = Field(default_factory=list)
    read: MemoryReadResult | None = None
    action: MemoryActionResult | None = None
    epochs: dict[str, int] = Field(default_factory=dict)
    next_cursor: int | None = None
    search_limited: bool = False


class MemoryAdapter:
    def execute(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        raise RuntimeError("memory tools require the Run runtime")


@dataclass(frozen=True)
class MemoryToolRuntime(AdapterToolRuntime):
    def invoke(
        self,
        context: ToolCallRuntime,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
    ) -> HandlerOutcome:
        from eidos_runtime.runtime.tool_execution import (
            HandlerOutcome,
            PreparedToolExecution,
        )

        service = context.store.database.memory
        session_id = str(context.store.read_run(run_id)["sessionId"])
        epochs: dict[str, int] = {}
        try:
            if call.name == "memory_read":
                request = MemoryRead.model_validate(call.arguments)
                value = service.read(
                    MemoryReadRequest(session_id=session_id, **request.model_dump()),
                    for_use=True,
                )
                epochs = value.epochs
                data = MemoryResultData(read=value, epochs=epochs)
            elif call.name == "memory_search":
                request = MemorySearch.model_validate(call.arguments)
                with service.database.lock:
                    state = service.list(
                        MemoryListRequest(
                            session_id=session_id, **request.model_dump()
                        ),
                        for_use=True,
                    )
                    epochs = service.use_epochs(
                        service.database.connection(),
                        session_id,
                        {
                            scope.id: scope.privacy_epoch
                            for scope in state.scopes
                            if scope.mode != "off"
                        },
                    )
                data = MemoryResultData(
                    documents=state.documents,
                    next_cursor=state.next_cursor,
                    search_limited=state.search_limited,
                    epochs=epochs,
                )
            else:
                request = MemoryToolEdit.model_validate(call.arguments)
                automatic = not request.user_requested
                if request.action == "delete" and automatic:
                    raise MemoryRejected("memory_explicit_request_required")
                with service.database.transaction() as connection:
                    user = connection.execute(
                        "SELECT id FROM items WHERE run_id=? AND kind='user_message' AND status='completed' AND incomplete=0 ORDER BY creation_seq DESC LIMIT 1",
                        (run_id,),
                    ).fetchone()
                    if user is None:
                        raise MemoryRejected("memory_evidence_required")
                    write = MemoryEditRequest(
                        session_id=session_id,
                        operation_id="memory-tool:" + str(item["id"]),
                        **request.model_dump(
                            exclude={"user_requested", "source_item_ids"}
                        ),
                        source_item_ids=request.source_item_ids or [user[0]],
                    )
                    source_revisions = service.validate_edit(write, automatic=automatic)
                    scope = service.repository.scopes(
                        connection, session_id, request.scope
                    )[0]
                    epochs = service.use_epochs(
                        connection, session_id, {scope.id: scope.privacy_epoch}
                    )
                if cancel.is_set():
                    raise MemoryRejected("memory_canceled")
                prepared = PreparedToolExecution(
                    approval_description={
                        "tool": call.name,
                        "path": request.path,
                        "action": request.action,
                        "content": request.content,
                    },
                    intent_preconditions={
                        "operationId": write.operation_id,
                        "expectedRevision": request.expected_revision,
                    },
                    transition_reason="memory_control",
                )
                if automatic:
                    context.controller.authorize_workspace_side_effect(
                        item=item, prepared=prepared
                    )
                else:
                    approval = context.controller.authorize_side_effect(
                        run_id=run_id, item=item, prepared=prepared, cancel=cancel
                    )
                    if approval.decision != "approve":
                        raise MemoryRejected("memory_approval_rejected")
                action = service.edit(
                    write,
                    automatic=automatic,
                    source_revisions=source_revisions,
                    cancel=cancel,
                    access_epochs=epochs,
                )
                with service.database.lock:
                    updated = service.repository.scope(
                        service.database.connection(), scope.id
                    )
                    epochs = service.use_epochs(
                        service.database.connection(),
                        session_id,
                        {scope.id: updated.privacy_epoch},
                    )
                data = MemoryResultData(action=action, epochs=epochs)
                context.events.deliver_pending()
            result = tool_result(
                call.name,
                "success",
                data.action.code if data.action else "memory_retrieved",
                {
                    "memory_saved": "Memory note saved.",
                    "memory_unchanged": "Memory note unchanged.",
                    "memory_deleted": "Memory note deleted.",
                }[data.action.code]
                if data.action
                else "Historical notes; apply their scope and verify changeable facts.",
                data.model_dump(mode="json"),
                data_model=MemoryResultData,
            )
            service.track_tool(
                run_id,
                str(item["id"]),
                epochs,
                result,
                retrieval=call.name != "memory_edit",
            )
            return HandlerOutcome(result, "completed", "completed")
        except MemoryRejected as error:
            code = str(error)
            logger.info(
                "Memory request rejected run_id=%s item_id=%s code=%s",
                run_id,
                item["id"],
                code,
            )
            messages = {
                "memory_revision_conflict": "The note changed. Read its current version before preparing another edit.",
                "memory_source_changed": "The original evidence changed before publication. Select current original sources.",
                "memory_automatic_disabled": "Automatic writes are unavailable here. Use userRequested only for the user's explicit save or correction request.",
                "memory_user_edit_protected": "The user owns this note. Change it only for an explicit user correction request.",
            }
            result = tool_result(
                call.name,
                "error",
                code,
                messages.get(
                    code,
                    "The memory request was not applied. Correct the request or continue with other task work.",
                ),
                MemoryResultData().model_dump(mode="json"),
                data_model=MemoryResultData,
            )
            return HandlerOutcome(result, "failed", "failed")


def memory_entries(
    *, child: bool = False, read_enabled: bool = True, write_enabled: bool = True
) -> tuple[ToolRegistryEntry, ...]:
    entries = []
    for name, description, model, effect in (
        (
            "memory_read",
            "Read MEMORY.md or a linked Markdown note. Continue with nextOffset and the returned revision until the needed detail is complete.",
            MemoryRead,
            "none",
        ),
        (
            "memory_search",
            "Find relevant Markdown memory notes by topic or wording; page with nextCursor.",
            MemorySearch,
            "none",
        ),
        (
            "memory_edit",
            "Immediately create, replace or delete a source-grounded Markdown note. Read before updating and supply expectedRevision (0 for new notes). Keep its conditions and a short useful summary; Runtime updates MEMORY.md. sourceItemIds refer to original user messages or successful ordinary tool results, never assistant or memory output.",
            MemoryToolEdit,
            "eidos_state",
        ),
    ):
        if (effect == "none" and not read_enabled) or (
            effect != "none" and (child or not write_enabled)
        ):
            continue
        spec = ToolSpec(
            name=name,
            description=description,
            sideEffect=effect,
            approvalRequired=False,
            timeoutSeconds=60,
            inputSchema=model.model_json_schema(by_alias=True),
            resultSchema=result_model(MemoryResultData).model_json_schema(
                by_alias=True
            ),
        )
        provenance = ToolProvenance(
            kind="builtin",
            sourceId="eidos.memory",
            sourceVersion="2",
            contentHash=hashlib.sha256(spec.model_dump_json().encode()).hexdigest(),
        )
        adapter = MemoryAdapter()
        entries.append(
            ToolRegistryEntry(
                spec,
                provenance,
                adapter,
                model,
                MemoryResultData,
                runtime=MemoryToolRuntime(adapter, spec, provenance),
            )
        )
    return tuple(entries)
