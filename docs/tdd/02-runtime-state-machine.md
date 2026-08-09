# Runtime、队列与状态机

版本：v0.4（探索草案）

范围说明：本文描述目标态状态机草案。历史第一期基线见 [MVP Lite](../archive/phases/mvp-lite.md)；第二期记录见 [第二期清单](../archive/phases/mvp-phase-2.md)。当前实现以 `docs/current-*.md`、代码和测试为准。

MVP Lite 历史基线包含 20 Step 上限；当前实现保留全局单活动 Run、串行模型/工具循环、Cancel、迟到审批和 worker 异常收敛，但已删除固定 Step 任务寿命。

第二期历史基线曾包含 20/80 Step 与 30/120 分钟预算；当前实现保留持久 FIFO、单执行槽、Segment/Step/Attempt、Finalization、Durable Intent 与 reconciliation 屏障，但 Step/Run effective time 只作 telemetry，30 分钟只触发非终止 Segment rollover。

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

- Run 是一次可排队、执行并进入明确终态的任务执行。
- Execution Segment 是一次实际 Runtime execution/lifecycle slice。
- Step 对应一次完整模型响应及其 ToolCall 批次。
- ModelAttempt 记录一次真实模型网络发送；同一 Step 的 Attempt 共享逻辑请求 ID，并由 Step 级 10 分钟 request cycle deadline 统一约束。

## 2. 全局单执行器

队列规则：

- 可同时创建和保留多个 Run。
- `queued` Run 按 `enqueued_at, creation_seq` FIFO 获取执行槽。
- 当前 Run 不可抢占；它进入审批等待或终态后释放执行槽。
- waiting_approval 不占执行槽。
- Approve、Reject 后继续时，将 Run 以新的 `enqueued_at` 追加到队尾。
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
finalizing
succeeded
failed
stopped
canceled
interrupted
```

主要流转：

```text
created -> queued -> running
running -> waiting_approval -> queued
running -> finalizing -> stopped
running -> succeeded|failed|canceled
queued|waiting_approval -> canceled
running|waiting_approval|finalizing -> interrupted
```

`stopped` 是 graceful-but-forced 的终态，例如 Context 压缩后仍不可恢复或 LoopGuard 确认循环。历史 SQLite 可能包含 `max_total_steps|max_effective_runtime|segment_step_limit|segment_time_limit`；当前 Runtime 不再产生这些 stop reason。

LoopGuard 不按相同 Tool、Error 或 no-progress 的累计轮数终止。`LoopStateFingerprint` 包含 exact Tool batch、Workspace version、reconciliation epoch、active errors 与 canonical durable-context frontier，排除 timestamp、Step index、Attempt/Call ID。已持久化结果仍有效时，第一次返回相同 state 会跳过 Tool 执行并注入一次 generic recovery；只有 recovery 后再次返回相同 fingerprint 才以 `repeated_tool_call|no_progress` graceful Finalization。`ProgressSignature` 持久化 state/recovery fingerprint，使重启后仍能恢复 convergence 状态。

### 3.1 执行态 RuntimeState

Run `status` 是可跨重启的事实；它不能直接替代单次执行循环的控制状态。第二期新增仅在内存中存在、由 StateMachine 维护的 `RuntimeState`：

```python
class RuntimeState(str, Enum):
    THINKING = "thinking"
    TOOL_EXECUTING = "tool_executing"
    WAITING_APPROVAL = "waiting_approval"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
