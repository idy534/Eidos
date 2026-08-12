# Eidos 当前能力

本文只回答“当前 main 已经能做什么”。每项能力都对应生产代码和测试入口。能力存在但没有进入默认 Run 的部分会明确标注。

## Desktop

- Eidos 提供 macOS Electron Desktop。
- Renderer、Preload 和 Main 之间使用 context-isolated typed IPC。
- Main 可以启动、健康检查、通知和关闭 Python Runtime。
- Desktop 可以选择 Workspace，读取 Runtime 提供的 Git context，并在创建 Thread 时选择 Local 或 Worktree execution。Git Worktree execution 可以选择 starting branch。Source dirty 且 starting branch 是 current branch 时，Desktop 默认勾选 `Include current changes`，但 Runtime 只接受显式的 `includeLocalChanges`。Non-Git Workspace 只启用 Local。
- Desktop 可以列出、读取、重命名和删除 Session。
- Desktop 可以在同一个 Session 中把执行工作区从 Local hand off 到 Managed Worktree，或 hand off 回 Local。Handoff 期间会禁用 Composer、Create Branch、Delete 和 Session 导航；完成后只刷新当前 Session 的 execution binding 和 Git review。
- Desktop 可以在 Session 对应的 managed Worktree 被 retention 清理后显示 Restore Worktree 提示。Restore 会调用 `session/restoreWorktree`，并继续使用同一个 Session 和同一个 `associatedWorktreeId`。当前 execution mode 是 Worktree 且 Worktree 已删除时，Composer 会保持只读。
- Settings 可以读取和修改 `automaticCleanup` 与 `managedWorktreeLimit`。Worktree limit 的有效范围是 1 到 100，默认值是 15。
- Execution Feed 可以展示用户消息、模型文本、ToolCall、Tool Result、Approval、终态和恢复后的历史。
- Composer 可以选择已配置 Model，并显示当前选中 Model 最近 Run 的 Context Usage。
- Desktop 支持上下文使用率的 Provider 来源和 estimated 来源展示。没有快照时显示无数据状态。
- Quit 流程会先处理活动 Run，再关闭 Runtime 和窗口资源。

## Session / Run

- Project 表示 filesystem workspace。Project 保存 `workspaceRoot`，并用 `gitAvailable` 表示可选 Git capability。
- Non-Git directory 可以使用 `executionMode = local` 创建 Local Execution Session。Runtime 将 `worktreeId` 保持为 NULL，Run、Tool、Shell cwd、Project Rules 和 Repository Intelligence 使用 Project workspace root。
- Git directory 可以使用 `executionMode = local` 创建不绑定 Worktree 的 Local Session，也可以使用 `executionMode = worktree` 创建 Managed Worktree Session。Worktree Session 的 Run、Tool、Shell cwd、Project Rules 和 Repository Intelligence 使用 Worktree root。
- `session/create` 的协议默认 `executionMode` 是 `local`。Worktree 请求会先解析可选 `baseRef` 为 immutable `baseCommit`，并接受显式的 `includeLocalChanges`。缺省 `baseRef` 使用当前 branch；repository 处于 detached HEAD 时使用 `HEAD`。不存在的 ref 返回 `BASE_REF_NOT_FOUND`。
- Local Session 和 Worktree Session 都是正式 Session projection。Worktree Session 默认使用 detached HEAD，`worktree.branch` 为 NULL。Git Project 的 Local Session 通过当前 Local checkout 提供 Git status 和 Git diff。
- Worktree 创建只会从 source repository root 的 `.worktreeinclude` 复制同时命中 `pathspec.GitIgnoreSpec` 且由 Git ignore 判定为 ignored 的 local files。Tracked files 和 untracked non-ignored files 不由 `.worktreeinclude` 复制。Runtime 只在 matched concrete path 上执行 `.git`、target boundary、source symlink、target parent、atomic replacement、fsync 和 permission safety；Runtime 不重新限制 Git pattern grammar。Managed Worktree 内的 `.worktreeinclude` 不具有 authority。Ignored 的 `EIDOS.override.md` 和 `AGENTS.override.md` 会自动 materialize；tracked override 只使用 Git checkout 内容。
- `includeLocalChanges = true` 要求 source `HEAD == baseCommit`。Runtime 使用 hardened Git CLI capture/apply full patch 和 staged patch，覆盖 tracked modified/deleted、staged、unstaged、binary、symlink、mode 和 untracked state。Source Workspace 不执行 stash、reset、checkout、add 或其他写入。Local-change state conflict、source change 和 materialization failure 会 rollback Worktree，无法安全清理时进入 `cleanup_required`。Dirty submodule checkout 不属于当前 transfer contract，Runtime 返回 `worktree_gitlink_unsupported`。
- `session/handoff` 在不创建新 Session 的情况下切换同一个 Session 的 execution binding。Session 保存 `associatedWorktreeId`，因此 Worktree → Local → Worktree 会回到同一个 Worktree。Handoff 会保存 current HEAD、committed movement、staged、unstaged、untracked、binary 和 dirty fingerprint，并拒绝 active Run、Local conflict、source/target drift、Git common directory mismatch 和缺失 Worktree。
- `session/restoreWorktree` 只恢复 Session 的 `associatedWorktreeId`。Deleted Worktree 会返回 `WORKTREE_RESTORE_REQUIRED`，invalid Worktree 会返回 `WORKTREE_RECOVERY_REQUIRED`。Restore 成功后，Run admission 可以重新使用原 Worktree；Runtime 不创建第二个 Worktree。
- Runtime 可以创建、排队、执行、取消、暂停、恢复和查询 Run。
- Run 使用持久 FIFO 和全局单 Execution Slot。多个非终态 Run 可以共存；等待 Approval 的 Run 会释放 Slot，让其他排队 Run 继续执行。
- Run 状态、Item、Step、ToolCall、Approval 和终态写入 SQLite，并通过 Event/Outbox 投影到 Desktop。
- 取消会传播到 Model、Tool、Shell、Approval 和 Async Task。已取消 Run 不会被迟到模型结果改成成功。
- Model Step Count、Segment Step Count 和 effective time 可以作为持久 telemetry 读取。
- 健康 Run 不受固定 model-step、Run duration 或 fixed repeated-call counter 限制。Segment rollover 不会把 Run 变成终态。

