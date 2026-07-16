# 总体架构

版本：v0.4

范围说明：本文描述完整目标态架构。第一期采用 [MVP Lite](../mvp-lite.md) 的 stdio JSON-RPC 双向协议，不实现本文件中的 loopback HTTP/SSE、Bearer Token 和完整目标态组件集合。

MVP Lite 当前实施状态：✅ Electron Renderer/Preload/Main/Python Runtime 四层骨架；✅ Main 拉起单 sidecar 并取得 Electron single-instance lock；✅ stdio JSON-RPC 双向请求、通知与审批；✅ SQLite、DeepSeek Chat SSE、文件工具和 Seatbelt Shell 主链路；✅ 真实模型“读取 -> 写入审批 -> Shell 审批 -> 最终回答”验收。

第二期实施边界：以 [第二期实施范围清单](../mvp-phase-2.md) 为准，继续使用现有 stdio JSON-RPC 和单 sidecar。第二期在此边界内补齐安全启动、持久 FIFO、Event、暂停/继续、Durable Intent、敏感扫描、文件工具契约，以及 Pydantic DTO/契约模型；不在本期切换 loopback HTTP/SSE 或引入 FastAPI、SQLAlchemy、Alembic。Pydantic 仅用于闭合数据模型与校验，不成为 Runtime 核心流程的替代品。

## 1. 技术目标

构建一个仅支持 macOS 的本地 Agent Runtime，优先保证以下主链路可运行、可审批、可追踪、可恢复：

```text
Electron Renderer
  -> Electron Main
  -> Python FastAPI sidecar
  -> Model Gateway / Runtime Engine
  -> Tool Executor
  -> macOS Seatbelt / File Tools
  -> SQLite State + Timeline
```

MVP 是单用户、单机、单 sidecar、单执行器。可以存在多个 Run，但不会并行调用模型或执行工具。

## 2. 技术选型

| 层 | 选型 | 说明 |
|---|---|---|
| Desktop | Electron + React + TypeScript | Workbench、审批、Diff、Artifact 和 Timeline |
| Runtime | Python 3 + FastAPI + Pydantic | Agent Loop、API、模型与工具生态 |
| Persistence | SQLite + SQLAlchemy 2.x + Alembic | 本地状态、队列、审批与事件 |
| Shell Sandbox | `/usr/bin/sandbox-exec` + Seatbelt policy | macOS 进程级文件和网络隔离 |
| Network | Eidos loopback managed proxy | ToolCall 域名白名单 |
| Streaming | sidecar SSE -> Main -> Renderer IPC | 模型、工具和状态事件 |

MVP 不引入 Rust、Redis、PostgreSQL、Docker、内嵌 PTY 或后台 daemon。

## 3. 进程架构

```text
React Renderer
  │ typed IPC only
  ▼
Electron Main
  ├── spawn/stop sidecar
  ├── hold runtime token and sidecar port
  ├── proxy HTTP and SSE
  ├── folder picker / open system Terminal
  └── app lifecycle
  │ bearer-authenticated loopback HTTP/SSE
  ▼
Python FastAPI Sidecar
  ├── Run Scheduler (single executor)
  ├── Runtime Engine / Context Builder
  ├── Model Gateway / Responses + Chat Adapters / Transport Controller
  ├── Model Profile / Capability Registry
  ├── Versioned Tool Contract Registry / Tool Executor
  ├── Approval / Resume / Recovery
  ├── Seatbelt Policy Builder
  ├── Toolchain Profile Registry
  ├── Workspace Manifest / Shell Resource Monitor
  ├── Shell Output Capture
  ├── Managed Network Proxy
  ├── Redaction Service
  └── Repository / Event Outbox
  │
  ├── signed minimal Shell guardian per active Shell
  ├── ~/.eidos/eidos.db
  ├── ~/.eidos/config.toml
  ├── workspace/public files
  └── sandboxed child process tree
```

## 4. 信任边界

### 4.1 Renderer

Renderer 处理模型生成的 Markdown、代码和链接，属于不可信展示层：

- 不知道 sidecar token 和随机端口。
- 不直接访问本地文件或 sidecar。
- 只能调用 Preload 暴露的类型化 API。
- 不能传入任意 IPC channel。

### 4.2 Electron Main

Main 是 Desktop 权限边界：

