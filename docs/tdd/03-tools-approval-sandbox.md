# 工具、审批与沙箱

版本：v0.4

范围说明：本文保留完整目标态工具契约。第一期工具集合、审批和安全底线以 [MVP Lite](../mvp-lite.md) 为准。

MVP Lite 当前实施状态：✅ 只读三工具；✅ `write_file/apply_patch` 候选与完整 diff；✅ Runtime→Main 双向审批、拒绝零副作用、取消/迟到响应；✅ fd-relative Workspace Guard；✅ Seatbelt 内 `RENAME_EXCL` 新建与 `RENAME_SWAP` 旧 hash CAS/回滚；✅ 原子提交读回与不确定副作用标记。`run_shell` 执行器仍未实现。

## 1. ToolDefinition 与 ToolContext

```python
class ToolDefinition(BaseModel):
    name: str
    description: str
    side_effect: Literal["none", "workspace", "eidos_state", "shell"]
    requires_approval: bool
    allow_batch: bool
    mode_applicability: set[Literal["workspace", "public"]]
    default_timeout_seconds: int
    max_timeout_seconds: int
    input_schema: dict
    result_schemas: dict

class ToolContext(BaseModel):
    run_id: UUID
    segment_id: UUID
    step_id: UUID
    tool_call_id: UUID
    session_id: UUID
    mode: Literal["workspace", "public"]
    active_root: Path
    state_root: Path
    sandbox_home: Path
    sandbox_tmp: Path
    run_remaining_seconds: int
    network_hosts: list[str] = []
    local_network: bool = False
```

ToolCall row 创建前，工具参数必须完成归一化、schema 校验、组合校验和敏感扫描。工具结果在投影为模型内容、参与 Context Builder 预算、交付 UI 和持久化前必须经过 Redaction Service。

### 1.1 工具契约版本

`tool_contract_version` 覆盖模型可见工具名/描述/input schema/静态默认值、effective arguments 归一化、模型可见 ToolResult schema、`side_effect/allow_batch/requires_approval/mode_applicability`、工具特有容量/timeout 和外部可观察执行语义。纯内部重构不递增版本。

- 创建 Run 时把当前版本复制进不可变 Run 快照；每个 Step 和 ToolCall 都使用该版本，新版本只适用于新 Run。
- Runtime 仅在旧实现仍满足当前安全底线时，为引用它的非终态 Run 保留实现。
- 当前 Redaction ruleset、Workspace Guard、Seatbelt、网络边界和全局资源上限始终作用于旧 Run，旧工具契约不得将其降级。
- 旧版本缺失或不兼容当前安全底线时，Runtime 零模型/工具执行，使其 pending/approved Approval 失效，并将 Run 置为 `waiting_user_input/runtime_contract_unsupported`。原 Run 只允许取消或按当前契约创建新 Run。
- 普通工具契约变化不使 Model Profile capability snapshot 失效。若变化涉及 Provider probe、wire 或请求语义，还必须递增对应 Gateway/model request contract version。

### 1.2 Eidos Tool Schema Dialect v1

内置 function tool 显式发送 `strict=false`。Provider strict function calling 永远不作为授权边界。

Function tool input 的 root 必须是 object。Dialect v1 只允许：

```text
type = object|array|string|integer|boolean
properties, required, additionalProperties=false
items, string enum, description, static default
minimum, maximum, minLength, maxLength, minItems, maxItems
```

Dialect v1 禁止 `$ref`、组合/否定、regex/pattern properties、条件/dependencies、unevaluated 关键字、自由 map、null union 和小数 `number`。每个 object（包括 array 中嵌套的 object）都必须用 `additionalProperties=false` 封闭。

启动时 Tool Registry 按 Dialect v1 校验每个 schema 和 default。任一非法 schema/default 都使 Runtime unavailable，不得静默丢弃关键字。JSON Schema 的字符/元素约束不能替代 Runtime 对 UTF-8 字节、跨字段关系、UUID/hash/path/host 格式或文件系统身份的检查。

参数归一化必须确定：

1. 解析恰好一个 JSON object，递归拒绝未知字段。
2. 只对缺失的 optional 字段应用静态 JSON literal default。缺失 required 字段仍是错误；显式 `null` 不等于缺失，且因 v1 无 null 类型而被拒绝。
3. 用完整 schema 校验唯一 effective arguments object。
4. 校验 ToolCall 批次/组合规则。
5. 对 effective arguments 执行敏感扫描。
6. 使用同一个 object 完成 hash、持久化、Approval 展示和执行。

不得把 raw/effective arguments 保存为两套竞争表示。`arguments_json` 只保存已扫描的 effective object；可选 `defaulted_field_paths` 只保存固定 JSON Pointer 路径，不保存值。Schema 错误只返回已扫描的安全路径和允许字段名，绝不返回未知值。

### 1.3 Canonical ToolResult

每个已创建 ToolCall 到达终态后都必须恰好有一个协议无关、不可变的 UTF-8 base canonical JSON envelope；Run 继续时再由它生成模型 projection：

```text
schema_version
tool_name
outcome = success|error|skipped|rejected|interrupted|unavailable
code nullable
summary nullable
data
model_content_truncated = true|false
side_effects_may_exist = true|false
```

`schema_version` 在 MVP 固定为整数 `1`；只有顶层字段、类型或 outcome 语义发生不兼容变化时递增。Per-tool data 变化只递增 `tool_contract_version`。Canonical JSON 固定为 Eidos Canonical JSON v1：无 BOM/多余空白的紧凑 UTF-8，array 保序，object key 递归按未转义 UTF-8 bytes 升序排列。该字节规则属于 `model_request_contract_version`；规则变化必须同步递增该版本。

Canonical JSON v1 只接受 Unicode scalar value；拒绝孤立 high/low surrogate，不做 NFC、NFD 或其他 Unicode normalization。String 固定转义 `\"`、`\\` 和 U+0000..U+001F；其中 backspace/tab/LF/form-feed/CR 使用 `\b|\t|\n|\f|\r`，其余控制字符使用小写 `\u00xx`。不转义 `/`、非 ASCII、U+2028 或 U+2029。Parser 必须在构造 map 前递归拒绝重复 key。

所有 ToolResult JSON integer 必须落在 `[-9007199254740991,9007199254740991]`；工具专属 schema 可以进一步收紧。Canonical lexical form 只能是十进制整数，不允许小数点、指数或 `-0`。真实工具域的非负测量超过上限时使用共享 `error/tool_result_numeric_limit_exceeded,data={}`；尚未开始的只读/副作用操作保持 `side_effects_may_exist=false`，已经开始的操作继续使用实际终态和 Q136/Q150 对账规则。错误类型、负数或内部 projector bug 不得伪装成测量溢出，仍进入 `tool_result_contract_violation`。

Python 与 JavaScript 必须使用专用 serializer，禁止依赖语言默认 JSON 行为。固定跨实现向量至少覆盖补充平面 scalar、BMP/补充字符 key 排序、NFC/NFD、全部转义类别、U+2028/U+2029、孤立 surrogate、递归重复 key、safe integer 边界和非法整数 lexical form。

`outcome` 是面向模型的终态投影，不是数据库状态副本：`succeeded -> success`；`failed|timeout|invalidated -> error`；`skipped -> skipped`；Approval rejected -> `rejected`；interrupted -> `interrupted`；请求后的 capability race -> `unavailable`。

