# Eidos 当前能力

本文只回答“当前 main 已经能做什么”。每项能力都对应生产代码和测试入口。能力存在但没有进入默认 Run 的部分会明确标注。

## Desktop

- Eidos 提供 macOS Electron Desktop。
- Renderer、Preload 和 Main 之间使用 context-isolated typed IPC。
- Main 可以启动、健康检查、通知和关闭 Python Runtime。
- Desktop 可以选择 Workspace，创建、列出、读取、重命名和删除 Session。
- Execution Feed 可以展示用户消息、模型文本、ToolCall、Tool Result、Approval、终态和恢复后的历史。
- Composer 可以选择已配置 Model，并显示当前选中 Model 最近 Run 的 Context Usage。
- Desktop 支持上下文使用率的 Provider 来源和 estimated 来源展示。没有快照时显示无数据状态。
- Quit 流程会先处理活动 Run，再关闭 Runtime 和窗口资源。

## Session / Run

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
- Repository Intelligence 的不完整 generation 不会替换上一个完整 generation。Watcher 只提供失效信号，不改变活动 Snapshot。
- RepositoryApplication、ContextApplication 和相关 persistence repositories 已提供 typed composition boundary。它们还没有全部成为 RuntimeEngine 默认 online Run 的强制路径。

## Runtime Git Worktree Kernel

- Runtime 可以从 verified Git repository discovery Project，并保存 canonical repository root 和 Git common directory。
- Runtime 可以创建、打开、验证、列出、恢复、清理和删除 managed Worktree。Worktree 使用 Runtime-controlled root 和 Runtime-generated branch。
- Runtime 可以实时查询 Worktree 的 HEAD、branch、dirty、staged、unstaged、untracked 和 conflict 状态。
- Runtime 可以返回 HEAD diff 和基于创建时 immutable `base_commit` 的 baseline diff。Diff 有界并返回 truncation metadata。
- SQLite v15 保存 Project、Worktree ownership 和 lifecycle state。Migration tests 覆盖 v14 → v15。
- Git lifecycle 不经过 Model Tool。当前能力只属于 Runtime infrastructure，Session binding 和 Desktop Project UI 尚未接入。

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

- 当前 SQLite schema version 是 15。
- 新数据库直接创建 v15。已有 v11、v12、v13 或 v14 数据库可以逐步迁移到 v15。v10 及更早版本不在当前启动迁移窗口。
- SQLite 保存 Session、Run、Item、ToolCall、Approval、Step、Model Attempt、Execution Segment、Durable Intent、Event、Outbox、Async Operation、Extension、Context、Repository Snapshot、Compaction、Checkpoint、Response Feedback、Run Revision、Project 和 Worktree。
- 业务事实变化与 Event/Outbox 在同一 transaction 中提交。
- SQLite 使用私有数据目录、WAL、busy timeout、完整性检查、单实例锁和 health-only 失败状态。

## Recovery

- Runtime 重启时会从 SQLite 收敛未完成 Run、ToolCall、Approval、Outbox、Long Task 和资源状态。
- Runtime 支持 cancel、pause、resume 和 restart verification 的 typed boundary。
- Resume 前会检查 Workspace identity、规则、Repository/Context snapshot、permission snapshot、Git 和 side-effect reconciliation 字段。
- Cancel、Tool timeout、Shell cleanup、MCP shutdown 和 Runtime shutdown 都有资源跟踪和有界等待。
- 不确定副作用不会自动重放。需要核验的事实会进入 reconciliation。

## Checkpoint

- Runtime 提供 checkpoint create/list 和 rewind/fork action lineage 的 typed RPC。
- Checkpoint 记录 Rule Snapshot、Repository Snapshot、Context Snapshot、Compaction Summary、Workspace identity、Git、permission、Model snapshot 和 reconciliation 状态引用。
- Checkpoint action 以 append-only lineage 保存 source Run、target Run 和 action kind。完整 rewind/fork Context 重建和 Git Worktree 隔离仍属于限制，而不是已完成闭环。

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
- GitProcess 对固定 Git argv 使用显式 cwd、shell=False、timeout、进程组终止和有界 stdout/stderr。Git diff 也有独立大小上限。
- `pnpm test` 覆盖 Runtime、contracts、Renderer state、Main 和 Renderer behavior。
- `pnpm check:python` 覆盖 Ruff、deptry、Runtime tests 和 Python dependency audit。
- Seatbelt native、Electron startup/shutdown、bundled Runtime、packaged App 和 packaging config 都有独立测试入口。
- Repository、Project Rules、Context、LoopGuard、response actions、schema migration、checkpoint、long task、MCP 和 telemetry 都有 focused test files。

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
- `runtime/eidos_runtime/db/schema.py`
- `runtime/eidos_runtime/persistence/`
