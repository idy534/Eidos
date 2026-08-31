from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import threading
from typing import ClassVar, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from eidos_runtime.db.repositories.execution import (
    ToolOutputPage,
    ToolOutputReadError,
)
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.sandbox.sensitive import SensitiveScanError, default_scanner
from eidos_runtime.tools.contracts import StrictToolModel, result_model
from eidos_runtime.tools.registry import (
    ToolProvenance,
    ToolRegistryEntry,
    ToolSpec,
)


READ_TOOL_OUTPUT_MIN_BYTES = 4
READ_TOOL_OUTPUT_MAX_BYTES = 16 * 1024
# Generic model projection escapes control characters. Keep the actual page
# below the projection budget even when every byte needs a JSON escape.
READ_TOOL_OUTPUT_MODEL_PAGE_BYTES = 4 * 1024
_JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991


class ReadToolOutputInput(StrictToolModel):
    callId: StrictStr = Field(min_length=1, max_length=256)
    stream: Literal["stdout", "stderr"] = "stdout"
    offsetBytes: StrictInt = Field(
        default=0, ge=0, le=_JSON_SAFE_INTEGER_MAX
    )
    maxBytes: StrictInt = Field(
        default=12 * 1024,
        ge=READ_TOOL_OUTPUT_MIN_BYTES,
        le=READ_TOOL_OUTPUT_MAX_BYTES,
    )
    fromEnd: bool = False

    @model_validator(mode="after")
    def validate_tail_offset(self) -> ReadToolOutputInput:
        if self.fromEnd and self.offsetBytes:
            raise ValueError("tail_read_cannot_set_offset")
        return self


class ReadToolOutputResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "callId", "stream", "content", "startByte", "endByte",
        "nextOffset", "totalBytes", "hasMoreBefore", "hasMoreAfter",
    )
    callId: StrictStr | None = None
    stream: Literal["stdout", "stderr"] | None = None
    content: StrictStr | None = None
    startByte: StrictInt | None = Field(default=None, ge=0)
    endByte: StrictInt | None = Field(default=None, ge=0)
    nextOffset: StrictInt | None = Field(default=None, ge=0)
    totalBytes: StrictInt | None = Field(default=None, ge=0)
    hasMoreBefore: bool | None = None
    hasMoreAfter: bool | None = None
    rawTruncated: bool | None = None
    rawOmittedBytes: StrictInt | None = Field(
        default=None, ge=0, le=_JSON_SAFE_INTEGER_MAX
    )

    @model_validator(mode="after")
    def validate_range(self) -> ReadToolOutputResultData:
        if any(
            value is None
            for value in (
                self.callId,
                self.stream,
                self.content,
                self.startByte,
                self.endByte,
                self.nextOffset,
                self.totalBytes,
                self.hasMoreBefore,
                self.hasMoreAfter,
            )
        ):
            return self
        assert self.startByte is not None
        assert self.endByte is not None
        assert self.nextOffset is not None
        assert self.totalBytes is not None
        assert self.hasMoreBefore is not None
        assert self.hasMoreAfter is not None
        if self.startByte > self.endByte or self.endByte > self.totalBytes:
            raise ValueError("invalid_output_range")
        if self.nextOffset != self.endByte:
            raise ValueError("invalid_next_output_offset")
        if self.hasMoreBefore != (self.startByte > 0):
            raise ValueError("invalid_output_before_marker")
        if self.hasMoreAfter != (self.endByte < self.totalBytes):
            raise ValueError("invalid_output_after_marker")
        if self.hasMoreAfter and self.nextOffset <= self.startByte:
            raise ValueError("non_advancing_output_offset")
        return self


