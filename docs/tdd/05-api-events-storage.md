# API、事件与存储

版本：v0.4

范围说明：本文描述完整目标态 HTTP/SSE/API/存储契约。第一期改用 [MVP Lite](../mvp-lite.md) 定义的 stdio JSON-RPC 双向协议和四类最小业务表。

MVP Lite 当前实施状态：

- ✅ Runtime 初始化并打开 SQLite，数据目录/数据库分别校验 `0700/0600`、owner、普通文件类型和 symlink 边界；MVP Lite Desktop 由 Electron single-instance lock 阻止第二个 sidecar，完整目标态的状态目录 OS lock 仍按后文延期。
- ✅ `session/create` 使用参数化 SQL 持久化 canonical Workspace identity；`session/list` 使用 opaque cursor；`session/read` 返回有界 Run/Item 页面。
- ✅ Run、Item、ToolCall 四类最小业务事实、单活动 Run 约束、模型步数、ToolCall 关联、Item 完成与启动 `interrupted` 收敛已实现。
- ✅ TypeScript Main Client 已通过真实子进程、通知路由、Fake Model 两轮工具循环和跨 Runtime 重启持久化测试；测试使用隔离数据根，不触碰真实 `~/.eidos`。
- ✅ JSON-RPC 业务错误保留闭合 code，经 Main/Preload 后映射为安全 Renderer 提示，不透传 Python、Provider 或 OS 原始错误。
- ⏳ 完整目标态迁移、事件表、复杂恢复与跨版本兼容仍未实现。

## 1. API 边界

Sidecar 只监听 `127.0.0.1` 随机端口，除 health 外全部要求：

```text
Authorization: Bearer {runtime_token}
```

Runtime token 只存在于 Electron Main 和 sidecar 内存，不写磁盘、stdout、Renderer、Event 或日志。

`GET /internal/health` 的安全响应是闭合 object，包含 `runtime_build_id,phase,stage,reason_code,api_contract_version,event_contract_version,capabilities`；ToolResult capability 为 `available|degraded|unavailable`，active quarantine 只含 `scope,tool_contract_version,tool_name,reason_code,detected_build_id`。storage/clock/migration 原因同样只用闭合 code，不返回 projector exception、result bytes、SQL、路径内容或原始错误。Tool scope 为 degraded，global scope 为 unavailable。该 endpoint 只报告状态，不提供清除 quarantine、释放 reserve 或恢复写操作。

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
GET    /api/v1/sessions/{session_id}/runs

POST   /api/v1/sessions/{session_id}/runs
GET    /api/v1/runs/{run_id}
GET    /api/v1/runs/{run_id}/snapshot
GET    /api/v1/runs/{run_id}/steps
GET    /api/v1/runs/{run_id}/events
POST   /api/v1/runs/{run_id}/cancel
POST   /api/v1/runs/{run_id}/user-input

POST   /api/v1/approvals/{approval_id}/approve
POST   /api/v1/approvals/{approval_id}/reject
GET    /api/v1/approvals/{approval_id}

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

### 2.1 闭合 DTO 与分页

`/api/v1` 的 request、success response 和每个 API error code 的 `details` 都使用递归闭合 schema；Pydantic 固定 `extra=forbid`，每个 endpoint 在 JSON 解析前强制自己的 body byte cap。禁止返回 ORM row、自由 map、未声明字段或因数据库新增列而扩大响应。

OpenAPI 是 HTTP DTO 的唯一来源，生成 Python validator、TypeScript type 与 runtime validator。Preload 只暴露命名方法；方法名可使用 TypeScript 风格，但 payload 保持相同字段语义。Main 必须验证 sidecar success/error 后才投影给 Renderer；未知字段、类型或 enum 固定 `runtime_contract_mismatch`，不得把原响应透传。

普通分页统一：

```text
request: limit=50 (1..200), cursor?
response: {items:[closed item DTO], next_cursor?}
```

无下一页时省略 cursor，不返回 null。小而固定的 Toolchain/capability 集合可使用声明上限的非分页数组。cursor 是 Renderer 只能透传的版本化 base64url canonical object，内部绑定 `cursor_version,endpoint,normalized_scope_hash,normalized_filter_hash,order,collection_high_water,last_key`；不含用户正文或权限。任一字段、endpoint、scope、filter、order 或版本不匹配返回 `invalid_cursor`。服务端每页重新鉴权，cursor 不是授权。

