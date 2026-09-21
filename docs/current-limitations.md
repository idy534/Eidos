# Eidos 当前限制

> 权限模式范围：本文原有的逐操作请求审批、永久拒绝和 Seatbelt 保护说明适用于 `manual` 与 `auto_review`。`auto_review` 用模型代替人工作出原有审批决定。用户在 Desktop 确认的 `full_access` Run 使用当前 macOS 用户的文件和网络权限，并关闭执行沙盒；该模式不保留 Eidos 数据、Runtime、系统 Skill 和 Git metadata 的永久写入保护。所有模式仍保留参数、身份、版本、取消、Durable Intent、结果校验和 Reconciliation。权限模式相关单元与行为测试已纳入测试套件。

- `session/read` 不提供 Step Resolution Review 内容，兼容字段 `stepResolutions` 固定为空数组。Desktop 当前不展示这些信息，Runtime 也未新增按需详情 RPC。完整执行快照仍持久化并由执行读取入口校验；打开 Session 不承担这些 Blob 的完整性检查。本项代码修订尚未验证，不能据此宣称 UI 打开耗时已经达标。

本文只记录 Stage 4+ 后续能力、其他平台支持，以及没有 Apple credentials 时无法验证的真实签名结果。本文不记录已经解决的问题，也不记录历史阶段。

## 平台与分发

- Eidos 当前只支持 macOS arm64 Desktop、Runtime Bundle、App 和 DMG。
- Linux、Windows、macOS x64、Universal binary 和其他平台的 bundled Runtime 与 Ripgrep artifact 尚未实现。
- Auto Update、GitHub Release 发布闭环和其他平台 artifact 尚未实现。
- Release packaging 的签名、notarization、stapling 和 Gatekeeper 命令已经接入脚本，但这些步骤需要 Apple credentials。没有 credentials 时不能证明已生成可发布的 Release artifact。

## Stage 4+

### Model Provider

- ModelConfigStore 只接受内置 DeepSeek、MiniMax、Kimi 和火山引擎 Catalog 中的九个 Model ID，包括火山引擎目录项 `glm-5.3-flash`。
- 当前不支持 arbitrary custom provider、arbitrary base URL、arbitrary model ID、连接测试或主动 capability probe。当前内置 Model Catalog 没有启用 Responses API 或 native Custom Tool capability。
- 当前内置模型的 wire API 固定为 OpenAI-compatible Chat Completions/SSE。Runtime 已有按 ModelProfile capability 路由的 Responses native adapter，但没有未经验证地为内置模型打开该路径。
- 思考强度选项和默认值按模型配置。当前 Catalog 中的选择项不证明 Provider 已接受对应请求参数。Volcengine Coding Plan `/api/coding/v3` 的模型专属 wire 字段、值和默认行为尚未通过可独立读取的官方端点文档或受控请求验证。MiniMax M3 直连 API 只确认支持思考开关，没有已确认的离散 effort 档位；Kimi K2.7 Code HighSpeed 固定开启思考，没有已确认的 effort 档位。
- 用户要求分阶段验收：生产代码修改完成后先等待用户确认；确认后再编写测试并集中验证。等待期间不新增测试，也不把尚未做的 Provider endpoint 验证写成通过。
- Chat Completions 没有原生的 Assistant `phase` 字段。Adapter 只根据 ToolCall 做 `commentary` 分类，并保留 Provider 的 `finish_reason`。`MessagePhase` 可以是 `commentary`、`final_answer`、`unknown` 或 `None`，但它不控制 Agent Loop。Agent Loop 使用 normalized response 的 `needs_follow_up` 决定继续采样还是完成当前 Turn。
- 回答流式输出的代码修订尚未编写或执行测试。敏感扫描仍按完整行释放文本，无换行的长段落会等到响应结束。尚未闭合的尖括号文本会等待闭合或完整响应校验，以避免跨片段的 Provider 控制标记进入 Feed。当前实现不是逐 token 输出。
- Assistant 文本在完整响应校验前可以作为 `in_progress` Item 显示。Runtime 只有在校验成功后才确认它；失败草稿不会进入后续模型上下文。Runtime 不会额外调用模型来提前判断最终回答。如果响应随后包含 ToolCall，现有 Feed 会把前面的文本归入过程区。
- 已发布文本会参与 transport retry 的安全判断。normalization 的 `protocol_error` 和 `length` 仍走现有有界 protocol repair；失败草稿先结束，新的 Attempt 使用新 Item。`content_filter`、cancel 和 authentication failure 不进入该 repair。旧 Event 缺少 offset 时不能获得新的偏移去重保证。增量缺口会等待现有 Run 结束快照刷新来修复。
- 本次未修改 SQLite 表结构，但新 delta Event 会保存 offset。旧 Runtime 的严格 Event 校验不接受这个字段，所以产生新 Event 后不能直接把同一数据目录交给旧 Runtime；回退需要恢复升级前备份。协议 Fixture 和回归测试按用户要求留到确认后的测试阶段。
- Context Usage 的 estimated 值是有界 fallback，不是 tokenizer 精确值。它不能单独证明 Provider 已拒绝请求。