@dataclass(frozen=True)
class ReadToolOutputAdapter:
    store: SessionStore
    run_id: str

    def execute(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        if cancel.is_set():
            return _error("tool_canceled", "Output reading was canceled")
        try:
            request = ReadToolOutputInput.model_validate(arguments)
            page = self.store.read_tool_output(
                self.run_id,
                tool_call_id=request.callId,
                stream=request.stream,
                offset_bytes=request.offsetBytes,
                max_bytes=min(request.maxBytes, READ_TOOL_OUTPUT_MODEL_PAGE_BYTES),
                from_end=request.fromEnd,
            )
        except ToolOutputReadError as error:
            return _error(error.code, _summary_for_code(error.code))
        except (TypeError, ValueError):
            return _error("invalid_arguments", "Invalid output read arguments")
        except Exception:
            return _error(
                "tool_output_unavailable",
                "The persisted shell output is unavailable",
            )
        if cancel.is_set():
            return _error("tool_canceled", "Output reading was canceled")
        result = _page_result(page)
        scan_result = _page_result(page, content=page.source_content)
        try:
            scanned = default_scanner().scan_json(scan_result)
        except SensitiveScanError:
            return _error(
                "sensitive_content_rejected",
                "Sensitive output was withheld",
            )
        if scanned != scan_result or not isinstance(scanned, dict):
            return _error(
                "sensitive_content_rejected",
                "Sensitive output was withheld",
            )
        return result


def read_tool_output_entry(
    store: SessionStore, run_id: str
) -> ToolRegistryEntry:
    input_schema = ReadToolOutputInput.model_json_schema(by_alias=True)
    result_schema = result_model(
        ReadToolOutputResultData
    ).model_json_schema(by_alias=True)
    encoded = json.dumps(
        (input_schema, result_schema),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    spec = ToolSpec.model_validate({
        "name": "read_tool_output",
        "description": (
            "Read a bounded stdout or stderr page from a completed or failed "
            "run_shell call in the current Session. Use the prior model callId. "
            "Set fromEnd=true for the tail; raw bytes omitted by the Shell output "
            "limit are not recoverable."
        ),
        "sideEffect": "none",
        "approvalRequired": False,
        "timeoutSeconds": 5,
        "batchPolicy": "parallel",
        "visibility": "direct",
        "inputSchema": input_schema,
        "resultSchema": result_schema,
        "modelProjectionPolicy": "generic",
        "contractVersion": 1,
    })
    return ToolRegistryEntry(
        spec=spec,
        provenance=ToolProvenance.model_validate({
            "kind": "builtin",
            "sourceId": "eidos.read-tool-output",
            "sourceVersion": "1",
            "contentHash": hashlib.sha256(encoded).hexdigest(),
        }),
        adapter=ReadToolOutputAdapter(store, run_id),
        input_model=ReadToolOutputInput,
        result_data_model=ReadToolOutputResultData,
    )


def _page_result(
    page: ToolOutputPage, *, content: str | None = None
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": "read_tool_output",
        "outcome": "success",
        "code": "ok",
        "summary": "Persisted shell output page read",
        "data": {
            "callId": page.call_id,
            "stream": page.stream,
            "content": page.content if content is None else content,
            "startByte": page.start_byte,
            "endByte": page.end_byte,
            "nextOffset": page.end_byte,
            "totalBytes": page.total_bytes,
            "hasMoreBefore": page.has_more_before,
            "hasMoreAfter": page.has_more_after,
            "rawTruncated": page.raw_truncated,
            "rawOmittedBytes": page.raw_omitted_bytes,
        },
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }


def _error(code: str, summary: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": "read_tool_output",
        "outcome": "error",
        "code": code,
        "summary": summary,
        "data": {},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }


def _summary_for_code(code: str) -> str:
    if code == "ambiguous_tool_call":
        return "The shell callId is ambiguous"
    if code == "invalid_output_offset":
        return "The output offset is not at a UTF-8 character boundary"
    if code == "tool_output_not_available":
        return "The persisted shell output is unavailable"
    return "The persisted shell output could not be read"


__all__ = [
    "READ_TOOL_OUTPUT_MAX_BYTES",
    "READ_TOOL_OUTPUT_MODEL_PAGE_BYTES",
    "READ_TOOL_OUTPUT_MIN_BYTES",
    "ReadToolOutputAdapter",
    "ReadToolOutputInput",
    "ReadToolOutputResultData",
    "read_tool_output_entry",
]