## Model

- ModelConfigStore 支持内置 Catalog 中的五个 Model：`deepseek-v4-pro`、`deepseek-v4-flash`、`MiniMax-M3`、`kimi-k3` 和 `kimi-k2.7-code-highspeed`。
- Model 配置保存在 `models.json`。默认位置是 `~/.eidos/models.json`。本地文件使用 owner-only 权限。
- API Key 通过本地 Model 配置写请求链路传到 Runtime：Renderer typed IPC → Electron Main → `model/create` / `model/update` JSON-RPC request → ModelConfigStore。Key 不进入模型列表/读取响应、SQLite、Event/Feed 或正常日志。
- Runtime 使用 OpenAI-compatible Chat Completions 和 SSE 流。
- Runtime 使用 Pydantic AI Model API 处理 Provider 构造、流式 Model Response、Usage 和 ToolCall 归一化。
- 每个 Run 固化 Model Profile、Model capability declaration 和 Extension Snapshot。活动 Run 不会被后续 Model 配置编辑或删除改变。
- Runtime 记录 Model Attempt、usage、response metadata、transport retry 诊断和稳定错误码。
- Runtime 可以声明和保存 reasoning capability，但不会把 Provider reasoning 或 chain-of-thought 当作普通 Feed 内容展示。

## Agent Loop

- `RuntimeEngine` 驱动 Context → Model Attempt → Response validation → Tool 或 Final Answer 的循环。
- Runtime 接受同一模型响应中的文本和有效 ToolCall。已校验的文本可以先作为普通 `assistant_message` 写入 Feed，Tool 执行完成后 Run 继续。
- Provider context pressure、`context_exceeded`、projection overflow 和 compaction progress 会参与下一次决策。
- Protocol validation failure 会被转换为受控的 protocol repair context。空响应有独立的重复响应处理。
- Cancellation、Approval、Reconciliation 和 operational segment rollover 都在安全点处理。
- LoopGuard 使用 ToolCall、Workspace version、reconciliation epoch、Context fact frontier 和 active error 的 semantic fingerprint。首次重复会注入恢复信息，恢复后再次回到同一状态才会以 `repeated_tool_call` 或 `no_progress` 停止。
- Runtime 没有固定的模型步数、Run 时长或 repeated-call counter 生命周期规则。

## Context

