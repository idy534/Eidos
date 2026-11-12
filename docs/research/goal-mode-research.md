# 目标模式 `/goal`：Codex / Claude Code 调研与 Eidos 方案建议

> 文档分类：调研与对照。本文保留 2026-11-03 调研时的外部产品行为、源码观察和建议，不作为当前实现或最终决策的证明。当前行为请以 [当前说明](../README.md#当前说明)、源码和测试为准。

> 2026-11-07 用户交互修订：当前行为见[目标模式](../current/features/goals.md)。目标模式从“＋”菜单或 `/goal` 开启，输入框在访问模式右边显示目标标签，并与 Plan 互斥。用户直接输入目标，权限沿用输入框选择。旧设置弹窗、单独创建入口、目标子命令和 Token 预算已移除。首条真实用户消息、Run 和目标一起提交。下文继续保留原调研与阶段建议，冲突内容以当前行为为准。

## 1. 结论

本文研究的能力是：用户给出一个可验证的目标，Agent 不需要用户逐轮催促，持续工作到目标完成、确实受阻或用尽用户给的预算为止。普通 Run 在模型认为“这一轮做完了”时结束；目标模式在 Run 结束后再判断“目标是否达成”，没达成就继续。

Codex 和 Claude Code 都已经提供 `/goal`，但两者的完成判定方式不同：

- **Codex**：目标是线程级持久状态，有 6 种状态和可选 Token 预算。每个回合结束、线程空闲时，宿主自动发起一个新回合，并注入一段续跑提示词。是否完成由干活的模型自己判断：模型先按要求逐条审计，再调用 `update_goal` 声明完成或受阻。宿主另有空回合和连续失败熔断。
- **Claude Code**：`/goal` 是一个会话级的 prompt 型 Stop Hook。每个回合结束后，由一个独立的小模型阅读对话记录，给出“未达成 / 已达成 / 不可能”三种判定。干活的模型不能自己宣布完成。

建议 Eidos 采用 Codex 的骨架，再吸收 Claude Code 的独立核验：

1. **目标是 Session 级持久事实**。它只决定“是否再接纳一个 Run”，不改变 Run 状态机，也不成为第二套循环。
2. **续跑单位是新 Run**。每个续跑 Run 重新固定模型、扩展和权限快照，用户输入天然优先，Checkpoint 粒度保持不变。
3. **完成判定分层**。P0 由模型逐条审计后通过 `update_goal` 声明，Runtime 对声明做结构校验；P1 增加可选的独立核验模型；用户需要确定性门槛时，沿用现有 Stop Hook。
4. **自治必须有界**。空转熔断、Run 失败转为受阻、可选 Token 预算，这些都是目标层的停止条件，不改变 AGENTS.md 对 Run 内“普通错误不得自动暂停”的规定。
5. **目标不改变权限**。只有用户能创建目标，模型没有创建工具。目标沿用创建时的审批模式，完全访问仍需 Desktop 确认。

## 2. 能力边界与 Eidos 现状

### 2.1 Eidos 现状

| 环节 | 现状 | 位置 |
| --- | --- | --- |
| Run 完成 | 模型给出无工具调用的最终回答时，`LoopDecisionEngine` 返回 `COMPLETE`，Engine 提交完成事实 | [decision.py](../../runtime/eidos_runtime/runtime/decision.py)、[engine.py:1281](../../runtime/eidos_runtime/runtime/engine.py:1281) |
| 完成前拦截 | 完成前会等待子 Run，并执行 Stop Hook。Hook 返回 `decision: block` 时，同一个 Run 继续采样 | [engine.py:1308](../../runtime/eidos_runtime/runtime/engine.py:1308)、[hooks.md](../current/features/hooks.md) |
| 循环收敛 | LoopGuard 用语义 fingerprint 判断无进展，返回 `PAUSE` 后由 RunFinalizer 收尾，Run 以 `stopped` 结束 | [loop_guard.py](../../runtime/eidos_runtime/runtime/loop_guard.py)、[engine.py:1268](../../runtime/eidos_runtime/runtime/engine.py:1268) |
| 长任务 | Run 内有安全点、用户暂停 / 恢复和 Segment rollover，但范围只在一个 Run 内 | [long_task.py](../../runtime/eidos_runtime/runtime/long_task.py) |
| 跨 Run 自动执行 | 定时任务按时间接纳普通 Run，并用 `automation_run_origins` 记录来源 | [automations.md](../current/features/automations.md) |
| 快捷指令 | Composer 只有 `/plan` 一个命令 | [InputPicker.tsx:11](../../desktop/renderer/src/components/InputPicker.tsx:11) |
| 压缩 | AGENTS.md 第 22 节要求压缩保留“当前目标”，但目前只能从对话里推断目标 | [compactor.py:48](../../runtime/eidos_runtime/context/compactor.py:48) |

用户今天已经可以自己写一个 Stop Hook，用 `decision: block` 逼模型继续，也就是社区所说的 Ralph loop。但这种做法缺少很多东西：

- 每个会话临时设定目标时，需要编辑配置文件并重新信任。
- 没有目标状态、暂停 / 继续、预算和进度展示。
- Runtime 重启后无法恢复。
- 所有续跑都挤在同一个 Run 里，Run 的快照、Checkpoint 和终态语义都会被拉长。

### 2.2 与相邻能力的区分

| 能力 | 下一轮何时开始 | 何时停止 | 状态范围 |
| --- | --- | --- | --- |
| 普通 Run | 用户发送消息 | 模型给出最终回答 | 一个 Run |
| Plan 模式 | 用户确认计划后启动执行 Run | 执行 Run 结束 | 规划 Run → 执行 Run |
| 步骤清单（[调研](task-checklist-research.md)） | 不触发新 Run，只记录进度 | 随 Run 结束 | Session 内的进度声明 |
| Stop Hook | 同一个 Run 继续采样 | 脚本决定 | 所有匹配的 Run |
| 定时任务 | 到达计划时间 | 用户暂停或删除计划 | 计划配置 |
| **目标模式（本文）** | 上一个 Run 结束且 Session 空闲 | 目标完成、受阻、用户暂停或清除、预算用尽 | Session 级目标记录 |

目标模式和步骤清单互补：目标定义“做到什么程度才算完”，清单显示“当前做到哪一步”。Codex 的续跑提示词也会在 `update_plan` 可用时，要求模型用它展示和目标绑定的步骤。

## 3. Codex 的做法

来源为 `openai/codex` main 分支源码，提交 `fbf5f0d`（2026-11-03 读取）。目标功能是独立扩展 crate `codex-rs/ext/goal`，通过线程、回合、工具和 Token 用量的生命周期回调接入核心循环。功能开关 `goals` 当前为 Stable，默认开启。

### 3.1 用户入口

| 命令 | 行为 |
| --- | --- |
| `/goal <objective>` | 创建目标；已有目标时替换 |
| `/goal` | 查看目标、状态、用量和预算 |
| `/goal edit` | 打开编辑器修改目标 |
| `/goal pause` / `/goal resume` | 把状态设为 `paused` / `active` |
| `/goal clear` | 删除目标 |

app-server 对外提供 `thread/goal/set`、`thread/goal/get`、`thread/goal/clear`，并推送 `thread/goal/updated` 和 `thread/goal/cleared`。目标正文最多 4,000 字符。TUI 遇到超长目标或附带图片时，会把正文写成 `goal-objective.md`，目标改成“先读取这个文件再继续”。

### 3.2 数据模型与状态

目标保存在独立的 SQLite 表中，每个线程最多一条：

```text
thread_goals(
  thread_id PK, goal_id, objective,
  status: active | paused | blocked | usage_limited | budget_limited | complete,
  token_budget?, tokens_used, time_used_seconds, created_at_ms, updated_at_ms
)
thread_goal_continuation_deferrals(thread_id PK)   -- 存在时暂不自动续跑
```

| 状态 | 谁能设置 | 含义 |
| --- | --- | --- |
| `active` | 用户创建或恢复 | 宿主会自动续跑 |
| `paused` | 用户；模型只能在用户明确要求时设置 | 停止续跑 |
| `blocked` | 模型在审计后设置；宿主在回合出错或熔断时设置 | 没有用户输入或外部变化就无法推进 |
| `usage_limited` | 宿主（账号用量上限） | 停止续跑 |
| `budget_limited` | 宿主（Token 预算用尽） | 当前回合收尾，不再续跑 |
| `complete` | 模型在审计后设置 | 目标达成 |

线程被 Fork 时，新线程复制目标快照，同时写入一条 deferral。新线程要等用户发起下一个回合才会续跑，避免两个线程同时自动推进同一个目标。

### 3.3 续跑机制

续跑的关键路径在 [runtime.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/src/runtime.rs) 的 `continue_if_idle`：

1. 线程进入空闲时触发 `on_thread_idle`。
2. 取得每线程一把的 `goal_state_lock`，防止用户同时修改或清除目标。
3. 检查 deferral，再从 SQLite 读取目标；状态不是 `active` 就退出。
4. 调用 `start_turn_if_idle` 发起新回合，输入是一段带 `goal` 来源标注的内部上下文，回合标记 `turn_trigger: "goal"`。线程已经有用户输入或其他工作时，宿主拒绝这次提交。
5. 记下自动续跑回合的 ID，供空回合熔断判断。

用户在目标进行中发起的回合同样计入目标：`on_turn_start` 发现目标为 `active` 或 `budget_limited` 时，把这个回合标为目标回合。Plan 模式的回合不计入目标。

### 3.4 模型工具

[spec.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/src/spec.rs) 定义了三个工具：

```text
get_goal    {}                                          → 目标、状态、预算和用量
create_goal { objective, token_budget? }                → 只能在用户或系统指令明确要求时调用；已有未完成目标时失败
update_goal { status: "complete" | "blocked" | "paused" }
```

工具描述写明了几条约束：

- `paused` 只能在用户明确要求时使用，模型不能自行暂停。
- `complete` 只能在目标确实达成、没有剩余工作时使用。不能因为预算快用完或准备停下来就标完成。
- `blocked` 只能在同一个阻碍连续出现至少三个目标回合后使用。用户恢复目标后，计数重新开始。困难、缓慢、不确定或希望澄清，都不是受阻的理由。
- 模型不能恢复目标，也不能设置预算或用量相关状态，这些由用户或宿主控制。

这些约束只写在工具描述和提示词里，handler 不校验回合数。

### 3.5 续跑提示词

每个续跑回合注入 [continuation.md](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/templates/goals/continuation.md)。它是整个功能的核心，要点如下：

- **目标是数据**：目标放在 `<objective>` 标签里，并声明它是用户提供的任务数据，不是更高优先级的指令。
- **不缩小目标**：本回合做不完时，推进真实目标并保持目标为进行中，不要把成功重新定义成一个更小、更容易的任务。
- **以证据为准**：以当前工作区和外部状态为准。之前的对话只用于定位，使用前要重新检查当前状态。
- **无进展自查**：把上一个目标回合归类为“有进展 / 已核实的等待 / 无进展”。只复述状态或只写计划不算进展。等待必须针对一个当前确认仍存活的进程、任务或工具句柄。观察超时不等于任务结束，不能因此重启任务。
- **忠实于目标**：不要因为更容易通过现有测试，就换成更窄、更安全或更好测的方案。
- **完成审计**：先假设完成尚未得到证明。从目标和引用的文件中逐条提取需求；为每条需求找到权威证据，并检查文件、命令输出、测试、PR 状态或运行行为；证据范围要和需求范围一致；不确定或间接的证据视为未完成。审计必须证明完成，而不是“没发现明显遗漏”。
- **受阻审计**：重复写明三回合门槛，并要求门槛满足后立即调用 `update_goal`，不要一直报告受阻却让目标保持进行中。

另外两个模板分别在预算用尽时要求本回合尽快收尾、说明剩余工作，以及在用户修改目标时说明新目标取代旧目标。

### 3.6 预算与用量核算

- `token_budget` 可选，默认没有预算。只有用户明确要求时，模型才可以在 `create_goal` 中设置。
- 核算按回合进行：回合开始记下 Token 基线，工具完成、回合结束或中止时，把增量写入 SQLite。用一个 permit 串行化，避免并发工具回调重复计费。
- 子 Agent 的 Token 用量累计到根线程的目标。
- 用量超过预算时，状态改为 `budget_limited`，并在当前回合中只注入一次收尾提示。回合不会被强行中止。

### 3.7 熔断

[accounting.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/src/accounting.rs) 和 [extension.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/src/extension.rs) 实现了四种由宿主触发的停止：

| 条件 | 结果 |
| --- | --- |
| 连续 3 个自动续跑回合没有任何活动（无工具、无文字、无推理摘要） | `blocked` |
| 连续 3 个回合有命令执行失败、且没有任何成功的工具调用 | `blocked` |
| 回合以不可重试错误结束，或重试已耗尽 | `blocked` |
| 账号用量上限 | `usage_limited` |

源码注释说明，回合出错就阻塞目标，是为了防止自动续跑反复撞同一个错误、持续消耗 Token，压缩失败就是一个例子。

### 3.8 其他细节

- **审查子 Agent 看不到目标工具**；其他子 Agent 的用量计入根目标。
- **压缩保留**：用户设定或修改目标会写成一条带宿主标注的用户消息，压缩时保留。超过 700 字节的目标整体省略并标记，源码注释的理由是“截断可能把限制变成授权”。
- **授权语义**：用户设定的目标会保留为用户授权证据，投影给审查 worker 操作的 reviewer 参考。只有宿主标注的消息才算，模型或工具输出里的同样文字不算；被省略的超长目标会明确标记为“缺失的用户指令”。

## 4. Claude Code 的做法

来源为 Claude Code 官方文档（2026-11-03 读取）：[Keep Claude working toward a goal](https://code.claude.com/docs/en/goal)、[Hooks guide](https://code.claude.com/docs/en/hooks-guide)。

### 4.1 用户入口

- `/goal <condition>`：设置完成条件，最多 4,000 字符。每个会话只有一个目标，新目标替换旧目标。设置后立即以条件为指令开始一个回合，不需要另发消息。
- `/goal`：查看条件、已运行时长、已评估回合数、Token 用量和评估器最近一次的理由。
- `/goal clear`：清除目标，`stop`、`off`、`reset`、`none`、`cancel` 都是别名。`/clear` 开启新对话也会清除目标。
- 非交互模式可以用 `claude -p "/goal ..."` 一次跑到底。
- 界面显示 `◎ /goal active` 和运行时长，每次判定都在记录中显示，可以展开查看理由。

### 4.2 评估机制

文档原文说 `/goal` 是“a wrapper around a session-scoped prompt-based Stop hook”。每个回合结束后，Claude Code 把条件和到目前为止的对话发给配置的小模型（默认 Haiku 档位），模型返回三种判定之一，并附简短理由：

| 判定 | 结果 |
| --- | --- |
| 未达成 | Claude 继续工作，理由作为下一回合的指引 |
| 已达成 | 清除目标，记录达成条目 |
| 不可能 | 清除目标，记录失败条目和理由 |

评估器不调用工具，只能判断 Claude 已经在对话中展示的内容。文档因此建议条件写成 Claude 的输出能证明的形式：一个可测量的终态、一个明确的检查方法（例如“`npm test` 退出码为 0”），以及必须保持不变的约束。

需要限制时长时，文档建议直接在条件里写“或 20 个回合后停止”，由 Claude 每回合汇报进度，评估器根据对话判断。

### 4.3 防空转与错误

- 如果 Claude 连续几个回合只回应评估器、没有调用任何工具，Claude Code 停止循环并警告，目标仍保留，用户下一条消息后恢复评估。底层是 Stop Hook 的通用上限：连续 8 次阻止且期间没有工具调用时，强制允许停止。
- **需要用户处理的错误清除目标**：认证失败（宿主托管凭据时除外）、额度耗尽、自动压缩也无法解决的上下文溢出、模型不可用。
- **其他错误保留目标**：服务过载、连接中断等可能自行恢复的错误自动重试，三次后暂停；限流、用量上限或 Hook 结束回合时直接暂停。

### 4.4 后台工作与签到

- 回合结束时，如果子 Agent 或后台 Shell 仍在运行，跳过本回合评估，等后台工作结束、结果作为新回合送达后再评估。
- 后台工作让目标等待 30 分钟后，触发一次签到：列出运行中的任务，让 Claude 读取输出，判断继续等、修复还是停止。之后间隔翻倍，最长为首次间隔的 4 倍。会话空闲时最多主动发起 3 次签到。

### 4.5 恢复、权限与前提

- 恢复会话时，仍处于进行中的目标会被恢复，但回合数、计时和 Token 基线重置；已达成或已清除的目标不恢复。
- 目标不改变权限模式。手动模式下工具调用仍会请求批准；要无人值守，需要配合 auto mode。文档的说法是：auto mode 去掉逐工具确认，`/goal` 去掉逐回合确认。
- 因为评估器属于 Hook 系统，`/goal` 遵循与 Hook 相同的工作区信任规则。`disableAllHooks` 或仅允许托管 Hook 时不可用，并会明确告诉用户原因。

文档还对比了三种让会话持续运行的方式：`/goal` 在上一回合结束时继续、由模型判定是否达成；`/loop` 按时间间隔重复；Stop Hook 由用户脚本决定，适用于配置范围内的所有会话。

## 5. 对照

| 维度 | Codex | Claude Code |
| --- | --- | --- |
| 实现形态 | 独立扩展，挂接生命周期回调 | 会话级 prompt 型 Stop Hook |
| 持久化 | SQLite 表，每线程一条 | 会话记录，恢复时还原条件 |
| 续跑单位 | 新回合，由宿主在空闲时发起 | Stop Hook 阻止停止，继续下一回合 |
| 完成判定 | 干活的模型逐条审计后调用 `update_goal` | 独立小模型阅读对话记录后判定 |
| 判定者能否用工具 | 能，审计时读文件、跑测试 | 不能，只读对话 |
| 受阻 / 不可能 | 模型在同一阻碍连续 3 回合后标 `blocked` | 评估器判定 `impossible` |
| 预算 | 结构化 Token 预算 | 写在条件文字里，由模型判断 |
| 空转保护 | 3 个空回合、3 个连续执行失败回合 | 连续 8 次阻止且无工具调用 |
| 出错 | 回合出错即 `blocked` | 按错误类型清除、重试或暂停 |
| 后台工作 | 未单独处理 | 推迟评估，定期签到 |
| 模型能否创建目标 | 能，但仅限用户明确要求 | 不能 |
| 权限 | 不改变 | 不改变 |

两者的共同点：

- 每个会话只有一个目标，正文上限都是 4,000 字符。
- 目标不改变权限模式，无人值守需要另外放宽审批。
- 都有防空转机制，并都允许用户随时打断或清除。
- 目标正文都被当作数据，而不是更高优先级的指令。

两者最大的分歧是**谁来判定完成**。Codex 让干活的模型自审：它掌握完整上下文和工具，能主动取证，但可能自我宽容，所以用很长的审计提示词约束它。Claude Code 用独立模型判定：不受执行者自我评价影响，但看不到对话以外的事实，只能相信对话里出现的输出。两种方式可以组合：执行模型取证并声明，独立模型核验声明和证据。

## 6. Eidos 方案建议

### 6.1 原则

1. **目标不接管 Run**：目标记录只回答“这个 Session 是否还要接纳下一个续跑 Run”。Run 的状态、完成、取消、恢复和 Finalizer 语义全部不变。这符合 AGENTS.md“不得在任意中间层增加第二个状态权威”的要求。
2. **完成是带证据的声明**：模型的完成声明和独立核验结果都是记录，不是 Runtime 对用户目标的证明。这与 [Durable Agent Loop](../architecture/runtime-loop.md) 中“Run 成功不等于达到用户目标”的说法一致。
3. **只有用户能设定目标**：模型没有创建工具。这比 Codex 更严格，可以防止项目文件或工具输出里的注入文字让 Agent 给自己开启无限循环。
4. **用户输入优先**：Session 已有排队 Run 时不接纳续跑。用户消息总是先执行，目标在其后继续。
5. **停止条件属于目标层**：AGENTS.md 规定普通工具错误、限流和重试预算不得自动暂停或结束 Run，这条规则继续适用于每个续跑 Run。目标层的熔断只决定“不再接纳下一个 Run”，不会中止正在执行的 Run。用户设置的预算是用户的明确选择，不属于“固定步数或时长作为自动终止条件”。

### 6.2 续跑方式

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| A. 同一 Run 内续跑（Claude Code 方式） | 在 `before_completion` 处判断目标，未达成就 `REBUILD_CONTEXT` | 改动最小，可以复用 Stop Hook 的续跑路径。但一个 Run 可能持续数小时：模型、扩展和权限快照无法更新，Checkpoint 粒度变粗，`succeeded` 的含义变模糊，重启后整个 Run 都是 `interrupted` |
| **B. 新的续跑 Run（Codex 方式，建议）** | Run 正常结束后，在同一 Session 接纳一个来源为目标的新 Run | 每个 Run 有界、快照新鲜；用户输入天然通过 Session FIFO 优先；重启和取消边界清楚。需要新表、接纳逻辑和事件 |
| C. 复用定时任务 | 用定时任务周期性触发 | 时间驱动，和“上一轮结束就继续”的语义不符；不采用 |

建议采用 B。

### 6.3 数据模型

```text
session_goals(
  id TEXT PRIMARY KEY,                 -- goal_id
  session_id TEXT NOT NULL REFERENCES sessions(id),
  objective TEXT NOT NULL,             -- 1..4000 字符
  status TEXT NOT NULL CHECK (status IN (
    'active', 'paused', 'blocked', 'budget_limited', 'complete', 'cleared'
  )),
  status_reason TEXT,                  -- 稳定原因码，见 6.4
  status_detail TEXT,                  -- 模型或核验给出的说明，有界
  approval_mode TEXT NOT NULL,         -- 创建时固定，续跑 Run 沿用
  full_access_confirmation TEXT,       -- 仅完全访问，沿用定时任务的确认版本
  token_budget INTEGER,                -- 可选
  consecutive_idle_runs INTEGER NOT NULL DEFAULT 0,
  blocked_audit_runs INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL,           -- 条件更新与 expectedRevision
  created_at, updated_at, settled_at
)
CREATE UNIQUE INDEX one_open_goal_per_session ON session_goals(session_id)
  WHERE status IN ('active', 'paused', 'blocked', 'budget_limited');

goal_runs(
  run_id TEXT PRIMARY KEY REFERENCES runs(id),
  goal_id TEXT NOT NULL REFERENCES session_goals(id),
  trigger TEXT NOT NULL CHECK (trigger IN ('goal_set', 'continuation', 'user_input', 'resume')),
  sequence INTEGER NOT NULL
)
```

- 用 `cleared` 代替删除行，保留历史，便于审计和 Desktop 展示已完成的目标。
- **用量不另设计数器**：Token 用量从 `goal_runs` 关联的 Run 及其子 Run 的 `model_attempts.usage_json` 汇总。SQLite 继续是唯一事实来源，不像 Codex 那样在内存中维护基线再增量写入。如果汇总成本成为问题，再在 Run 终态事务中维护缓存列。
- 运行时长由关联 Run 的 `total_effective_ms` 汇总。
- Repository 返回冻结的 `SessionGoal` 领域模型。按 AGENTS.md 第 16 节提供版本化迁移，覆盖升级、迁移失败回滚和重启恢复测试。

### 6.4 状态与转换

| 事件 | 条件 | 新状态 / 原因 | 是否接纳续跑 |
| --- | --- | --- | --- |
| 用户设定目标 | 无未结目标，或替换旧目标 | `active` | 立即以目标为输入接纳第一个 Run |
| 目标 Run 正常完成 | 目标仍为 `active`、版本未变、Session 无排队 Run | 不变 | 接纳续跑 Run |
| 模型 `update_goal(complete)` | 通过 6.6 的校验 | `complete` / `model_declared` | 否 |
| 模型 `update_goal(blocked)` | 达到受阻审计门槛 | `blocked` / `model_blocked` | 否 |
| 用户暂停、或模型按用户要求暂停 | — | `paused` / `user_paused` | 否；进行中的 Run 继续跑完 |
| 用户取消目标 Run | — | `paused` / `run_canceled` | 否 |
| Run 被 LoopGuard 停止 | Run 终态为 `stopped` | `blocked` / `no_progress` | 否 |
| Run 失败 | Run 终态为 `failed` | `blocked` / `run_failed`，附错误码 | 否 |
| 连续 3 个续跑 Run 空转 | 见 6.7 | `blocked` / `idle_runs` | 否 |
| Token 用量超过预算 | — | `budget_limited` | 否；当前 Run 收到一次收尾提醒 |
| Runtime 重启 | 目标 Run 被标为 `interrupted` | `paused` / `runtime_restarted`（建议，见第 7 节） | 否，等待用户继续 |
| 用户继续 | 状态为 `paused` / `blocked` / `budget_limited` | `active`，受阻审计计数清零 | 接纳续跑 Run |
| 用户修改目标 | — | 保持原状态，版本加一 | 活跃 Run 在下一采样边界收到“目标已更新” |
| 用户清除 | — | `cleared` | 否 |

Run 在 `waiting_approval`、`waiting_input` 或 `waiting_agents` 时，目标保持 `active`，Session 被占用，不会接纳续跑。审批等待不阻塞其他 Session，这一点沿用现有规则。

### 6.5 续跑接纳

续跑接纳参照定时任务“准备与接纳分开、接纳事务内复核”的做法：

1. **完成事务写入续跑意图**：目标 Run 的终态事务中，Runtime 根据目标状态写入一条续跑意图，或按 6.4 更新目标状态。终态、目标状态和 Event/Outbox 一起提交。这样不会出现“Run 已结束、续跑丢失”的中间状态。
2. **事务外准备**：Supervisor 管理的协调器读取续跑意图，通过普通 Run 创建链路准备模型、扩展和权限快照。
3. **接纳事务复核**：入队时在同一事务中复核目标 `revision`、`active` 状态和 Session 空闲。任何一项不满足就放弃这次续跑。用户暂停、清除或发送消息与续跑同时发生时，以先提交者为准，取消不会被迟到的续跑覆盖。
4. **续跑 Run 的输入**：`runs.user_input` 写入空串，`goal_runs.trigger = 'continuation'`。ContextBuilder 注入 `goal-continuation` 上下文段（见 6.8），Feed 不显示伪造的用户气泡。

用户在续跑 Run 执行中发送消息时，沿用现有引导输入，在下一采样边界被消费。用户在 Session 空闲时发送消息，会排在续跑之前；用户 Run 结束后，目标继续。

子 Run 沿用现有语义：父 Run 完成前等待子 Run，因此不需要 Claude Code 那样的“后台工作推迟评估”。子 Run 不注入目标工具，但用量计入父目标。

### 6.6 完成判定

**P0：模型自审声明。** 只有目标处于 `active`，且当前是父 Run 时，才注入 `update_goal`：

```text
update_goal {
  status: "complete" | "blocked" | "paused"
  summary: string (1..2000)                                       // 完成依据、阻碍说明或暂停原因
  evidence?: [ { requirement: string (≤300), proof: string (≤600) } ] (≤20)  // 完成时逐条列出
}
```

Runtime 能低成本校验的约束，在 Runtime 中校验，并返回稳定错误码，模型据此修正：

| 错误码 | 条件 | 说明 |
| --- | --- | --- |
| `goal_not_active` | 目标不是 `active` | 例如用户刚刚暂停 |
| `goal_blocked_too_early` | 本次激活以来的目标 Run 少于 3 个 | 把 Codex 只写在提示词里的三回合门槛变成 Runtime 校验。模型应结束本轮并说明阻碍，目标会在下一轮继续 |
| `goal_pause_not_requested` | 当前 Run 没有消费任何用户输入 | 模型只有在用户明确要求时才能暂停，这一点 Runtime 可以通过本 Run 是否消费了用户输入来判断 |
| `goal_evidence_required` | `complete` 时 `evidence` 为空 | 迫使模型逐条审计 |

调用成功后，目标状态、ToolResult 和 Event/Outbox 在同一事务提交。工具归入 `eidos_state` 类别，不需要审批。

**P1：可选的独立核验。** 用户创建目标时可以开启“独立核验”。模型声明 `complete` 时，Runtime 发起一次有界、无工具的 Structured Output 请求，形式参照自动审查和 RunFinalizer。输入包括目标、模型的 summary 与 evidence、近期工具结果投影；输出为 `met` / `not_met` / `impossible` 和理由：

- `met`：目标转为 `complete`，记录核验来源。
- `not_met`：目标保持 `active`，理由注入下一个续跑 Run。
- `impossible`：目标转为 `blocked` / `verifier_impossible`。

只在完成声明时核验，而不像 Claude Code 每回合都评估。原因是在方案 B 中，Run 结束却没有声明完成，本来就会触发续跑，每回合评估只会增加成本。核验模型使用 Model Profile 选择，来源顺序遵循 AGENTS.md 第 12 节。

**确定性门槛**：用户需要“测试通过才算完成”时，现有 Stop Hook 已经可以做到。是否增加每个目标单独配置的验收命令，留到 P2 评估。

### 6.7 防空转与预算

- **空转熔断**：续跑 Run 既没有调用工具，也没有调用 `update_goal`，就把 `consecutive_idle_runs` 加一；任何工具调用都会清零。连续 3 次时，目标转为 `blocked` / `idle_runs`。计数存在目标行中，重启不会清零。
- **Run 内收敛**：继续由 LoopGuard 处理。LoopGuard 停止 Run 后，目标转为 `blocked`，不会自动再开一个 Run 去撞同一个问题。
- **连续执行失败熔断**（P1）：参照 Codex，连续 3 个目标 Run 中有 Shell 失败、且没有任何成功工具调用时，转为 `blocked`。先通过评测确认空转熔断和 LoopGuard 是否已经足够。
- **Token 预算**：可选。每个 Step 边界从 SQLite 汇总用量。超出后状态改为 `budget_limited`，在下一采样边界注入一次收尾提醒，要求总结进展、剩余工作和下一步，然后让 Run 正常结束。不中止正在执行的工具，也不取消 Run。
- Eidos 用户使用自己的 API Key，费用是真实支出。建议创建目标时的界面提供预算输入框，但默认不设预算（见第 7 节）。

### 6.8 上下文投影

- **`goal-policy` 指令层**：目标为 `active` 时注入，写法参照 `memory-policy` 按条件注入（[instructions.py:223](../../runtime/eidos_runtime/model/instructions.py:223)）。内容改写自 Codex 的续跑模板：不缩小目标、以当前状态为证据、无进展自查、忠实于目标、完成审计和受阻审计。Eidos 内置的 DeepSeek、Kimi、GLM、MiniMax 等模型更需要明确的完成审计要求，这一层应该进入评测（见 6.12）。
- **`goal-continuation` 上下文段**：每个续跑 Run 开始时注入，包括放在 `<objective>` 标签中、标明为用户任务数据的目标正文，第几轮续跑，已用 Token 与预算，上一轮的结束方式，以及核验未通过时的理由。不注入 goal_id、Hash 或内部指纹。
- **目标修改**：活跃 Run 在下一采样边界收到 `goal-objective-updated` 段，说明新目标取代旧目标。
- **压缩**：`CompactSummary` 优先使用结构化目标，满足 AGENTS.md 第 22 节“保留当前目标”的要求。不能像 Codex 那样截断目标；目标超过压缩预算时整体保留，由压缩其他内容让出空间。
- **步骤清单**：清单工具可用时，`goal-policy` 建议用它展示与目标绑定的步骤，并提醒“更新清单不能代替实际工作”。

### 6.9 权限与安全

- 目标不改变权限。创建时选择审批模式并固定在目标行中，续跑 Run 沿用。手动模式下目标会停在审批等待处，目标卡片显示“等待审批”；无人值守建议使用替我审批。
- 完全访问沿用定时任务的方式：Desktop 显示危险确认，确认版本随目标保存，每次接纳续跑 Run 时传给既有的完全访问校验。模型、项目文件和工具参数都不能切换目标的审批模式。
- 目标正文是用户数据。Codex 会把用户设定的目标作为用户授权证据交给 reviewer 参考，Eidos 可以在 P1 让 auto_review 读取目标正文帮助判断风险。但目标正文只能作为参考，不能当作高危操作的授权，高危人工确认继续保留。
- 日志按用户输入处理目标正文，不记录到诊断日志的普通字段中。

### 6.10 与现有能力的关系

- **Plan 模式**：目标 Run 始终使用 execute 模式，Plan Run 不注入目标工具。P1 可以在计划确认处增加“作为目标执行”：目标正文写成“完成已确认计划第 N 版的全部实施步骤并满足其验收标准”，执行 Run 同时绑定计划和目标。
- **Stop Hook**：顺序不变。Run 完成前先执行用户的 Stop Hook，Hook 要求继续时同一个 Run 继续；Run 真正完成后，才由目标决定是否接纳续跑。P2 可以在 Hook 输入中增加目标字段。
- **定时任务**：两者独立。定时任务按时间触发，目标按完成情况触发。
- **Fork**：复制未结目标，新 Session 中的状态设为 `paused` / `forked`，用户确认后才继续，避免两个 Session 同时在同一工作区自动推进。这与 Codex 的 deferral 效果相同。
- **Rewind**：Rewind 本身要求没有活动 Run，目标不随 Rewind 回退。如果回退到目标设定之前，Desktop 提示用户是否清除目标。
- **Git Worktree**：长时间自动修改建议在 Worktree 中进行。Desktop 创建目标时给出提示，但不强制。

### 6.11 协议与 Desktop

- **RPC**：`goal/set`、`goal/get`、`goal/update`（暂停、继续、修改正文或预算，带 `expectedRevision`）、`goal/clear`，以及 `goal/updated` 通知。新方法注册到 Method Registry，Python DTO 生成 TypeScript 类型和校验器，同步双端 Fixture。
- **Composer**：在 [InputPicker.tsx](../../desktop/renderer/src/components/InputPicker.tsx) 中 `/plan` 旁边增加 `/goal`。`/goal <目标>` 打开确认面板，包括目标、审批模式、可选预算和独立核验开关；`/goal` 不带参数时显示状态；支持 `pause`、`resume`、`edit`、`clear`。
- **目标卡片**：固定在 Composer 上方，与步骤清单卡片共用区域。显示目标（默认折叠）、文字状态标签、已运行时长、续跑轮数、Token 用量与预算、最近的原因，以及暂停、继续、编辑、清除按钮。窄窗口下只显示状态行。
- **Feed**：续跑 Run 标为“目标续跑 · 第 N 轮”。目标事件显示为轻量过程行，例如“已暂停”“已受阻：连续三轮无进展”“已完成”。完成时展开 summary 和逐条 evidence，并标注“模型声明”或“已独立核验”。
- **侧栏**：目标为 `active` 的 Session 显示图标，用户一眼能看出哪些会话在自动工作。
- **通知**（P1）：目标完成或受阻时发送系统通知。
- **验收**：按 AGENTS.md 第 31.1 节提供交互截图，覆盖 1100px 和 420px，以及进行中、等待审批、受阻、预算用尽和完成状态。

### 6.12 评测

在 `docs/evaluations/` 建一组长任务：模块 API 迁移、按设计文档逐条实现、清空一批失败测试，以及容易提前宣布完成的任务。对比以下指标：

- 提前完成率：声明完成，但人工复核发现未完成。
- 误判受阻率：标记受阻，但实际还有可行路径。
- 完成率、续跑轮数、Token 和总耗时。
- 自审与自审加独立核验的差异。
- 各内置模型在 `goal-policy` 下的表现。

是否把独立核验设为默认，用这组数据决定。

## 7. 分阶段

| 阶段 | 范围 |
| --- | --- |
| P0 | `session_goals` / `goal_runs` 表与迁移；`goal/*` RPC、事件与 Fixture；Run 终态 → 目标状态转换；续跑意图与接纳；`update_goal` 与 Runtime 校验；空转熔断；Token 预算与收尾提醒；`goal-policy` 与续跑上下文段；压缩保留目标；`/goal` 命令、目标卡片与 Feed 标注 |
| P1 | 独立核验；连续执行失败熔断；计划确认后作为目标执行；auto_review 参考目标；侧栏图标与系统通知；Fork / Rewind 交互完善 |
| P2 | 每个目标的验收命令；渠道中的 `/goal`；Hook 输入增加目标字段；评测与默认值调整 |

P0 涉及 DB Schema、协议和 UI，不涉及目录重组和 Async 迁移，符合 AGENTS.md 第 24 节。建议放在同一个 PR，按“Runtime 目标与持久化”“续跑接纳与上下文”“协议与 Fixture”“Desktop 展示”分成四个 Commit。

### P0 需要的测试

- **状态转换**：6.4 表中每一行，包括非法转换的拒绝。
- **接纳竞争**：用户暂停、清除或发送消息与续跑接纳同时发生时，只有一方生效；取消不被迟到的续跑覆盖；同一 Session 不会出现两个续跑 Run。
- **原子性**：在 Run 终态事务和接纳事务前后注入故障，确认终态、目标状态、续跑意图和 Event 要么一起存在，要么一起不存在。
- **重启**：目标 Run 执行中重启，Run 标为 `interrupted`，目标按第 7 节的决定处理，不重放不确定副作用。
- **工具校验**：四个错误码，以及错误后修正重试。
- **熔断**：连续空转 Run 计数、工具调用清零、重启后计数保留；LoopGuard 停止和 Run 失败转为 `blocked`。
- **预算**：Run 中途越过预算时只提醒一次，Run 正常结束，不再接纳续跑；子 Run 用量计入父目标。
- **审批等待**：目标 Run 等待审批时，其他 Session 的 Run 不受影响，本 Session 不接纳续跑。
- **压缩**：压缩后模型上下文仍包含完整目标和状态。
- **协议**：Python 与 TypeScript 双端 Fixture 一致。
- **Desktop**：目标卡片各状态、窄窗口、Feed 标注、`/goal` 子命令。

## 8. 待确认的决策

1. **续跑单位**：新 Run（建议）还是同一 Run 内续跑。
2. **完成判定**：P0 只做模型自审（建议），还是把独立核验也放进 P0。
3. **重启后的行为**：目标转为暂停，等用户点继续（建议，避免完全访问模式在用户不知情时自动恢复工作）；还是像 Codex 一样在会话恢复后自动续跑。
4. **默认预算**：默认不设预算并提供输入框（建议）；还是设置默认上限。
5. **取消目标 Run**：目标转为暂停（建议）还是直接清除。
6. **模型能否创建目标**：不能（建议）；还是像 Codex 一样允许在用户明确要求时创建。
7. **受阻门槛**：Runtime 强制 3 轮（建议）；还是只在提示词中约束。
8. **命名**：命令 `/goal`、界面名“目标”、工具 `update_goal`。

## 9. 来源

- Codex 源码（main 分支，提交 `fbf5f0d`，2026-11-03）：
  - 目标扩展：[extension.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/src/extension.rs)、[runtime.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/src/runtime.rs)、[accounting.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/src/accounting.rs)、[spec.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/src/spec.rs)、[steering.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/src/steering.rs)
  - 提示词模板：[continuation.md](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/templates/goals/continuation.md)、[budget_limit.md](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/templates/goals/budget_limit.md)、[objective_updated.md](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/ext/goal/templates/goals/objective_updated.md)
  - 协议与持久化：[protocol.rs（ThreadGoalStatus）](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/protocol/src/protocol.rs)、[0001_thread_goals.sql](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/state/goals_migrations/0001_thread_goals.sql)、[0002_thread_goal_continuation_deferrals.sql](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/state/goals_migrations/0002_thread_goal_continuation_deferrals.sql)、[goals.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/state/src/runtime/goals.rs)、[thread_fork_goal.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/app-server/src/request_processors/thread_fork_goal.rs)、[common.rs（thread/goal 方法）](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/app-server-protocol/src/protocol/common.rs)
  - 上下文与授权：[user_goal.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/core/src/context/user_goal.rs)、[user_authorization.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/core/src/agent/control/user_authorization.rs)
  - TUI：[slash_dispatch.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/tui/src/chatwidget/slash_dispatch.rs)、[goal_files.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/tui/src/goal_files.rs)、[features/lib.rs](https://github.com/openai/codex/blob/fbf5f0d3a716d08c254d3e4941d5b12eaff5afe7/codex-rs/features/src/lib.rs)
- Claude Code 文档（2026-11-03）：[Keep Claude working toward a goal](https://code.claude.com/docs/en/goal)、[Hooks guide：Prompt-based hooks 与 Stop hook block cap](https://code.claude.com/docs/en/hooks-guide)
- 第三方背景（仅作版本时间线参考，未作为事实依据）：[Codex CLI /goal Workflows（v0.128）](https://codex.danielvaughan.com/2026/05/01/codex-cli-goal-workflows-persistent-long-horizon-task-execution/)

返回 [调研目录](README.md)。