- `success` 的 `code` 必须是 JSON `null`；其他 outcome 的 code 必须是非空 snake_case 枚举。
- 每个 `(tool_contract_version,tool_name,outcome)` 声明闭合允许 code 集；共享 code 如 `no_changes|approval_rejected|runtime_interrupted|tool_unavailable|tool_result_numeric_limit_exceeded` 具有全局唯一语义。
- Provider/OS 原始 code、errno、异常类名和任意动态字符串必须先映射，绝不直接进入模型可见 code。模型、UI、指标和状态机只能精确匹配枚举。
- `summary` 可为 null；非空时由 `(tool_contract_version,tool_name,outcome,code)` 唯一决定，只插入这些固定枚举，单行 UTF-8 <=1,024 bytes，禁止 CR/LF、C0 和 DEL，仍须扫描。路径、stdout/stderr、Reject feedback、原始错误和未知参数值不得进入 summary。

`data` 始终是 object，允许 `{}`，不得为 null/array/scalar。Schema 按 `(tool_contract_version,tool_name,outcome,code)` 选择并递归闭合；可选字段不适用时省略而不是填 null。禁止动态 key/free-form map。所有 string/array/nested object 有明确容量；array 保留业务顺序，无业务顺序的集合使用契约声明的稳定排序。

内部 ToolCall UUID 不包含在 envelope 中。Adapter 只在 wire envelope 中使用 `provider_call_id`，不得在 JSON 内重复。

不存在第二条模型可见自由文本结果路径。内部 `result_text`、日志和 UI 审计详情可以按既有安全规则独立保留。只读批次按 `batch_order` 为每个 ToolCall 生成一个 base。非法/敏感/非法组合批次不创建 ToolCall row，因此也不生成 synthetic ToolResult。终态/已取消 Run 无须向 Provider 发送 projection，但仍须持久化真实内部终态和 base。

### 1.4 ToolResult 契约故障

每个 projector 先按选定 result schema、容量和敏感规则验证，再提交 base envelope。无法生成合法结果时禁止发送 malformed JSON 或自由文本 fallback：

- 原子保存真实 ToolCall 终态、durable intent、已知后置条件和 `side_effects_may_exist`。
- Run 直接 `failed/tool_result_contract_violation`，零工具重试、零模型续接、零 Finalization；Model Profile snapshot 不失效。
- Per-tool data projector/schema 映射失败时持久隔离 `(tool_contract_version,tool_name)`；顶层 envelope、canonical serializer 或共享 summary/code 映射失败时隔离整个 ToolResult capability。
- Quarantine 跨普通重启保留。只有新 build/tool contract 实现通过对应确定性回归自检后才能清除；新 Step/Run 的 available tool set 必须排除被隔离能力。
- UI 使用独立 Runtime 故障卡；若副作用可能存在，引导新 Run 先只读核验，不得把内部 result_text 当作模型结果。

### 1.5 副作用工具结果 schema

所有 `path|source_path` 均为 active-root-relative UTF-8，最大 4,096 bytes；所有 SHA-256 均为 64 个小写 hex 字符。文件工具只有在提交后以相同父目录身份重新打开/查询目标并验证后置条件后，才能生成 success：

```text
write_file success = {
  path,
  change_kind = created|overwritten,
  old_sha256?,
  new_sha256,
  new_size_bytes
}

apply_patch success = {
  path,
  old_sha256,
  new_sha256,
  new_size_bytes,
  applied_hunk_count
}

delete_file success = {
  path,
  old_sha256,
  old_size_bytes,
  postcondition = absent
}
```

`write_file` created 时省略 `old_sha256`，overwritten 时必须存在；`applied_hunk_count` 为 `1..200`。new hash/size 必须来自提交后实际文件，delete absent 必须在已验证父目录身份下复检，不能回显候选预测值。成功结果事务按既有规则失效旧读取证据，但 ToolResult 不返回 Approval、durable intent、证据失效布尔值、临时路径、inode/mode/ACL/xattr 或 OS metadata。

已有目标的 write/apply 候选最终字节与当前字节完全相同时固定：

```text
outcome=skipped
code=no_changes
data={path,base_sha256}
side_effects_may_exist=false
```

`base_sha256` 来自 skipped 结果事务前最后一次路径身份与当前字节复检，并与候选 hash 相等；比较后发生变化返回 `file_version_conflict`。该结果零 Approval、零 durable intent、零文件接触，不失效读取证据、不增加或清零 Reject 计数、不设置事实确认屏障，但计入 Step 预算。新建空文件仍是 `success/created`。

`publish_artifact` success data 固定：

```text
{
  artifact_id,
  source_path,
  display_name,
  artifact_type,
  version,
  snapshot_sha256,
  size_bytes
}
```

`artifact_id` 是每次发布新生成且永久稳定的 canonical UUID，不编码 Session、源路径或 snapshot 位置；version 为同 Session/source path 下正整数。Success 前必须满足稳定源与复制后重开快照的 SHA-256 和逻辑字节数完全相同，字节数包含可选 BOM 且 <=32 MiB。ToolResult 不返回 source hash 的重复副本、Artifact URL、真实 snapshot path、MIME、summary、encoding/BOM、mode 或 allocated size。

### 1.6 Shell ToolResult

已启动 `run_shell` 的 success/error/interrupted data 使用同一闭合骨架：

```text
termination_reason = exited|timeout|canceled|resource_limit|output_capture_failed|process_signaled|runtime_interrupted
duration_ms
exit_code?
limit_kind?

stdout_observation_bytes, stdout_returned_bytes, stdout_truncated
stdout_head?, stdout_tail?
stderr_observation_bytes, stderr_returned_bytes, stderr_truncated
stderr_head?, stderr_tail?
output_redacted

workspace_changes = {
  attribution = observed_during_shell_window,
  created_count, deleted_count,
  content_modified_count, metadata_modified_count, type_changed_count,
  paths, omitted_path_count,
  manifest_incomplete,
  git_boundary_change_detected,
  protected_path_change_count
}
```

- `duration_ms` 是受控进程组持续时间的非负整数。`exit_code` 当且仅当 termination_reason=exited 时存在，范围 `0..255`。`limit_kind` 当且仅当 resource_limit 时存在，闭合为 `process_count|file_descriptors|memory_bytes|file_size_bytes|disk_growth_bytes`。非 Runtime 主动 signal 只映射 `process_signaled`，不返回 signal number。
- `*_observation_bytes` 是完整流经脱敏后的安全 UTF-8 字节数，位于工具级 head/tail 容量裁剪前；`*_returned_bytes` 是 base ToolResult 四个 observation string 实际字节数；`*_truncated` 严格等价于 observation>returned。Raw pipe byte counts 只留在受控内部审计，不进入 ToolResult、Event 或模型上下文。
- Base observation 沿用 32 KiB、stderr 优先最多 16 KiB、余量给 stdout、流内 head+tail 的 Q80 规则。空 string 字段省略；`output_redacted=true` 当任一流发生替换。Context projection 只裁四个正文 string，不修改 observation/returned/truncated。
- `workspace_changes.paths` 最多 50 项，每项闭合 `{path,change_kind}`，change_kind 为 `created|deleted|content_modified|metadata_modified|type_changed`，按 path/change_kind 稳定排序。普通分类计数、paths 和 omitted count 只覆盖非敏感且非 `.git` 的安全变化；protected 只返回聚合计数，`.git` 只返回布尔值。
- `manifest_incomplete=true` 时普通计数和 paths 只是已观察下界；顶层 side effects 为 true 并进入事实确认。未启动 Shell 使用 code 专属较小 data schema，不伪造 duration、output 或 manifest。

Shell outcome/code 固定映射与优先级：

1. 数据库真实终态 interrupted：`interrupted/runtime_interrupted`。
2. writable post-manifest 不完整：`error/workspace_change_manifest_incomplete`。
3. 输出捕获失败：`error/output_capture_failed`。
4. 资源限制：`error/shell_resource_limit_exceeded`。
5. timeout：`error/tool_timeout`。
6. 非 Runtime signal：`error/shell_process_signaled`。
7. exit code 非零：`error/shell_exit_nonzero`。
8. exit code 0 且 manifest 完整：`success/null`。

