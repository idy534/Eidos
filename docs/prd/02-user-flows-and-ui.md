# 用户流程与界面

版本：v0.4

范围说明：本文描述完整目标态用户体验。第一期只实现 [MVP Lite](../mvp-lite.md) 定义的 Workspace Mode、单活动 Run 和最小 Execution Feed。

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

## 3. Model Profile 流程

- 创建 Profile 只保存 OpenAI-compatible 连接配置和显式 `responses|chat_completions` wire API，不会自动使其可用于 Session。
- 用户必须显式执行 Test Connection；探测不携带用户任务、Session 消息、Workspace 内容或 Artifact 正文。
- 探测必须验证认证、模型存在、streaming、ToolCall 和 usage 契约，并生成版本化 capability snapshot。ToolCall 探测使用一个固定无副作用工具完成受控调用与 ToolResult 续接，不执行真实工具。
- 只有最近一次能力探测成功且存在有效 snapshot 的 Profile 才能被 Session 选择或用于创建新 Run；失败项显示安全的分类结果，不回显 API Key。
- Run 始终使用创建时固化的 Profile 与 capability snapshot。运行期间发现能力漂移时显示结构化模型错误，不静默修改原 Run 或切换 Profile。
- Snapshot 不按时间自动过期，也不在后台自动探测；连接/协议配置变化、Gateway 契约升级或运行中确认能力漂移时失效，重新使用前必须由用户再次 Test Connection。
- 用户可以编辑 Profile。名称等展示字段变化不影响 snapshot；连接、认证、参数和上下文上限变化会立即使 Profile 不可选择，既有 Run 不受影响。
- 用户可以 Archive 和恢复 Profile，但不能物理删除。Archived Profile 不可用于新 Session/Run，历史 Session、Run、Timeline 和 snapshot 保留；恢复后仍需满足有效 snapshot 条件。
- 每个 Profile 独占 API Key 凭证槽位；保存后不回显明文。替换密钥只影响该 Profile，不存在跨 Profile 共享或选择凭证。
- Run 不复制 API Key；既有 Run 的后续模型请求使用该 Profile 凭证槽中的当前密钥。轮换密钥不会修改 Run 的非密钥配置和能力快照。
- 认证模式只提供 `bearer`、`api_key_header` 和 `none`，不接受任意自定义 Header。
- `base_url` 可以指向公网、loopback、局域网或其他私网 HTTP(S) 服务；不允许 URL 内嵌凭证，跨 Origin Redirect 被拒绝。HTTP 端点明确标记为非加密连接。
- `base_url` 是 API 根地址；UI 展示 Adapter 追加后的最终 `/responses` 或 `/chat/completions` URL。用户填写完整 endpoint 时拒绝，不自动去重或猜测。
- HTTPS 始终校验证书、主机名和系统信任链，不提供忽略证书错误的继续入口。
- Provider 扩展参数可以透传，但不能覆盖 Runtime 管理的模型、消息、工具、streaming、认证、传输或输出上限字段。
- `context_window_tokens` 与 `max_output_tokens` 由用户显式填写，Eidos 不按模型名称自动推断；明确的上下文上限不匹配会使 Run 失败并要求修改 Profile 后重新测试。
- HTTP(S) streaming 是必需能力；WebSocket 是可选优化。已确认不支持 WebSocket 的 Profile 仍可通过原 endpoint 的 HTTP(S) 使用，不会因此变为不可选。
- Test Connection 分别展示 usage、ToolCall 分片关联、工具控制/Schema Dialect、无状态 ToolResult 续接和输出 token 字段结果；Chat 只在显式测试中协商兼容字段，Run 不临时试错。

## 4. Workspace 主流程

