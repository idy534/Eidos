# AGENTS.md
## 1. Scope and authority
本文件约束 Eidos 仓库中的编码、重构、测试和文档修改。
更深层目录存在更具体的 `AGENTS.md` 时，以更具体规则为准。
执行任务前必须阅读本文件、当前任务和相关 `docs/current-*`。
安装、启动、环境要求、测试命令和人工验收统一遵循 `DEVELOPMENT.md`。
规则优先级：System Safety > User Rules > 当前任务 > 本文件 > Workspace Rules > Path Rules > Skill Instructions > 当前代码和测试。
发现冲突时必须指出来源，不得静默选择低优先级规则。
当前代码和测试是实现事实；历史 Phase、PRD 和 TDD 只提供背景。
修改已实现行为时必须同步测试和 `docs/current-*`。
## 2. Product boundary
Eidos 是可独立安装的 macOS 桌面 Agent Runtime。
目标能力：多项目、多 Session、多编码线程、独立 Git Worktree、多 Provider、多 Model Profile、代码理解、搜索、修改、测试、完整 Diff、Checkpoint、Rewind、Fork、Sandbox、权限审查、长任务恢复、Skill、Plugin、MCP、有限并行 Agent、日志、诊断、备份、迁移和升级。
优先完成可靠的产品闭环，不为假想需求提前构建复杂抽象。
不要以理论上的绝对安全阻断主要流程；已建立的安全边界必须 fail closed。
单次 PR 范围必须小、单一、可测试、可回滚、可完整审查。
## 3. Overall architecture
```text
Electron Renderer
    │ typed IPC
    ▼
Electron Main
    │ JSON-RPC 2.0 over stdio / JSONL
    ▼
Python RuntimeServer
    ├── Application Services
    ├── Durable Runtime Core
    ├── Model Gateway
    ├── Tool Runtime
    ├── Sandbox
    ├── Extensions
    ├── Context
    └── SQLite
```
```text
Desktop        Electron + React + TypeScript
Main/Runtime   JSON-RPC 2.0 over stdio
Runtime        Python 3.11/3.12 + Pydantic v2
Async target   AnyIO
Persistence    SQLite
Sandbox        /usr/bin/sandbox-exec + Seatbelt
Package        pnpm + uv
```
Renderer 只能通过 context-isolated preload 暴露的 typed IPC 访问 Main。
Renderer 不得直接访问 Python Runtime 或 Node 系统能力。
Main 负责 Desktop 生命周期、Runtime 子进程和协议边界。
Runtime stdout 只能输出协议；日志写 stderr 或受控本地日志文件。
未经明确架构决策，不得把本地控制面改成 HTTP、WebSocket 或远程服务。
## 4. Startup chain
启动、安装和验证细节只维护在 `DEVELOPMENT.md`。
基础入口：
```bash
pnpm install
uv sync --locked
pnpm start
pnpm check:python
pnpm test
pnpm build
pnpm test:seatbelt-native
pnpm test:electron-smoke
```
不得重新引入 `pip install -r runtime/requirements.txt`。
修改 Python 生产依赖必须同时更新 `pyproject.toml` 和 `uv.lock`。
启动链路、环境变量或进程模型变化时必须同步 `DEVELOPMENT.md`。
## 5. Current control flow
```text
Renderer
  → preload typed IPC
  → Electron Main
  → RuntimeClient
  → Python RuntimeServer
  → Application Method
  → RunSupervisor
  → RuntimeEngine
  → SamplingRuntime / Model Client
  → ToolCallRuntime
  → ToolExecutionController
  → Tool Handler
  → ToolOrchestrator
  → Seatbelt / Process / Filesystem
  → SQLite transaction
  → Event + Outbox
  → JSON-RPC notification
  → Main
  → Renderer Feed
```
修改链路中的一个节点时必须检查上下游契约。
不得在任意中间层增加第二个状态权威。
不得绕过 RuntimeServer 直接向 Renderer 推送业务事实。
## 6. Target Runtime structure
逐步向以下结构收敛：
```text
runtime/eidos_runtime/
├── application/       # sessions, runs, models, extensions
├── domain/            # session, run, step, tool, approval, reconciliation, checkpoint
├── orchestration/     # coordinator, decisions, supervisor, finalization
├── infrastructure/    # async_kernel, model_driver, mcp_driver, process_driver, search_driver
├── persistence/       # database, repositories, recovery, outbox
├── protocol/          # registry, methods, schemas, projector
├── context/
├── sandbox/
├── tools/
├── extensions/
└── repo_intelligence/
```
这是目标分层，不要求一次性移动全部文件。
禁止为了目录美观进行大规模、无行为收益的搬迁。
每次迁移必须有独立边界、行为等价测试和可审查 Diff。
## 7. Layer dependency rules
`domain`：只表达领域状态、约束和决策；不依赖 Electron、JSON-RPC、SQLite Row、HTTP、线程、进程或具体 Driver。
`application`：编排完整用例；调用领域规则和端口；不直接执行 SQL、拼 JSON-RPC Envelope 或编译 Seatbelt Policy。
`orchestration`：协调 Run、Step 和 ToolCall；保留 Eidos Loop 语义；不实现 Provider 协议和具体搜索算法。
`infrastructure`：实现 HTTP、异步、进程、MCP、搜索等机制；不决定审批、权限和状态转换；不成为业务事实权威。
`persistence`：实现 SQLite 事务、Repository、Recovery 和 Outbox；不承担模型循环和协议路由；返回明确领域模型。
`protocol`：负责 DTO、Envelope、Method Registry 和事件投影；不执行 Tool，不包含内部 SQL 和核心业务决策。
依赖方向必须从外层指向领域端口，不得由 Domain 反向依赖 Infrastructure。
## 8. Must remain Eidos-owned
以下属于 Eidos 产品语义，必须继续自研：
- Durable Agent Loop
- Runtime 状态机
- Run、Session、Step 生命周期
- Tool Orchestration
- Approval 与 Permission Decision
- Sandbox Policy 和权限物化
- Durable Intent 和 Reconciliation
- Transactional Outbox 和 Recovery
- Checkpoint、Rewind、Fork
- Context Projection 和 Compaction Policy
- Tool Result Projection
- Loop Guard
- 幂等、副作用和一致性语义
- Plugin 安装、授权、隔离和快照
- Skill Catalog、Activation、Resource 语义
禁止使用 Pydantic AI Agent、LangGraph、Temporal、Prefect、Celery、DBOS 或 `transitions` 接管 Runtime Core。
禁止使用 SQLAlchemy ORM 重塑当前状态和事务模型。
可以使用 Pydantic AI 的 Model API，但不得使用其 Agent Loop 替换 Eidos Loop。
## 9. Mature infrastructure first
除上述核心语义外，写代码前必须先判断是否已有成熟实现。
决策顺序：
1. Python 标准库
2. 当前已锁定依赖
3. Pydantic AI、官方 MCP SDK、SQLite 内建能力
4. 维护活跃、边界清晰的成熟依赖
5. 最后才允许自研通用基础设施
新增自研基础设施前必须说明：现有方案为何不适用、自研范围、维护成本和测试策略。
不得手写已有成熟实现的 HTTP Provider 协议、SSE Parser、JSON Schema 标准解释器、Git Ignore 匹配、文件监听、大仓文本搜索、AST Parser、模糊匹配、Unified Diff Parser、重试退避、通用 TaskGroup 或双份 TypeScript DTO。
## 10. Dependency policy
新增依赖必须满足：持续维护、License 可接受、Python 3.11/3.12 可用、macOS arm64 可打包、不要求外部常驻服务、不隐式联网、API 边界清晰、可被 Sandbox 和测试控制、可锁版本、可被 `pip-audit` 审计。
新增依赖必须：
- 修改 `pyproject.toml`
- 更新 `uv.lock`
- 添加最小集成测试
- 通过 `deptry`
- 通过 `pip-audit`
不得为了一个简单函数引入大型框架。
不得添加未使用的可选能力。
不得依赖用户预装的 Node、Python、ripgrep 或其他命令行工具。
需要 Sidecar Binary 时必须随应用固定版本打包、校验 Hash 并接受 Sandbox 控制。
## 11. Final module decisions
| 模块 | Eidos 保留 | 优先使用 |
|---|---|---|
| Model Gateway | Profile、Snapshot、错误码、安全重试判断 | Pydantic AI Model/Provider/Profile |
| HTTP Retry | 是否允许重试 | Pydantic AI Retry Transport、Tenacity |
| Async Runtime | Supervisor 和资源语义 | AnyIO TaskGroup、CancelScope |
| JSON Schema | Bounds、禁远程引用、错误码 | `jsonschema`、`referencing` |
| Ignore Rules | 安全例外和最终范围 | `pathspec` |
| Data Directories | Eidos 目录策略 | `platformdirs` |
| File Watch | 最终事实核验 | `watchfiles` |
| Text Search | 安全、限额、结果契约 | bundled `ripgrep` |
| Repo AST | 索引结构和排序 | `tree-sitter` |
| Full-text Search | Ranking 组合策略 | SQLite FTS5 |
| Fuzzy Search | 权重策略 | `RapidFuzz` |
| Encoding | 拒绝和降级策略 | `charset-normalizer` |
| Diff Parsing | 写入和版本校验 | `unidiff` |
| Diff Rendering | 审查语义 | `difflib` |
| MCP | 授权、快照、Tool Contract | 官方 Python MCP SDK |
| Protocol DTO | 产品协议 | Pydantic v2 |
| TypeScript DTO | 审核生成边界 | JSON Schema 生成 |
| Persistence | 事务、状态和恢复语义 | `sqlite3` |
| Logs | 字段和脱敏策略 | `logging`，必要时 `structlog` |
| Tests | 产品验收门槛 | pytest、Hypothesis、respx |
| Dependency | 版本和审计策略 | uv、deptry、pip-audit |
当表中存在成熟方案时，不得重新手写等价实现。
偏离该表必须在 PR 描述中明确说明理由、替代方案和维护成本。
## 12. Model Gateway rules
只保留 `ModelProfile`、`RunModelSnapshot`、配置版本、Base URL 安全校验、本地 API Key 存储、稳定错误码、能力声明、Model Lease 和安全重试 Predicate。
交给 Pydantic AI：Provider Client、OpenAI-Compatible 请求、Responses API、Chat Completions API、流式解析、ToolCall Chunk 合并、Usage、Provider Profile、HTTP Client 生命周期和 Structured Output。
不得恢复主动 Capability Probe。
不得恢复独立“模型连接测试”流程。
能力来源：用户配置 > Pydantic AI Model Profile > Eidos Provider Preset > 保守默认值。
模型错误必须映射为稳定 Eidos 错误码，不得直接跨协议发送第三方异常对象。
HTTP 重试的退避、Retry-After 和可重试状态交给成熟实现；Eidos 只决定当前 Attempt 是否还能安全重新开始。
## 13. AnyIO and concurrency
AnyIO 是目标异步运行内核。
优先使用 `TaskGroup`、`CancelScope`、`fail_after`、Memory Object Stream 和 Async Process。
不得新增每请求一个 Event Loop、每 MCP 连接一个专属线程、无上限 ThreadPool、无所有者后台线程、无 Cancel Scope 长任务或仅靠 `thread.join()` 的关闭协议。
RunSupervisor、ResourceRegistry、FIFO、Execution Slot 和 Shutdown Quiescence 语义必须保留。
迁移必须证明：
- Cancel 不会被迟到结果覆盖
- Approval 等待可以释放 slot
- Shutdown 能达到资源静止
- Runtime 重启不会重放不确定副作用
- 并行结果保持模型声明顺序
异步实现变化必须独立 PR，不得同时修改协议、DB Schema 和 UI。
## 14. Tool execution
单 ToolCall 固定流程：
```text
Validate → Prepare → Request Approval → Commit Durable Intent
→ Execute → Verify → Commit Result → Reconcile when uncertain
```
不得绕过任何已存在阶段。
文件写入必须在 Workspace 内，有 Read Evidence 和 Base Hash，展示完整 Diff，审批前零修改，审批后重新验证版本，原子写入，验证最终内容，并在事务内提交 ToolResult 与 Event。
Shell 必须每次受控执行、默认禁网、不继承敏感环境变量、使用明确 cwd 和 timeout、有界输出、终止完整进程组、记录有效权限，并经过 Seatbelt 或明确扩权路径。
普通只读工具仅在完整批次满足 `parallel_safe` 时并行。
写入、Shell、MCP 和外部副作用工具默认独占。
副作用结果不确定时必须进入 Reconciliation，不得猜测成功或失败。
## 15. Sandbox
以下继续自研并保持 fail closed：Seatbelt Policy Compiler、Base/Additional/Effective Permission、永久拒绝路径、`.git` 保护、Eidos 数据目录保护、Runtime 代码目录保护、Workspace 根校验、inode/device/owner 校验、`O_NOFOLLOW`、fd-relative 访问、特殊文件检查、多硬链接检查、原子替换和 Sandbox Denial 分类。
不得用 `.gitignore` 跳过安全扫描。
不得把 `watchfiles` 事件当作安全事实。
不得因为开发便利默认 unsandboxed。
扩权必须可审查、可记录、可撤销；扩权后必须重新物化并核验有效权限。
## 16. SQLite and persistence
SQLite 是唯一业务事实来源。
内存对象只允许保存当前协调状态、缓存、活跃资源引用和诊断信息。
Session、Run、Item、ToolCall、Approval、Execution Segment、Step、Model Attempt、Durable Intent、Event、Outbox、Async Operation、Extension Snapshot 和 Checkpoint 必须持久化。
业务状态变化与 Event/Outbox 必须在同一事务提交。
不得引入第二套持久状态机。
不得全面迁移到 SQLAlchemy ORM；优先保留 Raw SQL、条件更新和明确事务。
Repository 应逐步返回 Pydantic Domain Model，不返回无约束裸字典。
Schema 变更必须有版本、Migration 或明确的开发期基线决策，并覆盖升级、失败回滚和 Recovery 测试。
## 17. Data structure index
| 边界 | 当前入口 |
|---|---|
| Desktop 共享契约 | `desktop/shared/domain-contracts.ts` |
| Main Runtime Client | `desktop/main/runtime-client.ts` |
| Python 协议 DTO | `runtime/eidos_runtime/protocol/schemas.py` |
| JSON-RPC Server | `runtime/eidos_runtime/protocol/server.py` |
| SQLite Schema | `runtime/eidos_runtime/db/schema.py` |
| Database/事务 | `runtime/eidos_runtime/db/database.py` |
| Repository | `runtime/eidos_runtime/db/repositories/` |
| Storage Facade | `runtime/eidos_runtime/db/storage.py` |
| Event/Outbox | `runtime/eidos_runtime/runtime/events.py`、`event_delivery.py`、`event_projector.py` |
| Runtime Loop | `runtime/eidos_runtime/runtime/engine.py`、`sampling.py`、`loop_guard.py` |
| Supervisor/资源 | `runtime/eidos_runtime/runtime/supervisor.py`、`resource_registry.py`、`state_machine.py` |
| Tool 执行 | `runtime/eidos_runtime/runtime/tool_runtime.py`、`tool_execution.py`、`tool_orchestrator.py` |
| Tool Contract | `runtime/eidos_runtime/tools/` |
| Context | `runtime/eidos_runtime/context/` |
| Sandbox | `runtime/eidos_runtime/sandbox/` |
| Model | `runtime/eidos_runtime/model/`、`model_gateway/` |
| Extensions | `runtime/eidos_runtime/extensions/` |
目标 DTO 按 `session`、`run`、`tool`、`model`、`extensions` 拆分到 `protocol/schemas/`。
`storage.py` 只保留必要兼容入口，逐步减少机械转发。
修改结构前必须确认调用者、测试、协议和持久化影响。
## 18. Pydantic and protocol
Pydantic v2 是协议和领域模型基础。
所有新模型使用统一基类：`extra="forbid"`、统一 alias、稳定 JSON 序列化、UTC 时间、JSON-safe integer、无隐式可变默认值、必要时 frozen，并明确区分 Domain Model 与 DTO。
禁止大量重复 `Field(alias="someCamelCase")`，优先统一 `alias_generator`。
ToolResult、Event 和 Operation 优先使用 Discriminated Union。
不得新增关键链路中的 `dict[str, object]` 或无边界 `Any`。
DB Row 转 Domain Model 必须集中在 Mapper 或 Repository。
JSON-RPC 2.0 over stdio 是固定控制通道，必须保持有界单行 JSON、严格 Envelope、稳定错误码、非协议 stdout fail closed、慢消费者有界和双端 Fixture 一致。
新方法优先注册到 Method Registry，不得继续扩展大型 `if/elif` 路由。
协议层只验证输入和映射输出，不执行 Tool 和内部 SQL。
Python DTO 和 TypeScript DTO 应由单一 Schema 源生成或校验。
协议变化必须更新 Fixture、Python/TypeScript 测试和 `docs/current-*`。
## 19. JSON Schema and Diff
Tool 参数标准校验使用 `jsonschema` 和 `referencing`。
Eidos 只保留 Schema 大小、值大小、深度、节点数、关键字范围、禁用远程 `$ref`、无网络解析和稳定错误码。
不得继续扩展手写 JSON Schema 解释器。
Unified Diff 解析优先使用 `unidiff`，展示可继续使用 `difflib`。
第三方库只负责解析；Read Evidence、Base Hash、Approval、权限、原子写入、版本冲突和最终验证仍由 Eidos 负责。
## 20. MCP, Plugin and Skill
MCP Transport 和 Session 使用官方 Python MCP SDK。
Eidos 继续负责 Server 配置、授权、进程限制、Tool Catalog 映射、Tool Result Contract、Extension Snapshot、生命周期、恢复和 Sandbox。
Plugin 不得直接 import 到 Runtime 主进程。
`pluggy` 或 Entry Point 只允许用于随应用发布的可信内部组件。
用户安装 Plugin 必须保持进程外或等价隔离边界。
Skill 继续采用 Catalog、Activation、Resource 三层。
不得把全部 Skill 内容无条件注入每个 Turn。
Catalog Snapshot 必须在 Turn 开始时固定，运行中不得被文件监听事件静默改变。
## 21. Workspace and Repo Intelligence
优先使用：
```text
pathspec             文件范围和 ignore
bundled ripgrep      即时文本搜索
tree-sitter          AST 和 Symbol Index
SQLite FTS5          全文检索
RapidFuzz            文件名和 Symbol 模糊匹配
charset-normalizer   编码识别
watchfiles           缓存失效和增量索引
unidiff              Unified Diff 解析
```
首批 Tree-sitter 语言只做 Python、TypeScript、TSX 和 Go。
不要一次性引入所有语言 Grammar。
Repository Ranking 由 Eidos 自研，至少组合 Symbol 精确匹配、FTS5 BM25、文件名模糊匹配、Import/Reference 权重、当前 Diff、最近修改、路径规则和 Context Budget。
索引和 Watch Event 只用于检索优化，不是文件安全和一致性的最终事实源。
## 22. Context
Context Builder 必须从持久事实构建，不得依赖模型反复 `read_file` 作为主要仓库理解方式。
Token Budget 必须逐步迁移为 Provider-aware 估算；允许启发式 fallback，但必须标记来源。
Compaction 必须保留当前目标、确认事实、未完成事项、Approval 状态、Tool 副作用、重要文件、Symbol、Source Item IDs 和验证结果。
模型辅助压缩必须使用 Structured Output，并保留 deterministic fallback。
压缩失败不得损坏或覆盖原始持久事实。
## 23. Logging and diagnostics
日志上下文尽量包含 `workspace_id`、`session_id`、`run_id`、`step_id`、`tool_call_id`、`operation_id`、`provider`、`model_id`、`process_id` 和 `task_name`。
日志不得包含 API Key、Authorization Header、凭证、完整敏感文件或未脱敏环境变量。
优先使用标准库 `logging`；只有明确减少样板时才引入 `structlog`。
OpenTelemetry 和 Sentry 必须作为后续独立 PR，不得混入核心重构。
默认诊断必须本地可用，不依赖远程遥测。
## 24. PR scope
单次 PR 必须只解决一个核心问题，边界明确，可独立测试、回滚和审查，不混入无关格式化、顺手重命名或跨层清理。
推荐范围：一个依赖替换、一个 Repository 领域、一个 RPC 领域、一个异步资源类型、一个 Tool Driver 或一个 Context 能力。
禁止一次 PR 同时包含目录重组、Async 迁移、DB Schema、Protocol 变更和 UI 修改。
必须先兼容迁移，再删除旧实现。
删除旧代码前必须有行为等价测试。
不得以“重构”为名改变未声明的产品行为。
## 25. Execution workflow
开始编码前：
1. 读取任务、本文件和相关 `docs/current-*`
2. 定位当前代码、测试、协议和数据影响
3. 画出最小调用链
4. 判断核心自研还是通用基建
5. 搜索并评估成熟依赖
6. 确定最小 PR 边界和验收条件
7. 再开始修改
实现中：
1. 先添加或调整测试
2. 保持旧行为可验证
3. 引入薄 Adapter
4. 迁移一个调用路径
5. 删除被替代实现
6. 更新当前架构文档
7. 运行定向和完整验证
完成后必须报告修改范围、Diff 规模、保留语义、采用依赖、删除机制、兼容性、数据影响、未完成事项和测试结果。
## 26. Test gates
修改 Python Runtime：
```bash
pnpm lint:python
pnpm test:runtime
pnpm deps:python
```
修改协议：
```bash
pnpm test:contracts
pnpm test:main
pnpm test:runtime
```
修改 Desktop：
```bash
pnpm test:desktop
pnpm build
```
修改 Sandbox 或 Shell：
```bash
pnpm test:seatbelt-native
```
修改启动或进程生命周期：
```bash
pnpm test:electron-smoke
```
合入前完整门槛：
```bash
pnpm check:python
pnpm test
pnpm build
pnpm test:seatbelt-native
pnpm test:electron-smoke
git diff --check
```
测试无法执行时必须说明具体原因，不得把跳过写成“通过”。
## 27. Reliability cases
涉及对应模块时必须覆盖：
- Runtime 重启
- Cancel 与迟到结果竞争
- Approval 等待和恢复
- 文件版本冲突
- Tool Timeout 和 Shell 进程组清理
- 模型流中断、Retry-After、ToolCall 跨 Chunk 合并
- 非协议 stdout
- SQLite Busy、事务失败和 Outbox 重投
- Durable Intent 未完成和不确定副作用
- MCP 进程异常退出
- Shutdown 资源静止
- 并行 Tool 有序汇总
- Sandbox Denial 和 Workspace 路径替换攻击
不要只测试 Happy Path。
## 28. Forbidden
禁止：
- 用大框架替代 Eidos Runtime Core
- 创建第二套状态权威
- 绕过 SQLite、Approval、Sandbox 或 Tool Verification
- 在协议 stdout 写日志
- 把 API Key 写入 SQLite 或日志
- 使用用户全局 Python 环境
- 依赖用户预装的 ripgrep
- 无界读取文件、输出、线程或 Task
- 远程解析 JSON Schema `$ref`
- 动态 import 用户 Plugin
- 一个 PR 混合多阶段重构
- 为减少行数删除必要领域语义
- 为假想需求提前构建复杂抽象
- 未经测试修改稳定错误码
- 未经迁移修改 SQLite Schema
- 仅修改文档声称能力完成
## 29. Success criteria
重构不是简单移动文件。
成功必须同时满足：行为不退化、核心语义仍由 Eidos 控制、通用机制由成熟依赖承担、重复代码减少、线程和取消模型更清晰、类型边界更严格、状态权威更单一、测试与诊断更容易、模块职责更集中、后续 PR 更小、macOS 独立安装能力不受影响。
方向指标：
```text
同等功能 Runtime 生产代码净减少约 15%～25%
通用基础设施自研代码减少 60% 以上
关键链路裸 dict 减少 70% 以上
手写 Provider/Wire 协议减少 80% 以上
每连接或每请求专属线程基本清除
```
不得为了数字牺牲可靠性。
## 30. Final checklist
- [ ] PR 只有一个明确主题
- [ ] 已区分核心自研和通用基建
- [ ] 已优先评估成熟依赖
- [ ] 没有引入第二套 Runtime 或状态权威
- [ ] 没有绕过 Approval 和 Sandbox
- [ ] 新依赖已进入 `pyproject.toml`
- [ ] `uv.lock` 更新是有意的
- [ ] `deptry` 和 `pip-audit` 已执行
- [ ] Pydantic 模型没有无边界 `Any`
- [ ] 协议变化同步了 Fixture
- [ ] DB 变化同步了 Schema 和测试
- [ ] Runtime 变化覆盖取消和恢复
- [ ] Tool 变化覆盖失败和副作用
- [ ] 日志未包含秘密
- [ ] `docs/current-*` 已同步
- [ ] `DEVELOPMENT.md` 启动链路仍正确
- [ ] 定向测试通过
- [ ] 完整测试通过或说明阻塞
- [ ] `git diff --check` 通过
- [ ] Diff 可由维护者完整审查
不满足时不得宣称任务完成。
