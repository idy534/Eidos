from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Protocol, TypeVar

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.errors import SessionActiveError
from eidos_runtime.domain.session import Session
from eidos_runtime.git.backend import GitBackend
from eidos_runtime.git.errors import (
    GitError,
    GitCommandFailedError,
    GitCommandTimeoutError,
    GitConflictError,
    GitIdentityUnavailableError,
    GitMergeConflictError,
    GitRebaseConflictError,
    GitNothingStagedError,
    GitRemoteCanceledError,
    GitRemoteUnsupportedError,
    GitUpstreamNotFoundError,
    WorktreeError,
)
from eidos_runtime.git.status import GitStatusSnapshot
from eidos_runtime.git.models import GitOperationState, GitRemoteObservation
from eidos_runtime.protocol.methods import (
    MethodResultDto,
    SessionGitCommitRequestDto,
    SessionGitCommitResponseDto,
    SessionGitDiscardRequestDto,
    SessionGitDiscardResponseDto,
    SessionGitFetchResponseDto,
    SessionGitMergeAbortRequestDto,
    SessionGitMergeAbortResponseDto,
    SessionGitMergeRequestDto,
    SessionGitMergeResponseDto,
    SessionGitPullRequestDto,
    SessionGitPullResponseDto,
    SessionGitPushRequestDto,
    SessionGitPushResponseDto,
    SessionGitRemoteStatusResponseDto,
    SessionGitRebaseAbortRequestDto,
    SessionGitRebaseAbortResponseDto,
    SessionGitRebaseContinueRequestDto,
    SessionGitRebaseContinueResponseDto,
    SessionGitRebaseRequestDto,
    SessionGitRebaseResponseDto,
    SessionGitStageRequestDto,
    SessionGitStageResponseDto,
    SessionGitStatusResponseDto,
    SessionGitUnstageRequestDto,
    SessionGitUnstageResponseDto,
)


ResultT = TypeVar("ResultT", bound=MethodResultDto)


@dataclass(frozen=True)
class GitMutationPlan:
    session: Session
    before: GitStatusSnapshot
    paths: tuple[str, ...] = ()
    message: str | None = None
    discard_untracked: bool = False


@dataclass(frozen=True)
class GitFetchPlan:
    session: Session
    root: Path
    remote: str


@dataclass(frozen=True)
class GitPullPlan:
    session: Session
    root: Path
    remote: str


@dataclass(frozen=True)
class GitPushPlan:
    session: Session
    root: Path
    remote: str
    destination_branch: str
    set_upstream: bool
    check_upstream: bool


@dataclass(frozen=True)
class GitMergePlan:
    session: Session
    root: Path
    target_commit: str | None = None


@dataclass(frozen=True)
class GitRebasePlan:
    session: Session
    root: Path
    target_commit: str | None = None


class GitWorkflowSessionRepository(Protocol):
    def read_session(self, session_id: str) -> Session | None: ...

    def assert_session_deletable(self, session_id: str) -> None: ...


class GitWorkflowWorktreePort(Protocol):
    git: GitBackend

    def status(self, worktree_id: str) -> GitStatusSnapshot: ...

    def local_status(self, repository_root: Path) -> GitStatusSnapshot: ...


