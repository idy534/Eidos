# Eidos 当前限制

本文只记录 Stage 4+ 后续能力、其他平台支持，以及没有 Apple credentials 时无法验证的真实签名结果。本文不记录已经解决的问题，也不记录历史阶段。

## 平台与分发

- Eidos 当前只支持 macOS arm64 Desktop、Runtime Bundle、App 和 DMG。
- Linux、Windows、macOS x64、Universal binary 和其他平台的 bundled Runtime 与 Ripgrep artifact 尚未实现。
- Auto Update、GitHub Release 发布闭环和其他平台 artifact 尚未实现。
- Release packaging 的签名、notarization、stapling 和 Gatekeeper 命令已经接入脚本，但这些步骤需要 Apple credentials。没有 credentials 时不能证明已生成可发布的 Release artifact。

## Stage 4+

### Model Provider

- ModelConfigStore 只接受内置 DeepSeek、MiniMax、Kimi 和火山引擎 Catalog 中的十四个 Model ID。
- 当前不支持 arbitrary custom provider、arbitrary base URL、arbitrary model ID、连接测试或主动 capability probe。当前内置 Model Catalog 没有启用 Responses API 或 native Custom Tool capability。
- 当前内置模型的 wire API 固定为 OpenAI-compatible Chat Completions/SSE。Runtime 已有按 ModelProfile capability 路由的 Responses native adapter，但没有未经验证地为内置模型打开该路径。
- Chat Completions 没有原生的 Assistant `phase` 字段。Adapter 只根据 ToolCall 做 `commentary` 分类，并保留 Provider 的 `finish_reason`。`MessagePhase` 可以是 `commentary`、`final_answer`、`unknown` 或 `None`，但它不控制 Agent Loop。Agent Loop 使用 normalized response 的 `needs_follow_up` 决定继续采样还是完成当前 Turn。
- Model 流收到的 provisional text 在完整响应通过校验前不会持久化。可重试的 transport failure 会复用 frozen snapshot。normalization 的 `protocol_error` 和 `length` 都进入同一条已有的有界 protocol repair，连续错误合计最多触发一次。`content_filter`、cancel 和 authentication failure 不进入该 repair。
- Context Usage 的 estimated 值是有界 fallback，不是 tokenizer 精确值。它不能单独证明 Provider 已拒绝请求。

### Run 并发与资源模型

- 普通 Run 没有并发上限。Runtime 按 Session 分别维护持久 FIFO，同一 Session 同时只运行一个 Run，因此一个 Session 可以排队多个 Run；不同 Session 可以并行，且不区分 Workspace、Local checkout 或 Managed Worktree。每个 Run 有独立的 `ToolConcurrencyGate` 和 `ShellProcessManager`，所以不同 Session 可以在同一个 Workspace 并行执行 Shell 和其他普通副作用。一个 Run 的长 Shell 仍会阻止这个 Run 启动新的副作用，但 `write_stdin` 可以继续管理原 Shell。等待 Approval 不会占用其他 Run 的执行资源。
- 当前每个活动 Run 使用一个 Worker Thread。模型异步 I/O、MCP、Managed Task 和安全只读批次由唯一 RuntimeAsyncKernel 管理。当前没有把整个 RuntimeEngine/RunSupervisor 改成原生 async 的实现。
- Workspace 写入、Shell、MCP、external 和 Eidos-state 的实际副作用窗口只在各自 Run 内独占。不同 Session 的 Run 可以并行，即使共享同一个 Workspace。安全只读的 `parallel_safe` 批次保留自身的有界并行。同一个 Workspace 的并发修改可能让 Workspace observation 或 diff 包含其他 Session 的变化。
- 当前没有用户可配置的统一 Run 成本、模型步数或有效时长上限。现有 step、segment 和 effective time 字段主要用于 telemetry 和 operational lifecycle。

### Workspace 与工具

