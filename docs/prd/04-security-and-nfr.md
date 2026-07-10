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
- 内容扫描命中 private key、API key、access token 或 password 的文件。

`.env.example` 等明确模板可以加入例外列表，但仍接受内容扫描。

Shell 输出在返回模型前必须脱敏；模型、工具、Event 和日志在落盘前必须再次经过统一脱敏层。

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
- `local_network=true` 是独立的单次审批能力，允许绑定和访问 loopback。
- 外网域名权限不隐含 localhost 权限，localhost 权限不隐含外网权限。
- ToolCall 完成后立即撤销网络权限。

## 6. 写入一致性

- 已有文件审批绑定 `base_sha256`。
- 新文件审批绑定 `expected_absent=true`。
- 删除审批绑定路径、文件类型和 `base_sha256`。
- 执行前重新校验；不一致时审批失效并返回 `file_version_conflict`。
- 文件落盘采用临时文件和原子替换。
- 一次文件工具只操作一个普通文件。

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
- 崩溃后将运行中的 ToolCall 标记为 `interrupted`，Run 等待用户确认。
- 文件提交关键区不可被取消打断。
- Shell timeout 或取消必须终止整个进程组。
- 达到 Run 硬上限后安全停止并保留已有 Artifact 与 Timeline。

## 10. 数据与容量

- Public files、Artifact、Event 和日志默认不自动清理。
- Shell stdout 最多保存 768KB，stderr 最多 512KB，合计最多 1MB。
- 返回模型的 Shell observation 最多 32KB。
- 文件读取和搜索结果必须有单次大小、命中数和上下文预算上限。
- P1 增加数据管理、存储统计和清理策略；MVP 至少展示数据根目录和占用错误。

