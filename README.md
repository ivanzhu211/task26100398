# approval_flow

审批流程执行库：嵌套顺序/并行段、会签（全员通过）、加签与局部退回重审。
上层直接调用，无接口层；实例与任务状态保存在 SQLite。

## 用法

```python
from approval_flow import ApprovalFlow

flow = ApprovalFlow("flow.db")          # SQLite 文件路径，":memory:" 亦可

definition = {
    "type": "parallel", "branches": [
        {"type": "approval", "id": "finance", "approvers": ["u1", "u2"]},
        {"type": "sequence", "items": [
            {"type": "parallel", "branches": [
                {"type": "approval", "id": "legal", "approvers": ["u3"]},
                {"type": "approval", "id": "security", "approvers": ["u4"]},
            ]},
            {"type": "approval", "id": "cto", "approvers": ["u5"]},
        ]},
    ],
}

flow.start_instance("inst-1", definition)   # 启动实例
q = flow.query("inst-1")                    # {"status": "running", "todos": [...]}
flow.act("inst-1", todo_id, "approve")      # 通过
flow.act("inst-1", todo_id, "countersign", add_approver="u9")  # 加签一人
flow.act("inst-1", todo_id, "rework")       # 退回重审（重开最内层并行段）
```

## 规则要点

- 顺序段前一项完成才进入下一项；并行段同时启动全部分支，全部完成才汇合。
- 审批节点每次进入产生新一轮待办（每人一份，标识实例内唯一），全员通过才完成。
- 加签：持有人追加一名审批人，本人不视为已通过；重复加入本轮名单中的人无效果。
- 退回重审：重开包含该节点的最内层并行段（不在并行段内则只重开该节点），
  段内未完成待办作废、通过结论失效、临时加签清除；段外状态不变。
- 已通过/已作废/上一轮的待办上的动作一律无效；实例结束后任何动作无效。

## 测试

```bash
python3 -m pytest tests/ -q
```
