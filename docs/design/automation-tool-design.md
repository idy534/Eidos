# automation：Session 内定时任务工具详细设计 V3

> 文档分类：设计与实施记录。本文保留记录时的方案、分支状态和验证范围。当前行为请以 [当前说明](../README.md#当前说明)、源码和测试为准。

修订日期：2026-10-15。本文保留实现前的设计依据，并补充 [本地定时任务方案 V3](scheduled-tasks-research-and-design.md) 和 [源码与缺口复核](../research/scheduled-tasks-gap-review.md)。当前实现、协议和验证边界以 [当前本地定时任务](../current/features/automations.md)、生产代码与测试为准。

**模型只使用一个定时任务工具 `automation`。工具通过 mode 处理查看、创建、更新、删除和时间预览。工具默认 deferred，模型通过 tool_search 激活后使用。创建时传 sessionId 表示复用已有 Session；不传表示每次触发新建 Session。**

本版只在本机运行，不检测漏跑，不补跑。周期任务使用受限五段 Cron；固定间隔只允许 10 分钟、30 分钟和 1 小时。一次性任务使用 at。

## 1. 单工具 Interface

| mode | 主要参数 | 结果 |
| --- | --- | --- |
| view | id，或分页与筛选条件 | 有 id 时返回详情；无 id 时返回列表 |
| create | kind、name、prompt、schedule；可选 status、model、approvalMode、sessionId、projectId、execution | 系统保存计划，返回真实 ID、版本、归属与未来时间 |
| update | id、expectedRevision、changes | 系统只修改明确提交的字段 |
| delete | id、expectedRevision | 系统软删除计划，保留历史与文件 |
| preview | kind、schedule | 系统只计算时间，不保存或执行任务 |

暂停和恢复使用 update 的 status，不新增 pause / resume mode。首版模型工具不增加立即执行 mode；页面继续使用 schedule/runNow。系统只维护一份工具定义、一个 Runtime Adapter 和一套应用模块。

参数采用封闭根对象。mode 和 kind 是两个不同概念：mode 表示管理操作；kind 只表示 at / cron。系统不把本地执行、会话方式或 heartbeat 放入 kind。

公开 Function Tool Schema 使用根对象，并提供简短的字段说明与示例。Runtime 用按 mode 区分的严格类型检查必填、禁填和互斥字段。系统不要求所有 Provider 支持根级 oneOf，也不把一个含全部可选字段的裸字典传入业务模块。实现需要验证单定义不超过现有 32 KiB 上限。

## 2. 按需激活

系统初始只提供一个短提示：

> 用户要求管理定时任务时，先使用 tool_search 精确搜索 automation。等待搜索结果，在后续 Step 取得完整定义后调用。用户只是调研或讨论方案时，不创建任务。查看任务使用 mode=view；时间不确定时可以先 preview。

```text
用户提出要求
  → tool_search(query="automation", limit=1)
  → Runtime 保存当前 Run 的激活记录
  → 下一 Step 提供完整 automation 定义
  → 模型按 mode 调用
  → Runtime 校验来源、目标、参数与版本
  → Runtime 返回真实保存或查询结果
```

当前 tool_search 只返回名称、描述、来源、分数等摘要，不直接附带 inputSchema。完整 Schema 由下一次模型请求中的 Tool Definition 提供。未激活调用沿用 tool_not_activated，不执行工具、创建计划或提交 Intent。[搜索](../../runtime/eidos_runtime/tools/search.py)、[快照](../../runtime/eidos_runtime/tools/registry.py#L653)

激活按 Run 持久化，不成为 Session 永久状态。当前激活历史最多保留 32 个名称；快照最多接受 16 个激活工具，总定义最多 128 KiB。工具因预算被移出后，模型可以再次精确搜索。已在快照中的工具无需重复搜索。

子 Agent 的目录不注册 automation。周期自动 Run 只注册能暂停自身计划的受限同名定义。一次性任务入队后已经停用，其自动 Run 不再需要自暂停定义。系统按 builtin / eidos.automation 来源和可信 Runtime 识别该工具，不按名字片段识别权限或卡片。

## 3. view：详情与列表

查看详情：

```json
{
  "mode": "view",
  "id": "6b53c59a-5e72-4db2-bcf2-c61b2f264573"
}
```

id 使用 Runtime 返回的真实 ID。本版使用规范 UUID，不把任务名称当作唯一 ID。用户表格中的 ping 可以表达示例对象，但模型不能把名称直接当成已存在的 UUID。名称查询通过 query 返回候选。

查找当前项目的任务：

```json
{
  "mode": "view",
  "scope": "current_project",
  "query": "部署",
  "limit": 10
}
```

| 字段 | 规则 |
| --- | --- |
| id | 有值时只读取详情，禁止混用列表筛选和分页参数 |
| scope | 无 id 时可选 current_session / current_project / all；默认 current_session |
| query | 可选名称关键词，最多 256 字符；系统不按模糊名称直接修改 |
| status | 可选 ACTIVE / PAUSED，仅用于列表筛选 |
| cursor | 可选不透明分页游标，最多 2 KiB |
| limit | 默认 10，范围 1 至 20 |

current_session 包含以当前 Session 为执行目标的计划，以及在当前 Session 创建的新会话计划。current_project 使用调用 Session 的真实项目；无项目时表示无项目计划，响应中明确归属。all 允许用户查找其他项目的真实对象，仍受调用来源检查和输出预算限制。

列表只返回 ID、revision、名称、状态、时间说明、执行方式、项目、Session、模型和下一次时间。详情返回完整可编辑配置、停用原因和当前版本。已删除对象按 ID 查看时明确返回删除标记，普通列表默认排除。执行历史通过页面现有的分页 Interface 查看，工具不重复返回完整聊天历史。

view 不改变配置、已读状态或 revision，不运行 prompt，也不为查询提交 Durable Intent。查询仍保存正常 ToolCall / ToolResult 事实。

## 4. create：创建任务

| 字段 | 规则 |
| --- | --- |
| mode | 必填 create |
| kind | 必填 at / cron |
| name | 必填非空文本，最多 80 字符 |
| prompt | 必填非空完整任务指令，UTF-8 最多 16 KiB |
| schedule | 必填；内容必须与 kind 对应 |
| status | 可省略，默认 ACTIVE；只允许 ACTIVE / PAUSED |
| model | 可省略；系统固定解析后的模型设置，具体规则见第 6 节 |
| approvalMode | 可省略，默认 auto_review（替我审批）；本版允许 manual / auto_review |
| sessionId | 可省略；提供真实 ID 时复用该 Session，不提供时每次触发新建 Session |
| projectId | 可省略、真实 ID 或显式 null；具体规则见下表 |
| execution | 只用于新会话任务，可省略；字段沿用现有 local / worktree 与基线规则 |

create 不接受 id、expectedRevision 或 changes。Runtime 生成计划 ID 和 revision=1。

复用已有 Session 的周期任务：

```json
{
  "mode": "create",
  "kind": "cron",
  "name": "检查部署",
  "prompt": "检查当前部署状态。部署成功后报告结果，并暂停本定时任务。状态未变化时不重复生成长篇报告。",
  "schedule": {
    "expression": "*/10 * * * *",
    "timezone": "Asia/Shanghai"
  },
  "status": "ACTIVE",
  "approvalMode": "auto_review",
  "sessionId": "7274d733-cf05-4a60-b632-e1c7c758817b"
}
```

无项目、每次新建 Session 的一次性任务：

```json
{
  "mode": "create",
  "kind": "at",
  "name": "整理研究提纲",
  "prompt": "整理一份本地定时任务的研究提纲，保存文档并说明文件位置。",
  "schedule": {
    "runAt": "2026-10-16T09:00:00+08:00",
    "timezone": "Asia/Shanghai"
  },
  "projectId": null,
  "status": "ACTIVE"
}
```

以上均为数据示例，不表示本次创建任务。创建只保存未来计划，不立即执行正文。

### Session 与项目归属

| create 输入 | Runtime 的明确行为 |
| --- | --- |
| 有 sessionId，无 projectId | 系统绑定该 Session，并从它解析实际项目或无项目归属 |
| 有 sessionId，也有 projectId | 系统检查两者一致，包括 null；不一致时拒绝创建 |
| 无 sessionId，有 projectId 字符串 | 系统每次在该项目创建新 Session |
| 无 sessionId，projectId=null | 系统每次创建无项目 Session，并分配私有 Workspace |
| 无 sessionId，也无 projectId | 系统每次新建 Session；项目默认继承调用 Session 的项目，调用处无项目时使用无项目 |

sessionId 不接受 null、空字符串、临时草稿 ID 或内部子 Agent Session。模型说“在这里继续”时，必须显式提交系统上下文中提供的当前真实 Session ID。模型不传 sessionId 时，Runtime 不能偷偷绑定当前 Session。

目标 Session 可以是用户明确指定的其他普通 Session。Runtime 必须验证 ID、可用性和项目归属；模型不能猜测 ID。用户只提供名称且对象不明确时，模型先查找现有会话信息或澄清。view 的任务查询本身不承担 Session 搜索。

新会话的 execution 可包含 executionMode=local / worktree 和可选 baseRef，复用现有受控工作区创建路径。默认 local。无项目和 Non-Git 项目不能选择 worktree。已有 Session 不接受 execution，其执行环境继续由 Session 的现有切换流程决定。系统不接收任意 cwd 或 workspaceRoot。

## 5. 时间与 preview

kind=at 时，schedule 只接受 runAt 和可选 timezone。runAt 必须带 UTC offset；Runtime 在创建、改时间或重新启用时拒绝过去时刻。系统保存对应 UTC 时刻与展示时区。

kind=cron 时，schedule 只接受 expression 和可选 timezone。Runtime 只接受三档固定间隔与总方案规定的每天、每周指定日、每月指定日规则。系统不接受秒、年份、月份限制或同时限制日期和星期。固定间隔只接受：

| 间隔 | 规范表达式 |
| --- | --- |
| 10 分钟 | */10 * * * * |
| 30 分钟 | */30 * * * * |
| 1 小时 | 0 * * * * |

kind 只保存一份。schedule 不再重复包含 kind。expression 最多 128 个 ASCII 字符；timezone 最多 64 字符，使用有效 IANA 地区时区。创建时省略时区，系统从 Desktop 提供的有效 IANA 时区解析并固定；系统不跟随以后本机时区变化静默改规则。

```json
{
  "mode": "preview",
  "kind": "cron",
  "schedule": {
    "expression": "0 9 * * 1-5",
    "timezone": "Asia/Shanghai"
  }
}
```

preview 只返回规范规则、基准时间、说明与最多五次未来时间。它不需要 name、prompt、Session 或模型配置，不保存计划、不提交 Intent、不调用模型。create / update 本身也返回未来时间，简单请求无需强制先 preview。

模型不传 now、nextRunAt、补跑、漏跑或系统唤醒参数。Cron 的十分钟按钟表刻度对齐；09:07 创建后的下一次是 09:10。用户要求“十分钟后执行一次”时，模型使用 at。

## 6. 模型配置与权限

model 的结构沿用现有 Run 模型选择：

```json
{
  "modelId": "configured-model-id",
  "reasoningSelection": "high"
}
```

modelId 必须对应已有配置；上例只是字段示例。reasoningSelection 复用当前 none / thinking / low / medium / high / max 选择，并按实际模型能力校验。[现有 Run 参数](../../runtime/eidos_runtime/protocol/methods.py#L434)

- create 省略 model 时，Runtime 固定当前调用 Run 的有效模型与思考选择，并在结果中返回。
- 提供 model 时，modelId 必填；reasoningSelection 省略时使用该模型的有效默认值，不套用另一个模型的选择。
- 每次触发重新验证已有配置，并固定本次 Run 快照。模型配置被删除、禁用或缺少凭证时，系统暂停计划并说明原因，不静默切换模型。
- 目标 Session 只决定上下文和执行位置。计划自己的模型配置不改变该 Session 中用户下一轮的模型选择。
- model 不接受 Provider URL、API Key、任意请求参数或权限模式。系统继续使用现有配置与凭证管理。

approvalMode 是计划的独立字段。create 省略时，Runtime 保存 auto_review（替我审批），不从创建对话、目标 Session 或当前 Run 继承。模型或页面可以按用户明确要求设置 manual（请求审批）。本版计划只接受这两个值；当前 Run 使用 full_access 也不改变计划默认值。

每次触发时，Runtime 将计划的 approvalMode 固定到本次 Run。修改计划只影响后续接受的 Run，不改变已经入队或正在执行的 Run，也不改变用户对话的审批选择。view、写入回执和任务卡片均显示实际保存的模式。

auto_review 复用现有替我审批流程：系统只审查原本需要审批的操作；高危请求继续由用户决定，审查失败、超时或无法判断时转为人工处理。待审批继续占用该计划的未完成执行，同计划后续时刻按既有忙碌规则跳过。未来 Run 不继承旧 Grant 或完全访问确认。[当前审批行为](../current/capabilities.md#L432)

## 7. update 与 delete

update 必填 id、expectedRevision 和非空 changes。expectedRevision 是 1 至 JSON-safe integer 上限内的严格整数。changes 只接受 name、prompt、kind、schedule、status、model、approvalMode、target。省略字段保留原值；除 target.projectId 外，显式 null 无效。

修改间隔：

```json
{
  "mode": "update",
  "id": "6b53c59a-5e72-4db2-bcf2-c61b2f264573",
  "expectedRevision": 1,
  "changes": {
    "schedule": {
      "expression": "*/30 * * * *",
      "timezone": "Asia/Shanghai"
    }
  }
}
```

schedule 提供时整体替换，timezone 省略时沿用计划已固定的时区，不使用当前调用位置的新时区。kind 省略时沿用原 kind；切换 at / cron 时必须同时提供匹配的新 schedule。model 提供时整体替换并解析默认思考设置。status=PAUSED 表示暂停；status=ACTIVE 表示恢复，只计算未来时间。已执行或已过期的一次性任务需要新的未来时间才能重新启用。

changes.approvalMode 允许 manual / auto_review。省略时保留原模式，不重新应用 create 的默认值；null 无效。修改模式需要真实用户要求和 expectedRevision，自暂停的自动 Run 不能修改审批模式。该字段只配置未来计划执行，不切换当前 Run 的模式。

target 只用于显式改变执行目标。它采用以下互斥形式：

- {sessionId: 真实ID}：系统绑定已有 Session，项目从该 Session 解析。可附 projectId 一致性检查。
- {projectId: 真实ID或null, execution: 可选设置}：系统改为每次新建 Session。projectId 必须显式给出，避免使用当前调用位置猜测。

target 不接受空对象。模型只改时间时不提交 target。省略 target 始终保留原绑定。需要从已有 Session 改为无项目新会话时，提交 changes.target={projectId:null}；系统不使用 sessionId=null 表达切换。

目标失效时，用户仍可查看、暂停、改文字或删除。启用与改绑必须重新验证目标。暂停计划不取消已经入队的 Run，用户继续使用现有取消入口。

```json
{
  "mode": "delete",
  "id": "6b53c59a-5e72-4db2-bcf2-c61b2f264573",
  "expectedRevision": 3
}
```

delete 只接受上述三个字段。系统软删除计划并停止未来触发，保留 Session、Workspace、文件、触发历史与回执。已软删除对象不能直接恢复。新操作重复删除返回已删除状态，不声称发生第二次变更。

## 8. 单工具的执行策略

### 当前事实与本版取舍

当前 ToolSpec 和 ToolExecutionPolicy 按工具固定声明 sideEffect。ToolDispatchPlan.side_effect 读取这个声明；Controller 在 Handler 前检查 Reconciliation。Registry 也只允许 sideEffect=none 的 parallel 工具进入并行片段。[Spec](../../runtime/eidos_runtime/tools/registry.py#L52)、[Dispatch](../../runtime/eidos_runtime/runtime/tool_dispatcher.py#L35)、[前置检查](../../runtime/eidos_runtime/runtime/tool_execution.py#L568)

因此，合并工具不能只合并 Schema，然后让所有 mode 都走状态写入。查询应当在存在未知副作用时仍能读取，查询取消或异常也不能凭工具名字被误报为未知写入。

| 项目 | 本版设计 |
| --- | --- |
| 名称、来源 | automation；builtin / eidos.automation / v1 |
| visibility | deferred |
| 注册层最大副作用声明 | eidos_state |
| batchPolicy | single；首版所有 mode 顺序执行 |
| approvalRequired | false；Runtime 仍校验真实来源、作用范围与版本 |
| timeoutSeconds | 30；创建计划时不执行任务正文或创建 Worktree |
| view / preview 的实际调用类别 | read_only，对应 sideEffect=none；不提交 Durable Intent |
| create / update / delete 的实际调用类别 | mutation，对应 sideEffect=eidos_state；使用原有写入流程 |

本版只增加可信内置 AutomationToolRuntime 对已校验命令的纯分类。分类结果进入本次 ToolDispatchPlan，在屏障、取消和结果核验中统一使用。无效或未校验参数不能取得只读类别。默认工具继续使用现有静态策略，MCP、Plugin 和模型参数不能自行覆盖策略。

实现不修改冻结的 Step Registry，不在一次调用中换工具定义，不增加第二个公开工具，也不增加按 mode 配置任意流程的通用框架。首版继续顺序执行，避免为只读 mode 增加并行批次改造。实施时必须覆盖这段新增的分类契约，不能把它描述成当前已有能力。

### 原有工具流程

所有调用都执行 Validate、来源检查、取消检查、Verify 和规范 ToolResult 提交。view / preview 的业务读取不产生可恢复副作用；它们不需要 Durable Intent。

写模式继续执行：Validate → Prepare → Resolve Permission → Commit Durable Intent → Execute → Verify → Commit Result。普通已授权的计划管理不重复弹窗。写入发生前，Runtime 再次检查来源、目标、revision 与取消状态。

## 9. 持久来源与调用权限

Runtime 在接受 Run 的同一事务中保存严格 ExecutionOrigin，并关联计划、occurrence、配置版本与原始逻辑请求。Context、权限、UI 和记忆共用同一来源解析。模型不能通过参数声明已经取得授权。

| 调用来源 | 可用范围 |
| --- | --- |
| 真实用户请求的主 Run | 模型按用户要求 view / preview / create / update / delete |
| 子 Agent | 系统不注册工具，Runtime 拒绝绕过调用 |
| 周期自动 Run | 系统只提供 update，且 changes 仅允许 status=PAUSED；ID 必须是自身计划 |

自动 Run 自暂停使用触发时冻结的 revision。版本冲突时停止该修改，不读取新版后自动重试暂停用户刚改过的计划。自暂停停止后续触发，当前 Run 继续报告结果。

保存的 prompt 是预先授权的任务内容。系统不把自动输入当作用户当下的新授权。自动 Run 的新 Item 不进入自动记忆学习，记忆写工具不开放；记忆读取遵守当前用户设置。同 Session 后续真实用户输入不受影响。自动 Run 的 regenerate 继续保留原来源限制。

同计划原执行仍有 reconciliationRequired 时，系统暂停该计划并关联原执行。恢复或立即执行不能通过新 Run 绕过核验。普通失败或 sideEffectsMayExist=true 本身不触发该规则。其他计划和普通 Session 不增加全局锁或配额。

## 10. 版本、幂等、取消与恢复

Runtime 按 id 和 expectedRevision 条件更新。成功修改后 revision 加一；相同值更新返回 unchanged，不加版本。view / preview 不改变版本。页面与模型使用相同规则，版本冲突后模型重新查看，不能静默覆盖。

计划认领后、Run 入队前发生修改时，旧版本触发作废。Run 已经入队后发生修改时，已接受 Run 保留自己的旧快照。最终入队事务复核启用状态、版本、同计划占用、目标 Session 忙碌条件、持久绑定与原执行核验状态。Git 与权限准备在事务外进行。

Runtime 从持久 ToolCall / Item 派生 operationId，模型不填写。计划变更、操作回执与领域 Event / Outbox 在同一 SQLite 事务保存。相同 ToolCall 重放返回原结果。精确重复 create 通过“原逻辑请求 + 规范请求”复用已有创建，范围不跨不同用户请求，也不做模糊语义去重。

regenerate 会产生新 Run 和用户 Item，因此 Runtime 通过 run_revisions 解析原逻辑请求。已经保存的计划不因重新生成回答再创建一份。原操作未提交时，系统可以按原逻辑请求完成第一次写入。真实用户编辑或新输入表达新要求时，系统按新请求处理。

当前工具控制器在 Handler 返回后提交最终 ToolResult；该提交接口没有外部事务参数。[控制器](../../runtime/eidos_runtime/runtime/tool_execution.py#L895)、[结果提交](../../runtime/eidos_runtime/db/repositories/execution.py#L1521)

计划写入需要适应这两个提交点。系统复用 operations 保存 automation/tool 与 automation/create 回执；回执、计划、领域事件先原子提交，最终 ToolResult 继续由现有 Controller 提交。恢复时先读回执，准确收敛两个提交点间的失败，不重放写入，也不把已取消 Run 改成成功。

取消先于计划事务提交时，系统不保存变更。计划事务先完成时，系统保留实际变更；取消当前对话不能撤销已经保存的计划。UI 和规范结果仍显示任务 ID、revision 与“计划已保存，当前对话已停止”。无法核验回执时，系统进入 Reconciliation，不猜测成功。

Fork、Checkpoint 和历史重放不复制计划，也不自动撤销全局计划变更。旧卡片只表示当时结果；用户点击查看时按 ID 重新读取。

## 11. 结果与错误

工具结果沿用 canonical ToolResult，toolName 固定 automation。data 按结果区分 detail / list / preview / mutation。已解析配置只保存一份权威值，返回时至少包含：

| 内容 | 说明 |
| --- | --- |
| id、revision、name、status、deletedAt | 系统返回真实身份与状态 |
| kind、schedule、scheduleLabel、nextRunAt、nextRuns | 系统返回规范时间规则与实际未来时刻 |
| target | 系统明确返回 existing_session 或 new_session、目标 Session / Project 及执行设置 |
| model、approvalMode | 系统回显解析后的模型与权限设置 |
| changedFields、replayed、warnings | 写结果说明实际变更、历史回执与必要提示 |

mutation 返回明确 sideEffectsMayExist 与 reconciliationRequired；view / preview 固定无业务副作用。PAUSED 或已删除任务 nextRunAt=null、nextRuns=[]。一次性任务成功入队后转为 PAUSED，记录 one_shot_fired，界面显示已结束。

所有结果服从现有模型投影预算。详情 prompt 最多 16 KiB；列表最多 20 条且有总字节预算，不能返回所有任务正文。回执重放表示历史保存结果，不能冒充最新配置。

新增错误建议包括 automation_schedule_not_supported、automation_time_in_past、automation_not_found、automation_revision_conflict、automation_target_unavailable、automation_model_unavailable、automation_source_not_allowed、automation_operation_conflict 和 automation_verification_required。字段错误沿用 invalid_arguments。错误码属于待实现契约；用户正文显示简短中文原因。

## 12. 归属、生命周期与验收

已有目标 Session 被删除时，系统暂停计划。新会话任务不因创建者 Session 删除而消失。Project 被删除时，系统暂停关联计划，不能把失去项目的计划解释成 Projectless。来源引用可以清空，目标类型与不可用原因必须保留。

新会话任务每次使用现有 Session / Workspace 创建流程。Git Worktree 使用固定 ref 解析本次 commit，不默认带入未提交改动或联网 fetch。Projectless 复用现有私有目录分配。系统按任务名称与日期时间生成执行标题，不为每次执行额外调用标题模型。

原会话沿用已有 Context Budget 和 Compaction。新会话使用自足 prompt，不默认复制创建会话的完整历史或临时附件。历史分页，Worktree 沿用现有 Snapshot / Retention / Restore。删除计划或标记已读不删除产物。

定向验收需要覆盖：

- 模型只发现一个 deferred 工具，搜索后下一 Step 才能使用完整定义。
- view 有 ID 时取详情，无 ID 时分页列出；查询和预览不写业务状态。
- mode 分类在屏障、取消与异常处理前生效，参数错误无法伪装成只读。
- create 的 sessionId 存在与缺省规则、项目继承、显式无项目和归属冲突均有验证。
- update 省略 target 不改绑，显式 target 可以在原 Session 与新 Session 方式间切换。
- 模型配置合法性、配置丢失、思考选择及 full_access 不继承均有验证。
- approvalMode 创建缺省为 auto_review；显式 manual、更新省略保留、拒绝 null / full_access、既有 Run 快照不变与审批回退均有验证。
- 版本竞争、Session 忙碌跳过、回执恢复、regenerate 防重和真实取消副作用均有验证。
- 自动来源、记忆资格、自暂停、一次性停用、目标删除和新会话准备失败均有验证。
- schema、双端协议、投影预算、无窗口结果与本地生命周期均有对应验证。

本轮只修订三份设计并统一工具名。生产代码、测试、Schema、协议和依赖未修改。功能测试、构建、原生验证与 Full Suite 未运行。工作区其他修改保持原样。
