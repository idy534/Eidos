# Eidos Agent Runtime MVP 技术设计文档 TDD

版本：v0.3
桌面端：Electron + React
本地 Runtime：Python FastAPI sidecar
数据库：SQLite
ORM：SQLAlchemy 2.x
迁移工具：Alembic
数据根目录：`~/.eidos`

---

## 1. 设计目标

构建一个本地桌面端 Eidos Agent Runtime MVP，支持：

```text
Electron 桌面端
Python FastAPI sidecar
SQLite 状态持久化
Eidos Home (~/.eidos)
Workspace Mode / Public Mode
Session 管理
Run 执行
Step 记录
ToolCall 执行
Approval 审批
审批恢复
Workspace 文件隔离
Execution Feed
Artifacts
```

核心原则：

1. Runtime 主流程稳定，工具可插拔。
2. 所有状态可持久化、可追踪、可恢复。
3. 文件和命令执行必须受 active root 约束。
4. 中高风险工具调用必须审批。
5. 第一版不做复杂 Planner，采用 ReAct-style loop。
6. 第一版不做分布式，单进程即可。
7. 第一版必须把审批恢复作为核心路径，而不是附属流程。
8. Eidos 是前台执行型 Agent，不作为后台 daemon 常驻。

---

## 2. 技术选型

### 2.1 桌面端：Electron + React

选择 Electron + React 的原因：

- Electron Main 适合管理本地进程、文件夹选择、打开文件和应用生命周期。
- React 适合构建 Execution Feed、Artifacts、Approval、文件树等复杂状态 UI。
- Web UI 生态适合 Markdown、代码预览、日志、终端输出等桌面工作台能力。
- MVP 阶段比 Tauri / Flutter / Qt 更快打通产品闭环。

### 2.2 Runtime：Python FastAPI sidecar

选择 Python sidecar 的原因：

- LLM SDK、工具生态、Agent 原型开发效率高。
- FastAPI + Pydantic 适合快速构建内部 API 和 schema。
- 后续接 LangGraph、MCP、Jupyter、文件处理工具更顺滑。
- 与 Electron 解耦，Runtime 可以独立测试。

### 2.3 数据库：SQLite

MVP 选择 SQLite，不选择 PostgreSQL。

原因：

1. Eidos 是个人桌面端应用，用户不应该为了启动应用安装 Docker。
2. MVP 是单用户、单机、单进程 Runtime，SQLite 足够。
3. `~/.eidos/eidos.db` 符合桌面应用的数据管理习惯。
4. Run / Step / ToolCall / Approval / Event / Artifact 都可以落 SQLite。

PostgreSQL 作为后续服务化、多用户、多 worker 部署选项，不进入 MVP。

### 2.4 暂不引入 Redis

MVP 阶段不强依赖 Redis。实时事件由 sidecar 写入 SQLite `events` 表，再由 Electron Main 通过 SSE 连接接收并转发给 Renderer。

```text
Runtime Engine
  ↓ persist event
SQLite events
  ↓ stream event
FastAPI SSE
  ↓ token-authenticated request
Electron Main
  ↓ IPC
React Renderer
```

---

## 3. 总体架构

```text
React Renderer
  │
  │ IPC invoke / subscribe
  ▼
Electron Main
  │
  ├── generate runtime token
  ├── start/stop Python sidecar
  ├── read sidecar stdout port
  ├── proxy HTTP API
  ├── proxy SSE events
  ├── choose workspace folder
  └── open file / folder
  │
  │ HTTP / SSE with token
  ▼
Python FastAPI Sidecar (127.0.0.1:random_port)
  │
  ├── Runtime Engine
  ├── Context Builder
  ├── Model Gateway
  ├── Tool Registry
  ├── Tool Executor
  ├── Approval Manager
  ├── Resume Coordinator
  ├── Workspace Manager
  └── Event Store
  │
  ├── ~/.eidos/eidos.db
  └── Local File System
```

安全边界：

- Renderer 不知道 sidecar port。
- Renderer 不持有 runtime token。
- Renderer 不直接访问 sidecar。
- Electron Main 代理所有 API 和 SSE。
- sidecar 只监听 `127.0.0.1` 随机端口。
- sidecar token 由 Electron Main 生成，通过 env 注入。
- sidecar stdout 只输出 port / ready 信息，不输出 token。

