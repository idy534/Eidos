from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Callable
import unicodedata

from eidos_runtime.db.storage import WorkspaceIdentity
from eidos_runtime.domain.project_workspace import DirectoryIdentity
from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.errors import GitError
from eidos_runtime.runtime.tool_orchestrator import (
    ExecApprovalRequirement,
    OrchestratorContext,
)
from eidos_runtime.sandbox.denial import SandboxDenied
from eidos_runtime.runtime.permission_policy import PermissionPolicyEvaluator, PermissionDisposition
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    BasePermissionProfile,
    FileSystemAccessMode,
    FileSystemPermissionEntry,
    SandboxAttempt,
    SandboxPermissions,
    canonical_permissions,
    merge_permissions,
)
from eidos_runtime.tools.contracts import RunShellInput
from eidos_runtime.workspace.reader import (
    WorkspacePathError, WorkspaceReader, capture_directory_identity,
)


@dataclass(frozen=True)
class ShellReviewScript:
    path: str
    content: str | None = None
    truncated: bool = False
    error: str | None = None
    version: str | None = None


def collect_shell_review_scripts(
    command: str, cwd: Path, roots: tuple[WorkspaceIdentity, ...],
) -> tuple[ShellReviewScript, ...]:
    """Preview at most three mentioned local scripts; never interpret Shell syntax.

    shlex only supplies candidate paths. Existing WorkspaceReader owns bounded,
    fd-relative reads and identity checks. These previews grant no permissions.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return ()
    scripts: list[ShellReviewScript] = []
    seen: set[Path] = set()
    for token in tokens:
        path = Path(token)
        if path.suffix.lower() not in {".py", ".sh", ".zsh", ".bash", ".js", ".cjs", ".mjs", ".ts", ".rb", ".pl"}:
            continue
        path = path if path.is_absolute() else cwd / path
        if path in seen:
            continue
        seen.add(path)
        root = next((root for root in roots if path.is_relative_to(root.path)), None)
        if root is None:
            scripts.append(ShellReviewScript(path=str(path), error="outside_review_roots"))
        else:
            try:
                with WorkspaceReader(root) as reader:
                    preview = reader.read_preview(str(path.relative_to(root.path)), limit=8 * 1024)
                scripts.append(ShellReviewScript(
                    path=str(path), content=preview.content, truncated=preview.truncated,
                    version=preview.version,
                    error=preview.reason if preview.content is None else None,
                ))
            except (OSError, WorkspacePathError) as error:
                scripts.append(ShellReviewScript(
                    path=str(path), error=getattr(error, "code", "script_unavailable"),
                ))
        if len(scripts) == 3:
            break
    return tuple(scripts)


_HOME_PREFIX = r"(?:~|\$\{HOME\}|\$HOME)"
_PATH_REFERENCE = re.compile(_HOME_PREFIX + r"?/[^\s'\"`;|&<>(){}\[\],:=*?]*")
_HOME_REFERENCE = re.compile(_HOME_PREFIX + r"(?=$|[\s'\"`;|&<>()])")


def references_core_protected_scope(
    command: str,
    cwd: Path,
    base: BasePermissionProfile,
    scripts: tuple[ShellReviewScript, ...] = (),
) -> bool:
    """Find literal references to Eidos core scopes in an unsandboxed command.

    This is a tripwire that routes such commands to human confirmation. It is
    not an enforcement boundary: an unsandboxed process can reach these paths
    without naming them, which the approval warning states.
    """
    core_write = tuple(Path(path) for path in base.protected_write_paths
                       if path not in base.approval_write_roots)
    core_data = tuple(Path(entry.path) for entry in (
        *base.permanent_denies, *base.hard_confidentiality_denies,
    ))
    ordinary = tuple(Path(path) for path in (*base.workspace_roots, *base.approval_write_roots))
    home = Path.home()

    def touches(path: Path, *, ancestors: bool = True) -> bool:
        path = Path(os.path.normpath(path))
        if any(_inside(path, root) for root in core_write):
            return True
        if ancestors and any(
            _inside(root, path) and not _inside(path, root)
            for root in (*core_write, *core_data)
        ):
            return True
        if any(_inside(path, root) for root in ordinary):
            return False
        return any(_inside(path, root) for root in core_data)

    def expand(value: str) -> Path:
        for prefix in ("${HOME}", "$HOME", "~"):
            if value == prefix or value.startswith(prefix + "/"):
                return home / value[len(prefix):].lstrip("/")
        return Path(value)

    if touches(cwd, ancestors=False):
        return True
    for text in (command, *(script.content for script in scripts if script.content)):
        if any(touches(expand(match.group(0))) for match in _PATH_REFERENCE.finditer(text)):
            return True
        if any(touches(home) for _match in _HOME_REFERENCE.finditer(text)):
            return True
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    # Relative operands are interpreted from cwd, e.g. `cat .eidos/state.db`.
    return any(
        touches(cwd / token)
        for token in tokens
        if token and not token.startswith(("-", "/", "~", "$"))
        and not set(token) <= set("();<>|&")
    )


def _inside(path: Path, root: Path) -> bool:
    """Compare as the host volume does; macOS volumes ignore case by default."""
    left, right = _path_key(path), _path_key(root)
    return left[:len(right)] == right


def _path_key(path: Path) -> tuple[str, ...]:
    if sys.platform not in {"darwin", "win32"}:
        return path.parts
    return tuple(unicodedata.normalize("NFD", part).casefold() for part in path.parts)


@dataclass(frozen=True)
class ShellOrchestrationRequest:
    input: RunShellInput
    workspace: WorkspaceIdentity
    cwd: WorkspaceIdentity
    git_write_roots: tuple[str, ...] = ()


class GitWriteAccessError(ValueError):
    pass


def resolve_git_write_roots(workspace: WorkspaceIdentity) -> tuple[str, ...]:
    try:
        discovery = DulwichGitBackend().discover(workspace.path)
        git_dir = Path(discovery.git_dir).resolve(strict=True)
        git_common_dir = Path(discovery.git_common_dir).resolve(strict=True)
        if (
            workspace.git_dir is not None
            and git_dir != workspace.git_dir
        ):
            raise GitWriteAccessError("git_repository_identity_changed")
        if (
            workspace.git_common_dir is not None
            and git_common_dir != workspace.git_common_dir
        ):
            raise GitWriteAccessError("git_repository_identity_changed")
        verify_git_metadata_identities(replace(workspace, additional_roots=()))
        return tuple(dict.fromkeys((str(git_dir), str(git_common_dir))))
    except GitWriteAccessError:
        raise
    except (GitError, OSError, ValueError) as error:
        raise GitWriteAccessError("git_repository_unavailable") from error


def verify_git_metadata_identities(workspace: WorkspaceIdentity) -> None:
    """Verify frozen directories without loading repository configuration."""
    checked: set[tuple[Path | None, DirectoryIdentity | None]] = set()
    for root in (workspace, *workspace.additional_roots):
        for binding in (
            (root.git_dir, root.git_dir_identity),
            (root.git_common_dir, root.git_common_identity),
        ):
            if binding not in checked:
                _verify_git_directory_identity(*binding)
                checked.add(binding)


def _verify_git_directory_identity(
    path: Path | None, expected: DirectoryIdentity | None
) -> None:
    if path is None:
        if expected is not None:
            raise GitWriteAccessError("git_repository_identity_changed")
        return
    if expected is None:
        raise GitWriteAccessError("git_repository_identity_changed")
    try:
        actual = capture_directory_identity(path)
    except WorkspacePathError as error:
        raise GitWriteAccessError("git_repository_identity_changed") from error
    if actual.path != path or (
        actual.device,
        actual.inode,
        actual.owner,
    ) != (expected.device, expected.inode, expected.owner):
        raise GitWriteAccessError("git_repository_identity_changed")


def effective_shell_permissions(
    shell_input: RunShellInput,
    git_write_roots: tuple[str, ...],
) -> AdditionalPermissionProfile | None:
    git_permissions = (
        AdditionalPermissionProfile(
            fileSystem=tuple(
                FileSystemPermissionEntry(
                    path=root,
                    access=FileSystemAccessMode.WRITE,
                )
                for root in git_write_roots
            )
        )
        if git_write_roots
        else None
    )
    merged = merge_permissions(
        shell_input.effective_additional_permissions,
        git_permissions,
    )
    # Policy and Seatbelt must see the same on-disk path. Raises OSError for
    # paths that do not exist or have no single canonical location.
    return None if merged.is_empty else canonical_permissions(merged)


class ShellOrchestrationRuntime:
    def __init__(
        self,
        execute_attempt: Callable[
            [SandboxAttempt], tuple[dict[str, object], SandboxDenied | None]
        ],
    ) -> None:
        self.execute_attempt = execute_attempt

    def workspace_roots(
        self,
        request: ShellOrchestrationRequest,
        _context: OrchestratorContext,
    ) -> tuple[str, ...]:
        return tuple(
            str(root.path)
            for root in (request.workspace, *request.workspace.additional_roots)
        )

    def sandbox_permissions(
        self, request: ShellOrchestrationRequest
    ) -> SandboxPermissions:
        return request.input.effective_sandbox_permissions

    def additional_permissions(
        self, request: ShellOrchestrationRequest
    ) -> AdditionalPermissionProfile | None:
        return effective_shell_permissions(
            request.input,
            request.git_write_roots,
        )

    def approval_requirement(
        self,
        request: ShellOrchestrationRequest,
        _context: OrchestratorContext,
    ) -> ExecApprovalRequirement:
        if _context.base_permissions.full_access or (
            request.input.effective_sandbox_permissions
            is SandboxPermissions.USE_DEFAULT
        ):
            return ExecApprovalRequirement.SKIP
        decision = PermissionPolicyEvaluator().evaluate(
            _context.base_permissions,
            _context.granted_permissions,
            self.additional_permissions(request),
            unsandboxed=request.input.effective_sandbox_permissions is SandboxPermissions.REQUIRE_ESCALATED,
        )
        return {
            PermissionDisposition.ALLOW: ExecApprovalRequirement.SKIP,
            PermissionDisposition.ASK: ExecApprovalRequirement.NEEDS_APPROVAL,
            PermissionDisposition.DENY: ExecApprovalRequirement.FORBIDDEN,
        }[decision.disposition]

    def escalation_allowed(
        self,
        _request: ShellOrchestrationRequest,
        _context: OrchestratorContext,
    ) -> bool:
        return True

    def run(
        self,
        _request: ShellOrchestrationRequest,
        attempt: SandboxAttempt,
        _context: OrchestratorContext,
    ) -> tuple[dict[str, object], SandboxDenied | None]:
        return self.execute_attempt(attempt)
