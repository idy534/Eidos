from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Protocol

from eidos_runtime.workspace.discovery_policy import (
    HARD_DISCOVERY_DIRECTORIES,
    SENSITIVE_DIRECTORIES,
    SENSITIVE_KEYWORDS,
    SENSITIVE_NAMES,
    SENSITIVE_SUFFIXES,
    is_discovery_path_allowed,
)
from eidos_runtime.workspace.discovery_scope import WorkspaceDiscoveryScope


PINNED_RIPGREP_VERSION = "15.2.0"
MAX_SEARCH_FILE_BYTES = 256 * 1024
MAX_RG_STDOUT_BYTES = 8 * 1024 * 1024
MAX_RG_STDERR_BYTES = 64 * 1024
MAX_RG_JSON_LINE_BYTES = 512 * 1024
MAX_RG_EVENTS = 20_000
MAX_RG_PREVIEW_CHARACTERS = 300
_PROCESS_POLL_SECONDS = 0.05
_TERMINATION_GRACE_SECONDS = 0.25
_MANIFEST_MAX_BYTES = 64 * 1024
_READ_FD = os.read
_RESOURCE_ROOT = (
    Path(__file__).resolve().parents[1] / "resources" / "bin" / "ripgrep"
)


class SearchDriverError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WorkspaceSearchRequest:
    query: str
    workspace_path: Path
    deadline: float
    max_results: int
    max_preview_characters: int
    discovery_scope: WorkspaceDiscoveryScope
    path: str = "."
    regex: bool = False
    include_globs: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceSearchMatch:
    path: str
    line: int
    column: int
    preview: str


@dataclass(frozen=True)
class WorkspaceSearchResult:
    matches: tuple[WorkspaceSearchMatch, ...]
    scanned_bytes: int
    truncated: bool
    truncation_reason: str | None


class WorkspaceSearchDriver(Protocol):
    def search(
        self,
        request: WorkspaceSearchRequest,
        cancel: threading.Event,
    ) -> WorkspaceSearchResult: ...


class RipgrepBinaryResolver:
    _verified_hashes: dict[tuple[object, ...], Path] = {}
    _cache_lock = threading.Lock()

    def __init__(
        self,
        *,
        resource_root: Path | None = None,
        explicit_path: Path | None = None,
    ) -> None:
        self._resource_root = resource_root or _RESOURCE_ROOT
        self._explicit_path = explicit_path

    def resolve(self) -> Path:
        if self._explicit_path is not None:
            path = self._explicit_path
            _validate_binary_file(path)
            return path
        manifest_path = self._resource_root / "manifest.json"
        try:
            manifest_bytes = manifest_path.read_bytes()
        except FileNotFoundError:
            raise SearchDriverError("search_backend_unavailable") from None
        except OSError:
            raise SearchDriverError("search_backend_invalid") from None
        if len(manifest_bytes) > _MANIFEST_MAX_BYTES:
            raise SearchDriverError("search_backend_invalid")
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SearchDriverError("search_backend_invalid") from None
        artifact_key = _artifact_key()
        try:
            if (
                not isinstance(manifest, dict)
                or manifest.get("version") != PINNED_RIPGREP_VERSION
                or set(manifest) != {"version", "artifacts"}
            ):
                raise KeyError
            artifacts = manifest["artifacts"]
            artifact = artifacts[artifact_key]
            relative_path = artifact["path"]
            expected_sha256 = artifact["sha256"]
            if (
                not isinstance(artifacts, dict)
                or not isinstance(artifact, dict)
                or set(artifact) != {"path", "sha256"}
                or not isinstance(relative_path, str)
                or not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or any(character not in "0123456789abcdef" for character in expected_sha256)
            ):
                raise KeyError
        except (KeyError, TypeError):
            raise SearchDriverError("search_backend_invalid") from None
        parts = relative_path.split("/")
        if any(part in {"", ".", ".."} for part in parts) or "\\" in relative_path:
            raise SearchDriverError("search_backend_invalid")
        path = self._resource_root.joinpath(*parts)
        try:
            metadata = _validate_binary_file(path)
            manifest_metadata = manifest_path.stat(follow_symlinks=False)
        except SearchDriverError:
            raise
        cache_key = (
            manifest_metadata.st_dev,
            manifest_metadata.st_ino,
            manifest_metadata.st_size,
            manifest_metadata.st_mtime_ns,
            manifest_metadata.st_ctime_ns,
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_mode,
            expected_sha256,
        )
        with self._cache_lock:
            cached = self._verified_hashes.get(cache_key)
        if cached == path:
            return path
        try:
            actual_sha256 = _sha256_file(path)
        except OSError:
            raise SearchDriverError("search_backend_invalid") from None
        if actual_sha256 != expected_sha256:
            raise SearchDriverError("search_backend_invalid")
        with self._cache_lock:
            self._verified_hashes[cache_key] = path
        return path


