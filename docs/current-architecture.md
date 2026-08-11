# Eidos 当前架构

本文只描述当前代码中的系统结构和职责。生产代码和自动化测试是本文的事实来源。本文不描述历史 Phase，也不描述尚未接入默认 Run 的目标态设计。

## 1. System Boundary

Eidos 是一个面向 macOS 的本地桌面 Agent Runtime。系统包含 Electron Desktop、Python Runtime、SQLite、本地 Workspace 和远端 Model Provider。

Renderer 是展示边界。Renderer 不能直接访问 Node 系统能力，也不能直接连接 Python Runtime。Electron Main 是 Desktop 和 Runtime 之间的控制边界。Python Runtime 是 Session、Run、Item、ToolCall、Approval、事件和副作用状态的业务协调边界。

远端模型只提供不可信的文本和 ToolCall 提案。Runtime 负责验证模型输出、构建 Context、执行 Tool、处理 Approval、应用 Sandbox Policy 和提交持久事实。

## Workspace model

Eidos 把用户选择的目录建模为 Project。Project 的基础事实是 `workspace_root`。Project 可以有 Git capability，但 Git 不是 Project 存在的前提。

```text
Project
 ├── workspace_root
 └── optional Git capability
          └── Thread / Session
                    ├── Local Execution → Project.workspace_root
                    └── Worktree Execution → Managed Worktree → Run
```

Non-Git Project 只能创建 Local Execution Session。Git Project 可以创建 Local 或 Worktree Execution Session。Local Session 的 `execution_mode` 是 `local`，`worktree_id` 是 NULL，Run 直接使用 `Project.workspace_root`。Worktree Session 的 `execution_mode` 是 `worktree`，它绑定 Managed Worktree，Run 使用该 Worktree root。Git Project 的 Session Git status 和 diff API 会读取当前 execution root。

文件读写、Shell、Skill、MCP、Context、Long Task、Sandbox 和 Checkpoint 属于 Workspace 或 Runtime 能力。它们不因为 Project 没有 Git 而失效。Git status 和 Git diff 在 Git Project 的当前 execution root 上提供。Managed Worktree 和 Git-based Fork 仍然只在 Git Project 中提供。

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
- 在 Quit 时取消活动 Run，等待 Runtime 收敛，并在必要时报告关闭失败。

Response Action 使用同一条 typed IPC 边界。Main 把 `responseAction/state`、`item/setFeedback` 和 `run/revise` 映射到 Runtime。

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

当前默认 compactor 把目标、约束、已完成动作、Workspace 变化、重要事实、失败尝试、未解决问题、决定、待处理 Approval 和下一步写入有界摘要。原始历史仍保存在 SQLite。ContextPlan、ContextSnapshot 和 Verified Compaction 也有 typed persistence boundary，但 Repository/Context 组合还不是每次默认 Run 的强制在线前置步骤。

## 7. Model Gateway

ModelConfigStore 使用受保护的 `models.json` 保存本地配置。默认位置是 `~/.eidos/models.json`，显式数据目录会改变 Runtime-owned 数据位置。文件按 owner-only 权限保存。

API Key 会经过本地模型配置写入链路：Renderer 通过 typed IPC 把配置交给 Electron Main，Main 再通过 `model/create` 或 `model/update` JSON-RPC request 把 `apiKey` 发送给 Runtime。Runtime 将 Key 写入受保护的 `models.json`。Key 不应出现在模型列表/读取响应、SQLite、Event/Execution Feed 或正常日志中。

当前 Model Catalog 只包含以下 Provider 和 Model ID：

```text
deepseek: deepseek-v4-pro, deepseek-v4-flash
minimax:  MiniMax-M3
kimi:    kimi-k3, kimi-k2.7-code-highspeed
```

ModelConfigStore 要求配置与内置 Catalog 严格匹配。当前 wire API 只有 OpenAI-compatible Chat Completions。Runtime 使用 Pydantic AI 的 Model API、Provider 和流式请求边界。Runtime 不提供 arbitrary custom provider、arbitrary base URL、arbitrary model ID 或 Responses API。

每个 Run 固化 Model Profile 和 Extension Snapshot。Model Lease 使用该快照创建 Provider Client。Model Attempt 保存 usage、响应元数据、有限的 transport retry 诊断和稳定 Eidos 错误码。Model Client 不拥有 Runtime Event Loop；共享 RuntimeAsyncKernel 负责其异步 I/O。

## 8. Tool Execution

