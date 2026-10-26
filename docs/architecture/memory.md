# 长期记忆架构

Eidos 当前使用“服务端生成的 `MEMORY.md` 短索引 + 主题 Markdown 笔记”。前台模型在当前 Run 内保存笔记，Run 等待保存结果。系统已经移除旧的后台模型整理、候选确认和历史回填路径。本文的核对基线见[组成总览](harness-overview.md)。

## 读取与发布结构

![记忆架构](diagrams/06-memory.svg)

图源：[Mermaid](diagrams/06-memory.mmd)。用户和模型按相对笔记路径访问内容，服务端把路径映射到当前有效版本。

| 层 | 它保存什么 | 它负责什么 |
| --- | --- | --- |
| SQLite | Scope、模式、笔记、revision、证据、操作结果、抑制记录与撤销版本 | 数据库决定哪些笔记与版本有效。 |
| 私有版本文件 | `memory/scopes/<scope UUID>/revisions/<revision UUID>.md` 正文 | 文件提供不可变正文；孤立文件不能成为有效记忆。 |
| 生成索引 | 有效笔记的路径、摘要与继续读取提示 | `MEMORY.md` 提供短入口；模型不能编辑它。 |
| Context 投影 | 索引、版本引用与隐私撤销版本 | 系统控制预算，并在后续使用前复查撤销。 |
| 工具与 Desktop | 搜索、读取、编辑、模式与导出 | 两类入口复用 MemoryService 与版本检查。 |

## 范围与模式

服务端按项目身份决定全局和项目范围。Projectless 使用全局范围。项目 Worktree 共享所属项目记忆。子 Agent 暂不开放记忆工具，也不自动加载记忆索引。

| 模式 | 自动索引与模型读取 | 模型自动保存 | 用户明确要求保存 |
| --- | --- | --- | --- |
| `off` | 系统关闭。 | 系统关闭。 | 系统仍可按当前 Run 的明确请求授权保存。 |
| `read` | 系统开启。 | 系统关闭。 | 系统允许明确保存和更正。 |
| `auto` | 系统开启。 | 系统在当前 Run 中执行。 | 系统允许明确保存和更正。 |

新范围默认 `auto`。所有会话按所属范围的记忆模式工作；会话级临时记忆模式已经移除。用户在设置页管理笔记的能力与模型模式分开处理。

定时任务和渠道输入不能成为自动记忆证据；它们的工具目录不开放记忆写入。记忆不授予权限，也不会修改 Run 的审批模式。

## 一次写入的算法

1. 模型选择全局或当前项目范围，读取已有笔记后调用 `memory_edit`。模型可以自动更新用户以前写过的笔记。
2. Runtime 从本次 Run 已完成的读取或写入结果取得目标 CAS 版本，并自动记录用户消息与最近普通工具结果。模型不填写来源 ID、保存意图标志或更新版本号。
3. 服务检查目标范围、当前模式、内容、敏感信息与目标版本。来源记录提供追踪，不再充当正文事实审核或聊天生命周期依赖。
4. auto 范围使用统一的记忆写入授权。其他模式的明确保存继续走普通 Run 审批。工具提交 Durable Intent 后准备私有正文。
5. 发布事务复核目标版本、模式、显式隐私撤销与取消状态。不同笔记的 generation 变化不构成冲突。事务从最新笔记集合重建小索引。
6. 事务提交笔记、版本、归档标记、来源记录、操作结果和 `memory.changed` Event/Outbox。Main 校验通知后，页面刷新。

相同 operation ID 和相同请求返回原结果。相同 ID 的不同请求会被拒绝。正文、标题、摘要与类型相同时，服务不增加版本。模型省略标题或类型时保留现有值，摘要允许省略。新笔记使用默认标题、类型和正文首个非空行摘要。

模型删除笔记时，服务保留版本并把笔记归档，默认索引与搜索不再显示它。设置页显示归档状态，并提供恢复与彻底删除。模型可以搜索归档记录，读取后重新写入原路径恢复。设置页的删除仍会彻底遗忘全部版本。

Runtime 不会逐句审核模型写入的内容。模型需要继续区分事实、决定、提议和假设。Assistant 自述不会被记录为已核实的工具观察。用户仍可以检查来源、历史和正文。

## 读取、检索与预算

索引最多 200 行、约 24 KiB。ContextBuilder 的投影最多占可用输入预算的 5%，且最多 2,000 个估算 Token。系统优先放当前项目索引，并预留继续搜索和分页读取提示。

`memory_search` 使用 FTS5 unicode61、可用时的 trigram、短词 LIKE 和 RapidFuzz 重排。每个候选源最多读取 500 条。查询分页覆盖这个有界候选集，命中上限时返回 `searchLimited`。系统不把查询原文直接当作可执行 FTS 表达式。

