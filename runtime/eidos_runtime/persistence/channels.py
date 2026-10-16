from __future__ import annotations

from collections.abc import Callable
import sqlite3
from uuid import NAMESPACE_URL, uuid4, uuid5

from eidos_runtime.db.database import Repository, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.channels import (
    ChannelConnection, ChannelDelivery, ChannelError, ChannelInbound, ChannelMessage, ChannelOrigin,
)
from eidos_runtime.runtime.state_machine import EventType


def stable_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, "eidos/channel/" + value))


class ChannelRepository(Repository):
    def changed(self, connection: sqlite3.Connection) -> None:
        append_event(connection, EventType.CHANNEL_CHANGED, now_ms(), {"id": "dingtalk"})

    def connection(self) -> ChannelConnection | None:
        with self.lock:
            row = self._connection().execute("SELECT connection_json FROM channel_connections LIMIT 1").fetchone()
        return ChannelConnection.model_validate_json(row[0]) if row else None

    def save(self, value: ChannelConnection, expected: int) -> None:
        with self.database.transaction() as connection:
            old = self.connection()
            if (old.revision if old else 0) != expected:
                raise ChannelError("渠道配置已经改变，请刷新后重试。")
            if value.revision != expected + 1 or (old is not None and old.id != value.id):
                raise ChannelError("渠道配置的身份或版本无效。")
            connection.execute("INSERT INTO channel_connections VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,connection_json=excluded.connection_json",
                               (value.id, value.revision, value.model_dump_json()))
            self.changed(connection)

    def receive(self, snapshot: ChannelConnection, message: ChannelMessage) -> str:
        identifier = stable_id(snapshot.id + "/" + snapshot.config.client_id + "/" + message.msg_id)
        with self.database.transaction() as connection:
            self.require_current(snapshot)
            inserted = connection.execute("INSERT OR IGNORE INTO channel_inbound(id,snapshot_json,message_json,status) VALUES(?,?,?,'pending')",
                               (identifier, snapshot.model_dump_json(), message.model_dump_json()))
            if inserted.rowcount:
                self.changed(connection)
        return identifier

    def require_current(self, snapshot: ChannelConnection) -> None:
        current = self.connection()
        if current is None or not current.config.enabled or current != snapshot:
            raise ChannelError("渠道已经停用或配置已经改变。")

    def inbound(self, identifier: str) -> ChannelInbound:
        with self.lock:
            row = self._connection().execute("SELECT * FROM channel_inbound WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ChannelError("渠道消息已不可用。")
        return ChannelInbound(id=row["id"], connection=ChannelConnection.model_validate_json(row["snapshot_json"]),
                              message=ChannelMessage.model_validate_json(row["message_json"]), status=row["status"],
                              session_id=row["session_id"], run_id=row["run_id"])

    def pending(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self._connection().execute("SELECT id FROM channel_inbound WHERE status='pending' ORDER BY sequence LIMIT 20")]

    def session(self, route: str) -> str | None:
        with self.lock:
            row = self._connection().execute("SELECT session_id FROM channel_sessions WHERE route=?", (route,)).fetchone()
        return row[0] if row else None

    def bind(self, route: str, session_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO channel_sessions VALUES(?,?)", (route, session_id))
            self.changed(connection)

    def project_root(self, project_id: str) -> str:
        with self.lock:
            row = self._connection().execute("SELECT workspace_root FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ChannelError("渠道绑定的项目已经不存在。")
        return row[0]

    def accept(self, identifier: str, session_id: str,
               write: Callable[[sqlite3.Connection], str]) -> str:
        with self.database.transaction() as connection:
            incoming = self.inbound(identifier)
            self.require_current(incoming.connection)
            if incoming.status != "pending":
                raise ChannelError("这条渠道消息已经处理。")
            run_id = write(connection)
            origin = ChannelOrigin(message_id=identifier, sender_id=incoming.message.sender_id,
                                   conversation_id=incoming.message.conversation_id)
            connection.execute("INSERT INTO channel_run_origins VALUES(?,?)", (run_id, origin.model_dump_json()))
            connection.execute("UPDATE channel_inbound SET status='submitted',session_id=?,run_id=? WHERE id=?",
                               (session_id, run_id, identifier))
            self.changed(connection)
            return run_id

    def reject(self, identifier: str, text: str) -> None:
        with self.database.transaction() as connection:
            changed = connection.execute("UPDATE channel_inbound SET status='rejected' WHERE id=? AND status='pending'", (identifier,)).rowcount
            if changed:
                self._reply(connection, identifier, "final", text)

    def origin(self, run_id: str) -> ChannelOrigin | None:
        with self.lock:
            row = self._connection().execute("SELECT origin_json FROM channel_run_origins WHERE run_id=?", (run_id,)).fetchone()
        return ChannelOrigin.model_validate_json(row[0]) if row else None

    def origins_for_runs(self, run_ids: list[str]) -> dict[str, ChannelOrigin]:
        if not run_ids:
            return {}
        with self.lock:
            rows = self._connection().execute("SELECT run_id,origin_json FROM channel_run_origins WHERE run_id IN (" + ",".join("?" for _ in run_ids) + ")", run_ids).fetchall()
        return {row[0]: ChannelOrigin.model_validate_json(row[1]) for row in rows}

    def _reply(self, connection: sqlite3.Connection, identifier: str, kind: str, text: str) -> None:
        # Bound the full answer and split by UTF-8 bytes, below DingTalk message limits.
        raw = text.encode("utf-8")
        if len(raw) > 24_000:
            raw = raw[:24_000].decode("utf-8", errors="ignore").encode() + "\n内容较长，请在 Eidos 查看完整回答。".encode()
        parts = []
        while raw:
            part = raw[:6000].decode("utf-8", errors="ignore")
            parts.append(part)
            raw = raw[len(part.encode()):]
        before = connection.total_changes
        for index, part in enumerate(parts):
            connection.execute("INSERT OR IGNORE INTO channel_deliveries(id,inbound_id,kind,text,status,created_at) VALUES(?,?,?,?,'pending',?)",
                               (str(uuid4()), identifier, f"{kind}:{index}", part, now_ms()))
        if connection.total_changes != before:
            self.changed(connection)

    def collect_replies(self) -> None:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT c.id,r.status,r.reconciliation_required,r.id AS run_id FROM channel_inbound c JOIN runs r ON r.id=c.run_id WHERE c.status='submitted' AND r.status IN ('waiting_approval','waiting_input','succeeded','failed','stopped','canceled','interrupted') AND NOT EXISTS(SELECT 1 FROM channel_deliveries d WHERE d.inbound_id=c.id AND (d.kind='final:0' OR (r.status='waiting_approval' AND d.kind='approval:0') OR (r.status='waiting_input' AND d.kind='input:0'))) ORDER BY c.sequence LIMIT 100").fetchall()
            for row in rows:
                status = row["status"]
                if status == "waiting_approval":
                    self._reply(connection, row["id"], "approval", "任务需要在本机 Eidos 确认后继续。")
                elif status == "waiting_input":
                    self._reply(connection, row["id"], "input", "任务需要补充信息，请在本机 Eidos 打开会话继续。")
                elif status in {"succeeded", "failed", "stopped", "canceled", "interrupted"}:
                    text = ""
                    if status == "succeeded":
                        item = connection.execute("SELECT content FROM items WHERE run_id=? AND kind='assistant_message' AND status='completed' AND incomplete=0 ORDER BY ordinal DESC LIMIT 1", (row["run_id"],)).fetchone()
                        text = item[0] if item and item[0] else "任务已完成，请在 Eidos 查看结果。"
                    else:
                        text = {"failed": "任务执行失败，请在 Eidos 查看原因。", "stopped": "任务已停止。", "canceled": "任务已取消。", "interrupted": "任务因本机中断而停止，请在 Eidos 查看。"}[status]
                    if row["reconciliation_required"]:
                        text += "\n部分操作结果需要在本机核验。"
                    self._reply(connection, row["id"], "final", text)

    @staticmethod
    def _delivery(row: sqlite3.Row) -> ChannelDelivery:
        return ChannelDelivery(**{name: row[name] for name in ChannelDelivery.model_fields})

    def deliveries(self) -> list[ChannelDelivery]:
        with self.lock:
            return [self._delivery(r) for r in self._connection().execute("SELECT * FROM channel_deliveries ORDER BY sequence DESC LIMIT 20")]

    def claim_delivery(self) -> ChannelDelivery | None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM channel_deliveries d WHERE status='pending' AND next_attempt<=? AND NOT EXISTS(SELECT 1 FROM channel_deliveries p WHERE p.inbound_id=d.inbound_id AND p.sequence<d.sequence AND p.status IN ('pending','sending')) ORDER BY sequence LIMIT 1", (now_ms(),)).fetchone()
            if row is None:
                return None
            connection.execute("UPDATE channel_deliveries SET status='sending',attempts=attempts+1 WHERE id=?", (row["id"],))
            self.changed(connection)
            return self._delivery(row).model_copy(update={"status": "sending"})

    def settle(self, identifier: str, status: str, reason: str | None = None, *, retry: bool = False) -> None:
        if status not in {"sent", "failed", "unknown"}:
            raise ValueError("invalid channel delivery status")
        with self.database.transaction() as connection:
            connection.execute("UPDATE channel_deliveries SET status=CASE WHEN ? AND attempts<5 THEN 'pending' ELSE ? END,reason=?,next_attempt=? WHERE id=? AND status='sending'",
                               (retry, status, reason, now_ms() + 60_000, identifier))
            current = connection.execute("SELECT inbound_id,sequence,kind,status FROM channel_deliveries WHERE id=?", (identifier,)).fetchone()
            if current and current["kind"].startswith("final:") and current["status"] in {"failed", "unknown"}:
                connection.execute("UPDATE channel_deliveries SET status='failed',reason='前一部分未确认发送，后续部分已停止。' WHERE inbound_id=? AND sequence>? AND kind LIKE 'final:%' AND status='pending'", (current["inbound_id"], current["sequence"]))
            self.changed(connection)

    def recover(self) -> None:
        with self.database.transaction() as connection:
            changed = connection.execute("UPDATE channel_deliveries SET status='unknown',reason='应用在发送期间退出，发送结果未知。' WHERE status='sending'").rowcount
            changed += connection.execute("UPDATE channel_deliveries AS d SET status='failed',reason='前一部分未确认发送，后续部分已停止。' WHERE d.status='pending' AND d.kind LIKE 'final:%' AND EXISTS(SELECT 1 FROM channel_deliveries p WHERE p.inbound_id=d.inbound_id AND p.sequence<d.sequence AND p.kind LIKE 'final:%' AND p.status IN ('failed','unknown'))").rowcount
            if changed:
                self.changed(connection)
