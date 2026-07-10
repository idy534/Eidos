# 用户流程与界面

版本：v0.4

## 1. Workbench 信息架构

MVP 保留三栏工作台，但不内嵌 Terminal：

```text
┌──────────────┬──────────────────────────────┬──────────────────────┐
│ 导航区        │ 核心交互区                    │ 上下文与产物区          │
├──────────────┼──────────────────────────────┼──────────────────────┤
│ Sessions     │ 对话与 Execution Feed         │ 文件树 / Artifacts      │
│ Workspaces   │ 工具与审批卡片                 │ Diff / 预览 / 日志       │
│ Models       │ Run 状态与输入框               │ 打开系统 Terminal       │
│ Settings     │ 队列状态                       │                      │
└──────────────┴──────────────────────────────┴──────────────────────┘
```

模式差异：

| 区域 | Workspace Mode | Public Mode |
|---|---|---|
| 文件树 | 显示 | 不显示 |
| 文件预览 | 显示 active root 文件 | 只预览 Artifact |
| Artifact | 显示 | 显示且是唯一产物入口 |
| 系统 Terminal | 可由用户点击打开 | 不提供 |

## 2. Execution Feed

Execution Feed 合并对话、执行流和 Timeline，至少展示：

- 用户输入与后续补充信息。
- 模型文本、工具调用和最终回答。
- ToolCall 状态、参数摘要和结果摘要。
- Approval 请求、决定、拒绝原因和版本冲突。
- Shell 运行状态、最近输出、输出大小、退出码和终止原因。
- 中断的模型流保留已显示进度文本，并明确标记“输出未完成”。
- Run 排队、运行、暂停、停止、失败、取消与完成状态。
- Artifact 发布与版本。
- 安全拒绝、事实确认屏障和降级摘要。
- 模型认证或配置错误的终态失败，以及更换 Profile 后创建新 Run 的操作入口。

Execution Feed 不保存或展示模型供应商的 raw reasoning：

- 有 ToolCall 的普通文本展示为 `assistant_progress`。
- 无 ToolCall 且响应完成的普通文本展示为 `final_answer`。
- reasoning token 数量可以作为用量元数据展示，但 reasoning 内容不进入消息、Timeline 或回放。
- UI 不使用“思维链”“内部思考”等表述。

## 3. Workspace 主流程

```text
选择 Workspace
  -> 创建 Session 并选择 Model Profile
  -> 提交任务，Run 进入 FIFO 队列
  -> 执行器获取 Run
  -> 模型通过只读工具收集上下文
  -> 模型单独提出一个写工具或 Shell
  -> 用户查看 diff/命令和权限范围
  -> Approve 或 Reject
  -> Runtime 复检前置条件并执行
  -> Agent 观察结果并继续
  -> 输出最终回答或进入暂停/终态
```

文件修改规则：

- 一次文件写 ToolCall 只操作一个普通文件。
- 创建使用 `write_file`，修改已有文件优先使用 `apply_patch`，明确删除使用 `delete_file`。
- 审批通过后、执行前必须重新验证文件版本。
- `file_version_conflict` 不执行原写入，由 Agent 重新读取并重新申请。

## 4. Public Mode 主流程

```text
创建 Public Session
  -> Agent 在内部 files/ 生成中间文件
  -> 写入仍逐文件审批
  -> Agent 对最终文件调用 publish_artifact
  -> Runtime 创建不可变快照
  -> 右栏展示 Artifact
```

普通写入、中间草稿和临时文件不会自动显示为 Artifact。同一源文件再次发布会产生新版本，不覆盖旧快照。

## 5. Approval 流程

### 5.1 需要审批

- `write_file`
- `apply_patch`
- `delete_file`
- `run_shell`

### 5.2 不需要审批

- 四个只读文件工具。
- `publish_artifact`；它必须独占模型响应，但只写 Eidos 本地 Artifact 索引和快照。

### 5.3 Reject

- Reject 原因作为 ToolCall 结果返回 Agent。
- 连续 Reject 达到 2 次后，Run 进入 `waiting_user_input`。
- 获批状态变更成功或用户补充新指令后，Reject 计数清零。
- 只读调用、模型重规划和失败写入不清零。

## 6. 暂停与恢复

### 6.1 waiting_approval

工具尚未执行。下次启动后继续展示原审批，审批不能改变工具参数，也不能提升权限。

### 6.2 waiting_user_input

由以下条件触发：

- 连续 Reject 2 次。
- 单个 Execution Segment 达到 20 Steps 或 30 分钟。
- Runtime 意外中断。
- 模型在首个 delta 后流式中断。
- 首个 delta 前的瞬时模型故障在自动重试耗尽后仍不可用。
- 模型连续两次返回无效协议响应。
- 执行结果无法确认，需要用户判断。

用户补充信息后，同一 Run 创建新 Execution Segment 并重新进入 FIFO 队列。

### 6.3 stopped

Run 达到 80 Steps 或 120 分钟有效执行时间后进入 `stopped`，不能恢复原 Run。用户可以查看收尾摘要，并基于现状创建新 Run。

## 7. 多 Run 与队列体验

- 用户可以创建和保留多个 Run。
- 任意时刻只有一个 Run 调用模型或执行工具。
- waiting 状态不占执行槽；恢复后追加到队尾。
- 当前 Run 不被新 Run 抢占。
- MVP 支持取消排队 Run，但不支持手动调序和优先级。

## 8. 关闭应用

- 没有运行中 Run 时正常退出 sidecar。
- 有运行中 Run 时，用户选择等待完成或取消后退出。
- 排队、waiting_approval 和 waiting_user_input Run 持久化，下一次启动恢复。
- 意外中断的运行中 Run 不自动重放工具，改为等待用户确认。
