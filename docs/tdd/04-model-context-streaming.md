# 模型、上下文与流式输出

版本：v0.4

范围说明：本文描述完整目标态 Model Gateway。第一期只实现 [MVP Lite](../mvp-lite.md) 的单 Responses HTTP(S) SSE Profile，不实现 Chat Adapter、WebSocket、Capability Probe 或 contract version 路由。

## 1. Model Profile

```text
id
name
base_url
model
wire_api = responses|chat_completions
api_key_ref
auth_mode = bearer|api_key_header|none
credential_revision
parameters_json
context_window_tokens
max_output_tokens
config_revision
configuration_hash
archived_at nullable
created_at
updated_at
```

- OpenAI-compatible provider 使用同一 Gateway 的 `ResponsesAdapter|ChatCompletionsAdapter`，不为具体厂商建立业务分支。Profile 必须显式选择 wire API；不按模型名、URL 或错误响应自动推断，也不在 Run 内跨协议回退。
- `context_window_tokens` 与 `max_output_tokens` 由用户显式填写且必填。Schema 固定 `4,096 <= context_window_tokens <= 4,194,304`、`1 <= max_output_tokens <= 262,144` 且 `max_output_tokens < context_window_tokens`；Runtime 不根据模型名称或 Provider 元数据自动覆盖。
- API Key MVP 存在 `~/.eidos/config.toml`；每个 Profile 使用唯一 `api_key_ref` 和独立凭证槽位，`none` 模式不要求密钥。
- `.eidos`/config 权限不符合 0700/0600 时拒绝加载密钥。
- Profile 创建只持久化配置，初始状态不可被 Session 选择；Runtime 不在创建请求中隐式执行网络探测。
- Run 快照保存 profile name/base_url/model/wire_api/parameters/context limits、`configuration_hash`、`model_request_contract_version` 和通过验证的 capability snapshot，不保存密钥。
- waiting、queued、running Run 始终使用创建时快照。
- API Key 不进入 Run 快照。每次实际发送模型请求前，Gateway 以 `profile_id` 从该 Profile 独占槽读取当前凭证和 `credential_revision`；密钥轮换后，既有 Run 保持原 auth mode/endpoint/model/parameters/capability snapshot，但使用新凭证。Attempt 保存实际 revision，不保存密钥、摘要或认证 Header。

### 1.1 生命周期与失效

- MVP 支持 Profile 创建、读取、编辑、Archive 和恢复，不提供物理删除。
- `name` 是纯展示字段；仅修改名称不改变 `config_revision/configuration_hash`，也不使 capability snapshot 失效。
- `base_url`、`model`、`wire_api`、`auth_mode`、API Key、`parameters_json`、context/output limits 任一变化都递增 `config_revision`，重新计算 `configuration_hash`，并在同一状态事务中使当前 capability snapshot 失效。
- `configuration_hash` 覆盖所有非密钥连接/协议字段及 `credential_revision`，绝不包含 API Key 原文、摘要或哈希。替换密钥只递增本 Profile 的 `credential_revision`；其他 Profile 不受影响。
- Snapshot 不设置时间 TTL，不由后台定时探测。Gateway contract version 或 model request contract version 变化、`model_capability_drift` 或 `model_context_limit_mismatch` 也会使当前 snapshot 失效。
- Archive 仅设置 `archived_at`，不删除或改写历史 snapshot。Archived Profile 的 `selectable=false`；恢复后只有仍满足当前配置、Gateway contract 和 model request contract 的有效 passed snapshot 才可重新选择。
- 既有 Run 始终使用内嵌快照；Profile 编辑、Archive、恢复、失效或重新测试都不能修改 Run。
- canonical serializer、输入估算/开销/margin、输出预留、传输重试或 timeout 语义变化时递增 `model_request_contract_version`，并使旧 Profile snapshot 对新 Run 失效。既有非终态 Run 继续路由到创建时版本；Runtime 必须保留仍被非终态 Run 引用的实现。若版本不可用，零模型请求并进入 `waiting_user_input/runtime_contract_unsupported`，只允许取消或基于原任务创建新 Run。

### 1.2 显式能力探测

`Test Connection` 是用户显式触发的 Model Profile 操作，不创建 Session、Message、Run、Step 或 ToolCall：