低优先级事实仍保留在 termination/manifest 字段。`.git` 或 protected 变化只设置事实确认屏障；若进程 exit 0 且 manifest 完整，ToolResult 仍为 success。未启动时用户拒绝为 `rejected/approval_rejected`，审批过期为 `error/approval_expired`，capability race 为 `unavailable/tool_unavailable`，Seatbelt/资源监控/preflight/版本复检使用既有精确 error code；Run 启动前取消为 `interrupted/runtime_interrupted`。

### 1.7 Side-effect 与非成功 data

`side_effects_may_exist` 表示在 Eidos 工具契约管理的物质性效果或获批外部系统中，可能存在当前 canonical ToolResult 与已验证后置条件尚未完整确认的副作用；它不表示工具是否属于可写类型，也不包含受控临时文件等实现元数据。

- false：只读工具、rejected/unavailable、明确未启动的 interrupted/error、skipped/no_changes、verified not_applied、write/apply/delete/publish 的 verified success。
- true：commit 后无法确认或 outcome_unknown，以及任一已启动 Shell 的任何终态。
- `reconciliation_required=true` 必须对应 side effects=true；反向不成立。正常 exit 0、manifest 完整且无 `.git`/protected 异常的 Shell 可 side effects=true 而不进入屏障。状态机必须结合 outcome/code/manifest 判定，禁止只读该布尔值。

共享非成功 data 固定：

- `rejected/approval_rejected`：`{user_feedback?}`。仅用户实际提供时存在，1..4,096 UTF-8 bytes；使用 Approval API 同一敏感扫描，原文返回且不得截断或被 Context 单独裁剪。命中或扫描失败时 Reject 事务不成立，也不生成结果。
- `unavailable/tool_unavailable`：`{}`。
- `interrupted/runtime_interrupted`：默认或未启动为 `{}`；已启动 Shell 必须使用 1.6 的专属 data。

不增加 generic retryable、recovery_action、suggested_action 或 phase；恢复取决于 Run 状态、gate、available tool set 与事实确认屏障。

### 1.8 Error data registry

ToolResult Error 与 API Error 是独立契约。ToolResult 禁止通用 `details|message|cause`、动态 map、raw stack/errno/Provider/OS message；每个 `(tool,error,code)` 默认 data=`{}`，只有模型确定性恢复所需且原 ToolCall 不含的安全事实才能进入 code 专属闭合 schema。API Error 可保留 `code,message,retryable,details,request_id` envelope，但 details 也必须按 API code 闭合、有界和安全，且绝不进入模型 ToolResult。内部诊断同样必须扫描、限长和访问隔离，不能默认原样持久化。

只读工具的非空 error data 只有：

```text
file_too_large_for_read_file = {actual_size_bytes,max_size_bytes}
line_too_large = {line_number,max_line_bytes}
```

前者的稳定 size 来自同一 fd，max 固定 2 MiB。后者必须先完成敏感扫描、编码与稳定性校验；deny/扫描失败优先。Range 选择请求范围内第一个超限行；read head/tail 两端均超限时选择较小源行号；read 完整模式 max=256 KiB，head/tail max=128 KiB。行号按 Q128 的 LF 规则且 1-based。其他只读 error data 均为 `{}`，所有错误零正文、零 evidence/matches/entries。

写/Patch/删除/Artifact 的非空 error data 只有：

```text
invalid_unified_diff = {safe_reason,hunk_index?}
patch_read_evidence_missing = {hunk_index}
approval_diff_too_large = {
  limit_kind,
  actual_diff_bytes,max_diff_bytes,
  actual_diff_lines,max_diff_lines
}
file_too_large_for_safe_write = {actual_size_bytes,max_size_bytes}
```

`safe_reason` 闭合为 `transport_not_lf|invalid_header|path_mismatch|multiple_files|unsupported_git_extension|invalid_hunk_header|invalid_hunk_body|overlapping_hunks|invalid_no_newline_marker`；hunk index 只返回按 Patch 顺序第一个失败项，能归属时为 `1..200`，不能归属时省略。Diff `limit_kind=bytes|lines|both` 必须与 actual/max 比较严格一致，四个计数均来自已扫描的完整 Runtime Diff。safe-write actual/max 是稳定候选逻辑字节且 actual>max。`file_version_conflict`、父目录/类型/编码/元数据/审批过期及 Artifact format/source change/corruption 等 data 为 `{}`；不返回 current/source/snapshot hash、格式类别、内部路径或 recommended tool。

## 2. 工具注册表

| Tool | Mode applicability | Side effect | Batch | Approval |
|---|---|---|---|---|
| list_files | workspace, public | none | yes | no |
| read_file | workspace, public | none | yes | no |
| read_file_range | workspace, public | none | yes | no |
| search_text | workspace, public | none | yes | no |
| write_file | workspace, public | workspace | no | yes |
| apply_patch | workspace, public | workspace | no | yes |
| delete_file | workspace, public | workspace | no | yes |
| run_shell | workspace, public | shell | no | yes |
| publish_artifact | workspace, public | eidos_state | no | no |

Public Mode 的 `active_root` 是 Session 内部 `files/`，因此九个工具使用与 Workspace Mode 相同的 active-root 安全契约；差异只在 Renderer 不展示底层文件树且最终产物必须显式发布。Mode applicability 是确定性工具集输入，不能替代 reconciliation、capability health、审批或执行前复检。

## 3. Workspace Guard

所有路径参数必须：

1. 是相对于 active root 的规范化相对路径。
2. 拒绝绝对路径和 `..`。
3. 使用 `Path.resolve()` 后通过 `relative_to(root)` 验证。
4. 对最终路径和每个已存在父目录检查符号链接逃逸。
5. 在文件打开后校验文件描述符对应的真实路径/类型，降低 TOCTOU。
6. 逐段列举父目录并将输入段与文件系统返回的真实目录项名称精确对应；不小写化、不静默 NFC/NFD 转换、不使用模糊 case-fold。
7. 输入拼写与真实目录项不一致、在当前卷语义下无法唯一对应，或文件名无法用 JSON UTF-8 安全表示时，分别返回 `path_spelling_mismatch|ambiguous_path|unsupported_filename_encoding`。

字符串 `startswith` 不能用于路径边界判断。

## 4. 只读工具

### 4.1 list_files

- 参数为 `path=".", max_depth=2, max_entries=500`；`max_depth` 最大 5，`max_entries` 最大 2,000，请求目录自身深度为 0。
- `path` 必须是 active root 内经 Guard 验证的真实目录；请求目录不存在或不可访问时整次失败。
- Success data 是闭合 object：`root_path, entries, excluded_directory_count, depth_limited_directory_count, skipped_permission_count, truncated, workspace_changed`，以及仅在 truncated 时存在的 `stop_reason=max_entries`。`root_path` 是 active-root-relative 请求目录，绝不含绝对路径，UTF-8 <=4,096 bytes。
- `entries` 最大 2,000，按下述稳定顺序；每项为闭合 object `path,type=file|directory|symlink|other,read_only`，其中 path UTF-8 <=4,096 bytes。Regular file 额外包含 `size_bytes,executable`，其他类型省略这两个字段。`read_only` 是策略投影，不是写入授权、版本或读取证据。
- 按规范化相对路径逐层字典序稳定遍历，同一父目录中目录先于其他类型。
- 不跟随 symlink，不返回 target。`.git` 只返回顶层入口并设置 `read_only=true`，不展开内部。
- 普通隐藏文件正常列出；命中敏感路径/文件名硬拒绝规则的条目完全隐藏，不返回名称、类型或单项原因。
- 目录性能排除使用 4.6 的共享固定集；不提供 `include_excluded`。
- 只有仍有可见条目因 `max_entries` 被省略时才设置 `truncated=true,stop_reason=max_entries`；恰好返回上限且遍历完成仍为 false。计数只覆盖已遍历前缀。Context projection 裁剪 entries 不修改工具级 truncated/stop_reason。
- 根目录开始/结束身份稳定但检测到子树变化时 `workspace_changed=true`；false 只表示未检测到，不承诺快照。敏感隐藏项不返回条目，也不进入任何模型可见计数。
- 遍历中单个子目录权限失败只增加 `skipped_permission_count` 并继续。
- Workspace UI 与 Agent Tool 复用同一路径边界 Guard，但使用不同的可见性投影：Agent `list_files` 按上述规则隐藏敏感条目；用户的 Workspace 文件树可显示受保护条目并标记 protected，但文件名不进入 Agent/模型上下文，正文预览仍受敏感读取规则限制。

