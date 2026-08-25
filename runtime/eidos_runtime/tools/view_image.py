from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from typing import Final
import zlib

from pydantic import ValidationError

from eidos_runtime.tools.contracts import (
    ViewImageInput,
    ViewImageResultData,
    result_model,
)
from eidos_runtime.tools.registry import (
    ToolProvenance,
    ToolRegistryEntry,
    ToolSpec,
)


MAX_VIEW_IMAGE_BYTES: Final = 10 * 1024 * 1024
_READ_CHUNK_BYTES: Final = 1024 * 1024
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI: Final = b"\xff\xd8"
_JPEG_SOF_MARKERS: Final = frozenset({
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
})


class ViewImageError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ViewImageRootAuthority:
    """Canonical filesystem roots supplied by the trusted Run owner."""

    workspace_root: Path
    active_skill_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        canonical_workspace = _canonical_root(self.workspace_root)
        canonical_skills: list[Path] = []
        seen = {canonical_workspace}
        for root in self.active_skill_roots:
            canonical = _canonical_root(root)
            if canonical not in seen:
                canonical_skills.append(canonical)
                seen.add(canonical)
        object.__setattr__(self, "workspace_root", canonical_workspace)
        object.__setattr__(self, "active_skill_roots", tuple(canonical_skills))

    @property
    def roots(self) -> tuple[Path, ...]:
        return (self.workspace_root, *self.active_skill_roots)


# These aliases make the authority seam discoverable to callers that describe
# the same object as image roots rather than a view-image authority.
ImageRootAuthority = ViewImageRootAuthority
ViewImageRoots = ViewImageRootAuthority


@dataclass(frozen=True)
class AuthorizedImage:
    path: Path
    mime: str
    size: int
    sha256: str
    data: bytes


class ViewImageTool:
    def __init__(self, authority: ViewImageRootAuthority) -> None:
        self.authority = authority

    def execute(
        self,
        arguments: dict[str, object],
        cancel: threading.Event,
    ) -> dict[str, object]:
        if cancel.is_set():
            return _error("tool_canceled", "Image read canceled")
        try:
            request = ViewImageInput.model_validate(arguments)
        except ValidationError:
            return _error("invalid_path", "Image path is invalid")
        try:
            image = read_authorized_image(request.path, self.authority)
        except ViewImageError as error:
            summaries = {
                "image_not_found": "Image file was not found",
                "image_too_large": "Image file is too large",
                "invalid_image": "File is not a supported PNG or JPEG image",
                "path_outside_authority": "Image path is outside the authorized roots",
                "unsafe_path": "Image path is not a safe regular file",
            }
            return _error(error.code, summaries.get(error.code, "Image could not be read"))
        return {
            "outcome": "success",
            "code": "ok",
            "summary": "Image loaded",
            "data": {
                "path": str(image.path),
                "mime": image.mime,
                "size": image.size,
                "sha256": image.sha256,
            },
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }


