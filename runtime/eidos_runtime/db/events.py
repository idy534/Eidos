from __future__ import annotations

import json
import sqlite3

from pydantic import Field, ValidationError

from eidos_runtime.protocol.schemas import (
    ClosedModel,
    EventEnvelopeDto,
    PluginRecordDto,
    McpServerRecordDto,
    RunDto,
    SessionDto,
)
from eidos_runtime.sandbox.sensitive import default_scanner
from eidos_runtime.runtime.state_machine import EventType, RunStatus


EVENT_CONTRACT_VERSION = 1


class SessionCreatedPayload(ClosedModel):
    session: SessionDto


class SessionTitleUpdatedPayload(ClosedModel):
    title: str


class RunCreatedPayload(ClosedModel):
    run: RunDto


class RunStatusChangedPayload(ClosedModel):
    previous: RunStatus
    current: RunStatus
    reason: str | None = None


class EntityStatusChangedPayload(ClosedModel):
    entity_id: str
    previous: str
    current: str
    reason: str | None = None


class ToolCallEventPayload(ClosedModel):
    tool_call_id: str
    code: str | None = None


class ReconciliationEventPayload(ClosedModel):
    epoch: int
    reason: str


class PluginEventPayload(ClosedModel):
    plugin: PluginRecordDto


class McpServerEventPayload(ClosedModel):
    server: McpServerRecordDto


class McpToolListChangedPayload(ClosedModel):
    plugin_id: str = Field(alias="pluginId")
    server_id: str = Field(alias="serverId")


EVENT_PAYLOADS: dict[EventType, type[ClosedModel]] = {
    EventType.SESSION_CREATED: SessionCreatedPayload,
    EventType.SESSION_TITLE_UPDATED: SessionTitleUpdatedPayload,
    EventType.RUN_CREATED: RunCreatedPayload,
    EventType.RUN_STATUS_CHANGED: RunStatusChangedPayload,
    EventType.SEGMENT_CREATED: EntityStatusChangedPayload,
    EventType.STEP_STATUS_CHANGED: EntityStatusChangedPayload,
    EventType.APPROVAL_STATUS_CHANGED: EntityStatusChangedPayload,
    EventType.TOOL_CALL_STARTED: ToolCallEventPayload,
    EventType.TOOL_CALL_COMPLETED: ToolCallEventPayload,
    EventType.RECONCILIATION_REQUIRED: ReconciliationEventPayload,
    EventType.RECONCILIATION_CLEARED: ReconciliationEventPayload,
    EventType.PLUGIN_IMPORTED: PluginEventPayload,
    EventType.PLUGIN_STATE_CHANGED: PluginEventPayload,
    EventType.MCP_SERVER_STATE_CHANGED: McpServerEventPayload,
    EventType.MCP_TOOL_LIST_CHANGED: McpToolListChangedPayload,
}


class IncompatibleEventError(ValueError):
    pass


def append_event(
    connection: sqlite3.Connection,
    event_type: EventType,
    occurred_at: int,
    payload: dict[str, object],
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    validated = EVENT_PAYLOADS[event_type].model_validate(payload).to_json_value()
    scanned = default_scanner().scan_json(validated)
    assert isinstance(scanned, dict)
    validated = EVENT_PAYLOADS[event_type].model_validate_json(
        json.dumps(scanned, ensure_ascii=False, separators=(",", ":"))
    ).to_json_value()
    cursor = connection.execute(
        """
        INSERT INTO events (
            event_contract_version, event_type, occurred_at,
            session_id, run_id, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            EVENT_CONTRACT_VERSION,
            event_type.value,
            occurred_at,
            session_id,
            run_id,
            json.dumps(validated, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        ),
    )
    return EventEnvelopeDto.model_validate({
        "eventContractVersion": EVENT_CONTRACT_VERSION,
        "eventId": cursor.lastrowid,
        "eventType": event_type.value,
        "occurredAt": occurred_at,
        "sessionId": session_id,
        "runId": run_id,
        "payload": validated,
    }).to_json_value()


def event_from_row(row: sqlite3.Row) -> dict[str, object] | None:
    if row["event_contract_version"] != EVENT_CONTRACT_VERSION:
        raise IncompatibleEventError("event contract version is incompatible")
    try:
        event_type = EventType(row["event_type"])
    except ValueError:
        return None
    try:
        payload = EVENT_PAYLOADS[event_type].model_validate_json(row["payload_json"])
    except ValidationError as error:
        raise IncompatibleEventError("event payload is incompatible") from error
    return EventEnvelopeDto.model_validate({
        "eventContractVersion": EVENT_CONTRACT_VERSION,
        "eventId": row["id"],
        "eventType": event_type.value,
        "occurredAt": row["occurred_at"],
        "sessionId": row["session_id"],
        "runId": row["run_id"],
        "payload": payload.to_json_value(),
    }).to_json_value()
