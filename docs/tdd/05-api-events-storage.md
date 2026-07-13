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
GET    /api/v1/tool-calls/{tool_call_id}/workspace-changes

GET    /api/v1/artifacts
GET    /api/v1/artifacts/{artifact_id}
GET    /api/v1/artifacts/{artifact_id}/content

POST   /api/v1/model-profiles
GET    /api/v1/model-profiles
GET    /api/v1/model-profiles/{profile_id}
PATCH  /api/v1/model-profiles/{profile_id}
POST   /api/v1/model-profiles/{profile_id}/test-connection
POST   /api/v1/model-profiles/{profile_id}/archive
POST   /api/v1/model-profiles/{profile_id}/restore

GET    /api/v1/toolchain-profiles
POST   /api/v1/toolchain-profiles/{profile_id}/enable
POST   /api/v1/toolchain-profiles/{profile_id}/disable
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

- 服务端在写入 Message、占用 idempotency key 或创建 Run 前扫描 `user_input`。
- `deny`/`redact` 命中时返回 `sensitive_user_input_rejected`，零落库且不占用 key；用户清理后可用原 key 重试。
- 同一 Session + idempotency_key 返回同一 Run。
- 创建前复检 Session 的 Model Profile 在当前配置下最新 capability snapshot 为 `passed`；否则返回 `model_profile_not_verified`，不占用 idempotency key、不创建 Message/Run/Segment。
- Run 创建时固化 Model Profile 与 capability snapshot，创建第一个 Segment，并进入 queued。

### 3.2 Approval

Approve/Reject 请求不接受工具参数编辑：

```json
{
  "decision_nonce": "approval-card-instance-id",
  "user_feedback": "optional"
}
```

- `decision_nonce` 防止 UI 重复提交旧卡片。
- 非空 `user_feedback` 在审批状态变更前扫描；命中或扫描失败时整个 approve/reject 请求拒绝，Approval 保持 pending，用户可移除 feedback 后重试。
- 服务端校验 Approval pending、Run waiting_approval、ToolCall 参数 hash 未变化。
- approve 仅更新状态并排队，执行器稍后复检文件和沙箱条件。

### 3.3 User Input

仅允许 Run waiting_user_input。请求先在受控内存中扫描原文；命中或扫描失败时不追加 Message、不重置计数、不创建 Segment 且不改变 Run 状态。扫描通过后，一个事务内追加消息、重置 Reject 和 sensitive ToolCall 计数、创建新 Segment、清除允许清除的 pause reason，并进入队尾。

### 3.4 Model Profile 生命周期

Create 请求包含 `name, base_url, model, wire_api=responses|chat_completions, auth_mode, api_key, parameters_json, context_window_tokens, max_output_tokens`。`bearer|api_key_header` 要求非空 API Key，`none` 禁止提交 API Key。创建只保存配置，返回 `profile_version, config_revision, has_api_key, archived=false, selectable=false, final_endpoint_preview`，绝不返回密钥。

PATCH 使用 `expected_profile_version` 做条件更新：

- `name` 是唯一不改变 `config_revision/configuration_hash` 的字段。
- `base_url/model/wire_api/auth_mode/parameters/context/max_output` 或 API Key replacement 属于能力相关变更；事务内递增 `config_revision`，更新 hash 并使当前 snapshot `valid=false`。
- API Key 字段使用 `api_key_action=keep|replace|clear`，从不以占位符回传。`clear` 只允许目标认证模式为 `none`；Archive 不清除密钥。
- 任意编辑都递增 `profile_version`；版本冲突返回 `model_profile_version_conflict`，零部分更新。

Archive/restore 同样要求 `expected_profile_version`。Archive 设置 `archived_at` 并使 `selectable=false`，不删除 Profile、凭证、snapshot 或引用；restore 清除 `archived_at`，但只有当前 snapshot 仍有效时才恢复 selectable。API 不提供 DELETE endpoint。

### 3.5 Model Profile 能力探测

- 创建 Profile 只校验并保存配置，不隐式发起 Test Connection；响应标记 `selectable=false`。
- `POST /api/v1/model-profiles/{profile_id}/test-connection` 由用户显式触发，使用固定 probe 输入，不接受 task、message、workspace、artifact 或自定义 prompt 字段。
- 探测不创建 Session/Run/Step/ToolCall/Event；probe ToolCall 永不进入工具注册表或执行器。
- 每次完成都原子创建一个新的 capability snapshot；响应只返回版本、五项必需能力、stateless continuation、可选 `websocket_transport`、固化 output token parameter、时间和安全错误码，不返回 API Key、Provider 原始响应或 probe 正文。
- Archived Profile 不可探测。只有非 Archived Profile 在当前配置、Gateway contract 和 model request contract 下最新 snapshot `probe_status=passed, valid=true` 时，Profile 列表才返回 `selectable=true`。

