# Eidos MVP 第二期实施范围

版本：v0.2

状态：🟡 未开工

## 1. 定位与优先级

第二期目标是把第一期的 Developer Preview 闭环升级为**可排队、可暂停、可恢复核验、可审计且不泄露敏感内容**的本地 Runtime 基础。

本清单是第二期唯一的实施范围与完成状态来源。每项必须满足自身验收条件、通过自动化验证，并在 PRD/TDD 的对应契约没有冲突后，才能将 `- [ ]` 改为 `- [x] ✅`。仅完成部分代码、人工演示或仅更新文档均不得打勾。

第一期的 [MVP Lite](mvp-lite.md) 仍是已完成基线。完整目标态 PRD/TDD 中未列出的能力并未取消，只是明确不属于本期。

## 2. 第二期边界

### 本期交付

- 在既有 Electron Main ↔ Python Runtime 的 stdio JSON-RPC 边界内完成实现；Renderer 继续只能通过类型化 Preload 调用 Runtime。
- 对状态存储、队列、恢复、安全扫描和文件工具建立可跨重启验证的事实来源。
- 每个用户可见的状态变化同时提供受限 DTO、持久 Event 和对应 Desktop 展示。

### 本期不交付

- loopback HTTP/SSE、随机端口、Bearer Token，以及 FastAPI、SQLAlchemy、Alembic 迁移。
- 多 Model Profile、Test Connection、Capability Snapshot、Responses Adapter、WebSocket 和传输降级。
- Public Mode、`publish_artifact`、Artifact 生命周期和数据清理。
- managed network proxy、Shell manifest、精确 RSS/fd/fork/allocated-block 监管、原生 Shell guardian。
- 内嵌 Terminal、智能摘要、Keychain、跨平台、多 Agent、后台运行。

> ponytail: 现有 stdio 单 Desktop 边界已覆盖第二期用户路径；只有出现多客户端或远程 Runtime 需求时，才评估 HTTP/SSE 迁移。

Pydantic v2 是本期唯一新增的 Runtime 依赖，用于 JSON-RPC DTO、持久化事实投影和工具契约的闭合校验；它不引入 HTTP 框架，也不代替状态机、Tool Schema Dialect、敏感扫描或文件系统授权。

## 3. 实施顺序与检查点

```text
P2-00 基线与契约
  -> P2-01 存储安全启动
  -> P2-02 Event 与幂等
  -> P2-03 FIFO 与状态机
  -> P2-04 暂停、继续与副作用对账
  -> P2-05 敏感内容边界
  -> P2-06 文件工具完整契约
  -> P2-07 Desktop 收口与第二期验收
```

- **检查点 A（P2-01 至 P2-03）**：已有数据库可安全启动；多个 Run 可 FIFO 排队；重启后状态、队列和 Timeline 不自相矛盾。
- **检查点 B（P2-04 至 P2-06）**：副作用不自动重放；敏感原文不进入模型、UI、Event 或 SQLite；文件工具在全部边界上 fail closed。
- **发布检查点（P2-07）**：所有本清单条目均为 `- [x]`，完整回归通过，文档状态与实现一致。

## 4. 契约追溯

| 清单分组 | PRD 主要落点 | TDD 主要落点 |
|---|---|---|
| P2-00 | F129、F136、F137 | 01 架构、05 API/Events/Storage、07 测试与里程碑 |
| P2-01 | F003、F127、F129、F132、F139 | 01 架构、05 API/Events/Storage |
| P2-02 | F030、F135、F136、F137、F138 | 05 API/Events/Storage、06 Desktop、07 测试与里程碑 |
| P2-03 | F007–F009、F025、F027、F034、F133 | 02 Runtime 状态机、05 API/Events/Storage |
| P2-04 | F023、F025、F036、F040、F120、F134 | 02 Runtime 状态机、03 工具/审批/沙箱、05 API/Events/Storage |
| P2-05 | F045–F049、F057、F058 | 03 工具/审批/沙箱、04 模型/上下文/流、05 API/Events/Storage |
| P2-06 | F013–F015、F018、F050–F053、F097–F126 | 03 工具/审批/沙箱、04 模型/上下文/流、07 测试与里程碑 |
| P2-07 | F029、F130、F133、F136 | 05 API/Events/Storage、06 Desktop、07 测试与里程碑 |

