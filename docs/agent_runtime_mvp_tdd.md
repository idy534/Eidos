# Eidos Agent Runtime MVP 技术设计文档 TDD

版本：v0.2  
语言：Python  
Web 框架：FastAPI  
数据库：PostgreSQL 16  
ORM：SQLAlchemy 2.x  
迁移工具：Alembic  
部署方式：本地 Docker Compose + Python 服务  

---

## 1. 设计目标

构建一个本地可部署的 Eidos Agent Runtime MVP，支持：

```text
Agent 配置
Session 管理
Run 执行
Step 记录
ToolCall 执行
Approval 审批
审批恢复
Workspace 文件隔离
SSE 事件流
Run Timeline 持久化
PostgreSQL 状态持久化
```

核心原则：

1. Runtime 主流程稳定，工具可插拔。
2. 所有状态可持久化、可追踪、可恢复。
3. 文件和命令执行必须受 workspace 约束。
4. 中高风险工具调用必须审批。
5. 第一版不做复杂 Planner，采用 ReAct-style loop。
6. 第一版不做分布式，单进程即可。
7. 第一版必须把审批恢复作为核心路径，而不是附属流程。

---

## 2. 技术选型

### 2.1 后端语言：Python

选择 Python 的原因：

- LLM SDK、工具生态、Agent 原型开发效率高。
- FastAPI + Pydantic 适合快速构建 API 和 schema。
- 后续接 LangGraph、MCP、Jupyter、文件处理工具更顺滑。

### 2.2 Web 框架：FastAPI

| 能力 | 说明 |
|---|---|
| 类型友好 | Pydantic schema 直接作为接口定义 |
| OpenAPI | 自动生成 API 文档 |
| async 支持 | 适合 SSE、模型流式调用、工具执行 |
| 生态成熟 | 部署、鉴权、中间件丰富 |

### 2.3 数据库：PostgreSQL

选择 PostgreSQL，不选择 SQLite。

原因：

1. Agent Runtime 是状态密集型系统。
2. Run / Step / ToolCall / Approval / Event 需要事务一致性。
3. Tool arguments、metadata、event payload 适合 JSONB。
4. 后续可平滑扩展到多 worker、多用户、多租户。
5. 本地 Docker 部署成本低。

### 2.4 暂不引入 Redis

MVP 阶段不强依赖 Redis。实时推送使用进程内 EventBus，但所有关键事件必须先持久化到 `events` 表，再推送给 SSE 订阅者。

```text
Runtime Engine
  ↓ persist event
PostgreSQL events
  ↓ publish event id
InMemory EventBus
  ↓
SSE Response
```

后续如果要多进程、多 worker，再引入 Redis Pub/Sub 或 NATS。

---

## 3. 总体架构

```text
Client / Web UI / CLI
        │
        ▼
FastAPI Server
        │
        ├── Agent API
        ├── Session API
        ├── Run API
        ├── Approval API
        ├── Workspace API
        └── SSE Event API
        │
        ▼
Runtime Engine
        │
        ├── Context Builder
        ├── Model Gateway
        ├── Tool Registry
        ├── Tool Executor
        ├── Approval Manager
        ├── Resume Coordinator
        ├── Workspace Manager
        └── Event Store / Event Bus
        │
        ├── PostgreSQL
        └── Local Workspace FS
```

---

## 4. 模块设计

### 4.1 API Layer

职责：

- 接收 HTTP 请求。
- 参数校验。
- 调用 Application Service。
- 返回标准响应。
- 提供 SSE 事件流。

主要接口：

```text
POST   /api/v1/agents
GET    /api/v1/agents/{agent_id}
POST   /api/v1/agents/{agent_id}/sessions
POST   /api/v1/sessions/{session_id}/runs
GET    /api/v1/runs/{run_id}
GET    /api/v1/runs/{run_id}/steps
GET    /api/v1/runs/{run_id}/events
POST   /api/v1/runs/{run_id}/cancel
POST   /api/v1/approvals/{approval_id}/approve
POST   /api/v1/approvals/{approval_id}/reject
GET    /api/v1/sessions/{session_id}/workspace/files
GET    /api/v1/sessions/{session_id}/workspace/files/content
```

