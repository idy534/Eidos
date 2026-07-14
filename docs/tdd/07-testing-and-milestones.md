# 测试与里程碑

版本：v0.4

范围说明：本文保留完整目标态测试设计与风险里程碑。第一期执行顺序和退出标准以 [MVP Lite](../mvp-lite.md) 的 L0-L3 为准。

## 1. 测试原则

- 安全边界和状态机必须由自动化测试证明，不能只依赖人工演示。
- 所有有副作用路径同时测试成功、拒绝、冲突、取消、中断和幂等。
- Seatbelt 测试仅在 macOS 运行；沙箱不可用必须视为失败或明确 skip 原因，不能改跑无沙箱命令。
- 测试目录、config 和数据库必须使用隔离临时根，不能触碰用户真实 `~/.eidos`。

## 2. Runtime 单元测试

### 2.1 路径与敏感规则

- `../`、绝对路径、前缀碰撞和 symlink 逃逸。
- 普通文件、目录、symlink、FIFO、device 的类型检查。
- `.env`、密钥、凭证文件拒绝；`.env.example` 名称例外仍扫描内容。
- 固定向量的 `deny`/`redact`/`allow_with_audit`、ruleset version、rule id/version 和重叠规则排序完全确定。
- 通用高熵代码常量、哈希和压缩数据不被误判为硬拒绝。
- 占位符幂等；不保留原值长度、前后缀、摘要、哈希或稳定关联 ID。
- 结构化字符串叶子替换不改变 schema；字段名/枚举/ID 命中时整个 payload 拒绝。
- `.git` read-only carve-out。
- 脱敏 exact API key 与 pattern rule；数据库不含原文。
- 严格 UTF-8、UTF-8 BOM、非法 UTF-8、NUL/二进制和 UTF-16/GB18030 分类；无替换字符静默解码。
- LF/CRLF、文件末无换行和 BOM 在读取/Diff/修改后的保留。

### 2.2 只读文件工具

- read_file 在 256 KiB 和 2 MiB 边界的完整/head+tail/拒绝，以及 head/tail 行完整性与 `omitted_bytes`。
- read_file_range 的 1-based 闭区间、非法范围、EOF 收缩、start 越界空结果、2,000 行/256 KiB 截断、`next_start_line` 和超大单行。
- search_text 的 512 字节单行 literal、ASCII case-fold、非 ASCII 精确匹配、重叠匹配、路径/行/列稳定排序、byte offset 与 300 code point preview。
- search_text 的 100/500 结果上限、结果/扫描首触发停止原因、分类跳过计数和单文件失败继续。
- list_files 的 2/5 深度、500/2,000 条目、目录优先稳定排序、隐藏敏感项、`.git` 不展开、固定排除集和 symlink target 不泄露。
- 安全/性能排除分层、完整路径段匹配、精确 lock 集、`go.sum` 例外、`.gitignore` 不解析、显式 excluded path 拒绝和直接 read 可用。
- case-sensitive/insensitive APFS 上的真实目录项拼写、NFC/NFD 异名、case 别名、非 UTF-8 可表示文件名和 Approval 不可迁移。
- NUL、PNG/JPEG/GIF/ZIP/gzip/PDF/ELF/Mach-O/SQLite/WASM magic、C0/DEL 双阈值边界与严格 UTF-8 错误分类。
- read/range 单 fd 中途改写重试一次后零正文/零证据；search 丢弃变化文件所有匹配并保留其他文件 hash。
- list 请求目录替换整体失败、子树变化 `workspace_changed=true`，以及只读批次可观察不同版本而无快照假象。
- list success data 的闭合字段、2,000 entries、regular-only size/executable、省略而非 null、相对 path 4,096 bytes、稳定排序及敏感项零条目/零计数。
- list 恰好完成于 max_entries 时 `truncated=false`；确有可见条目省略时才是 max_entries。根替换 error，子树变化只标 workspace_changed；Context projection 不改工具级状态。
- read_file 空/完整/head-tail 的 0/1/2 segments、脱敏后 256 KiB 与各 128 KiB、returned/omitted 不可互推、LF 行号、evidence 与脱敏整行交集及 1,024 段省略计数。
- read_file 的全文件任意 redact 使 content_redacted=true；Context 裁剪不改变 complete，read_result_id 不授权未返回/脱敏行。
- range start>EOF 的省略字段、complete 恒 false、单 segment 与 actual_range 一致、2,000 行/256 KiB stop reason/next line 及同边界 max_lines 优先。
- search 闭合 match/counter schema、稳定 path/line/column、根替换 error、workspace_changed 等价 changed count；模型可见 scanned 计数只含稳定安全文件，内部 budget_consumed_bytes 独立累计重试、部分读取、deny/失败/变化字节且绝不进入 ToolResult/Event/模型。
- search 完整扫描恰好 max_results 不截断，提前停止与双边界优先级正确；>300-code-point match 使用确定 preview 并设置 match_preview_truncated。

### 2.3 ToolCall 批次