```

- `queued` 由 Scheduler 持有，不进入 RuntimeEngine；认领为 `running` 后从 `THINKING` 开始。
- `THINKING -> TOOL_EXECUTING|WAITING_APPROVAL|FINALIZING|COMPLETED|FAILED|CANCELED`，工具完成后回到 `THINKING`；`WAITING_APPROVAL` 只可由有效审批或取消离开。
- 每次迁移先由 StateMachine 校验，再与 Run/Segment/Step/Approval 的持久事实和 Event 同事务提交；任何非法迁移安全失败，不能由调用方绕过。
- Sidecar 重启不反序列化旧 `RuntimeState`：它从持久 Run 状态和最后已提交事实重建可调度状态，绝不恢复已在内存中的模型或工具执行。

## 4. Execution Segment 与 telemetry

每次 Run 首次执行、user-input 恢复或 operational lifecycle rollover 都创建新 Segment。Step 数不触发 rollover；30 分钟 effective-time quantum 只在安全 Step boundary 完成旧 Segment 并启动新 Segment，同一 Run 继续：

```text
operational_effective_seconds = 1800
step_count = telemetry_only
```

以下时间不计入有效执行时间：

- queued
- waiting_approval

有效执行区间只用 TimeProvider monotonic clock 累计；区间结束时一次性向上取整为毫秒并持久化。wall `started_at/finished_at` 只用于审计和展示，不可相减驱动生命周期。`model_step_count`、Segment `step_count/effective_ms` 和 Run `total_effective_ms` 均持续持久化，但不能决定任务终止。

## 5. Step 与 ToolCall 批次

Step 流程：

```text
create step
  -> freeze deterministic available tool set
  -> stream model response
  -> parse complete response
  -> normalize effective arguments and validate entire ToolCall batch
  -> execute allowed batch serially OR create one or more ordered approvals
  -> persist observations
  -> complete step
```

批次分类：

- 1..N 个只读工具：合法，按声明顺序串行执行。
- 单个 write/apply/delete/run_shell：合法，进入审批。
- 单个 publish_artifact：合法，自动执行。
- 其他组合：`model_protocol_error`，整批零执行。

只读批次单项失败不阻断后续调用；取消、硬预算、安全异常终止剩余调用。

模型协议错误包括空响应、ToolCall ID/index/JSON 归并失败、ToolCall 流资源超限、调用未在本 Step 冻结集合中暴露的工具、未知字段、缺失 required、非法 null、其他参数 schema 错误和非法批次。处理规则：

- 第一次错误：`consecutive_protocol_errors += 1`，记录失败 Step，把具体错误反馈给下一 Step 的模型。
- 连续第二次错误：Run 执行一次无工具收尾并进入 stopped，`stop_reason=model_protocol_error`。
- 每个无效响应各计一个 Step，但一个 ToolCall 都不创建或执行。
- 任一合法模型响应将 `consecutive_protocol_errors` 清零。

### 5.1 敏感 ToolCall

完整解析后、创建 ToolCall row 与 Approval 之前，Runtime 在短暂内存中扫描所有字符串参数：

- `deny` 或 `redact` 命中：整个模型 ToolCall 拒绝，不创建 ToolCall/Approval，只持久化无原文的 `sensitive_tool_input_rejected` Event。
- 第一次连续命中：`consecutive_sensitive_tool_inputs += 1`，当前 Step 失败并将结构化安全错误反馈给下一 Step。
- 连续第二次命中：Run 执行一次无工具收尾并进入 stopped，`stop_reason=repeated_sensitive_tool_input`。
- 任一不含敏感 ToolCall 的合法完整响应清零该计数。
- 每次被拒绝的模型响应计一个 Step，但不增加 `consecutive_protocol_errors`。

若响应同时包含普通文本和敏感 ToolCall，已脱敏的文本保留为 `assistant_progress`，不得升级为 `final_answer`。

## 6. Approval 状态机

ToolCall：

```text
created -> skipped
created -> unavailable
created -> pending_approval -> approved -> running -> succeeded|failed|timeout|interrupted
                         └── rejected
                         └── invalidated