---

## 4. Eidos Home 目录

MVP 使用用户级 `~/.eidos` 作为应用数据根目录。

```text
~/.eidos/
  eidos.db
  config.toml
  agents/
  model_profiles/
  public/
    sessions/
      {session_id}/
        files/
        artifacts/
        runs/
        events/
  workspaces/
    {workspace_id}/
      workspace.toml
      sessions/
        {session_id}/
          artifacts/
          runs/
          events/
  logs/
  cache/
```

规则：

- Public Mode 的 `files/` 是内部执行空间，Renderer 不展示文件树。
- Public Mode 用户只通过 Artifacts 看到产物。
- Workspace Mode 的真实业务文件仍在用户选择的项目目录中。
- Workspace Mode 的运行记录、events、artifacts 索引保存在 `~/.eidos/workspaces/{workspace_id}/`。
- MVP 不主动清理 Public Mode 产物、Artifacts、Events 或 Logs。
- MVP 不在用户项目目录默认写 `.eidos/`。

---

## 5. 运行模式

### 5.1 Workspace Mode

Session 绑定用户选择的真实项目目录。

```text
session.mode = "workspace"
workspace.root_path = 用户选择的文件夹
active_root = workspace.root_path
state_root = ~/.eidos/workspaces/{workspace_id}/sessions/{session_id}
```

工具策略：

| 工具 | 策略 |
|---|---|
| list_files | 自动 |
| read_file | 自动，敏感文件仍禁止 |
| write_file | 审批 |
| run_shell | 审批 |

UI：

- 右栏显示文件树。
- 支持只读文件预览。
- 可展示 Artifacts、Workspace Terminal、日志。
- Workspace Terminal 基于当前工作目录打开，不绑定 Session、Run、Step 或 ToolCall。

### 5.2 Public Mode

Session 不绑定用户项目目录，使用 Eidos 公共空间。

```text
session.mode = "public"
active_root = ~/.eidos/public/sessions/{session_id}/files
state_root = ~/.eidos/public/sessions/{session_id}
```

工具策略：

| 工具 | 策略 |
|---|---|
| list_files | 自动，但仅供 Agent 内部使用 |
| read_file | 自动 |
| write_file | 自动 |
| run_shell | 审批 |

UI：

- 不显示文件树。
- 不展示底层 `files/`。
- 只展示 Artifacts、预览、日志。
- 不展示 Workspace Terminal。

---

## 6. Electron 设计

### 6.1 Electron Main

职责：

- 启动 / 停止 Python sidecar。
- 生成一次性 runtime token。
- 通过 env 将 token 传给 sidecar。
- 读取 sidecar stdout 获取 port。
- 代理 Renderer 的 HTTP API 请求。
- 代理 sidecar SSE，并通过 IPC 转发 event。
- 调用系统文件夹选择器。
- 打开文件和文件夹。
- 管理 Workspace Terminal 的本地 PTY 进程。
- 处理应用关闭生命周期。

sidecar 启动流程：

```text
Electron Main generate token
  ↓
spawn Python sidecar with EIDOS_RUNTIME_TOKEN env
  ↓
sidecar binds 127.0.0.1:random_port
  ↓
sidecar stdout: {"event":"ready","port":12345}
  ↓
Main stores port in memory
  ↓
Renderer can use IPC APIs
```

### 6.2 Renderer

职责：

- 展示三栏 Agent Workbench。
- 使用 xterm.js 渲染 Workspace Terminal。
- 通过 preload 暴露的最小 IPC API 调用 Main。
- 订阅 Main 转发的 run events。
- 不持有 token。
- 不直接访问 sidecar。

Electron 安全约束：

```text
contextIsolation: true
nodeIntegration: false
sandbox: true
preload 暴露最小 API
不向 Renderer 暴露 sidecar port/token
```

### 6.3 API 代理

Renderer 调用：

```ts
window.eidos.invoke("runs:create", payload)
window.eidos.subscribeRunEvents(runId, handler)
```

Main 内部代理：

```text
IPC request
  ↓
Main adds Authorization: Bearer {token}
  ↓
HTTP request to http://127.0.0.1:{port}/api/v1/...
  ↓
sidecar response
  ↓
Main returns sanitized response to Renderer
```

### 6.4 SSE 代理