- 内置只读文件工具可以处理当前 Workspace 和 active Skill root 内受支持的普通 UTF-8 文件。写入工具支持普通 Workspace 写入和经审批的外部普通文件写入；普通 Skill 需要明确写入授权，`skills/.system` 始终禁写。已有文件使用受控原地写入，新文件使用排他提交；工具不处理 hardlink、symlink、特殊文件、特殊 mode 或文件 flags。
- `apply_patch` 的输入取决于当前 Run 的 ModelProfile capability。`supports_custom_tools=true` 且 `supports_tool_grammar=true` 时，模型收到 native Custom / FREEFORM Tool，并直接提交 Codex Patch 原文。其他 Provider 继续接受结构化 `{ "changes": [...] }` Function 参数。Function compatibility 路径仍使用 `CodexPatchEncoder`，模型提交旧的 raw `{ "patch": "..." }` 输入不会兼容。
- Custom Tool 的 input delta 已在 Responses adapter 内部完成原文重组，但当前没有 patch preview event 或 Desktop live diff。完整 ToolCall 结束后，Runtime 才会解析、校验和提交 Workspace 变更。
- Add 内容会统一规范化为 LF，并按 Codex 行语义补尾部 LF。Add File 可以没有内容行；显式的 `+` 表示一条空内容行。因此空字符串、单个换行和两个换行会保持不同的解析结果。Parser 接受 CRLF 和外层空白，但不会猜测缺失的 envelope、marker 或行前缀。
- 本地 grammar 将上游 `add_line+` 改为 `add_line*`，以对齐 Codex Rust streaming parser 的空 Add 行为。Lark 对上游零宽文本正则的写法也使用了兼容 token。其他 Workspace 边界和文件安全限制不变。
- `apply_patch` 支持 Add、Update、Delete、Move 和多文件 Patch，但不提供通用二进制编辑、浏览器自动化或 Artifact 发布工具。
- Desktop Terminal 是用户直接操作的临时 PTY。它不属于 Agent Tool，不经过 Runtime Approval 或 Seatbelt，也不会写入 SQLite、Checkpoint、Conversation 或恢复状态。关闭 Terminal Tab、切换 Session execution binding、删除 Session、关闭窗口或退出应用都会终止对应 PTY。Review 和 Files 各只打开一个工具 Tab；Terminal 可以打开多个 Tab，Files 可以同时预览多个文件。
- Projectless Conversation 不创建 Project，也不提供 Workspace Explorer、Git status、Git diff 或 Repository Intelligence。它使用系统私有锚点作为 workspace，并提供文件工具、Shell、Skill、MCP 和 Plugin 资源。Desktop 不显示 Files 和文件树。它只支持 Local execution。
- Workspace Explorer 当前只预览有界 UTF-8 text/code 和 Markdown。二进制、图片、PDF、Office、archive 和 database 文件不做内嵌预览。Explorer 不提供编辑、搜索、文件拖放、重命名或删除。当前的拖动交互只用于调整文件树与预览区的宽高。
- Non-Git Project 已经支持 Local Execution Session。Non-Git Project 不支持 Git status、Git diff、Managed Worktree 或 Git-based Fork。Local Checkpoint Fork 共享真实 workspace，不提供 directory snapshot、copy-on-write 或 filesystem rewind。
- Git Project 可以创建 Local 或 Worktree Execution Session，也可以在同一个 Session 中执行 Local ↔ Managed Worktree Handoff。Local Session 支持切换 local branch，也支持基于当前 branch 创建并切换到新 branch。该操作要求 workspace clean 且没有任何共享该 workspace 的 active Run。Managed Worktree 默认是 detached HEAD。Runtime 已提供 structured status、compare ref、精确文本行统计、file-scoped diff、stage、unstage、discard、commit、fetch、fast-forward-only pull、push、merge、rebase 和对应 continue/abort typed API。Desktop Review 已提供文件手风琴、展开或折叠全部、Stage、Unstage、tracked/untracked Discard、Open in Editor、inline Review Comment，以及 Commit、Fetch、Pull、Push、Merge、Rebase 和 Local branch 控制。二进制文件没有可用的文本行统计，`statsIncomplete` 会明确标记。Stash 尚未实现。
- Desktop Remote Git 不提供 credential 配置或 PAT 输入。它只复用系统 Git credential helper 和 SSH Agent。Advanced Git target 当前只列出已观察到的 local branch。它不接受任意 revision 文本，也不提供 remote ref browser。
- Inline Review Comment 当前只支持单条行级 Comment、删除、精确 anchor 失效和 active Comment 批量发送。它不支持 thread、reply、mention、reaction、云同步或模糊 re-anchor。stale Comment 只保留为历史提示，不会自动进入 Agent feedback。
- Git diff 对已记录的 submodule 只观察 Gitlink HEAD 和 submodule workspace 缺失。submodule 内部的 nested working-tree dirtiness 尚未向父 repository diff 暴露。
- Workspace discovery 只读取 Workspace root 的 `.gitignore` 和 `.eidosignore`。当前不支持 nested `.gitignore`。
- Ignore 规则只影响普通 `list_files`/`search_text` 发现结果。Ignore 规则不是权限，也不会缩小 Shell security scan 或副作用 evidence 范围。
- `search_text` 没有 LSP、AST 查询和基于 Repo Intelligence 的默认搜索路径。它仍然使用受管 Ripgrep，结果、preview、单文件和查询大小都有界。
- 已声明 Tool 的参数错误会返回 `invalid_arguments` Tool Result。Runtime 只保留有界字段路径、稳定原因码和有限数值约束，例如 `field=yieldTimeMs, reason=less_than_equal, maximum=30000, actual=60000`，不返回原始参数值。敏感或超大的结果在 projection 重建为错误时仍保留显式的 `reconciliationRequired=false`，不会把它改成 unknown。
- Shell post-execution observation 在扫描超时、敏感条目或不完整 Workspace manifest 时可能是 `unknown`。这类 observation 不能替代 Runtime 明确报告的执行 uncertainty，也不会单独限制已明确退出的 Shell。对于仍由 Runtime 管理、结果带有有效 `sessionId` 且明确报告 `reconciliationRequired=false` 的 `executionStatus=running` Shell，Workspace observation 不完整不会单独建立 reconciliation。Runtime 仍会保留 `workspaceChangeState=unknown`、`workspaceDiffIncomplete=true` 和 `sideEffectsMayExist=true`。完整的 Workspace 状态与安全事实仍需要后置核验。

