# 目标模式

用户在输入框的“＋”菜单中点击“目标”，就会开启目标模式。输入框会在访问模式右边显示目标图标和“目标模式”标签。用户也可以选择 `/goal` 命令。两种入口使用相同的模式切换。界面不会显示单独的“设定目标”入口或目标设置弹窗。

用户直接在输入框填写目标，再点击发送。目标使用输入框中选中的模型、思考设置和访问模式。用户可以沿用普通输入的文件、扩展和历史引用。目标正文支持 1 到 4,000 个字符，引用沿用普通输入的 20 项上限。目标模式没有 Token 预算。

## 输入与模式切换

| 操作 | 当前行为 |
| --- | --- |
| “＋” → 目标 | 输入框显示目标模式标签，用户继续编辑正文。系统不会提前创建 Session 或目标。 |
| `/goal` | 输入框进入目标模式。命令本身不会创建目标或启动 Run。 |
| `/goal <目标>` | 用户发送后，Runtime 把后面的文字作为目标正文。 |
| 点击“目标模式”标签 | 输入框退出目标模式。有活动目标时，Runtime 先暂停后续续跑。当前 Run 继续完成。 |
| 切换到 Plan 模式 | Runtime 先暂停活动目标，再切换输入框标签。两种模式互斥。 |
| 在活动目标中发送补充消息 | Runtime 接纳普通用户 Run，并继续原目标。补充消息不会把目标缩小为这条消息。 |
| 目标从进行中变为暂停、受阻、完成或清除 | 输入框自动退出目标模式。之后的普通消息按普通 Run 执行，不会新建目标。 |
| 在目标暂停、受阻、完成或清除后，重新进入目标模式并发送新目标 | Runtime 创建新的目标。旧目标和执行历史继续保留。 |

已有目标的状态卡片贴在输入框上方，收起时只占一行：左侧状态图标（进行中带脉冲动画），中间是单行目标正文，非进行中状态显示文字标签，右侧用图标显示轮次、Token 和有效执行时长，并提供暂停或继续图标按钮。窄窗口只保留轮次。展开后显示完整正文、状态原因、逐条完成证据和“清除目标”。图标都有悬停说明和无障碍名称。状态区域不提供创建入口和配置表单。用户需要替换活动目标时，可以先退出目标模式，再进入目标模式并发送新正文。用户继续旧目标时，Runtime 会使用输入框当前选择的模型和已确认访问模式。

## 执行与完成

SQLite 是目标、执行来源、完成候选和续跑意图的事实来源。用户的第一条目标消息沿用 `run/start`，通过 `goalMode` 和可选的 `goalRevision` 表达创建目标的意图。Runtime 在同一个事务内保存真实用户消息、Run、目标、引用身份和 Event / Outbox。请求失败时，这些事实一起回滚。用户不会看到一条空的人类消息。客户端用同一个 `operationId` 重发首条目标消息或补充消息时，Runtime 返回原 Run，不会重复创建目标；幂等记录包含 `goalMode` 和 `goalRevision`，普通请求的记录保持不变。

第一轮 Run 属于真实用户输入。后续自动 Run 只保留来源标记。Feed 会给自动 Run 标注“目标续跑 · 第 N 轮”。自动 Run 不提供“重新回答”和“编辑并重新发送”。用户输入与自动续跑继续使用同一个 Session FIFO。用户队列已经存在时，Runtime 先处理用户队列。第一条目标消息仍在排队时，用户编辑正文会一起更新目标版本；用户删除消息会清除目标。用户将消息转为当前任务的补充消息时，Runtime 保留后续目标意图，并继续遵循当前任务的取消和版本规则。

目标只决定是否接纳下一次普通 Run。既有 Run 状态机、Supervisor、模型与扩展快照、取消、权限、工具执行和恢复继续负责实际执行。Run 终态、目标状态、续跑意图和 Event / Outbox 在同一个事务中提交。续跑意图只能接纳一次。Runtime 在最终接纳时重新检查目标版本、激活阶段、状态和 Session 占用。目标模式中的补充输入也会在事务中检查目标版本；已排队的用户输入会使旧完成候选失效。目标不会增加跨 Session 的执行配额或工作区锁。

父执行 Run 可以通过 `update_goal` 声明 `complete` 或 `blocked`。模型不能创建、暂停、继续、替换或清除目标。子 Agent、Plan Run、渠道 Run 和定时任务 Run 不获得目标控制工具。

完成声明需要 summary 和逐条 requirement / proof。单次声明最多保存 20 条证据。声明继续经过 Tool Controller 的准备、Durable Intent 和结果提交。候选声明与 ToolResult 一起保存。Runtime 只在必要子任务结束、Stop Hook 放行、取消和新输入检查通过、Run 正常成功时确认目标状态。Stop Hook 要求继续、新用户输入或后续工具执行会使旧声明失效。旧目标或旧激活阶段的请求不能覆盖新目标状态。

