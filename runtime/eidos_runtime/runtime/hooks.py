"""Run-scoped, durable command Hooks using the existing Shell sandbox and owner."""
from __future__ import annotations

from contextlib import AbstractContextManager, ExitStack, nullcontext
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Callable

import anyio
from pydantic import JsonValue, TypeAdapter

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.hooks import HookBinding, HookEvent, HookOutcome, HookOutput, HookPendingEvent, HookResult
from eidos_runtime.extensions.codex_hooks import matches, parse_output
from eidos_runtime.extensions.plugins import PluginCatalog, _private_directory
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.persistence.hooks import HookRepository
from eidos_runtime.persistence.shell_environment import ShellEnvironmentRepository
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.contracts import RuntimeCancelled
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.runtime_dependencies import RuntimeDependencyCoordinator
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile, BasePermissionProfile, FileSystemAccessMode,
    FileSystemPermissionEntry, SandboxAttempt, SandboxType,
    materialize_effective_profile, merge_permissions,
)
from eidos_runtime.sandbox.sensitive import SensitiveScanner
from eidos_runtime.sandbox.shell import prepare_shell_launch_for_execution, run_shell_process
from eidos_runtime.sandbox.workspace_manifest import capture_workspace_manifest, diff_workspace_manifests
from eidos_runtime.model.client import CustomToolPayload, ModelToolCall
from eidos_runtime.tools.contracts import RunShellInput