### 4.2 Runtime Engine

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
resume_loop(run_id, start_step_index = next_step_index)
  ↓
while step_count < max_steps:
  if canceled: finish canceled
  build_context
  call_model
  if final_answer: finish succeeded
  if tool_calls:
    for each tool_call:
      create tool_call
      check approval
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

### 4.3 Context Builder

职责：构造每次模型调用的上下文。

上下文顺序：

```text
1. System Prompt
2. Eidos Runtime 行为规则
3. 权限和安全规则
4. 当前 workspace 信息
5. 可用工具定义
6. 历史摘要，可选
7. 最近消息
8. 最近 Step / ToolCall / Approval 结果
9. 用户当前任务
```

设计原则：

- 静态内容放前面。
- 高频变化内容放后面。
- 工具列表顺序固定，避免上下文不稳定。
- tool result 过长时裁剪。
- 后续支持 compaction。

### 4.4 Model Gateway

职责：屏蔽不同模型 SDK 差异。

```python
from typing import Protocol

class ModelGateway(Protocol):
    async def stream_response(self, request: "ModelRequest") -> "ModelStream":
        ...

    async def create_response(self, request: "ModelRequest") -> "ModelResponse":
        ...
```

MVP 建议只实现一个 provider，例如 OpenAI-compatible API。

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

### 4.5 Tool Registry 和 ToolContext

工具定义不绑定具体 session；执行时通过 `ToolContext` 注入当前 run/session/workspace/limits。

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
    workspace_root: Path
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

Registry：

```python
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolExecutor] = {}

    def register(self, tool: ToolExecutor) -> None:
        name = tool.definition().name
        self._tools[name] = tool

    def get(self, name: str) -> ToolExecutor:
        return self._tools[name]

    def list_definitions(self) -> list[ToolDefinition]:
        return [tool.definition() for tool in self._tools.values()]
```

### 4.6 内置工具设计

#### 4.6.1 list_files

用途：查看 workspace 文件结构。  
风险：low。

参数：

```json
{
  "path": ".",
  "max_depth": 3
}
```

限制：

- 只能列出 workspace 内文件。
- 默认忽略 `.git`、`__pycache__`、`node_modules`、`.venv`、`.runtime`。

#### 4.6.2 read_file

用途：读取 workspace 文件。  
风险：low。

参数：

```json
{
  "path": "main.py",
  "start_line": 1,
  "end_line": 200
}
```

限制：

- 禁止读取 workspace 外路径。
- 禁止读取 `.env`、`.ssh` 等敏感文件。
- 单次最大读取 20000 字符。

#### 4.6.3 write_file

用途：写入 workspace 文件。  
风险：medium。

参数：

```json
{
  "path": "main.py",
  "content": "print('hello')",
  "mode": "overwrite"
}
```

限制：

- safe 模式下需要审批。
- 写入前保存旧内容快照。
- 禁止写 workspace 外路径。

#### 4.6.4 run_shell

用途：执行受控 shell 命令。  
风险：high。

参数：

```json
{
  "command": "python -m py_compile main.py",
  "cwd": ".",
  "reason": "检查 Python 文件是否有语法错误"
}
```

限制：

- 必须审批。
- cwd 必须在 workspace 内。
- 默认超时 30 秒。
- 输出最多 20000 字符。
- MVP 默认禁用交互式命令。
- 黑名单只作为额外保护，不作为唯一安全边界。
- 超时或 cancel 时必须终止进程组。

### 4.7 Approval Manager

职责：根据工具风险和策略判断是否需要人工审批。

审批模式：

| 模式 | 说明 |
|---|---|
| auto | 低/中风险自动，高风险审批 |
| safe | 低风险自动，中/高风险审批，默认 |
| manual | 所有工具调用都审批 |

审批动作：

