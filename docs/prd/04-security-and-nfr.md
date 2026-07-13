# 安全与非功能需求

版本：v0.4

## 1. 安全承诺

审批是用户对一次操作的授权，不是沙箱替代品。任何审批都不能扩大 ToolCall 声明的文件、网络和进程边界。

沙箱不可用时 Shell 不可用；Eidos 不提供无沙箱回退或“仍然执行”按钮。

## 2. 文件系统边界

`run_shell` 默认采用 `workspace_write`：

| 路径类别 | 权限 |
|---|---|
| active root | 读写，但敏感路径拒绝、`.git` 只读 |
| Eidos 为本次 ToolCall 分配的 temp/cache/virtual home | 读写 |
| System Runtime 与批准的工具链 | 只读 |
| 真实用户 Home 和其他用户数据路径 | 拒绝 |

文件工具与 Shell 必须拒绝路径穿越、绝对路径逃逸、前缀碰撞和符号链接逃逸。

路径验证使用文件系统实际目录项身份，不通过小写化、Unicode 规范化或字符串前缀推测目标。文件工具只修改当前用户所有的普通文件；其他所有者或 immutable/append-only flags 文件拒绝。

## 3. 敏感文件

以下文件不能通过审批绕过：

- `.env`、`.env.local` 等真实环境文件。
- 私钥、证书、云凭证、包管理器 token 文件。
- 文件名明确包含 `secret`、`token`、`credentials` 的凭证文件。
- 内容扫描命中高置信度 private key、已配置 API key、access token 或 password 的文件。

`.env.example` 等明确模板可以加入例外列表，但仍接受内容扫描。

内容规则分为：

- `deny`：高置信度真实凭证，文件读取整体拒绝。
- `redact`：疑似凭证，普通输出中替换命中片段。
- `allow_with_audit`：变量名、注释、明显占位符和测试示例，允许使用但记录非敏感命中元数据。

该分级由随应用发布的确定性规则给出，MVP 不调用模型判断，不提供用户白名单、临时关闭或 Approval 绕过。

敏感扫描覆盖用户输入、文件读取和搜索、写入/Patch/Shell 参数、Shell/模型输出、Artifact 发布与所有持久化 payload。扫描必须早于截断、摘要、展示、模型观察和落盘。

- 用户输入命中 `deny`/`redact` 时整次提交拒绝，不创建 Message、Run 或 Segment。
- 写入、Patch、Shell 和 Artifact 命中 `deny`/`redact` 时整个操作拒绝，不会替换后继续。
- Shell 和模型普通输出在返回模型或 UI 前脱敏；持久化前再执行统一扫描。
- 已持久化的消息、Timeline、工具日志和 Artifact 再次读取时使用当前规则重新扫描；MVP 不因此静默改写历史记录或不可变快照。
- 扫描超限、超时、编码异常或服务失败时 fail closed，不释放未确认安全的内容。
- 原始命中、长度、摘要和哈希均不得进入 UI、模型、SQLite、Event 或日志。

## 4. Shell 环境

- 固定使用 `/bin/zsh -f -c`。
- 不加载 `.zshrc`、`.zprofile` 等用户配置。
- 不继承用户环境变量、代理、云凭证或 SSH Agent。
- `HOME`、`TMPDIR` 和缓存目录指向 Eidos 管理目录。
- `PATH` 仅包含 System Runtime 和批准的只读工具链。
- `.git` 只读，并设置 `GIT_OPTIONAL_LOCKS=0`。
- Unix Domain Socket 在 MVP 中始终禁止。

## 5. 网络边界

- 默认禁止外网和 localhost。
- 外网访问必须声明域名白名单并经过本次 Shell 审批。
- Shell 只能连接 Eidos 本地受控代理，由代理执行域名策略。
- 白名单只接受规范化精确 host；非默认端口必须显式审批，不支持通配符。
- Proxy 对每个解析 IP 拒绝 loopback、私网、link-local、multicast 和 metadata 地址。
- Redirect 到新 host 时阻断并要求重新审批。
- `local_network=true` 是独立的单次审批能力，允许绑定和访问 loopback。
- `local_network=true` 不允许外部域名映射到本机地址。
- 外网域名权限不隐含 localhost 权限，localhost 权限不隐含外网权限。
- ToolCall 完成后立即撤销网络权限。
- Proxy 只保存 host、port、allow/deny、规则、时间和流量大小；不保存 URL、Header、Body，也不做 TLS MITM。

## 6. 写入一致性