所有可分页资源集合使用不可复用的内部单调 `creation_seq`；首请求冻结 `collection_high_water=MAX(creation_seq)`，后续查询固定 `creation_seq<=high_water` 并以 `creation_seq DESC` keyset 翻页。水位限定成员集合，last key 只负责翻页；公共 DTO 不必暴露 sequence。Run Step/Event/Shell log/Workspace change 分别复用 `global_step_index/event_id/chunk_index/change_index` 的单调 high-water 和 ASC keyset。禁止 offset、`created_at` 或 UUID 排序来声称稳定成员集合。Q146 snapshot/SSE 继续使用专用 `through_event_id`。

详情正文不塞入 list item：Approval Diff 因固定 512 KiB/5,000 行上限整份返回；Artifact content、Shell log 与完整 Workspace changes 使用各自有界详情/流接口。更新/删除仍不宣称跨页数据库 snapshot；cursor 失效后客户端从第一页重取。

## 3. 请求规则

### 3.0 持久化 operation 幂等

所有 Renderer 发起且修改 sidecar 持久状态的 POST/PATCH 必须携带 `Idempotency-Key: {canonical UUID}`；Preload 对应参数名为 `operationId`。Create Run 原 body `idempotency_key` 即迁移为此统一 key，不再同时保留第二个字段。GET/SSE、系统文件夹选择和 `openInSystemTerminal` 不进入该表，Main 不得自动把一次用户手势重试成外部动作。

在 schema、鉴权、权限和敏感扫描通过后，服务端构造版本化 canonical operation envelope：

```text
operation_contract_version, operation_kind, HTTP method, route template,
normalized workspace/resource scope, canonical validated body
```

hash 排除 operation ID 本身，但包含静态 default 后的全部语义输入。全局同 operation ID + 同 envelope hash 返回首次已提交的原 HTTP status 和闭合安全 body，零重复状态/Event；同 ID + 不同 hash 返回 `idempotency_key_reused`。每次重放仍重新鉴权，不允许 cached response 跨权限泄露。`decision_nonce`、`expected_*_version`、user-gesture nonce 和 Run 状态复检保留原语义，operation ID 不替代它们。

纯 SQLite 写把 operation completed record、领域状态、Event 与安全 response snapshot 放在同一事务。验证/鉴权/敏感拒绝不占 key、不保存 payload/hash，保持 Q57。operation record 不保存请求正文，只保存 contract/kind/scope/request hash、闭合响应和资源引用；MVP 不自动清理，计入存储统计。

Test Connection 等包含外部网络的 operation 先持久化 in-progress intent。崩溃后无法确认是否已发出/完成时，同 ID 固定返回 `operation_interrupted`，不得自动重发；用户再次明确点击生成新 ID。该本地机制不承诺 Provider、Shell 或文件系统 exactly-once。Cancel 的领域语义仍幂等；Approval 已决定/失效仍优先返回 `approval_already_decided|approval_invalidated`。

### 3.1 Create Run

```json
{
  "user_input": "..."
}
```

- 服务端在写入 Message、占用 idempotency key 或创建 Run 前扫描 `user_input`。
- `deny`/`redact` 命中时返回 `sensitive_user_input_rejected`，零落库且不占用 key；用户清理后可用原 key 重试。
- 同一 Create Run operation 按 3.0 返回同一 Run。
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
- 非空 `user_feedback` 必须为 1..4,096 UTF-8 bytes，并在审批状态变更前扫描；超限、命中或扫描失败时整个 approve/reject 请求拒绝，Approval 保持 pending，用户可移除 feedback 后重试。Reject 成功时该原文只进入 `approval_rejected` 的闭合 ToolResult data，不进入 summary。
- 服务端校验 Approval pending、Run waiting_approval、ToolCall 参数 hash 未变化。
- approve 仅更新状态并排队，执行器稍后复检文件和沙箱条件。

### 3.3 User Input

仅允许 Run waiting_user_input。请求先在受控内存中扫描原文；命中或扫描失败时不追加 Message、不重置计数、不创建 Segment 且不改变 Run 状态。扫描通过后，一个事务内追加消息、重置 Reject 和 sensitive ToolCall 计数、创建新 Segment、清除允许清除的 pause reason，并进入队尾。

### 3.3.1 RunSnapshot、Approval detail 与 allowed actions

`GET /api/v1/runs/{run_id}/snapshot` 在同一 SQLite read transaction 中读取规范化当前状态，并返回闭合 `RunSnapshot v1` 与该事务可见的本 Run `through_event_id=MAX(events.id)`；无 Event 时为 `0`。准确 schema 为：

