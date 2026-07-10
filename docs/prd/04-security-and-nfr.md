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

- 状态变更与 Event 在同一 SQLite 事务中提交。
- 有副作用 ToolCall 不自动重放。
- 有副作用 ToolCall 执行前必须先提交 execution intent、nonce、前置条件和预期后置条件。
- 执行结果提交失败或进程崩溃后，只能根据 hash/快照对账，不能重新执行原副作用。
- 崩溃后将运行中的 ToolCall 标记为 `interrupted`，Run 等待用户确认。
- 文件提交关键区不可被取消打断。
- Shell timeout 或取消必须终止整个进程组。
- 达到 Run 硬上限后安全停止并保留已有 Artifact 与 Timeline。

## 10. 数据与容量

- Public files、Artifact、Event 和日志默认不自动清理。
- Shell stdout 最多保存 768KB，stderr 最多 512KB，合计最多 1MB。
- 返回模型的 Shell observation 最多 32KB。
- 文件读取和搜索结果必须有单次大小、命中数和上下文预算上限。
- 单文件敏感扫描上限为 32 MiB；超限文件不返回正文。
- 单次 `search_text` 最多扫描 256 MiB 或 15 秒；只能返回已完成整文件扫描的结果并明确标记截断原因。
- P1 增加数据管理、存储统计和清理策略；MVP 至少展示数据根目录和占用错误。
