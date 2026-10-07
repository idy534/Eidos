from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import threading
from typing import ClassVar, Literal, TYPE_CHECKING

from pydantic import Field

from eidos_runtime.memory.contracts import (
    MemoryActionResult,
    MemoryEntry,
    MemoryGetRequest,
    MemoryManage,
    MemoryManageRequest,
    MemoryReadRequest,
    MemoryRecord,
    MemoryWriteRequest,
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
    limit: int = Field(default=5, ge=1, le=8)
    cursor: int = Field(default=0, ge=0, description="Pass next_cursor from a truncated response, even when entries is empty; keep the query and filters unchanged.")
    include_history: bool = False
    valid_at: int | None = Field(default=None, ge=0)


class MemoryRead(EidosFrozenStrictModel):
    entry_id: str = Field(min_length=1, max_length=256)
    revision: int | None = Field(default=None, ge=1)
    max_chars: int = Field(default=2048, ge=1, le=4096)


class MemoryToolRecord(MemoryRecord):
    source_quote: str | None = Field(
        default=None,
        min_length=1,
        max_length=8192,
        description="Exact quote from the latest user message in this Run. Use this for a paraphrase when sourceItemIds is omitted.",
    )
    source_quotes: dict[str, str] = Field(
        default_factory=dict,
        max_length=16,
        description="Exact user quotes keyed by sourceItemIds for older messages. The IDs and quotes must match.",
    )
    mode: Literal["candidate", "remember"] = Field(
        default="candidate",
        description="remember saves an explicit user request; candidate creates a pending proposal when automatic generation is enabled.",
    )


class MemoryResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ()
    entries: list[MemoryEntry] = Field(default_factory=list)
    action: MemoryActionResult | None = None
    epochs: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False
    next_cursor: int | None = None


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
        from eidos_runtime.persistence.collaboration import CollaborationRepository

        service = context.store.database.memory
        session_id = str(context.store.read_run(run_id)["sessionId"])
        operation = "memory-tool:" + str(item["id"])
        try:
            if call.name == "memory_search":
                request = MemorySearch.model_validate(call.arguments)
                with service.database.lock:
                    state = service.read(
                        MemoryReadRequest(session_id=session_id, **request.model_dump()),
                        for_use=True,
                    )
                    data = MemoryResultData(
                        entries=state.entries,
                        epochs=service.use_epochs(service.database.connection(), session_id,
                                                 {s.id: s.privacy_epoch for s in state.scopes}),
                        truncated=state.truncated,
                        next_cursor=state.next_cursor,
                    )
            elif call.name == "memory_read":
                request = MemoryRead.model_validate(call.arguments)
                with service.database.lock:
                    entry = service.get(
                        MemoryGetRequest(
                            session_id=session_id,
                            entry_id=request.entry_id,
                            revision=request.revision,
                        ),
                        for_use=True,
                    ).entry
                    scope = service.repository.scope(
                        service.database.connection(), entry.scope_id
                    )
                    data = MemoryResultData(
                        entries=[
                            entry.model_copy(
                                update={"content": entry.content[: request.max_chars]}
                            )
                        ],
                        epochs=service.use_epochs(service.database.connection(), session_id,
                                                 {scope.id: scope.privacy_epoch}),
                        truncated=len(entry.content) > request.max_chars,
                    )
            else:
                if (
                    CollaborationRepository(context.store.database).child_role_for_run(
                        run_id
                    )
                    is not None
                ):
                    raise MemoryRejected("memory_root_session_required")
                request = (
                    MemoryToolRecord if call.name == "memory_record" else MemoryManage
                ).model_validate(call.arguments)
                with service.database.lock:
                    user = service.database.connection().execute(
                        "SELECT id FROM items WHERE run_id=? AND kind='user_message' AND status='completed' AND incomplete=0 ORDER BY creation_seq DESC LIMIT 1",
                        (run_id,),
                    ).fetchone()
                if user is None:
                    raise MemoryRejected("memory_evidence_required")
                if isinstance(request, MemoryToolRecord):
                    service._safe_record(request)
                    sources = request.source_item_ids or [user[0]]
                    if request.source_item_ids:
                        if request.source_quote is not None:
                            raise MemoryRejected("memory_evidence_required")
                        quotes = request.source_quotes or {
                            identifier: request.content for identifier in sources
                        }
                    else:
                        if request.source_quotes:
                            raise MemoryRejected("memory_evidence_required")
                        quotes = {user[0]: request.source_quote or request.content}
                    if set(quotes) != set(sources):
                        raise MemoryRejected("memory_evidence_required")
                    write_request = MemoryWriteRequest(
                        session_id=session_id,
                        operation_id=operation,
                        **request.model_dump(
                            exclude={"mode", "source_item_ids", "source_quote", "source_quotes"}
                        ),
                        source_item_ids=sources,
                    )
                    if request.mode == "candidate":
                        with service.database.lock:
                            connection = service.database.connection()
                            scope = service.repository.scopes(
                                connection, session_id, request.scope
                            )[0]
                            service._assert_candidate_allowed(connection, write_request, scope.id)

                    def verify_quotes() -> dict[str, int]:
                        revisions = {}
                        with service.database.lock:
                            for identifier in sources:
                                original = service.database.connection().execute(
                                    "SELECT i.content,v.item_revision FROM items i JOIN memory_source_items v ON v.item_id=i.id WHERE i.id=? AND i.session_id=? AND i.kind='user_message'",
                                    (identifier, session_id),
                                ).fetchone()
                                if (
                                    original is None
                                    or not quotes[identifier].strip()
                                    or quotes[identifier] not in original[0]
                                ):
                                    raise MemoryRejected("memory_evidence_quote_invalid")
                                revisions[identifier] = original[1]
                        return revisions

                    verify_quotes()
                prepared = PreparedToolExecution(
                    approval_description={
                        "tool": call.name,
                        "action": request.model_dump(mode="json", by_alias=True),
                    },
                    intent_preconditions={"operationId": operation},
                    transition_reason="memory_control",
                )
                if (
                    isinstance(request, MemoryToolRecord)
                    and request.mode == "candidate"
                ):
                    context.controller.authorize_workspace_side_effect(
                        item=item, prepared=prepared
                    )
                else:
                    approval = context.controller.authorize_side_effect(
                        run_id=run_id, item=item, prepared=prepared, cancel=cancel
                    )
                    if approval.decision != "approve":
                        raise MemoryRejected("memory_approval_rejected")
                if isinstance(request, MemoryToolRecord):
                    source_revisions = verify_quotes()
                    action = service.record(
                        write_request,
                        candidate=request.mode == "candidate",
                        source_revisions=source_revisions,
                    )
                else:
                    action = service.manage(
                        MemoryManageRequest(
                            session_id=session_id,
                            operation_id=operation,
                            **request.model_dump(),
                        ),
                        source_item_ids=[user[0]],
                    )
                with service.database.lock:
                    scopes = service.repository.scopes(
                        service.database.connection(), session_id
                    )
                    data = MemoryResultData(
                        action=action, epochs=service.use_epochs(service.database.connection(), session_id,
                                                               {s.id: s.privacy_epoch for s in scopes})
                    )
                context.events.deliver_pending()
            result = tool_result(
                call.name,
                "success",
                data.action.code if data.action else "memory_retrieved",
                (
                    "Memory proposal saved; pending acceptance."
                    if data.action.status == "pending" else "Memory change applied."
                ) if data.action else "Historical evidence; verify relevance and dates.",
                data.model_dump(mode="json"),
                data_model=MemoryResultData,
            )
            service.track_tool(
                run_id,
                str(item["id"]),
                data.epochs,
                result,
                retrieval=call.name in {"memory_search", "memory_read"},
            )
            return HandlerOutcome(result, "completed", "completed")
        except MemoryRejected as error:
            code = str(error)
            logger.info(
                "Memory tool rejected run_id=%s item_id=%s tool=%s code=%s",
                run_id, item["id"], call.name, code,
            )
            return HandlerOutcome(
                tool_result(
                    call.name,
                    "error",
                    code,
                    {
                        "memory_evidence_quote_invalid": "Memory was not saved. sourceQuote must be an exact span of the latest user message, or sourceQuotes must match the supplied sourceItemIds.",
                        "memory_generation_disabled": "Automatic memory generation is off for this scope. Do not create a candidate; use mode remember only when the user explicitly requests a save.",
                    }.get(code, "Memory request was not applied; use current IDs and revisions or ask the user to resolve the restriction."),
                    data_model=MemoryResultData,
                ),
                "failed",
                "failed",
            )


def memory_entries(
    *, child: bool = False, read_enabled: bool = True, write_enabled: bool = True
) -> tuple[ToolRegistryEntry, ...]:
    entries = []
    for name, description, model, effect in (
        (
            "memory_search",
            "Search allowed historical memories for relevant prior preferences, decisions or experience.",
            MemorySearch,
            "none",
        ),
        (
            "memory_read",
            "Read a memory's content, sources and dates by entry ID and optional revision.",
            MemoryRead,
            "none",
        ),
        (
            "memory_record",
            "Save an explicitly requested, source-grounded memory or pending proposal.",
            MemoryToolRecord,
            "eidos_state",
        ),
        (
            "memory_manage",
            "Apply an explicit request to accept, correct, forget, pin, unpin or archive a memory by ID and current revision.",
            MemoryManage,
            "eidos_state",
        ),
    ):
        if effect == "none" and not read_enabled:
            continue
        if effect != "none" and (child or not write_enabled):
            continue
        spec = ToolSpec(
            name=name,
            description=description,
            sideEffect=effect,
            approvalRequired=name == "memory_manage",
            timeoutSeconds=60,
            inputSchema=model.model_json_schema(by_alias=True),
            resultSchema=result_model(MemoryResultData).model_json_schema(
                by_alias=True
            ),
        )
        provenance = ToolProvenance(
            kind="builtin",
            sourceId="eidos.memory",
            sourceVersion="1",
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
