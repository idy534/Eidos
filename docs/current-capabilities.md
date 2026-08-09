# Eidos 当前能力

本文只列出当前代码已经实现并有测试入口的能力。

## Desktop 与任务

- macOS Electron Desktop，Renderer/Main 隔离，typed preload IPC。
- 创建、分页列出、读取、重命名和删除 Session。
- 创建、排队、执行和取消 Run；持久 FIFO 与全局单执行 slot。
- 展示模型流式文本、ToolCall、审批、终态和恢复后的历史快照。
- 正常退出时取消活动任务、关闭 Runtime，并在有界等待后收敛资源。

## 模型与循环

- 本地多模型配置：DeepSeek V4 Pro/Flash、MiniMax M3、Kimi K3、Kimi K2.7 Code；配置持久化到 `~/.eidos/models.json`。
- OpenAI-compatible Chat Completions/SSE，通过 Pydantic AI Direct Model API 接入。
- 同步 Runtime 共享一个由 `RuntimeServer` 管理的 AnyIO `BlockingPortal` 执行模型异步 I/O；Model Client 不拥有 Event Loop 或线程。
- Session 选择器只展示已配置模型；新 Session 选择第一项，历史 Session 恢复最近 Run 模型，并支持 Turn 之间切换。
- 每个 Run 固化实际模型配置与扩展快照；活动 Run 不受模型配置后续编辑或删除影响。保留模型尝试记录、usage 和有限重试。
- 每个 Step 确定性解析并固化分层系统指令：System Safety、Base Agent、Runtime Policy、带来源的 Project Rules 和当前 Turn 的 Selected Skills；消息、工具与输出预算和最终 instructions 一起计入 Context Budget。
- Project Rules 与 Selected Skill 内容只进入声明的指令层，不在普通 user 消息中重复；Skill Catalog、历史、Tool Result、文件内容和元数据继续作为普通上下文数据。Step 快照保存实际发送的完整 instructions 与 UTF-8 SHA-256。
- Finalization 复用当前解析结果并临时追加无工具的 `finalization-policy`；标题生成使用隔离的标题系统指令、当前用户请求和空 Tool Definitions。
- 上下文构建、压缩、Run/Segment 预算、协议错误反馈、Loop Guard 和最终化；Context
  Usage 优先使用最近 Provider usage 的 Active Context，缺失时才使用标记为
  `estimated` 的有界 fallback，并区分模型窗口与投影安全上限。
- Composer 在模型选择器右侧显示当前模型最近 Run 的 Context Usage；Provider 数据显示
  `上下文 xx% · xxK / xxxK`，估算数据明确显示 `≈`，无可用快照显示 `上下文 --`。
  该展示通过 `context/usage` Runtime RPC 进入 Main 和 typed preload，不使用累计 Session
  Token Consumption。
- `parallel_tool_calls=true` 允许模型声明多调用；Runtime 只并发安全只读批次并保持声明顺序。

## 内置工具

- 安全只读：`list_files`、`read_file`、`read_file_range`、`search_text`。
- `list_files` 与 `search_text` 使用 Workspace 根目录的 `.gitignore`，再使用
  `.eidosignore` 过滤普通发现结果；后者可覆盖前者的普通忽略规则。
- `search_text` 使用 Eidos 随 Runtime 管理并校验 SHA256 的 Ripgrep 15.2.0
  macOS arm64 二进制；通过固定 argv、`shell=False`、最小环境和 JSON 事件协议执行，
  不读取用户 `PATH`、Ripgrep Config、嵌套或全局 Ignore，也不在运行时下载。
- `search_text` 当前仍只支持最大 512 UTF-8 bytes 的单行 Literal、ASCII
  case-insensitive 查询；单文件最大 256 KiB、preview 最大 300 字符、最多返回
  100 个 Match。超时、取消和结果上限都会终止并回收 Ripgrep 进程组。
- Workspace 变更：`write_file`、`apply_patch`、`delete_file`，均要求审批、版本复检和安全提交。`apply_patch` 使用 `unidiff` 解析结构与 metadata，但仍只接受单个已存在文件的严格 Unified Diff；Eidos 负责拒绝 Git 扩展、精确上下文校验和候选构建。
- Shell：`run_shell`，要求审批，默认经 macOS Seatbelt 执行并记录有界输出、进程终态和 Workspace 变化。
- 工具发现：`tool_search`。
- Skill 管理：`skill_create`、`skill_install`，使用专用 Eidos-state 路径和审批。

## 工具安全与结果

