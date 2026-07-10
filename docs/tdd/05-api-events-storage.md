# API、事件与存储

版本：v0.4

## 1. API 边界

Sidecar 只监听 `127.0.0.1` 随机端口，除 health 外全部要求：

```text
Authorization: Bearer {runtime_token}
```

Runtime token 只存在于 Electron Main 和 sidecar 内存，不写磁盘、stdout、Renderer、Event 或日志。

## 2. API 清单

```text
GET    /internal/health

POST   /api/v1/workspaces
GET    /api/v1/workspaces
GET    /api/v1/workspaces/{workspace_id}
GET    /api/v1/workspaces/{workspace_id}/files
GET    /api/v1/workspaces/{workspace_id}/files/content

POST   /api/v1/sessions
GET    /api/v1/sessions
GET    /api/v1/sessions/{session_id}
PATCH  /api/v1/sessions/{session_id}/model

POST   /api/v1/sessions/{session_id}/runs
GET    /api/v1/runs/{run_id}
GET    /api/v1/runs/{run_id}/steps
GET    /api/v1/runs/{run_id}/events
POST   /api/v1/runs/{run_id}/cancel
POST   /api/v1/runs/{run_id}/user-input

POST   /api/v1/approvals/{approval_id}/approve
POST   /api/v1/approvals/{approval_id}/reject

GET    /api/v1/tool-calls/{tool_call_id}
GET    /api/v1/tool-calls/{tool_call_id}/logs

GET    /api/v1/artifacts
GET    /api/v1/artifacts/{artifact_id}
GET    /api/v1/artifacts/{artifact_id}/content

POST   /api/v1/model-profiles
GET    /api/v1/model-profiles
```

`publish_artifact` 是 Agent Tool，不是 Renderer 创建 Artifact 的 API。Renderer 只读取已发布快照。

## 3. 请求规则

### 3.1 Create Run

```json
{
  "user_input": "...",
  "idempotency_key": "renderer-generated-uuid"
}
```

- 同一 Session + idempotency_key 返回同一 Run。
- Run 创建时固化 Model Profile snapshot，创建第一个 Segment，并进入 queued。

### 3.2 Approval

Approve/Reject 请求不接受工具参数编辑：

```json
{
  "decision_nonce": "approval-card-instance-id",
  "user_feedback": "optional"
}
```

- `decision_nonce` 防止 UI 重复提交旧卡片。
- 服务端校验 Approval pending、Run waiting_approval、ToolCall 参数 hash 未变化。
- approve 仅更新状态并排队，执行器稍后复检文件和沙箱条件。

### 3.3 User Input

仅允许 Run waiting_user_input。请求追加消息、重置 Reject 计数、创建新 Segment、清除允许清除的 pause reason，并进入队尾。

### 3.4 Session Model 切换

- Session 切换 Profile 只影响后续创建的 Run，现有 Run 继续使用自身快照。
- Model Profile API 的任何响应都不返回 API Key 明文。
- MVP 只创建和读取 Profile，不提供编辑或删除 API。

## 4. 错误模型

```json
{
  "error": {
    "code": "file_version_conflict",
    "message": "Target changed after approval",
    "retryable": false,
    "details": {},
    "request_id": "..."
  }
}
```

核心错误码：

```text
invalid_state
invalid_tool_batch
approval_already_decided
approval_invalidated
file_version_conflict
path_escape
sensitive_file
sandbox_unavailable
network_host_denied
local_network_denied
tool_timeout
runtime_interrupted
reconciliation_required
context_budget_exceeded
model_auth_failed
model_not_found
model_invalid_request
model_temporarily_unavailable
model_protocol_error
hardlink_not_allowed
file_metadata_preservation_failed
```

## 5. SSE 契约

```text
GET /api/v1/runs/{run_id}/events?after_event_id=1024
```

SSE：

```text
id: 1025
event: tool_call_created
data: {"schema_version":1,"run_id":"...","tool_call_id":"...","tool_name":"read_file"}
```

- `id` 是 events 自增主键，单调递增。
- 支持 query `after_event_id` 和标准 `Last-Event-ID`，两者冲突时返回 400。
- 只推送已提交事务中的 Event。
- 重连可能重复传输最后一个 Event；Renderer 按 event id 去重。
- 业务状态不因 SSE 消费或重放而改变。

## 6. Event 类型

```text
run_created
run_enqueued
run_started
run_status_changed
segment_started
segment_paused
step_started
model_attempt_started
model_output_chunk
model_stream_interrupted
model_attempt_failed
model_response_completed
model_protocol_error
tool_batch_rejected
tool_call_intent_committed
tool_call_created
tool_call_waiting_approval
tool_call_started
tool_call_output_chunk
tool_call_completed
tool_call_failed
tool_call_interrupted
approval_created
approval_approved
approval_rejected
approval_invalidated
file_version_conflict
reconciliation_required
reconciliation_completed
user_input_added
artifact_published
network_policy_decision
finalization_started
finalization_completed
run_succeeded
run_failed
run_stopped
run_canceled
```

每个 payload 带 `schema_version`。Event payload 在写入前脱敏；大正文保存在对应 message/tool log 表，Event 只包含有界摘要和引用 id。

`model_output_chunk` 必须携带 `content_kind=assistant_progress|final_answer` 和 `incomplete`；raw reasoning 不生成 Event。流中断后追加 `model_stream_interrupted`，已提交 progress chunk 保持可回放。网络 Event 只含域名级审计元数据。

## 7. 状态表与 Events 的关系

