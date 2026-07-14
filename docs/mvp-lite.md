# Eidos MVP Lite 范围

版本：v0.1

状态：第一期实现基线

## 1. 文档定位与优先级

MVP Lite 的唯一目标是尽快验证 Eidos 在 macOS 本机上的最小 Agent Runtime 闭环：

```text
创建 Session
  -> 提交一次 Run
  -> 模型输出文本或 ToolCall
  -> Runtime 执行只读工具或请求副作用审批
  -> 用户 Approve / Reject
  -> Runtime 回填 ToolResult 并继续模型循环
  -> 输出最终回答
```

本文是第一期实现范围的最高优先级文档。现有 v0.4 PRD、TDD 和 Q1-Q155 决策继续保留，作为完整目标态与后续加固依据；当它们与本文的首期范围、协议、实体或里程碑冲突时，第一期以本文为准。

MVP Lite 是单用户、单机、前台运行的 Developer Preview，不承诺完整生产级兼容、灾难恢复或跨版本恢复。

## 2. 成功标准

第一期成功必须同时满足：

- Electron 应用能够启动并拉起本地 Python Runtime。
- 用户能够选择一个 Workspace、创建 Session 并提交 Run。
- Runtime 能通过模型完成至少一次“模型 -> 工具 -> ToolResult -> 模型”的循环。
- 只读工具能够自动执行。
- 文件写入和 Shell 在执行前必须展示完整候选操作并等待用户审批。
- 获批操作只能在 Workspace 安全边界内执行；沙箱不可用时 Shell fail closed。
- Execution Feed 能展示用户输入、模型文本、ToolCall、审批和最终结果。
- 用户能够取消正在运行或等待审批的 Run。
- 应用重启后能够读取已完成的 Session、Run 和 Item；未完成 Run 不自动恢复或重放。

以下能力不作为第一期成功条件：Public Mode、Artifact、多 Run 队列、跨重启继续执行、双模型协议、WebSocket、复杂模型能力探测、完整敏感信息治理和生产级存储恢复。

## 3. 产品范围

### 3.1 第一期必须支持

| 领域 | MVP Lite 范围 |
|---|---|
| 平台 | 仅 macOS；Electron + React + Python Runtime |
| 模式 | 仅 Workspace Mode |
| Agent | 固定内置 Eidos Agent，不提供 Agent 管理 |
| 会话 | 创建和读取 Session |
| 执行 | 每个 Session 可创建多个历史 Run，但全应用任意时刻最多一个活动 Run |
| 模型 | 固定 DeepSeek `deepseek-v4-flash`；OpenAI-compatible Chat Completions HTTP(S) SSE streaming |
| Runtime | 串行 ReAct loop；最多 20 个模型 Step |
| 只读工具 | `list_files`、`read_file`、`search_text` |
| 文件写工具 | `write_file`、`apply_patch`；单 ToolCall 单文件 |
| Shell | `run_shell`；每次调用审批并在 Seatbelt `workspace_write` 中运行 |
| 审批 | 文件写入展示完整 diff；Shell 展示完整 command、cwd、网络状态和 timeout |
| Feed | 用户消息、模型进度/最终回答、ToolCall、审批、输出摘要和终态 |
| 持久化 | Session、Run、Item、ToolCall 的最小 SQLite 持久化 |
| 控制 | Cancel、固定超时、输出上限和 Runtime 退出清理 |

### 3.2 明确延后