### 4.2 read_file

| 文件大小 | 行为 |
|---|---|
| <= 256 KiB | 完整返回，`complete=true` |
| 256 KiB..2 MiB | 返回合计最多 256 KiB 的 head+tail，`complete=false` |
| > 2 MiB | 拒绝，返回 `file_too_large_for_read_file` 并要求 `read_file_range` |

大小按原始字节计算。head 和 tail 各最多 128 KiB 且合计不超过 256 KiB，不在 UTF-8 字符或行中间截断。边界单行本身超出可用配额时返回 `line_too_large`。

Success data 是闭合 object：

```text
read_result_id, path, base_sha256, size_bytes, total_lines
encoding=utf-8, bom, newline_style, complete, content_redacted
segments, evidence_ranges, omitted_evidence_range_count
returned_content_bytes, omitted_source_bytes
```

- `segments` 按源行升序，空文件为 `[]`，完整非空文件恰好 1 段，head+tail 恰好 2 段；每段为闭合 `{start_line,end_line,content}`。
- `segments[].content` 经脱敏后的 UTF-8 字节合计 <=256 KiB，head/tail 各 <=128 KiB，不含 JSON escape/envelope 字节，不拆字符或行。`returned_content_bytes` 使用该口径；`omitted_source_bytes` 统计未进入 segments 的原始正文字节，不能由 size 减 returned 推导。
- `evidence_ranges` 每项为闭合 `{start_line,end_line}`，是 segments 对应源行与“整行未发生任何脱敏”的交集，按行升序，模型最多看到 1,024 段；额外段数写入 `omitted_evidence_range_count`。Runtime 保存完整证据，但 read_result_id 只授权完整内部 evidence ranges，绝不授权未返回或被脱敏行。
- `content_redacted=true` 表示全文件任意位置存在 redact 命中，包括未返回中段；因此不能授予 write_file 完整覆盖资格。`complete` 只表示工具是否返回完整源正文，不受后续 Context projection 改变。
- 行号只按 LF 字节分隔；CRLF 中 CR 属于 terminator，裸 CR 不分行。空文件 0 行；非空文件 `total_lines=LF_count + (last_byte_is_LF ? 0 : 1)`。

二进制直接拒绝。文本文件在返回任何正文前执行最多 32 MiB 的全文件流式敏感扫描；任意位置命中 `deny` 时整次读取拒绝，只命中 `redact` 时替换对应片段后返回其余内容。

读取使用单个 `O_NOFOLLOW` 文件描述符。开始/结束 `fstat` 的 dev/inode/size/mtime_ns/ctime_ns 不一致时丢弃正文和证据，按只读瞬时错误自动重试一次；再次变化返回 `file_changed_during_read`。

### 4.3 read_file_range

参数：`path, start_line, end_line`。行号 1-based，区间为闭区间；`start_line < 1` 或 `end_line < start_line` 返回 `invalid_line_range`。

- 单次请求最多 2,000 行，返回正文最多 256 KiB，任一先到即截断。
- Success data 复用 read_file 的 path/hash/size/line/encoding/BOM/newline/redaction/evidence 字段，并固定 `complete=false`；另含闭合 `requested_range={start_line,end_line}`、`segments,returned_content_bytes,truncated`。非空结果恰好一个 segment，并包含与其行界完全相同的闭合 `actual_range={start_line,end_line}`。
- `start_line > total_lines`（含空文件）返回 `segments=[]、truncated=false`，省略 `actual_range,stop_reason,next_start_line`；`end_line` 越过 EOF 时收缩到最后一行。
- 只有 2,000 行或 256 KiB 上限使请求范围/EOF 尚未返回完时才设置 `truncated=true`，并同时包含 `stop_reason=max_lines|max_bytes` 与 `next_start_line=actual_range.end_line+1`。next 必须仍在请求范围和 EOF 内；否则这些字段全部省略。
- 若已返回 2,000 行且仍有下一行，或行数/字节同边界触发，固定 `max_lines`；否则下一完整行会使内容超过 256 KiB 时使用 `max_bytes`。
- 字节上限不返回半行；单行超过 256 KiB 返回 `line_too_large`，MVP 不提供残缺行。
- 识别 LF/CRLF 并保留原始换行，不静默规范化。UTF-8 BOM 不计入第一行正文。
- 即使只返回局部行，也必须先完成 32 MiB 上限下的全文件扫描；不能只扫描请求范围。
- 与 `read_file` 使用相同的单 fd 前/后稳定性校验和一次重试；失败时零正文、零读取证据。

### 4.4 search_text

- 参数为 `query, path=".", max_results=100`；`max_results` 最大 500。
- `query` 必须是单行、非空、最大 512 字节的 UTF-8 literal；换行或 NUL 返回 `invalid_search_query`。MVP 不解释 regex、glob 或转义序列。
- 默认大小写不敏感仅折叠 ASCII `A-Z`；非 ASCII 按原值精确匹配。
- `path` 必须是 active root 内受 Guard 验证的目录。候选文件按规范化相对路径字典序遍历，文件内按行、列排序。
- 目录和 lock 性能排除使用 4.6 的共享固定集；同时跳过二进制和敏感路径。
- Success data 是闭合 object：`root_path,matches,excluded_directory_count,excluded_lock_file_count,binary_file_count,unsupported_encoding_file_count,failed_file_count,changed_during_scan_count,scanned_file_count,scanned_bytes,truncated,workspace_changed`，以及仅在 truncated 时存在的 `stop_reason=max_results|scan_budget_exceeded`。这些字段是模型可见安全计数，不等于内部预算消耗；安全隐藏路径和 deny 文件不进入任一返回计数。
- `matches` 最大 500，按稳定 path/line/column 顺序；每项闭合 `{path,line,column,byte_offset,preview,preview_start_column,file_sha256,match_preview_truncated}`，root/match path 均为 active-root-relative UTF-8 <=4,096 bytes。line/column 1-based 且 column 按 Unicode code point；byte_offset 0-based，按含 BOM 原文件字节。
- preview 经脱敏后最多 300 Unicode code points/1,200 UTF-8 bytes且不拆字符。原匹配 <=300 code points 时优先完整包含匹配，剩余额度确定性分配给前后文；匹配本身更长时从 match start 返回前 300 code points，设置 `match_preview_truncated=true,preview_start_column=column`。
- 只有扫描提前停止且仍有安全候选未完成时才 `truncated=true`。完整扫描恰好得到 max_results 个结果仍为 false；结果上限和扫描预算同边界时固定 `max_results` 优先。MVP 无搜索游标。
- 每个候选文件完成全文件扫描后才能返回该文件的结果；`deny` 命中的文件零结果，只命中 `redact` 时 preview 脱敏后返回，并继续搜索其他文件。
- 单次最多扫描 256 MiB 或 15 秒；达限只返回已完成整文件扫描的结果。模型可见 `scanned_file_count` 只计稳定、完成扫描且通过安全投影的文本文件，包括零匹配和仅发生 redact 的文件；`scanned_bytes` 对这些文件各按稳定原始大小计一次，不含重试、部分读取、deny/敏感、二进制、unsupported encoding、I/O 失败或变化文件。
- Runtime 另存内部 `budget_consumed_bytes`，累计为本次搜索实际读取的所有原始字节，包括重试、部分读取以及最终因 deny、失败或变化而丢弃的字节，并以此执行 256 MiB 上限。该值只进入受控 `result_json`/metrics，不进入 ToolResult、Event 或模型上下文。
- `failed_file_count` 只计路径本身可安全展示、且不属于 binary/unsupported encoding/changed 分类的权限或 I/O 失败；性能排除计数只描述可见的共享排除集。敏感路径和安全 deny 结果始终零返回计数。
- 每个候选文件使用单 fd 前/后稳定性校验，变化时仅重试该文件一次；再次变化则丢弃该文件全部匹配、`changed_during_scan_count += 1`并继续。`workspace_changed` 严格等价于该计数大于 0；false 不承诺快照。每个返回项携带稳定版本 `file_sha256`。

