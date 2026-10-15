from __future__ import annotations

import json
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from eidos_runtime.db.database import canonical_hash
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.automation import Automation, AutomationRequest, AutomationResponse
from eidos_runtime.runtime.errors import tool_result
from eidos_runtime.runtime.state_machine import EventType
from eidos_runtime.tools.automation import AutomationData


def recover_automation_results(connection: sqlite3.Connection, now: int) -> None:
    """Only SQLite mutations owned by this builtin can be proved by receipts."""
    rows = connection.execute(
        "SELECT t.*,i.run_id,i.session_id,r.cancel_requested_at FROM tool_calls t "
        "JOIN items i ON i.id=t.item_id JOIN runs r ON r.id=i.run_id "
        "WHERE t.status='running' AND t.tool_name='automation' "
        "AND json_extract(t.provenance_json,'$.kind')='builtin' "
        "AND json_extract(t.provenance_json,'$.sourceId')='eidos.automation'",
    ).fetchall()
    for row in rows:
        try:
            command = AutomationRequest.model_validate_json(row["arguments_json"])
        except ValueError:
            continue
        wire = command.receipt_request()
        operation_id = str(uuid5(NAMESPACE_URL, "automation/tool/" + row["item_id"]))
        if command.mode == "create":
            root = row["run_id"]
            seen: set[str] = set()
            while root not in seen:
                seen.add(root)
                previous = connection.execute("SELECT source_run_id FROM run_revisions WHERE run_id=? AND revision_kind='regenerate'", (root,)).fetchone()
                if previous is None:
                    break
                root = previous[0]
            operation_id = str(uuid5(NAMESPACE_URL, "automation/create/" + root + "/" + json.dumps(wire, sort_keys=True, ensure_ascii=False)))
        receipt = connection.execute("SELECT * FROM operations WHERE id=? AND scope='automation/mutate'", (operation_id,)).fetchone()
        if receipt is not None and (receipt["status"] != "completed" or receipt["request_hash"] != canonical_hash(wire)):
            continue
        if receipt is not None:
            try:
                task = Automation.model_validate_json(receipt["result_json"])
            except ValueError:
                continue
            response = AutomationResponse(kind="mutation", automation=task, replayed=True)
            result = tool_result("automation", "success", "automation_recovered", "计划已保存，结果已恢复。",
                {"response": response.to_wire_dict()}, data_model=AutomationData,
                side_effects_may_exist=True, reconciliation_required=False)
        else:
            result = tool_result("automation", "error", "automation_interrupted", "执行被中断，计划没有修改。",
                data_model=AutomationData, side_effects_may_exist=False, reconciliation_required=False)
        status = "canceled" if row["cancel_requested_at"] is not None else "completed"
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        connection.execute("UPDATE tool_calls SET status=?,result_json=?,model_result_json=?,ui_result_json=?,completed_at=? WHERE id=?",
            (status, encoded, encoded, encoded, now, row["id"]))
        connection.execute("UPDATE items SET status=?,completed_at=? WHERE id=?", (status, now, row["item_id"]))
        connection.execute("UPDATE durable_intents SET status='completed',reconciled_at=? WHERE tool_call_id=?", (now, row["id"]))
        connection.execute("UPDATE tool_attempts SET status='completed',completed_at=? WHERE tool_call_id=? AND status IN ('running','uncertain')", (now, row["id"]))
        append_event(connection, EventType.ITEM_COMPLETED, now, {"item_id": row["item_id"]}, session_id=row["session_id"], run_id=row["run_id"])