### Run 并发与资源模型

- 普通 Run 没有并发上限。Runtime 按 Session 分别维护持久 FIFO，同一 Session 同时只运行一个 Run，因此一个 Session 可以排队多个 Run；不同 Session 可以并行，且不区分 Workspace、Local checkout 或 Managed Worktree。每个 Run 有独立的 `ToolConcurrencyGate` 和 `ShellProcessManager`，所以不同 Session 可以在同一个 Workspace 并行执行 Shell 和其他普通副作用。一个 Run 的长 Shell 仍会阻止这个 Run 启动新的副作用，但 `write_stdin` 可以继续管理原 Shell。等待 Approval 不会占用其他 Run 的执行资源。
- 当前每个活动 Run 使用一个 Worker Thread。模型异步 I/O、MCP、Managed Task 和安全只读批次由唯一 RuntimeAsyncKernel 管理。当前没有把整个 RuntimeEngine/RunSupervisor 改成原生 async 的实现。
- Managed Task 的提交与事件循环清理之间存在过同步锁循环等待。当前代码已将完整任务生命周期移到 worker，测试按用户要求延后。事故没有保留线程栈，因此该代码缺陷与事故之间尚未完成动态验证。该修订不解决所有慢 Git、stdout/stderr 背压或遥测关闭问题，也不改变既有 RPC deadline。
- Workspace 写入、Shell、MCP、external 和 Eidos-state 的实际副作用窗口只在各自 Run 内独占。不同 Session 的 Run 可以并行，即使共享同一个 Workspace。安全只读的 `parallel_safe` 批次保留自身的有界并行。同一个 Workspace 的并发修改可能让 Workspace observation 或 diff 包含其他 Session 的变化。
- 当前没有用户可配置的统一 Run 成本、模型步数或有效时长上限。现有 step、segment 和 effective time 字段主要用于 telemetry 和 operational lifecycle。

### Workspace 与工具