Tool Registry 保存 ToolSpec、输入和结果 Schema、Execution Policy、Concurrency Policy、Projection Policy 和 provenance。每个 Step 固化可见 Tool Set、Contract Hash 和 Extension Snapshot。

Runtime 对每个 ToolCall 执行以下阶段：

```text
Validate → Prepare → Approval when required → Durable Intent
→ Execute → Verify → canonical ToolResult → Event / Context projection
```

ToolExecutionController 负责 ToolCall 的生命周期、deadline、cancel 与迟到结果仲裁、结果校验、敏感扫描、Projection 和事务提交。Workspace mutation 要求 Read Evidence、Base Hash、完整 Diff、Approval 后的版本复检和原子提交。未知副作用会保留 `sideEffectsMayExist` 和 `reconciliationRequired`。

只有同时满足 `parallel_safe`、无副作用、参数安全和共享 Kernel 条件的只读批次可以并发执行。写入、Shell、Eidos-state、MCP 和其他外部工具保持独占。并发结果最终按模型声明顺序提交。

## 9. Sandbox & Approval

ApprovalCoordinator 把 Approval request、用户 decision、feedback、暂停状态和恢复状态写入 SQLite。Approval 不能修改 Tool 参数，也不能删除永久拒绝、Runtime 保护路径或 hard confidentiality deny。

默认 Shell attempt 使用 macOS Seatbelt。Seatbelt Policy 根据 Workspace、Runtime root、Eidos 数据目录、永久拒绝、附加权限和网络权限物化为 effective permission profile，并保存 profile hash。Shell 只有在受控审批后才启动。显式权限升级会创建新的 Approval attempt；Runtime 只有在 hard confidentiality deny 不存在并且 policy 允许时才允许 unsandboxed attempt。

Managed linked Worktree 会把 Worktree 的已验证 `git_dir` 和 Project 的已验证 `git_common_dir` 传入 Seatbelt。Seatbelt 只允许读取这两个 Git metadata root。它明确拒绝这些路径的写入。原始 repository working tree 不属于该 Thread 的 execution workspace。

Shell launch boundary 只验证 Workspace identity、cwd、Approval、Seatbelt readiness 和进程边界。Workspace-wide manifest observation 在命令执行后用于变化证据和 reconciliation。观察不完整会保留 unknown observation。它不会把已知成功退出自动改成不确定副作用。

## 10. Repository Intelligence

Repository Intelligence 已实现为独立的 typed infrastructure。它包括：

- 有界、可取消的 Repository Inventory；
- content hash、encoding、generated/vendor、git status 和 generation；
- Python、TypeScript、TSX、JavaScript 和 Go 的 Tree-sitter Index；
- symbols、imports、references、code chunks 和 parse diagnostics；
- Repository Map、SQLite FTS5 文档和 RapidFuzz/FTS 混合 Retrieval；
- generation-scoped persistence、完整代和 incomplete candidate 的区分；
- Retrieval Snapshot、ContextPlan 和 ContextSnapshot 的 hash 与证据绑定。

这些基础设施由 RepositoryApplication、ContextApplication 和 persistence repositories 提供。Worktree Session 的 Repository Intelligence root 使用该 Session 的 Worktree root。Local Session 使用 `Project.workspace_root`。Repository Intelligence 不要求 Git；Non-Git Project 也可以执行 inventory、文件类型识别、Tree-sitter、symbol index、search 和 retrieval。当前默认在线 Run 仍主要使用 ContextBuilder、Workspace Tool Result 和 SQLite Context Facts。RuntimeEngine 在每次 Model Attempt 前不会强制执行完整 Inventory → Index → Map → Retrieval → ContextPlan 组装。因此，Repository Intelligence 的结构和持久化是当前实现，自动进入每次 Run 仍是部分接线能力。

## 11. Persistence & Events

SQLite 是业务事实唯一权威。Session、Run、Item、ToolCall、Approval、Tool Attempt、Execution Segment、Step、Model Attempt、Durable Intent、Event、Outbox、Async Operation、Extension Snapshot、Context、Repository Snapshot、Compaction 和 Checkpoint 都有持久化边界。