- Public Mode、`publish_artifact` 和不可变 Artifact 快照。
- 多 Run 持久 FIFO、优先级、手动调序和并行执行。
- `ExecutionSegment`、80 Steps/120 分钟硬上限、`stopped` 和 Finalization Call。
- `read_file_range`、`delete_file`、Regex Search 和额外文本编码。
- pending Approval、模型流和工具执行的跨重启恢复。
- Durable Intent、事实确认屏障、Workspace 前后 manifest 和副作用自动对账。
- Responses Adapter、WebSocket、传输降级和多 Provider 自动兼容。
- Model Profile 编辑、Archive、凭证轮换、两阶段 Capability Probe 和 capability snapshot。
- `model_request_contract_version`、`tool_contract_version` 的旧版本路由与恢复。
- Canonical JSON、immutable base ToolResult projection 和 ToolResult quarantine。
- managed network proxy、域名级网络审批和 localhost 独立授权。
- Toolchain Profile、Shell guardian、进程树 RSS/fd/fork/allocated-block 精确监管。
- 全入口敏感扫描、跨 chunk 规则引擎、历史数据重扫和规则集升级。
- 全局 operation 幂等、稳定 keyset pagination、Event 前向兼容和 RunSnapshot 水位协议。
- 数据库迁移备份、emergency reserve、storage health-only 灾难恢复。
- ACL、xattr、flags 的完整保留；遇到 MVP Lite 不支持的复杂文件元数据时 fail closed。
- 内嵌 Terminal、后台 daemon、跨平台、多 Agent、MCP、Skill、浏览器和企业能力。

## 4. 首期架构

```text
React Renderer
  -> typed Preload API
  -> Electron Main
  <-> stdio JSON-RPC
  -> Python Runtime
       -> Model Client / DeepSeek Chat Completions HTTP SSE
       -> Runtime Loop
       -> Tool Registry
       -> Approval Gate
       -> Workspace Guard
       -> Seatbelt Shell
       -> SQLite
```

### 4.1 进程职责

Renderer：

- 只处理用户交互和受控展示。
- 不知道 Runtime 凭证，不直接访问本地文件或子进程 stdin/stdout。
- 只能调用 Preload 暴露的命名方法。

Electron Main：

- 创建并管理 Python Runtime 子进程。
- 独占 Runtime stdin/stdout，将 JSON-RPC 请求、响应和通知映射为类型化 Preload API。
- 负责文件夹选择、打开系统 Terminal 和应用生命周期。
- 不执行 Agent Shell，不把任意 IPC channel 暴露给 Renderer。

Python Runtime：

- 拥有 Session、Run、Item/ToolCall 状态和 SQLite。
- 调用模型、校验 ToolCall、执行工具并发起审批请求。
- 将模型、工具和状态变化以 JSON-RPC notification 推送给 Main。
- 仅将协议消息写入 stdout；所有诊断日志写入 stderr。

### 4.2 为什么第一期使用 stdio JSON-RPC

- Main 是 Runtime 的唯一父进程和客户端，不需要随机端口、Bearer Token 或 loopback 路由。
- 同一连接原生支持 Main 发请求、Runtime 发通知，以及 Runtime 主动发起审批请求。
- 不需要为第一期维护 REST、SSE、OpenAPI、HTTP DTO、IPC DTO 四套边界。
- stdout 可作为纯协议通道，stderr 可作为独立诊断通道。

MVP Lite 不开放 TCP、Unix Socket 或 WebSocket 监听。未来需要多客户端或远程 Runtime 时，再在同一领域契约外增加 transport adapter。

## 5. stdio JSON-RPC 双向协议

### 5.1 Framing 与通用规则

- 使用 JSON-RPC 2.0 envelope。
- stdin/stdout 使用 UTF-8 JSONL；每行恰好一个完整 JSON object。
- request/response 使用匹配的字符串 `id`；Main 发起的 id 固定以 `client-` 开头，Runtime 发起的 id 固定以 `server-` 开头，notification 不包含 `id`。
- 单条协议消息最大 1 MiB；大正文通过有界 Item delta 分块，不把文件或 Shell 全量日志塞入单条消息。
- stdout 禁止日志、banner、traceback 和其他非协议文本。
- stderr 日志不得包含 API Key、Authorization Header、完整文件正文或未脱敏 Shell 输出。
- Main 遇到非法 JSON、未知 response id 或 envelope 校验失败时终止当前 Runtime，并把活动 Run 标记为 interrupted。
- Runtime 对未知 method 返回标准 `-32601 Method not found`；参数校验失败返回 `-32602 Invalid params`。

### 5.2 初始化

Main 启动 Runtime 后必须先发送：

