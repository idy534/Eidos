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

- delta 到达后立即通过内部 EventBus 推送给 SSE。
- 按 100ms 或累计 4KB 合并为一个持久化 chunk，任一阈值先到即 flush。
- 完成时保存完整最终响应和 usage。
- 崩溃恢复以最后一个已提交 chunk 为准。
- UI delta 是临时视图；数据库 committed Event 是断线回放边界。

## 4. ToolCall 解析和组合校验

Model Gateway 输出完整 ToolCall list 后，Runtime：

1. 按工具 schema 校验名称和参数。
2. 计算每个工具 side_effect 分类。
3. 校验整个批次组合。
4. 非法时创建 `model_protocol_error`，零 ToolCall 执行。
5. 合法时按声明顺序创建 ToolCall。

模型必须在读取结果进入下一轮上下文后，才能提出基于结果的变更。一次响应中的只读 ToolCall 彼此不能依赖运行结果。

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
- 收到 delta 后失败：本 Attempt failed，不透明重试。
- Provider validation/auth 错误不重试。
- 用户取消会关闭流并结束 Step。

## 8. Finalization Call

Finalization 使用原 Run 模型快照，但：

- 无工具 schema。
- timeout 60 秒。
- 输入为有界结构化任务结果。
- 不允许重试产生的内容覆盖已有 Artifact。
- 调用失败由 Runtime 生成固定格式摘要。

## 9. 待确认 Q41

尚未决定模型原始 reasoning 内容是否保存或展示。实现前必须完成 Q41；当前 schema 不应预设持久化 raw reasoning。

