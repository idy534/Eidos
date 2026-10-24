"""Bounded, disposable JSONL view of the authoritative conversation items."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import uuid

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.plugins import _private_directory
from eidos_runtime.persistence.hooks import HookRepository
from eidos_runtime.sandbox.sensitive import SensitiveScanner


def write_transcript(store: SessionStore, session_id: str, sensitive: SensitiveScanner) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", session_id):
        raise ValueError("hook_transcript_session_invalid")
    assert store.data_directory is not None
    root = store.data_directory / "hook-transcripts"
    _private_directory(root)
    directory = root / session_id
    _private_directory(directory)
    path = directory / "transcript.jsonl"
    temporary = directory / (".transcript-" + uuid.uuid4().hex)
    items = HookRepository(store.database).transcript_items(session_id)
    truncated = len(items) > 1024
    size = 0
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            for item in items[-1024:]:
                truncated = truncated or item.truncated
                if item.kind in {"user_message", "assistant_message"}:
                    role = "user" if item.kind == "user_message" else "assistant"
                    payloads = [{"type": "message", "role": role, "content": [{"type": "input_text" if role == "user" else "output_text", "text": item.content}]}]
                elif item.tool_name:
                    payloads = [{"type": "function_call", "name": item.tool_name, "call_id": item.call_id, "arguments": item.arguments or "{}"},
                        {"type": "function_call_output", "call_id": item.call_id, "output": item.output or ""}]
                else:
                    continue
                for payload in payloads:
                    line = json.dumps({"type": "response_item", "timestamp": item.created_at, "payload": payload}, ensure_ascii=False)
                    encoded = (sensitive.redact_for_presentation(line).text + "\n").encode("utf-8")
                    if size + len(encoded) > 8 * 1024 * 1024 - 256:
                        truncated = True
                        break
                    output.write(encoded)
                    size += len(encoded)
            output.write((json.dumps({"type": "transcript_metadata", "format": "eidos-hook-v1", "truncated": truncated}) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def discard_transcript(store: SessionStore, session_id: str) -> None:
    if store.data_directory is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", session_id):
        return
    root = store.data_directory / "hook-transcripts"
    directory = root / session_id
    if root.is_symlink() or directory.is_symlink():
        return
    try:
        (directory / "transcript.jsonl").unlink(missing_ok=True)
        directory.rmdir()
    except OSError:
        # Disposable projections never prevent session deletion or shutdown.
        pass
