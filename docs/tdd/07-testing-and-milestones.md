# 测试与里程碑

版本：v0.4

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

### 2.3 ToolCall 批次

- N 个只读工具合法并保持顺序。
- 只读单项失败继续执行后续。
- read + apply、write + shell、两个 shell 整批零执行。
- publish_artifact 独占且不创建 Approval。
- 非法 model output 生成 model_protocol_error。

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
- 非当前 uid、immutable/append-only flags 拒绝；gid/mode/ACL/xattr/可保留 flags 完整复制验证失败零 replace。
- 逻辑/已分配字节对稀疏文件的分开计量，不以 sparse allocated size 放宽文件工具上限。
- publish_artifact 仅 <=32 MiB 严格 UTF-8、display_name/type 校验、二进制/PDF/Office/压缩/加密拒绝、snapshot 0600/hash 读时复检与 corrupted 状态。

### 2.5 状态机

- Run/Segment/Step/ToolCall/Approval 合法和非法流转。
- approve/reject/cancel 并发只有一个成功。
- Reject 计数增加和两种清零条件。
- Segment 20 Steps/30 分钟暂停。
- Run 80 Steps/120 分钟 Finalization -> stopped。
- Finalization 超时/失败生成降级摘要。
- reconciliation_required 屏障拒绝副作用，读取后解除。

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
- 不可裁剪输入超限时零 Provider 请求、零 started Attempt、Run failed/context_input_too_large、snapshot 不失效且预算明细不含原始 payload。
- model request contract 升级使旧 Profile snapshot 失效；既有 Run 继续旧 serializer/budget/timeout。unsupported 旧版本零 Step/Attempt 并进入 runtime_contract_unsupported。
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
- 认证、模型存在、streaming、ToolCall 参数解析和 usage 分别成功；任一失败都产生新的 `failed` snapshot 和安全错误码，Profile 仍不可选择。
- 全部通过时产生 `passed` snapshot；snapshot version 单调递增并绑定 configuration/Gateway/model request contract version，不覆盖历史结果。
- Session 创建/切换和 Run 创建均拒绝未验证或最新探测失败的 Profile；拒绝创建 Run 时不占用 idempotency key。
- Run 内嵌创建时的 Profile/capability snapshot；后续重新探测成功或失败都不改变既有 Run。
- 正常运行违反已通过的 streaming/ToolCall/usage 契约时返回 `model_capability_drift`，Run 直接 failed，零重试、零 Finalization、零 Profile 切换。
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

## 5. Desktop 集成测试

- Main token、sidecar ready port 和认证代理。
- Renderer 获取不到 token/port。
- 未列入白名单 IPC 无法调用。
- Markdown/XSS、导航和本地 URL 被阻止。
- SSE -> IPC -> Feed reducer。
- 文件夹选择与 Workspace unavailable。
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

## 6. 持久化测试

- `foreign_keys=ON`、WAL、busy_timeout 生效。
- enum CHECK、mode/workspace CHECK、unique idempotency key。
- Alembic 从空库升级和重复启动。
- 状态与 Event 原子提交。
- FIFO enqueued_at 重启保持。
- Artifact snapshot/source hash 和版本唯一性。
- capability snapshot 的 `(profile_id,snapshot_version)` 唯一性、五项必需能力、stateless continuation 与可选传输/输出字段、configuration/Gateway/model request contract version，以及 Run 内嵌快照不随外键记录变化。
- capability snapshot 的 WebSocket 可选结果和无 TTL `model_transport_health` 复合主键；新 snapshot 不继承旧 ws_disabled。
- ModelAttempt 的逻辑请求关联、transport、attempt index、实际 credential revision、request output、retry reason、usage reported/unknown 与 Step cycle deadline 可重启审计。
- Profile/snapshot/Run 的 wire API、model request contract、stateless continuation、output token parameter 持久一致；contract upgrade 只失效新 Run 选择，不改写旧快照。
- ToolCall 的内部 id/provider_call_id 双标识、`UNIQUE(step_id,provider_call_id)` 和回传映射；Provider ID 不替代内部外键。
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

## 7. 风险优先里程碑

### M0：macOS 安全可行性

- Seatbelt 静态策略模板和参数绑定。
- System Runtime read-only、active root write、外部 deny。
- 敏感和 `.git` carve-out。
- managed proxy、域名策略、localhost 独立权限。
- fail-closed 自检与集成测试。
- 版本化敏感规则、全入口扫描管线、跨 chunk 脱敏和 fail-closed 自检。
- Toolchain 只读根、Shell Approval 时效、前/后 manifest、进程树资源限制、allocated growth 监控和无阻塞输出捕获实机可行性。

M0 未通过前，不进入 Agent Shell 主链路实现。

### M1：Desktop 与 sidecar

- Electron/React/Python 骨架。
- Token、随机端口、类型化 IPC/API/SSE 代理。
- `~/.eidos` 权限与 config。

### M2：SQLite、队列与状态机

- Alembic schema。
- Run/Segment/Step/Attempt/ToolCall/Approval/Event。
- 单执行器 FIFO、预算、取消与恢复。

### M3：模型与只读闭环

- Model Profile 编辑/Archive/凭证隔离、显式能力探测、版本化 capability snapshot 与 Gateway 流。
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
- Q1-Q110 决策不得出现相反规则。
- 实现开始前冻结 v0.4 API schema、状态 enum 和 Tool schema。
