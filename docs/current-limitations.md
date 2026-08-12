# Eidos 当前限制

本文只回答“当前 main 还不能做什么，或者哪些能力还没有形成完整闭环”。本文不记录已经解决的问题，也不记录历史 Phase。

## 平台与分发

- Eidos 当前只支持 macOS arm64 Desktop、Runtime Bundle、App 和 DMG。
- Linux、Windows、macOS x64、Universal binary 和其他平台的 bundled Runtime 与 Ripgrep artifact 尚未实现。
- Auto Update、GitHub Release 发布闭环和其他平台 artifact 尚未实现。
- Release packaging 的签名、notarization、stapling 和 Gatekeeper 命令已经接入脚本，但这些步骤需要 Apple credentials。没有 credentials 时不能证明已生成可发布的 Release artifact。

## Model Provider

- ModelConfigStore 只接受内置 DeepSeek、MiniMax 和 Kimi Catalog 中的五个 Model ID。
- 当前不支持 arbitrary custom provider、arbitrary base URL、arbitrary model ID、Responses API、连接测试或主动 capability probe。
- 当前 wire API 固定为 OpenAI-compatible Chat Completions/SSE。
- Context Usage 的 estimated 值是有界 fallback，不是 tokenizer 精确值。它不能单独证明 Provider 已拒绝请求。

## Run 并发与资源模型

- 多个非终态 Run 可以同时存在，但 Runtime 同一时间只有一个 Run 可以占用全局 Execution Slot。等待 Approval 的 Run 会释放 Slot，因此它可以和另一个正在执行的 Run 并存。当前没有多个 Run 同时占用执行槽的 parallel Run，也没有 parallel Agent。
- 当前每个活动 Run 使用一个 Worker Thread。模型异步 I/O、MCP、Managed Task 和安全只读批次由唯一 RuntimeAsyncKernel 管理。当前没有把整个 RuntimeEngine/RunSupervisor 改成原生 async 的实现。
- Workspace 写入、Shell、Eidos-state、MCP 和 external Tool 不支持并发执行。
- 当前没有用户可配置的统一 Run 成本、模型步数或有效时长上限。现有 step、segment 和 effective time 字段主要用于 telemetry 和 operational lifecycle。

## Workspace 与工具

- 内置文件工具只处理当前 Workspace 内受支持的普通 UTF-8 文件。当前没有通用二进制编辑、内嵌 Terminal、浏览器自动化或 Artifact 发布工具。
- Workspace Explorer 当前只预览有界 UTF-8 text/code 和 Markdown。二进制、图片、PDF、Office、archive 和 database 文件不做内嵌预览。Explorer 不提供编辑、搜索、拖放、重命名或删除。
- Non-Git Project 已经支持 Local Execution Session。Non-Git Project 不支持 Git status、Git diff、Managed Worktree 或 Git-based Fork。Local Checkpoint Fork 共享真实 workspace，不提供 directory snapshot、copy-on-write 或 filesystem rewind。
- Git Project 可以创建 Local 或 Worktree Execution Session，也可以在同一个 Session 中执行 Local ↔ Managed Worktree Handoff。Managed Worktree 默认是 detached HEAD。Runtime 已提供 structured status、file-scoped diff、stage、unstage、discard、commit、fetch、fast-forward-only pull、push、merge、rebase 和对应 continue/abort typed API。Desktop Changes 视图已提供按文件 Diff、Stage、Unstage、tracked/untracked Discard、Open in Editor、inline Review Comment，以及 Commit、Fetch、Pull、Push、Merge 和 Rebase 控制。Stash 和 Real Checkpoint 尚未实现。
- Desktop Remote Git 不提供 credential 配置或 PAT 输入。它只复用系统 Git credential helper 和 SSH Agent。Advanced Git target 当前只列出已观察到的 local branch。它不接受任意 revision 文本，也不提供 remote ref browser。
- Inline Review Comment 当前只支持单条行级 Comment、删除、精确 anchor 失效和 active Comment 批量发送。它不支持 thread、reply、mention、reaction、云同步或模糊 re-anchor。stale Comment 只保留为历史提示，不会自动进入 Agent feedback。
- Git diff 对已记录的 submodule 只观察 Gitlink HEAD 和 submodule workspace 缺失。submodule 内部的 nested working-tree dirtiness 尚未向父 repository diff 暴露。
- Workspace discovery 只读取 Workspace root 的 `.gitignore` 和 `.eidosignore`。当前不支持 nested `.gitignore`。
- Ignore 规则只影响普通 `list_files`/`search_text` 发现结果。Ignore 规则不是权限，也不会缩小 Shell security scan 或副作用 evidence 范围。
- `search_text` 没有 LSP、AST 查询和基于 Repo Intelligence 的默认搜索路径。它仍然使用受管 Ripgrep，结果、preview、单文件和查询大小都有界。
- Shell post-execution observation 在扫描超时、敏感条目或不完整 Workspace manifest 时可能是 `unknown`。这类 observation 不能替代 Runtime 明确报告的执行 uncertainty。完整的 Workspace 状态与安全事实仍需要后置核验。

## Repository Intelligence