### 3.6 Session Model 切换

- Session 切换 Profile 只影响后续创建的 Run，现有 Run 继续使用自身快照。
- 创建 Session 或切换 Profile 时，服务端要求目标 Profile `selectable=true`；否则返回 `model_profile_not_verified`，原 Session 配置不变。
- Model Profile API 的任何响应都不返回 API Key 明文。

### 3.7 Toolchain Profile

- 列表只返回固定系统根和发现到的 `/opt/homebrew|/usr/local`，MVP 不接受 Renderer 提交任意路径。
- enable/disable 要求受信用户手势 nonce，事务内递增 `profile_version` 和全局 `shell_environment_version`。
- 变更后使所有引用旧 environment version 的 pending/approved Shell Approval invalidated，不影响已启动进程。

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
path_spelling_mismatch
ambiguous_path
unsupported_filename_encoding
excluded_path
file_too_large_for_read_file
file_changed_during_read
directory_changed_during_list
invalid_line_range
line_too_large
binary_file_not_supported
unsupported_text_encoding
invalid_search_query
approval_diff_too_large
file_too_large_for_safe_write
parent_directory_not_found
full_overwrite_requires_complete_read
patch_read_evidence_missing
invalid_unified_diff
mixed_newlines_not_supported_for_patch
mixed_newlines_not_supported_for_write
newline_style_mismatch
invalid_new_file_newline_style
file_owner_not_supported
file_flags_not_supported
sensitive_file
sensitive_content_denied
sensitive_scan_incomplete
sensitive_scan_limit_exceeded
sensitive_scan_failed
sensitive_user_input_rejected
sensitive_tool_input
sensitive_structured_payload_rejected
redaction_ruleset_downgrade
sandbox_unavailable
network_host_denied
local_network_denied
tool_timeout
runtime_interrupted
runtime_contract_unsupported
reconciliation_required
context_budget_exceeded
context_input_too_large
model_credential_unavailable
model_auth_failed
model_not_found
model_invalid_request
model_profile_not_verified
model_profile_archived
model_profile_version_conflict
model_profile_invalid_url
model_profile_endpoint_in_base_url
model_profile_reserved_parameter
model_profile_token_limits_invalid
model_capability_probe_failed
model_capability_drift
model_usage_unsupported
model_output_limit_parameter_unsupported
model_stateless_mode_unsupported
model_context_limit_mismatch
model_tls_validation_failed
model_temporarily_unavailable
model_protocol_error
model_protocol_limit_exceeded
model_output_limit_exceeded
model_output_truncated
model_output_blocked
repeated_sensitive_tool_input
hardlink_not_allowed
file_metadata_preservation_failed
artifact_format_not_supported
artifact_corrupted
approval_expired
workspace_preflight_limit_exceeded
toolchain_root_changed
shell_resource_limits_unavailable
output_capture_failed
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
model_transport_retrying
model_transport_fallback
model_output_chunk
model_output_stopped
model_stream_interrupted
model_attempt_failed
model_response_completed
model_protocol_error
tool_batch_rejected
sensitive_tool_input_rejected
sensitive_scan_failed
redaction_ruleset_changed
tool_call_skipped
tool_call_intent_committed
tool_call_created
tool_call_waiting_approval
tool_call_started
tool_call_output_chunk
shell_manifest_started
shell_manifest_completed
shell_manifest_incomplete
shell_resource_limit_exceeded
tool_call_completed
tool_call_failed
tool_call_interrupted
approval_created
approval_approved
approval_rejected
approval_invalidated
approval_expired
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

`model_output_chunk` 必须携带 `content_kind=assistant_progress|final_answer` 和 `incomplete`；raw reasoning 不生成 Event。流中断后追加 `model_stream_interrupted`；token 截断、内容过滤或 Runtime 流上限追加 `model_output_stopped`，payload 只含固定 reason、已提交字节数和 limit category。已提交 progress chunk 保持可回放。`model_transport_retrying|model_transport_fallback` 只包含 attempt index、transport、安全错误分类和有界 delay，不包含 endpoint path/query、Header 或 Provider 原始正文。网络 Event 只含域名级审计元数据。敏感相关 Event 只允许 ruleset version、rule id/version、action、命中数和安全字段路径/行号，不允许原值、长度、摘要或哈希。

