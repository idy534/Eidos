# Agent 团队

团队模式让一个 Session 的主 Agent（Lead）带领多个命名成员，通过看板、私聊和任务板协作完成较大的目标。成员跨轮存在，空闲时不占用模型调用，有人点名、分派任务或任务解锁时才被唤醒。设计过程与取舍见 [Agent Team P0 与 P1 详细设计](../../design/agent-team-p0-p1-design.md)。

## 入口与编制确认

| 操作 | 当前行为 |
| --- | --- |
| “＋” → 团队，或 `/team` | 输入框显示“团队模式”标签。命令本身不创建团队。 |
| `/team <目标>` 或团队模式下发送 | 普通用户 Run 开始，`run/start` 带 `teamMode`。Lead 获得 `team_propose` 工具。 |
| Lead 调用 `team_propose` | 编制方案写入 SQLite，Run 进入持久 `waiting_input`，与 `request_user_input` 相同。输入框位置显示编制卡片。 |
| 编制卡片 | 列出成员名字、类型、职责、工作区与初始任务，以及模型、审批模式和自动续跑开关。用户可以去掉成员、切换工作区，然后确认或拒绝。 |
| 确认 | `team/confirmRoster` 只保存决定。Lead 的 `team_propose` 恢复执行，在自己的 Durable Intent 之下创建成员 Worktree、成员 Session、任务和唤醒意图。团队建好后右侧工作区自动打开“团队”标签。 |
| 拒绝 | 工具返回拒绝结果，Lead 可以继续单独工作或重新提出。 |
| 提议作废 | Lead 的 Run 在用户答复前结束（停止、失败或中断），或确认后创建失败时，提议记为拒绝。`team/read` 只返回仍在等待且 Lead Run 仍活动的提议，团队入口随即恢复可用。 |
| 团队已存在 | 同一 Session 只有一个活动团队。团队入口在此期间不可用，之后的普通消息由 Lead 带团队处理。 |

团队模式与 Plan 模式互斥，可以与目标模式同时使用（见下文）。成员的审批模式、模型和思考档位在确认编制时冻结；`full_access` 只能来自用户在 Desktop 确认的访问模式。模板、模型和成员消息都不能切换审批模式。

## 成员、工作区与上下文

- 成员 Session 是内部 Session（`internal_kind = team_member`），不进入侧栏、搜索、定时任务或目标；不能直接 `run/start`，删除 Lead Session 时级联清理。
- 工作区默认 `shared`。Git 项目可以选择 `worktree`：成员 Worktree 以 Lead 工作区的当前快照为基准，复用子 Agent 的独立 Worktree 机制（创建、基准树、保留保护和删除清理）。非 Git 项目只能使用共享工作区。
- 成员的工作由 Lead 用 `team_integrate` 整合进 Lead 工作区，路径、权限、Durable Intent 和冲突规则与 `integrate_agent_changes` 相同。按任务整合只使用提交该任务时保存的交付，即使之后的提交或查看把它标为过期；没有交付或交付已丢弃的任务会被拒绝，需要按成员整合当前文件。Desktop 只读查看成员变更，不直接整合。
- 移除成员时，它所有未完成的任务（包括仍被阻塞的任务和关口）回到无人负责的待领取状态。移除仍在运行的 Worktree 成员时，先停止它的 Run，Worktree 在 Run 结束后删除。
- `context_turns`（0 到 8）可以把 Lead 最近几轮的用户消息和最终回答复制进成员的常驻指令，总量不超过 16 KiB。内容在成员创建时冻结，标为背景材料而非用户授权；工具输出、审批和思考过程不复制。
- 每个成员 Run 最多同时执行 8 个子 Agent（沿用每个父 Run 的规则）。成员不能添加成员，不能直接向用户提问或申请权限，需要用户决定时在看板上发给 `user`。

## 看板、私聊与任务

