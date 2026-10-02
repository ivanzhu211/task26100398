"""对外服务：启动实例、处理审批动作、查询实例。"""
from __future__ import annotations

import uuid
from typing import Any

from . import engine
from .models import (
    ActionType,
    InstanceState,
    InstanceStatus,
    TodoStatus,
    parse_definition,
)
from .store import Store


class ApprovalService:
    def __init__(self, db_path: str) -> None:
        self._store = Store(db_path)

    def start_instance(self, definition: dict[str, Any]) -> str:
        """按定义启动实例，返回实例 ID。"""
        parsed = parse_definition(definition)
        instance_id = uuid.uuid4().hex
        state = InstanceState(instance_id=instance_id)
        engine.start(parsed, state)
        self._store.create_instance(instance_id, parsed, state)
        return instance_id

    def handle_action(
        self,
        instance_id: str,
        todo_id: int,
        action: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """处理一次审批动作。动作无效（待办不存在/已办结/非本人/实例已结束）时不改变实例，返回 False。"""
        definition, state = self._store.load(instance_id)
        if action == ActionType.APPROVE.value:
            changed = engine.approve(definition, state, todo_id, actor)
        elif action == ActionType.ADD_SIGN.value:
            assignee = (payload or {}).get("assignee")
            if not isinstance(assignee, str) or not assignee:
                raise ValueError("add_sign 需要 payload.assignee")
            changed = engine.add_sign(definition, state, todo_id, actor, assignee)
        elif action == ActionType.REJECT.value:
            changed = engine.reject(definition, state, todo_id, actor)
        else:
            raise ValueError(f"未知动作: {action!r}")
        if changed:
            self._store.save(instance_id, state)
        return changed

    def get_instance(self, instance_id: str) -> dict[str, Any]:
        """查询实例：状态 + 当前全部待办。"""
        _, state = self._store.load(instance_id)
        return {
            "instance_id": state.instance_id,
            "status": state.status.value,
            "finished": state.status == InstanceStatus.FINISHED,
            "todos": [
                {
                    "todo_id": t.todo_id,
                    "node_key": t.node_key,
                    "assignee": t.assignee,
                    "round_no": t.round_no,
                    "added_by": t.added_by,
                }
                for t in state.todos
                if t.status == TodoStatus.PENDING
            ],
        }

    def close(self) -> None:
        self._store.close()
