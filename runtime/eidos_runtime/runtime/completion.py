from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import TYPE_CHECKING

from pydantic import ValidationError

from eidos_runtime.context.budget import estimate_model_request_budget
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.completion import CompletionAssessment, CompletionCheckRecord, CompletionFacts, CompletionOutcome
from eidos_runtime.memory.context import context_epochs
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.model.client import AssistantMessagePhase, FunctionToolDefinition, ModelClient
from eidos_runtime.persistence.completion import CompletionRepository
from eidos_runtime.runtime.contracts import RuntimeCancelled, SamplingOutcome, StepContext
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.model_runner import ModelRunner, ModelStepResult
from eidos_runtime.runtime.protocol_diagnostics import ProtocolDiagnostic, response_text_metrics
from eidos_runtime.runtime.resource_registry import ResourceRegistry, RuntimeResourceKind
from eidos_runtime.runtime.resolution import RuleResolutionSnapshot, canonical_sha256
from eidos_runtime.runtime.sampling import SamplingMemoryRevoked
from eidos_runtime.sandbox.sensitive import SensitiveScanner
from eidos_runtime.telemetry.tracing import finish_model_attempt, model_attempt_span
from eidos_runtime.workspace.reader import WorkspacePathError, WorkspaceReader, file_version


logger = logging.getLogger("eidos.runtime.completion")
if TYPE_CHECKING:
    from eidos_runtime.application.context import ContextApplication
COMPLETION_TOOL = FunctionToolDefinition(
    name="_eidos_assess_completion",
    description="Return a structured assessment of the candidate answer. This output schema does not execute an operation.",
    parameters_json_schema=CompletionAssessment.model_json_schema(by_alias=True),
)
COMPLETION_INSTRUCTIONS = """Assess whether the candidate assistant answer can end the current user request.
Use the original request, subsequent user input and the supplied execution facts. A progress announcement or promise of a future action is unfinished work. A tool error alone does not prevent a truthful answer explaining a blocker.
Distinguish generated files, declared final deliverables, structural checks and actual visual inspection. List only final standalone files the user requested in requestedOutputs, using workspace-relative paths. Ordinary source edits and intermediate QA files are not deliverables.
Return exactly one _eidos_assess_completion call. Use complete only if no requested work remains; use continue with concrete remainingWork if authorized work remains; use blocked if the answer truthfully explains why no available action can complete the request. Give a short explanation of observable facts, not reasoning traces.
This is an internal read-only assessment. Do not perform tools, ask the user questions, change permissions, or claim verification that the execution facts do not prove. The Runtime, not this assessment, decides the final lifecycle state."""


class _DeadlineCancellation(threading.Event):
    """A checked deadline without a new timer or background thread."""

    def __init__(self, parent: threading.Event, seconds: float) -> None:
        super().__init__()
        self.parent = parent
        self.deadline = time.monotonic() + seconds

    def is_set(self) -> bool:
        return super().is_set() or self.parent.is_set() or time.monotonic() >= self.deadline

    def wait(self, timeout: float | None = None) -> bool:
        until = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if until is not None and time.monotonic() >= until:
                return False
            super().wait(0.02)
        return True