- 内置只读文件工具可以处理当前 Workspace 和 active Skill root 内受支持的普通 UTF-8 文件。写入工具支持普通 Workspace 写入和经审批的外部普通文件写入；普通 Skill 需要明确写入授权，`skills/.system` 始终禁写。已有文件使用受控原地写入，新文件使用排他提交；工具不处理 hardlink、symlink、特殊文件、特殊 mode 或文件 flags。
- 普通 Workspace 读取、列目录和文本搜索不按敏感文件名或可识别敏感内容拒绝。`.git`、`.agents` 和 `.eidos` 默认发现时隐藏，但显式只读路径仍可列举、搜索和读取；这三个目录默认受到写入保护。只有获批的 `run_shell.gitWriteAccess` 可以写当前 repository 的 Git metadata。Workspace 外的 Eidos data 和 credential 路径仍由永久拒绝保护。
- `apply_patch` 统一使用 Codex Patch：支持 Custom 和 Grammar 的相应 profile 直接提交原文；Function profile 提交 `{ "patch": "..." }`。旧的 `{ "changes": [...] }` 不再接受。两条路径的 Patch 原文预算统一为 8 MiB，Function 外层 JSON 另留转义空间。`apply_patch` 不再按内容模式或敏感文件名拒绝 Patch。单文件读取、准备和提交校验上限统一为 16 MiB；一次 Patch 的准备内容预算为 64 MiB，Diff 和结果各有 64 MiB 上限。预算约束 Runtime 资源，工具说明不规定文件拆分、补丁大小偏好或分批时机。JSON 类型、Patch 语法、当前文件匹配和最终写入验证是不同阶段；单个阶段通过不代表编辑成功。离线回归验证契约和安全行为，不能代表真实 Provider/模型的成功率；模型成功率需要另行使用相同任务集测量。
- Custom Tool 的 input delta 在 Responses adapter 内部重组。Desktop 在完整参数通过解析并完成 Prepare 后显示持久补丁更新；它不展示尚未校验的逐 token 补丁。Prepare 完成不代表文件提交成功。
- Add 内容会统一规范化为 LF，并按 Codex 行语义补尾部 LF。Add File 可以没有内容行；显式的 `+` 表示一条空内容行。因此空字符串、单个换行和两个换行会保持不同的解析结果。Parser 接受 CRLF 和外层空白，但不会猜测缺失的 envelope、marker 或行前缀。
- 本地 grammar 将上游 `add_line+` 改为 `add_line*`，以对齐 Codex Rust streaming parser 的空 Add 行为。Lark 对上游零宽文本正则的写法也使用了兼容 token。其他 Workspace 边界和文件安全限制不变。
- `apply_patch` 支持 Add、Update、Delete、Move 和多文件 Patch，但不提供通用二进制编辑、浏览器自动化或 Artifact 发布工具。
- Desktop Terminal 是用户直接操作的临时 PTY。它不属于 Agent Tool，不经过 Runtime Approval 或 Seatbelt，也不会写入 SQLite、Checkpoint、Conversation 或恢复状态。关闭 Terminal Tab、切换 Session execution binding、删除 Session、关闭窗口或退出应用都会终止对应 PTY。Review 和 Files 各只打开一个工具 Tab；Terminal 可以打开多个 Tab，Files 可以同时预览多个文件。
- Projectless Conversation 不创建 Project，也不提供 Git status、Git diff 或 Repository Intelligence。它使用系统私有锚点作为 workspace，并提供文件工具、Shell、Skill、MCP、Plugin、Files 和网页预览。它只支持 Local execution。
- Workspace Explorer 支持有界 UTF-8 text/code、Markdown、常见图片、PDF 和 HTML。其他二进制、Office、archive 和 database 文件不做内嵌预览。Explorer 不提供编辑、搜索、文件拖放、重命名或删除。当前的拖动交互只用于调整文件树与预览区的宽高。
- Non-Git Project 已经支持 Local Execution Session。Non-Git Project 不支持 Git status、Git diff、Managed Worktree 或 Git-based Fork。Local Checkpoint Fork 共享真实 workspace，不提供 directory snapshot、copy-on-write 或 filesystem rewind。
- Git Project 可以创建 Local 或 Worktree Execution Session，也可以在同一个 Session 中执行 Local ↔ Managed Worktree Handoff。Local Session 支持切换 local branch，也支持基于当前 branch 创建并切换到新 branch。该操作要求 workspace clean 且没有任何共享该 workspace 的 active Run。Managed Worktree 默认是 detached HEAD。Runtime 已提供 structured status、compare ref、精确文本行统计、file-scoped diff、stage、unstage、discard、commit、fetch、fast-forward-only pull、push、merge、rebase 和对应 continue/abort typed API。Desktop Review 已提供文件手风琴、展开或折叠全部、Stage、Unstage、tracked/untracked Discard、Open in Editor、inline Review Comment，以及 Commit、Fetch、Pull、Push、Merge、Rebase 和 Local branch 控制。二进制文件没有可用的文本行统计，`statsIncomplete` 会明确标记。Stash 尚未实现。
- Desktop Remote Git 不提供 credential 配置或 PAT 输入。它只复用系统 Git credential helper 和 SSH Agent。Advanced Git target 当前只列出已观察到的 local branch。它不接受任意 revision 文本，也不提供 remote ref browser。
- Inline Review Comment 当前只支持单条行级 Comment、删除、精确 anchor 失效和 active Comment 批量发送。它不支持 thread、reply、mention、reaction、云同步或模糊 re-anchor。stale Comment 只保留为历史提示，不会自动进入 Agent feedback。
- Git diff 对已记录的 submodule 只观察 Gitlink HEAD 和 submodule workspace 缺失。submodule 内部的 nested working-tree dirtiness 尚未向父 repository diff 暴露。
- Workspace discovery 只读取 Workspace root 的 `.gitignore` 和 `.eidosignore`。当前不支持 nested `.gitignore`。
- Ignore 规则只影响普通 `list_files`/`search_text` 发现结果。`.git`、`.agents` 和 `.eidos` 等 hard discovery 目录默认隐藏，但显式只读路径仍可读取。Ignore 规则不是权限。
- `search_text` 没有 LSP、AST 查询和基于 Repo Intelligence 的默认搜索路径。它仍然使用受管 Ripgrep，结果、preview、单文件和查询大小都有界。首次等待和后续等待不会结束搜索进程，但搜索不能跨 Run 或 Runtime 重启恢复。每个 Run 最多同时运行 4 个搜索，最多保留 16 个未领取的搜索会话；单个搜索进程最多运行 600 秒。
- 已声明 Tool 的参数错误会返回 `invalid_arguments` Tool Result。Runtime 只保留有界字段路径、稳定原因码和有限数值约束，例如 `field=yieldTimeMs, reason=less_than_equal, maximum=30000, actual=60000`，不返回原始参数值。敏感或超大的结果在 projection 重建为错误时仍保留显式的 `reconciliationRequired=false`，不会把它改成 unknown。
- Shell post-execution observation 在超时或不完整 Workspace manifest 时可能是 `unknown`。这类 observation 不能替代 Runtime 明确报告的执行 uncertainty，也不会单独限制已明确退出的 Shell。对于仍由 Runtime 管理、结果带有有效 `sessionId` 且明确报告 `reconciliationRequired=false` 的 `executionStatus=running` Shell，Workspace observation 不完整不会单独建立 reconciliation。Runtime 仍会保留 `workspaceChangeState=unknown`、`workspaceDiffIncomplete=true` 和 `sideEffectsMayExist=true`。完整的 Workspace 状态与安全事实仍需要后置核验。
- `gitWriteAccess=request` 授权的是一条获批 Shell 命令对当前 repository Git metadata 的写入。Runtime 不解析或重写 Shell 命令。原生 Git 仍可能读取用户 Git 配置，并可能执行 repository hooks、credential helper 或命令中显式启动的程序。需要禁用这些机制的产品 Git 操作继续使用独立的 `HardenedGitRunner` typed API。首次 `gh` 登录和凭据配置不由 `run_shell` 自动完成。

