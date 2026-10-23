"""Connection generations fence canceled logins and stale credential refreshes."""
from __future__ import annotations

from eidos_runtime.db.database import Repository
from eidos_runtime.db.events import append_event
from eidos_runtime.models.mcp_connections import StoredMcpConnection
from eidos_runtime.runtime.state_machine import EventType


class McpConnectionRepository(Repository):
    def for_plugin(self, plugin_id: str) -> tuple[StoredMcpConnection, ...]:
        with self.lock:
            rows = self._connection().execute("SELECT * FROM mcp_connections WHERE plugin_id=? LIMIT 256", (plugin_id,)).fetchall()
        return tuple(StoredMcpConnection.model_validate(dict(row)) for row in rows)

    def get(self, plugin_id: str, server_id: str) -> StoredMcpConnection | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM mcp_connections WHERE plugin_id=? AND server_id=?", (plugin_id, server_id),
            ).fetchone()
        return StoredMcpConnection.model_validate(dict(row)) if row else None

    def save(self, record: StoredMcpConnection, *, expected_generation: str | None = None) -> bool:
        with self.lock, self._connection() as connection:
            values = record.to_internal_dict()
            if expected_generation is not None:
                cursor = connection.execute(
                    "UPDATE mcp_connections SET config_hash=:config_hash,generation=:generation,status=:status,"
                    "credential_ref=:credential_ref,account_label=:account_label,expires_at=:expires_at,"
                    "error_code=:error_code,updated_at=:updated_at "
                    "WHERE plugin_id=:plugin_id AND server_id=:server_id AND generation=:expected_generation",
                    {**values, "expected_generation": expected_generation},
                )
                if cursor.rowcount != 1:
                    return False
            else:
                connection.execute(
                    "INSERT INTO mcp_connections VALUES (:plugin_id,:server_id,:config_hash,:generation,:status,"
                    ":credential_ref,:account_label,:expires_at,:error_code,:updated_at) "
                    "ON CONFLICT(plugin_id,server_id) DO UPDATE SET config_hash=excluded.config_hash,"
                    "generation=excluded.generation,status=excluded.status,credential_ref=excluded.credential_ref,"
                    "account_label=excluded.account_label,expires_at=excluded.expires_at,"
                    "error_code=excluded.error_code,updated_at=excluded.updated_at", values,
                )
            append_event(connection, EventType.MCP_CONNECTION_CHANGED, record.updated_at, {
                "pluginId": record.plugin_id, "serverId": record.server_id,
                "status": record.status, "updatedAt": record.updated_at,
            })
        return True

    def recover(self) -> None:
        with self.lock:
            rows = self._connection().execute("SELECT * FROM mcp_connections WHERE status='connecting'").fetchall()
        for row in rows:
            current = StoredMcpConnection.model_validate(dict(row))
            self.save(current.model_copy(update={"status": "disconnected", "error_code": "mcp_login_interrupted"}),
                      expected_generation=current.generation)

    def retained(self, identifier: str) -> bool:
        with self.lock:
            return self._connection().execute(
                "SELECT 1 FROM mcp_connections WHERE credential_ref=? LIMIT 1", (identifier,),
            ).fetchone() is not None