`memory_read` 按路径和固定 revision 分页。单次最多返回 16,384 字符，单篇笔记最多 65,536 字符。系统没有旧的每 Run 三次读取配额。搜索不保证语义改写召回；模型仍可缩小查询或分页遍历文件列表。

工具结果分为完整存储结果、Desktop 视图和模型视图。模型视图只保留笔记定位、摘要或正文、编辑所需信息、分页位置和有效的错误说明。内部 ID、来源审计、时间戳、历史版本列表、userOwned 与撤销版本不会进入工具正文。归档记录按需带有 archived 标记。相对路径配合 `current/global` 表达范围；`revision` 继续用于固定分页和写入 CAS。

读取结果超过 48 KiB 序列化预算时，投影按完整字符返回正文前缀，并重新计算 `nextOffset`。模型继续调用 `memory_read`，不使用通用工具输出回读。完整结果与页面详情仍保留原始元数据。

## 撤销与遗忘

![记忆撤销时序](diagrams/07-memory-revocation.svg)

图源：[Mermaid](diagrams/07-memory-revocation.mmd)。用户彻底遗忘笔记或关闭范围记忆时，系统会使相关使用失效。

普通笔记更新、归档、恢复与聊天来源变化不推进隐私撤销版本。已经保存的笔记独立于原始聊天。不同 Run 可以继续使用各自已经固定的读取快照；同一笔记的写入仍检查版本冲突。

彻底遗忘与关闭记忆继续撤销相应快照和工具结果。服务在快照保存、重试、最终化和请求准入时检查隐私撤销版本，取消相关在途请求，并拒绝忽略取消的迟到响应。后续请求从合法事实重建。Context Usage 只读取预算和 usage 元数据。

ContextBuilder 从 `memory_tool_reads` 取得原始读取记录的隐私撤销版本，并把它保存在私有上下文元数据中。冻结分叉查询原始 Item 的记录。Provider 编码器只发送精简工具正文。

彻底遗忘会删除全部正文版本，并保留最小来源抑制记录。StorageMaintenance 由 Supervisor 管理，只清理撤销载荷、失效文件与 Blob，不调用模型。

## 存储与恢复限制

当前 schema 是 v43。v42→v43 保留笔记和历史，新增归档标记，并移除聊天来源对笔记的连带撤销。已遗忘和已隔离的数据不会恢复。v41→v42 删除会话级临时模式，保留已有笔记和已撤销的旧来源。v39→v40 按既定升级策略清空旧记忆并保留聊天等其他数据；v40→v41 修复历史自动版本的来源撤销。旧记忆接口和旧备份格式不兼容。升级细节和已有验证记录见[当前记忆](../current/features/memory.md)。

正文读取检查大小与 mtime。主动保留二者的篡改可能无法检出。遗忘不能追回已经发送给 Provider 的资料，也不能移除用户另存的导出、备份或自然语言复述。系统不承诺磁盘块安全擦除。

备份格式 `eidos-data-backup-v2` 保存一致的 SQLite、被引用正文、执行 Blob 和 Agent 定义。恢复导入新目录，接受 v40/v41/v42/v43 并完成升级，记忆模式设为 `read`。备份没有加密，也不包含 Workspace 和模型凭据。

## 源码与测试入口

| 机制 | 生产源码 | 相关测试 |
| --- | --- | --- |
| 严格契约与文件发布 | [contracts.py](../../runtime/eidos_runtime/memory/contracts.py)、[publication.py](../../runtime/eidos_runtime/memory/publication.py) | [笔记、CAS、分页与篡改](../../runtime/tests/test_memory.py) |
| 范围、证据与事务 | [service.py](../../runtime/eidos_runtime/memory/service.py)、[repository.py](../../runtime/eidos_runtime/memory/repository.py)、[工具](../../runtime/eidos_runtime/tools/memory.py) | [Run 集成与撤销](../../runtime/tests/test_memory_runtime.py) |
| 模型结果精简与分页 | [tool_projection.py](../../runtime/eidos_runtime/memory/tool_projection.py)、[context_projection.py](../../runtime/eidos_runtime/model/context_projection.py) | [模型结果契约与分叉恢复](../../runtime/tests/test_memory_tool_projection.py)、[自动更新与并发恢复](../../runtime/tests/test_memory_flow.py) |
| 来源与迁移 | [lifecycle.py](../../runtime/eidos_runtime/memory/lifecycle.py)、[document_schema.py](../../runtime/eidos_runtime/memory/document_schema.py)、[revocation_migration.py](../../runtime/eidos_runtime/memory/revocation_migration.py) | [撤销升级](../../runtime/tests/test_memory_revocation_migration.py) |
| 清理与备份 | [maintenance.py](../../runtime/eidos_runtime/db/maintenance.py)、[backup.py](../../runtime/eidos_runtime/db/backup.py) | [备份恢复](../../runtime/tests/test_data_backup.py) |

真实 Provider 的长期记忆质量仍需独立验收。返回[组成总览](harness-overview.md)。
