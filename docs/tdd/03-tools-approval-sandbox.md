# 工具、审批与沙箱

版本：v0.4

## 1. ToolDefinition 与 ToolContext

```python
class ToolDefinition(BaseModel):
    name: str
    side_effect: Literal["none", "workspace", "eidos_state", "shell"]
    requires_approval: bool
    allow_batch: bool
    default_timeout_seconds: int
    max_timeout_seconds: int
    input_schema: dict

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

Tool arguments must be schema-validated and sensitive-scanned before a ToolCall row is created. Tool result content passes through Redaction Service before truncation, model observation, UI delivery and persistence.

## 2. 工具注册表

| Tool | Side effect | Batch | Approval |
|---|---|---|---|
| list_files | none | yes | no |
| read_file | none | yes | no |
| read_file_range | none | yes | no |
| search_text | none | yes | no |
| write_file | workspace | no | yes |
| apply_patch | workspace | no | yes |
| delete_file | workspace | no | yes |
| run_shell | shell | no | yes |
| publish_artifact | eidos_state | no | no |

## 3. Workspace Guard

所有路径参数必须：

1. 是相对于 active root 的规范化相对路径。
2. 拒绝绝对路径和 `..`。
3. 使用 `Path.resolve()` 后通过 `relative_to(root)` 验证。
4. 对最终路径和每个已存在父目录检查符号链接逃逸。
5. 在文件打开后校验文件描述符对应的真实路径/类型，降低 TOCTOU。

字符串 `startswith` 不能用于路径边界判断。

## 4. 只读工具

### 4.1 list_files

- 只列出 active root。
- 默认排除依赖目录、构建产物、二进制和敏感文件内容。
- Workspace UI 与 Agent Tool 使用同一 Guard，但 UI 文件树不等于 Agent 可读取权限。

### 4.2 read_file

| 文件大小 | 行为 |
|---|---|
| <= 512KB | 完整读取，单次结果仍不超过 256KB |
| 512KB..2MB | head+tail，`truncated=true` |
| > 2MB | 拒绝，要求 read_file_range |

二进制直接拒绝。文本文件在返回任何正文前执行最多 32 MiB 的全文件流式敏感扫描；任意位置命中 `deny` 时整次读取拒绝，只命中 `redact` 时替换对应片段后返回其余内容。

### 4.3 read_file_range

参数：`path, start_line, end_line`。返回实际范围、总行数、文件大小和截断状态。即使只返回局部行，也必须先完成同一上限下的全文件扫描；不能只扫描请求范围。

### 4.4 search_text

- literal search，默认大小写不敏感。
- MVP 拒绝 regex。
- 必须提供受上限约束的 `max_results`。
- 排除依赖、构建、lock、二进制和敏感路径。
- 返回 path/line/column/preview；preview 先脱敏。
- 每个候选文件完成全文件扫描后才能返回该文件的结果；`deny` 命中的文件零结果，只命中 `redact` 时 preview 脱敏后返回，并继续搜索其他文件。
- 单次最多扫描 256 MiB 或 15 秒；达限只返回已完成整文件扫描的结果，并设置 `truncated=true, stop_reason=scan_budget_exceeded`。

文件与搜索扫描共用工具的单一 deadline，扫描时间计入现有 10–15 秒 Tool timeout，不创建可无限延长的隐藏扫描阶段。

## 5. 文件写工具

共同规则：

- 一次 ToolCall 只作用于一个普通文件。
- 必须是模型响应中的唯一 ToolCall。
- 审批卡展示路径、理由、diff 和版本前置条件。
- 批准后执行前重新校验。
- 使用同目录临时文件、fsync 和原子 replace；commit 区不可取消。
- 已有目标必须满足 `st_nlink == 1`；多链接文件返回 `hardlink_not_allowed`。
- 修改已有文件时，临时文件必须复制并验证 POSIX mode、ACL 和 extended attributes；失败时返回 `file_metadata_preservation_failed`，不进入 replace。
- 完整 content/patch 在创建 ToolCall/Approval 前扫描；`deny` 或 `redact` 命中时不保存原参数，也不使用占位符内容继续执行。

### 5.1 write_file

- 创建新文件：`expected_absent=true`。
- 新文件使用独占创建并固定 mode `0644`，不自动设置 executable bit。
- 完整覆盖已有文件：必须已经读取并携带 `base_sha256`。
- 已有文件默认优先使用 apply_patch。

### 5.2 apply_patch

- 只修改一个已存在普通文件。
- patch 必须基于已读取内容并携带 `base_sha256`。
- 应用前先在内存副本验证 patch 完整成功，再进入 commit。
- 使用保留原 mode、ACL 和 xattr 的临时副本完成原子替换；不要求保留 inode number。

### 5.3 delete_file

- 只删除一个已存在普通文件。
- 禁止目录、递归、通配符、批量和 symlink 本体/目标混淆。
- 审批绑定 path、file_type、base_sha256。
- 执行前验证 `st_nlink == 1`。
- 明确删除任务的 Agent 策略必须优先调用 delete_file。

`run_shell` 中的间接删除不构成沙箱违规；明显 `rm` 可在审批卡警告，但命令识别不是安全边界。

## 6. publish_artifact

参数：

```text
path
display_name
artifact_type
summary
```

执行规则：

- 只能发布 active root 内普通文件。
- 必须独占模型响应，但不需要审批。
- 复制到 `state_root/artifacts/{artifact_id}/`，不能保存 symlink 或动态引用。
- 复制前后校验源文件 stat/hash；变化则失败，不发布不一致快照。
- 保存 source_path、source_sha256、snapshot_sha256、size、mime、version。
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

输出限制：

- stdout 768KB，stderr 512KB，合计 1MB。
- stdout/stderr 的 `deny` 和 `redact` 命中都以统一占位符替换后继续；不因高置信度命中停止已获批 Shell，但原文不得进入任何下游。
- 超限保留 head+tail，标记 truncated，不因此终止命令。
- 模型 observation 最多 32KB，并在返回模型前脱敏。
- 保存 exit_code、duration、sizes、termination_reason 和 `side_effects_may_exist`。

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

应用启动执行 Seatbelt 自检：

- active root 测试目录可写。
- sandbox temp 可写。
- 外部用户文件不可读写。
- 敏感 carve-out 不可读。
- `.git` 不可写。
- 多链接普通文件使 writable Shell 自检/前置检查失败。
- 默认外网/loopback/Unix Socket 不可用。

Redaction Service 同时校验规则 schema、唯一 rule id、重叠排序、8 KiB 长度上限、幂等性和全部 test vectors。自检失败时所有内容通路 unavailable，不仅限于 Shell。

`sandbox-exec` 缺失、策略生成/编译失败或自检失败时，Shell capability 为 unavailable，审批和 API 都不能绕过。