- ContextBuilder 从 SQLite facts、当前 Run、Model Profile、Project Rule Snapshot、Selected Skill、历史 Item、Tool Result、Workspace state 和额外上下文构建模型 Context。
- Context Budget 记录 active tokens、模型窗口、百分比和 `provider`/`estimated` 来源。
- Provider Usage 优先作为 active Context truth。Provider Usage 缺失时，Runtime 使用有界 estimated fallback。
- ContextBuilder 对 Workspace state 未变化时完全相同的部分只读 Tool Result 做去重。
- ContextCompactor 使用 deterministic bounded extraction 保存任务目标、约束、动作、证据、修改、失败尝试、决定、待处理 Approval、未解决问题和下一步。
- Compaction Summary metadata 与主体一起持久化。原始历史不会被摘要替换。
- ContextPlan、ContextSnapshot 和 Verified Compaction 具有 typed persistence boundary，但它们还没有全部成为默认在线 Run 的强制组装路径。

## Project Rules

- ProjectRuleResolver 支持 `EIDOS.override.md`、`EIDOS.md`、`AGENTS.override.md`、`AGENTS.md` 和 `CLAUDE.md`。
- Resolver 从 Workspace root 到 effective cwd 逐目录解析。
- 每个目录只选一个最高优先级的非空候选。
- Resolver 使用共享 32 KiB byte budget，并记录 shadowed candidates、warning、原始 hash、包含字节数、directory level 和 effective cwd。
- InstructionResolver 将 System Safety、Base Agent、Runtime Policy、Project Rules 和 Selected Skill 组成有来源的 immutable instructions。
- Step Resolution 保存 resolved instruction hash。Project Rules 不会改变 Runtime Permission、Approval 或 Sandbox 的真实执行约束。

## Repository Discovery

- `list_files` 和 `search_text` 可以在 Workspace 内执行有界文件发现和文本搜索。
- Workspace discovery 使用根目录 `.gitignore` 与 `.eidosignore`，并把发现规则和安全权限分开处理。
- `search_text` 使用随 Runtime 管理、manifest 校验和 SHA256 校验的 macOS arm64 Ripgrep 资源。
- Repository Intelligence 基础设施已经实现 Inventory、Repository generation、Tree-sitter Index、symbols/imports/references/chunks、Repository Map、SQLite FTS5、RapidFuzz retrieval、Retrieval Snapshot 和 ContextPlan。
- Repository Intelligence 的不完整 generation 不会替换上一个完整 generation。完整 generation 原子保存相互绑定的 Inventory、Index 和 RepositoryMap。Workspace 激活会 fast restore 三者，不会读取当前 manifest、Git branch 或 Git HEAD，也不会重新运行 builder。
- `RepositoryWorkspaceRuntime` 为每个 Workspace identity 保存一个 active immutable Snapshot、recovery status、dirty paths、invalidation epoch 和 watcher。`ensure_ready()` 会在 Run 第一次模型执行前完成首次 generation build 或一次 reconciliation。Clean Run 不 scan。同一个 Run 的模型 Step 复用一次捕获的 generation。Watcher 只提供失效信号，不改变当前 Snapshot，也不在 Run 内自动 build 新 generation。
- Repository Generation 发布会验证 Inventory-bound manifest 内容，并在 commit 前用 Dulwich 再次验证 Git branch 和 HEAD。并发 readiness 只允许一个 build。build 期间的新 watcher event 会保留 dirty 和 reconciliation required。
- v1 mapless generation 通过独立 generation watermark 推进新 builder counter。首个 v2 complete generation 使用更高 generation，不会把 legacy row 当成可恢复 Snapshot。
- Existing Session read 的 Repository prewarm 是 best-effort。缺失 Local root 和 `MISSING`、`INVALID`、`DELETED` Worktree 不会阻止 Desktop 读取 Session snapshot。Run admission 仍执行权威 Workspace 校验。
- Session create、existing Session read 和完成 binding 变更的 handoff 会激活真实 execution workspace。Run admission 和 RuntimeEngine start 提供 authoritative fallback。Runtime shutdown 会停止 watcher。Cold start 会保留 reconciliation requirement，因为旧 Inventory 不能排除停机期间新增路径。
- RepositoryApplication、ContextApplication 和相关 persistence repositories 已提供 typed composition boundary。自动 Retrieval Query、ContextPlan 和 ContextSnapshot 模型注入还没有进入默认 online Run。

## Runtime Git Worktree Kernel