### Agent Shell

- `outputComplete=false` 表示输出尚未完整获得，原因可能是仍在运行、捕获失败或原始输出截断。旧结果缺少该字段时，系统不能推断输出完整。`outputCaptureError` 只保存安全原因码，不能恢复已经丢失或被安全扫描拒绝的内容。本次历史 Run 的具体捕获子原因不会被新代码补写。
- 同一对账 epoch 最多允许三轮工具恢复，随后现有 Finalizer 收尾并保留对账事实。系统不自动重放未知操作。模型的测试报告指引要求区分收集数与完成数，也要求标明缺失汇总和未执行阶段；这项指引不等于系统能够自动证明任意测试结论。

- Agent Shell 按 Run 独立管理，支持同一 Run 内的管道 stdin 和分段等待，但不提供 PTY，也不跨 Run 或 Runtime 重启恢复进程。不同 Session 的 Shell 可以并行，即使共享同一个 Workspace；同一 Run 的长 Shell 会阻止该 Run 启动新的副作用。
- Runtime 会检测并清理 background child，但 Agent Shell 不能管理持久后台进程。
- ShellEnvironmentSnapshot 不恢复 aliases、functions 或其他 shell state。
- Shell cwd 必须解析到 Workspace 内。调用方可以使用 Workspace-relative 路径或 Workspace 内的 canonical absolute 路径。Workspace 外的 absolute cwd 会返回 Tool Error。
- Agent Shell 的 raw stdout/stderr 仍有 256 KiB 上限。它不提供无限输出流。
- Agent Shell 会对 stdout 和 stderr 做 UTF-8 增量解码。Desktop Execution Feed 在运行中和终态保留两条流安全片段的释放顺序，并把 ANSI/OSC 控制序列按纯文本处理。旧 Item 缺少或为空的 `content` 时，Feed 使用结果中的 stdout/stderr 回退，并在结果存在时展示 `attemptCount`、`sandboxed` 和 `escalated` 事实。
- 模型收到的 `run_shell` 输出每条 stdout/stderr 流最多 16 KiB，整个结果 JSON 最多 48 KiB。模型投影保留每条流的首尾，并用独立的 `modelProjectionTruncated`、`modelProjectionOmittedBytes` 和 `modelProjectionContinuation` 说明模型省略的 stdout/stderr UTF-8 字节。原始 `truncated` 和 `omittedBytes` 仍表示 Shell 原始输出限制，已丢失的原始字节不能恢复。
- `read_tool_output` 只读取当前 Session 中已持久化的终态 `run_shell` 输出，过去 Run 可以读取。它要求 provider tool call ID，默认 stdout，也支持 stderr；运行中、跨 Session、缺失或歧义 ID 会拒绝。请求的 `maxBytes` 范围是 4 字节至 16 KiB，实际页可能更小。分页结果按 UTF-8 边界返回 `startByte`、`endByte` 和 `nextOffset`，调用方必须按 `nextOffset` 继续。该工具不会重新执行 Shell，也不会清除 reconciliation。
- Desktop Terminal 是另一条 Main-owned PTY 路径。Agent Shell 的限制不会改变 Desktop Terminal 的现有说明。