1. Gateway 只使用 Profile 配置、固定的 Eidos probe system/user 文本和固定无副作用 probe tool schema 构造请求。Probe schema 覆盖 `Eidos Tool Schema Dialect v1` 的所有允许结构/关键字，但不包含真实工具数据。
2. 请求不得读取或携带用户任务、Session 消息、Workspace、Artifact、Timeline 或 ToolResult 内容。
3. 探测只使用所选 wire API，依次验证认证、模型存在、Provider 接受配置的输出 token 上限、streaming terminal、工具控制/Schema Dialect、ToolCall 与 ToolResult 关联、stateless continuation 和最终 usage 字段。参数接受请求验证具工具请求可以显式发送 `strict=false, tool_choice=auto, parallel_tool_calls=true`，但不要求生成多调用。
4. 关联 probe 严格分两阶段：第一阶段只提供单一固定工具，发送 `strict=false, tool_choice=required, parallel_tool_calls=false`，必须得到恰好一个合法 ToolCall；Gateway 不执行，只生成固定 ToolResult。第二阶段回传该 ToolCall/ToolResult，移除 tools 并发送 `tool_choice=none`，必须返回固定确认文本和完整 usage。Probe ToolCall 永不进入 Tool Executor。
5. 短探测不声明已证明完整 context window。HTTP(S) streaming 是必需项；仅 Responses 额外探测 WebSocket 为 `supported|unsupported|unknown`，Chat 固定 `unsupported`。短探测使用 `request_max_output_tokens=min(profile.max_output_tokens,512)`，另一个无任务数据的参数校验请求验证 Provider 接受 Profile 声明的输出上限字段。
6. 只有全部必需检查通过才创建 `passed` capability snapshot；部分通过仍保存 `failed` snapshot 和安全分类结果，但 Profile 不可选择。
7. 每次探测都按 Profile 内单调递增 `snapshot_version` 创建新记录，绑定 `configuration_hash`、Gateway contract version 和探测时间，不覆盖历史结果。

Provider 返回的 context/output 元数据只作为安全提示字段返回 UI，不写回 Profile，也不参与 selectable 计算。

capability snapshot 至少包含：

```text
id
profile_id
snapshot_version
configuration_hash
gateway_contract_version
model_request_contract_version
tool_schema_dialect_version
probe_status = passed|failed
valid = true|false
authentication = passed|failed
model_exists = passed|failed
streaming = passed|failed
tool_call = passed|failed
tool_control = passed|failed
tool_schema = passed|failed
usage = passed|failed
websocket_transport = supported|unsupported|unknown
output_token_parameter = max_output_tokens|max_completion_tokens|max_tokens
stateless_continuation = passed|failed
error_code nullable
invalidation_reason nullable
checked_at
invalidated_at nullable
```

Profile 只有在未 Archive、当前 `configuration_hash`、Gateway contract version 与 model request contract version 下最新一次探测 `probe_status=passed` 且 `valid=true` 时才 `selectable=true`。Run 创建时把该 snapshot 的 ID、版本和完整能力字段复制进不可变 `model_config_snapshot`；后续探测不改变既有 Run。

正常运行中若 Provider 不再满足固化 snapshot 已通过的 streaming、工具控制、Tool Schema Dialect、ToolCall/ToolResult 关联或 usage 契约，Gateway 返回 `model_capability_drift`。若 Provider 明确返回 context-length exceeded，则映射为 `model_context_limit_mismatch`。两者都属于确定性模型兼容性错误：Run 直接 `failed`，不重试、不 Finalize、不修改 Run 快照；Runtime 同时使 Profile 当前 capability snapshot 失效，阻止新 Session/Run，直到用户编辑并重新测试。

WebSocket 不属于 selectable 的必需能力。瞬时 WebSocket 故障不使 capability snapshot 失效；明确的 Upgrade 拒绝、426 或协议不支持会在独立 transport health 记录中为当前 `(profile_id, configuration_hash, snapshot_version)` 设置 `ws_disabled=true`。该记录无 TTL、无后台探测，新 snapshot 自动使用新 key；HTTP(S) streaming 的确定性漂移仍按上段失效。

### 1.3 Endpoint、TLS 与认证

- `base_url` 必须是绝对 HTTP(S) URL；允许公网、loopback、局域网和其他私网，不执行地址类别或 DNS 私网过滤。
- `base_url` 是 API root，保存时移除 path 末尾 `/`。Responses Adapter 结构化追加单个 `responses` path segment；Chat Adapter 追加 `chat/completions`。若输入 path 已以 `/responses` 或 `/chat/completions` 结尾，返回 `model_profile_endpoint_in_base_url`，不猜测或去重。
- URL 禁止 userinfo。path prefix 和不含敏感内容的 query 可以保留；完整 URL 在保存前通过敏感扫描，禁止把密码、token 或 API Key 编码进 URL。
- endpoint 使用 URL 组件 API 插入到 path 尾部，原安全 query 保持；禁止字符串拼接。规范化 API root、wire API 和最终 endpoint 共同参与 `configuration_hash`，UI/API 可返回安全的最终 URL preview。
- Redirect 最多 5 次且每一跳必须保持相同 Origin；Origin 按 scheme、规范化 host 和有效 port 比较。跨 Origin、非 HTTP(S) 或 URL 内嵌凭证的 Location 立即失败。
- HTTPS 使用 macOS 系统信任库，必须验证证书有效期、主机名和完整信任链；HTTP client 不暴露 `verify=false` 或忽略证书错误路径。
- `auth_mode=bearer` 固定发送 `Authorization: Bearer <api_key>`；`api_key_header` 固定发送 `api-key: <api_key>`；`none` 不读取或发送凭证。
- Gateway 只添加固定 Content-Type、Accept、User-Agent 和上述认证 Header；Profile 不接受任意自定义 Header。
- API Key、完整 Authorization Header 和 Provider 原始错误正文不进入日志、Event、snapshot 或 API 响应。HTTP 端点在 UI 标记为非加密，但请求契约与 HTTPS 相同。