- `team_post` 发送看板消息或 `direct` 私聊。点名的空闲成员被唤醒；`all` 在各自下一轮可见但不唤醒；`user` 只显示给用户。私聊不进入其他成员的上下文，其他成员也读不到；Lead 和用户可以读取全部私聊。消息是任务数据，不是用户授权。
- 每次请求最多投递 16 条未读消息，每条预览 2,000 字符；超长消息以截断预览投递，全文用 `team_read` 读取。团队状态段不超过 48 KiB：先收缩任务板，再按时间顺序保留放得下的消息，其余留到下一次请求。只有模型请求成功后，请求中带上的消息才记为已投递。
- 任务有依赖、负责人、审查者、证据和里程碑。`claim` 使用条件更新，并发领取只有一方成功。需要审查的任务提交时必须引用自己（或自己派生的子 Agent）的工具结果作为证据。依赖完成或取消后，已分派的任务自动开始并通知负责人。
- 唤醒只由事件产生：点名消息、任务分派或解锁、审查请求、成员终态。Lead 自己引起的事件不唤醒 Lead。Run 正常结束时如果仍有未读消息，会再次被唤醒；被停止、取消、中断或失败的 Run 不会自行重启，未读消息等下一次点名或用户消息。团队创建或新增成员后立即启动成员；Runtime 重启后，待处理的唤醒在启动完成时开始。暂停的团队只接纳用户消息引起的唤醒。每个团队最多同时运行 8 个成员 Run，只在该团队内计数。

## 里程碑关口

- 关口任务（`kind = gate`）必须属于一个里程碑。只要同一里程碑还有未结束的普通任务，关口就保持阻塞；最后一个普通任务完成或取消后，关口的负责人被唤醒。
- 关口负责人审查整个里程碑：提交关口表示通过，依赖该关口的下一里程碑任务随之解锁；或者对已完成的任务执行 `reopen` 并写明原因，任务退回原负责人，该里程碑的所有关口回到阻塞状态，等任务再次完成后重新打开。
- Lead 和用户也可以 `reopen`。其他成员不能重新打开别人的任务。
- 关口开始审查或已通过后，如果有未完成的普通任务新建在或移入该里程碑，关口回到阻塞，负责人在下一轮看到通知，等这些任务完成后再次被唤醒。只有 Lead 和用户可以修改任务的里程碑。

## 模板

- 内置模板：`feature`（架构、实现、测试、核查）、`research`（调研、汇总、质疑）、`milestones`（两名实现者，加上按里程碑把关的 critic 与 challenger）。
- 用户模板放在数据目录的 `teams/*.toml`，最多读取 64 个文件，读取规则与 `agents/*.toml` 相同（属主、单链接、大小和读取期间不变）。无效文件被跳过；用户模板不能覆盖内置名称。
- Lead 在提出编制时看到模板目录，可以用 `template` 字段指定名称。模板在确认编制时冻结进团队，之后修改文件不影响该团队。冻结的模板提供：Lead 的分工指引、新建普通任务默认需要审查、以及为每个出现的里程碑自动创建 `gate.<n>.<审查者>` 关口任务。

模板字段：`name`、`description`、`guidance`、`members`（`name`、`role`、`brief`、可选 `workspace`）、`workspace_policy`（`shared` 或 `worktree_for_writers`）、`review_required`、`gate_reviewers`（必须是模板成员）。

## 与目标模式结合

- 团队 Session 中的所有 Run（包括目标续跑、定时任务和渠道消息）都以 Lead 身份运行，获得团队工具与团队状态。
- `teamMode` 可以与 `goalMode` 或 `goalRevision` 同时使用；在活动目标中切换到团队模式不会暂停目标。
- 团队有成员在运行或有待处理的唤醒时，目标续跑意图保持待处理，不重复启动 Lead；成员汇报后由团队唤醒带回 Lead，空闲后再按目标规则续跑。

## Hooks

- `TeamMemberIdle`：成员 Run 正常结束前触发，替代该成员的 `Stop`，与 `SubagentStop` 替代子 Agent 的 `Stop` 一样。按成员名匹配，`decision: block` 或退出码 2 让成员继续工作。
- `TeamTaskCompleted`：任务将要变为完成前触发（无需审查的提交，或审查者接受）。按任务键匹配，拒绝时任务保持原状态，工具结果说明 Hook 的原因。

输入字段包含 `team_id`、`teammate_name`，任务事件另有 `task_id`、`task_subject` 和 `task_description`。

## Desktop

