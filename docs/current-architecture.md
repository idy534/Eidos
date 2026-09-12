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

Desktop Workspace Explorer 也只读取当前 Session execution root。`WorkspaceExplorerApplication` 先解析 Local root 或验证 Managed Worktree identity，再调用共享 `WorkspaceReader`。`WorkspaceReader` 与 Agent 文件工具共用 fd-relative、`O_NOFOLLOW`、敏感路径、hard discovery directory 和 root ignore 规则。`workspace/listDirectory` 只返回一层子项。`workspace/readFilePreview` 返回有界 UTF-8 预览或图片/PDF/HTML 元数据。`workspace/readAsset` 按块读取受控资源。Projectless 使用当前 Session 的私有 Workspace，仍经过同一个 Reader。Renderer 对 Conversation 传来的历史文件路径先检查当前目录项；目录项明确缺失时，Renderer 不直接调用预览接口。目录列表截断时，Runtime 继续负责最终验证。Renderer 不直接读取 filesystem。

Desktop 会让 Conversation 始终保持挂载。Session header 右侧固定显示环境信息入口；用户可以通过工作区开关打开或关闭右侧 Workspace Dock。Dock 使用本地 Renderer 状态管理 Review、Terminal、Files 和 Browser Tab。Review 和 Files 各只有一个工具 Tab，Terminal 和 Browser 可以同时打开多个 Tab。Files Tab 内可以同时预览多个文件。文件 Tab 保留在预览栏中，预览区不显示当前路径、文件大小或手动刷新入口，侧栏布局默认给预览区更多空间。产物卡的图片会直接进入全屏预览，HTML 会直接打开隔离网页面板，PDF 会进入 Files 的内置预览，系统应用打开入口保持可用。Dock 普通展开时环境信息入口仍在 Session header，完全展开时入口显示在 Dock header。Session 对话的回答和提问框共用固定最大宽度并保持居中；回答右边与提问框右边对齐，窗口变宽时不会继续拉伸。Dock 支持 Tab 切换、关闭、空状态选择工具、全侧栏展开和关闭。Dock 与 Conversation 之间的分隔条可以拖动调整宽度。Files 的文件树与预览区也有独立的可拖动分隔条。Dock 关闭时，Conversation 内容在可用宽度内居中。Session 或 execution binding 变化时，Renderer 会关闭旧 Dock，并用新的 execution key 重新加载 Workspace 数据。

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
  ↓ per-Session FIFO admission and Run worker control
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
- 为 stdio RPC 请求记录超时状态。超时请求的迟到 response 会被安全丢弃，真正未知的 response id 仍然会触发协议错误；
- 管理 Runtime 启动代次和 Runtime 重启。Runtime 进程意外退出时，Desktop 会发布启动错误，Renderer 可以通过 typed IPC 请求重新启动 Runtime；
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

RunSupervisor 负责按 Session 维护持久 FIFO、Run Worker、Approval 等待、取消、暂停、恢复和关闭收敛。同一 Session 同时只运行一个 Run，因此一个 Session 可以排队多个 Run。不同 Session 的 Run 可以并行，且不区分 Workspace、Local checkout 或 Managed Worktree。普通 Run 没有并发上限。每个 Run 独立持有 `ToolConcurrencyGate` 和 `ShellProcessManager`。不同 Session 的 Run 可以在同一个 Workspace 并行执行 Shell 和其他普通副作用。一个 Run 的长 Shell 仍会阻止这个 Run 启动新的副作用，但 `write_stdin` 可以继续管理原 Shell。等待 Approval 不会占用其他 Run 的执行资源。

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

Model Step、Segment Step 和 effective time 在当前实现中是 telemetry 和 operational segment 信息。健康 Run 不会因为固定 model-step、Run duration 或固定 repeated-call counter 自动终止。LoopGuard 通过语义 fingerprint 判断是否收敛，不是固定步数限制。Segment 达到 operational quantum 时可以 rollover，但 rollover 不是 Run 终态。

Chat Completions Adapter 根据结构化响应事实把有 ToolCall 的响应归类为 `commentary`，并保留模型文本、可选 MessagePhase 和 Provider `finish_reason`。Chat Completions 没有原生的 Assistant phase，所以没有 phase 的响应使用 `unknown` 或 `None` 表示。RuntimeEngine 不读取 MessagePhase 或 `finish_reason=stop` 作为完成门控。每次 normalized sampling response 都得到 `needs_follow_up`：ToolCall 或待消费的当前 Turn 输入需要继续采样，assistant-only response 可以结束当前 Turn。可返回给模型的 Tool Result 和 Tool Error 都会进入 Context，再触发下一次 Sampling。Sampling 收到的 provisional text 在完整响应通过校验前不会持久化。可重试的 transport failure 即使已经收到 provisional text，也会复用同一个 frozen ContextSnapshot 创建新的 Model Attempt。已收到的文本或 ToolCall 不会自动重放。normalization 的 `protocol_error` 和 `length` 都进入同一条已有的有界 protocol repair，连续错误合计最多触发一次。`content_filter`、cancel 和 authentication failure 不进入该 repair，直接终止当前模型流程。

确定的 Tool Error 只表示本次尝试失败，不会单独把 Run 置为终态。Runtime 会把失败事实交给下一次模型决策。模型可以修正参数、选择替代 Tool，或者在没有安全路径时结束。等价重复且没有新事实时，LoopGuard 负责收敛。

RunFinalizer 为 context pressure、loop guard 等需要提前停止的路径生成有界、无 Tool 的回答。它不改变普通 Agent Loop 的 `needs_follow_up` 判定。Finalization Attempt 仍然记录自己的 timeout、model failure 和 output item 状态。

## 6. Context & Instructions

默认在线 Run 使用 `ContextBuilder` 从持久状态事实、当前 Run、Model Profile、Rule Snapshot、Selected Skill、历史 Item、Tool Result、Workspace State 和额外事实构建模型 Context。

`ProjectRuleResolver` 从 Workspace root 到 effective cwd 逐层读取每个目录中优先级最高的非空候选文件。候选顺序是：

```text
EIDOS.override.md
EIDOS.md
AGENTS.override.md
AGENTS.md
CLAUDE.md
```

每层只选择一个候选。Resolver 使用共享的 32 KiB UTF-8 byte budget。Resolver 记录 shadowed candidate、读取或预算 warning、原始 content hash、实际包含字节数、directory level、Workspace root 和 effective cwd。Run 使用 immutable rule snapshot，Step Resolution 保存 resolved instruction hash 和 effective cwd。

`InstructionResolver` 按 System Safety、Base Agent、Runtime Policy、Project Rules 和 Selected Skill 形成分层 instructions。Skill Catalog 属于 developer capability context。真正加载的第三方 `SKILL.md` 属于较低权限的 user context。Project Rules 和 Selected Skill 保留来源与 hash。它们不具备修改 Runtime Permission、Approval 或 Sandbox 的权限。

Context Budget 优先使用最近 Provider Usage 的 active input tokens。Provider Usage 不可用时，Runtime 使用标记为 `estimated` 的有界估算。Context pressure、Provider `context_exceeded` 和 projection overflow 会触发 deterministic bounded compaction 或一次安全恢复。没有新的可压缩历史或 Context 投影没有进展时，Run 以 `context_still_over_budget` 停止。