- 已有文件审批绑定 `base_sha256`。
- 新文件审批绑定 `expected_absent=true`。
- 删除审批绑定路径、文件类型和 `base_sha256`。
- 执行前重新校验；不一致时审批失效并返回 `file_version_conflict`。
- 文件落盘采用临时文件和原子替换。
- 一次文件工具只操作一个普通文件。
- 写入、修改和删除已有文件要求 `st_nlink=1`；多链接目标返回 `hardlink_not_allowed`。
- Writable Shell 执行前发现 active root 中存在多链接普通文件时 fail closed。
- 修改已有文件必须保留 POSIX mode、ACL 和 extended attributes；元数据复制或验证失败时原文件不变。
- 新文件默认 mode 为 `0644`；设置 executable bit 必须另行审批 Shell。
- 文件工具只处理严格 UTF-8 普通文件；已有 UTF-8 BOM 在修改时保留，新文件无 BOM。
- NUL、已知二进制 magic 和超阈值控制字节使文件按二进制拒绝；严格 UTF-8 失败使用不同错误。
- 修改已有文件还必须保留 gid 和可保留 file flags；新文件归当前用户，gid 按父目录/系统语义创建。
- 候选文件必须在审批前完成编码、敏感内容、类型、容量和完整 Diff 验证。
- `write_file` 覆盖已有文件必须引用当前 Run 的完整读取证据；`apply_patch` 每个 hunk 必须引用同 hash 下覆盖原文范围的读取证据。
- 已脱敏的读取不能证明 Agent 看过原文：含脱敏命中的完整读取不授予覆盖资格，命中所在整行不计入 Patch 读取证据。
- Patch 只按声明行号和原文精确应用，禁止 offset 搜索和 fuzz matching。
- 文件变更会使旧 hash 的读取证据失效；任何证据均不能跨 Run 复用。
- 父目录不存在时写入失败，不隐式创建目录；审批期间父目录身份或权限变化使审批失效。
- 删除不要求事先读取正文，但必须展示 Runtime 生成的完整删除 Diff，并在执行前复检路径、父目录、类型、编码和 hash。
- 单文件读取与搜索匹配必须来自稳定文件版本；并发变化时丢弃该文件结果，不把多个版本拼接为一次读取。

## 6.1 遍历与排除

- 安全排除永不可绕过。
- `list_files`/`search_text` 使用文档化的固定性能排除集，MVP 不解析 `.gitignore` 或用户配置。
- 性能排除不是文件访问权限；已知具体文件仍可通过直接读取或受审批写工具访问。

## 7. API Key MVP 风险接受

MVP 为尽快打通 Agent 主链路，允许 API Key 明文存放在 `~/.eidos/config.toml`。最低要求：

- `~/.eidos` 权限为 `0700`，`config.toml` 权限为 `0600`。
- 启动时发现权限过宽，拒绝加载密钥并提示修复。
- API Key 不进入 SQLite、Run 快照、Event、日志、错误或 Shell 环境。
- Agent Shell 无权读取真实 `~/.eidos`。
- 文档和 UI 必须明确这是 MVP 风险接受项，不得描述为加密存储。

## 8. Desktop 安全

- `contextIsolation=true`、`nodeIntegration=false`、Renderer sandbox 开启。
- Renderer 不持有 sidecar token 和端口，不直接访问 sidecar。
- Preload 只暴露类型化、按通道白名单的 API，不提供任意 `invoke(channel)`。
- 模型生成的 Markdown、代码和链接按不可信内容处理，禁止导航、脚本和任意本地资源访问。
- “打开系统 Terminal”只能由用户 UI 操作触发，模型和 Runtime 无法调用。

## 9. 可恢复性与可靠性

