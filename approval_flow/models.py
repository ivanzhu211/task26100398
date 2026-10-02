"""流程定义与运行时状态的类型定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

MAX_NODES = 50
MAX_PARALLEL_DEPTH = 3
MIN_APPROVERS = 1
MAX_APPROVERS = 5
MIN_BRANCHES = 2
MAX_BRANCHES = 5


class ElementKind(str, Enum):
    NODE = "node"
    SEQUENCE = "sequence"
    PARALLEL = "parallel"


class ElementStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"


class TodoStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"


class InstanceStatus(str, Enum):
    RUNNING = "running"
    FINISHED = "finished"


class ActionType(str, Enum):
    APPROVE = "approve"
    ADD_SIGN = "add_sign"
    REJECT = "reject"


class DefinitionError(ValueError):
    """流程定义不合法。"""


@dataclass
class ElementDef:
    """定义树中的一个元素（审批节点 / 顺序段 / 并行段）。"""

    key: str
    kind: ElementKind
    approvers: tuple[str, ...] = ()
    children: list["ElementDef"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind.value,
            "approvers": list(self.approvers),
            "children": [c.to_dict() for c in self.children],
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "ElementDef":
        return ElementDef(
            key=raw["key"],
            kind=ElementKind(raw["kind"]),
            approvers=tuple(raw["approvers"]),
            children=[ElementDef.from_dict(c) for c in raw["children"]],
        )


@dataclass
class Todo:
    todo_id: int
    node_key: str
    assignee: str
    round_no: int
    status: TodoStatus = TodoStatus.PENDING
    added_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "todo_id": self.todo_id,
            "node_key": self.node_key,
            "assignee": self.assignee,
            "round_no": self.round_no,
            "status": self.status.value,
            "added_by": self.added_by,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Todo":
        return Todo(
            todo_id=raw["todo_id"],
            node_key=raw["node_key"],
            assignee=raw["assignee"],
            round_no=raw["round_no"],
            status=TodoStatus(raw["status"]),
            added_by=raw["added_by"],
        )


@dataclass
class InstanceState:
    instance_id: str
    status: InstanceStatus = InstanceStatus.RUNNING
    element_status: dict[str, ElementStatus] = field(default_factory=dict)
    node_round: dict[str, int] = field(default_factory=dict)
    node_roster: dict[str, list[str]] = field(default_factory=dict)
    todos: list[Todo] = field(default_factory=list)
    next_todo_id: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "status": self.status.value,
            "element_status": {k: v.value for k, v in self.element_status.items()},
            "node_round": dict(self.node_round),
            "node_roster": {k: list(v) for k, v in self.node_roster.items()},
            "todos": [t.to_dict() for t in self.todos],
            "next_todo_id": self.next_todo_id,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "InstanceState":
        return InstanceState(
            instance_id=raw["instance_id"],
            status=InstanceStatus(raw["status"]),
            element_status={k: ElementStatus(v) for k, v in raw["element_status"].items()},
            node_round=dict(raw["node_round"]),
            node_roster={k: list(v) for k, v in raw["node_roster"].items()},
            todos=[Todo.from_dict(t) for t in raw["todos"]],
            next_todo_id=raw["next_todo_id"],
        )


def parse_definition(raw: dict[str, Any]) -> ElementDef:
    """解析并校验流程定义，返回定义树根节点。每个元素被赋予稳定 key。"""
    counters = {"nodes": 0}

    def build(spec: dict[str, Any], path: str, depth: int) -> ElementDef:
        if not isinstance(spec, dict):
            raise DefinitionError(f"元素 {path} 必须是对象")
        kind_raw = spec.get("type")
        if kind_raw == ElementKind.NODE.value:
            counters["nodes"] += 1
            if counters["nodes"] > MAX_NODES:
                raise DefinitionError(f"审批节点数量超过上限 {MAX_NODES}")
            approvers = spec.get("approvers")
            if (
                not isinstance(approvers, list)
                or not (MIN_APPROVERS <= len(approvers) <= MAX_APPROVERS)
                or len(set(approvers)) != len(approvers)
                or not all(isinstance(a, str) and a for a in approvers)
            ):
                raise DefinitionError(
                    f"节点 {path} 的审批人必须是 {MIN_APPROVERS}-{MAX_APPROVERS} 名不同人员"
                )
            return ElementDef(key=path, kind=ElementKind.NODE, approvers=tuple(approvers))
        if kind_raw == ElementKind.SEQUENCE.value:
            children_spec = spec.get("children")
            if not isinstance(children_spec, list) or not children_spec:
                raise DefinitionError(f"顺序段 {path} 必须包含非空 children")
            children = [
                build(c, f"{path}.{i}", depth) for i, c in enumerate(children_spec)
            ]
            return ElementDef(key=path, kind=ElementKind.SEQUENCE, children=children)
        if kind_raw == ElementKind.PARALLEL.value:
            if depth >= MAX_PARALLEL_DEPTH:
                raise DefinitionError(f"并行段嵌套超过 {MAX_PARALLEL_DEPTH} 层")
            branches_spec = spec.get("branches")
            if (
                not isinstance(branches_spec, list)
                or not (MIN_BRANCHES <= len(branches_spec) <= MAX_BRANCHES)
            ):
                raise DefinitionError(
                    f"并行段 {path} 必须包含 {MIN_BRANCHES}-{MAX_BRANCHES} 个分支"
                )
            children = [
                build(c, f"{path}.{i}", depth + 1) for i, c in enumerate(branches_spec)
            ]
            return ElementDef(key=path, kind=ElementKind.PARALLEL, children=children)
        raise DefinitionError(f"元素 {path} 类型未知: {kind_raw!r}")

    return build(raw, "root", 0)


def index_elements(root: ElementDef) -> dict[str, ElementDef]:
    """返回 key -> 元素 的索引。"""
    out: dict[str, ElementDef] = {}

    def walk(el: ElementDef) -> None:
        out[el.key] = el
        for c in el.children:
            walk(c)

    walk(root)
    return out


def innermost_parallel_ancestor(root: ElementDef, node_key: str) -> ElementDef | None:
    """返回包含指定元素的、最内层的并行段；不存在则返回 None。"""
    path: list[ElementDef] = []

    def find(el: ElementDef, trail: list[ElementDef]) -> bool:
        if el.key == node_key:
            path.extend(trail)
            return True
        for c in el.children:
            if find(c, trail + [el]):
                return True
        return False

    if not find(root, []):
        raise KeyError(f"未知元素: {node_key}")
    parallels = [el for el in path if el.kind == ElementKind.PARALLEL]
    return parallels[-1] if parallels else None