```json
{"jsonrpc":"2.0","id":"client-1","method":"initialize","params":{"client":{"name":"eidos-desktop","version":"0.1.0"},"protocolVersion":1}}
```

Runtime 完成 SQLite 初始化、工具注册和 Seatbelt 自检后返回：

```json
{"jsonrpc":"2.0","id":"client-1","result":{"protocolVersion":1,"runtimeVersion":"0.1.0","capabilities":{"runShell":false}}}
```

初始化完成前除 `initialize` 和 `runtime/shutdown` 外不接受业务请求。`runShell=false` 允许只读和文件工具闭环继续运行，但不得降级为无沙箱 Shell。

### 5.3 Main 发起的请求

| Method | 作用 | 结果 |
|---|---|---|
| ✅ `initialize` | 建立协议版本和能力握手 | Runtime 版本与 capability |
| ✅ `session/create` | 绑定 Workspace 并创建 Session | Session |
| ✅ `session/list` | 分页读取已有 Session 摘要 | `{items,nextCursor?}` |
| ✅ `session/read` | 读取 Session、历史 Run 与有界 Item 页面 | SessionSnapshot |
| ✅ `run/start` | 提交用户输入并开始一个 Run | 初始 Run |
| ✅ `run/cancel` | 取消活动或等待审批的 Run | 最终/当前 Run |
| ✅ `model/status` | 读取不含凭证明文的固定模型配置状态 | ModelStatus |
| ✅ `model/configure` | 保存 DeepSeek API Key；响应不回显凭证 | ModelStatus |
| ✅ `runtime/shutdown` | 有界停止 Runtime | 空结果 |

`session/list` 使用 `limit=50`、最大 200 和可选 opaque cursor；第一期不承诺跨页冻结成员集合，客户端遇到 cursor 失效时从第一页重取。`session/read` 使用 `itemLimit=200`、最大 500 和可选 `beforeItemId` 向前读取历史 Item。

`run/start` 只接受 `sessionId` 和 `userInput`。若已有活动 Run，固定返回 `RUN_ALREADY_ACTIVE`；MVP Lite 不隐式排队。

业务错误统一使用 JSON-RPC error，并保持一个闭合结构：

```json
{
  "jsonrpc":"2.0",
  "id":"client-2",
  "error":{
    "code":-32000,
    "message":"Request failed",
    "data":{"code":"RUN_ALREADY_ACTIVE","retryable":false}
  }
}
```

`error.message` 使用稳定安全文案；Renderer 只按 `error.data.code` 映射本地化提示和可用动作。第一期业务 code 固定为 `RUNTIME_NOT_INITIALIZED`、`PROTOCOL_VERSION_UNSUPPORTED`、`RUN_ALREADY_ACTIVE`、`RESOURCE_NOT_FOUND`、`INVALID_STATE`、`APPROVAL_NO_LONGER_PENDING`、`WORKSPACE_BOUNDARY_VIOLATION`、`SANDBOX_UNAVAILABLE` 和 `INTERNAL_ERROR`。Python traceback、Provider 原始错误和 OS 原始错误不得进入 error data。

### 5.4 Runtime 发起的请求

副作用 ToolCall 通过 Runtime 主动请求 Main：

```json
{
  "jsonrpc":"2.0",
  "id":"server-approval-uuid",
  "method":"item/requestApproval",
  "params":{
    "sessionId":"...",
    "runId":"...",
    "itemId":"...",
    "toolCallId":"...",
    "kind":"file_change",
    "summary":"Modify src/app.ts",
    "diff":"..."
  }
}
```

Main 只能返回：

```json
{"jsonrpc":"2.0","id":"server-approval-uuid","result":{"decision":"approve"}}
```

或：

```json
{"jsonrpc":"2.0","id":"server-approval-uuid","result":{"decision":"reject","feedback":"optional"}}
```

审批响应不能修改工具参数、diff、command、cwd、网络权限或 timeout。Run 被取消、Runtime 退出或请求 id 不再有效时，迟到响应必须被忽略。

### 5.5 Runtime 通知