当前默认 compactor 先生成确定性的有界候选摘要。候选摘要不会直接成为模型事实。Runtime 会从 `state.sqlite` 重载 Item、Tool Result、Workspace change、Approval 和 reconciliation 事实，再执行 `ContextCompactionVerifier`。Tool provenance 由候选摘要的 source Item IDs 解析到真实 ToolCall IDs，所以 pre-turn compaction 可以准确引用以前 Run 的 Tool facts，也不会附加无关 ToolCall。只有验证通过的 `VerifiedCompactSummary` 才会在同一个事务中写入权威 `compact_summaries`、增加计数并产生一条 `context.compacted` Event。原始 Item 和 Tool 事实仍保存在 `state.sqlite`。验证失败会保留上一份 verified summary。

## 7. Model Gateway

ModelConfigStore 使用受保护的 `models.json` 保存本地配置。默认位置是 `~/.eidos/models.json`，显式数据目录会改变 Runtime-owned 数据位置。文件按 owner-only 权限保存。

API Key 会经过本地模型配置写入链路：Renderer 通过 typed IPC 把配置交给 Electron Main，Main 再通过 `model/create` 或 `model/update` JSON-RPC request 把 `apiKey` 发送给 Runtime。Runtime 将 Key 写入受保护的 `models.json`。Key 不应出现在模型列表/读取响应、SQLite、Event/Execution Feed 或正常日志中。

当前 Model Catalog 只包含以下 Provider 和 Model ID：

```text
deepseek: deepseek-v4-pro, deepseek-v4-flash
minimax:  MiniMax-M3
kimi:    kimi-k3, kimi-k2.7-code-highspeed
volcengine: deepseek-v4-pro-ga-260813, deepseek-v4-flash-ga-260731,
            glm-5-2-260617, glm-5.3, minimax-m3,
            doubao-seed-evolving,
            doubao-seed-2-1-pro-260628, doubao-seed-2-1-turbo-260628,
            doubao-seed-2-0-code-preview-260215
```

ModelConfigStore 要求配置与内置 Catalog 严格匹配。当前内置 Model Catalog 使用 OpenAI-compatible Chat Completions。Chat Completions 是不支持 Responses、Custom Tool 或 Grammar 的模型的兼容路径，不是废弃路径。Runtime 另外保留一个按 ModelProfile wire API 路由的 OpenAI Responses native adapter。Responses profile 使用这个 adapter；只有 `supports_custom_tools=true` 且 `supports_tool_grammar=true` 的 profile 才会暴露 native Custom `apply_patch`，其他 Responses profile 仍发送 Function Tool。Chat Completions profile 继续使用 Pydantic AI 的 Function Tool API。Responses stream 只有 `response.completed` 可以产生可执行的 normalized response。`response.failed`、`response.incomplete`、`error` 和没有 terminal event 的 EOF 都 fail closed。模型请求取消覆盖流式上下文建立和 SSE 等待阶段。流建立后，Runtime 关闭 Responses stream，并取消等待中的 `anext` task。两条路径都复用 RuntimeAsyncKernel 的同一 asyncio loop。Runtime 不提供 arbitrary custom provider、arbitrary base URL、arbitrary model ID 或主动 capability probe。

每个 Run 固化 Model Profile 和 Extension Snapshot。Model Lease 使用该快照创建 Provider Client。Model Attempt 保存 usage、响应元数据、有限的 transport retry 诊断和稳定 Eidos 错误码。Model Client 不拥有 Runtime Event Loop；共享 RuntimeAsyncKernel 负责其异步 I/O。

## 8. Tool Execution

Tool Registry 保存 ToolSpec、输入和结果 Schema、Execution Policy、Concurrency Policy、Projection Policy 和 provenance。每个 Step 固化可见 Tool Set、Contract Hash 和 Extension Snapshot。

Runtime 对每个 ToolCall 执行以下阶段：

```text
Validate → Prepare → Permission Decision → Durable Intent
→ Execute → Verify → canonical ToolResult → Event / Context projection
```

ToolExecutionController 负责 ToolCall 的生命周期、deadline、cancel 与迟到结果仲裁、结果校验、敏感扫描、Projection 和事务提交。Workspace mutation 会在 Prepare 阶段读取当前文件，并生成 Base Hash 和完整 Diff。Workspace Permission 会直接授权普通文件变更。Runtime 会先提交 Durable Intent，再复检版本并受控提交已有文件的原地写入或新文件的排他创建。Runtime 会保留并展示已应用的完整 Diff。未知副作用会保留 `sideEffectsMayExist` 和 `reconciliationRequired`。Shell process manager 在 Run 内保留进程和有界输出读取任务。`run_shell` 可以先返回运行状态，模型随后使用 `write_stdin` 继续等待。Run 取消或收尾会清理原进程组。

已声明 Tool 的载荷类型正确但参数契约校验失败时，Runtime 会在 Prepare 前生成并提交 `invalid_arguments` Tool Error。该 ToolCall 仍然进入 SQLite、Event 和下一次 Model Context，但不会触发 Approval、Durable Intent 或 Tool Runtime。载荷类型错误、未声明 Tool、重复或无效 Call ID 等协议错误仍然进入 protocol repair。

`list_files`、`read_file`、`read_file_range` 和 `search_text` 的 Contract 只校验参数类型、大小和明显非法语法。ToolExecutor 的只读 Path Authority 再把相对路径绑定到 Workspace，把 canonical absolute path 绑定到 Workspace 或当前 Run 的 active Skill root。结果投影也遵循这个 authority：Workspace 结果保持 Workspace-relative，active Skill 结果返回 canonical absolute path，目录结果保留末尾 `/`，因此只读 Tool 结果可以直接 round-trip 到下一次只读 Tool。active Skill root 只读。未授权的 absolute path 返回普通 Tool Error，不进入 Tool 参数契约错误或协议修复。写入 Tool 使用 Workspace-relative 路径；`run_shell.cwd` 还接受 Workspace 内的 canonical absolute path，并在 shell launch boundary 归一化为 Workspace-relative 路径。active Skill root 和 Workspace 外路径不能成为 Shell cwd。

`apply_patch` 的 Function 和 Custom 路径统一使用 Codex Patch 文本。Function 的 `ApplyPatchInput` 只接受一个非空字符串字段 `patch`；Custom 直接接收 raw Patch。两条路径共用操作说明、`parse_patch` 和 Workspace prepare/commit，不再保留结构化 `changes/chunks` 输入或编码层。说明包含 Add、Update、Delete、Move、空文件、上下文、EOF 和最小示例。Function 说明明确标记历史 `changes` 格式已停用；Custom 说明明确禁止 JSON 包装。Parser 不读取 Workspace，也不执行匹配或写入；它把文本转换为 Eidos 的 Add、Update、Delete AST。统一的 Workspace write resolver 把 Workspace canonical absolute path 归一化为内部 relative path，并同时处理 source 与 move destination。Workspace 外的 canonical absolute path 会先经过现有 PermissionPolicyEvaluator 和权限物化检查，再准备完整 Diff。现有 ApprovalCoordinator 为本次路径、Diff、版本和权限请求建立持久审批；有效 Run Grant 可复用。审批通过后，作用域重新验证目录身份，提交 helper 再核验 Base Hash。现有 Workspace prepare、写前版本检查、边界和最终校验继续负责语义和安全事实。