### 1.4 Provider 扩展参数

- `parameters_json` 使用标准 JSON，UTF-8 编码后最大 32 KiB、最大嵌套深度 8、容器成员合计最大 256；禁止 NaN、Infinity、二进制值和敏感内容。
- Runtime 保留并拒绝用户提供 `model`、`messages|input|instructions`、`tools`、`tool_choice`、`parallel_tool_calls`、`stream`、`stream_options`、`store`、`previous_response_id`、`conversation|conversation_id|thread|thread_id`、`max_output_tokens`、`max_tokens`、`max_completion_tokens`、认证/Header、URL、代理、TLS 和其他传输层字段。
- 其他字段原样透传并纳入 `configuration_hash`；Runtime 构造核心请求后再合并扩展字段，遇到保留键整次返回 `model_profile_reserved_parameter`。
- Test Connection 必须使用 Profile 的实际扩展参数。Provider 确定性拒绝参数时 snapshot 为 failed，错误分类为 `model_invalid_request`。

### 1.5 输出字段协商

- Responses 固定发送 `max_output_tokens=request_max_output_tokens`。
- Chat 在 Test Connection 中先使用 `max_completion_tokens`。只有 Provider 明确返回该字段未知/不支持的确定性错误时，才以相同固定 probe 尝试 `max_tokens`；认证、网络、429、5xx、timeout 或含糊 invalid request 不触发切换。
- 两者都拒绝时探测失败 `model_output_limit_parameter_unsupported`；两者都接受时固定选择 `max_completion_tokens`。
- `output_token_parameter` 固化到 capability/Run snapshot；正常 Run 只使用该字段。后来被拒绝时是 `model_capability_drift`，不得运行时尝试另一字段。

### 1.6 工具请求控制

- 工具非空的普通和协议纠正请求在两种 wire API 中都固定 `tool_choice=auto, parallel_tool_calls=true`。Parallel 只影响模型能否在同响应提出多调用；Tool Executor 仍按 Q9 串行处理。
- 工具集为空的普通/纠正请求移除 `tools`，固定 `tool_choice=none`，不发 `parallel_tool_calls`。
- 所有 function tool 定义显式 `strict=false`；Profile 无法覆盖 `tools|tool_choice|parallel_tool_calls|strict`。
- 探测中对 `parallel_tool_calls=true`的确定性拒绝 -> `model_parallel_tool_calls_unsupported`；对 `required|none|parallel_tool_calls=false` 的确定性拒绝 -> `model_tool_control_unsupported`；对 `strict=false` 的确定性拒绝 -> `model_tool_schema_mode_unsupported`。运行时后续违反已通过契约为 `model_capability_drift`。

## 2. Model Gateway 流协议

```python
class ModelStreamEvent(BaseModel):
    type: Literal[
        "content_delta",
        "tool_call_delta",
        "usage",
        "completed",
        "failed",
    ]
    payload: dict

class ModelGateway(Protocol):
    async def stream_response(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]: ...
```

必须完整收到 `completed` 并解析 ToolCall 参数后，才能创建可执行 ToolCall。流中途失败时，部分 tool_call_delta 永远不能执行。

### 2.1 Wire Adapter 与完成判定

两个 Adapter 只输出统一内部事件，不把 Provider 原始对象泄露给 Runtime：

- Responses 固定 `store=false`，不发送 `previous_response_id` 或 conversation；只有 `response.completed` 产生内部 completed。`response.incomplete/max_output_tokens` -> `model_output_truncated`，`response.incomplete/content_filter` -> `model_output_blocked`，failed 按安全错误映射，无终态 EOF -> `model_stream_interrupted`。
- Chat 固定 `n=1, stream=true, stream_options.include_usage=true`，只接受 `choice.index=0`。必须同时收到合法 finish reason、完整 content/ToolCall、合法 usage 和 `[DONE]` 才 completed；仅 EOF 不完成。
- Chat `stop` 正常完成文本；`tool_calls` 仅在完整批次时完成；`length` -> truncated；`content_filter` -> blocked；null 只允许非终态；deprecated `function_call` 和未知值 -> `model_capability_drift`。
- truncated/blocked 不做协议纠正、重试或传输切换；已提交文本保持 incomplete，整个 ToolCall 批次丢弃，Run 分别进入 `waiting_user_input/model_output_truncated|model_output_blocked`，snapshot 不因正常 truncated/blocked 失效。

usage 必须包含非负整数 `input_tokens,output_tokens,total_tokens` 且 `total_tokens >= input_tokens + output_tokens`。cached/reasoning/audio 等细分字段可选但存在时必须非负。探测时 Provider 拒绝 `include_usage`、完成时缺失或非法 usage -> `model_usage_unsupported`；正常 Run 完成态出现则 `model_capability_drift`。失败/中断未完成且未收到 usage 仍按 Q96 为 unknown，不属于 drift。

### 2.2 Stateless continuation

