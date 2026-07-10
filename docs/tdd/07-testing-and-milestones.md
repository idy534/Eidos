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
- `.git` read-only carve-out。
- 脱敏 exact API key 与 pattern rule；数据库不含原文。

### 2.2 ToolCall 批次

- N 个只读工具合法并保持顺序。
- 只读单项失败继续执行后续。
- read + apply、write + shell、两个 shell 整批零执行。
- publish_artifact 独占且不创建 Approval。
- 非法 model output 生成 model_protocol_error。

### 2.3 文件工具

- write expected_absent。
- apply base_sha256。
- delete 路径、类型和 hash。
- 审批期间文件变化导致 approval invalidated/file_version_conflict。
- 临时文件 + 原子 replace；取消不能截断 commit。
- 每个 ToolCall 拒绝多个文件、目录、glob 和递归删除。

### 2.4 状态机

- Run/Segment/Step/ToolCall/Approval 合法和非法流转。
- approve/reject/cancel 并发只有一个成功。
- Reject 计数增加和两种清零条件。
- Segment 20 Steps/30 分钟暂停。
- Run 80 Steps/120 分钟 Finalization -> stopped。
- Finalization 超时/失败生成降级摘要。
- reconciliation_required 屏障拒绝副作用，读取后解除。

### 2.5 重试与上下文

- 模型首 delta 前瞬时错误最多 2 次。
- 首 delta 后不透明重试被禁止。
- 只读瞬时错误最多 1 次；确定错误不重试。
- 写/Shell/Artifact 零自动重试。
- 上下文裁剪顺序、不可裁剪项和 token 硬上限。

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
- 批准域名通过，未批准/redirect 新域名失败。
- `local_network=true` 仅本次调用允许 loopback。
- sandbox-exec 缺失/策略失败时 Shell unavailable，无回退。

## 4. Runtime 集成测试

- Public 无工具回复。
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
- SSE after_event_id 回放和去重。

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

## 6. 持久化测试

- `foreign_keys=ON`、WAL、busy_timeout 生效。
- enum CHECK、mode/workspace CHECK、unique idempotency key。
- Alembic 从空库升级和重复启动。
- 状态与 Event 原子提交。
- FIFO enqueued_at 重启保持。
- Artifact snapshot/source hash 和版本唯一性。
- config 权限 0700/0600；权限过宽拒绝密钥。
- SQLite、日志和 Event 中不存在测试 API Key 原文。

## 7. 风险优先里程碑

### M0：macOS 安全可行性

- Seatbelt 静态策略模板和参数绑定。
- System Runtime read-only、active root write、外部 deny。
- 敏感和 `.git` carve-out。
- managed proxy、域名策略、localhost 独立权限。
- fail-closed 自检与集成测试。

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

- Model Profile/Gateway 流。
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
- Q1-Q40 决策不得出现相反规则。
- Q41 完成后补齐模型推理内容的技术与验收约束。
- 实现开始前冻结 v0.4 API schema、状态 enum 和 Tool schema。

