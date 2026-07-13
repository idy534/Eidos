# 验收标准

版本：v0.4

## 1. 产品闭环

| 编号 | 验收项 | 标准 |
|---|---|---|
| A001 | macOS 启动 | Electron 启动并拉起 sidecar；非 macOS 明确拒绝 |
| A002 | 两种模式 | Workspace/Public Session 均可创建 Run |
| A003 | Model Profile | Session 可选 Profile，Run 固化不含密钥的快照 |
| A004 | 多 Run | 多个 Run 可创建、查看、排队、暂停和取消 |
| A005 | 单执行器 | 任意时刻最多一个 Run 调模型或执行工具 |
| A006 | FIFO | 新建和恢复按 `enqueued_at` 排队，重启后顺序保持 |

## 2. 工具与审批

| 编号 | 验收项 | 标准 |
|---|---|---|
| A010 | 只读批次 | 多个只读调用按顺序执行，单项失败不阻断后续 |
| A011 | 非法批次 | 混合副作用或多个副作用调用时整批零执行 |
| A012 | 单文件写入 | write/apply/delete 每次只作用于一个普通文件 |
| A013 | Diff 审批 | write/apply/delete 审批展示实际目标与 diff |
| A014 | 版本复检 | 审批等待期间文件变化会返回 `file_version_conflict`，不写入 |
| A015 | 删除工具 | delete_file 拒绝目录、递归、通配符和批量 |
| A016 | Artifact 发布 | 只有 publish_artifact 产生可见 Artifact |
| A017 | Artifact 不可变 | 修改/删除源文件不影响已发布快照；重复发布产生新版本 |
| A018 | Reject | 连续两次 Reject 后等待用户；计数按约定重置 |

## 3. Shell 安全

| 编号 | 验收项 | 标准 |
|---|---|---|
| A020 | Seatbelt | Shell 通过 `/usr/bin/sandbox-exec` 启动，子进程继承策略 |
| A021 | Fail closed | 沙箱缺失、自检或策略失败时 Shell 不可执行 |
| A022 | Workspace 写 | 可在 active root 和 Eidos temp 创建、修改、删除文件 |
| A023 | 外部拒绝 | 不能读写其他用户数据路径 |
| A024 | System Runtime | 系统和批准工具链只读可执行 |
| A025 | 敏感文件 | 文件工具和 Shell 都无法读取不可审批绕过的敏感路径 |
| A026 | Git 保护 | 可执行只读 Git 命令，无法写 `.git` |
| A027 | 环境隔离 | 不加载用户 rc，不继承宿主凭证和真实 HOME |
| A028 | 网络默认拒绝 | 默认不能访问外网、localhost 或 Unix Socket |
| A029 | 域名网络 | 只可通过代理访问本次审批域名，其他域名失败 |
| A030 | localhost | 仅 `local_network=true` 获批 ToolCall 可访问 loopback |
| A031 | 进程终止 | timeout/cancel 后整个 Shell 进程组被终止 |
| A032 | 无长驻服务 | ToolCall 结束后没有 Agent Shell 服务进程遗留 |

## 4. 状态、恢复与预算

| 编号 | 验收项 | 标准 |
|---|---|---|
| A040 | Segment | 20 Steps 或 30 分钟后进入 waiting_user_input |
| A041 | Segment 恢复 | 用户补充信息后新建 Segment 并进入队尾 |
| A042 | stopped | 80 Steps 或 120 分钟后进入不可恢复 `stopped` |
| A043 | Finalization | 硬停止前最多一次无工具收尾；失败有降级摘要 |
| A044 | 崩溃恢复 | 运行中 ToolCall 变为 interrupted，Run 不自动重放 |
| A045 | Approval 恢复 | waiting_approval 重启后仍可决定，工具未提前执行 |
| A046 | 事实确认 | 写/Shell 失败后，未只读核验前副作用调用被拒绝 |
| A047 | 协作式取消 | 原子提交如实完成；其他可取消工作被中断 |

## 5. 模型、事件与数据

