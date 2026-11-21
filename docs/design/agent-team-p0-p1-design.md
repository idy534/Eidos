# Agent Team P0 与 P1 详细设计

> 文档分类：设计与实施记录。本文细化 [Agent Team 调研与设计](agent-team-research-and-design.md) 中的 P0 和 P1。当前行为以 [当前说明](../README.md#当前说明)、源码和测试为准。

日期：2026-11-18。

状态：P0 已在 `feat/windows-support` 分支实施，与本文的差异见第 8 节；P1 尚未实施。2026-11-18 完成一轮设计评审，结论与处理见第 7 节。文中的源码位置已在 `feat/windows-support` 分支核对；第 6 节列出仍需在实施时确认的项目。

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
| P0-2 | 工作区交付（Delivery）与整合（Integration），包括三方文本合并 | 任务提交与 `team_integrate` |
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
- **权限口径**：受管 Worktree 与基准引用由 Runtime 创建，不改变父工作区的 HEAD、index、分支和文件。模型通过 `spawn_agent` 请求创建时不逐次审批，这与用户创建 Worktree Session 时的处理一致，用户已于 2026-11-18 确认。实施时需要在 `docs/current/` 中写明这一口径。

创建流程复用 Session 分叉的“独立 Worktree”路径（[session_fork.py](../../runtime/eidos_runtime/application/session_fork.py) 的 `_prepare_worktree`）：

```text
spawn_agent ToolCall → Validate → Resolve Permission → Durable Intent
  → 快照父工作区（snapshots.save 或 save_local，包含未提交的修改）
  → WorktreeLifecycle.prepare(scope=agent/workspace-create, operationId=spawn ItemId)
  → manager.create_prepared(compensate_on_failure=True)（同时应用源仓库的 .worktreeinclude）
  → manager.restore_snapshot_state(head, changes)
  → source_snapshot 核对 head 与 changes 一致
  → 写入基准提交，用 refs/eidos/agent-bases/<agentId> 固定
  → 同一事务：子 Session、子 Run、agent_delegations（含 worktree_id、base_commit）、Event/Outbox
```

- **基准提交**：快照的 anchor 只固定 HEAD 提交（`refs/eidos/worktree-snapshots/<id>`，见 [git/backend.py](../../runtime/eidos_runtime/git/backend.py) 的 `create_snapshot_anchor`）。未提交的修改保存在快照的 patch 中，所以 anchor 不能直接作为比较基准。Runtime 在恢复完成后生成基准提交：以 HEAD 树为起点，在临时 index 中应用快照 patch，写出树和提交对象。这个过程不改动子 Worktree 的 HEAD、index 和文件。交付和整合都以基准提交为准。
- **被忽略的文件**：受管 Worktree 的创建流程会复制源仓库 `.worktreeinclude` 声明的被忽略文件，例如 `.env`（[git/manager.py](../../runtime/eidos_runtime/git/manager.py) 的 `create_prepared`）。`node_modules`、`.venv` 等依赖目录通常被忽略，也不在声明中，所以子任务的 Worktree 里没有它们；Shell 默认禁网，子任务很难自行安装。父任务应把需要依赖的测试放在共享工作区的任务中，或者由用户在 `.worktreeinclude` 中声明需要复制的内容。
- **失败处理**：创建失败时返回工具错误，不创建子任务，已创建的 Worktree 由 `compensate_on_failure` 补偿。崩溃时，这次 ToolCall 记为不确定结果，不会重放；Recovery 清理未被任何委派引用的 Worktree。
- **生命周期范围**：`worktree_lifecycle_operations.scope` 有 CHECK 约束（[db/schema.py](../../runtime/eidos_runtime/db/schema.py)），新增 `agent/workspace-create` 需要用 Migration 重建该表。
- **保留策略**：现有自动清理默认开启，受管 Worktree 上限为 15 个（`runtime_settings` 的初始值）。`_protected_reason`（[worktree_retention.py](../../runtime/eidos_runtime/application/worktree_retention.py)）只保护有活动 Run 等情形，空闲子任务的 Worktree 可能被清理；清理后再启动 Run 会返回 `WORKTREE_RESTORE_REQUIRED`（[application/runs.py](../../runtime/eidos_runtime/application/runs.py)）。因此子任务的 Worktree 不参与自动清理，也不计入 `managed_worktree_limit`，只由下文的删除流程处理。
- **删除**：现有的父 Session 删除在同一个事务中递归删除子 Session 的数据，并且明确不删除工作区（[db/repositories/sessions.py](../../runtime/eidos_runtime/db/repositories/sessions.py) 的 `delete_session_committed`）。`find_delete_for_session` 只处理被删 Session 自己的 Worktree。P0 需要在应用层的 Session 删除流程中，先对每个拥有 Worktree 的子任务执行 Worktree 删除生命周期，再删除数据。启动时的 reconcile 还要清理绑定 Session 已经不存在的子任务 Worktree。用户也可以单独放弃某个子任务的 Worktree。
- **Windows**：需要在 Windows 11 的 NTFS 上验证创建、路径长度和清理。

### 3.2 交付（Delivery）

交付是子任务 Worktree 当前状态相对基准提交的逐文件变化。

**采集时机**：按需采集，即父任务调用 `review_agent_changes`、`integrate_agent_changes`，或用户在 Desktop 打开变更视图时采集。采集要求子任务没有活动 Run。完成路径上不做采集，所以采集失败不会影响 Run 终态。

**采集方式**：以 HEAD 树为起点，在临时 index 中记录子任务 Worktree 当前的文件状态，写出当前树，并以交付提交固定。交付就是基准树与当前树的差异。

- 范围与现有 `capture_worktree_changes` 一致：包含已跟踪和未跟踪的文件，不包含被忽略的文件。含有未提交修改的子模块不受支持，返回 `delivery_submodule_unsupported`。
- 比较的两端都是树，子任务自己提交过也不影响结果。
- 文件内容留在 Git 对象库中，用 `refs/eidos/agent-deliveries/<deliveryId>` 固定，不复制到 Eidos Blob。

文件条目：

| 字段 | 说明 |
| --- | --- |
| `path` | 相对工作区根目录的路径 |
| `kind` | `added`、`modified` 或 `deleted`；重命名记为删除加新增 |
| `base`、`result` | 基准和交付中的 Git 对象；新增文件没有 `base`，删除文件没有 `result` |
| `mode`、`binary` | 文件权限位与二进制标记 |

- `.git` 和文件工具的永久拒绝路径不进入交付。
- 交付记录 Worktree 的指纹。指纹变化后交付标记为 `stale`；子任务空闲时，整合会重新采集。
- **模型可见内容**：`review_agent_changes` 只返回路径、变化类型、增删行数和 Diff 分页，不返回对象 ID、Hash 或指纹（AGENTS.md“自动恢复与模型上下文”）。
- **限额**：最多 2,000 个文件；Diff 总量不超过现有 Worktree patch 上限 64 MiB（[git/native.py](../../runtime/eidos_runtime/git/native.py) 的 `DEFAULT_GIT_PATCH_BYTES`）；单个文件沿用受控提交的 16 MiB 上限（`MAX_FILE_BYTES`）。超限时交付标记为 `too_large` 并返回具体原因，用户可以改用 Review 或 Git 手工处理。
- **展示**：Diff 预览按 AGENTS.md 第 19 节使用 `difflib` 生成，作为 Blob 保存。

### 3.3 整合（Integration）

整合有两个入口：父任务的工具 `integrate_agent_changes(agentId, paths?)`，以及用户在 Desktop 中触发的 `agent/integrate`。两个入口共用同一套整合记录。

前提：子任务没有活动 Run；交付有效，如果已经过期则重新采集。整合目标是父 Session 当前的工作区。

每个文件按以下规则处理：

| 父工作区中该文件的当前内容 | 结果 |
| --- | --- |
| 等于基准 | 写入子任务的版本，状态为 `applied` |
| 已经等于子任务的版本 | 不写入，状态为 `already_applied` |
| 其他内容，且三方都是文本文件 | 用 `git merge-file` 在临时目录中三方合并（基准、父工作区当前内容、子任务版本）。没有冲突时写入合并结果，状态为 `merged`；有冲突时不写入，状态为 `conflict` |
| 其他内容，且涉及二进制、删除或权限位变化 | 不写入，状态为 `conflict` |

- **三方合并**：使用 Git 自带的实现，不自研合并算法（AGENTS.md 第 9 节）。Git 本来就是 Git 项目功能的依赖：macOS 使用 `/usr/bin/git`，Windows 使用随包的 Git（`git/native.py` 的 `_native_git_path`）。合并只在内存和临时目录中进行，不会把冲突标记写进工作区。
- **受控写入**：写入沿用文件工具的受控提交：Durable Intent 之后重新核验版本；已有文件先打开并核验 fd，再截断、完整写入、fsync 并验证最终内容；新文件使用排他创建；删除前核验文件身份。路径策略与文件工具相同，受保护的路径会被拒绝。版本核验以合并时读到的父文件内容为准，写入前内容又发生变化的文件记为 `conflict`。
- **权限**：工作区内的写入使用现有 Workspace Permission，不逐次审批。`full_access` 按现有规则处理。
- **整合记录**：每次整合先写入整合记录（`workspace_integrations`，包括操作 ID、交付、目标 Session 和逐文件计划），再逐个文件执行受控写入并记录结果。Desktop 发起的整合不是 ToolCall，没有 Durable Intent，这份记录承担同样的作用（AGENTS.md 第 16 节）。崩溃后，Recovery 读取各文件的当前内容：等于计划写入的内容记为 `applied`，等于写入前的内容记为未执行，其他情况记为不确定。系统不自动重放。
- **结果**：多个文件之间不保证原子性。结果逐个文件如实报告，不自动重试，也不回滚。截断后失败的文件记为不确定结果（AGENTS.md 第 14 节）。交付状态更新为 `integrated` 或 `partially_integrated`。冲突文件在 Desktop 中以“基准 / 父工作区 / 子任务”三列展示。
- **放弃**：用户通过 `agent/discardWorkspace` 删除子任务的 Worktree，同时删除基准和交付引用，交付标记为 `discarded`。

### 3.4 用户直接给子 Agent 发消息

- Desktop 在子任务详情页提供输入框，调用 `agent/message { operationId, parentRunId, agentId, text }`。`operationId` 必填，同一操作重试只写入一次。
- `agent_messages` 增加 `sender_kind`（`agent` 或 `user`）和 `operation_id`。消息与 Event/Outbox 在同一事务中写入。
- **投递**：
  - 子任务有活动 Run 时，消息在下一次构建上下文时作为“用户指示”送达，只对该子任务具有用户授权含义。
  - 子任务空闲、父 Run 仍然活动时，按 `followup_task` 的语义启动一次子 Run，以 `operationId` 作为来源标识。
  - 父 Run 已经结束时，返回 `agent_parent_inactive`。跨轮继续由 P1 的团队模式解决。
- 父任务通过现有的 Agent 证据段看到“用户直接指示了 X”以及消息预览。
- `run/start` 对子 Session 的拦截（[application/runs.py](../../runtime/eidos_runtime/application/runs.py) 中的 `is_child`）保持不变。用户消息只通过 `agent/message` 进入。

### 3.5 内置 `verifier` 类型

- `AgentRole` 增加 `verifier`。内置职责是：用证据核对其他 Agent 的结论，运行定向检查，审查 Diff 或交付，给出接受或退回及理由。除非父任务明确要求，Verifier 不修改文件。
- `verifier` 和 `explorer` 一样只是任务侧重，不是只读权限模式。
- `verifier` 成为保留名称。已有名为 `verifier` 的自定义定义会被标记为不可用，原因是“与内置类型重名”。Runtime 不改写用户文件，已有 Run 继续使用冻结的定义。
- 数据库约束已核对：从 v29 起，`agent_delegations.role` 只有长度约束（`db/schema.py` 的 `V29_SCHEMA_SQL`），新增内置类型不需要修改该表。需要修改的是 `AgentRole` 与 `BUILTIN_AGENT_ROLES`（[domain/agents.py](../../runtime/eidos_runtime/domain/agents.py)）、内置职责指令、生成的 TypeScript 契约和 Desktop 的类型标签。

### 3.6 用量汇总

- `AgentSummary` 增加 `usage` 字段，包括输入、输出、缓存读取和缓存写入的 token 数，以及有效执行时长。AgentPanel 显示每个子任务的用量和合计。
- 用量来自 `model_attempts.usage_json`（字段见 [model/client.py](../../runtime/eidos_runtime/model/client.py) 的 `ModelUsage`）。汇总方式沿用目标模式（[persistence/goals.py](../../runtime/eidos_runtime/persistence/goals.py)）：只要有一次尝试缺少用量，汇总就标记为不完整，不把缺失的部分按 0 计算。

### 3.7 协议、持久化与工具

| 类别 | 变化 |
| --- | --- |
| Schema（v54 → v55） | `agent_delegations` 增加 `workspace_mode`、`worktree_id`、`base_commit`；`agent_messages` 增加 `sender_kind`、`operation_id`；新增 `workspace_deliveries`、`workspace_integrations`；`worktree_lifecycle_operations.scope` 的 CHECK 增加 `agent/workspace-create`（重建表） |
| `workspace_deliveries` | `id`、`owner_kind`（P0 为 `agent`，P1 增加 `team_member`）、`owner_id`、`worktree_id`、`base_commit`、`result_commit`、`fingerprint`、`entries_json`、`diff_blob`、`status`（`ready`、`stale`、`too_large`、`integrated`、`partially_integrated`、`discarded`）、`created_at`、`integrated_at` |
| `workspace_integrations` | `id`（操作 ID）、`delivery_id`、`target_session_id`、`source`（`tool` 或 `desktop`）、`item_id`、`state`、`file_results_json`、`created_at`、`completed_at` |
| 工具 | `spawn_agent.workspace`；新增 `review_agent_changes`（只读，返回文件列表、增删行数和 Diff 分页）与 `integrate_agent_changes`（写入父工作区） |
| RPC | `agent/changes`、`agent/integrate`、`agent/discardWorkspace`、`agent/message`，全部注册到 Method Registry；修改类方法都要求 `operationId` |
| 契约 | 由 Python DTO 生成 TypeScript 类型，并同步双端 Fixture |

- JSON 列只在 Repository 中解析，领域层使用带类型的模型（AGENTS.md 第 18 节）。
- Migration 覆盖新库、v54 升级、失败回滚和外键检查。
- Blob GC 保留被交付引用的 Diff 预览 Blob。基准和交付引用随 Worktree 删除一起删除。

### 3.8 Desktop

- AgentPanel 的子任务行显示工作区模式和用量。
- 子任务详情页增加输入框和“变更”视图。“变更”视图复用 Review 的 Diff 渲染，提供“整合”和“放弃”两个操作，自动合并的文件和冲突文件分别列出。
- 布局沿用现有的子任务详情，不增加说明性文字。

### 3.9 P0 验收

| 类别 | 用例 |
| --- | --- |
| Worktree 创建 | Git Local 与 Worktree 两种父 Session；基准包含未提交的修改，并与快照内容一致；`.worktreeinclude` 生效；非 Git 项目报错；创建中途崩溃后的恢复与补偿；同一 spawn 重放得到同一个 Worktree |
| 清理与保留 | 删除父 Session 时删除子任务的 Worktree；启动时清理无主的子任务 Worktree；自动保留策略不清理子任务 Worktree，也不把它们计入上限 |
| 交付 | 新增、修改、删除和二进制文件；子任务自行提交后结果仍然正确；保护路径被排除；含未提交修改的子模块报错；超出限额；指纹变化后过期；子任务运行中拒绝采集；模型可见结果不含 Hash |
| 整合 | 四种文件结果，包括无冲突和有冲突的三方合并；部分冲突；写入中途失败记为不确定结果；整合中途崩溃后逐文件恢复；与父 Run 同时写入同一文件时版本核验能发现冲突；Desktop 用同一 `operationId` 重试只执行一次 |
| 用户消息 | 活动子任务在下一次上下文中收到消息；空闲子任务被启动；父任务已结束时被拒绝；父任务看到通知；同一 `operationId` 重试只写入一次 |
| verifier | 内置类型可以派生；同名自定义定义被标记为不可用；旧数据兼容 |
| 平台 | 真实 Seatbelt 下子任务只能写自己的 Worktree；Electron smoke（preload 有变化）；Windows NTFS 上的 Worktree 和 `git merge-file` |

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
- 非 Git 项目的隔离副本，以及 Worktree 的依赖准备命令。这些放到 P2 评估。
- 渠道或定时任务触发，放到 P3。

### 4.2 用户流程

1. 用户点击 `+` → 团队，或输入 `/team <目标>`。P1 中团队模式与目标模式、Plan 模式互斥。渠道 Run 和定时任务 Run 不提供团队工具。
2. Lead 的第一个 Run 是普通的用户 Run，可以使用团队工具。Lead 调用 `team_propose` 后，编制方案写入 SQLite，这个 Run 进入持久的 `waiting_input` 状态。做法与 `request_user_input` 相同（[tools/planning.py](../../runtime/eidos_runtime/tools/planning.py)）。
3. Desktop 显示编制确认卡片，列出每个成员的名字、类型、模型、工作区模式和初始任务，以及并发上限、审批模式和自动续跑使用的模型。用户可以删除成员、修改工作区模式，然后确认或拒绝。
4. 用户确认后，`team/confirmRoster` 只保存用户的决定，然后调度 Lead 的 Run 继续执行。`team_propose` 恢复执行，在自己的 Durable Intent 之下创建团队：
   - 对 Lead 工作区做一次快照，所有 `worktree` 成员共用这一个基准。
   - 通过生命周期范围 `team/member-create` 逐个创建成员 Worktree，创建方式与 P0-1 相同。
   - 在一个事务中提交团队、成员、成员 Session、初始任务、唤醒意图和 Event/Outbox。
   - 任一 Worktree 创建失败时，补偿已创建的 Worktree，工具返回错误并指出失败的成员，Lead 可以调整后重新提出。
   - 发生崩溃时，这次 ToolCall 记为不确定结果，不会重放。Recovery 清理未被成员引用的 Worktree。
   - Lead 的 Run 在等待期间被取消时，确认返回 `team_proposal_inactive`。用户拒绝时，工具返回拒绝结果，Lead 可以继续以单 Agent 方式工作，或者重新提出。
5. Lead 继续拆分任务并分派。`RunResources._set_registry` 在每一步都会重建工具集，所以团队建立后的下一步就能使用其余团队工具，与目标工具的出现方式相同。成员被唤醒后执行，在看板上发消息、私聊、提交任务。
6. 发生事件时，Lead 自动续跑：审查任务、整合变化、向用户汇报。
7. 用户随时可以给任一成员发消息、处理审批、暂停或继续团队、停止所有成员，或者解散团队。

### 4.3 数据模型

Schema 在 P0 的基础上再升一个版本。所有业务状态变化都与 Event/Outbox 在同一事务中提交。

| 表 | 关键列 | 约束 |
| --- | --- | --- |
| `sessions`（新增列） | `internal_kind` | 取 `agent_child`、`team_member` 或 NULL；Migration 为现有子 Session 回填 `agent_child`，见 4.8 |
| `teams` | `id`、`lead_session_id`、`status`、`run_settings_json`（冻结的模型、思考档位、审批模式和完全访问确认）、`auto_wake_lead`、`catalog_ref`、`version`、`created_at`、`updated_at` | 每个 Session 同时最多一个未解散的团队（部分唯一索引）；`status` 取 `active`、`paused`、`dissolving`、`dissolved` |
| `team_proposals` | `id`、`lead_run_id`、`item_id`、`proposal_json`、`decision`、`decision_json`、`created_at`、`decided_at` | `item_id` 唯一；`decision` 取 `pending`、`confirmed`、`declined` |
| `team_members` | `id`、`team_id`、`name`、`role`、`definition_json`、`model_json`、`session_id`、`workspace_mode`、`worktree_id`、`base_commit`、`status`、`created_at`、`removed_at` | 同一团队内 `name` 唯一；`session_id` 唯一；`status` 取 `active` 或 `removed` |
| `team_tasks` | `id`、`team_id`、`key`、`title`、`description`、`acceptance`、`owner_member_id`、`reviewer`、`status`、`review_required`、`paths_json`、`evidence_json`、`delivery_id`、`version`、`created_by`、`created_at`、`updated_at` | 同一团队内 `key` 唯一；`status` 取 `open`、`in_progress`、`in_review`、`done`、`canceled` |
| `team_task_deps` | `task_id`、`depends_on_task_id` | 联合主键；创建时拒绝循环依赖 |
| `team_messages` | `id`、`team_id`、`seq`、`channel`、`author_kind`、`author_member_id`、`to_all`、`task_id`、`content`、`source_item_id`、`operation_id`、`created_at` | `(team_id, seq)` 唯一；`source_item_id` 与 `operation_id` 各自唯一，用于幂等；`channel` 取 `board` 或 `direct` |
| `team_message_deliveries` | `message_id`、`recipient`、`model_attempt_id`、`delivered_at` | 联合主键 `(message_id, recipient)`；写消息时为每个收件人插入一行（`all` 在写入时展开）；`(recipient, delivered_at)` 建索引 |
| `team_wakes` | `id`、`team_id`、`target`、`reasons_json`、`status`、`run_id`、`created_at` | 每个 `target` 最多一条 `pending`（部分唯一索引），做法与 `goal_continuations` 一致；`status` 取 `pending`、`accepted`、`obsolete` |
| `team_runs` | `run_id`、`team_id`、`member_id`（Lead 为空）、`wake_id`、`origin` | 标记自动 Run 的来源；`origin` 取 `wake` 或 `user_message` |
| `worktree_lifecycle_operations`（约束） | `scope` | CHECK 增加 `team/member-create`（重建表） |

- 成员的运行状态（空闲、运行、等待审批）由 `runs` 表推导，不另行保存。
- 成员的定义从 Lead Run 冻结的 Agent 目录中解析。模型使用定义中的模型，没有定义时继承团队冻结的 Lead 模型。
- 成员 Session 不出现在侧栏，只能从团队面板打开，标题为成员名字。
- `agent_delegations` 继续只服务子 Agent。成员的交付复用 `workspace_deliveries`，`owner_kind` 为 `team_member`。
- 所有 JSON 列只在 Repository 中解析为带类型的模型。

### 4.4 状态机

**Team**

| 从 | 到 | 触发 |
| --- | --- | --- |
| — | `active` | `team_propose` 在用户确认后完成创建 |
| `active` | `paused` | 用户暂停 |
| `paused` | `active` | 用户继续 |
| `active` 或 `paused` | `dissolving` | 用户解散，或删除 Lead Session |
| `dissolving` | `dissolved` | 全部成员 Run 达到终态，资源静止，成员 Worktree 已删除 |

**Task**

| 从 | 到 | 操作者 | 条件 |
| --- | --- | --- | --- |
| `open` | `in_progress` | 负责人领取，或 Lead 指派 | 依赖全部为 `done`；负责人为活动成员 |
| `in_progress` | `in_review` | 负责人 | `review_required` 为真时必须附带证据；`worktree` 成员同时采集交付 |
| `in_progress` | `done` | 负责人 | 只限 `review_required` 为假的任务 |
| `in_review` | `done` | `reviewer`（Lead 或指定的 Verifier 成员） | 接受 |
| `in_review` | `in_progress` | `reviewer` | 退回，必须附带原因 |
| `in_progress` 或 `in_review` | `open` | Lead 或用户 | 释放；负责人被移除时自动释放 |
| 任一非终态 | `canceled` | Lead 或用户 | — |

- 任务被阻塞是由依赖推导出的状态，不单独存储。
- 状态迁移使用条件 UPDATE，以当前状态和负责人作为前提，不需要模型提供版本号。只有修改标题、描述、验收标准等字段的 `update` 操作使用 `version` 防止覆盖。
- 每次迁移在同一事务中写入系统消息、唤醒意图和 Event/Outbox。
- 迁移前检查发起者 Run 的状态：已经取消的成员 Run 产生的迟到结果不能推进任务。
- 任务状态只是协调状态。“已完成”的证据是被引用的工具结果或交付。

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
- Lead 负责集成和最终汇报，所以可以按需读取私聊，但私聊不会推送进 Lead 的上下文。用户已于 2026-11-18 确认这一点。
- Claude Code 文档没有说明 Lead 能否看到私聊；Antigravity 子 Agent 之间可以互相读取对话记录。

寻址与投递：

- 地址包括成员名字、`lead`、`all` 和 `user`。`all` 和 `user` 只能用于看板。私聊必须恰好有一个收件人。
- 发给 `all` 的消息只在收件人下次运行时送达，不唤醒空闲的收件人。点名的消息才会唤醒，见 4.6。
- 保留名称：`lead`、`all`、`user`、`parent`、`system`、`team`。
- 每次构建上下文，最多投递 16 条未投递的消息，每条预览最多 2,000 字符；完整内容通过 `team_read` 按 ID 读取。这些做法与现有子 Agent 消息一致。
- 模型请求成功完成后才写入投递记录；失败、取消或只准备未发送的请求不消耗消息。这与现有子 Agent 消息回执的规则一致（[persistence/collaboration.py](../../runtime/eidos_runtime/persistence/collaboration.py)）。
- 单条消息最多 64 KiB UTF-8。
- 所有消息都是数据，不是授权。用户消息的作者由 Runtime 根据 RPC 来源写入，只对被寻址的成员具有用户授权含义。

### 4.6 唤醒与调度

**原则**：

- 唤醒只由事件产生，不由状态条件产生。事件在引起它的事务中写入唤醒意图。
- 由 Lead 自己的 Run 引起的事件不唤醒 Lead；由某个成员自己引起的事件不唤醒该成员。
- 空闲回调只负责接纳已经存在的意图，不重新评估任何条件，否则 Lead 会在每次空闲时被重新唤醒。

**事件与唤醒目标**

| 事件 | 写入位置 | 唤醒目标 |
| --- | --- | --- |
| 点名的看板消息、私聊或用户消息（不含 `all`） | 写消息的事务 | 被点名的收件人 |
| 任务被指派、被退回或解除阻塞 | 任务迁移的事务 | 负责人 |
| 任务进入 `in_review` | 任务迁移的事务 | 审查者（Lead 或 Verifier 成员） |
| 最后一个非终态任务进入终态 | 任务迁移的事务 | Lead |
| 成员 Run 进入终态，且该成员没有 `in_progress` 任务，或者 Run 失败、中断 | Run 终态事务 | Lead |
| 任意 Run 进入终态，其 Session 仍有未投递的团队消息 | Run 终态事务 | 该 Run 所属的成员或 Lead |

- 每个目标最多一条 `pending` 意图。多个事件合并为一次唤醒，原因累积记录在 `reasons_json`。
- 发往 `user` 的消息不唤醒任何 Agent，只在界面上提醒用户。
- Lead 的唤醒意图只在 `auto_wake_lead` 为真时写入。

**接纳**

- 待处理意图在目标 Session 空闲时接纳，有三个触发点：
  - 写入意图的事务提交后，目标已经空闲时立即接纳。
  - Supervisor 的 `on_session_idle` 回调。目标续跑也使用这个回调（[protocol/server.py](../../runtime/eidos_runtime/protocol/server.py) 的 `_wake_goals`），它目前只有一个回调槽，需要改成可以分发给多个订阅者。
  - 继续团队，以及 Runtime 启动恢复。
- 接纳逻辑放在应用层的 TeamApplication 中，通过 Supervisor 的受管任务执行。目标续跑把接纳循环写在 `protocol/server.py` 中，团队不沿用这种做法（AGENTS.md 第 7 节）。
- 接纳在一个事务中完成：
  1. 复核团队状态、成员状态和 Session 是否空闲。
  2. 用团队冻结的运行设置创建排队 Run（见 4.8）。
  3. 写入 `team_runs`，把意图标记为 `accepted`，写入 Event/Outbox。
- 自动 Run 通过 `runs.start` 的团队来源参数进入，做法与目标续跑的 `goal_intent_id` 相同。Desktop 直接对成员 Session 调用 `run/start` 仍被拒绝。
- Session 中已经有排队的用户 Run 时，意图标记为 `obsolete`，由用户的 Run 读取这些消息。
- 自动 Run 在 Feed 中标注为“团队续跑”，不提供“重新回答”和“编辑并重新发送”。

**运行中到达的消息**

- Engine 在完成前检查是否有未投递的团队消息，有则重建上下文，与现有子 Agent 消息的处理相同（[runtime/engine.py](../../runtime/eidos_runtime/runtime/engine.py) 中的 `has_unread_messages`）。
- 这项检查不在终态事务中，消息可能在检查之后、终态提交之前写入，而写消息的事务看到目标正在运行，不会写入唤醒意图。因此 Run 终态事务（[db/transitions.py](../../runtime/eidos_runtime/db/transitions.py) 中 `settle_goal_run` 所在的位置）还要再检查一次，仍有未投递消息时，在同一事务中写入唤醒意图。

**调度条件**

现有的选取排队 Run 的查询（[db/repositories/runs.py](../../runtime/eidos_runtime/db/repositories/runs.py)）增加条件。成员 Session 的排队 Run 必须同时满足：

- 团队处于 `active`；或者团队处于 `paused`，且该 Run 来自用户消息。
- 团队中处于 `running` 或 `finalizing` 的成员 Run 少于 8 个。

补充说明：

- 等待审批或输入的成员不占名额。Lead 的 Run 不计入。
- 成员派生的子 Agent 按原有条件以父 Run 计数。
- 这项限制只作用于该团队，不是全局配额。

**暂停**

暂停后，只接纳由用户消息产生的唤醒意图；其他意图保持待处理，继续团队后再接纳。暂停前已经排队但尚未开始的自动 Run 暂不执行，已经在运行的 Run 继续执行。

**往返提示**

如果任意两方（包括 Lead）连续互相唤醒超过 6 次，且期间没有任何任务状态变化，Runtime 向 Lead 和用户各发一条系统提示。系统不自动暂停或结束任何 Run。

### 4.7 工作区

**Git 项目**

- 每个成员可以选择 `shared` 或 `worktree`。`worktree` 成员的创建方式与 P0-1 相同。一次编制确认只对 Lead 工作区做一次快照，所有 `worktree` 成员共用这个基准，快照包含未提交的修改。
- 编制确认卡片上，`explorer` 和 `verifier` 默认使用 `shared`。有两个及以上写入成员时，写入成员默认使用 `worktree`。用户可以修改。
- **依赖与验证位置**：`worktree` 成员没有被忽略的依赖目录（见 P0-1）。P1 的验证分两步：
  1. 成员在自己的 Worktree 中运行能运行的检查，结果作为证据随任务提交。
  2. 整合到 Lead 工作区后，需要整体验证时，Lead 创建验证任务，交给共享工作区中的 Verifier。

  共享工作区中的 Verifier 看不到尚未整合的成员变化，所以审查 `worktree` 成员的任务时，只能检查交付的 Diff 和证据。为审查单独创建基于交付的 Worktree 放到 P2。
- `worktree` 成员提交任务时，Runtime 采集交付并关联到任务。Lead 通过 `team_integrate` 整合，用户也可以在团队面板中整合，规则同 P0-2。
- 移除仍有未整合交付或未采集变化的 `worktree` 成员时，工具返回 `member_has_unintegrated_changes`。Lead 需要先整合，或者显式传入 `discardChanges=true`。用户在 Desktop 中移除时需要二次确认。
- **保留与清理**：成员 Worktree 与 P0 的子任务 Worktree 一样，不参与自动保留清理，也不计入上限。它们由移除成员、解散团队和删除 Lead Session 的流程删除。

**非 Git 项目**

- P1 只支持 `shared`。
- 任务的 `paths` 只是声明，不是锁，因为 AGENTS.md 第 13 节禁止跨 Run 的共享工作区副作用门。
- 文件工具的写入有版本核验，冲突会作为错误返回给成员，由成员重新读取后处理。Shell 命令（例如 `sed -i` 或脚本）的写入没有版本核验，可能覆盖其他成员的修改。编制确认卡片会提示全部成员共用同一份文件，任务分配应避免多个成员修改同一个文件。
- 对于文档和调研类任务，建议成员通过消息或最终回答交付结果，或者写入各自负责的文件，由 Lead 汇总。
- 隔离副本放到 P2 评估：macOS APFS 可以用 `clonefile` 低成本复制；Windows NTFS 没有对应能力，需要有上限的复制。

### 4.8 权限、运行设置与内部 Session

**冻结的运行设置**

- 用户确认编制时，Runtime 冻结发起 `team_propose` 的 Lead Run 的模型、思考档位、审批模式和完全访问确认，保存到 `teams.run_settings_json`。目标续跑和定时任务也采用同样的做法，在启动自动 Run 时传入冻结的值（`protocol/server.py` 中两处 `fullAccessConfirmation`）。
- Lead 的自动续跑和成员的 Run 都使用这些设置。成员的模型按定义解析，没有定义时继承冻结的 Lead 模型。
- 用户在输入框中给 Lead 发的消息仍使用输入框当时的选择。
- 用户可以在团队面板中修改冻结设置，修改只影响之后新建的 Run。改为 `full_access` 时需要再次确认。
- `full_access` 在确认卡片上使用 danger 样式，需要用户显式确认。模型、成员消息和模板都不能切换审批模式。

**审批**

- 成员的扩展快照和个性化设置在成员创建时从 Lead 冻结。
- 成员不继承 Lead 的 Run Grant，需要时通过自己的审批获得。
- 成员的工具审批沿用现有界面，并附带成员身份。团队面板汇总审批队列，提供“下一个等待审批”的入口。Lead 不能代替用户批准。`auto_review` 照常评估每个请求，高危请求仍然需要人工确认。

**成员的工具范围**

- 成员保留协作工具（作为自己子 Agent 的父任务）和成员可用的团队工具。
- 成员没有 `request_user_input`、`request_permissions`、`write_plan`、目标工具和定时任务工具。需要用户决定时，在看板上发给 `user`。
- 系统生成的唤醒 Run 不写入长期记忆，这与自动目标 Run 的处理相同（[runtime/run_resources.py](../../runtime/eidos_runtime/runtime/run_resources.py)）。
- 用户发给成员的消息会触发 `UserPromptSubmit` Hook，系统生成的唤醒输入不触发。现有子任务 Run 也不触发这个 Hook（`runtime/engine.py`）。

**内部 Session**

现有代码通过 `agent_delegations.child_session_id` 判断一个 Session 是否为子任务，这类判断分布在以下位置：

- 列表与检索：侧栏（`persistence/sidebar.py`）、Session 列表（`db/repositories/sessions.py`）、搜索（`persistence/search.py`）、历史检索（`persistence/history_search.py`）、会话历史（`persistence/session_history.py`）。
- 输入与自动化：`run/start` 拦截（`application/runs.py`）、排队输入（`persistence/queued_runs.py`）、目标（`persistence/goals.py`）、定时任务（`persistence/automations.py`、`application/automations.py`）。
- 共享资源：记忆归属（`memory/repository.py`）、Hook（`persistence/hooks.py`、`runtime/engine.py`）、浏览器授权（`persistence/browser_grants.py`）、Projectless 重置（`persistence/projectless_reset.py`）。
- 生命周期：Session 删除与删除保护（`db/repositories/sessions.py`）、调度与恢复（`db/repositories/runs.py`、`db/recovery.py`、`db/transitions.py`、`db/repositories/execution.py`）、工具集组装（`runtime/run_resources.py`）。

团队成员 Session 在这些位置需要同样的处理。如果逐处追加对团队表的判断，很容易漏掉某一处，导致成员 Session 出现在侧栏或搜索中、被设置目标或定时任务、被直接 `run/start`，或者写入记忆。P1 的第一个 Commit 先为 `sessions` 增加 `internal_kind` 列，把“是否为内部 Session”的判断统一改为读取该列，并为每处补充行为等价测试；需要父子关系的查询（调度计数、父任务查找）继续使用原来的关联表。

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
| `team_task` | `op`（`create`、`update`、`assign`、`claim`、`submit`、`accept`、`reject`、`release`、`cancel`）、`key`、该操作所需的字段、`evidence[]`（`itemId` 或 `toolCallId`）、`note`；只有 `update` 需要 `version` |
| `team_post` | `to[]`、`channel`（`board` 或 `direct`）、`text`、`task`（可选） |
| `team_read` | `kind`（`messages`、`message`、`tasks`、`task`、`member`）、`id`、`cursor`、`filter`（成员、任务、频道） |
| `team_wait` | `members[]`、`tasks[]`、`mode`（`any` 或 `all`）、`timeoutMs`（1,000–300,000） |
| `team_integrate` | `task` 或 `member`（二选一）、`paths[]`（可选） |

- 有副作用的团队工具，副作用类型都是 `eidos_state`，走现有 ToolCall 流程和 Durable Intent，并按 Item ID 幂等重放。
- `team_integrate` 写入 Lead 的工作区，副作用类型为文件写入，规则同 P0-2。
- `team_wait` 复用 `wait_agents` 的持久等待和持有 Worker 的语义。发给 Lead 的消息、用户输入和超时都会结束等待。
- **证据归属**：证据只能引用工具结果，并且这些结果必须来自提交者自己 Session 中的 Run，或其子 Agent Session 中的 Run。Runtime 在提交时校验归属，不通过时返回 `team_evidence_invalid`。

**稳定错误码**

`team_not_active`、`team_proposal_inactive`、`team_member_limit`、`team_member_name_invalid`、`team_member_name_exists`、`team_member_not_found`、`team_recipient_not_found`、`team_direct_single_recipient`、`team_task_not_found`、`team_task_version_conflict`、`team_task_state_conflict`、`team_task_blocked`、`team_task_cycle`、`team_task_forbidden`、`team_evidence_required`、`team_evidence_invalid`、`member_has_unintegrated_changes`、`delivery_stale`、`delivery_too_large`、`delivery_submodule_unsupported`、`agent_worktree_requires_git`、`team_mode_conflict`。

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
| 移除成员 | 取消它的 Run；释放它负责的任务；交付规则见 4.7；删除它的 Worktree |
| 解散 | 有未整合交付时，用户先选择整合或放弃；进入 `dissolving`；取消全部 Run 并等待资源静止；删除全部成员 Worktree；进入 `dissolved`；成员 Session 只读保留；之后可以在同一 Session 建立新团队 |
| Lead Session 分叉 | 新 Session 不带团队 |
| 删除 Lead Session | 先按解散流程删除成员 Worktree，再级联删除成员 Session 和团队数据 |
| Runtime 重启 | 成员 Run 走现有 Recovery；待处理的唤醒意图重新接纳；看板、投递记录和任务都已持久化；`dissolving` 的团队继续完成解散；`team/member-create` 留下的无主 Worktree 被清理；不确定副作用照常记录，不重放 |
| 取消与迟到结果 | 任务迁移检查发起者 Run 的状态，迟到结果不能推进任务 |
| 备份与 Blob GC | 团队表随数据库备份；交付 Blob 在被引用期间保留 |

### 4.12 协议

| 方法 | 作用 |
| --- | --- |
| `team/read` | 读取团队快照：团队、成员及推导状态、任务、交付和用量 |
| `team/messages` | 按游标分页读取消息，可以按成员、任务和频道过滤 |
| `team/confirmRoster` | 保存用户对编制的确认或拒绝，可以附带修改 |
| `team/post` | 用户向成员或看板发消息 |
| `team/taskUpdate` | 用户接受、退回或取消任务 |
| `team/integrate` | 用户整合某个交付 |
| `team/setAutoWake`、`team/setRunSettings` | 修改自动续跑开关和冻结的运行设置 |
| `team/pause`、`team/resume`、`team/stopMembers`、`team/removeMember`、`team/dissolve` | 生命周期操作 |

- 所有修改类方法都要求 `operationId`，同一操作重试只执行一次。
- 事件通过 Outbox 发出：`team.changed` 携带快照版本，`team.message` 和 `team.task` 是增量通知，Renderer 按版本拉取。
- 所有方法注册到 Method Registry。DTO 由 Python Schema 生成 TypeScript 类型。协议变化同步双端 Fixture 和 `docs/current/`。

### 4.13 Desktop

- **入口**：`+` → 团队，或 `/team`。输入框显示团队模式标签，与目标模式的交互方式一致。
- **编制确认卡片**：放在 Lead 的 Feed 中，展示成员和冻结的运行设置。Lead 创建 Worktree 期间显示进度，失败时标出对应的成员。
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
| 交付 | 2,000 个文件，Diff 总量 64 MiB，单文件 16 MiB |

### 4.15 观测与成本

- 日志增加 `team_id`、`member_id` 和 `wake_id`。成员 Run 的 Trace 链接到 Lead，复用现有 Trace 上下文的传递方式。
- 团队面板显示每个成员和整个团队的 token 用量及时长，包括成员派生的子 Agent；缺少用量时标记为不完整。
- 诊断导出包含团队快照，但不包含消息正文。

### 4.16 验收

| 类别 | 用例 |
| --- | --- |
| 内部 Session | `internal_kind` 回填；每个列出的调用点对成员 Session 的行为与子 Session 一致：不进侧栏和搜索、不能设置目标或定时任务、不能直接 `run/start`、删除受保护、记忆与 Hook 按规则处理 |
| 领域 | 任务状态机各条迁移；循环依赖被拒绝；阻塞状态推导；用 Hypothesis 生成随机迁移序列，检验不变量 |
| 唤醒 | 唤醒只由事件产生：Lead 自动 Run 结束后不会被状态条件再次唤醒；Lead 自己引起的事件不唤醒 Lead；`all` 不唤醒空闲成员；审查者被唤醒；终态事务为未投递消息补写意图；有用户排队 Run 时意图被标为 `obsolete`；暂停时只接纳用户消息的意图 |
| Repository | 并发领取同一任务只有一方成功；意图唯一并合并；接纳只发生一次；8 个并发名额按团队计数 |
| 工具 | 按角色校验权限；幂等重放；私聊恰好一个收件人；`all` 和 `user` 只能用于看板；提交审查必须带证据；证据归属校验；移除有未整合变化的成员被拒绝 |
| 上下文 | 投递上限；只有模型请求成功后才记为已投递；运行中到达的消息触发下一次构建；压缩后团队状态仍在；私聊不进入第三方上下文 |
| 编制与恢复 | 确认后由 `team_propose` 创建团队；Worktree 创建失败时补偿；创建中途崩溃后无主 Worktree 被清理；Lead Run 取消后确认被拒绝；有待处理意图、运行中成员和 `dissolving` 团队时重启；取消与迟到结果竞争；成员等待审批时不阻塞其他成员和其他 Session；Shutdown 时大量空闲成员能达到资源静止 |
| 工作区 | Git 与非 Git 项目；成员的子 Agent 落在成员工作区内；成员 Worktree 不被自动保留策略清理；解散和删除 Lead Session 时删除成员 Worktree |
| 协议 | 双端 Fixture；所有修改类方法的 `operationId` 幂等 |
| Desktop | 编制卡片、团队面板三个视图、成员详情输入、整合与冲突展示、窄窗口、等待与错误状态的行为测试；真实窗口中的交互验收 |
| 平台 | 真实 Seatbelt 下成员只能写自己的 Worktree；Electron smoke；Windows NTFS 上的成员 Worktree |

实施阶段先运行定向测试，不默认运行 Full Suite（AGENTS.md 第 25 和 26 节）。用真实模型评估团队效果，按[总体方案](agent-team-research-and-design.md#10-评测与验收)第 10 节单独进行。

## 5. 提交边界

P0 和 P1 各作为一个 PR，每个 PR 内按问题分成独立 Commit。

| PR | Commit |
| --- | --- |
| P0 | 1. Schema v55：委派的工作区字段、消息来源、交付与整合记录、生命周期范围<br>2. Worktree 创建、基准提交、保留保护与删除清理<br>3. 交付、三方合并与整合<br>4. `verifier`、用户消息与用量汇总<br>5. 协议、Fixture 与 Desktop<br>6. `docs/current/` |
| P1 | 1. `sessions.internal_kind` 与全部内部 Session 调用点，附行为等价测试<br>2. Schema、团队领域、Repository 与调度条件<br>3. 团队工具、上下文、唤醒与终态处理<br>4. 协议与 Fixture<br>5. Desktop<br>6. `docs/current/` |

## 6. 核对结果与剩余项

| 项目 | 结果 |
| --- | --- |
| `GitWorkingTreePatch` 的范围 | 已核对：包含已跟踪和未跟踪的文件，不包含被忽略的文件；含未提交修改的子模块不支持（`git/backend.py` 的 `capture_worktree_changes`） |
| 快照 anchor 能否作为基准 | 已核对：只固定 HEAD，不能作为基准；改用基准提交（3.1） |
| 角色名的数据库约束 | 已核对：v29 起只有长度约束（3.5） |
| `usage_json` 的位置与汇总 | 已核对：`model_attempts.usage_json`，按目标模式的方式汇总（3.6） |
| 目标续跑的接纳位置 | 已核对：`protocol/server.py` 的 `_wake_goals`，由 `supervisor.on_session_idle` 和目标变化触发；团队改在应用层实现（4.6） |
| `.worktreeinclude` 是否覆盖复用路径 | 已核对：`create_prepared` 会应用（3.1） |
| Windows 上的 Worktree 与 `git merge-file` | 未核对，需要在 Windows 11 NTFS 上验证 |
| 大仓库中临时 index 写树的耗时 | 未核对，需要实测 |

## 7. 评审记录

日期：2026-11-18。

方式：执行者对照源码自行复核。当前环境没有 AGENTS.md 第 31 节所说的 `code-reviewer`，本轮也没有使用其他独立审查。评审只核对了源码与文档，没有运行测试。

结论：确认 19 个问题，其中高风险 3 个、中风险 9 个、低风险 7 个，均已在本文中修订。另有 2 项产品决定，用户已确认按现方案执行。

| 编号 | 严重程度 | 问题 | 证据 | 处理 |
| --- | --- | --- | --- | --- |
| R1 | 高 | 删除父 Session 时，子任务的独立 Worktree 不会被删除 | `delete_session_committed` 递归删除子 Session 数据，并注明不删除工作区（`db/repositories/sessions.py`）；`find_delete_for_session` 只处理被删 Session 自己的 Worktree（`application/sessions.py`） | 3.1 增加应用层删除与启动清理 |
| R2 | 高 | 自动保留策略会清理空闲子任务或成员的 Worktree；团队的 Worktree 还会占用用户自己 Worktree 的名额 | 自动清理默认开启，上限 15（`db/schema.py` 的 `runtime_settings`）；`_protected_reason` 只保护活动 Run 等情形（`application/worktree_retention.py`）；清理后启动 Run 返回 `WORKTREE_RESTORE_REQUIRED`（`application/runs.py`） | 3.1、4.7 增加保护，且不计入上限 |
| R3 | 高 | Lead 的唤醒原因写成了状态条件，会反复触发自动续跑 | 原 4.6 把“成员空闲且没有进行中的任务”“所有任务都已结束”列为唤醒原因；空闲回调会在每次 Lead 空闲时触发 | 4.6 改为事件触发，排除 Lead 自己引起的事件，空闲回调只接纳已有意图 |
| R4 | 中 | 运行中到达的消息可能丢失唤醒 | 完成前的未读检查不在终态事务中（`runtime/engine.py`、`db/transitions.py`） | 4.6 在终态事务中补写唤醒意图 |
| R5 | 中 | 自动 Run 缺少冻结的模型、思考档位、审批模式和完全访问确认 | 目标续跑和定时任务启动自动 Run 时传入冻结值（`protocol/server.py`） | 4.3、4.8 增加冻结的运行设置 |
| R6 | 中 | 子任务 Session 的特殊处理分布在约 20 处，原设计只提到侧栏 | 4.8 列出的调用点 | P1 第一个 Commit 引入 `internal_kind`；4.8 写明成员工具范围 |
| R7 | 中 | 快照 anchor 不能作为交付基准；同一文件被父任务改动时整合只能失败 | `create_snapshot_anchor` 只写 HEAD 引用（`git/backend.py`）；未提交的修改在快照 patch 中（`git/models.py`） | 3.1 增加基准提交；3.3 增加 `git merge-file` 三方合并 |
| R8 | 中 | 编制确认在 RPC 中创建 Worktree，崩溃后没有恢复语义；新增生命周期范围需要 Migration | 原 4.2 第 4 步；`worktree_lifecycle_operations.scope` 有 CHECK 约束（`db/schema.py`） | 4.2 改由恢复执行的 `team_propose` 创建；3.7、4.3 增加 Migration |
| R9 | 中 | `all` 会唤醒全部空闲成员，容易形成消息风暴；往返提示只覆盖成员之间 | 原 4.5、4.6 | `all` 不唤醒；往返提示包括 Lead |
| R10 | 中 | 证据引用没有归属校验 | 原 4.9 | 4.9 增加归属校验 |
| R11 | 中 | 用户发起的修改操作缺少 `operationId`；Desktop 发起的整合没有持久记录 | `session/delete` 等现有方法带操作 ID（`application/sessions.py`）；AGENTS.md 第 16 节 | 3.3、3.4、3.7、4.12 补齐 |
| R12 | 中 | `worktree` 成员没有被忽略的依赖目录；共享工作区的 Verifier 看不到未整合的变化 | `.worktreeinclude` 只复制声明的文件（`git/manager.py`）；Shell 默认禁网 | 3.1、4.7 写明依赖处理和验证位置 |
| R13 | 低 | 消息投递记录的写入时机写错 | 回执在模型请求成功后写入（`persistence/collaboration.py`） | 4.5 改正 |
| R14 | 低 | 暂停时“不接纳唤醒”与“用户消息可以执行”互相矛盾 | 原 4.6 | 4.6 改正 |
| R15 | 低 | 被指定为审查者的成员不会被唤醒 | 原 4.6 唤醒原因表 | 4.6 补充 |
| R16 | 低 | 领取等状态迁移要求 `version`，增加模型负担 | 原 4.9 | 只有 `update` 需要 |
| R17 | 低 | 非 Git 共享工作区的冲突描述过于乐观 | Shell 写入不经过版本核验 | 4.7 改正 |
| R18 | 低 | 收件人保存在 JSON 列中，投递查询需要扫描 JSON | 原 4.3 | 改为按收件人存储的投递表 |
| R19 | 低 | 两份设计文档有不一致之处 | 总体方案 8.5、8.11、第 9 节 | 已同步修改 |

产品确认（用户于 2026-11-18 确认按现方案执行）：

1. Lead 可以按需读取成员之间的私聊，私聊不推送进 Lead 的上下文（4.5）。
2. 模型通过 `spawn_agent` 创建受管 Worktree 时不逐次审批（3.1）。

## 8. P0 实施记录

日期：2026-11-18。以下是实施与第 3 节设计不同的地方及原因。

| 设计 | 实施 | 原因 |
| --- | --- | --- |
| 新增生命周期范围 `agent/workspace-create`，并重建 CHECK 约束 | 复用 `session/create` 范围，操作 ID 以 `agent-workspace-` 开头 | 子任务创建本来就是“建 Worktree 并绑定新 Session”，复用现有状态迁移即可，不需要重建表 |
| 基准提交 | 基准树，固定在 `refs/eidos/agent-workspaces/<agentId>/base`；最新交付树固定在同目录的 `delivery` | 比较只需要树，不需要提交身份和作者配置 |
| `workspace_integrations` 表与 Desktop 直接整合 | v55 建了该表但没有使用，v56 将其删除；整合只通过父任务的 `integrate_agent_changes`，Desktop 只提供查看和放弃 | Desktop 发起的写入没有 Run 的权限快照和 Durable Intent，需要单独设计；工具路径完整复用 `apply_patch` 的受控写入 |
| 冲突文件逐个标记，其余文件照常写入 | 任一请求的文件冲突时整个调用不写入，并列出路径；父任务可用 `paths` 整合其余文件 | 避免一次调用留下部分写入；父任务能明确选择 |
| 二进制文件按整文件替换整合 | 二进制文件不由工具整合，结果中列出 | 现有受控写入的 Diff 生成只支持 UTF-8 文本 |
| `agent/discardWorkspace` 带 `operationId` | 不带 | 放弃是幂等操作，重复执行没有额外副作用 |
| 用量放在子任务摘要中 | 只在 Desktop 读取的摘要中计算 | 用量在运行中持续变化，若进入引擎比较的协作状态，会让完成判断反复重建上下文 |
| — | 新增 `agent/changes` | Desktop 变更视图需要读取文件列表和分页 Diff |

验证见本轮交付说明。真实 Seatbelt 下 Worktree 子任务的 Shell 执行和 Windows NTFS 尚未单独验证。

## 9. P1 与 P2 实施记录

日期：2026-11-20。当前行为见 [Agent 团队](../current/features/teams.md)。以下列出实施与第 4 节及[总体方案](agent-team-research-and-design.md)第 9 节 P2 不同的地方。

### P1

| 设计 | 实施 | 原因 |
| --- | --- | --- |
| Schema 版本未定 | v56：`sessions.internal_kind`（回填 `agent_child`）与团队各表；删除未使用的 `workspace_integrations` | 一次迁移完成内部 Session 与团队数据；见第 8 节 |
| `team/integrate` 由用户在 Desktop 整合 | 不提供；整合只走 Lead 的 `team_integrate` | 与 P0 相同：Desktop 写入没有 Run 的权限快照和 Durable Intent |
| `team/setAutoWake`、`team/setRunSettings`、`team/pause`、`team/resume`、`team/dissolve` | 合并为 `team/updateSettings` 与 `team/setStatus`；新增 `team/memberChanges` 只读查看成员变更 | 方法更少，含义不变；成员面板需要读取 Diff |
| 各方法分别声明 | `protocol/teams.py` 的 `TEAM_METHODS` 是唯一方法表，Runtime 按它注册，Desktop 的类型、方法映射与校验器由它生成 | 避免两端维护两份方法与 DTO 对应关系 |
| `team.changed` 携带快照版本，另有 `team.message`、`team.task` 增量通知 | 只有 `team.changed` → `team/updated { sessionId }`；Renderer 重新读取，看板按游标增量拉取；成员 Session 的 Run 通知也会刷新 Lead 的团队视图 | 一种通知足够驱动界面，避免两套增量语义 |
| Feed 列出本次 Run 收到的团队事件 | Feed 只用一行标注“团队续跑 · 成员”，消息在团队面板查看 | 先保证 Feed 不被消息淹没；逐条事件行留待后续 |
| 成员详情复用子任务详情 | 复用执行记录组件，审批、停止、变更与发消息由团队面板提供 | 成员没有委派记录，不能直接复用整个子任务面板 |
| 团队拒绝统一映射 | 新增 `TEAM_*` 稳定错误码，操作 ID 冲突沿用 `OPERATION_ID_REUSED` | Desktop 需要区分“有未整合变更”“任务已变化”等情况 |
| — | 事件投递对团队唤醒的 Run 不再查找用户消息 | 这类 Run 没有用户消息；原逻辑会让投影失败并阻塞其后全部通知（stdio 往返测试发现） |

P1 未实施：实验开关、Lead 输入框中的 `@成员` 消息、发给 `user` 的消息与成员审批的系统通知、诊断导出中的团队快照，以及日志中的 `team_id`、`member_id`、`wake_id` 字段。

### P2

| 设计 | 实施 | 原因 |
| --- | --- | --- |
| `teams/*.toml` 模板（编制、角色、Verifier 比例、工作区策略） | 内置 `feature`、`research`、`milestones` 写在 `infrastructure/team_templates.py`；用户模板读取数据目录 `teams/*.toml`，不能覆盖内置名称。模板在确认编制时冻结，提供 Lead 指引、普通任务默认审查、工作区策略提示和关口审查者 | 内置模板随代码发布，不依赖打包资源；冻结保证运行中的团队不受文件修改影响。Verifier 比例由成员组成表达，不另设字段 |
| 里程碑计划产物、Critic 与 Challenger 关口 | 任务增加 `kind = gate`。关口按里程碑隐式等待全部普通任务，不占依赖上限；负责人提交即通过，或用新操作 `reopen` 退回已完成任务，同里程碑关口回到阻塞。模板的 `gate_reviewers` 为每个里程碑自动创建关口 | 关口拒绝能退回具体任务，并持续阻挡下一里程碑；Critic 与 Challenger 用 `verifier` 类型加不同职责表达，不新增内置类型 |
| 团队与目标模式结合 | `teamMode` 可以与 `goalMode`、`goalRevision` 同用；团队 Session 中的目标续跑以 Lead 身份运行；团队有成员运行或待处理唤醒时，目标续跑等待 | 避免目标续跑与团队唤醒交替重跑 Lead |
| Hook `TeamMemberIdle`、`TeamTaskCompleted` | 已接入：前者替代成员的 `Stop`，后者在任务将要完成前运行，拒绝时任务保持原状态 | 语义分别对齐 `SubagentStop` 和工具前置拒绝 |
| 成员上下文分叉（类似 `fork_turns`） | `context_turns` 把 Lead 最近 0 到 8 轮的用户消息与最终回答冻结为成员常驻指令，上限 16 KiB | 复制条目到成员 Session 会带入工具输出和权限语境；冻结文本保持缓存前缀稳定 |
| Lead 交接 | 未单独实施；Lead 依靠 Session 压缩继续，压缩保留团队状态段 | 现有压缩已覆盖上下文重置，单独继任者需要新的持久语义 |
| 团队级 Grant | 未实施 | 涉及权限边界，需要单独设计撤销与审查方式 |
| 成员再派子 Agent | 保留，按每个成员 Run 计数 | 第 2 节已决定 |

P3（常驻团队）未实施。团队 Session 中的定时任务 Run 和渠道 Run 已经以 Lead 身份运行，可以驱动已有团队；事件到任务的自动转换、Thread 订阅和规则化主动行为仍在路线图中。

返回 [设计目录](README.md)。
