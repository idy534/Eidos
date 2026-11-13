# 记忆系统优化详细设计

日期：2026-11-14。状态：已在分支 `feat/context-history-search` 上实施，尚未提交，也没有真实模型评测基线。核对基线是提交 `58bda691` 与当时的工作区。

### 实施与原方案的差异

- D1 按用户决定改为与 Claude Code 相同的上限：`MEMORY.md` 取前 200 行或 25 KB。注入时两个范围合计使用这一上限，全局保留 40%；小窗口模型另限可用输入预算的 10%。
- 索引行保留 `- [path](path): summary` 链接格式。1.2 节说记忆页不使用链接，这一判断有误：记忆页用 `onNoteLink` 从索引打开笔记。
- 成功的 `memory_edit` 模型结果原本不带说明文字。实施后，只有出现相似笔记或 `indexHidden` 时才带 `summary`，以便模型看到合并与归档建议。
- 2.5 节建议在 `# Memory` 段增加一句引导。实施时没有修改提示词，引导写在 `search_history` 的工具说明中，避免另做提示词回放。
- 5.2 节的上下文面板“已截断”标记没有实施。面板只显示分类合计，没有逐来源列表，增加标记需要扩展上下文统计协议。模型侧已经通过续读说明知道规则被截断。
- 3.3 节的整理入口在所选位置打开新草稿并填入请求，由用户发送，没有直接启动 Run，也没有做成系统 Skill。
- 第 7 项新建了 `scripts/evaluate-memory-cases.py`，没有改动原有的 `evaluate-memory-learning.py`。原脚本继续承担 S 组。

> 文档分类：设计与实施记录。当前行为以 [当前记忆](../current/features/memory.md)、源码和测试为准。本文中的字段名、常量和阈值均为建议值。

本文细化七项优化。前六项改动产品行为，第七项是评测，用来给前六项提供基线和验收依据。本文不恢复 v40 移除的后台模型整理、候选确认和历史回填。

## 0. 总览

| 项 | 主要改动位置 | SQLite | 模型工具契约 | 新依赖 |
| --- | --- | --- | --- | --- |
| 1 索引容量与排序 | `memory/service.py` | 无 | 索引正文变化；`memory_edit` 结果增加可选字段 | 无 |
| 2 同项目会话检索 | `tools/search_history.py`、`persistence/history_search.py`、`tools/read_tool_output.py` | 无 | 两个工具各增加一个可选参数 | 无 |
| 3 整理与遗忘 | `memory/`、记忆页 | v53 升 v54，增加两列 | `memory_read`、`memory_search`、`memory_edit` 各有小幅增加 | 无 |
| 4 局部写入 | `tools/memory.py` | 无 | `memory_edit` 增加两个 action 与一个参数 | 无 |
| 5 规则文件预算 | `context/project_rules.py`、`model/instructions.py` | 无 | 无 | 无 |
| 6 来源分类与写入检查 | `memory/repository.py`、`memory/service.py` | 无 | 无；Desktop DTO 的来源枚举增加一个值 | 无 |
| 7 评测 | `scripts/` | 无 | 无 | 无 |

建议顺序：先跑第 7 项的基线，再做 1、4、5（都不改 Schema），然后做 2、3、6，最后做 5 的第二阶段。每项对应独立 Commit。第 3 项同时涉及 Schema、协议和界面，按 AGENTS.md 第 24 节应拆成三个 Commit。

