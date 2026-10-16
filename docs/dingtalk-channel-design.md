# 钉钉渠道接入方案与实现记录

本方案对应分支 `codex/dingtalk-channel`。该分支从 `codex/custom-subagents` 创建。文档会随实现更新，未验证内容不会记为完成。

## 目标与首版范围

用户在所属组织的内部群中 @ 应用机器人，或与机器人单聊。保持运行的本地 Eidos 接收文字消息，创建或复用独立 Session，通过普通 Run 执行，并向原会话回复。Desktop 同时展示普通 Session 和执行记录。

首版配置一个钉钉应用连接。会话按连接、组织、钉钉会话、发送者和项目隔离。用户在 Desktop 选择模型、项目和审批模式。渠道不提供 full_access，不接受消息指定本机执行路径。首版不接收图片或文件，不提供群内审批按钮，也不复制已有本地会话历史。

## 平台事实

- 应用机器人支持收发消息。Stream 使用本机主动建立的 TLS 长连接，无需公网回调地址。
- 群聊仅投递 @ 机器人的消息。单聊无需 @。
- Stream 不能发送 IM 回复。回复使用 HTTPS。首版统一使用 OpenAPI，避免依赖临时 sessionWebhook。
- 官方协议将机器人消息描述为 fire-and-forget。系统不能承诺补收离线期间的消息。已经落库的消息可以恢复。
- 多个客户端使用同一应用订阅时，平台可能分配消息到其中一个连接。用户应只在一台 Eidos 上启用同一机器人。

