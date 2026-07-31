# Eidos 当前架构

本文只描述当前代码。目标态和历史 Phase 文档不构成当前实现依据。

## 进程与控制面

```mermaid
flowchart LR
    Renderer["Electron Renderer"] -->|typed IPC| Main["Electron Main"]
    Main -->|JSON-RPC 2.0 over stdio/JSONL| Server["Python RuntimeServer"]
    Server --> Store["SQLite SessionStore"]
    Server --> Supervisor["RunSupervisor"]
    Supervisor --> Engine["RuntimeEngine"]
    Engine --> Model["SamplingRuntime / DeepSeek"]
    Engine --> Batch["ToolCallRuntime"]
    Batch --> Single["ToolExecutionController"]
    Single --> Handlers["Tool handlers / Registry runtimes"]
    Handlers --> Sandbox["ToolOrchestrator / Seatbelt"]
```

Renderer 只通过 context-isolated preload 暴露的 typed IPC 访问 Main。Main 启动 Python sidecar、校验 JSON-RPC 响应和通知，并负责 Desktop 生命周期。Runtime stdout 只输出协议，日志写 stderr；本地不开放 HTTP、WebSocket 或其他控制端口。

## 状态与恢复权威

- SQLite schema v7 保存 Session、Run、Item、ToolCall、审批、执行段、Step、模型尝试、Durable Intent、事件、Outbox、异步操作和扩展快照。
- SQLite 是唯一业务事实来源。`RunSupervisor` 的 worker/slot、`ResourceRegistry` 和 `RuntimePhaseTracker` 只保存运行中协调或诊断状态。
- `Run.status` 是持久状态权威。`Run.runtimeState` 是可选传输提示；当前 DB mapper 不依赖它恢复执行。
- 业务变更和 Event/Outbox 在同一提交中落库；通知从已提交事件投影。启动恢复不会重放不确定副作用。
- Runtime 只允许一个 Run 占用全局执行 slot；等待审批时可以释放 slot，恢复后重新进入 FIFO。

## Runtime 与 Tool 职责

| 组件 | 当前职责 | 不负责 |
|---|---|---|
| `RuntimeEngine` | 单个 Run 的模型/工具循环协调、预算决策、终止与错误收敛 | 具体工具实现、单 ToolCall 生命周期、沙箱策略 |
| `ToolCallRuntime` | 一个 Step 的 ToolCall 批次校验、创建顺序、并发选择和有序汇总 | Durable Intent/终态提交、权限升级 |
| `ToolExecutionController` | 单个 ToolCall 的 prepare/execute/verify、deadline、取消、Durable Intent、结果校验/投影、终态与 reconciliation | 模型循环、批次调度、Seatbelt 策略 |
| `ToolOrchestrator` | Shell attempt 的有效权限物化、审批要求、Seatbelt/unsandboxed attempt 选择和一次权限升级 | ToolCall DB 生命周期、批次、进程监督实现 |

Workspace discovery is a separate presentation boundary. `list_files` and
`search_text` load root `.gitignore` followed by root `.eidosignore` through
the Workspace descriptor and use PathSpec only to filter ordinary discovery
results. The later `.eidosignore` rules may refine ordinary Git-ignore
matches; Eidos-owned hard discovery directories remain non-overridable.
Ignore rules are not permissions: explicit file operations retain their
existing Workspace and sensitive-content checks, while WorkspaceIndex shell
preflight and side-effect manifests continue their independent security and
evidence traversals.

`search_text` delegates text matching to the synchronous
`RipgrepSearchDriver`. The production resolver accepts only the pinned
Ripgrep 15.2.0 macOS arm64 resource at
`runtime/eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg`, verifies its
manifest identity, owner, mode and SHA256, and never searches `PATH` or
downloads a binary. The driver launches a fixed argv with `shell=False`, a
minimal environment, `--no-config`, fixed-string matching and ASCII-only
case folding. Ripgrep ignore sources are disabled so nested/global/user ignore
files cannot change C2 semantics; the already-loaded `WorkspaceDiscoveryScope`
and shared Eidos discovery policy filter every returned path. Hard and
sensitive directories are also excluded in argv as defense in depth, but argv
globs and ignore rules are not treated as security authorization.

Ripgrep stdout, stderr, JSON line size, event count, preview, file size and
result count are bounded. Deadline, cancellation and the 100-result limit
terminate and reap the dedicated process group. `scannedBytes` is the sum of
Ripgrep `end.stats.bytes_searched` for accepted files that produced a match,
falling back to the stable validated file size only when the result limit ends
the process before its `end` event; ignored and sensitive paths do not enter
the metric. Missing, invalid or malformed backends fail explicitly and never
fall back to the former Python traversal.