文件与搜索扫描共用工具的单一 deadline，扫描时间计入现有 10–15 秒 Tool timeout，不创建可无限延长的隐藏扫描阶段。

### 4.5 文本编码与二进制

- 读取、搜索、write/apply/delete 只处理严格 UTF-8 或带 UTF-8 BOM 的普通文本文件。
- 任意 NUL，或文件开头命中 PNG/JPEG/GIF/ZIP/gzip/PDF/ELF/Mach-O/SQLite/WASM 固定 magic signature，直接返回 `binary_file_not_supported`。
- 除 TAB/LF/CR/FF 外的 C0/DEL 控制字节同时满足 `count >= 8` 且占原始字节 `>1%` 时按二进制拒绝。
- 未命中二进制规则但严格 UTF-8 解码失败时返回 `unsupported_text_encoding`，禁止使用替换字符继续。
- 文件大小和所有容量上限按原始字节计算；行/列在成功解码后计算。
- BOM 不进入正文，但返回 `encoding=utf-8, bom=true|false`。修改已有文件保留 BOM，新文件统一 UTF-8 无 BOM。
- `apply_patch` 执行前除 hash 外再次检查 encoding/BOM；变化时 Approval 失效。
- `search_text` 跳过二进制和不支持编码的文件，分开计数并继续。`list_files` 仍可返回这些条目的安全元数据。
- UTF-16/UTF-32/GBK/GB18030/Big5 和字节范围读取属于 P1，MVP 不自动转换。

### 4.6 共享排除策略

- 安全排除（敏感路径、`.git` 内部、symlink 逃逸、特殊文件）先于性能排除，永不可绕过。
- 目录集为完整路径段、大小写敏感的 `node_modules, .venv, venv, __pycache__, dist, build, target, .next, coverage`，不误伤 `build-tools` 或 `targeting`。
- `search_text` 额外跳过精确文件名 `package-lock.json, npm-shrinkwrap.json, yarn.lock, pnpm-lock.yaml, Pipfile.lock, poetry.lock, uv.lock, Cargo.lock`；`go.sum` 不在排除集。
- MVP 不解析 `.gitignore, .ignore` 或全局 Git ignore，不接受用户自定义和 `include_excluded`。ignore 文件本身正常列出/读取/搜索。
- 显式将 list/search `path` 指向性能排除目录时返回 `excluded_path`。已知具体文件仍可由 read/range 或受审批写工具直接访问。
- `list_files` 可显示 lock 文件名和安全元数据，只有 search 不扫描其正文。返回分类跳过数，不暴露安全隐藏条目名称。

### 4.7 Workspace 并发变化

- MVP 只保证单文件结果来自一个稳定版本，不保证只读批次或整个 Workspace 是时间点快照。
- `list_files` 记录请求目录开始/结束的 dev/inode/mtime_ns/ctime_ns。请求目录被移动、替换或删除时整次返回 `directory_changed_during_list`；只检测到子树变化时保留已有条目但设置 `workspace_changed=true`。
- `search_text` 同样绑定请求根开始/结束身份；根移动、替换或删除时整次返回 `directory_changed_during_search`，不返回部分 success。
- list/search 结果不是文件存在性、版本或写入证据。每个读取/搜索结果保留自己的 hash/时间，Context Builder 不合并为“Workspace 当前版本”。

## 5. 文件写工具

共同规则：

- 一次 ToolCall 只作用于一个普通文件。
- 必须是模型响应中的唯一 ToolCall。
- 审批卡展示路径、理由、Runtime 生成的完整 unified diff 和版本前置条件；不使用模型提供的 diff 作为事实来源。
- 批准后执行前重新校验。
- 写入内容/候选文件/Diff 在创建 Approval 前完成所有校验；Diff 超过 512 KiB 或 5,000 展示行时返回 `approval_diff_too_large`，不创建 Approval，不允许截断展示后继续。
- 候选文件最大 32 MiB，超限返回 `file_too_large_for_safe_write`。Runtime 先在内存或受控临时文件生成完整候选内容，再校验编码、类型、敏感规则和容量。
- Diff 保留 LF/CRLF 差异并标记文件末尾是否缺少换行。新文件从 `/dev/null` 生成，删除到 `/dev/null`。
- 使用同目录临时文件、fsync 和原子 replace；commit 区不可取消。
- 受控临时文件在成功或已知失败后立即清理；进程崩溃才进入启动对账清理。
- 目标的已存在父目录链在审批时保存 `relative_path, st_dev, st_ino, mode, uid, gid, acl_digest`；执行时使用不跟随 symlink 的逐段打开重建相同链。移动、替换、删除或权限/所有者/ACL 变化返回 `file_version_conflict`。
- 已有目标必须满足 `st_nlink == 1`；多链接文件返回 `hardlink_not_allowed`。
- 已有目标必须 `st_uid == geteuid()`；其他所有者返回 `file_owner_not_supported`。`UF_IMMUTABLE|SF_IMMUTABLE|UF_APPEND|SF_APPEND` 任一存在时返回 `file_flags_not_supported`。
- 修改已有文件时，临时文件必须复制并验证 gid、POSIX mode、ACL、全部 extended attributes 和可保留 file flags；任一复制/验证失败返回 `file_metadata_preservation_failed`，不进入 replace。
- 新文件 uid 为当前用户，gid 遵循父目录 setgid/系统创建语义，mode 仍强制 `0644`。
- 容量、扫描和 Diff 上限使用逻辑字节 `st_size`。同时记录 `allocated_bytes=st_blocks*512`，但文件工具不以 allocated size 放宽 logical size 上限。
- 完整 content/patch 在创建 ToolCall/Approval 前扫描；`deny` 或 `redact` 命中时不保存原参数，也不使用占位符内容继续执行。
- 对已有文件，候选最终字节 SHA-256 与当前 SHA-256 相同时不创建 Approval/durable intent，ToolCall 以 `outcome=skipped,code=no_changes` 结束并追加 `tool_call_skipped`。在结果事务前发现文件变化则改为 `file_version_conflict`。
- no-op 不清零 Reject 计数、不设置事实确认屏障、不使读取证据失效，但正常计入 Step 预算。新建空文件仍是真实副作用，不是 no-op。

### 5.1 write_file