规范化业务表是当前状态事实来源。Events 是追加式 Timeline/Outbox，不用于重建全部状态。

事务模板：

```text
BEGIN IMMEDIATE
  validate current state/version
  update domain rows
  insert event rows
COMMIT
notify in-memory SSE publisher with committed max event id
```

Event insert 失败时状态事务回滚。SSE publisher 崩溃不丢 Event，重启后从 SQLite 读取。

文件系统与 SQLite 不能组成单一事务。副作用执行前先提交 `tool_call_intent_committed`；执行后再提交结果。恢复时依据 execution nonce 和后置条件对账，禁止自动重放。

## 8. SQLite 配置

每个连接必须启用：

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
```

- Alembic 管理 schema，不使用 ORM AutoMigrate。
- 时间统一存 UTC ISO-8601 或 INTEGER epoch，项目内只能选一种。
- enum 使用 CHECK 约束。
- JSON 字段包含 schema version，写入前验证大小。
- 重要状态转换使用 `BEGIN IMMEDIATE` 和条件更新。

## 9. 表模型

### 9.1 基础表

| 表 | 关键字段/约束 |
|---|---|
| agents | id, name；MVP seed 唯一 Eidos Agent |
| model_profiles | name unique, base_url, model, api_key_ref, parameters, context/max output |
| workspaces | canonical_root_path unique, state_path, status |
| sessions | agent_id, model_profile_id, mode, workspace_id, active/state roots；mode/workspace CHECK |
| messages | session_id, run_id nullable, role, content_kind, content, metadata, created_at；禁止 raw reasoning kind |

Session 约束：

```text
mode=workspace -> workspace_id NOT NULL
mode=public    -> workspace_id IS NULL
```

### 9.2 Runtime 表

`runs`：

```text
id, session_id, agent_id, status
idempotency_key, enqueued_at, executor_lease_id
current_segment_id, total_steps, effective_elapsed_ms
max_total_steps=80, max_total_effective_seconds=7200
consecutive_rejects, consecutive_protocol_errors, reconciliation_required
pause_reason, stop_reason, error_code, error_message
model_profile_id, model_config_snapshot
started_at, finished_at, created_at, updated_at
UNIQUE(session_id, idempotency_key)
```

`execution_segments`：

```text
id, run_id, segment_index, status
step_budget=20, time_budget_seconds=1800
steps_used, effective_elapsed_ms
pause_reason, started_at, finished_at
UNIQUE(run_id, segment_index)
```

`run_steps`：

```text
id, run_id, segment_id
global_step_index, segment_step_index
status, model_response_json, error_code
started_at, finished_at
UNIQUE(run_id, global_step_index)
UNIQUE(segment_id, segment_step_index)
```

`model_attempts`：

```text
id, step_id, attempt_index, status
provider_request_id, first_delta_at, usage_json
error_code, error_message, started_at, finished_at
UNIQUE(step_id, attempt_index)
```

### 9.3 Tool 与审批

`tool_calls` 至少包含：

```text
id, run_id, step_id, batch_order
tool_name, side_effect, arguments_json, arguments_hash
status, risk_level, timeout_seconds, execution_nonce
preconditions_json, expected_postconditions_json, result_json, result_text
side_effects_may_exist, stdout/stderr sizes and truncation
exit_code, termination_reason, error_code, error_message
started_at, finished_at, created_at, updated_at
UNIQUE(step_id, batch_order)
```

`approvals` 至少包含：

```text
id, run_id, tool_call_id UNIQUE
status, decision_nonce, requested_args_hash
requested_permissions_json, diff_text
user_feedback, invalidation_reason
created_at, decided_at
```

ToolCall 不反向保存 approval_id，避免双向可空外键；通过 approvals.tool_call_id 查询。

### 9.4 日志、Artifact 与 Event

`tool_call_logs`：`tool_call_id, stream, chunk_index, redacted_content, truncated`，组合唯一。

`network_audit_logs`：`tool_call_id, host, port, decision, decision_rule, bytes_sent, bytes_received, started_at, finished_at`。禁止 URL、Header、Body 和 TLS 明文列。

`artifacts`：

```text
id, run_id, session_id
source_path, source_sha256
snapshot_path, snapshot_sha256
display_name, artifact_type, mime_type, size_bytes
version, summary, created_at
```

`events`：`id INTEGER PRIMARY KEY AUTOINCREMENT, run_id, event_type, schema_version, payload_json, created_at`，索引 `(run_id,id)`。

## 10. 可观测性

结构化日志至少携带当前可用的：

```text
request_id
session_id
run_id
segment_id
step_id
model_attempt_id
tool_call_id
approval_id
event_id
```

本地指标至少记录：

- FIFO queue wait、Run/Segment 有效执行时间。
- 模型首 delta 延迟、总耗时、重试和失败分类。
- ToolCall/Approval 耗时、timeout、cancel、interrupted 和冲突。
- Seatbelt 自检失败、策略拒绝、代理 host 拒绝和 localhost 申请。
- Event backlog、SSE reconnect 和 redaction 命中数。

日志和指标都不得包含 API Key、原始敏感命中或未脱敏 payload。

## 11. Redaction 与容量

- Message、Model response、Tool args/result/log、Event payload 和错误在持久化前统一脱敏。
- 已配置 API Key 使用 exact-match 规则，常见凭证使用 pattern rule。
- 原始敏感命中不写数据库；只保存 rule id 和 placeholder。
- API/SSE payload 有硬上限；大内容通过分页/流式 detail API 获取。
