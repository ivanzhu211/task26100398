"""执行核心：实例推进、审批动作、查询。

运行状态（JSON，存于 SQLite）：
  status:   running | finished
  todo_seq: 待办标识发号器，实例内单调递增、永不复用
  todos:    [{id, node, round, approver, status}]  status: pending|approved|void
  elements: 以定义路径为键的元素状态
    approval: {round, completed_round, extras}  extras 为本轮临时加签人
    sequence: {index, done}
    parallel: {done}
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .definition import (
    ApprovalNodeDef,
    Element,
    ParallelDef,
    SequenceDef,
    find_element,
    innermost_parallel_ancestor,
    parse_definition,
)
from .store import Store

ACTIONS = ("approve", "countersign", "rework")


class Engine:
    def __init__(self, store: Store) -> None:
        self._store = store

    # ---------- 对外能力 ----------

    def start_instance(self, instance_id: str, definition: dict[str, Any]) -> None:
        root = parse_definition(definition)
        state: dict[str, Any] = {
            "status": "running",
            "todo_seq": 0,
            "todos": [],
            "elements": {},
        }

        def init_states(el: Element) -> None:
            if isinstance(el, ApprovalNodeDef):
                state["elements"][el.path] = {"round": 0, "completed_round": 0, "extras": []}
            elif isinstance(el, SequenceDef):
                state["elements"][el.path] = {"index": 0, "done": False}
                for item in el.items:
                    init_states(item)
            else:
                state["elements"][el.path] = {"done": False}
                for branch in el.branches:
                    init_states(branch)

        init_states(root)
        self._enter(root, state)
        self._store.create_instance(
            instance_id, json.dumps(definition), json.dumps(state)
        )

    def act(
        self,
        instance_id: str,
        todo_id: str,
        action: str,
        add_approver: Optional[str] = None,
    ) -> bool:
        """处理审批动作；动作无效时返回 False 且不改变实例。"""
        if action not in ACTIONS:
            return False
        definition_json, state_json = self._store.load(instance_id)
        root = parse_definition(json.loads(definition_json))
        state = json.loads(state_json)
        if state["status"] != "running":
            return False
        todo = next((t for t in state["todos"] if t["id"] == todo_id), None)
        if todo is None or todo["status"] != "pending":
            return False
        node_state = state["elements"][todo["node"]]
        if todo["round"] != node_state["round"]:
            return False

        if action == "approve":
            todo["status"] = "approved"
            self._check_node_complete(root, todo["node"], state)
        elif action == "countersign":
            if not add_approver:
                return False
            node_def = find_element(root, todo["node"])
            assert isinstance(node_def, ApprovalNodeDef)
            roster = node_def.approvers + node_state["extras"]
            if add_approver not in roster:
                node_state["extras"].append(add_approver)
                self._new_todo(state, todo["node"], node_state["round"], add_approver)
        else:  # rework
            target = innermost_parallel_ancestor(root, todo["node"])
            target_el: Element = target if target is not None else find_element(root, todo["node"])
            self._reset_subtree(target_el, state)
            self._enter(target_el, state)

        self._store.save_state(instance_id, json.dumps(state))
        return True

    def query(self, instance_id: str) -> dict[str, Any]:
        definition_json, state_json = self._store.load(instance_id)
        root = parse_definition(json.loads(definition_json))
        state = json.loads(state_json)
        todos = []
        for t in state["todos"]:
            if t["status"] != "pending":
                continue
            node_def = find_element(root, t["node"])
            assert isinstance(node_def, ApprovalNodeDef)
            todos.append(
                {
                    "id": t["id"],
                    "node_id": node_def.node_id,
                    "approver": t["approver"],
                    "round": t["round"],
                }
            )
        return {"instance_id": instance_id, "status": state["status"], "todos": todos}

    # ---------- 内部推进 ----------

    def _new_todo(self, state: dict, node_path: str, round_no: int, approver: str) -> None:
        state["todo_seq"] += 1
        state["todos"].append(
            {
                "id": f"t{state['todo_seq']}",
                "node": node_path,
                "round": round_no,
                "approver": approver,
                "status": "pending",
            }
        )

    def _enter(self, el: Element, state: dict) -> None:
        """进入一个元素：审批节点产生新一轮待办，容器递归进入子元素。"""
        el_state = state["elements"][el.path]
        if isinstance(el, ApprovalNodeDef):
            el_state["round"] += 1
            el_state["extras"] = []
            for approver in el.approvers:
                self._new_todo(state, el.path, el_state["round"], approver)
        elif isinstance(el, SequenceDef):
            el_state["index"] = 0
            el_state["done"] = False
            self._enter(el.items[0], state)
        else:
            assert isinstance(el, ParallelDef)
            el_state["done"] = False
            for branch in el.branches:
                self._enter(branch, state)

    def _is_done(self, el: Element, state: dict) -> bool:
        el_state = state["elements"][el.path]
        if isinstance(el, ApprovalNodeDef):
            return el_state["round"] > 0 and el_state["completed_round"] == el_state["round"]
        return el_state["done"]

    def _check_node_complete(self, root: Element, node_path: str, state: dict) -> None:
        node_def = find_element(root, node_path)
        assert isinstance(node_def, ApprovalNodeDef)
        node_state = state["elements"][node_path]
        required = set(node_def.approvers) | set(node_state["extras"])
        approved = {
            t["approver"]
            for t in state["todos"]
            if t["node"] == node_path
            and t["round"] == node_state["round"]
            and t["status"] == "approved"
        }
        if required <= approved:
            node_state["completed_round"] = node_state["round"]
            self._propagate(root, node_def, state)

    def _propagate(self, root: Element, el: Element, state: dict) -> None:
        """元素完成后向上推进；到达根则实例结束。"""
        if el.path == root.path:
            state["status"] = "finished"
            return
        parent_path = ".".join(el.path.split(".")[:-1])
        parent = find_element(root, parent_path)
        parent_state = state["elements"][parent_path]
        if isinstance(parent, SequenceDef):
            child_index = int(el.path.split(".")[-1])
            if parent_state["done"] or child_index != parent_state["index"]:
                return
            parent_state["index"] += 1
            if parent_state["index"] == len(parent.items):
                parent_state["done"] = True
                self._propagate(root, parent, state)
            else:
                self._enter(parent.items[parent_state["index"]], state)
        else:
            assert isinstance(parent, ParallelDef)
            if parent_state["done"]:
                return
            if all(self._is_done(b, state) for b in parent.branches):
                parent_state["done"] = True
                self._propagate(root, parent, state)

    def _reset_subtree(self, el: Element, state: dict) -> None:
        """重开子树：作废未完成待办、清除完成结论，随后由 _enter 重新启动。"""
        if isinstance(el, ApprovalNodeDef):
            for t in state["todos"]:
                if t["node"] == el.path and t["status"] == "pending":
                    t["status"] = "void"
            el_state = state["elements"][el.path]
            el_state["extras"] = []
        elif isinstance(el, SequenceDef):
            state["elements"][el.path]["done"] = False
            for item in el.items:
                self._reset_subtree(item, state)
        else:
            assert isinstance(el, ParallelDef)
            state["elements"][el.path]["done"] = False
            for branch in el.branches:
                self._reset_subtree(branch, state)