- `content` 最大 512 KiB，按 UTF-8 编码后字节计算。
- `content` 是逻辑 Unicode 文本，拒绝 NUL 和孤立 surrogate。新文件只允许 LF；CRLF、裸 CR 或 mixed 返回 `invalid_new_file_newline_style`，末尾换行完全由 content 决定。
- 创建新文件：`expected_absent=true`。
- 目标父目录必须已存在；不存在时返回 `parent_directory_not_found`，不隐式创建任何目录。
- 新文件使用独占创建并固定 mode `0644`，不自动设置 executable bit。
- 不能替换目录、symlink、socket、FIFO 或 device。
- 完整覆盖已有文件：原文件必须 <=256 KiB，并引用当前 Run 中 `complete=true, content_redacted=false`、路径/hash 一致的 `read_file` 读取结果 ID。
- range、head+tail、search preview 和 UI 预览不构成完整覆盖证据；证据绑定 `run_id + path + base_sha256`，可跨 Step/Segment 但不可跨 Run。
- 不满足完整读取时返回 `full_overwrite_requires_complete_read`。
- 覆盖 LF 文件时 `newline_style` 必须为 `lf`且 content 不得含 CR；覆盖 CRLF 文件时参数必须显式 `newline_style=crlf`，Runtime 将逻辑 LF 行编码为 CRLF。原文件 mixed 或声明不匹配返回 `mixed_newlines_not_supported_for_write|newline_style_mismatch`。
- BOM 不属于 content，按原读取结果保留。candidate SHA-256/Diff 必须基于最终 BOM+换行编码后字节。
- 已有文件默认优先使用 apply_patch。

### 5.2 apply_patch

- 只修改一个已存在普通文件。
- patch 最大 256 KiB、5,000 行、200 hunks，任一超限即拒绝。
- `patch` 必须是严格 UTF-8、LF 传输的单文件 unified diff，包含与 ToolCall 规范化路径完全一致的 `--- a/{path}` / `+++ b/{path}`。
- 只支持标准 `@@ -old_start,old_count +new_start,new_count @@`，省略 count 按 1。拒绝多文件 section、rename/copy/mode change、binary patch、submodule 和任何 Git 扩展 header。
- 参数包含 `base_sha256` 和一个或多个 `read_result_ids`。Runtime 将读取结果解析为同一 `run_id + path + base_sha256` 下的行区间并集。
- 每个 hunk 的原文件侧删除行和上下文行必须完全位于该并集内；任何发生脱敏命中的整行在建立证据时从区间中扣除。search preview、list_files、UI 预览和模型自报行号不是读取证据。
- 纯插入 hunk 必须锚定已读取的相邻行；BOF/EOF 需已读取对应边界，空文件需完整空文件读取。无可验证锚点返回 `patch_read_evidence_missing`。
- 只在声明行号上精确匹配原文，禁止 offset 搜索和 fuzz matching。应用前先在候选副本验证全部 hunk 成功，再进入 commit。
- 目标换行只允许 `lf|crlf|none`；`mixed` 返回 `mixed_newlines_not_supported_for_patch`。Patch 行正文匹配解码后文本，Runtime 按原统一换行编码新/替换行，未命中原始字节区间保持不变。
- 支持标准 `\ No newline at end of file`标记；缺少明确标记时不得意外改变末尾换行。BOM 始终位于 Patch 行模型之外并保留。
- 解析失败返回 `invalid_unified_diff`，ToolResult data 只含闭合 `safe_reason` 和可选 `hunk_index`；不生成部分候选文件或 Approval。Runtime 最终审批 Diff 始终从原始/候选字节重新生成。
- 使用保留原 mode、ACL 和 xattr 的临时副本完成原子替换；不要求保留 inode number。

### 5.3 delete_file

- 不要求读取证据，但只删除一个 <=512 KiB、严格 UTF-8/可选 BOM、`st_nlink=1` 的已存在普通文件。
- 禁止目录、递归、通配符、批量和 symlink 本体/目标混淆。
- 二进制、不支持编码、敏感或无法在 Diff 上限内完整展示的文件拒绝；不提供截断 Diff 审批。
- Runtime 生成完整 `file -> /dev/null` Diff。审批绑定规范化 path、父目录链身份、file_type、size、encoding/BOM 和 `base_sha256`。
- 执行时不跟随 symlink 打开并复检，然后先提交 durable intent，再删除已验证目录项。后置条件为该目录项不存在。
- 崩溃恢复只根据 intent 和目录项现状对账；若同路径出现新文件，标记 outcome_unknown，绝不再执行删除。
- 明确删除任务的 Agent 策略必须优先调用 delete_file。

`run_shell` 中的间接删除不构成沙箱违规；明显 `rm` 可在审批卡警告，但命令识别不是安全边界。

任一 write/apply/delete 成功后，Runtime 使该路径旧 hash 下的所有读取证据失效。同路径后续出现的新文件不继承旧证据或 Approval。

目录创建/删除不新增文件工具，由独占且受审批的 Shell 执行。Runtime 内部同目录临时文件使用包含 `tool_call_id + execution_nonce` 的保留命名并记入 durable intent；启动恢复只清理名称、inode/目录身份与 intent 全部匹配的残留，不删除任意同名用户文件。

## 6. publish_artifact

参数：

```text
path
display_name
artifact_type = text|markdown|json|csv|html|code
summary
```

执行规则：

- 只能发布 active root 内普通文件。
- 源文件必须 <=32 MiB，通过严格 UTF-8/可选 BOM、二进制检测和全文件敏感扫描。二进制、PDF/Office、压缩、加密或需格式解析才能完整扫描的容器返回 `artifact_format_not_supported`。
- 必须独占模型响应，但不需要审批。
- `display_name` 为 1..255 个 Unicode code point，拒绝路径分隔符、NUL/控制字符和 `.`/`..`；它只是展示名，不参与快照路径。
- 复制到 `state_root/artifacts/{artifact_id}/`，不能保存 symlink 或动态引用。
- 复制前后校验源文件 stat/hash；变化则返回 `artifact_source_changed`，不发布不一致快照。
- 快照目录 mode `0700`，文件 mode `0600`；不向 Renderer 暴露真实路径。“不可变”由无修改 API+每次读取 hash 复检保证，快照损坏时返回 `artifact_corrupted`。
- 保存 source_path、source_sha256、snapshot_sha256、logical_size、allocated_size、encoding/BOM、mime、artifact_type、version。HTML/Markdown 预览继续按不可信内容 sanitizer 处理。
- 同一源路径再次发布创建新 Artifact id/version，不覆盖旧快照。
- 复制前对源文件执行全文件敏感扫描；`deny` 或 `redact` 都拒绝发布，不创建经脱敏改写的快照。

## 7. run_shell 契约

输入：

```json
{
  "command": "python -m pytest",
  "timeout_seconds": 120,
  "network": {"allowed_hosts": []},
  "local_network": false
}
```

执行：

```text
/usr/bin/sandbox-exec <policy args> /bin/zsh -f -c <command>
```

- command 是完整字符串，审批卡原样展示。
- command 在创建 ToolCall/Approval 前扫描；敏感命中时 UI 不得接收原 command。
- Runtime 不自动包裹、追加或改写命令。
- Writable Shell 启动前扫描 active root 中普通文件的 link count；存在 `st_nlink > 1` 时返回 `hardlink_not_allowed` 并拒绝启动。
- APFS clone/copy-on-write 不按 hardlink 处理。
- 默认 timeout 120 秒，最大 600 秒，并受 Run 剩余预算限制。
- 不支持持久服务；取消或 timeout 终止整个进程组。
- 删除原“静默 90 秒终止”规则。

### 7.1 Shell Approval 绑定与时效