- 每个 Step 从 Eidos 本地 Timeline/Context Builder 重建完整语义输入，不依赖 Provider history。Provider response ID 只写 Attempt 审计字段。
- Responses ToolResult 编码为 `function_call_output.call_id=provider_call_id`；Chat 编码为 `role=tool,tool_call_id=provider_call_id`。相应完整 assistant ToolCall item/message 必须在本地上下文中先于结果。
- WebSocket session 只承载传输，不持有不可替代语义状态。Provider 必须依赖服务端会话才能完成 ToolResult continuation 时，Test Connection 失败 `model_stateless_mode_unsupported`。

### 2.3 传输选择

- 仅 `wire_api=responses` 且 `websocket_transport=supported`、当前 snapshot 未设置 `ws_disabled` 时优先使用 Responses-over-WebSocket；Chat 或 `unsupported|unknown` 直接使用 HTTP(S) streaming。
- WebSocket URL 只能从固化 `base_url` 做同 Origin、同 path prefix 的协议映射：`https->wss`、`http->ws`。降级沿用原 `base_url` 的 `https|http` scheme，不允许静默改写 scheme。
- 首个 delta 前 WebSocket 遇到瞬时网络错误时最多重放 5 次；明确不支持时不消耗重试预算，立即切换 HTTP(S)。
- WebSocket 预算耗尽后，同一逻辑模型请求以完全相同的 model、canonical input、tools、parameters 和 `request_max_output_tokens` 切换 HTTP(S)；当前 Run 后续请求粘滞使用 HTTP(S)。
- 瞬时降级不跨 Run；新 Run 依据 capability snapshot 和 transport health 重新选择。明确 `ws_disabled` 跨 Run 生效，直到用户显式 Test Connection 生成新 snapshot。
- 收到首个 delta 后禁止传输切换或请求重放；中断按 `model_stream_interrupted` 处理。

## 3. 流式事件

- delta 到达后先进入增量敏感扫描器，只有已确认安全或已脱敏的文本才通过内部 EventBus 推送给 SSE。
- `tool_call_delta` 在完整解析和参数扫描前只存在于短暂内存，不对 UI 流式展示、不持久化原始片段；通过后只生成受控的 ToolCall 摘要事件。
- 按 100ms 或累计 4KB 合并为一个持久化 chunk，任一阈值先到即 flush。
- 完成时保存经增量扫描后合并的完整最终响应和 usage；Provider 原始响应不落盘。
- 崩溃恢复以最后一个已提交 chunk 为准。
- UI delta 是临时视图；数据库 committed Event 是断线回放边界。
- `deny`/`redact` 命中的普通 content delta 统一替换后继续；不因普通文本单次命中终止 Run。
- 扫描器失败后不 flush 保留窗口，丢弃未完整解析的 ToolCall，Run 进入 `waiting_user_input/sensitive_scan_failed`。

流式资源计数在解压和协议解码后、内容进入下游前执行：

```text
max_visible_text_bytes =
    CLAMP(request_max_output_tokens * 16, 64 KiB, 4 MiB)
max_discarded_reasoning_bytes = 2 MiB
max_single_stream_event_bytes = 1 MiB
max_total_stream_payload_bytes = 8 MiB
```

visible 统计全部普通 content UTF-8 字节；tool arguments 同时计入 Q106 自身上限和 8 MiB 总量；reasoning 即使立即丢弃也计数。任一上限超出即关闭流，Attempt/Step=`model_output_limit_exceeded`，Run waiting_user_input，零重试/纠正/传输切换。已提交安全文本保持 `assistant_progress/incomplete=true`，未完成 ToolCall 丢弃，usage 已完整收到则 reported，否则 unknown；snapshot 不失效。

## 4. ToolCall 解析和组合校验

### 4.1 Adapter assembler

- 每个合法 ToolCall 生成 Eidos 内部 UUID；Provider 标识单独保存为 `provider_call_id`，仅用于向相同 wire API 回传 ToolResult。内部 UUID 不发送给 Provider。
- `provider_call_id` 必须非空、本响应唯一、UTF-8 <=256 bytes 且无控制字符；缺失/重复不得生成替代 ID。
- Chat 固定 choice 0，按 `tool_calls[].index` 归并；index 从 0 连续且不得重绑到其他 ID。Responses 按 output index/item ID 归并且最终必须有唯一 call_id。
- name/ID/type 可以跨 chunk 分片；已确定内容不得被后续片段修改。arguments 严格按同调用流顺序追加，completed 后必须恰好解析为一个 JSON object。
- 缺失/冲突 ID、非法 index、字段矛盾或 arguments 非单一完整 object 统一 `model_protocol_error`，按 Q45 纠正一次；零 ToolCall row/Approval/原始参数持久化。

assembler 硬上限：

```text
tool_calls_per_response = 16
arguments_per_call = 1 MiB UTF-8
arguments_per_response = 2 MiB UTF-8
tool_call_delta_count = 16_384
tool_name = 128 UTF-8 bytes
json_depth = 16
json_container_members = 2_048
```

边界值允许相等，超过即取消流并产生 `model_protocol_limit_exceeded`，按 Q45 计一次协议错误；纠正输入只含超限维度和固定上限。工具 schema 的更小限制随后继续生效，上述容量不放宽任何工具。