| Method | 语义 |
|---|---|
| `run/started` | Run 开始执行 |
| `item/started` | 一个用户消息、模型消息或 ToolCall Item 开始 |
| `item/delta` | 模型文本或 Shell 输出的有界增量 |
| `item/completed` | Item 的权威终态与安全结果 |
| `run/completed` | Run 进入终态 |

每个 Item 生命周期固定为：

```text
item/started -> 0..N item/delta -> item/completed
```

UI 必须以 `item/completed` 作为工具、文件修改和命令执行的权威结果；delta 只用于实时展示。

## 6. 首期领域模型

```text
Session
  └── Run
        └── Item 1..N
              └── ToolCall 0..1
```

### 6.1 Session

Session 表示一个绑定 Workspace 的持续对话上下文：

- 固定 `id, workspace_root, created_at, updated_at`。
- 一个 Session 包含按创建顺序排列的多个 Run。
- Model Profile、Agent 和 Workspace 在 MVP Lite 中创建后不可切换；需要变化时创建新 Session。

### 6.2 Run

Run 表示一次用户输入触发的完整 Agent 执行：

- 一个 Run 只有一个初始 `userInput`。
- 模型、工具和审批都在同一 Run 内循环。
- 用户在 Run 结束后补充信息时创建新 Run，不恢复旧 Run，不创建 Execution Segment。
- 全应用任意时刻最多一个活动 Run。

Run 状态固定为：

```text
running
waiting_approval
succeeded
failed
canceled
interrupted
```

合法流转：

```text
running -> waiting_approval -> running
running|waiting_approval -> canceled
running -> succeeded|failed
running|waiting_approval -> interrupted
```

`interrupted` 是 Runtime 进程退出、协议损坏或应用异常关闭后的终态，不能原地恢复。用户可以在同一 Session 创建新 Run，模型上下文只使用已持久化完成的 Item。

### 6.3 Item 与 ToolCall

Item 是 Feed 和模型历史中的顺序事实，使用闭合 tagged union：

```text
user_message
assistant_message
file_change
command_execution
tool_call
```

Item 公共字段：

```text
id, session_id, run_id, kind, status, created_at, completed_at?
```

Item 状态固定为：

```text
in_progress|completed|failed|declined|canceled
```

ToolCall 是 Item 的执行详情，不增加新的层级：

```text
id, item_id, tool_name, arguments_json, result_json, started_at, completed_at
```

模型输出中的工具参数始终按不可信输入处理。Runtime 在创建 ToolCall 前完成工具名、闭合参数 schema、Workspace 边界和组合规则校验；工具是否向模型暴露不构成执行授权。

## 7. Runtime Loop

```text
persist user_message Item
  -> build bounded model input from completed Items
  -> stream DeepSeek Chat Completions API
  -> no ToolCall: complete assistant_message and Run succeeded
  -> ToolCall: validate complete response
       -> read tool: execute directly
       -> file/shell tool: create Item and request approval
       -> append bounded ToolResult
  -> next model Step
```

固定规则：

- 每个 Run 最多 20 个模型 Step；达到上限直接 failed，不执行 Finalization。
- ToolCall 只有在模型响应完整结束且参数完整解析后才能执行。
- 只读 ToolCall 可以在同一响应中出现多个，但按声明顺序串行执行。
- `write_file`、`apply_patch` 或 `run_shell` 必须是响应中唯一 ToolCall。
- 非法组合整批零执行，并向模型返回一个有界协议错误；连续第二次非法响应使 Run failed。
- 不保存或展示 raw reasoning；模型普通文本只分为 progress 和 final answer。
- 不做智能 compaction；输入超过配置窗口时 Run failed，并提示用户创建新 Session。

## 8. 工具与安全底线

### 8.1 文件工具