- N 个只读工具合法并保持顺序。
- 只读单项失败继续执行后续。
- read + apply、write + shell、两个 shell 整批零执行。
- publish_artifact 独占且不创建 Approval。
- 非法 model output 生成 model_protocol_error。
- Tool Schema Dialect v1 对 root/nested object、object array items 递归要求 `additionalProperties=false`；未知字段、缺 required、非法 null 和 free-form map 全部零 ToolCall row/Approval/执行。
- Dialect v1 的 type/enum/default/min-max/length/items 边界和明确禁用的 `$ref`、组合/条件关键字、`number` 分别通过启动静态验证；非法 schema/default 使 Runtime 不 ready。
- optional 静态 default 只在字段缺失时应用；显式 null 不等于缺失。effective arguments/hash/defaulted JSON Pointer 在重启、重试和同一 Run 中稳定，不读取时间、环境或 Workspace。
- effective arguments 完整 schema -> 组合 -> 敏感扫描 -> hash/Approval/执行顺序不可跳过；错误详情只含安全字段路径和允许字段名，不含未知值。
- 每个已创建 ToolCall 恰好产生一个 canonical ToolResult envelope；六种 outcome、闭合 data schema、summary 上限/扫描、截断和 `side_effects_may_exist` 映射完整。
- ToolResult schema_version=1 的 Python/JavaScript 固定向量覆盖未转义 UTF-8 key 排序、array 保序、补充平面 scalar、BMP/补充字符排序差异、NFC/NFD 原样、控制字符、quote/backslash、U+2028/U+2029、孤立 surrogate、递归重复 key、无 BOM/空白和 model request contract 版本变化。
- safe integer 最小/最大及边界外、非法 `-0`/小数/指数逐一验证；真实非负测量溢出只产生 `tool_result_numeric_limit_exceeded`，类型/负数/projector bug 仍 quarantine，Python/JS 不发生精度漂移。
- summary 对相同 contract/tool/outcome/code 字节完全相同、<=1,024 bytes 且无动态内容；success code null，其他 code 必填闭合，Provider/OS 原值不泄漏。
- data 对 `(contract,tool,outcome,code)` 递归闭合、始终 object；optional 省略、动态 key/null/scalar/无界 array/string 均拒绝。
- Per-tool projector 与 top-level serializer/shared mapping 注入故障分别产生 tool/global quarantine；真实副作用保存、Run failed、零 fallback/续接/Finalization、Profile snapshot 有效。

### 2.4 文件工具

- write expected_absent。
- apply base_sha256。
- delete 路径、类型和 hash。
- write/apply/delete 对 `st_nlink>1` 返回 hardlink_not_allowed。
- 修改已有文件保留 mode、ACL、xattr；复制失败时原文件不变。
- 新文件 mode 为 0644，脚本不会自动获得 executable bit。
- 审批期间文件变化导致 approval invalidated/file_version_conflict。
- 临时文件 + 原子 replace；取消不能截断 commit。
- 每个 ToolCall 拒绝多个文件、目录、glob 和递归删除。
- write content 512 KiB、patch 256 KiB/5,000 行/200 hunks、候选文件 32 MiB 边界和先生成后验证。
- Runtime 生成 write/apply/delete 完整 unified diff；512 KiB/5,000 展示行超限时零 Approval，无截断审批。
- 父目录不存在时零隐式创建；父链 dev/inode/mode/owner/ACL 变化时审批失效。
- write_file 覆盖只接受 <=256 KiB、完整、同 Run/path/hash 的 read_file 证据；head+tail/range/search/UI 证据均拒绝。
- 含脱敏命中的完整读取不能授予 write_file 覆盖资格；脱敏行从 apply_patch 读取证据区间中扣除。
- apply_patch 的多读取区间并集覆盖、纯插入边界锚点、空文件和跨 Run/path/hash/invalidated 证据拒绝。
- patch 声明行精确匹配，offset/fuzz 为零；任一 hunk 失败时候选文件零提交。
- delete_file 无读取证据也可申请，但二进制/编码/敏感/大小/Diff 上限和父链任一失败都零 Approval。
- delete durable intent 后崩溃：原目录项消失可对账 applied，同路径新对象 outcome_unknown 且永不重删。
- write/apply/delete 成功后旧 hash 读取证据失效，同路径新文件不继承。
- Runtime 临时文件恢复清理必须同时匹配保留名称、intent nonce、inode 和父目录身份。
- unified diff 严格路径、单文件、count 省略=1、多文件/Git 扩展拒绝、LF/CRLF 插入编码、mixed 拒绝、BOM 保留与 no-final-newline 标记。
- write_file 新文件 LF/NUL/surrogate 校验，覆盖 newline_style 显式匹配、CRLF 编码、mixed 拒绝和无静默规范化。
- 相同字节 write/apply -> skipped/no_changes：零 Approval/intent/文件接触，Reject 不清零、证据不失效；比较后并发变化返回 conflict。
- write created/overwritten、apply、delete success 的闭合字段、optional old hash、hunk count 与 postcondition 只来自提交后重开/目录项复检；候选预测值不得直接成为 success。
- skipped/no_changes data 恰为 path/base hash；仅适用于已存在目标，新建空文件仍为 success/created，结果事务前路径变化为 conflict。
- 非当前 uid、immutable/append-only flags 拒绝；gid/mode/ACL/xattr/可保留 flags 完整复制验证失败零 replace。
- 逻辑/已分配字节对稀疏文件的分开计量，不以 sparse allocated size 放宽文件工具上限。
- publish_artifact 仅 <=32 MiB 严格 UTF-8、display_name/type 校验、二进制/PDF/Office/压缩/加密拒绝、snapshot 0600/hash 读时复检与 corrupted 状态。
- Artifact success 的 UUID、source path、display/type/version/snapshot hash/size 闭合 schema；稳定源与重开快照 hash/size 相等，不返回 URL、snapshot path 或重复 source hash。
- read 大文件 error 返回稳定 actual/max size；超长行在安全扫描/编码/稳定性通过后按 read/range 唯一行选择规则返回 line/max，零 actual line bytes 与正文/证据。
- invalid diff safe_reason/hunk、缺证据首 hunk、Diff actual/max/limit kind 和 safe-write actual/max 的 code 专属 schema；conflict、Artifact 与其他 error data 为 `{}` 且无 generic message/details/cause。

### 2.5 状态机