- Runtime 可以从任意已验证的 filesystem workspace discovery Project。Git repository root 和 Git common directory 是成对可空的 optional Project capability。
- Runtime 对 Non-Git Project 提供 Local Execution Session。Git Project 可以选择 Local 或 Managed Worktree Execution。
- Runtime 提供 `project/gitContext`，通过现有 `GitBackend` 返回 Git availability、current branch、HEAD、local branches、dirty 和 changed file count。Renderer 和 Electron Main 不执行 Git CLI。
- Runtime 可以创建、打开、验证、列出、恢复、清理和删除 managed Worktree。新 Worktree 使用 Runtime-controlled root 和 detached HEAD，不创建 `eidos/*` branch。`baseRef`、immutable `baseCommit` 和 nullable `branch` 会进入 Worktree projection。
- Runtime 提供 managed Worktree retention。Retention 只处理 `ownership = managed` 的 active Worktree，并按 durable `last_used_at` 保留最近 N 个。Runtime 默认保留 15 个，且可以关闭自动清理。active Run、unfinished Handoff、unfinished lifecycle、invalid identity、legacy managed branch 和无法完成 Snapshot 的 Worktree 会被跳过。
- Runtime 提供 durable Worktree Snapshot、safe cleanup 和 Restore。Snapshot 使用压缩的 Git CLI full/staged patch bytes、Pydantic manifest、SHA-256 artifact checksum 和 `refs/eidos/worktree-snapshots/<snapshot_id>` hidden ref。Artifact 只保存 `full.patch.gz`、`staged.patch.gz` 和 `manifest.json`，不保存完整 Index、unchanged blob 或 structured repository state，所以 capture 和 artifact 大小接近 O(changed files)。Snapshot 不保存 ignored environment。Restore 会重新 materialize `.worktreeinclude`，再由 Git CLI apply patch，并恢复原 Worktree id、原 Worktree root、Snapshot HEAD 和原始 `baseCommit`。
- Runtime 可以实时查询 Worktree 的 HEAD、detached/attached branch、dirty、staged、unstaged、untracked 和 conflict 状态。Detached durable identity 与 observed branch 都为 NULL 时才有效；legacy attached Worktree 仍要求 branch identity 匹配。
- `session/createBranch` 可以在 active、valid、detached Worktree 中执行 `Create Branch Here`。Runtime 在 durable intent 中冻结当前 HEAD 为 `expected_head`，然后创建 branch 并再次验证该 HEAD。`base_commit` 仍是 Worktree 创建时的 immutable baseline，不参与 branch attach HEAD 判断。Runtime 不创建第二个 Worktree，不改变 HEAD 或 working-tree changes。成功后 Worktree 持久化 `branch_ownership = user`。User Branch handoff 到 Local 后，Runtime 只在确认 Local branch 和 HEAD 后清除 Worktree 的 `branch`、`checkout_branch` 和 `branch_ownership` metadata，不删除或修改 Git ref；该 branch 之后属于普通用户 Git 资源。Session delete 会移除仍由 Eidos 管理的 Worktree，但保留 user branch。已存在或在其他 Worktree checkout 的 branch 会拒绝。Branch attach 后 HEAD 改变会进入 recovery required，Runtime 不 force switch。
- Runtime 可以返回 HEAD diff 和基于创建时 immutable `base_commit` 的 baseline diff。两种 Diff 都包含 tracked 和 untracked files。Diff 使用 typed `GitDiffObservation`、有界 patch 和 truncation metadata，不修改 Git Index 或 object store。
- SQLite v1 基线保存 Project、Worktree ownership、branch ownership、`worktrees.last_used_at`、`runtime_settings`、`worktree_snapshots`、lifecycle state、`sessions.execution_mode`、`sessions.worktree_id`、`sessions.associated_worktree_id`、`worktrees.checkout_branch` 和 managed lifecycle intent。基线测试验证完整表结构、Worktree FK、Run、nullable branch、retention defaults 和 Worktree lifecycle fields。
- Session create、Session delete、managed Checkpoint Fork、Create Branch Here 和 Session Handoff 支持同进程 operation serialization、durable prepare、restart reconciliation 和同 operationId retry。Retry 不会重新生成 Worktree identity。Branch attach recovery 和 Handoff recovery 不会 force switch、force checkout 或创建第二个 Worktree。
- `GitBackend` 把 Git mechanics 与 Eidos Worktree lifecycle 分开。Eidos 不实现 Git working-tree 或 index semantics。Dulwich 负责 discovery、refs、branch validation、revision、ignore observation、worktree list/remove/prune、switch、reset、normal clean 和 hidden snapshot anchors。唯一的 `GitCli` authority 负责 status porcelain、diff、patch capture/apply、untracked path/new-file patch、Worktree Add 和 destructive `clean -fdx`；它不作为 Dulwich failure retry path。
- 所有 Git CLI 调用都经过 `HardenedGitRunner`。Runner 负责 argv、stdin bytes、bounded output、timeout、process group cleanup、isolated environment、hooks/fsmonitor/credential/askpass/pager 和 external diff/textconv/filter hardening。Status、diff、capture、apply、Worktree Add 和 destructive clean 不会无意执行 configured helper。
- Git CLI diff 可以观察 Gitlink/submodule commit 变化。Dirty submodule checkout 不能由 patch apply 完整恢复，因此 capture 对 changed gitlink 返回 typed `worktree_gitlink_unsupported`，不自己创建 nested Repo、Blob 或 IndexEntry。
- Linked managed Worktree 的 `git_dir` 和 `git_common_dir` 可以在默认 Seatbelt 中只读访问。Git metadata 写入仍然被 Seatbelt 拒绝。原始 repository working tree 不在该 Workspace 的访问范围内。
- Git lifecycle 不经过 Model Tool。Desktop 以 Session 为单位读取 branch、status 和 diff。Sidebar 不轮询所有 Thread；dirty indicator 使用已有的 Session status cache。