代码中有两个模块级 `ToolRuntime` Protocol：

- `eidos_runtime.tools.registry.ToolRuntime` 是 Registry 工具的 prepare/execute/verify/invoke 契约。
- `eidos_runtime.runtime.tool_orchestrator.ToolRuntime` 是 `ToolOrchestrator` 接收的沙箱 attempt 契约。

二者没有交叉导入或运行时类型冲突；当前无需重命名。文档和代码引用时应保留模块限定或结合所在模块理解。

动态 MCP/外部 Tool Schema 由 `jsonschema` 的 Draft 2020-12 校验器执行标准 `type`、枚举、边界和闭合对象规则；Eidos 的 `BoundedJsonSchema` 仍在构造前 fail-closed 地限制 Schema/Value 字节、深度和节点数、允许关键字与类型、JSON 安全数值和稳定错误码。它不支持 `$ref` 或其他引用关键字，并使用没有检索器的 `referencing.Registry`，因此 Schema 不能触发网络、文件或包资源访问。默认值是 Eidos 独立的确定性投影：只写入显式属性默认值，且不创建缺失父对象。

## 多 ToolCall 语义

`parallel_tool_calls=true` 只允许模型在一次响应中声明多个 ToolCall，不代表 Runtime 无条件并发。

1. `ToolDispatcher` 先校验整批调用、工具可用性、参数契约、重复 provider ID 和 batch policy；非法组合整批零执行。
2. 只有全部工具同时满足 `batchPolicy=parallel` 与 `concurrency.mode=parallel_safe`，且输入通过敏感信息检查时，批次才并发。
3. 当前符合条件的是内置安全只读工具。Workspace 写入、Shell、Eidos state 和外部/MCP 工具均为 `single`/`exclusive`，不得并发。
4. ToolCall row、`batchOrder`、模型上下文结果和批次汇总始终按模型声明顺序排列，不按线程完成顺序排列。
5. 并发基础设施故障取消同批任务并向上收敛；普通只读工具错误保留为对应 ToolResult，不改变其他结果的声明顺序。

## 关键代码入口

| 边界 | 路径 |
|---|---|
| Desktop shared contract | `desktop/shared/domain-contracts.ts` |
| Model Profile generated contract | `runtime/eidos_runtime/contracts/export_model_profile.py` → `contracts/generated/model-profile.schema.json` → `desktop/shared/generated/runtime/model-profile.ts` |
| Main JSON-RPC validator/client | `desktop/main/runtime-client.ts` |
| Python DTO | `runtime/eidos_runtime/protocol/schemas.py` |
| JSON-RPC server | `runtime/eidos_runtime/protocol/server.py` |

Model Profile 能力只由本地声明解析：显式用户声明优先于内置 Provider Preset，Preset 缺失时保守为不支持。Eidos 不发送 Test Connection 或能力探测请求；网络、认证和 Provider 兼容性仅由真实 Model Attempt 的稳定错误映射确认。新 Run 冻结当时解析出的能力，历史持久化 Snapshot 仅用于读取兼容，不决定 Profile 是否可选。Model Gateway 直接用冻结 Profile 构造 Pydantic AI Provider 和 Model，`WireAPI` 选择对应模型类；Eidos 不维护独立的 Provider/Wire transport registry。注入的 HTTP Client 由 Pydantic AI `AsyncTenacityTransport` 执行建立响应流前的唯一网络重试，OpenAI SDK `max_retries=0`；`RetryPolicy.max_attempts` 是单个逻辑 Model Attempt 内的总 HTTP 请求数，Transport Retry 不增加 SQLite `model_attempt`，流已消费后不重放。

Runtime Core 目前仍是同步 Durable Runtime，`RunSupervisor` 与 Run Worker 仍使用线程。模型异步 I/O 由 `RuntimeServer` 唯一持有的 `RuntimeAsyncKernel` 承载：一个进程级 AnyIO `BlockingPortal` 可并发执行多个 Model Client、Run Sampling 与标题生成请求；Client 只通过该 kernel 调用 Pydantic AI 的公开异步 Direct API。关闭顺序先结束 Run/Model Client，再关闭 kernel 并释放唯一 `async_kernel` 资源。MCP、Tool Execution 与 Managed Task 尚未迁移到该 kernel。
| DB schema/mappers/events | `runtime/eidos_runtime/db/` |
| Run loop | `runtime/eidos_runtime/runtime/engine.py` |
| Tool batch/single/orchestration | `runtime/eidos_runtime/runtime/tool_runtime.py`, `tool_execution.py`, `tool_orchestrator.py` |
| Tool contracts/registry | `runtime/eidos_runtime/tools/` |