Shell `tool_call_output_chunk` 必须携带 `chunk_index, stream_index, stream, captured_at, redacted, truncated, tail_replay`。`chunk_index` 在单 ToolCall 内全局单调，`stream_index` 在 stdout/stderr 各自单调。回放只返回最终持久化 head/省略标记/tail，不恢复已丢弃中间正文。

`tool_call_skipped` 只对零字节变化的 write/apply 生成，payload 包含 `result_code=no_changes, path, base_sha256`，不得伪造 approval/intent id。Shell manifest Event 只携带计数、完整性、安全引用 id 和有界路径摘要。

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
| security_metadata | singleton id, highest_ruleset_generation, last_ruleset_version, updated_at；用于防止应用回滚降级敏感规则 |
| runtime_settings | singleton id, shell_environment_version, gateway_contract_version, current_model_request_contract_version, updated_at |
| toolchain_profiles | id, fixed_kind, name, canonical_root, dev, inode, bin_dirs_json, enabled, profile_version, enabled_at, invalidated_at；canonical_root 仅允许预定义候选 |
| model_profiles | id, name unique, base_url, model, wire_api, auth_mode, api_key_ref UNIQUE nullable, credential_revision, parameters_json, context_window_tokens, max_output_tokens, profile_version, config_revision, configuration_hash, archived_at, created_at, updated_at；不保存 API Key |
| model_profile_capability_snapshots | id, profile_id, snapshot_version, configuration_hash, gateway_contract_version, model_request_contract_version, probe_status, valid, authentication, model_exists, streaming, tool_call, usage, stateless_continuation, websocket_transport, output_token_parameter, error_code, invalidation_reason, checked_at, invalidated_at；`UNIQUE(profile_id,snapshot_version)` |
| model_transport_health | profile_id, configuration_hash, snapshot_version, ws_disabled, disabled_reason, disabled_at；`PRIMARY KEY(profile_id,configuration_hash,snapshot_version)`，无 TTL |
| workspaces | canonical_root_path unique, state_path, status |
| sessions | agent_id, model_profile_id, mode, workspace_id, active/state roots；mode/workspace CHECK；创建/切换时目标 Profile 必须非 Archived 且存在当前配置的 valid passed snapshot |
| messages | session_id, run_id nullable, role, content_kind, content, metadata, ruleset_version, created_at；禁止 raw reasoning kind |

Profile 凭证保存在 `~/.eidos/config.toml` 的 profile-id 专属槽位中，包含 `api_key` 与单调 `credential_revision`。一个槽位只能被同 id Profile 引用，禁止共享引用。替换/清除使用 mode `0600` 的临时文件、fsync 和原子 replace；随后提交 DB 配置版本与 snapshot 失效事务。若进程在两者之间崩溃，启动时发现 config/DB credential revision 不一致即 fail closed：同步到较新的 revision、使 snapshot 失效，但不回滚或回显密钥。

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
consecutive_rejects, consecutive_protocol_errors, consecutive_sensitive_tool_inputs
reconciliation_required, ruleset_version_snapshot
pause_reason, stop_reason, error_code, error_message
model_profile_id, model_capability_snapshot_id, model_config_snapshot
started_at, finished_at, created_at, updated_at
UNIQUE(session_id, idempotency_key)
```

`model_config_snapshot` 必须内嵌创建 Run 时的非密钥 Profile 配置、wire API、auth mode、创建时 credential revision、`configuration_hash`、capability snapshot ID/version、Gateway/model request contract version、必需能力、stateless continuation、可选 WebSocket 和 output token parameter。`model_capability_snapshot_id` 只用于审计关联；运行时不得通过该外键读取更新后的能力替代内嵌快照。每次发送仍从 Profile 当前凭证槽读取密钥，并把实际 credential revision 写入 ModelAttempt，不修改该快照。

Profile 不允许硬删除；Session/Run/snapshot 外键均使用 `ON DELETE RESTRICT`。Gateway 或 model request contract version 升级时，启动事务把旧 version 的当前 passed snapshot 设置为 `valid=false, invalidation_reason=gateway_contract_changed|model_request_contract_changed`。运行时 `model_capability_drift|model_context_limit_mismatch` 使 Run failed 的同一数据库事务也使对应 Profile 当前 snapshot `valid=false`，但不修改 Run 内嵌快照。启动时非终态 Run 引用 unsupported request contract 时不执行，原子进入 `waiting_user_input/runtime_contract_unsupported`。

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
model_cycle_started_at, model_cycle_deadline_at
started_at, finished_at
UNIQUE(run_id, global_step_index)
UNIQUE(segment_id, segment_step_index)
```

敏感 ToolCall 被拒绝时 `model_response_json` 只保存经扫描的普通文本与结构化拒绝摘要，不保存原始 ToolCall 参数或 provider response。

