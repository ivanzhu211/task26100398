"""验收测试：嵌套并行、会签、局部重审。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from approval_flow import ApprovalService


def nested_definition():
    """root = 顺序[
      并行 P1 {
        分支A: 节点 nA [a1],
        分支B: 顺序[ 并行 P2 { nB1 [b1], nB2 [b2] }, 节点 nAfter [c1] ]
      }
    ]"""
    return {
        "type": "sequence",
        "children": [
            {
                "type": "parallel",
                "branches": [
                    {"type": "node", "approvers": ["a1"]},
                    {
                        "type": "sequence",
                        "children": [
                            {
                                "type": "parallel",
                                "branches": [
                                    {"type": "node", "approvers": ["b1"]},
                                    {"type": "node", "approvers": ["b2"]},
                                ],
                            },
                            {"type": "node", "approvers": ["c1"]},
                        ],
                    },
                ],
            }
        ],
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc = ApprovalService(os.path.join(self.tmp, "test.db"))

    def tearDown(self):
        self.svc.close()

    def todo_map(self, iid):
        """当前待办：assignee -> todo_id（每人一份时方便取用）。"""
        return {t["assignee"]: t["todo_id"] for t in self.svc.get_instance(iid)["todos"]}

    def assignees(self, iid):
        return sorted(t["assignee"] for t in self.svc.get_instance(iid)["todos"])

    def finished(self, iid):
        return self.svc.get_instance(iid)["finished"]


class TestInnerRerunKeepsOuterBranch(Base):
    """外层已有分支完成，另一分支发生多轮内层重审。"""

    def test_multi_round_inner_rerun(self):
        iid = self.svc.start_instance(nested_definition())
        self.assertEqual(self.assignees(iid), ["a1", "b1", "b2"])

        # 分支A 完成
        self.assertTrue(self.svc.handle_action(iid, self.todo_map(iid)["a1"], "approve", "a1"))
        self.assertEqual(self.assignees(iid), ["b1", "b2"])
        self.assertFalse(self.finished(iid))

        # 内层 P2 第一轮：b1 通过，b2 退回 -> 只重开 P2
        t1 = self.todo_map(iid)
        self.assertTrue(self.svc.handle_action(iid, t1["b1"], "approve", "b1"))
        self.assertTrue(self.svc.handle_action(iid, t1["b2"], "reject", "b2"))
        # P2 两个节点都回到新一轮；b1 之前的通过作废；分支A 不受影响
        self.assertEqual(self.assignees(iid), ["b1", "b2"])
        self.assertFalse(self.finished(iid))

        # 第一轮旧待办（含已通过的 b1、已作废的 b2）动作均无影响
        self.assertFalse(self.svc.handle_action(iid, t1["b1"], "approve", "b1"))
        self.assertFalse(self.svc.handle_action(iid, t1["b2"], "approve", "b2"))
        self.assertFalse(self.svc.handle_action(iid, t1["b1"], "reject", "b1"))
        self.assertEqual(self.assignees(iid), ["b1", "b2"])

        # 第二轮：b1 通过，b2 再次退回 -> 又一轮
        t2 = self.todo_map(iid)
        self.assertNotEqual(t2["b1"], t1["b1"])  # 待办标识永不复用
        self.assertTrue(self.svc.handle_action(iid, t2["b1"], "approve", "b1"))
        self.assertTrue(self.svc.handle_action(iid, t2["b2"], "reject", "b2"))
        self.assertEqual(self.assignees(iid), ["b1", "b2"])
        self.assertFalse(self.svc.handle_action(iid, t2["b1"], "approve", "b1"))

        # 第三轮：b1、b2 都通过 -> P2 汇合，进入 nAfter；分支A 不会被重做
        t3 = self.todo_map(iid)
        self.assertTrue(self.svc.handle_action(iid, t3["b1"], "approve", "b1"))
        self.assertFalse(self.finished(iid))
        self.assertTrue(self.svc.handle_action(iid, t3["b2"], "approve", "b2"))
        self.assertEqual(self.assignees(iid), ["c1"])
        self.assertFalse(self.finished(iid))

        # nAfter 通过 -> 整个流程结束；结束后旧动作不能重新激活
        self.assertTrue(self.svc.handle_action(iid, self.todo_map(iid)["c1"], "approve", "c1"))
        self.assertTrue(self.finished(iid))
        self.assertEqual(self.assignees(iid), [])
        self.assertFalse(self.svc.handle_action(iid, t3["b1"], "reject", "b1"))
        self.assertTrue(self.finished(iid))


class TestRejectAfterInnerJoinExpandsScope(Base):
    """内层汇合后的审批节点退回，重开范围扩大到外层并行段。"""

    def test_reject_after_inner_join_resets_outer_parallel(self):
        iid = self.svc.start_instance(nested_definition())
        t = self.todo_map(iid)
        # 分支A 完成；内层 P2 完成并汇合；进入 nAfter
        self.assertTrue(self.svc.handle_action(iid, t["a1"], "approve", "a1"))
        self.assertTrue(self.svc.handle_action(iid, t["b1"], "approve", "b1"))
        self.assertTrue(self.svc.handle_action(iid, t["b2"], "approve", "b2"))
        self.assertEqual(self.assignees(iid), ["c1"])

        # nAfter 退回：其所在最内层并行段是外层 P1，整个 P1 重开
        c1_todo = self.todo_map(iid)["c1"]
        self.assertTrue(self.svc.handle_action(iid, c1_todo, "reject", "c1"))
        # 原先保留的通过结论（a1、b1、b2）全部失效，回到 P1 起点
        self.assertEqual(self.assignees(iid), ["a1", "b1", "b2"])
        self.assertFalse(self.finished(iid))

        # 旧待办（含已通过的 a1/b1/b2、已作废的 c1）全部无效
        for person in ("a1", "b1", "b2"):
            self.assertFalse(self.svc.handle_action(iid, t[person], "approve", person))
        self.assertFalse(self.svc.handle_action(iid, c1_todo, "approve", "c1"))
        self.assertEqual(self.assignees(iid), ["a1", "b1", "b2"])

        # 重新走完全部流程，不能提前结束
        t2 = self.todo_map(iid)
        self.assertTrue(self.svc.handle_action(iid, t2["a1"], "approve", "a1"))
        self.assertTrue(self.svc.handle_action(iid, t2["b1"], "approve", "b1"))
        self.assertFalse(self.finished(iid))
        self.assertTrue(self.svc.handle_action(iid, t2["b2"], "approve", "b2"))
        self.assertEqual(self.assignees(iid), ["c1"])
        self.assertTrue(self.svc.handle_action(iid, self.todo_map(iid)["c1"], "approve", "c1"))
        self.assertTrue(self.finished(iid))


class TestAddSignAndRerun(Base):
    """部分通过后加签：新成员未通过不得推进；重审清除临时加签。"""

    def single_node(self, approvers):
        return self.svc.start_instance({"type": "node", "approvers": approvers})

    def test_add_sign_blocks_progress_and_keeps_existing_approvals(self):
        iid = self.single_node(["x", "y"])
        t = self.todo_map(iid)
        self.assertTrue(self.svc.handle_action(iid, t["x"], "approve", "x"))
        # y 加签 z：y 自己并未通过
        self.assertTrue(
            self.svc.handle_action(iid, t["y"], "add_sign", "y", {"assignee": "z"})
        )
        self.assertEqual(self.assignees(iid), ["y", "z"])
        # 重复加签已在名单中的人：不产生额外待办，也不撤销 x 的通过
        self.assertTrue(
            self.svc.handle_action(iid, t["y"], "add_sign", "y", {"assignee": "x"})
        )
        self.assertEqual(self.assignees(iid), ["y", "z"])
        # y 通过但 z 未通过：不得推进
        self.assertTrue(self.svc.handle_action(iid, t["y"], "approve", "y"))
        self.assertFalse(self.finished(iid))
        self.assertEqual(self.assignees(iid), ["z"])
        # z 通过后节点完成（x 不必重新审批）
        self.assertTrue(self.svc.handle_action(iid, self.todo_map(iid)["z"], "approve", "z"))
        self.assertTrue(self.finished(iid))

    def test_rerun_clears_temp_signers(self):
        iid = self.single_node(["x", "y"])
        t = self.todo_map(iid)
        self.assertTrue(self.svc.handle_action(iid, t["x"], "approve", "x"))
        self.assertTrue(
            self.svc.handle_action(iid, t["y"], "add_sign", "y", {"assignee": "z"})
        )
        # y 退回重审（节点不在并行段内，只重开该节点）
        self.assertTrue(self.svc.handle_action(iid, t["y"], "reject", "y"))
        # 新一轮恢复模板名单：只有 x、y；临时加签的 z 被清除
        self.assertEqual(self.assignees(iid), ["x", "y"])
        self.assertFalse(self.finished(iid))
        # 旧待办全部无效：x 上一轮通过不算数，z 的待办已作废
        self.assertFalse(self.svc.handle_action(iid, t["x"], "approve", "x"))
        self.assertFalse(self.svc.handle_action(iid, t["y"], "approve", "y"))
        z_todo = t["y"] + 1  # z 的旧待办 id（顺序分配）
        self.assertFalse(self.svc.handle_action(iid, z_todo, "approve", "z"))
        self.assertEqual(self.assignees(iid), ["x", "y"])
        # 新一轮：只接受新待办上的意见
        t2 = self.todo_map(iid)
        self.assertTrue(self.svc.handle_action(iid, t2["x"], "approve", "x"))
        self.assertFalse(self.finished(iid))
        self.assertTrue(self.svc.handle_action(iid, t2["y"], "approve", "y"))
        self.assertTrue(self.finished(iid))


class TestGuardRails(Base):
    """基本防护：非本人待办、未知实例、结束后动作。"""

    def test_action_by_non_holder_rejected(self):
        iid = self.svc.start_instance({"type": "node", "approvers": ["x"]})
        tid = self.todo_map(iid)["x"]
        self.assertFalse(self.svc.handle_action(iid, tid, "approve", "someone_else"))
        self.assertFalse(self.finished(iid))

    def test_unknown_instance_raises(self):
        with self.assertRaises(KeyError):
            self.svc.get_instance("nope")

    def test_persistence_across_service_instances(self):
        db = os.path.join(self.tmp, "shared.db")
        svc1 = ApprovalService(db)
        iid = svc1.start_instance({"type": "node", "approvers": ["x", "y"]})
        tid = svc1.get_instance(iid)["todos"][0]["todo_id"]
        svc1.handle_action(iid, tid, "approve", "x")
        svc1.close()
        svc2 = ApprovalService(db)
        self.assertEqual(
            sorted(t["assignee"] for t in svc2.get_instance(iid)["todos"]), ["y"]
        )
        tid_y = svc2.get_instance(iid)["todos"][0]["todo_id"]
        svc2.handle_action(iid, tid_y, "approve", "y")
        self.assertTrue(svc2.get_instance(iid)["finished"])
        svc2.close()


if __name__ == "__main__":
    unittest.main()
