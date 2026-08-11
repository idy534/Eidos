from __future__ import annotations

from datetime import UTC, datetime
import logging
from pathlib import Path
import time
import uuid
from collections.abc import Callable

from eidos_runtime.db.database import Database, WorkspaceIdentity
from eidos_runtime.db.errors import ResourceNotFoundError
from eidos_runtime.domain.project import Project
from eidos_runtime.domain.worktree import (
    OrphanWorktreeCandidate,
    Worktree,
    WorktreeCleanupReport,
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
    WorktreeOwnership,
    WorktreeRecoveryReport,
    WorktreeState,
    WorktreeValidation,
    WorktreeView,
)
from eidos_runtime.git.discovery import GitRepositoryDiscoveryService
from eidos_runtime.git.backend import (
    DulwichGitBackend,
    GitBackend,
    NativeGitFallback,
)
from eidos_runtime.git.errors import (
    GitCommandFailedError,
    GitCommandTimeoutError,
    WorktreeError,
)
from eidos_runtime.git.models import (
    GitRepositoryDiscovery,
    GitWorktreeEntry,
    ProjectResolution,
)
from eidos_runtime.git.process import (
    DEFAULT_GIT_DIFF_BYTES,
    GitCommandResult,
    GitProcess,
)
from eidos_runtime.git.status import (
    DiffScope,
    GitDiffSnapshot,
    GitStatusSnapshot,
    parse_porcelain_v2_status,
    utc_now,
)
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository
from eidos_runtime.persistence.worktree_lifecycle import WorktreeLifecycleRepository


MAX_BRANCH_COLLISION_ATTEMPTS = 8


