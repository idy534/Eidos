# 功能需求

版本：v0.4

## 1. P0 功能清单

| 编号 | 功能 | 要求 |
|---|---|---|
| F001 | macOS 桌面应用 | 启动 Electron 应用并拉起本地 sidecar |
| F002 | 默认 Agent | 首次启动创建默认 Eidos Agent；不提供多 Agent UI |
| F003 | Eidos Home | 初始化并校验 `~/.eidos` 权限与目录结构 |
| F004 | Workspace Mode | 选择本地目录并创建 Workspace Session |
| F005 | Public Mode | 不选择项目目录也能创建 Session 和 Run |
| F006 | Model Profile | 配置多个 OpenAI-compatible Profile；Run 固化配置快照 |
| F007 | Run 队列 | 多 Run 可保留；全局单执行器；持久化 FIFO |
| F008 | Execution Segment | 单段最多 20 Steps/30 分钟；恢复创建新 Segment |
| F009 | Run 硬上限 | 最多 80 Steps/120 分钟；到限进入 `stopped` |
| F010 | Agent Loop | 模型调用、ToolCall、观察结果严格串行 |
| F011 | Tool 批次校验 | 只读工具可批量；有副作用工具独占；非法组合零执行 |
| F012 | list_files | 列出 active root 内受控文件结构 |
| F013 | read_file | 分级读取 active root 内普通文本文件 |
| F014 | read_file_range | 按行读取局部内容 |
| F015 | search_text | literal、大小写不敏感的受控搜索 |
| F016 | write_file | 创建单个文件或完整生成单个文件；审批并展示 diff |
| F017 | apply_patch | 修改单个已有文件；审批并展示 diff |
| F018 | delete_file | 删除单个普通文件；禁止目录、递归、通配符与批量 |
| F019 | run_shell | macOS Seatbelt `workspace_write` Shell；每次调用审批 |
| F020 | publish_artifact | 显式发布单个现有文件，生成不可变快照 |
| F021 | Approval | 记录请求参数、权限、diff/命令与用户决定 |
| F022 | 文件版本复检 | 审批后执行前验证 hash、目标不存在或删除前版本 |
| F023 | Reject 恢复 | Reject 返回 Agent；连续 2 次后等待用户输入 |
| F024 | 事实确认屏障 | 写或 Shell 失败后先只读核验，再允许下一次副作用 |
| F025 | waiting_user_input | 用户补充信息后继续同一 Run 并重新排队 |
| F026 | 崩溃恢复 | 不自动重放；运行中 ToolCall 标记 interrupted |
| F027 | Cancel | 排队/等待立即取消；运行中协作式取消 |
| F028 | Model Stream | 实时输出、分块持久化、保存最终响应 |
| F029 | Execution Feed | 展示消息、工具、审批、状态、错误和 Artifact |
| F030 | Event Timeline | 所有关键事件与状态变更同事务持久化 |
| F031 | Context Budget | P0 提供确定性的有界上下文裁剪 |
| F032 | Redaction | 截断、展示、返回模型与持久化前按确定性规则分级处理 |
| F033 | 有限重试 | 模型首 delta 前最多 2 次；只读瞬时错误 1 次 |
| F034 | Finalization | 硬停止前一次最长 60 秒、无工具的收尾调用 |
| F035 | 系统 Terminal | Workspace 中只提供用户点击打开系统 Terminal |
| F036 | 模型流中断恢复 | 首 delta 后中断不重试，保留未完成进度并等待用户输入 |
| F037 | 模型配置错误 | 认证、模型不存在和确定性请求错误直接终止 Run |
| F038 | 模型瞬时故障 | 首 delta 前重试耗尽后暂停，同一 Step 内 Attempt 不重复计步 |
| F039 | 模型协议纠正 | 连续两次无效响应后暂停；合法响应清零连续错误计数 |
| F040 | Durable Intent | 副作用执行前持久化意图，恢复时对账而不重放 |
| F041 | 代理审计 | 只记录域名级元数据，不记录请求内容或解密 TLS |
| F042 | Host 校验 | 精确 host/port、解析 IP 校验、拒绝通配符和跨 host redirect |
| F043 | Hardlink 防护 | 写工具拒绝多链接文件；Writable Shell 前置扫描 fail closed |
| F044 | 文件元数据 | 修改已有文件保留 mode、ACL、xattr；新文件默认 0644 |
| F045 | 敏感规则 | 版本化 `deny`/`redact`/`allow_with_audit`；MVP 不使用模型判断 |
| F046 | 入口扫描 | 用户输入、文件/搜索、Shell/模型输出和持久化入口统一扫描 |
| F047 | 副作用阻断 | 写/Patch/Shell/Artifact 敏感命中整个拒绝，不静默改写后执行 |
| F048 | 扫描失败关闭 | 扫描失败、超时、超限或编码无法安全处理时不释放正文 |
| F049 | 敏感重试暂停 | 模型连续两次提出敏感 ToolCall 后等待用户输入 |

## 2. ToolCall 组合规则

| 类型 | 工具 | 同响应数量 | 审批 |
|---|---|---:|---|
| 只读 | list_files/read_file/read_file_range/search_text | 1..N，按声明顺序串行 | 否 |
| Workspace 副作用 | write_file/apply_patch/delete_file | 必须唯一 | 是 |
| Shell 副作用 | run_shell | 必须唯一 | 是 |
| Eidos 本地副作用 | publish_artifact | 必须唯一 | 否 |

其他规则：

- 模型必须先观察读取结果，再在下一 Step 中提出写入或 Shell。
- 非法组合在执行前整体拒绝，一个 ToolCall 都不执行。
- 合法只读批次中单项失败不阻断后续项；取消、总超时和安全异常除外。
- 明确删除必须优先使用 `delete_file`，但获批 Shell 产生的间接删除不属于沙箱禁止项。

## 3. 时间与重试限制

| 项目 | 默认 | 上限/规则 |
|---|---:|---|
| Segment Steps | 20 | 固定 |
| Segment 有效时间 | 30 分钟 | 固定 |
| Run 总 Steps | 80 | 硬上限 |
| Run 总有效时间 | 120 分钟 | 硬上限 |
| Finalization Call | 60 秒 | 无工具 |
| run_shell | 120 秒 | 单次最大 600 秒 |
| 文件/搜索工具 | 10–15 秒 | 工具固定上限 |
| 单文件敏感扫描 | 32 MiB | 超限不返回正文 |
| 单次搜索扫描 | 256 MiB / 15 秒 | 可返回已完成整文件扫描的安全结果 |
| 模型重试 | 2 次 | 仅首个 delta 前的瞬时错误 |
| 只读工具重试 | 1 次 | 仅瞬时错误 |
| 写/Shell 重试 | 0 | 必须先确认事实 |

排队、waiting_approval 和 waiting_user_input 时间不计入有效执行时间。

## 4. P1/P2

| 优先级 | 功能 |
|---|---|
| P1 | 内嵌 Workspace Terminal |
| P1 | Edit then Approve |
| P1 | 文件写入前快照与文件级恢复 |
| P1 | 智能摘要式上下文 compaction |
| P1 | Timeout 与预算配置 UI |
| P1 | Regex Search |
| P1 | macOS Keychain 密钥存储 |
| P1 | Artifact/Session 数据管理与自动清理策略 |
| P1 | 敏感规则升级后的历史数据重扫、Artifact 隔离与安全迁移 |
| P2 | Windows/Linux 支持 |
| P2 | 后台执行、tray 和通知 |
| P2 | 多 Agent、多执行器和并行工具 |
| P2 | PostgreSQL 与服务化部署 |