- Run/Segment/Step/ToolCall/Approval 合法和非法流转。
- approve/reject/cancel 并发只有一个成功。
- Reject 计数增加和两种清零条件。
- Reject feedback 的 1..4,096 UTF-8 bytes、扫描失败保持 pending、成功后仅进入 approval_rejected data；未提供时 `{}`，summary 不含反馈。
- Segment 20 Steps/30 分钟暂停。
- Run 80 Steps/120 分钟 Finalization -> stopped。
- Finalization 超时/失败生成降级摘要。
- reconciliation_required 屏障拒绝副作用；每次新不确定性递增 epoch，Step 冻结 observed epoch，只有正常 completed 且至少一个只读 success 才 CAS 清除。部分成功、合法空结果、truncated/workspace_changed 可计，error/无工具/中断不计，旧 Step 不能清新 episode，未清除 final 保留审计。
- allowed_actions 覆盖所有 status、闭合 pause reason、Workspace available/unavailable、Approval pending/decided/invalidated；写 API 事务重算，cancel canceled 幂等，其他终态和未知 pause fail closed。
- side_effects 映射覆盖只读/未启动/no-op/verified success=false、outcome_unknown/已启动 Shell=true；reconciliation=>true 但正常成功 Shell 证明反向不成立。
- Shell 多故障按 interrupted、manifest、capture、limit、timeout、signal、nonzero、success 优先级唯一映射；`.git`/protected 只加屏障不改写 exit 0 success。
- TimeProvider 固定向量覆盖 ApiTimestampV1/DurationMs safe integer、monotonic 区间单次 ceil、wall 不参与 elapsed、Shell continuous/wall 任一先到、同 boot 跨 sidecar/sleep 延续、boot/timebase mismatch 失效、部分/完整回拨与前跳。

### 2.6 重试与上下文

- WebSocket supported 时 initial + 5 次首 delta 前重放后切换 HTTP(S)，HTTP(S) initial + 2 次重试后 waiting_user_input；多个 Attempt 只计一个 Step。
- 426/Upgrade rejected/协议不支持立即降级且不消耗 WebSocket 重试；瞬时降级只对当前 Run 粘滞，明确不支持按 snapshot 跨 Run 抑制。
- WebSocket unsupported/unknown 直接使用 HTTP(S)，Profile 仍可选；HTTP base_url 降级后仍是 HTTP，HTTPS base_url 仍校验证书。
- 首 delta 后透明重试和传输切换均被禁止。
- 首 delta 后中断会保留 incomplete progress、丢弃部分 ToolCall，并进入 waiting_user_input。
- 模型流中断的失败 Step 计入 Segment 和 Run Step 预算。
- 同一 Step request cycle 固定 10 分钟，包含 DNS/TLS、全部 Attempt、`1/2/4/8/16` 与 `1/2` 秒退避和 Retry-After；局部 15/180/120 秒 timer 均被 cycle deadline 截断。
- Retry-After 上限 60 秒；超过剩余周期不会越过 deadline。Finalization 独立 60 秒且输出上限 `min(profile.max_output_tokens,4096)`。
- 只读瞬时错误最多 1 次；确定错误不重试。
- 写/Shell/Artifact 零自动重试。
- canonical serializer 稳定且版本化；对 ASCII、多字节 UTF-8、JSON escape、message/tool call/tool result 数量验证 `bytes + 64 + 8m + 16c + 16r`。
- safety margin 验证 `CLAMP(CEIL(context*0.02),1024,8192)` 的下界、中间值、上界和取整；发送前严格满足 `estimated_input <= context-request_output-margin`。
- 普通/协议纠正使用 Profile output 上限，probe 使用 `min(profile.max_output_tokens,512)`；同一逻辑请求所有重放值一致。
- Context Builder 先放不可裁剪内容，再按固定顺序添加/裁剪可选内容并复算完整 canonical payload。
- Immutable base envelope/hash 在所有 Step 不变；每 Step projection 只裁声明字段、保留 core status、记录省略量并在首次 Attempt 前冻结。后续 Step 不得换等量前缀重新显露。
- Projection 前按当前 ruleset 重扫只能减少或加强脱敏；同 Step WebSocket/HTTP(S) Attempt 的 projection hash、base hash 和 canonical payload hash 完全相同。
- list/search projection 只能保留 base array 的稳定前缀；read/range 先整体删除所有 segment content，再裁 evidence_ranges 尾部；对应 `context_omitted_*` 只在正数时出现，且精确控制 model_content_truncated。
- Shell projection 对任意目标 B 都先保留 stderr、再保留 stdout，流内按 `CEIL(B/2)` head 和 `FLOOR(B/2)` tail 取合法 UTF-8/脱敏占位符边界；边界舍入不重新分配，`context_omitted_output_bytes` 必须等于 base 与 projection observation 字节差。
- 不可字段级裁剪的 ToolResult 只能按历史项整体纳入或不纳入；协议关联要求纳入而预算仍不足时进入 context_input_too_large，不得临时新增省略字段或破坏闭合 schema。
- 不可裁剪输入超限时零 Provider 请求、零 started Attempt、Run failed/context_input_too_large、snapshot 不失效且预算明细不含原始 payload。
- model request contract 升级使旧 Profile snapshot 失效；既有 Run 继续旧 serializer/budget/timeout。unsupported 旧版本零 Step/Attempt 并进入 runtime_contract_unsupported。
- tool contract 升级不失效 Profile snapshot；旧 Run 始终使用固化版本。旧实现缺失或低于当前安全底线时零模型/工具执行、pending/approved Approval invalidated，并进入 runtime_contract_unsupported。
- available tool set 只依赖固化 contract、请求类型、mode、持久 gate 和单工具 health；任务文本、路径存在性、预算和常用度变化不改变集合。
- Workspace/Public Mode 的九个工具都按各自 active root 可用；Public 底层文件树不可见不改变 Tool Registry，reconciliation 和单工具 health 仍能进一步隐藏工具。
- 首次 Attempt 冻结稳定排序工具名及完整定义 hash；WebSocket/HTTP(S) 重放保持不变，下一普通/纠正 Step 重新计算。空集合发送 none 并省略 tools/parallel，非空发送 auto/parallel true。
- 未在冻结集合中的调用进入协议纠正；已暴露工具在请求后变 unavailable 时产生 `tool_unavailable` canonical observation、零副作用且不增加协议错误计数。
- 可见文本动态 64 KiB..4 MiB、reasoning 2 MiB、单 Event 1 MiB 和总流 8 MiB 的等于/超一边界；超限零重试且已提交文本 incomplete。
- Provider raw reasoning 内容不会进入 Message、Event、日志或后续上下文。
- assistant_progress/final_answer 分类正确，reasoning usage 只保存数值元数据。
- 模型认证、模型不存在和确定性 invalid request 不重试、不 Finalize，Run 直接 failed。
- 空/畸形/未知工具/非法批次响应连续两次后暂停，合法响应清零协议错误计数。
- 模型 content 跨 chunk 凭证先脱敏后展示/落盘；扫描失败不 flush 保留窗口。
- 敏感 ToolCall 零 ToolCall row/零 Approval；两次后 `repeated_sensitive_tool_input`，不影响协议错误计数。
- 任一合法无敏感 ToolCall 响应清零敏感 ToolCall 计数。

