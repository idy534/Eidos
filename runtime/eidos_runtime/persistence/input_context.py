from __future__ import annotations

import json

from eidos_runtime.db.errors import StorageError

from eidos_runtime.db.database import Database, now_ms
from eidos_runtime.domain.input_reference import InputDraft, InputSnapshot, InputReference


class InputContextRepository:
    def __init__(self, database: Database):
        self.database = database

    def save(self, snapshot: InputSnapshot) -> None:
        with self.database.lock, self.database.json_blobs.lock:
            payload = snapshot.model_dump_json()
            with self.database.transaction() as connection:
                connection.execute(
                    "DELETE FROM input_references WHERE created_at < ? "
                    "AND id NOT IN (SELECT json_extract(value, '$.id') FROM items, json_each(items.input_references_json)) "
                    "AND id NOT IN (SELECT value FROM input_drafts, json_each(input_drafts.reference_ids_json))",
                    (now_ms() - 7 * 24 * 60 * 60 * 1000,),
                )
                total = connection.execute("SELECT COALESCE(SUM(byte_size), 0) FROM input_references WHERE id != ?", (snapshot.reference.id,)).fetchone()[0]
                if total + len(payload.encode()) > 512 * 1024 * 1024:
                    raise ValueError("input_storage_limit")
            stored = self.database.json_blobs.put_json('input-reference', payload)
            with self.database.transaction() as connection:
                connection.execute(
                    'INSERT OR IGNORE INTO input_references (id, snapshot_json, reference_json, byte_size, created_at) VALUES (?, ?, ?, ?, ?)',
                    (snapshot.reference.id, stored, snapshot.reference.model_dump_json(), len(payload.encode()), now_ms()),
                )

    def read(self, identifier: str) -> InputSnapshot:
        with self.database.lock:
            row = self.database.connection().execute(
                'SELECT snapshot_json, reference_json FROM input_references WHERE id = ?', (identifier,),
            ).fetchone()
            if row is None:
                raise ValueError('reference_not_found')
            snapshot = InputSnapshot.model_validate_json(self.database.json_blobs.read_json(
                row['snapshot_json'], expected_kind='input-reference',
            ))
            if snapshot.reference.id != identifier or snapshot.reference != InputReference.model_validate_json(row["reference_json"]):
                raise StorageError('input_reference_corrupt')
            return snapshot

    def reference(self, identifier: str) -> InputReference:
        with self.database.lock:
            row = self.database.connection().execute('SELECT reference_json FROM input_references WHERE id = ?', (identifier,)).fetchone()
            if row is None:
                raise ValueError('reference_not_found')
            return InputReference.model_validate_json(row[0])

    def history_excerpt(self, source: str, item_ids: list[str]) -> tuple[str, str]:
        with self.database.lock:
            connection = self.database.connection()
            session = connection.execute('SELECT title FROM sessions WHERE id = ?', (source,)).fetchone()
            if session is None:
                raise ValueError('session_not_found')
            item_filter = ''
            parameters = [source]
            if item_ids:
                item_filter = ' AND id IN (' + ','.join('?' for _ in item_ids) + ')'
                parameters.extend(item_ids)
            rows = connection.execute(
                "SELECT id, kind, substr(content, 1, 12000) AS content, length(content) AS length FROM items WHERE session_id = ? "
                "AND kind IN ('user_message', 'assistant_message') AND content IS NOT NULL "
                + item_filter + " ORDER BY created_at DESC, creation_seq DESC LIMIT 20", parameters,
            ).fetchall()
        if item_ids and len(rows) != len(set(item_ids)):
            raise ValueError('history_selection_unavailable')
        if not rows:
            raise ValueError('history_empty')
        text = '\n\n'.join(
            f"[{row['kind']} / {row['id']}]\n{row['content']}" + ('\n[消息摘录已截断]' if row['length'] > 12000 else '')
            for row in reversed(rows)
        )
        if len(text) > 65536:
            text = text[:65500] + '\n[历史摘录已截断]'
        return session['title'] or '历史对话', text

    def for_run(self, run_id: str) -> list[InputSnapshot]:
        with self.database.lock:
            row = self.database.connection().execute(
                "SELECT input_references_json FROM items WHERE run_id = ? AND kind = 'user_message' ORDER BY ordinal LIMIT 1",
                (run_id,),
            ).fetchone()
            return [self.read(value['id']) for value in json.loads(row[0])] if row else []

    def read_draft(self, key: str) -> InputDraft:
        with self.database.lock:
            row = self.database.connection().execute(
                'SELECT text, reference_ids_json FROM input_drafts WHERE key = ?', (key,),
            ).fetchone()
            if row is None:
                return InputDraft()
            return InputDraft(text=row['text'], references=tuple(
                self.reference(identifier) for identifier in json.loads(row['reference_ids_json'])
            ))

    def delete_draft(self, key: str) -> None:
        with self.database.transaction() as connection:
            connection.execute('DELETE FROM input_drafts WHERE key = ?', (key,))

    def write_draft(self, key: str, text: str, identifiers: list[str]) -> InputDraft:
        draft = InputDraft(text=text, references=tuple(self.reference(identifier) for identifier in dict.fromkeys(identifiers)))
        with self.database.transaction() as connection:
            if not text and not identifiers:
                connection.execute('DELETE FROM input_drafts WHERE key = ?', (key,))
            else:
                connection.execute(
                    'INSERT INTO input_drafts (key, text, reference_ids_json, updated_at) VALUES (?, ?, ?, ?) '
                    'ON CONFLICT(key) DO UPDATE SET text = excluded.text, reference_ids_json = excluded.reference_ids_json, updated_at = excluded.updated_at',
                    (key, text, json.dumps(list(dict.fromkeys(identifiers))), now_ms()),
                )
        return draft
