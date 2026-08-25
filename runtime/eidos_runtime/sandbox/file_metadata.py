from __future__ import annotations

import ctypes
import errno
import sys


COPYFILE_ACL = 1 << 0
COPYFILE_XATTR = 1 << 2
DARWIN_ACL_TYPE_EXTENDED = 0x00000100
MAX_XATTR_COUNT = 256
MAX_XATTR_BYTES = 4 * 1024 * 1024


class FileMetadataError(RuntimeError):
    pass


def copy_replace_metadata(source_fd: int, target_fd: int) -> None:
    """Copy replace-sensitive metadata between already-verified file descriptors."""
    if sys.platform != "darwin":
        return
    before = _darwin_metadata_signature(source_fd)
    libc = _darwin_libc()
    fcopyfile = libc.fcopyfile
    fcopyfile.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    fcopyfile.restype = ctypes.c_int
    ctypes.set_errno(0)
    if fcopyfile(
        source_fd,
        target_fd,
        None,
        COPYFILE_ACL | COPYFILE_XATTR,
    ) != 0:
        raise FileMetadataError(
            f"fcopyfile failed with errno {ctypes.get_errno()}"
        )
    after = _darwin_metadata_signature(target_fd)
    if after != before:
        raise FileMetadataError("copied file metadata did not match the source")


def _darwin_metadata_signature(
    descriptor: int,
) -> tuple[tuple[tuple[bytes, bytes], ...], bytes | None]:
    libc = _darwin_libc()
    xattrs = _darwin_xattrs(libc, descriptor)
    acl = _darwin_acl_text(libc, descriptor)
    return tuple(sorted(xattrs.items())), acl


def _darwin_xattrs(libc, descriptor: int) -> dict[bytes, bytes]:
    flistxattr = libc.flistxattr
    fgetxattr = libc.fgetxattr
    flistxattr.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_int,
    ]
    flistxattr.restype = ctypes.c_ssize_t
    fgetxattr.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    fgetxattr.restype = ctypes.c_ssize_t

    ctypes.set_errno(0)
    required = flistxattr(descriptor, None, 0, 0)
    if required < 0:
        raise FileMetadataError(
            f"flistxattr failed with errno {ctypes.get_errno()}"
        )
    if required == 0:
        return {}
    if required > MAX_XATTR_BYTES:
        raise FileMetadataError("extended attribute names exceed the limit")
    names_buffer = ctypes.create_string_buffer(required)
    actual = flistxattr(descriptor, names_buffer, required, 0)
    if actual != required:
        raise FileMetadataError("extended attribute names changed during read")
    names = tuple(
        name for name in names_buffer.raw[:actual].split(b"\0") if name
    )
    if len(names) > MAX_XATTR_COUNT:
        raise FileMetadataError("extended attribute count exceeds the limit")

    values: dict[bytes, bytes] = {}
    total = required
    for name in names:
        ctypes.set_errno(0)
        value_size = fgetxattr(descriptor, name, None, 0, 0, 0)
        if value_size < 0:
            raise FileMetadataError(
                f"fgetxattr failed with errno {ctypes.get_errno()}"
            )
        total += value_size
        if total > MAX_XATTR_BYTES:
            raise FileMetadataError("extended attribute data exceeds the limit")
        if value_size == 0:
            values[name] = b""
            continue
        value_buffer = ctypes.create_string_buffer(value_size)
        actual_size = fgetxattr(
            descriptor,
            name,
            value_buffer,
            value_size,
            0,
            0,
        )
        if actual_size != value_size:
            raise FileMetadataError(
                "extended attribute changed during read"
            )
        values[name] = value_buffer.raw[:actual_size]
    return values


def _darwin_acl_text(libc, descriptor: int) -> bytes | None:
    acl_get_fd_np = libc.acl_get_fd_np
    acl_to_text = libc.acl_to_text
    acl_free = libc.acl_free
    acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    acl_get_fd_np.restype = ctypes.c_void_p
    acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
    acl_to_text.restype = ctypes.c_void_p
    acl_free.argtypes = [ctypes.c_void_p]
    acl_free.restype = ctypes.c_int

    ctypes.set_errno(0)
    acl = acl_get_fd_np(descriptor, DARWIN_ACL_TYPE_EXTENDED)
    if not acl:
        if ctypes.get_errno() == errno.ENOENT:
            return None
        raise FileMetadataError(
            f"acl_get_fd_np failed with errno {ctypes.get_errno()}"
        )
    text_pointer = None
    try:
        length = ctypes.c_ssize_t()
        text_pointer = acl_to_text(acl, ctypes.byref(length))
        if not text_pointer:
            raise FileMetadataError(
                f"acl_to_text failed with errno {ctypes.get_errno()}"
            )
        return ctypes.string_at(text_pointer, length.value)
    finally:
        if text_pointer:
            acl_free(text_pointer)
        acl_free(acl)


def _darwin_libc():
    try:
        return ctypes.CDLL(None, use_errno=True)
    except OSError as error:
        raise FileMetadataError("Darwin metadata APIs are unavailable") from error