### 2.7 Model Profile 能力探测

- 创建 Profile 只落配置且不发网络请求，初始 `selectable=false`。
- Test Connection 请求只含 Profile 配置、固定 probe 文本和固定无副作用 tool schema；断言不含任务、Session、Workspace、Artifact、Timeline 或 ToolResult 数据。
- 认证、模型存在、streaming、工具控制、`strict=false` Schema 模式、ToolCall/ToolResult 关联、stateless continuation 和 usage 分别成功；任一失败都产生新的 `failed` snapshot 和安全错误码，Profile 仍不可选择。
- 全部通过时产生 `passed` snapshot；snapshot version 单调递增并绑定 configuration/Gateway/model request contract version，不覆盖历史结果。
- Session 创建/切换和 Run 创建均拒绝未验证或最新探测失败的 Profile；拒绝创建 Run 时不占用 idempotency key。
- Run 内嵌创建时的 Profile/capability snapshot；后续重新探测成功或失败都不改变既有 Run。
- 正常运行违反已通过的 streaming、工具控制、Tool Schema Dialect、ToolCall/ToolResult 关联或 usage 契约时返回 `model_capability_drift`，Run 直接 failed，零重试、零 Finalization、零 Profile 切换。
- Snapshot 无时间 TTL且无后台探测；配置/Gateway/model request contract 变化、capability drift 和 context mismatch 分别触发失效，新 Session/Run 均被阻断。
- 只改 name 保持 snapshot 有效；修改 endpoint/model/auth/key/parameters/context limits 递增配置版本并失效，条件更新冲突零部分修改。
- Archive 后不可选、不可测试且历史引用完整；restore 复用仍有效 snapshot，否则保持不可选；不存在 DELETE API 或级联删除。
- 两个 Profile 即使保存相同 Key 也使用不同 credential slot/revision；替换一个只失效一个，API/日志/DB 不回显原文或凭证摘要。
- bearer、api_key_header 和 none 分别生成唯一固定认证 Header 或零认证；任意自定义 Header/传输字段注入被拒绝。
- 公网、loopback、局域网和私网 HTTP(S) 均可连接且不做 DNS 地址分类；非 HTTP(S)、userinfo、敏感 query 和跨 Origin Redirect 拒绝，同 Origin Redirect 有界通过。
- HTTPS 的有效系统信任链通过；过期、主机名错误、自签名未信任证书失败且没有 verify=false 分支；HTTP 仍可连接。
- 扩展参数稳定透传；保留字段、NaN/Infinity、敏感值、32 KiB/深度/成员上限分别拒绝，并参与 configuration hash。
- context/output 在 4,096..4,194,304 和 1..262,144 固定边界内且 output<context；Provider 元数据不覆盖用户值，明确 context-length exceeded 映射为 `model_context_limit_mismatch` 并失效。
- 必需 HTTP(S) streaming 与可选 WebSocket 分开探测；supported/unsupported/unknown 都正确持久化，后两者不使 passed snapshot 失败。
- Test Connection 的短 probe 使用最多 512 输出 token，独立参数校验验证 Profile 声明上限。
- Responses/Chat 显式 wire_api 分别通过；修改 wire 递增 config version 并失效，不存在按 URL/模型/错误自动推断或跨协议 fallback。
- Responses 固化 max_output_tokens；Chat 明确 unknown max_completion_tokens 后才探测 max_tokens。认证/网络/429/5xx/含糊 invalid request 不触发字段切换。
- 两阶段关联 probe 第一阶段固定单工具 `required/parallel=false/strict=false` 且恰好一 call，第二阶段移除 tools、`none` 并回传固定结果；probe ToolCall 永不执行或落业务表。
- 非空工具集的普通/纠正请求显式 `auto/parallel=true/strict=false`；空集与 Finalization 无 tools、`none` 且省略 parallel；Profile parameters 无法覆盖保留字段。
- Test Connection 对 parallel true、required/none/parallel false、strict false 的确定性拒绝分别映射 `model_parallel_tool_calls_unsupported|model_tool_control_unsupported|model_tool_schema_mode_unsupported`。
- 固定 Dialect probe 覆盖全部允许结构/关键字，并验证 Provider 接受及参数 roundtrip；不把 Provider 是否执行 schema 校验当成授权条件。

### 2.8 Wire Adapter 与流归一化