### 4.2 Runtime 校验

Model Gateway 输出完整 ToolCall list 后，Runtime：

1. 校验工具名属于本 Step 首次 Attempt 冻结的 available tool set。
2. 按 Run 的 `tool_contract_version` 和 Tool Schema Dialect v1 解析参数 object；只对缺失的 optional 字段应用 schema 声明的静态默认值，生成唯一 effective arguments。
3. 对 effective arguments 执行完整 schema 校验，并计算每个工具的 side_effect 分类。
4. 校验整个批次组合。
5. 对整批 effective arguments 执行敏感扫描。
6. 全部合法时按模型声明顺序创建 ToolCall；后续只持久化和执行 effective arguments。

未知字段、缺失 required、非法 null、schema 错误、未在冻结集合中暴露的工具和非法批次统一创建 `model_protocol_error`，零 ToolCall row、零 Approval、零执行；未知字段不得忽略、透传或由 Runtime 猜测修正。默认值只能是 Tool Schema 中的静态 JSON literal，不得读取当前时间、环境变量、Workspace 或其他运行时状态；同一 Run 内按固化 tool contract 保持稳定。可选审计字段 `defaulted_field_paths` 只保存 JSON Pointer 路径，不保存第二份参数值。

组合与 schema 合法后、创建 ToolCall 之前，Runtime 对完整 effective arguments 执行敏感扫描。命中 `deny`/`redact` 时按 `sensitive_tool_input` 处理，不创建 ToolCall，也不增加协议错误计数。连续两次命中后暂停，任一完整合法且无敏感 ToolCall 的响应清零该计数。

若工具已在冻结集合中暴露，但在收到 ToolCall 后因 Shell 能力、reconciliation 或其他当前执行状态变化而不可执行，Runtime 创建该合法 ToolCall 的终态 `unavailable/tool_unavailable` 和 canonical ToolResult，保证零副作用；这不是模型协议错误。Redaction、Workspace Guard、Seatbelt 等全局安全组件故障仍按既有规则 fail closed，不降级成普通 `tool_unavailable`。

模型必须在读取结果进入下一轮上下文后，才能提出基于结果的变更。一次响应中的只读 ToolCall 彼此不能依赖运行结果。

空响应、无法解析的 ToolCall、未知工具、参数 schema 错误和非法批次统一视为模型协议错误。Runtime 允许下一 Step 自动纠正一次；连续第二次错误后进入 waiting_user_input。每次无效响应计一个 Step，合法响应清零连续计数。

## 5. Context Builder

稳定顺序：

```text
1. 内置 system prompt
2. Runtime 和 ToolCall 组合协议
3. Session mode / active root /安全边界
4. 当步冻结的 available tool definitions
5. 原始任务和后续用户输入
6. 当前 Segment 状态与剩余预算
7. 未解决审批、冲突、reconciliation 状态
8. 最近消息、Step 和 ToolResult
9. Artifact 与结构化历史摘要
```

所有注入项必须有单项上限和总 token 预算。

每个 Step 的 available tool set 只由 Run `tool_contract_version`、请求种类、工具声明的 mode applicability、reconciliation 等持久 Runtime gate，以及单工具 capability health 决定；不得读取任务文本、目标路径是否存在、上下文预算或工具常用程度做启发式筛选。集合按工具名稳定排序。首次 Attempt 前持久化工具名列表，并对完整 model-visible definitions 的 canonical serialization 计算 `tool_set_hash`；同一 Step 的所有传输重试/重放使用完全相同的集合与 hash，下一普通或协议纠正 Step 重新计算。

非空集合发送 `tools`、`tool_choice=auto`、`parallel_tool_calls=true`；空集合省略 `tools`，显式发送 `tool_choice=none`，并省略 `parallel_tool_calls`。Finalization 始终使用空集合。隐藏工具只是减少无效 ToolCall，不构成安全边界；Runtime 仍独立执行 schema、组合、审批和沙箱校验。

每个已创建 ToolCall 到达终态后恰好生成一个协议无关、不可变的 base ToolResult JSON：`schema_version,tool_name,outcome,code,summary,data,model_content_truncated,side_effects_may_exist`。`schema_version=1`；`outcome` 固定为 `success|error|skipped|rejected|interrupted|unavailable`；`data` 必须符合 `(tool_contract_version,tool_name,outcome,code)` 的闭合 schema。Base 按 ToolResult canonical JSON v1 序列化并保存 hash，`model_content_truncated=false`；只有 Run 继续时才生成并发送 Step projection。

Context Builder 不修改 base，而为每个 Step 生成 model-visible projection：

1. 先按当前 `ruleset_version` 重扫 base；只允许拒绝注入或加强脱敏，不回写 base。
2. 只裁剪 tool contract 明确标记为 truncatable 的 data 字段。Projection schema 必须预先声明 `omitted_count|omitted_bytes` 等字段；Context Builder 不得临时增加 key。
3. 永远保留 `schema_version,tool_name,outcome,code,summary,side_effects_may_exist` 和工具声明的 core count/status。发生裁剪时设置 `model_content_truncated=true`，但不修改工具级 `complete|truncated|stop_reason`。
4. 后续 Step 对每个可裁剪字段只能保持相同确定性范围或进一步减少/脱敏，不得换一批等量条目重新显露。
5. 第一个实际 Attempt 前冻结 projection bytes/hash、引用的 base hash 和完整 canonical model payload hash；同一 Step 的所有 Attempt 和传输使用相同投影。