`ModelToolCall` 的裸 `dict` 永远表示 Function payload。Custom payload 必须显式使用 `CustomToolPayload`。`tool_calls.payload_kind` 是 SQLite 中独立且受约束的类型 authority。ContextBuilder、DB mapper 和下一次 Provider projection 都读取这个字段，不再从 `arguments_json` 猜测类型。历史 Custom call/result 在 Responses 中继续投影为 native Custom item，在 Chat Completions 中投影为有界的普通历史信息。当前 Step 固化的 Tool Definition 仍然是当前输入 contract 的唯一 authority。 `apply_patch` builtin provenance 的 `sourceVersion` 为 `2`，新的输入 schema 和说明参与现有 Contract Hash；`contractVersion=1` 仍表示未变的 ToolSpec/ToolResult envelope 版本。历史参数和结果保持原样，不转换、不重放，不迁移 SQLite。旧输入重新提交时会得到有界的 `invalid_arguments` 提示，包含当前 `patch` 格式示例和无文件修改的说明。Patch 格式错误会提示修正标记，匹配失败会提示重新读取当前文件。模型通过现有 Loop 纠正输入；Runtime 不猜测参数、不放宽匹配、不自动重试写入。v7 到 v8 migration 只在迁移边界兼容旧的 native `apply_patch` envelope。

`apply_patch.lark` 的语法来源是 `openai/codex` 的 `codex-rs/core/assets/tools/apply_patch.lark`。本地 grammar 将上游的 `add_line+` 改为 `add_line*`，因为 Codex Rust streaming parser 允许没有内容行的 Add File；显式的 `+` 仍表示一条空内容行。上游 grammar 中的部分空行正则不能直接交给 Lark，所以本地 grammar 也对这些 token 做了 Lark 兼容适配。Parser 可以规范化 CRLF 和外层空白，也支持首个 Update 片段不带 `@@`。Parser 不会猜测缺失的 `*** Begin Patch`、`*** End Patch` 或 `+`/`-` 前缀。Function 使用 `{ "patch": "..." }` 包装，Custom 不使用 JSON 包装。

Tool Result 的 `reconciliationRequired` 是本次执行是否建立 reconciliation barrier 的权威结果。`sideEffectsMayExist` 只保留历史证据。它不是完成条件。Runtime 只有在 Tool Result 缺少显式 reconciliation 判断时，才为旧结果使用保守兼容规则。敏感扫描或结果超限导致 projection 重建时，Runtime 仍保留显式的 `reconciliationRequired=false`，不会把它改成 unknown。未清除的 barrier 会阻止 Run 提交 `succeeded`。Runtime 不会自动重放有副作用的 Tool。

Shell 每次 ToolCall 只等待一个窗口。Shell 输出会持续追加到原命令 Item。命令退出后，Runtime 更新原命令的完整 `executionStatus`、`exitCode`、`stdout`、`stderr` 和 `termination`。普通且确定的 Shell 退出会把 Item 按命令结果标记为 `completed` 或 `failed`。确定的 `shell_exit_nonzero` 会把 Item 标记为 `failed`，但会把 ToolCall 标记为 `completed`。Workspace observation 不完整只影响 observation metadata，不会把已退出的 Shell 变成只读 reconciliation。`reconciliationRequired = true`、timeout、background child 清理未完成和真正的 Shell 启动失败仍然把 ToolCall 标记为 `failed` 并保持 fail closed。

只有执行最终状态未知时，Shell 才会建立 reconciliation barrier。Reconciliation 默认把 Run 置为 `CONTINUE_READ_ONLY`，而不是直接 interrupt。Barrier 只允许安全只读 Tool。其他副作用 Tool 会得到普通的 `reconciliation_required` Tool Error。Workspace refresh 只会清除来源属于 Workspace mutation、且可以由该 refresh 核验的 barrier。Shell、MCP、external、Eidos-state 和 unknown barrier 不能由 Workspace refresh 清除。Runtime 不会自动重放原 Shell。unsandboxed 或 additional permission 失败，以及 MCP、external、Eidos-state 的未知结果继续 fail closed。

内置 `workspace_dependencies` Tool 通过一个只读目录接口返回 Eidos 自带并经过校验的 Python、ripgrep、Python import roots 和受支持包版本。它在成功结果的 `data` 中返回 `activeSkillDependencyBindings`，并可返回 `defaultDependencyBindingId`。校验哈希只保留在 Runtime 内部，不在 Tool result 中回显。这个 Tool 的 model projection 只保留 `code` 和 `data`；canonical result 和 UI result 继续保留 Runtime 状态字段。调用方使用这些路径执行已有 Workspace 任务。这个 Tool 不创建新的执行器，也不绕过 Shell、Approval 或 Seatbelt。实际命令仍然进入现有 `run_shell` 链路。`run_shell` 输入的绑定字段是 `dependencyBindingId`。普通没有 binding 的 Shell 保持原有环境和执行路径。

Bundled Runtime dependency 使用三层资源边界。Skill 内容层保存 `SKILL.md`、`agents/eidos.yaml`、脚本和其他 Skill 资源。产品依赖层保存 `resources/runtime-dependencies/` 中的 Python 与 Node 锁定输入，并把构建结果放入 Bundle 的 `dependencies/` 目录。Runtime manifest 层在 Bundle 根目录生成并校验 `runtime.json`，文件记录固定版本、相对路径和 SHA-256 inventory。Catalog 层在 Runtime 初始化时读取并完整校验这个 manifest 一次，并把同一个已校验 Catalog 传给每个 Run。`workspace_dependencies` 不再承担首次 Bundle 全量校验。Shell 启动前仍会重新校验绑定，防止 Bundle 在启动后发生变化。Activation 层在 Run 中固定 manifest hash、Bundle snapshot hash 和 Skill requirements，并为每个 active Skill 生成 `dependencyBindingId`。Resource 层把已验证 binding 投影到现有 Shell environment 和 permission profile。它把 Bundle root 和依赖 root 设为受保护的只读路径。它把 Workspace 保持为唯一的工作目录。

`runtime.json` 只描述随 App 发布的系统依赖。Python 依赖位于 Bundle 的 `dependencies/python`，Node 依赖位于 Bundle 的 `dependencies/node`。Skill 的 `agents/eidos.yaml` 只保存 typed declaration。Skill 目录不承载依赖包。Node loader 使用 Bundle 的 Node 和 `node_modules`，并为 CJS 与 ESM 保留各自的相对导入语义。普通没有 binding 的 Shell 仍走既有 `run_shell`、Host shell snapshot、Approval 和 Seatbelt 链路。

已有普通文件的候选内容通过 stdin 有界传入受控 helper，不生成临时文件。helper 使用 `O_NOFOLLOW` 打开并核验目标，检查当前内容哈希后执行 `ftruncate`、完整写入、`fsync` 和内容/路径身份核验。Runtime 不再克隆已有文件，也不再复制或逐字节比较 xattr/ACL。文件保留原 inode，系统管理现有元数据。真实 ACL 或 mode 拒绝写入时，Runtime 不会通过替换 inode 或 chmod 绕过限制。Runtime 仍然拒绝 symlink、hardlink、特殊文件、owner 不匹配、特殊 mode 和文件 flags。

文件提交仍使用现有 Seatbelt helper；外部路径使用物化后的附加权限，Sandbox 的 Workspace Root 仍是原工作区。已有外部文件只申请该文件的写权限；创建新文件时才申请最近的已有父目录。沙盒不可用时，Runtime 只在现有策略允许且用户明确批准后启动无沙盒 helper。审批不提供系统管理员权限，也不覆盖永久拒绝路径。新文件继续使用排他 rename 提交；Add 覆盖已有文件、Move 覆盖已有目标都走原地写入。多文件不提供整体事务。截断后没有普通取消点，helper 完成当前有界文件后再停止后续修改；异常、超时或非约定 helper 退出进入不确定结果。Runtime 保留已提交的前缀变化和取消后的文件结果，不会自动回滚或重放。写前哈希检查不提供跨外部编辑器的原子 CAS 保证。外部文件的 Durable Intent 使用 `mutationScope=external_file`。Workspace 刷新不能解除外部文件的不确定状态；Repository 在事务内重新检查意图范围，避免把外部修改误标为已核实。