- 所有路径必须是 active root 下的相对路径。
- 拒绝绝对路径、`..`、symlink 逃逸、特殊文件和 `.git` 内部写入。
- `list_files` 不跟随 symlink，并使用固定深度与条目上限。
- `read_file` 只读取有界的严格 UTF-8 普通文件。
- `search_text` 仅支持 literal search，并限制扫描字节、时间和结果数。
- `write_file` 用于新文件或完整内容写入；覆盖已有文件前必须完整读取同版本文件。
- `apply_patch` 是修改已有文件的默认工具；只接受单文件 strict unified diff。
- 文件审批展示 Runtime 根据当前文件与候选内容生成的完整 diff；diff 超限时拒绝操作。
- Approve 后、写入前重新校验目标 hash；变化时返回 `file_version_conflict`。
- 使用同目录临时文件、fsync 和原子 replace；不支持的 ACL/xattr/flags 文件直接拒绝。

### 8.2 Shell

- 每次 `run_shell` 都必须审批，不提供 session 级永久放行。
- 命令在 `/usr/bin/sandbox-exec` Seatbelt `workspace_write` 中运行。
- active root 可写；`.git`、`~/.eidos` 和其他用户数据路径不可写。
- 默认禁止网络、localhost 和 Unix Socket；MVP Lite 不提供单次放行。
- 使用干净的临时 HOME，不加载用户 rc，不继承宿主凭证环境变量。
- 默认 timeout 120 秒，最大 600 秒；stdout/stderr 合计持久化上限 1 MiB。
- Cancel 或 timeout 向进程组发送 SIGTERM，短暂宽限后 SIGKILL。
- Seatbelt 策略、自检或进程组清理不可用时 `run_shell` capability 为 false。

### 8.3 最小敏感边界

- API Key 只存在于 mode 0600 的本地配置，不进入 SQLite、stdout、Item 或 stderr。
- 固定拒绝读取常见凭证文件和 `~/.ssh`、`~/.aws`、`~/.config` 等 active root 外路径。
- Shell 不继承 API Key、SSH agent、云凭证和真实 HOME。
- MVP Lite 不承诺内容级 Secret 检测；UI 必须明确这是 Developer Preview 限制。

## 9. 最小持久化

SQLite 只要求四类业务表：

| 表 | 作用 |
|---|---|
| `sessions` | Workspace 与会话元数据 |
| `runs` | 用户输入、状态、错误和时间 |
| `items` | Feed 与模型历史中的顺序事实 |
| `tool_calls` | 工具参数、审批决定与安全结果 |

MVP Lite 不单独建立 Segment、Attempt、Approval、Event、Operation、Manifest、Capability Snapshot 或 Quarantine 表。审批事实作为对应 ToolCall/Item 的字段保存，协议 notification 不作为数据库事实来源。

持久化规则：

- 领域状态和对应 completed Item 在同一 SQLite 事务提交。
- 模型 delta 可以按固定 100ms/4 KiB 合并写入当前 assistant Item。
- 文件系统与 SQLite 不宣称原子事务；文件写入完成但结果未提交时，重启后只把 Run 标记 interrupted，不自动重放或自动推断成功。
- 启动时将所有 `running|waiting_approval` Run 标记为 interrupted，并清除未完成的审批请求。
- SessionSnapshot 从规范化表读取，不通过通知回放重建状态。
- 第一版 schema 只支持新库初始化；正式分发产生真实用户数据前再引入 Alembic migration 与备份策略。

## 10. MVP Lite 里程碑

### L0：进程与协议闭环 ✅

- ✅ Electron Main 拉起 Python Runtime。
- ✅ stdio JSON-RPC initialize、shutdown 和 stderr 日志隔离。
- ✅ Renderer 通过 Preload 显示 Runtime ready/error。

退出标准：✅ 已通过自动化与 macOS 实机验证。应用可稳定启动和关闭，stdout 无非协议输出，非法协议能安全终止 Runtime。

### L1：模型与只读闭环

- ✅ Session、Run、Item、ToolCall 最小 SQLite 持久化；Item 历史有界分页；启动时未完成 Run 收敛为 `interrupted`。
- ✅ 确定性 Fake Model 已跑通“模型 -> `read_file` -> ToolResult -> 模型最终回答”的两轮循环。
- ✅ DeepSeek `deepseek-v4-flash` Chat Completions SSE Adapter 已实现；API Key 以 `0600` 保存且不进入响应、SQLite 或日志；尚待用户在界面配置真实 Key 后完成联网验收。
- ✅ `list_files`、`read_file`、`search_text` 已实现有界执行、敏感路径拒绝、symlink/root rebinding 防护和取消。
- ✅ Item 生命周期通知、Main 协议校验和基础 Execution Feed 已实现。
- ✅ Renderer 已支持选择 Workspace、创建/读取 Session、配置模型、提交/取消 Run 与展示流式结果。