资料：[机器人类型](https://open-dingtalk.github.io/developerpedia/docs/learn/bot/overview/)、[Stream](https://open-dingtalk.github.io/developerpedia/docs/learn/stream/overview/)、[协议](https://open-dingtalk.github.io/developerpedia/docs/learn/stream/protocol/)、[回复方式](https://open-dingtalk.github.io/developerpedia/docs/learn/bot/appbot/reply/)、[官方 SDK](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)。

## 最小调用链与模块边界

```text
Desktop 设置 → typed preload IPC → Main → stdio JSON-RPC
  → ChannelApplication → SQLite 配置
钉钉 Stream → 官方 SDK Adapter → ChannelApplication → SQLite 接收记录
  → SessionApplication → RunApplication → 现有 Supervisor / Agent Loop
  → SQLite 已提交回答 → 渠道待发送记录 → HTTPS OpenAPI → 钉钉
```

Runtime 的唯一业务事实来源仍是 SQLite。渠道不另建 Run 状态机。渠道服务使用现有 AnyIO Kernel 和资源管理，不启动独立 Event Loop。所有网络发送均有超时和有界内容。原 Desktop Event/Outbox 与渠道投递记录分别确认。

## 持久化、授权与恢复

实现需要配置、会话映射、消息接收、Run 来源和回复投递记录。消息以连接和钉钉原始 msgId 去重。Run 与渠道来源在同一事务接纳。配置变更或停用后，旧准备不能启动新 Run。消息回调只保存已通过允许列表检查的有界消息，再返回 ACK。

Client Secret 存入受保护的本地凭证文件。SQLite 仅保存凭证引用。AccessToken 只在内存缓存。原始 webhook、凭证和平台原始异常不得进入 Event、普通日志或模型上下文。

渠道输入保留远端发送者来源，不能充当本机高危人工确认。渠道输入不进入自动记忆学习。回复只使用已提交的最终回答或简明状态说明，不发送思考、工具输出或本机会话后续结果。

接收记录在重启后恢复；已提交的 Run 不重新创建。发送前保存 sending 状态。进程在 sending 中退出，或发送超时且结果不能确定时，记录为 unknown，不自动重发。平台明确限流可以延迟重试。用户可以在 Desktop 查看失败和未知结果。

## 依赖决策

标准库和当前依赖没有钉钉 Stream 业务 SDK。实现锁定官方 `dingtalk-stream==0.24.4b1`，并更新 pyproject.toml 与 uv.lock。该版本是预发布版。稳定版 0.24.3 不具备本次需要的上游取消与关闭修复，所以实现采用包含这些修复的发布版本。实现没有使用 start_forever，也没有使用 SDK 自带线程池 Handler。

SDK 的收连接、心跳、ACK 和重连由上游维护。Eidos 使用现有 AnyIO Kernel 的 asyncio 后端调用 start，并在取消时调用 stop。Eidos 关闭了 SDK 仅为上报 localIp 而执行的外部 IP 探测，也隔离了可能包含完整回调和 ticket 的原始 SDK 日志。

OpenAPI 发送使用已锁定的 httpx2。首版只封装获取 Token、向群发消息、向用户发消息三个接口。SDK 的简单 reply 方法依赖临时 webhook，而且使用同步 HTTP，所以实现没有直接调用它们。发送 Adapter 不实现通用 HTTP、WebSocket 或模型 Provider 协议。维护者需要跟进这三个 API 的字段、回执和权限变化，并通过 MockTransport 与真实平台验收验证。

SDK 新增的运行依赖包括 aiohttp、websockets 及其依赖。macOS arm64 Bundle 已完成构建和 smoke。Eidos 继续自行管理身份、授权、Run、事务和恢复。

## 已实现的数据与协议

| 对象 | 已实现的用途 |
| --- | --- |
| channel_connections | 保存一个应用连接、版本、允许列表、模型、项目和凭证引用 |
| channel_sessions | 保存连接、组织、会话、发送者与项目的 Session 映射 |
| channel_inbound | 保存去重 ID、冻结配置、规范化消息及接纳结果 |
| channel_run_origins | 随普通 Run 原子保存远端来源 |
| channel_deliveries | 保存待发送、发送中、已发送、失败和未知结果 |

Schema 版本从 29 升至 30。普通启动和历史持久化布局升级都调用同一迁移。迁移失败会回滚 DDL 与版本。旧版本不能直接打开 v30；程序回退需要恢复升级前的完整数据目录，包括凭证文件。

channel/read 返回脱敏连接、运行状态和最近 20 条回复。channel/save 使用 expectedRevision 防止覆盖其他修改。Secret 留空只在 Client ID 相同时复用。Secret 文件先保存，SQLite 事务再引用它；失败遗留的无引用文件不会成为业务事实来源。当前实现不会自动清理旧凭证文件。

Domain 模型位于 domain/channels.py。RPC DTO 位于 protocol/channels.py。TypeScript DTO 和 AJV Schema 由 scripts/generate-channel-contracts.mjs 生成。两端共享 protocol/fixtures/channels-v1.json。审批模式默认 manual，单聊默认关闭。配置最多允许 100 个用户与 100 个群。

发送目标来自原始平台会话，不能由模型回答修改。发送使用机器人新消息，不保证原生引用回复或话题线程。成功回执表示钉钉接受发送，不表示用户已读。配置停用后，已接纳 Run 保持普通执行语义；用户可以在 Desktop 取消它们。旧配置尚未接纳的消息不会提交新 Run。

首版把文字输入限制为 16,000 字符，把 Stream 帧限制为 128 KiB，把 HTTP 响应限制为 64 KiB。回复限制约 24 KiB，按每段不超过 6,000 UTF-8 字节投递。系统最多对明确可重试的失败尝试五次，间隔一分钟。发送结果未知时不自动重发。多段回复的前一段未确认时，后续段停止。

## 实施与验证记录

- [x] 已从当前分支创建开发分支。
- [x] 已确认现有 Session、Run、AnyIO、Event/Outbox 和设置入口。
- [x] 已实现 Schema 升级和失败回滚。
- [x] 已实现渠道配置、受保护凭证和生成式双端契约。
- [x] 已实现 Stream 生命周期、消息去重和普通 Run 接纳。
- [x] 已实现最终回复、等待提示和不确定投递记录。
- [x] 已实现 Desktop 设置与投递状态展示。
- [x] 已执行 Runtime、协议和 Renderer 定向验证。
- [x] 已执行依赖审计、构建、原生启动退出与 Bundle 验证。主依赖审计有下述未通过项。

## 最终验证结果

| 验证 | 本次结果 |
| --- | --- |
| Runtime 七个定向文件 | 151 项通过，其中钉钉模块 31 项 |
| Renderer 四个相关文件 | 84 项通过 |
| Main/Runtime 三个相关文件 | 5 项通过 |
| Desktop 契约检查 | 通过 |
| Ruff、Deptry | 通过 |
| Main 与 Renderer TypeScript 检查 | 通过 |
| Preload、Main、Renderer 构建 | 通过；Vite 保留原有的大 chunk 提示 |
| 原生 Electron 页面 | 凭证遮蔽、保存、键盘操作与 1440/1024/768/320 宽度通过 |
| 原生启动退出 smoke | 通过 |
| macOS arm64 Runtime Bundle、Bundle smoke、Bundled Seatbelt smoke | 通过 |
| git diff --check | 通过 |
| 独立打包 Python 依赖审计 | 通过 |
| 主 Python 依赖审计 | 未通过，发现基线 Pydantic AI 漏洞，详见下文 |
| Full Suite、DMG、真实钉钉应用 | 本次没有执行 |

Runtime 的七个定向文件为 test_channels.py、test_storage_schema.py、test_automations.py、test_custom_agents.py、test_response_actions.py、test_event_delivery_recovery.py 和 test_queued_runs.py。验证覆盖消息身份、去重、Session 隔离、事务回滚、SQLite 错误、禁用竞争、记忆与授权来源、真实数据库重启、发送取消、未知投递、原生审批恢复和受管理回调到 Run 再到发送回执的完整链路。平台网络端使用模拟 SDK 或 MockTransport。

外层沙箱中的 Bundle smoke 和审批恢复用例曾受到 Seatbelt 环境限制。原生重跑已通过。Renderer 并行首轮曾有一个 MCP 编辑用例失败；该用例单独复测、整个 SettingsPage 文件复测，以及最终四文件串行验证均通过。本次没有为这些情况删除断言或扩大 Full Suite。

pip-audit 在主依赖中发现已有 pydantic-ai-slim 2.43.0 的五条问题：PYSEC-2026-4285、4286、4284、4290、4291。当前基线已锁定相同版本。本次新增的钉钉依赖没有出现在审计结果中。本次没有升级该已有模型依赖，也没有新增忽略规则。因此，主依赖审计仍明确记为未通过。

实现保留既有 Agent Loop、Session FIFO、Sandbox、Approval、Grant、Durable Intent、Verification 和 Reconciliation。实现没有替换旧机制，没有移动已有模块，也没有新增云端控制通道。协议继续使用 v1，新 RPC 与 channelOrigin 为本次扩展；Desktop 和 Runtime 需要配套更新。完整当前行为见 [当前渠道接入](current-channels.md)。

本次 Diff 覆盖 61 个文件，约新增 2,860 行、删除 15 行。其中 417 行是自动生成的 TypeScript 契约和 Schema，uv.lock 新增 329 行。改动集中在钉钉接入、Runtime 接纳与来源、持久化迁移、typed IPC、设置页面、定向验证和文档。

## 待补充与真实验收

实际表结构、RPC、默认值、身份字段、消息限额、配置变更语义、迁移兼容性和测试结果已经补齐。以下内容仍需要真实应用或后续独立工作：

- [ ] 用户在本机保存真实应用凭证，并确认租户的发送权限、应用发布和可用范围。
- [ ] 用户验证真实群 @、单聊和实际 senderStaffId / senderCorpId / conversationId。
- [ ] 用户验证真实 Token 续期、平台限流和长任务回复。
- [ ] 用户验证断网、休眠唤醒、关窗后继续接收与长时间重连。
- [ ] 维护者单独处理基线 Pydantic AI 依赖漏洞，并重新审计。
- [ ] 维护者补充 Python 3.11 的实际运行验证；本次原生 Bundle 使用 Python 3.12。

后续可选范围包括图片、文件、卡片流式更新、原生引用回复、群内审批和多组织应用分发。这些内容未进入首版实现。

真实平台验收需要用户创建应用并提供本机配置。验收覆盖群 @、单聊、连续提问、允许列表、取消、审批、长任务、断网、重启、休眠唤醒、关窗后接收和完全退出。没有真实凭证时，这些项目保持未验证。Full Suite 只在用户明确要求时执行。