- Pydantic/JSON Schema 输入校验、闭合 ToolSpec、Step 固化 tool set/hash。
- ToolCall 单生命周期控制、deadline/cancel 仲裁、Durable Intent、结果验证和敏感信息扫描。
- Canonical ToolResult、模型投影与 UI 投影；副作用不确定时保留 `sideEffectsMayExist` 和 reconciliation。
- Workspace 路径/身份检查、敏感路径拒绝、原子文件提交和变更 manifest。
- 发现忽略规则不是权限：显式读写 ignored path 仍遵循既有 Workspace、安全内容与审批规则；
  Shell security scan 和副作用 evidence 不使用这些忽略规则。
- Ripgrep 的 argv 排除仅是搜索缩减；每个候选 Match 仍由 Eidos 对 Workspace-relative
  路径、C2 DiscoveryScope、硬目录、敏感名称、symlink、普通文件、大小、稳定性、
  binary 与严格 UTF-8 进行独立后置校验。
- Shell Seatbelt fail-closed、动态权限物化、显式审批和最多一次权限升级。

## 扩展

- 导入、启用、禁用和移除本地 Plugin v1。
- 内置、用户和 Plugin Skill Catalog；Run/Step 固化来源与内容 hash。
- stdio MCP Tools、显式 Server consent、`connector`/`workspace_read` 权限档案、逐次外部工具审批。
- MCP 和延迟工具进入统一 Registry、ToolSpec、ToolResult 与 provenance。
- 每个 MCP Connection 是进程级 `RuntimeAsyncKernel` 拥有的长生命周期 AnyIO Service；连接不再创建专用线程或 Event Loop，同一 `ClientSession` 的 Tool Call 与 Tool List Refresh 串行进入受控同步/异步边界。

## 持久化、事件与恢复

- SQLite schema v10，支持原子 v9 → v10 migration、私有数据目录、单实例状态锁、WAL、完整性检查和 health-only 失败模式。
- Session/Run/Item/ToolCall、审批、Segment/Step/Attempt、Durable Intent、事件/Outbox、异步操作和扩展状态持久化。
- 业务事件与 Outbox 原子提交，按数据库 event ID 投影通知；发送失败保留待投递事实。
- 启动时收敛未完成 Run、ToolCall、审批和资源状态，不自动重放可能产生副作用的操作。
- `ResourceRegistry` 跟踪 Run worker、唯一异步内核、Kernel-owned async task、模型 lease、工具、Shell、MCP、finalization 和异步请求；成功 shutdown 要求资源清空。Kernel-owned task 通过有界 handle 诊断记录 owner、task、状态、deadline 和稳定错误码；shutdown 不依赖线程名前缀或重复的全局 Tool 计数。
- Title Generation 与 Plugin Import 等 Managed Task 由 Kernel Task Handle 拥有；现有同步 target 通过 AnyIO worker thread bridge 执行并保留 cooperative cancellation Event，不再创建 Eidos 专用命名线程。
- 符合 `parallel_safe` policy 的只读 Tool Batch 由共享 Kernel 内的 AnyIO TaskGroup 协调；现有同步 Driver 通过有界 worker thread bridge 执行，结果、Item、Event 和 Context Fact 仍按模型声明顺序提交。

## Typed Runtime and Repository Intelligence

- Strict/frozen domain records and typed SQLite mappers for Session-adjacent
  Run, Item, Step, ToolCall, Approval and ModelAttempt reads; corrupted rows
  fail through explicit persistence diagnostics.
- JSON-RPC method lookup and request validation through a duplicate-safe typed
  Method Registry, while initialization/draining/reconfiguration gates remain
  explicit.
- Bounded Repository Inventory with UTF-8/binary classification, content
  hashes, generation IDs, generated/vendor flags and cancellable scans.
- Tree-sitter index snapshots for Python, TypeScript/TSX, JavaScript and Go,
  including bounded symbols, imports, references, chunks and parse diagnostics.
- Deterministic Repository Map discovery and hybrid FTS5/RapidFuzz retrieval;
  every selected evidence record exposes its score breakdown and reasons.
- Persisted generation-scoped FTS5 and typed symbol/definition/import/
  reference/path/test-source queries with bounded cancellation.
- Immutable rule, context-plan and per-model-attempt context snapshots with
  frozen model budget, repository generations and stale-evidence rejection.
- Typed compaction verification against persisted source Items and required
  reconciliation facts, without replacing SQLite business facts.
- Typed long-task progress/control facts with compare-and-set pause, resume,
  cancel and interruption transitions, plus Workspace/Git/rule/index/context/
  permission/side-effect resume verification.
- Typed `run/status`, `run/pause`, `run/resume` and checkpoint create/list/
  rewind/fork JSON-RPC boundaries; RuntimeEngine consumes pause intent at
  explicit model/tool/approval/slot safe points.
- Thin `RepositoryApplication` and `ContextApplication` boundaries compose the
  typed snapshot builders and context verification without creating a second
  scheduler or business-fact authority.
