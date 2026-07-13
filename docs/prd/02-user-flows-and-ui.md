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

Workspace 文件树是面向本机用户的视图，可显示受保护条目并标记不可预览；Agent 的 `list_files` 使用更严格的可见性投影，敏感文件名不进入模型上下文。

## 2. Execution Feed

Execution Feed 合并对话、执行流和 Timeline，至少展示：

- 用户输入与后续补充信息。
- 模型文本、工具调用和最终回答。
- ToolCall 状态、参数摘要和结果摘要。
- Approval 请求、决定、拒绝原因和版本冲突。
- Shell 运行状态、最近输出、输出大小、退出码和终止原因。
- Shell 执行窗口内观察到的文件变化分类、manifest 完整性和资源上限触发原因。
- stdout/stderr 中间省略标记、命令结束后的 tail 回放和断线后一致的持久化回放。
- 中断的模型流保留已显示进度文本，并明确标记“输出未完成”。
- Run 排队、运行、暂停、停止、失败、取消与完成状态。
- Artifact 发布与版本。
- 安全拒绝、事实确认屏障和降级摘要。
- 敏感输入、敏感 ToolCall、扫描资源超限和扫描服务故障的结构化提示。
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
- 文件工具只处理严格 UTF-8 文本；二进制、其他编码和超大文件使用受审批 Shell。
- `write_file` 不隐式创建父目录；目录创建和删除使用受审批 Shell。
- 覆盖已有文件必须先完整读取；Patch 只能修改 Agent 已读取的行区间。
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

MVP Artifact 只支持最大 32 MiB 的严格 UTF-8 文本；二进制、压缩包、加密容器或需专用格式解析才能完成敏感扫描的文件不能发布。

## 5. Approval 流程

### 5.1 需要审批

- `write_file`
- `apply_patch`
- `delete_file`
- `run_shell`

文件审批卡必须展示 Runtime 根据当前文件和候选内容生成的完整 Diff。超出展示上限时整个操作拒绝，不提供“截断展示仍审批”。

已有文件候选字节与当前版本完全相同时，Execution Feed 显示 `skipped/no_changes`，不创建 Approval。

Shell 审批卡必须说明命令作用于执行时的当前 Workspace，并展示完整 command、PATH/Toolchain Profile、网络权限、timeout 与固定资源上限。用户批准后 5 分钟未开始执行时，授权失效并需重新审批。

### 5.2 不需要审批

- 四个只读文件工具。
- `publish_artifact`；它必须独占模型响应，但只写 Eidos 本地 Artifact 索引和快照。

### 5.3 Reject

- Reject 原因作为 ToolCall 结果返回 Agent。
- 连续 Reject 达到 2 次后，Run 进入 `waiting_user_input`。
- 获批状态变更成功或用户补充新指令后，Reject 计数清零。
- 只读调用、模型重规划和失败写入不清零。

### 5.4 敏感内容

- 用户任务、后续补充和 Approval feedback 等自由文本在提交前扫描；疑似凭证命中 `deny` 或 `redact` 时整次提交拒绝。
- 高置信度内容拒绝、文件名/路径硬拒绝不提供 Approval 绕过。
- 模型和 Shell 普通输出中的疑似凭证在展示、持久化或返回模型前脱敏。
- 写入、Patch、Shell 或 Artifact 发布命中 `deny`/`redact` 时整个操作拒绝，不会将脱敏后的内容静默执行或发布。
- UI 只展示规则 ID/版本、规则集版本、等级、命中数和安全位置，不回显原始命中、长度、摘要或哈希。

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
- 模型连续两次提出包含敏感内容的 ToolCall。
- 流式敏感扫描器故障，且存在未确认安全的模型输出。
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

## 8. Toolchain Settings

- 默认只使用 macOS 系统工具目录。
- Settings 可检测但不自动启用 `/opt/homebrew` 和 `/usr/local`；用户需逐个确认 Toolchain Profile。
- 已启用根目录被替换时 Profile 自动禁用，不会退化为任意 PATH。
- MVP 不提供用户 Home 工具链或任意目录选择器。

## 9. 关闭应用

- 没有运行中 Run 时正常退出 sidecar。
- 有运行中 Run 时，用户选择等待完成或取消后退出。
- 排队、waiting_approval 和 waiting_user_input Run 持久化，下一次启动恢复。
- 意外中断的运行中 Run 不自动重放工具，改为等待用户确认。