- 生成每次 sidecar 生命周期独立的 runtime token。
- 通过环境变量把 token 传给 sidecar；stdout 只把当前 child 的类型化 `listening`/`ready` 行作为控制消息，其他内容按安全日志处理。
- 为 API/SSE 请求附加 token，并对响应做字段白名单转换。
- 用户点击时调用文件夹选择器或打开系统 Terminal。
- 不执行 Agent Shell。

### 4.3 Python sidecar

Sidecar 是受信任 Runtime，但 Agent 输入、模型输出和 ToolCall 参数均不可信：

- Tool Registry 以独立 `tool_contract_version` 固化模型可见定义、Tool Schema Dialect v1、effective arguments 归一化、ToolResult schema、mode applicability 和执行语义；Run 创建时固定版本，Step 再冻结确定性的 available tool set。
- Provider 对工具参数的约束不是授权边界。所有 ToolCall 都由本地 Runtime 递归闭合 schema、组合、敏感、审批和权限校验；wire 请求显式使用 `strict=false`。
- Tool Executor 只消费 effective arguments；ToolCall 保存唯一 immutable base ToolResult，Context Builder 生成冻结的有界 projection，两种 Adapter 只编码同一 Step projection，不生成额外自由文本结果。
- 两种 wire Adapter 只负责确定性协议编解码；Provider conversation/response 状态不作为 Runtime 事实来源，stream/event/ToolCall assembler 在进入业务状态前执行硬容量限制。
- 文件工具经 Workspace Guard。
- Shell 只通过 Seatbelt 执行；沙箱不可用时 fail closed。
- sidecar 可以读取 `~/.eidos/config.toml`，沙箱子进程不能读取真实 `~/.eidos`。

## 5. 启动流程

```text
Main acquire Electron single-instance lock
  -> Main generate runtime token
  -> spawn sidecar with token
  -> sidecar verify/create ~/.eidos owner, symlink, mode and parent identity
  -> sidecar acquire full-lifetime exclusive OS lock
  -> sidecar bind 127.0.0.1:random_port and emit listening
  -> health-only router gate
  -> sidecar verify same-filesystem allocated emergency reserve and storage headroom
  -> sidecar validate DB revision, backup/migrate if needed, and verify integrity
  -> sidecar validate boot/continuous timebase and apply timed-Approval invalidation before accepting commands
  -> sidecar reconcile profile credential revisions and invalidate stale Gateway capability snapshots
  -> sidecar load/self-test current Tool Contract and Tool Schema Dialect
  -> sidecar load ToolResult quarantine and run build-specific projector/serializer regression self-tests
  -> sidecar load and self-test Redaction ruleset
  -> sidecar run Seatbelt self-test
  -> sidecar discover/validate Toolchain Profiles
  -> sidecar self-test Shell guardian, limits, manifest monitor and output capture
  -> sidecar reconcile guardian/intents/running state and rebuild FIFO
  -> atomically flush unique ready {port, event/API contract versions, capability states}
  -> release scheduler ready latch
  -> Main starts API/SSE proxy
```

`listening` 只表示 loopback socket 已绑定，不表示 Runtime 可用。ready 前的路由 gate 必须早于业务路由匹配和 request body 解析：除固定、内存安全的 `/internal/health` 外，所有 API/SSE 固定返回 `503 runtime_not_ready`，不读取业务表、不占 idempotency key、不创建 Event。Main 只接受当前 child 的单个、匹配 schema 的 ready，之后才开放业务 IPC；scheduler 在同一 ready latch 释放前不得认领 Run。

可以在 ready 中降级的 capability 只有彼此隔离且不破坏安全主链路的能力：Shell guardian/Seatbelt/资源监控失败使 `run_shell` unavailable；单个 Toolchain/Profile 失败只禁用该项；per-tool/global ToolResult quarantine 分别禁用对应工具/全部工具；已安全持久化的未知 Shell 后置事实使相关 Run/Workspace 保持 reconciliation。状态目录/独占锁、storage reserve/headroom、DB revision/迁移/完整性/恢复、API/Event contract 握手、Redaction 核心自检、当前 Tool Registry/Schema Dialect/shared serializer 安全自检或 running/FIFO 对账不一致时保持 health-only，禁止发布 ready。

health 只返回闭合的 `phase,stage,reason_code,capabilities`；不得包含异常正文、SQL、用户路径内容、凭证或 stack。

Seatbelt 自检失败不阻止只读文件工具和模型回复，但 `run_shell` 必须报告 unavailable。

Redaction 规则 schema、重叠顺序、最大匹配长度或测试向量自检失败时，所有可能把不可信内容发送给模型/UI 或写入持久化的 API 不可用；只保留 health 和安全配置诊断能力，不存在未扫描回退。