```text
{
  schema_version = 1,
  through_event_id: nonnegative integer,
  run: {
    run_id, session_id, workspace_id?, model_profile_id,
    status = created|queued|running|waiting_approval|waiting_user_input|
             finalizing|succeeded|failed|stopped|canceled,
    pause_reason?, stop_reason?, error_code?,
    reconciliation_required: boolean,
    reconciliation_epoch: nonnegative integer
  },
  active_segment?: {
    segment_id, segment_index: positive integer,
    steps_used: nonnegative integer, max_steps: positive integer,
    effective_elapsed_ms: nonnegative integer,
    max_effective_ms: positive integer
  },
  current_step?: {
    step_id, global_step_index: positive integer,
    segment_step_index: positive integer
  },
  active_tool_call?: { tool_call_id, tool_name },
  run_budget: {
    steps_used: nonnegative integer, max_steps: positive integer,
    effective_elapsed_ms: nonnegative integer,
    max_effective_ms: positive integer
  },
  allowed_actions: [closed action enum],
  pending_approval?: ApprovalSummaryV1
}
```

所有 object 递归 `additionalProperties=false`，所有 id 为 canonical UUID，所有 string 有对应 enum/长度上限；不使用 null，不适用的 optional 字段省略。`workspace_id` 仅 Public Mode 省略。active fields 只在对应规范化当前行存在且一致时出现；任何悬空/多 active 行都是 snapshot invariant error。`pause_reason|stop_reason|error_code` 必须来自各自闭合 registry，不携带动态 message。Snapshot 不内嵌 Timeline body、完整 Diff、日志或 manifest；详情使用有界专用 API。

`ApprovalSummaryV1` 固定为：

```text
approval_id, tool_call_id, status=pending, decision_nonce, tool_name,
created_at, risk_level, requested_permissions, safe_target_summary,
diff_sha256?, diff_size_bytes?, diff_line_count?, detail_ref
```

其中 `status` 只能是 pending，`risk_level=low|medium|high`；`requested_permissions` 是按枚举序稳定排序、无重复的 `workspace_write|shell_execution|external_network|local_network` 数组；`safe_target_summary` 是已扫描单行 UTF-8、最大 1,024 bytes；`detail_ref` 必须精确指向当前 approval id 的同 Origin 相对 API resource，不能是任意 URL。三个 diff 字段对文件 Approval 同时存在、其他 Approval 同时省略。`created_at` 使用 Q154 的 ApiTimestampV1 UTC Unix 毫秒整数，禁止端点替换为 ISO 字符串。

`GET /api/v1/approvals/{approval_id}` 是完整命令/Diff、effective arguments、preconditions、读取证据、环境和限制的权威来源；文件 Diff 因已有 512 KiB/5,000 行上限而整份返回。若其他详情采用分页，Renderer 必须取得全部页并验证内容 hash 后才启用 Approve。详情只有在相同 `approval_id+decision_nonce` 仍 pending 时才能安装；Approval 不设置 pending expiry，Shell 的五分钟 expiry 仍从 approve 成功开始。

Renderer 原子替换 snapshot 后，只从 `through_event_id` 之后订阅 SSE；状态变更与 Event 同事务提交保证无缺口。未知 Event schema version 停止增量并重新取 snapshot；未知 Snapshot schema version 显示兼容错误，不得循环重试。Run 不存在返回 `run_not_found`，snapshot invariant 失败返回安全错误而非部分 snapshot。终态 Run 返回 200、active/pending 字段省略且 `allowed_actions=[]`，无需建立 SSE。

`allowed_actions` v1 只允许按字典序排列的 `approve_pending_approval|cancel_run|reject_pending_approval|submit_user_input`：

- queued/running：`cancel_run`。
- waiting_approval 且当前唯一 Approval 仍 pending、nonce/Run 匹配：approve、cancel、reject。
- waiting_user_input 的可恢复 reason：submit、cancel。精确白名单为 `segment_step_limit|segment_time_limit|model_protocol_error|repeated_sensitive_tool_input|repeated_approval_rejection|model_temporarily_unavailable|model_stream_interrupted|model_output_truncated|model_output_blocked|model_output_limit_exceeded|sensitive_scan_failed|runtime_interrupted|reconciliation_required`；Run flag 为 reconciliation 时副作用 gate 仍保持。
- `workspace_unavailable` 在用户显式恢复并重新验证 Workspace 身份前只有 cancel；恢复后才允许 submit。`runtime_contract_unsupported` 只有 cancel；未知 pause reason fail closed。
- created/finalizing/终态：空。对已 canceled Run 重复 cancel 仍按既有规则幂等成功；其他终态拒绝。

