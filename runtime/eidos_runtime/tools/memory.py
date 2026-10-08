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
    MemoryRecordRequest,
    MemoryWriteRequest,
)
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.memory.fact_review import MemoryFactAssessment
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


class MemoryToolRecord(MemoryRecordRequest):
    source_quote: str | None = Field(
        default=None,
        min_length=1,
        max_length=8192,
        description="Exact factual quote from a user message or successful ordinary tool result in this session. Runtime resolves recent sources when sourceItemIds is omitted. Cite the fact, not a later request to remember it.",
    )
    source_quotes: dict[str, str] = Field(
        default_factory=dict,
        max_length=16,
        description="Exact original quotes keyed by sourceItemIds when known. The IDs and quotes must match the evidenceClass.",
    )
    mode: Literal["candidate", "remember", "automatic"] = Field(
        default="candidate",
        description="automatic saves confirmed facts as active; candidate submits a separate pending proposal. Both require automaticLearning=true and current scope. inferred requires candidate. remember follows an explicit save request.",
    )


class MemoryResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ()
    entries: list[MemoryEntry] = Field(default_factory=list)
    action: MemoryActionResult | None = None
    epochs: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False
    next_cursor: int | None = None
    # Decode historical results; new foreground writes do not emit assessments.
    fact_assessment: MemoryFactAssessment | None = None


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
        access_epochs = {}
        source_revisions = None
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
                    source_kind = "tool_call" if request.evidence_class == "observed_verified" else "user_message"
                    if request.source_item_ids:
                        if request.source_quote is not None:
                            raise MemoryRejected("memory_evidence_required")
                        sources = request.source_item_ids
                        quotes = request.source_quotes or {
                            identifier: request.content for identifier in sources
                        }
                    else:
                        if request.source_quotes:
                            raise MemoryRejected("memory_evidence_required")
                        quote = request.source_quote or request.content
                        # Resolve bounded recent originals; explicit IDs can reach older evidence.
                        with service.database.lock:
                            originals = service.database.connection().execute(
                                "SELECT i.id,i.run_id,instr(COALESCE(NULLIF(i.content,''),t.result_json,''),?) AS matched "
                                "FROM items i JOIN memory_source_items v ON v.item_id=i.id "
                                "LEFT JOIN tool_calls t ON t.item_id=i.id WHERE i.session_id=? AND i.kind=? "
                                "AND i.status='completed' AND i.incomplete=0 AND v.eligible=1 ORDER BY i.creation_seq DESC LIMIT 32",
                                (quote, session_id, source_kind),
                            ).fetchall()
                        matches = [original for original in originals if original["matched"]]
                        current = next((original for original in matches if original["run_id"] == run_id), None)
                        if current is not None:
                            matches = [current]
                        if not matches:
                            raise MemoryRejected("memory_evidence_quote_invalid")
                        if len(matches) != 1:
                            raise MemoryRejected("memory_evidence_ambiguous")
                        sources = [matches[0]["id"]]
                        quotes = {sources[0]: quote}
                    if set(quotes) != set(sources):
                        raise MemoryRejected("memory_evidence_required")
                    write_request = MemoryWriteRequest(
                        session_id=session_id,
                        operation_id=operation,
                        **request.model_dump(
                            exclude={"source_item_ids", "source_quote", "source_quotes"}
                        ),
                        source_item_ids=sources,
                    )
                    def verify_quotes() -> dict[str, int]:
                        revisions = {}
                        with service.database.lock:
                            for identifier in sources:
                                original = service.database.connection().execute(
                                    "SELECT v.item_revision,instr(COALESCE(NULLIF(i.content,''),t.result_json,''),?) AS matched "
                                    "FROM items i JOIN memory_source_items v ON v.item_id=i.id LEFT JOIN tool_calls t ON t.item_id=i.id "
                                    "WHERE i.id=? AND i.session_id=? AND i.kind=?",
                                    (quotes[identifier], identifier, session_id, source_kind),
                                ).fetchone()
                                if (
                                    original is None
                                    or not quotes[identifier].strip()
                                    or not original["matched"]
                                ):
                                    raise MemoryRejected("memory_evidence_quote_invalid")
                                revisions[identifier] = original["item_revision"]
                        return revisions

                    source_revisions = verify_quotes()
                    service.validate_record(write_request, source_revisions)
                    with service.database.transaction() as connection:
                        scope = service.repository.scopes(connection, session_id, request.scope)[0]
                        access_epochs = service.use_epochs(connection, session_id, {scope.id: scope.privacy_epoch})
                elif request.action == "correct":
                    # Bind corrections to their original source and target before authorization.
                    with service.database.transaction() as connection:
                        target = service.repository.get(connection, session_id, request.entry_id)
                        if target.revision != request.expected_revision:
                            raise MemoryRejected("memory_revision_conflict")
                        scope = service.repository.scope(connection, target.scope_id)
                        evidence_rows = service.repository.evidence(connection, session_id, [user[0]], target.scope_id, "explicit_user")
                        source_revisions = {e.item_id: e.item_revision for e in evidence_rows}
                    correction = MemoryWriteRequest(session_id=session_id, operation_id=operation, mode="remember",
                        scope="global" if scope.kind == "global" else "current", content=request.content,
                        title=request.content[:80], kind=target.kind, source_item_ids=[user[0]],
                        target_entry_id=target.id, expected_revision=target.revision)
                    service.validate_record(correction, source_revisions)
                    with service.database.transaction() as connection:
                        access_epochs = service.use_epochs(connection, session_id, {scope.id: scope.privacy_epoch})
                if cancel.is_set():
                    raise MemoryRejected("memory_canceled")
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
                    and request.mode in {"candidate", "automatic"}
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
                    action = service.record(
                        write_request,
                        source_revisions=source_revisions,
                        cancel=cancel,
                        access_epochs=access_epochs,
                    )
                else:
                    action = service.manage(
                        MemoryManageRequest(
                            session_id=session_id,
                            operation_id=operation,
                            **request.model_dump(),
                        ),
                        source_item_ids=[user[0]],
                        access_epochs=access_epochs or None,
                        source_revisions=source_revisions,
                        cancel=cancel,
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
                    if data.action.status == "pending" else "Memory already saved; no change."
                    if data.action.code == "memory_unchanged" else "Memory change applied."
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
            with service.database.transaction() as connection:
                if access_epochs and not service.valid_epochs(connection, access_epochs):
                    code, access_epochs = "memory_snapshot_revoked", {}
            logger.info(
                "Memory tool rejected run_id=%s item_id=%s tool=%s code=%s",
                run_id, item["id"], call.name, code,
            )
            result = tool_result(
                    call.name,
                    "error",
                    code,
                    {
                        "memory_evidence_quote_invalid": "Memory was not saved. Cite the exact original factual statement or successful tool result, using sourceItemIds and sourceQuotes for older sources.",
                        "memory_confirmed_evidence_required": "No memory was saved. automatic and remember require confirmed evidence; use candidate for an inferred claim only when automatic learning is enabled.",
                        "memory_evidence_ambiguous": "Several originals match sourceQuote. Supply the intended sourceItemIds and sourceQuotes; do not guess an ID.",
                        "memory_generation_disabled": "Automatic and candidate writes are disabled for this scope. Use remember only for an explicit user save request.",
                        "memory_candidate_scope_invalid": "Automatic and candidate writes are authorized only for currentScope. Use scope=current; do not promote project information to global memory.",
                    }.get(code, "Memory request was not applied; use current IDs and revisions or ask the user to resolve the restriction."),
                    MemoryResultData(epochs=access_epochs).model_dump(mode="json"),
                    data_model=MemoryResultData,
                )
            if access_epochs:
                try:
                    service.track_tool(run_id, str(item["id"]), access_epochs, result, retrieval=False)
                except MemoryRejected:
                    result = tool_result(call.name, "error", "memory_snapshot_revoked", "Memory access was revoked. Rebuild from current authorized sources.", MemoryResultData().model_dump(mode="json"), data_model=MemoryResultData)
            return HandlerOutcome(result, "failed", "failed")


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
            "Save source-grounded information that can improve future related work, with its scope and conditions preserved. For an unchanged existing fact, preserve its content and dates and supply its ID/revision to reuse it. Revise a target only for a clear correction.",
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