右侧工作区的“团队”标签包含三个视图，窄窗口改为下拉切换：

- **看板**：消息时间线，可以按成员和频道筛选；底部可以发给 Lead、某个成员或全体，并可选私聊。
- **成员**：名字、类型、状态、工作区、模型、用量和待审批数量。点击进入成员详情：执行记录、审批、停止、Worktree 变更、给该成员发消息，以及移除。成员有未整合变更时，移除会提示，用户可以选择放弃变更并移除。
- **任务**：按状态分组，显示负责人、审查者、依赖、证据、整合状态、里程碑和关口；可以接受、退回（需写原因）、取消，以及重新打开里程碑中已完成的任务。

面板头部提供暂停或继续、停止所有成员、自动续跑开关和解散（需确认）。解散会停止成员 Run，并在它们结束后删除成员 Worktree。Lead 的 Feed 用“团队续跑”标注由团队事件启动的 Run；这些 Run 不提供“重新回答”和“编辑并重新发送”。

## 协议

| 方法 | 作用 |
| --- | --- |
| `team/read` | 团队、待确认编制和每名成员的用量（含其派生的子 Agent） |
| `team/messages` | 按游标分页读取消息，可以按成员、任务和频道过滤 |
| `team/memberChanges` | 只读读取 Worktree 成员的文件列表和分页 Diff |
| `team/confirmRoster` | 保存用户对编制的确认或拒绝 |
| `team/post`、`team/taskUpdate` | 用户发消息，或接受、退回、取消、重新打开任务 |
| `team/setStatus`、`team/stopMembers`、`team/removeMember`、`team/updateSettings` | 生命周期与设置 |

修改类方法都要求 `operationId`。方法表 `TEAM_METHODS` 是唯一来源，Desktop 的类型、方法映射和校验器由 `scripts/generate-team-contracts.mjs` 生成，`protocol/fixtures/teams-v1.json` 由两端测试共同校验。团队拒绝映射为稳定错误码：`TEAM_MEMBER_HAS_CHANGES`、`TEAM_MEMBER_RUNNING`、`TEAM_PROPOSAL_INACTIVE`、`TEAM_NOT_ACTIVE`、`TEAM_TASK_CONFLICT`、`TEAM_WORKSPACE_UNAVAILABLE`、`TEAM_INVALID_REQUEST`，操作 ID 冲突沿用 `OPERATION_ID_REUSED`。

## 限额

| 项目 | 上限 |
| --- | --- |
| 每个 Session 的活动团队 | 1 |
| 每个团队的活动成员 | 16 |
| 每个团队同时运行的成员 Run | 8 |
| 每个团队的任务 | 200；每个任务最多 16 个依赖 |
| 单条消息 | 64 KiB UTF-8 |
| 每次投递的消息 | 16 条，每条预览 2,000 字符 |
| 成员共享的 Lead 上下文 | 8 轮，16 KiB |
| 模板文件 | 64 个 |

## 验证入口

```bash
.venv/bin/python -m pytest runtime/tests/test_teams.py -q
pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/components/teams
pnpm build:main && node --test dist/main/runtime-client-teams.test.js
node_modules/.bin/electron scripts/test-teams.cjs
```

Main 测试通过真实 stdio 启动 Runtime，用 `EIDOS_FAKE_MODEL=team` 的脚本模型走完提出、确认、成员运行和解散。Electron 脚本在隔离页面中使用生产 Composer、编制卡片和团队面板，检查入口、三个视图、成员详情和多种窗口宽度；`EIDOS_TEAM_SCREENSHOTS` 可以指定截图目录。页面使用模拟数据，不读取日常 Eidos 数据，也不代表真实模型的团队效果。

## 尚未提供

- 团队级 Run Grant：成员不继承 Lead 的 Grant，需要时各自通过审批获得。
- Lead 交接：Lead 依靠 Session 压缩继续工作，压缩保留团队状态段；没有单独的继任者交接。
- 常驻团队：定时任务和渠道消息可以驱动已存在团队的 Lead，但没有事件到任务的自动转换、Thread 订阅或规则化的主动行为。
- 非 Git 项目的成员隔离副本与 Worktree 依赖准备命令。

返回 [当前功能说明](README.md)。
