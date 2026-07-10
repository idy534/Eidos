# 模型、上下文与流式输出

版本：v0.4

## 1. Model Profile

```text
id
name
base_url
model
api_key_ref
parameters_json
context_window_tokens
max_output_tokens
created_at
updated_at
```

- OpenAI-compatible provider 使用同一 Gateway，不为具体厂商建立业务分支。
- `context_window_tokens` 与 `max_output_tokens` 必填，否则 Runtime 无法计算上下文预算。
- API Key MVP 存在 `~/.eidos/config.toml`；`api_key_ref` 指向配置项。
- `.eidos`/config 权限不符合 0700/0600 时拒绝加载密钥。
- Run 快照保存 profile name/base_url/model/parameters/context limits，不保存密钥。
- waiting、queued、running Run 始终使用创建时快照。

## 2. Model Gateway 流协议

```python
class ModelStreamEvent(BaseModel):
    type: Literal[
        "content_delta",
        "tool_call_delta",
        "usage",
        "completed",
        "failed",
    ]
    payload: dict

class ModelGateway(Protocol):
    async def stream_response(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]: ...
```

必须完整收到 `completed` 并解析 ToolCall 参数后，才能创建可执行 ToolCall。流中途失败时，部分 tool_call_delta 永远不能执行。

## 3. 流式事件

- delta 到达后先进入增量敏感扫描器，只有已确认安全或已脱敏的文本才通过内部 EventBus 推送给 SSE。
- `tool_call_delta` 在完整解析和参数扫描前只存在于短暂内存，不对 UI 流式展示、不持久化原始片段；通过后只生成受控的 ToolCall 摘要事件。
- 按 100ms 或累计 4KB 合并为一个持久化 chunk，任一阈值先到即 flush。
- 完成时保存经增量扫描后合并的完整最终响应和 usage；Provider 原始响应不落盘。
- 崩溃恢复以最后一个已提交 chunk 为准。
- UI delta 是临时视图；数据库 committed Event 是断线回放边界。
- `deny`/`redact` 命中的普通 content delta 统一替换后继续；不因普通文本单次命中终止 Run。
- 扫描器失败后不 flush 保留窗口，丢弃未完整解析的 ToolCall，Run 进入 `waiting_user_input/sensitive_scan_failed`。

## 4. ToolCall 解析和组合校验

Model Gateway 输出完整 ToolCall list 后，Runtime：

1. 按工具 schema 校验名称和参数。
2. 计算每个工具 side_effect 分类。
3. 校验整个批次组合。
4. 非法时创建 `model_protocol_error`，零 ToolCall 执行。
5. 合法时按声明顺序创建 ToolCall。

组合与 schema 合法后、创建 ToolCall 之前，Runtime 对完整参数执行敏感扫描。命中 `deny`/`redact` 时按 `sensitive_tool_input` 处理，不创建 ToolCall，也不增加协议错误计数。连续两次命中后暂停，任一完整合法且无敏感 ToolCall 的响应清零该计数。

模型必须在读取结果进入下一轮上下文后，才能提出基于结果的变更。一次响应中的只读 ToolCall 彼此不能依赖运行结果。

空响应、无法解析的 ToolCall、未知工具、参数 schema 错误和非法批次统一视为模型协议错误。Runtime 允许下一 Step 自动纠正一次；连续第二次错误后进入 waiting_user_input。每次无效响应计一个 Step，合法响应清零连续计数。

## 5. Context Builder

稳定顺序：

```text
1. 内置 system prompt
2. Runtime 和 ToolCall 组合协议
3. Session mode / active root /安全边界
4. 工具 schema
5. 原始任务和后续用户输入
6. 当前 Segment 状态与剩余预算
7. 未解决审批、冲突、reconciliation 状态
8. 最近消息、Step 和 ToolResult
9. Artifact 与结构化历史摘要
```

所有注入项必须有单项上限和总 token 预算。