当前 Tool Contract、任一 input/result schema、静态 default 或 Dialect v1 固定 probe 自检失败时 Runtime 不进入 ready。升级后若非终态 Run 的旧 `tool_contract_version` 实现不存在或不再满足当前安全底线，该 Run 零模型/工具执行并进入 `waiting_user_input/runtime_contract_unsupported`；只能取消或创建新 Run。

ToolResult quarantine 普通重启后仍有效。新 build 只可在对应 top-level serializer/shared mapping 或 per-tool projector 的确定性失败向量全部通过后清除相应 scope；未清除的 tool scope 从 available tool set 排除，global scope 禁止全部工具循环。

MVP 只从随应用发布的只读资源加载一个规则集，不从网络、Workspace 或用户配置加载规则。

Shell guardian、限制、Workspace manifest/磁盘增长监控或输出捕获任一自检失败时，与 Seatbelt 失败相同：只使 `run_shell` unavailable，不降级为无监控 Shell。

## 6. Eidos Home

```text
~/.eidos/                         mode 0700
  eidos.db
  runtime.lock                    OS lock authority; file content is diagnostic only
  config.toml                     mode 0600
  emergency.reserve              mode 0600; >=16 MiB allocated, same filesystem as DB
  backups/                        mode 0700; migration backups and manifests
  public/sessions/{session_id}/
    files/
    artifacts/{artifact_id}/
  workspaces/{workspace_id}/
    workspace.toml
    sessions/{session_id}/
      artifacts/{artifact_id}/
  sandbox/
    homes/{tool_call_id}/
    tmp/{tool_call_id}/
    cache/{tool_call_id}/
    manifests/{tool_call_id}/{execution_nonce}/
    guardian-leases/{tool_call_id}/{execution_nonce}.json
  logs/
```

- Workspace 业务文件仍在用户选择的目录。
- Artifact 目录保存不可变快照，不保存动态链接。
- ToolCall 沙箱目录在调用结束后可清理；审计元数据保存在数据库。
- MVP 不自动清理 Public files、Artifact、Event 和日志。

## 7. 代码目录建议

```text
Eidos/
  desktop/
    main/
    preload/
    renderer/
  runtime/app/
    api/
    db/
    scheduler/
    runtime/
    model/
    tools/
    sandbox/
    network/
    approval/
    artifacts/
    events/
    security/
  runtime/resources/seatbelt/
  tests/runtime/
  tests/desktop/
  docs/
```

Seatbelt profile 作为只读资源随 sidecar 打包。策略模板、参数绑定和 Shell 启动必须集中在 `sandbox/`，禁止业务代码自行 spawn Agent 命令。

## 7.1 第二期 RuntimeEngine 模块

第二期把现有 `eidos_runtime.runtime_loop.RuntimeLoop` 演进为 `eidos_runtime/runtime/` 包。目标是责任与测试 seam 的拆分，不是按行数切分文件；`RuntimeEngine.run(run_id, cancel)` 是 Runtime Server 和测试唯一需要知道的执行接口。

```text
eidos_runtime/runtime/
  loop.py              # RuntimeEngine：装配依赖、驱动一次 Run、无业务分支复制
  state_machine.py     # Run/执行态合法迁移、allowed_actions、迁移原因
  model_runner.py      # 单 Step 的上下文、流、Attempt、模型响应归一化
  tool_dispatcher.py   # ToolSpec 选择、批次校验、effective arguments 与执行分派
  approval.py          # Approval 请求、决策、失效、Reject 计数
  events.py            # 状态事实与 Event 的同事务记录及安全通知投影
  errors.py            # 闭合 Runtime/Tool 错误码与安全映射
```

- `loop.py` 只协调状态机给出的下一步，不直接解析模型流、执行工具或写 Approval/Event。
- `state_machine.py` 是唯一允许从事件推导持久 Run 状态和瞬时 `RuntimeState` 的模块；Storage 仍负责事务与条件更新，不在状态机中嵌入 SQL。
- `model_runner.py`、`tool_dispatcher.py` 和 `approval.py` 只返回闭合结果或领域事件，不能自行把任意字典写入 SQLite、Event 或 Renderer。
- `events.py` 是 Runtime 内部事件 seam，不改变现有 stdio notification 通道；通知必须由已提交的事实投影生成。
- 先迁移 `RuntimeLoop` 的职责与测试，再删除旧模块；不得保留两个可独立运行的 Loop。
