"""NTFS handles for the Runtime's local filesystem operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
from functools import lru_cache
import msvcrt
import os
from pathlib import Path, PureWindowsPath
import stat


_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_security = ctypes.WinDLL("advapi32", use_last_error=True)


def _api(library: ctypes.WinDLL, name: str, result: object, *arguments: object):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


_DWORD = wintypes.DWORD
_HANDLE = wintypes.HANDLE
_PTR = ctypes.c_void_p
_DWORD_PTR = ctypes.POINTER(_DWORD)
_PTR_PTR = ctypes.POINTER(_PTR)
_create_file = _api(
    _kernel, "CreateFileW", _HANDLE, wintypes.LPCWSTR, _DWORD, _DWORD, _PTR,
    _DWORD, _DWORD, _HANDLE,
)
_close = _api(_kernel, "CloseHandle", wintypes.BOOL, _HANDLE)
_final_path = _api(_kernel, "GetFinalPathNameByHandleW", _DWORD, _HANDLE, wintypes.LPWSTR, _DWORD, _DWORD)
_drive_type = _api(_kernel, "GetDriveTypeW", wintypes.UINT, wintypes.LPCWSTR)
_volume = _api(
    _kernel, "GetVolumeInformationW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPWSTR,
    _DWORD, _DWORD_PTR, _DWORD_PTR, _DWORD_PTR, wintypes.LPWSTR, _DWORD,
)
_get_process = _api(_kernel, "GetCurrentProcess", _HANDLE)
_open_token = _api(_security, "OpenProcessToken", wintypes.BOOL, _HANDLE, _DWORD, ctypes.POINTER(_HANDLE))
_token_info = _api(_security, "GetTokenInformation", wintypes.BOOL, _HANDLE, wintypes.INT, _PTR, _DWORD, _DWORD_PTR)
_local_free = _api(_kernel, "LocalFree", _PTR, _PTR)
_sid_string = _api(_security, "ConvertSidToStringSidW", wintypes.BOOL, _PTR, _PTR_PTR)
_security_info = _api(
    _security, "GetSecurityInfo", _DWORD, _HANDLE, wintypes.INT, _DWORD,
    _PTR_PTR, _PTR_PTR, _PTR_PTR, _PTR_PTR, _PTR_PTR,
)
_get_ace = _api(_security, "GetAce", wintypes.BOOL, _PTR, _DWORD, _PTR_PTR)
_set_file_info = _api(
    _kernel, "SetFileInformationByHandle", wintypes.BOOL, _HANDLE, wintypes.INT, _PTR, _DWORD,
)
_move = _api(_kernel, "MoveFileExW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPCWSTR, _DWORD)

_INVALID_HANDLE = ctypes.c_void_p(-1).value
_READ_ATTRIBUTES = 0x80
_LIST_DIRECTORY = 1
_READ_CONTROL = 0x20000
_DELETE = 0x10000
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_SHARE_READ_WRITE = 0x3
_FILE_DIRECTORY = 0x10
_FILE_REPARSE = 0x400
_OPEN_EXISTING = 3
_OPEN_ALWAYS = 4
_CREATE_NEW = 1
_DIRECTORY_AND_REPARSE_FLAGS = 0x02200000
_EXTENDED_PREFIX = "\\\\?\\"


class _FileAttributes(ctypes.Structure):
    _fields_ = [("attributes", _DWORD), ("tag", _DWORD)]


class _RenameInfo(ctypes.Structure):
    _fields_ = [
        ("replace", _DWORD), ("root", _HANDLE), ("length", _DWORD), ("name", wintypes.WCHAR * 1),
    ]


_get_file_info = _api(
    _kernel, "GetFileInformationByHandleEx", wintypes.BOOL, _HANDLE, wintypes.INT, _PTR, _DWORD,
)


def _error(code: int | None = None) -> OSError:
    return ctypes.WinError(ctypes.get_last_error() if code is None else code)


def _extended(path: Path) -> str:
    return _EXTENDED_PREFIX + str(path)


def _check_local_path(path: Path) -> None:
    if (
        not path.is_absolute()
        or len(path.drive) != 2
        or path.drive[1] != ":"
        or any(not valid_name(part) for part in path.parts[1:])
        or _drive_type(path.anchor) != 3  # DRIVE_FIXED
    ):
        raise PermissionError("Windows paths must be on a local drive")
    filesystem = ctypes.create_unicode_buffer(32)
    if not _volume(path.anchor, None, 0, None, None, None, filesystem, len(filesystem)):
        raise _error()
    if filesystem.value != "NTFS":
        raise PermissionError("Windows paths must be on NTFS")


def valid_name(name: str) -> bool:
    return (
        bool(name)
        and name[-1] not in ". "
        and not PureWindowsPath(name).is_reserved()
        and all(ord(character) >= 32 and character not in '<>:"/\\|?*' for character in name)
    )


def _path_for_handle(handle: int) -> Path:
    size = _final_path(handle, None, 0, 0)
    if size == 0 or size > 32768:
        raise _error()
    buffer = ctypes.create_unicode_buffer(size)
    result = _final_path(handle, buffer, size, 0)
    if not 0 < result < size:
        raise _error()
    if not buffer.value.startswith(_EXTENDED_PREFIX):
        raise PermissionError("Windows file has no local DOS path")
    return Path(buffer.value[len(_EXTENDED_PREFIX):])


def _require_kind(handle: int, *, directory: bool) -> None:
    information = _FileAttributes()
    if not _get_file_info(handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
        raise _error()
    if information.attributes & _FILE_REPARSE:
        raise PermissionError("Windows reparse points are outside the local file scope")
    if bool(information.attributes & _FILE_DIRECTORY) != directory:
        raise PermissionError("Windows file type does not match the requested operation")
    if directory:
        flags = _DWORD()
        if not _get_file_info(handle, 23, ctypes.byref(flags), ctypes.sizeof(flags)):
            raise _error()  # FileCaseSensitiveInfo
        if flags.value & 1:
            raise PermissionError("case-sensitive Windows directories are outside the local file scope")


def _open_handle(
    path: Path, access: int, *, directory: bool, share: int = _SHARE_READ_WRITE,
    disposition: int = _OPEN_EXISTING,
) -> int:
    handle = _create_file(_extended(path), access, share, None, disposition, _DIRECTORY_AND_REPARSE_FLAGS, None)
    if handle == _INVALID_HANDLE:
        raise _error()
    try:
        _require_kind(handle, directory=directory)
        # Refuse alternate names for protected paths, including 8.3 aliases.
        if _path_for_handle(handle) != path:
            raise PermissionError("Windows file name does not match its canonical path")
        return handle
    except BaseException:
        _close(handle)
        raise


@contextmanager
def _pinned_directory(path: Path) -> Iterator[int]:
    _check_local_path(path)
    with ExitStack() as stack:
        anchor = Path(path.anchor)
        for depth in range(1, len(path.parts) + 1):
            current = anchor.joinpath(*path.parts[1:depth])
            # Attribute-only opens do not enforce the same sharing checks as
            # data access. Hold list access and deny write/delete sharing.
            handle = _open_handle(current, _LIST_DIRECTORY | _READ_ATTRIBUTES, directory=True, share=1)
            stack.callback(_close, handle)
        yield handle


@contextmanager
def _pinned_parent(path: Path | str, dir_fd: int | None) -> Iterator[Path]:
    name = Path(path)
    if dir_fd is None:
        _check_local_path(name)
        with _pinned_directory(name.parent):
            yield name
        return
    if name.anchor or not name.parts or any(not valid_name(part) for part in name.parts):
        raise PermissionError("invalid Windows relative path")
    root = _path_for_handle(msvcrt.get_osfhandle(dir_fd))
    original = os.fstat(dir_fd)
    with _pinned_directory(root):
        named = root.stat()
        if (named.st_dev, named.st_ino) != (original.st_dev, original.st_ino):
            raise PermissionError("Windows directory identity changed")
        if name.parent == Path("."):
            yield root / name
        else:
            with _pinned_directory(root / name.parent):
                yield root / name


@contextmanager
def named_directory(descriptor: int) -> Iterator[Path]:
    root = _path_for_handle(msvcrt.get_osfhandle(descriptor))
    with _pinned_directory(root):
        named, opened = root.stat(), os.fstat(descriptor)
        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
            raise PermissionError("Windows directory identity changed")
        yield root


def descriptor_path(descriptor: int) -> Path:
    return _path_for_handle(msvcrt.get_osfhandle(descriptor))


def open_directory(path: Path | str, *, dir_fd: int | None = None) -> int:
    with _pinned_parent(path, dir_fd) as target:
        handle = _open_handle(target, _LIST_DIRECTORY | _READ_ATTRIBUTES | _READ_CONTROL, directory=True)
    return _as_fd(handle, os.O_RDONLY)


def open_file(
    path: Path | str, flags: int, *, dir_fd: int | None = None,
) -> int:
    access_mode = flags & (os.O_WRONLY | os.O_RDWR)
    access = _READ_CONTROL | _READ_ATTRIBUTES
    if access_mode != os.O_WRONLY:
        access |= _GENERIC_READ
    if access_mode:
        access |= _GENERIC_WRITE
    disposition = _OPEN_EXISTING
    if flags & os.O_CREAT:
        disposition = _CREATE_NEW if flags & os.O_EXCL else _OPEN_ALWAYS
    with _pinned_parent(path, dir_fd) as target:
        handle = _open_handle(target, access, directory=False, disposition=disposition)
    fd = _as_fd(handle, access_mode | (flags & os.O_APPEND))
    if flags & os.O_TRUNC:
        try:
            os.ftruncate(fd, 0)
        except BaseException:
            os.close(fd)
            raise
    return fd


def _as_fd(handle: int, flags: int) -> int:
    try:
        return msvcrt.open_osfhandle(handle, flags | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        _close(handle)
        raise


def lstat_at(path: Path | str, descriptor: int) -> os.stat_result:
    with _pinned_parent(path, descriptor) as target:
        return target.lstat()


def mkdir_at(path: str, descriptor: int, mode: int) -> None:
    with _pinned_parent(path, descriptor) as target:
        target.mkdir(mode=mode)


def rmdir_at(path: str, descriptor: int) -> None:
    with _pinned_parent(path, descriptor) as target:
        _remove(target, directory=True)


def unlink_at(path: str, descriptor: int) -> None:
    with _pinned_parent(path, descriptor) as target:
        _remove(target, directory=False)


def _remove(path: Path, *, directory: bool) -> None:
    handle = _open_handle(path, _DELETE, directory=directory, share=1)
    try:
        mark_for_deletion(handle)
    finally:
        _close(handle)


def mark_for_deletion(handle: int) -> None:
    disposition = ctypes.c_ubyte(1)
    if not _set_file_info(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
        raise _error()


@contextmanager
def commit_handle(path: Path, *, write: bool = False, delete_access: bool = False) -> Iterator[int]:
    """Hold an existing regular file against rename, deletion and other writers."""
    access = _GENERIC_READ | _READ_CONTROL
    flags = os.O_RDONLY
    if write:
        access |= _GENERIC_WRITE
        flags = os.O_RDWR
    if delete_access:
        access |= _DELETE
    with _pinned_parent(path, None):
        handle = _open_handle(path, access, directory=False, share=1)
        descriptor = _as_fd(handle, flags)
        try:
            yield descriptor
        finally:
            os.close(descriptor)


def delete_open_file(descriptor: int) -> None:
    mark_for_deletion(msvcrt.get_osfhandle(descriptor))


def publish_open_file(descriptor: int, target: Path) -> None:
    # The source is the validated open handle. FileRenameInfo with Replace
    # false provides an exclusive, same-volume name publication.
    with _pinned_parent(target, None):
        name = _extended(target).encode("utf-16-le")
        size = _RenameInfo.name.offset + len(name) + 2
        buffer = ctypes.create_string_buffer(max(size, ctypes.sizeof(_RenameInfo)))
        info = _RenameInfo.from_buffer(buffer)
        info.length = len(name)
        ctypes.memmove(ctypes.addressof(buffer) + _RenameInfo.name.offset, name, len(name))
        handle = msvcrt.get_osfhandle(descriptor)
        if not _set_file_info(handle, 3, buffer, size):
            raise _error()


def replace(source: Path, target: Path) -> None:
    with _pinned_parent(source, None), _pinned_parent(target, None):
        if not _move(_extended(source), _extended(target), 0x9):  # REPLACE_EXISTING | WRITE_THROUGH
            raise _error()


def _sid_text(sid: int) -> str:
    result = _PTR()
    if not _sid_string(sid, ctypes.byref(result)):
        raise _error()
    try:
        return ctypes.wstring_at(result)
    finally:
        _local_free(result)


@lru_cache(maxsize=1)
def _current_sid() -> str:
    token = _HANDLE()
    if not _open_token(_get_process(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        raise _error()
    try:
        size = _DWORD()
        _token_info(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        if not 0 < size.value <= 65536:
            raise _error()
        buffer = ctypes.create_string_buffer(size.value)
        if not _token_info(token, 1, buffer, size, ctypes.byref(size)):
            raise _error()
        return _sid_text(ctypes.cast(buffer, _PTR_PTR).contents.value)
    finally:
        _close(token)


def _check_security(handle: int, *, private: bool, asset: bool) -> bool:
    owner, dacl, descriptor = _PTR(), _PTR(), _PTR()
    status = _security_info(
        handle, 1, 0x5 if private or asset else 0x1, ctypes.byref(owner), None,
        ctypes.byref(dacl) if private or asset else None, None, ctypes.byref(descriptor),
    )
    if status:
        raise _error(status)
    try:
        sid = _current_sid()
        allowed = {sid, "S-1-5-18", "S-1-5-32-544"}  # SYSTEM, Administrators
        if not owner or _sid_text(owner.value) not in (allowed if asset else {sid}):
            return False
        if not (private or asset):
            return True
        if not dacl:
            return False
        # ACL header: revision, reserved, byte size, entry count, reserved.
        count = ctypes.c_ushort.from_address(dacl.value + 4).value
        allowed.add("S-1-3-4")  # OWNER RIGHTS, used by CPython's private mkdir
        for index in range(count):
            ace = _PTR()
            if not _get_ace(dacl, index, ctypes.byref(ace)):
                raise _error()
            kind = ctypes.c_ubyte.from_address(ace.value).value
            if kind == 1:  # ACCESS_DENIED_ACE_TYPE
                continue
            if kind != 0:
                return False
            if _sid_text(ace.value + 8) not in allowed:
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                write_rights = 0x500D0116  # generic write/all, write/append/attributes, delete, change ACL/owner
                if not asset or mask & write_rights:
                    return False
        return True
    finally:
        _local_free(descriptor)


def owner_matches(path: Path | int, *, private: bool = False, asset: bool = False) -> bool:
    try:
        if isinstance(path, int):
            return _check_security(msvcrt.get_osfhandle(path), private=private, asset=asset)
        with _pinned_parent(path, None) as target:
            metadata = target.lstat()
            handle = _open_handle(target, _READ_CONTROL | _READ_ATTRIBUTES, directory=stat.S_ISDIR(metadata.st_mode))
            try:
                return _check_security(handle, private=private, asset=asset)
            finally:
                _close(handle)
    except OSError:
        return False


def executable_version(path: Path) -> str:
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(_PTR), _DWORD_PTR]
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not 0 < size <= 1024 * 1024:
        return "unknown"
    data = ctypes.create_string_buffer(size)
    info, length = _PTR(), _DWORD()
    if not version.GetFileVersionInfoW(str(path), 0, size, data) or not version.VerQueryValueW(
        data, "\\", ctypes.byref(info), ctypes.byref(length),
    ):
        return "unknown"
    if not info or length.value < 16:
        return "unknown"
    words = ctypes.cast(info, _DWORD_PTR)
    if words[0] != 0xFEEF04BD:  # VS_FIXEDFILEINFO signature
        return "unknown"
    ms, ls = words[2], words[3]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