只有同时满足 `parallel_safe`、无副作用、参数安全和共享 Kernel 条件的只读批次可以在自身的有界范围内并发执行。每个 Run 的 Workspace write、Shell、Eidos-state、MCP 和 external Tool 实际副作用窗口使用该 Run 自己的独占门，所以同一 Run 内保持串行，不同 Session 的 Run 可以并行，即使它们共享同一个 Workspace。Approval 等待不占用该 Run 的副作用门。并发结果最终按模型声明顺序提交。

## 9. Sandbox & Approval

ApprovalCoordinator 把扩权 Approval request、用户 decision、feedback、暂停状态和恢复状态写入 SQLite。普通 Workspace 文件变更和默认 Workspace Seatbelt Shell 不创建 Approval。模型可以用 `networkAccess=request` 表达高层联网意图。Shell contract 会把该 intent 规范化为现有 additional network permission。Runtime 会在启动进程前请求 Approval，获批后仍使用 Seatbelt。旧的底层权限输入继续兼容。联网、附加路径、unsandboxed、MCP 和 Eidos-state 副作用继续使用 Approval。Approval 不能修改 Tool 参数，也不能删除永久拒绝、Runtime 保护路径或 hard confidentiality deny。

默认 Shell attempt 使用 macOS Seatbelt。Seatbelt Policy 根据 Workspace、Runtime root、Eidos 数据目录、永久拒绝、附加权限和网络权限物化为 effective permission profile，并保存 profile hash。默认 Workspace profile 在权限校验和 Durable Intent 后直接启动。显式权限升级会创建新的 Approval attempt。Runtime 只有在 hard confidentiality deny 不存在并且 policy 允许时才允许 unsandboxed attempt。

### Shell

HostShellResolver 先读取当前账户的 login shell，再读取 `SHELL`，最后按 `/bin/zsh`、`/bin/bash`、`/bin/sh` 顺序选择 fallback。Resolver 只接受存在、可执行且 basename 为 `zsh`、`bash` 或 `sh` 的绝对路径。

ShellEnvironmentSnapshotProvider 使用 resolved shell 的 `-lc` 做一次 bounded 环境捕获。默认 attempt 会先验证 Seatbelt profile，然后在同一个 effective Seatbelt 边界内执行 trusted capture script。捕获使用 NUL 分隔的环境格式。捕获上限是 512 KiB，超时是 10 秒。缓存 key 包含 shell executable、canonical cwd 和实际 capture launch identity。同一个 key 不会重复捕获。普通命令使用 resolved shell 的 `-c`，不会使用 `-lc`。

捕获失败时，Provider 使用 sanitized parent environment，并记录有界的稳定 warning。Snapshot 不恢复 aliases、functions 或其他 shell state。

Shell 的 effective environment 保留真实 `HOME`、snapshot 的 Host `PATH`、真实 `TMPDIR`、`USER`、`LOGNAME`、`LANG` 和 `LC_*`。Runtime 只把 bundled `rg` 的目录去重后追加到 `PATH` 末尾。Provider 在启动 login shell 前移除继承的 `EIDOS_*` 和 packaged Runtime Python control environment。用户 profile 随后声明的普通开发环境仍会进入 snapshot。`run_shell` 不从 `models.json` 注入 API Key，也不强制禁用用户的 Git 配置。`HardenedGitRunner` 仍是独立的 Git 执行路径。

Shell reader 为 stdout 和 stderr 分别保留 UTF-8 增量解码器。每次收到的字节只会生成已经可以解码的文本片段，结束时再 flush 未完成的解码状态。Shell handler 按安全扫描器释放顺序把安全片段追加到同一个 Item content，并保留每个片段的顺序事实。Runtime 在每次等待窗口结束后把进程 session 和增量输出交给模型。模型通过 `write_stdin` 继续等待或停止命令。内部跟进 Item 保留审计记录；Renderer 把正常跟进隐藏，原命令卡片按 `executionStatus` 显示运行状态。

Desktop `ExecutionFeed` 的 Shell Item 默认折叠。用户展开后，Feed 在 Shell 运行中和终态都渲染累计的 Item content。它在已有累计内容时不再追加结果中的最终 stdout/stderr，因此不会重复输出，也不会丢失 stdout/stderr 的接收顺序。旧 Item 缺少或为空的 `content` 时，Renderer 使用结果中的 stdout 和 stderr 作为兼容回退。待审批、已批准和已拒绝的 Shell 历史都使用这个 Shell Item，审批状态单独显示。

Shell 输出在 Renderer 中使用成熟的 ANSI stripping 实现转为纯文本。ANSI 和 OSC 控制序列不会被解释，OSC 超链接也不会被激活。Shell Result 的 `attemptCount`、`sandboxed` 和 `escalated` 字段继续作为已有执行与权限事实，并在 Feed 中展示。

`run_shell` 的模型结果投影对 stdout 和 stderr 各保留首尾，每条流最多 16 KiB，整个 JSON 最多 48 KiB。原始 `truncated` 和 `omittedBytes` 保持为原始 Shell 事实，不会被模型投影覆盖。模型投影使用独立的 `modelProjectionTruncated`、`modelProjectionOmittedBytes` 和 `modelProjectionContinuation` 字段，其中 omitted bytes 只计算模型省略的 stdout/stderr UTF-8 字节。

`read_tool_output` 是只读的持久 Shell 输出分页工具。它使用前一次 `run_shell` 的 provider tool call ID，默认读取 stdout，也可以读取 stderr。Runtime 只从当前 Session 的持久化终态 `run_shell` 结果读取，所以过去 Run 可以读取，运行中结果、跨 Session 结果、缺失 ID 和歧义 ID 会拒绝。`offsetBytes`、`maxBytes`（请求范围 4 字节至 16 KiB）和 `fromEnd` 控制分页；Runtime 会在 UTF-8 边界返回实际 `startByte`、`endByte` 和 `nextOffset`，实际页可以小于请求值。该工具不会重新执行 Shell，也不会清除 reconciliation。Shell 原始输出上限已经丢失的字节无法恢复。

### Default filesystem

默认 Seatbelt profile 允许全盘 file read、普通 executable 和 dylib 的 executable mapping。永久拒绝和 hard confidentiality deny 同时拒绝 file read、file map 和 file write。

默认 profile 只允许 Workspace、snapshot `TMPDIR` 和 canonical system temp root `/tmp` 写入。macOS 的 `/tmp` 会规范化为 `/private/tmp`。真实 `HOME` 和其他位置默认只读。

Seatbelt 永久拒绝 Eidos data 和 credential 路径的 read、write 和 map。data 内的 projectless 或 Worktree workspace 仍可读写。active Skill root 允许 read 和 execute，但拒绝 write。`.git`、linked Worktree metadata 和 Git common metadata 只读。Workspace `.env` 可以被进程读取，但输出仍经过 SensitiveScanner。默认 network 继续拒绝，只有 effective profile 启用 network 时才允许连接。明确的 network denial 不会进入通用 unsandboxed retry。

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

