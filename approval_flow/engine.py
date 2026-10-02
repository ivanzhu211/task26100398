"""执行核心：推进、审批动作、局部重审。

约定：
- 每个定义元素有稳定 key，运行时状态全部放在 InstanceState 中。
- 节点每次被激活即开启新一轮：轮次 +1，名单恢复为模板名单，生成待办。
- 节点完成条件：当前轮次下不存在 PENDING 待办（名单中每人都已通过）。
- 退回重审：重置包含该节点的最内层并行段整棵子树；不在任何并行段内则只重置该节点。
"""
from __future__ import annotations

from .models import (
    ElementDef,
    ElementKind,
    ElementStatus,
    InstanceState,
    InstanceStatus,
    Todo,
    TodoStatus,
    index_elements,
    innermost_parallel_ancestor,
)


def _parent_map(root: ElementDef) -> dict[str, ElementDef]:
    out: dict[str, ElementDef] = {}

    def walk(el: ElementDef) -> None:
        for c in el.children:
            out[c.key] = el
            walk(c)

    walk(root)
    return out


def _subtree_keys(el: ElementDef) -> set[str]:
    keys = {el.key}
    for c in el.children:
        keys |= _subtree_keys(c)
    return keys


def _new_todo(state: InstanceState, node_key: str, assignee: str, round_no: int, added_by: str | None) -> Todo:
    todo = Todo(
        todo_id=state.next_todo_id,
        node_key=node_key,
        assignee=assignee,
        round_no=round_no,
        added_by=added_by,
    )
    state.next_todo_id += 1
    state.todos.append(todo)
    return todo


def _activate(el: ElementDef, state: InstanceState) -> None:
    """激活元素：节点开新一轮并生成待办，顺序段进入第一项，并行段启动全部分支。"""
    state.element_status[el.key] = ElementStatus.ACTIVE
    if el.kind == ElementKind.NODE:
        round_no = state.node_round.get(el.key, 0) + 1
        state.node_round[el.key] = round_no
        state.node_roster[el.key] = list(el.approvers)
        for person in el.approvers:
            _new_todo(state, el.key, person, round_no, added_by=None)
    elif el.kind == ElementKind.SEQUENCE:
        _activate(el.children[0], state)
    elif el.kind == ElementKind.PARALLEL:
        for branch in el.children:
            _activate(branch, state)


def _complete(el: ElementDef, state: InstanceState, parents: dict[str, ElementDef], root: ElementDef) -> None:
    """标记元素完成并向上传播汇合。"""
    state.element_status[el.key] = ElementStatus.COMPLETED
    parent = parents.get(el.key)
    if parent is None:
        state.status = InstanceStatus.FINISHED
        return
    if parent.kind == ElementKind.SEQUENCE:
        idx = next(i for i, c in enumerate(parent.children) if c.key == el.key)
        if idx + 1 < len(parent.children):
            _activate(parent.children[idx + 1], state)
        else:
            _complete(parent, state, parents, root)
    elif parent.kind == ElementKind.PARALLEL:
        if all(
            state.element_status.get(c.key) == ElementStatus.COMPLETED
            for c in parent.children
        ):
            _complete(parent, state, parents, root)


def _find_pending_todo(state: InstanceState, todo_id: int, actor: str) -> Todo | None:
    for t in state.todos:
        if t.todo_id == todo_id:
            if t.status != TodoStatus.PENDING or t.assignee != actor:
                return None
            return t
    return None


def start(definition: ElementDef, state: InstanceState) -> None:
    """启动实例：激活根元素并产生首轮待办。"""
    for key in index_elements(definition):
        state.element_status[key] = ElementStatus.PENDING
    _activate(definition, state)


def approve(definition: ElementDef, state: InstanceState, todo_id: int, actor: str) -> bool:
    if state.status != InstanceStatus.RUNNING:
        return False
    todo = _find_pending_todo(state, todo_id, actor)
    if todo is None:
        return False
    todo.status = TodoStatus.APPROVED
    idx = index_elements(definition)
    node = idx[todo.node_key]
    round_no = state.node_round[node.key]
    if todo.round_no != round_no:
        return True  # 旧轮次待办（理论上已被作废），不影响当前轮
    still_pending = any(
        t.status == TodoStatus.PENDING and t.node_key == node.key and t.round_no == round_no
        for t in state.todos
    )
    if not still_pending:
        _complete(node, state, _parent_map(definition), definition)
    return True


def add_sign(
    definition: ElementDef, state: InstanceState, todo_id: int, actor: str, assignee: str
) -> bool:
    if state.status != InstanceStatus.RUNNING:
        return False
    todo = _find_pending_todo(state, todo_id, actor)
    if todo is None:
        return False
    idx = index_elements(definition)
    if todo.node_key not in idx:
        return False
    round_no = state.node_round[todo.node_key]
    if todo.round_no != round_no:
        return False
    roster = state.node_roster[todo.node_key]
    if assignee in roster:
        return True  # 本轮已在名单中：不产生额外待办，也不撤销已有意见
    roster.append(assignee)
    _new_todo(state, todo.node_key, assignee, round_no, added_by=actor)
    return True


def reject(definition: ElementDef, state: InstanceState, todo_id: int, actor: str) -> bool:
    if state.status != InstanceStatus.RUNNING:
        return False
    todo = _find_pending_todo(state, todo_id, actor)
    if todo is None:
        return False
    idx = index_elements(definition)
    node = idx.get(todo.node_key)
    if node is None:
        return False
    round_no = state.node_round.get(node.key)
    if todo.round_no != round_no:
        return False
    scope = innermost_parallel_ancestor(definition, node.key) or node
    scope_keys = _subtree_keys(scope)
    # 作废范围内所有未完成待办
    for t in state.todos:
        if t.status == TodoStatus.PENDING and t.node_key in scope_keys:
            t.status = TodoStatus.CANCELLED
    # 范围内元素全部回到未开始，名单与轮次在重新激活时恢复模板
    for key in scope_keys:
        state.element_status[key] = ElementStatus.PENDING
        state.node_roster.pop(key, None)
    _activate(scope, state)
    return True