- Approval 绑定完整 command/arguments hash、active root dev/inode、Seatbelt profile version、环境模板版本、Toolchain Profile ids/versions/PATH、timeout、精确 host/port 和 local_network。
- 它不绑定审批时 Workspace 内容快照，UI 必须原文说明“命令作用于执行时的当前 Workspace”。Runtime 不解析 Shell 语法来猜测脚本/输入文件依赖。
- pending 可持久等待用户。approve 成功时设置 ApiTimestampV1 `approved_at, approval_expires_at=approved_at+300000ms`、boot-session identity 与 continuous-monotonic deadline；continuous 或 wall 任一先到时 Approval -> invalidated，ToolCall 返回 `approval_expired`，不启动进程。同 boot sidecar 重启沿用原 deadline；boot/timebase 不可证明或 clock rollback 按 Q154 失效。
- 一旦 Shell 在时效内开始，5 分钟不再是运行截止时间；由 timeout/Run 预算/资源限制管理。
- 执行前复检 active root、arguments hash、沙箱/环境/Toolchain version 和网络权限；任一变化使原 Approval 失效。Timeline 保存 created/approved/started 时间以解释延迟。

### 7.2 Workspace Manifest

- Writable Shell 的 hardlink 全树扫描同时生成前置 manifest，不使用 4.6 性能排除；最多 200,000 目录项或 30 秒。超限返回 `workspace_preflight_limit_exceeded`，零 durable intent/零执行。
- 普通非敏感条目包含 `path,type,dev,inode,logical_size,allocated_size,mtime_ns,ctime_ns,mode,uid,gid,nlink`。敏感路径不持久化名称或哈希，只保存聚合计数。
- 前置 manifest 以受控文件保存在 Eidos state root，内容先扫描并由 durable intent 引用。intent commit 后才 spawn Shell。
- 进程组结束后生成同规则后置 manifest，计算 `created|deleted|content_modified|metadata_modified|type_changed`。执行后仍存在且变化的 <=32 MiB 普通非敏感文件补充 SHA-256；其他只保存元数据变化。
- `.git` 任何变化单独标记 `git_boundary_change_detected`；敏感区变化只记录 `protected_path_change_count`。两者均作为安全异常进入 waiting_user_input，不泄露隐藏名称。
- 变化归因字段固定为 `observed_during_shell_window`，不声称命令独立造成。UI 默认展示前 200 个安全路径，详情 API 可读完整非敏感列表；模型只得到分类计数、前 50 个安全路径和精确 `omitted_path_count`。
- 后置扫描超限/失败或进程崩溃保留真实 Shell 结果，设置 `change_manifest_incomplete=true, side_effects_may_exist=true`并进入事实确认屏障。对账不重跑命令，不自动回滚。
- 成功提交完整结果后清理 manifest 文件；崩溃恢复完成前保留。

### 7.3 资源限制

- child 启动前设置 `RLIMIT_NOFILE=256, RLIMIT_FSIZE=1 GiB, RLIMIT_CORE=0`。进程数通过同 uid 当前进程数+64 的子进程 RLIMIT_NPROC 尽力限制，并由进程组监控器强制该 ToolCall 后代最大 64。
- 进程树聚合 RSS 最大 2 GiB；默认每 1 秒采样，连续两次超限后终止整个进程组。
- active root+sandbox home/tmp/cache 的净磁盘增长最大 2 GiB，按 `st_blocks*512` 的 allocated bytes 计算；manifest 同时保留 logical/allocated 值。监控使用文件系统事件+最长 1 秒的对账窗口，不把 logical sparse 大小当作实际磁盘占用。
- 任一限制触发保存 `termination_reason=resource_limit, limit_kind, observed, limit`，终止进程组，设置 `side_effects_may_exist=true`并继续后置 manifest/事实确认。
- 监控器启动/持续运行自检失败时不允许无保护继续：启动前返回 `shell_resource_limits_unavailable`，运行中故障则终止进程组。
- 该 2 GiB 磁盘限制是检测后终止，不宣称为 Seatbelt 内核配额。M0 必须证明事件/对账监控在支持的 macOS 版本上可靠；未通过时 Writable Shell capability unavailable。
- 上限在 MVP 固定，Approval/Settings 无放宽通道。更大任务由用户在系统 Terminal 执行。

### 7.4 stdout/stderr 流

- pipe reader 独立持续排空 stdout/stderr，不等待 SSE/Renderer/DB 消费。原始字节先执行字节凭证扫描，再增量 UTF-8 解码；非法序列以 `\xNN` 转义，文本规则再扫描解码结果。
- 安全文本按 100ms 或累计 4 KiB 合并，任一先到即 flush。每个 chunk 包含全局单调 `chunk_index`、流内 `stream_index`、`stream`、时间戳、redaction/truncation/tail_replay。全局序只表示 Runtime 观察交错，不声称 OS 同时写的绝对顺序。
- 保留上限为 stdout 768 KiB、stderr 512 KiB、合计 1 MiB。先为 stderr 分配最多 512 KiB，stdout 使用剩余且不超过 768 KiB。流超限时在最终预算内等量保留 head/tail，不截断 UTF-8 字符或脱敏占位符。
- UI 实时显示 head；达到 head 上限时只发一次中间省略标记，Runtime 继续排空管道并维护滚动 tail，结束时以 `tail_replay=true` 补发。持久化与最终 UI head/tail 完全一致。
- 模型 observation 最多 32 KiB：stderr 优先最多 16 KiB，剩余给 stdout，流内仍 head/tail。内部审计保存 exit_code、duration、raw original/持久化 retained 字节数、termination_reason 和 redaction 元数据；ToolResult 只返回 1.6 定义的脱敏后 observation/returned 字节口径，raw counts 不进入 Event 或模型。
- pipe reader -> 日志聚合器 -> Event publisher 使用有界队列。慢 SSE/Renderer 订阅断开后从最后 committed event id 回放，不增长无界内存，也不阻塞子进程。
- DB write/日志聚合故障终止进程组，返回 `output_capture_failed, side_effects_may_exist=true`并进入事实确认。cancel/timeout/resource limit 后在短暂宽限期内排空已产生管道字节，仍受同一上限。

### 7.5 Shell guardian

每个 Shell 由随应用签名发布的最小原生 guardian 直接作为 `sandbox-exec` 父进程启动。guardian 只实现 spawn、绝对 deadline、控制通道、信号、wait 和进程身份跟踪；策略、审批、Seatbelt profile、结果判定和对账仍由 Python sidecar 决定。它不是后台 daemon、LaunchAgent 或通用 XPC 服务。

- guardian 同时持有 Main 与 sidecar 的生命周期控制通道。两者 EOF/heartbeat 失效、取消或 deadline 到达时，固定先向受控 PGID 和已识别后代发送 TERM，2 秒后对仍存活目标发送 KILL，并持续 wait/reap。
- Main 或 sidecar 可在 guardian 单独故障时按同一进程身份记录接管有界清理；guardian 则必须能在 Main 与 sidecar 同时异常退出后继续完成清理。
- durable lease 保存 guardian/child PID、OS 进程启动身份、execution nonce 和已识别后代身份。任何清理都必须匹配进程启动身份，禁止仅凭 PID、PGID、nonce 或陈旧 lease 杀进程。
- 已开始 Shell 的 guardian/控制故障按真实进程终态进入 `interrupted` 与必要的 reconciliation；不得重跑命令。已知向量自检失败时 `run_shell` unavailable。
- M0 必须覆盖 background、nohup、double-fork、setsid、忽略 TERM、Main+sidecar `SIGKILL` 和 PID reuse 防护。契约只承诺有界清理受控进程组与已识别后代；Main、sidecar、guardian 同时被强杀等无法控制情形不作绝对零遗留承诺。

## 8. Seatbelt 文件策略