从 SQLite、Tool log 或 Artifact 读取的历史正文在进入 Context Builder 前按当前 `ruleset_version` 再扫描。读时 `deny` 不注入正文，`redact` 只注入脱敏版本；不修改原始 Timeline 或 Artifact 快照。

## 6. P0 确定性裁剪

计算：

```text
input_budget = context_window_tokens - max_output_tokens - safety_margin
```

不可裁剪：

- system/runtime 安全规则。
- 原始任务和所有用户补充指令。
- 当前 Segment。
- waiting approval、错误、冲突和事实确认屏障。
- Artifact 元数据。

按顺序优先裁剪：

1. 最旧 Shell stdout/stderr 正文。
2. 最旧文件正文和搜索 preview。
3. 最旧普通模型进度文本。
4. 更早 Step 的完整结果。

裁剪后保留 path、hash、状态、size、摘要和 `content_omitted=true`，Agent 可以重新读取。MVP 不调用模型生成 compaction summary。

## 7. Model Retry

每次网络尝试写入 ModelAttempt：

- 首个 delta 前遇到网络失败、429、5xx：最多 2 次重试，指数退避并尊重 Retry-After。
- 重试耗尽后 Step 标记 `model_temporarily_unavailable`，Run 进入 waiting_user_input。
- 同一 Step 的多个 ModelAttempt 只占一个 Step 预算；用户继续时创建新 Segment。
- 收到 delta 后失败：本 Attempt 与 Step 标记 `model_stream_interrupted`，不透明重试。
- 已提交 content chunk 保留为 `assistant_progress/incomplete`，不得升级为 final_answer。
- 部分 tool_call_delta 丢弃，不创建 ToolCall row。
- Run 进入 waiting_user_input；用户继续时创建新 Segment。
- 失败 Step 计入 Run 的 80 Steps 硬上限。
- Provider validation/auth 错误不重试。
- `401/403` 认证错误、确定性的 model not found、invalid request 或不支持参数直接终止 Run。
- 终止时保存结构化 `model_auth_failed|model_not_found|model_invalid_request`，不调用 Finalization。
- Run 的 Model Profile snapshot 不可修改；修复配置后只能创建新 Run。
- 用户取消会关闭流并结束 Step。

## 8. Finalization Call

Finalization 使用原 Run 模型快照，但：

- 无工具 schema。
- timeout 60 秒。
- 输入为有界结构化任务结果。
- 不允许重试产生的内容覆盖已有 Artifact。
- 调用失败由 Runtime 生成固定格式摘要。

## 9. Reasoning 内容边界

- Provider 返回的 raw reasoning/reasoning tokens 内容不写 Message、Event、日志或上下文回放。
- Provider 支持关闭 reasoning 内容返回时，Gateway 应关闭；无法关闭时消费后立即丢弃内容。
- 有 ToolCall 的普通 content delta 标记为 `assistant_progress`。
- 无 ToolCall 且响应 completed 的普通 content 标记为 `final_answer`。
- `assistant_progress` 可以实时展示和按普通文本规则持久化，但不得命名为思维链或内部思考。
- reasoning token 数量、耗时和费用可以保存在 usage metadata 中，不能反推出内容。
- ToolCall 参数解析不依赖 reasoning 内容。

## 10. 规则版本

- Sidecar 生命周期中只使用启动自检通过的单一 `ruleset_version`，不热加载。
- Sidecar 将已成功使用的最高 `ruleset_generation` 保存在 security metadata。当前应用携带的 generation 更低时 Redaction Service 不可用；回滚构建必须携带不低于已生效 generation 的规则资源。
- Run 创建时把当前版本写入快照；每个扫描结果记录实际版本。
- 应用升级重启后，queued/waiting Run 使用新版本继续，并在恢复前追加 `redaction_ruleset_changed` Event。安全规则不因 Run 的旧快照而降级。
- MVP 不远程下载、不允许 Workspace/用户覆盖，也不对旧数据执行升级后的全量追溯重扫；历史数据安全迁移属于 P1。