### Agent Shell

- Agent zsh/bash 使用原生 `pipefail`，管道前段的失败会影响管道退出码；POSIX sh 保持原行为。`head` 提前关闭管道可能导致 SIGPIPE，调用方应优先使用工具的有界输出。分号后命令的成功仍不能证明前面所有阶段通过。Runtime 不解析任意命令正文来推断业务成功。
- Runtime 的依赖、TLS 和视觉验收指引约束模型决策，但它们不是任意 Shell 程序的静态安全证明。系统不会自动安装缺失的 Office/渲染工具，也不会把结构检查当作逐页视觉验收。`succeeded` 表示 Run 正常结束，不表示所有人工验收已完成。

- `outputComplete=false` 表示输出尚未完整获得，原因可能是仍在运行、捕获失败或原始输出截断。旧结果缺少该字段时，系统不能推断输出完整。`outputCaptureError` 只保存捕获原因码，不能恢复已经丢失的内容；当前 Shell 聚合输出不会因可识别凭据被拒绝。本次历史 Run 的具体捕获子原因不会被新代码补写。
- 同一对账 epoch 最多允许三轮工具恢复，随后现有 Finalizer 收尾并保留对账事实。系统不自动重放未知操作。模型的测试报告指引要求区分收集数与完成数，也要求标明缺失汇总和未执行阶段；这项指引不等于系统能够自动证明任意测试结论。

- Agent Shell 按 Run 独立管理，支持同一 Run 内的管道 stdin 和分段等待，但不提供 PTY，也不跨 Run 或 Runtime 重启恢复进程。不同 Session 的 Shell 可以并行，即使共享同一个 Workspace；同一 Run 的长 Shell 会阻止该 Run 启动新的副作用。
- Runtime 会检测并清理 background child，但 Agent Shell 不能管理持久后台进程。
- ShellEnvironmentSnapshot 不恢复 aliases、functions 或其他 shell state。
- Skill 依赖自动绑定只适用于现有识别器能识别的直接脚本调用，包括 `$RUNTIME_PYTHON` 和 `$RUNTIME_NODE`。Runtime 不会从任意 Shell 包装、变量脚本路径或已激活 Skill 列表推断普通命令的依赖，也不会自动安装缺失包。
- Shell cwd 必须解析到 Workspace 内。调用方可以使用 Workspace-relative 路径或 Workspace 内的 canonical absolute 路径。Workspace 外的 absolute cwd 会返回 Tool Error。
- Agent Shell 的 raw stdout/stderr 仍有 256 KiB 上限。它不提供无限输出流。
- Agent Shell 会对 stdout 和 stderr 做 UTF-8 增量解码。Desktop Execution Feed 在运行中和终态保留两条流的展示脱敏片段顺序，并把 ANSI/OSC 控制序列按纯文本处理；聚合结果仍保留原始 stdout/stderr。旧 Item 缺少或为空的 `content` 时，Feed 使用结果中的 stdout/stderr 回退，并在结果存在时展示 `attemptCount`、`sandboxed` 和 `escalated` 事实。
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
- 模型请求前缀仍有两条中途可变的来源。其一，resolved instructions 的 `runtime-permissions` 层渲染了 `Available tools` 清单和 rejected approval 计数，Run 中途连接 MCP 或出现被拒审批会改写这一层。其二，`user_context_layers` 中的 `selected-skill:*` 位于历史之前，Run 中途激活 Skill 会在全部历史之前插入新消息。两者都会让该 Step 的指令哈希变化并使后续历史无法命中缓存。
- Protocol 与 Desktop 目前都不暴露 cache token：`cache_read_tokens` / `cache_write_tokens` 只存在于 `usage_json` 与 tracing，因此 Prompt Cache 命中率无法从 UI 或协议观测，也没有 `structuralReuseTokens` / cache break 归因字段。
- 当前 Retrieval Query 只使用可以从用户目标、Inventory、Index、已有 Tool Result、dirty path 和 committed change 直接确认的信号。它没有 embedding、Vector Search、复杂 query rewrite、Base Index + Worktree Overlay，也没有 cross-worktree sharing。
- Watcher 事件不是 Workspace 安全事实。Watcher 不会静默修改当前 Run 的 immutable snapshot。