| 编号 | 验收项 | 标准 |
|---|---|---|
| A050 | 模型流 | delta 实时可见、分块持久化、最终响应完整保存 |
| A051 | 有界上下文 | 长 Run 不会因无限累积历史而超过配置窗口 |
| A052 | 重试边界 | 模型和只读工具只按瞬时错误规则重试，副作用零自动重试 |
| A053 | Event 事务 | 状态与对应 Event 同时成功或同时回滚 |
| A054 | SSE 回放 | 可从最后 event id 恢复，不重复改变业务状态 |
| A055 | 配置权限 | `.eidos`/`config.toml` 权限不合规时拒绝加载 API Key |
| A056 | 密钥不落库 | SQLite、Event、日志和 Run 快照中不出现已配置 API Key |
| A057 | 统一脱敏 | 模型可见 Shell 结果及所有持久化 payload 均通过脱敏层 |
| A058 | 模型流中断 | 首 delta 后中断时无 ToolCall 执行；未完成进度可见，Run 等待用户输入 |
| A059 | 模型配置错误 | 认证、模型不存在或确定性请求错误直接 failed，不执行 Finalization，可用新 Profile 创建新 Run |

## 6. Desktop

| 编号 | 验收项 | 标准 |
|---|---|---|
| A060 | Renderer 隔离 | Renderer 无法读取 sidecar port/token 或调用未列入白名单的 IPC |
| A061 | 无内嵌 Terminal | MVP 不启动 PTY；用户按钮可打开系统 Terminal |
| A062 | 前台退出 | 运行中 Run 退出前要求等待或取消；等待/排队状态可恢复 |
| A063 | 推理内容边界 | Feed、SQLite、Event 和日志中没有 raw reasoning；只展示 progress/final answer，可保存 token 用量 |

## 7. 可靠性与边界补充

| 编号 | 验收项 | 标准 |
|---|---|---|
| A064 | 瞬时模型故障 | 首 delta 前重试耗尽后 waiting_user_input；多个 Attempt 只计一个 Step |
| A065 | 模型协议错误 | 无效响应零工具执行；连续两次后暂停，合法响应清零计数 |
| A066 | Durable Intent | 副作用前意图已持久化；模拟结果提交失败时恢复只对账不重放 |
| A067 | 代理零内容 | 代理审计中没有 URL、Header、Body 或 TLS 明文 |
| A068 | Host 防绕过 | 通配符、私网解析、非批准端口和跨 host redirect 均被拒绝 |
| A069 | Hardlink 防护 | 文件工具拒绝 `st_nlink>1`；Writable Shell 在存在多链接文件时不可执行 |
| A070 | 元数据保留 | 修改保留 mode/ACL/xattr；元数据失败零替换；新文件为 0644 |

## 8. 敏感内容边界

| 编号 | 验收项 | 标准 |
|---|---|---|
| A071 | 分级规则 | 固定输入在各入口一致得到 `deny`/`redact`/`allow_with_audit`、rule id/version 及 ruleset version |
| A072 | 全文件拒绝 | 文件任意位置命中 `deny` 时 read/range/search 不返回该文件任何正文 |
| A073 | 输入阻断 | 任务/补充命中时不落库、不发模型、不创建/恢复 Run 且不占用 idempotency key；审批 feedback 命中时决策保持 pending |
| A074 | 副作用阻断 | 敏感写/Patch/Shell/Artifact 零执行、零 Approval，不以脱敏内容代替执行 |
| A075 | 跨块脱敏 | 跨 output chunk 的凭证不会在 UI、模型观察、Event、SQLite 或日志泄露 |
| A076 | 扫描顺序 | 原始内容先扫描再截断/摘要；扫描失败或超限不释放未确认正文 |
| A077 | 占位符 | 脱敏统一为 `[REDACTED:<rule_id>]`，无原值长度、摘要、哈希或跨记录关联标识 |
| A078 | 敏感重试 | 模型连续两次生成敏感 ToolCall 后 waiting_user_input，且不增加协议错误计数 |
| A079 | 规则集失效 | 规则自检失败时不存在未扫描降级通道；用户无法热加载、白名单或审批绕过 |
| A080 | 规则版本变更 | 升级后恢复旧 Run 使用新规则并记录版本变更；旧内容读取时按当前规则扫描；旧应用不能降级已生效规则 |

