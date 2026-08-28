# Eidos 当前限制

本文只回答“当前 main 还不能做什么，或者哪些能力还没有形成完整闭环”。本文不记录已经解决的问题，也不记录历史 Phase。

## 平台与分发

- Eidos 当前只支持 macOS arm64 Desktop、Runtime Bundle、App 和 DMG。
- Linux、Windows、macOS x64、Universal binary 和其他平台的 bundled Runtime 与 Ripgrep artifact 尚未实现。
- Auto Update、GitHub Release 发布闭环和其他平台 artifact 尚未实现。
- Release packaging 的签名、notarization、stapling 和 Gatekeeper 命令已经接入脚本，但这些步骤需要 Apple credentials。没有 credentials 时不能证明已生成可发布的 Release artifact。

## Model Provider

- ModelConfigStore 只接受内置 DeepSeek、MiniMax、Kimi 和火山引擎 Catalog 中的十四个 Model ID。
- 当前不支持 arbitrary custom provider、arbitrary base URL、arbitrary model ID、Responses API、连接测试或主动 capability probe。
- 当前 wire API 固定为 OpenAI-compatible Chat Completions/SSE。
- Chat Completions 没有原生的 Assistant `phase` 字段。Adapter 只根据 ToolCall 做 `commentary` 分类，并保留 Provider 的 `finish_reason`。`MessagePhase` 可以是 `commentary`、`final_answer`、`unknown` 或 `None`，但它不控制 Agent Loop。Agent Loop 使用 normalized response 的 `needs_follow_up` 决定继续采样还是完成当前 Turn。
- Context Usage 的 estimated 值是有界 fallback，不是 tokenizer 精确值。它不能单独证明 Provider 已拒绝请求。

## Run 并发与资源模型

- 多个非终态 Run 可以同时存在，但 Runtime 同一时间只有一个 Run 可以占用全局 Execution Slot。等待 Approval 的 Run 会释放 Slot，因此它可以和另一个正在执行的 Run 并存。当前没有多个 Run 同时占用执行槽的 parallel Run，也没有 parallel Agent。
- 当前每个活动 Run 使用一个 Worker Thread。模型异步 I/O、MCP、Managed Task 和安全只读批次由唯一 RuntimeAsyncKernel 管理。当前没有把整个 RuntimeEngine/RunSupervisor 改成原生 async 的实现。
- Workspace 写入、Shell、Eidos-state、MCP 和 external Tool 不支持并发执行。
- 当前没有用户可配置的统一 Run 成本、模型步数或有效时长上限。现有 step、segment 和 effective time 字段主要用于 telemetry 和 operational lifecycle。

## Workspace 与工具

- 内置文件工具只处理当前 Workspace 内受支持的普通 UTF-8 文件。工具会通过 macOS clonefile 路径保留 mode、扩展属性（包括 `com.apple.provenance`）和 ACL；不支持 clonefile 的文件系统会使用受校验的安全回退。工具不处理 hardlink、symlink、特殊文件、特殊 mode 或文件 flags。`apply_patch` 已支持 Codex 风格的 Add、Update、Delete、Move 和多文件 Patch，但仍不提供通用二进制编辑、浏览器自动化或 Artifact 发布工具。
- `workspace_dependencies` 只公开 Eidos 明确随包提供的可执行文件和 Python 包。当前集合不是通用包管理器。Tool 不安装依赖，也不会使用用户全局 Python。未列出的库仍然不可假设存在。
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
- Shell post-execution observation 在扫描超时、敏感条目或不完整 Workspace manifest 时可能是 `unknown`。这类 observation 不能替代 Runtime 明确报告的执行 uncertainty。完整的 Workspace 状态与安全事实仍需要后置核验。

## Agent Shell

- Agent `run_shell` 不提供 PTY、stdin、interactive session 或 persistent/background process manager。
- Runtime 会检测并清理 background child，但 Agent Shell 不能管理持久后台进程。
- ShellEnvironmentSnapshot 不恢复 aliases、functions 或其他 shell state。
- Shell cwd 仍然必须是 Workspace-relative 的有效路径。
- Agent Shell 的 raw stdout/stderr 仍有 256 KiB 上限。它不提供无限输出流。
- Desktop Terminal 是另一条 Main-owned PTY 路径。Agent Shell 的限制不会改变 Desktop Terminal 的现有说明。

## Repository Intelligence