def view_image_entry(
    *,
    supports_images: bool,
    authority: ViewImageRootAuthority,
) -> ToolRegistryEntry | None:
    """Build the built-in entry only for a model that accepts image inputs."""

    if not supports_images:
        return None
    input_schema = ViewImageInput.model_json_schema(by_alias=True)
    result_schema = result_model(ViewImageResultData).model_json_schema(by_alias=True)
    encoded = json.dumps(
        (input_schema, result_schema),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return ToolRegistryEntry(
        spec=ToolSpec.model_validate({
            "name": "view_image",
            "description": (
                "View a local PNG or JPEG image from the authorized workspace or active Skill roots."
            ),
            "sideEffect": "none",
            "approvalRequired": False,
            "timeoutSeconds": 5,
            "batchPolicy": "parallel",
            "visibility": "direct",
            "inputSchema": input_schema,
            "resultSchema": result_schema,
            "modelProjectionPolicy": "view_image",
            "contractVersion": 1,
        }),
        provenance=ToolProvenance.model_validate({
            "kind": "builtin",
            "sourceId": "eidos.view-image",
            "sourceVersion": "1",
            "contentHash": hashlib.sha256(encoded).hexdigest(),
        }),
        adapter=ViewImageTool(authority),
        input_model=ViewImageInput,
        result_data_model=ViewImageResultData,
    )


def build_view_image_entry(
    *,
    supports_images: bool,
    authority: ViewImageRootAuthority,
) -> ToolRegistryEntry | None:
    return view_image_entry(
        supports_images=supports_images,
        authority=authority,
    )


def read_authorized_image(
    path: str,
    authority: ViewImageRootAuthority,
) -> AuthorizedImage:
    parts = _path_parts(path)
    candidates = _candidates(path, parts, authority)
    if not candidates:
        raise ViewImageError("path_outside_authority")

    for root, relative_parts in candidates:
        try:
            data, canonical_path = _read_regular_file(root, relative_parts)
        except FileNotFoundError:
            continue
        image_mime = _detect_image_mime(data)
        return AuthorizedImage(
            path=canonical_path,
            mime=image_mime,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            data=data,
        )
    raise ViewImageError("image_not_found")


def _canonical_root(root: Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute():
        raise ValueError("image_root_must_be_absolute")
    try:
        canonical = candidate.resolve(strict=True)
        metadata = os.stat(canonical, follow_symlinks=False)
    except OSError as error:
        raise ValueError("image_root_unavailable") from error
    if canonical != candidate or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("image_root_must_be_canonical_directory")
    if metadata.st_uid != os.getuid():
        raise ValueError("image_root_owner_mismatch")
    return canonical


def _path_parts(path: str) -> tuple[str, ...]:
    if (
        not isinstance(path, str)
        or not path
        or "\x00" in path
        or "\\" in path
        or path.endswith("/")
    ):
        raise ViewImageError("invalid_path")
    parts = path.split("/")
    if path.startswith("/"):
        parts = parts[1:]
    if any(part in {"", ".", ".."} for part in parts):
        raise ViewImageError("invalid_path")
    if path.startswith("/"):
        return tuple(Path(path).parts[1:])
    return tuple(path.split("/"))


def _candidates(
    path: str,
    parts: tuple[str, ...],
    authority: ViewImageRootAuthority,
) -> tuple[tuple[Path, tuple[str, ...]], ...]:
    if path.startswith("/"):
        absolute = Path(path)
        return tuple(
            (root, tuple(absolute.relative_to(root).parts))
            for root in authority.roots
            if _is_relative_to(absolute, root)
        )
    return tuple((root, parts) for root in authority.roots)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_regular_file(
    root: Path,
    relative_parts: tuple[str, ...],
) -> tuple[bytes, Path]:
    if not relative_parts:
        raise ViewImageError("unsafe_path")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    root_fd = os.open(root, directory_flags | nofollow | cloexec)
    current_fd = root_fd
    try:
        _validate_directory_fd(current_fd)
        for component in relative_parts[:-1]:
            next_fd = os.open(
                component,
                directory_flags | nofollow | cloexec,
                dir_fd=current_fd,
            )
            try:
                _validate_directory_fd(next_fd)
            except Exception:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
        try:
            file_fd = os.open(
                relative_parts[-1],
                os.O_RDONLY | nofollow | cloexec,
                dir_fd=current_fd,
            )
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM}:
                raise ViewImageError("unsafe_path") from error
            raise
        try:
            metadata = os.fstat(file_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
            ):
                raise ViewImageError("unsafe_path")
            if metadata.st_size > MAX_VIEW_IMAGE_BYTES:
                raise ViewImageError("image_too_large")
            data = _read_exact(file_fd, metadata.st_size)
            after = os.fstat(file_fd)
            if (
                after.st_dev != metadata.st_dev
                or after.st_ino != metadata.st_ino
                or after.st_size != metadata.st_size
            ):
                raise ViewImageError("unsafe_path")
        finally:
            os.close(file_fd)
    except FileNotFoundError:
        raise
    except ViewImageError:
        raise
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM}:
            raise ViewImageError("unsafe_path") from error
        raise
    finally:
        os.close(current_fd)
    return data, root.joinpath(*relative_parts)


def _validate_directory_fd(directory_fd: int) -> None:
    metadata = os.fstat(directory_fd)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ViewImageError("unsafe_path")


