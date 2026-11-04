# 实时活动行设计

状态：P1、P2 已于 2026-11-04 实施（行为说明见 [当前架构](../current/architecture.md#实时活动行与思考摘要)）。交互与视觉见 [设计稿](live-activity-line-mockup.html)（浏览器直接打开，可切换场景、点击查看）。当前行为以源码为准。

## 1. 问题

Run 进行中，Feed 末尾只在“尚无任何过程项、也没有回复”时显示一行渐变的“正在思考…”（`ExecutionFeed.tsx` 的 `showThinking`）。一旦出现第一个工具调用，这行就消失，用户只能从工具组行的转圈和“正在运行 …”推断进度。模型在两次工具调用之间“想什么”完全不可见，长任务里 Feed 末尾像是停住了。

## 2. 方案概述

在当前 Run 的最末尾放一行**实时活动行**：

```text
[Mark 描边动画]  确认本地技能目录里“获取技能”对应的技能          ⌃
```

- 只在 Run 进行中出现，Run 进入终态即消失；不保留历史，不写入 SQLite。
- 一行、不换行、超出省略；不显示计时（上方“正在处理 Ns”已有）。
- 取代现有的 `thinking-indicator`，同一位置、同一套 shimmer 文字。

### 2.1 与工具组的分工

实时活动行出现后，**它是 Feed 里唯一会动的元素**。工具组行（“运行 2 条命令”）回到静态的叠层图标，不再转圈和 shimmer，只保留汇总文案。否则末尾会同时出现两个转圈。工具组展开后的逐条状态不变。

### 2.2 文字来源（从高到低）

| 优先级 | 来源 | 示例 |
|---|---|---|
| 1 | 模型思考摘要的标题（首行/粗体标题，≤ 80 字） | 确认本地技能目录里… |
| 2 | 正在执行的工具，复用 `toolSummary` / `shellSummary` | 正在运行 ls ~/.eidos/skills |
| 3 | 兜底 | 正在思考 |

`finalizing` 显示“正在整理回复”。`waiting_approval`、`waiting_input`、`waiting_agents` 时隐藏该行，由审批/提问/子任务自己的界面承担，避免两处同时提示。最终回复开始流式输出后隐藏该行。

### 2.3 视觉

沿用工具组的语法，不引入新颜色或字号。

| 项 | 取值 |
|---|---|
| 容器 | 与 `.tool-group > summary` 同款：`margin-left: -0.5rem`、`padding: 0.1rem 0.5rem`、`--radius-md` |
| 字号 | `--font-size-body` / `--line-height-body` |
| 文字 | `--tg-text`，运行态 shimmer（同 `tool-group-shimmer`） |
| 图标 | 15px，`--accent`。Eidos 切角方块轮廓为底（20% 不透明），一段亮线沿轮廓循环（`stroke-dashoffset`，1.7s linear） |
| 悬停 | 有摘要时背景 `rgba(0,0,0,0.045)`，文字 `--tg-text-strong`，行尾出现小箭头 |
| 文字切换 | 新文字上移 5px、淡入、轻微去模糊，300ms `--apple-spring` |
| 切换节奏 | 每条文字至少停留 1.2s；期间只保留最新一条，避免摘要连续到达时闪烁 |

选择 Mark 描边而不是现有的圆弧 spinner，是为了与工具组内的 spinner 区分，同时让这个动画带有 Eidos 的识别度。

### 2.4 点击查看

只有存在思考摘要正文时，整行才是按钮（`aria-expanded`、`aria-haspopup="dialog"`）；只有状态文案时它是普通的 `role="status"`，没有悬停和手型，不制造“点了没反应”的错觉。

点击后在行**上方**弹出气泡，不推动 Feed，也不让底部输入区位移：

- 小标题“思考摘要 · 仅本次运行”
- 当前摘要标题（13px 粗体）与正文（13px，最多约 7 行，超出滚动）
- 分隔线下列出本次 Run 此前的摘要标题，最多 4 条，单行省略

Esc、点击外部、Run 结束时关闭。样式使用 `--shadow-popover`、`--radius-lg`，与现有菜单一致。不做展开/折叠，因此不会产生布局抖动。

### 2.5 可访问性与降级

- `prefers-reduced-motion`：图标静止（保留一段亮线），文字不 shimmer、不位移。
- 容器 `role="status"`；文字更新受 1.2s 停留限制，不会连续打断读屏。
- 窄窗口：图标固定，文字单行省略，气泡宽度取 `min(27rem, 100%)`。

## 3. 数据来源与实施范围

**关键事实：当前 Renderer 拿不到思考内容。** Runtime 只把供应商的 `ThinkingPart` 收进 `ModelReasoningPart`，用于下一轮回放，“never a conversation Item”（`model/replay.py`）；流式回调只转发文本增量（`model/pydantic_ai_client.py` 的 `PartStartEvent` / `PartDeltaEvent` 对 `TextPart`）。因此分两步：

**P1：仅 Renderer。** 实现 2.1–2.5，文字来源只用优先级 2、3。不改协议、不改持久化，可独立回滚。已经解决“Feed 末尾像停住了”和“同时两个转圈”的问题。

**P2：转发思考摘要。** Runtime 在模型流中把摘要级思考作为**有界、不持久化**的实时信号发给 Renderer。需要先确定：

- 只转发供应商明确提供的摘要（如 Responses API 的 reasoning summary、Anthropic 的 summarized thinking）。`reasoning_content` 这类原始思考链不展示，避免泄露文件内容或凭据，并受日志脱敏规则约束。
- 单条长度上限（标题 80 字、正文约 600 字）、发送频率上限、未知字段 fail closed。
- 不写 SQLite、不进 Outbox：它不是业务事实，与“Run 结束后不展示”一致。这与现有“业务变化必须同事务提交 Event”的规则不冲突，但需要在协议文档里单独写明这是瞬时通知，丢失不影响恢复。
- 协议变化同步 DTO、Fixture、Python/TypeScript 测试和 `docs/current/`。
- 部分供应商/模型没有摘要，此时自然落到优先级 2、3。

### 3.1 实施与设计的差异

- 摘要在其后有工具完成时视为过期（以 Renderer 收到的时间比较），避免一个早期摘要在整个 Run 里一直显示；独立工具行（未折叠进工具组）已显示自己的动作，实时行此时显示“正在执行”，避免重复。

- 摘要只在 Responses API 的自定义模型上出现：请求带思考强度（非 `none`）时附带 `reasoning.summary: "auto"`。Chat Completions 的思考内容是原始思考链，不展示。Responses 客户端目前只对自定义模型发送思考强度，内置模型没有摘要，显示工具文案或“正在思考”。
- 瞬时通知名为 `run/thinking`，由 `RuntimeEvents.publish_transient` 发送；`ModelClient.complete` 签名不变，摘要通过 ContextVar 传给 Sampling。
- 部分自定义网关可能拒绝 `summary` 字段；此时模型请求会失败，需要在该模型的设置中改用不带思考强度的配置。这一点未对真实网关验证。

## 4. 验证计划

- Renderer 行为测试：各 Run 状态下的显示/隐藏、工具组静止、文字优先级、点击气泡与 Esc 关闭、终态消失、`prefers-reduced-motion`。
- 更新 `ExecutionFeed.test.tsx` 中对 `thinking-indicator` 的断言。
- 在真实窗口中检查长文本、窄窗口、审批出现与消失、Run 取消。

## 5. 回退

P1 只改 Renderer，还原 `ExecutionFeed.tsx`/`.css` 即可。P2 的瞬时通知可通过不发送回到 P1，协议字段保持可选。