```

`unavailable` 只用于工具已在本 Step 冻结集合中暴露、ToolCall 已通过 schema/组合/敏感校验，但执行前 Runtime gate 或单工具 capability health 发生变化的竞态；错误码固定 `tool_unavailable`，零副作用，并生成 canonical ToolResult。未暴露工具仍是模型协议错误。每个已创建 ToolCall 的数据库终态按 `tool_contract_version` 投影为唯一 `success|error|skipped|rejected|interrupted|unavailable` ToolResult outcome；Run 已终止时可以不再发送给 Provider，但内部结果必须持久化。

Approval：

```text
pending -> approved|rejected|invalidated|canceled
```

幂等条件：

- approve/reject 仅允许 Approval pending 且 Run waiting_approval。
- 请求参数 hash 必须与审批创建时一致。
- cancel 与 approve/reject 使用条件更新，只能一个事务成功。
- approve 只改变审批和排队状态；真正执行由单执行器完成。
- Shell approve 设置 `approval_expires_at=approved_at+300000ms`、当前 boot-session identity 和 continuous-monotonic deadline。执行器开始 ToolCall 前取 continuous deadline 或 wall expiry 任一先到；超时原子转为 Approval invalidated/ToolCall failed(`approval_expired`)，不启动进程。同 boot sidecar 重启沿用原 continuous deadline；boot/timebase 不匹配或时钟回拨按 Q154 启动 invalidation，均不重置 TTL。

Reject 计数：

- Reject：`consecutive_rejects += 1`。
- 获批的状态变更 ToolCall 成功：清零。
- 只读调用、重规划和失败副作用不清零。
- `skipped/no_changes` 不是“获批的状态变更成功”，不清零。
- 首次拒绝后，同一 Run 的后续审批请求自动拒绝；模型必须改走无需审批的路径，或给出用户可自行执行的策略后结束。

## 7. 写入版本冲突

批准后，ToolCall 重新获得执行槽时必须复检：

- 所有文件操作：已存在父目录链的 path/dev/inode/mode/uid/gid 快照未变，逐段打开中无 symlink 替换。
- 已有文件：当前 SHA-256 等于 `base_sha256`，普通文件类型、size、encoding/BOM 和 link count 未变。
- 新建文件：目标仍不存在，父目录仍可用，不自动创建缺失父目录。
- 删除文件：路径、普通文件类型、size、encoding/BOM、`st_nlink=1` 和 SHA-256 均未变化。

不满足时：

```text
approval -> invalidated
tool_call -> failed(error_code=file_version_conflict)
run -> running
Agent 下一 Step 重新读取并重新申请
```

原审批不能迁移到新参数或新 diff。

任一 write/apply/delete 成功的结果事务同时将该 `run_id + path + old_sha256` 下的读取证据标记为 invalidated。同路径后续出现的新文件不继承旧证据。

### 7.1 Durable Intent

任何副作用 ToolCall 在真正执行前先提交意图事务：

```text
tool_call.status = running
execution_nonce = random uuid
preconditions_json = approved preconditions
expected_postconditions_json = expected hash/snapshot
shell_baseline_manifest_ref = nullable
insert tool_call_started event
COMMIT
```

随后执行文件、Artifact 或 Shell 操作，再在第二个事务保存实际结果和 Event。第二个事务失败或进程崩溃时：

- 文件工具比较目标 hash，判定 applied/not_applied/outcome_unknown。
- delete_file 只检查已审批目录项是否仍不存在；同路径出现任何新对象时为 outcome_unknown，禁止再次删除。
- publish_artifact 校验快照文件与 hash，允许补记已完成结果。
- run_shell 使用已提交 baseline manifest 与当前 Workspace 对账，统一标记 interrupted/side_effects_may_exist；不重跑命令。
- 恢复过程只能对账，不能再次执行原副作用。

## 8. 事实确认屏障

以下结果设置 `reconciliation_required=true`：

- 写工具返回 `outcome_unknown`。
- Shell 非零退出、timeout 或 interrupted，且可能已经有副作用。
- Shell resource_limit、output_capture_failed、change_manifest_incomplete、git_boundary_change_detected 或 protected_path_change_count>0。

`side_effects_may_exist` 只表示 canonical ToolResult 尚未完整确认的物质性副作用，不能单独驱动屏障。只读、rejected/unavailable、明确未启动、no_changes、verified not_applied 与 verified file/Artifact success 为 false；outcome_unknown 与任一已启动 Shell 终态为 true。所有 reconciliation_required 结果必须为 true，但反向不成立：Shell exit 0、完整 manifest 且无 `.git`/protected 异常时保持 success、side effects=true 并允许模型继续。

Shell code 按 `interrupted > workspace_change_manifest_incomplete > output_capture_failed > shell_resource_limit_exceeded > tool_timeout > shell_process_signaled > shell_exit_nonzero > success` 选择；低优先级进程事实仍保留在 ToolResult data。`.git`/protected 变化只增加事实确认，不把 exit 0 改写为 error。

屏障期间，下一模型响应只能：

- 调用只读工具确认现状；或
- 输出最终说明。

副作用 ToolCall 会被整批拒绝。事实确认不是“整个 Workspace 已被证明一致”的声明，而是强制在再次变更前发生一个成功、可审计的只读观察：

- 每次产生新的不确定副作用时，即使 `reconciliation_required` 已为 true，也原子递增 `reconciliation_epoch`，持久化触发 ToolCall/reason/Step 的 episode。
- 每个 Step 创建时冻结 `observed_reconciliation_epoch` 和只读 available tool set；本 Step 中途不得因读取成功而加入副作用工具。
- 只有该 Step 正常 completed、至少一个只读 ToolCall 的 canonical outcome 为 `success` 且结果/Events 已提交时，才可用 `WHERE reconciliation_epoch=:observed_epoch` 条件更新清除 flag，并记录 clearing Step/ToolCalls。cancel、interrupted、Runtime 安全故障导致的不完整 Step 不计。
- 同一 Step 部分读取失败但至少一个成功可以清除；`truncated=true`、`workspace_changed=true`、空文件、完整空目录和零匹配仍是合法的局部 success。error/skipped/rejected/interrupted/unavailable、无 ToolCall 文本或路径不存在错误不计。
- CAS 失败表示出现更新 episode；旧 Step 不得清除它。屏障只对下一 Step 重新计算工具集。
- 未出现 qualifying success 时模型可直接输出 final；Run 可终止，但保留 `reconciliation_required=true`、epoch 与未清除 episode 供审计，不伪造已对账。

仍无法判断时进入 interrupted；后续核验必须通过新 Run 执行，不能直接清除屏障。

文件工具失败后 Runtime 先执行内部 postcondition 检查，返回：

```text
applied | not_applied | outcome_unknown
```

## 9. Retry

- 模型固定使用 HTTP 请求与 SSE 响应流；首个 delta 前遇到网络错误、429、5xx 最多重试 2 次。
- 重试仍失败或 request cycle 达到 10 分钟时，当前 Step 标记 `failed/model_temporarily_unavailable`，Run 进入 failed。
- 每个 Attempt 的 connect/first-delta/stream-idle 上限分别为 15/180/120 秒，所有 Attempt 和退避共享 request cycle deadline。
- 多个 ModelAttempt 仍属于同一个 Step，只计一次 Step 预算。
- 可重试的流错误在同一 Step 内按指数退避重放相同模型输入，默认最多重试 5 次；每次重试创建新的 ModelAttempt，不要求用户输入。已显示文本保留为 incomplete assistant 供 UI 审计，但不进入模型上下文。
- Provider token 截断、内容过滤和 Runtime 输出流超限分别标记 `model_output_truncated|model_output_blocked|model_output_limit_exceeded`；Run failed，已提交文本 incomplete，整个 ToolCall 批次丢弃，零自动重试。
- 已显示文本保留为 `assistant_progress` 并标记 `incomplete=true`，不能成为 final_answer。
- 未完整解析的 ToolCall 全部丢弃，一个也不创建或执行。
- 6 个 ModelAttempt 都在输出过可见文本后中断，Run 进入 failed，`error_code=MODEL_STREAM_INTERRUPTED`；始终没有可见文本则重试耗尽后同样 failed。
- 本次失败 Step 计入 Segment 和 Run Step 预算。
- 只读工具仅对 timeout、EINTR、EAGAIN 等瞬时错误重试 1 次。
- not_found、validation、permission、sensitive_file 不重试。
- 写工具、publish_artifact 和 run_shell 不自动重试或重放。
- API Key 无效、模型不存在、Base URL 或请求参数确定性错误使 Run 直接进入 failed。
- 本地预算发现不可裁剪输入超限时零模型发送，Step/Run 以 `context_input_too_large` failed，不使 capability snapshot 失效。
- 每次请求读取 Profile 当前凭证；凭证槽缺失或不可读时不使用 Run 创建时旧密钥，Run 直接 failed。
- TLS 校验失败、明确的 context-length exceeded 或 Provider 违反固化的 streaming、工具控制、Tool Schema Dialect、ToolCall/ToolResult 关联或 usage 契约同样直接 failed；后两类还在终态事务中使 Profile 当前 capability snapshot 失效。
- 该终态失败不执行 Finalization Call，不允许替换 Run 的模型快照后恢复。
- 已有 Timeline 和 Artifact 保留；用户修复或更换 Profile 后创建新 Run。
- 模型流敏感扫描器失败时，未确认安全的文本和 ToolCall 丢弃，Step 标记 `sensitive_scan_failed`，Run 进入 failed。
- Run 固化的 model request contract 或 tool contract 实现不可用/不再满足当前安全底线时不创建 Step/Attempt，不执行工具，并使 pending/approved Approval invalidated；Run 进入 `interrupted/runtime_contract_unsupported`，不能用新版本继续原 Run。
- ToolCall 已形成真实终态但 base ToolResult/projector/schema/canonical serializer invariant 失败时，原子保存实际副作用和 quarantine；Run 直接 `failed/tool_result_contract_violation`，零工具重试、零模型续接、零 Finalization，且不失效 Model Profile snapshot。用户输入不能恢复该 Run。

## 10. Cancel

- queued/waiting_approval：事务内直接 canceled。
- Model stream/只读工具：取消异步任务。
- run_shell：SIGTERM 进程组，宽限期后 SIGKILL。
- 文件工具未进入 commit 前可取消；进入原子 commit 后完成提交，再把 Run 标记 canceled。
- 重复 cancel 幂等。

## 11. 崩溃恢复

启动时执行恢复事务：

- queued 保持 queued。
- waiting_approval、running 和 finalizing Run -> interrupted，`error_code=RUNTIME_INTERRUPTED`。
- running ToolCall -> interrupted；只读或明确尚未启动副作用执行时 `side_effects_may_exist=false`，已进入 commit 的文件/Artifact 和任一已启动 Shell 为 true。
- 不自动重放 ModelAttempt、文件工具、Artifact 或 Shell。
- 仅清理名称、tool_call_id/execution_nonce、inode 和父目录身份与 durable intent 全部匹配的 Runtime 临时文件。
- running Shell 存在 baseline manifest 时先保留文件并尝试后置对账；完整结果事务成功后才清理 manifest。
- 清除过期 executor lease，恢复 FIFO 调度。

用户后续可创建新 Run；Agent 必须先读取现状。

## 12. Finalization

确认 graceful-but-forced stop condition 后：

1. Run 进入 finalizing 并释放普通工具权限。
2. 执行一次最多 60 秒的无工具模型调用。
3. 输入仅包含任务、关键步骤、Artifact、错误和未完成项。
4. 成功保存摘要；失败由 Runtime 生成结构化降级摘要。
5. Run 进入 stopped。

Finalization 不能产生 ToolCall，也不伪造业务 Model Step telemetry。
