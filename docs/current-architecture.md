# Eidos 当前架构

本文只描述当前代码中的系统结构和职责。生产代码和自动化测试是本文的事实来源。本文不描述历史 Phase，也不描述尚未接入默认 Run 的目标态设计。

## 1. System Boundary

Eidos 是一个面向 macOS 的本地桌面 Agent Runtime。系统包含 Electron Desktop、Python Runtime、SQLite、本地 Workspace 和远端 Model Provider。

Renderer 是展示边界。Renderer 不能直接访问 Node 系统能力，也不能直接连接 Python Runtime。Electron Main 是 Desktop 和 Runtime 之间的控制边界。Python Runtime 是 Session、Run、Item、ToolCall、Approval、事件和副作用状态的业务协调边界。

远端模型只提供不可信的文本和 ToolCall 提案。Runtime 负责验证模型输出、构建 Context、执行 Tool、处理 Approval、应用 Sandbox Policy 和提交持久事实。

## Workspace model

Eidos 把用户选择的目录建模为 Project。Project 的基础事实是 `name` 和 `workspace_root`。Project 可以有 Git capability，但 Git 不是 Project 存在的前提。Project 由 `project/create` 显式写入 SQLite，不依赖 Session 创建。`name` 可以省略，Runtime 会使用 canonical Workspace 的文件夹名。

```text
Project
 ├── workspace_root
 └── optional Git capability
          └── Thread / Session
                    ├── Local Execution → Project.workspace_root
                    └── Worktree Execution → Managed Worktree → Run
```

Non-Git Project 只能创建 Local Execution Session。Git Project 可以创建 Local 或 Worktree Execution Session。Desktop 的新建会话入口先保留本地草稿，首次提交时才物化 Session。Local Session 的 `execution_mode` 是 `local`，`worktree_id` 是 NULL，Run 直接使用 `Project.workspace_root`。Worktree Session 的 `execution_mode` 是 `worktree`，它绑定 Managed Worktree，Run 使用该 Worktree root。Git Project 的 Session Git status 和 diff API 会读取当前 execution root。

文件读写、Shell、Skill、MCP、Context、Long Task、Sandbox 和 Checkpoint 属于 Workspace 或 Runtime 能力。它们不因为 Project 没有 Git 而失效。Git status 和 Git diff 在 Git Project 的当前 execution root 上提供。Managed Worktree 和 Git-based Fork 仍然只在 Git Project 中提供。

Desktop Workspace Explorer 也只读取当前 Session execution root。`WorkspaceExplorerApplication` 先解析 Local root 或验证 Managed Worktree identity，再调用共享 `WorkspaceReader`。`WorkspaceReader` 与 Agent 文件工具共用 fd-relative、`O_NOFOLLOW`、敏感路径、hard discovery directory 和 root ignore 规则。`workspace/listDirectory` 只返回一层子项。`workspace/readFilePreview` 只返回有界 UTF-8 预览。Renderer 不直接读取 filesystem。

Desktop 会让 Conversation 始终保持挂载。用户可以从 Session header 右上角唯一的按钮打开或关闭右侧 Workspace Dock。Dock 使用本地 Renderer 状态管理 Review、Terminal 和 Files Tab。Review 和 Files 各只有一个工具 Tab，Terminal 可以同时打开多个 Tab。Files Tab 内可以同时预览多个文件。文件 Tab、当前路径和文件大小共享一条预览栏，侧栏布局默认给预览区更多空间。Dock 支持 Tab 切换、关闭、空状态选择工具、全侧栏展开和关闭。Dock 与 Conversation 之间的分隔条可以拖动调整宽度。Files 的文件树与预览区也有独立的可拖动分隔条。Dock 关闭时，Conversation 内容在可用宽度内居中。Session 或 execution binding 变化时，Renderer 会关闭旧 Dock，并用新的 execution key 重新加载 Workspace 数据。

## 2. Process Architecture

当前默认调用链如下：

```text
Renderer
  ↓ typed IPC through context-isolated preload
Electron Main
  ↓ JSON-RPC 2.0 / JSONL over stdio
RuntimeServer
  ↓ typed Method Registry and Application boundaries
RunSupervisor
  ↓ FIFO execution slot and Run worker control
RuntimeEngine
  ↓ Model Attempt / ToolCall / finalization coordination
SamplingRuntime / Model Gateway
ToolCallRuntime / ToolExecutionController
  ↓
Tool handlers / ToolOrchestrator / Seatbelt / Process / Filesystem
  ↓ SQLite transaction
Event / Outbox
  ↓ JSON-RPC notification
Electron Main → Renderer
```

Python Runtime 的 stdout 只输出有界的 JSON-RPC 行。Runtime 日志写入 stderr 或受控本地日志。RuntimeServer 的 `initialize`、`runtime/health` 和 `runtime/shutdown` 负责握手、健康状态和有序关闭。

`runtime/eidos_runtime/runtime/loop.py` 只导出 `RuntimeEngine` 的兼容名称。它不是当前 Runtime Loop 的第二套实现。

## 3. Desktop Boundary

Renderer 通过 context-isolated preload 使用 typed IPC。Preload 使用 `contextBridge` 暴露受限的 `window.eidosRuntime` API。Renderer 不直接读取 Runtime stdout，也不直接访问文件系统、子进程和 API Key。

Electron Main 负责以下职责：

- 创建启用 `contextIsolation`、禁用 `nodeIntegration` 和启用 sandbox 的 BrowserWindow；
- 解析开发 Runtime 和打包 Runtime 的路径；
- 启动、读取和关闭 Python Runtime 子进程；
- 校验 JSON-RPC response、notification 和协议行大小；
- 转发 Session、Run、Context Usage、Model、Extension 和 Approval 的 typed API；
- 将 Runtime 主动发起的 Approval 请求投影到 Renderer；
- 为用户直接操作的 Terminal 创建和回收 Main-owned PTY；
- 在 Quit 时取消活动 Run，等待 Runtime 收敛，并在必要时报告关闭失败。

Response Action 使用同一条 typed IPC 边界。Main 把 `responseAction/state`、`item/setFeedback` 和 `run/revise` 映射到 Runtime。

Terminal 不经过 Python Runtime，也不是 Agent Shell Tool。Renderer 只通过 typed IPC 发送 Session id、输入和尺寸。Main 会重新读取 Session，并从 Local workspace 或 active Managed Worktree 得到权威 cwd。Main 使用 `/bin/zsh -l` 和受限环境创建 PTY。每个 PTY 绑定创建它的 WebContents。Main 会在窗口销毁、Session Handoff、Worktree Restore、Session Delete 和应用退出时终止对应 PTY。Projectless Session 会在 Renderer 和 Main 两层拒绝 Terminal。

## 4. Runtime Transport & Application Layer

`RuntimeServer` 维护 SQLite Store、ModelConfigStore、RunSupervisor、RuntimeAsyncKernel 和 Method Registry。Method Registry 负责方法注册、typed request/response 校验、重复注册检查和稳定错误映射。

应用层为 Session、Run、Response Action、Model、Extension、Repository、Context、Checkpoint 和 Long Task 提供边界。RuntimeServer 仍保留少量兼容入口，但它不会建立第二套状态权威。

`ResponseActionRuntimeServer` 在默认 `__main__` 入口上扩展 Response Action 方法。它与基础 RuntimeServer 共用初始化、健康检查、关闭、Supervisor 和 SQLite。