## 5. 详细实施清单

### P2-00：冻结一期基线与第二期契约

- [ ] P2-00-01 记录当前 MVP Lite 版本、协议 fixture、SQLite schema 和自动化测试结果，作为第二期回归基线。
- [ ] P2-00-02 保持 JSON-RPC v1 可解析；新增字段、状态和 Event 只通过显式版本或兼容 DTO 引入，未知字段安全拒绝。
- [ ] P2-00-03 冻结第二期新增 Run、Segment、Step、Approval、Event 和 operation 的状态枚举及合法流转表。
- [ ] P2-00-04 为每个第二期清单项标注其 PRD 功能编号、TDD 小节和自动化测试位置。
- [ ] P2-00-05 引入 Pydantic v2，并以 `extra="forbid"`、strict 验证和显式 JSON 导出统一校验 ApprovalDecision、Run、Item、ToolResult、Event 与 JSON-RPC request/response；不得把 SQLite row、异常对象或自由 map 直接投影给 Renderer。
- [ ] P2-00-06 将现有 `RuntimeLoop` 演进为 `RuntimeEngine`：保留 `run(run_id, cancel)` 这一外部接口，内部按状态机、模型运行、工具调度、审批、事件和错误映射分责；禁止为拆文件新增只转发一次的浅层接口。

验收：旧 Session 可读取；现有协议向量、Runtime 测试、Desktop 测试和构建全部通过。

### P2-01：状态存储安全启动与前向迁移

- [ ] P2-01-01 启动前验证 `~/.eidos`、数据库和锁文件的 owner、普通文件类型、非 symlink 与权限；失败时不打开业务 Runtime。
- [ ] P2-01-02 取得状态目录全生命周期独占 OS lock；第二个 sidecar 不得迁移、调度或执行，并返回安全 health 状态。
- [ ] P2-01-03 启用并验证 SQLite `foreign_keys`、WAL 和 `busy_timeout`；所有业务写入使用显式事务。
- [ ] P2-01-04 引入前向 schema revision；空库初始化当前 revision，未知、缺失或高于当前 revision 的数据库只进入 health-only。
- [ ] P2-01-05 已有 revision 迁移前创建同文件系统的受限权限备份、hash 与 manifest；备份或迁移失败不得覆盖源库。
- [ ] P2-01-06 启动迁移后重新打开数据库并执行 integrity/foreign-key 检查；失败保持 health-only 且保留原库与备份。
- [ ] P2-01-07 创建并校验非 sparse `emergency.reserve`；磁盘空间、I/O 或损坏故障时停止业务写入，恢复前完成 WAL/完整性复检。

验收：空库、旧库、未知 revision、迁移失败、第二 sidecar、满盘/损坏模拟均有隔离自动化测试；任何失败都不产生业务执行。

### P2-02：Event、时间与操作幂等基础

- [ ] P2-02-01 引入统一 UTC Unix 毫秒时间与 monotonic duration；持久化时间使用 safe integer，不以墙钟计算运行预算。
- [ ] P2-02-02 定义版本化 Event envelope、事件类型和闭合 payload registry；未知可忽略事件与未知不兼容版本的处理规则可测试。
- [ ] P2-02-03 将 Run、Approval、Segment、ToolCall 和恢复状态的事实变更与对应 Event 放入同一 SQLite 事务。
- [ ] P2-02-04 为所有 Renderer 发起的写请求增加 operation ID、scope 与请求 hash；同 ID/同请求返回原结果，不重复写状态或 Event。
- [ ] P2-02-05 同 operation ID/不同请求安全冲突；敏感拒绝和鉴权/校验失败不占用 operation ID。
- [ ] P2-02-06 为 Session/Run 读取提供 snapshot 与 `through_event_id` 水位；Desktop 从水位续接时不漏状态，也不将旧 Approval 视为 pending。
- [ ] P2-02-07 使用 keyset cursor 与创建序号分页；cursor 必须绑定 scope、排序和 high-water，禁止 offset 分页。

验收：重放、并发、事务回滚、Event 断档、未知 Event 版本、分页新增数据和 snapshot 续接均有自动化验证。

### P2-03：持久 FIFO 与 Segment 状态机