当前 `SCHEMA_VERSION` 是 21。新数据库直接创建 v21。已有数据库按当前 migration chain 逐步迁移到 v21。v10 及更早版本不在当前启动迁移窗口内，未知版本 fail closed。v16 增加 `sessions.worktree_id` 和索引。v17 增加 `worktree_lifecycle_operations`，只记录 `session/create`、`session/delete` 和 `checkpoint/fork` 的有限 durable intent。v18 把 Project 从 Git repository 泛化为 filesystem workspace，并把 Git repository root 和 common dir 改为成对可空字段。v19 增加 `sessions.execution_mode`，按旧 `worktree_id` 回填 `local` 或 `worktree`，并让 `worktrees.branch` 支持 NULL。v20 增加 `branch_ownership`、local-change source snapshot 字段和 `worktree/attach-branch` lifecycle scope。v21 增加 `sessions.associated_worktree_id`、Worktree 的 `checkout_branch` 和 `session_handoff_operations`。v17 → v21 会保留已有 Project、Worktree、Session 和 Run。旧 Worktree Session 会把 active `worktree_id` 回填到 associated binding。

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

Runtime Git Worktree Kernel 由 `runtime/eidos_runtime/git/` 和 `runtime/eidos_runtime/persistence/worktrees.py` 提供。`WorktreeManager` 管理 Project discovery、managed Worktree create/open/validate/list/recover/cleanup/delete、实时 Git status 和 HEAD/baseline diff。

当前对象关系是：

```text
Project
 ├── workspace_root
 └── optional Git capability
        └── Thread / Session
                ├── Local Execution → Project.workspace_root → Runs
                └── Worktree Execution → Managed Worktree → Runs
```

新的 `session/create` 接收 `workspaceRoot`、`executionMode`、可选的 `baseRef` 和显式的 `includeLocalChanges`。Runtime 先通过 Project resolution boundary 校验并 canonicalize 用户选择的目录，再检测可选 Git capability。`executionMode = local` 时，Runtime 创建 `worktree_id = NULL` 的 Local Session，不创建 Git side effect，也不创建 Worktree lifecycle intent。`executionMode = worktree` 时，Runtime 要求 Git capability，通过 `GitBackend` 将 `baseRef` 解析为 immutable `base_commit`，确定 `project_id`、`worktree_id`、`worktree_root` 和 `branch = NULL`，写入 durable lifecycle intent，然后通过 hardened native seam 执行 `git worktree add --detach`、只复制 ignored 且命中 source `.worktreeinclude` 的文件、自动复制 ignored 的 `EIDOS.override.md` 和 `AGENTS.override.md`，以及可选的只读 Git patch/index dirty transfer。没有 Git 时，Worktree 请求返回 typed `WORKTREE_REQUIRES_GIT`。没有显式 `baseRef` 时，Runtime 使用当前 branch；repository 处于 detached HEAD 时使用 `HEAD`。Local Run 使用 `Project.workspace_root`，Worktree Run 使用 Worktree root。

### Local ↔ Managed Worktree Handoff

一个 Session 可以在 Local 和它自己的 Managed Worktree 之间切换。Handoff 不创建新 Session，不修改 items、runs、events、title 或 checkpoint lineage。`execution_mode` 和 active `worktree_id` 只描述当前执行绑定。`associated_worktree_id` 记录该 Session 之前创建的、以后还可以返回的 Managed Worktree。原生 Local Session 的两个 Worktree 字段都是 NULL。

Runtime 通过 strict `session/handoff` DTO 接收 `sessionId`、`target` 和 `operationId`。Handoff 需要 Session 没有 queued、running、waiting_approval 或 finalizing Run。Runtime 为每次 handoff 固化 immutable `HandoffPlan`，并在 `session_handoff_operations` 中记录 prepared、source captured、target materialized、session rebound、completed 或 cleanup required。

Local → Worktree 没有 associated Worktree 时复用 WorktreeManager 的 prepared creation、source snapshot 和 `NativeWorktreeChangeTransfer`。当前 HEAD、staged、unstaged、untracked、binary 和 committed changes 都进入目标 Worktree。已有 associated Worktree 时只验证并复用原来的 Worktree；缺失或 deleted 返回 `WORKTREE_RESTORE_REQUIRED`，invalid 返回 `WORKTREE_RECOVERY_REQUIRED`，Runtime 不创建第二个 Worktree。

Worktree → Local 先捕获两边的 Git common directory、HEAD、branch、status 和 dirty fingerprint。两边必须属于同一个 Git common directory。Local target 有未能证明安全的修改时返回 `HANDOFF_LOCAL_CONFLICT`，Runtime 不 reset、clean 或 force checkout 用户 Local。Attached user branch 会先在 Managed Worktree detach，再由 Local 无 force 地 acquire。Detached source 会让 Local 保持 detached HEAD。Managed Worktree 只在 transfer 成功后执行受控清理。