- API root 的 trailing slash、path prefix 和安全 query 结构化追加 `/responses|/chat/completions`；已含 endpoint、字符串拼接歧义和跨 Origin Redirect 分别拒绝。
- Responses `store=false` 且无 previous/conversation；只有 response.completed 完成，incomplete max token/content filter、failed 和无终态 EOF 正确映射。
- Chat 固定 n=1/include_usage；choice 0 的 stop/tool_calls/length/content_filter/null、deprecated/unknown finish reason、缺 `[DONE]` 和 EOF 分别验证。
- usage 必需字段非负且 total>=input+output；optional 细分非负。探测缺失/非法失败，正常完成缺失触发 drift，中断缺失为 unknown。
- Chat tool call index 从 0 连续，Responses item/call ID 稳定；名称/ID/arguments 跨任意 chunk 边界归并，缺失/重复/重绑/冲突/非 object JSON 零 ToolCall。
- internal UUID 与 <=256-byte provider_call_id 分离；Responses function_call_output 和 Chat role=tool 使用原 provider ID 完成 stateless continuation。
- ToolCall 16 calls、单/总 1/2 MiB、16,384 deltas、128-byte name、JSON 16 depth/2,048 members 的等于/超一及大量零/小分片属性测试。
- truncated/blocked/stream-limit 时即使存在已完整解析的单个 call，也丢弃整个批次；已提交 content 只作为 incomplete progress。
- 每个 Step 从本地记录重建语义等价上下文；Provider response ID 改变、WebSocket->HTTP(S)、重启和 API Key 轮换都不丢失 ToolResult 关联。
- Responses 与 Chat 都只编码同一 canonical ToolResult JSON；内部 UUID/自由文本不进入模型内容，Provider call ID 只做关联。只读批次按 batch_order 一调用一 envelope。
- Responses 与 Chat 对同一 Step projection 的 JSON bytes/hash 完全相同；Adapter 只添加 wire 关联。Base/projection hash 不匹配时零 Provider 请求并触发 contract violation。
- rejected、skipped/no_changes、unavailable、interrupted 在模型继续时进入后续上下文；终止 Run 不发 Provider 但仍保存真实内部终态。非法/敏感/非法组合因零 ToolCall row，也零 synthetic ToolResult。

## 3. Seatbelt 集成测试

每次测试使用独立 active root、sandbox home/tmp 和外部 sentinel：

- active root 创建、修改、删除成功。
- sandbox home/tmp 可写。
- 外部 sentinel 不可读写。
- System Runtime 可读/可执行但不可写。
- 子进程继承限制。
- 敏感 carve-out 不可读。
- `.git` 可读不可写。
- 默认外网、localhost、bind 和 Unix Socket 失败。
- 只允许连接 managed proxy port。
- 精确批准 host/port 通过，通配符、未批准 host、私网解析和 redirect 新 host 失败。
- `local_network=true` 仅本次调用允许 loopback。
- 外部域名不能借 local_network 映射到本机地址。
- Writable Shell 遇到多链接普通文件 fail closed；APFS clone 不误判。
- sandbox-exec 缺失/策略失败时 Shell unavailable，无回退。
- 系统 Toolchain 默认可执行；Homebrew/本地根未启用不可读，启用后只读可执行，root 替换使 Profile/Approval 失效。
- active root/`.` 不在 PATH，Workspace 明确相对可执行；用户 Home `.nvm/.asdf/.pyenv/.cargo` 和 rc 初始化无法访问。
- Shell resource/manifest/output capture 任一自检故障使 Shell unavailable，不降级。

### 3.1 Shell 资源与输出

- fork 超过 64、fd 超过 256、core dump、单文件 >1 GiB、聚合 RSS >2 GiB 连续两次和 allocated growth >2 GiB，均终止整个进程组。
- 监控器启动/运行中故障、采样窗口、并发 IDE 磁盘增长归因文案和 sidecar 在终止后保持可用。
- stdout/stderr 高频交错、无换行大块、非法 UTF-8、跨 chunk 凭证、100ms/4 KiB flush、全局/流内序号和慢消费者断开回放。
- stdout 768 KiB/stderr 512 KiB/合计 1 MiB 的 stderr 优先分配、等量 head/tail、中间省略、tail_replay 与 32 KiB 模型 observation。
- DB/日志聚合故障和 cancel/timeout/resource limit 后排空宽限，零原始敏感或已丢弃中间内容重现。
- 已启动 Shell ToolResult 的 termination/exit/limit optional 规则、脱敏 observation/returned/truncated 字节恒等式、四个 head/tail 上限和 output_redacted；raw pipe bytes 零 ToolResult/Event 泄漏。
- workspace_changes 的固定 attribution、五类安全计数、50 条稳定 path/change、omitted count、manifest 下界、`.git` 布尔与 protected 聚合；未启动错误不得伪造 terminal data。
- guardian 覆盖 background、nohup、double-fork、setsid、忽略 TERM、绝对 deadline、TERM 后 2 秒 KILL、Main+sidecar `SIGKILL`、guardian 单独故障接管、PID reuse/start identity 和 lease 恢复；失败时 Shell unavailable，测试不声称证明任意进程树。

## 4. Runtime 集成测试

