from __future__ import annotations


class GitError(RuntimeError):
    """A bounded, typed failure from a Runtime-owned Git command."""

    def __init__(
        self,
        code: str,
        operation: str,
        *,
        returncode: int | None = None,
        stderr: str = "",
    ) -> None:
        self.code = code
        self.operation = operation
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(code)


class GitCommandFailedError(GitError):
    def __init__(
        self,
        operation: str,
        *,
        returncode: int | None,
        stderr: str = "",
    ) -> None:
        super().__init__(
            "git_command_failed",
            operation,
            returncode=returncode,
            stderr=stderr,
        )


class GitCommandTimeoutError(GitError):
    def __init__(self, operation: str) -> None:
        super().__init__("git_command_timeout", operation)


class GitUnsupportedOperationError(GitError):
    def __init__(self, operation: str, *, stderr: str = "") -> None:
        super().__init__("git_unsupported_operation", operation, stderr=stderr)


class GitNothingStagedError(GitError):
    def __init__(self) -> None:
        super().__init__("git_nothing_staged", "commit")


class GitIdentityUnavailableError(GitError):
    def __init__(self) -> None:
        super().__init__("git_identity_unavailable", "commit")


class GitConflictError(GitError):
    def __init__(self) -> None:
        super().__init__("git_conflict", "commit")


class GitRemoteUnsupportedError(GitError):
    def __init__(self) -> None:
        super().__init__("git_remote_unsupported", "fetch")


class GitRemoteCanceledError(GitError):
    def __init__(self) -> None:
        super().__init__("git_remote_canceled", "fetch")


class GitUpstreamNotFoundError(GitError):
    def __init__(self) -> None:
        super().__init__("git_upstream_not_found", "remote-upstream")


class WorktreeError(RuntimeError):
    """A stable Worktree lifecycle failure without subprocess leakage."""

    def __init__(self, code: str, *, operation: str | None = None) -> None:
        self.code = code
        self.operation = operation
        super().__init__(code)


__all__ = [
    "GitCommandFailedError",
    "GitCommandTimeoutError",
    "GitConflictError",
    "GitError",
    "GitIdentityUnavailableError",
    "GitNothingStagedError",
    "GitRemoteCanceledError",
    "GitRemoteUnsupportedError",
    "GitUpstreamNotFoundError",
    "GitUnsupportedOperationError",
    "WorktreeError",
]