`model_attempts`：

```text
id, step_id, logical_model_request_id, attempt_index, status
wire_api = responses|chat_completions
transport = websocket|http_stream
credential_revision, request_max_output_tokens, output_token_parameter, retry_reason
provider_request_id, first_delta_at
visible_text_bytes, discarded_reasoning_bytes, stream_payload_bytes
usage_status = reported|unknown, usage_json
error_code, error_message, started_at, finished_at
UNIQUE(step_id, attempt_index)
```

同一逻辑请求的每次真实网络发送各占一行；WebSocket 到 HTTP(S) 的重放不得复用 Attempt 行。Run usage 汇总只累加 `usage_status=reported` 的结构化值，并同时返回 `attempt_count` 与 `unknown_usage_attempt_count`，不得把 unknown 转成零。`logical_model_request_id` 是 Eidos 内部关联 ID，不作为厂商幂等承诺。

### 9.3 Tool 与审批

`tool_calls` 至少包含：

```text
id, run_id, step_id, batch_order
provider_call_id, tool_name, side_effect, arguments_json, arguments_hash
status, risk_level, timeout_seconds, execution_nonce
preconditions_json, expected_postconditions_json, result_json, result_text
candidate_sha256, read_result_ids_json
side_effects_may_exist, change_manifest_incomplete
stdout_original_bytes, stderr_original_bytes
stdout_retained_bytes, stderr_retained_bytes
stdout_truncated, stderr_truncated
exit_code, termination_reason, limit_kind, error_code, error_message
started_at, finished_at, created_at, updated_at
UNIQUE(step_id, batch_order)
UNIQUE(step_id, provider_call_id)
```

`approvals` 至少包含：

```text
id, run_id, tool_call_id UNIQUE
status, decision_nonce, requested_args_hash
requested_permissions_json, diff_text
diff_sha256, diff_size_bytes, diff_line_count
environment_version, approved_at, approval_expires_at
user_feedback, invalidation_reason
created_at, decided_at
```

ToolCall 不反向保存 approval_id，避免双向可空外键；通过 approvals.tool_call_id 查询。

`approval_expires_at` 只对 Shell Approval 非空。数据库 `CHECK`/应用 enum 必须覆盖 ToolCall 的 `skipped` 与 Approval 的 `pending|approved|rejected|invalidated|canceled`；`approval_expired` 是 invalidated 的原因和 ToolCall 错误码，不新增可歧义的 Approval 状态。

`file_read_results`：

```text
id, tool_call_id UNIQUE, run_id, path, base_sha256
size_bytes, encoding, bom, complete, content_redacted
created_at, invalidated_at
```

`file_read_ranges`：

```text
read_result_id, range_index, start_line, end_line, range_kind
PRIMARY KEY(read_result_id, range_index)
```

- `read_result_id` 外键指向 file_read_results 并 `ON DELETE CASCADE`；范围行号满足 `start_line >= 1 AND end_line >= start_line`，`range_index` 从 0 递增。
- 完整空文件读取使用 `complete=true` 且零 range rows，不与“没有读取”混淆。
- `complete=true` 只能由 <=256 KiB 的 `read_file` 产生；`read_file_range` 即使恰好覆盖全文件也保持 `complete=false`，不授予 write_file 完整覆盖资格。
- head+tail 产生两个 `range_kind=head|tail` 的实际行区间；被省略中间区域不记为证据。发生脱敏命中的整行也从 range rows 中扣除，必要时将一个实际返回范围拆成多个证据区间。
- write_file 完整覆盖必须引用 `complete=true, content_redacted=false`、size <=256 KiB、run/path/hash 一致且未 invalidated 的 read_file 结果。
- apply_patch 在创建 Approval 前将 `read_result_ids_json` 引用解析为行区间并集并验证所有 hunk；证据跨 Run/path/hash 或已失效均拒绝。
- write/apply/delete 成功的结果事务使同一 `run_id + path + old_sha256` 的读取结果失效。

文件 Approval 的 `preconditions_json` 保存版本化 schema，至少包含规范化 path、父目录链身份、expected_absent 或 base type/size/hash/encoding/BOM/link count、读取证据引用和 candidate hash。`diff_sha256` 同时覆盖 UI 展示的完整 Diff；approve 时与 `requested_args_hash` 一起复检，禁止用截断或重生成 Diff 替换原审批内容。

### 9.4 日志、Artifact 与 Event