MVP projection schema 与裁剪单元固定如下：

| ToolResult | 可裁剪字段 | 保留规则 | Projection 省略字段 |
|---|---|---|---|
| `list_files` success | `data.entries` | 只保留 base 稳定顺序的前 N 项，N 只能跨 Step 不增 | `context_omitted_entry_count` |
| `search_text` success | `data.matches` | 只保留 base 稳定顺序的前 N 项，N 只能跨 Step 不增 | `context_omitted_match_count` |
| `read_file`/`read_file_range` success | `segments[].content` | 一次删除该结果全部 segment content；不修改 segment 行界 | `context_omitted_content_bytes` |
| `read_file`/`read_file_range` success | `evidence_ranges` | 只保留行序前 N 段，N 只能跨 Step 不增 | `context_omitted_evidence_range_count` |
| `run_shell` success/error/interrupted | `data.stdout_head,data.stdout_tail,data.stderr_head,data.stderr_tail` | 优先保留 stderr、再分配 stdout；流内按固定 head/tail 算法缩短 | `context_omitted_output_bytes` |

上述 `context_omitted_*` 是 projection schema 预声明的非负整数字段：仅值大于 0 时存在；按 base model-visible 内容计算，不与工具执行自身的 `omitted_source_bytes|omitted_evidence_range_count|truncated` 合并。Read/range 先整体删除 segment content，再裁 evidence_ranges 尾部；list/search 只删 array 尾部。所有其他 ToolResult data 在 MVP 中不可字段级裁剪，只能按 P0 历史项优先级整体不注入；若协议关联要求该 ToolResult，则整体不注入不可用，必须继续裁更低优先级内容或触发 `context_input_too_large`。

Shell projection 的目标 observation 正文字节预算 `B` 由 Context Builder 给出，范围为 0 到 base 四个 observation string 的 UTF-8 字节合计。先令 `stderr_budget=min(B,base_stderr_bytes)`，再令 `stdout_budget=min(B-stderr_budget,base_stdout_bytes)`，因此收缩时先移除 stdout、后移除 stderr。每个流令 `head_budget=CEIL(stream_budget/2),tail_budget=FLOOR(stream_budget/2)`；分别保留 base head 的最长合法 UTF-8 前缀和 base tail 的最长合法 UTF-8 后缀，不拆 Unicode scalar 或完整脱敏占位符，也不把边界舍入后的余额转给另一段/流。零长度 string 字段省略；非 observation 的 exit/resource/termination/manifest core 字段保持不变。`context_omitted_output_bytes=base_model_output_bytes-projected_model_output_bytes`，其中两者只统计四个模型可见 string，不使用工具级 `*_observation_bytes` 完整流计数；由此可唯一重建任一预算下的 projection。

`model_content_truncated=true` 当且仅当任一 `context_omitted_* > 0` 或当前规则重扫比 base 产生更严格脱敏。每个 projection 生成后必须重新通过对应闭合 projection schema 和 canonical serializer。跨 Step 单调性按 base hash 下的 entries/matches/evidence prefix 长度和 segment content present/absent 逐字段比较。

Adapter 将该 Step 冻结的同一 projection JSON 编码为 Responses `function_call_output.output` 或 Chat `role=tool` content，不得另建模型可见自由文本结果通道。只读批次按 `batch_order` 每个调用各有一个 base 和对应投影。非法/敏感/非法组合响应因未创建 ToolCall，不生成 synthetic ToolResult；已创建调用的 rejected、no-op/skipped、unavailable 和 interrupted 结果若模型仍继续则必须进入上下文。终止或已取消 Run 无须再发给 Provider，但内部状态仍需如实持久化。

`read_file`/`read_file_range` ToolResult 包含 `read_result_id,path,base_sha256,complete,content_redacted,segments,evidence_ranges,omitted_evidence_range_count,encoding,bom`，供后续 write/apply 引用。Evidence 只覆盖已返回且整行未脱敏的范围；Context Builder 可裁剪 segments 正文，但不会扩大 Runtime 证据。若 read_result_id 也不再可见，模型应重新读取，不得自行构造。

list/search 不注入“Workspace snapshot”结论。工具级 `workspace_changed`、`changed_during_scan_count`、计数和 stop_reason 始终保留；Context projection 只保留 entries/matches 的确定性前缀并记录省略量，不改写工具级 truncated。搜索项带自己的 `file_sha256`，不与其他文件 hash 合并。

`skipped/no_changes` ToolResult data 恰为 `{path,base_sha256}`，模型可直接继续；该 outcome 固定表示零 Approval、零 intent、零文件接触，不得解读为获批写入。