- Runtime 完成状态目录校验、唯一执行权、数据库 revision/完整性、契约和安全自检、崩溃对账与 FIFO 恢复后才开放业务 API 和调度；失败时只保留最小安全诊断。
- SQLite 只允许向前迁移；迁移前创建一致、可验证且不自动覆盖的备份。未知或更高 schema、备份失败、迁移失败或完整性失败均不得继续执行业务。
- 状态变更与 Event 在同一 SQLite 事务中提交。
- 有副作用 ToolCall 不自动重放。
- 有副作用 ToolCall 执行前必须先提交 execution intent、nonce、前置条件和预期后置条件。
- 执行结果提交失败或进程崩溃后，只能根据 hash/快照对账，不能重新执行原副作用。
- 崩溃后将运行中的 ToolCall 标记为 `interrupted`，Run 等待用户确认。
- 文件提交关键区不可被取消打断。
- Shell timeout 或取消必须终止整个进程组。
- 每个 Agent Shell 由最小生命周期 guardian 托管；Main/sidecar 失联、deadline 或取消时清理受控进程组和已识别后代。MVP 不把该能力描述为 macOS 上任意进程树的绝对证明。
- 达到 Run 硬上限后安全停止并保留已有 Artifact 与 Timeline。
- Workspace 授权同时绑定用户选择路径和跨重启持久目录身份；身份缺失、移动或替换时 fail closed，不自动 rebind，也不复活旧 Approval。
- 同一状态目录的 OS 独占锁是唯一执行权依据；PID、锁文件内容和进程 nonce 仅用于诊断，不能用于抢占。
- Renderer 发起的持久化写操作使用跨重启 operation idempotency；它只防止本地状态重复提交，不承诺 Provider、Shell 或文件系统 exactly-once。
- 当前状态只能来自规范化表/详情 API；Event 是可回放增量，新增可忽略类型不能成为旧客户端恢复正确状态所必需的唯一事实。

### 9.1 时间与存储故障

- DB/API/Event 的业务时间统一为 UTC Unix epoch milliseconds 的 JSON safe integer；持续时间、预算和进程内 deadline 使用 monotonic clock，UI 才转换本地时区。
- 系统时钟回拨不得延长已有有期限授权；Shell Approval 绑定可验证的 boot-session/continuous-monotonic deadline，同 boot 重启延续原期限，boot/timebase 不可证明或检测到回拨时在开放业务前失效。sleep/停机时间计入原 TTL，时钟前跳可以安全地提前失效。
- SQLite/state root 无法可靠提交时，Runtime 停止调度和业务写入，进入 health-only。未提交结果不能仅凭内存向 UI 声称成功。
- MVP 可保留同卷、有界、真实分配的 emergency reserve 辅助空间耗尽后的 rollback/诊断，但不承诺足以完成任意 WAL/事务；I/O 或损坏故障不得通过释放 reserve 假装修复。
- 恢复必须先满足空间、WAL、integrity、foreign key 和 durable intent 对账，再重新 ready。Eidos 不自动删除 Event、Artifact、日志、backup 或用户文件来腾空间，也不自动覆盖当前数据库。

## 10. 数据与容量

- Public files、Artifact、Event 和日志默认不自动清理。
- Shell stdout 最多保存 768 KiB，stderr 最多 512 KiB，合计最多 1 MiB；合计超限时 stderr 优先，流内保留 head+tail。
- 返回模型的 Shell observation 最多 32KB。
- 文件读取和搜索结果必须有单次大小、命中数和上下文预算上限。
- 单文件敏感扫描上限为 32 MiB；超限文件不返回正文。
- 单次 `search_text` 最多扫描 256 MiB 或 15 秒；只能返回已完成整文件扫描的结果并明确标记截断原因。
- 稀疏文件的读写上限按逻辑字节计算；Shell 磁盘增长保护按已分配 blocks 计算并同时展示逻辑/已分配变化。
- P1 增加数据管理、存储统计和清理策略；MVP 至少展示数据根目录和占用错误。

## 11. Shell 审批、审计与资源

- Shell Approval 授权 command、active root、沙箱/环境模板、Toolchain Profile、timeout 和网络权限，不授权审批时 Workspace 快照。
- 批准后 5 分钟未开始的 Shell 授权失效。
- Writable Shell 执行前后建立受控 manifest，只将变化归因为“执行窗口内观察到”，不自动回滚。
- Shell 固定使用 64 进程、256 fd、2 GiB 进程树 RSS、1 GiB 单文件和 2 GiB 已分配磁盘净增长上限。
- 资源监控或输出捕获故障时终止整个进程组，保留真实结果并进入事实确认屏障。

## 12. Model Provider 连接边界