Snapshot action 只是 affordance。Cancel/User Input/Approve/Reject 都必须在写事务内重算相同谓词；通用状态不满足返回 `action_not_allowed` 与当前水位。Approval 已决定/失效的并发竞态继续返回更精确的 `approval_already_decided|approval_invalidated`。

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
- 每次完成都原子创建一个新的 capability snapshot；响应只返回版本、必需能力（含工具控制、Tool Schema Dialect 接受和 ToolCall/ToolResult 关联）、stateless continuation、可选 `websocket_transport`、固化 output token parameter、时间和安全错误码，不返回 API Key、Provider 原始响应或 probe 正文。
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

该 envelope 只服务 Renderer/HTTP 客户端，不进入模型上下文；它与 canonical ToolResult Error 独立。`details` 必须按 API code 使用闭合、有界、安全 schema，禁止动态 map；UI 只按 code 本地化和决定动作。ToolResult error 不复用这里的 `message|retryable|details|request_id`，只使用 outcome/code/固定 summary/code 专属 data。Provider/OS 原始错误正文、stack 与 errno 即使仅用于内部诊断，也必须先安全映射或经过扫描、限长和访问隔离。

核心错误码：

```text
invalid_state
action_not_allowed
runtime_not_ready
runtime_already_active
run_not_found
runtime_contract_mismatch
invalid_cursor
idempotency_key_reused
operation_in_progress
operation_interrupted
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
directory_changed_during_search
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
workspace_unavailable
workspace_identity_changed
workspace_volume_identity_unsupported
workspace_storage_exhausted
storage_unavailable
clock_rollback_detected
tool_result_contract_violation
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
model_parallel_tool_calls_unsupported
model_tool_control_unsupported
model_tool_schema_mode_unsupported
model_usage_unsupported
model_output_limit_parameter_unsupported
model_stateless_mode_unsupported
model_context_limit_mismatch
model_tls_validation_failed
model_temporarily_unavailable
model_protocol_error
model_protocol_limit_exceeded
tool_unavailable
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
workspace_change_manifest_incomplete
shell_resource_limit_exceeded
shell_process_signaled
shell_exit_nonzero
artifact_source_changed
```

## 5. SSE 契约

```text
GET /api/v1/runs/{run_id}/events?after_event_id=1024
```

SSE：

```text
id: 1025
event: tool_call_created
data: {"event_id":1025,"run_id":"...","event_type":"tool_call_created","schema_version":1,"created_at":1780000000000,"payload":{"tool_call_id":"...","tool_name":"read_file"}}
```

- `id` 是 events 自增主键，单调递增。
- 支持 query `after_event_id` 和标准 `Last-Event-ID`，两者冲突时返回 400。
- 只推送已提交事务中的 Event。
- 重连可能重复传输最后一个 Event；Renderer 按 event id 去重。
- 业务状态不因 SSE 消费或重放而改变。

## 6. Event 类型

Event API envelope v1 固定为闭合 object：

```text
event_id, run_id, event_type, schema_version, created_at, payload
```

`event_type` 是最大 128 bytes 的受限 snake_case；`schema_version` 表示 `(event_type,schema_version)` 对应的递归闭合 payload 版本；`created_at` 是 ApiTimestampV1。SSE `id/event` 必须与 data 的 `event_id/event_type` 相同。Event schema 不原地改变字段或语义；任何变化发布新 payload version。写入、REST、SSE、Main 和 Reducer 使用同源 registry/validator。

Event 只承载 Timeline/增量；RunSnapshot、规范化业务表和详情 API 永远是当前事实。Approval、allowed actions、Run/ToolCall 终态或安全 gate 不得只存在于 Event。Reducer 按 event id 升序处理；id 为全局序列，同一 Run 允许跳号。`id<=watermark` 的重复无条件忽略，不比较 wire payload：当前 ruleset 的读时脱敏/整体替代可以合法改变同一持久 Event 的响应字节。

`event_contract_version` 由 ready 握手实际比较。只有 contract 明确声明“同一兼容版本中新增 type 可忽略，且不参与旧 Reducer 正确重建规范化状态”时，未知 `event_type` 才可前向兼容：Main 丢弃未知原 payload，生成闭合 `{kind=unsupported_event,event_type}` 安全占位，Renderer 显示后推进水位。旧客户端必须理解的状态语义变化必须提升不兼容 event contract 并在业务 IPC/SSE 开放前阻断。

已知 type 的未知 `schema_version` 或 payload 校验失败时，Reducer 停止且不推进 offending id，重新获取 RunSnapshot。若 snapshot `through_event_id` 已覆盖它，则原子安装并从新水位继续；若尚未覆盖，只允许有界重取，随后 `runtime_contract_mismatch`，禁止循环。读时敏感替换固定为闭合 `{kind=content_unavailable,reason=sensitive_structured_payload_rejected}`，保留原 envelope id/type/version，所有 Renderer 必须接受并推进，不尝试恢复原 payload。

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
tool_call_unavailable
tool_result_contract_violation
runtime_capability_quarantined
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

