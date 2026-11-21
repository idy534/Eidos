# Agent Team 调研与设计方案

> 文档分类：设计与实施记录（含调研）。本文记录 2026-11-18 的外部调研、Eidos 现状对照和推荐方案。外部产品大多处于预览或实验阶段，行为会随版本变化。当前行为以 [当前说明](../README.md#当前说明)、源码和测试为准。

日期：2026-11-18。

状态：调研与方案设计。本文没有对应实现，也没有运行评测。第 12 节的问题已于 2026-11-18 确认。P0 和 P1 的详细设计见 [Agent Team P0 与 P1 详细设计](agent-team-p0-p1-design.md)，两份文档不一致时以详细设计为准。

## 1. 结论

1. “Agent Team”不是单一形态。本轮涉及的产品分成四类：
   - **编码团队**：Antigravity `/swarm` 与 `/teamwork`、Claude Code Agent Teams、Codex Multi-agent V2、Kimi Agent Swarm。一个协调者带多个并行成员，成员之间可以沟通，并共享任务或看板。
   - **常驻路由**：DeepSeek Super-agent。一个常驻 Router 面向人，按事件把工作交给并行 Worker，目标是节省人的注意力。
   - **常驻个人 Agent**：ChatGPT dots、Claude Duet、Meta Muse。它们是长期在线、主动汇报、自带云端电脑的单个 Agent。公开资料没有把多 Agent 协作写成已发布能力，dots 把“团队”列为后续计划。它们对 Eidos 的参考价值在常驻、触发、主动汇报和规则化授权。
   - **群聊**：阿里云 AgentTeams、Coze 3.0，以及 Bloome、Alook 等。人和多个 Agent 在同一房间里通过 @ 协作。资料多为厂商宣传和中文评测。
2. 编码团队的共同方向是：成员有名字并持续存在；团队共享任务或看板；成员之间直接沟通；用户可以直接与任一成员对话；闲置成员不消耗 token；并发写入的成员使用隔离工作区；验证角色固定为团队的一部分。
3. 反面证据同样明确。多 Agent 会显著增加 token 消耗：Anthropic 的研究系统约为普通对话的 15 倍，DeepSeek 合成测试的缓存读取约为单会话的 3.8 倍。在依赖多、需要共享上下文的编码任务上，并行甚至可能降低成功率。团队模式应由用户显式开启，不能取代默认的单 Agent 和子 Agent。
4. Eidos 已有大部分底座：持久的 Child Session 和 Run、消息与回执、持久等待、自定义 Agent 类型与模型、Managed Worktree、目标续跑、渠道、定时任务、审批和 Seatbelt。主要缺口有五项：
   - 委派绑定父 Run，下一轮对话看不到上一轮的子任务。
   - 通信是星型的，子任务只能联系父任务。
   - 没有共享任务板。
   - 成员没有工作区隔离，也没有集成流程。
   - 用户不能直接指挥子任务。
5. 推荐路线：在现有 Session 和 Run 之上增加 Session 级 Team 协作层，不新建第二套 Runtime 或状态权威。实施分四期：
   - P0 补强子 Agent 底座。
   - P1 Team MVP：Lead、命名成员、团队看板、任务板、可选 Worktree 和 Verifier。
   - P2 长程团队与模板：结合目标模式、里程碑和审查关口。
   - P3 常驻团队：Router–Worker 结构，接入渠道和定时任务。
6. 第 12 节的决策已确认：每个 Team 最多同时执行 8 个成员 Run；Git 与非 Git 项目都支持；公开看板加成员私聊；Lead 自动续跑默认开启；团队模式保留子 Agent 工具；常驻团队进入路线图。

## 2. 术语与范围

| 术语 | 本文含义 |
| --- | --- |
| 子 Agent | 父 Run 内的一次性委派，结果回传父任务。这是 Eidos 的现有能力。 |
| Agent Team | Session 级的一组命名成员。成员共享任务板和看板，彼此可以沟通，用户可以直接与成员对话。 |
| Lead | 团队中面向用户的成员，负责拆解、分派、集成和最终汇报。默认就是用户所在的 Session。 |
| Member | 拥有独立 Session、独立上下文和可选独立工作区的团队成员。 |
| 看板（Board） | 团队唯一的有序消息日志，支持 `@成员`、`@all`、`@lead` 和 `@user`。 |
| 任务板（Task Board） | 共享任务列表，记录负责人、依赖、状态和证据引用。 |
| 常驻团队 | 由渠道、定时任务等事件驱动的长期团队，不依赖用户逐轮发起。 |

本文覆盖调研、差距、架构方案、分期和验收。跨机器或跨组织的 Agent 互联（A2A）和模型训练不在范围内。

## 3. 业界调研

### 3.1 总览

| 产品 | 形态与状态 | 拓扑 | 通信 | 任务组织 | 工作区 | 用户介入 |
| --- | --- | --- | --- | --- | --- | --- |
| Antigravity `/swarm` | 编码团队，预览 | 协调者、命名 Worker、Verifier 和 Monitor | 共享看板，支持 `@name`、`@group`、`@all`，没有私聊；等待中的成员不耗 token | 协调者按互不冲突的方式拆分，在看板发布分工 | 默认共享；并发编辑或构建时，协调者可以给成员独立工作副本 | 可直接给单个 Worker 发消息；逐个 Worker 审批；Board、Members、Graph 三个视图 |
| Antigravity `/teamwork` | 长程编码团队（数小时到数天） | Sentinel、Project Orchestrator、Explorer 与 Worker，以及多个审查关口 | 工作区内的请求、计划、进度三份结构化产物 | 里程碑与依赖流；文件分配给单个 Worker | 独立项目目录，成员各有草稿目录 | 开始前进行范围访谈，用户确认一次 |
| Antigravity 子 Agent | 委派 | 父子关系，可多层，深度上限 10 | 按会话 ID 发消息，对象可以是父、子或同级 | 由父任务提示词决定 | `inherit`、`branch`（Git worktree）或 `share` | 审批冒泡到主界面 |
| Claude Code Agent Teams | 编码团队，实验功能 | Lead 加 Teammates（各自独立实例）；不可嵌套；一个 Session 只有一个团队 | 每个成员一个邮箱，成员间点对点；自动投递；成员空闲时通知 Lead | 共享任务列表：三种状态、依赖、自领取（文件锁防抢） | 共享工作区，同文件编辑会互相覆盖 | 可直接与任一成员对话；权限提示汇聚到 Lead；建议 3–5 人 |
| Codex Multi-agent V2 | 子 Agent 增强 | 树形，路径寻址；第三方指南称默认深度 3 | `send_message`、`followup_task`、`wait_agent`、`interrupt_agent` | 没有共享任务板 | 资料未说明 | 子线程不接受用户直接输入，社区有开放请求 |
| Kimi Agent Swarm | 研究与通用任务 | 可训练的编排者加大量并行子 Agent，K2.6 最多 300 个 | 由编排者汇总 | 编排者动态拆分（PARL 训练） | 云端 | 用户只与编排者交互 |
| DeepSeek Super-agent | 常驻路由，研究原型 | 常驻 Router 加并行 Worker；Worker 可以再派子 Agent，也可以自成团队 | Thread 订阅，后续消息直接投递给 Worker | Trigger → Eval → Ticket，Ticket 附带不可变 Mandate | 每个 Ticket 一个目录，可配置为 git worktree | 用户与 Router 或已订阅的 Thread 交互 |
| dots、Duet、Muse | 常驻个人 Agent | 单个 Agent；dots 把团队列为后续计划 | 主动消息、简报 | 定时任务、后台研究、邮件和日历事件 | 各自的云端 VM | 规则化授权和审批卡片 |
| 群聊类（阿里云 AgentTeams、Coze 3.0、Bloome 等） | 人与多个 Agent 同房间 | Team、Room、Task；Leader–Worker | @mention | 分发任务后汇总 | 取决于平台 | 多人与多个 Agent 同时参与 |

### 3.2 各产品要点

**Antigravity：三层能力。** 官方文档把多 Agent 能力分为三层：子 Agent 做委派，`/swarm` 提供实时看板，`/teamwork` 面向带关口的长程项目。对 Eidos 有参考价值的设计如下：

- `/swarm` 的协调者本身不做技术工作。它为每 2–4 个 Worker 配一个 Verifier，并配一个只在团队停滞时才醒来的 Monitor。
- 看板全部公开，成员之间没有私聊。等待中的成员在被 @ 之前不耗 token。
- 默认共享工作区。只有成员同时编辑或构建时，协调者才给成员独立工作副本，移除成员时一并删除该副本。
- 审批按 Worker 分别处理，界面提供“跳到下一个等待审批的成员”。文档提醒团队会更快消耗配额；成员触及配额后暂停，不会自动重启。
- `/teamwork` 先做范围访谈，用户确认一次后才开始执行。Orchestrator 在里程碑之间交接给新的继任者以重置上下文。Critic、Challenger、Auditor 和 Success Auditor 分别负责评审、对抗测试、证据核对和最终验收。项目还有 development、demo、benchmark 三种完整性模式。
- 子 Agent 每次调用都可以选择继承工作区、使用独立 Git worktree 或共享目录。

**Claude Code Agent Teams。** 该功能是实验性的，需要显式开启。

- Lead 与 Teammates 是各自独立的实例，每个实例有自己的上下文。
- 共享任务列表有 pending、in progress、completed 三种状态，支持依赖。依赖完成后任务自动解锁。成员可以自领取未分配的任务，领取使用文件锁防止竞争。
- 每个成员有一个邮箱，成员之间可以点对点发消息，投递是自动的。成员停止时会通知 Lead，并附上最终回答。
- 用户可以直接与任一成员对话。成员的权限模式继承自 Lead，权限提示汇聚到 Lead 所在的 Session。
- 文档列出的限制包括：进程内成员不能随 Session 恢复；一个 Session 只有一个团队；不能嵌套；Lead 固定；同一文件的编辑会互相覆盖；token 消耗随活跃成员数增长。
- 文档的指引是：需要快速汇报结果的专注工作用子 Agent；需要成员彼此分享发现、相互质疑时用团队；顺序任务、同文件编辑和强依赖任务不适合团队。
- 文档还提供 `TeammateIdle`、`TaskCreated`、`TaskCompleted` 三个 Hook。

**DeepSeek Super-agent。** DeepSeek Harness 团队于 2026-10-31 发布设计长文和 `dsh-superagent-prototype` 仓库。仓库使用 TypeScript 和 MIT 许可证，README 声明它是仅供参考的研究原型，不是官方产品。

- **出发点**：一个人通常只能同时跟进 3–5 个活跃会话，人的注意力成为并行工作的上限。
- **Router**：常驻，面向人，负责分派和跨任务记忆，不负责拆解任务。
- **Worker**：上下文相互隔离，使用最强的模型，不直接与用户交流。
- **领域模型**：Source 产生 Trigger。Eval 是唯一可以创建 Ticket 的角色，并为 Ticket 附加不可变的 Mandate，记录策略版本、范围和强度。Worker 订阅 Thread 后，同一 Thread 的后续消息直接投递给它，不再经过 Router。
- **原型执行**：每个 Ticket 有自己的目录，可以通过配置的 prepare 命令创建 git worktree。前台 Shell 运行超过 5 秒转入后台，每轮最多再运行 30 分钟。原型的命令以操作者身份运行，没有审批，仓库明确说明命令拦截不是安全边界。Eidos 不能照搬这一点。
- **主动行为**：长文描述了“静默复现并修复”的策略，Agent 确认问题和修法后才发言。仓库规则是 Agent 只在被要求或有书面规则允许时行动。
- **合成测试**：Super-agent 完成时间 p50/p95 为 12.4/15.7 分钟，单会话为 20.4/29.5 分钟；缓存读取约 2,323 万 token，单会话约 605 万。去掉 Thread 直投后，Router 决策约增加 30%。作者称这些结果是初步的。
- **作者列出的开放问题**：超出单个 Router 的扩展、Worker 之间的通信、长期记忆、会话隐藏后的可见性，以及凭据安全。

**Kimi Agent Swarm。** Kimi 通过 PARL 强化学习训练编排者。奖励里加入并行项，防止编排者退化为串行执行（serial collapse）。帮助页还列出了“假并行”失效模式，即拆出无意义的子任务刷指标。K2.5 最多支持 100 个子 Agent，K2.6 提高到 300 个，官方称关键步骤减少 3–4.5 倍。Kimi 的方案主要在训练侧，对 Eidos 的价值在于提供评测时要识别的失效模式。

**Codex Multi-agent V2。** Codex V2 的工具集与 Eidos 现有协作工具相近，Eidos 早期就对照过 Codex。社区 Issue 反映：V2 的并发配置含义容易混淆，默认会话总线程数为 4 且包含根线程；子线程不接受用户直接输入，有用户请求开放纠偏能力。用户直接指挥成员是 Eidos 可以补上的体验。

**常驻个人 Agent（dots、Duet、Muse）。**

- 三个产品都在云端为 Agent 提供独立电脑或 VM，Agent 可以在用户离开后继续工作，并主动汇报。
- dots 支持定时任务和只读的后台研究。它的 Custom Rules 为每类动作设置四种处理方式：直接执行、预先批准后执行、执行前询问、交给用户。Auto-review 不能被规则关闭。
- Duet 每隔几小时检查一次已连接的账户。发送、付款、签署和删除之前都会询问用户，出站消息另有分类器检查敏感信息。
- Muse 用独立的 Sentinel 审查出网动作。
- 这些产品的参考价值在 P3：常驻、触发、主动汇报，以及独立于执行 Agent 的审查层。Eidos 已有 `auto_review` 审批模式，可以作为对应基础。

**群聊类产品。** 中文评测称，阿里云 AgentTeams 提供 Human↔Agent、Agent↔Agent、多人对多 Agent 三种协作模式，并统一使用 Team、Room、Task 语义。Coze 3.0 转向多人多 Agent 的项目化协作。Bloome、Alook 等产品把 Agent 当作群成员，通过 @ 调用。这类资料多为宣传材料，可靠性没有独立验证。

### 3.3 研究与反面证据

- **Anthropic 多 Agent 研究系统（2025-06）**：采用编排者–工作者结构。单 Agent 约消耗普通对话 4 倍的 token，多 Agent 约 15 倍。在 BrowseComp 评测中，token 用量本身解释了约 80% 的表现差异。该结构适合可以广度展开的研究任务；对可并行部分较少、依赖较多的编码任务，收益有限。
- **Cognition《Don't Build Multi-Agents》（2025-06）**：每个动作都隐含决策。并行子 Agent 看不到彼此的决策，结果容易冲突，因此应尽量共享完整上下文。
- **MAST（Cemri 等，2025）**：把多 Agent 系统的失败归纳为三类共 14 种：系统设计与规格、Agent 之间失配、任务验证与终止。
- **arXiv 2610.10263（2026-10）**：比较了 Codex Multi-agent V2、Claude Code 和 Kimi AgentSwarm 的动态并发。论文认为并行在三种情况下有帮助：完成互补工作、探索替代方案、独立验证主实现。论文建议选择性启用并发。二手摘要提到：某基准的通过率下降；主 Agent 设定的汇合时间超出任务预算，导致必需的子任务被截断；关键路径被单个组件占据时，其他成员闲置。本文没有阅读论文原文，具体数字以原文为准。

### 3.4 趋势归纳

| 趋势 | 代表 | 对 Eidos 的含义 |
| --- | --- | --- |
| 成员身份化：有名字、角色、模型，并持续存在 | `/swarm`、Claude Code | 成员需要跨轮存在，不能绑定在单个 Run 上 |
| 去中心化但可见的通信 | `/swarm` 公开看板；Claude Code 点对点邮箱 | 需要成员之间的通信，同时保证用户能看到 |
| 共享任务状态 | Claude Code 任务列表；`/teamwork` 计划产物 | 需要带依赖和负责人的任务板 |
| 按需隔离 | `/swarm` 工作副本；Antigravity `branch`；DeepSeek Ticket 目录 | 并发写入时使用 Worktree，并提供可审查的集成流程 |
| 制度化的验证角色 | Verifier、Critic、Challenger、Auditor | 需要任务级的验收状态和证据引用 |
| 用户可以直接介入任一成员，审批集中处理 | Claude Code、`/swarm` | 需要成员级对话入口和统一的审批队列 |
| 闲置不耗 token，事件唤醒 | `/swarm`、DeepSeek | 复用 Eidos 的持久等待和续跑意图 |
| 常驻化与主动性 | DeepSeek、dots、Duet | 作为 P3，依赖渠道、定时任务和规则化授权 |
| 成本透明，选择性使用 | 各家均有配额提醒；研究结论 | 团队模式由用户开启，并展示成本 |

## 4. Eidos 现状

| 方面 | 当前事实 | 来源 |
| --- | --- | --- |
| 工具 | 父任务有 `spawn_agent`、`send_message`、`followup_task`、`wait_agents`、`list_agents`、`read_agent_result`、`read_agent_message`、`stop_agent`；子任务只有 `send_message` 和 `read_agent_message` | [协作工具](../../runtime/eidos_runtime/tools/collaboration.py) |
| 类型与模型 | 内置 `default`、`explorer`、`worker`，以及数据目录 `agents/*.toml` 中的自定义类型。目录在 Run 接纳时固定，自定义类型可以指定模型和指令 | [自定义子 Agent](custom-subagents-design.md)、[能力说明](../current/capabilities.md) |
| 生命周期 | `agent_delegations.parent_run_id` 把委派绑定在父 Run 上。父任务完成时等待必需子任务，并取消可选子任务。新 Run 只读取自己的委派，所以子任务不能跨轮复用 | [持久化](../../runtime/eidos_runtime/persistence/collaboration.py)、[engine.py](../../runtime/eidos_runtime/runtime/engine.py) |
| 拓扑 | 子任务只能给父任务发消息（`child_can_only_message_parent`），不能再派生，也不能直接请求用户输入或权限 | 同上 |
| 限额 | 每个父 Run 最多 16 个委派，最多 8 个同时执行；单条消息最多 64 KiB | [domain/collaboration.py](../../runtime/eidos_runtime/domain/collaboration.py) |
| 工作区 | Child Session 复制父 Session 的执行绑定（包括 `worktree_id`），共享父工作区，没有独立 Worktree | [持久化](../../runtime/eidos_runtime/persistence/collaboration.py)、[Git Worktree](../architecture/git-worktree.md) |
| 等待 | 持久的 `waiting_agents` 状态会释放 Worker。`mode=any`、新消息、用户输入和超时都能结束等待 | [Plan 与协作](../architecture/collaboration-planning.md) |
| Desktop | 环境信息中显示子 Agent 列表，右侧工作区提供详情、审批和停止 | [能力说明](../current/capabilities.md) |
| 可复用资产 | Managed Worktree（创建、分叉、Snapshot、Restore）；Review 中的 Diff、Commit、Merge、Rebase；目标模式的续跑意图（只接纳一次，与状态同事务提交）；Session 分叉；钉钉与飞书渠道；定时任务；Hooks；记忆；`auto_review`；评测程序设计（尚未实现） | [Git Worktree](../architecture/git-worktree.md)、[目标模式](../current/features/goals.md)、[渠道](../current/features/channels.md)、[定时任务](../current/features/automations.md)、[评测设计](evaluation-system-design.md) |

## 5. 差距分析

| 能力 | 业界做法 | Eidos 现状 | 差距 |
| --- | --- | --- | --- |
| 成员跨轮存在 | Claude Code 与 Antigravity 的成员在 Session 内持续存在，可以被唤醒 | 绑定父 Run，下一轮不可见 | 高 |
| 成员之间的通信 | 看板或邮箱 | 只有子任务到父任务的单向通道 | 高 |
| 共享任务板 | Claude Code 任务列表；`/teamwork` 计划产物 | Plan 只属于一个 Run，没有负责人和依赖 | 高 |
| 写入隔离与集成 | 按需使用 worktree 或工作副本 | 共享父工作区 | 高 |
| 用户直接指挥成员 | Claude Code、Antigravity 支持 | 只能查看详情、审批和停止 | 中 |
| 验证角色与关口 | Verifier、Critic、Auditor | 可以用自定义类型模拟，但没有任务级验收状态 | 中 |
| 团队成本可见 | 配额提醒 | 只有 Run 级用量，没有团队汇总 | 中 |
| 团队模板 | `/teamwork` 角色体系 | 只有单个 Agent 类型的定义 | 中 |
| 闲置零消耗与事件唤醒 | 各家都支持 | 已有持久等待，还需要新增成员唤醒 | 低 |
| 常驻与主动 | Router、订阅、规则 | 已有渠道、定时任务和目标续跑 | 中，P3 处理 |

## 6. 设计目标与非目标

目标：

- 用户显式开启团队，可以看到成员、任务和看板，并随时介入任一成员。
- 并行写入互不覆盖，集成过程可以审查。
- 闲置成员不占用 Worker，也不消耗 token。
- 复用现有 Runtime Loop、RunSupervisor、Session FIFO、SQLite 与 Event/Outbox、审批和 Sandbox。
- Runtime 重启后，团队、任务、看板和唤醒意图完整恢复，不重放不确定副作用。

非目标：

- 不做没有主持者的自由群聊，即不由系统自动选择下一个发言者。
- 不引入 AutoGen、CrewAI、LangGraph 等框架接管调度（AGENTS.md 第 8 节）。
- 不增加跨 Session 的全局 Run 或 Worker 上限，也不增加跨 Run 的共享工作区副作用门（AGENTS.md 第 13 节）。
- 首期不做嵌套团队、成员再派子 Agent、跨机器成员或 A2A。
- 团队不提升任何成员的权限。模型、模板和成员消息都不能切换审批模式。

## 7. 方案比较

| 方案 | 描述 | 优点 | 问题 | 结论 |
| --- | --- | --- | --- | --- |
| A. 增强子 Agent | 子 Agent 跨轮存在，增加同级消息和可选 Worktree | 改动最小 | 仍以父 Run 为中心，没有共享任务；用户的心智仍是“委派” | 作为 P0 底座 |
| B. Lead、成员、任务板和看板 | 结合 Claude Code 与 `/swarm` 的做法 | 适合编码；可审计；复用多 | 需要新增表、工具和界面 | 推荐作为 P1 |
| C. 里程碑长程团队 | 参考 `/teamwork` | 适合持续数小时的任务 | 依赖 B 的底座，关口设计复杂 | P2，与目标模式结合 |
| D. 常驻 Router–Worker | 参考 DeepSeek | 节省注意力，可以接入事件 | 依赖渠道、授权规则和后台策略 | P3 |
| E. 开放群聊 | AutoGen GroupChat 和群聊类产品 | 形态直观 | 发言权和停止条件难以控制；token 消耗不可预测；责任不清 | 不做；看板加 @ 已覆盖主要价值 |

**通信方式：公开看板优先于私聊邮箱。** Claude Code 使用点对点邮箱，Antigravity `/swarm` 使用全公开看板。Eidos 首期选择单一有序看板加显式收件人，理由有三点：

- 用户能看到全部协作过程，事后可以按顺序审计。
- 一条有序日志比多个邮箱更容易做到持久化、恢复和幂等。
- 投递时只把发给某个成员的消息放进它的上下文，看板再长也不会撑大单个成员的上下文。

代价是看板可能变得嘈杂，需要在界面上按成员和任务过滤。

用户确认后，P1 在公开看板之外增加成员之间的私聊。私聊只推送给收件人，其他成员不能读取，但对用户始终可见。可见范围见[详细设计 4.5 节](agent-team-p0-p1-design.md#45-看板与私聊)。

## 8. 推荐方案：Session 级 Team 协作层

### 8.1 总体结构

```text
Lead Session（用户所在）
  └── Team（SQLite）
        ├── Members ── 每个成员一个 Member Session ── Run（现有 Agent Loop）
        ├── Task Board（任务、依赖、负责人、证据引用）
        ├── Board（有序看板消息与投递回执）
        └── Wake Intents（成员或 Lead 的唤醒意图，只接纳一次）
```

最小调用链：

```text
Renderer → preload typed IPC → Main → RuntimeServer
  → TeamApplication → TeamRepository（SQLite 事务 + Event/Outbox）
  → Wake Intent → RunSupervisor（Session FIFO）→ RuntimeEngine（现有）
  → team_* 工具 → ToolCallRuntime → TeamToolRuntime → TeamRepository
```

原则：

- Team 只保存协调状态。执行事实仍然记录在 Run、Item 和 ToolCall 中，不形成第二个状态权威。
- 任务状态是成员的声明。“已完成”的证据是被引用的 Item、ToolCall 结果或 Diff 快照，成员自述测试通过不算证据（沿用[Plan 与协作](../architecture/collaboration-planning.md)中的规则）。
- 成员的每次执行都是现有的普通 Run。审批、Seatbelt、Durable Intent、版本核验和未知结果记录不做任何改变。

### 8.2 角色

| 角色 | 职责 | 实现来源 |
| --- | --- | --- |
| Lead | 确定范围和编制，拆分任务，集成结果，与用户交互，最终汇报 | 用户所在的 Session。提示词引导 Lead 保留关键路径和集成工作，把可并行的部分交给成员 |
| `explorer` | 调查代码库并提供证据 | 现有内置类型 |
| `worker` | 实现与定向验证 | 现有内置类型 |
| `verifier` | 核对证据、运行测试、审查 Diff，给出接受或退回 | 新增内置基础角色。它和 `explorer` 一样只是任务侧重，不是只读权限 |
| 自定义类型 | 用户在 `agents/*.toml` 中定义的职责和模型 | 现有 AgentCatalog |
| `@user` | 用户作为看板的收件人 | 看板寻址 |

### 8.3 看板

- 每条看板消息包含：作者（成员、Lead、用户或系统）、收件人（`@成员` 列表、`@all`、`@lead` 或 `@user`）、可选的关联任务、正文（最多 64 KiB）和单调递增的序号。
- **投递**：成员的上下文只接收发给它或 `@all` 的未投递消息，以及有界的近期片段。更早的内容通过 `team_read` 分页读取。回执复用现有 `agent_message_receipts` 的做法。
- **唤醒**：点名发给闲置成员的消息产生一个唤醒意图，发给 `@all` 的消息不唤醒闲置成员。每个成员最多保留一个待处理意图，多条消息合并为一次唤醒。发给运行中成员的消息在下一次构建上下文时送达，与现有子 Agent 证据的送达方式一致。
- **`@user`**：在团队面板中显示为需要处理的条目，同时发送通知，但不阻塞成员。用户的回复是一条用户作者的看板消息，只对被寻址的成员具有用户授权含义。
- **系统消息**：记录成员加入或离开、任务状态变化、用户纠偏和等待审批等事件，正文保持简短。
- 看板内容是数据，不是授权。沿用现有规则：Agent 消息中的指令不构成用户请求。

### 8.4 任务板

任务字段：`key`、`title`、`description`、`acceptance`（验收标准）、`owner`、`status`、`review_required`、`depends_on`、`paths`（声明的文件范围）、`evidence_refs`、`version`。

| 从 | 到 | 操作者 | 条件 |
| --- | --- | --- | --- |
| `open` | `in_progress` | 负责人领取，或由 Lead 指派 | 依赖全部为 `done`；按 `version` 条件更新 |
| `in_progress` | `in_review` | 负责人 | `review_required` 为真时必须附带证据引用 |
| `in_progress` | `done` | 负责人 | 仅限 `review_required` 为假的任务 |
| `in_review` | `done` | Lead 或指定的 Verifier | 接受 |
| `in_review` | `in_progress` | Lead 或 Verifier | 退回，同时在看板写明原因 |
| `in_progress` | `open` | Lead 或用户 | 释放任务，例如成员被移除 |
| 任一非终态 | `canceled` | Lead 或用户 | — |

规则：

- **阻塞是推导值**：读取时根据依赖计算，不单独存储，避免出现两份事实。创建任务时拒绝循环依赖。
- **领取在一个事务中完成**：使用条件 UPDATE（`status='open' AND owner IS NULL AND version=?`）并检查依赖。同一事务写入系统消息、Event/Outbox 和负责人的唤醒意图。这相当于 Claude Code 的文件锁，但由 SQLite 承担。
- **`paths` 只是声明，不是锁**。AGENTS.md 第 13 节禁止跨 Run 的共享工作区副作用门。真正防止冲突的是 Worktree 隔离和现有的文件版本核验。
- **Plan 与任务板分开**。Plan 仍是某个 Run 自己的步骤，任务板是团队共享的状态，两者不合并。

### 8.5 工作区与集成

- 成员工作区有两种模式：
  - `shared`：借用 Lead 的执行绑定，与现有子 Agent 相同。
  - `worktree`：基于明确的基准提交新建 Managed Worktree，HEAD 为 detached。
- **默认规则**：`explorer` 和 `verifier` 使用 `shared`。在 Git 项目中有两个及以上成员同时写入时，写入成员使用 `worktree`。Lead 逐个成员决定，编制确认卡片上会显示结果。
- **基准**：创建成员时取 Lead 工作区的快照。复用 Session 分叉的独立 Worktree 路径后，快照包含 Lead 未提交的修改，见[详细设计 3.1 节](agent-team-p0-p1-design.md#31-spawn_agentworkspace)。
- **交付**：成员把任务提交为 `in_review` 时，Runtime 记录该成员 Worktree 相对基准的 Diff 快照，并关联到任务的证据。这是 Runtime 自身的操作，不要求模型自己执行 `git commit`，因此不触碰项目 Git metadata 的写入保护。基准提交与交付的做法见[详细设计 3.1、3.2 节](agent-team-p0-p1-design.md#31-spawn_agentworkspace)。
- **集成**：由 Lead（`team_integrate`）或用户（团队面板）把交付整合到 Lead 工作区。逐个文件校验基准；没有冲突的文本修改用 `git merge-file` 三方合并，冲突直接展示，不写入冲突标记。整合是 Lead 工作区的受控文件写入，规则见[详细设计 3.3 节](agent-team-p0-p1-design.md#33-整合integration)。
- **清理**：成员 Worktree 不参与自动保留清理，也不计入受管 Worktree 上限，由移除成员、解散团队和删除 Lead Session 的流程删除。
- **非 Git 项目**：只能使用 `shared`。确认卡片会提示并发写入的成员共享同一份文件。
- **Windows**：需要在 NTFS 上验证 Worktree 的创建、路径长度和清理。

### 8.6 权限与审批

- Team 保存创建时用户确认的审批模式，即发起编制的 Lead Run 的模式，同时冻结模型和思考档位，供自动续跑和成员使用（见[详细设计 4.8 节](agent-team-p0-p1-design.md#48-权限运行设置与内部-session)）。成员的 Run 使用该模式；扩展快照和个性化设置在成员创建时冻结，与现有子 Agent 一致。
- 只有用户在 Desktop 创建团队时确认过 `full_access`，团队才使用该模式。模板和模型都不能设置它。
- 成员的审批请求带有成员身份。团队面板汇总审批队列，并提供“下一个等待审批”的入口。Lead 不能代替用户批准。`auto_review` 照常评估每个请求，高危请求仍然需要人工确认。
- **Run Grant**：现有子任务可以使用父 Run 仍然有效的 Grant。团队模式下 Lead 的 Run 会结束和更替，所以首期成员不继承 Lead 的 Grant，需要时自行通过审批获得。代价是审批次数会增加。团队级 Grant 放到 P2 评估。
- 成员不能添加成员。首期成员不能直接调用 `request_permissions` 或 `request_user_input`，需要用户决定时通过 `@user` 提出。P2 再评估是否开放。

### 8.7 调度、并发与唤醒

- **限额**：每个团队最多 16 个成员（沿用 `MAX_AGENTS`），最多 8 个成员 Run 同时处于 `running` 或 `finalizing`。等待审批和等待输入的成员不占名额，语义与现有实现一致，只是计数单位从父 Run 改为 Team。这不是 Runtime 的全局配额，Lead 自己的 Run 也不计入。该规则已确认，见第 12 节。
- **唤醒意图**：新增 `team_wakes` 表。每个成员最多一条待处理意图。意图接纳时，在同一事务内创建成员 Run 并写入 Event/Outbox，只接纳一次。这一机制与目标模式的续跑意图相同。
- **Lead 唤醒**：Lead 没有活动 Run 时，如果有消息发给 `@lead`，或有任务进入 `in_review` 或 `done`，系统生成 Lead 唤醒意图，接纳为一次自动 Lead Run，在 Feed 中标注为“团队续跑”。用户自己的输入在 Session FIFO 中优先处理。团队设置提供自动唤醒开关；暂停团队后不再接纳新的唤醒意图。唤醒只由事件触发，不由状态条件触发，具体规则见[详细设计 4.6 节](agent-team-p0-p1-design.md#46-唤醒与调度)。
- **闲置**：没有活动 Run 的成员不占用 Worker，也不消耗 token。
- **Lead 在 Run 内等待**：`team_wait` 复用 `wait_agents` 的持久等待语义，支持 `any` 和 `all`，可以按任务或成员等待。发给 `@lead` 的消息、用户输入和超时都会结束等待。
- **防止消息往返**：成员自己发的消息不会唤醒自己，多条消息合并为一次唤醒。如果两个成员持续互相唤醒而任务没有进展，系统向 Lead 和用户提示，但不自动暂停或结束 Run（AGENTS.md“自动恢复与模型上下文”）。

### 8.8 生命周期、取消与恢复

- **创建**：用户进入团队模式后，Lead 的第一个 Run 通过 `team_propose` 提出编制。Desktop 显示确认卡片，列出成员、类型、模型、工作区模式和并发上限。用户确认后，Team 与成员在同一事务中创建。之后 Lead 可以在限额内增减成员，变化记录到看板。
- **停止**：用户可以停止全部成员。取消会传播到所有活动的成员 Run。只停止 Lead 当前的 Run 不会取消成员，因为成员属于团队，不属于 Lead 的某个 Run。
- **暂停**：暂停后不再接纳唤醒意图，已经运行的 Run 继续执行。
- **解散**：取消全部活动 Run 并等待资源静止。成员 Session 和历史保留，Worktree 按保留策略处理。
- **移除成员**：取消该成员的 Run，把它负责的 `in_progress` 任务释放为 `open`，并在看板记录。
- **重启**：成员 Run 通过现有 Recovery 恢复。待处理的唤醒意图重新评估。看板和回执都已持久化。不确定副作用照常记录，不会重放。
- **取消与迟到结果**：任务状态迁移与成员 Run 的终态在同一事务中检查。迟到的结果不能覆盖取消，也不能推进任务。
- **分叉与删除**：Session 分叉不复制活动团队，这与分叉不复制活动子任务的现有行为一致。删除 Session 时级联清理团队。

### 8.9 上下文

- **成员上下文**依次包括：系统指令、角色与定义快照、团队名册摘要（名称、角色、状态）、成员负责的任务卡片、发给它的未投递消息（有界），以及它自己的历史（照常压缩）。成员上下文不复制 Lead 的对话。
- **Lead 上下文**包括：团队快照（成员、各状态任务数、需要处理的事项）、发给 `@lead` 的未投递消息，以及成员的最终结果（沿用现有子 Agent 证据机制）。
- **压缩**：需要保留团队状态的引用，包括负责的任务、未决定的事项和集成状态（AGENTS.md 第 22 节）。
- **P2**：评估为成员提供有界的上下文分叉，类似 Codex 的 `fork_turns`，用来回应 Cognition 提出的上下文共享问题。

### 8.10 Desktop

- **入口**：`+` → 团队，或使用 `/team <目标>`，交互方式与目标模式一致。P1 中团队模式与目标模式、Plan 模式互斥，叠加放到 P2。
- **编制确认卡片**：显示成员、模型、工作区模式和并发上限，用户确认一次。
- **团队面板**位于右侧工作区，包含三个视图：
  - 看板：可以按成员和任务过滤。
  - 成员：显示状态、模型、工作区、用量和待审批数量。
  - 任务：按状态分组，显示依赖、负责人、证据，并提供接受和退回操作。
- **直接指挥成员**：在 Lead 输入框中 `@成员`，或在成员详情中输入，两者产生同一种事实：一条发给该成员的用户消息，并唤醒该成员。全部用户纠偏都只有这一种事实，Lead 也能在看板中看到。
- **Feed**：Lead 的 Feed 只显示可折叠的团队事件行，不逐条展开看板消息。
- **通知**：`@user` 消息和审批请求会触发通知。
- 界面遵循 AGENTS.md 的设计规则，尽量减少辅助标签和说明，让布局本身表达结构。

### 8.11 工具

团队模式下，Lead 和成员都保留子 Agent 工具，成员不能再创建成员。依据是 Claude Code 和 DeepSeek 的做法，详见[详细设计第 2 节](agent-team-p0-p1-design.md#2-团队模式下的子-agent)。下表只列团队工具，完整参数见[详细设计 4.9 节](agent-team-p0-p1-design.md#49-工具)。

| 工具 | 使用者 | 作用 |
| --- | --- | --- |
| `team_propose` | Lead | 在团队建立前提出编制，由用户确认 |
| `team_add_member` / `team_remove_member` | Lead | 在限额内增减成员，可指定类型和工作区模式 |
| `team_task` | Lead、成员、Verifier | 通过 `op` 创建、指派、领取、提交审查、接受、退回、释放或取消任务；按角色校验权限 |
| `team_post` | 全部 | 向指定收件人发送看板消息 |
| `team_read` | 全部 | 分页读取看板、任务和成员的结果 |
| `team_wait` | Lead | 持久等待任务或成员 |
| `team_integrate` | Lead | 把成员的交付整合到 Lead 工作区 |

所有有副作用的工具都走现有 ToolCall 流程，副作用类型为 `eidos_state`，需要 Durable Intent，并像 `spawn_agent` 一样按 Item ID 幂等重放。

### 8.12 协议与持久化

- **新增表**：`teams`、`team_members`、`team_tasks`、`team_task_deps`、`team_posts`、`team_post_receipts`、`team_wakes`。成员通过外键关联 `sessions`，并可以关联 Worktree。Schema 版本加一，配套 Migration，覆盖新库、升级、失败回滚和外键检查。
- `agent_delegations` 继续服务子 Agent，团队成员不写入该表。
- **新增 RPC 方法**：`team/read`、`team/confirmRoster`、`team/post`、`team/pause`、`team/resume`、`team/stopMembers`、`team/dissolve`、`team/taskUpdate`（用户接受、退回或取消）。所有方法注册到 Method Registry。DTO 由 Python Schema 生成 TypeScript 类型，并同步双端 Fixture。
- **事件**：团队、看板和任务的变化通过 Event/Outbox 发出，与业务状态在同一事务中提交。
- **覆盖范围**：备份、Blob GC 和 Session 删除都需要包含团队数据。

### 8.13 成本与观测

- 成员视图显示每个成员和整个团队的 token 用量及用时。确认卡片只展示模型和并发上限，不给出不可靠的成本预估。
- 日志增加 `team_id` 和 `member_id` 字段。成员 Run 的 Trace 链接到 Lead，复用现有的 Trace 上下文传递。
- 诊断导出包含团队快照。

### 8.14 失败模式与对策

| 失败模式（MAST 类别或论文观察） | 对策 |
| --- | --- |
| 角色漂移、规格不清 | 冻结的定义快照；任务卡片写明范围和验收标准 |
| 重复劳动 | 用条件更新领取任务；名册与任务板进入每个成员的上下文 |
| 决策冲突 | 看板作为共享的决策日志；由 Lead 集成；写入成员使用 Worktree |
| 消息风暴或往返 | 只投递发给自己的消息；合并唤醒；检测到往返时提示，不自动停止 |
| 过早完成、走过场的验证 | `review_required`、证据引用和 Verifier；最终汇报区分“已核实”和“成员声称” |
| 汇合时间与预算不匹配 | 不使用固定汇合时间；用持久唤醒代替长时间睡眠等待 |
| 关键路径瓶颈导致其他成员闲置 | Lead 保留关键路径；闲置成员不耗 token |
| 跨成员的提示注入 | 看板内容视为数据；成员不能授权；`auto_review` 照常评估 |

## 9. 分期路线

| 阶段 | 范围 | 验收要点 | 不包含 |
| --- | --- | --- | --- |
| P0：子 Agent 底座 | worker 可选独立 Worktree，结果以交付进入 Review 并支持三方文本合并（[子 Agent 对照](../research/agent-delegation-alignment.md)已列为下一阶段）；用户直接给子 Agent 发消息；新增 `verifier` 基础角色；子任务用量汇总 | 并发 worker 不互相覆盖；集成冲突可见；Worktree 创建失败时如实报错；删除父 Session 时清理子任务 Worktree | 跨轮存在、看板 |
| P1：Team MVP | 第 8 节的全部内容；实验开关；每个 Session 一个团队；不嵌套 | 第 10 节的可靠性用例；真实窗口中的交互验收 | 模板、目标模式结合、常驻 |
| P2：长程团队与模板 | `teams/*.toml` 模板（编制、角色、Verifier 比例、工作区策略）；团队与目标模式结合；里程碑计划产物；Critic 和 Challenger 关口；Lead 交接（压缩后由继任者接手）；Hook 事件 `TeamMemberIdle` 和 `TeamTaskCompleted`；成员上下文分叉；团队级 Grant；评估是否允许成员再派子 Agent | 持续数小时的任务可以跨重启推进；关口拒绝能退回任务 | 常驻触发 |
| P3：常驻团队 | 渠道和定时任务的事件生成团队任务；Thread 订阅，后续消息直接投递给成员；规则化主动行为（观察、建议、执行，每级对应现有审批模式）；团队长期记忆 | 事件只被接纳一次；主动行为遵守规则和审批；用户能看到全部后台工作 | 跨机器、A2A |

建议每期作为一个 PR，按以下边界分 Commit：Schema、领域与 Repository；工具、调度与唤醒；协议与 Fixture；Desktop。这符合 AGENTS.md 第 13 节和第 24 节的要求。

## 10. 评测与验收

- **对照方式**：基于[评测程序设计](evaluation-system-design.md)，比较单 Agent、子 Agent 和团队三种形态。
- **任务类别**：
  1. 可以并行的多模块功能。
  2. 需要广度展开的调研。
  3. 审查与对抗测试。
  4. 顺序任务和同文件修改，作为负对照。
- **指标**：通过率、墙钟时间、token 用量（包括缓存读取）、用户介入次数、审批次数、集成冲突数、被放弃的任务数。
- **放开条件**：前三类至少有一类明显获益，且负对照没有退化之前，团队模式保持实验状态。
- **可靠性用例**（AGENTS.md 第 27 节）：
  - Runtime 在团队运行中重启。
  - 取消与迟到结果竞争任务状态。
  - 一个成员等待审批时，不阻塞其他成员和其他 Session。
  - 唤醒意图只接纳一次。
  - 两个成员同时领取同一任务。
  - 依赖完成后自动解锁。
  - Worktree 创建失败。
  - 大量闲置成员时，Shutdown 能达到资源静止。
  - Windows 上的 Worktree。

## 11. 风险

- **成本**：并行会成倍增加 token。缓解方式是用户显式开启、展示用量、按成员选择模型，并保持选择性使用。
- **注意力**：团队越大，用户越难跟进。看板过滤、集中审批和 Lead 的汇总可以缓解，但不能消除。
- **复杂度膨胀**：Team 必须保持为一层薄的协调层。一旦出现第二套状态机或调度器，应当回退设计。
- **模型差异**：低价模型作为成员可能拖累质量。成员使用的模型在界面上明确显示，并由评测决定默认值。
- **跨成员注入**：网页或文件中的指令可能经过某个成员的看板消息传给其他成员。看板内容始终视为数据，审批不受影响。

## 12. 已确认的决策

以下问题由用户于 2026-11-18 确认。

| 问题 | 决策 |
| --- | --- |
| 并发计数单位 | 每个 Team 最多同时执行 8 个成员 Run，计数只作用于该 Team，不是全局配额。AGENTS.md 第 13 节已同步 |
| P1 的项目范围 | Git 项目和非 Git 项目都支持。非 Git 项目在 P1 只能共享工作区 |
| 通信方式 | 公开看板加成员之间的私聊 |
| Lead 自动续跑 | 默认开启 |
| 子 Agent 工具 | 用户要求按市面产品决定。结论是保留，不隐藏；成员不能再创建成员 |
| 常驻团队 | 进入路线图，作为 P3 |

各项决策的具体落地见 [Agent Team P0 与 P1 详细设计](agent-team-p0-p1-design.md)。

## 13. 参考资料

外部资料访问于 2026-11-18。第三方资料仅作旁证，以官方文档为准。

Antigravity：

- [Swarm 官方文档](https://antigravity.google/docs/swarm/)
- [Teamwork 官方文档](https://antigravity.google/docs/teamwork/)
- [Subagents 官方文档](https://antigravity.google/docs/subagents/)
- [TestingCatalog 报道](https://www.testingcatalog.com/google-testing-agent-swarm-mode-for-antigravity/)
- [cellcog 解读](https://cellcog.ai/blog/google-antigravity-swarm/)

Claude Code：

- [Agent Teams 官方文档](https://code.claude.com/docs/en/agent-teams)
- [Subagents 官方文档](https://code.claude.com/docs/en/sub-agents)

DeepSeek Super-agent：

- [原型仓库](https://github.com/deepseek-ai/dsh-superagent-prototype)
- [AgentsPulse 解读](https://agentspulse.github.io/tutorials/deepseek-super-agent-architecture/)
- [cellcog 解读](https://cellcog.ai/blog/deepseek-super-agent/)
- [SCMP 报道](https://www.scmp.com/tech/article/3372884/deepseek-unveils-ai-super-agent-coding-race-openai-anthropic)
- [Global Times 报道](https://www.globaltimes.cn/page/202611/1373392.shtml)

Kimi：

- [Agent Swarm 帮助页](https://www.kimi.com/help/agent/agent-swarm)
- [InfoQ 报道](https://www.infoq.com/news/2026/02/kimi-k25-swarm)

Codex：

- [Issue #33885：子线程纠偏](https://github.com/openai/codex/issues/33885)
- [Issue #40211：并发配置说明](https://github.com/openai/codex/issues/40211)
- [第三方 V2 指南](https://codex.danielvaughan.com/2026/04/11/codex-cli-multi-agent-orchestration-v2-complete-guide/)

常驻个人 Agent：

- [dots（DataCamp）](https://www.datacamp.com/blog/openai-dots)
- [dots（Composio）](https://composio.dev/content/a-guide-to-chatgpt-dots-how-openai-s-always-on-agents-work)
- [ChatGPT Wallet（CNBC）](https://www.cnbc.com/2026/11/13/openai-chatgpt-wallet-dot-purchases.html)
- [Claude Duet（TechCrunch）](https://techcrunch.com/2026/10/22/claude-duet-app-is-anthropics-bet-on-agents/)
- [Claude Duet（DataCamp）](https://www.datacamp.com/blog/claude-duet)
- [Muse（TechCrunch）](https://techcrunch.com/2026/09/23/everything-new-coming-to-metas-ai-agent-muse/)
- [Muse（FullStack）](https://www.fullstack.com/blog/inside-metas-muse-permissions-tools-and-personal-ai)

群聊类产品：

- [阿里云 AgentTeams 解读](https://www.yydm.cn/article/1495.html)
- [多 Agent 协作平台评测](https://www.cnblogs.com/A-I-ke/articles/20695653)
- [Bloome](https://bloome.im/features/multi-agent)
- [Alook](https://alook.ai/blog/introducing-alook)

研究：

- [Anthropic：How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Cognition：Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents)
- [MAST：Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657)
- [When Sub-Agents Work in Parallel（arXiv 2610.10263）](https://arxiv.org/pdf/2610.10263)

Eidos 内部：

- [Plan、人机协作与子 Agent](../architecture/collaboration-planning.md)
- [自定义子 Agent 设计](custom-subagents-design.md)
- [子 Agent：Codex 对照](../research/agent-delegation-alignment.md)
- [Git Worktree](../architecture/git-worktree.md)
- [目标模式](../current/features/goals.md)
- [评测程序设计](evaluation-system-design.md)

返回 [设计目录](README.md)。
