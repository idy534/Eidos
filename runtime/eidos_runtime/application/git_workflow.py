from __future__ import annotations

from pathlib import Path
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
    WorktreeError,
)
from eidos_runtime.git.status import GitStatusSnapshot
from eidos_runtime.protocol.methods import (
    MethodResultDto,
    SessionGitCommitRequestDto,
    SessionGitCommitResponseDto,
    SessionGitStageRequestDto,
    SessionGitStageResponseDto,
    SessionGitStatusResponseDto,
    SessionGitUnstageRequestDto,
    SessionGitUnstageResponseDto,
)


ResultT = TypeVar("ResultT", bound=MethodResultDto)


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

    def stage(self, request: SessionGitStageRequestDto) -> SessionGitStageResponseDto:
        session, before = self._prepare_mutation(request.session_id)
        paths = _validated_paths(Path(before.worktree_root), request.paths)
        try:
            self._worktrees.git.stage(Path(before.worktree_root), paths)
        except GitError as error:
            raise _workflow_error(error) from error
        after = self._status(session)
        return _mutation_result(SessionGitStageResponseDto, after)

    def unstage(
        self, request: SessionGitUnstageRequestDto
    ) -> SessionGitUnstageResponseDto:
        session, before = self._prepare_mutation(request.session_id)
        paths = _validated_paths(Path(before.worktree_root), request.paths)
        try:
            self._worktrees.git.unstage(Path(before.worktree_root), paths)
        except GitError as error:
            raise _workflow_error(error) from error
        after = self._status(session)
        return _mutation_result(SessionGitUnstageResponseDto, after)

    def commit(
        self, request: SessionGitCommitRequestDto
    ) -> SessionGitCommitResponseDto:
        session, before = self._prepare_mutation(request.session_id)
        if before.branch is None:
            raise ApplicationError(
                "GIT_BRANCH_REQUIRED", "Git commit requires an attached branch"
            )
        try:
            self._worktrees.git.commit(Path(before.worktree_root), request.message)
        except GitError as error:
            raise _workflow_error(error) from error
        after = self._status(session)
        return _mutation_result(
            SessionGitCommitResponseDto,
            after,
            commit=after.head,
        )

    def _prepare_mutation(self, session_id: str) -> tuple[Session, GitStatusSnapshot]:
        session = self._repository.read_session(session_id)
        if session is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
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


def _workflow_error(error: GitError | WorktreeError) -> ApplicationError:
    if isinstance(error, GitNothingStagedError):
        return ApplicationError("GIT_NOTHING_STAGED")
    if isinstance(error, GitIdentityUnavailableError):
        return ApplicationError("GIT_IDENTITY_UNAVAILABLE")
    if isinstance(error, GitConflictError):
        return ApplicationError("GIT_CONFLICT")
    if isinstance(error, GitCommandTimeoutError):
        return ApplicationError("GIT_COMMAND_TIMEOUT")
    if isinstance(error, GitCommandFailedError):
        return ApplicationError("GIT_COMMAND_FAILED")
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


__all__ = ["GitWorkflowApplication"]