`worktrees.branch` 保存 durable user branch identity，`worktrees.checkout_branch` 保存当前实际 checkout branch。Worktree 返回 background 时可以保持 detached checkout，即使 durable user branch 仍然存在。Handoff 完成后，下一次 Run 按新的 execution root 重新构建 resolution；已有 Run 的 `RunResolutionSnapshot.workspace_identity` 不会更新。Inactive Worktree 的完成后 fingerprint 发生变化时，返回 `HANDOFF_TARGET_CHANGED`。

`sessions.workspace_root` 保存 Project workspace root。Session 持久化的 `execution_mode` 是执行语义的权威字段，`worktree_id` 是 Worktree binding。Session projection 同时提供 `projectId`、`workspaceRoot`、`gitAvailable` 和 `executionMode`，因此 Desktop 不需要通过 `worktree_id` 推断执行模式。

WorktreeManager 保留 Eidos 的 Project、Worktree、Session、Run、operationId、SQLite lifecycle、recovery、compensation 和 Sandbox 语义。Git mechanics 通过 typed `GitBackend` 提供。默认 backend 是 `DulwichGitBackend`。它直接返回 `GitRepositoryDiscovery`、`GitRepositoryContext`、`GitStatusObservation`、`GitDiffObservation` 和 `GitWorktreeEntry`，并使用 Dulwich 处理 discovery、ref、local branch、status、ignore observation、diff、worktree list/remove/prune 和 legacy compare-and-delete branch。`NativeWorktreeCreator`、`NativeWorktreeChangeTransfer`、`NativeBranchAttacher`、`NativeWorktreeCheckout` 和 `NativeWorktreeCleaner` 是窄的 native seam，分别处理 checkout、只读 Git patch/index dirty transfer、`git switch -c`、无 force 的 detach/branch checkout 和创建失败清理。它们通过 `HardenedGitRunner` 保留 timeout、bounded output、禁用 hook/fsmonitor/filter、credential/prompt 和进程组清理。Manager 不知道 subprocess result、porcelain 或 native adapter。

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

Checkpoint create/list 和 rewind/fork action lineage 通过 typed RPC 暴露。Checkpoint 保存规则、Repository、Context、compaction、Workspace identity、Git、permission、Model snapshot 和 reconciliation 引用。Managed Fork v1 从 `checkpoint.git_head` 创建一个新的 detached managed Worktree，再创建绑定的 Worktree Session、Run 和 `checkpoint_action`。新 Worktree 的 `branch` 是 NULL。Local Fork 使用同一个 Project 和同一个 `workspace_root` 创建新的 Local Session、Run 和 lineage。Local Fork 是 conversation/runtime fork，不是 filesystem snapshot；两个 Local Thread 会共享真实目录。当前实现不提供 copy-on-write、directory snapshot 或 filesystem rewind。

Worktree Session create、Session delete、managed Checkpoint Fork 和 Create Branch Here 使用 v20 durable lifecycle intent。Session Handoff 使用 v21 的同级 `session_handoff_operations`。Local Session create、delete 和 Local Fork 不创建 Git lifecycle intent。Local delete 只删除 Eidos Session 数据，不删除 `workspace_root` 或用户文件。Runtime 启动时先完成 SQLite 初始化，再运行 Worktree observation、Worktree lifecycle 和 Session Handoff reconciliation，之后才暴露业务应用。Retry 使用原始 intent 中的同一 Worktree id、root、branch 和 base commit。新 Worktree 的 branch 是 NULL，验证要求实际 Git branch 也为 NULL。已有 legacy attached Worktree 仍要求 branch identity 完全匹配。`includeLocalChanges = true` 要求 source `HEAD == baseCommit`，并在创建前后重新核验 source identity、HEAD、branch、status 和 patch。Source Workspace 不执行 stash、reset、checkout 或 add。冲突会进入 compensation；无法证明清理完成时进入 `cleanup_required`。Create Branch Here 在 durable intent 中保存当前 `expected_head`，并用该字段完成 attach 和 recovery；`base_commit` 不会随着 commit 更新。HEAD 在 branch create 后发生外部改变时，Runtime 进入 recovery required，不 force switch。显式 user branch 会随 Worktree 删除保留。Runtime 不执行 force remove、未知路径递归删除或 branch 强制删除。

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