每个上述 type/version 都必须有独立闭合 payload schema。Event payload 在写入前脱敏；大正文保存在对应 message/tool log 表，Event 只包含有界摘要和引用 id。

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
- 所有业务 `*_at` 统一存 UTC Unix epoch milliseconds INTEGER，并按 ApiTimestampV1 输出；禁止 ISO 字符串混用。
- enum 使用 CHECK 约束。
- JSON 字段包含 schema version，写入前验证大小。
- 重要状态转换使用 `BEGIN IMMEDIATE` 和条件更新。

### 8.1 启动迁移

状态目录身份/owner/non-symlink/`0700` 验证通过并取得全生命周期 `runtime.lock` 后，sidecar 才允许打开数据库。数据库连接池、业务路由、恢复器和 scheduler 均须等迁移 gate 完成。MVP 只支持 forward migration：revision 缺失、未知或高于当前 build 均保持 health-only；禁止自动 `stamp`、downgrade 或猜测 revision。真正的新空库直接初始化当前 head。

需要迁移时：

1. 使用 SQLite Online Backup API 从源连接创建一致 backup 到 mode `0600` 临时文件；关闭并对 backup 执行完整性检查、fsync 和 SHA-256。
2. 以原子 rename 安装不会覆盖既有文件的 backup，并 fsync backup 目录；随后把固定记录 source revision、target build/revision、backup hash、bytes 和创建时间的 manifest 写入同目录 mode `0600` 临时文件，关闭、fsync、原子 rename 且再次 fsync 目录。backup 与 manifest 在迁移开始前都必须 durable；任一步失败都不修改源库。
3. 关闭其他连接，通过自定义 Alembic harness 把唯一显式 connection 和 transaction 传给全部 migration。Migration 禁止 `autocommit_block`、`VACUUM`、journal mode 变化或在框架外另开连接。
4. 提交前执行 `PRAGMA foreign_key_check` 和 `PRAGMA integrity_check`；成功提交后关闭、重新打开源库，验证 target revision、foreign key 与 integrity，再允许后续恢复和 ready。

迁移/复检失败保留源库、backup 与 manifest，不自动覆盖或删除 backup，也不继续运行旧 schema。备份计入本地存储占用、空间预检和 UI 数据目录说明；MVP 不自动清理。

`tool_result_numeric_limit_exceeded` 属于模型可见 ToolResult code registry，不属于本章 API Error registry；不得为了复用 HTTP 错误列表而改变其 envelope。

### 8.2 时间契约

`ApiTimestampV1` 是 `[0,9007199254740991]` 的 UTC Unix epoch milliseconds JSON/SQLite integer。所有 duration/budget/timeout 使用同范围非负 `*_ms`；UI 才把 timestamp 转为本地时区。文件 `mtime_ns|ctime_ns|birthtime_ns`、进程启动 identity 等是闭合内部 OS metadata，不属于 ApiTimestampV1，也不因名称相似直接进入 API/模型。

Runtime 只通过可注入 TimeProvider 获取时间。DNS/TLS、首 delta、idle、model cycle、退避、Shell deadline、guardian 宽限和有效执行区间在同一进程中全部使用原生 monotonic clock；禁止用 wall time 相减计算持续时间。一次实际计费区间结束并持久化时，才把 monotonic elapsed 向上取整为毫秒；deadline 和区间内累计保持原生精度，不按轮询反复取整。

跨重启期限同时保存绝对 `*_at` 与可验证的 OS boot-session identity/continuous-monotonic deadline。M0 必须证明所选 macOS continuous clock 在同一 boot 跨 sidecar 进程、系统 sleep 后仍同源单调；boot identity 或时基不可取得时 timed Shell Approval capability fail closed。Shell Approval 取 continuous deadline 与 `approval_expires_at` 任一先到。`security_metadata.highest_observed_wall_time_ms` 随业务写事务单调更新，业务 `*_at=max(system_wall,high_water)`；`creation_seq` 承担排序，因此 timestamp 重复不影响分页。

启动先读当前 boot identity、continuous clock 与 wall high-water。timed approved Shell 的 boot identity 与当前不匹配、无法证明同一 continuous 时基，或 continuous deadline 已到时，在接受任何 Renderer 命令前原子 invalidated，reason=`approval_timebase_changed|approval_expired`。同 boot 重启按原 continuous deadline，不重建五分钟，sleep/停机时间计入 TTL。若 `system_wall_time_ms < highest_observed_wall_time_ms`，再把全部 timed approved Shell invalidated 为 `clock_rollback_detected`；pending Approval 无期限，不受影响。high-water 只用于审计/检测/钳制，不能证明剩余 TTL；部分回拨即使未低于 high-water，也由 continuous deadline 防止延长。墙钟前跳可能使期限提前失效，不自动补偿。

