"""验收测试：嵌套并行、局部重审、会签加签。"""
import pytest

from approval_flow import ApprovalFlow, DefinitionError


def make_flow():
    return ApprovalFlow(":memory:")


def nested_definition():
    """P0 并行：[A] 与 顺序[ P1 并行[B, C], D ]"""
    return {
        "type": "parallel",
        "branches": [
            {"type": "approval", "id": "A", "approvers": ["a1"]},
            {
                "type": "sequence",
                "items": [
                    {
                        "type": "parallel",
                        "branches": [
                            {"type": "approval", "id": "B", "approvers": ["b1"]},
                            {"type": "approval", "id": "C", "approvers": ["c1"]},
                        ],
                    },
                    {"type": "approval", "id": "D", "approvers": ["d1"]},
                ],
            },
        ],
    }


def todo_map(flow, instance_id):
    q = flow.query(instance_id)
    return {t["node_id"]: t for t in q["todos"]}, q["status"]


def todo_for(flow, instance_id, approver):
    matches = [t for t in flow.query(instance_id)["todos"] if t["approver"] == approver]
    assert len(matches) == 1
    return matches[0]


def approve_all(flow, instance_id):
    """通过当前全部待办，返回本轮处理的待办快照。"""
    q = flow.query(instance_id)
    snapshot = list(q["todos"])
    for t in snapshot:
        assert flow.act(instance_id, t["id"], "approve")
    return snapshot


# ---------- 场景一：外层分支已完成，内层多轮重审 ----------


def test_inner_rework_preserves_completed_outer_branch():
    flow = make_flow()
    flow.start_instance("i1", nested_definition())

    todos, status = todo_map(flow, "i1")
    assert status == "running"
    assert set(todos) == {"A", "B", "C"}

    # 外层分支 A 完成
    assert flow.act("i1", todos["A"]["id"], "approve")
    todos, _ = todo_map(flow, "i1")
    assert set(todos) == {"B", "C"}

    stale_ids = []
    # 多轮内层重审：B 通过、C 退回，反复两轮
    for round_no in (1, 2):
        todos, _ = todo_map(flow, "i1")
        assert set(todos) == {"B", "C"}
        assert flow.act("i1", todos["B"]["id"], "approve")
        stale_ids.extend(t["id"] for t in todos.values())
        assert flow.act("i1", todos["C"]["id"], "rework")
        # 内层重审不影响外层已完成的 A：A 不产生新待办
        current, status = todo_map(flow, "i1")
        assert status == "running"
        assert set(current) == {"B", "C"}
        assert all(t["round"] == round_no + 1 for t in current.values())

    # 所有旧待办（含 B 已通过的、被作废的）的任何动作都无效
    for old_id in stale_ids:
        before = flow.query("i1")
        assert not flow.act("i1", old_id, "approve")
        assert not flow.act("i1", old_id, "rework")
        assert not flow.act("i1", old_id, "countersign", add_approver="x")
        assert flow.query("i1") == before

    # 新一轮 B、C 通过 -> 进入 D；实例不能提前结束
    approve_all(flow, "i1")
    todos, status = todo_map(flow, "i1")
    assert status == "running"
    assert set(todos) == {"D"}

    # D 通过 -> 两分支汇合，实例结束
    approve_all(flow, "i1")
    todos, status = todo_map(flow, "i1")
    assert status == "finished"
    assert todos == {}

    # 结束后旧动作不能重新激活实例
    assert not flow.act("i1", stale_ids[0], "approve")
    assert flow.query("i1")["status"] == "finished"


# ---------- 场景二：内层汇合后的节点退回，重开范围扩大到外层并行段 ----------


def test_rework_after_inner_join_reopens_outer_parallel():
    flow = make_flow()
    flow.start_instance("i2", nested_definition())

    # A、B、C 全部通过：外层分支 A 完成，内层 P1 汇合，进入 D
    first_round = approve_all(flow, "i2")
    todos, status = todo_map(flow, "i2")
    assert status == "running"
    assert set(todos) == {"D"}

    # D 退回：D 不在内层 P1 内，其最内层并行段是外层 P0 -> 整棵 P0 重开
    assert flow.act("i2", todos["D"]["id"], "rework")
    todos, status = todo_map(flow, "i2")
    assert status == "running"
    # A、B、C 全部重新出现，原先保留的通过结论失效
    assert set(todos) == {"A", "B", "C"}
    assert all(t["round"] == 2 for t in todos.values())

    # 第一轮的旧待办（含已通过的 A/B/C）全部失效
    for t in first_round:
        assert not flow.act("i2", t["id"], "approve")

    # 重开后必须全部重新审批，实例不能提前结束
    approve_all(flow, "i2")
    todos, status = todo_map(flow, "i2")
    assert status == "running"
    assert set(todos) == {"D"}
    approve_all(flow, "i2")
    _, status = todo_map(flow, "i2")
    assert status == "finished"


