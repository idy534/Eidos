from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import stat
import sys
from types import SimpleNamespace

import pytest

from eidos_runtime.sandbox import mxc_policy
from eidos_runtime.sandbox.mxc_policy import MxcPathInfo, MxcPolicyError, compile_mxc_policy
from eidos_runtime.sandbox.permissions import (
    EffectivePermissionProfile,
    FileSystemAccessMode,
    MaterializedFileSystemPermissionEntry,
)


WORKSPACE = PureWindowsPath(r"D:\work")
HOME = PureWindowsPath(r"C:\Users\Ada\Temp\private-home")
TEMP = PureWindowsPath(r"C:\Users\Ada\Temp\private-tmp")
HOST_READS = (r"C:\Users", r"C:\Windows", r"D:\other")
SHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
ENVIRONMENT = {
    "SystemRoot": r"C:\Windows",
    "LocalAppData": r"C:\Users\Ada\AppData\Local",
    "HOME": r"C:\Users\Ada",
    "TEMP": r"C:\Users\Ada\Temp",
}
DIRECTORY = MxcPathInfo(True, 1, 42)
FILE = MxcPathInfo(False, 1, 43)


def entry(
    path: str | PureWindowsPath, access: str, source: str = "base", *,
    recursive: bool = True,
) -> MaterializedFileSystemPermissionEntry:
    return MaterializedFileSystemPermissionEntry.model_validate({
        "requestedPath": str(path), "resolvedPath": str(path), "access": FileSystemAccessMode(access),
        "source": source, "recursive": recursive,
    })


def profile(
    *, workspace: PureWindowsPath = WORKSPACE, entries: tuple = (), **fields: object,
) -> EffectivePermissionProfile:
    return EffectivePermissionProfile.model_validate({
        "workspaceRoots": (str(workspace),),
        "entries": (entry(workspace, "write"), *entries),
        "permanentDenies": (), "hardConfidentialityDenies": (), "profileHash": "0" * 64,
        "networkEnabled": False, "runtimeRoots": (),
        "protectedMetadataPaths": (), "protectedWritePaths": (),
        **{name: tuple(value) if isinstance(value, list) else value for name, value in fields.items()},
    })


def compile_policy(
    permissions: EffectivePermissionProfile,
    files: dict[PureWindowsPath, MxcPathInfo | None] | None = None,
    *, host_reads: tuple[str, ...] = HOST_READS, **options: object,
) -> mxc_policy.MxcPolicy:
    known = {HOME: DIRECTORY, TEMP: DIRECTORY}
    known.update({PureWindowsPath(path): DIRECTORY for path in permissions.workspace_roots})
    known.update(files or {})
    return compile_mxc_policy(
        permissions, sandbox_home=HOME, sandbox_tmp=TEMP,
        readonly_roots=lambda: host_reads, inspect_path=known.get, **options,
    )


def test_request_matches_pinned_mxc_schema_and_passes_explicit_environment() -> None:
    policy = compile_policy(profile(), {WORKSPACE / ".git": DIRECTORY})

    request = json.loads(policy.request([SHELL, "-c", "Write-Output hello"], WORKSPACE, ENVIRONMENT))
    assert request["version"] == "1.0.0"
    assert request["containment"] == "processcontainer"
    assert request["filesystem"] == {
        "readwritePaths": [str(HOME), str(TEMP), str(WORKSPACE)],
        "readonlyPaths": [r"C:\Users", r"C:\Windows", r"D:\other", str(WORKSPACE / ".git")],
        "deniedPaths": [str(WORKSPACE / ".agents"), str(WORKSPACE / ".eidos")],
    }
    assert request["network"] == {
        "egress": {"default": "deny"},
        "ingress": {"default": "deny", "hostLoopback": "deny"},
    }
    assert request["ui"] == {"disable": False, "clipboard": "none", "injection": False}
    process = request["process"]
    assert process["cwd"] == str(WORKSPACE)
    assert process["commandLine"] == SHELL + ' -c "Write-Output hello"'
    assert process["inheritDefaultEnv"] is False
    assert set(process["env"]) == {
        str("HOME=" + str(HOME)), str("TEMP=" + str(TEMP)), str("TMP=" + str(TEMP)),
        str("TMPDIR=" + str(TEMP)), *(
            name.upper() + "=" + value for name, value in ENVIRONMENT.items()
            if name not in {"HOME", "TEMP"}
        ),
    }
    assert [PureWindowsPath(str(path)) for path in policy.readonly_pins] == [WORKSPACE / ".git"]