### Repository Intelligence

- Inventory、Repository generations、Tree-sitter Index、symbols/imports/references/chunks、Repository Map、SQLite FTS5、Retrieval Snapshot、ContextPlan 和 ContextSnapshot 已经有 typed infrastructure、persistence 和 focused tests。
- Repository Generation readiness 已经进入 Runtime。Workspace 激活只 fast restore 和启动 watcher。`RuntimeEngine.run()` 会在第一个 Model Step 前执行一次 `ensure_ready()`。首次 build、cold-start reconciliation 和 watcher-invalidated 的下一个 Run 会执行 bounded Inventory scan。Clean Run 和同 Run 后续 Model Step 不会重复 scan。
- v1 数据库中的旧 generation 没有 persisted RepositoryMap。v6 拆库迁移会把它们标为 incomplete，不会用当前文件系统回填旧 Map。Runtime 只读取它们的 generation watermark。首次新 build 会生成更高的 complete generation。
- Cold start 仍然不能只凭旧 Inventory 证明仓库 clean，所以第一个 Run 会 reconcile。当前实现使用一次 bounded full Inventory scan。它没有 partial directory index、filesystem journal、Base Index + Worktree Overlay 或增量 Map 算法。
- Repository build 是增强能力。Canceled、incomplete、manifest verification failure 或 Git state change 不会替换旧 active generation。没有旧 complete generation 时，Snapshot 仍可为空，Agent 继续依赖 Workspace tools。
- 当前默认 online Run 已经自动执行一次 grounded Repository Retrieval，并通过 ContextBuilder 注入 Repository overview 和 evidence。每个 ModelAttempt 也会绑定精确 ContextSnapshot。
- 当前 Retrieval Query 只使用可以从用户目标、Inventory、Index、已有 Tool Result、dirty path 和 committed change 直接确认的信号。它没有 embedding、Vector Search、复杂 query rewrite、Base Index + Worktree Overlay，也没有 cross-worktree sharing。
- Watcher 事件不是 Workspace 安全事实。Watcher 不会静默修改当前 Run 的 immutable snapshot。

### Recovery 与 Checkpoint