## 5. Run Orchestration

RunSupervisor 负责持久 FIFO、全局 Execution Slot、Run Worker、Approval 等待、取消、暂停、恢复和关闭收敛。多个非终态 Run 可以同时存在，但同一时刻只有一个 Run 占用 Execution Slot。等待 Approval 的 Run 会停放并释放 Execution Slot，Supervisor 可以继续调度下一个排队 Run；等待 Slot 的 Worker 也不会形成第二个执行权威。

RunSupervisor 把同步 Durable Runtime Core 与进程级 `RuntimeAsyncKernel` 连接起来。RuntimeAsyncKernel 持有一个 AnyIO Blocking Portal。Model 异步 I/O、MCP Connection、Managed Task 和安全只读并行批次通过这个 Kernel 执行。Run Worker 仍是当前 Run 的同步控制边界。

RuntimeEngine 在每个模型 Step 前生成不可变的 Step Resolution、Rule、Context、Model 和 Tool 快照。它按以下顺序协调一次循环：

```text
consume input → resolve rules → build context → sample model
→ validate text and ToolCalls → execute or finalize
→ commit result and facts → inspect cancellation, context and progress
→ continue, compact, recover, pause or finish
```

RuntimeEngine 下的主要职责是：

- `ContextBuilder` 和 `InstructionResolver` 构建本次模型请求；
- `ProjectRuleResolver` 固化项目规则快照；
- `SamplingRuntime` 和 Model Gateway 执行一次模型请求；
- `ToolCallRuntime`、`ToolExecutionController` 和 `ToolOrchestrator` 处理 Tool；
- `ApprovalCoordinator` 处理可持久化的 Approval 暂停和恢复；
- `ContextCompactor` 处理 Context 压力；
- `LoopGuard` 处理语义状态收敛；
- `RunFinalizer` 生成有界、无 Tool 的最终回答。

Model Step、Segment Step 和 effective time 在当前实现中是 telemetry 和 operational segment 信息。健康 Run 不会因为固定 model-step、Run duration 或固定 repeated-call counter 自动终止。Segment 达到 operational quantum 时可以 rollover，但 rollover 不是 Run 终态。

Chat Completions Adapter 会把模型文本归一化为 `commentary`、`final_answer` 或 `unknown`。Runtime 私有的最终答复标记只属于 Adapter 兼容协议。Adapter 会在持久化和展示前移除该标记。RuntimeEngine 只读取结构化阶段。没有 ToolCall 的文本只有在阶段是 `final_answer` 时才能完成 Run。`commentary` 和 `unknown` 会在同一个 Step 内触发一次有界协议修复。重复失败会以 `MODEL_PROTOCOL_ERROR` 结束，未声明的文字不会进入 Conversation。

RunFinalizer 使用同一套结构化阶段判断。Finalizer 会对 `commentary` 和 `unknown` 执行一次有界协议修复。两次响应都不满足终态契约时，Finalization Attempt 会记录 `finalization_protocol_error`，无效文本不会创建 Assistant Item。空响应、ToolCall 和 Provider 控制文本保留原有处理。Finalizer 的总 timeout 同时约束初次请求和协议修复请求。

## 6. Context & Instructions

默认在线 Run 使用 `ContextBuilder` 从 SQLite 事实、当前 Run、Model Profile、Rule Snapshot、Selected Skill、历史 Item、Tool Result、Workspace State 和额外事实构建模型 Context。

`ProjectRuleResolver` 从 Workspace root 到 effective cwd 逐层读取每个目录中优先级最高的非空候选文件。候选顺序是：

```text
EIDOS.override.md
EIDOS.md
AGENTS.override.md
AGENTS.md
CLAUDE.md
```

每层只选择一个候选。Resolver 使用共享的 32 KiB UTF-8 byte budget。Resolver 记录 shadowed candidate、读取或预算 warning、原始 content hash、实际包含字节数、directory level、Workspace root 和 effective cwd。Run 使用 immutable rule snapshot，Step Resolution 保存 resolved instruction hash 和 effective cwd。

`InstructionResolver` 按 System Safety、Base Agent、Runtime Policy、Project Rules 和 Selected Skill 形成分层 instructions。Project Rules 和 Selected Skill 保留来源与 hash。它们不具备修改 Runtime Permission、Approval 或 Sandbox 的权限。

Context Budget 优先使用最近 Provider Usage 的 active input tokens。Provider Usage 不可用时，Runtime 使用标记为 `estimated` 的有界估算。Context pressure、Provider `context_exceeded` 和 projection overflow 会触发 deterministic bounded compaction 或一次安全恢复。没有新的可压缩历史或 Context 投影没有进展时，Run 以 `context_still_over_budget` 停止。

当前默认 compactor 先生成确定性的有界候选摘要。候选摘要不会直接成为模型事实。Runtime 会从 SQLite 重载 Item、Tool Result、Workspace change、Approval 和 reconciliation 事实，再执行 `ContextCompactionVerifier`。Tool provenance 由候选摘要的 source Item IDs 解析到真实 ToolCall IDs，所以 pre-turn compaction 可以准确引用以前 Run 的 Tool facts，也不会附加无关 ToolCall。只有验证通过的 `VerifiedCompactSummary` 才会在同一个事务中写入权威 `compact_summaries`、增加计数并产生一条 `context.compacted` Event。原始历史仍保存在 SQLite。验证失败会保留上一份 verified summary。

## 7. Model Gateway

ModelConfigStore 使用受保护的 `models.json` 保存本地配置。默认位置是 `~/.eidos/models.json`，显式数据目录会改变 Runtime-owned 数据位置。文件按 owner-only 权限保存。

API Key 会经过本地模型配置写入链路：Renderer 通过 typed IPC 把配置交给 Electron Main，Main 再通过 `model/create` 或 `model/update` JSON-RPC request 把 `apiKey` 发送给 Runtime。Runtime 将 Key 写入受保护的 `models.json`。Key 不应出现在模型列表/读取响应、SQLite、Event/Execution Feed 或正常日志中。

当前 Model Catalog 只包含以下 Provider 和 Model ID：

```text
deepseek: deepseek-v4-pro, deepseek-v4-flash
minimax:  MiniMax-M3
kimi:    kimi-k3, kimi-k2.7-code-highspeed
volcengine: deepseek-v4-pro-ga-260813, deepseek-v4-flash-ga-260731,
            glm-5-2-260617, doubao-seed-evolving,
            doubao-seed-2-1-pro-260628, doubao-seed-2-1-turbo-260628,
            doubao-seed-2-0-code-preview-260215
```

ModelConfigStore 要求配置与内置 Catalog 严格匹配。当前 wire API 只有 OpenAI-compatible Chat Completions。Runtime 使用 Pydantic AI 的 Model API、Provider 和流式请求边界。Runtime 不提供 arbitrary custom provider、arbitrary base URL、arbitrary model ID 或 Responses API。

每个 Run 固化 Model Profile 和 Extension Snapshot。Model Lease 使用该快照创建 Provider Client。Model Attempt 保存 usage、响应元数据、有限的 transport retry 诊断和稳定 Eidos 错误码。Model Client 不拥有 Runtime Event Loop；共享 RuntimeAsyncKernel 负责其异步 I/O。

## 8. Tool Execution