def test_linked_metadata_reads_do_not_reopen_the_private_data_directory() -> None:
    data = PureWindowsPath(r"C:\Users\Ada\.eidos")
    workspace = data / "worktrees" / "current"
    git = data / "repositories" / "one" / ".git"
    worktree_dir = git / "worktrees" / "current"
    blocked = data / "secrets"
    blocked_child = blocked / "private"
    skill = data / "skills" / "active"
    permissions = profile(
        workspace=workspace, permanentDenies=[entry(data, "deny", "permanent_deny")],
        hardConfidentialityDenies=[entry(blocked, "deny", "hard_deny")],
        entries=(entry(skill, "read", "active_skill"), entry(blocked_child, "read", "additional")),
        activeSkillRoots=[str(skill)], protectedWritePaths=[str(blocked_child)],
        runtimeRoots=[str(blocked_child)],
    )
    policy = compile_policy(
        permissions, {git: DIRECTORY, worktree_dir: DIRECTORY, workspace / ".git": FILE,
                      skill: DIRECTORY, blocked_child: DIRECTORY},
        git_common_dir=git, git_worktree_dir=worktree_dir,
    )

    assert set(policy.readwrite) == {str(workspace), str(HOME), str(TEMP)}
    assert {str(data), str(blocked), str(workspace / ".agents"), str(workspace / ".eidos")} == set(policy.denied)
    assert {str(git), str(worktree_dir), str(workspace / ".git"), str(skill)} <= set(policy.readonly)
    assert all(str(path) not in policy.readonly for path in (data, blocked, blocked_child))
    assert [PureWindowsPath(str(path)) for path in policy.readonly_pins] == [workspace / ".git"]


def test_approved_metadata_writes_preserve_sibling_protection_and_exclusions() -> None:
    git = WORKSPACE / ".git"
    agents = WORKSPACE / ".agents"
    private = git / "private"
    allowed = git / "config"
    write = entry(allowed, "write", "additional", recursive=False)
    blocked_child = private / "elsewhere"
    permissions = profile(
        entries=(write, entry(private, "deny", "additional"), entry(blocked_child, "read", "additional")),
        protectedWritePaths=[str(git), str(agents), str(blocked_child)],
        approvalWriteRoots=[str(git)],
        protectedOverrides=[entry(blocked_child, "read", "additional")],
    )
    policy = compile_policy(permissions, {
        git: DIRECTORY, agents: DIRECTORY, allowed: FILE, private: DIRECTORY,
        blocked_child: DIRECTORY,
    })

    assert str(allowed) in policy.readwrite
    assert {str(git), str(agents)} <= set(policy.readonly)
    assert str(private) in policy.denied
    assert str(blocked_child) not in policy.readonly


def test_more_specific_exact_override_does_not_drop_nested_confidentiality() -> None:
    data = PureWindowsPath(r"C:\Users\Ada\.eidos")
    secret = data / "secret"
    credentials = secret / "credentials"
    approve = entry(secret, "write", "additional")
    permission = profile(
        entries=(approve,), protectedOverrides=[approve],
        permanentDenies=[entry(data, "deny", "permanent_deny")],
        hardConfidentialityDenies=[
            entry(secret, "deny", "hard_deny"), entry(credentials, "deny", "hard_deny"),
        ],
    )
    policy = compile_policy(permission, {secret: DIRECTORY})

    assert str(secret) in policy.readwrite
    assert str(secret) not in policy.denied
    assert {str(data), str(credentials)} <= set(policy.denied)


def test_approved_read_inside_workspace_does_not_narrow_existing_write() -> None:
    config = WORKSPACE / "app.config"
    policy = compile_policy(profile(entries=(entry(config, "read", "additional"),)), {config: FILE})
    assert str(config) not in policy.readonly
    assert str(WORKSPACE) in policy.readwrite