- [ ] P2-03-01 将第二个 Run 从 `RUN_ALREADY_ACTIVE` 改为持久 `queued`；全局只允许一个 Run 处于模型/工具执行槽。
- [ ] P2-03-02 以不可抢占 FIFO `enqueued_at` 调度；取消排队 Run 不影响其他 Run 顺序，重启后顺序保持。
- [ ] P2-03-03 增加 Execution Segment、Step 与 Attempt 的最小持久事实；历史 Run 不因新状态写入丢失一期 Item/ToolCall 记录。
- [ ] P2-03-04 强制每个 Segment 最多 20 Steps/30 分钟，整个 Run 最多 80 Steps/120 分钟；等待审批和等待用户输入不消耗有效时间。
- [ ] P2-03-05 到 Run 硬上限时执行一次最长 60 秒、无工具权限的 Finalization；成功或失败后均进入带结构化原因的 `stopped`。
- [ ] P2-03-06 所有 Run/Segment/Step/Approval 合法与非法流转由 Runtime 单点校验；取消、批准与拒绝竞态仅一个提交成功。
- [ ] P2-03-07 Runtime 为每个状态返回闭合 `allowed_actions`；Desktop 不根据本地猜测开放继续、批准、拒绝或取消按钮。
- [ ] P2-03-08 引入持久 Run 状态之外的执行态 `RuntimeState`；调度等待不进入 Loop，Loop 仅在 `thinking`、`tool_executing`、`waiting_approval`、`waiting_user_input`、`finalizing` 与终态间迁移，并由单一 StateMachine 校验和记录迁移原因。

验收：两个以上 Run、重启、取消、超时、Step 上限、Finalization 和 Approval 并发的状态与 Event 均可确定复现。

### P2-04：暂停、继续、Durable Intent 与事实确认

- [ ] P2-04-01 Approval Reject 计数持久化；连续两次 Reject 后 Run 进入 `waiting_user_input`，获批状态变更或新 Segment 才能清零。
- [ ] P2-04-02 用户补充输入创建同一 Run 的新 Segment 并重新入队；不回放旧模型请求、ToolCall 或已执行副作用。
- [ ] P2-04-03 模型首 delta 后流中断保留已扫描的 incomplete progress，丢弃未完成 ToolCall，并进入 `waiting_user_input`。
- [ ] P2-04-04 每个文件写入和 Shell 在实际执行前于同一事务持久化 Durable Intent、参数 hash、前置条件和审批事实。
- [ ] P2-04-05 进程退出或结果提交失败后只执行对账：不得重发模型请求、不得重跑文件提交或 Shell。
- [ ] P2-04-06 写入或 Shell 失败、被中断或结果不确定时创建 reconciliation episode；后续副作用一律拒绝。
- [ ] P2-04-07 仅该 episode 之后成功完成的只读观察可以清除屏障；旧观察、无工具 Step、失败或中断都不能清除。
- [ ] P2-04-08 Desktop 明示暂停原因、`side_effects_may_exist` 和必须核验的下一步，不展示内部错误或原始系统详情。

验收：Reject 两次、崩溃窗口、结果提交失败、Shell 已启动中断、只读核验成功/失败均有自动化恢复测试，且零副作用自动重放。

### P2-05：版本化敏感内容边界

- [ ] P2-05-01 将规则集作为只读应用资源加载，包含版本、rule ID、rule version 与确定性优先级；启动自检失败时内容路径 fail closed。
- [ ] P2-05-02 实现 `deny`、`redact`、`allow_with_audit` 与固定占位符 `[REDACTED:<rule_id>]`；不保留原值长度、摘要、哈希或关联标识。
- [ ] P2-05-03 在创建 Run、用户补充和 Approval feedback 前扫描；命中拒绝时零持久化、零模型请求、零 operation 占用。
- [ ] P2-05-04 在模型文本流、ToolCall 参数、ToolResult、文件读取/搜索、Shell 输出、Event、SQLite 与日志入口先扫描、后截断或展示。
- [ ] P2-05-05 敏感文件名或文件正文命中时文件工具不返回正文；写/Patch/Shell 参数命中时零 Approval、零 intent、零执行。
- [ ] P2-05-06 支持跨 chunk 输出匹配；扫描超时、容量超限、编码无法安全处理或规则异常时不释放未确认正文。
- [ ] P2-05-07 模型连续两次生成敏感 ToolCall 时进入 `waiting_user_input`，不计入协议错误；所有安全错误只返回闭合安全 code/data。

