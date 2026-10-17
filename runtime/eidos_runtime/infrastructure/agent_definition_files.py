"""Bounded, version-checked access to the user's fixed-schema TOML files."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tomllib
from collections.abc import Iterator

from eidos_runtime.domain.agents import AgentDefinition, MAX_AGENT_FILE_BYTES


class AgentDefinitionFileError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def read_definition_bytes(directory: int, name: str) -> bytes:
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory
    )
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.getuid()
            or before.st_size > MAX_AGENT_FILE_BYTES
        ):
            raise ValueError("unsafe_file")
        raw = stream.read(MAX_AGENT_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > MAX_AGENT_FILE_BYTES or any(
        getattr(before, key) != getattr(after, key)
        for key in (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    ):
        raise ValueError("changed_file")
    return raw


def encode_definition(definition: AgentDefinition) -> bytes:
    # JSON's quoted scalar strings also form TOML basic strings. This adapter
    # writes only AgentDefinition's fixed fields; tomllib validates the result.
    values = definition.model_dump(exclude_none=True)
    model = values.pop("model", None)
    lines = [
        f"{key} = {json.dumps(value, ensure_ascii=False)}"
        for key, value in values.items()
    ]
    if model:
        lines += ["", "[model]"]
        lines += [
            f"{key} = {json.dumps(value, ensure_ascii=False)}"
            for key, value in model.items()
        ]
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    if len(raw) > MAX_AGENT_FILE_BYTES:
        raise AgentDefinitionFileError(
            "AGENT_DEFINITION_INVALID", "智能体配置超过 64 KiB，请缩短指令。"
        )
    if AgentDefinition.model_validate(tomllib.loads(raw.decode())) != definition:
        raise AgentDefinitionFileError(
            "AGENT_DEFINITION_INVALID", "智能体配置无法保存。"
        )
    return raw


class AgentDefinitionFiles:
    def __init__(self, root: Path):
        self.root = root

    @contextmanager
    def directory(self, *, create: bool = False) -> Iterator[int]:
        try:
            if create:
                self.root.mkdir(mode=0o700, exist_ok=True)
            descriptor = os.open(
                self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                if os.fstat(descriptor).st_uid != os.getuid():
                    raise ValueError("owner")
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._check_directory(descriptor)
                yield descriptor
            finally:
                os.close(descriptor)
        except AgentDefinitionFileError:
            raise
        except BlockingIOError as error:
            raise AgentDefinitionFileError(
                "AGENT_DEFINITION_BUSY", "智能体配置正在被修改，请稍后重试。"
            ) from error
        except (OSError, ValueError) as error:
            raise AgentDefinitionFileError(
                "AGENT_DEFINITION_IO_ERROR",
                "智能体配置不可读写，请检查文件和目录权限。",
            ) from error

    def _check_directory(self, directory: int) -> None:
        opened = os.fstat(directory)
        current = os.stat(self.root, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (
            current.st_dev,
            current.st_ino,
        ) or not stat.S_ISDIR(current.st_mode):
            raise AgentDefinitionFileError(
                "AGENT_DEFINITION_CONFLICT", "智能体目录已经变化，请刷新后重试。"
            )

    def filename(self, source: str) -> str:
        path = Path(source)
        if (
            path.parent != self.root
            or path.name in {"", ".", ".."}
            or not path.name.endswith(".toml")
        ):
            raise AgentDefinitionFileError(
                "AGENT_DEFINITION_INVALID", "智能体配置路径无效。"
            )
        return path.name

    def read(self, directory: int, filename: str) -> bytes:
        return read_definition_bytes(directory, filename)

    def check_version(self, raw: bytes, expected: str | None) -> None:
        if expected is None or hashlib.sha256(raw).hexdigest() != expected:
            raise AgentDefinitionFileError(
                "AGENT_DEFINITION_CONFLICT", "智能体配置已经变化，请重新打开后再修改。"
            )

    def write(
        self, directory: int, filename: str, raw: bytes, expected: str | None
    ) -> None:
        self._check_directory(directory)
        flags = os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW
        flags |= os.O_EXCL | os.O_CREAT if expected is None else 0
        try:
            descriptor = os.open(filename, flags, 0o600, dir_fd=directory)
        except FileExistsError as error:
            raise AgentDefinitionFileError(
                "AGENT_DEFINITION_EXISTS", "同名智能体配置文件已经存在。"
            ) from error
        with os.fdopen(descriptor, "wb") as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
            ):
                raise ValueError("unsafe_file")
            if expected is not None:
                self.check_version(self.read(directory, filename), expected)
            current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
            if (
                before.st_dev,
                before.st_ino,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                current.st_dev,
                current.st_ino,
                current.st_mtime_ns,
                current.st_ctime_ns,
            ):
                raise AgentDefinitionFileError(
                    "AGENT_DEFINITION_CONFLICT",
                    "智能体配置已经变化，请重新打开后再修改。",
                )
            try:
                stream.truncate(0)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            except OSError as error:
                raise AgentDefinitionFileError(
                    "AGENT_DEFINITION_WRITE_UNCERTAIN",
                    "写入未能确认，配置可能已经变化，请刷新列表并检查文件。",
                ) from error
        self._check_directory(directory)
        if self.read(directory, filename) != raw:
            raise AgentDefinitionFileError(
                "AGENT_DEFINITION_CONFLICT",
                "智能体配置保存后发生变化，请刷新并检查文件。",
            )
        try:
            os.fsync(directory)
        except OSError as error:
            raise AgentDefinitionFileError(
                "AGENT_DEFINITION_WRITE_UNCERTAIN",
                "保存未能确认，配置可能已经变化，请刷新列表并检查文件。",
            ) from error

    def remove(self, directory: int, filename: str, expected: str) -> None:
        self._check_directory(directory)
        before = os.stat(filename, dir_fd=directory, follow_symlinks=False)
        self.check_version(self.read(directory, filename), expected)
        if before != os.stat(filename, dir_fd=directory, follow_symlinks=False):
            raise AgentDefinitionFileError(
                "AGENT_DEFINITION_CONFLICT", "智能体配置已经变化，请刷新后重试。"
            )
        os.unlink(filename, dir_fd=directory)
        try:
            os.fsync(directory)
        except OSError as error:
            raise AgentDefinitionFileError(
                "AGENT_DEFINITION_WRITE_UNCERTAIN",
                "删除未能确认，配置可能已经变化，请刷新列表并检查文件。",
            ) from error