`ContextBuilder` 是默认在线 Run 的唯一模型输入投影器。它把 Project Rules、Skills、SQLite history、verified compact summary、Repository overview 和 Retrieval evidence 放入一个结构化 `ModelContextItem` 序列。每个 ModelAttempt 在 Sampling 前持久化完整的 `ContextSnapshot`。Snapshot 原样保存 model context、resolved instructions、tool definitions、Model/Rule metadata 和可空 Repository lineage。Sampling 只读取已绑定的 Snapshot。可重试的 transport failure 即使已经收到尚未持久化的 provisional text，也会先完成旧 Attempt，再创建独立 Attempt 并复用同一个 Snapshot。已有文本或 ToolCall 进度不会自动重放。协议修复会建立新的 ModelAttempt 和新的 Snapshot。已声明 Tool 的参数校验错误会通过持久化的 `invalid_arguments` Tool Result 进入下一次 Model Context。该结果只保留有界的错误码和摘要，不携带原始参数。真正的协议错误仍然使用 protocol repair context。

Workspace Explorer 复用 `RepositoryWatchController`。Watcher 事件只产生 `workspace/changed` 缓存失效通知。Renderer 根据相对路径刷新已加载的父目录。Watcher 不提供路径安全事实，也不修改 Run snapshot。

## 11. Persistence & Events

`state.sqlite` 是可变业务状态的唯一权威。它保存 Session、Run、Item、ToolCall、Approval、Tool Attempt、Execution Segment、Step、Model Attempt、Durable Intent、Event、Outbox、Async Operation、Extension Snapshot、Context lineage、Compaction 和 Checkpoint。业务状态变化与 Event/Outbox 仍在同一个 `state.sqlite` transaction 中提交。

Runtime 按职责使用多个独立存储。`repository.sqlite` 保存可重建的 Inventory、Index、Symbol、Reference、Chunk 和 FTS5 数据。它只保留每个 Workspace identity 的最新候选与最新完整 generation，并使用 incremental auto-vacuum 回收删除页。`thread_history.sqlite` 只索引按 Session 分段的 append-only Event JSONL。Runtime 先 fsync JSONL，再提交文件 offset；启动时会截断未提交尾部并继续投影。`logs.sqlite` 只索引本地日志 JSONL，当前使用独立 schema v2。Runtime 会把使用 `content_sha256` 的旧 v1 表迁移为 `chain_sha256`，也会接纳已经使用 `chain_sha256` 的 v1 表。日志按 8 MiB 分段，默认总量约 128 MiB，Runtime 优先删除最旧的 sealed segment。`memories.sqlite` 只保存 Memory metadata，正文使用 content-addressed Markdown 文件。当前 verified compaction 尚未自动写入 MemoryStore。

完整 ContextSnapshot 和 StepResolutionSnapshot 使用 gzip content-addressed Blob。`state.sqlite` 只保存版本、kind、相对路径、SHA-256 和大小。Runtime 对 owner、mode、路径、压缩数据、大小、JSON 和 checksum 执行 fail-closed 校验。Session 删除后，Runtime 会删除对应 history，并回收不再引用的 Blob。JSONL、Memory 和 Repository 数据都不能改变 `state.sqlite` 中的业务状态。

当前 `SCHEMA_VERSION` 是 8。新主库不创建 Repository 表。Runtime 支持 v1→v2→v3→v4→v5→v6→v7→v8 顺序升级。v5→v6 先把 Repository generation 写入临时数据库，完成完整性检查和 fsync，再原子替换 `repository.sqlite`。Runtime 随后删除主库中的 Repository 表并使用持久 marker 执行 `VACUUM`。v6→v7 新增 `run_dependency_snapshots` 和 `run_dependency_bindings`。v7→v8 为 `tool_calls` 增加受约束的 `payload_kind`，历史正式数据默认为 Function，迁移边界只对旧的 native `apply_patch` envelope 做一次性兼容 backfill。两个表继续使用 `state.sqlite` 作为业务事实来源。中断后，Runtime 可以重新复制或继续压缩。旧 `eidos.db` 会先 checkpoint WAL、检查完整性，再原子改名为 `state.sqlite`。未知 revision、未来 revision、双主库冲突和损坏 Blob 都 fail closed。

Outbox 投递失败不会删除事实。Runtime 重启会从 `state.sqlite`、Outbox、Long Task 和 Resource 状态恢复或进入 reconciliation。其他数据库和文件不参与跨库业务 transaction。

In-memory 对象只保存当前协调状态、缓存、活跃资源引用和诊断信息。它不是 Session、Run、Tool 或 Event 的第二个事实来源。

## 12. Observability / OpenTelemetry

Runtime 入口初始化进程级 `TelemetryProvider`。OpenTelemetry 是非权威 Observability 层，不参与 Run 状态迁移，也不替代 SQLite 业务事实。Telemetry 初始化、Span 写入、flush 或 shutdown 失败会被 Runtime 自身日志捕获，不应成为 Agent Loop 的状态来源。

当前 Trace 覆盖三个主要执行边界：

```text
eidos.run
  ├── eidos.model.attempt
  └── eidos.tool.call
```

Run Span 记录 Run、Session、Model 和终态。Model Attempt Span 记录配置 Provider、响应 Provider、resolved model、Provider response ID、响应状态、阶段、finish reason、Tool 数量、响应文本大小、TTFT、duration、transport retry 和 input/output/cache token usage。SQLite 的 Model Attempt 还记录响应文本哈希和受限协议诊断 JSON。诊断 JSON 只包含错误路径、Tool 名称、Call ID、参数字段名和类型、参数字节数、契约指纹与 Tool Snapshot 哈希。它不保存原始响应或参数值，也不生成模型 Tool 参数哈希。Tool Call Span 记录 Tool 名称、Call ID、Tool status、Workspace changed 和异常状态。

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
`workspaceRoot` 省略或为 null 时，Runtime 创建 projectless Session。该 Session 使用 Runtime 数据目录内的私有锚点目录作为内部执行 workspace 和 identity。默认路径是 `~/.eidos/.eidos-projectless/<session_id>`。自定义 `EIDOS_DATA_DIR` 时，锚点目录仍位于该数据目录内。该 Session 不会创建 Project、Worktree 或 Repository workspace。它固定为 Local execution。Run resolution 为这个系统 workspace 生成普通的 workspace permission profile，并保留数据目录保护。RunResources 仍创建文件工具、Shell、Skill、MCP 和 Plugin 资源。projectless Run 不注入 Project Rules、Repository Context 或 workspace-environment。Desktop 仍提供当前 Session 的 Files、输出内容和文件预览，但不提供 Git Review。

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

Local Git Session 还提供 `session/gitSwitchBranch` 和 `session/gitCreateBranch`。Runtime 从当前 Project workspace 读取 local branches。Runtime 只允许 clean、没有进行中 Git operation、且整个 workspace 没有 active Run 时切换或创建分支。`gitCreateBranch` 从当前 HEAD 创建分支并立即切换到新分支。两个方法都使用现有 `operations` 表和 `operationId` replay。当前分支仍以 Git HEAD 为事实，不新增 Session 或 SQLite 分支字段。Local Session 共用真实 workspace，所以不同 Local Session 在 Git 分支切换操作上共用这条 workspace 锁；这条操作锁不限制不同 Session 的 Run、Shell 或普通副作用并行。分支变化完成后，Runtime 会使 RepositoryWorkspaceRuntime 失效，下一次读取会重新观察 branch 和 HEAD。

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

Skill 当前按以下边界工作：

```text
Discovery → Catalog Snapshot → Selection
          → selected SKILL.md → existing tools / run_shell
          → active Skill root → Seatbelt Sandbox
```