策略默认 `(deny default)`，允许子进程继承同一沙箱。路径矩阵：

| Root | Access |
|---|---|
| active root | read/write |
| active root 敏感 carve-out | deny |
| active root `/.git` | read-only |
| sandbox home/tmp/cache | read/write |
| System Runtime | read-only + executable mapping |
| approved toolchain roots | read-only + executable mapping |
| true HOME / `~/.eidos` / other user paths | deny |

调用固定使用 `/usr/bin/sandbox-exec`，不从 PATH 解析。Profile 使用静态模板和 `-D` 参数绑定路径；路径与 command 均通过 argv 传递，不拼接进 shell wrapper。

## 9. Shell 环境

Runtime 从空白环境构造 allowlist：

```text
HOME=<sandbox_home>
TMPDIR=<sandbox_tmp>
PATH=<system and approved toolchains>
LANG=<safe default>
LC_ALL=<safe default>
GIT_OPTIONAL_LOCKS=0
HTTP_PROXY/HTTPS_PROXY=<managed proxy when approved>
```

不继承宿主 env、rc 文件、SSH_AUTH_SOCK、云凭证、包管理器 token 或真实代理配置。Unix Domain Socket 始终禁止。

Toolchain Profile：

- 系统根 `/bin,/usr/bin,/usr/sbin,/sbin,/usr/libexec` 随应用只读信任并默认启用。`/opt/homebrew` 和 `/usr/local` 可在启动时发现，但只有用户在 Settings 启用后才进入 Seatbelt/PATH。
- Profile 保存 `id,name,canonical_root,dev,inode,enabled_at,profile_version,bin_dirs`；`bin_dirs` 只能是该根下实际存在的 `bin|sbin`。Seatbelt 对整个根只读/可执行，绝不授权写。
- PATH 固定顺序为已启用 `/opt/homebrew/bin,/opt/homebrew/sbin,/usr/local/bin,/usr/local/sbin`（存在者），然后 `/usr/bin:/bin:/usr/sbin:/sbin`。MVP 不提供手动调序。
- Profile root 不存在、变为 symlink 或 dev/inode 改变时自动禁用并返回 `toolchain_root_changed`。启用/禁用使环境模板版本递增，所有 pending/approved Shell Approval 失效。
- active root 和 `.` 不自动加入 PATH。Workspace `.venv`/`node_modules/.bin` 等程序必须用明确相对路径，并继续受 active root/hardlink 规则限制。
- 用户 Home 下 `.nvm,.asdf,.pyenv,.cargo` 等不进入 MVP；不执行 `brew shellenv|nvm use|pyenv init` 等宿主初始化。无可用工具时返回限制，不尝试读取真实 Home。

## 10. 网络策略

默认：外网、loopback、bind 和 Unix Socket 均拒绝。

外网批准：

1. ToolCall 声明精确 `allowed_hosts` 和非默认端口。
2. Host 统一小写、去除尾部点并规范化 IDN；拒绝通配符。
3. 审批卡展示规范化域名和端口列表。
4. Seatbelt 只允许连接 Eidos loopback proxy 端口。
5. Proxy 校验每个 DNS 结果，拒绝 loopback、私网、link-local、multicast 和 metadata 地址。
6. Proxy 只放行批准 host/port；重定向到新 host 时拒绝。
7. 不提供 unrestricted 模式。

localhost 批准：

- `local_network=true` 单独显示和审批。
- 只在本 ToolCall 生命周期允许 loopback bind/outbound。
- 不隐含外网或 Unix Socket 权限。
- 外部域名解析到本机地址不能借用 local_network 权限。

Proxy 审计只记录 tool_call_id、host、port、allow/deny、decision_rule、时间和收发字节数。禁止记录 URL path/query、Header、Cookie、Authorization、Body 或 TLS 明文；HTTPS 使用 CONNECT，不安装根证书或执行 MITM。

## 11. 敏感规则与脱敏

敏感路径 deny 至少覆盖 `.env`、私钥、凭证、token 文件；`.env.example` 仅作为名称例外，仍扫描内容。路径 deny 和内容 `deny` 都不可审批绕过。

规则集是随应用发布的只读版本化资源，带单调递增的 `ruleset_generation` 和可读 `ruleset_version`；每条规则包含 `rule_id, rule_version, action, matcher, test_vectors`：

- `deny`：已配置 API Key 精确匹配、私钥正文和结构完整且可校验的已知凭证。
- `redact`：形态像凭证但不能验证真实性的 token、密码赋值或特定凭证规则辅助的高熵串。
- `allow_with_audit`：变量名、注释、明显占位符、测试夹具和文档示例。

MVP 不使用模型或通用熵阈值判定敏感性。已配置 API Key 作为内存动态规则 `configured_api_key`，不持久化值、摘要或哈希。

管线顺序：

```text
raw input/output
  -> path hard deny
  -> configured API key exact match
  -> versioned credential rules
  -> deny/redact/allow decision
  -> truncation or summary
  -> model/UI delivery
  -> persistence redaction pass
  -> Event/DB/log
```

文件使用全文件流式扫描。Shell 和模型输出使用带保留窗口的增量扫描，安全窗口确认前不向下游释放；单条规则跨 chunk 最大匹配长度为 8 KiB，超限规则在加载时拒绝。

占位符固定为 `[REDACTED:<rule_id>]`，替换完整命中且不保留前后缀、长度、摘要、哈希或跨记录关联 ID。重叠规则按处理等级、更长匹配、`rule_id` 的顺序决定唯一结果；合法占位符排除于再次匹配，保证幂等。

结构化 payload 只允许替换字符串叶子值；字段名和其他结构 token 仍需检查但不可改写。命中字段名、枚举、ID 或其他无法安全替换的结构位置时，整个 payload 以 `sensitive_structured_payload_rejected` 拒绝。审计只保存 ruleset version、rule id/version/action、字段路径或文件行号和命中数。

任何扫描超限、超时、编码异常或扫描器失败都返回结构化错误并 fail closed，不释放未完成扫描的正文。

## 12. Fail Closed 与自检

MVP Lite 当前实施状态：

- ✅ 静态 Seatbelt profile 与 `-D` canonical path 参数已实现。
- ✅ 实机自检已覆盖 active root、sandbox home/tmp、外部 sentinel、敏感 carve-out、`.git`、symlink 逃逸、子进程继承、loopback 拒绝和基础进程组 timeout。
- ✅ 自检已接入 Runtime initialize；任何失败都保持 Shell unavailable，不存在无沙箱回退。
- ⏳ hardlink 前置扫描、managed proxy、Toolchain、manifest、资源监控、输出捕获、guardian 与 Redaction Service 自检仍未实现，因此产品能力 `runShell` 继续为 false。

应用启动执行 Seatbelt 自检：

- active root 测试目录可写。
- sandbox temp 可写。
- 外部用户文件不可读写。
- 敏感 carve-out 不可读。
- `.git` 不可写。
- 多链接普通文件使 writable Shell 自检/前置检查失败。
- 默认外网/loopback/Unix Socket 不可用。
- Toolchain root 只读可执行、不可写，未启用根不可读。
- 进程/fd/core/单文件限制、进程树 RSS/数量监控、磁盘增长事件监控、manifest 对账和输出捕获链路必须通过 M0 实机故障注入。

Redaction Service 同时校验规则 schema、唯一 rule id、重叠排序、8 KiB 长度上限、幂等性和全部 test vectors。自检失败时所有内容通路 unavailable，不仅限于 Shell。

`sandbox-exec` 缺失、策略生成/编译失败、必需 Toolchain 或任一强制资源/manifest/输出捕获自检失败时，Shell capability 为 unavailable，审批和 API 都不能绕过。