class GitWorkflowApplication:
    """Own Session policy while native Git owns Index and commit semantics."""

    def __init__(
        self,
        repository: GitWorkflowSessionRepository,
        worktrees: GitWorkflowWorktreePort,
    ) -> None:
        self._repository = repository
        self._worktrees = worktrees

    def preflight_stage(self, request: SessionGitStageRequestDto) -> GitMutationPlan:
        session, before = self._prepare_mutation(request.session_id)
        paths = _validated_paths(Path(before.worktree_root), request.paths)
        return GitMutationPlan(session=session, before=before, paths=paths)

    def stage(self, plan: GitMutationPlan) -> SessionGitStageResponseDto:
        try:
            self._worktrees.git.stage(Path(plan.before.worktree_root), plan.paths)
        except GitError as error:
            raise _workflow_error(error) from error
        after = self._status(plan.session)
        return _mutation_result(SessionGitStageResponseDto, after)

    def preflight_unstage(
        self, request: SessionGitUnstageRequestDto
    ) -> GitMutationPlan:
        session, before = self._prepare_mutation(request.session_id)
        paths = _validated_paths(Path(before.worktree_root), request.paths)
        return GitMutationPlan(session=session, before=before, paths=paths)

    def unstage(self, plan: GitMutationPlan) -> SessionGitUnstageResponseDto:
        try:
            self._worktrees.git.unstage(Path(plan.before.worktree_root), plan.paths)
        except GitError as error:
            raise _workflow_error(error) from error
        after = self._status(plan.session)
        return _mutation_result(SessionGitUnstageResponseDto, after)

    def preflight_discard(
        self, request: SessionGitDiscardRequestDto
    ) -> GitMutationPlan:
        session, before = self._prepare_mutation(request.session_id)
        path = _validated_paths(Path(before.worktree_root), [request.path])[0]
        if path in before.conflict_files:
            raise ApplicationError("GIT_CONFLICT")
        if path in before.unstaged_files:
            return GitMutationPlan(session=session, before=before, paths=(path,))
        if path in before.untracked_files:
            return GitMutationPlan(
                session=session,
                before=before,
                paths=(path,),
                discard_untracked=True,
            )
        if path in before.staged_files:
            raise ApplicationError("GIT_DISCARD_REQUIRES_UNSTAGED")
        raise ApplicationError("GIT_INVALID_PATH")

    def discard(self, plan: GitMutationPlan) -> SessionGitDiscardResponseDto:
        if len(plan.paths) != 1:
            raise AssertionError("discard plan requires one path")
        try:
            self._worktrees.git.discard(
                Path(plan.before.worktree_root),
                plan.paths[0],
                untracked=plan.discard_untracked,
            )
        except GitError as error:
            raise _workflow_error(error) from error
        return _mutation_result(SessionGitDiscardResponseDto, self._status(plan.session))

    def preflight_commit(
        self, request: SessionGitCommitRequestDto
    ) -> GitMutationPlan:
        session, before = self._prepare_mutation(request.session_id)
        if before.branch is None:
            raise ApplicationError(
                "GIT_BRANCH_REQUIRED", "Git commit requires an attached branch"
            )
        return GitMutationPlan(
            session=session, before=before, message=request.message
        )

    def commit(self, plan: GitMutationPlan) -> SessionGitCommitResponseDto:
        if plan.message is None:
            raise AssertionError("commit plan requires a message")
        try:
            self._worktrees.git.commit(
                Path(plan.before.worktree_root), plan.message
            )
        except GitError as error:
            raise _workflow_error(error) from error
        after = self._status(plan.session)
        return _mutation_result(
            SessionGitCommitResponseDto,
            after,
            commit=after.head,
        )

    def preflight_merge(self, request: SessionGitMergeRequestDto) -> GitMergePlan:
        session, status = self._prepare_mutation(request.session_id)
        root = Path(status.worktree_root)
        if status.branch is None:
            raise ApplicationError("GIT_BRANCH_REQUIRED")
        try:
            state = self._worktrees.git.operation_state(root)
        except GitError as error:
            raise _workflow_error(error) from error
        if state is not GitOperationState.NONE:
            raise ApplicationError("GIT_OPERATION_IN_PROGRESS")
        if status.dirty:
            raise ApplicationError("GIT_WORKTREE_DIRTY")
        try:
            if request.target in self._worktrees.git.local_branches(root):
                target_commit = self._worktrees.git.branch_commit(
                    root, request.target
                )
                if target_commit is None:
                    raise ApplicationError("GIT_MERGE_TARGET_INVALID")
            else:
                target_commit = self._worktrees.git.resolve_revision(
                    root, request.target
                )
        except GitError as error:
            raise ApplicationError("GIT_MERGE_TARGET_INVALID") from error
        return GitMergePlan(
            session=session, root=root, target_commit=target_commit
        )

    def merge(self, plan: GitMergePlan) -> SessionGitMergeResponseDto:
        if plan.target_commit is None:
            raise AssertionError("merge plan requires a target")
        try:
            self._worktrees.git.merge(plan.root, plan.target_commit)
        except GitMergeConflictError:
            return self._operation_result(plan.session)
        except GitError as error:
            raise _workflow_error(error) from error
        return self._operation_result(plan.session)

    def preflight_merge_abort(
        self, request: SessionGitMergeAbortRequestDto
    ) -> GitMergePlan:
        session, status = self._prepare_mutation(request.session_id)
        root = Path(status.worktree_root)
        try:
            state = self._worktrees.git.operation_state(root)
        except GitError as error:
            raise _workflow_error(error) from error
        if state is not GitOperationState.MERGE:
            raise ApplicationError("GIT_MERGE_NOT_IN_PROGRESS")
        return GitMergePlan(session=session, root=root)

    def merge_abort(
        self, plan: GitMergePlan
    ) -> SessionGitMergeAbortResponseDto:
        try:
            self._worktrees.git.merge_abort(plan.root)
        except GitError as error:
            raise _workflow_error(error) from error
        return self._operation_result(
            plan.session, result_type=SessionGitMergeAbortResponseDto
        )

    def preflight_rebase(
        self, request: SessionGitRebaseRequestDto
    ) -> GitRebasePlan:
        session, status = self._prepare_mutation(request.session_id)
        root = Path(status.worktree_root)
        if status.branch is None:
            raise ApplicationError("GIT_BRANCH_REQUIRED")
        try:
            state = self._worktrees.git.operation_state(root)
        except GitError as error:
            raise _workflow_error(error) from error
        if state is not GitOperationState.NONE:
            raise ApplicationError("GIT_OPERATION_IN_PROGRESS")
        if status.dirty:
            raise ApplicationError("GIT_WORKTREE_DIRTY")
        try:
            if request.target in self._worktrees.git.local_branches(root):
                target_commit = self._worktrees.git.branch_commit(
                    root, request.target
                )
                if target_commit is None:
                    raise ApplicationError("GIT_REBASE_TARGET_INVALID")
            else:
                target_commit = self._worktrees.git.resolve_revision(
                    root, request.target
                )
        except GitError as error:
            raise ApplicationError("GIT_REBASE_TARGET_INVALID") from error
        return GitRebasePlan(
            session=session, root=root, target_commit=target_commit
        )

    def rebase(self, plan: GitRebasePlan) -> SessionGitRebaseResponseDto:
        if plan.target_commit is None:
            raise AssertionError("rebase plan requires a target")
        try:
            self._worktrees.git.rebase(plan.root, plan.target_commit)
        except GitRebaseConflictError:
            return self._operation_result(
                plan.session, result_type=SessionGitRebaseResponseDto
            )
        except GitError as error:
            raise _workflow_error(error) from error
        return self._operation_result(
            plan.session, result_type=SessionGitRebaseResponseDto
        )

    def preflight_rebase_continue(
        self, request: SessionGitRebaseContinueRequestDto
    ) -> GitRebasePlan:
        session, status = self._prepare_mutation(request.session_id)
        root = Path(status.worktree_root)
        try:
            state = self._worktrees.git.operation_state(root)
        except GitError as error:
            raise _workflow_error(error) from error
        if state is not GitOperationState.REBASE:
            raise ApplicationError("GIT_REBASE_NOT_IN_PROGRESS")
        return GitRebasePlan(session=session, root=root)

    def rebase_continue(
        self, plan: GitRebasePlan
    ) -> SessionGitRebaseContinueResponseDto:
        try:
            self._worktrees.git.rebase_continue(plan.root)
        except GitRebaseConflictError:
            return self._operation_result(
                plan.session, result_type=SessionGitRebaseContinueResponseDto
            )
        except GitError as error:
            raise _workflow_error(error) from error
        return self._operation_result(
            plan.session, result_type=SessionGitRebaseContinueResponseDto
        )

    def preflight_rebase_abort(
        self, request: SessionGitRebaseAbortRequestDto
    ) -> GitRebasePlan:
        session, status = self._prepare_mutation(request.session_id)
        root = Path(status.worktree_root)
        try:
            state = self._worktrees.git.operation_state(root)
        except GitError as error:
            raise _workflow_error(error) from error
        if state is not GitOperationState.REBASE:
            raise ApplicationError("GIT_REBASE_NOT_IN_PROGRESS")
        return GitRebasePlan(session=session, root=root)

    def rebase_abort(
        self, plan: GitRebasePlan
    ) -> SessionGitRebaseAbortResponseDto:
        try:
            self._worktrees.git.rebase_abort(plan.root)
        except GitError as error:
            raise _workflow_error(error) from error
        return self._operation_result(
            plan.session, result_type=SessionGitRebaseAbortResponseDto
        )

    def _operation_result(
        self,
        session: Session,
        *,
        result_type: type[SessionGitMergeResponseDto] = SessionGitMergeResponseDto,
    ) -> SessionGitMergeResponseDto:
        after = self._status(session)
        try:
            state = self._worktrees.git.operation_state(
                Path(after.worktree_root)
            )
        except GitError as error:
            raise _workflow_error(error) from error
        return result_type.model_validate(
            {
                "head": after.head,
                "branch": after.branch,
                "status": _status_result(after).to_json_value(),
                "operationState": state.value,
                "conflictFiles": list(after.conflict_files),
            }
        )

    def remote_status(self, session_id: str) -> SessionGitRemoteStatusResponseDto:
        session = self._read_session(session_id)
        status = self._status(session)
        try:
            observation = self._worktrees.git.remote_status(
                Path(status.worktree_root)
            )
        except GitError as error:
            raise _workflow_error(error) from error
        return _remote_status_result(observation)

    def preflight_fetch(
        self, session_id: str, requested_remote: str | None
    ) -> GitFetchPlan:
        session, status = self._prepare_mutation(session_id)
        try:
            observation = self._worktrees.git.remote_status(
                Path(status.worktree_root)
            )
        except GitError as error:
            raise _workflow_error(error) from error
        names = {remote.name for remote in observation.remotes}
        if requested_remote is not None:
            if requested_remote not in names:
                raise ApplicationError("GIT_REMOTE_NOT_FOUND")
            remote = requested_remote
        elif observation.upstream is not None:
            remote = observation.upstream.remote
        elif len(names) == 1:
            remote = next(iter(names))
        else:
            raise ApplicationError("GIT_REMOTE_REQUIRED")
        try:
            self._worktrees.git.validate_remote_transport(
                Path(status.worktree_root), remote
            )
        except GitError as error:
            raise _workflow_error(error) from error
        return GitFetchPlan(
            session=session, root=Path(status.worktree_root), remote=remote
        )

    def fetch(
        self, plan: GitFetchPlan, cancel: threading.Event
    ) -> SessionGitFetchResponseDto:
        try:
            observation = self._worktrees.git.fetch(
                plan.root, plan.remote, cancel=cancel
            )
            head = self._worktrees.git.head(plan.root)
        except GitError as error:
            raise _workflow_error(error) from error
        return SessionGitFetchResponseDto.model_validate(
            {
                **_remote_status_result(observation).to_json_value(),
                "remote": plan.remote,
                "head": head,
            }
        )

    def preflight_pull(self, request: SessionGitPullRequestDto) -> GitPullPlan:
        session, status = self._prepare_mutation(request.session_id)
        if status.branch is None:
            raise ApplicationError("GIT_BRANCH_REQUIRED")
        if status.dirty:
            raise ApplicationError("GIT_WORKTREE_DIRTY")
        try:
            observation = self._worktrees.git.remote_status(
                Path(status.worktree_root)
            )
        except GitError as error:
            raise _workflow_error(error) from error
        if observation.upstream is None:
            raise ApplicationError("GIT_UPSTREAM_NOT_FOUND")
        remote = observation.upstream.remote
        try:
            self._worktrees.git.validate_remote_transport(
                Path(status.worktree_root), remote
            )
        except GitError as error:
            raise _workflow_error(error) from error
        return GitPullPlan(
            session=session, root=Path(status.worktree_root), remote=remote
        )

    def pull(
        self, plan: GitPullPlan, cancel: threading.Event
    ) -> SessionGitPullResponseDto:
        try:
            observation = self._worktrees.git.fetch(
                plan.root, plan.remote, cancel=cancel
            )
        except GitError as error:
            raise _workflow_error(error) from error
        if observation.ahead is None or observation.behind is None:
            raise ApplicationError("GIT_UPSTREAM_NOT_FOUND")
        if observation.ahead > 0 and observation.behind > 0:
            raise ApplicationError("GIT_REMOTE_DIVERGED")
        if cancel.is_set():
            raise ApplicationError("GIT_REMOTE_CANCELED")
        current = self._status(plan.session)
        if current.dirty:
            raise ApplicationError("GIT_WORKTREE_DIRTY")
        if observation.ahead == 0 and observation.behind > 0:
            try:
                observation = self._worktrees.git.merge_upstream_ff_only(
                    plan.root
                )
            except GitError as error:
                raise _workflow_error(error) from error
            current = self._status(plan.session)
        return _remote_mutation_result(
            SessionGitPullResponseDto,
            observation,
            remote=plan.remote,
            status=current,
        )

    def preflight_push(self, request: SessionGitPushRequestDto) -> GitPushPlan:
        session, status = self._prepare_mutation(request.session_id)
        if status.branch is None:
            raise ApplicationError("GIT_BRANCH_REQUIRED")
        try:
            observation = self._worktrees.git.remote_status(
                Path(status.worktree_root)
            )
        except GitError as error:
            raise _workflow_error(error) from error
        names = {remote.name for remote in observation.remotes}
        if request.remote is not None:
            if request.remote not in names:
                raise ApplicationError("GIT_REMOTE_NOT_FOUND")
            remote = request.remote
        elif observation.upstream is not None:
            remote = observation.upstream.remote
        elif len(names) == 1:
            remote = next(iter(names))
        else:
            raise ApplicationError("GIT_REMOTE_REQUIRED")
        upstream_matches = (
            observation.upstream is not None
            and observation.upstream.remote == remote
        )
        destination = (
            observation.upstream.branch
            if upstream_matches and observation.upstream is not None
            else status.branch
        )
        try:
            self._worktrees.git.validate_remote_transport(
                Path(status.worktree_root), remote
            )
        except GitError as error:
            raise _workflow_error(error) from error
        return GitPushPlan(
            session=session,
            root=Path(status.worktree_root),
            remote=remote,
            destination_branch=destination,
            set_upstream=observation.upstream is None,
            check_upstream=upstream_matches,
        )

    def push(
        self, plan: GitPushPlan, cancel: threading.Event
    ) -> SessionGitPushResponseDto:
        try:
            observation = self._worktrees.git.fetch(
                plan.root, plan.remote, cancel=cancel
            )
        except GitError as error:
            raise _workflow_error(error) from error
        if plan.check_upstream:
            if observation.ahead is None or observation.behind is None:
                raise ApplicationError("GIT_UPSTREAM_NOT_FOUND")
            if observation.ahead > 0 and observation.behind > 0:
                raise ApplicationError("GIT_REMOTE_DIVERGED")
            if observation.behind > 0:
                raise ApplicationError("GIT_REMOTE_BEHIND")
        if cancel.is_set():
            raise ApplicationError("GIT_REMOTE_CANCELED")
        try:
            observation = self._worktrees.git.push(
                plan.root,
                plan.remote,
                destination_branch=plan.destination_branch,
                set_upstream=plan.set_upstream,
                cancel=cancel,
            )
        except GitError as error:
            raise _workflow_error(error) from error
        current = self._status(plan.session)
        return _remote_mutation_result(
            SessionGitPushResponseDto,
            observation,
            remote=plan.remote,
            status=current,
        )

    def _read_session(self, session_id: str) -> Session:
        session = self._repository.read_session(session_id)
        if session is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
        return session

    def _prepare_mutation(self, session_id: str) -> tuple[Session, GitStatusSnapshot]:
        session = self._read_session(session_id)
        try:
            self._repository.assert_session_deletable(session_id)
        except SessionActiveError as error:
            raise ApplicationError(
                "GIT_WORKFLOW_BUSY", "session has an active Run"
            ) from error
        return session, self._status(session)

    def _status(self, session: Session) -> GitStatusSnapshot:
        try:
            if session.worktree_id is not None:
                return self._worktrees.status(session.worktree_id)
            return self._worktrees.local_status(Path(session.workspace_root))
        except WorktreeError as error:
            raise _workflow_error(error) from error


