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
    GitNothingStagedError,
    GitRemoteCanceledError,
    GitRemoteUnsupportedError,
    GitUpstreamNotFoundError,
    WorktreeError,
)
from eidos_runtime.git.status import GitStatusSnapshot
from eidos_runtime.git.models import GitRemoteObservation
from eidos_runtime.protocol.methods import (
    MethodResultDto,
    SessionGitCommitRequestDto,
    SessionGitCommitResponseDto,
    SessionGitFetchResponseDto,
    SessionGitRemoteStatusResponseDto,
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


@dataclass(frozen=True)
class GitFetchPlan:
    session: Session
    root: Path
    remote: str


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
            if error.operation == "fetch"
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
            if error.operation.startswith("remote") or error.operation == "fetch"
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


__all__ = ["GitFetchPlan", "GitMutationPlan", "GitWorkflowApplication"]
