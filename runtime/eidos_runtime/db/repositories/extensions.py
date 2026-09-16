from __future__ import annotations

import json

from eidos_runtime.db.database import Repository, now_ms as _now_ms
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError
from eidos_runtime.db.events import append_event, event_from_row
from eidos_runtime.db.mappers import _load_json_object, _plugin_from_row
from eidos_runtime.runtime.state_machine import EventType

class ExtensionRepository(Repository):
    def plugin_record(self, plugin_id: str) -> dict[str, object] | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM plugins WHERE id = ?", (plugin_id,)
            ).fetchone()
        return _plugin_from_row(row) if row is not None else None

    def list_plugin_records(
        self, *, include_removed: bool = False
    ) -> list[dict[str, object]]:
        sql = "SELECT * FROM plugins"
        if not include_removed:
            sql += " WHERE status = 'installed'"
        sql += " ORDER BY id"
        with self.lock:
            rows = self._connection().execute(sql).fetchall()
        return [_plugin_from_row(row) for row in rows]

    def insert_plugin_record(self, record: dict[str, object]) -> dict[str, object]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO plugins (
                    id, name, version, description, manifest_json, content_hash,
                    enabled, status, installed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 'installed', ?, ?)
                """,
                (
                    record["id"], record["name"], record["version"],
                    record["description"], record["manifestJson"],
                    record["contentHash"], now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM plugins WHERE id = ?", (record["id"],)
            ).fetchone()
            append_event(
                connection,
                EventType.PLUGIN_IMPORTED,
                now,
                {"plugin": _plugin_from_row(row)},
            )
        result = self.plugin_record(str(record["id"]))
        assert result is not None
        return result

    def set_plugin_enabled(
        self, plugin_id: str, enabled: bool
    ) -> dict[str, object]:
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE plugins SET enabled = ?, updated_at = ?
                WHERE id = ? AND status = 'installed'
                """,
                (int(enabled), _now_ms(), plugin_id),
            )
            if updated.rowcount != 1:
                raise ResourceNotFoundError("plugin not found")
            row = connection.execute(
                "SELECT * FROM plugins WHERE id = ?", (plugin_id,)
            ).fetchone()
            append_event(
                connection,
                EventType.PLUGIN_STATE_CHANGED,
                _now_ms(),
                {"plugin": _plugin_from_row(row)},
            )
        result = self.plugin_record(plugin_id)
        assert result is not None
        return result

    def remove_plugin_record(self, plugin_id: str) -> dict[str, object]:
        current = self.plugin_record(plugin_id)
        if current is None:
            raise ResourceNotFoundError("plugin not found")
        if current["status"] == "removed":
            return current
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE plugins
                SET enabled = 0, status = 'removed', updated_at = ?
                WHERE id = ? AND status = 'installed'
                """,
                (_now_ms(), plugin_id),
            )
            row = connection.execute(
                "SELECT * FROM plugins WHERE id = ?", (plugin_id,)
            ).fetchone()
            append_event(
                connection,
                EventType.PLUGIN_STATE_CHANGED,
                _now_ms(),
                {"plugin": _plugin_from_row(row)},
            )
        result = self.plugin_record(plugin_id)
        assert result is not None
        return result

    def plugin_referenced_by_nonterminal_run(
        self, plugin_id: str, content_hash: str
    ) -> bool:
        terminal = {"succeeded", "failed", "stopped", "canceled", "interrupted"}
        with self.lock:
            rows = self._connection().execute(
                "SELECT status, extension_snapshot_json FROM runs"
            ).fetchall()
        for row in rows:
            if row["status"] in terminal:
                continue
            snapshot = _load_json_object(row["extension_snapshot_json"])
            plugins = snapshot.get("plugins") if snapshot else None
            if not isinstance(plugins, list):
                continue
            if any(
                isinstance(plugin, dict)
                and plugin.get("id") == plugin_id
                and plugin.get("contentHash") == content_hash
                for plugin in plugins
            ):
                return True
        return False

    def list_manual_mcp_servers(self) -> list[dict[str, object]]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT * FROM manual_mcp_servers ORDER BY server_id"
            ).fetchall()
        return [_manual_mcp_from_row(row) for row in rows]

    def manual_mcp_server(self, server_id: str) -> dict[str, object] | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM manual_mcp_servers WHERE server_id = ?",
                (server_id,),
            ).fetchone()
        return _manual_mcp_from_row(row) if row is not None else None

    def manual_mcp_server_config(self, server_id: str) -> dict[str, object]:
        record = self.manual_mcp_server(server_id)
        if record is None:
            raise ResourceNotFoundError("mcp server not found")
        try:
            values = json.loads(
                self.database.json_blobs.read_json(
                    str(record["envBlobRef"]), expected_kind="mcp-env"
                )
            )
        except (TypeError, json.JSONDecodeError, StorageError) as error:
            raise StorageError("manual_mcp_env_invalid") from error
        if not isinstance(values, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in values.items()
        ):
            raise StorageError("manual_mcp_env_invalid")
        return {**record, "env": values}

    def insert_manual_mcp_server(
        self, record: dict[str, object]
    ) -> dict[str, object]:
        now = _now_ms()
        env_blob_ref = record.get("envBlobRef")
        if env_blob_ref is None:
            env_blob_ref = self.database.json_blobs.put_json(
                "mcp-env",
                json.dumps(
                    record.get("env", {}),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO manual_mcp_servers (
                    server_id, executable, argv_json, env_blob_ref, env_names_json,
                    cwd, permission_profile, startup_timeout_seconds,
                    tool_timeout_seconds, content_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["serverId"],
                    record["executable"],
                    json.dumps(record["argv"], ensure_ascii=False, separators=(",", ":")),
                    env_blob_ref,
                    json.dumps(record["envNames"], ensure_ascii=False, separators=(",", ":")),
                    record["cwd"],
                    record["permissionProfile"],
                    record["startupTimeoutSeconds"],
                    record["toolTimeoutSeconds"],
                    record["contentHash"],
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO mcp_server_states (
                    plugin_id, server_id, consented, error_code, updated_at
                ) VALUES ('manual', ?, 0, NULL, ?)
                """,
                (record["serverId"], now),
            )
            append_event(
                connection,
                EventType.MCP_SERVER_STATE_CHANGED,
                now,
                {"server": _manual_mcp_projection(record, now)},
            )
        result = self.manual_mcp_server(str(record["serverId"]))
        assert result is not None
        return result

    def mcp_server_state(
        self, plugin_id: str, server_id: str
    ) -> dict[str, object]:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT consented, error_code, updated_at
                FROM mcp_server_states WHERE plugin_id = ? AND server_id = ?
                """,
                (plugin_id, server_id),
            ).fetchone()
        return {
            "consented": bool(row["consented"]) if row is not None else False,
            "errorCode": row["error_code"] if row is not None else None,
            "updatedAt": row["updated_at"] if row is not None else 0,
        }

    def set_mcp_server_state(
        self,
        server: dict[str, object],
        *,
        consented: bool,
        error_code: str | None = None,
    ) -> dict[str, object]:
        now = _now_ms()
        projection = {
            **server,
            "consented": consented,
            "available": bool(server["declaredEnabled"]) and consented and error_code is None,
            "errorCode": error_code,
            "updatedAt": now,
        }
        if error_code is None:
            projection.pop("errorCode")
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO mcp_server_states (
                    plugin_id, server_id, consented, error_code, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(plugin_id, server_id) DO UPDATE SET
                    consented = excluded.consented,
                    error_code = excluded.error_code,
                    updated_at = excluded.updated_at
                """,
                (
                    server["pluginId"], server["serverId"], int(consented),
                    error_code, now,
                ),
            )
            append_event(
                connection,
                EventType.MCP_SERVER_STATE_CHANGED,
                now,
                {"server": projection},
            )
        return projection

    def activated_tools(self, run_id: str) -> tuple[str, ...]:
        with self.lock:
            row = self._connection().execute(
                "SELECT activated_tools_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        try:
            values = json.loads(row["activated_tools_json"])
        except (TypeError, json.JSONDecodeError):
            raise StorageError("activated_tools_invalid") from None
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise StorageError("activated_tools_invalid")
        return tuple(values)

    def activate_tools(self, run_id: str, names: tuple[str, ...]) -> tuple[str, ...]:
        current = set(self.activated_tools(run_id))
        current.update(names)
        ordered = tuple(sorted(current, key=lambda value: value.encode("utf-8")))[:32]
        encoded = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                "UPDATE runs SET activated_tools_json = ?, updated_at = ? WHERE id = ?",
                (encoded, _now_ms(), run_id),
            )
            if updated.rowcount != 1:
                raise ResourceNotFoundError("run not found")
        return ordered

    def record_mcp_tool_list_changed(self, plugin_id: str, server_id: str) -> None:
        with self.lock, self._connection() as connection:
            append_event(
                connection,
                EventType.MCP_TOOL_LIST_CHANGED,
                _now_ms(),
                {"plugin_id": plugin_id, "server_id": server_id},
            )

    def extension_event_waterline(self) -> int:
        with self.lock:
            row = self._connection().execute(
                "SELECT COALESCE(MAX(id), 0) FROM events WHERE session_id IS NULL"
            ).fetchone()
        return int(row[0])

    def list_extension_events(
        self, *, after_event_id: int = 0, limit: int = 200
    ) -> dict[str, object]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT * FROM events
                WHERE session_id IS NULL AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (after_event_id, limit + 1),
            ).fetchall()
            waterline = self.extension_event_waterline()
        events = [event_from_row(row) for row in rows[:limit]]
        return {
            "items": [event for event in events if event is not None],
            "hasMore": len(rows) > limit,
            "throughEventId": waterline,
        }


