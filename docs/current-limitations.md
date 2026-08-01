# Eidos 当前限制

本文只描述当前代码边界，不列未来路线图。

- 仅支持 macOS Desktop。Shell 隔离依赖可用的原生 `/usr/bin/sandbox-exec` 与随包策略资源；Self-Test 失败时 Shell 能力 fail-closed。
- 模型 Provider 固定为 DeepSeek，wire API 固定为 Chat Completions；没有通用 Model Profile 编辑器或 Responses API。
- 全局同一时间只执行一个 Run。单个模型响应内只有安全只读工具可并发；Workspace 写入、Shell、Eidos-state 和 MCP/外部工具不得并发。
- Eidos 1.0 不追求零线程架构：Durable Runtime core 与 SQLite 仍保持同步，每个活跃 Run 一个 Worker Thread 是刻意的隔离边界。异步网络 I/O、MCP、Managed Task 和并行只读 Batch 统一由唯一进程级 AnyIO Kernel 管理；blocking callback 不得在 Kernel Event Loop 上运行。
- 原生 async `RuntimeEngine`/`RunSupervisor` 不是 Eidos 1.0 路线图。只有 Run-thread scalability 成为实测瓶颈、并行 Agent 数超过有界线程模型、SQLite 被替换为 async persistence boundary，或 profiling 证明显著 Event Loop/线程竞争时，才重新评估该转换。
- 内置文件工具只处理当前 Workspace 内受支持的普通 UTF-8 文件；没有通用二进制编辑、内嵌 Terminal、浏览器自动化或 Artifact 发布工具。
- Repository discovery 目前只读取 Workspace 根目录的 `.gitignore` 和 `.eidosignore`；
  不支持嵌套 `.gitignore`。这些规则只控制 `list_files` / `search_text` 的普通展示，
  不是文件权限，也不会缩小安全扫描或副作用证据范围。
- `search_text` 只支持 Literal、ASCII case-insensitive 搜索，没有 Regex、Glob、
  文件类型筛选、分页或 Repo Intelligence。结果上限为 100，preview 上限为 300 字符，
  单文件上限为 256 KiB；`scannedBytes` 仅统计通过 Eidos 后置策略且产生 Match 的文件。
- 当前只提交 Ripgrep 15.2.0 的 macOS arm64 受管资源。最终应用打包必须把
  `runtime/eidos_runtime/resources/bin/ripgrep/` 原样放入 Python Runtime 资源树并保留
  `darwin-arm64/rg` 的可执行位；当前 PR 不扩展 Electron Packager，也不支持 macOS x64、
  Linux 或 Windows artifact。资源缺失或校验失败时 `search_text` 明确失败，不使用 PATH
  或 Python 搜索 fallback。
- Plugin 只支持本地受管包；MCP 只支持 stdio Tools。没有远程市场、OAuth、Streamable HTTP、Resources、Prompts、Sampling 或 Tasks。
- MCP startup 受单一、有界的 readiness deadline 约束；ready 后 Connection 是无 deadline 的长生命周期 Service。MCP Tool List Changed 只做本地、串行的 SQLite bookkeeping，可能在关闭时等待一个已经开始的 callback 完成；callback 失败不会终止已建立的 MCP session。
- Runtime 不恢复内存中的模型请求、进程或 ToolCall。重启从 SQLite 事实收敛，可能有副作用的未确认执行要求 reconciliation，不自动重放。
- 数据库只接受当前 schema v9 或全新数据库；当前没有通用历史 Migration 框架。
- Phase E-F 的 Repository Inventory、Tree-sitter Index、Repository Map、
  Retrieval、ContextPlan 和 LongTaskRepository 已有严格 typed seam 与 focused
  tests，但尚未全部成为 RuntimeEngine 的默认在线路径。当前 retrieval 的
  FTS5 表是进程内派生索引，重启后从完整 Inventory/Index 重建；Inventory/Index
  快照尚未单独持久化为新的 SQLite 表。
- `LongTaskRepository` 与 `ResumeVerifier` 已持久化控制意图、进度和校验结果，
  但 Desktop 尚未暴露 `run/pause`、`run/resume` RPC，RunSupervisor 也尚未消费
  这些控制事实。现有 `run/cancel` 生命周期保持原实现；暂停期间的资源释放、
  Checkpoint/Rewind/Fork 联动和启动时完整 Restart Verification 仍待接入。
- `ContextCompactionVerifier` 是可复用的验证边界；兼容性的
  `ContextCompactor` 仍写入现有 `compact_summaries` 结构，因此 Event、ToolCall
  和 Repository Evidence provenance 尚未作为完整持久字段保存。
- `application/` 已建立 Session、Run 和 TaskLifecycle 的最小边界，但部分
  RuntimeServer handler 仍通过 `SessionStore` 兼容入口执行，尚未完成所有顶层
  use case 的迁移。
- `Run.runtimeState` 是可选跨语言契约字段，不是持久恢复权威；当前稳定权威是 `Run.status` 加 SQLite 中的审批、Step、ToolCall 和 reconciliation 事实。
- 原生 Seatbelt、进程组和 Electron 启动验证需要真实 macOS 执行环境；受限嵌套沙箱不能替代该证据。