def test_policy_hash_tracks_only_effective_policy() -> None:
    policy = compile_policy(profile())
    reversed_roots = compile_policy(profile(), host_reads=tuple(reversed(HOST_READS)))
    network = compile_policy(profile(networkEnabled=True))

    assert policy.profile_hash == reversed_roots.profile_hash
    assert policy.profile_hash != network.profile_hash
    request = json.loads(network.request([SHELL], WORKSPACE, ENVIRONMENT))
    assert request["network"] == {
        "egress": {"default": "allow"},
        "ingress": {"default": "deny", "hostLoopback": "deny"},
    }


def test_rejects_noncanonical_local_paths() -> None:
    for outside in (r"\\server\share\file", r"D:\path\..\contained", r"D:\dir\file:alternate", "D:\\"):
        with pytest.raises(MxcPolicyError, match="canonical local paths"):
            compile_policy(profile(entries=(entry(outside, "read", "additional"),)))


def test_rejects_unrepresentable_exact_directory_grant() -> None:
    extra = PureWindowsPath(r"C:\exact-directory")
    permission = profile(entries=(entry(extra, "write", "additional", recursive=False),))
    with pytest.raises(MxcPolicyError, match="exact scope"):
        compile_policy(permission, {extra: DIRECTORY})


def test_rejects_request_limits_without_exposing_environment() -> None:
    policy = compile_policy(profile())
    with pytest.raises(MxcPolicyError, match="command-line limit"):
        policy.request([SHELL, "🚧" * 17000], WORKSPACE, ENVIRONMENT)
    with pytest.raises(MxcPolicyError, match="size limit"):
        policy.request([SHELL], WORKSPACE, {**ENVIRONMENT, "VARIABLE": "x" * 1024 * 1024})


def test_old_protected_path_identity_cannot_be_replaced_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "existing-metadata"
    target.write_text("marker", encoding="utf-8")
    policy = compile_policy(profile(), {WORKSPACE / ".git": FILE})
    descriptors: list[int] = []

    def open_replacement(_path: Path) -> int:
        descriptor = os.open(target, os.O_RDONLY)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(mxc_policy.local_files, "open_file", open_replacement)
    with pytest.raises(MxcPolicyError, match="changed before launch"):
        with policy.pin_readonly_paths():
            pytest.fail("the altered protected path must prevent execution")
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_unreadable_drives_receive_no_grant_and_keep_the_policy_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    system = tmp_path / "system"
    (system / "Windows").mkdir(parents=True)
    (system / "pagefile.sys").write_bytes(b"")
    (system / "Junction").symlink_to(system / "Windows", target_is_directory=True)
    listed = tmp_path / "listed"
    listed.mkdir()
    drives = {"C:\\": system, "D:\\": tmp_path / "locked", "E:\\": listed}
    real_children = mxc_policy._drive_children

    def open_directory(root: str) -> int:
        if root == "D:\\":
            raise OSError(5, "volume unavailable")
        return os.open(drives[root], os.O_RDONLY)

    def drive_children(root: str):
        if root == "E:\\":
            # A listing that fails part way must not leave a partial grant.
            yield str(listed / "partial")
            raise PermissionError("listing denied")
        yield from real_children(str(drives[root]))

    monkeypatch.setattr(mxc_policy.local_files, "WINDOWS", True)
    monkeypatch.setattr(mxc_policy.local_files, "open_directory", open_directory)
    monkeypatch.setattr(mxc_policy.local_files, "is_link", lambda metadata: stat.S_ISLNK(metadata.st_mode))
    monkeypatch.setattr(mxc_policy, "_drive_children", drive_children)
    monkeypatch.setitem(sys.modules, "eidos_runtime.infrastructure.windows_files", SimpleNamespace(
        logical_drive_roots=lambda: tuple(drives),
    ))

    assert sorted(mxc_policy._local_readonly_roots()) == sorted(
        (str(system / "Windows"), str(system / "pagefile.sys")),
    )