```text
Python sidecar
  -> SSE /api/v1/runs/{run_id}/events

Electron Main
  -> 持有 token
  -> 建立 SSE 连接
  -> 接收 runtime event
  -> 通过 IPC 推送给 Renderer

Renderer
  -> 只订阅 Main 转发的 run events
```

### 6.5 Workspace Terminal

Workspace Terminal 是桌面端右栏的 Workspace 级交互式终端。

技术边界：

- Terminal 由 Electron Main 管理 PTY 进程，Renderer 只负责渲染和输入输出转发。
- Terminal 基于当前 Workspace 的 root path 打开，类似 IDE 内置终端。
- Terminal 不经过 Python sidecar，不进入 Runtime Engine。
- Terminal 不绑定 Session、Run、Step 或 ToolCall。
- 用户在 Terminal 中手动输入的命令不进入 `tool_calls`、`approvals` 或 `events` 表。
- Agent 通过 `run_shell` 执行的命令仍由 Runtime Engine 作为 ToolCall 执行，并展示在中栏 Execution Feed。
- Public Mode 不创建 Workspace Terminal。

---

## 7. UI 信息架构

Eidos MVP 采用三栏 Agent Workbench。

```text
┌──────────────┬──────────────────────────────┬──────────────────────┐
│ 导航区        │ 核心交互区                    │ 上下文与产物区          │
├──────────────┼──────────────────────────────┼──────────────────────┤
│ 会话          │ 对话                          │ 文件树 / Artifacts      │
│ 工作区        │ Execution Feed                 │ Terminal                │
│ 模型          │ 审批卡片                       │ Diff 预留 / 预览         │
│ 设置          │ 输入框                         │ 日志                    │
└──────────────┴──────────────────────────────┴──────────────────────┘
```

中栏把对话、执行流和时间线合并为 Execution Feed。

右栏在 MVP 中提供预览、Artifacts、Workspace Terminal 和日志；Diff 只预留入口，完整 Diff 展示放到 P1。

Execution Feed event card 类型：

- user_message
- assistant_message
- model_step
- tool_call
- approval_request
- approval_result
- tool_result
- artifact_created
- error
- final_answer

---

## 8. Model Profile 设计

MVP 支持多个 OpenAI-compatible model profile。DeepSeek 等兼容 OpenAI 协议的模型通过用户配置 `base_url / api_key / model` 接入，不做特殊 provider 分支。

规则：

- 每个 Session 可以选择一个 model profile。
- 切换 Session 模型时提示：只影响后续 Run，不影响历史 Run 和正在运行的 Run。
- Run 创建时固化 `model_config_snapshot`。
- running / waiting_approval / canceling 状态的 Run 不允许切换模型。
- approval resume 必须继续使用原 Run 的模型快照。
- `model_config_snapshot` 不保存明文 API key。

配置存储：

```text
model_profiles 表:
  id
  name
  base_url
  model
  api_key_ref
  parameters_json
  created_at
  updated_at
```

MVP 中 `api_key_ref` 可以指向 `~/.eidos/config.toml` 中的本地加密或明文配置；后续可迁移到系统 Keychain。

Run 快照：

```json
{
  "profile_name": "DeepSeek V4 Pro",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-v4-pro",
  "parameters": {
    "temperature": 0.2
  }
}
```

---

## 9. API Layer

sidecar API 仅供 Electron Main 内部调用。

主要接口：

```text
GET    /internal/health
POST   /api/v1/sessions
GET    /api/v1/sessions
GET    /api/v1/sessions/{session_id}
PATCH  /api/v1/sessions/{session_id}/model
POST   /api/v1/sessions/{session_id}/runs
GET    /api/v1/runs/{run_id}
GET    /api/v1/runs/{run_id}/steps
GET    /api/v1/runs/{run_id}/events
POST   /api/v1/runs/{run_id}/cancel
POST   /api/v1/approvals/{approval_id}/approve
POST   /api/v1/approvals/{approval_id}/reject
GET    /api/v1/workspaces/{workspace_id}/files
GET    /api/v1/workspaces/{workspace_id}/files/content
GET    /api/v1/artifacts
GET    /api/v1/artifacts/{artifact_id}
POST   /api/v1/model-profiles
GET    /api/v1/model-profiles
```

认证：

- 所有 API 除 health 外都要求 `Authorization: Bearer {runtime_token}`。
- token 来自 env `EIDOS_RUNTIME_TOKEN`。
- token 不写入日志、磁盘或 stdout。

