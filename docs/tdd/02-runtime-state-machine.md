# Runtime、队列与状态机

版本：v0.4

## 1. 核心实体

```text
Session
  └── Run
        ├── ExecutionSegment
        │     └── Step
        │           ├── ModelAttempt
        │           └── ToolCall 1..N
        ├── Approval
        ├── Artifact
        └── Event
```

- Run 是一次可暂停、恢复和排队的任务执行。
- Execution Segment 是一次连续执行预算，用户补充信息后创建新 Segment。
- Step 对应一次完整模型响应及其 ToolCall 批次。
- ModelAttempt 记录同一 Step 的模型传输重试。

## 2. 全局单执行器

队列规则：

- 可同时创建和保留多个 Run。
- `queued` Run 按 `enqueued_at, id` FIFO 获取执行槽。
- 当前 Run 不可抢占；它进入等待态或终态后释放执行槽。
- waiting_approval/waiting_user_input 不占执行槽。
- Approve、Reject 后继续或 user-input 恢复时，将 Run 以新的 `enqueued_at` 追加到队尾。
- 队列顺序持久化，重启后恢复。
- MVP 不支持优先级和手动调序。

调度器通过 SQLite 条件更新认领一个 Run：

```sql
UPDATE runs
SET status = 'running', executor_lease_id = :lease, started_at = COALESCE(started_at, :now)
WHERE id = (
  SELECT id FROM runs WHERE status = 'queued'
  ORDER BY enqueued_at, id LIMIT 1
)
AND NOT EXISTS (SELECT 1 FROM runs WHERE status = 'running');
```

单进程仍使用条件更新保证错误的重复调度不会并行执行。

## 3. Run 状态

```text
created
queued
running
waiting_approval
waiting_user_input
finalizing
succeeded
failed
stopped
canceled
```

主要流转：

```text
created -> queued -> running
running -> waiting_approval -> queued
running -> waiting_user_input -> queued
running -> finalizing -> stopped
running -> succeeded|failed|canceled
queued|waiting_approval|waiting_user_input -> canceled
waiting_approval -> waiting_user_input
```

`stopped` 是硬预算耗尽的终态：

- `stop_reason=max_total_steps|max_effective_runtime`
- 不允许恢复原 Run。
- 用户可基于摘要和当前 Workspace 创建新 Run。

## 4. Execution Segment 与预算

每次 Run 首次执行或 user-input 恢复都创建新 Segment：

```text
max_steps = 20
max_effective_seconds = 1800
```

Run 硬上限：

```text
max_total_steps = 80
max_total_effective_seconds = 7200
```

以下时间不计入有效执行时间：

- queued
- waiting_approval
- waiting_user_input

Segment 到限时进入 `waiting_user_input`，`pause_reason=segment_step_limit|segment_time_limit`。Run 到硬上限时进入 Finalization，随后 `stopped`。

## 5. Step 与 ToolCall 批次

Step 流程：

```text
create step
  -> stream model response
  -> parse complete response
  -> validate entire ToolCall batch
  -> execute allowed batch serially OR create one approval
  -> persist observations
  -> complete step
```

批次分类：

- 1..N 个只读工具：合法，按声明顺序串行执行。
- 单个 write/apply/delete/run_shell：合法，进入审批。
- 单个 publish_artifact：合法，自动执行。
- 其他组合：`model_protocol_error`，整批零执行。

只读批次单项失败不阻断后续调用；取消、硬预算、安全异常终止剩余调用。

模型协议错误包括空响应、ToolCall JSON 无法解析、未知工具、参数 schema 错误和非法批次。处理规则：

- 第一次错误：`consecutive_protocol_errors += 1`，记录失败 Step，把具体错误反馈给下一 Step 的模型。
- 连续第二次错误：Run 进入 waiting_user_input，`pause_reason=model_protocol_error`。
- 每个无效响应各计一个 Step，但一个 ToolCall 都不创建或执行。
- 任一合法模型响应将 `consecutive_protocol_errors` 清零。

## 6. Approval 状态机

ToolCall：

```text
created -> pending_approval -> approved -> running -> succeeded|failed|timeout|interrupted
                         └── rejected
                         └── invalidated
```

Approval：

```text
pending -> approved|rejected|invalidated|canceled
```

幂等条件：

- approve/reject 仅允许 Approval pending 且 Run waiting_approval。
- 请求参数 hash 必须与审批创建时一致。
- cancel 与 approve/reject 使用条件更新，只能一个事务成功。
- approve 只改变审批和排队状态；真正执行由单执行器完成。

Reject 计数：

- Reject：`consecutive_rejects += 1`。
- 获批的状态变更 ToolCall 成功：清零。
- user-input 创建新 Segment：清零。
- 只读调用、重规划和失败副作用不清零。
- 达到 2：进入 waiting_user_input，不再自动提出第三次变更。