Discovery 读取 bundled system、用户和 Plugin Skill 目录。系统 Skill 的私有目录读取会忽略 Finder 生成的 `.DS_Store` 文件，但仍拒绝其他不符合所有者、类型或权限要求的文件。`SkillCatalog` 使用共享的 `parse_skill_manifest` 校验 `SKILL.md` 的 frontmatter。Catalog 只保留顶层 `name`、`description`、qualified ID、source、version、source hash 和 content hash。`license`、`compatibility`、`metadata`、`allowed-tools` 等其他 frontmatter 字段可以存在，但不会变成 Catalog、Tool Schema、Permission 或 Sandbox 权威。

Turn 开始时，Catalog Snapshot 固化可用 Skill。Selection 当前支持用户输入中的 qualified `@source:name` 和唯一的 `@name`/`$name` 引用。SelectedSkillSet 固化本 Turn 的选中 ID。选中后，Runtime 才完整读取对应的 `SKILL.md`。Catalog 不把完整 Skill tree 注入 Context。

Skill 使用 progressive disclosure。Catalog 只提供发现信息。`SKILL.md` 是选中后的主说明。`skill_read_resource` 只在说明需要时读取 `references/`、`scripts/` 或 `assets/` 下的相对路径。Resource path 必须是相对于包含该 Skill 的 `SKILL.md` 的目录，且不能包含绝对路径、`..`、symlink 或非 regular file。选中的 active Skill root 也可以供四个只读文件 Tool 使用 canonical absolute path，但它不会因此获得写入权限。Skill 脚本不是新的 Runtime Tool；模型仍然使用已有的 `skill_read`、`skill_read_resource`、文件工具、`run_shell` 和其他已注册 Tool。

`agents/eidos.yaml` 是可选的 Skill metadata 文件。Runtime 使用统一 YAML loader 读取其中的 interface、asset、tool dependency、policy 和 `runtimeDependencies` metadata。`runtimeDependencies` 使用严格的 `RuntimeRequirements` discriminated union。它只接受有界的 Python package、Node package 和 executable 声明。文件有大小、owner、regular-file、路径和字段边界。无效的可选 runtime declaration 会保留固定 error code，不会清除旧的 interface、MCP dependency 或 policy metadata。无效的整个可选 YAML 会保留现有 display fallback，并报告固定 metadata error。`allow_implicit_invocation = false` 会禁止 Shell 识别自动激活该 Skill，但不会阻止显式选择或 `skill_read` 激活。`dependencies.tools` 不会安装 Python、pip、npm、系统命令或其他运行时依赖，也不会改变 Eidos 的 Permission、Approval 或 Sandbox。

`run_shell` 会经过 `SkillAccess` 对受信任 Catalog entry 做隐式脚本识别。只有支持的 runner 调用已知 Skill root 下的相对 `scripts/` 文件时，Runtime 才记录 implicit activation，并把该 Skill root 加入本次 Shell 的权限物化。Workspace root 保持读写；active Skill root 默认只读，并额外允许脚本所需的 executable mapping。用户对普通 Skill 的具体写入授权可以成为这个只读规则的例外，系统 Skill 永久禁写。带 runtime declaration 的 Skill consumer 必须从 `workspace_dependencies` 的 `activeSkillDependencyBindings` 中选择匹配 `skillQualifiedId` 且状态为 `ready` 的 binding，再把其 `dependencyBindingId` 传给 `run_shell`。模型不能使用顶层默认 binding 替代不匹配的 active Skill binding。无效或非 ready 声明不能按无依赖处理。隐式识别只提供 activation 证据，不会授权任意 Workspace 外路径。

Skill binary assets 会在安装和目录读取中按 bytes 保留。`skill_read_resource` 只返回有界 UTF-8 文本，因此 DOCX、PPTX、PDF、XLSX、PNG 等 binary resource 不会被当作文本注入 Context。支持图像输入的 Model 才会注册 `view_image`。`view_image` 从 Workspace root 或 active Skill root 读取受信任的 PNG/JPEG，并把经 hash 和 size 复核的 binary content 投影为 Pydantic AI 的 multimodal `BinaryContent`。其他 binary asset 仍由已有 Tool 或 Skill 脚本按其自身格式处理。

Skill MCP dependency 不是 Skill 安装器。RunResources 会收集 active Skill 的 `type: mcp` 声明，并与本 Run extension snapshot 中已配置且 available 的 Plugin MCP server 比较。Runtime 保存 installed、missing 或 unsupported 的结构化诊断。Runtime 也会把未满足项作为低权限 user context warning 提供给后续 Step。这个检查不会安装、启用或启动新的 MCP server。Plugin 声明的 MCP server 仍由独立的 Plugin/MCP 配置、consent、官方 Python MCP SDK stdio client、Tool Registry、Approval 和 Sandbox 链路管理。Runtime 不负责 pip、npm、系统包或 MCP server 的自动安装。

`SkillCatalog` 管理 bundled system skills、用户和 Plugin Skill。Turn 开始时，Catalog Snapshot 和 SelectedSkillSet 固化 qualified ID、source、version、source kind、content hash、canonical `file:` locator 和 implicit policy。`SkillAccess` 只从该可信 snapshot locator 激活 canonical root。模型不能通过传入任意 absolute path 扩大 Shell 权限。显式选择、成功的 `skill_read` 和已知 `scripts/` Shell invocation 都会沿 RunResources → ToolCallRuntime → Shell → Seatbelt 使用同一份 Run-scoped activation state。

MCP 当前使用官方 Python MCP SDK 的 stdio client。MCP Server 由 RuntimeAsyncKernel 持有长生命周期连接。Server Tool 会进入统一 Tool Registry，保留 MCP provenance，并按 external Tool 经过 Approval、Sandbox、timeout、结果校验和 reconciliation。MCP 进程使用受控环境、进程组和 connector 或 workspace-read Seatbelt policy。

## 15. Packaging & Distribution

源码开发使用仓库 `.venv/bin/python`，Runtime root 是仓库 `runtime/`。打包开发路径从 `process.resourcesPath/runtime/` 解析 bundled Python 和 `runtime/app`，不回退到系统 Python、PATH、`.venv` 或用户 `PYTHONHOME`。

`build-macos-runtime.sh` 生成 macOS arm64 的 self-contained Runtime Bundle。Bundle 包含 managed CPython 3.12.13、锁定的 production dependencies、Eidos Runtime、Seatbelt 资源、`apply_patch.lark` grammar、受管 Ripgrep、Node CJS/ESM loader 和 Node package root。构建阶段会生成并校验 Bundle 根目录的 `runtime.json`，也会检查 grammar 和依赖资源存在，并通过 bundled smoke 验证 Bundle 内导入。Electron Builder 将 Bundle 放入 App resources，DMG 目标只配置 arm64。

`package:mac` 生成未签名本地 DMG，并执行 packaged App、Runtime、SQLite、Seatbelt 和从 DMG 复制 App 的 smoke。Release 流程必须先完成 Bundle 文件复制和 `runtime.json` 生成，再进行嵌套文件签名。签名后要刷新并重新校验 manifest hash，之后才做最终 `codesign`、notarization 和 stapling。`package:mac:release` 要求 Developer ID 和 Apple notarization credentials，随后执行 hardened runtime、签名、notarization、stapling、`codesign`、`spctl` 和 `stapler` 验证。没有对应 credentials 时，仓库不能验证真实签名结果。

## 16. Runtime Recovery