def _validated_paths(root: Path, values: list[str]) -> tuple[str, ...]:
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise ApplicationError("GIT_WORKTREE_MISSING") from error
    normalized: list[str] = []
    for value in values:
        relative = Path(value)
        if (
            not value
            or "\x00" in value
            or relative.is_absolute()
            or any(part in {"", ".", "..", ".git"} for part in relative.parts)
        ):
            raise ApplicationError("GIT_INVALID_PATH")
        parent = resolved_root
        for part in relative.parts[:-1]:
            parent /= part
            if parent.is_symlink():
                raise ApplicationError("GIT_INVALID_PATH")
        normalized.append(value)
    return tuple(normalized)


def _status_result(status: GitStatusSnapshot) -> SessionGitStatusResponseDto:
    return SessionGitStatusResponseDto.model_validate(
        {
            "worktreeId": status.worktree_id,
            "branch": status.branch,
            "head": status.head,
            "baseRef": status.base_ref,
            "baseCommit": status.base_commit,
            "dirty": status.dirty,
            "stagedCount": status.staged_count,
            "unstagedCount": status.unstaged_count,
            "untrackedCount": status.untracked_count,
            "conflictCount": status.conflict_count,
            "stagedFiles": list(status.staged_files),
            "unstagedFiles": list(status.unstaged_files),
            "untrackedFiles": list(status.untracked_files),
            "conflictFiles": list(status.conflict_files),
            "observedAt": int(status.observed_at.timestamp() * 1000),
        }
    )