- Inventory、Repository generations、Tree-sitter Index、symbols/imports/references/chunks、Repository Map、SQLite FTS5、Retrieval Snapshot、ContextPlan 和 ContextSnapshot 已经有 typed infrastructure、persistence 和 focused tests。
- Repository Generation readiness 已经进入 Runtime。Workspace 激活只 fast restore 和启动 watcher。`RuntimeEngine.run()` 会在第一个 Model Step 前执行一次 `ensure_ready()`。首次 build、cold-start reconciliation 和 watcher-invalidated 的下一个 Run 会执行 bounded Inventory scan。Clean Run 和同 Run 后续 Model Step 不会重复 scan。
- v1 数据库中的旧 generation 没有 persisted RepositoryMap。v2 migration 不会用当前文件系统回填旧 Map，所以这些旧 generation 不属于 fully-restorable generation。Runtime 只读取它们的 generation watermark。首次新 build 会生成更高的 complete generation。
- Cold start 仍然不能只凭旧 Inventory 证明仓库 clean，所以第一个 Run 会 reconcile。当前实现使用一次 bounded full Inventory scan。它没有 partial directory index、filesystem journal、Base Index + Worktree Overlay 或增量 Map 算法。
- Repository build 是增强能力。Canceled、incomplete、manifest verification failure 或 Git state change 不会替换旧 active generation。没有旧 complete generation 时，Snapshot 仍可为空，Agent 继续依赖 Workspace tools。
- 当前默认 online Run 已经自动执行一次 grounded Repository Retrieval，并通过 ContextBuilder 注入 Repository overview 和 evidence。每个 ModelAttempt 也会绑定精确 ContextSnapshot。
- 当前 Retrieval Query 只使用可以从用户目标、Inventory、Index、已有 Tool Result、dirty path 和 committed change 直接确认的信号。它没有 embedding、Vector Search、复杂 query rewrite、Base Index + Worktree Overlay，也没有 cross-worktree sharing。
- Watcher 事件不是 Workspace 安全事实。Watcher 不会静默修改当前 Run 的 immutable snapshot。

## Recovery 与 Checkpoint

- Runtime 可以持久化 Long Task 控制、pause/resume/cancel、restart verification 结果和 reconciliation 状态，但 Restart Verification 尚未覆盖完整的 Git diff、credential、MCP、Seatbelt、pending Approval、unfinished ToolCall、Durable Intent 和 Checkpoint 兼容性集合。
- Checkpoint create/list 和 rewind/fork lineage 已持久化并暴露 typed RPC。Managed 和 Local Git Checkpoint 会保存 HEAD、staged、unstaged 和 untracked Git 状态。Managed Fork 会恢复独立 Worktree 的完整 checkpoint Git 状态。Managed 和 Local Rewind 会恢复原 checkout 的完整 checkpoint Git 状态。Local Rewind 只允许用户显式调用。Rewind 尚未重建完整逻辑 Context。Fork 仍不会复制全部非 Git immutable snapshots。Ignored 文件不进入 Checkpoint artifact。
- Worktree Session create、Session delete、managed Checkpoint Fork、managed Checkpoint Rewind、Create Branch Here、retention cleanup 和 Restore 使用 durable lifecycle intent。Session Handoff 使用 durable operation、strict HandoffPlan 和 startup recovery。Create Branch Here 使用 attach 时冻结的 `expected_head`，不使用创建时的 `base_commit` 判断当前 branch HEAD。Runtime 仍会拒绝 dirty Worktree delete，并保留无法证明安全的目录和 legacy attached branch。Retention 只处理 managed Worktree，不处理 adopted Worktree、Permanent Worktree 或按 bytes 的 disk quota。User Branch handoff 给 Local 后只释放 Eidos Worktree metadata，不删除 Git ref；Session delete 仍会保留这个普通用户 branch。Local Session delete 不删除用户 workspace。当前仍不提供 Permanent Worktree、Pinned Chat、Archive Chat、multi-Session shared Worktree、dependency cache Snapshot 或 Pull Request UI。
- Linked Worktree 的 Git metadata read 已在真实 macOS Seatbelt 中验证。Git metadata write、原始 repository working-tree access 和不匹配的 Worktree recovery 会被拒绝。Desktop dirty indicator 只使用 `project/gitContext` 和当前 Session status，不做所有 Thread 的持续轮询。Non-Git Local Workspace Checkpoint 仍不保存或恢复 filesystem state。
- Parallel Agent 尚未实现。cross-worktree Repository Intelligence sharing 尚未实现。
- Runtime 不会恢复内存中的 Model request、Process 或 ToolCall。可能有副作用的未确认执行必须先进入 reconciliation，Runtime 不会自动重放。

## Compaction 与 Context

- 默认 ContextCompactor 使用 deterministic bounded extraction 生成候选摘要。当前没有 model-assisted proposal。
- 候选摘要必须通过 SQLite 事实验证，才能原子写入 verified record 和权威摘要。Tool provenance 从 summary 的 source Item IDs 对应到真实 ToolCall IDs，并支持 pre-turn 跨 Run 历史。当前 deterministic compactor 不吸收 Event 内容或 Retrieval evidence 正文，所以不会虚假附加这些 provenance。验证失败时，Runtime 保留上一份 verified summary。原始历史不会被删除。
- Context Usage Desktop 只展示当前选中 Model 对应 Run 的有效 Context Usage。同一 Session、同一 Model 启动或切换到新 Run 时，如果新 Run 尚未产生 Usage，Renderer 会保留上一份可用 Usage，直到新快照到达；切换 Session/Model 或本来没有历史 Usage 时才显示无数据状态。
- Provider 明确 `context_exceeded` 后，如果没有新的可压缩历史或 Context projection 没有进展，Runtime 会以 `context_still_over_budget` 停止。

## Extension 与 MCP

- Plugin 当前只支持本地受管 Plugin v1。当前没有远程 Plugin marketplace、OAuth 安装或任意运行时动态 import 用户 Plugin 的能力。
- Skill Catalog 当前只投影 `SKILL.md` frontmatter 中的顶层 `name` 和 `description`。其他字段可以保留在原始 Skill 内容中，但不会进入 Catalog、协议或权限判断。
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
