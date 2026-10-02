"""流程定义：解析、校验与路径标注。

定义以嵌套 dict 描述：
  {"type": "approval", "id": "n1", "approvers": ["a", "b"]}
  {"type": "sequence", "items": [<element>, ...]}
  {"type": "parallel", "branches": [<element>, ...]}
每个元素被赋予稳定路径（如 "0"、"0.2.1"），运行期状态以路径为键。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ApprovalNodeDef:
    path: str
    node_id: str
    approvers: list[str]


@dataclass
class SequenceDef:
    path: str
    items: list["Element"] = field(default_factory=list)


@dataclass
class ParallelDef:
    path: str
    branches: list["Element"] = field(default_factory=list)


Element = ApprovalNodeDef | SequenceDef | ParallelDef

MAX_APPROVERS = 5
MAX_PARALLEL_DEPTH = 3
MAX_APPROVAL_NODES = 50


class DefinitionError(ValueError):
    """流程定义不合法。"""


def _children(element: Element) -> list[Element]:
    if isinstance(element, SequenceDef):
        return element.items
    if isinstance(element, ParallelDef):
        return element.branches
    return []


def parse_definition(raw: dict[str, Any]) -> Element:
    """解析并校验流程定义，为每个元素标注稳定路径。"""
    if not isinstance(raw, dict):
        raise DefinitionError("definition must be a dict")
    counter = {"approval_nodes": 0}
    root = _parse_element(raw, "0", 0, counter)
    return root


def _parse_element(raw: dict[str, Any], path: str, parallel_depth: int, counter: dict) -> Element:
    kind = raw.get("type")
    if kind == "approval":
        counter["approval_nodes"] += 1
        if counter["approval_nodes"] > MAX_APPROVAL_NODES:
            raise DefinitionError("too many approval nodes")
        node_id = raw.get("id")
        approvers = raw.get("approvers")
        if not isinstance(node_id, str) or not node_id:
            raise DefinitionError(f"approval node at {path} missing id")
        if (
            not isinstance(approvers, list)
            or not (1 <= len(approvers) <= MAX_APPROVERS)
            or len(set(approvers)) != len(approvers)
            or not all(isinstance(a, str) and a for a in approvers)
        ):
            raise DefinitionError(f"approval node {node_id} needs 1-5 distinct approvers")
        return ApprovalNodeDef(path=path, node_id=node_id, approvers=list(approvers))
    if kind == "sequence":
        items = raw.get("items")
        if not isinstance(items, list) or not items:
            raise DefinitionError(f"sequence at {path} needs non-empty items")
        return SequenceDef(
            path=path,
            items=[_parse_element(c, f"{path}.{i}", parallel_depth, counter) for i, c in enumerate(items)],
        )
    if kind == "parallel":
        if parallel_depth >= MAX_PARALLEL_DEPTH:
            raise DefinitionError(f"parallel nesting exceeds {MAX_PARALLEL_DEPTH} levels at {path}")
        branches = raw.get("branches")
        if not isinstance(branches, list) or not (2 <= len(branches) <= 5):
            raise DefinitionError(f"parallel at {path} needs 2-5 branches")
        return ParallelDef(
            path=path,
            branches=[
                _parse_element(c, f"{path}.{i}", parallel_depth + 1, counter)
                for i, c in enumerate(branches)
            ],
        )
    raise DefinitionError(f"unknown element type at {path}: {kind!r}")


def find_element(root: Element, path: str) -> Element:
    current = root
    parts = path.split(".")[1:]  # 首段恒为 "0"
    for part in parts:
        current = _children(current)[int(part)]
    return current


def innermost_parallel_ancestor(root: Element, path: str) -> Optional[ParallelDef]:
    """返回静态包含 path 的最内层并行段；不存在则返回 None。"""
    target_parts = path.split(".")
    best: Optional[ParallelDef] = None

    def walk(element: Element, ancestors: list[Element]) -> None:
        nonlocal best
        if element.path.split(".") == target_parts:
            return
        for child in _children(element):
            child_parts = child.path.split(".")
            if child_parts == target_parts[: len(child_parts)]:
                if isinstance(element, ParallelDef):
                    best = element
                walk(child, ancestors + [element])
                return

    walk(root, [])
    return best