---

## 10. Runtime Engine

职责：

- 驱动 Agent Loop。
- 创建 Step。
- 调用模型。
- 解析模型输出。
- 创建 ToolCall。
- 判断是否需要审批。
- 执行工具。
- 保存工具结果。
- 持久化并推送事件。
- 处理失败、取消、超时。

核心流程：

```text
start_run(run_id)
  ↓
load run.model_config_snapshot
  ↓
resume_loop(run_id, start_step_index = next_step_index)
  ↓
while step_count < max_steps:
  if canceled: finish canceled
  build_context
  call_model with run snapshot
  if final_answer: finish succeeded
  if tool_calls:
    for each tool_call:
      create tool_call
      check approval by session mode and tool risk
      if need approval:
        create approval
        mark tool_call pending_approval
        mark run waiting_approval
        return
      execute tool with ToolContext
      save result
  mark step succeeded
  continue
```

审批恢复：

```text
resume_after_approval(run_id, approval_id)
  ↓
lock run / approval / tool_call
  ↓
verify run.status = waiting_approval
  ↓
verify approval.status = pending
  ↓
apply approve or reject
  ↓
continue with original run.model_config_snapshot
```

---

## 11. Context Builder

上下文顺序：

```text
1. 内置 system prompt（不暴露给用户）
2. Eidos Runtime 行为规则
3. 当前 session mode
4. active root 信息
5. 权限和安全规则
6. 可用工具定义
7. 历史摘要，可选
8. 最近消息
9. 最近 Step / ToolCall / Approval 结果
10. 用户当前任务
```

设计原则：

- system prompt 是内部运行协议，不提供查看或编辑入口。
- 静态内容放前面。
- 高频变化内容放后面。
- 工具列表顺序固定，避免上下文不稳定。
- tool result 过长时裁剪。
- 后续支持 compaction。

---

## 12. Model Gateway

MVP 实现一个 OpenAI-compatible gateway。

```python
from typing import Protocol

class ModelGateway(Protocol):
    async def create_response(self, request: "ModelRequest") -> "ModelResponse":
        ...
```

模型输出统一为：

```python
from pydantic import BaseModel, Field

class ToolCallRequest(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)

class ModelResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    finish_reason: str | None = None
```

---

## 13. Tool Registry 和 ToolContext

工具定义不绑定具体 session；执行时通过 `ToolContext` 注入当前 run/session/active_root/limits。

```python
from pathlib import Path
from typing import Protocol
from uuid import UUID
from pydantic import BaseModel, Field

class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict
    risk_level: str
    timeout_seconds: int = 30

class ToolContext(BaseModel):
    run_id: UUID
    session_id: UUID
    mode: str
    active_root: Path
    state_root: Path
    output_limit: int = 20000
    shell_timeout_seconds: int = 30

    class Config:
        arbitrary_types_allowed = True

class ToolResult(BaseModel):
    content: str
    metadata: dict = Field(default_factory=dict)

class ToolExecutor(Protocol):
    def definition(self) -> ToolDefinition:
        ...

    async def execute(self, ctx: ToolContext, args: dict) -> ToolResult:
        ...
```

---

## 14. 内置工具设计

### 14.1 list_files

用途：查看 active root 文件结构。
风险：low。

限制：

- 只能列出 active root 内文件。
- Workspace Mode 中用于 UI 文件树和模型工具调用。
- Public Mode 中只供 Agent 内部使用，UI 不展示文件树。
- 默认忽略 `.git`、`__pycache__`、`node_modules`、`.venv`、`.runtime`。

### 14.2 read_file

用途：读取 active root 内文件。
风险：low。

限制：

- 禁止读取 active root 外路径。
- 禁止读取 `.env`、`.ssh` 等敏感文件。
- 单次最大读取 20000 字符。

### 14.3 write_file

用途：写入 active root 内文件。
风险：medium。

限制：

- Workspace Mode 默认需要审批。
- Public Mode 默认自动执行。
- MVP 不做写入前快照。
- 写入行为必须记录 tool_call 参数、目标路径、写入模式、内容摘要和 event。
- 禁止写 active root 外路径。

### 14.4 run_shell

用途：执行受控 shell 命令。
风险：high。