### Recovery 与 Checkpoint

- Runtime 可以持久化 Long Task 控制、pause/resume/cancel、restart verification 结果和 reconciliation 状态。启动时，没有未完成 Tool 执行或不确定副作用的 active Run，且同时没有 cancel request、没有 reconciliation barrier、没有未决有副作用 Durable Intent 和没有 running ToolAttempt 时，才可以重新排队。更广泛的 Restart Verification 尚未覆盖完整的 Git diff、credential、MCP、Seatbelt、pending Approval、unfinished ToolCall、Durable Intent 和 Checkpoint 兼容性集合。
- Checkpoint create/list 和 rewind/fork lineage 已持久化并暴露 typed RPC。Managed 和 Local Git Checkpoint 会保存 HEAD、staged、unstaged 和 untracked Git 状态。Managed Fork 会恢复独立 Worktree 的完整 checkpoint Git 状态。Managed 和 Local Rewind 会恢复原 checkout 的完整 checkpoint Git 状态。Local Rewind 只允许用户显式调用。Rewind 尚未重建完整逻辑 Context。Fork 仍不会复制全部非 Git immutable snapshots。Ignored 文件不进入 Checkpoint artifact。
- Worktree Session create、Session delete、managed Checkpoint Fork、managed Checkpoint Rewind、Create Branch Here、retention cleanup 和 Restore 使用 durable lifecycle intent。Session Handoff 使用 durable operation、strict HandoffPlan 和 startup recovery。Create Branch Here 使用 attach 时冻结的 `expected_head`，不使用创建时的 `base_commit` 判断当前 branch HEAD。Runtime 仍会拒绝 dirty Worktree delete，并保留无法证明安全的目录和 legacy attached branch。Retention 只处理 managed Worktree，不处理 adopted Worktree、Permanent Worktree 或按 bytes 的 disk quota。User Branch handoff 给 Local 后只释放 Eidos Worktree metadata，不删除 Git ref；Session delete 仍会保留这个普通用户 branch。Local Session delete 不删除用户 workspace。当前仍不提供 Permanent Worktree、Pinned Chat、Archive Chat、multi-Session shared Worktree、dependency cache Snapshot 或 Pull Request UI。
- Linked Worktree 的 Git metadata read 和获批后的精确 write 已在真实 macOS Seatbelt 中验证。默认 write、原始 repository working-tree access 和不匹配的 Worktree recovery 仍会被拒绝。Desktop dirty indicator 只使用 `project/gitContext` 和当前 Session status，不做所有 Thread 的持续轮询。Non-Git Local Workspace Checkpoint 仍不保存或恢复 filesystem state。
- Parallel Agent / subagent 尚未实现。本次不为未来 subagent 预设并发上限。cross-worktree Repository Intelligence sharing 尚未实现。
- Runtime 不会恢复内存中的 Model request、Process 或 ToolCall。可能有副作用且执行状态未知的操作必须先进入 reconciliation，Runtime 不会自动重放。已明确 `termination=exit` 且有 `exitCode` 的 Shell 即使 Workspace observation 不完整，也不会因此进入只读模式或阻止后续 ToolCall。取消已停止的 Run 正常返回。未清除的 reconciliation barrier 保留在 `interrupted` 终态中；`sideEffectsMayExist` 不会单独阻断取消。Workspace refresh 只能清除 Workspace mutation 的可核验 barrier，不能清除 Shell、MCP、external、Eidos-state 或 unknown barrier。Timeout、background child 清理未完成、unsandboxed 或 additional permission 失败，以及 MCP、external、Eidos-state 的未知结果仍然 fail closed。

### Compaction 与 Context

- 主动压缩是减少旧历史重发的软策略，不保证固定 token 成本或最优任务步骤。Runtime 保留用户消息、Skill 正文和近期证据，因此有些输入仍会超过软阈值。候选摘要未通过验证时，Runtime 保留原投影。新的有效读取仍可构成进展；LoopGuard 不判断任意任务的业务完成比例。

