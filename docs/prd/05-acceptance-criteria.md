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