def _manual_mcp_from_row(row: object) -> dict[str, object]:
    if row is None:
        raise StorageError("manual_mcp_server_invalid")
    try:
        argv = json.loads(row["argv_json"])
        env_names = json.loads(row["env_names_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise StorageError("manual_mcp_server_invalid") from error
    if (
        not isinstance(argv, list)
        or not all(isinstance(value, str) for value in argv)
        or not isinstance(env_names, list)
        or not all(isinstance(value, str) for value in env_names)
    ):
        raise StorageError("manual_mcp_server_invalid")
    return {
        "serverId": row["server_id"],
        "executable": row["executable"],
        "argv": argv,
        "envBlobRef": row["env_blob_ref"],
        "envNames": env_names,
        "cwd": row["cwd"],
        "permissionProfile": row["permission_profile"],
        "startupTimeoutSeconds": row["startup_timeout_seconds"],
        "toolTimeoutSeconds": row["tool_timeout_seconds"],
        "contentHash": row["content_hash"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _manual_mcp_projection(
    record: dict[str, object], updated_at: int, *, consented: bool = False,
    error_code: str | None = None,
) -> dict[str, object]:
    projection = {
        "schemaVersion": 1,
        "pluginId": "manual",
        "pluginVersion": "manual",
        "pluginHash": record["contentHash"],
        "serverId": record["serverId"],
        "executable": record["executable"],
        "argv": record["argv"],
        "envNames": record["envNames"],
        "cwd": record["cwd"],
        "permissionProfile": record["permissionProfile"],
        "startupTimeoutSeconds": record["startupTimeoutSeconds"],
        "toolTimeoutSeconds": record["toolTimeoutSeconds"],
        "declaredEnabled": True,
        "consented": consented,
        "available": consented and error_code is None,
        "updatedAt": updated_at,
    }
    if error_code is not None:
        projection["errorCode"] = error_code
    return projection
