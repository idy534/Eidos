# Agent Team P0 与 P1 详细设计

> 文档分类：设计与实施记录。本文细化 [Agent Team 调研与设计](agent-team-research-and-design.md) 中的 P0 和 P1。当前行为以 [当前说明](../README.md#当前说明)、源码和测试为准。

日期：2026-11-18。

状态：详细设计，尚未实施。文中的源码位置已在 2026-11-18 的 `feat/windows-support` 分支核对；标为“实施前核对”的项目还没有验证。

## 1. 已确认的决策

用户于 2026-11-18 确认以下决策。

| 决策 | 内容 | 影响 |
| --- | --- | --- |
| 并发计数 | 每个 Team 最多同时执行 8 个成员 Run，计数只作用于该 Team，不是全局配额 | AGENTS.md 第 13 节已同步 |
| 项目范围 | Git 项目和非 Git 项目都支持 | 非 Git 项目在 P1 只能共享工作区，见 4.7 |
| 通信方式 | 公开看板，加成员之间的私聊 | 见 4.5 |
| Lead 自动续跑 | 默认开启 | 见 4.6 |
| 子 Agent 工具 | 由设计按市面产品决定 | 保留，不隐藏，见第 2 节 |
| 常驻团队 | 进入路线图 | 作为 P3，不在本文范围内 |

## 2. 团队模式下的子 Agent

### 2.1 市面产品的做法

| 产品 | 协调者能否派子 Agent | 成员能否派子 Agent | 能否嵌套团队 |
| --- | --- | --- | --- |
| Claude Code Agent Teams | 能。开启团队后，带名字的调用创建成员，不带名字的调用仍是子 Agent | 能。进程内成员的子 Agent 只能在前台运行 | 不能，成员不能再创建成员 |
| Antigravity | 子 Agent 最多嵌套 10 层；`/swarm` 文档没有说明 | 文档没有说明 | 文档没有说明 |
| DeepSeek Super-agent | Router 不拆解任务 | Worker 可以派子 Agent，也可以自成团队 | 原型允许 Worker 自成团队 |
| Codex Multi-agent V2 | 能，第三方指南称默认深度 3 | 没有团队概念 | 没有团队概念 |

有明确资料的产品都保留了子 Agent，并禁止成员再建团队。

### 2.2 Eidos 规则

- **不隐藏**：Lead 和成员都保留 `spawn_agent` 一族工具。两者语义不同：
  - 成员持久存在、有名字、参与看板和私聊，用户可以直接指挥。
  - 子 Agent 是一次性的，只向调用者汇报，属于调用者的 Run。
- **不嵌套团队**：成员不能添加或移除成员，也不能调用 `team_propose`。
- **子 Agent 仍是一层**：子 Agent 不能再派生，不能使用任何团队工具，也看不到看板。
- **计数分开**：子 Agent 按其父 Run 计数，每个父 Run 最多同时 8 个。这部分不占用 Team 的 8 个成员名额。
- **生命周期**：成员的 Run 结束时沿用现有规则，等待必需的子 Agent，取消可选的子 Agent。
- **工作区**：成员的子 Agent 选择 `shared` 时，与成员共用工作区。如果成员本身在 Worktree 中，子 Agent 也在这个 Worktree 中。选择 `worktree` 时，以成员工作区的快照为基准新建 Worktree。
- **工具描述写清边界**：需要长期并行、需要与其他成员交流或需要用户直接指挥的工作，使用团队成员；短小、专注、只需汇报结果的调查或验证，使用子 Agent。

## 3. P0：子 Agent 底座

P0 不引入团队概念。它补齐子 Agent 的五项能力，这些能力在 P1 中直接复用。

| 编号 | 能力 | 复用到 P1 的位置 |
| --- | --- | --- |
| P0-1 | `spawn_agent` 可选独立 Worktree | 成员的 `worktree` 模式 |
| P0-2 | 工作区交付（Delivery）与整合（Integration） | 任务提交与 `team_integrate` |
| P0-3 | 用户直接给子 Agent 发消息 | 用户指挥成员 |
| P0-4 | 内置 `verifier` 类型 | 团队 Verifier |
| P0-5 | 子任务用量汇总 | 团队用量 |

P0 不包含跨轮存在、看板、任务板和自动续跑。

### 3.1 `spawn_agent.workspace`

`SpawnAgent` 增加参数：

| 参数 | 取值 | 默认 | 说明 |
| --- | --- | --- | --- |
| `workspace` | `shared` 或 `worktree` | `shared` | 旧调用保持原行为；重复 ToolCall 的幂等比较也包括该字段 |

规则：

- `worktree` 要求父 Session 属于 Git 项目，Local 执行和 Worktree 执行都可以。非 Git 项目返回 `agent_worktree_requires_git`。
- 现有子任务创建会复制父 Session 的执行绑定（[persistence/collaboration.py](../../runtime/eidos_runtime/persistence/collaboration.py) 的 `spawn`）。`worktree` 模式改为绑定新建的 Worktree，`execution_mode` 为 `worktree`。
- 子 Run 按自己的 Worktree 根目录物化权限快照，Seatbelt 的工作区就是这个 Worktree。子任务不获得父工作区的写权限。

创建流程复用 Session 分叉的“独立 Worktree”路径（[session_fork.py](../../runtime/eidos_runtime/application/session_fork.py) 的 `_prepare_worktree`）：

```text
spawn_agent ToolCall → Validate → Resolve Permission → Durable Intent
  → 快照父工作区（snapshots.save 或 save_local，包含未提交的修改）
  → WorktreeLifecycle.prepare(scope=agent_workspace, operationId=spawn ItemId)
  → manager.create_prepared(compensate_on_failure=True)
  → manager.restore_snapshot_state(head, changes)
  → source_snapshot 核对 head 与 changes 一致
  → 同一事务：子 Session、子 Run、agent_delegations（含 worktree_id、base_snapshot_id）、Event/Outbox
```

- **基准**：父工作区的快照，包括未提交的修改。快照 ID 记为 `base_snapshot_id`，供交付时比较。
- **失败处理**：创建失败返回工具错误，不创建子任务。Worktree 的补偿和未完成操作沿用 `WorktreeLifecycleState` 的现有恢复流程（`PREPARED`、`WORKTREE_CREATED`、`CLEANUP_REQUIRED`）。
- **新增生命周期范围**：`WorktreeLifecycleScope` 增加 `agent_workspace`，`operationId` 取 spawn 的 Item ID，重放时得到同一个 Worktree。
- **归属与清理**：Worktree 属于子 Session。父 Session 删除时，沿用现有 Session 删除流程（`find_delete_for_session`）清理。用户也可以单独放弃。保留策略沿用 [Worktree Retention](../architecture/git-worktree.md)。
- **Windows**：需要在 Windows 11 的 NTFS 上验证创建、路径长度和清理。

### 3.2 交付（Delivery）

交付是子任务 Worktree 相对 `base_snapshot_id` 的逐文件变化。

**采集时机**：按需采集，即父任务调用 `review_agent_changes`、`integrate_agent_changes`，或用户在 Desktop 打开变更视图时采集。采集要求子任务没有活动 Run。完成路径上不做采集，失败也不会影响 Run 终态。

**采集内容**：用 `source_snapshot(include_local_changes=True)` 读取当前状态，与基准状态比较，生成文件条目：

| 字段 | 说明 |
| --- | --- |
| `path` | 相对工作区根目录的路径 |
| `kind` | `added`、`modified` 或 `deleted`；重命名记为删除加新增 |
| `baseHash` | 基准内容的 SHA-256；新增文件为空 |
| `newBlob` | 新内容的内容寻址 Blob；删除时为空 |
| `mode`、`binary` | 文件权限位与二进制标记 |

- `.git` 和文件工具的永久拒绝路径不进入交付。
- 交付记录 Worktree 指纹。指纹变化后，交付标记为 `stale`；如果子任务处于空闲状态，整合时会重新采集。
- **限额**：最多 2,000 个文件，新内容合计最多 64 MiB，单文件沿用 [file_limits.py](../../runtime/eidos_runtime/file_limits.py) 的上限。超限时交付标记为 `too_large`，并返回具体原因；用户可以改用 Review 或 Git 手工处理。
- **展示**：Diff 预览按 AGENTS.md 第 19 节使用 `difflib` 生成，作为 Blob 保存。
- **实施前核对**：`GitWorkingTreePatch` 对未跟踪文件和被忽略文件的处理规则，以及快照 anchor 能否直接用作基准。

### 3.3 整合（Integration）

整合有两个入口：父任务的工具 `integrate_agent_changes(agentId, paths?)`，以及用户在 Desktop 中触发的 `agent/integrate`。

前提：子任务没有活动 Run；交付有效，如果已经过期则重新采集。整合目标是父 Session 当前的工作区。

每个文件按乐观并发规则处理：

| 父工作区中该文件的当前内容 | 结果 |
| --- | --- |
| 等于基准 | 写入子任务的版本，状态为 `applied` |
| 已经等于子任务的版本 | 不写入，状态为 `already_applied` |
| 其他内容 | 不写入，状态为 `conflict` |

- 写入沿用文件工具的受控提交：Durable Intent 之后重新核验版本；已有文件先打开并核验 fd，再截断、完整写入、fsync 并验证最终内容；新文件使用排他创建；删除前核验文件身份。路径策略与文件工具相同，受保护的路径会被拒绝。
- 工作区内的写入使用现有 Workspace Permission，不逐次审批。`full_access` 按现有规则处理。
- 多个文件之间不保证原子性。结果逐个文件如实报告，不自动重试，也不回滚。截断后失败的文件记为不确定结果（AGENTS.md 第 14 节）。
- 交付状态更新为 `integrated` 或 `partially_integrated`。冲突文件在 Desktop 中以“基准 / 父工作区 / 子任务”三列展示。P0 不做自动合并；三方文本合并作为后续改进，引入前需要按 AGENTS.md 第 9 节评估成熟实现。
- 放弃：用户通过 `agent/discardWorkspace` 删除子任务的 Worktree，交付标记为 `discarded`。

### 3.4 用户直接给子 Agent 发消息

- Desktop 在子任务详情页提供输入框，调用 `agent/message { parentRunId, agentId, text }`。
- `agent_messages` 增加 `sender_kind`（`agent` 或 `user`）。消息与 Event/Outbox 在同一事务中写入。
- **投递**：
  - 子任务有活动 Run 时，消息在下一次构建上下文时作为“用户指示”送达，只对该子任务具有用户授权含义。
  - 子任务空闲、父 Run 仍然活动时，按 `followup_task` 的语义启动一次子 Run。
  - 父 Run 已经结束时，返回 `agent_parent_inactive`。跨轮继续由 P1 的团队模式解决。
- 父任务通过现有的 Agent 证据段看到“用户直接指示了 X”以及消息预览。
- `run/start` 对子 Session 的拦截（[application/runs.py](../../runtime/eidos_runtime/application/runs.py) 中的 `is_child`）保持不变。用户消息只通过 `agent/message` 进入。

### 3.5 内置 `verifier` 类型

- `AgentRole` 增加 `verifier`。内置职责是：用证据核对其他 Agent 的结论，运行定向检查，审查 Diff 或交付，给出接受或退回及理由。除非父任务明确要求，Verifier 不修改文件。
- `verifier` 和 `explorer` 一样只是任务侧重，不是只读权限模式。
- `verifier` 成为保留名称。已有名为 `verifier` 的自定义定义会被标记为不可用，原因是“与内置类型重名”。Runtime 不改写用户文件，已有 Run 继续使用冻结的定义。
- **实施前核对**：数据库中是否有约束列出了内置角色名。

### 3.6 用量汇总

- `AgentSummary` 增加 `usage` 字段，包括输入、输出、缓存读取的 token 数和有效执行时长，由子 Run 已有的用量记录汇总。AgentPanel 显示每个子任务的用量和合计。
- **实施前核对**：现有 `usage_json` 所在的表，以及按 Run 汇总的口径。

### 3.7 协议、持久化与工具

| 类别 | 变化 |
| --- | --- |
| Schema（v54 → v55） | `agent_delegations` 增加 `workspace_mode`、`worktree_id`、`base_snapshot_id`；`agent_messages` 增加 `sender_kind`；新增 `workspace_deliveries` 表；生命周期范围增加 `agent_workspace` |
| `workspace_deliveries` | `id`、`owner_kind`（P0 为 `agent`，P1 增加 `team_member`）、`owner_id`、`worktree_id`、`base_snapshot_id`、`fingerprint`、`entries_blob`、`diff_blob`、`status`（`ready`、`stale`、`too_large`、`integrated`、`partially_integrated`、`discarded`）、`created_at`、`integrated_at` |
| 工具 | `spawn_agent.workspace`；新增 `review_agent_changes`（只读，返回文件列表、增删行数和 Diff 分页）与 `integrate_agent_changes`（写入父工作区） |
| RPC | `agent/changes`、`agent/integrate`、`agent/discardWorkspace`、`agent/message`，全部注册到 Method Registry |
| 契约 | 由 Python DTO 生成 TypeScript 类型，并同步双端 Fixture |

Migration 覆盖新库、v54 升级、失败回滚和外键检查。Blob GC 保留被交付引用的 Blob。

### 3.8 Desktop

- AgentPanel 的子任务行显示工作区模式和用量。
- 子任务详情页增加输入框和“变更”视图，“变更”视图复用 Review 的 Diff 渲染，提供“整合”和“放弃”两个操作。冲突文件单独列出。
- 布局沿用现有的子任务详情，不增加说明性文字。

### 3.9 P0 验收

| 类别 | 用例 |
| --- | --- |
| Worktree 创建 | Git Local 与 Worktree 两种父 Session；包含未提交修改的基准；非 Git 项目报错；创建中途崩溃后的恢复；补偿清理；同一 spawn 重放得到同一个 Worktree |
| 交付 | 新增、修改、删除和二进制文件；保护路径被排除；超出限额；指纹变化后过期；子任务运行中拒绝采集 |
| 整合 | 三种文件结果；部分冲突；写入中途失败记为不确定结果；与父 Run 同时写入同一文件时，版本核验能发现冲突；用户发起整合 |
| 用户消息 | 活动子任务的下一次上下文收到消息；空闲子任务被启动；父任务已结束时被拒绝；父任务看到通知 |
| verifier | 内置类型可以派生；同名自定义定义被标记为不可用；旧数据兼容 |
| 平台 | 真实 Seatbelt 下子任务只能写自己的 Worktree；Electron smoke（preload 有变化）；Windows NTFS 上的 Worktree |

## 4. P1：Team MVP

### 4.1 范围

包含：

- 团队模式的入口和编制确认。
- 命名成员跨轮持久存在，闲置时不耗资源，有事件时被唤醒。
- 公开看板和成员私聊。
- 任务板，包括依赖、领取、审查和证据。
- 成员工作区：`shared`，Git 项目可选 `worktree`。
- Lead 自动续跑，默认开启。
- 团队面板，用户可以直接指挥成员。
- 每个 Team 最多 8 个成员同时运行。

不包含：

- 团队模板，与目标模式或 Plan 模式叠加，里程碑与关口，Lead 交接，团队级 Grant，成员上下文分叉。这些放到 P2。
- 非 Git 项目的隔离副本，放到 P2 评估。
- 渠道或定时任务触发，放到 P3。

### 4.2 用户流程

1. 用户点击 `+` → 团队，或输入 `/team <目标>`。P1 中团队模式与目标模式、Plan 模式互斥。
2. Lead 的第一个 Run 是普通的用户 Run，可以使用团队工具。Lead 调用 `team_propose` 提出编制后，这个 Run 进入现有的持久 `waiting_input` 状态，与用户输入请求共用同一套机制。
3. Desktop 显示编制确认卡片，列出每个成员的名字、类型、模型、工作区模式和初始任务，以及并发上限和团队的审批模式。用户可以删除成员、修改工作区模式，然后确认或拒绝。
4. 确认后，Runtime 依次为 `worktree` 成员创建 Worktree，然后在一个事务中提交团队、成员、成员 Session、初始任务和唤醒意图，并结束 Lead 的等待。任一 Worktree 创建失败时，整体确认失败并补偿已创建的 Worktree，卡片指出失败的成员，用户可以改用 `shared` 后重试。
5. Lead 继续拆分任务并分派。成员被唤醒后执行，在看板上发消息、私聊、提交任务。
6. 发生事件时，Lead 自动续跑：审查任务、整合变化、向用户汇报。
7. 用户随时可以给任一成员发消息、处理审批、暂停或继续团队、停止所有成员，或者解散团队。

### 4.3 数据模型

Schema 在 P0 的基础上再升一个版本，新增以下表。所有业务状态变化都与 Event/Outbox 在同一事务中提交。

| 表 | 关键列 | 约束 |
| --- | --- | --- |
| `teams` | `id`、`lead_session_id`、`status`、`approval_mode`、`auto_wake_lead`、`catalog_ref`、`version`、`created_at`、`updated_at` | 每个 Session 同时最多一个未解散的团队（部分唯一索引）；`status` 取 `active`、`paused`、`dissolving`、`dissolved` |
| `team_members` | `id`、`team_id`、`name`、`role`、`definition_json`、`model_profile_json`、`session_id`、`workspace_mode`、`worktree_id`、`base_snapshot_id`、`status`、`created_at`、`removed_at` | 同一团队内 `name` 唯一；`session_id` 唯一；`status` 取 `active` 或 `removed` |
| `team_tasks` | `id`、`team_id`、`key`、`title`、`description`、`acceptance`、`owner_member_id`、`reviewer`、`status`、`review_required`、`paths_json`、`evidence_json`、`delivery_id`、`version`、`created_by`、`created_at`、`updated_at` | 同一团队内 `key` 唯一；`status` 取 `open`、`in_progress`、`in_review`、`done`、`canceled` |
| `team_task_deps` | `task_id`、`depends_on_task_id` | 联合主键；创建时拒绝循环依赖 |
| `team_messages` | `id`、`team_id`、`seq`、`channel`、`author_kind`、`author_member_id`、`recipients_json`、`to_all`、`task_id`、`content`、`source_item_id`、`created_at` | `(team_id, seq)` 唯一；`source_item_id` 唯一，用于幂等；`channel` 取 `board` 或 `direct` |
| `team_message_receipts` | `message_id`、`recipient`、`run_id`、`delivered_at` | 联合主键；做法与 `agent_message_receipts` 一致 |
| `team_wakes` | `id`、`team_id`、`target`、`reason`、`status`、`run_id`、`created_at` | 每个 `target` 最多一条 `pending`（部分唯一索引），做法与 `goal_continuations` 一致；`status` 取 `pending`、`accepted`、`obsolete` |
| `team_runs` | `run_id`、`team_id`、`member_id`（Lead 为空）、`wake_id`、`origin` | 标记自动 Run 的来源；`origin` 取 `wake` 或 `user_message` |

- 成员的运行状态（空闲、运行、等待审批）由 `runs` 表推导，不另行保存。
- 成员的定义从 Lead Run 冻结的 Agent 目录中解析。模型使用定义中的模型，没有定义时继承 Lead Run 的模型快照。
- 成员 Session 不出现在侧栏，只能从团队面板打开，标题为成员名字。
- `agent_delegations` 继续只服务子 Agent。成员的交付复用 `workspace_deliveries`，`owner_kind` 为 `team_member`。

### 4.4 状态机

**Team**

| 从 | 到 | 触发 |
| --- | --- | --- |
| — | `active` | 用户确认编制 |
| `active` | `paused` | 用户暂停 |
| `paused` | `active` | 用户继续 |
| `active` 或 `paused` | `dissolving` | 用户解散，或删除 Lead Session |
| `dissolving` | `dissolved` | 全部成员 Run 达到终态并且资源静止 |

**Task**

| 从 | 到 | 操作者 | 条件 |
| --- | --- | --- | --- |
| `open` | `in_progress` | 负责人领取，或 Lead 指派 | 依赖全部为 `done`；`version` 一致；负责人为活动成员 |
| `in_progress` | `in_review` | 负责人 | `review_required` 为真时必须附带证据；`worktree` 成员同时采集交付 |
| `in_progress` | `done` | 负责人 | 只限 `review_required` 为假的任务 |
| `in_review` | `done` | `reviewer`（Lead 或指定的 Verifier 成员） | 接受 |
| `in_review` | `in_progress` | `reviewer` | 退回，必须附带原因 |
| `in_progress` 或 `in_review` | `open` | Lead 或用户 | 释放；负责人被移除时自动释放 |
| 任一非终态 | `canceled` | Lead 或用户 | — |

- 任务被阻塞是由依赖推导出的状态，不单独存储。
- 每次迁移使用条件 UPDATE，在同一事务中写入系统消息、唤醒意图和 Event/Outbox。
- 迁移前检查发起者 Run 的终态：已经取消的成员 Run 产生的迟到结果不能推进任务。
- 任务状态只是协调状态。“已完成”的证据是被引用的 Item、ToolCall 结果或交付。

**Delivery** 沿用 P0 的状态。任务接受和变化整合是两件事：Lead 的团队快照会单独列出“已接受但未整合”的交付。

### 4.5 看板与私聊

| 类型 | 写入者 | 进入谁的上下文 | 谁能通过 `team_read` 读取 | 用户 |
| --- | --- | --- | --- | --- |
| 看板 | Lead、成员 | 收件人；`all` 表示全部活动成员和 Lead | 全部成员和 Lead | 全部可见 |
| 私聊 | Lead、成员 | 唯一收件人 | 收发双方和 Lead | 全部可见 |
| 用户消息 | 用户 | 收件人 | 全部成员和 Lead | 全部可见 |
| 系统消息 | Runtime | 按事件确定收件人 | 全部成员和 Lead | 全部可见 |

关于私聊的取舍：

- 私聊的“私”是指不推送给第三方，其他成员也不能读取。
- 私聊对用户始终可见，这符合 Eidos 不对用户隐藏 Agent 行为的原则。
- Lead 负责集成和最终汇报，所以可以按需读取私聊，但私聊不会推送进 Lead 的上下文。
- Claude Code 文档没有说明 Lead 能否看到私聊；Antigravity 子 Agent 之间可以互相读取对话记录。

寻址与投递：

- 地址包括成员名字、`lead`、`all` 和 `user`。`all` 和 `user` 只能用于看板。私聊必须恰好有一个收件人。
- 保留名称：`lead`、`all`、`user`、`parent`、`system`、`team`。
- 每次构建上下文，最多投递 16 条未投递的消息，每条预览最多 2,000 字符；完整内容通过 `team_read` 按 ID 读取。这些做法与现有子 Agent 消息一致。
- 回执在上下文快照提交时写入。
- 单条消息最多 64 KiB UTF-8。
- 所有消息都是数据，不是授权。用户消息只对被寻址的成员具有用户授权含义。

### 4.6 唤醒与调度

**唤醒意图**

| 目标 | 原因 |
| --- | --- |
| 成员 | 收到看板消息、私聊或用户消息；被指派任务；负责的任务解除阻塞或被退回 |
| Lead（`auto_wake_lead` 为真） | 收到发给 `lead` 的消息；有任务进入 `in_review` 且由 Lead 审查；成员空闲且没有进行中的任务；成员 Run 失败或中断；所有任务都已结束 |

- 每个目标最多一条 `pending` 意图。多个事件合并为一次唤醒，触发原因累积记录。
- 发往 `user` 的消息不唤醒任何 Agent，只在界面上提醒用户。

**接纳**

接纳方式沿用目标续跑的 `ready_continuations`。接纳条件：团队处于 `active`；目标 Session 没有活动或排队的 Run；成员没有被移除。接纳时在同一事务中创建排队 Run，输入为系统生成的续跑说明，同时写入 `team_runs`（`origin=wake`），把意图标记为 `accepted`，并写入 Event/Outbox。

- Session 中已经有排队的用户 Run 时，意图标记为 `obsolete`，由用户的 Run 读取这些消息。
- 自动 Run 在 Feed 中标注为“团队续跑”，不提供“重新回答”和“编辑并重新发送”。

**运行中到达的消息**

现有的完成复核会检查待处理输入和子任务结果，并为迟到的内容触发下一次上下文构建。P1 把“本 Session 还有未投递的团队消息”加入这项检查，与迟到的子任务结果走同一路径。

**调度条件**

现有的选取排队 Run 的查询（[runs.py](../../runtime/eidos_runtime/db/repositories/runs.py)）增加一个条件：成员 Session 的排队 Run 只在两种情况下可执行——团队处于 `active` 且团队中 `running` 或 `finalizing` 的成员 Run 少于 8 个；或者该 Run 来自用户消息（`origin=user_message`），这种 Run 在 `paused` 状态下也可以执行，但仍受 8 个的限制。

- 等待审批或输入的成员不占名额。Lead 的 Run 不计入。
- 成员派生的子 Agent 按原有条件以父 Run 计数。
- 这项限制只作用于该团队，不是全局配额。

**暂停**

暂停后不再接纳唤醒意图，自动续跑产生的排队 Run 暂不执行，已经在运行的 Run 继续执行。继续团队后恢复正常。

**往返提示**

如果两个成员连续互相唤醒超过 6 次，且期间没有任何任务状态变化，Runtime 向 Lead 和用户各发一条系统提示。系统不自动暂停或结束任何 Run。

### 4.7 工作区

**Git 项目**

- 每个成员可以选择 `shared` 或 `worktree`。`worktree` 成员通过 P0-1 以 Lead 工作区的快照创建 Worktree，快照包含未提交的修改。
- 编制确认卡片上，`explorer` 和 `verifier` 默认使用 `shared`。有两个及以上写入成员时，写入成员默认使用 `worktree`。用户可以修改。
- `worktree` 成员提交任务时，Runtime 采集交付并关联到任务。Lead 通过 `team_integrate` 整合，用户也可以在团队面板中整合，规则同 P0-2。
- 移除仍有未整合交付或未采集变化的 `worktree` 成员时，工具返回 `member_has_unintegrated_changes`。Lead 需要先整合，或者显式传入 `discardChanges=true`。用户在 Desktop 中移除时需要二次确认。

**非 Git 项目**

- P1 只支持 `shared`。编制确认卡片会说明全部成员共用同一份文件。
- 任务的 `paths` 只是声明，不是锁，因为 AGENTS.md 第 13 节禁止跨 Run 的共享工作区副作用门。冲突由现有的文件版本核验发现，工具把冲突作为错误返回给成员，由成员重新读取后处理。
- 对于文档和调研类任务，建议成员通过消息或最终回答交付结果，或者写入各自负责的文件，由 Lead 汇总。
- 隔离副本放到 P2 评估：macOS APFS 可以用 `clonefile` 低成本复制；Windows NTFS 没有对应能力，需要有上限的复制。

### 4.8 权限与审批

- **团队审批模式**：编制确认时取 Lead Session 当前的模式，并在卡片上展示。`full_access` 使用 danger 样式，需要用户显式确认。之后只有用户可以在团队面板中修改，修改只影响之后新建的成员 Run。
- **成员继承**：成员 Run 使用团队的审批模式，扩展快照和个性化设置在成员创建时从 Lead 冻结。
- **Grant**：成员不继承 Lead 的 Run Grant，需要时通过自己的审批获得。
- **审批界面**：成员的工具审批沿用现有界面，附带成员身份。团队面板汇总审批队列，并提供“下一个等待审批”的入口。Lead 不能代替用户批准。`auto_review` 照常评估每个请求，高危请求仍然需要人工确认。
- **成员不可用的工具**：`request_user_input`、`request_permissions`、`write_plan`、`update_goal`。成员需要用户决定时，在看板上发给 `user`。
- 模型、成员消息和模板都不能切换审批模式。

### 4.9 工具

**能力矩阵**

| 工具 | Lead | 成员 | 子 Agent |
| --- | --- | --- | --- |
| `team_propose` | 仅在团队建立前 | — | — |
| `team_add_member`、`team_remove_member` | ✓ | — | — |
| `team_task` | 全部操作 | `create`、`claim`、`update`、`submit`、`release`；被指定为审查者时可以 `accept`、`reject` | — |
| `team_post` | ✓ | ✓ | — |
| `team_read` | ✓ | ✓（私聊只能读自己参与的） | — |
| `team_wait` | ✓ | — | — |
| `team_integrate` | ✓ | — | — |
| `spawn_agent` 一族 | ✓ | ✓ | 沿用现有子任务范围 |

成员没有 `team_wait`：成员结束当前 Run 后变为空闲，有事件时会被唤醒，不需要占着 Worker 等待。

**参数**

| 工具 | 参数 |
| --- | --- |
| `team_propose` | `summary`（不超过 2,000 字符）；`members[]`（1–16 个：`name`、`role`、`brief`、`workspace`）；`tasks[]`（可选，最多 50 个：`key`、`title`、`description`、`acceptance`、`owner`、`dependsOn`、`reviewRequired`、`reviewer`、`paths`） |
| `team_add_member` | `name`、`role`、`brief`（不超过 64 KiB）、`workspace` |
| `team_remove_member` | `name`、`reason`、`discardChanges`（默认 `false`） |
| `team_task` | `op`（`create`、`update`、`assign`、`claim`、`submit`、`accept`、`reject`、`release`、`cancel`）、`key`、`version`（领取和迁移时必填）、该操作所需的字段，以及 `evidence[]`（`itemId` 或 `toolCallId`）、`note` |
| `team_post` | `to[]`、`channel`（`board` 或 `direct`）、`text`、`task`（可选） |
| `team_read` | `kind`（`messages`、`message`、`tasks`、`task`、`member`）、`id`、`cursor`、`filter`（成员、任务、频道） |
| `team_wait` | `members[]`、`tasks[]`、`mode`（`any` 或 `all`）、`timeoutMs`（1,000–300,000） |
| `team_integrate` | `task` 或 `member`（二选一）、`paths[]`（可选） |

- 有副作用的团队工具，副作用类型都是 `eidos_state`，走现有 ToolCall 流程和 Durable Intent，并按 Item ID 幂等重放。
- `team_integrate` 写入 Lead 的工作区，副作用类型为文件写入，规则同 P0-2。
- `team_wait` 复用 `wait_agents` 的持久等待和持有 Worker 的语义。发给 Lead 的消息、用户输入和超时都会结束等待。

**稳定错误码**

`team_not_active`、`team_member_limit`、`team_member_name_invalid`、`team_member_name_exists`、`team_member_not_found`、`team_recipient_not_found`、`team_direct_single_recipient`、`team_task_not_found`、`team_task_version_conflict`、`team_task_blocked`、`team_task_cycle`、`team_task_forbidden`、`team_evidence_required`、`member_has_unintegrated_changes`、`delivery_stale`、`delivery_too_large`、`agent_worktree_requires_git`、`team_mode_conflict`。

### 4.10 上下文

**成员上下文**按顺序包括：

1. 现有的系统指令、权限说明，以及角色与定义快照。
2. 团队段：团队目标摘要；你的名字和角色；名册（名字、角色、状态、工作区）；你负责的任务卡片；未分配的可领取任务（有界）；地址规则。
3. 未投递的消息（有界）。
4. 自己的历史，照常压缩。

**Lead 上下文**在普通上下文之外增加团队快照：各状态的任务数；等待 Lead 审查的任务；已接受但未整合的交付；发给 `user` 但还没有答复的问题；最近的用户指示（每个成员一条）；发给 `lead` 的未投递消息；成员的最终结果（沿用子 Agent 证据机制）。

- 团队段和消息段放在请求尾部，保持 Provider 前缀缓存可以复用（现有 ContextBuilder 原则）。团队段最多约 8K token，超出部分提示用 `team_read` 分页读取。
- 压缩时保留团队 ID、自己负责的任务、未决定的事项和集成状态（AGENTS.md 第 22 节）。
- 成员上下文不复制 Lead 的对话。

### 4.11 生命周期与恢复

| 场景 | 行为 |
| --- | --- |
| 停止 Lead 当前的 Run | 只取消 Lead 的这个 Run；成员不受影响 |
| 停止全部成员 | 取消所有活动的成员 Run，向下传播到它们的子 Agent；团队保持 `active` |
| 移除成员 | 取消它的 Run；释放它负责的任务；交付规则见 4.7 |
| 解散 | 进入 `dissolving`；取消全部 Run 并等待资源静止；进入 `dissolved`；成员 Session 只读保留；Worktree 按保留策略处理；之后可以在同一 Session 建立新团队 |
| Lead Session 分叉 | 新 Session 不带团队 |
| 删除 Lead Session | 先解散，再级联删除成员 Session 和 Worktree |
| Runtime 重启 | 成员 Run 走现有 Recovery；待处理的唤醒意图重新评估；看板、回执和任务都已持久化；`dissolving` 的团队继续完成解散；不确定副作用照常记录，不重放 |
| 取消与迟到结果 | 任务迁移检查发起者 Run 的终态，迟到结果不能推进任务 |
| 备份与 Blob GC | 团队表随数据库备份；交付 Blob 在被引用期间保留 |

### 4.12 协议

| 方法 | 作用 |
| --- | --- |
| `team/read` | 读取团队快照：团队、成员及推导状态、任务、交付和用量 |
| `team/messages` | 按游标分页读取消息，可以按成员、任务和频道过滤 |
| `team/confirmRoster` | 确认或拒绝编制，可以附带用户修改；必填 `operationId` 保证幂等 |
| `team/post` | 用户向成员或看板发消息 |
| `team/taskUpdate` | 用户接受、退回或取消任务 |
| `team/integrate` | 用户整合某个交付 |
| `team/setAutoWake`、`team/setApprovalMode` | 修改团队设置 |
| `team/pause`、`team/resume`、`team/stopMembers`、`team/removeMember`、`team/dissolve` | 生命周期操作 |

- 事件通过 Outbox 发出：`team.changed` 携带快照版本，`team.message` 和 `team.task` 是增量通知，Renderer 按版本拉取。
- 所有方法注册到 Method Registry。DTO 由 Python Schema 生成 TypeScript 类型。协议变化同步双端 Fixture 和 `docs/current/`。

### 4.13 Desktop

- **入口**：`+` → 团队，或 `/team`。输入框显示团队模式标签，与目标模式的交互方式一致。
- **编制确认卡片**：放在 Lead 的 Feed 中。准备 Worktree 时显示进度，失败时标出对应的成员。
- **团队面板**位于右侧工作区，包含三个视图：
  - 看板：时间线，可以按成员、任务和频道过滤；私聊以收发双方标识。
  - 成员：名字、类型、模型、状态、工作区、用量和待审批数量。点击进入成员详情。
  - 任务：按状态分组，显示依赖、负责人、审查者和证据，并提供接受、退回和取消操作。
- **成员详情**：复用子任务详情，包括执行记录、审批、停止和变更视图，底部是给该成员发消息的输入框。
- **Lead 输入框**：`@成员` 发出的消息与成员详情输入框发出的消息是同一种用户消息。
- **Feed**：Lead 的 Feed 只显示本次 Run 收到的团队事件，以可折叠的行展示，不逐条展开消息。
- **通知**：发给 `user` 的消息和成员的审批请求会触发系统通知。
- **窄窗口**：团队面板的三个视图改为下拉切换，成员行只保留名字和状态。
- 布局遵循 AGENTS.md 的界面规则，不增加辅助说明。

### 4.14 限额汇总

| 项目 | 上限 |
| --- | --- |
| 每个 Session 的团队 | 同时 1 个 |
| 每个团队的活动成员 | 16 |
| 每个团队同时执行的成员 Run | 8（只作用于该团队） |
| 每个成员 Run 同时执行的子 Agent | 8（沿用每个父 Run 的规则） |
| 每个团队的任务 | 200；每个任务最多 16 个依赖 |
| 单条消息 | 64 KiB UTF-8 |
| 每次上下文投递的消息 | 16 条，每条预览 2,000 字符 |
| 成员名字 | `^[a-z][a-z0-9_-]*$`，1–40 个字符，不能使用保留名称 |
| 交付 | 2,000 个文件，新内容合计 64 MiB |

### 4.15 观测与成本

- 日志增加 `team_id`、`member_id` 和 `wake_id`。成员 Run 的 Trace 链接到 Lead，复用现有 Trace 上下文的传递方式。
- 团队面板显示每个成员和整个团队的 token 用量及时长，包括成员派生的子 Agent。
- 诊断导出包含团队快照，但不包含消息正文。

### 4.16 验收

| 类别 | 用例 |
| --- | --- |
| 领域 | 任务状态机各条迁移；循环依赖被拒绝；阻塞状态推导；用 Hypothesis 生成随机迁移序列，检验不变量 |
| Repository | 并发领取同一任务只有一方成功；唤醒意图唯一并合并；接纳只发生一次；有用户排队 Run 时意图被标为 `obsolete`；8 个并发名额按团队计数；暂停后排队 Run 保持等待 |
| 工具 | 按角色校验权限；幂等重放；私聊恰好一个收件人；`all` 和 `user` 只能用于看板；提交审查必须带证据；移除有未整合变化的成员被拒绝 |
| 上下文 | 投递上限与回执；运行中到达的消息触发下一次构建；压缩后团队状态仍在；私聊不进入第三方上下文 |
| 恢复 | 有待处理意图、运行中成员和 `dissolving` 团队时重启；取消与迟到结果竞争；成员等待审批时不阻塞其他成员和其他 Session；Shutdown 时大量空闲成员能达到资源静止 |
| 工作区 | 编制确认中 Worktree 创建失败后整体回滚；Git 与非 Git 项目；成员的子 Agent 落在成员工作区内 |
| 协议 | 双端 Fixture；`operationId` 幂等 |
| Desktop | 编制卡片、团队面板三个视图、成员详情输入、整合与冲突展示、窄窗口、等待与错误状态的行为测试；真实窗口中的交互验收 |
| 平台 | 真实 Seatbelt 下成员只能写自己的 Worktree；Electron smoke；Windows NTFS 上的成员 Worktree |

实施阶段先运行定向测试，不默认运行 Full Suite（AGENTS.md 第 25 和 26 节）。用真实模型评估团队效果，按[总体方案](agent-team-research-and-design.md#10-评测与验收)第 10 节单独进行。

## 5. 提交边界

P0 和 P1 各作为一个 PR，每个 PR 内按问题分成独立 Commit。

| PR | Commit |
| --- | --- |
| P0 | 1. Schema v55，交付与整合的领域和 Repository<br>2. `spawn_agent.workspace`、Worktree 生命周期与恢复<br>3. `verifier`、用户消息与用量汇总<br>4. 协议、Fixture 与 Desktop<br>5. `docs/current/` |
| P1 | 1. Schema、团队领域、Repository 与调度条件<br>2. 团队工具、上下文、唤醒与完成复核<br>3. 协议与 Fixture<br>4. Desktop<br>5. `docs/current/` |

## 6. 实施前核对清单

| 项目 | 原因 |
| --- | --- |
| `GitWorkingTreePatch` 对未跟踪文件和被忽略文件的处理规则 | 决定交付的范围 |
| 快照 anchor 能否作为交付基准 | 决定交付采集的实现方式 |
| 角色名在数据库中是否有 CHECK 约束 | 关系到新增 `verifier` 是否需要 Migration |
| `usage_json` 所在的表和汇总口径 | 关系到用量汇总 |
| 目标续跑接纳循环的调用位置 | 团队唤醒复用同一个接纳点 |
| Windows 上 Worktree 的路径长度与清理 | 双平台要求 |

返回 [设计目录](README.md)。
