# 总体架构

版本：v0.4

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
  ├── Model Gateway
  ├── Tool Registry / Tool Executor
  ├── Approval / Resume / Recovery
  ├── Seatbelt Policy Builder
  ├── Managed Network Proxy
  ├── Redaction Service
  └── Repository / Event Outbox
  │
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
- 通过环境变量把 token 传给 sidecar；stdout 只读取 ready/port。
- 为 API/SSE 请求附加 token，并对响应做字段白名单转换。
- 用户点击时调用文件夹选择器或打开系统 Terminal。
- 不执行 Agent Shell。

### 4.3 Python sidecar

Sidecar 是受信任 Runtime，但 Agent 输入、模型输出和 ToolCall 参数均不可信：

- 所有 ToolCall 先通过 schema、组合和权限校验。
- 文件工具经 Workspace Guard。
- Shell 只通过 Seatbelt 执行；沙箱不可用时 fail closed。
- sidecar 可以读取 `~/.eidos/config.toml`，沙箱子进程不能读取真实 `~/.eidos`。

## 5. 启动流程

```text
Main generate runtime token
  -> spawn sidecar with token
  -> sidecar verify ~/.eidos permissions and migrate DB
  -> sidecar load and self-test Redaction ruleset
  -> sidecar run Seatbelt self-test
  -> sidecar bind 127.0.0.1:random_port
  -> stdout {"event":"ready","port":12345,"shell_available":true|false,"redaction_available":true|false}
  -> Main starts API/SSE proxy
  -> sidecar reconciles interrupted state and restores FIFO queue
```

Seatbelt 自检失败不阻止只读文件工具和模型回复，但 `run_shell` 必须报告 unavailable。

Redaction 规则 schema、重叠顺序、最大匹配长度或测试向量自检失败时，所有可能把不可信内容发送给模型/UI 或写入持久化的 API 不可用；只保留 health 和安全配置诊断能力，不存在未扫描回退。

MVP 只从随应用发布的只读资源加载一个规则集，不从网络、Workspace 或用户配置加载规则。

## 6. Eidos Home

```text
~/.eidos/                         mode 0700
  eidos.db
  config.toml                     mode 0600
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