class HookService:
    def __init__(
        self, store: SessionStore, run_id: str, *, resources: ResourceRegistry,
        sensitive: SensitiveScanner, events: RuntimeEvents,
        async_kernel: RuntimeAsyncKernel | None = None,
        runtime_dependencies: RuntimeDependencyCoordinator | None = None,
        launch_window: Callable[[threading.Event], AbstractContextManager[object]] | None = None,
    ) -> None:
        self.store, self.run_id = store, run_id
        self.repository = HookRepository(store.database)
        self.catalog = PluginCatalog(store)
        self.resources, self.sensitive, self.events = resources, sensitive, events
        self.async_kernel, self.dependencies = async_kernel, runtime_dependencies
        self.launch_window = launch_window or (lambda _cancel: nullcontext())
        self.bindings = tuple(HookBinding.model_validate(value) for value in store.read_run_extension_snapshot(run_id).get("hooks", []))
        self.base = BasePermissionProfile.model_validate_json(store.read_run_resolution_snapshot(run_id).permission_profile_json)
        self.projected_frontier = 0

    @property
    def active(self) -> bool:
        return bool(self.bindings)

    def dispatch(
        self, event: HookEvent, event_key: str, cancel: threading.Event, *,
        names: tuple[str, ...] = (), fields: dict[str, JsonValue] | None = None,
    ) -> HookOutcome:
        selected = tuple(binding for binding in self.bindings if binding.definition.event == event and matches(binding.definition, names))
        if not selected:
            return HookOutcome()
        if cancel.is_set():
            raise RuntimeCancelled
        run = self.store.read_run(self.run_id)
        collaboration = CollaborationRepository(self.store.database)
        parent = collaboration.model_state(self.run_id).parent_run_id
        model = self.store.read_model_profile(self.run_id)
        input_value: dict[str, JsonValue] = {
            "session_id": str(self.store.read_run(parent)["sessionId"] if parent else run["sessionId"]),
            "turn_id": self.run_id, "transcript_path": None,
            "cwd": str(self.store.workspace_for_run(self.run_id).path),
            "hook_event_name": event, "model": model.remote_model_id or model.model_id,
            "permission_mode": "plan" if run.get("workMode") == "plan" else "bypassPermissions" if self.base.full_access else "default",
            **(fields or {}),
        }
        input_json = json.dumps(input_value, ensure_ascii=False, separators=(",", ":"))
        if len(input_json.encode("utf-8")) > 1024 * 1024:
            for binding in selected:
                invocation_id, _saved, created = self.repository.reserve(self.run_id, binding, event_key,
                    json.dumps({"hook_event_name": event, "input_omitted": True, "input_bytes": len(input_json.encode("utf-8"))}))
                if created:
                    self.repository.complete(invocation_id, HookResult(status="failed", error_code="hook_input_limit"))
            self.events.deliver_pending()
            return HookOutcome()
        completed: list[tuple[int, HookResult]] = []

        async def coordinate() -> None:
            limiter = anyio.CapacityLimiter(8)

            async def execute(index: int, binding: HookBinding) -> None:
                result = await anyio.to_thread.run_sync(self._execute, binding, event_key, input_json, cancel, limiter=limiter)
                completed.append((index, result))

            async with anyio.create_task_group() as group:
                for index, binding in enumerate(selected):
                    group.start_soon(execute, index, binding)

        if len(selected) > 1 and self.async_kernel is not None:
            self.async_kernel.call(coordinate)
        else:
            completed.extend((index, self._execute(binding, event_key, input_json, cancel)) for index, binding in enumerate(selected))
        if cancel.is_set():
            raise RuntimeCancelled
        self.events.deliver_pending()
        results = [result for _, result in sorted(completed)]
        updated = next((result.updated_input for _, result in reversed(completed) if result.updated_input is not None), None)
        return HookOutcome(
            deny=any(result.action == "deny" for result in results),
            stop=any(result.action == "stop" for result in results),
            continuation=tuple(result.message for result in results if result.action == "continue"),
            feedback=tuple(result.message for result in results if result.action in {"feedback", "deny"}),
            context=tuple(result.additional_context for result in results if result.additional_context),
            updated_input=None if any(result.action == "deny" for result in results) else updated,
        )

    def _execute(self, binding: HookBinding, event_key: str, input_json: str, cancel: threading.Event) -> HookResult:
        invocation_id, saved, created = self.repository.reserve(self.run_id, binding, event_key, input_json)
        if not created:
            assert saved is not None
            return saved
        started = time.monotonic()
        launched = False
        before = None
        output = None
        launch_scope = ExitStack()
        try:
            if cancel.is_set():
                raise RuntimeCancelled
            launch_scope.enter_context(self.launch_window(cancel))
            self.catalog.package(binding.plugin_id, expected_hash=binding.plugin_hash)
            root = self.catalog.installed_root(binding.plugin_id)
            data = self.catalog.root / binding.plugin_id / "hook-data"
            _private_directory(data)
            base = self.base.model_copy(update={"active_skill_roots": (*self.base.active_skill_roots, str(root))})
            dependency_environment = None
            if self.dependencies is not None:
                dependency = self.dependencies.default_binding()
                if dependency is not None:
                    base = self.dependencies.permission_profile(base, dependency)
                    dependency_environment = self.dependencies.shell_environment(dependency)
            # Trust review grants only this plugin's own private writable data directory.
            permissions = materialize_effective_profile(base, merge_permissions(
                self.store.run_permission_grants(self.run_id),
                AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(path=str(data), access=FileSystemAccessMode.WRITE),)),
            ), allow_protected=True)
            workspace = self.store.workspace_for_run(self.run_id)
            attempt = SandboxAttempt(ordinal=0, sandbox=SandboxType.NONE if base.full_access else SandboxType.MACOS_SEATBELT,
                sandboxRequested=not base.full_access, permissions=permissions,
                sandboxCwd=str(workspace.path), workspaceRoots=(str(workspace.path),))
            launch = prepare_shell_launch_for_execution(workspace, binding.definition.command, workspace, attempt,
                active_skill_roots=(root,), dependency_environment=dependency_environment,
                environment_policy=ShellEnvironmentRepository(self.store.database).read().policy)
            environment = {**launch.environment, "PLUGIN_ROOT": str(root), "PLUGIN_DATA": str(data),
                           "CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_PLUGIN_DATA": str(data)}
            if dependency_environment is None:
                environment["RUNTIME_PYTHON"] = sys.executable
                environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
            launch = replace(launch, environment=environment)
            before = capture_workspace_manifest(workspace.path)

            def mark_started() -> None:
                nonlocal launched
                launched = True
                self.repository.started(invocation_id, attempt.model_dump_json(by_alias=True))
                launch_scope.close()

            raw = run_shell_process(launch, timeout_seconds=binding.definition.timeout_seconds, cancel=cancel,
                # Package checks and host environment capture do not consume the command timeout.
                on_delta=lambda _text: None, started=time.monotonic(), resource_registry=self.resources,
                owner_id=invocation_id, stdin_bytes=input_json.encode("utf-8"),
                on_started=mark_started)
            body = raw.get("data", {})
            stdout = self.sensitive.redact_for_presentation(str(body.get("stdout", ""))).text
            stderr = self.sensitive.redact_for_presentation(str(body.get("stderr", ""))).text
            output = HookOutput(stdout=stdout[:262144], stderr=stderr[:262144],
                raw_truncated=body.get("truncated") is True or len(stdout) > 262144 or len(stderr) > 262144,
                raw_omitted_bytes=int(body.get("omittedBytes", 0)) + len(stdout[262144:].encode("utf-8")) + len(stderr[262144:].encode("utf-8")))
            if body.get("termination") != "exit":
                result = HookResult(status="unknown", error_code="hook_" + str(body.get("termination", "execution_failed")))
            elif output.raw_truncated:
                result = HookResult(status="failed", error_code="hook_output_limit")
            else:
                result = parse_output(binding.definition.event, stdout, stderr, body.get("exitCode"))
                if result.updated_input is not None and json.loads(input_json).get("tool_name") in {"Bash", "apply_patch"} and not isinstance(result.updated_input.get("command"), str):
                    result = HookResult(status="failed", error_code="hook_updated_input_invalid")
            # ContextLimit=0 removes the Hook-local soft limit, never the Runtime hard bound.
            limit = min(65536, binding.definition.additional_context_limit * 4) if binding.definition.additional_context_limit else 65536
            result = result.model_copy(update={"additional_context": result.additional_context[:limit],
                "context_truncated": result.context_truncated or len(result.additional_context) > limit,
                "output_call_id": "hook:" + invocation_id,
                "duration_ms": max(0, int((time.monotonic() - started) * 1000))})
        except RuntimeCancelled:
            result = HookResult(status="unknown" if launched else "failed", error_code="hook_canceled")
        except Exception as error:
            # Keep command/configuration text and environment values out of logs and errors.
            result = HookResult(status="unknown" if launched else "failed", error_code="hook_execution_" + type(error).__name__)
        finally:
            launch_scope.close()
        if before is not None and launched:
            after = capture_workspace_manifest(self.store.workspace_for_run(self.run_id).path)
            diff = diff_workspace_manifests(before, after)
            result = result.model_copy(update={"workspace_changed": diff.changed,
                "workspace_changes": tuple((*diff.created, *diff.modified, *diff.deleted)[:256]) if diff.complete else ()})
        self.repository.complete(invocation_id, result, output)
        return result

    def pending_post_tool(self, call: ModelToolCall, item_id: str, result: dict[str, object]) -> HookPendingEvent | None:
        if not any(binding.definition.event == "PostToolUse" for binding in self.bindings):
            return None
        _name, aliases, fields = self._post_fields(call, result)
        if not any(binding.definition.event == "PostToolUse" and matches(binding.definition, aliases) for binding in self.bindings):
            return None
        return HookPendingEvent(event_key="post:" + item_id, fields=fields)

    def before_completion(self, item_id: str, message: str, cancel: threading.Event) -> HookOutcome:
        self.flush(cancel)
        if self.repository.frontier(self.run_id) > self.projected_frontier:
            return HookOutcome(continuation=("Observe the newly committed Hook feedback before finishing.",))
        collaboration = CollaborationRepository(self.store.database)
        child = collaboration.child_for_run(self.run_id)
        fields: dict[str, JsonValue] = {"stop_hook_active": self.repository.stop_active(self.run_id),
            "last_assistant_message": message}
        names = ()
        if child:
            definition = collaboration.child_definition_for_run(self.run_id)
            agent_type = definition.name if definition else collaboration.child_role_for_run(self.run_id)
            fields.update({"agent_id": str(self.store.read_run(self.run_id)["sessionId"]),
                "agent_type": agent_type, "agent_transcript_path": None})
            names = (str(agent_type),)
        return self.dispatch("SubagentStop" if child else "Stop", "stop:" + item_id, cancel,
            names=names, fields=fields)

    def flush(self, cancel: threading.Event) -> None:
        while pending := self.repository.pending(self.run_id):
            for key, fields in pending:
                if cancel.is_set():
                    raise RuntimeCancelled
                name = str(fields["tool_name"])
                self.dispatch("PostToolUse", key, cancel,
                    names=(name, "Edit", "Write") if name == "apply_patch" else (name,), fields=fields)
                self.repository.delivered(self.run_id, key)

    @staticmethod
    def _post_fields(call: ModelToolCall, result: dict[str, object]) -> tuple[str, tuple[str, ...], dict[str, JsonValue]]:
        arguments = {"patch": call.payload.input} if isinstance(call.payload, CustomToolPayload) else call.arguments
        name, aliases, value = compatible_tool_input(call.name, arguments)
        response = TypeAdapter(JsonValue).validate_python(result)
        return name, aliases, {"tool_name": name, "tool_use_id": call.provider_call_id, "tool_input": value, "tool_response": response}


def compatible_tool_input(name: str, arguments: dict[str, object]) -> tuple[str, tuple[str, ...], dict[str, JsonValue]]:
    value = TypeAdapter(dict[str, JsonValue]).validate_python(arguments)
    if name == "run_shell":
        value = TypeAdapter(dict[str, JsonValue]).validate_python(
            RunShellInput.model_validate_json(json.dumps(arguments)).model_dump(mode="json", by_alias=True, exclude_none=True))
        return "Bash", ("Bash",), value
    if name == "apply_patch":
        return "apply_patch", ("apply_patch", "Edit", "Write"), {"command": value.get("patch", "")}
    return name, (name,), value


def rewritten_arguments(name: str, updated: dict[str, JsonValue]) -> dict[str, object]:
    if name in {"run_shell", "apply_patch"}:
        if not isinstance(updated.get("command"), str):
            raise ValueError("hook_updated_input_invalid")
        if name == "apply_patch":
            return {"patch": updated["command"]}
    return dict(updated)
