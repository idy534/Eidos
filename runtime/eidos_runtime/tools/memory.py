from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
import hashlib
import json
import logging
import threading
from typing import ClassVar, Literal, TYPE_CHECKING

from pydantic import Field, field_validator, model_validator

from eidos_runtime.memory.contracts import (
    MemoryActionResult,
    MemoryDocument,
    MemoryKind,
    MemoryEditRequest,
    MemoryListRequest,
    MemoryReadRequest,
    MemoryReadResult,
    validate_path,
)
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.runtime.errors import tool_result
from eidos_runtime.tools.contracts import (
    MEMORY_PROJECTOR,
    StrictToolModel,
    result_model,
)
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
    query: str = Field(default="", max_length=512)
    scope: Literal["current", "global", "allowed"] = "allowed"
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=8, ge=1, le=16)
    include_archived: bool = False


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


class MemoryToolEdit(EidosFrozenStrictModel):
    scope: Literal["current", "global"] = "current"
    path: str = Field(min_length=1, max_length=200)
    action: Literal["write", "delete"] = "write"
    title: str = Field(default="", max_length=160)
    summary: str = Field(default="", max_length=240)
    kind: MemoryKind | None = None
    content: str | None = Field(default=None, max_length=65536)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return validate_path(value)

    @model_validator(mode="after")
    def valid_content(self):
        if self.action == "write" and (not self.content or not self.content.strip()):
            raise ValueError("memory_content_required")
        return self


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
                            for scope in service.repository.scopes(
                                service.database.connection(), session_id, request.scope
                            )
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
                with service.database.transaction() as connection:
                    scope = service.repository.scopes(
                        connection, session_id, request.scope
                    )[0]
                    pending = context.store.typed_runtime_repository().read_pending_approval(run_id)
                    pending_request = (
                        json.loads(pending.request_json)
                        if pending is not None and pending.item_id == str(item["id"])
                        else None
                    )
                    automatic = scope.mode == "auto" and pending_request is None
                    reading_enabled = (
                        pending_request.get("memoryReadingEnabled") is True
                        if pending_request is not None
                        else scope.mode != "off"
                    )
                    revision, kind = service.edit_base(
                        run_id, scope.id, request.path, item_id=str(item["id"])
                    )
                    write = MemoryEditRequest(
                        session_id=session_id,
                        operation_id="memory-tool:" + str(item["id"]),
                        **request.model_dump(exclude={"kind"}),
                        kind=request.kind or kind,
                        expected_revision=revision,
                        source_item_ids=service.audit_sources(run_id),
                    )
                    old_body = service.validate_edit(write, automatic=automatic)
                    epochs = service.use_epochs(
                        connection, session_id, {scope.id: scope.privacy_epoch}
                    )
                if cancel.is_set():
                    raise MemoryRejected("memory_canceled")
                display_path = f"memory/{request.scope}/{request.path}"
                diff = "" if automatic else "".join(
                    line if line.endswith("\n")
                    else line + "\n\\ No newline at end of file\n"
                    for line in unified_diff(
                        old_body.splitlines(keepends=True) if reading_enabled else [],
                        (request.content or "").splitlines(keepends=True),
                        fromfile="a/" + display_path,
                        tofile="b/" + display_path,
                    )
                )
                summary = (
                    f"Archive memory note {display_path}; history is retained."
                    if request.action == "delete"
                    else f"Replace memory note {display_path}."
                )
                if not reading_enabled:
                    summary += " Memory reading is off; the previous body is withheld."
                prepared = PreparedToolExecution(
                    approval_description={
                        "kind": "file_change",
                        "summary": summary,
                        "diff": diff,
                        "expectedRevision": write.expected_revision,
                        "memoryReadingEnabled": reading_enabled,
                    },
                    approval_diff=diff,
                    intent_preconditions={
                        "operationId": write.operation_id,
                        "expectedRevision": write.expected_revision,
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
                    archive=request.action == "delete",
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
                    "memory_archived": "Memory note archived. History is retained; read the path to restore it. Use Memory settings for permanent forgetting.",
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
            if context.controller.approval is not None:
                context.controller.approval.invalidate_unstarted(
                    run_id, item, code, cancel
                )
            logger.info(
                "Memory request rejected run_id=%s item_id=%s code=%s",
                run_id,
                item["id"],
                code,
            )
            messages = {
                "memory_revision_conflict": "The note changed. Prepare a new edit against its latest version; read it first when memory reading is enabled.",
                "memory_read_required": "Read this note with memory_read before updating or archiving it. Runtime carries its revision automatically.",
                "memory_forgotten_source": "This source was explicitly forgotten. Continue without saving it again; new information needs a new user statement.",
                "memory_automatic_disabled": "Automatic memory is disabled for this scope. Continue the task or ask the user to enable it in Memory settings.",
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
    if child:
        return ()
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
            "Find Markdown memory notes by wording, or omit query to list notes. Page with nextCursor. Set includeArchived to recover previously archived notes.",
            MemorySearch,
            "none",
        ),
        (
            "memory_edit",
            "Create or replace a Markdown memory note immediately. When reading is enabled, read existing notes before updating; Runtime carries revision and source records. Omitted title, kind and summary are handled automatically. Delete archives a note with recoverable history; read its path and write to restore it. Runtime updates MEMORY.md. Use Memory settings for permanent forgetting.",
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
            sourceVersion="3",
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
                projector=MEMORY_PROJECTOR,
            )
        )
    return tuple(entries)