Tool Registry 保存 ToolSpec、输入和结果 Schema、Execution Policy、Concurrency Policy、Projection Policy 和 provenance。每个 Step 固化可见 Tool Set、Contract Hash 和 Extension Snapshot。

Runtime 对每个 ToolCall 执行以下阶段：

```text
Validate → Prepare → Permission Decision → Durable Intent
→ Execute → Verify → canonical ToolResult → Event / Context projection
```

ToolExecutionController 负责 ToolCall 的生命周期、deadline、cancel 与迟到结果仲裁、结果校验、敏感扫描、Projection 和事务提交。Workspace mutation 会在 Prepare 阶段读取当前文件，并生成 Base Hash 和完整 Diff。Workspace Permission 会直接授权普通文件变更。Runtime 会先提交 Durable Intent，再复检版本并原子提交。Runtime 会保留并展示已应用的完整 Diff。未知副作用会保留 `sideEffectsMayExist` 和 `reconciliationRequired`。

现有文件的原子替换会保留 mode、扩展属性和 ACL。Runtime 使用已验证的文件描述符和 macOS `fcopyfile` 复制这些元数据。Runtime 仍然拒绝 symlink、hardlink、特殊文件、owner 不匹配、特殊 mode 和文件 flags。

只有同时满足 `parallel_safe`、无副作用、参数安全和共享 Kernel 条件的只读批次可以并发执行。写入、Shell、Eidos-state、MCP 和其他外部工具保持独占。并发结果最终按模型声明顺序提交。

## 9. Sandbox & Approval

ApprovalCoordinator 把扩权 Approval request、用户 decision、feedback、暂停状态和恢复状态写入 SQLite。普通 Workspace 文件变更和默认 Workspace Seatbelt Shell 不创建 Approval。联网、附加路径、unsandboxed、MCP 和 Eidos-state 副作用继续使用 Approval。Approval 不能修改 Tool 参数，也不能删除永久拒绝、Runtime 保护路径或 hard confidentiality deny。

默认 Shell attempt 使用 macOS Seatbelt。Seatbelt Policy 根据 Workspace、Runtime root、Eidos 数据目录、永久拒绝、附加权限和网络权限物化为 effective permission profile，并保存 profile hash。默认 Workspace profile 在权限校验和 Durable Intent 后直接启动。显式权限升级会创建新的 Approval attempt；Runtime 只有在 hard confidentiality deny 不存在并且 policy 允许时才允许 unsandboxed attempt。

Managed linked Worktree 会把 Worktree 的已验证 `git_dir` 和 Project 的已验证 `git_common_dir` 传入 Seatbelt。Seatbelt 只允许读取这两个 Git metadata root。它明确拒绝这些路径的写入。原始 repository working tree 不属于该 Thread 的 execution workspace。

Shell launch boundary 只验证 Workspace identity、cwd、Approval、Seatbelt readiness 和进程边界。Workspace-wide manifest observation 在命令执行后用于变化证据和 reconciliation。观察不完整会保留 unknown observation。它不会把已知成功退出自动改成不确定副作用。

## 10. Repository Intelligence

Repository Intelligence 已实现为独立的 typed infrastructure。它包括：

- 有界、可取消的 Repository Inventory；
- content hash、encoding、generated/vendor、git status 和 generation；
- Python、TypeScript、TSX、JavaScript 和 Go 的 Tree-sitter Query 驱动 Index；
- symbols、imports、references、code chunks 和 parse diagnostics；
- Repository Map、SQLite FTS5 文档和 RapidFuzz/FTS 混合 Retrieval；
- generation-scoped persistence、完整代和 incomplete candidate 的区分；
- Retrieval Snapshot、ContextPlan 和 ContextSnapshot 的 hash 与证据绑定。

这些基础设施由 RepositoryApplication、ContextApplication 和 persistence repositories 提供。Worktree Session 的 Repository Intelligence root 使用该 Session 的 Worktree root。Local Session 使用 `Project.workspace_root`。Repository Intelligence 不要求 Git；Non-Git Project 也可以执行 inventory、文件类型识别、Tree-sitter、symbol index、search 和 retrieval。

`RepositoryWorkspaceRuntime` 是进程级的 Workspace 生命周期边界。Session create 和 existing Session read 会快速预热这个边界。Session read 的预热是 best-effort。Local root 不存在时会跳过。Worktree 只有在 state 为 `ACTIVE` 且 execution root 可用时才会预热。`MISSING`、`INVALID` 和 `DELETED` 不会阻止 Session snapshot 返回。完成 execution binding 变更的 Session handoff 也会激活新 root。Run admission 仍然负责权威 Workspace 校验。Runtime shutdown 会停止全部 watcher。

Workspace 激活只读取 SQLite 中的 latest complete generation metadata 和 recovery status。一个完整 generation 同时包含相互绑定的 persisted Inventory、Index 和 RepositoryMap。激活路径不会加载这三个持久事实，也不会调用 Inventory、Index 或 RepositoryMap builder。没有 complete generation 时，active snapshot 保持为空。`ensure_ready()` 会在 Run worker 中恢复 immutable `RepositoryAnalysisSnapshot`。

激活不会在请求路径中逐文件校验已持久化 Inventory 的 metadata。文件 metadata 校验只在显式 recovery 或 reconciliation 阶段执行。

`RuntimeEngine.run()` 在第一次模型执行前调用 `ensure_ready()`。空 Snapshot 会触发首次 bounded Inventory build。Cold start 或 watcher 失效会触发一次 reconciliation。Reconciliation 复用完整 Inventory scan 和 Index 的 previous-generation reuse。Clean active generation 会直接复用，不会 scan。RuntimeEngine 随后捕获 immutable `RepositoryAnalysisSnapshot`。同一个 Run 的所有 Model Step 都复用这个 view。

Watcher 只会合并 dirty path、增加 invalidation epoch，并把 recovery status 标记为 reconciliation required。Watcher 不会替换 active snapshot，也不会生成新 generation。如果 build 期间出现 watcher event，Runtime 可以保存已内部验证的 complete generation 作为新 baseline，但 active state 仍保持 dirty 和 reconciliation required。下一个 Run 才会再次 reconcile。并发 `ensure_ready()` 由每个 active state 的 Condition 串行化。同一 Workspace 同一时刻只有一个 Repository build。

RepositoryMap 的 manifest 读取使用 Inventory 中的 device、inode、size、mtime 和 content hash 进行 verified read。Map 捕获 Git branch 和 HEAD 后，Application 会在 SQLite commit 前用 Dulwich 再读一次。Manifest 或 Git state 在关键窗口改变时，candidate 不会成为 authoritative complete generation。完整 generation 的 Snapshot、recovery status 和 dirty bookkeeping 在一个锁保护范围内发布。

v1 mapless generation 仍然不能恢复为 active Snapshot。Persistence 会单独读取 Inventory 和 Index 的 generation watermark。首次 v2 build 会从 watermark 的下一代开始。Runtime 不会把 legacy row 当成 authoritative generation，也不会直接修改 builder 的私有 counter。