- 默认 ContextCompactor 使用 deterministic bounded extraction 生成候选摘要。当前没有 model-assisted proposal。
- 候选摘要必须通过 `state.sqlite` 事实验证，才能原子写入 verified record 和权威摘要。Tool provenance 从 summary 的 source Item IDs 对应到真实 ToolCall IDs，并支持 pre-turn 跨 Run 历史。当前 deterministic compactor 不吸收 Event 内容或 Retrieval evidence 正文，所以不会虚假附加这些 provenance。验证失败时，Runtime 保留上一份 verified summary。原始 Item 和 Tool 事实不会被删除。Thread history JSONL 当前是 Event projection，不是独立的全量 Conversation authority。
- MemoryStore 已经提供独立 `memories.sqlite` 和 content-addressed Markdown 存储，但当前 ContextCompactor、用户长期记忆和跨 Session 检索尚未接入这个 Store。
- Context Usage Desktop 展示当前选中 Model 对应 Run 最新 ContextSnapshot 的有效 Context Usage。Snapshot 有 Provider usage 时，Runtime 使用该次请求的 `input_tokens`；Snapshot 没有 Provider usage 时，Runtime 使用 `projected_input_tokens` 的 estimated 值。新 Run 在产生自己的 Snapshot 前显示无数据状态。estimated 值仍然是本地启发式估算，不是 Provider tokenizer 的精确结果。
- Provider 明确 `context_exceeded` 后，如果没有新的可压缩历史或 Context projection 没有进展，Runtime 会以 `context_still_over_budget` 停止。

### Extension 与 MCP

- Plugin 当前只支持本地受管 Plugin v1。当前没有远程 Plugin marketplace、OAuth 安装或任意运行时动态 import 用户 Plugin 的能力。
- Skill Catalog 当前只投影 `SKILL.md` frontmatter 中的顶层 `name` 和 `description`。`agents/eidos.yaml` 的 `runtimeDependencies` 使用严格 typed parser。其他字段可以保留在原始 Skill 内容中，但不会进入 Catalog、协议或权限判断。
- MCP 当前只支持 stdio Tools。Settings 支持创建、编辑和卸载本地手动 MCP。新建或编辑后的 Server 默认未授权，需要通过审阅入口重新授权。当前没有连接测试入口，也没有 Streamable HTTP、远程 MCP transport、OAuth、Resources、Prompts、Sampling 或 Tasks。
- MCP ready connection 是长生命周期 Service，但 startup、Tool call、Tool list、cancel 和 shutdown 都有各自的有界等待。已经开始的 Tool List Changed bookkeeping callback 可能在关闭时需要等待完成。

### Observability / OpenTelemetry

- 当前 OpenTelemetry 集成只配置 Traces。Runtime 的本地 JSONL 日志不等于 OTel Logs pipeline。Runtime 没有建立 OTel Metrics 或 Logs exporter，也没有把 Trace 或本地日志作为业务事实或恢复依据。
- `OTEL_TRACES_EXPORTER` 默认是 `none`，因此默认不会把 Trace 导出到外部 Observability 后端。需要显式配置 `console` 或 `otlp` 才会导出。
- 当前 Trace 主要覆盖 Run、Model Attempt 和 Tool Call。它不是完整的 Desktop 操作链、SQLite transaction、Repository Intelligence、Approval 或 Sandbox 内部阶段的全链路 tracing。
- 较短的默认导出预算和较长的批次间隔只限制遥测开销，不能修复不可用的 collector。SDK 在持续失败或队列耗尽时仍可能丢弃 spans；本地日志、SQLite 和 Outbox 保持独立。维护者的显式 OTEL 配置可以改变默认预算。

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

权限 Grant 只覆盖当前 Run，用户不能选择 Session 或全局范围。当前权限模式代码已加入模型自动审批和完全访问。Runtime 仍不提供 Network Proxy、域名授权或持久 allowlist。路径权限只使用具体路径，不支持 glob。

旧审批或缺少完整执行事实的待批动作不会被猜测为可恢复。Runtime 会继续中断不确定执行，并保留 Reconciliation。用户批准权限不会自动重放之前失败的 Shell。

## Run 收尾与 Shell 执行期限

- 模型提交最终答复时，Runtime 在同一事务提交答复和 Run 终态。未清除的 reconciliation 会使 Run 进入 `interrupted`，不会再强制模型继续只读核验，也不会清除未知 Durable Intent 或放行新副作用。
- 取消后 Worker 已退出时，RPC 返回 `canceled` 或 `interrupted`。系统记录取消完成时间；副作用未知不再作为取消失败。无 Worker 的 queued Run 如果已带有未确认副作用，也进入 `interrupted`。重复取消已中断 Run 返回原终态。Worker 仍存活时继续报告 `RUN_CANCEL_TIMEOUT`。
- 新 Run 的 Shell 不再设置默认进程总期限。`run_shell` 首次等待默认 10 秒，范围 250 毫秒至 30 秒；模型随后使用 `write_stdin` 等待，默认 30 秒，范围 250 毫秒至 60 秒。等待窗口到期只返回 `shell_running` 和 `sessionId`，不会结束进程或触发 reconciliation。ToolSpec 的 600 秒 watchdog 只限制单次启动、审批重试或跟进调用，审批等待不计入预算。历史 3600 秒 ToolSpec 仍可读取，但新工具不会用它限制进程寿命。
- 控制器把已有 Shell 结果转成超时或取消结果时，会保留已有输出和终止信息，并继续执行结果校验和输出限额；展示片段使用 best-effort 凭据脱敏，聚合 stdout/stderr 保留原始内容。进程清理和未知副作用仍按原规则处理。此修改不恢复旧结果中已经缺失的 stdout/stderr。
- Runtime context 使用 `recentToolErrorFingerprints` 表示最近工具错误。空列表不代表对账完成。LoopGuard 不把 assistant 文本变化或最近错误列表中的错误消失单独当作新进展。