### 8.3 Storage fail-closed

state DB 同一文件系统保存 mode `0600`、真实分配且 fsync 的 `emergency.reserve`，MVP 固定下限 16 MiB。ready 前验证 allocated blocks 达标，禁止 sparse；它是 best-effort rollback/诊断空间，不保证任意大小 WAL、事务或文件系统 metadata 操作。单次状态事务与最小诊断 payload 仍须有独立上限。

只有 `SQLITE_FULL`、底层 `ENOSPC` 或 `EDQUOT` 可审计地释放 reserve 一次；释放后只允许 SQLite rollback/WAL 收尾和最小诊断，Runtime 随即进入 `health-only/storage_unavailable`，不能继续业务。`SQLITE_IOERR_*`、EIO/fsync、只读、CORRUPT/NOTADB 等同样 health-only，但不得消耗 reserve。诊断写仍失败时只返回闭合内存 health，不声称已持久化。

state persistence 不可靠后：scheduler 停止认领，业务写/SSE publisher 关闭，模型和只读任务取消，guardian 终止已启动 Shell。事前 durable intent 仍是恢复依据；结果/Event 事务未提交就不得用内存结果向 UI 宣称成功，也不得自动重试 DB 写、删除数据或重放副作用。

重启或用户显式“重新检查”先确认空间足以同时容纳 reserve 重建、当前 WAL/journal recovery 和固定余量，再重新创建/fsync reserve、打开 SQLite 完成 WAL recovery、integrity/foreign-key check、clock check 和 durable intent 对账；任一步失败继续 health-only，全部通过才可进入 Q145 ready。损坏时只展示安全原因、数据根和 Q143 backup 引用；MVP 不自动覆盖当前 DB。

Workspace/Artifact 目标盘错误按工具阶段处理：temp write/flush 在 rename 前确定 `ENOSPC|EDQUOT` 时清理受控 temp、原目标不变，返回 `workspace_storage_exhausted,side_effects_may_exist=false`；rename 或目录 fsync 阶段异常必须执行 postcondition，无法确认则 `outcome_unknown+reconciliation_required`。Artifact temp copy 失败不创建索引；Shell 保留真实进程/limit/manifest 结果。若连相应 ToolResult/Event 都不能提交，则升级为 state storage health-only。

Migration 开始前对源 DB/WAL、backup/manifest、迁移临时增长、固定余量和 reserve 做保守空间预检；不足时零迁移。Eidos 不自动删除 Public files、Artifact、Event、log、backup 或用户 Workspace 数据来恢复容量。

## 9. 表模型

### 9.1 基础表