Runtime 启动时会收敛未完成的 Run、ToolCall、Approval、Outbox 和资源状态。对于没有未完成 Tool 执行或不确定副作用的 active Run，只有在没有 cancel request、没有 reconciliation barrier、没有未决有副作用 Durable Intent，且没有 running ToolAttempt 时，Runtime 才会把 Run 和执行段重新排回队列。其他不确定执行仍然进入 `interrupted`。Pending Approval 仍按既有的结构化待批规则恢复。Cancellation 在 SQLite 中先记录 request，再通过 Run Worker、Model request、Tool process、Approval wait 和 Async Task 传播。取消会停止执行。未清除的 reconciliation barrier 会使 Run 返回 `interrupted`，并保留副作用事实；Worker 已退出时，取消 RPC 正常返回。`sideEffectsMayExist` 只是历史证据。迟到结果不能把已取消 Run 改回成功。

Long Task 控制事实写入 `operations` 的 `long_task/control` scope。`run/pause` 在模型、工具和 Approval 安全点生效。`run/resume` 需要重新记录 Workspace identity、规则、Repository/Context snapshot、permission snapshot、Git 和 reconciliation 检查结果。未确认副作用不会自动重放。

Workspace-local 的 reconciliation 可以在当前 Run 中通过受限只读 Tool 继续。Workspace refresh 只能清除 Workspace mutation 的可核验 barrier，不能清除 Shell、MCP、external、Eidos-state 或 unknown barrier。未清除的 reconciliation barrier 仍然阻止成功终态。没有安全核验路径的 timeout、background child、unsandboxed、additional permission、MCP、external 和 Eidos-state 未知结果仍然 fail closed。

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

## Approval R1：权限请求闭环

`PermissionPolicyEvaluator` 只根据 Base、当前 Run Grant、动作请求和硬限制返回 `allow / ask / deny`。该组件不访问 SQLite、不创建 Approval，也不执行 Tool。权限类型继续使用 `AdditionalPermissionProfile`、`NetworkPermissions` 和现有文件权限条目。

模型可以调用 `request_permissions` 申请网络和具体绝对路径权限。空请求会进入 Tool 参数校验错误。Runtime 会先规范化路径并检查保护边界。已有权限返回 `already_granted`；受保护权限返回 `permission_not_requestable`；可申请权限通过唯一的 `ApprovalCoordinator` 等待批准或拒绝。

Run Grant 由当前 Run 中已批准的 `approvals.request_json` 派生。请求必须带有 `kind=permission_request` 和 `grantScope=run`。SQLite 对 Approval 的原子决策同时决定 Grant 是否存在，因此没有新的授权表和第二份状态。Run 终止后，Grant 不再生效。普通 Shell 把 Base、Run Grant 和单次动作请求一起物化为 `EffectivePermissionProfile`。Seatbelt 只消费该结果。显式 Shell 扩权仍只授权当前动作。

已知网络 denial 会进入同一个权限请求流程。Runtime 会在 Approval 中保存已完成的 Shell 结果。用户批准后，当前调用返回 `permission_granted_retry_required`，模型可以再次调用普通 Shell。Runtime 不会自动重跑这个命令。拒绝返回 `user_rejected_network`。相同请求的拒绝按持久化 fingerprint 去重，不会阻止不同命令或不同类型的审批。LoopGuard 的状态包含权限状态，权限变化后允许模型重新执行相同动作。

Runtime 重启可以恢复 R1 的结构化待批请求。恢复只接受没有不确定执行的暂停点。未执行的动作会重新准备并检查当前 Tool 契约和原审批 fingerprint。网络 denial 的恢复只交回保存的已知结果，不重放 Shell。缺少完整事实、契约变化、取消和不确定副作用继续 fail closed。恢复仍使用原 Approval、原 Run worker、ApprovalCoordinator 和 approve/reject RPC。

数据库版本为 10。v8→v9 只为 `tool_calls` 增加可空 `raw_arguments_json`。新调用保存经过敏感内容检查的 Provider 原始参数，`arguments_json` 继续保存执行所用的规范化参数。旧行保持 NULL，Runtime 不会伪造历史原始参数。v9→v10 将全局 `running`/`finalizing` 唯一索引改为按 `session_id` 的唯一索引；迁移不改写旧的 `waiting_approval` 重叠记录，恢复 admission 按 Session 串行处理。旧版本逐级迁移，失败会回滚。

Desktop 的 `ComposerSlot` 在 `waiting_approval` 时用 `ApprovalComposer` 替换普通输入框。文件、Shell、网络、MCP 和权限申请共用批准、拒绝、响应中、失效和错误状态。Eidos State 的既有文件变更映射保持兼容。Feed 只显示审批历史。Session 的 `activeRunStatus` 从 Active Run 派生，不写入 Session 表；Sidebar 对等待审批显示“等待批准”。

## Run 收尾与 Shell 执行期限

- 模型提交最终答复时，Runtime 在同一事务提交答复和 Run 终态。未清除的 reconciliation 会使 Run 进入 `interrupted`，不会再强制模型继续只读核验，也不会清除未知 Durable Intent 或放行新副作用。
- 取消后 Worker 已退出时，RPC 返回 `canceled` 或 `interrupted`。系统记录取消完成时间；副作用未知不再作为取消失败。无 Worker 的 queued Run 如果已带有未确认副作用，也进入 `interrupted`。重复取消已中断 Run 返回原终态。Worker 仍存活时继续报告 `RUN_CANCEL_TIMEOUT`。
- 新 Run 的 Shell 不再设置默认进程总期限。`run_shell` 首次等待默认 10 秒，范围 250 毫秒至 30 秒；模型随后使用 `write_stdin` 等待，默认 30 秒，范围 250 毫秒至 60 秒。等待窗口到期只返回 `shell_running` 和 `sessionId`，不会结束进程或触发 reconciliation。ToolSpec 的 600 秒 watchdog 只限制单次启动、审批重试或跟进调用，审批等待不计入预算。历史 3600 秒 ToolSpec 仍可读取，但新工具不会用它限制进程寿命。
- 控制器把已有 Shell 结果转成超时或取消结果时，会保留已有输出和终止信息，并继续执行结果校验、输出限额和敏感扫描。进程清理和未知副作用仍按原规则处理。此修改不恢复旧结果中已经缺失的 stdout/stderr。
- Runtime context 使用 `recentToolErrorFingerprints` 表示最近工具错误。空列表不代表对账完成。LoopGuard 不把 assistant 文本变化或最近错误列表中的错误消失单独当作新进展。


### Shell 会话收尾

Shell 结果使用可选字段 `outputComplete` 和 `outputCaptureError` 分别记录输出完整性与安全原因码。`truncated`、`omittedBytes` 继续只描述原始输出上限。进程明确正常退出后的 `output_read_failed` 不单独建立对账屏障；扫描拒绝、持久化失败、进程读取循环异常和未知退出继续保留对账。结果携带原命令的 `outputCallId`，模型用它读取持久化输出，不能把 Shell `sessionId` 当成该 ID。`read_tool_output` 的分页结果保留 `outputComplete`；读取到保留内容的末尾不代表原始输出完整，旧结果的该字段保持 NULL。

Context 从未决 Durable Intent 投影最多 16 条 `reconciliationOrigins`，包含原工具、provider call ID、原因码和输出捕获原因，不注入原始命令或异常正文。空列表不代表对账已完成。LoopGuard 在同一 epoch 下最多接受三轮带有未解除对账的工具结果；新的普通读取结果不重置计数。Step 进度签名保存对账与有效 Shell poll 标记，旧签名默认没有该标记。正常 Run 不受该计数限制，有效空输入 Shell poll 不计数。达到限制后，Runtime 使用现有 Finalizer 汇报阻塞，保留未决 Intent 和对账事实。原 Shell 退出回调已经记录的不确定结果，不会因空输入轮询再次返回而重复增加 epoch。