- 已有文件的原地写入不是原子内容替换。外部读者可能看到中间内容；写前版本检查不能阻止外部编辑器在检查后并发写入。异常终止可能留下部分文件；Runtime 不会自动覆盖回旧内容或重放补丁。
- 工作区外的已有文件按具体文件申请写权限。新文件使用目标最近的已存在父目录作为请求范围，审批会展示目录和具体文件。不存在的父目录可在批准后创建。受控文件工具仍不能写入永久拒绝路径、链接、`.git`、`.agents` 和 `.eidos`；Git metadata 的单次 Shell 授权只走 `gitWriteAccess`。无沙盒审批不会绕过操作系统 ACL、只读权限或系统隐私权限。

- 外部文件写入的不确定结果需要对外部目标另行核实。当前 Workspace 刷新不覆盖外部文件，因此不能自动清除此类 Reconciliation；Runtime 不自动重放或回滚。

- 普通 Skill 的授权不能覆盖整个数据目录或包含 `.system` 的 Skill 容器。新文件的最近已存在父目录如果包含系统 Skill，工具会拒绝该宽泛授权；新建 Skill 可以使用已有的 `skill_create` 审批流程。
- 系统 Skill 的永久写入保护需要保留在执行环境中。因此任意 Shell 的无沙盒提权在存在永久写入保护时不可用；审批过的受控文件 helper 仍可对允许的具体目标进行无沙盒写入。

## 本轮产物实现的边界

- 本次后续的 `declare_outputs` 结果封装修复尚未补充或运行测试，也未运行 lint、构建或 smoke。历史契约校验失败的声明不会自动变成成功记录；修复后的 Runtime 需要重新执行声明，才能为这些文件生成产物卡。
- 2026-09-13 的产物声明修订已经补充 Renderer、Runtime、协议和 Main 测试。`pnpm test:fast` 和 `pnpm test:full` 已通过。Full 不包含独立的 `large_repository` 规模测试，也不包含真实 Desktop 人工验收；原生 Seatbelt 和 Electron smoke 未运行。
- `declare_outputs` 只声明当前 Run Workspace 内已有的普通文件，不支持外部路径、目录、远程 URL 或历史文件字节快照。工具只核验元数据，不创建、不修改、不校验文件内容，不证明文件由本轮生成。每批最多 20 项；任一文件失败会使整个批次失败，重复声明更新同一文件的声明。系统提示要求仅对用户期望接收的独立终态交付文件调用，创建或修改过不等于交付物，拿不准时默认不声明；但模型仍可能漏调或误调。漏调时只有原始文件和工具记录，界面不会猜测交付；误调仍以已提交的声明事实展示，界面不擅自隐藏。当前没有撤回声明或历史版本下载功能。
- 文件链接和产物卡打开当前 Workspace 内容。历史 ToolCall 保留执行时的 Diff；预览不提供历史文件字节快照。卡片在打开时比较声明版本与当前版本，并显示变化或打开失败信息；当前文件可能在核验后再次变化，图片和 HTML 预览仍使用已有版本授权检查。环境输出汇总已加载的成功声明，不代表完整目录或内容质量。删除不会抹除交付历史，缺失状态在点击时核验。执行目录不同的历史卡片禁止打开。Projectless 对话支持产物声明和文本修改卡。卡片标题使用声明标题或文件名。
- 旧会话没有 `declare_outputs` 记录时不会显示推断的产物卡。工具记录、最终回复中的文件链接和 Files 文件树继续可用；旧 Skill 的 `purpose="output"` 文本和普通路径不会自动补成声明。
- 支持内置预览的产物卡同时提供内置预览、系统应用打开和在 Finder 中显示。文档、演示文稿和 Excel 文件已有专属产物分类，DOC/DOCX、PPT/PPTX、XLS/XLSX/XLSM 提供受控的系统应用打开与在 Finder 中显示，暂不提供内置预览。CSV/TSV 使用文本预览，尚无电子表格网格、公式计算或幻灯片内置查看器。对应的分类、图标和 Workspace Reader 测试已经覆盖这些边界。
- 文本修改卡会区分已完成、部分失败和待核验记录。重复修改同一路径且各次补丁完整时，卡片显示累计增删，并明确标记“累计”；该统计包含重复编辑，不是最终净差异。缺少补丁时仍显示行数未完整统计。当前没有可精确恢复本轮修改的用户级 Checkpoint 入口，所以“撤销”按钮只显示不可用原因。
- “最近一轮”展示文件工具记录的补丁。Shell 的文件观察只能给出路径变化，不能证明每条变化都由该 Shell 独占造成；完整仓库差异仍在仓库范围查看。
- 未提交文件列表为只读审查，不提供暂存、取消暂存、丢弃或按修改块操作；需要变更 Index 时使用提交弹层的暂存后提交，或在外部终端操作。
- Browser 是用户可操作的预览面板，尚未向 Agent 注册浏览器自动化 Tool。网页面板当前不提供标注入口，也没有独立的后退和前进 IPC，所以这两个导航按钮保持不可用。地址栏使用 `tldts` 识别域名、IP 和本地地址；输入框可以在已打开页面后继续编辑，无法确认是地址时会提交 Google 搜索。
- 本地 HTML 禁止外部网络资源、嵌套 frame 和表单提交。依赖外部资源或开发框架的页面应通过用户启动的 HTTP 开发服务预览。HTTP 页面可以按浏览器语义联网，但不能读取本地预览授权。页面权限申请、新窗口和下载尚未开放。
- 图片/PDF 的实际 Chromium 渲染、本地网页 CSP、开发服务热更新、原生视图遮挡和生命周期仍待 Desktop 验收。Office 预览和高级发布不属于本轮前四阶段。