- Runtime 可以持久化 Long Task 控制、pause/resume/cancel、restart verification 结果和 reconciliation 状态。启动时，没有未完成 Tool 执行或不确定副作用的 active Run，且同时没有 cancel request、没有 reconciliation barrier、没有未决有副作用 Durable Intent 和没有 running ToolAttempt 时，才可以重新排队。更广泛的 Restart Verification 尚未覆盖完整的 Git diff、credential、MCP、Seatbelt、pending Approval、unfinished ToolCall、Durable Intent 和 Checkpoint 兼容性集合。
- Checkpoint create/list 和 rewind/fork lineage 已持久化并暴露 typed RPC。Managed 和 Local Git Checkpoint 会保存 HEAD、staged、unstaged 和 untracked Git 状态。Managed Fork 会恢复独立 Worktree 的完整 checkpoint Git 状态。Managed 和 Local Rewind 会恢复原 checkout 的完整 checkpoint Git 状态。Local Rewind 只允许用户显式调用。Rewind 尚未重建完整逻辑 Context。Fork 仍不会复制全部非 Git immutable snapshots。Ignored 文件不进入 Checkpoint artifact。
- Worktree Session create、Session delete、managed Checkpoint Fork、managed Checkpoint Rewind、Create Branch Here、retention cleanup 和 Restore 使用 durable lifecycle intent。Session Handoff 使用 durable operation、strict HandoffPlan 和 startup recovery。Create Branch Here 使用 attach 时冻结的 `expected_head`，不使用创建时的 `base_commit` 判断当前 branch HEAD。Runtime 仍会拒绝 dirty Worktree delete，并保留无法证明安全的目录和 legacy attached branch。Retention 只处理 managed Worktree，不处理 adopted Worktree、Permanent Worktree 或按 bytes 的 disk quota。User Branch handoff 给 Local 后只释放 Eidos Worktree metadata，不删除 Git ref；Session delete 仍会保留这个普通用户 branch。Local Session delete 不删除用户 workspace。当前仍不提供 Permanent Worktree、Pinned Chat、Archive Chat、multi-Session shared Worktree、dependency cache Snapshot 或 Pull Request UI。
- Linked Worktree 的 Git metadata read 已在真实 macOS Seatbelt 中验证。Git metadata write、原始 repository working-tree access 和不匹配的 Worktree recovery 会被拒绝。Desktop dirty indicator 只使用 `project/gitContext` 和当前 Session status，不做所有 Thread 的持续轮询。Non-Git Local Workspace Checkpoint 仍不保存或恢复 filesystem state。
- Parallel Agent / subagent 尚未实现。本次不为未来 subagent 预设并发上限。cross-worktree Repository Intelligence sharing 尚未实现。
- Runtime 不会恢复内存中的 Model request、Process 或 ToolCall。可能有副作用且执行状态未知的操作必须先进入 reconciliation，Runtime 不会自动重放。已明确 `termination=exit` 且有 `exitCode` 的 Shell 即使 Workspace observation 不完整，也不会因此进入只读模式或阻止后续 ToolCall。取消已停止的 Run 正常返回。未清除的 reconciliation barrier 保留在 `interrupted` 终态中；`sideEffectsMayExist` 不会单独阻断取消。Workspace refresh 只能清除 Workspace mutation 的可核验 barrier，不能清除 Shell、MCP、external、Eidos-state 或 unknown barrier。Timeout、background child 清理未完成、unsandboxed 或 additional permission 失败，以及 MCP、external、Eidos-state 的未知结果仍然 fail closed。

### Compaction 与 Context