- Public 无工具回复。
- Model Profile 创建 -> Test Connection -> Session 选择 -> Run 固化 snapshot；未测试/失败 Profile 在 Session 和 Run 两个入口均被拒绝。
- Profile 编辑 -> snapshot 失效 -> 重新测试，以及 Archive/restore、Gateway contract 升级失效和 capability/context drift 终态事务。
- 任意地址类别的 HTTP(S) Provider、同/跨 Origin Redirect、三种认证模式、TLS 系统信任链和扩展参数/保留字段端到端请求断言。
- WebSocket 五次重放 -> HTTP(S) 两次重试、明确不支持立即降级、Run 级粘滞和 snapshot 级 ws_disabled 的端到端状态/Event/时钟断言。
- Run 创建后轮换 API Key：下一 Attempt 使用新密钥和新 credential revision，但 endpoint/model/parameters/capability 仍取 Run 快照；凭证缺失时不回退旧密钥。
- Responses 与 Chat 两条完整闭环：任务 -> 分片 ToolCall -> 本地执行 -> stateless ToolResult -> final；断言内部事件和业务状态一致。
- Workspace 只读批次 -> 写审批 -> 版本复检 -> 成功。
- Public 写文件 -> publish_artifact -> 不可变快照。
- delete_file approve/reject/conflict。
- run_shell approve、网络 host 审批、localhost 审批。
- Shell nonzero/timeout/interrupted -> fact reconciliation -> 后续变更。
- 多 Run FIFO，无并行模型/工具调用。
- waiting Run 释放执行槽，恢复后入队尾。
- queued Run 取消。
- 崩溃恢复不重放副作用。
- Event 与状态同事务；模拟 Event insert 失败回滚状态。
- 所有持久化 POST/PATCH 的 operation ID：同 envelope 并发/断线/重启只提交一次并重放原 response；跨 method/route/resource/body 复用冲突；重放重新鉴权；敏感/鉴权/validation 失败不占 key；nonce/version/gesture guard 仍生效。
- Test Connection 在 intent 前、网络发送中、结果提交前崩溃的同 ID 分别返回可确定结果或 operation_interrupted，绝不自动重发；新用户点击使用新 ID。
- closed OpenAPI DTO 的 Python/TypeScript/runtime validator contract tests；unknown/missing/null/enum/body cap 和 ORM 新列零泄漏。分页 cursor 覆盖 endpoint/scope/filter/order/version 篡改、每页重新鉴权、limit、creation_seq high-water、last key、并发新增/更新/删除和零 offset。
- Event envelope/type payload 固定向量、全局 id 跳号/重复无条件忽略、同一持久 Event 在 ruleset 变化后 wire payload 可不同、服务端 immutable hash 校验、content_unavailable、兼容未知 type 安全占位、已知未知 version resnapshot、snapshot 未覆盖有界失败和不兼容 event contract ready 阻断。
- health-only 启动顺序注入状态目录、lock、DB revision/迁移/完整性、契约 serializer、Redaction 和恢复故障；ready 前零业务 body parse/Event/idempotency/调度，ready 只发布一次且 scheduler 后释放。
- 同一读事务 RunSnapshot/through_event_id，在 snapshot 前、事务中、安装后并发写 Event 均无缺口；terminal 不开 SSE，未知 Event resnapshot，未知 snapshot schema 停止兼容。
- 副作用 intent 已提交但结果未提交时，重启只对账不重放。
- file/artifact 根据 hash 补记结果；Shell 标记 interrupted/side_effects_may_exist。
- SSE after_event_id 回放和去重。
- Create Run/user-input 敏感命中时零 Message/Run/Segment 变更，Create Run 不占用 idempotency key。
- approve/reject feedback 敏感命中时 Approval 保持 pending，决策与 feedback 都零落库。
- read/range 在请求区间外命中 `deny` 时也零正文；search 跳过整个敏感文件但继续其他文件。
- 单文件 32 MiB、搜索 256 MiB/15 秒和规则 8 KiB 边界值，以及超限/编码异常/扫描失败的 fail-closed 路径。
- 写/Patch/Shell 参数和 Artifact 源文件敏感命中时零审批、零执行、零静默改写。
- 升级后恢复 waiting/queued Run 先记录 ruleset 变更，再使用新规则入队。
- 新规则对旧 Message/Event/Tool log/Artifact 的读时 deny/redact 生效，但不改写原始历史记录。
- Event 读时脱敏/整体安全替换仍保留 id/type，SSE reducer 可连续推进且无原 payload 泄露。
- 以更低 ruleset generation 启动时内容通路 fail closed；携带相同或更高 generation 的回滚构建可正常启动。
- 大仓库 list/search 的稳定顺序、有界内存/时间、分类跳过和无失效游标依赖。
- read evidence 在多 Step/Segment 可用、Context 正文裁剪不改变证据，但绝不跨 Run 或文件新版本。
- Shell approved_at+300s 时效、pending 不过期、执行开始后不受 5 分钟中断、环境/Toolchain/root 变更 invalidated 与无进程启动。
- manifest 200,000 项/30 秒前置 fail closed，前置 intent 引用，后置 created/deleted/content/metadata/type、敏感聚合、`.git` 异常和“执行窗口观察”归因。
- 后置 manifest 超限/崩溃进入事实确认，不重跑命令/不回滚，完整提交后才清理 manifest file。
- Workspace 同路径替换、同身份移动、卸载重挂、volume UUID/birthtime 不可用、显式恢复旧身份和 available path/identity 唯一约束；旧 Approval 永久 invalidated，Run 不自动继续。
- state DB 注入 SQLITE_FULL/ENOSPC/EDQUOT/IOERR/EIO/fsync/readonly/corrupt；只有空间类释放 reserve 一次，所有类别停 scheduler/API 写并保留 durable intent。释放后诊断仍失败只返回内存 health。
- Workspace/Artifact temp write、flush、rename、目录 fsync、postcondition 各阶段注入满盘/I/O 故障；只有 rename 前确定失败可声称原目标不变，之后 outcome_unknown/reconciliation，ToolResult 提交失败升级全局 storage health-only。

## 5. Desktop 集成测试