def _read_exact(file_fd: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(file_fd, min(remaining, _READ_CHUNK_BYTES))
        if not chunk:
            raise ViewImageError("unsafe_path")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _detect_image_mime(data: bytes) -> str:
    if data.startswith(_PNG_SIGNATURE):
        _validate_png(data)
        return "image/png"
    if data.startswith(_JPEG_SOI):
        _validate_jpeg(data)
        return "image/jpeg"
    raise ViewImageError("invalid_image")


def _validate_png(data: bytes) -> None:
    if len(data) < len(_PNG_SIGNATURE) + 12:
        raise ViewImageError("invalid_image")
    offset = len(_PNG_SIGNATURE)
    saw_ihdr = False
    saw_idat = False
    saw_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ViewImageError("invalid_image")
        length = int.from_bytes(data[offset:offset + 4], "big")
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(data):
            raise ViewImageError("invalid_image")
        kind = data[offset + 4:offset + 8]
        payload = data[chunk_start:chunk_end]
        crc = int.from_bytes(data[chunk_end:chunk_end + 4], "big")
        if not all(
            65 <= value <= 90 or 97 <= value <= 122
            for value in kind
        ) or (
            zlib.crc32(kind + payload) & 0xFFFFFFFF
        ) != crc:
            raise ViewImageError("invalid_image")
        if not saw_ihdr:
            if kind != b"IHDR" or len(payload) != 13:
                raise ViewImageError("invalid_image")
            width = int.from_bytes(payload[0:4], "big")
            height = int.from_bytes(payload[4:8], "big")
            bit_depth = payload[8]
            color_type = payload[9]
            valid_bit_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                not width
                or not height
                or bit_depth not in valid_bit_depths.get(color_type, set())
                or payload[10] != 0
                or payload[11] != 0
                or payload[12] not in {0, 1}
            ):
                raise ViewImageError("invalid_image")
            saw_ihdr = True
        elif kind == b"IHDR":
            raise ViewImageError("invalid_image")
        if kind == b"IDAT":
            saw_idat = True
        if kind == b"IEND":
            if payload or not saw_ihdr or not saw_idat:
                raise ViewImageError("invalid_image")
            saw_iend = True
            offset = chunk_end + 4
            break
        offset = chunk_end + 4
    if not saw_iend or offset != len(data):
        raise ViewImageError("invalid_image")


def _validate_jpeg(data: bytes) -> None:
    if len(data) < 4 or not data.startswith(_JPEG_SOI):
        raise ViewImageError("invalid_image")
    offset = 2
    saw_sof = False
    saw_sos = False
    saw_eoi = False
    while offset < len(data):
        if data[offset] != 0xFF:
            raise ViewImageError("invalid_image")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            raise ViewImageError("invalid_image")
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            saw_eoi = True
            break
        if marker == 0xDA:
            offset = _read_jpeg_segment_end(data, offset)
            saw_sos = True
            offset, marker = _skip_jpeg_scan(data, offset)
            if marker == 0xD9:
                saw_eoi = True
                offset += 2
                break
            continue
        if marker in {0x01, *range(0xD0, 0xD8)}:
            continue
        segment_start = offset
        offset = _read_jpeg_segment_end(data, segment_start)
        if marker in _JPEG_SOF_MARKERS:
            length = int.from_bytes(data[segment_start:segment_start + 2], "big")
            payload = data[segment_start + 2:segment_start + length]
            if len(payload) < 6 or not int.from_bytes(payload[1:3], "big") or not int.from_bytes(payload[3:5], "big"):
                raise ViewImageError("invalid_image")
            components = payload[5]
            if not components or len(payload) != 6 + components * 3:
                raise ViewImageError("invalid_image")
            saw_sof = True
    if not saw_sof or not saw_sos or not saw_eoi or offset != len(data):
        raise ViewImageError("invalid_image")


def _read_jpeg_segment_end(data: bytes, marker_offset: int) -> int:
    if marker_offset + 2 > len(data):
        raise ViewImageError("invalid_image")
    length = int.from_bytes(data[marker_offset:marker_offset + 2], "big")
    if length < 2 or marker_offset + length > len(data):
        raise ViewImageError("invalid_image")
    return marker_offset + length


def _skip_jpeg_scan(data: bytes, offset: int) -> tuple[int, int]:
    while offset < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            raise ViewImageError("invalid_image")
        marker = data[offset]
        if marker == 0:
            offset += 1
            continue
        return offset - 1, marker
    raise ViewImageError("invalid_image")


def _error(code: str, summary: str) -> dict[str, object]:
    return {
        "outcome": "error",
        "code": code,
        "summary": summary,
        "data": {},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }
