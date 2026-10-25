# Session 聊天分叉：Codex 调研与 Eidos 方案

> 文档分类：设计与实施记录。本文保留记录时的方案、分支状态和验证范围。当前行为请以 [当前说明](../README.md#当前说明)、源码和测试为准。

调研日期：2026-10-13。实现日期：2026-10-14。本文保留调研依据和设计过程。当前实现使用 `session/fork`，分支为 `feat/session-conversation-fork`。当前能力与限制以 `docs/current/` 和源码为准。

**建议：Eidos 增加独立的 `session/fork`。用户可以从一次已经结束的回答创建新 Session。新 Session 默认保留到该回答为止的完整聊天历史，包含该回答。新 Session 等待用户输入下一条消息。**

## 1. Codex 已确认的行为

OpenAI 的更新记录显示，Codex app 26.317 在 2026-03-18 增加了从较早消息分叉的能力。[官方更新记录](https://learn.chatgpt.com/docs/changelog)

Codex App Server 用 `thread/fork` 创建新的聊天 ID。参数 `lastTurnId` 指定包含在新聊天中的最后一轮。接口保留该轮，排除之后的轮次。接口拒绝正在进行的指定轮次。返回值可以包含 `forkedFromId`。[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server#threads)

```json
{
  "method": "thread/fork",
  "id": 12,
  "params": {
    "threadId": "source-thread",
    "lastTurnId": "chosen-turn"
  }
}
```

官方文档把 Thread、Turn 和 Item 分成三层。一次 Turn 包含用户请求和随后的 Agent 工作。因此，这个公开接口的截止单位是完整轮次。它不是任意字符或任意工具执行位置。[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server#core-primitives)

官方 `/fork` 说明还提供了 Local 和 Worktree 两种目标。Worktree 用来隔离文件修改。[官方命令说明](https://learn.chatgpt.com/docs/reference/slash-commands)、[官方 Worktree 文档](https://learn.chatgpt.com/docs/environments/git-worktrees)

本机只读检查补充了以下证据：

| 检查对象 | 本机结果 | 结论的范围 |
| --- | --- | --- |
| 独立 CLI | `codex-cli 0.142.4` | 该版本导出的 `ThreadForkParams` 没有 `lastTurnId`。旧 CLI 的 Schema 不能代表当前 Desktop |
| ChatGPT Desktop | `26.1007.30912` | 检查对象是本机安装包 |
| Desktop 内置 CLI | `0.162.0-alpha.17.2` | 该版本导出的 Schema 包含 `lastTurnId`、`beforeTurnId` 和 `forkedFromId` |
| Desktop 打包代码 | 历史回答的分叉操作使用目标 Turn；Worktree 分叉传入 `startingState: working-tree` | 该版本把聊天截止点与当前文件状态分开 |

本机证据来自 `/Applications/ChatGPT.app/Contents/Resources/app.asar`。相关模块是 `webview/assets/local-conversation-thread-c8c5e92ea89e.js`。本次检查没有创建分叉，没有发送消息，也没有改动用户聊天。本文没有做真实 UI 操作验收。

**Eidos 可以采用相同的产品规则：聊天历史有截止点，文件状态有单独的选择。用户从旧回答分叉，不应默认理解成文件也回到了旧回答时的状态。**

## 2. Eidos 现状与缺口

当前实现已经支持 `checkpoint/fork`。Local 路径创建新 Session，然后把父 Run 的 `userInput`、引用和模型再次入队。Managed 路径先恢复 Checkpoint 的 Git 状态，再创建新 Session 和新 Run。两条路径都没有复制整个 Session 的聊天前缀。[Checkpoint 实现](../../runtime/eidos_runtime/application/checkpoints.py#L536)、[Managed Fork 实现](../../runtime/eidos_runtime/application/checkpoints.py#L652)

当前 Checkpoint Fork 还要求 Session 绑定 Project。因此，它不能直接作为通用的 Projectless 聊天分叉入口。[Project 检查](../../runtime/eidos_runtime/application/checkpoints.py#L547)

当前 `session/read` 按 Session 查询真实 Run、Item 和 ToolCall，并支持历史分页。当前 Context 也只查询该 Session 的 Item。两个读取路径都需要增加继承历史的支持。[历史读取](../../runtime/eidos_runtime/db/repositories/sessions.py#L906)、[Context 事实读取](../../runtime/eidos_runtime/db/repositories/context.py#L43)

当前模型上下文还依赖 Tool Result、用户引用、图片和 `ModelResponseReplay`。只复制用户消息和回答文字会遗漏这些内容，也可能破坏工具调用与结果的对应关系。[Context 构建](../../runtime/eidos_runtime/context/builder.py#L176)、[模型回放读取](../../runtime/eidos_runtime/db/repositories/context.py#L350)

当前 Session 删除会删除它的 Run、Item、ToolCall 和模型记录。只保存父 Session ID 和截止 Item ID，会让新 Session 依赖父 Session 的存活。[Session 删除](../../runtime/eidos_runtime/db/repositories/sessions.py#L648)

当前 `memory.lifecycle.fork_source` 只记录记忆来源关系和临时状态。该函数没有回答截止点，也没有复制聊天历史。[记忆来源实现](../../runtime/eidos_runtime/memory/lifecycle.py#L65)

## 3. 第一版产品规则

第一版把“一次回答”定义为一个已经结束的 Run 的完整回答。界面在这次回答的操作区提供“从这里分叉”。Runtime 根据回答 Item 校验对应 Run。Runtime 不开放正在流式输出的回答、过程说明或未完成工具批次作为分叉点。

如果一次回答由多个文本块组成，界面提供一个回答级入口。这个入口保留整次回答。以后如果需要从 Run 内某段过程说明分叉，产品需要单独定义更细的截止规则。

| 情况 | 建议行为 |
| --- | --- |
| 用户选择第 N 次回答 | 新 Session 保留第 1 到第 N 次完整轮次，包含第 N 次回答 |
| 原 Session 已经有第 N+1 次及之后的内容 | 新 Session 的继承历史不包含这些内容 |
| 原 Session 正在执行较晚的 Run | 用户仍可以从较早的、已经结束的回答分叉。操作不取消当前 Run |
| 用户完成分叉 | 界面打开新 Session，聚焦空输入框。Runtime 不创建可执行的新 Run |
| 用户在新 Session 发消息 | Runtime 按普通 `run/start` 流程创建独立 Run |
| 用户从分叉 Session 再分叉 | Runtime 固定该 Session 的可见前缀，包含它已经继承的历史 |
| 用户删除原 Session | 新 Session 仍保留自己的历史和附件快照 |
| 用户取消原 Session 的 Run | 新 Session 的 Run 不受父子 Agent 取消规则影响 |

新 Session 是独立任务。分叉关系只是来源信息。Runtime 不把它登记为 `agent_delegations` 中的子 Agent。

第一版固定使用“完整历史前缀”。产品暂时不增加“只继承摘要”“只继承最后回答”等选项。完整继承表示记录完整保存。后续模型请求仍然使用已有的上下文预算和压缩规则。

## 4. 继承内容与执行状态

| 内容 | 建议处理 |
| --- | --- |
| 用户消息、回答、历史失败与取消记录 | Runtime 保留原内容和原结果，并标记为继承历史 |
| 工具调用参数、工具结果、历史 Diff | Runtime 保存逻辑记录。新 Session 不重放这些工具 |
| 工具图片、用户图片、文本附件和输入引用快照 | 新 Session 持有这些内容的存储引用 |
| 同一模型响应中的文本、工具调用和回放数据 | Runtime 保留响应分组和调用 ID，按既有模型转换规则投影 |
| 已存在的压缩摘要 | Runtime 只允许复用来源全部位于前缀内、且验证仍有效的摘要。更晚生成且包含后续信息的摘要不进入分叉 |
| 更晚的编辑重发和重新生成 | Runtime 不把截止点之后的替换关系带入分叉。Runtime 固定截止点对应的历史路径 |
| 模型与思考强度 | 界面可以带入偏好。新 Run 重新检查当前 Profile 的可用性 |
| Workspace 规则、Skill、Plugin、MCP 配置 | 新 Run 按当前目标工作区重新解析和固定配置 |
| 草稿 | 新 Session 使用空草稿。原 Session 的草稿保持原样 |
| 待批请求、Run Grant、进程、Worker、待执行工具和子任务 | Runtime 不复制这些活动资源或授权 |
| 完全访问模式 | 新 Run 继续经过现有 Desktop 人工确认流程。聊天分叉本身不授予完全访问 |
| Checkpoint、原始文件和产物路径 | 聊天记录可以保留引用。普通聊天分叉不自动复制这些文件 |

新 Session 不能把历史读取结果当作当前文件的写入证据。文件工具仍要读取或核验当前文件，并检查身份、Base Hash 和最终结果。

Runtime 不能通过新 Session ID 清除原工作区的未知副作用。新 Run 的启动和工具执行仍要检查当前目标工作区的有效 Reconciliation 状态。历史记录可以保留未知结果，但 Runtime 不能把未知结果改成成功，也不能自动重试原工具。

记忆继续服从当前用户设置和隐私撤销规则。继承历史不作为新发生的用户活动再次学习。全局或项目记忆属于当前环境，不承诺回到旧回答的时间点。产品的历史截止承诺只针对聊天前缀及其派生摘要。

## 5. 文件环境的选择

普通聊天分叉采用当前文件状态。Checkpoint 恢复采用历史文件状态。界面需要让用户理解这两个选项的区别。

| 来源 | 第一版默认目标 | 文件行为 |
| --- | --- | --- |
| Local Git Project | 相同 Project 和相同 Local 目录 | 两个 Session 共享当前文件。用户可以选择独立 Worktree |
| Local Non-Git Project | 相同 Project 和相同目录 | 两个 Session 共享当前文件 |
| Projectless | 相同目录，并保留 Projectless 属性 | 两个 Session 共享当前文件。分叉不把目录自动注册为 Project |
| Managed Worktree | 新的独立 Managed Worktree | 新 Worktree 从来源当前 HEAD 和受支持的本地变更创建。新 Session 不共用父 Session 的受管 Worktree 所有权 |

Projectless 的现有普通创建流程会分配新目录。分叉需要单独的“绑定已核验来源目录”路径。该路径只允许绑定当前来源 Session 的执行目录，并保留无项目属性。[当前 Projectless 创建](../../runtime/eidos_runtime/application/sessions.py#L646)、[当前目录与 Session 校验](../../runtime/eidos_runtime/db/repositories/sessions.py#L132)

Managed 来源需要复用 `WorktreeManager`、`GitBackend` 和现有 durable lifecycle。Runtime 在创建前后核验来源 HEAD、文件身份和变更。Runtime 不对来源执行 stash、reset、checkout 或 add。忽略文件继续遵守 `.worktreeinclude` 规则。运行中的来源改变了文件时，Runtime 返回来源变化错误，不生成一份混合状态。

第一版不提供“把当前目录恢复到分叉回答时的文件状态”。后续版本可以在分叉对话框增加“从已有 Checkpoint 创建独立 Worktree”。Runtime 只能使用明确匹配该回答的 Checkpoint。Runtime 不能用当前文件生成一个 Checkpoint，再宣称它是历史回答时的状态。

同目录选项的说明可以写成：“新会话会保留截至这次回答的聊天。两条会话会使用同一目录中的当前文件。”独立 Worktree 选项的说明可以写成：“新会话会在独立工作树中继续。工作树使用当前文件状态。”

## 6. 历史保存方式

建议采用“固定的历史前缀快照 + 新 Session 自有记录”。SQLite 保存快照身份、顺序和来源。现有 JsonBlobStore 保存较大的内容。新 Session 持有自己的内容引用。

| 方案 | 问题或收益 |
| --- | --- |
| 只复制消息文字 | 该方案遗漏附件、工具结果和模型回放 |
| 只保存父 Session ID 和截止 ID | 该方案依赖父记录存活。多次分叉还需要递归读取祖先 |
| 复制整套 Run、Approval、Intent 和 Worker 状态 | 该方案容易把历史执行复制成可恢复的任务，也会混淆授权和统计 |
| 固定逻辑历史快照，并独立持有 Blob 引用 | 该方案保留历史内容，并让执行状态继续由新 Session 的真实 Run 管理 |

建议增加三个用途明确的存储结构。实现时可以按现有存储惯例调整命名：

| 结构 | 保存的内容 |
| --- | --- |
| `session_forks` | 目标 Session、来源 Session、来源 Run、回答 Item、快照版本、校验值和创建时间 |
| `session_history_entries` | 目标 Session 的继承顺序、展示 ID、来源 ID、响应分组和严格类型的逻辑历史记录 |
| `session_history_resources` | 目标 Session 持有的输入引用、工具内容、模型回放和 Blob 引用 |

来源 ID 是历史来源信息。它们不能使用会随父 Session 删除而删除目标历史的级联规则。目标 Session 删除时，Runtime 释放该目标持有的引用。还有其他引用的 Blob 继续保留。

历史记录使用严格 Pydantic 类型。一个记录保存完整的逻辑事实，再分别投影为界面记录和模型事实。Runtime 不保存两份会独立变化的历史。历史中的 Run 只提供展示信息，不加入真实 `runs` 的调度、恢复、任务状态和费用统计。

新 Session 再次分叉时，Runtime 直接固定它的历史块和自有记录。新快照可以复用不可变内容块，但目标 Session 持有独立引用。读取时不沿父 Session 链递归查询。

Blob GC 必须扫描新增资源引用。输入引用的七天清理也必须计入继承历史。目前这两个入口只识别既有表，因此它们是实现范围的一部分。[Blob 引用扫描](../../runtime/eidos_runtime/db/layout.py#L1063)、[输入引用清理](../../runtime/eidos_runtime/persistence/input_context.py#L15)

调研时仓库使用 Schema 25。当前实现增加了 v25 → v26 的事务迁移。旧 Session 默认没有继承历史。[当前 Schema](../../runtime/eidos_runtime/db/schema.py#L9)

## 7. 截止点与一致性

Runtime 根据 `sourceItemId` 找到来源 Run，并验证这条 Item 属于来源 Session、回答已经完整落盘、Run 已经结束。Runtime 再固定前面完整 Run 与目标 Run 的逻辑历史。

如果用户选择的是继承历史中的回答，Runtime 从当前来源 Session 自己持有的快照解析原 Run 的历史信息。这个过程不查询祖先的真实 Run，也不要求祖先 Session 仍然存在。

Runtime 按 Run 的持久顺序和 Run 内 Item 的顺序确定范围。Runtime 不能只使用时间戳，也不能只写 `items.creation_seq <= anchor.creation_seq`。排队的后续用户输入可能在当前 Run 结束前就已经入库。这样的输入属于后续 Run，不应进入分叉。

Runtime 固定截止点内生效的编辑和重新生成关系。截止点之后发生的替换不能隐藏当时选中的旧回答。Context 和界面需要使用同一条固定历史路径。[当前回答替换校验](../../runtime/eidos_runtime/persistence/response_actions.py#L106)

历史保存需要有界分批读取。Runtime 不能用只返回最近 200 条的 `session/read` 响应，也不能用已经受模型预算裁剪的 `ContextFacts` 作为完整复制源。Runtime 应从持久记录读取完整前缀，并保留原始正文和引用。

建议流程如下：

1. Runtime 校验来源、回答和目标环境，并保存 `operationId` 与请求摘要。
2. Runtime 通过 SQLite 一致读视图分批生成不可变历史块。存储过程核验 Blob 校验值，并保护正在捕获的资源不被清理。
3. 同目录路径在最终短事务中创建 Session、提交历史清单与资源引用、记录来源，并写入 Event/Outbox 和操作结果。
4. 独立 Worktree 路径通过现有 durable lifecycle 创建目标环境。它只在环境核验完成后发布可用的新 Session。
5. Runtime 处理失败、取消和重启时释放未发布资源，或保留需要对账的环境记录。Runtime 不发布半份历史。

捕获期间的源删除、Rewind、引用撤销和正文变化必须被协调或在发布前被发现。源 Session 追加截止点之后的新消息可以继续，不应让一次已经固定的早期分叉失效。实现需要显式覆盖捕获与 GC 的竞争，不能只依赖内存中的父 ID。

同一个 `operationId` 和同一请求返回同一个新 Session。相同 ID 配不同截止点或目标环境返回冲突。Runtime 重启后的重试不能创建第二个 Session 或第二个 Worktree。用户之后删除了目标 Session，旧请求的重试不能复活它。

存储过程需要设置单块大小和总量上限。超限时，Runtime 明确返回失败，界面保留原会话。Runtime 不能静默把“完整继承”改成“只继承最近若干条”。模型调用的 Token Budget 是另一项限制，它不改变已保存历史的完整性。

## 8. 接口与调用链

建议接口只接收来源、回答和目标环境。Runtime 自己决定截止范围。

```json
{
  "method": "session/fork",
  "id": 31,
  "params": {
    "operationId": "fork-operation-id",
    "sourceSessionId": "source-session-id",
    "sourceItemId": "completed-answer-item-id",
    "target": { "kind": "same_workspace" }
  }
}
```

另一个目标类型是 `{ "kind": "new_worktree", "state": "current_workspace" }`。Runtime 不接受模型提供的任意目标路径或权限模式。

响应返回新 `SessionDto`、来源信息和历史条数等元数据。响应不返回待执行的新 Run，也不携带完整历史。Renderer 随后通过分页读取展示聊天。

最小调用链为：

```text
回答操作 → typed preload IPC → Main RuntimeClient
→ RuntimeServer Method Registry → SessionForkApplication
→ SessionHistoryRepository / 现有 Session 与 Worktree 创建
→ SQLite + Event/Outbox → Renderer 打开新 Session
```

`SessionForkApplication` 集中处理截止点、历史、目标环境、幂等和发布。Protocol 只验证输入与投影输出。Repository 负责事务和历史记录。Git 操作继续使用现有模块。Domain 不依赖 SQLite Row、JSON-RPC 或 Electron。

`session/read` 继续使用一条统一分页顺序。接口可以增加可选的 `historyRuns` 和 `historyItems`。真实 `runs`、`items` 保留原含义。继承记录使用独立 DTO 和稳定的展示 ID，并携带 `originSessionId`、`originRunId`、`originItemId`。Renderer 把两类记录合成 Feed，但不把历史 ID 交给停止、审批、编辑重发或 Checkpoint 控制接口。

分页游标必须识别继承记录与真实记录。大文本分页读取、消息引用选择器、历史搜索和工具内容预览也需要从目标 Session 的历史快照读取。它们不能回到父 Session 查询正文。

Context 通过同一历史读取模块获得“继承前缀 + 自有记录”。模型转换继续使用现有响应分组、Tool Result 顺序、Replay 校验、预算和压缩规则。原始故障记录可以展示；未闭合工具批次不能被投影成一组伪造的成功对话。

## 9. Desktop 交互

回答操作区增加“从这里分叉”。按钮支持键盘操作。正在流式输出的当前回答不提供这个操作。用户可以对较早的完整回答使用它。

非 Git 和 Projectless 来源可以直接创建同目录分叉。Git Local 来源提供“在当前目录继续”和“在独立工作树继续”。Managed 来源创建独立 Worktree。界面使用简短说明展示文件状态差别，不展示内部 Snapshot ID、Schema 或 Intent。

操作期间，界面只阻止重复提交同一分叉。原 Session 的执行和阅读可以继续。失败后，界面留在来源位置。成功后，界面刷新侧栏，打开新 Session，并聚焦空输入框。

新 Session 在历史与新输入的交界处显示“从『来源会话』的这次回答分叉”。用户可以打开来源并定位原回答。来源被删除时，界面显示来源已删除，但继承历史仍可读。

新 Session 可以继续从继承历史中的回答分叉。Runtime 使用目标 Session 的历史记录定位前缀，不要求祖先仍然存在。

## 10. 实施范围与验收

建议围绕这次目标完成一个 PR。实现可以分成三个可审查的 Commit：历史存储与迁移、Application/协议与模型上下文、Desktop 交互与当前文档。各阶段保留现有 `checkpoint/fork` 行为。最后一阶段再按需要复用它内部的环境创建逻辑。实现不进行目录搬迁或异步内核重构。

第一版范围包含聊天前缀、附件与模型回放、Local/Projectless 绑定、Git 独立 Worktree、嵌套分叉、删除与 GC、分页和 Desktop 入口。第一版不包含任意 Run 中间位置分叉、历史文件自动回退、分叉合并或额外分叉树页面。

验收需要覆盖以下行为：

| 验收场景 | 必须验证的结果 |
| --- | --- |
| 三轮聊天从第二轮分叉 | 新 Session 包含前两轮，排除第三轮；新 Session 没有自动运行 |
| 原 Session 继续、新 Session 继续 | 两条聊天互不追加内容；两个 Run 使用现有独立调度 |
| 后续输入已经排队 | 后续 Run 的用户消息不进入较早回答的分叉 |
| 多工具响应、模型回放和图片 | 工具调用与结果顺序正确，Replay 不丢失，附件不依赖原文件重读 |
| 旧回答被后来重新生成 | 新 Session 保留选中回答对应的历史路径 |
| 长历史与压缩 | 全部记录可分页查看；模型继续遵守预算；更晚摘要不泄漏后续聊天 |
| 删除来源、重启与 Blob GC | 新 Session 的历史、图片和输入引用仍可用 |
| 分叉后再次分叉 | 新前缀顺序正确，祖先删除不破坏读取 |
| 批准、Grant、完全访问与写入证据 | 历史记录不能为新 Run 授权，也不能成为当前文件的有效写入证据 |
| 未知副作用 | 新 Run 仍受当前工作区的 Reconciliation 约束 |
| 重复请求、SQLite 失败与 Outbox 重投 | 操作不重复创建资源，也不发布部分 Session |
| 捕获时删除、撤销与 GC | 操作要么完整发布，要么明确失败，不能返回缺失正文的成功结果 |
| Worktree 创建、来源变化和重启 | 来源目录不变；目标状态可核验；重试复用原 durable identity |
| Projectless 分叉 | 新 Session 保持无项目属性，源目录不变，Session 删除不删除共享文件 |
| Desktop 实际操作 | 旧回答入口、键盘、长历史、焦点、失败恢复和新 Session 定位均符合规则 |

实现阶段需要执行相关 Runtime 定向测试、Ruff、Deptry、Python/TypeScript 协议 Fixture 校验，以及相关 Renderer/Main 测试和必要构建。新增 preload 暴露时，执行 Electron smoke。Worktree 生命周期变化时，执行对应原生定向验证。执行者只在用户明确要求时运行 Full Suite。

实现需要同步 `docs/current/architecture.md`、`docs/current/capabilities.md` 和 `docs/current/limitations.md`。实现不需要新增第三方依赖。SQLite、Pydantic、JsonBlobStore 和现有 Git/Worktree 模块承担存储、类型与文件环境机制。Eidos 继续拥有 Session、权限、Reconciliation、事务和恢复规则。

当前实现已经接通回答入口、协议、Application、历史持久化、模型上下文和独立 Worktree。`session_forks` 保存来源元数据；每条 `session_history_entries` 保存带 SHA256 校验的 Blob 引用；`session_history_resources` 独立保留附件、工具结果和私有回放。新 Session 不创建 Run。单次分叉最多保留 20,000 条记录，记录与独立资源的未压缩内容合计最多为 128 MiB。原 Checkpoint Fork 的行为保留。

实现固定分叉时的文件快照。来源在快照完成后的新修改不会使已捕获的目标文件改变。源 Session 存在未确认副作用时，Runtime 拒绝分叉。Worktree 创建后的提交失败保留 durable lifecycle，供同一请求重试。继承记录支持分页、再次分叉、来源删除、附件保留、私有回放和独立压缩。验证记录见 `docs/current/limitations.md`。
