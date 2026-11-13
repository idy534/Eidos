"""Files a Run has read or patched, used to load subdirectory project rules.

Only the structured path arguments of file tools count. Paths inside Shell
commands are not parsed. The resolver still checks every rule candidate
against the workspace boundary, so these paths only widen which directories
it looks at.
"""

from __future__ import annotations

import json

from eidos_runtime.db.database import Repository

_READ_TOOLS = ("read_file", "read_file_range")
_SCAN_LIMIT = 2_000


class RulePathRepository(Repository):
    def touched_paths(self, run_id: str) -> tuple[str, ...]:
        """Paths in first-touch order, without duplicates."""
        with self.lock:
            rows = self._connection().execute(
                """SELECT t.tool_name,t.arguments_json,t.result_json
                   FROM tool_calls t JOIN items i ON i.id=t.item_id
                   WHERE i.run_id=? AND t.status='completed'
                     AND t.tool_name IN ('read_file','read_file_range','apply_patch')
                   ORDER BY t.creation_seq LIMIT ?""",
                (run_id, _SCAN_LIMIT),
            ).fetchall()
        paths: dict[str, None] = {}
        for row in rows:
            for path in _paths(row["tool_name"], row["arguments_json"], row["result_json"]):
                paths.setdefault(path, None)
        return tuple(paths)


def _paths(tool_name: str, arguments_json: str | None, result_json: str | None) -> list[str]:
    try:
        if tool_name in _READ_TOOLS:
            path = json.loads(arguments_json or "{}").get("path")
            return [path] if isinstance(path, str) else []
        result = json.loads(result_json or "{}")
        if result.get("outcome") != "success":
            return []
        changes = (result.get("data") or {}).get("changes") or []
        return [
            value
            for change in changes
            if isinstance(change, dict)
            for value in (change.get("path"), change.get("newPath"))
            if isinstance(value, str)
        ]
    except (ValueError, AttributeError):
        return []