`RuntimeEngine.run()` 会在 Repository Generation ready 后，在 `ActiveRepositoryState` 的同一个锁内捕获 Snapshot、dirty paths 和 invalidation epoch。Runtime 使用这个 immutable capture 构造一次有界 `RepositoryRetrievalQuery`。Query 只使用当前用户目标、Inventory/Index 中可以确认的 path 和 symbol、已有 read/search Tool Result、capture 中的 dirty path 和最近 committed change。Runtime 对该 Run 只执行一次 Retrieval，并固定同一个 `RunRepositoryContext`。capture 后的 Watcher event 只会让 active Workspace 变脏，并由下一 Run reconciliation 观察。

`RetrievalSnapshot` 是 immutable content-addressed artifact。SQLite 只保存一份 Retrieval JSON。`run_repository_retrievals` 保存 Run 对 artifact 的使用关系。ContextPlan 继续保存 attempt lineage，但 artifact identity 不承担 Run ownership。两个 Run 可以共享同一个 Retrieval Snapshot ID，并分别解析自己的 evidence lineage。

`ContextBuilder` 是默认在线 Run 的唯一模型输入投影器。它把 Project Rules、Skills、SQLite history、verified compact summary、Repository overview 和 Retrieval evidence 放入一个结构化 `ModelContextItem` 序列。每个 ModelAttempt 在 Sampling 前持久化完整的 `ContextSnapshot`。Snapshot 原样保存 model context、resolved instructions、tool definitions、Model/Rule metadata 和可空 Repository lineage。Sampling 只读取已绑定的 Snapshot。Provider transport retry 复用同一个 Snapshot。协议修复会建立新的 ModelAttempt 和新的 Snapshot。

Workspace Explorer 复用 `RepositoryWatchController`。Watcher 事件只产生 `workspace/changed` 缓存失效通知。Renderer 根据相对路径刷新已加载的父目录。Watcher 不提供路径安全事实，也不修改 Run snapshot。

## 11. Persistence & Events

SQLite 是业务事实唯一权威。Session、Run、Item、ToolCall、Approval、Tool Attempt、Execution Segment、Step、Model Attempt、Durable Intent、Event、Outbox、Async Operation、Extension Snapshot、Context、Repository Snapshot、Compaction 和 Checkpoint 都有持久化边界。

当前 `SCHEMA_VERSION` 是 4。新数据库直接创建完整 schema。Runtime 会在一个事务中执行 v3→v4，也会按 v1→v2→v3→v4 顺序升级。v1→v2 为 Repository Generation 增加 nullable `repository_map_json`。v2→v3 先创建新表并复制数据，再删除旧 Context 表并把新表改为最终名称。迁移不会先 rename 旧表。`model_attempts.context_snapshot_id` 最终仍引用 `context_snapshots(id)`，已有 binding 和 JSON 保持不变，`foreign_key_check` 必须为空。v2→v3 同时把 ContextPlan Repository lineage 改为 nullable，并拆分 Retrieval artifact 与 Run binding。v3→v4 为 `projects` 增加可空的 `name` 列，旧 Project 读取时使用 Workspace basename 作为回退名称。迁移失败会回滚。未知 revision 和未来 revision fail closed。当前 schema 也包含 Project、Worktree、Session Handoff、retention/restore fields、Workspace Explorer 和 inline `review_comments`。

业务状态变化与 Event/Outbox 在同一 SQLite transaction 中提交。Outbox 投递失败不会删除事实。Runtime 重启会从 SQLite、Outbox、Long Task 和 Resource 状态恢复或进入 reconciliation。

In-memory 对象只保存当前协调状态、缓存、活跃资源引用和诊断信息。它不是 Session、Run、Tool 或 Event 的第二个事实来源。

## 12. Observability / OpenTelemetry

Runtime 入口初始化进程级 `TelemetryProvider`。OpenTelemetry 是非权威 Observability 层，不参与 Run 状态迁移，也不替代 SQLite 业务事实。Telemetry 初始化、Span 写入、flush 或 shutdown 失败会被 Runtime 自身日志捕获，不应成为 Agent Loop 的状态来源。

当前 Trace 覆盖三个主要执行边界：

```text
eidos.run
  ├── eidos.model.attempt
  └── eidos.tool.call
```

Run Span 记录 Run、Session、Model 和终态。Model Attempt Span 记录 Provider、resolved model、finish reason、TTFT、duration、transport retry 和 input/output/cache token usage。Tool Call Span 记录 Tool 名称、Call ID、Tool status、Workspace changed 和异常状态。

`OTEL_TRACES_EXPORTER` 默认是 `none`。当前支持 `console` 和 `otlp`；console exporter 写 stderr，OTLP 使用 HTTP Trace exporter。`OTEL_SDK_DISABLED` 可以关闭 SDK，`OTEL_SERVICE_NAME` 可以覆盖默认的 `eidos-runtime`，`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 可以设置 OTLP Trace endpoint。

## 13. Runtime Git Worktree Kernel

Runtime Git Worktree Kernel 由 `runtime/eidos_runtime/git/` 和 `runtime/eidos_runtime/persistence/worktrees.py` 提供。`WorktreeManager` 管理 Project discovery、managed Worktree create/open/validate/list/recover/cleanup/delete、实时 Git status 和 HEAD/baseline diff。默认 Managed Worktree 根目录是 `~/.eidos/.eidos-worktrees/<worktree_id>`。`GitWorkflowApplication` 是窄的 Session use-case boundary。它解析当前 execution root，检查 Worktree、Local workspace 和 active Run，再把 stage、unstage、commit 和 Local branch mutation 交给 `GitBackend`。

当前对象关系是：

```text
Project
 ├── workspace_root
 └── optional Git capability
        └── Thread / Session
                ├── Local Execution → Project.workspace_root → Runs
                ├── Worktree Execution → Managed Worktree → Runs
                └── Projectless Local → private anchor → Runs with Runtime resources
