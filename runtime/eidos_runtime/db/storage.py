from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
from typing import TypeVar

from eidos_runtime.context.facts import CompactSummary, ContextFacts
from eidos_runtime.db.database import (
    DATABASE_NAME,
    RESERVE_BYTES,
    RESERVE_NAME,
    CommittedMutation,
    Database,
    Repository,
    WorkspaceIdentity,
)
from eidos_runtime.db.errors import (
    ActiveRunError,
    ContextLimitExceeded,
    InvalidCursorError,
    InvalidRunStateError,
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
    RunLimitReached,
    SegmentLimitReached,
    SessionActiveError,
    StorageError,
    WorkspaceBoundaryError,
)
from eidos_runtime.db.recovery import recover_runtime_facts
from eidos_runtime.db.repositories import (
    AsyncOperationRepository,
    ContextRepository,
    ExecutionRepository,
    ExtensionRepository,
    RunRepository,
    SessionRepository,
)
from eidos_runtime.db.repositories.context import RECENT_CONTEXT_STEPS
from eidos_runtime.db.repositories.async_operations import AsyncOperation
from eidos_runtime.db.repositories.sessions import DEFAULT_LIST_LIMIT
from eidos_runtime.db.schema import SCHEMA_VERSION
from eidos_runtime.model.client import ModelProfileSnapshot, ModelUsage
from eidos_runtime.model.config import DEFAULT_MODEL_ID
from eidos_runtime.runtime.contracts import ProgressSignature


SCHEMA_REVISION = SCHEMA_VERSION
TRepository = TypeVar("TRepository", bound=Repository)