验收：固定规则向量、跨 chunk、所有入口、重启后历史读取及 SQLite/Event/日志原文断言均通过。

### P2-06：文件工具与 ToolResult 完整契约

- [ ] P2-06-01 固化 `tool_contract_version`、递归闭合 Tool schema 与 effective arguments；未知字段、非法组合或敏感参数零 ToolCall/Approval/执行。
- [ ] P2-06-01a 将 Tool Registry 的注册项统一为 Pydantic `ToolSpec`，固定 name、description、side effect 类别、审批要求、timeout、input schema 与 result schema；Registry 启动即校验，工具实现只能接收已校验的 effective arguments。
- [ ] P2-06-02 为每个 ToolCall 生成唯一版本化 canonical ToolResult；base result 不可变，模型 Context 只能使用有界 projection，不能改写事实。
- [ ] P2-06-03 `read_file` 实现 <=256 KiB 完整、256 KiB..2 MiB head+tail、>2 MiB 拒绝；严格 UTF-8/可选 BOM、二进制和其他编码给出分类安全错误。
- [ ] P2-06-04 新增 `read_file_range`：1-based 闭区间、2,000 行/256 KiB 上限、永不返回半行，并给出可继续的 next line。
- [ ] P2-06-05 `search_text` 实现单行 literal、ASCII 不区分大小写、稳定 path/line/column 排序、受控 preview 与明确截断原因。
- [ ] P2-06-06 `list_files` 实现固定深度/条目上限、稳定排序、敏感隐藏、固定排除与不跟随 symlink；不解析 `.gitignore`。
- [ ] P2-06-07 新增 `delete_file`：只允许 active root 内单个普通 UTF-8 文件，生成完整 Diff、审批、版本复检、Durable Intent 和崩溃后只对账。
- [ ] P2-06-08 所有读取工具在单文件变化时丢弃该文件结果并有界重试；不得声称跨文件 Workspace 快照。
- [ ] P2-06-09 所有 ToolResult 的 outcome/code/data、容量、排序、整数与 Unicode 序列化由固定 Python/TypeScript 向量共同验证。

验收：容量、编码、竞态、symlink/hardlink、删除、Tool schema、canonical 结果与跨语言向量回归全部通过。

### P2-07：Desktop 收口与第二期发布验收

- [ ] P2-07-01 Renderer 合同、Preload 和 Main 对新增 DTO/状态执行闭合校验；未知字段或过期响应不进入 UI 状态。
- [ ] P2-07-02 Session Sidebar 与 Execution Feed 展示 queued、running、waiting_approval、waiting_user_input、stopped、interrupted 和 storage health 状态。
- [ ] P2-07-03 Feed 展示 Timeline、拒绝/继续入口、对账屏障和安全摘要；不显示 raw reasoning、敏感原文、API Key、内部路径或原始 OS/Provider 错误。
- [ ] P2-07-04 通过 snapshot + Event 水位恢复页面；重载与延迟通知不能覆盖更新的权威状态。
- [ ] P2-07-05 在隔离临时根执行 Runtime、Desktop、协议 fixture、迁移、重启、敏感扫描和 macOS Seatbelt 回归。
- [ ] P2-07-06 更新 PRD/TDD 的实施状态、M2 进度和本清单复选框；确认未把未完成的完整目标态能力标记为已实现。

验收：`pnpm test`、第二期新增存储/状态机/安全回归及 macOS 实机 smoke test 全部通过；本清单无未完成条目后才可标记第二期完成。

## 6. 后续阶段入口

以下能力依赖第二期完成，但不应提前实现：

- Model Profile、Capability Snapshot、双 wire Adapter、Attempt 审计、Context Builder 和重试/传输降级。
- Public Mode、Artifact、不可变快照和数据管理。
- managed proxy、Shell manifest/资源监控/guardian，以及传输层 HTTP/SSE 演进。

当 P2-07 全部完成后，再从上述能力中选择一条完整纵向链路作为第三期范围，避免并行引入多个尚无持久化与安全基础的子系统。