- Model `base_url` 不按公网、loopback、局域网或私网类别设置地址白名单，但只接受 HTTP(S)。
- URL 不允许内嵌用户名、密码或 token；同 Origin Redirect 可以继续，跨 Origin Redirect 必须拒绝。
- HTTPS 必须校验证书有效期、主机名和 macOS 系统信任链，不提供关闭校验、忽略错误或单次继续入口。
- HTTP 端点允许使用，但 UI 必须明确提示 API Key、任务和模型上下文将通过非加密连接传输。
- 每个 Model Profile 独占凭证槽位；API Key 保存后不回显、不共享，替换只影响本 Profile。
- Run 快照不包含 API Key；Gateway 每次发送前从 Profile 专属槽读取当前凭证。日志、Event、Attempt 和 capability snapshot 只记录凭证 revision，不记录密钥或其摘要。
- 认证仅支持固定 `bearer`、`api_key_header` 和 `none` 模式；用户不能用扩展参数注入 Header、代理、证书或传输配置。
- WebSocket 与 HTTP(S) streaming 使用相同的认证和敏感日志边界；WSS/HTTPS 必须执行相同的系统信任校验。传输降级不得关闭证书校验、改变 Origin 或泄露 Provider 原始错误正文。
- usage 缺失必须标记为 unknown；不得为了展示确定成本而推算或填零。Provider 重放可能产生重复计算或计费，UI 必须如实展示 Attempt 数和 usage 完整性。
- Eidos 不主动请求 Provider 保存会话：Responses 使用 `store=false`，Chat 每次发送本地重建的消息。该设置不构成第三方零留存保证，UI 必须提示实际保留仍受 Provider 条款约束。
- Provider response/conversation ID 只可作为审计元数据，不得成为恢复、ToolResult 续接或传输降级的唯一状态来源。
- 模型流在协议解码后执行单 Event、总 payload、可见文本、reasoning 和 ToolCall 参数多层容量检查；超限内容不得进入 Event、日志、数据库或审批。

## 13. 工具输入与结果边界

- Provider 返回的 ToolCall 参数不因 `strict` 声明而受信；未通过本地封闭 schema、组合、敏感扫描和当前状态复检时零执行。
- 默认值只能是随工具契约发布的静态 JSON literal，不得从时间、Workspace、环境或隐藏状态派生。审批所见参数必须与最终执行参数相同。
- 工具可用性裁剪只能依赖固化契约与可审计 Runtime 状态；不得根据任务文本、常用程度或模型输出猜测。
- ToolResult 的模型可见内容只来自封闭 envelope 及按 contract/tool/outcome/code 定义的 data schema；summary 必须有界、固定模板化且已扫描，不提供另一条任意文本旁路。
- ToolResult integer 必须在跨 Python/JavaScript 精确可表达的 safe integer 范围内；真实工具测量溢出时返回固定安全错误，不允许四舍五入、字符串替代或失真发送。
- Eidos Canonical JSON v1 保留 Unicode code point 原样，不做 NFC/NFD 规范化；非法 surrogate、递归重复 key 和非规范整数拒绝，Python 与 JavaScript 必须产生相同 UTF-8 bytes。
- Provider、OS、异常类型和用户反馈原文不得进入 ToolResult code 或 summary；动态内容只能进入明确有界、递归封闭且重新扫描的 data 字段。
- ToolResult base 是不可变事实。Context 投影只能按版本化规则删除或加强脱敏已声明可裁剪字段，并保存省略量；不得改变 outcome、code、工具级截断或副作用状态，也不得在后续 Step 重新显露另一批等量内容。
- 敏感隐藏文件既不进入 list/search 项目，也不进入模型可见聚合计数；避免通过多次差分推断受保护路径。
- ToolResult schema、投影或序列化 invariant 失败时不得返回自由文本 fallback。Runtime 保留真实副作用事实、终止当前 Run，并隔离故障工具或整个结果能力，直到受验证的新实现解除。
- ToolResult error 不提供通用 `details/message/cause` 或任意 map；只有按工具与 code 声明的有界字段可进入模型。API Error 是独立的 Renderer/HTTP 契约，也必须按 code 闭合并安全化。
- Reject feedback 是唯一共享 rejection data 的可选用户原文：必须先扫描、不得截断，且不得进入 summary。Provider/OS 动态错误、stack、errno 和内部诊断不能因只用于审计而绕过扫描与最小化。
- 模型可见 Shell 字节计数只使用脱敏后的 observation 口径；原始 pipe 字节数、完整 manifest、敏感路径和真实 snapshot 路径不得进入 ToolResult、Event 或模型上下文。
- `side_effects_may_exist=true` 不得单独自动推导事实确认；但任何 `reconciliation_required=true` 的工具结果都必须同时标记可能存在未确认副作用。
- 每次新的副作用不确定性形成独立、持久化的确认 episode；只有之后正常完成的只读 Step 至少提交一个成功观察，才允许下一 Step 重新获得副作用工具。合法空观察可作为事实，旧观察不能清除更新的 episode。
- 旧 `tool_contract_version` 不得降级当前 Redaction、Workspace Guard、Seatbelt、网络和全局资源底线；无法安全兼容时停止执行而不是恢复旧安全语义。
