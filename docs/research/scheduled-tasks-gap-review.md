# 本地定时任务：延迟工具与方案缺口复核

> 文档分类：调研与对照。本文保留调研时的事实、建议和缺口，不作为当前实现或最终决策的证明。当前行为请以 [当前说明](../README.md#当前说明)、源码和测试为准。

复核日期：2026-10-15。本文保留实现前的源码复核与规则依据。当前实现、协议和验证边界以 [当前本地定时任务](../current-automations.md)、生产代码与测试为准。

相关方案：[总方案 V3](../design/scheduled-tasks-research-and-design.md)、[automation 详细设计](../design/automation-tool-design.md)。

**模型只使用一个 automation 工具，mode 区分管理操作。工具采用 deferred：初始模型请求不包含完整定义，模型先使用 tool_search，后续 Step 才取得工具定义并调用。Runtime 仍负责权限、触发、持久化和恢复。**

## 1. Eidos 的延迟工具机制已经能做什么

| 已核实的源码事实 | 对本功能的影响 |
| --- | --- |
| ToolSpec 已有 direct / deferred；model_definitions 只选择 direct 或已激活工具 | 新工具不需要新的加载框架，只需要正确注册 |
| RunResources 已为 deferred 工具统一建立 tool_search 候选集 | 定时工具可以进入同一个搜索入口 |
| tool_search 按名称、描述和来源字段匹配，不使用语义向量搜索 | 系统指令应提供精确名称与简短能力提示 |
| 搜索结果只包含 name、description、provenance、score 等摘要 | 模型不从搜索结果 JSON 中取得完整 inputSchema |
| 搜索命中的名称经 verify 返回，并写入当前 Run 的 activated_tools_json | 激活只影响当前 Run，保存位置不是 Session |
| 下一次 Step 创建时重新读取激活集合，并生成 Tool Definition 和冻结快照 | 完整描述、参数结构和输入格式在下一次模型请求中出现 |
| 未激活的 deferred 调用返回 tool_not_activated，不执行 Runtime | 搜索和业务调用不能依赖同一 Step 内动态改变工具集合 |
| 激活历史保留最近 32 个名称；一次快照最多接受 16 个、总定义预算 128 KiB、单定义上限 32 KiB | 工具可能因预算未进入快照；搜索命中不能被解释成永久可用 |
| MCP 工具目前带 mcp__server__tool 名称，Registry 也处理同名冲突 | 系统应继续使用内置 provenance 区分管理工具，不能靠名称片段识别权限或 UI 卡片 |

源码入口：

- [搜索与激活输出](../../runtime/eidos_runtime/tools/search.py)
- [工具可见性与快照预算](../../runtime/eidos_runtime/tools/registry.py#L653)
- [Run 中的激活记录](../../runtime/eidos_runtime/db/repositories/extensions.py#L463)
- [下一 Step 的定义](../../runtime/eidos_runtime/runtime/step_context.py#L39)
- [未激活调用](../../runtime/eidos_runtime/runtime/tool_execution.py#L557)
- [MCP 名称与来源](../../runtime/eidos_runtime/extensions/mcp.py#L860)

当前测试已经有“搜索前隐藏、下一快照可见”的用例。本轮只读取用例，没有重新运行，也没有证明新定时工具已经通过测试。[现有测试](../../runtime/tests/test_tool_registry.py#L77)

## 2. 推荐的发现与调用流程

初始请求保留一个短提示，不注入完整参数：

> 用户明确要求管理定时任务时，先使用 tool_search 精确搜索 automation。等待搜索结果，在后续 Step 取得完整定义后按 mode 调用。查看使用 view，时间预览使用 preview。用户只是讨论方案时，不创建任务。

创建流程：

```text
用户提出定时要求
  → Step N：tool_search(query="automation", limit=1)
  → Runtime 保存激活名称
  → Step N+1：模型取得完整 automation 定义
  → 模型提交 create
  → Runtime 保存计划并返回真实结果
```

更新前需要查找 ID 时，模型先通过同一工具的 view 读取实际配置，再提交 update。已经在当前快照可见的工具可以直接调用，不重复搜索。

本版不增加通用工具组自动加载。精确搜索避免中文关键词分词差异，也避免把其他工具的相似描述当成内置工具。工具描述仍可保留“定时任务、计划、提醒、暂停、schedule、automation”等词，帮助普通能力检索。

系统初始保留工具名和能力提示，表示“隐藏完整定义”，不是“让模型完全不知道存在这项能力”。如果两者都隐藏，当前搜索实现无法保证模型会主动发现正确工具。

搜索不执行任务、不创建计划、不发出授权，也不提升 Run 权限。模型不能把搜索结果当作写入成功。它在同一批次中先搜索、后调用隐藏工具时，Runtime 仍返回现有 tool_not_activated；模型随后按正常流程修正。

### 激活生命周期

- 新用户 Run 默认重新按需搜索。上一轮激活不作为 Session 永久状态。
- 同一 Run 的暂停、恢复或重启可以保留已存激活名称，但快照仍按当前合法 Registry 和预算筛选。
- 自动触发的新 Run 也重新按需搜索。周期任务需要自暂停时，只能发现当前来源允许的受限定义。
- 子 Agent 的目录不包含 automation，因此搜索也不能让它出现。
- 历史工具定义、Fork 记录和搜索结果不能替代当前 Step 定义与权限检查。
- 模型遇到已被预算移出的工具时，可以再次精确搜索。系统不新增永久置顶或无限 Schema 缓存。

如果产品以后要求 tool_search 的 JSON 直接附带 Schema，那是另一项结果契约改动。当前方案不需要它，因为后续模型请求已经能携带完整定义。

### 单工具合并后的新增接入点

当前 ToolSpec 按工具固定声明 sideEffect，ToolDispatchPlan 读取该声明，Controller 在 Handler 前使用它决定屏障。单纯把只读与写模式放进同一个 eidos_state 工具，会使查询也受写入屏障影响。[Spec](../../runtime/eidos_runtime/tools/registry.py#L52)、[Dispatch](../../runtime/eidos_runtime/runtime/tool_dispatcher.py#L35)、[屏障](../../runtime/eidos_runtime/runtime/tool_execution.py#L568)

V3 保留一个 deferred 工具，并统一顺序执行。可信内置 AutomationToolRuntime 对已校验命令提供纯分类，view / preview 为只读，其余为状态写入。分类结果进入本次 DispatchPlan，供屏障、取消和结果核验共用；无效参数不能取得只读类别。该接入点尚未实现，不能只改 Schema 就宣称单工具已经可用。

该取舍减少模型需要发现的工具数量。首版不额外优化只读 mode 的并行，不增加任意 mode 流程框架，不允许外部工具或模型参数自行覆盖副作用策略。

### 创建时的目标规则

create 有 sessionId 时，系统复用该已有 Session，并从它解析项目。有 projectId 时必须与目标 Session 一致。create 无 sessionId 时，每次触发新建 Session；projectId 字符串表示指定项目，null 表示无项目，省略表示继承调用 Session 的归属。

用户要求“在这里继续”时，模型显式提交当前真实 Session ID。系统不把缺省 sessionId 解释成复用当前 Session。update 省略 target 则保留原绑定；显式 changes.target 才改变执行位置。

## 3. 实现前必须补齐：自动输入来源

### 当前事实

create_run 会把 Run 输入写成 completed user_message。memory_source_insert 会收录新 Item，memory_source_items 的 eligible 默认是 1。[Run 输入](../../runtime/eidos_runtime/db/repositories/runs.py#L272)、[记忆来源](../../runtime/eidos_runtime/memory/schema.py#L19)、[插入触发器](../../runtime/eidos_runtime/memory/schema.py#L105)

### 缺口与推断

已有工具设计说“Runtime 注入来源”，但没有定义持久化来源。如果只把保存的任务正文传给 create_run，后续代码可能把自动输入当作用户新说的话。该风险来自现有结构，本轮没有做动态复现。

### 本版决定

Runtime 在接受定时 Run 的同一事务中保存明确的 ExecutionOrigin，并关联 occurrence。该来源必须能从 SQLite 恢复。实现可以扩展现有 Run resolution 的严格类型和必要关联，不引入第二套执行状态机。

来源至少能区分真实用户输入、自动触发、子任务和已有 Run 的修订来源。Context、工具权限、UI 和记忆处理共用同一解析入口，不能各自按文本或函数名猜测。

保存的定时 prompt 是用户预先授权的任务内容，但不是用户当下的新授权。Runtime 在自动输入旁提供计划 ID、配置版本与计划时间。真实用户之后在同一 Session 新发出的输入仍走普通路径。

本版自动 Run 新生成的 Item 不进入自动记忆学习，其记忆写工具也不开放；它可以按当前用户设置读取记忆。实现需要在 Item 生成及记忆候选过滤中落实这一点，不能关闭整个 Session 的记忆功能，否则会误伤同 Session 的真实用户输入。

## 4. 实现前必须补齐：不确定副作用与下一次触发

### 当前事实

side_effects_blocked(run_id) 读取当前 Run 的 reconciliation_required。当前 Run admission 的 Workspace 检查主要验证执行身份。[屏障查询](../../runtime/eidos_runtime/db/repositories/runs.py#L661)、[工作区接纳](../../runtime/eidos_runtime/application/runs.py#L493)

这些入口不能证明新 Run 会继承上一次的副作用屏障。依赖模型阅读历史后自行避免重放，也不能替代调度判断。

### 本版决定

同一计划的上一次执行若仍有 reconciliationRequired=true，系统暂停该计划，并记录 awaiting_verification、原 occurrence 和 Run ID。这个决定只作用于该计划，不新增跨 Run 的全局 Workspace 门或普通 Run 配额。

用户通过现有核验路径处理后，再显式恢复计划。单纯把计划改成 ACTIVE 或点击立即执行，不能跳过未解决的原执行事实。

系统不因为普通失败、非零退出码或 sideEffectsMayExist=true 就自动暂停。它只使用明确的 reconciliationRequired 及原有核验事实。已知结果继续遵守原有重试与错误规则。

该行为属于不确定副作用处理，不是漏跑检测或补跑。

## 5. 实现前必须补齐：编辑、暂停、忙碌与入队竞争

### 当前事实

当前 Run 创建支持外部事务连接。普通 run/start 本来允许排队，不会天然满足“定时任务遇到忙碌就跳过”的产品规则。[Run 创建](../../runtime/eidos_runtime/db/repositories/runs.py#L122)、[FIFO](../../runtime/eidos_runtime/db/repositories/runs.py#L375)

### 本版决定

认领触发时，Runtime 原子检查同计划是否已有准备中或未结束执行。准备环境后，Runtime 在 queued Run 入库的同一事务内重新检查：

1. 计划仍启用且未删除。
2. 当前 revision 仍等于本次认领版本。
3. 持久目标绑定仍与已核验的执行环境一致。
4. 同计划没有其他占用。
5. 原会话没有活动 Run 或已排队的用户消息。
6. 原执行没有未清除的核验要求。

任何条件不满足时，系统结束该准备记录，并按原因跳过或报告；系统不放入等待队列。工作区、Git 和权限物化在事务外完成；短事务只复核持久条件，后续执行仍保留原有身份和权限校验。

**用户编辑发生在认领之后、Run 提交之前时，旧触发作废。用户编辑发生在 Run 提交之后时，已接受 Run 保留旧快照继续执行。** 本规则替代旧方案中“已认领触发一律保留旧配置继续”的含糊表述。

新 Worktree 已经准备好但触发作废时，系统使用现有生命周期收敛准备结果；系统不删除无法证明归属的目录。准备记录和 operation ID 保留，不能通过重建触发丢弃清理事实。

暂停计划与取消当前 Run 仍是两个操作。界面应展示已提交 Run 的 ID，让用户可以使用原取消入口。

## 6. 实现前必须补齐：重新生成与来源继承

### 当前事实

response_actions.revise 会调用普通 Run start，用原 userInput 创建新的 Run；随后记录 regenerate 或 edit 的 run_revisions。[修订入口](../../runtime/eidos_runtime/application/response_actions.py#L62)

因此，“同 ToolCall 防重”和“同用户 Item 防重”都不足以覆盖重新生成，因为新的 Run 会创建新的用户 Item。

### 本版决定

Runtime 根据持久 run_revisions 解析逻辑请求来源。regenerate 沿用原请求的计划管理回执。已经成功创建的任务不会因重新生成回答而再创建一份。模型可以查询当前任务并重新说明结果。

原操作从未提交时，Runtime 可以按原请求继续第一次执行，但仍使用原逻辑请求的幂等身份。真实用户编辑或新发消息表达了新的管理要求时，系统按新请求处理，并通过真实 ID 更新已有任务。

自动触发 Run 的 regenerate 必须保留自动来源限制。系统不能因为新 Run 表面包含 user_message 就升级为普通管理权限。

Fork、Checkpoint 或历史重放不复制定时计划，不撤销已经发生的全局计划变更。新 Session 的模型需要当前用户要求和 query 结果，才能管理原计划。实现前必须检查所有内部 Run 创建入口是否保存来源，而不只检查 run/start。

## 7. 本版需要明确：模型、扩展与目标变化

| 变化 | 本版处理 |
| --- | --- |
| 指定模型被删除、禁用或缺少凭证 | 系统暂停计划并显示具体原因，不静默换模型 |
| Provider 暂时失败或网络暂时不可用 | 当前 occurrence 使用现有 Run / Model Attempt 的失败与安全重试规则；系统不新增整个任务的自动重试 |
| 用户修改同一模型配置 | 下一次 Run 使用当前有效配置，并固定自己的模型快照 |
| Skill、Plugin 或 MCP 配置发生变化 | 每次新 Run 从当前已授权配置固定扩展快照；计划不永久保存旧 Run 的授权 |
| 显式指定的 Skill 已不可用 | 系统报告未能使用指定能力，不自动换成同名的其他来源 |
| 创建对话的审批模式不同 | 计划省略 approvalMode 时统一保存 auto_review（替我审批），不继承创建 Run 或目标 Session 的模式；用户可显式选择 manual |
| 用户更新计划审批模式 | changes.approvalMode 只影响后续接受的 Run，省略时保留原值；自动 Run 不允许修改审批模式 |
| 当前 Run 是 full_access | 新计划仍默认 auto_review，并明确回显；系统不跨 Run 复制完全访问或 Grant |
| 原会话切换 Local / Worktree | 任务沿用该 Session 经过核验的新执行绑定，下一次接受 Run 时记录实际位置 |
| 创建或更新明确指定模型 | 工具接收已有 modelId 与 reasoningSelection，复用当前模型能力校验；不接收凭证或任意 Provider 配置 |
| Session 或 Project 被删除 | 系统在同一删除事务中暂停目标计划，保留历史与原因 |
| 独立任务的创建者 Session 被删除 | 独立任务继续存在，来源引用可以清空，不把目标项目引用误当来源引用 |

Project 删除导致 projectId 缺失时，系统不能把一个项目任务解释为 Projectless。持久 binding 必须保留目标类型与不可用状态。现有项目删除只检查 Session、Worktree 等条件，实施时需要增加关联计划处理。[项目删除](../../runtime/eidos_runtime/application/projects.py#L84)

## 8. 本版需要明确：历史、工作区和上下文增长

当前 Session 上下文会保留已有 Run 和 Item，定时任务每次新建会话时也会产生真实 Session / Workspace。当前 Worktree retention 根据活跃执行、安全和最近使用等事实处理目录，不识别定时结果是否已读。[Retention](../../runtime/eidos_runtime/application/worktree_retention.py#L303)

本版采用以下规则：

- 原会话任务继续使用已有 Context Budget 和 Compaction。系统不承诺无限保留全部历史，也不把完整任务列表放入系统提示词。
- 独立任务使用自足 prompt，不默认复制创建它的 Session 的完整聊天或临时附件。模型若要依赖原会话上下文，应选择原会话方式。
- 模型不能把任务创建时的临时文件路径当作长期可用内容。用户引用需要作为正式持久引用保存，或在每次执行时从明确目标重新读取；本版不支持的引用形式要直接说明。
- 独立任务标题可以使用“任务名称 + 本次日期时间”，复用现有 Session title 字段；系统不为每次执行额外调用标题模型。Projectless 名称继续使用已有分配与身份规则。
- 任务历史分页，且不自动删除 Session 或用户产物。是否增加历史清理属于后续需求。
- Worktree 沿用当前 Snapshot / Retention / Restore 流程，卡片要能说明目录已被回收并提供现有恢复入口。系统不为所有定时结果永久固定 Worktree。
- 标记结果已读不能触发删除文件。删除计划也不能删除运行结果。

## 9. 本版需要明确：生命周期、时间与一次性状态

Runtime 目前使用 RUNNING、RECONFIGURING 和 DRAINING 控制状态，持续受管任务也有 persistent 标记。[控制状态](../../runtime/eidos_runtime/runtime/supervisor.py#L84)、[重配置](../../runtime/eidos_runtime/runtime/supervisor.py#L792)

定时模块需要接入这些已有状态。系统在 DRAINING 和 RECONFIGURING 时不接收新触发，不把常驻调度本身计入需要用户取消的业务 Run。恢复调度时只计算未来时间。系统不新增永久线程或第二个 Event Loop。

本版还需要在实现前验证：

- Main 能向 Runtime 提供可验证的 IANA 时区，打包 Runtime 能读取对应地区规则。
- 预览、创建与真实调度使用同一实现，固定间隔三档由 Runtime 校验。
- 休眠与恢复的通知处理先更新未来时间，再开放触发；正常定时器延迟在当前分钟内可接受，跨过该分钟不执行旧时刻。
- 时间回拨不会再次接受已记录的同一 UTC 触发；系统不因此扫描过去历史。
- 单次任务在成功提交 Run 的同一事务中停用；环境准备失败不伪装成“已执行”。
- 一次性任务自动停用后，不再给它的自动 Run 暴露无意义的自暂停能力。周期任务自暂停仍校验原配置版本。
- 没有窗口时，模型失败与待审批结果保存在 SQLite。用户重新打开窗口后能看到；首版不依赖系统通知是否送达。

系统没有统一的用户可配置 Run 成本或时长预算。方案不能声称已有这些能力。当前范围继续依靠三档间隔、同计划不重叠和人工暂停管理重复执行，不为本轮引入预算框架。

## 10. 实施顺序与验证范围

| 优先级 | 实施前需要形成的明确结果 |
| --- | --- |
| 先完成 | 单个 deferred 工具、mode 对应的真实副作用类别、下一 Step 生效与每 Run 激活范围 |
| 先完成 | sessionId 存在或缺省的两种执行方式、项目继承、显式无项目与目标一致性 |
| 先完成 | 持久执行来源、自动输入的记忆资格、自动 Run 与子任务的工具权限 |
| 先完成 | 同计划认领、版本变化和 Session 忙碌条件与 Run 入队的原子检查 |
| 先完成 | 不确定副作用使本计划暂停、操作回执恢复、取消后的实际变更展示 |
| 同批完成 | regenerate 防重、Session / Project 删除联动、模型与扩展变化 |
| 同批完成 | Worktree 结果恢复、标题、分页、时区、退出和一次性停用 |
| 暂不扩展 | 漏跑、补跑、云端、工具组加载、全局 Run 配额、通用成本框架、自动清理产物 |

有些规则已经在旧文档中提到，但当前仍只有设计，没有生产实现。不能把“已有方案描述”当成“源码已经保证”。

定向验证需要覆盖：初始定义隐藏、搜索结果与 Schema 的区别、同 Step 调用拒绝、下一 Step 调用成功、预算淘汰后重新激活、重启和新 Run 的差别、mode 分类及只读查询、sessionId 缺省与项目归属、子任务目录过滤、自动输入不成为新授权、记忆隔离、并发更新与入队、旧 Run 核验状态、regenerate 防重、删除联动和无窗口结果恢复。

当前已有 test_tool_registry 等测试能提供部分基础。本轮没有运行这些测试。新增实现需要单独补充对应验证；Full Suite 仍只在用户明确要求时运行。

本轮修订三份设计，统一单工具名 automation、mode、Session 绑定和模型设置。生产代码、当前能力文档、依赖和测试均未修改。工作区其他改动保持原样。