Non-Git Project 不提供 Git status、Git diff、Managed Worktree 或 Git-based Fork。它仍然提供文件、Shell、Skill、MCP、Context、Long Task、Sandbox 和 Checkpoint。Local Checkpoint Fork 创建共享同一真实目录的新 Session；它不创建 filesystem snapshot。

## Tools

- Tool Registry 统一保存 ToolSpec、Schema、Execution Policy、Concurrency Policy、Projection Policy 和 provenance。
- 内置只读 Tool 包括 `list_files`、`read_file`、`read_file_range` 和 `search_text`。
- Workspace mutation Tool 包括 `write_file`、`apply_patch` 和 `delete_file`。
- `apply_patch` 使用 bounded Unified Diff 解析，并要求单文件、Read Evidence、Base Hash、Approval 和版本复检。
- `tool_search` 可以从当前 Tool Snapshot 中发现延迟 Tool。
- `skill_create` 和 `skill_install` 使用受控的 Eidos-state Tool 路径，并经过现有 Approval/Tool contract。
- ToolCallRuntime 和 ToolExecutionController 会执行输入校验、准备、Intent、执行、验证、敏感扫描、结果投影和事务提交。
- 只有安全只读的 `parallel_safe` Tool 批次可以并发。副作用 Tool 保持独占，结果按模型声明顺序提交。

## Shell

- `run_shell` 需要 Approval。
- 默认 Shell attempt 使用 macOS Seatbelt，使用明确 cwd、受控环境、超时、有界 stdout/stderr 和进程组终止。
- Shell 不继承宿主 API Key、`HOME` 或任意敏感环境变量。
- Shell launch boundary 验证 Workspace identity 和 cwd。post-execution observation 记录 Workspace diff、退出状态和 reconciliation 需要性。
- Workspace manifest observation 不完整时可以产生 `unknown` observation。已知成功退出不会仅因为观察不完整而被改成不确定副作用。
- Shell 支持最多一次明确的权限升级 attempt。升级仍需要新的 Approval，并且不能移除 hard confidentiality deny。

## Approval / Sandbox

- File change Approval 展示完整 diff。磁盘在 Approval 前不发生写入。
- File change Approval 后会重新验证版本并原子提交，再验证最终内容。
- Command Execution Approval 展示 command、cwd、timeout、network 和 effective sandbox permissions。
- MCP external Tool 使用同一 Approval、Sandbox、Tool Result 和 reconciliation 语义。
- Seatbelt Policy、Workspace boundary、Eidos data/runtime protection、sensitive scanning 和 resource cleanup 失败时 fail closed。
- Durable Intent 记录已授权但可能有副作用的执行。未知结果会进入 reconciliation，Runtime 不会猜测成功或失败。

## Response Actions