| 表 | 关键字段/约束 |
|---|---|
| agents | id, name；MVP seed 唯一 Eidos Agent |
| security_metadata | singleton id, highest_ruleset_generation, last_ruleset_version, highest_observed_wall_time_ms, updated_at；用于防止规则回滚和检测墙钟回拨 |
| runtime_settings | singleton id, api_contract_version, operation_contract_version, event_contract_version, shell_environment_version, gateway_contract_version, current_model_request_contract_version, current_tool_contract_version, updated_at |
| api_operations | operation_id UUID PRIMARY KEY, operation_contract_version, operation_kind, method, route_template, normalized_scope_hash, request_hash, status=in_progress\|completed\|interrupted, response_status, response_body_json, resource_type, resource_id, created_at, finished_at；无 raw request，completed response 闭合且已扫描 |
| tool_result_capability_quarantines | id, scope=tool|global, tool_contract_version nullable, tool_name nullable, detected_build_id, reason_code, active, detected_at, cleared_at nullable, cleared_build_id nullable；active tool scope 唯一 `(tool_contract_version,tool_name)`，global 同时最多一条 |
| toolchain_profiles | id, fixed_kind, name, canonical_root, dev, inode, bin_dirs_json, enabled, profile_version, enabled_at, invalidated_at；canonical_root 仅允许预定义候选 |
| model_profiles | creation_seq INTEGER PRIMARY KEY AUTOINCREMENT, id UUID UNIQUE, name unique, base_url, model, wire_api, auth_mode, api_key_ref UNIQUE nullable, credential_revision, parameters_json, context_window_tokens, max_output_tokens, profile_version, config_revision, configuration_hash, archived_at, created_at, updated_at；不保存 API Key |
| model_profile_capability_snapshots | id, profile_id, snapshot_version, configuration_hash, gateway_contract_version, model_request_contract_version, tool_schema_dialect_version, probe_status, valid, authentication, model_exists, streaming, tool_control, tool_schema, tool_call, usage, stateless_continuation, websocket_transport, output_token_parameter, error_code, invalidation_reason, checked_at, invalidated_at；`UNIQUE(profile_id,snapshot_version)` |
| model_transport_health | profile_id, configuration_hash, snapshot_version, ws_disabled, disabled_reason, disabled_at；`PRIMARY KEY(profile_id,configuration_hash,snapshot_version)`，无 TTL |
| workspaces | creation_seq INTEGER PRIMARY KEY AUTOINCREMENT, id UUID UNIQUE, canonical_root_path, volume_uuid, root_inode, root_birthtime_ns, last_seen_dev, state_path, status=available\|unavailable, unavailable_reason, created_at, last_verified_at；available 行分别对 canonical path 与 `(volume_uuid,root_inode,root_birthtime_ns)` 建立部分唯一约束 |
| sessions | creation_seq INTEGER PRIMARY KEY AUTOINCREMENT, id UUID UNIQUE, agent_id, model_profile_id, mode, workspace_id, active/state roots；mode/workspace CHECK；创建/切换时目标 Profile 必须非 Archived 且存在当前配置的 valid passed snapshot |
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
creation_seq INTEGER PRIMARY KEY AUTOINCREMENT, id UUID UNIQUE, session_id, agent_id, status
creation_operation_id UNIQUE, enqueued_at, executor_lease_id
current_segment_id, total_steps, effective_elapsed_ms
max_total_steps=80, max_total_effective_seconds=7200
consecutive_rejects, consecutive_protocol_errors, consecutive_sensitive_tool_inputs
reconciliation_required, reconciliation_epoch, ruleset_version_snapshot
pause_reason, stop_reason, error_code, error_message
model_profile_id, model_capability_snapshot_id, model_config_snapshot
tool_contract_version
started_at, finished_at, created_at, updated_at
```

`model_config_snapshot` 必须内嵌创建 Run 时的非密钥 Profile 配置、wire API、auth mode、创建时 credential revision、`configuration_hash`、capability snapshot ID/version、Gateway/model request contract version、`tool_schema_dialect_version`、必需能力（含 tool control/schema）、stateless continuation、可选 WebSocket 和 output token parameter。`model_capability_snapshot_id` 只用于审计关联；运行时不得通过该外键读取更新后的能力替代内嵌快照。每次发送仍从 Profile 当前凭证槽读取密钥，并把实际 credential revision 写入 ModelAttempt，不修改该快照。`tool_contract_version` 是独立 Run 快照字段，不因同一 Profile 后续重测或工具升级而改变。

Profile 不允许硬删除；Session/Run/snapshot 外键均使用 `ON DELETE RESTRICT`。Gateway 或 model request contract version 升级时，启动事务把旧 version 的当前 passed snapshot 设置为 `valid=false, invalidation_reason=gateway_contract_changed|model_request_contract_changed`。Tool Schema Dialect 扩展属于 Gateway contract 变更并要求重测；只改变具体工具定义/默认值/结果 schema 时只递增 `tool_contract_version`，不失效 Profile snapshot。运行时 `model_capability_drift|model_context_limit_mismatch` 使 Run failed 的同一数据库事务也使对应 Profile 当前 snapshot `valid=false`，但不修改 Run 内嵌快照。启动时非终态 Run 引用 unsupported request/tool contract，或旧 tool contract 不再满足当前安全底线时，不创建 Step/Attempt、不执行工具，使 pending/approved Approval invalidated，并原子进入 `waiting_user_input/runtime_contract_unsupported`。

`execution_segments`：

```text
id, run_id, segment_index, status
step_budget=20, time_budget_seconds=1800
steps_used, effective_elapsed_ms
pause_reason, started_at, finished_at
UNIQUE(run_id, segment_index)
```

每次新的不确定副作用即使 `reconciliation_required` 已为 true，也递增 Run epoch，并创建闭合 `reconciliation_episodes(run_id,epoch,trigger_tool_call_id,reason_code,trigger_step_id,created_at,cleared_at,cleared_by_step_id)`；`UNIQUE(run_id,epoch)`。Qualifying ToolCall ids 使用关联表持久化，不使用自由 JSON map。

`run_steps`：

```text
id, run_id, segment_id
global_step_index, segment_step_index
status, model_response_json, error_code
observed_reconciliation_epoch
available_tool_names_json, tool_set_hash
canonical_model_payload_hash, projection_ruleset_version
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
canonical_model_payload_hash
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
provider_call_id, tool_name, tool_contract_version, side_effect
arguments_json, arguments_hash, defaulted_field_paths_json
status, risk_level, timeout_seconds, execution_nonce
preconditions_json, expected_postconditions_json, result_json, result_text
model_result_base_envelope_json, model_result_base_hash
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
approval_boot_session_id, approval_continuous_deadline_ticks
user_feedback, invalidation_reason
created_at, decided_at
```

ToolCall 不反向保存 approval_id，避免双向可空外键；通过 approvals.tool_call_id 查询。

`arguments_json` 是应用静态默认值并通过完整本地校验后的唯一 effective arguments；不得另存 raw arguments。`defaulted_field_paths_json` 仅含稳定 JSON Pointer 数组且不重复参数值。`model_result_base_envelope_json` 保存唯一不可变的协议无关 base ToolResult，`model_result_base_hash` 覆盖 canonical JSON v1 bytes；内部执行详情仍在 `result_json/result_text`，不得绕过 base/projection 进入模型上下文。

`step_tool_result_projections`：

```text
target_step_id, source_step_id, source_tool_call_id, projection_order
base_result_hash, projection_json, projection_hash
tool_contract_version, model_request_contract_version, ruleset_version
model_content_truncated, created_at
PRIMARY KEY(target_step_id, source_tool_call_id)
UNIQUE(target_step_id, projection_order)
```

Projection 必须引用未变化的 base hash。`projection_order` 是目标 Step canonical history 中的全局连续序号，按 source `global_step_index`、该 source Step 的 ToolCall `batch_order` 排序，不能直接复用会跨 Step 重复的 `batch_order`。`projection_json/hash` 和 `run_steps.canonical_model_payload_hash` 在第一个 Attempt 前冻结。每个 ModelAttempt 复制相同 payload hash。重启重建时必须逐一复算并相等，否则不发送 Provider 请求并按 `tool_result_contract_violation` 失败。

ToolResult projector/schema/serializer invariant 失败时，在保存真实 ToolCall 终态的同一事务写入 quarantine 与安全 Event，并使 Run failed。Tool scope 只从新 Step/Run 隐藏对应版本工具；global scope 禁止所有后续工具循环。普通重启不清除 active quarantine；只有不同 `cleared_build_id` 的实现通过对应 deterministic regression self-test 后原子清除。该流程不修改 Model Profile snapshot。

`approval_expires_at|approval_boot_session_id|approval_continuous_deadline_ticks` 只对已 approved 的 Shell Approval 同时非空；pending 或非 Shell 同时为空。数据库 `CHECK`/应用 enum 必须覆盖 ToolCall 的 `skipped|unavailable` 与 Approval 的 `pending|approved|rejected|invalidated|canceled`；`approval_expired|approval_timebase_changed|clock_rollback_detected` 是 invalidated reason，只有 `approval_expired` 同时成为 ToolCall error code，不新增可歧义的 Approval 状态。

`file_read_results`：

```text
id, tool_call_id UNIQUE, run_id, path, base_sha256
size_bytes, total_lines, encoding, bom, newline_style
complete, content_redacted, returned_content_bytes, omitted_source_bytes
model_visible_evidence_range_count, omitted_evidence_range_count
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
creation_seq INTEGER PRIMARY KEY AUTOINCREMENT, id UUID UNIQUE, run_id, session_id
source_path, source_sha256
snapshot_path, snapshot_sha256
display_name, artifact_type, mime_type
logical_size_bytes, allocated_size_bytes, encoding, bom
version, summary, status, created_at
```

Artifact `id` 为不编码业务信息的 canonical UUID。`status=available|corrupted`。每次 content API 在当前敏感规则扫描前先复检 snapshot SHA-256；不一致时原子标记 corrupted 并返回零正文错误。`UNIQUE(session_id, source_path, version)`，同一 session/source_path 的 version 从 1 单调递增。

`events`：`id INTEGER PRIMARY KEY AUTOINCREMENT, run_id, event_type, schema_version, payload_json, envelope_sha256, created_at`，索引 `(run_id,id)`。hash 覆盖持久化的 canonical Event envelope，只用于服务端读前完整性校验且不进入 API/Renderer；写入后 immutable。随后才按当前 ruleset 生成可能不同字节的 wire projection。

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
- Event 回放不得因读时扫描跳过 event id。字符串叶子命中时在当次响应内脱敏；结构 token 命中时保留原 `event_id,event_type,schema_version`，将 payload 整体替换为固定 `{kind=content_unavailable,reason=sensitive_structured_payload_rejected}`。
- API/SSE payload 有硬上限；大内容通过分页/流式 detail API 获取，但分页不得绕过全文件扫描。
