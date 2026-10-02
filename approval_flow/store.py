"""SQLite 持久化：实例定义与运行状态。

每个实例一行：定义 JSON（启动后不再改变）+ 运行状态 JSON。
调用串行执行，每次调用在一次事务内完成读-改-写。
"""
from __future__ import annotations

import sqlite3


class InstanceNotFound(KeyError):
    pass


class InstanceExists(ValueError):
    pass


class Store:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instances (
                instance_id TEXT PRIMARY KEY,
                definition  TEXT NOT NULL,
                state       TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def create_instance(self, instance_id: str, definition_json: str, state_json: str) -> None:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO instances (instance_id, definition, state) VALUES (?, ?, ?)",
                    (instance_id, definition_json, state_json),
                )
        except sqlite3.IntegrityError:
            raise InstanceExists(instance_id) from None

    def load(self, instance_id: str) -> tuple[str, str]:
        """返回 (definition_json, state_json)。"""
        row = self._conn.execute(
            "SELECT definition, state FROM instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if row is None:
            raise InstanceNotFound(instance_id)
        return row[0], row[1]

    def save_state(self, instance_id: str, state_json: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE instances SET state = ? WHERE instance_id = ?",
                (state_json, instance_id),
            )

    def close(self) -> None:
        self._conn.close()