- 已完成的 assistant response 支持 `up` 和 `down` feedback。
- 最新可见的终态 Run 支持 regenerate。
- 最新可见的用户输入支持 edit resend。
- Response Action 通过 `responseAction/state`、`item/setFeedback` 和 `run/revise` 持久化到 `response_feedback` 与 `run_revisions`。
- Revision 会创建新的 Run，并保存 source Run 与 revision kind。Source Run 的历史不会被当作新的可见 Run 重复参与后续 Revision。

## Plugin / Skill / MCP

- PluginCatalog 支持本地 Plugin v1 的导入、启用、禁用和移除。
- Plugin manifest 可以声明 Skill 和 MCP Server。安装内容有文件数量、大小、路径、manifest、版本冲突和 content hash 校验。
- SkillCatalog 支持 bundled system Skill、用户 Skill、Plugin Skill、Catalog Snapshot、SelectedSkillSet、主资源和受控 Resource 读取。
- Skill provenance、Plugin hash、Skill content hash 和 activation snapshot 进入 Run/Step 边界。
- MCP 当前支持 stdio Tools。Server consent、`connector`/`workspace_read` permission profile、Tool discovery、Tool call、timeout、结果 schema 和 Tool List Changed bookkeeping 已接入。
- MCP Connection 由唯一 RuntimeAsyncKernel 持有，不为每个连接创建专用 Event Loop。

## Persistence

- 当前 SQLite schema 是 v2。新数据库直接创建完整当前 schema。Runtime 支持 v1 到 v2 的单步事务迁移。其他旧 revision、未知 revision 和未来 revision 不会自动迁移。版本不匹配时，Runtime 保持数据库不变并进入 `health_only`。
- SQLite 保存 Session、Run、Item、ToolCall、Approval、Step、Model Attempt、Execution Segment、Durable Intent、Event、Outbox、Async Operation、Extension、Context、Repository Snapshot、Compaction、Checkpoint、Response Feedback、Run Revision、Project 和 Worktree。
- 业务事实变化与 Event/Outbox 在同一 transaction 中提交。
- SQLite 使用私有数据目录、WAL、busy timeout、完整性检查、单实例锁和 health-only 失败状态。

## Recovery

- Runtime 重启时会从 SQLite 收敛未完成 Run、ToolCall、Approval、Outbox、Long Task 和资源状态。
- Runtime 启动时会读取 Worktree lifecycle intent、Snapshot metadata、Snapshot artifact、hidden ref 和 Session Handoff operation，并在业务应用暴露前执行 bounded reconciliation。Worktree Session create、Session delete、Managed Checkpoint Fork、Branch attach、retention cleanup、Restore 和 Session Handoff 可以在 restart 后恢复或进入 cleanup required。Local Session 不需要 Git lifecycle recovery，除非它有未完成的 Handoff operation。
- Runtime 支持 cancel、pause、resume 和 restart verification 的 typed boundary。
- Resume 前会检查 Workspace identity、规则、Repository/Context snapshot、permission snapshot、Git 和 side-effect reconciliation 字段。
- Cancel、Tool timeout、Shell cleanup、MCP shutdown 和 Runtime shutdown 都有资源跟踪和有界等待。
- 不确定副作用不会自动重放。需要核验的事实会进入 reconciliation。

## Checkpoint

- Runtime 提供 checkpoint create/list 和 rewind/fork action lineage 的 typed RPC。
- Checkpoint 记录 Rule Snapshot、Repository Snapshot、Context Snapshot、Compaction Summary、Workspace identity、Git、permission、Model snapshot 和 reconciliation 状态引用。
- Managed Checkpoint Fork v1 从 `checkpoint.gitHead` 创建新的 detached managed Worktree，并保存 `branch = NULL` 的 Worktree Session、Run 和 checkpoint action lineage。相同 `checkpointId` 与 `operationId` 不会创建第二套 Worktree、Session、Run 或 action。
- Local Checkpoint Fork 使用相同 Project、相同 workspace root 和 `worktreeId = NULL` 创建新的 Local Session、Run 和 lineage。两个 Local Thread 共享真实目录。该 Fork 不表示 filesystem snapshot。
- Checkpoint action 以 append-only lineage 保存 source Run、target Run 和 action kind。完整 rewind/fork Context 重建、全部 immutable snapshot 复制和完整未提交 patch 恢复仍属于限制。

## Distribution