class WorktreeManager:
    """Runtime-owned Git Worktree lifecycle module.

    The interface hides the non-atomic Git/SQLite compensation sequence. Git
    remains the authority for current HEAD, dirty state, and diff contents;
    SQLite stores only durable lifecycle facts.
    """

    def __init__(
        self,
        database: Database,
        *,
        managed_root: Path | None = None,
        git_process: GitProcess | None = None,
        git_backend: GitBackend | None = None,
        repository: ProjectWorktreeRepository | None = None,
        id_factory: Callable[[], str] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if database.health_state != "ready":
            raise WorktreeError("storage_not_ready")
        self.database = database
        self.logger = logger or logging.getLogger(__name__)
        self.git: GitBackend = git_backend or git_process or DulwichGitBackend(
            native_fallback=NativeGitFallback(logger=self.logger)
        )
        self.discovery = GitRepositoryDiscoveryService(self.git)
        self.repository = repository or ProjectWorktreeRepository(database)
        self.lifecycle = WorktreeLifecycleRepository(database)
        self._id_factory = id_factory or (lambda: f"wt_{uuid.uuid4().hex}")
        self.managed_root = self._resolve_managed_root(managed_root)

    def project(self, project_id: str) -> Project:
        project = self.repository.read_project(project_id)
        if project is None:
            raise WorktreeError("repository_not_found")
        return project

    def discover(self, repository_seed: Path | str) -> GitRepositoryDiscovery:
        """Expose read-only repository discovery to application ports."""

        return self.discovery.discover(repository_seed)

    def resolve_project(self, workspace_seed: Path | str) -> ProjectResolution:
        """Resolve a filesystem workspace and its optional Git capability."""

        seed = Path(workspace_seed)
        if seed.is_symlink():
            raise WorktreeError("workspace_symlink")
        try:
            workspace = seed.resolve(strict=True)
        except OSError as error:
            raise WorktreeError("repository_not_found") from error
        if not workspace.is_dir():
            raise WorktreeError("repository_not_found")
        discovery = self.discovery.resolve(workspace)
        if discovery is None:
            project = self.repository.get_or_create_project(workspace)
            return ProjectResolution(project=project, git=None)
        project = self.repository.get_or_create_project(
            discovery.repository_root, discovery
        )
        return ProjectResolution(project=project, git=discovery)

    def create(
        self,
        repository_root: Path | str,
        base_ref: str | None = None,
    ) -> Worktree:
        plan = self.prepare_create(repository_root, base_ref=base_ref)
        return self.create_prepared(plan)

    def prepare_create(
        self,
        repository_root: Path | str,
        base_ref: str | None = None,
    ) -> Worktree:
        """Resolve all managed Worktree identity before Git changes state."""

        discovery = self.discovery.discover(repository_root)
        project = self.repository.get_or_create_project(
            discovery.repository_root, discovery
        )
        if base_ref is not None:
            resolved_base_ref = base_ref
        else:
            try:
                resolved_base_ref = self.git.symbolic_ref_short(
                    Path(discovery.repository_root)
                ) or "HEAD"
            except GitCommandTimeoutError:
                raise WorktreeError("git_command_timeout") from None
            except GitCommandFailedError as error:
                raise WorktreeError("git_command_failed") from error
        try:
            base_commit = self.git.resolve_ref(
                Path(discovery.repository_root), resolved_base_ref
            )
        except GitCommandTimeoutError:
            raise WorktreeError("git_command_timeout") from None
        except (GitCommandFailedError, ValueError) as error:
            raise WorktreeError("base_ref_not_found") from error

        self._ensure_managed_root()
        worktree_id, branch, worktree_root = self._allocate_identity(
            Path(discovery.repository_root)
        )
        now = _timestamp(_now_ms())
        return Worktree(
            id=worktree_id,
            project_id=project.id,
            worktree_root=str(worktree_root),
            git_dir=str(worktree_root / ".git"),
            base_ref=resolved_base_ref,
            base_commit=base_commit,
            branch=branch,
            ownership=WorktreeOwnership.MANAGED,
            state=WorktreeState.ACTIVE,
            created_at=now,
            updated_at=now,
        )

    def create_prepared(
        self,
        plan: Worktree,
        *,
        compensate_on_failure: bool = True,
    ) -> Worktree:
        """Create or adopt exactly one already prepared Worktree plan.

        A durable retry never reallocates an id, branch, or root.  An existing
        physical Worktree is adopted only after exact identity validation.
        """

        started = time.monotonic()
        project = self.repository.read_project(plan.project_id)
        if project is None:
            raise WorktreeError("worktree_repository_mismatch")
        self._ensure_managed_root()
        candidate = plan
        existing = self.repository.read_worktree(plan.id)
        if existing is not None:
            if not _same_prepared_worktree(existing, plan):
                raise WorktreeError("worktree_recovery_conflict")
            validation = self.validate(existing.id)
            if validation.valid:
                return validation.worktree
            raise WorktreeError(validation.code or "worktree_invalid")

        self._log_lifecycle(
            logging.INFO,
            "worktree create started",
            worktree_id=plan.id,
            project_id=plan.project_id,
            operation="create",
        )
        creation_attempted = False
        try:
            entries = self._entries_for_observation(project)
            entry = next(
                (
                    item for item in entries
                    if item.worktree_root == plan.worktree_root
                ),
                None,
            )
            root = Path(plan.worktree_root)
            branch_exists = self.git.branch_exists(
                Path(project.workspace_root), plan.branch
            )
            if entry is not None:
                if (
                    entry.branch != plan.branch
                    or entry.head != plan.base_commit
                ):
                    raise WorktreeError("worktree_recovery_conflict")
                created_discovery = self.discovery.discover(root)
                candidate = candidate.model_copy(
                    update={"git_dir": created_discovery.git_dir}
                )
            else:
                if root.exists() or root.is_symlink() or branch_exists:
                    raise WorktreeError("worktree_recovery_conflict")
                creation_attempted = True
                self.git.worktree_add(
                    Path(project.workspace_root),
                    root,
                    plan.branch,
                    plan.base_commit,
                )
                created_discovery = self.discovery.discover(root)
                candidate = candidate.model_copy(
                    update={"git_dir": created_discovery.git_dir}
                )
            validation = self._validate_record(
                candidate,
                project,
                entries=self._worktree_entries(project),
                persist_state=False,
            )
            if not validation.valid:
                raise WorktreeError(validation.code or "worktree_invalid")
            persisted = self.repository.insert_worktree(candidate)
        except GitCommandTimeoutError:
            if creation_attempted and compensate_on_failure:
                self._compensate_create(project, candidate)
            raise WorktreeError("git_command_timeout") from None
        except GitCommandFailedError as error:
            if creation_attempted and compensate_on_failure:
                self._compensate_create(project, candidate)
            if _looks_like_branch_conflict(error):
                raise WorktreeError("worktree_branch_conflict") from error
            raise WorktreeError("worktree_create_failed") from error
        except WorktreeError:
            if creation_attempted and compensate_on_failure:
                self._compensate_create(project, candidate)
            raise
        except Exception as error:
            if creation_attempted and compensate_on_failure:
                self._compensate_create(project, candidate)
            self.logger.error(
                "worktree persistence failed",
                extra={
                    "worktree_id": candidate.id,
                    "project_id": candidate.project_id,
                    "operation": "create",
                    "result": "recovery_needed",
                },
            )
            raise WorktreeError("worktree_persistence_failed") from error
        self._log_lifecycle(
            logging.INFO,
            "worktree created",
            worktree_id=persisted.id,
            project_id=persisted.project_id,
            operation="create",
            duration=time.monotonic() - started,
            result="success",
        )
        return persisted

    def prepared_from_lifecycle(
        self, operation: WorktreeLifecycleOperation
    ) -> Worktree:
        required = (
            operation.project_id,
            operation.worktree_id,
            operation.worktree_root,
            operation.base_ref,
            operation.branch,
            operation.base_commit,
        )
        if any(value is None for value in required):
            raise WorktreeError("worktree_lifecycle_invalid")
        assert (
            operation.project_id is not None
            and operation.worktree_id is not None
            and operation.worktree_root is not None
            and operation.base_ref is not None
            and operation.branch is not None
            and operation.base_commit is not None
        )
        return Worktree(
            id=operation.worktree_id,
            project_id=operation.project_id,
            worktree_root=operation.worktree_root,
            git_dir=str(Path(operation.worktree_root) / ".git"),
            base_ref=operation.base_ref,
            base_commit=operation.base_commit,
            branch=operation.branch,
            ownership=WorktreeOwnership.MANAGED,
            state=WorktreeState.ACTIVE,
            created_at=operation.created_at,
            updated_at=operation.updated_at,
        )

    def recover_lifecycle(self) -> tuple[WorktreeLifecycleOperation, ...]:
        """Reconcile unfinished managed Worktree intents at startup."""

        reconciled: list[WorktreeLifecycleOperation] = []
        for operation in self.lifecycle.list_unfinished():
            started = time.monotonic()
            action = "inspect"
            try:
                if operation.scope in {
                    WorktreeLifecycleScope.SESSION_CREATE,
                    WorktreeLifecycleScope.CHECKPOINT_FORK,
                }:
                    action = "reconcile_create"
                    plan = self.prepared_from_lifecycle(operation)
                    worktree = self.create_prepared(
                        plan, compensate_on_failure=False
                    )
                    validation = self.validate(worktree.id)
                    if not validation.valid:
                        raise WorktreeError(
                            validation.code or "worktree_invalid"
                        )
                    if operation.state is WorktreeLifecycleState.PREPARED:
                        operation = self.lifecycle.update_state(
                            operation.scope,
                            operation.operation_id,
                            WorktreeLifecycleState.WORKTREE_CREATED,
                        )
                    reconciled.append(operation)
                    self._log_recovery(
                        operation,
                        action=action,
                        result="success",
                        duration=time.monotonic() - started,
                    )
                    continue

                action = "reconcile_delete"
                if operation.worktree_id is None:
                    raise WorktreeError("worktree_lifecycle_invalid")
                if operation.state is WorktreeLifecycleState.PREPARED:
                    self.delete(operation.worktree_id)
                worktree = self._read_worktree(operation.worktree_id)
                if worktree.state is not WorktreeState.DELETED:
                    raise WorktreeError("worktree_delete_incomplete")
                operation = self.lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.WORKTREE_DELETED,
                )
                reconciled.append(operation)
                self._log_recovery(
                    operation,
                    action=action,
                    result="success",
                    duration=time.monotonic() - started,
                )
            except (WorktreeError, ResourceNotFoundError) as error:
                error_code = (
                    error.code
                    if isinstance(error, WorktreeError)
                    else "worktree_lifecycle_not_found"
                )
                self._mark_lifecycle_worktree_unavailable(operation, error_code)
                try:
                    operation = self.lifecycle.update_state(
                        operation.scope,
                        operation.operation_id,
                        WorktreeLifecycleState.CLEANUP_REQUIRED,
                        error_code=error_code,
                    )
                except ResourceNotFoundError:
                    pass
                self._log_recovery(
                    operation,
                    action=action,
                    result="cleanup_required",
                    error_code=error_code,
                    duration=time.monotonic() - started,
                )
            except Exception:
                self._mark_lifecycle_worktree_unavailable(
                    operation, "worktree_recovery_failed"
                )
                try:
                    operation = self.lifecycle.update_state(
                        operation.scope,
                        operation.operation_id,
                        WorktreeLifecycleState.CLEANUP_REQUIRED,
                        error_code="worktree_recovery_failed",
                    )
                except ResourceNotFoundError:
                    pass
                self._log_recovery(
                    operation,
                    action=action,
                    result="cleanup_required",
                    error_code="worktree_recovery_failed",
                    duration=time.monotonic() - started,
                )
        return tuple(reconciled)

    def _mark_lifecycle_worktree_unavailable(
        self, operation: WorktreeLifecycleOperation, error_code: str
    ) -> None:
        if operation.worktree_id is None:
            return
        try:
            existing = self.repository.read_worktree(operation.worktree_id)
            if existing is not None and existing.state is not WorktreeState.DELETED:
                self.repository.update_state(existing.id, WorktreeState.INVALID)
        except Exception:
            self.logger.warning(
                "worktree lifecycle could not mark Worktree unavailable",
                extra={
                    "project_id": operation.project_id,
                    "worktree_id": operation.worktree_id,
                    "session_id": operation.session_id,
                    "operation_id": operation.operation_id,
                    "scope": operation.scope.value,
                    "result": "unavailable",
                    "error_code": error_code,
                },
            )

    def open(self, worktree_id: str, *, allow_inactive: bool = False) -> Worktree:
        self._read_worktree(worktree_id)
        validation = self.validate(worktree_id)
        if not validation.valid and not allow_inactive:
            raise WorktreeError(validation.code or "worktree_invalid")
        return validation.worktree

    def validate(self, worktree_id: str) -> WorktreeValidation:
        worktree = self._read_worktree(worktree_id)
        if worktree.state is WorktreeState.DELETED:
            return self._deleted_validation(worktree)
        project = self.repository.read_project(worktree.project_id)
        if project is None:
            return self._record_validation(
                worktree,
                valid=False,
                code="worktree_repository_mismatch",
                state=WorktreeState.INVALID,
            )
        return self._validate_record(
            worktree,
            project,
            entries=self._entries_for_observation(project),
            persist_state=True,
        )

    def execution_identity(self, worktree_id: str) -> WorkspaceIdentity:
        """Validate a managed Worktree and capture one stable path identity."""

        worktree = self._read_worktree(worktree_id)
        root = Path(worktree.worktree_root)
        validation = self.validate(worktree_id)
        if not validation.valid or validation.head is None:
            raise WorktreeError(validation.code or "worktree_invalid")
        try:
            if root.is_symlink() or not root.is_dir():
                raise WorktreeError("worktree_not_found")
            before = root.stat()
            resolved = root.resolve(strict=True)
        except OSError:
            raise WorktreeError("worktree_not_found") from None
        validation = self.validate(worktree_id)
        if not validation.valid or validation.head is None:
            raise WorktreeError(validation.code or "worktree_invalid")
        try:
            if root.is_symlink() or root.resolve(strict=True) != resolved:
                raise WorktreeError("workspace_identity_changed")
            after = root.stat()
        except OSError:
            raise WorktreeError("workspace_identity_changed") from None
        if (
            before.st_dev,
            before.st_ino,
            before.st_uid,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_uid,
        ):
            raise WorktreeError("workspace_identity_changed")
        return WorkspaceIdentity(
            path=resolved,
            device=after.st_dev,
            inode=after.st_ino,
            owner=after.st_uid,
            git_dir=(
                Path(validation.observed_git_dir)
                if validation.observed_git_dir is not None
                else None
            ),
            git_common_dir=(
                Path(validation.observed_git_common_dir)
                if validation.observed_git_common_dir is not None
                else None
            ),
        )

    def list(self, project_id: str | None = None) -> tuple[WorktreeView, ...]:
        projects = (
            (self.project(project_id),)
            if project_id is not None
            else self.repository.list_projects()
        )
        views: list[WorktreeView] = []
        for project in projects:
            entries = self._entries_for_observation(project)
            by_root = {entry.worktree_root: entry for entry in entries}
            for worktree in self.repository.list_worktrees(project.id):
                validation = self._validate_record(
                    worktree,
                    project,
                    entries=entries,
                    persist_state=False,
                )
                entry = by_root.get(worktree.worktree_root)
                dirty: bool | None = None
                if validation.valid:
                    dirty = self._status_counts(Path(worktree.worktree_root))[4]
                views.append(
                    WorktreeView(
                        worktree=validation.worktree,
                        actual_present=entry is not None,
                        head=validation.head,
                        branch=validation.observed_branch,
                        dirty=dirty,
                    )
                )
        return tuple(views)

    def recover(self) -> WorktreeRecoveryReport:
        started = time.monotonic()
        updated: list[Worktree] = []
        orphan_candidates: list[OrphanWorktreeCandidate] = []
        for project in self.repository.list_projects():
            try:
                entries = self._entries_for_observation(project)
                known = self.repository.list_worktrees(project.id)
            except WorktreeError as error:
                self.logger.warning(
                    "worktree project recovery observation unavailable",
                    extra={
                        "project_id": project.id,
                        "scope": "worktree/recovery",
                        "recovery_action": "skip_project",
                        "result": "unavailable",
                        "error_code": error.code,
                    },
                )
                continue
            known_roots = {worktree.worktree_root for worktree in known}
            try:
                for worktree in known:
                    if worktree.state is WorktreeState.DELETED:
                        continue
                    validation = self._validate_record(
                        worktree,
                        project,
                        entries=entries,
                        persist_state=True,
                    )
                    if validation.worktree != worktree:
                        updated.append(validation.worktree)
                for entry in entries:
                    if entry.worktree_root == project.workspace_root:
                        continue
                    if entry.worktree_root in known_roots:
                        continue
                    orphan_candidates.append(
                        self._orphan_candidate(project, entry)
                    )
            except WorktreeError as error:
                self.logger.warning(
                    "worktree project recovery stopped after bounded failure",
                    extra={
                        "project_id": project.id,
                        "scope": "worktree/recovery",
                        "recovery_action": "skip_project",
                        "result": "unavailable",
                        "error_code": error.code,
                    },
                )
        self.recover_lifecycle()
        self._log_lifecycle(
            logging.INFO,
            "worktree recovery result",
            operation="recover",
            duration=time.monotonic() - started,
            result="success",
        )
        return WorktreeRecoveryReport(
            updated_worktrees=tuple(updated),
            orphan_candidates=tuple(orphan_candidates),
        )

    def cleanup(self) -> WorktreeCleanupReport:
        started = time.monotonic()
        projects = self.repository.list_projects()
        for project in projects:
            try:
                self.git.worktree_prune(Path(project.workspace_root))
            except GitCommandTimeoutError:
                raise WorktreeError("git_command_timeout") from None
            except GitCommandFailedError as error:
                raise WorktreeError("git_command_failed") from error
        self.recover()
        marked_deleted: list[Worktree] = []
        for project in projects:
            entries = self._worktree_entries(project)
            entry_roots = {entry.worktree_root for entry in entries}
            for worktree in self.repository.list_worktrees(project.id):
                if (
                    worktree.ownership is WorktreeOwnership.MANAGED
                    and worktree.state is WorktreeState.MISSING
                    and not Path(worktree.worktree_root).exists()
                    and worktree.worktree_root not in entry_roots
                ):
                    marked_deleted.append(
                        self.repository.update_state(
                            worktree.id, WorktreeState.DELETED
                        )
                    )
        self._log_lifecycle(
            logging.INFO,
            "worktree cleanup completed",
            operation="cleanup",
            duration=time.monotonic() - started,
            result="success",
        )
        return WorktreeCleanupReport(
            pruned_project_ids=tuple(project.id for project in projects),
            marked_deleted=tuple(marked_deleted),
        )

    def delete(self, worktree_id: str) -> Worktree:
        started = time.monotonic()
        worktree = self._read_worktree(worktree_id)
        if worktree.ownership is not WorktreeOwnership.MANAGED:
            raise WorktreeError("worktree_not_owned")
        if worktree.state is WorktreeState.DELETED:
            return worktree
        project = self.repository.read_project(worktree.project_id)
        if project is None:
            raise WorktreeError("worktree_repository_mismatch")
        entries = self._entries_for_observation(project)
        root = Path(worktree.worktree_root)
        entry_present = any(
            entry.worktree_root == worktree.worktree_root for entry in entries
        )
        if not root.exists() and not root.is_symlink() and not entry_present:
            return self._mark_deleted_after_remove(worktree, project, started)
        validation = self._validate_record(
            worktree,
            project,
            entries=entries,
            persist_state=False,
        )
        if not validation.valid:
            raise WorktreeError(validation.code or "worktree_invalid")
        staged, unstaged, untracked, conflicts, dirty = self._status_counts(
            root
        )
        del staged, unstaged, untracked, conflicts
        if dirty:
            self._log_lifecycle(
                logging.WARNING,
                "worktree delete refused dirty",
                worktree_id=worktree.id,
                project_id=worktree.project_id,
                operation="delete",
                result="worktree_dirty",
            )
            raise WorktreeError("worktree_dirty")
        try:
            self.git.worktree_remove(
                Path(project.workspace_root), root
            )
        except GitCommandTimeoutError:
            raise WorktreeError("git_command_timeout") from None
        except GitCommandFailedError as error:
            raise WorktreeError("worktree_remove_failed") from error
        return self._mark_deleted_after_remove(worktree, project, started)

    def rollback_create(self, worktree_id: str) -> Worktree:
        """Remove an unbound Runtime-created Worktree and its unchanged branch."""

        started = time.monotonic()
        worktree = self._read_worktree(worktree_id)
        if worktree.ownership is not WorktreeOwnership.MANAGED:
            raise WorktreeError("worktree_not_owned")
        if self.repository.worktree_is_bound(worktree_id):
            raise WorktreeError("worktree_still_bound")
        project = self._project_for(worktree)
        entries = self._entries_for_observation(project)
        root = Path(worktree.worktree_root)
        entry_present = any(
            entry.worktree_root == worktree.worktree_root for entry in entries
        )
        if worktree.state is not WorktreeState.DELETED:
            if root.exists() or root.is_symlink() or entry_present:
                validation = self._validate_record(
                    worktree,
                    project,
                    entries=entries,
                    persist_state=False,
                )
                if not validation.valid:
                    raise WorktreeError(validation.code or "worktree_invalid")
                if self._status_counts(root)[4]:
                    raise WorktreeError("worktree_cleanup_required")
                try:
                    self.git.worktree_remove(Path(project.workspace_root), root)
                except GitCommandTimeoutError:
                    raise WorktreeError("git_command_timeout") from None
                except GitCommandFailedError as error:
                    raise WorktreeError("worktree_remove_failed") from error
            entries = self._entries_for_observation(project)
            if root.exists() or root.is_symlink() or any(
                entry.worktree_root == worktree.worktree_root for entry in entries
            ):
                raise WorktreeError("worktree_cleanup_required")
        branch_removed = self._remove_created_branch_if_safe(
            project,
            worktree,
            entries,
        )
        deleted = (
            worktree
            if worktree.state is WorktreeState.DELETED
            else self._mark_deleted_after_remove(worktree, project, started)
        )
        if not branch_removed:
            raise WorktreeError("worktree_cleanup_required")
        self._log_lifecycle(
            logging.INFO,
            "worktree create rollback completed",
            worktree_id=worktree.id,
            project_id=worktree.project_id,
            operation="create-rollback",
            duration=time.monotonic() - started,
            result="success",
        )
        return deleted

    def _mark_deleted_after_remove(
        self,
        worktree: Worktree,
        project: Project,
        started: float,
    ) -> Worktree:
        try:
            deleted = self.repository.update_state(
                worktree.id, WorktreeState.DELETED
            )
        except Exception as error:
            self.logger.error(
                "worktree delete persistence failed",
                extra={
                    "worktree_id": worktree.id,
                    "project_id": worktree.project_id,
                    "operation": "delete",
                    "result": "recovery_needed",
                },
            )
            raise WorktreeError("worktree_persistence_failed") from error
        self._log_lifecycle(
            logging.INFO,
            "worktree removed",
            worktree_id=worktree.id,
            project_id=worktree.project_id,
            operation="delete",
            duration=time.monotonic() - started,
            result="success",
        )
        return deleted

    def status(self, worktree_id: str) -> GitStatusSnapshot:
        worktree = self._read_worktree(worktree_id)
        project = self._project_for(worktree)
        validation = self._validate_record(
            worktree,
            project,
            entries=self._entries_for_observation(project),
            persist_state=False,
        )
        if not validation.valid:
            raise WorktreeError(validation.code or "worktree_invalid")
        staged, unstaged, untracked, conflicts, dirty = self._status_counts(
            Path(worktree.worktree_root)
        )
        head = validation.head
        branch = validation.observed_branch
        if head is None or branch is None:
            raise WorktreeError("worktree_invalid")
        return GitStatusSnapshot(
            worktree_id=worktree.id,
            repository_root=project.workspace_root,
            worktree_root=worktree.worktree_root,
            base_ref=worktree.base_ref,
            base_commit=worktree.base_commit,
            branch=branch,
            head=head,
            dirty=dirty,
            staged_count=staged,
            unstaged_count=unstaged,
            untracked_count=untracked,
            conflict_count=conflicts,
            observed_at=utc_now(),
        )

    def diff(
        self,
        worktree_id: str,
        *,
        scope: DiffScope = DiffScope.HEAD,
    ) -> GitDiffSnapshot:
        worktree = self._read_worktree(worktree_id)
        project = self._project_for(worktree)
        validation = self._validate_record(
            worktree,
            project,
            entries=self._entries_for_observation(project),
            persist_state=False,
        )
        if not validation.valid or validation.head is None:
            raise WorktreeError(validation.code or "worktree_invalid")
        root = Path(worktree.worktree_root)
        try:
            if scope is DiffScope.HEAD:
                diff_result = self.git.diff_head(root)
            elif scope is DiffScope.BASELINE:
                diff_result = self.git.diff_baseline(root, worktree.base_commit)
            else:
                raise WorktreeError("worktree_invalid")
            names = self.git.diff_name_only(
                root,
                scope=scope.value,
                base_commit=(
                    worktree.base_commit if scope is DiffScope.BASELINE else None
                ),
            )
            tracked_files = self._machine_paths(names)
            untracked_result = self.git.untracked_files(root)
            untracked_files = self._machine_paths(untracked_result)
        except GitCommandTimeoutError:
            raise WorktreeError("git_command_timeout") from None
        except GitCommandFailedError as error:
            raise WorktreeError("git_command_failed") from error
        except ValueError as error:
            raise WorktreeError("git_observation_incomplete") from error
        _, _, _, _, dirty = self._status_counts(root)
        changed_files = _unique_paths((*tracked_files, *untracked_files))
        unified_diff = diff_result.stdout
        truncated = (
            diff_result.stdout_truncated
            or diff_result.stderr_truncated
        )
        for relative_path in untracked_files:
            path_parts = Path(relative_path).parts
            if (
                Path(relative_path).is_absolute()
                or any(part == ".." for part in path_parts)
            ):
                raise WorktreeError("git_observation_incomplete")
            used_bytes = len(unified_diff.encode("utf-8"))
            separator_bytes = int(bool(unified_diff and not unified_diff.endswith("\n")))
            remaining_bytes = DEFAULT_GIT_DIFF_BYTES - used_bytes - separator_bytes
            if remaining_bytes <= 0:
                truncated = True
                break
            try:
                untracked_diff = self.git.diff_untracked(
                    root,
                    relative_path,
                    output_limit_bytes=remaining_bytes,
                )
            except GitCommandTimeoutError:
                raise WorktreeError("git_command_timeout") from None
            except GitCommandFailedError as error:
                raise WorktreeError("git_command_failed") from error
            except ValueError as error:
                raise WorktreeError("git_observation_incomplete") from error
            if untracked_diff.stdout:
                if unified_diff and not unified_diff.endswith("\n"):
                    unified_diff += "\n"
                unified_diff += untracked_diff.stdout
            truncated = truncated or untracked_diff.stdout_truncated
        return GitDiffSnapshot(
            scope=scope,
            base_commit=worktree.base_commit,
            head=validation.head,
            dirty=dirty,
            changed_files=changed_files,
            unified_diff=unified_diff,
            truncated=truncated,
            observed_at=utc_now(),
        )

    def _deleted_validation(
        self,
        worktree: Worktree,
        *,
        entry: GitWorktreeEntry | None = None,
    ) -> WorktreeValidation:
        return WorktreeValidation(
            worktree=worktree,
            valid=False,
            code="worktree_deleted",
            head=entry.head if entry is not None else None,
            observed_worktree_root=(
                entry.worktree_root if entry is not None else None
            ),
            observed_branch=entry.branch if entry is not None else None,
        )

    def _validate_record(
        self,
        worktree: Worktree,
        project: Project,
        *,
        entries: tuple[GitWorktreeEntry, ...],
        persist_state: bool,
    ) -> WorktreeValidation:
        root = Path(worktree.worktree_root)
        entry = next(
            (item for item in entries if item.worktree_root == worktree.worktree_root),
            None,
        )
        if worktree.state is WorktreeState.DELETED:
            return self._deleted_validation(worktree, entry=entry)
        if not root.exists() or not root.is_dir() or root.is_symlink():
            return self._record_validation(
                worktree,
                valid=False,
                code="worktree_not_found",
                state=WorktreeState.MISSING,
                persist_state=persist_state and worktree.state is not WorktreeState.DELETED,
            )
        git_marker = root / ".git"
        if not git_marker.exists() and not git_marker.is_symlink():
            return self._record_validation(
                worktree,
                valid=False,
                code="worktree_not_found",
                state=WorktreeState.MISSING,
                persist_state=persist_state
                and worktree.state is not WorktreeState.DELETED,
            )
        try:
            discovered = self.discovery.discover(root)
        except WorktreeError as error:
            if error.code == "git_command_timeout":
                return self._record_validation(
                    worktree,
                    valid=False,
                    code="git_command_timeout",
                    state=worktree.state,
                    persist_state=False,
                )
            if isinstance(error.__cause__, GitCommandFailedError):
                return self._record_validation(
                    worktree,
                    valid=False,
                    code="git_observation_failed",
                    state=worktree.state,
                    persist_state=False,
                )
            state = (
                WorktreeState.MISSING
                if error.code in {"not_a_git_repository", "repository_not_found"}
                else WorktreeState.INVALID
            )
            return self._record_validation(
                worktree,
                valid=False,
                code="worktree_not_found" if state is WorktreeState.MISSING else error.code,
                state=state,
                persist_state=persist_state and worktree.state is not WorktreeState.DELETED,
            )
        actual_root = Path(discovered.repository_root).resolve()
        actual_git_dir = Path(discovered.git_dir).resolve()
        actual_common_dir = Path(discovered.git_common_dir).resolve()
        if entry is None:
            mismatch = actual_common_dir != Path(project.git_common_dir).resolve()
            return self._record_validation(
                worktree,
                valid=False,
                code=(
                    "worktree_repository_mismatch"
                    if mismatch
                    else "worktree_invalid"
                ),
                state=WorktreeState.INVALID,
                observed_worktree_root=str(actual_root),
                observed_git_dir=str(actual_git_dir),
                observed_git_common_dir=str(actual_common_dir),
                persist_state=persist_state,
            )
        if actual_root != root.resolve():
            return self._record_validation(
                worktree,
                valid=False,
                code="worktree_invalid",
                state=WorktreeState.INVALID,
                observed_worktree_root=str(actual_root),
                observed_git_dir=str(actual_git_dir),
                observed_git_common_dir=str(actual_common_dir),
                persist_state=persist_state,
            )
        if actual_git_dir != Path(worktree.git_dir).resolve():
            return self._record_validation(
                worktree,
                valid=False,
                code="worktree_invalid",
                state=WorktreeState.INVALID,
                observed_worktree_root=str(actual_root),
                observed_git_dir=str(actual_git_dir),
                observed_git_common_dir=str(actual_common_dir),
                persist_state=persist_state,
            )
        if actual_common_dir != Path(project.git_common_dir).resolve():
            return self._record_validation(
                worktree,
                valid=False,
                code="worktree_repository_mismatch",
                state=WorktreeState.INVALID,
                observed_worktree_root=str(actual_root),
                observed_git_dir=str(actual_git_dir),
                observed_git_common_dir=str(actual_common_dir),
                persist_state=persist_state,
            )
        try:
            branch = self.git.symbolic_ref_short(root)
            head = self.git.resolve_ref(root, "HEAD")
        except GitCommandTimeoutError:
            return self._record_validation(
                worktree,
                valid=False,
                code="git_command_timeout",
                state=worktree.state,
                persist_state=False,
            )
        except GitCommandFailedError:
            return self._record_validation(
                worktree,
                valid=False,
                code="git_observation_failed",
                state=worktree.state,
                persist_state=False,
            )
        if branch != worktree.branch:
            return self._record_validation(
                worktree,
                valid=False,
                code="worktree_invalid",
                state=WorktreeState.INVALID,
                head=head,
                observed_worktree_root=str(actual_root),
                observed_git_dir=str(actual_git_dir),
                observed_git_common_dir=str(actual_common_dir),
                observed_branch=branch,
                persist_state=persist_state,
            )
        return self._record_validation(
            worktree,
            valid=True,
            code=None,
            state=WorktreeState.ACTIVE,
            head=head,
            observed_worktree_root=str(actual_root),
            observed_git_dir=str(actual_git_dir),
            observed_git_common_dir=str(actual_common_dir),
            observed_branch=branch,
            persist_state=persist_state,
        )

    def _record_validation(
        self,
        worktree: Worktree,
        *,
        valid: bool,
        code: str | None,
        state: WorktreeState,
        head: str | None = None,
        observed_worktree_root: str | None = None,
        observed_git_dir: str | None = None,
        observed_git_common_dir: str | None = None,
        observed_branch: str | None = None,
        persist_state: bool = False,
    ) -> WorktreeValidation:
        persisted = worktree
        if (
            persist_state
            and worktree.state is not WorktreeState.DELETED
            and worktree.state is not state
        ):
            try:
                persisted = self.repository.update_state(worktree.id, state)
            except ResourceNotFoundError:
                raise WorktreeError("worktree_not_found") from None
        return WorktreeValidation(
            worktree=persisted,
            valid=valid,
            code=code,
            head=head,
            observed_worktree_root=observed_worktree_root,
            observed_git_dir=observed_git_dir,
            observed_git_common_dir=observed_git_common_dir,
            observed_branch=observed_branch,
        )

    def _worktree_entries(self, project: Project) -> tuple[GitWorktreeEntry, ...]:
        try:
            result = self.git.worktree_list(Path(project.workspace_root))
        except GitCommandTimeoutError:
            raise WorktreeError("git_command_timeout") from None
        except GitCommandFailedError as error:
            raise WorktreeError("git_observation_failed") from error
        if isinstance(result, tuple):
            return result
        if result.stdout_truncated or result.stderr_truncated:
            raise WorktreeError("git_observation_incomplete")
        try:
            return _parse_worktree_list(result.stdout)
        except ValueError as error:
            raise WorktreeError("git_observation_incomplete") from error

    def _entries_for_observation(
        self, project: Project
    ) -> tuple[GitWorktreeEntry, ...]:
        return self._worktree_entries(project)

    def _project_for(self, worktree: Worktree) -> Project:
        project = self.repository.read_project(worktree.project_id)
        if project is None:
            raise WorktreeError("worktree_repository_mismatch")
        return project

    def _read_worktree(self, worktree_id: str) -> Worktree:
        worktree = self.repository.read_worktree(worktree_id)
        if worktree is None:
            raise WorktreeError("worktree_not_found")
        return worktree

    def _status_counts(self, root: Path) -> tuple[int, int, int, int, bool]:
        try:
            result = self.git.status_porcelain_v2(root)
        except GitCommandTimeoutError:
            raise WorktreeError("git_command_timeout") from None
        except GitCommandFailedError as error:
            raise WorktreeError("git_command_failed") from error
        if result.stdout_truncated or result.stderr_truncated:
            raise WorktreeError("git_observation_incomplete")
        try:
            staged, unstaged, untracked, conflicts = parse_porcelain_v2_status(
                result.stdout
            )
        except ValueError as error:
            raise WorktreeError("git_observation_incomplete") from error
        return staged, unstaged, untracked, conflicts, bool(
            staged or unstaged or untracked or conflicts
        )

    def _machine_paths(self, result: GitCommandResult) -> tuple[str, ...]:
        if result.stdout_truncated or result.stderr_truncated:
            raise WorktreeError("git_observation_incomplete")
        try:
            return _parse_nul_paths(result.stdout)
        except ValueError as error:
            raise WorktreeError("git_observation_incomplete") from error

    def _allocate_identity(self, repository_root: Path) -> tuple[str, str, Path]:
        for _ in range(MAX_BRANCH_COLLISION_ATTEMPTS):
            worktree_id = self._new_worktree_id()
            branch = f"eidos/{worktree_id.removeprefix('wt_')[:12]}"
            root = self.managed_root / worktree_id
            if root.exists() or root.is_symlink():
                continue
            try:
                if self.git.branch_exists(repository_root, branch):
                    continue
            except GitCommandTimeoutError:
                raise WorktreeError("git_command_timeout") from None
            except GitCommandFailedError as error:
                raise WorktreeError("git_command_failed") from error
            return worktree_id, branch, root
        raise WorktreeError("worktree_branch_conflict")

    def _new_worktree_id(self) -> str:
        value = self._id_factory()
        if (
            not value
            or len(value) > 128
            or value in {".", ".."}
            or Path(value).name != value
            or "/" in value
            or "\\" in value
            or "\x00" in value
        ):
            raise WorktreeError("worktree_create_failed")
        return value

    def _resolve_managed_root(self, value: Path | None) -> Path:
        if value is None:
            data = self.database.data_directory or (Path.home() / ".eidos")
            value = data.parent / (
                ".eidos-worktrees" if data.name == ".eidos" else f"{data.name}-worktrees"
            )
        if not value.is_absolute():
            raise WorktreeError("managed_worktree_root_invalid")
        if value.exists() and (value.is_symlink() or not value.is_dir()):
            raise WorktreeError("managed_worktree_root_invalid")
        resolved = value.resolve(strict=False)
        if self.database.workspace_overlaps_data(resolved):
            raise WorktreeError("managed_worktree_root_overlaps_data")
        return resolved

    def _ensure_managed_root(self) -> None:
        try:
            self.managed_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as error:
            raise WorktreeError("worktree_create_failed") from error
        if self.managed_root.is_symlink() or not self.managed_root.is_dir():
            raise WorktreeError("managed_worktree_root_invalid")
        if self.database.workspace_overlaps_data(self.managed_root):
            raise WorktreeError("managed_worktree_root_overlaps_data")

    def _compensate_create(self, project: Project, worktree: Worktree) -> None:
        root = Path(worktree.worktree_root)
        try:
            entries = self._worktree_entries(project)
            created_entry = next(
                (
                    entry
                    for entry in entries
                    if entry.worktree_root == worktree.worktree_root
                ),
                None,
            )
            if created_entry is not None:
                self.git.worktree_remove(Path(project.workspace_root), root)
            if root.exists():
                raise RuntimeError("created worktree root remains after remove")
            remaining_entries = self._worktree_entries(project)
            if any(
                entry.worktree_root == worktree.worktree_root
                for entry in remaining_entries
            ):
                raise RuntimeError("created worktree metadata remains after remove")
            if not self._remove_created_branch_if_safe(
                project,
                worktree,
                remaining_entries,
            ):
                return
            self.logger.info(
                "worktree create compensation completed",
                extra={
                    "worktree_id": worktree.id,
                    "project_id": worktree.project_id,
                    "operation": "create-compensation",
                    "result": "success",
                },
            )
        except Exception:
            self.logger.error(
                "worktree create compensation needs recovery",
                extra={
                    "worktree_id": worktree.id,
                    "project_id": worktree.project_id,
                    "operation": "create-compensation",
                    "result": "recovery_needed",
                },
            )

    def _remove_created_branch_if_safe(
        self,
        project: Project,
        worktree: Worktree,
        entries: tuple[GitWorktreeEntry, ...],
    ) -> bool:
        if any(entry.branch == worktree.branch for entry in entries):
            self._log_compensation_recovery(
                worktree,
                "runtime-created branch is still used by a worktree",
            )
            return False
        repository_root = Path(project.workspace_root)
        try:
            current = self.git.try_resolve_ref(
                repository_root,
                f"refs/heads/{worktree.branch}",
            )
        except (GitCommandFailedError, GitCommandTimeoutError):
            self._log_compensation_recovery(
                worktree,
                "runtime-created branch could not be observed",
            )
            return False
        if current is None:
            return True
        if current != worktree.base_commit:
            self._log_compensation_recovery(
                worktree,
                "runtime-created branch changed before compensation",
            )
            return False
        try:
            self.git.update_ref_delete(
                repository_root,
                worktree.branch,
                worktree.base_commit,
            )
        except (GitCommandFailedError, GitCommandTimeoutError):
            self._log_compensation_recovery(
                worktree,
                "runtime-created branch conditional delete failed",
            )
            return False
        try:
            remaining = self.git.try_resolve_ref(
                repository_root,
                f"refs/heads/{worktree.branch}",
            )
        except (GitCommandFailedError, GitCommandTimeoutError):
            self._log_compensation_recovery(
                worktree,
                "runtime-created branch deletion could not be verified",
            )
            return False
        if remaining is not None:
            self._log_compensation_recovery(
                worktree,
                "runtime-created branch remains after compensation",
            )
            return False
        return True

    def _log_compensation_recovery(self, worktree: Worktree, reason: str) -> None:
        self.logger.error(
            "worktree create compensation needs recovery",
            extra={
                "worktree_id": worktree.id,
                "project_id": worktree.project_id,
                "operation": "create-compensation",
                "result": "recovery_needed",
                "reason": reason,
            },
        )

    def _orphan_candidate(
        self,
        project: Project,
        entry: GitWorktreeEntry,
    ) -> OrphanWorktreeCandidate:
        root = Path(entry.worktree_root)
        try:
            discovered = self.discovery.discover(root)
            git_dir = discovered.git_dir
        except WorktreeError:
            git_dir = str((root / ".git").resolve(strict=False))
        return OrphanWorktreeCandidate(
            project_id=project.id,
            worktree_root=entry.worktree_root,
            git_dir=git_dir,
            branch=entry.branch,
            head=entry.head,
        )

    def _log_lifecycle(
        self,
        level: int,
        message: str,
        *,
        worktree_id: str | None = None,
        project_id: str | None = None,
        operation: str,
        duration: float | None = None,
        result: str | None = None,
    ) -> None:
        extra: dict[str, object] = {"operation": operation}
        if worktree_id is not None:
            extra["worktree_id"] = worktree_id
        if project_id is not None:
            extra["project_id"] = project_id
        if duration is not None:
            extra["duration"] = duration
        if result is not None:
            extra["result"] = result
        self.logger.log(level, message, extra=extra)

    def _log_recovery(
        self,
        operation: WorktreeLifecycleOperation,
        *,
        action: str,
        result: str,
        error_code: str | None = None,
        duration: float | None = None,
    ) -> None:
        extra: dict[str, object] = {
            "project_id": operation.project_id,
            "worktree_id": operation.worktree_id,
            "session_id": operation.session_id,
            "operation_id": operation.operation_id,
            "scope": operation.scope.value,
            "state": operation.state.value,
            "recovery_action": action,
            "result": result,
        }
        if error_code is not None:
            extra["error_code"] = error_code
        if duration is not None:
            extra["duration"] = duration
        self.logger.info("worktree lifecycle recovery", extra=extra)


