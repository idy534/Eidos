from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
from collections.abc import Callable

from PIL import Image
from charset_normalizer import from_bytes

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.extensions import ExtensionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.input_reference import InputReference, InputSnapshot
from eidos_runtime.persistence.input_context import InputContextRepository
from eidos_runtime.protocol.input_context import (
    DraftReadRequest, DraftWriteRequest, DraftResponse, InputPrepareRequest,
    InputReadRequest, InputReferenceResponse, InputPreviewResponse,
    InputReadAssetRequest, InputReadAssetResponse,
)
from eidos_runtime.tools.view_image import read_authorized_image, ViewImageRootAuthority

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 65536


class InputContextApplication:
    def __init__(self, store: SessionStore, extensions: ExtensionApplication, scan_text: Callable[[str], str]):
        self.store = store
        self.repository = InputContextRepository(store.database)
        self.extensions = extensions
        self.scan_text = scan_text

    def prepare(self, request: InputPrepareRequest) -> InputReferenceResponse:
        try:
            snapshot = self._prepare(request)
            self.repository.save(snapshot)
            return InputReferenceResponse(reference=snapshot.reference)
        except ApplicationError:
            raise
        except (OSError, ValueError, UnicodeError) as error:
            raise ApplicationError('INVALID_PARAMS', '引用不可用，请检查路径、格式或大小。') from error

    def _prepare(self, request: InputPrepareRequest) -> InputSnapshot:
        kind, source = request.kind, request.source
        label = request.label or source
        text, image, mime, selection_id, plugin_id = '', None, None, None, request.plugin_id
        image_tokens = 0
        status = 'content'
        raw = b''
        if kind in {'file', 'directory', 'image'}:
            path = Path(source).expanduser().resolve(strict=True)
            data = self.store.database.data_directory
            if data is not None and path.is_relative_to(data.resolve()):
                if request.session_id is None:
                    raise ApplicationError('WORKSPACE_SENSITIVE_PATH')
                workspace = self.store.workspace_for_session(request.session_id).path.resolve()
                if workspace == data.resolve() or not path.is_relative_to(workspace):
                    raise ApplicationError('WORKSPACE_SENSITIVE_PATH')
            source, label = str(path), path.name or str(path)
            descriptor = _open_path(path)
            try:
                before = os.fstat(descriptor)
                if stat.S_ISDIR(before.st_mode):
                    kind, status = 'directory', 'location'
                    names = []
                    with os.scandir(descriptor) as entries:
                        for entry in entries:
                            if len(names) >= 200:
                                names.append('…目录摘要仅包含前 200 项')
                                break
                            names.append(entry.name + ('/' if entry.is_dir(follow_symlinks=False) else ''))
                    text = '目录位置；未读取子文件内容，也未授予写权限。\n' + '\n'.join(sorted(names))
                    raw = text.encode()
                else:
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE_BYTES:
                        raise ValueError('unsafe_or_large_file')
                    with os.fdopen(os.dup(descriptor), 'rb') as stream:
                        raw = stream.read(MAX_FILE_BYTES + 1)
                    after = os.fstat(descriptor)
                    if len(raw) > MAX_FILE_BYTES or _identity(before) != _identity(after):
                        raise ValueError('file_changed')
                    if path.suffix.lower() in {'.png', '.jpg', '.jpeg'} or kind == 'image':
                        picture = read_authorized_image(source, ViewImageRootAuthority(path.parent))
                        if hashlib.sha256(raw).hexdigest() != picture.sha256:
                            raise ValueError('file_changed')
                        kind, mime = 'image', picture.mime
                        image = base64.b64encode(raw).decode('ascii')
                        with Image.open(io.BytesIO(raw)) as picture:
                            image_tokens = 1024 + math.ceil(picture.width / 512) * math.ceil(picture.height / 512) * 256
                        text = '图片内容已固定。'
                    elif path.suffix.lower() not in {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.gz', '.tar', '.7z'} and b'\0' not in raw and len(raw) <= 256 * 1024:
                        decoded = from_bytes(raw).best()
                        if decoded is not None:
                            lines = str(decoded).splitlines(keepends=True)
                            if request.start_line is not None:
                                if request.end_line > len(lines):
                                    raise ValueError('line_range_outside_file')
                                lines = lines[request.start_line - 1:request.end_line]
                                label += f':{request.start_line}-{request.end_line}'
                            full_text = ''.join(lines)
                            text = full_text[:MAX_TEXT_CHARS - 40]
                            if len(full_text) > len(text):
                                text += '\n[引用内容已截断]'
                        else:
                            status = 'location'
                    else:
                        status = 'location'
                    if status == 'location':
                        text = '仅提供文件位置和版本；文件未被解析。后续读取仍遵循当前 Run 权限。'
            finally:
                os.close(descriptor)
            if request.origin == 'clipboard':
                if kind != 'image':
                    raise ValueError('clipboard_requires_image')
                source, label = 'clipboard:image', '粘贴的图片'
        elif kind == 'history':
            title, text = self.repository.history_excerpt(source, request.item_ids)
            label = request.label or title
            raw = text.encode()
        elif kind == 'excerpt':
            text = request.text or ''
            if not text.strip():
                raise ValueError('empty_excerpt')
            raw = text.encode()
        else:
            status, selection_id = 'selection', source
            if kind == 'skill':
                selected = next((item for item in self.extensions.list_skills().skills if item.qualified_id == source and item.available and item.enabled), None)
                if selected is None:
                    raise ValueError('skill_unavailable')
                label, text = selected.name, selected.description
                raw = selected.content_hash.encode()
            elif kind == 'plugin':
                selected = next((item for item in self.extensions.list_plugins().plugins if item.id == source and item.enabled and item.status == 'installed'), None)
                if selected is None:
                    raise ValueError('plugin_unavailable')
                label, text = selected.name, selected.description
                raw = selected.content_hash.encode()
            elif kind == 'mcp':
                selected = next((item for item in self.extensions.list_mcp_servers().servers if item.server_id == source and item.plugin_id == plugin_id and item.available and item.consented), None)
                if selected is None:
                    raise ValueError('mcp_unavailable')
                label = source
                text = '用户选择此 MCP；工具调用仍需遵循当前权限与审批。'
                raw = selected.model_dump_json().encode()
        text = self.scan_text(text)
        source = self.scan_text(source)
        label = self.scan_text(label)[:512]
        digest = hashlib.sha256(raw).hexdigest()
        identifier = hashlib.sha256(json.dumps([kind, source, label, text, digest, plugin_id], ensure_ascii=False).encode()).hexdigest()
        reference = InputReference(id=identifier, kind=kind, label=label, source=source, sha256=digest, status=status, size=len(raw))
        return InputSnapshot(reference=reference, text=text, image=image, mime=mime, image_token_estimate=image_tokens, selection_id=selection_id, plugin_id=plugin_id)

    def read(self, request: InputReadRequest) -> InputPreviewResponse:
        try:
            snapshot = self.repository.read(request.id)
        except ValueError as error:
            raise ApplicationError('RESOURCE_NOT_FOUND') from error
        thumbnail = None
        if snapshot.image:
            with Image.open(io.BytesIO(base64.b64decode(snapshot.image))) as picture:
                picture.thumbnail((480, 480))
                buffer = io.BytesIO()
                picture.convert('RGB').save(buffer, format='JPEG', quality=75)
                thumbnail = 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()
        return InputPreviewResponse(reference=snapshot.reference, text=snapshot.text, thumbnail=thumbnail)

    def read_asset(self, request: InputReadAssetRequest) -> InputReadAssetResponse:
        try:
            snapshot = self.repository.read(request.id)
        except ValueError as error:
            raise ApplicationError('RESOURCE_NOT_FOUND') from error
        if not snapshot.image:
            raise ApplicationError('INVALID_PARAMS', '引用不包含图片内容。')
        raw = base64.b64decode(snapshot.image)
        chunk = raw[request.offset : request.offset + 192 * 1024]
        complete = request.offset + len(chunk) >= len(raw)
        return InputReadAssetResponse(
            id=request.id,
            data=base64.b64encode(chunk).decode('ascii'),
            mime_type=snapshot.mime or 'image/png',
            size_bytes=len(raw),
            next_offset=request.offset + len(chunk),
            complete=complete,
        )

    def read_draft(self, request: DraftReadRequest) -> DraftResponse:
        self._validate_draft_key(request.key)
        draft = self.repository.read_draft(request.key)
        return DraftResponse(text=draft.text, references=list(draft.references))

    def write_draft(self, request: DraftWriteRequest) -> DraftResponse:
        self._validate_draft_key(request.key)
        draft = self.repository.write_draft(request.key, self.scan_text(request.text), request.references)
        return DraftResponse(text=draft.text, references=list(draft.references))

    def _validate_draft_key(self, key: str) -> None:
        if key != 'new-conversation' and self.store.read_session(key) is None:
            raise ApplicationError('RESOURCE_NOT_FOUND')

    def validate_selections(self, snapshots: list[InputSnapshot]) -> None:
        for snapshot in snapshots:
            if snapshot.reference.status != 'selection':
                continue
            current = self._prepare(InputPrepareRequest(
                kind=snapshot.reference.kind, source=snapshot.selection_id or snapshot.reference.source,
                pluginId=snapshot.plugin_id,
            ))
            if current.reference.sha256 != snapshot.reference.sha256:
                raise ApplicationError('INVALID_STATE', '扩展已经变化，请移除引用后重新选择。')


def _open_path(path: Path) -> int:
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(path.parts) - 2:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_uid, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