class RipgrepFileEnumerator:
    """Bounded repository file membership from the pinned ripgrep binary."""

    def __init__(self, resolver: RipgrepBinaryResolver | None = None) -> None:
        self._resolver = resolver or RipgrepBinaryResolver()

    def enumerate(
        self,
        workspace_path: Path,
        *,
        deadline: float,
        max_entries: int,
        cancel: threading.Event,
        path: str = ".",
    ) -> tuple[tuple[str, ...], bool]:
        if max_entries < 1:
            return (), False
        binary = self._resolver.resolve()
        ignore_files = self.ignore_files(
            workspace_path, deadline=deadline, cancel=cancel
        )
        argv = _build_discovery_argv(binary, workspace_path, ignore_files, path=path)
        try:
            process = subprocess.Popen(
                argv,
                cwd=workspace_path,
                env={"LC_ALL": "C", "LANG": "C"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
                shell=False,
            )
        except OSError:
            raise SearchDriverError("search_backend_unavailable") from None
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        buffer = bytearray()
        paths: list[str] = []
        stdout_bytes = 0
        stderr_bytes = 0
        truncated = False
        try:
            while selector.get_map() or process.poll() is None:
                if cancel.is_set():
                    raise SearchDriverError("search_backend_canceled")
                if time.monotonic() >= deadline:
                    raise SearchDriverError("search_backend_timeout")
                ready = selector.select(timeout=_PROCESS_POLL_SECONDS)
                for key, _mask in ready:
                    chunk = _READ_FD(key.fileobj.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stderr":
                        stderr_bytes += len(chunk)
                        if stderr_bytes > MAX_RG_STDERR_BYTES:
                            raise SearchDriverError("search_backend_failed")
                        continue
                    stdout_bytes += len(chunk)
                    if stdout_bytes > MAX_RG_STDOUT_BYTES:
                        raise SearchDriverError("search_backend_protocol_error")
                    buffer.extend(chunk)
                    while b"\0" in buffer:
                        encoded, _, remainder = buffer.partition(b"\0")
                        buffer = bytearray(remainder)
                        if not encoded:
                            continue
                        try:
                            path = encoded.decode("utf-8", errors="strict")
                        except UnicodeDecodeError:
                            raise SearchDriverError(
                                "search_backend_protocol_error"
                            ) from None
                        path = path.removeprefix("./")
                        if is_discovery_path_allowed(path):
                            paths.append(path)
                            if len(paths) >= max_entries:
                                truncated = True
                                break
                    if truncated:
                        break
                if truncated:
                    _terminate_and_reap(process)
                    break
                if process.poll() is not None and not selector.get_map():
                    break
            if buffer:
                try:
                    path = buffer.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    raise SearchDriverError("search_backend_protocol_error") from None
                path = path.removeprefix("./")
                if is_discovery_path_allowed(path):
                    paths.append(path)
            if not truncated:
                returncode = process.wait(timeout=1)
                if returncode not in {0, 1}:
                    raise SearchDriverError("search_backend_failed")
            return tuple(sorted(paths[:max_entries], key=os.fsencode)), truncated
        except (OSError, subprocess.TimeoutExpired):
            raise SearchDriverError("search_backend_failed") from None
        except SearchDriverError:
            _terminate_and_reap(process)
            raise
        finally:
            selector.close()
            if process.poll() is None:
                _terminate_and_reap(process)
            process.stdout.close()
            process.stderr.close()

    def ignore_files(
        self,
        workspace_path: Path,
        *,
        deadline: float,
        cancel: threading.Event,
    ) -> tuple[Path, ...]:
        """Find nested VCS ignore files using ripgrep itself."""
        binary = self._resolver.resolve()
        argv = [
            str(binary), "--files", "--null", "--hidden", "--no-follow",
            "--no-config", "--no-ignore", "--sort", "path",
            "--glob", "**/.gitignore", "--", ".",
        ]
        try:
            process = subprocess.Popen(
                argv,
                cwd=workspace_path,
                env={"LC_ALL": "C", "LANG": "C"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
                shell=False,
            )
        except OSError:
            raise SearchDriverError("search_backend_unavailable") from None
        assert process.stdout is not None and process.stderr is not None
        ignore_deadline = min(deadline, time.monotonic() + 0.1)
        try:
            while True:
                if cancel.is_set():
                    raise SearchDriverError("search_backend_canceled")
                if time.monotonic() >= ignore_deadline:
                    _terminate_and_reap(process)
                    return _ignore_files(workspace_path)
                try:
                    output, error = process.communicate(timeout=_PROCESS_POLL_SECONDS)
                except subprocess.TimeoutExpired:
                    continue
                if len(output) > MAX_RG_STDOUT_BYTES or len(error) > MAX_RG_STDERR_BYTES:
                    raise SearchDriverError("search_backend_protocol_error")
                if process.returncode not in {0, 1}:
                    raise SearchDriverError("search_backend_failed")
                paths = tuple(
                    path.removeprefix("./")
                    for path in output.decode("utf-8", errors="strict").split("\0")
                    if path and is_discovery_path_allowed(path.removeprefix("./"))
                )
                return _ignore_files(workspace_path) + tuple(
                    workspace_path / path for path in paths
                )
        except UnicodeDecodeError:
            raise SearchDriverError("search_backend_protocol_error") from None
        except SearchDriverError:
            _terminate_and_reap(process)
            raise
        finally:
            if process.poll() is None:
                _terminate_and_reap(process)
            process.stdout.close()
            process.stderr.close()


class RipgrepSearchDriver:
    def __init__(self, resolver: RipgrepBinaryResolver | None = None) -> None:
        self._resolver = resolver or RipgrepBinaryResolver()

    def search(
        self,
        request: WorkspaceSearchRequest,
        cancel: threading.Event,
    ) -> WorkspaceSearchResult:
        binary = self._resolver.resolve()
        ignore_files = RipgrepFileEnumerator(self._resolver).ignore_files(
            request.workspace_path,
            deadline=request.deadline,
            cancel=cancel,
        )
        argv = _build_argv(
            binary,
            workspace_path=request.workspace_path,
            query=request.query,
            path=request.path,
            regex=request.regex,
            include_globs=request.include_globs,
            ignore_files=ignore_files,
        )
        try:
            process = subprocess.Popen(
                argv,
                cwd=request.workspace_path,
                env={"LC_ALL": "C", "LANG": "C"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
                shell=False,
            )
        except OSError:
            raise SearchDriverError("search_backend_unavailable") from None
        parser = _RipgrepEventParser(request)
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        stdout_buffer = bytearray()
        stdout_bytes = 0
        stderr_bytes = 0
        controlled_limit = False
        try:
            while selector.get_map() or process.poll() is None:
                if cancel.is_set():
                    raise SearchDriverError("search_backend_canceled")
                if time.monotonic() >= request.deadline:
                    raise SearchDriverError("search_backend_timeout")
                ready = selector.select(timeout=_PROCESS_POLL_SECONDS)
                for key, _mask in ready:
                    try:
                        chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    except OSError:
                        raise SearchDriverError("search_backend_failed") from None
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stderr":
                        stderr_bytes += len(chunk)
                        if stderr_bytes > MAX_RG_STDERR_BYTES:
                            raise SearchDriverError("search_backend_failed")
                        continue
                    stdout_bytes += len(chunk)
                    if stdout_bytes > MAX_RG_STDOUT_BYTES:
                        raise SearchDriverError("search_backend_protocol_error")
                    stdout_buffer.extend(chunk)
                    while True:
                        newline = stdout_buffer.find(b"\n")
                        if newline < 0:
                            if len(stdout_buffer) > MAX_RG_JSON_LINE_BYTES:
                                raise SearchDriverError(
                                    "search_backend_protocol_error"
                                )
                            break
                        if newline > MAX_RG_JSON_LINE_BYTES:
                            raise SearchDriverError("search_backend_protocol_error")
                        line = bytes(stdout_buffer[:newline])
                        del stdout_buffer[: newline + 1]
                        if parser.accept(line):
                            controlled_limit = True
                            break
                    if controlled_limit:
                        break
                if controlled_limit:
                    break
                if process.poll() is not None and not selector.get_map():
                    break
            if controlled_limit:
                _terminate_and_reap(process)
                return parser.result(truncated=True, reason="result_limit")
            if stdout_buffer:
                if len(stdout_buffer) > MAX_RG_JSON_LINE_BYTES:
                    raise SearchDriverError("search_backend_protocol_error")
                parser.accept(bytes(stdout_buffer))
            returncode = process.wait(timeout=1)
            if returncode == 1 and parser.event_count == 0:
                return WorkspaceSearchResult((), 0, False, None)
            if returncode not in {0, 1}:
                raise SearchDriverError("search_backend_failed")
            if not parser.saw_summary:
                raise SearchDriverError("search_backend_protocol_error")
            return parser.result(truncated=False, reason=None)
        except subprocess.TimeoutExpired:
            raise SearchDriverError("search_backend_failed") from None
        except SearchDriverError:
            _terminate_and_reap(process)
            raise
        finally:
            selector.close()
            if process.poll() is None:
                _terminate_and_reap(process)
            process.stdout.close()
            process.stderr.close()


class _RipgrepEventParser:
    def __init__(self, request: WorkspaceSearchRequest) -> None:
        self.request = request
        self.matches: list[WorkspaceSearchMatch] = []
        self.accepted_paths: dict[str, int] = {}
        self.searched_bytes_by_path: dict[str, int] = {}
        self.rejected_paths: set[str] = set()
        self.event_count = 0
        self.saw_summary = False

    def accept(self, encoded: bytes) -> bool:
        self.event_count += 1
        if self.event_count > MAX_RG_EVENTS or not encoded:
            raise SearchDriverError("search_backend_protocol_error")
        try:
            event = json.loads(encoded.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SearchDriverError("search_backend_protocol_error") from None
        if not isinstance(event, dict) or set(event) != {"type", "data"}:
            raise SearchDriverError("search_backend_protocol_error")
        event_type = event["type"]
        data = event["data"]
        if event_type not in {"begin", "match", "end", "summary"}:
            raise SearchDriverError("search_backend_protocol_error")
        if not isinstance(data, dict):
            raise SearchDriverError("search_backend_protocol_error")
        if event_type == "summary":
            _validated_nonnegative_stat(data, "stats", "bytes_searched")
            self.saw_summary = True
            return False
        path = _event_path(data)
        if event_type == "begin":
            return False
        if event_type == "end":
            searched = _validated_nonnegative_stat(data, "stats", "bytes_searched")
            if path in self.accepted_paths:
                self.searched_bytes_by_path[path] = searched
            return False
        return self._accept_match(path, data)

    def _accept_match(self, path: str, data: dict[str, object]) -> bool:
        line_number = data.get("line_number")
        submatches = data.get("submatches")
        lines = data.get("lines")
        if (
            isinstance(line_number, bool)
            or not isinstance(line_number, int)
            or line_number < 1
            or not isinstance(submatches, list)
            or not submatches
            or not isinstance(lines, dict)
            or set(lines) not in ({"text"}, {"bytes"})
        ):
            raise SearchDriverError("search_backend_protocol_error")
        if "bytes" in lines:
            _decode_base64(lines["bytes"])
            self.rejected_paths.add(path)
            return False
        text = lines["text"]
        if not isinstance(text, str):
            raise SearchDriverError("search_backend_protocol_error")
        first = submatches[0]
        if not isinstance(first, dict):
            raise SearchDriverError("search_backend_protocol_error")
        start = first.get("start")
        end = first.get("end")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 0
            or end <= start
        ):
            raise SearchDriverError("search_backend_protocol_error")
        encoded_text = text.encode("utf-8")
        if end > len(encoded_text):
            raise SearchDriverError("search_backend_protocol_error")
        try:
            column = len(encoded_text[:start].decode("utf-8", errors="strict")) + 1
        except UnicodeDecodeError:
            raise SearchDriverError("search_backend_protocol_error") from None
        if path in self.rejected_paths:
            return False
        if path not in self.accepted_paths:
            if (
                not is_discovery_path_allowed(path)
                or not _is_within_search_path(path, self.request.path)
                or self.request.discovery_scope.is_ignored(path, is_directory=False)
            ):
                self.rejected_paths.add(path)
                return False
            size = _validate_matched_file(self.request.workspace_path, path)
            if size is None:
                self.rejected_paths.add(path)
                return False
            self.accepted_paths[path] = size
        preview = text.rstrip("\r\n")[: self.request.max_preview_characters]
        self.matches.append(
            WorkspaceSearchMatch(path, line_number, column, preview)
        )
        return len(self.matches) >= self.request.max_results

    def result(self, *, truncated: bool, reason: str | None) -> WorkspaceSearchResult:
        scanned_bytes = sum(
            self.searched_bytes_by_path.get(path, size)
            for path, size in self.accepted_paths.items()
        )
        return WorkspaceSearchResult(
            tuple(self.matches), scanned_bytes, truncated, reason
        )


def _artifact_key() -> str:
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "darwin-arm64"
    raise SearchDriverError("search_backend_unavailable")


def _validate_binary_file(path: Path) -> os.stat_result:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        raise SearchDriverError("search_backend_unavailable") from None
    except OSError:
        raise SearchDriverError("search_backend_invalid") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not metadata.st_mode & stat.S_IXUSR
    ):
        raise SearchDriverError("search_backend_invalid")
    return metadata


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(128 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _build_argv(
    binary: Path,
    *,
    workspace_path: Path,
    query: str,
    path: str = ".",
    regex: bool = False,
    include_globs: tuple[str, ...] = (),
    ignore_files: tuple[Path, ...] | None = None,
    disable_vcs_ignore: bool | None = None,
) -> list[str]:
    _validate_search_path(path)
    for glob in include_globs:
        _validate_include_glob(glob)
    argv = [
        str(binary),
        "--json",
        "--ignore-case",
        "--no-unicode",
        "--line-number",
        "--column",
        "--max-filesize",
        "256K",
        "--encoding",
        "utf-8",
        *_discovery_options(
            disable_vcs_ignore if disable_vcs_ignore is not None
            else _needs_scope_fallback(workspace_path)
        ),
        "--sort",
        "path",
    ]
    if not regex:
        argv.insert(2, "--fixed-strings")
    for glob in include_globs:
        argv.extend(("--glob", glob))
    argv.extend(_hard_discovery_globs())
    effective_ignore_files = ignore_files or _ignore_files(workspace_path)
    if _needs_scope_fallback(workspace_path):
        effective_ignore_files = tuple(
            path for path in effective_ignore_files if path.name == ".eidosignore"
        )
    for ignore_file in effective_ignore_files:
        argv.extend(("--ignore-file", str(ignore_file)))
    argv.extend(("--", query, path))
    return argv


def _build_discovery_argv(
    binary: Path,
    workspace_path: Path,
    ignore_files: tuple[Path, ...] | None = None,
    disable_vcs_ignore: bool | None = None,
    path: str = ".",
) -> list[str]:
    argv = [
        str(binary), "--files", "--null",
        *_discovery_options(
            disable_vcs_ignore if disable_vcs_ignore is not None
            else _needs_scope_fallback(workspace_path)
        ),
        "--sort", "path",
    ]
    argv.extend(_hard_discovery_globs())
    effective_ignore_files = ignore_files or _ignore_files(workspace_path)
    if _needs_scope_fallback(workspace_path):
        effective_ignore_files = tuple(
            path for path in effective_ignore_files if path.name == ".eidosignore"
        )
    for ignore_file in effective_ignore_files:
        argv.extend(("--ignore-file", str(ignore_file)))
    argv.extend(("--", path))
    return argv


def _discovery_options(disable_vcs_ignore: bool = False) -> tuple[str, ...]:
    options = [
        "--no-config",
        "--no-ignore-dot",
        "--no-ignore-global",
        "--no-ignore-parent",
        "--hidden",
        "--no-follow",
    ]
    if disable_vcs_ignore:
        options.append("--no-ignore-vcs")
    return tuple(options)


def _hard_discovery_globs() -> tuple[str, ...]:
    globs: list[str] = []
    for directory in sorted(HARD_DISCOVERY_DIRECTORIES | SENSITIVE_DIRECTORIES):
        globs.extend(("--glob", f"!**/{directory}/**"))
    for name in sorted(SENSITIVE_NAMES | {".env"}):
        globs.extend(("--glob", f"!**/{name}"))
    for suffix in sorted(SENSITIVE_SUFFIXES):
        globs.extend(("--glob", f"!**/*{suffix}"))
    for keyword in sorted(SENSITIVE_KEYWORDS):
        globs.extend(("--glob", f"!**/*{keyword}*"))
    globs.extend(("--glob", "!**/.eidos-*"))
    return tuple(globs)


def _ignore_files(workspace_path: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for name in (".gitignore", ".eidosignore"):
        path = workspace_path / name
        try:
            metadata = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            raise SearchDriverError("search_backend_invalid") from None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_RG_JSON_LINE_BYTES:
            raise SearchDriverError("search_backend_invalid")
        paths.append(path)
    return tuple(paths)


def _needs_scope_fallback(workspace_path: Path) -> bool:
    path = workspace_path / ".eidosignore"
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_RG_JSON_LINE_BYTES:
        return False
    try:
        content = path.read_bytes()
    except OSError:
        return False
    return any(line.lstrip().startswith(b"!") for line in content.splitlines())


def _validate_search_path(path: str) -> None:
    if path == ".":
        return
    if (
        path
        and not path.startswith("/")
        and "\\" not in path
        and "\x00" not in path
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    ):
        return
    raise SearchDriverError("search_backend_protocol_error")


def _validate_include_glob(value: str) -> None:
    if (
        not value
        or "\x00" in value
        or "\n" in value
        or "\r" in value
        or "\\" in value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/")
               if part not in {"**"})
    ):
        raise SearchDriverError("search_backend_protocol_error")


def _is_within_search_path(path: str, search_path: str) -> bool:
    return search_path == "." or path.startswith(f"{search_path}/")


def _event_path(data: dict[str, object]) -> str:
    path_value = data.get("path")
    if not isinstance(path_value, dict) or set(path_value) not in ({"text"}, {"bytes"}):
        raise SearchDriverError("search_backend_protocol_error")
    if "bytes" in path_value:
        raw = _decode_base64(path_value["bytes"])
        try:
            path = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise SearchDriverError("search_backend_protocol_error") from None
    else:
        path = path_value["text"]
        if not isinstance(path, str):
            raise SearchDriverError("search_backend_protocol_error")
    if path.startswith("./"):
        path = path[2:]
    if not is_discovery_path_allowed(path):
        # A syntactically valid sensitive path is filtered later, but malformed paths
        # are always a backend protocol violation.
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise SearchDriverError("search_backend_protocol_error")
    return path


def _decode_base64(value: object) -> bytes:
    if not isinstance(value, str):
        raise SearchDriverError("search_backend_protocol_error")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise SearchDriverError("search_backend_protocol_error") from None


def _validated_nonnegative_stat(
    data: dict[str, object], container: str, name: str
) -> int:
    stats = data.get(container)
    value = stats.get(name) if isinstance(stats, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SearchDriverError("search_backend_protocol_error")
    return value


def _validate_matched_file(workspace: Path, relative_path: str) -> int | None:
    parts = relative_path.split("/")
    directory_fd = -1
    descriptor = -1
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_fd = os.open(workspace, directory_flags)
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        descriptor = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_SEARCH_FILE_BYTES
        ):
            return None
        chunks: list[bytes] = []
        remaining = MAX_SEARCH_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)
    if (
        len(content) > MAX_SEARCH_FILE_BYTES
        or len(content) != before.st_size
        or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_nlink,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_nlink,
        )
        or b"\x00" in content
    ):
        return None
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    return after.st_size


def _terminate_and_reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        process.wait()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=_TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        raise SearchDriverError("search_backend_failed") from None