## 7. 写入版本冲突

批准后，ToolCall 重新获得执行槽时必须复检：

- 已有文件：当前 SHA-256 等于 `base_sha256`。
- 新建文件：目标仍不存在。
- 删除文件：路径、普通文件类型和 SHA-256 均未变化。

不满足时：

```text
approval -> invalidated
tool_call -> failed(error_code=file_version_conflict)
run -> running
Agent 下一 Step 重新读取并重新申请
```

原审批不能迁移到新参数或新 diff。

### 7.1 Durable Intent

任何副作用 ToolCall 在真正执行前先提交意图事务：

```text
tool_call.status = running
execution_nonce = random uuid
preconditions_json = approved preconditions
expected_postconditions_json = expected hash/snapshot
insert tool_call_started event
COMMIT
```

随后执行文件、Artifact 或 Shell 操作，再在第二个事务保存实际结果和 Event。第二个事务失败或进程崩溃时：

- 文件工具比较目标 hash，判定 applied/not_applied/outcome_unknown。
- publish_artifact 校验快照文件与 hash，允许补记已完成结果。
- run_shell 统一标记 interrupted/side_effects_may_exist。
- 恢复过程只能对账，不能再次执行原副作用。

## 8. 事实确认屏障

以下结果设置 `reconciliation_required=true`：

- 写工具返回 `outcome_unknown`。
- Shell 非零退出、timeout 或 interrupted，且可能已经有副作用。

屏障期间，下一模型响应只能：

- 调用只读工具确认现状；或
- 输出最终说明。

副作用 ToolCall 会被整批拒绝。完成至少一个只读 Step 后解除屏障；仍无法判断时进入 waiting_user_input。

文件工具失败后 Runtime 先执行内部 postcondition 检查，返回：

```text
applied | not_applied | outcome_unknown
```

## 9. Retry

- 模型请求在首个 delta 前遇到网络错误、429、5xx：最多重试 2 次。
- 两次重试仍失败时，当前 Step 标记 `failed/model_temporarily_unavailable`，Run 进入 waiting_user_input。
- 设置 `pause_reason=model_temporarily_unavailable`；多个 ModelAttempt 仍属于同一个 Step，只计一次 Step 预算。
- 用户继续时创建新 Segment，使用原 Model Profile snapshot 重新入队。
- 收到任何 delta 后不透明重试；Step 标记 `failed/model_stream_interrupted`。
- 已显示文本保留为 `assistant_progress` 并标记 `incomplete=true`，不能成为 final_answer。
- 未完整解析的 ToolCall 全部丢弃，一个也不创建或执行。
- Run 进入 waiting_user_input，`pause_reason=model_stream_interrupted`。
- 本次失败 Step 计入 Segment 和 Run Step 预算；用户继续时创建新 Segment 并重新入队。
- 只读工具仅对 timeout、EINTR、EAGAIN 等瞬时错误重试 1 次。
- not_found、validation、permission、sensitive_file 不重试。
- 写工具、publish_artifact 和 run_shell 不自动重试或重放。
- API Key 无效、模型不存在、Base URL 或请求参数确定性错误使 Run 直接进入 failed。
- 该终态失败不执行 Finalization Call，不允许替换 Run 的模型快照后恢复。
- 已有 Timeline 和 Artifact 保留；用户修复或更换 Profile 后创建新 Run。

## 10. Cancel

- queued/waiting：事务内直接 canceled。
- Model stream/只读工具：取消异步任务。
- run_shell：SIGTERM 进程组，宽限期后 SIGKILL。
- 文件工具未进入 commit 前可取消；进入原子 commit 后完成提交，再把 Run 标记 canceled。
- 重复 cancel 幂等。

## 11. 崩溃恢复

启动时执行恢复事务：

- queued 保持 queued。
- waiting_approval/waiting_user_input 保持原状态。
- running Run -> waiting_user_input，`pause_reason=runtime_interrupted`。
- finalizing Run 不重新调用模型；Runtime 根据已提交事件生成降级摘要并进入 stopped。
- running ToolCall -> interrupted，`side_effects_may_exist=true`。
- 不自动重放 ModelAttempt、文件工具、Artifact 或 Shell。
- 清除过期 executor lease，恢复 FIFO 调度。

用户恢复后，Run 创建新 Segment；Agent 必须先读取现状。

## 12. Finalization

达到硬上限后：

1. Run 进入 finalizing 并释放普通工具权限。
2. 执行一次最多 60 秒的无工具模型调用。
3. 输入仅包含任务、关键步骤、Artifact、错误和未完成项。
4. 成功保存摘要；失败由 Runtime 生成结构化降级摘要。
5. Run 进入 stopped。

Finalization 不计入 80 个业务 Steps，不能产生 ToolCall。