退出标准：⏳ 代码与离线闭环已完成；仍需使用真实 DeepSeek Key 完成一次“读取工具 -> ToolResult -> 最终回答”的联网验收。

### L2：文件写入审批闭环

- ✅ `write_file`、`apply_patch` 已实现闭合参数校验、单文件/256 KiB 上限与已有文件同 Run 读取证据。
- ✅ Runtime 从冻结候选内容生成完整 diff；控制字符、symlink、硬链接、复杂 flags/xattr 和敏感路径 fail closed。
- ✅ 双向 `item/requestApproval`、Approve/Reject、取消与迟到响应竞态已实现。
- ✅ 批准提交使用 Seatbelt 内的 `RENAME_EXCL`（新文件）或 `RENAME_SWAP + 旧 hash 校验/冲突回滚`（已有文件），并 fsync、读回验证；post-commit 不确定性明确标记 `sideEffectsMayExist=true`。
- ✅ 完成通知不重复大体积 arguments/diff，保持在 1 MiB JSON-RPC 上限内。

退出标准：✅ 自动化测试已覆盖 Approve 按 diff 修改、Reject 零修改、审批等待期间版本冲突、CAS 回滚、symlink/root rebinding、取消和迟到 Approve。

### L3：开发者可用闭环

- ✅ Seatbelt `run_shell`、逐次命令审批、默认断网、干净 HOME/环境、256 KiB 有界输出、timeout 和进程组取消。
- ✅ Workspace 主界面与历史 Session/Run 读取。
- ✅ Runtime 异常退出统一标记 `interrupted`，不自动重放。
- ✅ 首期端到端测试与 Developer Preview 限制说明。

退出标准：⏳ 离线 Fake Model 与真实 Seatbelt 闭环已完成；仍需用户在界面配置真实 DeepSeek Key 后完成一次“阅读代码 -> 审批修改 -> 审批执行测试 -> 最终回答”的联网验收。

L3 前置风险验证状态：

- ✅ Seatbelt 使用静态 profile 和 `-D` 路径参数，不从 PATH 解析 `sandbox-exec`。
- ✅ macOS 实机 smoke test 已覆盖 Workspace/Home/Temp 创建修改删除、外部与敏感路径拒绝、`.git` 只读、symlink 逃逸、子进程继承、loopback 拒绝和基础进程组 timeout。
- ✅ Runtime initialize 会执行 Seatbelt 自检；失败保持 fail closed，且在完整 `run_shell` 落地前即使自检通过也保持 `runShell=false`。
- ✅ Shell Approval、默认断网、输出上限、timeout/cancel 与真正的 `run_shell` ToolCall 已实现。
- ⏳ manifest、完整 RSS/fd/fork 资源监管、managed network 继续按 MVP Lite 延后，不阻塞 Developer Preview。

## 11. 第一期开工门槛

- [ ] stdio JSON-RPC envelope、method、DTO 和错误码形成固定 v1 测试向量。
- [x] `Session -> Run -> Item/ToolCall` 数据模型没有 Segment、Attempt 或独立 Event 依赖。
- [x] Runtime Loop 能在 fake model 与真实只读工具上完成至少两轮循环。
- [x] Workspace Guard 和 Seatbelt 在目标 macOS 版本完成实机 smoke test。
- [x] 文件写入 diff 与 hash 复检有独立测试。
- [x] 审批请求、取消和迟到响应的竞态有集成测试。
- [ ] stdout/stderr 隔离、消息大小和慢消费者行为有协议测试。
- [x] PRD/TDD 后续实现任务明确标注 `MVP Lite` 或 `完整目标态`，不再混用 P0。
