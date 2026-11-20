"""Bring a child's Worktree delivery into the parent workspace.

The tool is a workspace mutation: it prepares ordinary file changes and the
parent Run commits them through the same controlled file path as apply_patch,
with the same permissions, Durable Intent, version checks and unknown-outcome
records. Nothing is written when any requested file conflicts.
"""
from __future__ import annotations

import hashlib
import threading
from typing import TYPE_CHECKING

from eidos_runtime.application.agent_workspaces import AgentWorkspaceError, Delivery
from eidos_runtime.domain.collaboration import IntegrateAgentChanges
from eidos_runtime.tools.registry import ToolProvenance, ToolRegistryEntry, ToolSpec
from eidos_runtime.tools.workspace import (
    AppliedPatchDelta, FileChange, PreparedPatch, ToolExecutor, _error, _make_patch_change,
)

if TYPE_CHECKING:
    from eidos_runtime.application.agent_workspaces import AgentWorkspaceService
    from eidos_runtime.application.teams import TeamApplication

TOOL_NAME = 'integrate_agent_changes'
_DESCRIPTION = (
    'Bring the changes of an idle child that runs in its own Worktree into your workspace. '
    'Files you have not changed since the child started take the child version; text files changed on both sides '
    'are merged when Git finds no conflict. If any requested file conflicts, or is binary, nothing is written and '
    'the result lists those paths: integrate the others with paths, then resolve the rest yourself. '
    'Review the changes first with review_agent_changes.'
)


class DeliveryIntegrationAdapter:
    """Prepare file changes from one Worktree delivery for the parent's file-change pipeline."""

    tool_name = TOOL_NAME

    def __init__(self, executor: ToolExecutor, spec: ToolSpec, workspaces: AgentWorkspaceService, run_id: str) -> None:
        self.executor = executor
        self.spec = spec
        self.workspace = executor.workspace
        self.workspaces = workspaces
        self.run_id = run_id
        self._planned: dict[int, tuple[str, bool]] = {}
        self._lock = threading.Lock()

    def execute(self, arguments: dict[str, object], cancel: threading.Event) -> dict[str, object]:
        raise RuntimeError('integrate_agent_changes requires the Run runtime')

    def _delivery(self, arguments: object) -> tuple[Delivery, list[str] | None]:
        request = IntegrateAgentChanges.model_validate(arguments)
        return self.workspaces.capture(self.run_id, request.agent_id), request.paths

    def prepare_file_change(self, arguments: object, cancel: threading.Event) -> PreparedPatch | dict[str, object]:
        TOOL_NAME = self.tool_name
        try:
            delivery, paths = self._delivery(arguments)
            prefix = self.workspaces.git.show_prefix(self.workspace.path)
        except AgentWorkspaceError as error:
            return _error(TOOL_NAME, error.code, 'The changes are not available')
        entries = {entry.path: entry for entry in delivery.entries}
        requested = list(dict.fromkeys(paths)) if paths is not None else list(entries)
        unknown = [path for path in requested if path not in entries]
        if unknown:
            return _error(TOOL_NAME, 'agent_change_path_unknown', 'Not a changed path: ' + ', '.join(unknown[:20]))
        changes: list[FileChange] = []
        blocked: list[str] = []
        for path in requested:
            entry = entries[path]
            if prefix and not path.startswith(prefix):
                blocked.append(f'{path} (outside this workspace)')
                continue
            relative = path[len(prefix):]
            if entry.binary:
                blocked.append(f'{path} (binary)')
                continue
            try:
                existing = self.executor._read_existing_for_change(relative, cancel, allow_missing_parents=True)
                base = self.workspaces.read_object(delivery, entry.base) if entry.base else None
                result = self.workspaces.read_object(delivery, entry.result) if entry.result else None
            except AgentWorkspaceError as error:
                return _error(TOOL_NAME, error.code, 'The changes are not available')
            except Exception:
                blocked.append(f'{path} (unreadable in this workspace)')
                continue
            current = existing[0] if existing is not None else None
            if current == result:
                continue
            candidate: bytes | None
            if current == base:
                candidate = result
            elif current is not None and base is not None and result is not None:
                try:
                    candidate = self.workspaces.merge_text(delivery, current, base, result)
                except AgentWorkspaceError:
                    candidate = None
                if candidate is None:
                    blocked.append(f'{path} (conflict)')
                    continue
            else:
                blocked.append(f'{path} (conflict)')
                continue
            if candidate == current:
                # A previous integration already produced this content.
                continue
            mode = entry.mode & 0o777 or 0o644
            try:
                if candidate is None:
                    changes.append(_make_patch_change(path=relative, candidate=b'', existing=existing,
                        mode=existing[1] if existing else 0o644, kind='delete', delete=True))
                else:
                    changes.append(_make_patch_change(path=relative, candidate=candidate, existing=existing,
                        mode=mode, kind='add' if existing is None else 'update', create_missing_parent=existing is None))
            except UnicodeDecodeError:
                blocked.append(f'{path} (not UTF-8 text)')
        if blocked:
            return _error(TOOL_NAME, 'agent_changes_conflict',
                'Nothing was written. These paths need your decision: ' + '; '.join(blocked[:50]))
        if not changes:
            return _error(TOOL_NAME, 'agent_changes_already_integrated', 'Every requested file already matches the child version')
        prepared = PreparedPatch(changes=tuple(changes), diff=''.join(change.diff for change in changes))
        with self._lock:
            self._planned[id(prepared)] = (delivery.id, len(requested) == len(entries))
        return prepared

    def commit_patch(self, prepared: PreparedPatch, cancel: threading.Event) -> tuple[dict[str, object], AppliedPatchDelta]:
        result, delta = self.executor.commit_patch(self.tool_name, prepared, cancel)
        with self._lock:
            planned = self._planned.pop(id(prepared), None)
        if planned is not None and delta.changes:
            self.workspaces.mark_integration(planned[0], complete=planned[1] and result.get('outcome') == 'success')
        return result, delta

    def commit_file_change(self, change: FileChange, cancel: threading.Event) -> dict[str, object]:
        return self.executor.commit_file_change(self.tool_name, change, cancel)