## 9. 文件工具精确契约

| 编号 | 验收项 | 标准 |
|---|---|---|
| A081 | read_file 分级 | <=256 KiB 完整；256 KiB..2 MiB 返回 <=256 KiB head+tail；>2 MiB 拒绝并引导 range |
| A082 | range 语义 | 1-based 闭区间；2,000 行/256 KiB 截断可继续；不返回半行 |
| A083 | 编码边界 | 严格 UTF-8/可选 BOM 成功；二进制与其他编码错误可区分；修改保留 BOM |
| A084 | 搜索稳定 | literal/ASCII case-fold、路径-行-列排序、重叠匹配、preview 和首个停止原因符合契约 |
| A085 | 文件树有界 | 深度/条目上限、隐藏敏感条目、`.git` 不展开、symlink 不跟随且不泄露 target |
| A086 | 完整 Diff | write/apply/delete Diff 由 Runtime 生成；超出 512 KiB/5,000 行时零 Approval，UI 无截断审批 |
| A087 | 父目录 | 父目录不存在时零创建；审批期间父目录变化使 Approval 失效 |
| A088 | 覆盖证据 | write_file 只覆盖 <=256 KiB 且当前 Run 已完整、无脱敏地读取同 hash 的文件 |
| A089 | Patch 证据 | 每个 hunk 原文都被引用的同 hash 非脱敏读取区间覆盖；无 offset/fuzz；变更后旧证据失效 |
| A090 | 删除对账 | 仅 <=512 KiB 普通 UTF-8 文件可完整 Diff 审批删除；崩溃后不重删同路径新文件 |

## 10. 文件与 Shell 闭环

| 编号 | 验收项 | 标准 |
|---|---|---|
| A091 | 排除分层 | 安全排除无绕过；固定目录/lock 精确匹配；`.gitignore` 不影响 MVP 结果 |
| A092 | 单文件一致 | 读取中变化零正文/零证据；搜索变化文件零匹配但继续其他文件；无跨文件快照假象 |
| A093 | Unified Diff | 仅单文件标准 hunk；路径/行号/原文精确；Git 扩展/mixed newline/offset/fuzz 均拒绝 |
| A094 | write 换行 | 新文件只 LF；覆盖保留原 LF/CRLF/BOM/末尾换行语义；无静默规范化 |
| A095 | 文件 no-op | 相同候选字节零 Approval、零 intent、零文件接触，ToolCall `skipped/no_changes`，Reject 计数不清零 |
| A096 | Shell 授权时效 | 审批参数/环境精确绑定；开始前超过 5 分钟失效；UI 明示不绑定 Workspace 快照 |
| A097 | Shell Manifest | 前置超限零执行；后置 created/deleted/content/metadata/type 变化可审计；敏感名隐藏；崩溃不重放 |
| A098 | Toolchain Profile | 默认系统 PATH；Homebrew/本地根须用户启用且替换后自动禁用；无真实 Home/rc/任意根回退 |
| A099 | Shell 资源保护 | fork、fd、RSS、单文件、磁盘增长和监控器故障均终止进程组，sidecar 保持可用 |
| A100 | Shell 输出 | 双流交错序可回放；100ms/4 KiB 合并；stderr 优先 1 MiB head+tail；慢订阅者不阻塞子进程 |
| A101 | 路径名称稳定 | case-sensitive/insensitive 卷均以真实目录项唯一定位，Unicode/case 别名不能迁移 Approval |
| A102 | 二进制判定 | NUL、已知 magic、控制字节阈值与严格 UTF-8 错误稳定可测，无替换字符降级 |
| A103 | 所有权与 flags | 非当前 uid、immutable/append-only 文件零修改；成功修改保留 gid/mode/ACL/xattr/可保留 flags |
| A104 | 稀疏文件 | 文件工具按 logical size 限制；Shell 磁盘保护按 allocated blocks；审计同时展示两种变化 |
| A105 | Artifact 发布边界 | <=32 MiB 严格 UTF-8 文本发布为不可变快照；二进制/压缩/加密/格式不可扫描时零 Artifact |
