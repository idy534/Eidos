from __future__ import annotations

import sqlite3

from eidos_runtime.db.database import Database
from eidos_runtime.domain.agents import AgentCatalogSnapshot


class AgentSnapshotRepository:
    def __init__(self, database: Database):
        self.database = database

    def encode(self, snapshot: AgentCatalogSnapshot) -> str:
        return self.database.json_blobs.put_json(
            "agent-catalog", snapshot.model_dump_json()
        )

    def read(self, run_id: str) -> AgentCatalogSnapshot:
        with self.database.lock:
            row = (
                self.database.connection()
                .execute(
                    "SELECT snapshot_json FROM run_agent_catalogs WHERE run_id=?",
                    (run_id,),
                )
                .fetchone()
            )
            return (
                AgentCatalogSnapshot.model_validate_json(
                    self.database.json_blobs.read_json(
                        row[0], expected_kind="agent-catalog"
                    )
                )
                if row
                else AgentCatalogSnapshot()
            )

    @staticmethod
    def insert(connection: sqlite3.Connection, run_id: str, encoded: str) -> None:
        connection.execute(
            "INSERT INTO run_agent_catalogs (run_id,snapshot_json) VALUES (?,?)",
            (run_id, encoded),
        )