已启动 Shell ToolResult 的模型部分只包含 32 KiB 受控 stdout/stderr observation、exit/resource/termination 元数据、manifest 分类计数、前 50 个安全路径和完整性标志。`*_observation_bytes` 使用脱敏后、工具容量裁剪前口径；raw pipe bytes、完整 manifest、Toolchain 内部文件列表和 protected path 不进入 ToolResult、Event 或模型上下文。Context projection 只裁四个 observation string 并记录 `context_omitted_output_bytes`，不修改工具级 observation/returned/truncated 计数。

从 SQLite、Tool log 或 Artifact 读取的历史正文在进入 Context Builder 前按当前 `ruleset_version` 再扫描。读时 `deny` 不注入正文，`redact` 只注入脱敏版本；不修改原始 Timeline 或 Artifact 快照。

每个 Step 都从上述本地事实重建完整模型语义历史，包括需要继续的 assistant ToolCall 与对应 ToolResult；不得用 Provider response/conversation ID 替代本地项。Context Builder 先生成协议无关 canonical model-visible items，再由 Run 固化 wire Adapter 编码，预算统计覆盖最终实际模型可见序列化结果。

## 6. P0 确定性裁剪

发送前预算使用 Run 的 `model_request_contract_version` 对应的稳定、紧凑 UTF-8 canonical serializer。`canonical_model_visible_payload` 包含所有实际模型可见 system/runtime 指令、工具 schema、消息、ToolResult、结构化字段名和 JSON 转义字节，不包含认证、URL/传输协议字段、日志或 Eidos 内部元数据。

计算：

```text
payload_estimate_tokens =
    UTF8_BYTE_LENGTH(canonical_model_visible_payload)

protocol_overhead_tokens =
    64
    + message_count * 8
    + tool_call_count * 16
    + tool_result_count * 16

estimated_input_tokens =
    payload_estimate_tokens + protocol_overhead_tokens

safety_margin_tokens =
    CLAMP(CEIL(context_window_tokens * 0.02), 1_024, 8_192)

usable_input_budget =
    context_window_tokens
    - request_max_output_tokens
    - safety_margin_tokens

estimated_input_tokens <= usable_input_budget
```

每个 UTF-8 字节按一个估算 token 计入；这是保守预算规则，不声称是所有 Provider tokenizer 的严格数学上界。Context Builder 先验证不可裁剪内容，再按优先级添加可选内容并持续复算完整 canonical payload，而不是只累计正文长度。

`request_max_output_tokens` 固定为：

- 普通 Agent Step 和协议纠正 Step：Run 快照中的 `profile.max_output_tokens`。
- Finalization：`min(profile.max_output_tokens, 4_096)`。
- Test Connection 短探测：`min(profile.max_output_tokens, 512)`。

同一逻辑请求的所有重放保持相同值，每个 ModelAttempt 保存实际值。Provider 元数据或响应不得静默覆盖。

不可裁剪：

- system/runtime 安全规则。
- 原始任务和所有用户补充指令。
- 当前 Segment。
- waiting approval、错误、冲突和事实确认屏障。
- Artifact 元数据。

按顺序优先裁剪：

1. 最旧 Shell stdout/stderr 正文。
2. 最旧文件正文和搜索 preview。
3. 最旧普通模型进度文本。
4. 更早 Step 的完整结果。

字段级裁剪严格使用第 5 节预声明的 `context_omitted_*` projection schema，保留对应工具的 path/hash/core status/count；不得临时增加 `content_omitted` 或其他 key。不可字段级裁剪且协议要求关联的 ToolResult 必须整体保留；预算仍不足时进入 `context_input_too_large`。Agent 可根据保留事实重新读取，MVP 不调用模型生成 compaction summary。

裁剪后若不可裁剪内容仍使 `estimated_input_tokens > usable_input_budget`，Gateway 零网络请求且不创建 started ModelAttempt；当前 Step 若已存在则标记 `failed/context_input_too_large`，Run 直接 `failed`、不 Finalize、capability snapshot 保持有效。保存并向 UI 返回 `estimated_required_tokens, usable_input_budget, request_max_output_tokens, safety_margin_tokens`，不包含原始 payload。只有本地预算通过后 Provider 明确返回 context-length exceeded 才是 `model_context_limit_mismatch`。

## 7. Model Retry

每次实际网络发送都写入独立 ModelAttempt；同一 Step 的这些 Attempt 共享 `logical_model_request_id`，但拥有不同 `attempt_index`、transport、凭证 revision、Provider request id、时间和结果。MVP 不发送或依赖厂商专有 `Idempotency-Key`；首 delta 前的重放仍可能产生重复 Provider 计算或计费。

同一 Step 的一个逻辑模型请求周期从第一次发送前开始，共享 10 分钟 hard deadline。该 deadline 覆盖 DNS、TLS、建连、读取、全部 Attempt、Retry-After、固定退避和 WebSocket 到 HTTP(S) 的降级；不是每个 Attempt 各有 10 分钟。每个 Attempt 的局部上限为：

- connect：15 秒。
- first delta：180 秒。
- stream idle：120 秒。