`run_shell` 是 Agent 工具调用，不复用右栏 Workspace Terminal。它的命令、参数、审批、输出和结果仍然作为 ToolCall 进入 Execution Feed。

限制：

- Public Mode 和 Workspace Mode 都必须审批。
- cwd 必须在 active root 内。
- 默认超时 30 秒。
- 输出最多 20000 字符。
- MVP 默认禁用交互式命令。
- 黑名单只作为额外保护，不作为唯一安全边界。
- 超时或 cancel 时必须终止进程组。

---

## 15. Workspace Guard

路径校验必须使用 `relative_to` / `is_relative_to`，不能使用字符串 `startswith`。

```python
from pathlib import Path

def resolve_active_path(active_root: Path, user_path: str) -> Path:
    root = active_root.resolve()
    target = (root / user_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError("path escapes active root") from exc
    return target
```

敏感文件规则：

```text
.env
.env.*
*.pem
*.key
id_rsa
id_ed25519
.ssh/*
```

---

## 16. Event Store / Execution Feed

MVP 使用 SQLite `events` 表作为事实来源。Electron Main 通过 SSE 读取事件并转发给 Renderer。

事件类型：

```text
run_started
run_status_changed
step_started
model_delta
tool_call_created
tool_call_waiting_approval
tool_call_started
tool_call_succeeded
tool_call_failed
approval_created
approval_approved
approval_rejected
artifact_created
step_succeeded
step_failed
run_succeeded
run_failed
run_canceled
```

SSE 格式：

```text
id: 1024
event: tool_call_created
data: {"run_id":"...","tool_name":"read_file","args":{...}}
```

SSE 支持断线回放：

```text
GET /api/v1/runs/{run_id}/events?after_event_id=1024
```

Renderer 看到的是 Main 转换后的 Execution Feed item，而不是 raw event 的唯一展示方式。

---

## 17. 状态机设计

### 17.1 Run 状态

```text
created
running
waiting_approval
succeeded
failed
canceled
expired
```

状态流转：

```text
created -> running
running -> waiting_approval
waiting_approval -> running
running -> succeeded
running -> failed
running -> canceled
waiting_approval -> canceled
running -> expired
waiting_approval -> expired
```

### 17.2 Step 状态

```text
created
running
waiting_approval
succeeded
failed
skipped
```

当某个 tool call 需要审批时，当前 step 进入 `waiting_approval`，恢复后继续完成同一个 step。

### 17.3 ToolCall 状态

```text
created
pending_approval
approved
rejected
running
succeeded
failed
timeout
```

### 17.4 Approval 状态

```text
pending
approved
rejected
expired
```

### 17.5 幂等与并发规则

- approve 只允许 `approval.status = pending` 且 `run.status = waiting_approval` 时成功。
- reject 只允许 `approval.status = pending` 且 `run.status = waiting_approval` 时成功。
- cancel 只允许 `run.status = running` 时成功。
- 所有状态转换使用事务和条件更新。
- 对同一 run 的 resume 必须串行执行。
- resume 必须继续使用原 Run 的 `model_config_snapshot`。

---

## 18. 数据库设计

### 18.1 表清单

| 表 | 说明 |
|---|---|
| agents | Agent 配置，MVP 只有默认 Eidos |
| model_profiles | OpenAI-compatible 模型配置 |
| workspaces | Workspace Mode 的真实项目目录记录 |
| sessions | 会话和 mode |
| messages | 用户和助手消息 |
| runs | 一次任务执行 |
| run_steps | 执行步骤 |
| tool_calls | 工具调用 |
| approvals | 审批记录 |
| artifacts | 任务产物 |
| events | 运行事件，MVP 必须持久化 |

### 18.2 DDL 草案

