# approval-flow

审批流程执行库（Python + SQLite），由上层直接调用，无接口层。

## 能力

- `ApprovalService.start_instance(definition)` 启动实例，返回实例 ID
- `ApprovalService.handle_action(instance_id, todo_id, action, actor, payload)` 处理审批动作（`approve` / `add_sign` / `reject`），无效动作不改变实例并返回 `False`
- `ApprovalService.get_instance(instance_id)` 查询实例状态与当前全部待办

## 流程定义

```python
{
    "type": "sequence",          # 或 "parallel" / "node"
    "children": [                 # 顺序段：children；并行段：branches（2-5 个）
        {"type": "node", "approvers": ["alice", "bob"]},   # 1-5 名不同审批人
        {"type": "parallel", "branches": [ ... ]},
    ],
}
```

## 语义

- 节点每次进入开启新一轮，名单恢复模板，全员通过才完成；`add_sign` 追加会签人（不视为发起人已通过），重复加签为无操作。
- `reject` 只重开包含该节点的最内层并行段：段内全部作废重来，段外已完成/等待中的工作保持不变；不在并行段内则只重开该节点。
- 待办 ID 实例内单调不复用；已办结/已作废待办上的动作一律无效；实例结束后不可再被激活。

## 测试

```bash
python3 -m pytest tests/ -q
```