`tool_call_logs`：`tool_call_id, chunk_index, stream, stream_index, captured_at, redacted_content, byte_count, truncated, tail_replay`。`UNIQUE(tool_call_id,chunk_index)` 保证交错序，`UNIQUE(tool_call_id,stream,stream_index)` 保证流内序。省略标记是显式日志行，不是未持久化 UI 推测。

`shell_manifests`：

```text
id, tool_call_id, execution_nonce, phase=before|after
state_path, manifest_sha256, status, entry_count
protected_path_change_count, git_boundary_change_detected
logical_bytes, allocated_bytes, duration_ms, created_at
UNIQUE(tool_call_id, phase)
```

`shell_workspace_changes`：

```text
id, tool_call_id, change_index, path, change_type
before_metadata_json, after_metadata_json, after_sha256 nullable
logical_delta, allocated_delta
UNIQUE(tool_call_id, change_index)
```

- `state_path` 是 Eidos 管理根下的受控相对路径，不对 Renderer 返回。manifest file 使用版本化 JSONL schema、mode `0600`，先脱敏再持久化。
- 敏感路径和 `.git` 详细条目不进入 `shell_workspace_changes`；只保存聚合/安全异常字段。
- 完整结果事务成功后删除 manifest file 并将 `state_path=NULL,status=committed`；变化摘要表保留。崩溃恢复依据 running intent 保留文件。

`network_audit_logs`：`tool_call_id, host, port, decision, decision_rule, bytes_sent, bytes_received, started_at, finished_at`。禁止 URL、Header、Body 和 TLS 明文列。

`redaction_audit_logs`：`run_id nullable, step_id nullable, source_kind, ruleset_version, rule_id, rule_version, action, safe_location, hit_count, created_at`。`safe_location` 只能保存字段路径或文件行号；表中不得出现原值、长度、摘要、哈希或关联 token。

`artifacts`：

```text
id, run_id, session_id
source_path, source_sha256
snapshot_path, snapshot_sha256
display_name, artifact_type, mime_type
logical_size_bytes, allocated_size_bytes, encoding, bom
version, summary, status, created_at
```

Artifact `status=available|corrupted`。每次 content API 在当前敏感规则扫描前先复检 snapshot SHA-256；不一致时原子标记 corrupted 并返回零正文错误。`UNIQUE(session_id, source_path, version)`，同一 session/source_path 的 version 从 1 单调递增。

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
- 模型首 delta 延迟、request cycle 总耗时、分 transport 的 Attempt/重试/降级和失败分类，以及 reported usage 与 unknown usage Attempt 数。
- Model Profile Test Connection 各能力结果、snapshot 失效原因、Archive/恢复、TLS/Redirect/保留参数拒绝和 context mismatch；指标不含 base_url query、API Key 或 Provider 原始错误正文。
- ToolCall/Approval 耗时、timeout、cancel、interrupted 和冲突。
- Seatbelt 自检失败、策略拒绝、代理 host 拒绝和 localhost 申请。
- Event backlog、SSE reconnect 和 redaction 命中数。
- Redaction ruleset 自检状态、扫描字节/耗时、超限、失败、分级命中和敏感 ToolCall 暂停数。

日志和指标都不得包含 API Key、原始敏感命中或未脱敏 payload。

## 11. Redaction 与容量

- Message、Model response、Tool args/result/log、Event payload 和错误在持久化前统一执行结构感知扫描。
- 字符串叶子中的 `redact` 使用 `[REDACTED:<rule_id>]`；非叶子结构命中时整个 payload 拒绝，不为了落库破坏 schema。
- 持久化最后屏障如仍发现 `deny`，视为上游安全不变式被破坏，拒绝整个 payload 并回滚所属事务；不把高置信度原文改写后继续落库。
- 原始敏感命中不写数据库；只保存 ruleset/rule id、action、命中计数、安全位置和 placeholder。
- 写入前的持久化扫描是独立最后屏障，不能因上游已扫描而跳过。
- Run 快照保存创建时 `ruleset_version`；应用升级后的首次恢复事务先写入 `redaction_ruleset_changed` Event，再将 Run 入队。
- Message、Event 回放、Tool log、Artifact 和其他历史正文的读 API 在响应前按当前规则再扫描。`deny` 返回无正文的结构化拒绝，`redact` 只影响当次响应；MVP 不就地改写追加式 Event 或不可变 Artifact。
- Event 回放不得因读时扫描跳过 event id。字符串叶子命中时在当次响应内脱敏；结构 token 命中时保留原 `id` 和 `event_type`，将 payload 整体替换为符合 Event envelope schema 的 `content_unavailable/sensitive_structured_payload_rejected` 安全载荷。
- API/SSE payload 有硬上限；大内容通过分页/流式 detail API 获取，但分页不得绕过全文件扫描。