```text
选择 Workspace
  -> 创建 Session 并选择已通过能力测试的 Model Profile
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
- 每个 Step 的模型请求只包含当时合法可用的工具；事实确认屏障期不暴露副作用工具，Shell capability 不可用时不暴露 `run_shell`。
- 所有继续进入模型上下文的工具结果使用同一结构化 envelope，明确区分成功、错误、跳过、拒绝、中断和不可用。
- Execution Feed 与模型上下文共享同一结果事实，但分别使用安全字段白名单与有界投影；上下文裁剪不会改变工具实际是否完成、工具自身是否截断或是否可能产生副作用。
- Runtime 无法形成合法结构化工具结果时，Run 直接失败并展示 Runtime 契约故障；不发送自由文本替代结果，也不把故障误报为 Provider 或用户输入问题。
- 文件变更成功卡只展示提交后复检的路径、操作和版本事实；no-op 卡明确未审批、未接触文件。Artifact 卡通过稳定 Artifact id 打开，不展示内部 snapshot 路径或模型生成的 URL。
- Shell 结果卡分开展示进程终态、脱敏输出和“执行窗口内观察到”的 Workspace 变化；原始输出长度、完整 manifest、受保护路径名称和 OS 原始错误不进入模型结果。
- `side_effects_may_exist` 表示结果存在未确认副作用，不等于“工具可写”；是否进入事实确认以 outcome/code、对账完整性和安全异常共同决定。

## 5. Public Mode 主流程

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

## 6. Approval 流程

### 6.1 需要审批

- `write_file`
- `apply_patch`
- `delete_file`
- `run_shell`

文件审批卡必须展示 Runtime 根据当前文件和候选内容生成的完整 Diff。超出展示上限时整个操作拒绝，不提供“截断展示仍审批”。

已有文件候选字节与当前版本完全相同时，Execution Feed 显示 `skipped/no_changes`，不创建 Approval。

Shell 审批卡必须说明命令作用于执行时的当前 Workspace，并展示完整 command、PATH/Toolchain Profile、网络权限、timeout 与固定资源上限。用户批准后 5 分钟未开始执行时，授权失效并需重新审批。

### 6.2 不需要审批

- 四个只读文件工具。
- `publish_artifact`；它必须独占模型响应，但只写 Eidos 本地 Artifact 索引和快照。

### 6.3 Reject

- Reject 原因通过扫描且不超过容量边界后，作为封闭 ToolCall data 返回 Agent；未提供原因时不伪造文案。
- 连续 Reject 达到 2 次后，Run 进入 `waiting_user_input`。
- 获批状态变更成功或用户补充新指令后，Reject 计数清零。
- 只读调用、模型重规划和失败写入不清零。

### 6.4 敏感内容

- 用户任务、后续补充和 Approval feedback 等自由文本在提交前扫描；疑似凭证命中 `deny` 或 `redact` 时整次提交拒绝。
- 高置信度内容拒绝、文件名/路径硬拒绝不提供 Approval 绕过。
- 模型和 Shell 普通输出中的疑似凭证在展示、持久化或返回模型前脱敏。
- 写入、Patch、Shell 或 Artifact 发布命中 `deny`/`redact` 时整个操作拒绝，不会将脱敏后的内容静默执行或发布。
- UI 只展示规则 ID/版本、规则集版本、等级、命中数和安全位置，不回显原始命中、长度、摘要或哈希。

## 7. 暂停与恢复

### 7.1 waiting_approval

工具尚未执行。下次启动后继续展示原审批，审批不能改变工具参数，也不能提升权限。

### 7.2 waiting_user_input

由以下条件触发：

- 连续 Reject 2 次。
- 单个 Execution Segment 达到 20 Steps 或 30 分钟。
- Runtime 意外中断。
- 模型在首个 delta 后流式中断。
- 首个 delta 前的瞬时模型故障在自动重试耗尽后仍不可用。
- 模型连续两次返回无效协议响应。
- 模型连续两次提出包含敏感内容的 ToolCall。
- 模型输出被 token 上限、Provider 内容过滤或 Eidos 流式资源上限终止。
- 流式敏感扫描器故障，且存在未确认安全的模型输出。
- Run 固化的 model request contract 实现已不可用；此时原 Run 只能取消或复制原任务创建新 Run。
- Workspace 消失、被替换或身份无法验证；恢复前只能取消，用户显式重新选择并验证原身份后才可继续，旧审批不会恢复。
- 执行结果无法确认，需要用户判断。

用户补充信息后，同一 Run 创建新 Execution Segment 并重新进入 FIFO 队列。

“继续、取消、Approve、Reject”只按 Runtime 返回的当前 `allowed_actions` 渲染。它是界面提示而不是授权；提交时服务端仍以最新状态重新判断。不可恢复的 waiting 原因不显示继续入口，未知原因默认不允许继续。

模型重连期间，Execution Feed 显示有界重试进度和 WebSocket 到 HTTP(S) streaming 的降级；首个 delta 后不显示“正在重连”，而是保留未完成进度并暂停。瞬时错误耗尽终止当前模型请求周期，不直接终止 Run。

模型因输出 token 上限、内容过滤或 Eidos 输出资源上限而停止时，已确认安全的文本保留为未完成进度，任何 ToolCall 都不执行。UI 区分 `model_output_truncated|model_output_blocked|model_output_limit_exceeded`，并允许用户在新 Segment 中要求缩短或拆分输出。

### 7.3 failed

本地预算判断发现不可裁剪输入已经超过可用上下文时，Run 以 `context_input_too_large` 失败，不发送模型请求，也不使 Model Profile 失效。UI 展示估算需求、可用输入预算、输出预留和 safety margin，并引导缩短任务后创建新 Run 或改用更大上下文的 Profile。

### 7.4 stopped

Run 达到 80 Steps 或 120 分钟有效执行时间后进入 `stopped`，不能恢复原 Run。用户可以查看收尾摘要，并基于现状创建新 Run。

## 8. 多 Run 与队列体验

- 用户可以创建和保留多个 Run。
- 任意时刻只有一个 Run 调用模型或执行工具。
- waiting 状态不占执行槽；恢复后追加到队尾。
- 当前 Run 不被新 Run 抢占。
- MVP 支持取消排队 Run，但不支持手动调序和优先级。

## 9. Toolchain Settings

- 默认只使用 macOS 系统工具目录。
- Settings 可检测但不自动启用 `/opt/homebrew` 和 `/usr/local`；用户需逐个确认 Toolchain Profile。
- 已启用根目录被替换时 Profile 自动禁用，不会退化为任意 PATH。
- MVP 不提供用户 Home 工具链或任意目录选择器。

## 10. 关闭应用

- 没有运行中 Run 时正常退出 sidecar。
- 有运行中 Run 时，用户选择等待完成或取消后退出。
- 排队、waiting_approval 和 waiting_user_input Run 持久化，下一次启动恢复。
- 意外中断的运行中 Run 不自动重放工具，改为等待用户确认。

## 11. 启动与页面恢复

- 应用启动时先显示 Runtime 初始化/诊断状态；数据库迁移、契约和安全自检、崩溃对账与队列恢复完成前，不能创建、恢复或审批 Run。
- 同一用户数据目录只允许一个 Eidos Runtime 执行；重复启动激活已有窗口，旧 sidecar 仍在退出时显示有界等待或 `runtime_already_active`，不强制接管。
- Workbench 首次打开或重载时先原子安装当前 RunSnapshot，再从其 Event 水位续接；不得仅靠历史 Event 猜测当前审批和状态。
- pending Approval 摘要必须绑定当前 nonce；完整命令、Diff 和前置条件从权威详情读取，详情过期或被替换时禁用 Approve。
- 列表翻页只透传 Runtime cursor；cursor 失效时从第一页重取，不猜 offset 或排序。旧 UI 遇到明确可忽略的新 Timeline 类型显示安全占位，遇到状态语义不兼容则停止增量并提示升级/重启。
- 存储不可用时 Workbench 进入 Runtime 诊断态，显示安全故障类别和数据目录，允许用户释放空间后显式重新检查；不提供“自动删除旧记录后继续”。