def _parse_worktree_list(output: str) -> tuple[GitWorktreeEntry, ...]:
    entries: list[GitWorktreeEntry] = []
    current: dict[str, object] = {}
    if output and not output.endswith("\x00\x00"):
        raise ValueError("Git worktree list output is incomplete")
    for field in output.split("\x00"):
        if not field:
            if current:
                if "worktree" not in current:
                    raise ValueError("Git worktree record is incomplete")
                head = current.get("head")
                branch = current.get("branch")
                entries.append(
                    GitWorktreeEntry(
                        worktree_root=str(current["worktree"]),
                        head=head if isinstance(head, str) else None,
                        branch=branch if isinstance(branch, str) else None,
                        prunable=bool(current.get("prunable", False)),
                    )
                )
            current = {}
            continue
        key, separator, value = field.partition(" ")
        if key == "worktree" and not separator:
            raise ValueError("Git worktree path is missing")
        if key == "worktree":
            current["worktree"] = str(Path(value).resolve(strict=False))
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            current["branch"] = None
        elif key == "prunable":
            current["prunable"] = True
    if current:
        raise ValueError("Git worktree list output is incomplete")
    return tuple(entries)


def _parse_nul_paths(output: str) -> tuple[str, ...]:
    if output and not output.endswith("\x00"):
        raise ValueError("Git path output is incomplete")
    return tuple(path for path in output.split("\x00") if path)


def _unique_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return tuple(unique)


def _looks_like_branch_conflict(error: GitCommandFailedError) -> bool:
    return any(
        marker in error.stderr.lower()
        for marker in (
            "already exists",
            "branch name",
            "is already checked out",
        )
    )


def _same_prepared_worktree(existing: Worktree, plan: Worktree) -> bool:
    return all(
        getattr(existing, field) == getattr(plan, field)
        for field in (
            "id",
            "project_id",
            "worktree_root",
            "base_ref",
            "base_commit",
            "branch",
            "ownership",
        )
    )


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


__all__ = ["WorktreeManager"]