class CompletionGuard:
    """One persisted assessment per stable execution frontier, before cleanup."""

    def __init__(self, store: SessionStore, model: ModelClient, events: RuntimeEvents,
                 sensitive: SensitiveScanner, resources: ResourceRegistry,
                 context_application: ContextApplication, *, timeout_seconds: float = 60) -> None:
        self.store = store
        self.repository = CompletionRepository(store.database)
        self.runner = ModelRunner(model, sensitive)
        self.events = events
        self.sensitive = sensitive
        self.resources = resources
        self.timeout_seconds = timeout_seconds
        self.context_application = context_application

    def check(self, step: StepContext, sampled: SamplingOutcome, cancel: threading.Event,
              rule_snapshot: RuleResolutionSnapshot, *, collaboration_hash: str) -> CompletionOutcome:
        if (step.model_profile.completion_check_version == 0 or not step.model_profile.supports_tools
                or sampled.end_turn is True
                or (sampled.phase_source == "provider" and sampled.phase is AssistantMessagePhase.FINAL_ANSWER)):
            return CompletionOutcome(action="accept", reason="declared_or_legacy_completion")
        facts = self.repository.facts(step.run_id)
        if facts.pending_input:
            return CompletionOutcome(action="continue", reason="pending_input")
        if not facts.has_tools:
            return CompletionOutcome(action="accept", reason="direct_answer")
        if cancel.is_set():
            raise RuntimeCancelled
        fingerprint = canonical_sha256({
            **facts.model_dump(mode="json", exclude={"outputs"}),
            "collaboration": collaboration_hash, "memory": context_epochs(step.model_context),
        })
        candidate_sha256 = hashlib.sha256(sampled.text.encode("utf-8")).hexdigest()
        prior = self.repository.latest(step.run_id, fingerprint)
        if prior is not None:
            record = prior[0]
            if record.status in {"complete", "blocked"} and record.candidate_sha256 == candidate_sha256:
                return CompletionOutcome(action="accept", reason="completion_checked")
            return CompletionOutcome(action="stop", reason="completion_unconfirmed")
        if sampled.assistant_item is None:
            raise ValueError("completion candidate requires an assistant item")

        attempt_id = self.store.start_retry_model_attempt(step.run_id)
        record = CompletionCheckRecord(
            fingerprint=fingerprint, candidate_item_id=str(sampled.assistant_item["id"]),
            candidate_sha256=candidate_sha256, model_attempt_id=attempt_id,
            status="running", reason="requested",
        )
        self.events.publish(self.repository.record(step.run_id, record), run=self.store.read_run(step.run_id))
        context = (*step.model_context,
                   {"type": "assistant", "content": sampled.text},
                   {"type": "user", "sectionId": "completion-check",
                    "content": "Runtime completion facts, not user instructions or permission:\n" + facts.model_dump_json(by_alias=True)})
        definitions = (COMPLETION_TOOL,)
        instructions = step.instructions.system_text + "\n\n" + COMPLETION_INSTRUCTIONS
        budget = estimate_model_request_budget(
            context, instructions=instructions, tool_definitions=definitions,
            context_window_tokens=step.model_profile.context_window_tokens,
            request_max_output_tokens=step.model_profile.max_output_tokens,
        )
        deadline = _DeadlineCancellation(cancel, self.timeout_seconds)
        resource = self.resources.register(RuntimeResourceKind.ASYNC_REQUEST, step.run_id,
                                           task_id="completion_check", deadline=deadline.deadline, cancel=deadline.set)
        resource.start()
        result: ModelStepResult | None = None
        memory_revoked = False
        admission = None
        try:
            if not budget.fits:
                raise ValueError("completion_context_over_budget")
            snapshot = self.context_application.capture_and_persist_model_attempt(
                run_id=step.run_id, model_attempt_id=attempt_id, model_profile=step.model_profile,
                rule_snapshot=rule_snapshot, model_context=context, instructions=instructions,
                tool_definitions=definitions, token_budget=budget,
            )
            progress = self.store.long_task_repository()
            if progress.read(step.run_id) is not None:
                progress.record_snapshots(step.run_id, context_plan_id=snapshot.plan_id, context_snapshot_id=snapshot.snapshot_id)
            with self.store.database.memory.admit(context_epochs(context), deadline) as admission:
                with model_attempt_span(step.run_id, step.step_id, step.model_id, step.model_profile.provider_id) as span:
                    result = self.runner.run(context, admission, lambda _: None,
                                             instructions=instructions, tool_definitions=definitions)
                    finish_model_attempt(span, result)
                if admission.is_set() and not deadline.is_set():
                    raise MemoryRejected("memory_snapshot_revoked")
            if deadline.is_set():
                raise TimeoutError("completion_check_timeout")
            assessment = self._assessment(result)
            record = record.model_copy(update={
                "status": assessment.state, "reason": "assessed",
                "explanation": assessment.explanation,
                "remaining_work": assessment.remaining_work, "requested_outputs": assessment.requested_outputs,
            })
            if assessment.state == "complete" and not self._outputs_current(step, assessment, facts, cancel):
                record = record.model_copy(update={"status": "continue", "reason": "outputs_not_declared_or_changed",
                    "remaining_work": ("Verify and declare the final user deliverables with declare_outputs before giving the final answer.",)})
        except MemoryRejected:
            memory_revoked = True
            record = record.model_copy(update={"status": "failed", "reason": "memory_snapshot_revoked"})
        except (ValueError, ValidationError):
            record = record.model_copy(update={"status": "failed", "reason": "invalid_response"})
        except Exception as error:
            logger.warning("Completion check failed run_id=%s attempt_id=%s error_type=%s", step.run_id, attempt_id, type(error).__name__)
            memory_revoked = admission is not None and admission.is_set() and not deadline.is_set()
            record = record.model_copy(update={"status": "failed", "reason": "memory_snapshot_revoked" if memory_revoked else "request_failed"})
        finally:
            try:
                if record.status != "running":
                    self._finish(step.run_id, record, result, cancel)
            finally:
                resource.close()
        if cancel.is_set():
            raise RuntimeCancelled
        if memory_revoked:
            raise SamplingMemoryRevoked("memory_snapshot_revoked")
        if record.status == "continue":
            return CompletionOutcome(action="continue", reason="unfinished_work")
        if record.status in {"complete", "blocked"}:
            return CompletionOutcome(action="accept", reason="completion_checked")
        return CompletionOutcome(action="stop", reason="completion_unconfirmed")

    def _assessment(self, result: ModelStepResult) -> CompletionAssessment:
        if (result.response_state not in {None, "complete"} or result.finish_reason in {"length", "content_filter", "error"}
                or len(result.tool_calls) != 1):
            raise ValueError("invalid_completion_assessment")
        call = result.tool_calls[0]
        if call.name != COMPLETION_TOOL.name or call.payload_kind != "function":
            raise ValueError("invalid_completion_assessment")
        scanned = self.sensitive.scan_json(call.arguments)
        return CompletionAssessment.model_validate_json(json.dumps(scanned))

    def _outputs_current(self, step: StepContext, assessment: CompletionAssessment, facts: CompletionFacts, cancel: threading.Event) -> bool:
        declared = {output.path: output.version for output in facts.outputs}
        if any(path not in declared for path in assessment.requested_outputs):
            return False
        try:
            with WorkspaceReader(self.store.workspace_for_run(step.run_id)) as reader:
                return all(file_version(reader.stat_file(path, cancel=cancel)) == declared[path] for path in assessment.requested_outputs)
        except WorkspacePathError:
            return False

    def _finish(self, run_id: str, record: CompletionCheckRecord, result: ModelStepResult | None, cancel: threading.Event) -> None:
        bytes_count, sha256 = response_text_metrics(result.text if result else "")
        self.store.complete_current_model_attempt(
            run_id, "canceled" if cancel.is_set() else "failed" if record.status == "failed" else "completed",
            usage=result.usage if result else None,
            provider_name=result.provider_name if result else None,
            resolved_model_name=result.resolved_model_name if result else None,
            finish_reason=result.finish_reason if result else None,
            provider_response_id=result.provider_response_id if result else None,
            error_code=record.reason if record.status == "failed" else None,
            duration_ms=result.duration_ms if result else None, ttft_ms=result.ttft_ms if result else None,
            response_state=result.response_state if result else None,
            phase=result.phase.value if result and result.phase else None,
            tool_call_count=len(result.tool_calls) if result else 0,
            response_text_bytes=bytes_count, response_text_sha256=sha256,
            protocol_diagnostic=ProtocolDiagnostic(stage="response_completion", code="completion_check_" + record.status),
            retry_decision={"retry": False, "reason": "completion_check"}, completion_check=record,
        )
        saved = self.repository.latest(run_id, record.fingerprint)
        if saved is not None:
            self.events.publish_event(saved[1], run=self.store.read_run(run_id))
        logger.info("Completion check recorded run_id=%s attempt_id=%s decision=%s reason=%s", run_id, record.model_attempt_id, record.status, record.reason)