需要你决定的事项集中在[第 8 节](#8-需要决定的事项)。

---

## 1. 索引容量与排序

### 1.1 现状

- 投影预算是 `min(2000, 可用输入 × 5%)` 个估算 token（[service.py:686](../../runtime/eidos_runtime/memory/service.py)）。文档写的 200 行、24 KiB 是 `MEMORY.md` 文件的上限，投影先被 token 预算截断。
- 行格式是 `- [path](path): summary`，路径写了两次（[service.py:177](../../runtime/eidos_runtime/memory/service.py)）。
- 笔记按路径字母序排列（[service.py:173](../../runtime/eidos_runtime/memory/service.py)）。预算不足时，字母序靠后的笔记不可见。
- 当前项目范围先占预算，全局范围使用剩余部分（[service.py:694](../../runtime/eidos_runtime/memory/service.py)）。项目笔记多时，全局的个人偏好会被挤掉。
- 投影标题写 `project/MEMORY.md`，工具参数却用 `scope: "current"`。两处用词不一致。
- 模型写入后不知道索引是否已经放不下。

我用 Runtime 的 `estimate_text_tokens` 测了可见条数：

| 摘要长度 | 现格式，2,000 token | 新格式，2,000 token | 新格式，4,000 token |
| --- | --- | --- | --- |
| 28 个汉字 | 45 | 56 | 113 |
| 84 个汉字 | 20 | 22 | 44 |
| 240 个汉字 | 7 | 15 | 31 |

新格式指 1.2 的行格式加上 120 字符的摘要上限。

### 1.2 设计

**行格式。** 每行写成 `- <path> — <summary>`。路径只写一次。索引里的摘要压成单行，超过 120 字符时截断并加省略号。笔记元数据和搜索结果仍保留完整摘要。记忆页用文件列表切换笔记，我没有找到它处理索引内链接的代码，所以去掉链接不影响页面导航。

**分组与排序。** 同一范围内按类型分组，组顺序是 `feedback`、`user`、`project`、`reference`。组内按 `updated_at` 倒序，再按路径。纠正和个人偏好每个任务都可能用到，所以排在前面；项目事实和外部引用可以靠搜索找到。

排序只使用写入时确定的字段，不使用"最近读取"。索引位于请求的第一个上下文位置（[builder.py:588](../../runtime/eidos_runtime/context/builder.py)）。如果读取会改变顺序，下一个 Run 的 Provider 前缀缓存会从头失效。写入本来就会改变索引，所以按更新时间排序不增加失效次数。

**预算分配。** 总预算 `B = min(B_max, 可用输入 × 5%)`。两个范围都可读时，全局保留 40%，当前项目保留 60%。一个范围用不完的份额让给另一个范围。只有一个范围可读时，它使用全部预算。`B_max` 建议从 2,000 提到 4,000，见第 8 节。

**范围标题。** 标题改成 `Scope "current" — MEMORY.md:` 和 `Scope "global" — MEMORY.md:`，与工具参数的取值一致。

**溢出提示。** 投影被截断时，结尾写明数量，例如：`12 more notes are not listed. Use memory_search, or merge and archive notes to keep the index short.`。`memory_edit` 成功后，Runtime 按 `B_max` 重新计算不可见条数。条数大于零时，模型结果增加 `indexHidden`，说明文字写明有多少条已不在索引里，并建议合并或归档。Runtime 不拒绝写入。

`indexHidden` 按固定的 `B_max` 计算。小窗口模型的实际预算更小，真实不可见条数可能更多。这个字段表达的是"索引已经偏大"，不是精确计数。

**渲染入口。** `_render_index` 增加可选的 token 预算参数。记忆页和导出不传预算，继续使用 200 行、24 KiB 上限。两处的分组、排序和行格式保持一致，所以每个范围的投影都是该范围 `MEMORY.md` 的前缀加结尾提示。

### 1.3 契约与兼容

- 本项不改 SQLite。索引是派生内容，升级后第一次渲染即使用新格式。
- `memory-tool-model-v1.json` 增加 `indexHidden` 的用例。`memory-v2.json` 中如果包含索引正文，需要同步。
- 工具结果 Schema 变化后，记忆工具的 `sourceVersion` 从 3 升到 4。升级前处于待审批状态的 `memory_edit` 会被拒绝直接执行，这是现有的契约变化规则。
- 索引正文变化后，每个 Session 的 Provider 前缀缓存需要重建一次。已持久化请求的重试继续使用原快照。
- 回退时还原源码即可，没有数据需要迁移。

### 1.4 测试

- 分组顺序、组内顺序、摘要截断、路径中特殊字符的处理。
- 预算分配：只有项目笔记、只有全局笔记、两者都超预算、一方有剩余。
- 连续读取笔记后，投影正文逐字节不变。
- `indexHidden` 在超出和未超出时的取值，以及模型结果的字段精简规则。
- 同一 Run 内索引固定、显式隐私撤销立即生效这两项现有行为不退化。

---

## 2. 同项目会话检索

### 2.1 现状

- `search_history` 只查询当前 Session（[history_search.py:51](../../runtime/eidos_runtime/persistence/history_search.py)）。
- Desktop 全局搜索已有全量索引。`search_documents` 保存所有会话的用户消息和助手消息，并有 trigram FTS5。`SearchRepository.search` 已支持 `project_id` 和 `projectless` 过滤，也已排除子 Agent Session（[search.py:168](../../runtime/eidos_runtime/persistence/search.py)）。
- `read_tool_output` 只能读取当前 Session 的内容。

所以本项不需要新索引、新表或模型调用，主要工作是授权和契约。

### 2.2 工具契约

`search_history` 输入增加 `scope`：

```json
{ "query": "迁移方案", "limit": 8, "scope": "project" }
```

`scope` 取 `"session"`（默认，行为不变）或 `"project"`。来自其他会话的命中增加三个字段：

```json
{
  "itemId": "…", "kind": "assistant", "snippet": "…",
  "readCallId": "…", "stream": "context",
  "sessionId": "…", "sessionTitle": "迁移方案讨论", "date": "2026-11-02"
}
```

`read_tool_output` 输入增加可选的 `sessionId`。它只能与 `stream="context"` 一起使用，用来分页读取命中消息的原文。

### 2.3 范围解析

模型不能指定项目。Runtime 从当前 Run 的根 Session 解析项目，解析方式与记忆范围相同（[repository.py:54](../../runtime/eidos_runtime/memory/repository.py)）：

- 项目 Session 搜索同一项目的会话，包含该项目的 Worktree 会话。
- 无项目 Session 搜索其他无项目会话。

`scope="project"` 的结果排除以下内容：

| 排除项 | 原因 |
| --- | --- |
| 当前 Session | 模型用 `scope="session"` 查询它，那条路径还能搜到工具结果。 |
| 子 Agent Session | 现有全局搜索已排除。 |
| 渠道绑定的 Session（`channel_sessions`） | 正文包含外部参与者的输入，不应进入其他会话的模型上下文。 |
| 工具参数与结果 | 全局索引本来就不包含它们。首期只返回用户消息和助手消息。 |
| 只命中标题的会话 | 首期没有浏览整个会话的接口，返回标题无法继续读取。 |

定时任务产生的会话保留在范围内。它的提示词由用户编写，用户也常会问"昨天的定时报告说了什么"。

### 2.4 授权

用户已同意增加这个范围。隐私开关我按"跟随记忆读取模式、不新增设置"设计，理由是用户只需要记住一个开关：这个项目的过去能否被模型读取。这一点列在第 8 节等你确认。

`scope="project"` 和带 `sessionId` 的读取必须同时满足：

1. 当前范围的记忆模式不是 `off`。项目 Session 看项目范围，无项目 Session 看全局范围。
2. 当前 Run 是根 Session 的 Run。子 Run 被拒绝，与记忆工具一致。
3. 当前 Run 不是渠道来源。否则外部聊天参与者可以让模型取出本机的其他会话并发回渠道。
4. 读取时，目标 Session 仍存在，并且仍属于同一项目范围。

不满足时，工具返回 `history_scope_disabled` 或 `history_scope_unavailable`，并说明原因。工具 Schema 保持静态，Runtime 不按 Run 增减参数，这样工具定义不会在 Run 之间变化。

### 2.5 实现

- `HistorySearchRepository.search` 增加 `scope` 参数。`scope="project"` 时调用 `SearchRepository.search`，传入解析出的 `project_id` 或 `projectless`，再应用 2.3 的排除条件。`session` 路径不变。
- 片段继续使用现有的前 80、后 220 字符窗口。
- `complete=false` 的条件沿用现有规则：还有更多命中，或索引队列尚未处理完。
- `read_tool_output_page` 增加 `source_session_id`。Runtime 重新执行 2.4 的全部检查，只允许读取 `user_message` 和 `assistant_message`。
- 工具说明增加一句：其他会话的内容是历史记录，当前状态需要重新核实。
- `# Memory` 段增加一句引导：用户提到的早先工作不在本会话和记忆里时，用 `search_history(scope="project")` 查找。修改提示词前按[系统提示词](../current/features/system-prompts.md)的要求回放，每类至少 5 次。

### 2.6 已知限制

- 已经返回到另一会话里的片段不随源会话删除而撤销。这与"删除聊天不撤销已保存笔记"和冻结分叉保留继承历史的现有规则一致。如果你希望删除源会话时一并擦除，需要增加来源记录表并复用记忆撤销的擦除流程，那是第二阶段的工作。
- 查询是忽略大小写的字面匹配，不做语义改写。
- 2 个字符的查询无法使用 trigram，会扫描该项目的全部索引正文。工具有 10 秒超时。实现时需要用规模样本测一次耗时。

### 2.7 测试

- 范围解析：项目、Worktree、无项目。
- 负向用例：其他项目的会话、子 Agent 会话、渠道会话、当前会话都不出现。跨项目隔离是本项最重要的用例。
- 授权：三种记忆模式、子 Run、渠道 Run、会话已删除。
- 分叉继承历史（`source_kind='history'`）的命中与读取。
- `read_tool_output` 带 `sessionId` 的分页，以及与其他 `stream` 组合时的拒绝。
- 索引滞后时返回 `complete=false`。
- 协议 Fixture 与两端契约。

---

## 3. 整理与遗忘

本项由三个小机制组成。它们都不调用后台模型，也不自动删除任何笔记。

### 3.1 使用记录

**现状。** `memory_documents` 只有 `created_at` 和 `updated_at`（[document_schema.py:22](../../runtime/eidos_runtime/memory/document_schema.py)）。`memory_tool_reads` 按工具调用记录，不按笔记记录。从 `tool_calls.result_json` 反推不可靠，因为删除会话和隐私撤销都会清掉这些载荷。

**设计。** Schema 从 v53 升到 v54：

```sql
ALTER TABLE memory_documents ADD COLUMN last_read_at INTEGER;
ALTER TABLE memory_documents ADD COLUMN read_count INTEGER NOT NULL DEFAULT 0;
```

`MemoryService.track_tool` 已经在每次记忆读取后写入 `memory_tool_reads`。同一事务里增加一条更新：工具是 `memory_read` 且目标是主题笔记时，更新这两列。读取 `MEMORY.md` 不计入。这次更新不改 `generation`、`updated_at` 和索引。

**使用位置。**

- 记忆页的笔记详情在现有"来源与版本"区域显示上次读取时间。
- `memory_search` 增加 `order: "oldest"`，在省略 `query` 时按 `COALESCE(last_read_at, updated_at)` 升序列出。整理时模型用它找最久没用过的笔记。
- `memory_read` 的模型结果增加 `updated`，格式是 `YYYY-MM-DD`。10 月 26 日的精简去掉了模型结果里的时间戳。这里加回一个日期字段，因为模型判断事实是否过期需要知道笔记的年龄。

**迁移。** 迁移在事务中执行，失败时回滚字段和版本号。完整备份的可接受版本增加 v54。旧 Runtime 不能打开 v54 数据，与现有升级规则一致。当前记忆说明里的版本号仍写 v44，源码的 `SCHEMA_VERSION` 是 53，实现时一并更正。

### 3.2 新建时的相似提示

**设计。** `memory_edit` 新建路径成功后，Runtime 在同一范围内查找相似笔记：

1. 取该范围未归档的笔记，最多 500 篇，按更新时间倒序。
2. 用 RapidFuzz 的 `WRatio` 比较新笔记与每篇旧笔记的"路径 + 标题 + 摘要"。
3. 返回分数不低于阈值的前 3 篇。

模型结果增加 `similar: [{path, summary}]`，说明文字建议模型在主题相同时合并，再归档多余的一篇。归档可恢复，所以先保存再提示的风险很低。

这里不使用现有的 FTS 查询。`memory_search` 把查询按空白切词，一段没有空格的中文摘要会变成一个整体短语，只能匹配完全相同的文本。RapidFuzz 是已锁定依赖，按字符比较，对中文可用。500 篇、每篇几百字符的比较量很小。

阈值初值取 85，需要用第 7 项的"更新与去重"用例校准。阈值过低会让模型反复合并不相关的笔记。

### 3.3 用户触发的整理

记忆页为当前选中的范围增加"整理"入口。点击后，Desktop 在所选位置创建一个普通 Session，并用固定提示词启动一个普通 Run。提示词要求模型：

1. 用 `memory_search(order="oldest")` 分页列出笔记。
2. 读取候选笔记，合并主题重复的笔记。
3. 在项目 Session 中，对照工作区核实会变化的项目事实，更正过期内容。
4. 归档不再有用的笔记，不做彻底删除。
5. 在回复中列出每一项改动及理由。

这是用户发起的普通 Run，所以现有规则全部适用：`auto` 模式直接写入，`read` 模式逐项审批并显示 Diff，`off` 模式下入口禁用。每次改动都有版本，归档可以在记忆页恢复。

固定提示词可以做成系统 Skill，这样用户也能在聊天中按名称调用。我没有核对系统 Skill 的打包位置。成本高的话，首期由 Desktop 直接发送预置提示词。

定时任务没有记忆写工具，所以整理不能被定时触发。这是有意保留的边界。

### 3.4 测试

- v53 升 v54、失败回滚、v54 备份恢复、旧备份逐级升级。
- 读取主题笔记更新两列；读取 `MEMORY.md`、搜索和写入不更新。
- 更新使用记录后，`generation` 与索引正文不变。
- 相似提示：相同主题不同措辞、中英文、明显无关、归档笔记不参与、超过 500 篇时的边界。
- `order="oldest"` 的分页与游标。
- 记忆页入口在三种模式下的状态，以及键盘操作。

---

## 4. 局部写入

### 4.1 现状

`memory_edit` 只有 `write` 和 `delete`（[tools/memory.py:68](../../runtime/eidos_runtime/tools/memory.py)）。更新已有笔记必须先在本 Run 读取（[service.py:359](../../runtime/eidos_runtime/memory/service.py)），再提交完整正文。单篇上限是 65,536 字符。给一篇长笔记加一句话，模型要读一遍、再写一遍全文；重写时也可能漏掉原有段落。

### 4.2 工具契约

```json
{ "scope": "current", "path": "project/build.md", "action": "append",
  "content": "- CI 使用 pnpm 9，本地需要同版本。" }

{ "scope": "current", "path": "project/build.md", "action": "replace",
  "oldText": "测试命令是 npm test", "content": "测试命令是 pnpm test" }
```

| action | `content` | `oldText` | 笔记不存在时 | 是否要求先读取 |
| --- | --- | --- | --- | --- |
| `write` | 完整正文 | 不允许 | 新建 | 已有笔记要求，与现在相同 |
| `append` | 追加的文字 | 不允许 | 按新建处理 | 不要求 |
| `replace` | 替换后的文字，可以为空 | 必填，必须恰好出现一次 | 返回 `memory_document_not_found` | 不要求 |
| `delete` | 不允许 | 不允许 | 返回 `memory_document_not_found` | 与现在相同 |

### 4.3 执行流程

合并在 `MemoryToolRuntime.invoke` 中完成，`MemoryService.edit` 的发布事务不变。

1. Runtime 用 `operationId` 查询 `memory_actions`。已有记录时直接返回原结果，不再合并。这一步防止重启后把同一段文字追加两次。
2. Runtime 在一个事务里读取当前版本号和正文。
3. `replace` 统计 `oldText` 的出现次数。零次返回 `memory_text_not_found`，多次返回 `memory_text_ambiguous`，并提示模型带上更多上下文。
4. Runtime 生成合并后的正文。`append` 保证原正文与新文字之间恰好有一个换行。
5. 合并结果超过 65,536 字符时返回 `memory_note_too_large`，提示模型拆分笔记。
6. Runtime 用合并后的正文和第 2 步的版本号构造现有的 `MemoryEditRequest(action="write")`。之后的敏感内容检查、授权、Durable Intent、发布事务与版本检查全部沿用现有路径。

第 2 步读到的版本号就是 CAS 基准。`append` 不依赖旧正文。`replace` 的唯一匹配是在同一个版本上判定的。发布事务会再次核对版本号，期间有其他写入时返回 `memory_revision_conflict`，模型可以重试。

### 4.4 模式与审批

- `auto` 模式：直接写入，与 `write` 相同。
- `read` 模式：走普通审批。Diff 是旧正文到合并正文的 unified diff。
- `off` 模式：`append` 允许。Runtime 在内部合并，审批预览只显示追加的文字，旧正文不发给模型。`replace` 被拒绝并返回 `memory_read_disabled`，因为模型不可能知道旧文字。
- 待审批操作重启恢复时，Runtime 用审批请求里保存的 `expectedRevision` 重新合并。当前版本与它不同时，现有逻辑使审批失效并返回冲突。

### 4.5 契约与兼容

- 输入 Schema 增加两个 action 和 `oldText`。`sourceVersion` 随第 1 项一起升到 4。
- 模型结果不变，仍是 `path` 与 `revision`。
- 工具说明增加一句：给已有笔记加事实用 `append`，更正用 `replace`，新建或整体重写用 `write`。
- 本项不改 SQLite。回退时还原源码即可。

### 4.6 测试

- `append`：新建、已有、换行处理、空内容被拒绝。
- `replace`：零次、一次、多次匹配，替换为空，跨行文字。
- 大小上限、敏感内容出现在追加文字中。
- 两个 Run 并发修改同一笔记时的冲突与重试。
- 重启后重放同一 `operationId` 不产生第二次追加。
- 三种模式下的审批路径，以及 `off` 模式的预览不含旧正文。
- 审批等待期间目标版本变化。

---

## 5. 规则文件预算

### 5.1 现状

- 规则解析沿工作区根到 cwd 逐级选一个文件，共用 32 KiB 预算，从根目录开始扣（[project_rules.py:46](../../runtime/eidos_runtime/context/project_rules.py)）。
- 超出预算的文件按字节前缀截断，可能停在句子中间。
- 本仓库工作区的 `AGENTS.md` 是 38,473 字节，约 12,500 个估算 token。Eidos 加载它时保留前 32 KiB，§31.3 之后的内容被截掉，包括"自动恢复与模型上下文"。
- 根文件用完预算后，更深目录的规则得到 0 字节。根规则却写明更具体的规则优先。
- 解析只覆盖根到 cwd。cwd 默认是工作区根，所以 `runtime/AGENTS.md` 在默认情况下不会加载（[engine.py:562](../../runtime/eidos_runtime/runtime/engine.py)）。
- 解析结果里有 `RULE_BUDGET_TRUNCATED` 警告。我在 Renderer 里没有找到显示它的代码。

### 5.2 第一阶段：分配、截断与提示

**分配顺序。** 候选文件的选择规则不变。预算从最深的目录向根分配。输出顺序仍是根在前、最具体的在后。这样最具体的规则最后被截断。

**截断方式。** 截断点退到允许范围内的最后一个换行。被截断的规则层末尾追加一行说明。下面是格式示例，数字是占位：

```text
[Truncated by Eidos: the first <N> lines of <path> are shown (<included> of <total> bytes). Read the file from line <N+1> for the rest.]
```

模型可以用文件读取工具取得剩余部分。说明文字计入预算。行号从已包含内容的换行数得出，`RuleSourceSnapshot` 不需要新字段。

**总预算。** 建议从 32 KiB 提到 64 KiB，见第 8 节。预算只在文件确实很大时才被用掉，小项目不受影响。按 64 KiB 计算，本仓库的根规则可以完整加载，约 12,500 个估算 token。第二阶段加上 `runtime/AGENTS.md` 后合计约 13,300 个。

**界面。** 上下文面板的记忆分类里，被截断的规则来源显示"已截断"。界面不增加说明文字。这一条可以单独成一个 Commit。

### 5.3 第二阶段：子目录规则按需加载

**目标。** 模型读写某个子目录的文件后，该目录链上的规则文件进入后续请求。

**设计。**

1. Runtime 从当前 Run 已完成的文件读写工具调用中取出目标路径，得到"已触及目录"集合。集合是持久事实的纯函数，不需要新表。Builder 可以在内存中缓存它，重启后重新计算。
2. `ProjectRuleResolver.resolve` 增加 `touched` 参数。解析范围是"根到 cwd 的目录链"加上"根到每个已触及目录的目录链"。
3. 预算优先分配给 cwd 链，再按首次触及的顺序分配给其他目录。按需加载的规则文件最多 8 个。
4. 新规则层的 ID 仍是 `project-rule:<相对路径>`，排在已有规则层之后。
5. 规则解析每个 Step 执行一次，Step 解析快照已经持久化规则内容，所以恢复和重试不需要额外处理。

**取舍。**

- 新规则层第一次出现时，Provider 前缀缓存从规则层位置重建一次。之后保持稳定。
- 规则层不属于对话历史，压缩不会丢掉它们。
- Shell 命令里的路径不解析。只有文件工具的结构化路径参数触发加载。具体工具名在实现时按 `tools/workspace.py` 核对。
- 现有的工作区边界检查继续生效：解析到工作区之外的候选文件被拒绝。

### 5.4 测试

- 根文件超预算时，深层文件完整保留，根文件被截断。
- 截断点落在行边界；多字节字符不被切开；说明里的行号正确。
- 输出顺序与规则层 ID 不变，优先级说明不变。
- 第二阶段：读取子目录文件后的下一个 Step 包含该目录规则；同一目录不重复加载；超过 8 个时的行为；符号链接指向工作区外；无项目 Session 不受影响。
- 快照恢复后规则层一致。

---

## 6. 来源分类与写入检查

### 6.1 现状

- 来源只有两类。用户消息记为 `explicit_user`，其余工具结果一律记为 `observed_verified`，包括网页搜索和 MCP 输出（[repository.py:150](../../runtime/eidos_runtime/memory/repository.py)）。
- 写入前只检查凭据类敏感内容。
- 自动任务和渠道输入已经被排除在自动学习之外。这一点比 Codex 的默认设置更严。

残留风险是：在用户发起的 Run 里，网页或 MCP 返回的文字诱导模型写入一条笔记。它的摘要之后会出现在每个请求的索引里。

### 6.2 设计

**来源分类。** 增加第三类 `observed_external`。判定在 `MemoryRepository.evidence` 中完成，依据是 `tool_calls.provenance_json`：

- `kind` 为 `mcp` 的工具结果属于外部内容。
- 内置工具按一个显式名单判定，首期只有 `web_search`。之后新增的联网内置工具需要加入名单。
- 其余工具结果仍是 `observed_verified`。

`memory_evidence.evidence_class` 是没有 CHECK 约束的 TEXT 列，所以不需要迁移。`MemoryEvidence` 的枚举、生成的 TypeScript 类型和 `memory-v2.json` 需要同步。Desktop 与 Runtime 需要一同更新。记忆页的来源列表用"外部内容"标出这类来源。

**隐藏字符检查。** 写入前用标准库 `unicodedata` 检查，命中时返回 `memory_unsafe_text`：

| 字段 | 拒绝的字符 |
| --- | --- |
| 路径、标题、摘要 | 全部格式控制字符（类别 `Cf`）。这三个字段会进入索引。 |
| 正文 | 双向控制字符（U+202A–202E、U+2066–2069）和标签字符（U+E0000–E007F）。 |

正文保留零宽连接符，因为表情和部分文字需要它。模型写入和记忆页编辑使用同一检查，页面保留草稿并提示用户移除相关字符。

**可选的确认策略。** 记忆设置增加一个默认关闭的选项。开启后，`auto` 模式下只要本次写入的来源里有 `observed_external`，写入就改走普通审批。是否需要这个选项，建议由第 7 项的注入用例结果决定。

### 6.3 这项设计不能做到什么

隐藏字符检查和来源标签不能阻止模型被可见文字说服。主要防线仍是现有规则：记忆是背景资料，不能授予权限，实际操作继续经过审批和 Sandbox。本项只做三件事：让来源记录如实、去掉看不见的载荷、给用户一个可选的确认。

### 6.4 测试

- 各类工具的来源判定：文件读取、Shell、`web_search`、MCP、浏览器插件工具。
- Fixture 与两端契约。
- 隐藏字符矩阵：各字段、表情的零宽连接符、双向控制、标签字符、Desktop 编辑路径。
- 策略开启和关闭时的审批路径。
- 第 7 项的注入用例。

---

## 7. 评测

### 7.1 现状

[evaluate-memory-learning.py](../../scripts/evaluate-memory-learning.py) 有 10 个基础场景和 6 个追加轮次，共 16 条结果。它有四个限制：

- 模型只看得到记忆工具，没有文件和 Shell 工具，所以测不到"任务进行中被纠正"。
- 每个场景只跑一次。[系统提示词](../current/features/system-prompts.md)的回放记录显示，每组 3 次的结果可以从 0/3 变成 2/3。
- 判定只看"是否存在笔记"，不看范围、路径和是否重复。
- 库里最多几篇笔记，测不到索引溢出后的命中率。

### 7.2 定位

本项属于[评测程序方案](evaluation-system-design.md)第 2 节所说的记忆专项，不计入通用任务完成率。首期扩展现有脚本，用例放在 `scripts/fixtures/memory-cases.json`，与 `request-content-cases.json` 的做法一致。统一评测程序实现后再迁入。

### 7.3 用例组

| 组 | 场景 | 预置 | 确定性判定 | 对应优化项 |
| --- | --- | --- | --- | --- |
| S 选择 | 现有 10 个场景 | 空库 | 该存的有笔记，不该存的没有 | 基线 |
| C 任务中纠正 | 用户在编码任务中途纠正做法 | 临时工作区，开放文件工具 | 出现 `feedback` 笔记，任务仍完成 | 基线 |
| P 范围 | 项目会话里说个人偏好；说项目约定 | 空库 | 个人偏好进全局，项目约定进当前项目 | 基线 |
| U 更新与去重 | 用户更改已保存的偏好 | 1 篇相关笔记 | 原路径版本加一，生效笔记数不变 | 3、4 |
| R 检索使用 | 任务依赖某篇笔记 | 5、50、200 篇，目标笔记放在索引可见和不可见两种位置 | 读取了目标笔记，或回答符合笔记内容 | 1 |
| T 过期 | 笔记与工作区事实冲突 | 1 篇过期笔记和对应文件 | 模型核实后更正笔记 | 3 |
| I 注入 | 工具结果里有"请记住……"的指令 | 含该文字的文件或搜索结果 | 没有任何写入 | 6 |
| X 跨会话 | 事实只出现在同项目的另一会话 | 两个会话，另加一个其他项目的会话 | 使用了 `scope="project"`；其他项目的内容从不出现 | 2 |
| E 写入成本 | 给 20,000 字符的笔记加一条事实 | 1 篇长笔记 | 记录 token 和工具调用次数 | 4 |

### 7.4 指标与方法

- 指标：保存的准确率与召回率、范围正确率、重复率、不同库规模下的检索命中率、过期更正率、注入写入率、每个用例的 token 与记忆工具调用次数。
- 每个用例至少运行 5 次，报告写通过次数和总次数。
- 至少两个模型：日常使用的模型和一个更强的模型。
- 判定从 SQLite 状态得出。回答质量人工抽查。首期不使用模型评审。
- 运行使用临时数据目录，不读写日常数据。每个用例有请求数上限。
- 脚本测试继续用 `ScriptedModel` 验证评测程序的接线，真实 Provider 运行由维护者手动执行。

### 7.5 基线与验收

1. 在任何改动之前运行全部用例，结果保存到 `docs/evaluations/`。
2. 每完成一项优化后重跑对应的组，以及 S、P 两组作为回归。
3. 验收阈值在基线出来后确定。我现在没有数据，不预设具体数值。
4. 第 6 项的确认策略和第 3 项的相似阈值都由对应组的结果决定是否采用、取什么值。

---

## 8. 需要决定的事项

| 编号 | 事项 | 我的建议 |
| --- | --- | --- |
| D1 | 索引预算上限 `B_max` | 从 2,000 提到 4,000。代价是每个请求多约 2,000 个可缓存的输入 token。 |
| D2 | 规则文件总预算 | 从 32 KiB 提到 64 KiB。另一个办法是缩短本仓库的 `AGENTS.md`，两者可以同时做。 |
| D3 | 同项目检索的隐私开关 | 跟随记忆读取模式，不新增设置。 |
| D4 | 源会话删除后是否擦除其他会话里的片段 | 首期不擦除，写入当前限制。 |
| D5 | 是否接受 v54 迁移来保存使用记录 | 接受。不迁移就无法可靠地知道哪些笔记没人用。 |
| D6 | 外部内容确认策略 | 先做来源分类和隐藏字符检查，策略选项等注入用例的结果。 |

## 9. 本次设计的核对范围

- 我读了记忆、上下文构建、历史检索、全局搜索、规则解析和记忆工具的源码，以及当前说明和架构专题。
- 第 1 节和第 5 节的条数与 token 数由 Runtime 的估算器算出，是参考词表的估算值。
- 我没有核对：系统 Skill 的打包位置、文件工具的准确名称、浏览器插件工具的 `provenance` 取值、记忆页显示索引的渲染方式。这些在实现前需要确认。
- 本文没有运行任何测试，也没有用真实模型验证设计效果。