- `pnpm build:runtime:mac` 可以构建 macOS arm64 的 self-contained Runtime Bundle。
- Bundle 使用 managed CPython 3.12.13、锁定的 production dependencies、Runtime 资源和受管 Ripgrep。
- Packaged Electron 使用 `Contents/Resources/runtime/`，不回退到系统 Python、PATH、`.venv` 或用户 `PYTHONHOME`。
- `pnpm package:mac` 生成未签名的本地 arm64 DMG，并执行 packaged smoke。
- `pnpm package:mac:release` 接入签名、hardened runtime、notarization、stapling 和 Gatekeeper 验证。Release 需要构建机提供 Apple credentials。

## Observability / OpenTelemetry

- Runtime 入口初始化进程级 OpenTelemetry Trace Provider。默认 `OTEL_TRACES_EXPORTER=none`，因此默认不会向外部后端导出 Trace。
- 当前支持 `console` 和 OTLP HTTP Trace exporter。`OTEL_SDK_DISABLED` 可以关闭 SDK，`OTEL_SERVICE_NAME` 可以覆盖服务名，`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 可以配置 OTLP Trace endpoint。
- Runtime 创建 `eidos.run`、`eidos.model.attempt` 和 `eidos.tool.call` Span。
- Model Attempt Trace 可以记录 Provider、resolved model、finish reason、TTFT、duration、transport retry、input/output token 和 cache token usage。
- Tool Trace 可以记录 Tool 名称、Call ID、终态、Workspace changed 和异常；Run Trace 可以记录 Run、Session、Model 和最终状态。
- OpenTelemetry 只提供 Observability，不参与 SQLite 事实、Run 状态迁移、Approval 或 Reconciliation 决策。

## Diagnostics / Tests

- Runtime stdout、stderr、JSON-RPC 行大小、未知 response id、非协议 stdout 和协议错误都有边界检查。
- `GitBackend` contract tests 覆盖 discovery、HEAD、branch、status、diff、untracked、conflict、linked Worktree、Unicode path、binary、symlink、mode、staged/index transfer 和 Pydantic snapshot manifest。配置 marker tests 验证 hook、fsmonitor、textconv、external diff、clean/process/smudge filter 和 worktree hook 不会执行。
- Native Seatbelt tests 使用真实 linked Worktree 验证 Git read、普通 Worktree write 和 Git metadata write denial。Local Workspace 使用没有 Git path 的独立 Seatbelt profile。
- Phase 3D focused tests 覆盖 retention limit、`last_used_at`、protected Worktree、dirty Snapshot、binary/untracked/deleted/staged patch、detached commit anchor、ignored environment、same identity Restore、Run admission、Session Delete cleanup 和 cleanup/restore restart recovery。
- `pnpm test` 覆盖 Runtime、contracts、Renderer state、Main 和 Renderer behavior。
- `pnpm check:python` 覆盖 Ruff、deptry、Runtime tests 和 Python dependency audit。
- Seatbelt native、Electron startup/shutdown、bundled Runtime、packaged App 和 packaging config 都有独立测试入口。
- Repository、Project Rules、Context、LoopGuard、response actions、schema baseline、checkpoint、long task、MCP 和 telemetry 都有 focused test files。

## Implementation Anchors

- `desktop/main/main.ts`
- `desktop/main/preload.ts`
- `desktop/renderer/src/components/ExecutionFeed.tsx`
- `desktop/renderer/src/components/ContextIndicator.tsx`
- `runtime/eidos_runtime/protocol/response_server.py`
- `runtime/eidos_runtime/runtime/engine.py`
- `runtime/eidos_runtime/runtime/loop_guard.py`
- `runtime/eidos_runtime/context/`
- `runtime/eidos_runtime/model/config.py`
- `runtime/eidos_runtime/tools/`
- `runtime/eidos_runtime/sandbox/`
- `runtime/eidos_runtime/extensions/`
- `runtime/eidos_runtime/repo_intelligence/`
- `runtime/eidos_runtime/telemetry/provider.py`
- `runtime/eidos_runtime/telemetry/tracing.py`
- `runtime/eidos_runtime/git/`
- `runtime/eidos_runtime/application/worktree_retention.py`
- `runtime/eidos_runtime/git/snapshot_artifacts.py`
- `runtime/eidos_runtime/persistence/worktree_snapshots.py`
- `runtime/eidos_runtime/db/schema.py`
- `runtime/eidos_runtime/persistence/`