Runtime 对 `blocked` 只校验本次激活至少已有三个目标 Run。模型仍需判断同一阻碍是否连续存在，以及是否确实没有可行路径。Runtime 不把轮数当作语义证明。完成声明也不是确定性验收。用户需要确定性条件时，可以使用既有 Stop Hook。

## 状态、用量与恢复

目标有 `active`、`paused`、`blocked`、`complete` 和 `cleared` 五种状态。

- Run 失败或被 LoopGuard 停止时，Runtime 把当前目标转为受阻。之后排队的普通用户输入不会推迟受阻。这轮 Run 执行期间，用户已经发送补充消息或继续目标时，旧激活阶段的失败不会覆盖新的激活阶段。
- 用户取消目标 Run 时，Runtime 暂停目标。旧激活阶段的迟到取消或失败不能覆盖后来的继续操作。
- 目标连续三轮没有新的有效工具结果时，Runtime 停止续跑。重复读取、失败工具和目标状态工具不会直接清零计数。这个检查使用有界结果记录，不是完整的任务进展判断。
- 用量统计汇总关联父 Run 和子 Run 的模型输入与输出 Token。缓存 Token 不重复加算。统计不包含压缩和自动审批。Provider 未返回完整用量时，界面明确说明统计不完整。用量只用于展示，不触发目标停止。

ContextBuilder 从 SQLite 单独投影完整目标与当前状态。`goal-policy` 提供审计规则，`goal-current` 提供用户任务数据。目标正文不进入高优先级指令。上下文压缩和恢复继续保留完整目标。历史摘要不会成为目标的第二个权威。

用户发送或继续目标时，模型和访问模式来自输入框。Runtime 在接纳请求时冻结这些设置，供后续续跑使用。完全访问继续使用已有访问模式选择器的危险确认。确认覆盖当前目标的后续执行。模型参数、项目文件和工具结果不能切换模式。自动续跑不进入自动记忆学习，也不提供记忆写工具；第一轮真实用户输入保留普通用户 Run 的记忆规则。

Runtime 重启时会暂停活动目标，并废弃待接纳意图。Runtime 会取消已入队或可恢复的自动目标 Run，再沿用既有恢复处理。系统不会自动重放不确定副作用。真实用户 Run 保留原有恢复语义。用户需要手动继续目标。Checkpoint Rewind 会暂停目标。当前 Fork 不复制目标。

## 数据兼容

Schema v51 增加 `session_goals`、`goal_runs` 和 `goal_continuations`。Schema v52 移除旧目标 JSON 中的 Token 预算，并为每个目标 Run 保存冻结的引用身份。自动续跑从这份记录读取引用，不生成空的人类消息。旧版因预算耗尽而停止的目标会转为暂停，不会自动恢复执行。历史预算事件仍作为历史事实保存，并投影为暂停记录。

普通启动、旧数据分库和备份恢复都接入 v50 → v51 → v52 迁移。用户不能直接用旧 Runtime 打开新版本数据。用户回退应用版本时，需要恢复兼容的升级前备份。代码回退不等于数据降级。

目标管理 RPC 仅保留 `goal/get`、`goal/update`（暂停与继续）和 `goal/clear`。旧 `goal/set`、目标编辑表单、`/goal pause|resume|edit|clear` 子命令和预算接口均已移除。创建与替换目标统一使用普通输入提交。

## 当前范围与验证入口

当前版本仍为目标模式 P0。独立核验模型、计划确认后绑定目标、目标专用验收命令、目标历史管理页、侧栏图标、系统通知和渠道命令尚未实现。Feed 只为自动续跑 Run 和目标状态变化（继续、暂停、受阻、完成、清除）显示带图标的单行标记，数据来自关联 Run 的最近 20 条目标事件；目标设定和正文修改不再单独占一行。真实模型的提前完成率、误判受阻率和长期成本尚未验证。

```bash
uv run --locked pytest -q runtime/tests/test_goals.py runtime/tests/test_run_completion.py
node scripts/generate-goal-contracts.mjs
node scripts/generate-planning-contracts.mjs
pnpm build:main
node --test dist/main/runtime-client-goals.test.js
pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/components/goals/GoalPanel.behavior.test.tsx desktop/renderer/src/components/goals/GoalFeed.behavior.test.tsx desktop/renderer/src/components/Composer.behavior.test.tsx desktop/renderer/src/components/InputPicker.behavior.test.tsx
node_modules/.bin/electron scripts/test-goals.cjs
```

`EIDOS_GOAL_SCREENSHOTS` 可以指定隔离页面的截图目录。页面使用生产 Composer、InputPicker 和目标状态组件。页面检查使用模拟数据，不读取日常 Eidos 数据。自动化测试和隔离截图不代表真实 Provider 的长任务验收。