```sql
CREATE TABLE agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE model_profiles (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    base_url        TEXT NOT NULL,
    model           TEXT NOT NULL,
    api_key_ref     TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE workspaces (
    id              TEXT PRIMARY KEY,
    name            TEXT,
    root_path       TEXT NOT NULL,
    state_path      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE sessions (
    id                  TEXT PRIMARY KEY,
    agent_id            TEXT NOT NULL REFERENCES agents(id),
    model_profile_id    TEXT REFERENCES model_profiles(id),
    workspace_id        TEXT REFERENCES workspaces(id),
    mode                TEXT NOT NULL,
    title               TEXT,
    active_root_path    TEXT NOT NULL,
    state_root_path     TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active',
    last_active_at      TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    run_id          TEXT,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE TABLE runs (
    id                      TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL REFERENCES sessions(id),
    agent_id                TEXT NOT NULL REFERENCES agents(id),
    user_input              TEXT NOT NULL,
    status                  TEXT NOT NULL,
    current_step_id         TEXT,
    max_steps               INTEGER NOT NULL DEFAULT 20,
    model_profile_id        TEXT,
    model_config_snapshot   TEXT NOT NULL,
    error_message           TEXT,
    started_at              TEXT,
    finished_at             TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE run_steps (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    step_index      INTEGER NOT NULL,
    step_type       TEXT NOT NULL,
    status          TEXT NOT NULL,
    model_input     TEXT,
    model_output    TEXT,
    error_message   TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(run_id, step_index)
);

CREATE TABLE tool_calls (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    step_id         TEXT NOT NULL REFERENCES run_steps(id),
    tool_name       TEXT NOT NULL,
    arguments_json  TEXT NOT NULL,
    result_text     TEXT,
    status          TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    approval_id     TEXT,
    error_message   TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE approvals (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    tool_call_id    TEXT NOT NULL REFERENCES tool_calls(id),
    status          TEXT NOT NULL,
    reason          TEXT,
    requested_args  TEXT NOT NULL,
    approved_args   TEXT,
    user_feedback   TEXT,
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    UNIQUE(tool_call_id)
);

CREATE TABLE artifacts (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    name            TEXT NOT NULL,
    artifact_type   TEXT NOT NULL,
    path            TEXT NOT NULL,
    summary         TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE TABLE events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    event_type      TEXT NOT NULL,
    payload_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE INDEX idx_sessions_mode ON sessions(mode);
CREATE INDEX idx_sessions_last_active_at ON sessions(last_active_at);
CREATE INDEX idx_runs_session_id ON runs(session_id);
CREATE INDEX idx_runs_status ON runs(status);
CREATE INDEX idx_run_steps_run_id ON run_steps(run_id);
CREATE INDEX idx_tool_calls_run_id ON tool_calls(run_id);
CREATE INDEX idx_tool_calls_step_id ON tool_calls(step_id);
CREATE INDEX idx_approvals_run_id ON approvals(run_id);
CREATE INDEX idx_approvals_status ON approvals(status);
CREATE INDEX idx_events_run_id_id ON events(run_id, id);
```

---

## 19. 前台生命周期

规则：

- Electron Main 是 sidecar 的父进程。
- 窗口关闭即准备退出 Runtime。
- 没有 running Run 时，Main 正常停止 sidecar。
- 有 running Run 时，Renderer 弹窗要求用户选择等待完成或取消任务并退出。
- waiting_approval Run 可以持久化；下次启动后仍显示 waiting_approval，用户可继续 approve / reject。
- sidecar 不作为后台 daemon 存活。

启动恢复：

```text
start app
  ↓
start sidecar
  ↓
load ~/.eidos/eidos.db
  ↓
restore latest active session
  ↓
show waiting_approval runs if any
```

---

## 20. 代码目录结构

```text
Eidos/
  package.json
  pyproject.toml
  README.md

  desktop/
    main/
      index.ts
      sidecar.ts
      ipc.ts
      file-dialog.ts
    preload/
      index.ts
    renderer/
      src/
        App.tsx
        components/
        features/
        styles/

  runtime/
    app/
      main.py
      api/
      core/
      db/
      schemas/
      runtime/
      model/
      tools/
      workspace/
      approval/
      services/

  docs/
    agent_runtime_mvp_prd.md
    agent_runtime_mvp_tdd.md

  tests/
    runtime/
      test_workspace_guard.py
      test_approval_policy.py
      test_runtime_state.py
      test_approval_resume.py
      test_event_replay.py
```

---

## 21. 测试设计

### 21.1 Runtime 单元测试

| 测试 | 说明 |
|---|---|
| workspace path escape | `../` 不可逃逸 |
| workspace prefix escape | `/tmp/work` 不能误放行 `/tmp/work2` |
| workspace symlink escape | workspace 内 symlink 指向外部时不可逃逸 |
| sensitive file block | `.env`、`.ssh` 不可读 |
| approval policy | 不同 mode / 风险等级是否触发审批 |
| run state transition | 状态流转合法 |
| approval idempotency | approve / reject 重复请求不会重复执行 |
| model snapshot | Run 创建时固化模型配置 |
| shell forbidden command | 危险命令被拒绝 |