- Inventory、Repository generations、Tree-sitter Index、symbols/imports/references/chunks、Repository Map、SQLite FTS5、Retrieval Snapshot、ContextPlan 和 ContextSnapshot 已经有 typed infrastructure、persistence 和 focused tests。
- 这些基础设施还没有全部进入 RuntimeEngine 的默认 online Run path。默认 Model Attempt 前不会强制执行完整的 Inventory → Index → Map → Retrieval → ContextPlan → ContextSnapshot 组装。
- 因此，当前不能把 Repository Intelligence 描述成“没有实现”，也不能把它描述成“每次 Run 都自动使用”。正确状态是 implemented infrastructure、partially wired、not yet product-complete。
- Watcher 只提供缓存失效信号。Watcher 事件不是 Workspace 安全事实，也不会静默修改当前 Run 的 immutable snapshot。

## Recovery 与 Checkpoint

- Runtime 可以持久化 Long Task 控制、pause/resume/cancel、restart verification 结果和 reconciliation 状态，但 Restart Verification 尚未覆盖完整的 Git diff、credential、MCP、Seatbelt、pending Approval、unfinished ToolCall、Durable Intent 和 Checkpoint 兼容性集合。
- Checkpoint create/list 和 rewind/fork lineage 已持久化并暴露 typed RPC。Managed Checkpoint 会保存 HEAD、staged、unstaged 和 untracked Git 状态。Managed Fork 会恢复独立 Worktree 的完整 checkpoint Git 状态。Managed Rewind 会恢复原 Worktree 的完整 checkpoint Git 状态。Rewind 尚未重建完整逻辑 Context。Fork 仍不会复制全部非 Git immutable snapshots。Ignored 文件不进入 Checkpoint artifact。
- Worktree Session create、Session delete、managed Checkpoint Fork、managed Checkpoint Rewind、Create Branch Here、retention cleanup 和 Restore 使用 durable lifecycle intent。Session Handoff 使用 durable operation、strict HandoffPlan 和 startup recovery。Create Branch Here 使用 attach 时冻结的 `expected_head`，不使用创建时的 `base_commit` 判断当前 branch HEAD。Runtime 仍会拒绝 dirty Worktree delete，并保留无法证明安全的目录和 legacy attached branch。Retention 只处理 managed Worktree，不处理 adopted Worktree、Permanent Worktree 或按 bytes 的 disk quota。User Branch handoff 给 Local 后只释放 Eidos Worktree metadata，不删除 Git ref；Session delete 仍会保留这个普通用户 branch。Local Session delete 不删除用户 workspace。当前仍不提供 Permanent Worktree、Pinned Chat、Archive Chat、multi-Session shared Worktree、dependency cache Snapshot 或 Pull Request UI。
- Linked Worktree 的 Git metadata read 已在真实 macOS Seatbelt 中验证。Git metadata write、原始 repository working-tree access 和不匹配的 Worktree recovery 会被拒绝。Desktop dirty indicator 只使用 `project/gitContext` 和当前 Session status，不做所有 Thread 的持续轮询。Local Workspace Checkpoint 仍不保存或恢复 filesystem state。
- Parallel Agent 尚未实现。cross-worktree Repository Intelligence sharing 尚未实现。
- Runtime 不会恢复内存中的 Model request、Process 或 ToolCall。可能有副作用的未确认执行必须先进入 reconciliation，Runtime 不会自动重放。

## Compaction 与 Context

- 默认 ContextCompactor 是 deterministic bounded extraction。当前没有 model-assisted compaction。
- ContextCompactionVerifier、VerifiedCompaction persistence、ContextPlan 和 ContextSnapshot 已经存在，但兼容的默认 ContextCompactor 尚未自动切换到完整 verified compaction write path。
- Context Usage Desktop 只展示当前选中 Model 对应 Run 的有效 Context Usage。同一 Session、同一 Model 启动或切换到新 Run 时，如果新 Run 尚未产生 Usage，Renderer 会保留上一份可用 Usage，直到新快照到达；切换 Session/Model 或本来没有历史 Usage 时才显示无数据状态。
- Provider 明确 `context_exceeded` 后，如果没有新的可压缩历史或 Context projection 没有进展，Runtime 会以 `context_still_over_budget` 停止。

## Extension 与 MCP

- Plugin 当前只支持本地受管 Plugin v1。当前没有远程 Plugin marketplace、OAuth 安装或任意运行时动态 import 用户 Plugin 的能力。
- MCP 当前只支持 stdio Tools。当前没有 Streamable HTTP、远程 MCP transport、OAuth、Resources、Prompts、Sampling 或 Tasks。
- MCP ready connection 是长生命周期 Service，但 startup、Tool call、Tool list、cancel 和 shutdown 都有各自的有界等待。已经开始的 Tool List Changed bookkeeping callback 可能在关闭时需要等待完成。

## Observability / OpenTelemetry

- 当前 OpenTelemetry 集成只配置 Traces。Runtime 没有建立 OTel Metrics 或 Logs pipeline，也没有把 Trace 作为 SQLite 业务事实或恢复依据。
- `OTEL_TRACES_EXPORTER` 默认是 `none`，因此默认不会把 Trace 导出到外部 Observability 后端。需要显式配置 `console` 或 `otlp` 才会导出。
- 当前 Trace 主要覆盖 Run、Model Attempt 和 Tool Call。它不是完整的 Desktop 操作链、SQLite transaction、Repository Intelligence、Approval 或 Sandbox 内部阶段的全链路 tracing。

## Application 边界

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