class AgentIntegrationAdapter(DeliveryIntegrationAdapter):
    pass


def agent_integration_entry(executor: ToolExecutor, workspaces: AgentWorkspaceService, run_id: str) -> ToolRegistryEntry:
    patch_entry = next(entry for entry in executor_registry_entries(executor) if entry.spec.name == 'apply_patch')
    spec = ToolSpec(name=TOOL_NAME, description=_DESCRIPTION, sideEffect='workspace', approvalRequired=False,
        timeoutSeconds=120, inputSchema=IntegrateAgentChanges.model_json_schema(by_alias=True),
        resultSchema=patch_entry.spec.result_schema)
    provenance = ToolProvenance(kind='builtin', sourceId='eidos.collaboration', sourceVersion='1',
        contentHash=hashlib.sha256(spec.model_dump_json().encode()).hexdigest())
    adapter = AgentIntegrationAdapter(executor, spec, workspaces, run_id)
    return ToolRegistryEntry(spec, provenance, adapter, IntegrateAgentChanges, patch_entry.result_data_model,
        projector=patch_entry.projector, execution_policy=patch_entry.execution_policy)


def executor_registry_entries(executor: ToolExecutor) -> tuple[ToolRegistryEntry, ...]:
    return executor.registry.entries



TEAM_TOOL_NAME = 'team_integrate'
_TEAM_DESCRIPTION = (
    "Bring a team member's Worktree changes into your workspace, by task (its submitted delivery) or by member "
    "(its current files; the member must be idle). Files you have not changed take the member version; text files "
    "changed on both sides are merged when Git finds no conflict. If any requested file conflicts or is binary, "
    "nothing is written and the result lists those paths: integrate the others with paths."
)


class TeamIntegrationAdapter(DeliveryIntegrationAdapter):
    tool_name = TEAM_TOOL_NAME

    def __init__(self, executor: ToolExecutor, spec: ToolSpec, workspaces: AgentWorkspaceService, run_id: str,
                 teams: TeamApplication) -> None:
        super().__init__(executor, spec, workspaces, run_id)
        self.teams = teams

    def _delivery(self, arguments: object) -> tuple[Delivery, list[str] | None]:
        from eidos_runtime.domain.teams import TeamIntegrateRequest, TeamRejected

        request = TeamIntegrateRequest.model_validate(arguments)
        role = self.teams.repository.role_for_run(self.run_id)
        if role is None or role.role != 'lead' or role.team_id is None:
            raise AgentWorkspaceError('team_not_active')
        try:
            if request.task is not None:
                task = self.teams.repository.task_row(role.team_id, request.task)
                if task['delivery_id'] is not None:
                    delivery = self.workspaces.delivery(task['delivery_id'])
                    if delivery is not None and delivery.status in {'ready', 'partially_integrated', 'integrated'}:
                        return delivery, request.paths
                if task['owner_member_id'] is None:
                    raise AgentWorkspaceError('delivery_failed')
                name = next(member.name for member in self.teams.repository.team(role.team_id).members
                            if member.id == task['owner_member_id'])
            else:
                name = str(request.member)
            return self.workspaces.capture_owner(self.teams.member_owner(role.team_id, name)), request.paths
        except TeamRejected as error:
            raise AgentWorkspaceError(str(error)) from error


def team_integration_entry(executor: ToolExecutor, workspaces: AgentWorkspaceService, run_id: str,
                           teams: TeamApplication) -> ToolRegistryEntry:
    from eidos_runtime.domain.teams import TeamIntegrateRequest

    patch_entry = next(entry for entry in executor_registry_entries(executor) if entry.spec.name == 'apply_patch')
    spec = ToolSpec(name=TEAM_TOOL_NAME, description=_TEAM_DESCRIPTION, sideEffect='workspace', approvalRequired=False,
        timeoutSeconds=120, inputSchema=TeamIntegrateRequest.model_json_schema(by_alias=True),
        resultSchema=patch_entry.spec.result_schema)
    provenance = ToolProvenance(kind='builtin', sourceId='eidos.team', sourceVersion='1',
        contentHash=hashlib.sha256(spec.model_dump_json().encode()).hexdigest())
    adapter = TeamIntegrationAdapter(executor, spec, workspaces, run_id, teams)
    return ToolRegistryEntry(spec, provenance, adapter, TeamIntegrateRequest, patch_entry.result_data_model,
        projector=patch_entry.projector, execution_policy=patch_entry.execution_policy)