- 默认 ContextCompactor 使用 deterministic bounded extraction 生成候选摘要。当前没有 model-assisted proposal。
- 候选摘要必须通过 `state.sqlite` 事实验证，才能原子写入 verified record 和权威摘要。Tool provenance 从 summary 的 source Item IDs 对应到真实 ToolCall IDs，并支持 pre-turn 跨 Run 历史。当前 deterministic compactor 不吸收 Event 内容或 Retrieval evidence 正文，所以不会虚假附加这些 provenance。验证失败时，Runtime 保留上一份 verified summary。原始 Item 和 Tool 事实不会被删除。Thread history JSONL 当前是 Event projection，不是独立的全量 Conversation authority。
- MemoryStore 已经提供独立 `memories.sqlite` 和 content-addressed Markdown 存储，但当前 ContextCompactor、用户长期记忆和跨 Session 检索尚未接入这个 Store。
- Context Usage Desktop 只展示当前选中 Model 对应 Run 的有效 Context Usage。同一 Session、同一 Model 启动或切换到新 Run 时，如果新 Run 尚未产生 Usage，Renderer 会保留上一份可用 Usage，直到新快照到达；切换 Session/Model 或本来没有历史 Usage 时才显示无数据状态。
- Provider 明确 `context_exceeded` 后，如果没有新的可压缩历史或 Context projection 没有进展，Runtime 会以 `context_still_over_budget` 停止。

### Extension 与 MCP

- Plugin 当前只支持本地受管 Plugin v1。当前没有远程 Plugin marketplace、OAuth 安装或任意运行时动态 import 用户 Plugin 的能力。
- Skill Catalog 当前只投影 `SKILL.md` frontmatter 中的顶层 `name` 和 `description`。`agents/eidos.yaml` 的 `runtimeDependencies` 使用严格 typed parser。其他字段可以保留在原始 Skill 内容中，但不会进入 Catalog、协议或权限判断。
- MCP 当前只支持 stdio Tools。当前没有 Streamable HTTP、远程 MCP transport、OAuth、Resources、Prompts、Sampling 或 Tasks。
- MCP ready connection 是长生命周期 Service，但 startup、Tool call、Tool list、cancel 和 shutdown 都有各自的有界等待。已经开始的 Tool List Changed bookkeeping callback 可能在关闭时需要等待完成。

### Observability / OpenTelemetry

- 当前 OpenTelemetry 集成只配置 Traces。Runtime 的本地 JSONL 日志不等于 OTel Logs pipeline。Runtime 没有建立 OTel Metrics 或 Logs exporter，也没有把 Trace 或本地日志作为业务事实或恢复依据。
- `OTEL_TRACES_EXPORTER` 默认是 `none`，因此默认不会把 Trace 导出到外部 Observability 后端。需要显式配置 `console` 或 `otlp` 才会导出。
- 当前 Trace 主要覆盖 Run、Model Attempt 和 Tool Call。它不是完整的 Desktop 操作链、SQLite transaction、Repository Intelligence、Approval 或 Sandbox 内部阶段的全链路 tracing。

### Application 边界

- `application/` 已建立 Session、Run、Response Action、Model、Extension、Repository、Context、Checkpoint 和 TaskLifecycle 的部分边界。
- 部分 RuntimeServer handler 仍通过 SessionStore 兼容入口执行。所有顶层 use case 尚未完成统一 Application migration。
- `Run.runtimeState` 是可选跨语言 DTO 字段，不是恢复权威。当前恢复权威仍然是 SQLite 中的 Run status、Approval、Step、ToolCall、Durable Intent 和 reconciliation 事实。

## Implementation Anchors

- `runtime/eidos_runtime/model/config.py`
- `runtime/eidos_runtime/model_gateway/`
- `runtime/eidos_runtime/runtime/supervisor.py`
- `runtime/eidos_runtime/runtime/engine.py`
- `runtime/eidos_runtime/context/compactor.py`
- `runtime/eidos_runtime/context/verified_compaction.py`
- `runtime/eidos_runtime/application/repository.py`
- `runtime/eidos_runtime/application/context.py`
- `runtime/eidos_runtime/repo_intelligence/`
- `runtime/eidos_runtime/persistence/checkpoints.py`
- `runtime/eidos_runtime/persistence/repository_intelligence.py`
- `runtime/eidos_runtime/extensions/`
- `runtime/eidos_runtime/sandbox/`
- `runtime/eidos_runtime/telemetry/provider.py`
- `runtime/eidos_runtime/telemetry/tracing.py`
- `runtime/eidos_runtime/db/schema.py`
- `runtime/eidos_runtime/db/database.py`
- `runtime/eidos_runtime/git/`
- `runtime/eidos_runtime/persistence/worktrees.py`