- 大 Diff 和工具结果超过 64 KiB 时，Desktop 通过 `toolCall/readText` 按页读取完整的持久文本，每页最多 16,384 个 Unicode 字符。大 Diff 当前使用原文分页展示，分页内容不进入逐行评论组件。小 Diff 继续使用原有差异视图。页码偏移按字符计算，内容标识使用 UTF-8 SHA-256；Runtime 不把显示摘要当作完整修改记录。
- apply_patch 容量、流式累积和完整文本分页改动已完成单元测试和契约验证。大文本与真实 Provider 的流式端到端测试仍待真实模型调用验证。

### 技能设置管理的当前边界

- 本次设置重构已补充 Runtime 管理、数据库迁移、协议 Fixture、Main 集成和 Renderer 行为测试。定向测试、协议契约检查、Python 检查、Renderer 状态与行为测试、构建、Seatbelt 和 Electron smoke 已完成。Runtime 与 Main 全量测试首次运行发现的问题已经修正，并完成失败用例的定向复测。人工 UI 验收和真实 Provider 验证仍未完成。
- 插件技能的单独卸载是持久化的移除标记，不删除插件包内文件。当前页面不提供恢复已卸载插件技能的入口。
- 独立用户技能的物理清理保守地等待所有非终态 Run 结束。Runtime 在扩展清理回调、下次读取设置列表或启动时重试。目录内容、owner 或 inode/device 变化会保留文件并延后清理；用户需要处理这种外部变更，Runtime 不会猜测并删除新目录。
- 技能开关是独立偏好。插件关闭时，其技能仍可查看和配置，但不会进入新 Run 的可用技能目录。详情会提示所属插件尚未启用。
- 详情暂不加载技能图标资源，也不开放 Markdown 内的本地资源跳转。图标使用名称首字符；用户可通过 Finder 查看完整技能目录。


## 权限模式的验证范围

- 权限模式已通过组件单元与行为测试、协议校验和数据迁移测试。真实模型审查决策质量仍依赖于所配置模型的审查推理能力；静态检查不能证明所有外部第三方模型的审查决策稳定性。
- 自动审批复用当前 Run 的模型和 Provider，没有单独的审查模型设置。模型可能误判；确定性硬拒绝继续生效，但模型审查不能保证识别所有风险或提示注入。
- 自动审批只检查原本需要 Approval 的动作。默认允许的 Workspace 操作不会额外审查。审查证据只包括最近八条用户消息及当前动作事实；证据不足时策略要求拒绝。超限、超时、错误和重启中断不会转交人工，也不会自动重试同一请求。
- 完全访问会失去 Eidos 自身路径的沙盒保护。用户可以通过 Shell 改动 Eidos 数据、系统 Skill 或 Git metadata。macOS 的系统权限仍然有效，内置文件工具的普通文件约束仍然有效。
- 当前模式作用于单个 Run。用户不能在 Run 执行中切换模式。重新生成完全访问 Run 的回答会回到人工模式；用户可以在 Composer 重新选择完全访问并确认。
- 本期不实现自定义审批规则、`config.toml` 权限解析、域名代理或持久 allowlist。审批元数据保存可取得的审查 Token Usage，当前 UI 不提供独立的审查费用汇总。