- Electron 第二实例只激活已有窗口；Main token、listening/health-only、唯一 ready、sidecar port 和认证代理。
- 两个 sidecar 竞争同一 `runtime.lock` 只有一个进入 DB；陈旧锁文件不阻塞，真实 OS lock 不可通过删文件/PID/nonce 抢占。lock fd 的 `O_CLOEXEC/FD_CLOEXEC` 和所有 child fd table 断言 guardian/Shell 不继承；Main crash 后新实例有界等待旧 sidecar退出，再完整恢复。
- Renderer 获取不到 token/port。
- 未列入白名单 IPC 无法调用。
- Markdown/XSS、导航和本地 URL 被阻止。
- SSE -> IPC -> Feed reducer。
- RunSnapshot 原子安装 -> SSE 水位续接 -> Feed reducer；Approval summary/detail nonce 竞态禁用旧 Approve。
- 文件夹选择、持久 volume/inode/birthtime 身份、Workspace unavailable/显式恢复和旧审批不复活。
- 打开系统 Terminal 仅允许用户手势和 workspace_id。
- 不加载 node-pty，不存在内嵌 Terminal。
- 关闭窗口等待/取消流程。
- sidecar 异常退出后 runtime_disconnected，重启后 interrupted 恢复。
- 审批卡展示完整 Runtime Diff、编码/BOM、读取证据范围和父目录状态；超大 Diff 不产生卡片。
- read/range 大小、范围、二进制和编码错误展示不同的恢复引导。
- Shell 卡快照警告、PATH/Toolchain/资源/授权倒计时，no_changes 非审批卡，stdout/stderr 省略/tail 不重算。
- manifest 卡前 200 路径/详情列表、敏感名零泄露、完整性状态，Artifact 仅文本格式与 corrupted 禁用预览。
- Toolchain Settings 只列固定候选，用户手势 enable/disable、root 替换自动禁用和无任意路径输入。
- Model Profile 的未测试/测试中/通过/失败状态、逐能力安全结果、未验证禁用选择，以及 capability drift 后重新测试并创建新 Run 的引导。
- Profile 编辑的失效提示、API Key 保持/替换/清除、Archive/恢复无删除、HTTP 明文警告、TLS 无绕过、固定认证模式和用户声明 token limits。
- WebSocket 可选状态、重连计数、一次性 HTTP(S) 降级提示、reported/unknown usage 汇总和 context_input_too_large 预算错误卡。
- wire API 选择、最终 endpoint preview、output token parameter 探测结果、stateless/第三方留存提示、三类 output stopped 与 runtime contract unsupported 卡片。
- 每个状态只按 snapshot allowed_actions 渲染；recoverable/irrecoverable/unknown/workspace unavailable 的 Continue、Approval 竞态和 terminal 空动作均符合服务端矩阵。
- Main 对 success/error DTO 二次 runtime validation、cursor 只透传、unknown Event 原 payload 零 Renderer 泄漏、不兼容 contract 停流；storage health-only 只显示安全 reason/data root/recheck 且无自动清理/恢复按钮。
- UTC 毫秒的本地化展示、客户端时钟回拨/休眠不延长 Approval、启动 clock rollback invalidation 在业务 IPC 前可见。

## 6. 持久化测试