class SessionStore:
    def __init__(self, data_directory: Path | None = None) -> None:
        self._database = Database(data_directory)
        self._sessions: SessionRepository | None = None
        self._runs: RunRepository | None = None
        self._execution: ExecutionRepository | None = None
        self._extensions: ExtensionRepository | None = None
        self._context: ContextRepository | None = None
        self._async_operations: AsyncOperationRepository | None = None

    def initialize(self) -> None:
        self._database.initialize()
        if self._database.health_state != "ready":
            return
        try:
            with self._database.transaction() as connection:
                recover_runtime_facts(connection)
        except (OSError, sqlite3.Error, StorageError) as error:
            self._database.mark_failed(error)
            return
        except Exception:
            self._database.close()
            raise
        self._sessions = SessionRepository(self._database)
        self._runs = RunRepository(self._database)
        self._execution = ExecutionRepository(self._database)
        self._extensions = ExtensionRepository(self._database)
        self._context = ContextRepository(self._database)
        self._async_operations = AsyncOperationRepository(self._database)

    @property
    def data_directory(self) -> Path | None:
        return self._database.data_directory

    @property
    def connection(self) -> sqlite3.Connection | None:
        return self._database.raw_connection

    @property
    def lock(self) -> threading.RLock:
        return self._database.lock

    @property
    def health_state(self) -> str:
        return self._database.health_state

    @property
    def health_code(self) -> str | None:
        return self._database.health_code

    def close(self) -> None:
        self._database.close()

    def health(self) -> dict[str, object]:
        return self._database.health()

    def accept_async_operation(
        self,
        *,
        request_id: str | None,
        operation_id: str,
        scope: str,
        request: dict[str, object],
    ) -> tuple[AsyncOperation, bool]:
        return self._repository(self._async_operations).accept(
            request_id=request_id,
            operation_id=operation_id,
            scope=scope,
            request=request,
        )

    def start_async_operation(
        self, operation_id: str
    ) -> AsyncOperation:
        return self._repository(self._async_operations).start(operation_id)

    def complete_async_operation(
        self, operation_id: str, result: dict[str, object]
    ) -> AsyncOperation:
        return self._repository(self._async_operations).complete(
            operation_id, result
        )

    def fail_async_operation(
        self, operation_id: str, error_code: str
    ) -> AsyncOperation:
        return self._repository(self._async_operations).fail(
            operation_id, error_code
        )

    def cancel_async_operation(
        self, operation_id: str
    ) -> AsyncOperation:
        return self._repository(self._async_operations).cancel(operation_id)

    def cancel_active_async_operations(
        self,
    ) -> tuple[AsyncOperation, ...]:
        return self._repository(
            self._async_operations
        ).cancel_active()

    @staticmethod
    def _repository(repository: TRepository | None) -> TRepository:
        if repository is None:
            raise StorageError("storage is not initialized")
        return repository

    def create_session(
        self, workspace_root: str, *, operation_id: str | None = None
    ) -> dict[str, object]:
        return self._repository(self._sessions).create_session(
            workspace_root,
            operation_id=operation_id,
        )

    def list_sessions(
        self, *, limit: int = DEFAULT_LIST_LIMIT, cursor: str | None = None
    ) -> dict[str, object]:
        return self._repository(self._sessions).list_sessions(limit=limit, cursor=cursor)

    def read_session(self, session_id: str) -> dict[str, object] | None:
        return self._repository(self._sessions).read_session(session_id)

    def session_model_id(self, session_id: str) -> str | None:
        return self._repository(self._sessions).session_model_id(session_id)

    def rename_session(
        self,
        session_id: str,
        title: str,
        *,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        return self._repository(self._sessions).rename_session(
            session_id,
            title,
            operation_id=operation_id,
        )

    def begin_title_generation_committed(
        self, session_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(
            self._sessions
        ).begin_title_generation_committed(session_id)

    def finish_title_generation_committed(
        self,
        session_id: str,
        title: str,
        *,
        failure_reason: str | None = None,
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(
            self._sessions
        ).finish_title_generation_committed(
            session_id, title, failure_reason=failure_reason
        )

    def delete_session(
        self,
        session_id: str,
        *,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        return self._repository(self._sessions).delete_session(
            session_id,
            operation_id=operation_id,
        )

    def create_run(
        self,
        session_id: str,
        user_input: str,
        *,
        operation_id: str | None = None,
        queued: bool = False,
        session_title: str | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        model_profile: ModelProfileSnapshot | None = None,
        extension_snapshot: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        return self._repository(self._runs).create_run(
            session_id,
            user_input,
            operation_id=operation_id,
            queued=queued,
            session_title=session_title,
            model_id=model_id,
            model_profile=model_profile,
            extension_snapshot=extension_snapshot,
        )

    def enqueue_run(
        self,
        session_id: str,
        user_input: str,
        *,
        operation_id: str | None = None,
        session_title: str | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        model_profile: ModelProfileSnapshot | None = None,
        extension_snapshot: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        return self._repository(self._runs).enqueue_run(
            session_id,
            user_input,
            operation_id=operation_id,
            session_title=session_title,
            model_id=model_id,
            model_profile=model_profile,
            extension_snapshot=extension_snapshot,
        )

    def continue_run(
        self,
        run_id: str,
        user_input: str,
        *,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        return self._repository(self._runs).continue_run(
            run_id,
            user_input,
            operation_id=operation_id,
        )

    def claim_next_run(self) -> dict[str, object] | None:
        return self._repository(self._runs).claim_next_run()

    def claim_next_run_committed(
        self,
    ) -> CommittedMutation[dict[str, object]] | None:
        return self._repository(self._runs).claim_next_run_committed()

    def read_run(self, run_id: str) -> dict[str, object]:
        return self._repository(self._runs).read_run(run_id)

    def read_model_profile(self, run_id: str) -> ModelProfileSnapshot:
        return self._repository(self._runs).read_model_profile(run_id)

    def run_budget(self, run_id: str) -> dict[str, int]:
        return self._repository(self._runs).run_budget(run_id)

    def read_runtime_start_event(self, run_id: str) -> dict[str, object]:
        return self._repository(self._runs).read_runtime_start_event(run_id)

    def plugin_record(self, plugin_id: str) -> dict[str, object] | None:
        return self._repository(self._extensions).plugin_record(plugin_id)

    def list_plugin_records(
        self, *, include_removed: bool = False
    ) -> list[dict[str, object]]:
        return self._repository(self._extensions).list_plugin_records(
            include_removed=include_removed,
        )

    def insert_plugin_record(self, record: dict[str, object]) -> dict[str, object]:
        return self._repository(self._extensions).insert_plugin_record(record)

    def set_plugin_enabled(
        self, plugin_id: str, enabled: bool
    ) -> dict[str, object]:
        return self._repository(self._extensions).set_plugin_enabled(plugin_id, enabled)

    def remove_plugin_record(self, plugin_id: str) -> dict[str, object]:
        return self._repository(self._extensions).remove_plugin_record(plugin_id)

    def plugin_referenced_by_nonterminal_run(
        self, plugin_id: str, content_hash: str
    ) -> bool:
        return self._repository(self._extensions).plugin_referenced_by_nonterminal_run(
            plugin_id,
            content_hash,
        )

    def mcp_server_state(
        self, plugin_id: str, server_id: str
    ) -> dict[str, object]:
        return self._repository(self._extensions).mcp_server_state(plugin_id, server_id)

    def set_mcp_server_state(
        self,
        server: dict[str, object],
        *,
        consented: bool,
        error_code: str | None = None,
    ) -> dict[str, object]:
        return self._repository(self._extensions).set_mcp_server_state(
            server,
            consented=consented,
            error_code=error_code,
        )

    def activated_tools(self, run_id: str) -> tuple[str, ...]:
        return self._repository(self._extensions).activated_tools(run_id)

    def activate_tools(self, run_id: str, names: tuple[str, ...]) -> tuple[str, ...]:
        return self._repository(self._extensions).activate_tools(run_id, names)

    def record_mcp_tool_list_changed(self, plugin_id: str, server_id: str) -> None:
        return self._repository(self._extensions).record_mcp_tool_list_changed(plugin_id, server_id)

    def extension_event_waterline(self) -> int:
        return self._repository(self._extensions).extension_event_waterline()

    def list_extension_events(
        self, *, after_event_id: int = 0, limit: int = 200
    ) -> dict[str, object]:
        return self._repository(self._extensions).list_extension_events(
            after_event_id=after_event_id,
            limit=limit,
        )

    def read_item(self, item_id: str) -> dict[str, object]:
        return self._repository(self._execution).read_item(item_id)

    def read_session_snapshot(
        self,
        session_id: str,
        *,
        item_limit: int = 200,
        before_item_id: str | None = None,
    ) -> dict[str, object]:
        return self._repository(self._sessions).read_session_snapshot(
            session_id,
            item_limit=item_limit,
            before_item_id=before_item_id,
        )

    def list_events(
        self, session_id: str, *, after_event_id: int, limit: int = 200
    ) -> dict[str, object]:
        return self._repository(self._sessions).list_events(
            session_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def get_user_item(self, run_id: str) -> dict[str, object]:
        return self._repository(self._execution).get_user_item(run_id)

    def workspace_for_run(self, run_id: str) -> WorkspaceIdentity:
        return self._repository(self._execution).workspace_for_run(run_id)

    def increment_model_step(
        self,
        run_id: str,
        *,
        tool_snapshot: dict[str, object] | None = None,
    ) -> int:
        return self._repository(self._execution).increment_model_step(
            run_id,
            tool_snapshot=tool_snapshot,
        )

    def read_step_tool_snapshot(
        self, run_id: str, model_step_index: int
    ) -> dict[str, object]:
        return self._repository(self._execution).read_step_tool_snapshot(run_id, model_step_index)

    def read_current_step_fact(self, run_id: str) -> dict[str, object]:
        return self._repository(self._execution).read_current_step_fact(run_id)

    def add_effective_time(self, run_id: str, elapsed_ms: int) -> None:
        return self._repository(self._execution).add_effective_time(run_id, elapsed_ms)

    def add_effective_time_committed(
        self, run_id: str, elapsed_ms: int
    ) -> CommittedMutation[dict[str, object]] | None:
        return self._repository(self._execution).add_effective_time_committed(run_id, elapsed_ms)

    def complete_current_step(
        self,
        run_id: str,
        status_value: str,
        *,
        reason: str | None = None,
        progress_signature: ProgressSignature | None = None,
    ) -> None:
        return self._repository(self._execution).complete_current_step(
            run_id,
            status_value,
            reason=reason,
            progress_signature=progress_signature,
        )

    def recent_progress_signatures(
        self, run_id: str, limit: int = 8
    ) -> tuple[ProgressSignature, ...]:
        return self._repository(self._execution).recent_progress_signatures(run_id, limit)

    def complete_current_model_attempt(
        self,
        run_id: str,
        status: str,
        *,
        usage: ModelUsage | None = None,
        provider_name: str | None = None,
        resolved_model_name: str | None = None,
        finish_reason: str | None = None,
        provider_response_id: str | None = None,
        error_code: str | None = None,
        http_status: int | None = None,
        ttft_ms: int | None = None,
        duration_ms: int | None = None,
        had_progress: bool = False,
    ) -> bool:
        return self._repository(self._execution).complete_current_model_attempt(
            run_id,
            status,
            usage=usage,
            provider_name=provider_name,
            resolved_model_name=resolved_model_name,
            finish_reason=finish_reason,
            provider_response_id=provider_response_id,
            error_code=error_code,
            http_status=http_status,
            ttft_ms=ttft_ms,
            duration_ms=duration_ms,
            had_progress=had_progress,
        )

    def start_retry_model_attempt(self, run_id: str) -> None:
        return self._repository(self._execution).start_retry_model_attempt(run_id)

    def read_model_attempts(self, run_id: str) -> list[dict[str, object]]:
        return self._repository(self._execution).read_model_attempts(run_id)

    def create_assistant_item(
        self, run_id: str, model_step_index: int
    ) -> dict[str, object]:
        return self._repository(self._execution).create_assistant_item(run_id, model_step_index)

    def create_assistant_item_committed(
        self, run_id: str, model_step_index: int
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._execution).create_assistant_item_committed(
            run_id,
            model_step_index,
        )

    def create_finalization_assistant_item(
        self, run_id: str
    ) -> dict[str, object]:
        return self._repository(self._execution).create_finalization_assistant_item(run_id)

    def create_finalization_assistant_item_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._execution).create_finalization_assistant_item_committed(
            run_id,
        )

    def append_item_content(self, item_id: str, delta: str) -> dict[str, object]:
        return self._repository(self._execution).append_item_content(item_id, delta)

    def append_item_deltas_committed(
        self,
        item_id: str,
        deltas: tuple[str, ...],
        first_sequence: int,
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._execution).append_item_deltas_committed(
            item_id,
            deltas,
            first_sequence,
        )

    def complete_assistant_item(self, item_id: str) -> dict[str, object]:
        return self._repository(self._execution).complete_assistant_item(item_id)

    def complete_assistant_item_committed(
        self, item_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._execution).complete_assistant_item_committed(item_id)

    def mark_assistant_incomplete(self, item_id: str) -> dict[str, object]:
        return self._repository(self._execution).mark_assistant_incomplete(item_id)

    def mark_assistant_incomplete_committed(
        self, item_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._execution).mark_assistant_incomplete_committed(item_id)

    def mark_assistant_incomplete_if_active_committed(
        self, item_id: str
    ) -> CommittedMutation[dict[str, object]] | None:
        return self._repository(self._execution).mark_assistant_incomplete_if_active_committed(
            item_id,
        )

    def complete_assistant_and_run(
        self, item_id: str, run_id: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        return self._repository(self._execution).complete_assistant_and_run(item_id, run_id)

    def complete_assistant_and_run_committed(
        self, item_id: str, run_id: str
    ) -> CommittedMutation[tuple[dict[str, object], dict[str, object]]]:
        return self._repository(self._execution).complete_assistant_and_run_committed(
            item_id,
            run_id,
        )

    def create_tool_item(
        self,
        run_id: str,
        model_step_index: int,
        batch_order: int,
        provider_call_id: str,
        tool_name: str,
        arguments_json: str,
        *,
        provenance: dict[str, object] | None = None,
        tool_set_hash: str | None = None,
    ) -> dict[str, object]:
        return self._repository(self._execution).create_tool_item(
            run_id,
            model_step_index,
            batch_order,
            provider_call_id,
            tool_name,
            arguments_json,
            provenance=provenance,
            tool_set_hash=tool_set_hash,
        )

    def create_tool_item_committed(
        self,
        run_id: str,
        model_step_index: int,
        batch_order: int,
        provider_call_id: str,
        tool_name: str,
        arguments_json: str,
        *,
        provenance: dict[str, object] | None = None,
        tool_set_hash: str | None = None,
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._execution).create_tool_item_committed(
            run_id,
            model_step_index,
            batch_order,
            provider_call_id,
            tool_name,
            arguments_json,
            provenance=provenance,
            tool_set_hash=tool_set_hash,
        )

    def complete_tool_item(
        self,
        item_id: str,
        result_json: str,
        *,
        item_status: str = "completed",
        tool_status: str = "completed",
        workspace_changed: bool = False,
        diff_hash: str | None = None,
    ) -> dict[str, object]:
        return self._repository(self._execution).complete_tool_item(
            item_id,
            result_json,
            item_status=item_status,
            tool_status=tool_status,
            workspace_changed=workspace_changed,
            diff_hash=diff_hash,
        )

    def complete_tool_item_committed(
        self,
        item_id: str,
        result_json: str,
        *,
        item_status: str = "completed",
        tool_status: str = "completed",
        workspace_changed: bool = False,
        diff_hash: str | None = None,
        duration_ms: int | None = None,
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._execution).complete_tool_item_committed(
            item_id,
            result_json,
            item_status=item_status,
            tool_status=tool_status,
            workspace_changed=workspace_changed,
            diff_hash=diff_hash,
            duration_ms=duration_ms,
        )

    def complete_tool_item_once_committed(
        self,
        item_id: str,
        result_json: str,
        *,
        item_status: str,
        tool_status: str,
        workspace_changed: bool = False,
        diff_hash: str | None = None,
        duration_ms: int | None = None,
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(
            self._execution
        ).complete_tool_item_once_committed(
            item_id,
            result_json,
            item_status=item_status,
            tool_status=tool_status,
            workspace_changed=workspace_changed,
            diff_hash=diff_hash,
            duration_ms=duration_ms,
        )

    def begin_approval(
        self,
        item_id: str,
        diff: str,
        base_sha256: str | None,
    ) -> dict[str, object]:
        return self._repository(self._execution).begin_approval(item_id, diff, base_sha256)

    def begin_approval_committed(
        self,
        item_id: str,
        diff: str,
        base_sha256: str | None,
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._execution).begin_approval_committed(
            item_id,
            diff,
            base_sha256,
        )

    def resolve_approval(
        self,
        item_id: str,
        decision: str,
        feedback: str | None,
        *,
        requeue: bool = False,
    ) -> dict[str, object]:
        return self._repository(self._execution).resolve_approval(
            item_id,
            decision,
            feedback,
            requeue=requeue,
        )

    def resolve_approval_committed(
        self,
        item_id: str,
        decision: str,
        feedback: str | None,
        *,
        requeue: bool = False,
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._execution).resolve_approval_committed(
            item_id,
            decision,
            feedback,
            requeue=requeue,
        )

    def clear_rejects(self, run_id: str) -> None:
        return self._repository(self._runs).clear_rejects(run_id)

    def record_sensitive_tool_input(self, run_id: str) -> int:
        return self._repository(self._runs).record_sensitive_tool_input(run_id)

    def clear_sensitive_tool_inputs(self, run_id: str) -> None:
        return self._repository(self._runs).clear_sensitive_tool_inputs(run_id)

    def side_effects_blocked(self, run_id: str) -> bool:
        return self._repository(self._runs).side_effects_blocked(run_id)

    def begin_durable_intent(
        self,
        item_id: str,
        *,
        preconditions: dict[str, object],
    ) -> str:
        return self._repository(self._execution).begin_durable_intent(
            item_id,
            preconditions=preconditions,
        )

    def side_effect_authorized(self, item_id: str) -> bool:
        return self._repository(self._execution).side_effect_authorized(item_id)

    def has_read_evidence(
        self, run_id: str, path: str, sha256: str
    ) -> bool:
        return self._repository(self._execution).has_read_evidence(run_id, path, sha256)

    def record_protocol_error(self, run_id: str) -> int:
        return self._repository(self._runs).record_protocol_error(run_id)

    def clear_protocol_errors(self, run_id: str) -> None:
        return self._repository(self._runs).clear_protocol_errors(run_id)

    def pause_run(self, run_id: str, reason: str) -> dict[str, object]:
        return self._repository(self._runs).pause_run(run_id, reason)

    def pause_run_committed(
        self, run_id: str, reason: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._runs).pause_run_committed(run_id, reason)

    def begin_finalization(self, run_id: str) -> dict[str, object]:
        return self._repository(self._runs).begin_finalization(run_id)

    def begin_finalization_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._runs).begin_finalization_committed(run_id)

    def begin_finalization_attempt_committed(
        self, run_id: str, *, model_id: str
    ) -> CommittedMutation[tuple[dict[str, object], dict[str, object]]]:
        return self._repository(
            self._runs
        ).begin_finalization_attempt_committed(run_id, model_id=model_id)

    def read_finalization_attempts(
        self, run_id: str
    ) -> tuple[dict[str, object], ...]:
        return self._repository(self._runs).read_finalization_attempts(run_id)

    def stop_run(self, run_id: str, reason: str) -> dict[str, object]:
        return self._repository(self._runs).stop_run(run_id, reason)

    def stop_run_committed(
        self, run_id: str, reason: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._runs).stop_run_committed(run_id, reason)

    def complete_finalization_and_stop_committed(
        self,
        item_id: str | None,
        run_id: str,
        stop_reason: str,
        *,
        attempt_id: str | None = None,
        attempt_status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> CommittedMutation[
        tuple[dict[str, object] | None, dict[str, object]]
    ]:
        return self._repository(self._runs).complete_finalization_and_stop_committed(
            item_id,
            run_id,
            stop_reason,
            attempt_id=attempt_id,
            attempt_status=attempt_status,
            error_code=error_code,
            error_message=error_message,
        )

    def fail_run(self, run_id: str, error_code: str) -> dict[str, object]:
        return self._repository(self._runs).fail_run(run_id, error_code)

    def fail_run_committed(
        self, run_id: str, error_code: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._runs).fail_run_committed(run_id, error_code)

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> dict[str, object]:
        return self._repository(self._runs).cancel_run(run_id, operation_id=operation_id)

    def request_cancel_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._runs).request_cancel_committed(run_id)

    def mark_cancel_failed_committed(
        self, run_id: str, failure_code: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._runs).mark_cancel_failed_committed(
            run_id, failure_code
        )

    def complete_requested_cancel_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._runs).complete_requested_cancel_committed(run_id)

    def nonterminal_run_ids(self) -> tuple[str, ...]:
        return self._repository(self._runs).nonterminal_run_ids()

    def cancel_run_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._runs).cancel_run_committed(run_id)

    def cancel_waiting_approval_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._runs).cancel_waiting_approval_committed(run_id)

    def interrupt_run(self, run_id: str) -> dict[str, object]:
        return self._repository(self._runs).interrupt_run(run_id)

    def interrupt_run_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._repository(self._runs).interrupt_run_committed(run_id)

    def canceled_items_for_run(self, run_id: str) -> list[dict[str, object]]:
        return self._repository(self._runs).canceled_items_for_run(run_id)

    def context_projection_facts(self, run_id: str) -> ContextFacts:
        return self._repository(self._context).context_projection_facts(run_id)

    def compaction_candidate_facts(self, run_id: str) -> ContextFacts:
        return self._repository(self._context).compaction_candidate_facts(run_id)

    def latest_compact_summary(self, run_id: str) -> CompactSummary | None:
        return self._repository(self._context).latest_compact_summary(run_id)

    def compaction_count(self, run_id: str) -> int:
        return self._repository(self._context).compaction_count(run_id)

    def commit_compaction(
        self, run_id: str, phase: str, summary: CompactSummary
    ) -> CommittedMutation[CompactSummary]:
        return self._repository(self._context).commit_compaction(run_id, phase, summary)

    def enqueue_input(self, run_id: str, content: str) -> str:
        return self._repository(self._execution).enqueue_input(run_id, content)

    def has_pending_input(self, run_id: str) -> bool:
        return self._repository(self._execution).has_pending_input(run_id)

    def consume_pending_inputs(self, run_id: str) -> int:
        return self._repository(self._execution).consume_pending_inputs(run_id)

    def consume_pending_input_facts(
        self, run_id: str
    ) -> tuple[tuple[str, str], ...]:
        return self._repository(self._execution).consume_pending_input_facts(run_id)

    def operation_result(
        self, operation_id: str, scope: str, request: dict[str, object]
    ) -> object | None:
        return self._database.operation_result(operation_id, scope, request)

    def record_operation_result(
        self,
        operation_id: str,
        scope: str,
        request: dict[str, object],
        result: dict[str, object],
    ) -> dict[str, object]:
        return self._database.execute_idempotent(
            lambda _connection: result,
            operation_id=operation_id,
            operation_scope=scope,
            operation_request=request,
        )