# ---------- 场景三：部分通过后加签，重审清除临时加签 ----------


def test_countersign_then_rework_clears_temporary_approvers():
    flow = make_flow()
    definition = {
        "type": "sequence",
        "items": [
            {"type": "approval", "id": "N", "approvers": ["x", "y"]},
            {"type": "approval", "id": "M", "approvers": ["m"]},
        ],
    }
    flow.start_instance("i3", definition)

    q = flow.query("i3")
    assert {t["approver"] for t in q["todos"]} == {"x", "y"}

    # x 先通过；y 加签 z（y 自己并未通过）
    assert flow.act("i3", todo_for(flow, "i3", "x")["id"], "approve")
    y_todo = todo_for(flow, "i3", "y")
    assert flow.act("i3", y_todo["id"], "countersign", add_approver="z")
    current = flow.query("i3")["todos"]
    assert {t["approver"] for t in current} == {"y", "z"}

    # 重复加签已在名单中的人：不产生额外待办
    assert flow.act("i3", y_todo["id"], "countersign", add_approver="z")
    assert flow.act("i3", y_todo["id"], "countersign", add_approver="x")
    assert len(flow.query("i3")["todos"]) == 2

    # y 通过、z 未通过时不得推进
    assert flow.act("i3", y_todo["id"], "approve")
    q = flow.query("i3")
    assert [t["approver"] for t in q["todos"]] == ["z"]
    assert q["status"] == "running"

    # z 退回重审：N 不在任何并行段内，只重开 N；临时加签 z 不带入新一轮
    z_todo = q["todos"][0]
    assert flow.act("i3", z_todo["id"], "rework")
    q = flow.query("i3")
    assert {t["approver"] for t in q["todos"]} == {"x", "y"}
    assert all(t["round"] == 2 for t in q["todos"])

    # 旧待办（含 z 的、x 已通过的）一律无效
    assert not flow.act("i3", z_todo["id"], "approve")
    assert not flow.act("i3", y_todo["id"], "approve")
    assert {t["approver"] for t in flow.query("i3")["todos"]} == {"x", "y"}

    # 新一轮只接受新待办上的意见；x、y 通过后进入 M
    approve_all(flow, "i3")
    todos, status = todo_map(flow, "i3")
    assert status == "running"
    assert set(todos) == {"M"}
    approve_all(flow, "i3")
    assert flow.query("i3")["status"] == "finished"


# ---------- 定义校验与基础行为 ----------


def test_definition_validation():
    flow = make_flow()
    with pytest.raises(DefinitionError):
        flow.start_instance("bad1", {"type": "approval", "id": "n", "approvers": []})
    with pytest.raises(DefinitionError):
        flow.start_instance("bad2", {"type": "approval", "id": "n", "approvers": ["a", "a"]})
    with pytest.raises(DefinitionError):
        flow.start_instance("bad3", {"type": "parallel", "branches": [
            {"type": "approval", "id": "n", "approvers": ["a"]},
        ]})
    with pytest.raises(DefinitionError):
        deep = {"type": "approval", "id": "n", "approvers": ["a"]}
        for _ in range(4):
            deep = {"type": "parallel", "branches": [deep, {"type": "approval", "id": "m", "approvers": ["b"]}]}
        flow.start_instance("bad4", deep)


def test_todo_ids_never_reused():
    flow = make_flow()
    definition = {"type": "approval", "id": "N", "approvers": ["u"]}
    flow.start_instance("i4", definition)
    seen = set()
    for _ in range(3):
        q = flow.query("i4")
        assert len(q["todos"]) == 1
        todo = q["todos"][0]
        assert todo["id"] not in seen
        seen.add(todo["id"])
        if len(seen) < 3:
            assert flow.act("i4", todo["id"], "rework")
        else:
            assert flow.act("i4", todo["id"], "approve")
    assert flow.query("i4")["status"] == "finished"


def test_sqlite_persistence(tmp_path):
    db = str(tmp_path / "flow.db")
    flow = ApprovalFlow(db)
    flow.start_instance("i5", {"type": "approval", "id": "N", "approvers": ["u1", "u2"]})
    todo = flow.query("i5")["todos"][0]
    flow.act("i5", todo["id"], "approve")
    flow.close()

    # 重新打开数据库：状态仍在，剩余待办可见
    flow2 = ApprovalFlow(db)
    q = flow2.query("i5")
    assert q["status"] == "running"
    assert [t["approver"] for t in q["todos"]] == ["u2"]
    flow2.act("i5", q["todos"][0]["id"], "approve")
    assert flow2.query("i5")["status"] == "finished"