| 动作 | 说明 |
|---|---|
| approve | 原参数执行 |
| edit | 修改参数后执行，P1 可做 |
| reject | 拒绝执行，并把反馈返回模型 |

MVP 先实现 approve / reject。

### 4.8 Workspace Manager

职责：

- 创建 session workspace。
- 校验文件路径。
- 阻止路径逃逸。
- 读写文件。
- 保存 artifact。

目录结构：

```text
.runtime/
  workspaces/
    {session_id}/
      files/
      artifacts/
      snapshots/
      logs/
```

路径校验逻辑必须使用 `relative_to` / `is_relative_to`，不能使用字符串 `startswith`。

```python
from pathlib import Path

def resolve_workspace_path(workspace_root: Path, user_path: str) -> Path:
    root = workspace_root.resolve()
    target = (root / user_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError("path escapes workspace") from exc
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

### 4.9 Event Store / Event Bus / SSE

MVP 使用 PostgreSQL `events` 表作为事实来源，进程内 EventBus 只负责实时通知。

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

SSE 需要支持断线回放：

```text
GET /api/v1/runs/{run_id}/events?after_event_id=1024
```

---

## 5. 状态机设计

### 5.1 Run 状态

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

### 5.2 Step 状态

```text
created
running
waiting_approval
succeeded
failed
skipped
```

当某个 tool call 需要审批时，当前 step 进入 `waiting_approval`，恢复后继续完成同一个 step。

### 5.3 ToolCall 状态

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

### 5.4 Approval 状态

```text
pending
approved
rejected
expired
```

### 5.5 幂等与并发规则

- approve 只允许 `approval.status = pending` 且 `run.status = waiting_approval` 时成功。
- reject 只允许 `approval.status = pending` 且 `run.status = waiting_approval` 时成功。
- cancel 只允许 `run.status in (running, waiting_approval)` 时成功。
- 所有状态转换使用事务和条件更新。
- 对同一 run 的 resume 必须串行执行。

---

## 6. 数据库设计

### 6.1 表清单

| 表 | 说明 |
|---|---|
| agents | Agent 配置 |
| sessions | 会话和 workspace |
| messages | 用户和助手消息 |
| runs | 一次任务执行 |
| run_steps | 执行步骤 |
| tool_calls | 工具调用 |
| approvals | 审批记录 |
| artifacts | 任务产物 |
| events | 运行事件，MVP 必须持久化 |

### 6.2 DDL

```sql
CREATE TABLE agents (
    id              UUID PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    description     TEXT,
    system_prompt   TEXT NOT NULL,
    model           VARCHAR(128) NOT NULL,
    temperature     NUMERIC(3,2) NOT NULL DEFAULT 0.20,
    max_steps       INTEGER NOT NULL DEFAULT 20,
    approval_mode   VARCHAR(32) NOT NULL DEFAULT 'safe',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    id              UUID PRIMARY KEY,
    agent_id        UUID NOT NULL REFERENCES agents(id),
    title           VARCHAR(256),
    workspace_path  TEXT NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id              UUID PRIMARY KEY,
    session_id      UUID NOT NULL REFERENCES sessions(id),
    run_id          UUID,
    role            VARCHAR(32) NOT NULL,
    content         TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE runs (
    id              UUID PRIMARY KEY,
    session_id      UUID NOT NULL REFERENCES sessions(id),
    agent_id        UUID NOT NULL REFERENCES agents(id),
    user_input      TEXT NOT NULL,
    status          VARCHAR(32) NOT NULL,
    current_step_id UUID,
    max_steps       INTEGER NOT NULL DEFAULT 20,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE run_steps (
    id              UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(id),
    step_index      INTEGER NOT NULL,
    step_type       VARCHAR(32) NOT NULL,
    status          VARCHAR(32) NOT NULL,
    model_input     TEXT,
    model_output    TEXT,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, step_index)
);

CREATE TABLE tool_calls (
    id              UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(id),
    step_id         UUID NOT NULL REFERENCES run_steps(id),
    tool_name       VARCHAR(128) NOT NULL,
    arguments_json  JSONB NOT NULL,
    result_text     TEXT,
    status          VARCHAR(32) NOT NULL,
    risk_level      VARCHAR(32) NOT NULL,
    approval_id     UUID,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
    id              UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(id),
    tool_call_id    UUID NOT NULL REFERENCES tool_calls(id),
    status          VARCHAR(32) NOT NULL,
    reason          TEXT,
    requested_args  JSONB NOT NULL,
    approved_args   JSONB,
    user_feedback   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ,
    UNIQUE(tool_call_id)
);

CREATE TABLE artifacts (
    id              UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(id),
    session_id      UUID NOT NULL REFERENCES sessions(id),
    name            VARCHAR(256) NOT NULL,
    artifact_type   VARCHAR(64) NOT NULL,
    path            TEXT NOT NULL,
    summary         TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE events (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES runs(id),
    event_type      VARCHAR(128) NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sessions_agent_id ON sessions(agent_id);
CREATE INDEX idx_messages_session_id ON messages(session_id);
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

## 7. API 设计

### 7.1 创建 Agent

```http
POST /api/v1/agents
```

请求：

```json
{
  "name": "Eidos",
  "description": "让想法拥有可执行的形态。",
  "system_prompt": "你是 Eidos，一个本地任务执行 Agent。你需要理解用户任务，在当前 workspace 内读取和写入文件，并在执行中高风险操作前请求审批。你必须保留清晰、可追踪的执行过程。",
  "model": "gpt-5.5",
  "temperature": 0.2,
  "max_steps": 20,
  "approval_mode": "safe"
}
```

### 7.2 创建 Session

```http
POST /api/v1/agents/{agent_id}/sessions
```

请求：

```json
{
  "title": "FastAPI demo task"
}
```

响应：

```json
{
  "id": "...",
  "agent_id": "...",
  "workspace_path": ".runtime/workspaces/{session_id}/files"
}
```

### 7.3 创建 Run

```http
POST /api/v1/sessions/{session_id}/runs
```

请求：

```json
{
  "input": "帮我创建一个 FastAPI demo，包含 /health 接口和 README。",
  "stream": true
}
```

响应：

```json
{
  "run_id": "...",
  "status": "created",
  "events_url": "/api/v1/runs/{run_id}/events"
}
```

### 7.4 SSE 事件

```http
GET /api/v1/runs/{run_id}/events?after_event_id=0
```

### 7.5 审批通过

```http
POST /api/v1/approvals/{approval_id}/approve
```

请求：

```json
{
  "approved_args": null
}
```

`approved_args = null` 表示使用原始参数。

### 7.6 审批拒绝

```http
POST /api/v1/approvals/{approval_id}/reject
```

请求：

```json
{
  "feedback": "不要执行命令，直接告诉我手动验证方式。"
}
```

### 7.7 取消 Run

```http
POST /api/v1/runs/{run_id}/cancel
```

---

## 8. 代码目录结构

```text
Eidos/
  pyproject.toml
  README.md
  docker-compose.yml
  .env.example

  app/
    main.py

    api/
      routes_agents.py
      routes_sessions.py
      routes_runs.py
      routes_approvals.py
      routes_workspace.py

    core/
      config.py
      logging.py
      errors.py

    db/
      base.py
      session.py
      models.py
      migrations/

    schemas/
      agent.py
      session.py
      run.py
      step.py
      tool.py
      approval.py
      event.py

    runtime/
      engine.py
      loop.py
      context_builder.py
      state.py
      event_store.py
      event_bus.py
      resume.py
      compactor.py

    model/
      gateway.py
      openai_compatible.py
      types.py

    tools/
      base.py
      registry.py
      list_files.py
      read_file.py
      write_file.py
      run_shell.py

    workspace/
      manager.py
      guard.py
      snapshots.py

    approval/
      manager.py
      policy.py

    services/
      agent_service.py
      session_service.py
      run_service.py
      approval_service.py

  docs/
    agent_runtime_mvp_prd.md
    agent_runtime_mvp_tdd.md

  tests/
    test_workspace_guard.py
    test_approval_policy.py
    test_runtime_state.py
    test_approval_resume.py
    test_event_replay.py
```

---

## 9. 核心代码骨架

### 9.1 Runtime Engine

```python
import logging
from uuid import UUID

logger = logging.getLogger(__name__)

class RuntimeEngine:
    def __init__(
        self,
        store,
        context_builder,
        model_gateway,
        tool_registry,
        approval_manager,
        event_store,
        tool_context_factory,
    ):
        self.store = store
        self.context_builder = context_builder
        self.model_gateway = model_gateway
        self.tool_registry = tool_registry
        self.approval_manager = approval_manager
        self.event_store = event_store
        self.tool_context_factory = tool_context_factory

    async def run(self, run_id: UUID) -> None:
        run = await self.store.get_run_for_update(run_id)
        start_step_index = await self.store.next_step_index(run_id)
        await self.store.update_run_status(run_id, "running")
        await self.event_store.publish(run_id, "run_started", {"run_id": str(run_id)})
        await self._run_loop(run_id, start_step_index)

    async def resume_after_approval(self, run_id: UUID, approval_id: UUID) -> None:
        approval = await self.store.get_pending_approval_for_update(approval_id)
        run = await self.store.get_run_for_update(run_id)

        if run.status != "waiting_approval":
            return

        tool_call = await self.store.get_tool_call_for_update(approval.tool_call_id)

        if approval.status == "approved":
            await self.store.update_tool_call_status(tool_call.id, "approved")
            tool = self.tool_registry.get(tool_call.tool_name)
            args = approval.approved_args or approval.requested_args
            await self._execute_tool(run_id, tool_call.step_id, tool_call.id, tool, args)

        elif approval.status == "rejected":
            rejected = "User rejected this tool call."
            if approval.user_feedback:
                rejected = f"{rejected} Feedback: {approval.user_feedback}"
            await self.store.save_tool_result(tool_call.id, rejected, None)
            await self.store.update_tool_call_status(tool_call.id, "rejected")
            await self.event_store.publish(run_id, "approval_rejected", {
                "approval_id": str(approval.id),
                "tool_call_id": str(tool_call.id),
            })

        await self.store.update_step_status(tool_call.step_id, "succeeded")
        await self.store.update_run_status(run_id, "running")
        next_step_index = await self.store.next_step_index(run_id)
        await self._run_loop(run_id, next_step_index)

    async def _run_loop(self, run_id: UUID, start_step_index: int) -> None:
        run = await self.store.get_run(run_id)

        try:
            for step_index in range(start_step_index, run.max_steps):
                if await self.store.is_run_canceled(run_id):
                    await self.store.update_run_status(run_id, "canceled")
                    await self.event_store.publish(run_id, "run_canceled", {})
                    return

                step = await self.store.create_step(
                    run_id=run_id,
                    step_index=step_index,
                    step_type="model",
                    status="running",
                )

                await self.event_store.publish(run_id, "step_started", {
                    "step_id": str(step.id),
                    "step_index": step_index,
                })

                model_request = await self.context_builder.build(run_id)
                model_response = await self.model_gateway.create_response(model_request)
                await self.store.save_step_model_output(step.id, model_response.model_dump_json())

                if model_response.content and not model_response.tool_calls:
                    await self.store.save_assistant_message(run.session_id, run_id, model_response.content)
                    await self.store.update_step_status(step.id, "succeeded")
                    await self.store.update_run_status(run_id, "succeeded")
                    await self.event_store.publish(run_id, "run_succeeded", {})
                    return

                for call in model_response.tool_calls:
                    tool = self.tool_registry.get(call.name)
                    definition = tool.definition()
                    tool_call = await self.store.create_tool_call(
                        run_id=run_id,
                        step_id=step.id,
                        tool_name=call.name,
                        arguments_json=call.arguments,
                        risk_level=definition.risk_level,
                        status="created",
                    )

                    await self.event_store.publish(run_id, "tool_call_created", {
                        "tool_call_id": str(tool_call.id),
                        "tool_name": call.name,
                        "arguments": call.arguments,
                    })

                    decision = await self.approval_manager.check(run, definition, call.arguments)
                    if decision.need_approval:
                        approval = await self.store.create_approval(
                            run_id=run_id,
                            tool_call_id=tool_call.id,
                            requested_args=call.arguments,
                            reason=decision.reason,
                        )
                        await self.store.link_tool_call_approval(tool_call.id, approval.id)
                        await self.store.update_tool_call_status(tool_call.id, "pending_approval")
                        await self.store.update_step_status(step.id, "waiting_approval")
                        await self.store.update_run_status(run_id, "waiting_approval")
                        await self.event_store.publish(run_id, "approval_created", {
                            "approval_id": str(approval.id),
                            "tool_call_id": str(tool_call.id),
                            "reason": decision.reason,
                        })
                        return

                    await self._execute_tool(run_id, step.id, tool_call.id, tool, call.arguments)

                await self.store.update_step_status(step.id, "succeeded")

            await self.store.update_run_status(run_id, "failed", error_message="exceeded max steps")
            await self.event_store.publish(run_id, "run_failed", {"error": "exceeded max steps"})

        except Exception as exc:
            logger.exception("agent run failed", extra={"run_id": str(run_id)})
            await self.store.update_run_status(run_id, "failed", error_message=str(exc))
            await self.event_store.publish(run_id, "run_failed", {"error": str(exc)})

    async def _execute_tool(self, run_id, step_id, tool_call_id, tool, arguments):
        await self.store.update_tool_call_status(tool_call_id, "running")
        await self.event_store.publish(run_id, "tool_call_started", {
            "tool_call_id": str(tool_call_id),
        })

        ctx = await self.tool_context_factory.create(run_id)
        try:
            result = await tool.execute(ctx, arguments)
            await self.store.save_tool_result(tool_call_id, result.content, None)
            await self.store.update_tool_call_status(tool_call_id, "succeeded")
            await self.event_store.publish(run_id, "tool_call_succeeded", {
                "tool_call_id": str(tool_call_id),
                "result": result.content[:2000],
            })
        except Exception as exc:
            logger.exception("tool execution failed", extra={"tool_call_id": str(tool_call_id)})
            await self.store.save_tool_result(tool_call_id, "", str(exc))
            await self.store.update_tool_call_status(tool_call_id, "failed")
            await self.event_store.publish(run_id, "tool_call_failed", {
                "tool_call_id": str(tool_call_id),
                "error": str(exc),
            })
```

### 9.2 Workspace Guard

```python
from pathlib import Path

SENSITIVE_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
}

SENSITIVE_SUFFIXES = {
    ".pem",
    ".key",
}

class WorkspaceGuard:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()

    def resolve(self, user_path: str) -> Path:
        target = (self.workspace_root / user_path).resolve()
        try:
            target.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(f"path escapes workspace: {user_path}") from exc

        self._check_sensitive_path(target)
        return target

    def _check_sensitive_path(self, path: Path) -> None:
        parts = set(path.parts)
        if ".ssh" in parts:
            raise PermissionError("access to .ssh is forbidden")

        if path.name in SENSITIVE_NAMES:
            raise PermissionError(f"access to sensitive file is forbidden: {path.name}")

        if path.suffix in SENSITIVE_SUFFIXES:
            raise PermissionError(f"access to sensitive suffix is forbidden: {path.suffix}")
```

### 9.3 run_shell 工具

```python
import asyncio
import logging
import os
import signal

logger = logging.getLogger(__name__)

FORBIDDEN_PATTERNS = [
    "sudo",
    "su ",
    "rm -rf /",
    "rm -rf ~",
    "mkfs",
    "dd ",
    "shutdown",
    "reboot",
    "chmod -R 777",
    "chown -R",
    "curl | sh",
    "wget | sh",
]

SAFE_ENV_KEYS = {
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
}

class RunShellTool:
    def definition(self):
        return ToolDefinition(
            name="run_shell",
            description="在当前 workspace 内执行受控 shell 命令。高风险操作，必须审批。",
            risk_level="high",
            timeout_seconds=30,
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string", "default": "."},
                    "reason": {"type": "string"},
                },
                "required": ["command", "reason"],
            },
        )

    async def execute(self, ctx: ToolContext, args: dict) -> ToolResult:
        command = args["command"].strip()
        cwd = args.get("cwd", ".")

        self._validate_command(command)
        resolved_cwd = WorkspaceGuard(ctx.workspace_root).resolve(cwd)
        env = {key: value for key, value in os.environ.items() if key in SAFE_ENV_KEYS}

        logger.info("executing shell command", extra={
            "run_id": str(ctx.run_id),
            "command": command,
            "cwd": str(resolved_cwd),
        })

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(resolved_cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=ctx.shell_timeout_seconds)
        except asyncio.TimeoutError:
            os.killpg(proc.pid, signal.SIGKILL)
            raise TimeoutError(f"shell command timeout after {ctx.shell_timeout_seconds} seconds")

        output = (
            f"exit_code: {proc.returncode}\n"
            f"stdout:\n{stdout.decode(errors='replace')}\n"
            f"stderr:\n{stderr.decode(errors='replace')}"
        )

        return ToolResult(content=output[:ctx.output_limit], metadata={"exit_code": proc.returncode})

    def _validate_command(self, command: str) -> None:
        lowered = command.lower()
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in lowered:
                raise PermissionError(f"forbidden shell command pattern: {pattern}")
```

---

## 10. 审批恢复设计

### 10.1 approve 流程

```text
POST /approvals/{id}/approve
  ↓
事务锁定 approval / run / tool_call
  ↓
仅当 approval=pending 且 run=waiting_approval 时继续
  ↓
更新 approval = approved
  ↓
更新 tool_call = approved
  ↓
执行原 tool_call 或 approved_args
  ↓
保存 tool result
  ↓
更新原 step = succeeded
  ↓
更新 run = running
  ↓
RuntimeEngine._run_loop(run_id, next_step_index)
```

### 10.2 reject 流程

```text
POST /approvals/{id}/reject
  ↓
事务锁定 approval / run / tool_call
  ↓
仅当 approval=pending 且 run=waiting_approval 时继续
  ↓
更新 approval = rejected
  ↓
更新 tool_call = rejected
  ↓
把 rejection feedback 写入 tool result
  ↓
更新原 step = succeeded
  ↓
更新 run = running
  ↓
RuntimeEngine._run_loop(run_id, next_step_index)
```

拒绝后的 tool result 示例：

```json
{
  "content": "User rejected this tool call. Feedback: 不要执行命令，直接给我手动验证方式。"
}
```

模型看到这个结果后，应重新选择方案。

---

## 11. 本地部署设计

### 11.1 docker-compose.yml

```yaml
services:
  postgres:
    image: postgres:16
    container_name: eidos-postgres
    environment:
      POSTGRES_USER: eidos
      POSTGRES_PASSWORD: eidos123
      POSTGRES_DB: eidos
    ports:
      - "5432:5432"
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U eidos -d eidos"]
      interval: 5s
      timeout: 3s
      retries: 10
```

### 11.2 .env.example

```env
APP_NAME=Eidos
APP_ENV=local
DATABASE_URL=postgresql+asyncpg://eidos:eidos123@localhost:5432/eidos
WORKSPACE_ROOT=.runtime/workspaces
MODEL_PROVIDER=openai_compatible
MODEL_BASE_URL=https://api.openai.com/v1
MODEL_API_KEY=replace_me
MODEL_NAME=gpt-5.5
DEFAULT_APPROVAL_MODE=safe
DEFAULT_MAX_STEPS=20
SHELL_TIMEOUT_SECONDS=30
TOOL_OUTPUT_LIMIT=20000
```

### 11.3 启动流程

```bash
# 1. 启动 PostgreSQL
docker compose up -d postgres

# 2. 安装依赖
uv sync

# 3. 执行迁移
alembic upgrade head

# 4. 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## 12. 日志设计

日志必须包含：

```text
run_id
session_id
step_id
tool_call_id
approval_id
event_id
event_type
status
latency_ms
error
```

---

## 13. 测试设计

### 13.1 单元测试

| 测试 | 说明 |
|---|---|
| workspace path escape | `../` 不可逃逸 |
| workspace prefix escape | `/tmp/work` 不能误放行 `/tmp/work2` |
| workspace symlink escape | workspace 内 symlink 指向外部时不可逃逸 |
| sensitive file block | `.env`、`.ssh` 不可读 |
| approval policy | 不同风险等级是否触发审批 |
| run state transition | 状态流转合法 |
| approval idempotency | approve / reject 重复请求不会重复执行 |
| shell forbidden command | 危险命令被拒绝 |

### 13.2 集成测试

| 测试 | 说明 |
|---|---|
| create run without tool | 模型直接回复 |
| create run with read_file | 工具自动执行 |
| create run with write_file | safe 模式触发审批 |
| approve shell | 审批后执行原 tool call 并从下一 step 继续 |
| reject shell | 拒绝后模型继续 |
| cancel running run | 任务可取消 |
| event replay | SSE 可以从指定 event id 回放 |

---

## 14. MVP 里程碑

### M1：基础 API + DB

- FastAPI 项目骨架
- PostgreSQL docker-compose
- SQLAlchemy models
- Alembic migrations
- Agent / Session / Run API

### M2：Runtime Loop

- Context Builder
- Model Gateway
- Runtime Engine
- Step / ToolCall 持久化
- Event Store / SSE 事件流

### M3：内置工具

- list_files
- read_file
- write_file
- run_shell
- workspace guard

### M4：审批恢复

- approval create
- approve
- reject
- resume_after_approval
- cancel run
- 幂等和并发状态转换

### M5：体验补齐

- Run Timeline API
- Workspace 文件查看 API
- Artifact 保存
- 错误展示
- 基础测试用例

---

## 15. 后续扩展预留

| 扩展 | 预留点 |
|---|---|
| MCP | ToolExecutor 抽象可以映射 MCP tool |
| Skill | Context Builder 增加 skill recall |
| Memory | Context Builder 增加 memory recall |
| 多模型 | ModelGateway 支持 provider registry |
| 多 worker | EventBus 替换为 Redis/NATS |
| Docker Sandbox | run_shell 从本机 shell 替换为容器执行 |
| Web UI | 复用 SSE + Timeline API |
| 平台资源 | 新增 KnowledgeTool / DatabaseTool 等工具 |

---

## 16. 关键设计取舍

### 16.1 为什么不用复杂工作流

MVP 的重点是 Runtime 稳定，不是编排复杂度。ReAct-style loop 更适合第一版验证。

### 16.2 为什么先不用 LangGraph

可以参考 LangGraph 的持久化和 human-in-the-loop 思想，但 MVP 自研 Runtime 更利于掌握状态机、审批、工具执行和 workspace 这些底层能力。

后续可以选择：

- 继续自研 Runtime。
- 把 Engine 替换为 LangGraph。
- 只在复杂任务里引入 LangGraph。

### 16.3 为什么 shell 必须审批

Shell 是高风险工具，可能修改文件、访问网络、泄漏敏感信息或执行破坏性命令。MVP 阶段不应该追求完全自动执行。

### 16.4 为什么选择 PostgreSQL

Agent Runtime 的核心资产是状态轨迹。PostgreSQL 能更好地承载状态持久化、JSONB、事务、索引和后续扩展。

---

## 17. 参考设计来源

- OpenAI Codex CLI：本地运行的 coding agent。
- OpenAI Codex Agent Loop：sandbox、approval mode、cwd、工具列表、prompt caching、context compaction。
- LangChain / LangGraph Human-in-the-Loop：工具调用审批、中断、持久化恢复、approve / edit / reject / respond。