def _mutation_result(
    result_type: type[ResultT],
    status: GitStatusSnapshot,
    *,
    commit: str | None = None,
) -> ResultT:
    value: dict[str, object] = {
        "head": status.head,
        "branch": status.branch,
        "status": _status_result(status).to_json_value(),
    }
    if commit is not None:
        value["commit"] = commit
    return result_type.model_validate(value)


def _remote_status_result(
    observation: GitRemoteObservation,
) -> SessionGitRemoteStatusResponseDto:
    return SessionGitRemoteStatusResponseDto.model_validate(
        {
            "branch": observation.branch,
            "remotes": [remote.to_wire_dict() for remote in observation.remotes],
            "upstream": (
                observation.upstream.to_wire_dict()
                if observation.upstream is not None
                else None
            ),
            "ahead": observation.ahead,
            "behind": observation.behind,
        }
    )


def _remote_mutation_result(
    result_type: type[ResultT],
    observation: GitRemoteObservation,
    *,
    remote: str,
    status: GitStatusSnapshot,
) -> ResultT:
    return result_type.model_validate(
        {
            **_remote_status_result(observation).to_json_value(),
            "remote": remote,
            "head": status.head,
            "status": _status_result(status).to_json_value(),
        }
    )


def _workflow_error(error: GitError | WorktreeError) -> ApplicationError:
    if isinstance(error, GitNothingStagedError):
        return ApplicationError("GIT_NOTHING_STAGED")
    if isinstance(error, GitIdentityUnavailableError):
        return ApplicationError("GIT_IDENTITY_UNAVAILABLE")
    if isinstance(error, GitConflictError):
        return ApplicationError("GIT_CONFLICT")
    if isinstance(error, GitCommandTimeoutError):
        return ApplicationError(
            "GIT_REMOTE_TIMEOUT"
            if error.operation in {"fetch", "push", "pull-ff-only"}
            else "GIT_COMMAND_TIMEOUT"
        )
    if isinstance(error, GitRemoteCanceledError):
        return ApplicationError("GIT_REMOTE_CANCELED")
    if isinstance(error, GitRemoteUnsupportedError):
        return ApplicationError("GIT_REMOTE_UNSUPPORTED")
    if isinstance(error, GitUpstreamNotFoundError):
        return ApplicationError("GIT_UPSTREAM_NOT_FOUND")
    if isinstance(error, GitCommandFailedError):
        return ApplicationError(
            "GIT_REMOTE_FAILED"
            if error.operation.startswith("remote")
            or error.operation in {"fetch", "push", "pull-ff-only"}
            else "GIT_COMMAND_FAILED"
        )
    if isinstance(error, WorktreeError):
        return ApplicationError(
            {
                "not_a_git_repository": "GIT_NOT_REPOSITORY",
                "worktree_not_found": "GIT_WORKTREE_NOT_FOUND",
                "worktree_missing": "GIT_WORKTREE_MISSING",
            }.get(
                error.code,
                "GIT_WORKTREE_INVALID"
                if error.code.startswith("worktree_")
                else "GIT_COMMAND_FAILED",
            )
        )
    return ApplicationError("GIT_COMMAND_FAILED")


__all__ = [
    "GitFetchPlan",
    "GitMutationPlan",
    "GitMergePlan",
    "GitRebasePlan",
    "GitPullPlan",
    "GitPushPlan",
    "GitWorkflowApplication",
]