任何局部等待都取局部上限与 cycle 剩余时间的较小值。WebSocket 五次重试退避固定 `1s,2s,4s,8s,16s`；HTTP(S) 两次重试退避固定 `1s,2s`。合法 Retry-After 优先，但上限 60 秒且不能越过 cycle deadline。

- 首个 delta 前 WebSocket 瞬时错误按传输规则重试并降级；HTTP(S) 对网络失败、429、5xx 最多重试 2 次。
- 周期或 HTTP(S) 重试耗尽后 Step 标记 `model_temporarily_unavailable`，Run 进入 waiting_user_input。
- 同一 Step 的多个 ModelAttempt 只占一个 Step 预算；用户继续时创建新 Segment。
- 收到 delta 后失败：本 Attempt 与 Step 标记 `model_stream_interrupted`，不透明重试、不切换传输。
- Provider 正常报告 output token 截断、内容过滤或 Runtime 触发输出流容量上限时，分别进入 `waiting_user_input/model_output_truncated|model_output_blocked|model_output_limit_exceeded`；三者均不重试、不切换传输、不执行该响应任何 ToolCall。
- 已提交 content chunk 保留为 `assistant_progress/incomplete`，不得升级为 final_answer。
- 部分 tool_call_delta 丢弃，不创建 ToolCall row。
- Run 进入 waiting_user_input；用户继续时创建新 Segment。
- 失败 Step 计入 Run 的 80 Steps 硬上限。
- Provider validation/auth 错误不重试。
- `401/403` 认证错误、确定性的 model not found、invalid request 或不支持参数直接终止 Run。
- TLS 证书/主机名/信任链错误不重试，保存 `model_tls_validation_failed`；不存在关闭校验后继续的分支。
- 当前凭证槽缺失、不可读或与 Run 固化 auth mode 不兼容时不回退到旧密钥，保存 `model_credential_unavailable` 并直接 failed；Provider 401/403 仍为 `model_auth_failed`。
- 明确的 context-length exceeded 保存 `model_context_limit_mismatch`；wire terminal、streaming、工具控制、Tool Schema Dialect、ToolCall/ToolResult 关联、usage、stateless continuation 或固化输出字段与 snapshot 不符保存 `model_capability_drift`。两者在 Run failed 事务中使 Profile 当前 snapshot 失效。
- 终止时保存结构化 `model_credential_unavailable|model_auth_failed|model_not_found|model_invalid_request|model_tls_validation_failed|context_input_too_large|model_context_limit_mismatch|model_capability_drift`，不调用 Finalization。
- Run 的 Model Profile snapshot 不可修改；修复配置后只能创建新 Run。
- 用户取消会关闭流并结束 Step。

usage 按 Attempt 保存：Provider 明确报告的 usage 逐份计入 `reported_usage_total`；失败 Attempt 未返回 usage 时保存 `usage_status=unknown`，不得填零或推算。Run 汇总同时返回已报告 usage、unknown Attempt 数和总 Attempt 数。任何 ToolCall 都必须等当前响应 completed 后才创建，因此传输重放不会重复执行本地副作用。

## 8. Finalization Call

Finalization 使用原 Run 模型快照，但：

- 无工具 schema。
- 显式发送 `tool_choice=none`，省略 `parallel_tool_calls`。
- timeout 60 秒。
- `request_max_output_tokens=min(profile.max_output_tokens,4_096)`。
- 使用 Run snapshot 固化的 wire API 与 `output_token_parameter`，保持 stateless；不重新协商字段。
- 输入为有界结构化任务结果。
- 不允许重试产生的内容覆盖已有 Artifact。
- 调用失败由 Runtime 生成固定格式摘要。

## 9. Reasoning 内容边界

- Provider 返回的 raw reasoning/reasoning tokens 内容不写 Message、Event、日志或上下文回放。
- Provider 支持关闭 reasoning 内容返回时，Gateway 应关闭；无法关闭时消费后立即丢弃内容。
- 有 ToolCall 的普通 content delta 标记为 `assistant_progress`。
- 无 ToolCall 且响应 completed 的普通 content 标记为 `final_answer`。
- `assistant_progress` 可以实时展示和按普通文本规则持久化，但不得命名为思维链或内部思考。
- reasoning token 数量、耗时和费用可以保存在 usage metadata 中，不能反推出内容。
- ToolCall 参数解析不依赖 reasoning 内容。

## 10. 规则版本

- Sidecar 生命周期中只使用启动自检通过的单一 `ruleset_version`，不热加载。
- Sidecar 将已成功使用的最高 `ruleset_generation` 保存在 security metadata。当前应用携带的 generation 更低时 Redaction Service 不可用；回滚构建必须携带不低于已生效 generation 的规则资源。
- Run 创建时把当前版本写入快照；每个扫描结果记录实际版本。
- 应用升级重启后，queued/waiting Run 使用新版本继续，并在恢复前追加 `redaction_ruleset_changed` Event。安全规则不因 Run 的旧快照而降级。
- MVP 不远程下载、不允许 Workspace/用户覆盖，也不对旧数据执行升级后的全量追溯重扫；历史数据安全迁移属于 P1。
