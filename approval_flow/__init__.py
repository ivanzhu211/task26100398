"""审批流程执行库：嵌套顺序/并行段、会签、加签与局部重审。"""
from .definition import DefinitionError
from .engine import Engine
from .store import Store

__all__ = ["ApprovalFlow", "DefinitionError"]


class ApprovalFlow:
    """上层直接调用的入口。串行调用，状态保存在 SQLite。"""

    def __init__(self, db_path: str) -> None:
        self._engine = Engine(Store(db_path))

    def start_instance(self, instance_id: str, definition: dict) -> None:
        self._engine.start_instance(instance_id, definition)

    def act(self, instance_id: str, todo_id: str, action: str, add_approver: str | None = None) -> bool:
        return self._engine.act(instance_id, todo_id, action, add_approver)

    def query(self, instance_id: str) -> dict:
        return self._engine.query(instance_id)

    def close(self) -> None:
        self._engine._store.close()
