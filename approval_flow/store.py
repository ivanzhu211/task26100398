"""SQLite 持久化层：实例定义与状态整体序列化保存。"""
from __future__ import annotations

import json
import sqlite3

from .models import ElementDef, InstanceState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    instance_id TEXT PRIMARY KEY,
    definition TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Store:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def create_instance(
        self, instance_id: str, definition: ElementDef, state: InstanceState
    ) -> None:
        self._conn.execute(
            "INSERT INTO instances (instance_id, definition, state) VALUES (?, ?, ?)",
            (instance_id, json.dumps(definition.to_dict()), json.dumps(state.to_dict())),
        )
        self._conn.commit()

    def load(self, instance_id: str) -> tuple[ElementDef, InstanceState]:
        row = self._conn.execute(
            "SELECT definition, state FROM instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"实例不存在: {instance_id}")
        return ElementDef.from_dict(json.loads(row[0])), InstanceState.from_dict(
            json.loads(row[1])
        )

    def save(self, instance_id: str, state: InstanceState) -> None:
        cur = self._conn.execute(
            "UPDATE instances SET state = ? WHERE instance_id = ?",
            (json.dumps(state.to_dict()), instance_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"实例不存在: {instance_id}")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