```

新的 `project/create` 接收可选的 `name` 和必需的 `workspaceRoot`。Runtime 先通过 Project resolution boundary 校验并 canonicalize 用户选择的目录，再保存 Project 元数据。名称缺省时，Runtime 使用 canonical Workspace 的文件夹名。这个调用不创建 Session。新的 `session/create` 接收 `workspaceRoot`、`executionMode`、可选的 `baseRef` 和显式的 `includeLocalChanges`。Runtime 先通过 Project resolution boundary 校验并 canonicalize 用户选择的目录，再检测可选 Git capability。`executionMode = local` 时，Runtime 创建 `worktree_id = NULL` 的 Local Session，不创建 Git side effect，也不创建 Worktree lifecycle intent。`executionMode = worktree` 时，Runtime 要求 Git capability，通过 `GitBackend` 将 `baseRef` 解析为 immutable `base_commit`，确定 `project_id`、`worktree_id`、`worktree_root` 和 `branch = NULL`，写入 durable lifecycle intent，然后通过唯一的 hardened Git CLI `worktree add --detach` 创建 Worktree。Runtime 只复制 ignored 且命中 source `.worktreeinclude` 的文件、自动复制 ignored 的 `EIDOS.override.md` 和 `AGENTS.override.md`，以及可选的 Git patch bytes。没有 Git 时，Worktree 请求返回 typed `WORKTREE_REQUIRES_GIT`。没有显式 `baseRef` 时，Runtime 使用当前 branch；repository 处于 detached HEAD 时使用 `HEAD`。Local Run 使用 `Project.workspace_root`，Worktree Run 使用 Worktree root。
`workspaceRoot` 省略或为 null 时，Runtime 创建 projectless Session。该 Session 使用 Runtime 数据目录内的私有锚点目录作为内部执行 workspace 和 identity。默认路径是 `~/.eidos/.eidos-projectless/<session_id>`。自定义 `EIDOS_DATA_DIR` 时，锚点目录仍位于该数据目录内。该 Session 不会创建 Project、Worktree 或 Repository workspace。它固定为 Local execution。Run resolution 为这个系统 workspace 生成普通的 workspace permission profile，并保留数据目录保护。RunResources 仍创建文件工具、Shell、Skill、MCP 和 Plugin 资源。projectless Run 不注入 Project Rules、Repository Context 或 workspace-environment。Desktop 仍不显示 Files 和文件树。

### Local ↔ Managed Worktree Handoff

一个 Session 可以在 Local 和它自己的 Managed Worktree 之间切换。Handoff 不创建新 Session，不修改 items、runs、events、title 或 checkpoint lineage。`execution_mode` 和 active `worktree_id` 只描述当前执行绑定。`associated_worktree_id` 记录该 Session 之前创建的、以后还可以返回的 Managed Worktree。原生 Local Session 的两个 Worktree 字段都是 NULL。

Runtime 通过 strict `session/handoff` DTO 接收 `sessionId`、`target` 和 `operationId`。Handoff 需要 Session 没有 queued、running、waiting_approval 或 finalizing Run。Runtime 为每次 handoff 固化 immutable `HandoffPlan`，并在 `session_handoff_operations` 中记录 prepared、source captured、target materialized、session rebound、completed 或 cleanup required。

Local → Worktree 没有 associated Worktree 时复用 WorktreeManager 的 prepared creation、source snapshot 和 Git CLI full/staged patch transfer。当前 HEAD、staged、unstaged、untracked、binary、symlink、mode 和 committed changes 都进入目标 Worktree。Dirty submodule checkout 不在 transfer contract 内，Runtime 返回 typed `worktree_gitlink_unsupported`。已有 associated Worktree 时只验证并复用原来的 Worktree；缺失或 deleted 返回 `WORKTREE_RESTORE_REQUIRED`，invalid 返回 `WORKTREE_RECOVERY_REQUIRED`，Runtime 不创建第二个 Worktree。

Worktree → Local 先捕获两边的 Git common directory、HEAD、branch、status 和 dirty fingerprint。两边必须属于同一个 Git common directory。Local target 有未能证明安全的修改时返回 `HANDOFF_LOCAL_CONFLICT`，Runtime 不 reset、clean 或 force checkout 用户 Local。Attached user branch 会先在 Managed Worktree detach，再由 Local 无 force 地 acquire。确认 Local branch 和 HEAD 后，Runtime 只清除 Worktree 的 branch metadata，不删除或修改 Git ref。Detached source 会让 Local 保持 detached HEAD。Managed Worktree 只在 transfer 成功后执行受控清理。

`worktrees.branch` 保存 Eidos 当前管理的 user branch identity，`worktrees.checkout_branch` 保存当前实际 checkout branch。User Branch handoff 给 Local 后，Runtime 将这两个字段和 `branch_ownership` 一起清为 NULL、NULL、`none`；Git branch ref 仍然保留。Worktree 返回 background 时可以保持 detached checkout。Handoff 完成后，下一次 Run 按新的 execution root 重新构建 resolution；已有 Run 的 `RunResolutionSnapshot.workspace_identity` 不会更新。Inactive Worktree 的完成后 fingerprint 发生变化时，返回 `HANDOFF_TARGET_CHANGED`。

`sessions.workspace_root` 保存 Project workspace root，projectless Session 保存私有锚点目录。Session 持久化的 `execution_mode` 是执行语义的权威字段，`worktree_id` 是 Worktree binding。Session projection 对普通 Session 提供 `projectId`、`workspaceRoot`、`gitAvailable` 和 `executionMode`；projectless Session 提供 `projectless = true` 和 `project = null`。因此 Desktop 不需要通过 `worktree_id` 推断执行模式。

当 `associated_worktree_id` 对应的 Worktree 为 deleted 且存在 ready Snapshot 时，Session projection 提供 `worktreeRestoreAvailable = true`。Desktop 显示 Restore Worktree action。Desktop 在 Worktree execution unavailable 时保持 Composer read-only；Local execution 可以继续使用 Local workspace。

### Managed Worktree Retention、Snapshot 和 Restore

Session history 与 Worktree directory 使用不同的生命周期。Managed Worktree directory 可以被 retention 删除。Session、Run、Event、Checkpoint 和 `associated_worktree_id` 继续保留。Worktree row 继续保留为 `state = deleted` 的历史 identity。

`WorktreeRetentionService` 只处理 `ownership = managed` 的 active Worktree。Service 读取 SQLite 的 `runtime_settings`，默认 `automatic_cleanup = true`、`managed_worktree_limit = 15`。Service 按 `last_used_at DESC` 保留最近 N 个，并按 oldest first 处理多余 candidate。Service 跳过 active Run、unfinished Handoff、unfinished lifecycle、`cleanup_required`、invalid validation、legacy managed branch、无法证明 Git/filesystem identity 和 Snapshot 失败的 Worktree。Service 不处理 adopted Worktree，也不使用 directory mtime、path 或 worktree id 作为 recency authority。

Runtime 在 startup recovery、Managed Worktree create 和 Restore 成功后执行一次 `WorktreeRetentionService.reconcile()`。Runtime 不执行后台 polling。Runtime 在 create、Handoff target 成功、Run admission、Create Branch Here 和 Restore 成功时更新 `worktrees.last_used_at`。Status、diff 和 UI polling 不更新该字段。

`WorktreeSnapshot` 的 SQLite metadata 与 artifact store 分离。Artifact store 的路径是 `<EIDOS_DATA_DIR>/worktree-snapshots/<snapshot_id>/`，目录内只保存 `full.patch.gz`、`staged.patch.gz` 和 `manifest.json`。`GitWorkingTreePatch` 只包含 raw `full_patch` 和 `staged_patch` bytes。Capture 使用 Git CLI 的成熟 diff semantics，并只读取 changed paths、untracked new-file patch 和必要的 patch metadata；它不读取全部 tracked content，也不序列化整个 Index，因此 snapshot artifact 接近 O(changed files)。Artifact 使用 gzip、SHA-256、temporary directory、`os.replace`、file fsync 和 directory fsync。Ignored environment 不进入 artifact。Restore 会从当前 source repository 重新 materialize `.worktreeinclude` 和 ignored rule override，再把 patch 交给 Git CLI apply。

每个 ready Snapshot 都使用 `refs/eidos/worktree-snapshots/<snapshot_id>` 指向 `snapshot.head`。该 ref 是 hidden reachability anchor，不是 branch。Runtime 使用 compare-and-set 创建 ref，使用 compare-and-delete 删除 ref。Runtime 只有在 patch artifact、checksum、hidden ref 和 SQLite ready row 都成功后才会删除 Worktree。Detached commit 因为仍被 hidden ref 引用，所以不依赖普通 branch ref。

Retention cleanup 使用 `worktree/retention-cleanup` lifecycle：`prepared → snapshot_saved → worktree_deleted → completed`。Restore 使用 `worktree/restore` lifecycle：`prepared → worktree_created → state_materialized → worktree_rebound → completed`。Restore 读取 `latest_ready_snapshot(worktree_id)`，验证 Project identity、artifact checksum、hidden ref 和 source fingerprint，然后通过 `GitBackend` 在同一个 root 创建 detached Worktree。Restore 设置 `checkout_branch = NULL`，保留原 `worktree_id` 和原 `base_commit`，所以 baseline diff 仍然使用原始 baseline。Restore 失败会清理新建 partial Worktree；不能证明安全时会进入 `cleanup_required`，并返回 `WORKTREE_RESTORE_REQUIRED`。

Session Delete 通过既有 `session/delete` lifecycle 清理 Snapshot metadata、artifact 和 hidden ref，再删除 Session。Hidden ref 删除必须匹配 Snapshot HEAD。User branch 不属于 Snapshot cleanup，因此 Session Delete 保留 user branch。Startup 会校验 ready row、artifact 和 hidden ref，并只清理有 SQLite ownership proof 的 orphan artifact/ref。

Project 和 Session 使用独立生命周期。`project/list` 从 SQLite `projects` 表读取 Project projection。`session/delete` 只删除 Session 及其允许清理的生命周期数据，不删除 Project row。`project/delete` 需要用户显式调用。Runtime 会先通过 Session 删除边界清理没有标题且没有 Run 的历史空 Session，再检查正式 Session、未完成 Worktree lifecycle、Snapshot 和 Handoff。检查通过后，Runtime 只删除 Project 与已完成的 Worktree 元数据，不删除 Workspace 文件、Git 仓库或 Git branch。Projectless Session 不创建 Project，因此不会进入 Project 列表。

WorktreeManager 保留 Eidos 的 Project、Worktree、Session、Run、operationId、SQLite lifecycle、recovery、compensation 和 Sandbox 语义。Git mechanics 通过 typed `GitBackend` 提供。Eidos 不实现 Git working-tree、Index、patch、commit、merge tree、rebase replay、conflict marker、fetch、fast-forward 或 push semantics。Dulwich 负责 discovery、HEAD、refs、branch metadata、revision、ignore observation 和 Worktree metadata mechanics。唯一的 `GitCli` authority 负责 status、file-scoped diff、stage、unstage、discard、commit、fetch、merge、merge abort、rebase、rebase continue/abort、push、patch capture/apply、untracked new-file patch、Worktree Add 和 destructive `clean -fdx`。`HardenedGitRunner` 继续禁用 hooks、pager、interactive prompt 和 fsmonitor。Observation 和 Local Mutation profile 禁用 credentials。Remote profile 只开放受控 credential helper 和 SSH Agent。Observation command 继续禁用 executable filters。Stage 由 native `git add` 执行 repository 和受控 user global config 中的 clean/process filter。Commit、merge commit 和 rebase replay 只读取 repository-local identity，或从用户 global config 受控读取 `user.name` 和 `user.email`，再通过命令级配置传给 Git。Runtime 始终禁用 hooks。Runtime 不对失败的 Dulwich 或 Git CLI operation 自动切换另一套 semantics。Manager 不知道 subprocess result。

最终 Git 开发工作流使用单一且明确的权威边界。Dulwich 负责 repository discovery、refs 和 revision metadata。Native Git 负责 Working Tree、Index、diff、commit、remote、merge、rebase 和 snapshot patch semantics。共享 `WorkspaceReader` 负责 Desktop Workspace Explorer 的有界文件观察。Review 的 Accept/Unstage 结果以 Git Index 为事实。HTTPS 认证交给 Git Credential Helper，SSH 认证交给 OpenSSH 和受控 SSH Agent。Checkpoint 复用现有 Git snapshot artifact、checksum 和 hidden ref，不建立第二套文件快照格式。

`session/gitStatus` 返回文件列表和兼容 count。`session/gitDiff` 接受可选的 workspace-relative `path` 和 `compareRef`。显式 `compareRef` 会先解析为 commit。无效 ref 返回 `GIT_COMPARE_REF_INVALID`。Managed Worktree 的 baseline 使用创建时冻结的 `base_commit`，并返回保存的 `base_ref` 标签。Local Session 的 baseline 默认使用当前 upstream remote-tracking ref；本地没有可解析 upstream 时回退当前 HEAD。响应还返回 native Git `--numstat` 得到的 additions、deletions 和 `statsIncomplete`。二进制文件会把统计标为不完整。行数统计不依赖有界 unified patch 是否截断。这些 file API 使用 `GIT_LITERAL_PATHSPECS=1`，所以合法文件名中的 `:`、`*`、`?` 和 `[]` 不会变成 Git pathspec expression。`session/gitStage`、`session/gitUnstage`、`session/gitDiscard` 和 `session/gitCommit` 只在没有 active Run 时修改当前 execution root。四个 mutation 都接收 `operationId`。Runtime 先完成无副作用 preflight，再在现有 `operations` 表提交 `in_progress`。Git 在 SQLite transaction 外执行。Runtime 随后重新观察 Git，并在第二个短事务中保存 completed result。Completed retry 直接 replay。相同 id 和不同 request 返回 `OPERATION_ID_REUSED`。未完成 operation 返回 `OPERATION_IN_PROGRESS`，Runtime 不盲目重做 Git。Discard 只处理 structured status 已确认的 tracked unstaged 或 untracked file。它不会隐式 Unstage，也不会处理 conflict。Stage 和 Commit 是独立动作。Commit 只提交当前 Index 中的 staged changes。Detached managed Worktree 返回 `GIT_BRANCH_REQUIRED`，用户必须先调用 `session/createBranch`。Commit 后 Managed Worktree 的 `base_commit` 保持不变，所以它的 baseline diff 继续表示整个任务从创建基线开始的改动。

Local Git Session 还提供 `session/gitSwitchBranch` 和 `session/gitCreateBranch`。Runtime 从当前 Project workspace 读取 local branches。Runtime 只允许 clean、没有进行中 Git operation、且整个 workspace 没有 active Run 时切换或创建分支。`gitCreateBranch` 从当前 HEAD 创建分支并立即切换到新分支。两个方法都使用现有 `operations` 表和 `operationId` replay。当前分支仍以 Git HEAD 为事实，不新增 Session 或 SQLite 分支字段。Local Session 共用真实 workspace，所以不同 Local Session 也共用这条 workspace 锁。分支变化完成后，Runtime 会使 RepositoryWorkspaceRuntime 失效，下一次读取会重新观察 branch 和 HEAD。

Desktop Review 不解析 Git status，也不把 repository-wide patch 拆成文件。Renderer 使用 structured status 和 baseline `changedFiles` 建立文件手风琴。全仓响应只用于列表、比较信息和总行数。文件展开时，Renderer 才调用 `session/gitDiff(path)`。展开全部会按顺序读取文件 Diff，避免同时发起无界请求。`react-diff-view` 负责解析和显示 unified patch。Stage、Unstage 和 Discard 仍通过 Runtime 修改 Git Index。Main 的 Open in Editor handler 从 Session 解析 execution root，再用 canonical path 和 regular-file 检查阻止相对路径与 symlink 越界。Commit、Push 和已有高级 Git 操作复用原 typed Runtime API，并放在同一个提交弹层中。

`HardenedGitRunner` 通过 `GitExecutionProfile` 区分 `OBSERVE`、`LOCAL_MUTATION` 和 `REMOTE`。OBSERVE 隔离 user global config、credential helper、prompt、hooks、fsmonitor 和 executable filters。LOCAL_MUTATION 只在 native `git add` 需要 clean/process filter 时读取 user config，credential 与 prompt 仍禁用。REMOTE 使用 user HOME 和 global Git config，所以 Git 可以复用 `credential.helper`、`url.*.insteadOf` 和用户 SSH config。REMOTE 只 allowlist 当前用户拥有的 Unix `SSH_AUTH_SOCK`，并设置 `/usr/bin/ssh -o BatchMode=yes`。Runtime 不读取或保存 HTTPS secret，也不继承任意 `GIT_*`、`SSH_*` 或其他 process environment。Remote URL 不进入 Desktop DTO。

`session/gitRemoteStatus` 使用 native Git 返回 remote name、current branch、upstream remote/branch 和 `HEAD...@{upstream}` 的 ahead/behind。配置中的 upstream identity 与本地 remote-tracking ref 是两个事实。upstream 已配置但 tracking ref 尚不存在时，Runtime 仍返回 upstream remote/branch，并把 ahead/behind 返回为 null。`session/gitFetch` 仍可根据该 identity 选择 remote。`session/gitFetch` 只接受 repository 已配置的 remote name。它优先选择 upstream remote，其次选择唯一 remote。Fetch 映射为 `git fetch -- <remote>`，不增加 prune、tags、all 或 force。Runtime 在 Fetch 后重新观察 HEAD、branch、upstream 和 ahead/behind。Fetch 使用 120 秒上限，并通过现有 `DeferredMethodResult`、managed task、async operation 和 process-group cleanup 在 JSON-RPC input loop 外执行。Shutdown 或 managed-resource cancellation 会设置 cancel signal。Runner 随后终止并回收 Git、SSH 和 credential helper 所在的进程组。本阶段没有新增公开的通用 operation-cancel RPC。

`session/gitPull` 只允许没有 active Run、attached branch、已配置 upstream 和 clean Workspace。Runtime 先执行 `git fetch <upstream-remote>`。Remote 没有领先时 Pull 是 no-op。本地只落后时，Runtime 执行 `git merge --ff-only --no-edit @{upstream}`。Diverged 返回 `GIT_REMOTE_DIVERGED`。Runtime 不读取 ambient `pull.rebase`、`pull.ff` 或用户默认 Pull strategy，也不自动 stash。`session/gitPush` 先 Fetch 目标 remote。已知 upstream 落后时返回 `GIT_REMOTE_BEHIND`，diverged 时返回 `GIT_REMOTE_DIVERGED`。已有 upstream 使用 `git push <remote> HEAD:<upstream-branch>`。没有 upstream 时使用 `git push --set-upstream <remote> HEAD`。Runtime 不使用 force、tags、all 或 branch deletion。Pull 和 Push 完成后都会重新观察 HEAD、structured status 和 remote status。Session 的 `base_commit` 不会改变。

Local Git mutation、Fetch、Pull 和 Push 都先完成无副作用 preflight，再 durable prepare operation。Preflight 只检查 Session、active Run、execution root、branch、Workspace cleanliness、path 和 remote/upstream metadata。Deferred external operation 的 prepare 在同一个 SQLite transaction 中写入 `operations` 的 in-progress reservation 和 `async_operations` 的 accepted lifecycle。任一写入失败会一起回滚。`operations` 表仍是 operationId replay 的结果权威。`async_operations` 只记录 deferred task 生命周期。Completed 和已确定失败的 operation 都会 replay 原结果或原错误；相同 id 和不同 request 返回 `OPERATION_ID_REUSED`。未完成外部操作返回 `OPERATION_IN_PROGRESS`，不会再次执行 Git。明确失败会把两个 operation 记录都收敛到 terminal 状态；Push 无法证明结果时保存 `GIT_REMOTE_OUTCOME_UNCERTAIN` 和 `sideEffectsMayExist`。

`session/gitMerge` 先检查 idle Session、attached branch、clean Workspace，以及没有已有 merge 或 rebase。Eidos 通过 Dulwich 把 local branch 或显式 revision 解析成固定 commit id。Native Git 执行 `git merge --no-edit <commit>`。成功后 Runtime 重新观察 HEAD、status 和 operation state。冲突不是通用失败。Runtime 返回 `operationState=merge` 和 structured `conflictFiles`，并把该结果写入 operationId replay。`session/gitMergeAbort` 只执行 native `git merge --abort`。Merge commit 使用与 commit 相同的受控 identity。Hooks 继续禁用。Session 的 `base_commit` 不会改变。

`session/gitRebase` 使用与 Merge 相同的 preflight 和 target resolution 边界。Native Git 执行 `git rebase <resolved-commit>`。冲突结果返回 `operationState=rebase` 和 structured `conflictFiles`，并写入 operationId replay。修复文件并 Stage 后，`session/gitRebaseContinue` 执行 `git rebase --continue`。Runner 把 `GIT_EDITOR` 设为无副作用的成功程序，所以 Git 保留原 commit message，并且不会打开 editor。`GIT_SEQUENCE_EDITOR` 始终禁用。`session/gitRebaseAbort` 只执行 native `git rebase --abort`。Git repository 中的 rebase metadata 是唯一 operation state 权威。Eidos 不持久化第二份 merge/rebase 状态。Session 的 `base_commit` 不会改变。

Inline Review Comment 是 Eidos 产品语义，不进入 `GitBackend`。SQLite `review_comments` 是 Comment 的唯一事实来源。记录保存 Session、path、Diff scope、old/new side、line、body、观察到的 HEAD、Diff hash、active/stale 状态和时间。`review/createComment` 会先按 operationId 回放已完成结果，再重新请求 file-scoped native Git Diff，并核对 HEAD、changed file、SHA-256 Diff hash 和由 `unidiff` 解析出的 old/new 行 anchor。HEAD 或 Diff hash 变化返回 `REVIEW_DIFF_CHANGED`；side/line 不存在返回 `REVIEW_ANCHOR_INVALID`。`review/listComments` 会再次观察对应 Diff。只有 HEAD 和 Diff hash 都相同时，Comment 才保持 active。Runtime 不做模糊重定位。

Renderer 使用 `react-diff-view` 的 gutter event 和 widget extension 展示 Comment。Renderer 不修改第三方 Diff renderer。`review/createComment` 和 `review/deleteComment` 使用现有 `operations` 表提供 operationId replay。Comment 创建和删除不会启动 Run。显式 Send Review Feedback 只收集 active Comment，把它们格式化为普通用户输入，再调用现有 `run/start` 链路。

Desktop `GitWorkflowControls` 只编排现有 typed Git API。它读取 `session/gitRemoteStatus` 和 `project/gitContext.branches`，不解析 Git 输出。Commit、Fetch、Pull、Push、Merge、Rebase 和 continue/abort 都携带 operationId。传输未知或 operation 仍在进行时，Renderer 保留 operationId；已确定的 terminal failure 会删除缓存并让下一次显式点击生成新的 identity；`GIT_REMOTE_OUTCOME_UNCERTAIN` 会先提示用户检查远端状态。message、target、branch 或 HEAD 改变后，Renderer 会生成新的 identity。一个操作执行时，其他冲突操作会被禁用。Active Run、Session Handoff 和 branch attach 期间，AppShell 也会禁用这些控制。操作完成后，Renderer 刷新 structured status、当前 file Diff、Remote observation 和 mutation 返回的 operation state。Desktop 不保存 Git credential，也不改变 native Git policy。

Run 的执行 identity 固化在 `RunResolutionSnapshot.workspace_identity`。Runtime 会在启动和恢复 Run 时重新验证 Worktree、root、Git dir、Git common dir 和 inode/device/owner。Runtime 不会把 managed Run fallback 到 repository root。

Runtime 使用 typed Git observations，并在 observation failure、timeout 或 bounded diff truncation 时不更新 lifecycle state。`deleted` 是 terminal state。Kernel 不修改 Run 并发语义。

## 14. Extension Runtime

PluginCatalog 只接收受控的本地 Plugin v1 包。Runtime 校验 manifest、文件数量、单文件和总大小、content hash，并把安装内容写入私有 extensions 目录。Plugin 可以声明 Skill 和 MCP Server。Plugin 的启用状态、版本、hash 和引用关系进入 SQLite。

SkillCatalog 管理 bundled system skills、用户和 Plugin Skill。Turn 开始时，Catalog Snapshot 和 SelectedSkillSet 固化 qualified ID、source、version 和 content hash。Skill 主资源与受控 resource path 通过 bounded read 读取。

MCP 当前使用官方 Python MCP SDK 的 stdio client。MCP Server 由 RuntimeAsyncKernel 持有长生命周期连接。Server Tool 会进入统一 Tool Registry，保留 MCP provenance，并按 external Tool 经过 Approval、Sandbox、timeout、结果校验和 reconciliation。MCP 进程使用受控环境、进程组和 connector 或 workspace-read Seatbelt policy。

## 15. Packaging & Distribution

源码开发使用仓库 `.venv/bin/python`，Runtime root 是仓库 `runtime/`。打包开发路径从 `process.resourcesPath/runtime/` 解析 bundled Python 和 `runtime/app`，不回退到系统 Python、PATH、`.venv` 或用户 `PYTHONHOME`。

`build-macos-runtime.sh` 生成 macOS arm64 的 self-contained Runtime Bundle。Bundle 包含 managed CPython 3.12.13、锁定的 production dependencies、Eidos Runtime、Seatbelt 资源和受管 Ripgrep。Electron Builder 将 Bundle 放入 App resources，DMG 目标只配置 arm64。

`package:mac` 生成未签名本地 DMG，并执行 packaged App、Runtime、SQLite、Seatbelt 和从 DMG 复制 App 的 smoke。`package:mac:release` 要求 Developer ID 和 Apple notarization credentials，随后执行 hardened runtime、签名、notarization、stapling、`codesign`、`spctl` 和 `stapler` 验证。

## 16. Runtime Recovery

Runtime 启动时会收敛未完成的 Run、ToolCall、Approval、Outbox 和资源状态。Cancellation 在 SQLite 中先记录 request，再通过 Run Worker、Model request、Tool process、Approval wait 和 Async Task 传播。迟到结果不能把已取消 Run 改回成功。

Long Task 控制事实写入 `operations` 的 `long_task/control` scope。`run/pause` 在模型、工具、Approval 和 Slot 安全点生效。`run/resume` 需要重新记录 Workspace identity、规则、Repository/Context snapshot、permission snapshot、Git 和 reconciliation 检查结果。未确认副作用不会自动重放。

Checkpoint create/list 和 rewind/fork action lineage 通过 typed RPC 暴露。Checkpoint 保存规则、Repository、Context、compaction、Workspace identity、Git、permission、Model snapshot 和 reconciliation 引用。Managed 和 Local Git Checkpoint 都复用 `worktree_snapshots` metadata、Git patch artifact、checksum 和 hidden ref，保存 HEAD、staged、unstaged 和 untracked 状态。Managed Fork 创建新的 detached managed Worktree，并恢复完整 Checkpoint Git 状态。Local Fork 仍使用同一个 Project 和同一个 `workspace_root` 创建新的 Local Session、Run 和 lineage，所以两个 Local Thread 共享真实目录。Managed 和 Local Rewind 会在原 checkout 中恢复完整 Checkpoint Git 状态；Local Rewind 只允许用户显式调用。Non-Git Local Workspace 仍不提供 filesystem snapshot、copy-on-write 或 rewind。

Worktree Session create、Session delete、managed Checkpoint Fork、Create Branch Here、retention cleanup 和 Restore 使用当前 durable lifecycle intent。Session Handoff 使用同一 `session_handoff_operations` 边界。Local Session create、delete 和 Local Fork 不创建 Git lifecycle intent。Local delete 只删除 Eidos Session 数据，不删除 `workspace_root` 或用户文件。Runtime 启动时先完成 SQLite 初始化，再运行 Worktree observation、Worktree lifecycle、Snapshot storage、retention/restore 和 Session Handoff reconciliation，之后才暴露业务应用。Retry 使用原始 intent 中的同一 Worktree id、root、branch 和 base commit。新 Worktree 的 branch 是 NULL，验证要求实际 Git branch 也为 NULL。已有 legacy attached Worktree 仍要求 branch identity 完全匹配。`includeLocalChanges = true` 要求 source `HEAD == baseCommit`，并在创建前后重新核验 source identity、HEAD、branch、status 和 patch。Source Workspace 不执行 stash、reset、checkout 或 add。冲突会进入 compensation；无法证明清理完成时进入 `cleanup_required`。Create Branch Here 在 durable intent 中保存当前 `expected_head`，并用该字段完成 attach 和 recovery；`base_commit` 不会随着 commit 更新。HEAD 在 branch create 后发生外部改变时，Runtime 进入 recovery required，不 force switch。Retention cleanup 只有在 Snapshot ready、artifact checksum 和 hidden ref 都通过验证后才执行 `reset --hard`、`clean -fdx` 和 non-force Worktree remove。Restore 失败会保留 Snapshot，并在不能安全清理时进入 `cleanup_required`。显式 user branch 会随 Worktree 删除保留。Runtime 不执行 force remove、未知路径递归删除或 branch 强制删除。

## 17. Implementation Anchors

维护者核对本文时，优先从以下稳定入口读取代码：

- `desktop/main/main.ts`
- `desktop/main/runtime-client.ts`
- `desktop/main/runtime-paths.ts`
- `desktop/main/preload.ts`
- `desktop/shared/`
- `runtime/eidos_runtime/protocol/server.py`
- `runtime/eidos_runtime/protocol/response_server.py`
- `runtime/eidos_runtime/protocol/registry.py`
- `runtime/eidos_runtime/application/`
- `runtime/eidos_runtime/runtime/supervisor.py`
- `runtime/eidos_runtime/runtime/engine.py`
- `runtime/eidos_runtime/context/`
- `runtime/eidos_runtime/model/`
- `runtime/eidos_runtime/tools/`
- `runtime/eidos_runtime/sandbox/`
- `runtime/eidos_runtime/extensions/`
- `runtime/eidos_runtime/repo_intelligence/`
- `runtime/eidos_runtime/telemetry/provider.py`
- `runtime/eidos_runtime/telemetry/tracing.py`
- `runtime/eidos_runtime/git/`
- `runtime/eidos_runtime/db/schema.py`
- `runtime/eidos_runtime/db/storage.py`
- `runtime/eidos_runtime/persistence/`