- `foreign_keys=ON`、WAL、busy_timeout 生效。
- enum CHECK、mode/workspace CHECK、全局 unique operation ID/request hash/closed response，以及 Run `creation_operation_id` 引用。
- Alembic 空库初始化 head 和重复启动；已知旧 revision 先通过 SQLite Online Backup API 生成 mode 0600、fsync、hash/manifest、原子安装的完整备份，再以单 connection/transaction 迁移并复检 foreign keys/integrity/target revision。
- backup 临时写/关闭/校验/fsync/rename/目录 fsync/manifest 任一点故障均不改源库；migration SQL、commit 后 reopen 或 integrity 故障保持 health-only并保留源/备份。未知/缺失/newer revision、autocommit/VACUUM/journal 变化和自动 downgrade/stamp 全部拒绝。
- 状态与 Event 原子提交。
- FIFO enqueued_at 重启保持。
- Artifact snapshot/source hash 和版本唯一性。
- capability snapshot 的 `(profile_id,snapshot_version)` 唯一性、必需能力（含 tool control/schema）、Tool Schema Dialect、stateless continuation 与可选传输/输出字段、configuration/Gateway/model request contract version，以及 Run 内嵌快照不随外键记录变化。
- capability snapshot 的 WebSocket 可选结果和无 TTL `model_transport_health` 复合主键；新 snapshot 不继承旧 ws_disabled。
- ModelAttempt 的逻辑请求关联、transport、attempt index、实际 credential revision、request output、retry reason、usage reported/unknown 与 Step cycle deadline 可重启审计。
- Profile/snapshot/Run 的 wire API、model request contract、stateless continuation、output token parameter 持久一致；contract upgrade 只失效新 Run 选择，不改写旧快照。
- ToolCall 的内部 id/provider_call_id 双标识、`UNIQUE(step_id,provider_call_id)` 和回传映射；Provider ID 不替代内部外键。
- Run `tool_contract_version`、Step `available_tool_names_json/tool_set_hash` 与 ToolCall contract version 一致；同 Step 重放不能写入不同集合。
- ToolCall 只保存 effective arguments/hash 与无值 `defaulted_field_paths_json`；immutable base envelope/hash 一一对应，数据库 status 到六种 outcome 的投影完整。
- `step_tool_result_projections` 的 target/source 引用、base/projection hash、ruleset/tool/model contract version、全局连续 projection_order 与 Step/Attempt canonical payload hash 可重启复算且唯一；多个 source Step 具有相同 batch_order 时不得碰撞。
- Tool/global quarantine 的 active 唯一约束、detected/cleared build 审计、普通重启保留和新 build 定向 self-test 清除事务。
- workspaces 的 available path/持久 identity 部分唯一约束、状态转换与 Approval 永久 invalidation；runs reconciliation epoch、episodes、Step observed epoch、qualifying calls 和 CAS clearing 可重启审计。
- pageable tables 的 AUTOINCREMENT creation_seq 不复用、cursor high-water 跨重启稳定；Event contract version、per-type payload version 和 envelope hash 可审计。
- highest_observed_wall_time_ms 单调更新；timed approved Shell 的 boot-session/continuous deadline 同 boot 跨进程可验证，boot/timebase mismatch 或 high-water 回拨在业务开放前原子 invalidated，pending 保留且 TTL 不重置。
- emergency.reserve 与 DB 同文件系统、mode 0600、非稀疏 allocated >=16 MiB、fsync；空间不足恢复预检同时覆盖 reserve、WAL/journal 和余量，WAL/integrity/FK/intent 任一失败不 ready。
- file_read_results 的 total_lines/complete/redaction/returned/omitted/evidence counts 与完整 file_read_ranges 持久一致；模型可见 evidence 上限不截断 Runtime 授权事实。
- 多 Attempt usage 只累计 reported 值，unknown 计数保留；重复 Provider request id 不触发 ToolCall 重复创建。
- Profile/snapshot 的 profile/config/credential version、valid/invalidation 字段、Archive 保留引用、`ON DELETE RESTRICT` 和 selectable 查询条件。
- config.toml profile-id 独占 credential slot、0600 原子替换、config/DB revision 崩溃窗口启动对账，以及一个 Profile 替换密钥不改变其他 Profile。
- config 权限 0700/0600；权限过宽拒绝密钥。
- SQLite、日志和 Event 中不存在测试 API Key 原文。
- Proxy audit 只含 host/port/decision/bytes，不含 URL、Header、Body 或 TLS 明文。
- 对 Message、Model response、Tool args/result/log、Event、错误和结构化日志执行原始凭证全库断言。
- redaction audit 只含 ruleset/rule/action、命中数和安全位置，不含原值可派生数据。
- Redaction Service 自检失败时内容 API fail closed，不影响 health/安全诊断。
- file_read_results/ranges 的 FK/CHECK/级联删除、空文件 complete 语义、head/tail 双区间和成功写后事务内 invalidation。
- Approval 中 arguments/preconditions/candidate/diff hash 一致，篡改、截断或重生成 Diff 都不可 approve。
- toolchain_profiles 固定 root 约束、profile/environment version 递增与旧 Approval 失效事务。
- shell manifest file/DB ref/hash、敏感条目不落库、changes 唯一序号、完整结果后清理和崩溃保留。
- shell log 全局/流内唯一约束、省略/tail_replay 持久化和断线回放不恢复中间内容。
- Artifact session/source version 唯一、logical/allocated/encoding/BOM/type 字段、snapshot hash 损坏标记与零正文读取。

## 7. 完整目标态风险里程碑

### M0：macOS 安全可行性

- Seatbelt 静态策略模板和参数绑定。
- System Runtime read-only、active root write、外部 deny。
- 敏感和 `.git` carve-out。
- managed proxy、域名策略、localhost 独立权限。
- fail-closed 自检与集成测试。
- 版本化敏感规则、全入口扫描管线、跨 chunk 脱敏和 fail-closed 自检。
- Toolchain 只读根、Shell Approval boot-session/continuous 时效、前/后 manifest、进程树资源限制、allocated growth 监控、无阻塞输出捕获和最小原生 guardian 异常清理实机可行性。

M0 未通过前，不进入 Agent Shell 主链路实现。

### M1：Desktop 与 sidecar

- Electron/React/Python 骨架。
- 单实例、Token、listening/ready gate、随机端口、类型化 IPC/API/SSE 代理和 RunSnapshot 水位恢复。
- OpenAPI 同源 DTO/validator、operation ID 转发、Event contract 握手和 storage health-only UI。
- `~/.eidos` 权限与 config。

### M2：SQLite、队列与状态机

- 状态目录独占 OS lock、SQLite 一致备份与 forward-only Alembic migration。
- ApiTimestampV1/TimeProvider/high-water、operation records、collection creation sequence、Event payload registry 与 emergency reserve 恢复。
- Run/Segment/Step/Attempt/ToolCall/Approval/Event。
- 单执行器 FIFO、预算、取消与恢复。
- 独立 tool contract version、当步 tool set/hash 和 canonical ToolResult 持久化。
- Immutable base、Step projection/hash、ToolResult quarantine 和读取证据完整/模型投影分层。

### M3：模型与只读闭环

- Model Profile 编辑/Archive/凭证隔离、显式能力探测、版本化 capability snapshot 与 Gateway 流。
- 双 wire 的工具控制、Tool Schema Dialect v1、effective arguments 和两阶段 ToolCall/ToolResult probe。
- ToolResult canonical JSON v1、闭合 code/data registry 与 list/read/range/search/file/Artifact/Shell result projector。
- Context Builder 与有界裁剪。
- 四个只读工具、批次校验和有限重试。
- Execution Feed 基础展示。

### M4：审批与副作用工具

- write/apply/delete 单文件审批。
- 版本复检和原子提交。
- run_shell Seatbelt 执行、输出上限和事实确认屏障。

### M5：Artifact、恢复与产品验收

- publish_artifact 不可变快照。
- Public/Workspace UI。
- 崩溃恢复、Finalization、stopped。
- 系统 Terminal 打开入口。
- PRD 全量验收与安全回归。

## 8. 文档完成标准

- PRD 每个 P0 要求在 TDD 和测试中有对应落点。
- Q1-Q155 决策不得出现相反规则。
- 实现开始前冻结 v0.4 API schema、状态 enum 和 Tool schema。
