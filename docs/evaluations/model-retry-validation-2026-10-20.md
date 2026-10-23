# 模型重试第一、二阶段交付记录

> 文档分类：验证与交付记录。本文只证明记录时的验证范围，不代表后续版本或完整回归已经通过。当前行为请以 [当前说明](../README.md#当前说明)、源码和测试为准。

本次实现基于开始任务时的工作区，保留了已有未提交修改。系统没有提交、推送或修改用户实际数据目录。

## 实现范围

普通 SamplingRuntime（含子 Agent）统一管理请求组。系统默认最多发送 6 次请求。系统在首次临时失败后开启 180 秒恢复窗口，并让等待与后续请求共同消耗窗口。Tenacity 计算退避与随机等待，Pydantic AI 解析 Retry-After。前台作用域关闭底层重复重试，SDK 保持 max_retries=0；辅助调用保留原策略。

系统区分临时故障与明确的余额、配额、认证和参数错误。系统不再把前台重试耗尽交给外层容量恢复，因此不会重新领取次数。系统用完预算后记录任务失败。本轮确认方案与 AGENTS.md 后增的无限恢复规则冲突，实施采用了“当前任务优先于仓库规则”的顺序。

系统为每次生成建立独立 Attempt 和草稿。系统复用冻结上下文，保留失败草稿，拒绝旧 Attempt 的迟到增量，并在完整响应校验后执行工具。系统不会因为连接恢复而重跑已完成的文件或 Shell 操作。Runtime 重启会保留已消耗的次数、输入、等待时间及期限；系统不会提前消费恢复期间的新消息。

## 数据与兼容性

Schema 30→31 为 model_attempts 增加 request_group_id、retry_index、retry_deadline_at、assistant_item_id，以及组内序号唯一索引。旧记录保持空关联。系统通过原 SQLite 事务和 Event/Outbox 提交草稿失败及重试决定。回退旧 Runtime 前需要恢复升级前备份。

系统没有增加依赖，也没有修改 pyproject.toml、uv.lock、启动链、公共 RPC 或 Desktop 布局。系统没有新增倒计时、暂停状态或继续按钮。特殊 RunFinalizer、审批、压缩、标题与记忆调用保持原来的预算。

## 定向验证

本轮验证使用临时数据目录、模拟 Provider 和合成内容。测试没有读取真实凭据，也没有产生模型费用。Shell 用例在临时 Workspace 中启动真实进程，验证断流后不会重复追加文件；该结果不代表 Seatbelt 边界已重新验收。

执行者运行了以下定向检查：

- 重试与恢复新用例：`uv run --locked pytest runtime/tests/test_model_retry_recovery.py -q`
- 持久化、快照、终止与恢复：`uv run --locked pytest runtime/tests/test_model_persistence.py runtime/tests/test_runtime_architecture.py runtime/tests/test_model_attempt_context_snapshot.py runtime/tests/test_run_termination.py runtime/tests/test_loop_recovery.py -q -k 'not repeated_length_finish_stops_after_one_repair and not tool_input_kind_failure_preserves_tool_and_actual_terminal_attempt'`
- Schema 与渠道升级：`uv run --locked pytest runtime/tests/test_storage_schema.py runtime/tests/test_channels.py -q`
- Adapter 与自定义模型：`uv run --locked pytest runtime/tests/test_pydantic_ai_client.py runtime/tests/test_custom_model_providers.py -q`
- 容量恢复分支：`uv run --locked pytest runtime/tests/test_run_capacity_continuation.py -q -k provider_failures`

测试覆盖两条实际 Adapter 与模拟 HTTP/SSE、断流和残缺工具参数、503、配额、过长 Retry-After、请求卡住与关闭、六次上限、独立草稿、文件和 Shell 不重复执行、取消、其他 Session 继续执行、重启预算延续、新消息保留，以及迁移和事务失败回滚。

本轮定向验证通过 228 项不同用例：新增重试回归 37 项；既有持久化、快照、终止与恢复 44 项；Schema 与渠道 65 项；Adapter 与自定义模型 79 项；容量分支 3 项。执行者在最后的小范围超时分类修订后又验证了相关超时与流关闭用例，并重新运行全部 37 项新回归。旧测试中的已知失败另列于下文。

本次范围涉及 29 个文件，相对开工快照约新增 1,300 行、删除 90 行，大部分新增行属于参数化回归和 HTTP/SSE 测试数据。这个范围不包含工作区其他既有改动。

执行者还运行了修改文件的 Ruff、`uv run --locked deptry runtime/eidos_runtime`、`scripts/audit-python.sh` 和 `git diff --check`。两组锁定 Python 依赖审计均未发现已知漏洞。

## 已有失败与未验证内容

执行者在扩大到相关旧测试时发现以下四项失败，并在开工前快照中逐项复现。执行者保留了这些已有行为，未将它们混入本次修复：

| 测试 | 开工快照的实际差异 |
|---|---|
| test_repeated_length_finish_stops_after_one_repair | 旧断言要求两次调用；当前容量恢复使脚本消费三次 |
| test_tool_input_kind_failure_preserves_tool_and_actual_terminal_attempt | 旧断言要求工具契约错误；当前记录为 empty_response |
| test_crashed_durable_intent_is_reconciled_without_replay | 旧断言要求未知操作阻塞后续工具；当前规则已移除该阻塞 |
| test_read_step_requires_explicit_workspace_refresh_to_clear_reconciliation | 旧断言要求恢复核验门；当前规则已移除该门 |

执行者没有运行 Full Suite、真实 Provider、人工断网、Electron、DMG 或发布验证。模拟 HTTP/SSE 结果不能替代真实端点验收。
