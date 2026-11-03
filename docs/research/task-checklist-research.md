# 任务拆解与步骤清单：Codex / Claude Code 调研与 Eidos 方案建议

> 文档分类：调研与对照。本文保留 2026-11-03 调研时的外部产品行为、源码观察和建议，不作为当前实现或最终决策的证明。当前行为请以 [当前说明](../README.md#当前说明)、源码和测试为准。

## 1. 结论

本文研究的能力是：模型在普通执行中把复杂任务拆成有序步骤，逐步推进，完成一步就打勾，用户实时看到进度。它和现有 Plan 模式不是同一件事。Plan 模式是用户显式选择的“先计划、确认后执行”的工作模式；步骤清单是执行期的进度工具，不需要用户切换模式，也不需要确认。

Codex 和 Claude Code 都把这两件事分开实现：

- Codex 用 `update_plan` 做执行期清单。Plan 模式另有 `<proposed_plan>` 产物，并且在 Plan 模式下调用 `update_plan` 会直接报错。
- Claude Code 先用 `TodoWrite`，后来改为 `TaskCreate` / `TaskUpdate` / `TaskGet` / `TaskList`。Plan 模式另用 `EnterPlanMode` / `ExitPlanMode`。

建议 Eidos 采用同类方案：**模型负责拆解和打勾，Runtime 负责校验、持久化和上下文投影，Desktop 只负责展示**。Runtime 不驱动步骤执行器。“按序执行”通过四个机制保证：同一时刻最多一个进行中步骤；提示词要求先勾掉上一步再开始下一步；界面按顺序展示；关键节点重新注入当前清单。P0 只做一个整表替换的工具 `update_todos`，再加一张修订记录表、一张进度卡片和一个随工具出现的指令层。

## 2. 与现有 Plan 模式的边界

### 2.1 Eidos 现状

| 环节 | 现状 | 位置 |
| --- | --- | --- |
| Plan 模式 | `write_plan` 只在 Plan Run 注入；非 Plan Run 调用会返回 `plan_run_required` | [planning.py](../../runtime/eidos_runtime/tools/planning.py)、[run_resources.py:286](../../runtime/eidos_runtime/runtime/run_resources.py:286) |
| 确认后执行 | 执行 Run 绑定已确认版本，计划正文作为 `referenced-plan` 上下文注入 | [builder.py:173](../../runtime/eidos_runtime/context/builder.py:173) |
| 普通执行的拆解 | 只有提示词里一句“需要时规划步骤”，没有结构化进度 | [prompts.py:35](../../runtime/eidos_runtime/model/prompts.py:35) |
| 进度可见性 | 依靠模型写可见正文（PR #119 的进度句要求），没有结构化步骤 | [prompts.py](../../runtime/eidos_runtime/model/prompts.py) `# Communication` |
| 压缩时的待办 | `next_actions` 从 Assistant 文本里用关键词（`next`、`todo`、`下一步`、`接下来`）启发式提取 | [compactor.py:48](../../runtime/eidos_runtime/context/compactor.py:48)、[facts.py:80](../../runtime/eidos_runtime/context/facts.py:80) |

所以缺口是：执行 Run 里没有可被 Runtime 读取、校验、持久化和展示的步骤状态。确认后的计划只作为一段 Markdown 参考材料进入执行 Run，执行过程中看不出做到了哪一步。

### 2.2 两种能力的区分

| 维度 | Plan 模式（已有） | 步骤清单（本文） |
| --- | --- | --- |
| 触发 | 用户通过模式选择或 `/plan` 显式进入 | 模型在普通执行中自行判断，用户也可以要求 |
| 产物 | 带版本的 Markdown 计划文档 | 3～12 条短步骤及其状态 |
| 是否需要用户确认 | 需要，确认后才启动执行 Run | 不需要，随执行实时更新 |
| 是否允许副作用 | 指令约束“确认前不实现”，权限沿用原模式 | 不改变任何权限，只记录进度 |
| 生命周期 | 跨 Run（规划 Run → 执行 Run） | 随执行推进，跨 Run 时由新 Run 决定是否沿用 |
| 两者关系 | 执行 Run 可以把已确认计划的实施步骤转成清单 | 不能在 Plan Run 中使用 |

## 3. Codex 的做法

来源为 `openai/codex` main 分支源码（2026-11-03 读取）。

### 3.1 工具契约

`update_plan` 的参数定义在 [plan_tool.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/plan_tool.rs)，工具描述在 [plan_spec.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/plan_spec.rs)：

```text
update_plan {
  explanation?: string          // 本次更新的说明
  plan: [ { step: string, status: "pending" | "in_progress" | "completed" } ]
}
```

- 每次调用都传完整清单，没有步骤 ID，也没有依赖或负责人字段。
- 工具描述只要求“最多一个 `in_progress`”，这一条由提示词约束，handler 不做校验。
- 源码注释说明，这个结构最早来自一个 VS Code 的 todo MCP。

### 3.2 执行与状态

[plan.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/plan.rs) 的 handler 很薄：

1. 当前回合处于 Plan 模式时返回错误：`update_plan` 是 TODO/checklist 工具，不允许在 Plan 模式使用。
2. 解析参数后发出 `EventMsg::PlanUpdate`，给模型的返回值固定为 `Plan updated`。
3. 它注册为 builtin control tool，由配置项 `update_plan_enabled` 控制是否注入（[spec_plan.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/spec_plan.rs)）。

Codex 不单独保存清单。清单只存在于对话历史中的工具调用参数，以及 rollout 记录的事件里。app-server 向客户端推送 `TurnPlanUpdatedNotification { threadId, turnId, explanation, plan }`（[TS 定义](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/typescript/v2/TurnPlanUpdatedNotification.ts)）。

### 3.3 提示词

基础指令 [default.md](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/prompts/base_instructions/default.md) 用两节写清单规则，要点如下：

- **何时使用**：任务不简单且需要多步、步骤有先后依赖、需求有歧义需要先列目标、需要中间检查点、用户一次提了多件事、用户要求使用 TODO、工作中发现了需要在交付前完成的新步骤。
- **何时不用**：简单或单步请求不用；不要用填充步骤凑数；不要写自己做不到的步骤，例如无法执行的测试。
- **质量**：提示词附了高质量和低质量清单各三个例子。高质量步骤具体、可验证，例如“Parse Markdown via CommonMark library”；低质量步骤笼统，例如“Make styles look good”。每步 5～7 个词。
- **推进**：执行下一条命令前，先确认上一步完成并标记；一次调用可以勾掉多步；全部完成时也要调用一次，把所有步骤标为完成。未完成时始终保持恰好一个 `in_progress`。
- **变更**：中途改计划时重新调用，并在 `explanation` 里说明理由。
- **不重复**：更新后不要在正文里复述整张清单，因为界面已经显示；正文只说变化和下一步。

工具关闭时，[update_plan_instructions.rs](https://github.com/openai/codex/blob/main/codex-rs/prompts/src/update_plan_instructions.rs) 会从 Codex 自带的提示词中删掉这些章节，用户自定义指令不受影响。

### 3.4 与 Plan 模式的隔离

Codex 的 Plan 模式是一种 collaboration mode（[plan.md 模板](https://github.com/openai/codex/blob/main/codex-rs/collaboration-mode-templates/templates/plan.md)）。模板专门写了一节“Plan Mode vs update_plan tool”，说明 `update_plan` 只是清单工具，既不进入也不退出 Plan 模式。Plan 模式的产物是正文中的 `<proposed_plan>` 块，服务端把它解析为独立的 `Plan` 线程条目，并通过 `item/plan/delta` 流式推送（[plan_item.rs 测试](https://github.com/openai/codex/blob/main/codex-rs/app-server/tests/suite/v2/plan_item.rs)）。

Codex 需要专门写这一节，说明两者同名带来过混淆。Eidos 命名时应该避开 `plan`。

## 4. Claude Code 的做法

来源为 Claude Code 官方文档（2026-11-03 读取）：[Tools reference](https://code.claude.com/docs/en/tools-reference)、[Track todos](https://code.claude.com/docs/en/agent-sdk/todo-tracking)、[Agent SDK TypeScript](https://code.claude.com/docs/en/agent-sdk/typescript)、[Interactive mode](https://code.claude.com/docs/en/interactive-mode)、[Agent teams](https://code.claude.com/docs/en/agent-teams)、[Hooks](https://code.claude.com/docs/en/hooks)。

### 4.1 第一代：TodoWrite

```text
TodoWrite { todos: [ { content: string, status: "pending" | "in_progress" | "completed", activeForm: string } ] }
→ { oldTodos: [...], newTodos: [...] }
```

形状和 Codex 一样，也是整表替换。多出的 `activeForm` 是步骤的进行时说法，例如“正在运行测试”，界面在步骤进行中显示它。工具输出带回新旧两版清单，客户端可以直接算出差异。

### 4.2 第二代：Task 工具（取代 TodoWrite 成为默认）

```text
TaskCreate { subject, description, activeForm?, metadata? }            → { task: { id, subject } }
TaskUpdate { taskId, status?: pending|in_progress|completed|deleted,
             subject?, description?, activeForm?, addBlocks?, addBlockedBy?, owner?, metadata? }
                                                                        → { success, taskId, updatedFields, statusChange?, error? }
TaskGet    { taskId }                                                   → 单个任务或 null
TaskList   {}                                                           → { tasks: [ { id, subject, status, owner?, blockedBy } ] }
```

第二代的变化：

- **逐条增改，带 ID**：删除通过 `status: "deleted"` 表达。
- **依赖和负责人**：`addBlocks` / `addBlockedBy` / `owner` 主要服务 agent teams。teammate 从共享清单认领任务，认领时用文件锁防竞争。前置任务完成后，被阻塞的任务会自动解除阻塞。
- **落盘与共享**：清单保存在 `~/.claude/tasks/` 下，会话结束后仍保留。设置 `CLAUDE_CODE_TASK_LIST_ID` 后，多个会话可以共享同一份清单。文档明确说清单在上下文压缩后仍保留。
- **Hook**：`TaskCreated`；`TaskCompleted` 在任务被标为完成时触发，Hook 以退出码 2 结束可以阻止完成并把反馈交给模型，用来强制“测试通过才算完成”之类的标准。
- **模型容错**：SDK 文档提到，Claude Code 执行前会修正接近但不正确的键名，例如把 `id`、`task_id` 改成 `taskId`，把 `active_form` 改成 `activeForm`。可见逐条增改加 ID 的形状对模型的格式准确性要求更高。

### 4.3 第三代：按模型能力取舍（v2.1.268 起）

这些工具现在默认只提供给 Opus 4.7、Sonnet 4.6、Haiku 4.5 及更早的模型。文档给出的理由是：更新的模型不写清单也能跟踪多步任务，而工具定义和提醒会占用上下文。新模型上需要显式开启（`CLAUDE_CODE_ENABLE_TODO_TOOLS=1`、`--allowedTools` 等）。例外是后台会话和云端会话：无论什么模型，工具都保留，因为这类会话通常没有人实时盯着。

这条演变对 Eidos 有两点启发：

- 清单的价值取决于两个因素：模型自身跟踪多步任务的能力，以及是否有人需要实时看到进度。
- 是否启用应该能按模型配置，而不是写死。

### 4.4 Claude 桌面端的任务列表

以下内容来自本次调研会话中 Claude 桌面端（Code 标签页）暴露给模型的工具契约，不是公开文档，可能随版本变化：

- 由宿主应用提供任务列表工具，列表显示在最新一条消息下方。
- 首次调用传完整清单（1～12 步，每步是不超过约 7 个词的祈使短语）。之后可以只传 `advance: true`，表示完成当前步骤并开始下一步；也可以用 `done` / `start` 按序号跳步。计划变化时重新传完整清单。
- 要求与另一个实际工具调用放在同一条消息里发出，不为更新清单单独消耗一轮请求。
- 每次传完整清单时附带 `scope`（首个任务、新任务或延续），宿主据此判断是替换旧清单还是延续。
- 回合结束时由一个收尾调用统一勾掉剩余步骤，并给出回顾。

## 5. 对照

| 维度 | Codex `update_plan` | Claude `TodoWrite` | Claude Task 工具 | Claude 桌面任务列表 |
| --- | --- | --- | --- | --- |
| 调用形状 | 整表替换 | 整表替换 | 逐条增改，带 ID | 整表 + 增量推进 |
| 状态 | 3 种 | 3 种 | 3 种 + deleted | 3 种 |
| 单一进行中 | 仅提示词约束 | 仅提示词约束 | 未查到说明 | 仅提示词约束 |
| 状态位置 | 对话历史 + 事件 | 会话记录 | 磁盘文件，可跨会话 | 宿主应用 |
| 依赖 / 负责人 | 无 | 无 | 有 | 无 |
| 压缩后 | 依赖历史与摘要 | 未查到说明 | 文档明确保留 | 不涉及 |
| 给模型的结果 | 固定 `Plan updated` | 新旧清单 | 结构化结果 | 一行确认 |
| 与 Plan 模式 | Plan 模式下报错 | 独立 | 独立 | 独立 |

共同点：

- 都由模型拆解，宿主不替模型执行步骤。
- 状态都很少，步骤都很短。
- 都要求更新后不要在正文里复述清单。
- 都把勾选视为模型的进度声明。只有 Claude 的 `TaskCompleted` Hook 允许用户自己给“完成”加门槛。

## 6. Eidos 方案建议

### 6.1 原则

1. **模型拥有拆解，Runtime 拥有事实**：清单内容和勾选由模型决定。Runtime 校验结构、持久化修订、生成差异并投影到上下文和界面。勾选只是模型的进度声明，不能作为验证证据。这与 [协作文档](../architecture/collaboration-planning.md) 中“计划文字不能证明操作已完成”一致。
2. **不做 Runtime 驱动的步骤执行器**：让 Runtime 逐步调度“第 N 步”会和 Eidos Loop 形成第二套循环和状态权威。它还会限制模型在发现新情况时调整顺序。需要并行或隔离的步骤，已有 `spawn_agent` 和等待记录可以承担。
3. **不设完成门**：AGENTS.md 要求普通错误、未知结果等不得阻止 Run 完成。清单未勾完同样不能成为 Runtime 阻止完成的理由。用户想要强制门槛时，可以用现有 Stop Hook 的 `decision: block` 自行配置（见 [hooks.md](../current/features/hooks.md)）。
4. **按模型和场景可关闭**：借鉴 Claude Code 第三代的做法，但 Eidos 默认开启。理由是内置 Catalog 主要是 DeepSeek、Kimi、GLM、MiniMax 等模型，而用户明确要求界面打勾。

### 6.2 工具契约（P0）

命名建议使用 `update_todos`，界面名称用“步骤”。`plan` 已被 Plan 模式和 `write_plan` 占用，Codex 的经验表明同名会让模型混淆。`task` 已被子 Agent 委派（`followup_task`）和定时任务占用。

```text
update_todos {
  explanation?: string (≤ 500)                    // 计划变化或删减步骤时说明原因
  items: [ { content: string (1..120), status: "pending" | "in_progress" | "completed" } ] (1..12)
}
```

- **采用整表替换**：整表替换是幂等的，模型不需要记 ID，对 Eidos 内置模型更稳。逐条增改需要的依赖和负责人，在 Eidos 中已由委派与等待记录覆盖。
- **Runtime 校验并返回稳定错误码**，模型据此修正后重试：
  - `todo_items_invalid`：条数或长度超出范围，或有空步骤。
  - `todo_multiple_in_progress`：超过一个 `in_progress`。Codex 和 Claude 只在提示词里约束这一条，Eidos 在 Runtime 里校验，成本很低。
  - `todo_unavailable_in_plan_mode`：Plan Run 不注入这个工具，handler 里再加一道保护，与 `write_plan` 的 `plan_run_required` 对称。
- **给模型的结果**：只返回一行，例如“已更新步骤（2/5 完成）”，加上必要的错误说明。不返回 Hash 或修订指纹，符合 AGENTS.md“模型业务上下文不注入无意义 Hash”的要求。
- **差异**：Runtime 用上一版和本版生成差异（新完成、新开始、删除、新增），写入 Event，供界面做打勾动效和过程行。

`advance` 这类增量写法（Claude 桌面端的做法）可以省输出 Token，但依赖 Runtime 侧的当前状态，模型出错时更难自我修正。建议放到 P2，根据评测再决定。

### 6.3 执行链与调度

- **归类为 `eidos_state` 工具**：它确实写入 Eidos 状态，归入现有类别可以复用 Validate → Prepare → Resolve Permission → Commit Durable Intent → Execute → Verify → Commit Result 的完整阶段，不新增执行类别。`approvalRequired=false`，不需要 Workspace 权限。
- **原子提交**：清单修订、ToolResult 和 Event/Outbox 在同一个 SQLite 事务提交。重启后要么整笔存在，要么整笔不存在，不会出现未知结果，也不需要重放。
- **并行批次**：当前只有 `side_effect == "none"` 的工具能并行（[registry.py:812](../../runtime/eidos_runtime/tools/registry.py:812)），所以 `update_todos` 会把一批只读调用切成前后两段。它本身是瞬时操作，P0 可以接受。如果评测发现模型常把它和大量读取放在同一批，再评估是否增加“与结果同事务提交的控制工具”类别。同一批里的多次更新按模型声明顺序生效，最后一次为准，沿用现有顺序保证。
- **取消与重启**：Run 取消后清单保留原样。界面把 `in_progress` 展示为“已中断”，这只是展示，不改写事实。
- **重复调用**：内容完全相同的连续更新返回“无变化”，交给现有 Loop Guard 处理，不新增自动暂停。
- **子 Run**：每个 Child Session 有自己的清单，Child 不能修改父清单。父 Session 的 Agent 面板可以显示子任务进度（P1）。

### 6.4 持久化

| 方案 | 做法 | 问题 |
| --- | --- | --- |
| A. 从工具调用推导（Codex 方式） | 不建表，取最近一次成功的 `update_todos` 参数 | Renderer 的条目是按需挂载的（`onEnsureItemMounted`），最近一次调用可能尚未加载；上下文构建和压缩每次都要扫历史 |
| B. 独立修订表（建议） | 每次成功调用追加一条修订 | 需要 Schema 迁移 |

建议采用 B：

```text
todo_revisions(
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  source_item_id TEXT NOT NULL,     -- 对应的 ToolCall Item，用于 Rewind / Fork 的谱系
  revision INTEGER NOT NULL,
  items_json TEXT NOT NULL,         -- 已通过 Pydantic 校验，最多 12 条
  explanation TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE(session_id, revision)
)
```

- 当前清单取最新的有效修订。Rewind 后，来源 Item 已失效的修订不再有效，与现有 Item 可见性保持一致。Fork 时把有效的最新修订复制到新 Session，并记录来源。
- Repository 返回冻结的 `TodoList` / `TodoItem` 领域模型，不返回裸字典。
- 按 AGENTS.md 第 16 节提供版本化迁移，并覆盖升级、迁移失败回滚和重启恢复测试。

### 6.5 上下文投影

- **随工具出现的指令层**：参照 `memory-policy` 按工具可用性注入的写法（[instructions.py:221](../../runtime/eidos_runtime/model/instructions.py:221)），新增 `todo-policy` 层，只在工具可用时注入。内容综合 Codex 和 Claude 的经验：
  - 3 步以上、多个子请求、或用户要求时使用；简单任务不用；不写填充步骤，也不写做不到的步骤。
  - 步骤要具体、可验证，用短祈使句。
  - 开始执行前把第一步标为进行中；开始下一步前先勾掉上一步；全部完成后再调用一次。
  - 只有确实完成才标为完成。遇到阻碍时，保持当前步骤为进行中，并新增一步写明需要解决什么。
  - 计划变化时在 `explanation` 中说明原因。
  - 不在正文复述清单，正文只说变化和下一步。这与 PR #119 的可见进度句互补：进度句说明“在做什么”，清单显示“做到哪”。
- **何时注入当前清单**：以 `<system-reminder>` 上下文段 `current-todos` 注入，不在每个采样步都注入。工具调用本身已在历史中，每步重复会浪费 Token，也会破坏前缀缓存。注入时机：
  1. 新 Run 开始时，Session 存在上一次 Run 留下的未完成清单（用户追加消息、上次被取消）。由模型决定沿用还是替换。
  2. 上下文压缩后。`CompactSummary.next_actions` 改为优先使用结构化清单，不再只依赖 `_NEXT_ACTION_MARKERS` 关键词。清单条目标注为“模型进度声明”，与现有“Assistant 自述带未验证标记”的规则一致。
  3. 工具历史投影已经省略了最近一次 `update_todos` 调用时。
- **Plan 模式衔接**：执行 Run 绑定已确认计划时，在执行模式指令中加一句：先把计划的实施步骤转成 `update_todos`。由模型转写，Runtime 不解析计划 Markdown。

### 6.6 收尾时是否提醒未完成项

| 选项 | 行为 | 评价 |
| --- | --- | --- |
| 不提醒（Codex） | 只靠提示词 | 最简单；较弱的模型可能漏掉步骤 |
| 提醒一次（建议作为 P1 可选） | 模型准备结束 Run，但仍有 `pending` / `in_progress` 步骤时，Runtime 注入一次提醒并继续采样；每个 Run 最多一次 | 能补救漏项；不阻止完成；提醒必须说明“如果是有意留下，请更新清单或在最终回答中说明”，避免模型为了勾完而硬做 |
| 阻止完成 | 类似 `TaskCompleted` 门 | 违反 AGENTS.md 的自动恢复与完成规则，不采用；用户可以用 Stop Hook 自行实现 |

### 6.7 Desktop 界面

- **进度卡片**：Run 活跃时固定在 Composer 上方，显示“2/5”、当前步骤（突出显示）、已完成（打勾）和待办步骤。窄窗口下默认只显示当前步骤，可以展开。状态要有文字，不能只靠图标区分。
- **Feed**：`update_todos` 不作为普通工具行展示（参照 `write_plan` 在 [ExecutionFeed.tsx:381](../../desktop/renderer/src/components/ExecutionFeed.tsx:381) 的特殊处理），改为一行轻量的过程记录，例如“完成：迁移 Schema；开始：补充测试”。
- **结束后**：最终清单折叠进该 Run 的结果区，历史中可以看到计划做什么、做完了什么。
- **子任务**：Agent 面板显示每个 Child 的进度（P1）。
- **渠道与定时任务**：没人盯着的场景更需要进度。Claude Code 也因此在后台和云端会话中始终保留清单工具。可以在渠道回复或定时任务结果中附带清单摘要（P2）。
- **验收**：按 AGENTS.md 第 31.1 节提供交互截图，覆盖 1100px 和 420px、进行中、取消和错误状态。

### 6.8 模型适配与评测

- 在 Model Profile 中增加 `todo_tool` 能力开关，来源顺序遵循“用户配置 > Pydantic AI Model Profile > Eidos Provider Preset > 保守默认值”，默认开启。设置页提供全局开关。
- 在 `docs/evaluations/` 建一组多步任务：多个子请求、需要先调查再修改、中途需要改计划。对比开启和关闭时的子请求遗漏率、完成率、工具轮次和 Token。是否在某些模型上默认关闭，用这组数据决定。

## 7. 分阶段

| 阶段 | 范围 |
| --- | --- |
| P0 | `update_todos` 工具与校验；`todo_revisions` 表与迁移；Event/Outbox 与读取协议（可以并入 `planning.read` 或单独方法）；`todo-policy` 指令层；压缩摘要使用结构化清单；进度卡片与 Feed 过程行 |
| P1 | 新 Run 开始时注入未完成清单；收尾提醒（如采用）；执行已确认计划时转写清单；Agent 面板显示子任务进度；Model Profile 开关 |
| P2 | 渠道与定时任务的进度摘要；`advance` 增量写法；`TodoUpdated` / `TodoCompleted` Hook；评测与默认值调整 |

P0 涉及 DB Schema、协议和 UI。AGENTS.md 第 24 节禁止一个 PR 同时包含目录重组、Async 迁移、DB Schema、Protocol 和 UI 修改；P0 不涉及前两项。P0 建议放在同一 PR，按“Runtime 工具与持久化”“协议与 Fixture”“Desktop 展示”分成三个 Commit。

### P0 需要的测试

- 校验：条数和长度越界、多个进行中、空步骤、Plan 模式拒绝、错误后修正重试。
- 原子性：在结果提交前后注入故障，确认修订、ToolResult 和 Event 要么一起存在，要么一起不存在；重启后不重放。
- 取消：进行中取消，迟到结果不覆盖取消状态，清单保留。
- Rewind / Fork：回退后当前清单回到对应修订；Fork 后新 Session 有独立清单和来源。
- 压缩：压缩后模型上下文仍有当前清单，并带“模型进度声明”标注。
- 并行批次：同批多次更新按声明顺序生效；与只读调用混排时结果顺序正确。
- 子 Run：Child 的清单与父清单隔离。
- 协议：Python 与 TypeScript 双端 Fixture 一致。
- Desktop：卡片的各种状态、窄窗口、Feed 过程行，以及 Run 结束后的折叠。

## 8. 待确认的决策

1. 工具名和界面名：`update_todos` / “步骤”，或其他名称。
2. 清单作用域：建议按 Session 保存、由每次 Run 更新，并在新 Run 开始时注入未完成清单；另一种是每个 Run 独立。
3. 收尾提醒：不提醒，还是每个 Run 最多提醒一次。
4. Feed 中是否完全隐藏 `update_todos`，只保留进度卡片。
5. 默认开启范围：所有模型默认开启，还是按 Provider Preset 区分。

## 9. 来源

- Codex 源码（main 分支，2026-11-03）：[plan_tool.rs](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/plan_tool.rs)、[plan.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/plan.rs)、[plan_spec.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/plan_spec.rs)、[spec_plan.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/spec_plan.rs)、[default.md](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/prompts/base_instructions/default.md)、[update_plan_instructions.rs](https://github.com/openai/codex/blob/main/codex-rs/prompts/src/update_plan_instructions.rs)、[Plan 模式模板](https://github.com/openai/codex/blob/main/codex-rs/collaboration-mode-templates/templates/plan.md)、[TurnPlanUpdatedNotification.ts](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/typescript/v2/TurnPlanUpdatedNotification.ts)、[plan_item.rs](https://github.com/openai/codex/blob/main/codex-rs/app-server/tests/suite/v2/plan_item.rs)
- Claude Code 文档（2026-11-03）：[Tools reference](https://code.claude.com/docs/en/tools-reference)、[Track todos](https://code.claude.com/docs/en/agent-sdk/todo-tracking)、[Agent SDK TypeScript](https://code.claude.com/docs/en/agent-sdk/typescript)、[Interactive mode：Task list](https://code.claude.com/docs/en/interactive-mode)、[Agent teams](https://code.claude.com/docs/en/agent-teams)、[Hooks：TaskCompleted](https://code.claude.com/docs/en/hooks)
- Claude 桌面端任务列表：本次调研会话中观察到的工具契约，无公开文档。

返回 [调研目录](README.md)。
