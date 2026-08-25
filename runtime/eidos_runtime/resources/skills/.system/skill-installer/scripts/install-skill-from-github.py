#!/usr/bin/env python3
"""Install validated GitHub skills into the Eidos user skill directory."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import uuid
import zipfile

RUNTIME_ROOT = Path(__file__).resolve().parents[6]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from github_utils import github_request  # noqa: E402

from eidos_runtime.extensions.skill_manifest import (  # noqa: E402
    SkillManifestError,
    parse_skill_manifest,
)


DEFAULT_REF = "main"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_SKILL_BYTES = 128 * 1024
MAX_SKILL_FILES = 512
MAX_SKILL_FILE_BYTES = 4 * 1024 * 1024
MAX_SKILL_TOTAL_BYTES = 8 * 1024 * 1024
REPOSITORY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


@dataclass
class Args:
    url: str | None = None
    repo: str | None = None
    path: list[str] | None = None
    ref: str = DEFAULT_REF
    dest: str | None = None
    name: str | None = None
    method: str = "auto"


@dataclass
class Source:
    owner: str
    repo: str
    ref: str
    paths: list[str]


class InstallError(Exception):
    pass


class _SkillFiles(dict[str, bytes]):
    executable_paths: frozenset[str] = frozenset()


def _eidos_home() -> Path:
    return Path(os.environ.get("EIDOS_DATA_DIR", "~/.eidos")).expanduser()


def _default_dest() -> Path:
    return _eidos_home() / "skills"


def _tmp_root() -> Path:
    root = Path(tempfile.gettempdir()) / "eidos-skill-installer"
    root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def _parse_github_url(url: str, default_ref: str) -> tuple[str, str, str, str | None]:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise InstallError("Only HTTPS GitHub URLs are supported.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise InstallError("Invalid GitHub URL.")
    owner, repo = parts[:2]
    repo = repo.removesuffix(".git")
    ref, subpath = default_ref, ""
    if len(parts) > 2:
        if parts[2] not in {"tree", "blob"} or len(parts) < 4:
            raise InstallError("GitHub URL must use /tree/<ref>/<path>.")
        ref = parts[3]
        subpath = "/".join(parts[4:])
    return owner, repo, ref, subpath or None


def _resolve_source(args: Args) -> Source:
    if args.url:
        owner, repo, ref, url_path = _parse_github_url(args.url, args.ref)
        paths = list(args.path) if args.path else ([url_path] if url_path else [])
    else:
        parts = [part for part in (args.repo or "").split("/") if part]
        if len(parts) != 2:
            raise InstallError("--repo must be owner/repo.")
        owner, repo, ref, paths = parts[0], parts[1], args.ref, list(args.path or [])
    if (
        not REPOSITORY_PART.fullmatch(owner)
        or not REPOSITORY_PART.fullmatch(repo)
        or not ref
        or any(character in ref for character in "\x00\r\n")
    ):
        raise InstallError("Invalid GitHub repository or ref.")
    if not paths:
        raise InstallError("At least one --path is required.")
    for path in paths:
        pure = PurePosixPath(path)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise InstallError("Skill paths must stay inside the repository.")
    return Source(owner, repo, ref, paths)


def _download(source: Source, destination: Path) -> Path:
    payload = github_request(
        "https://codeload.github.com/"
        f"{urllib.parse.quote(source.owner, safe='')}/"
        f"{urllib.parse.quote(source.repo, safe='')}/zip/"
        f"{urllib.parse.quote(source.ref, safe='')}",
        "eidos-skill-installer",
        MAX_ARCHIVE_BYTES,
    )
    archive = destination / "repo.zip"
    archive.write_bytes(payload)
    with zipfile.ZipFile(archive) as bundle:
        total = 0
        roots: set[str] = set()
        for entry in bundle.infolist():
            pure = PurePosixPath(entry.filename)
            if pure.is_absolute() or any(part == ".." for part in pure.parts):
                raise InstallError("Archive contains an unsafe path.")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise InstallError("Archive contains a symbolic link.")
            total += entry.file_size
            if total > MAX_ARCHIVE_EXPANDED_BYTES:
                raise InstallError("Archive is too large.")
            if pure.parts:
                roots.add(pure.parts[0])
        if len(roots) != 1:
            raise InstallError("Unexpected archive layout.")
        bundle.extractall(destination)
    return destination / next(iter(roots))


def _run_git(arguments: list[str]) -> None:
    result = subprocess.run(
        arguments, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        raise InstallError(result.stderr.strip() or "Git command failed.")


def _git_checkout(source: Source, destination: Path) -> Path:
    checkout = destination / "repo"
    urls = [
        f"https://github.com/{source.owner}/{source.repo}.git",
        f"git@github.com:{source.owner}/{source.repo}.git",
    ]
    last_error: InstallError | None = None
    for url in urls:
        try:
            _run_git([
                "git", "clone", "--filter=blob:none", "--depth", "1", "--sparse",
                "--single-branch", "--branch", source.ref, url, str(checkout),
            ])
            _run_git([
                "git", "-C", str(checkout), "sparse-checkout", "set", "--",
                *source.paths,
            ])
            return checkout
        except InstallError as error:
            last_error = error
            shutil.rmtree(checkout, ignore_errors=True)
    assert last_error is not None
    raise last_error


def _prepare(source: Source, method: str, temporary: Path) -> Path:
    if method in {"auto", "download"}:
        try:
            return _download(source, temporary)
        except (InstallError, ValueError, urllib.error.URLError, zipfile.BadZipFile) as error:
            if method == "download":
                raise InstallError(str(error)) from error
    if method in {"auto", "git"}:
        return _git_checkout(source, temporary)
    raise InstallError("Unsupported method.")


def _metadata(content: bytes, default_name: str = "skill") -> tuple[str, str]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise InstallError("SKILL.md must be UTF-8.") from None
    try:
        manifest = parse_skill_manifest(text, default_name)
    except SkillManifestError as error:
        raise InstallError(str(error)) from None
    return manifest.name, manifest.description


def _read_skill(path: Path) -> tuple[str, dict[str, bytes]]:
    if path.is_symlink() or not path.is_dir():
        raise InstallError("Skill source must be a directory.")
    files = _SkillFiles()
    executable_paths: set[str] = set()
    total = 0
    for directory, names, filenames in os.walk(path, followlinks=False):
        parent = Path(directory)
        for name in [*names, *filenames]:
            candidate = parent / name
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise InstallError("Skills cannot contain symbolic links.")
            if name in names:
                if not stat.S_ISDIR(metadata.st_mode):
                    raise InstallError("Skill contains a special file.")
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_SKILL_FILE_BYTES:
                raise InstallError("Skill contains an unsupported file.")
            relative = candidate.relative_to(path).as_posix()
            data = candidate.read_bytes()
            files[relative] = data
            total += len(data)
            if metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                executable_paths.add(relative)
            if len(files) > MAX_SKILL_FILES or total > MAX_SKILL_TOTAL_BYTES:
                raise InstallError("Skill is too large.")
    if "SKILL.md" not in files:
        raise InstallError("SKILL.md is missing.")
    if len(files["SKILL.md"]) > MAX_SKILL_BYTES:
        raise InstallError("SKILL.md is too large.")
    skill_name, _description = _metadata(files["SKILL.md"], path.name)
    files.executable_paths = frozenset(executable_paths)
    return skill_name, files


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise InstallError("Destination must not be a symbolic link.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise InstallError("Destination owner is invalid.")
    os.chmod(path, 0o700)


def _install(
    destination: Path,
    files: dict[str, bytes],
    executable_paths: frozenset[str] = frozenset(),
) -> None:
    if not executable_paths:
        executable_paths = getattr(files, "executable_paths", frozenset())
    staging = destination.parent / f".install-{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    try:
        for relative, data in files.items():
            target = staging.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(target.parent, 0o700)
            mode = 0o700 if relative in executable_paths else 0o600
            descriptor = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode
            )
            try:
                os.fchmod(descriptor, mode)
                offset = 0
                while offset < len(data):
                    offset += os.write(descriptor, data[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        os.replace(staging, destination)
        descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _parse_args(argv: list[str]) -> Args:
    parser = argparse.ArgumentParser(description="Install an Eidos skill from GitHub.")
    parser.add_argument("--repo")
    parser.add_argument("--url")
    parser.add_argument("--path", nargs="+")
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--dest")
    parser.add_argument("--name")
    parser.add_argument("--method", choices=["auto", "download", "git"], default="auto")
    return parser.parse_args(argv, namespace=Args())


def main(argv: list[str]) -> int:
    try:
        args = _parse_args(argv)
        source = _resolve_source(args)
        destination_root = Path(args.dest).expanduser() if args.dest else _default_dest()
        if destination_root.name == ".system":
            raise InstallError("The .system directory is managed by Eidos.")
        _private_directory(destination_root)
        temporary = Path(tempfile.mkdtemp(prefix="install-", dir=_tmp_root()))
        try:
            repository = _prepare(source, args.method, temporary)
            prepared: list[tuple[str, Path, dict[str, bytes], frozenset[str]]] = []
            for source_path in source.paths:
                metadata_name, files = _read_skill(repository.joinpath(*PurePosixPath(source_path).parts))
                requested_name = args.name if len(source.paths) == 1 and args.name else metadata_name
                if requested_name != metadata_name:
                    raise InstallError("Destination name must match SKILL.md name.")
                destination = destination_root / metadata_name
                if destination.exists() or (destination_root / ".system" / metadata_name).exists():
                    raise InstallError(f"Skill already exists: {metadata_name}")
                prepared.append((
                    metadata_name,
                    destination,
                    files,
                    getattr(files, "executable_paths", frozenset()),
                ))
            for name, destination, files, executable_paths in prepared:
                _install(destination, files, executable_paths)
                print(f"Installed {name} to {destination}")
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        return 0
    except (InstallError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