### 21.2 Runtime 集成测试

| 测试 | 说明 |
|---|---|
| public run without tool | Public Mode 模型直接回复 |
| public write_file | Public Mode 自动写入并登记 artifact |
| workspace write_file approval | Workspace Mode 写入触发审批 |
| approve shell | 审批后执行原 tool call 并从下一 step 继续 |
| reject shell | 拒绝后模型继续 |
| cancel running run | running 任务可取消 |
| event replay | SSE 可以从指定 event id 回放 |

### 21.3 Desktop 集成测试

| 测试 | 说明 |
|---|---|
| sidecar startup | Main 生成 token、env 注入、读取 port |
| renderer isolation | Renderer 拿不到 token 和 port |
| api proxy | Renderer 通过 IPC 调 Main，Main 代理 sidecar |
| sse proxy | Main 转发 run events 给 Renderer |
| workspace terminal | Main 管理 PTY，Renderer 只渲染，不写入 Runtime events |
| quit with running run | running Run 关闭窗口时要求取消或等待 |
| restore session | 启动恢复最近 active session |

---

## 22. MVP 里程碑

### M1：桌面壳 + sidecar

- Electron 项目骨架
- React Renderer
- Python FastAPI sidecar
- Main 启停 sidecar
- token env 注入
- IPC API proxy

### M2：SQLite + Eidos Home

- `~/.eidos` 初始化
- SQLite schema
- SQLAlchemy models
- Alembic migrations
- 默认 Eidos Agent
- model profiles

### M3：Session / Run / Event

- Workspace Mode / Public Mode Session
- Run API
- Event Store
- Main 代理 SSE
- Execution Feed

### M4：Runtime Loop + 工具

- Context Builder
- Model Gateway
- Runtime Engine
- list_files / read_file / write_file / run_shell
- Workspace Guard

### M5：审批恢复 + 生命周期

- approval create
- approve
- reject
- resume_after_approval
- cancel run
- 关闭窗口 running Run 处理
- waiting_approval 启动恢复

### M6：Workbench 体验

- 三栏布局
- Workspace 文件树和只读预览
- Public Artifacts
- Workspace Terminal / 日志
- 基础错误展示

---

## 23. 后续扩展预留

| 扩展 | 预留点 |
|---|---|
| apply_patch | ToolExecutor 可新增 patch 工具 |
| Diff | Artifacts / ToolCall 可补 diff view |
| 写入快照 | state_root 下新增 snapshots |
| MCP | ToolExecutor 抽象可以映射 MCP tool |
| Skill | Context Builder 增加 skill recall |
| Memory | Context Builder 增加 memory recall |
| 多 Agent | agents 表已保留 |
| 多 worker | EventBus / DB 层可替换 |
| PostgreSQL | SQLAlchemy + Alembic 保留迁移空间 |
| Docker Sandbox | run_shell 从本机 shell 替换为容器执行 |
| 后台执行 | Electron tray / notification / daemon 生命周期 |

---

## 24. 关键设计取舍

### 24.1 为什么用 Electron + React

MVP 的重点是先做出完整 Agent Workbench。Electron + React 在桌面能力、复杂 UI、日志/代码/Markdown 展示、进程管理上更快。

### 24.2 为什么 Python sidecar

Python 更适合快速构建 Agent Runtime、模型调用、工具执行和后续 LangGraph / MCP 集成。

### 24.3 为什么 SQLite

Eidos 是个人桌面端应用，SQLite 减少本地部署阻力；PostgreSQL 留作服务化演进。

### 24.4 为什么 Renderer 不直连 sidecar

token 不进入 Renderer，减少攻击面。所有 sidecar API 和 SSE 都经 Electron Main 代理。

### 24.5 为什么不做后台执行

Eidos MVP 是前台执行型 Agent，不是任务守护器。后台执行会引入 tray、通知、任务守护、崩溃恢复等复杂度。

### 24.6 为什么 MVP 不做写入快照

先降低复杂度，验证可执行 Agent 主路径。MVP 的可恢复只指审批恢复和 Run 状态恢复，文件级恢复放后续。