`ShellProcessManager` 继续使用现有 Run 资源和输出读取线程。每条输出流持有同一个敏感扫描器，轮询不会结束扫描器，也不会释放尚未完整的行。输出上限继续由原始字节计数控制。模型轮询只读取尚未交付的安全输出。

启动 ToolCall 可以完成一次 `shell_running` 观察，但原 Durable Intent 保持 `running`。Runtime 在初次结果提交后才启用退出回调。退出回调更新原命令的 canonical/UI 结果、Workspace diff 和 Intent，并在同一 SQLite 事务中产生事件。Runtime 不会改写模型已经收到的历史结果。`read_tool_output` 在命令退出后读取原命令累计输出。

当前 Run 的 `ToolConcurrencyGate` 在命令仍然运行或退出结果尚未提交时保留 Shell 占用。这个 Run 的其他副作用调用返回 `shell_session_busy`，模型可以继续只读工作、使用 `write_stdin` 管理原 Shell 或等待原命令。其他 Session 的 Run 使用各自的 gate，可以在同一个 Workspace 并行执行 Shell 和其他普通副作用。空输入观察不会新建 Intent，也不会重复获取该 Run 的 gate；非空输入先核对 Run 内会话，再创建输入 Intent。输入沿用原进程权限，不能借此扩权。同一个 Workspace 的并发修改可能让 Workspace observation 或 diff 包含其他 Session 的变化。权限、Sandbox、Durable Intent、取消和进程组清理仍按各自 Run 独立记录和收敛。

模型直接提交最终答复时，Runtime 先停止活跃命令并完成退出记录。Runtime 保留答复，但把提前停止命令的 Run 记为 `interrupted`。其他最终化和取消路径也会清理会话。已知退出与副作用未知分别记录；Runtime 不会把正常等待写成 reconciliation。运行中的 Intent 在重启后继续走原有恢复流程，Runtime 不会根据 PID 重连或自动重放命令。LoopGuard 仅对当前 Run 中仍运行且参数有效的空输入观察跳过重复判断。

### Projectless 文件提交与 Skill 写入权限

`secure_workspace_move` 向受控 helper 传递已核验的 Workspace 目录 fd。helper 核对目录身份，并从该 fd 向下使用 `O_NOFOLLOW` 访问目标。它不再从 `/` 逐级打开 `.eidos` 等受保护祖先。当前 Projectless Workspace 的普通文件沿原有 Workspace Permission 执行，不逐次审批；其他会话和数据文件仍受数据目录 deny 保护。helper 的新建提交按 errno 区分目标已存在与其他失败，诊断只记录固定阶段和数字 errno。

Base/Effective Permission 的 `approvalWriteRoots` 标记用户 Skill 存储目录。字段默认空，旧权限快照仍可读取；新 Run 的产品权限工厂填入数据目录的 `skills`。Skill 激活只提供读权限，普通 Skill 的写入必须来自明确的附加授权。Runtime、PermissionPolicyEvaluator 和 Seatbelt 使用同一授权范围；精确文件授权不会开放同目录其他文件。Seatbelt 只允许获批目标祖先的 metadata 检查，不开放祖先目录内容。`skills/.system` 作为独立 protected write path 永久禁写，即使它被选作 Workspace 也不能修改。

Runtime 不启动会丢失永久写入保护的裸 Shell 无沙盒执行。受控文件 helper 可在明确审批后不使用 Seatbelt，但 Runtime 会继续核验每个目标的保护范围、身份和版本。权限等待后的重物化、Durable Intent、最终内容验证和 Reconciliation 规则保持不变。Skill 文件修改不重写当前 Turn 的 Catalog Snapshot；后续 Turn 重新发现修改后的内容。

### 产物预览与反馈

本轮实现沿用 `Renderer → typed preload IPC → Main → RuntimeClient → WorkspaceExplorerApplication → WorkspaceReader`。Main 的 `ArtifactPreviewManager` 只保存短期资源授权和原生网页视图。文件权限、Workspace 身份和有界读取仍由 Runtime 判定；Renderer 和网页不直接使用 Node 文件 API。资源授权绑定 WebContents、Session、execution root、Workspace 身份和所选文件版本。每个资源块都重新检查版本。文本资源完整扫描后才释放原始字节；扫描产生脱敏替换时，原始资源会被拒绝。

图片/PDF 在主 Renderer 中作为被动资源加载。可运行的 HTML 使用没有 preload 的 sandboxed `WebContentsView`，并使用独立临时 Session。该视图不共享主应用的 IPC 或 Cookie。Main 注册资源协议、拒绝额外权限和下载，并在生命周期结束时清理视图及授权。Browser 的网页和滚动状态不是持久业务事实。网页地址识别在 Renderer 边界使用直接依赖的 `tldts`；不能识别为地址的无空格输入转为 Google 搜索。刷新通过 Renderer 状态触发一次逆时针半圈图标动画，不显示独立加载提示。当前网页面板不渲染标注入口。

`record_workspace_change` 在同一 SQLite 事务内记录准备后的 Diff 和 `item.updated` Event。Event/Outbox 将更新后的 Item 投影为 `item/updated`。Desktop 的“最近一轮”从已有 Item/ToolCall 读取，不维护另一个 Run 成功状态。准备中、完成和失败仍使用原 ToolCall 状态。原始模型参数流不作为可执行补丁展示。

Desktop 的 `TurnResults` 在每个 Run 的最终回答文本之后、回复复制和反馈操作之前投影文本修改和格式化产物。它从分页后的 Session Item/ToolCall 记录读取，不创建 Artifact 表或第二套执行状态。Projectless Session 同样投影文本修改卡。文本卡通过独立 text-review 面板审查持久化补丁，不依赖 Git；面板不列入工作区工具菜单。文本修改按路径归组；重复路径保留各次完整补丁的累计增删，并显示“累计”及非净差异说明。任一次缺少补丁时，统计保持未知。HTML 卡片通过现有受控文件预览读取当前 `<title>`，不把文件名作为正文标题。`OutputContent` 在环境信息中按路径保留当前 Session 最新产物记录。Files 只负责文件树和预览，不再负责结果列表。

修改卡的 Review 请求携带 Run、文件路径和 Item 定位，并打开右侧的最近一轮 Diff。支持内置预览的产物卡同时复用 Workspace Explorer、隔离 HTML 预览和 Main 的受控系统应用打开入口；DOCX 没有内置预览时只展示系统应用打开。当前没有可证明安全的本轮 Checkpoint 恢复入口，因此卡片的撤销操作保持禁用并显示原因。

`session/gitReadPatch` 读取 index 或 worktree 文本差异并返回 Diff hash。`session/gitApplyHunk` 在现有 Session Git operation 边界内检查 active Run、路径和当前补丁 hash。现有 `unidiff` 负责选择 hunk，原生 Git 负责校验和修改。操作必须携带 operationId，沿用 SQLite 幂等与不确定操作语义。本轮没有数据库迁移或第二套 Artifact 表；浏览器地址识别新增直接依赖 `tldts`。

当前修订的自动测试、构建、原生 Seatbelt 和 Electron smoke 已通过。真实 Desktop 操作验收仍未完成，具体边界见 `current-limitations.md` 的“本轮产物实现的边界”。