## Approval R1 的范围

权限 Grant 只覆盖当前 Run，用户不能选择 Session 或全局范围。R1 不提供 Approve for me、Full Access、Network Proxy、域名授权、持久 allowlist 或自动审批。路径权限只使用具体路径，不支持 glob。

旧审批或缺少完整执行事实的待批动作不会被猜测为可恢复。Runtime 会继续中断不确定执行，并保留 Reconciliation。用户批准权限不会自动重放之前失败的 Shell。

## Run 收尾与 Shell 执行期限

- 模型提交最终答复时，Runtime 在同一事务提交答复和 Run 终态。未清除的 reconciliation 会使 Run 进入 `interrupted`，不会再强制模型继续只读核验，也不会清除未知 Durable Intent 或放行新副作用。
- 取消后 Worker 已退出时，RPC 返回 `canceled` 或 `interrupted`。系统记录取消完成时间；副作用未知不再作为取消失败。无 Worker 的 queued Run 如果已带有未确认副作用，也进入 `interrupted`。重复取消已中断 Run 返回原终态。Worker 仍存活时继续报告 `RUN_CANCEL_TIMEOUT`。
- 新 Run 的 Shell 不再设置默认进程总期限。`run_shell` 首次等待默认 10 秒，范围 250 毫秒至 30 秒；模型随后使用 `write_stdin` 等待，默认 30 秒，范围 250 毫秒至 60 秒。等待窗口到期只返回 `shell_running` 和 `sessionId`，不会结束进程或触发 reconciliation。ToolSpec 的 600 秒 watchdog 只限制单次启动、审批重试或跟进调用，审批等待不计入预算。历史 3600 秒 ToolSpec 仍可读取，但新工具不会用它限制进程寿命。
- 控制器把已有 Shell 结果转成超时或取消结果时，会保留已有输出和终止信息，并继续执行结果校验、输出限额和敏感扫描。进程清理和未知副作用仍按原规则处理。此修改不恢复旧结果中已经缺失的 stdout/stderr。
- Runtime context 使用 `recentToolErrorFingerprints` 表示最近工具错误。空列表不代表对账完成。LoopGuard 不把 assistant 文本变化或最近错误列表中的错误消失单独当作新进展。

- 已有文件的原地写入不是原子内容替换。外部读者可能看到中间内容；写前版本检查不能阻止外部编辑器在检查后并发写入。异常终止可能留下部分文件；Runtime 不会自动覆盖回旧内容或重放补丁。
- 工作区外的已有文件按具体文件申请写权限。新文件使用目标最近的已存在父目录作为请求范围，审批会展示目录和具体文件。不存在的父目录可在批准后创建。永久拒绝路径、链接和敏感路径仍不能写入。无沙盒审批不会绕过操作系统 ACL、只读权限或系统隐私权限。

- 外部文件写入的不确定结果需要对外部目标另行核实。当前 Workspace 刷新不覆盖外部文件，因此不能自动清除此类 Reconciliation；Runtime 不自动重放或回滚。

- 普通 Skill 的授权不能覆盖整个数据目录或包含 `.system` 的 Skill 容器。新文件的最近已存在父目录如果包含系统 Skill，工具会拒绝该宽泛授权；新建 Skill 可以使用已有的 `skill_create` 审批流程。
- 系统 Skill 的永久写入保护需要保留在执行环境中。因此任意 Shell 的无沙盒提权在存在永久写入保护时不可用；审批过的受控文件 helper 仍可对允许的具体目标进行无沙盒写入。
