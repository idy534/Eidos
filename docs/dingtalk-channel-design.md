# 钉钉渠道接入方案与实现记录

飞书与企业微信扩展沿用本文的 Session、Run 和回复记录设计，新增内容见 [飞书与微信渠道方案](feishu-wechat-channel-design.md)。

本方案对应分支 `codex/dingtalk-channel`。该分支从 `codex/custom-subagents` 创建。文档会随实现更新，未验证内容不会记为完成。

## 目标与首版范围

用户在所属组织的内部群中 @ 应用机器人，或与机器人单聊。保持运行的本地 Eidos 接收文字消息，创建或复用独立 Session，通过普通 Run 执行，并向原会话回复。Desktop 同时展示普通 Session 和执行记录。

首版配置一个钉钉应用连接。会话按连接、组织、钉钉会话、发送者和项目隔离。用户在 Desktop 选择模型、项目和审批模式。渠道支持在 Desktop 二次确认并保存的 full_access，授权覆盖该渠道后续收到的消息。消息不能自行切换审批模式或绑定的执行位置。首版不接收图片或文件，不提供群内审批按钮，也不复制已有本地会话历史。

## 平台事实

- 应用机器人支持收发消息。Stream 使用本机主动建立的 TLS 长连接，无需公网回调地址。
- 群聊仅投递 @ 机器人的消息。单聊无需 @。
- Stream 不能发送 IM 回复。回复使用 HTTPS。首版统一使用 OpenAPI，避免依赖临时 sessionWebhook。
- 官方协议将机器人消息描述为 fire-and-forget。系统不能承诺补收离线期间的消息。已经落库的消息可以恢复。
- 多个客户端使用同一应用订阅时，平台可能分配消息到其中一个连接。用户应只在一台 Eidos 上启用同一机器人。
- Stream 建连凭证是 Client ID 和 Client Secret。官方回调包含 robotCode、senderCorpId、senderStaffId 和 conversationId。用户无需另填机器人、组织、用户或群 ID。Eidos 用回调中的 RobotCode 回复，用发送方组织 ID 隔离 Session。

资料：[机器人类型](https://open-dingtalk.github.io/developerpedia/docs/learn/bot/overview/)、[Stream](https://open-dingtalk.github.io/developerpedia/docs/learn/stream/overview/)、[协议](https://open-dingtalk.github.io/developerpedia/docs/learn/stream/protocol/)、[回复方式](https://open-dingtalk.github.io/developerpedia/docs/learn/bot/appbot/reply/)、[官方 SDK 回调解析](https://github.com/open-dingtalk/dingtalk-stream-sdk-python/blob/main/dingtalk_stream/chatbot.py)。

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

实现需要配置、会话映射、消息接收、Run 来源和回复投递记录。消息以连接和钉钉原始 msgId 去重。Run 与渠道来源在同一事务接纳。配置变更或停用后，旧准备不能启动新 Run。消息回调只保存来自已配置 Stream 连接的有界消息，再返回 ACK。单聊默认接收，群聊只接收被 @ 的消息。Eidos 不再设置用户和群允许列表，应用的可用范围由钉钉管理。

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
| channel_connections | 保存一个应用连接、版本、模型、项目、审批模式和凭证引用 |
| channel_sessions | 保存连接、组织、会话、发送者与项目的 Session 映射 |
| channel_inbound | 保存去重 ID、冻结配置、规范化消息及接纳结果 |
| channel_run_origins | 随普通 Run 原子保存远端来源 |
| channel_deliveries | 保存待发送、发送中、已发送、失败和未知结果 |

Schema 版本从 29 升至 30。普通启动和历史持久化布局升级都调用同一迁移。迁移失败会回滚 DDL 与版本。旧版本不能直接打开 v30；程序回退需要恢复升级前的完整数据目录，包括凭证文件。

channel/read 返回脱敏连接、运行状态和最近 20 条回复。channel/save 使用 expectedRevision 防止覆盖其他修改。Secret 留空只在 Client ID 相同时复用。Secret 文件先保存，SQLite 事务再引用它；失败遗留的无引用文件不会成为业务事实来源。当前实现不会自动清理旧凭证文件。

Domain 模型位于 domain/channels.py。RPC DTO 位于 protocol/channels.py。TypeScript DTO 和 AJV Schema 由 scripts/generate-channel-contracts.mjs 生成。两端共享 protocol/fixtures/channels-v1.json。审批模式默认 manual，单聊默认支持。配置不包含 RobotCode、CorpID、用户或群允许列表，也不提供单聊开关。

配置精简保留 Schema v30，没有新增表或列。Repository 为已有 connection_json 和 snapshot_json 提供明确的读取转换，只移除这五个旧配置字段。新请求仍采用严格 DTO，不能继续传入旧字段或任意未知字段。旧消息保留原始消息身份和冻结配置版本，旧 Session 仍使用相同映射。下一次保存会去掉旧配置字段。Desktop 与 Runtime 需要配套更新；旧 Runtime 仍要求这些字段，所以程序回退需要恢复备份。

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

## 首版验证结果（配置精简前）

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

首版 Diff 覆盖 61 个文件，约新增 2,860 行、删除 15 行。其中 417 行是自动生成的 TypeScript 契约和 Schema，uv.lock 新增 329 行。改动集中在钉钉接入、Runtime 接纳与来源、持久化迁移、typed IPC、设置页面、定向验证和文档。

## 配置精简记录（2026-10-16）

用户要求默认支持单聊，并移除 Eidos 内的用户和群白名单。实现同时移除了手填 RobotCode 和 CorpID。Stream 仍只用 Client ID 和 Secret 认证。消息仍经过同一个 ChannelApplication、SQLite 和普通 Run 链路，原有审批和 Sandbox 规则继续生效。

本轮定向验证覆盖默认单聊、群聊 @、停用过滤、不同发送者及组织的 Session 隔离、回调中的回复目标、旧配置与待处理消息恢复，以及 Python/TypeScript 对旧请求字段的共同拒绝。真实钉钉平台验收仍然待补充。

| 本轮验证 | 结果 |
| --- | --- |
| Runtime test_channels.py | 32 项通过；旧配置恢复用例补强后单独复测通过 |
| Renderer ChannelSettings.behavior.test.tsx | 9 项通过 |
| Main runtime-client-channels.test.js | 2 项通过，包含真实 Runtime 重启 |
| Main、Renderer TypeScript 与 Desktop 契约检查 | 通过 |
| Ruff、Deptry、git diff --check | 通过 |
| Renderer 构建 | 通过，保留既有大 chunk 提示 |
| 原生 Electron 页面 | 保存、字段移除、凭证遮蔽、Tab 顺序及 1440/1024/768/320 宽度通过 |
| pip-audit | 外层沙箱无法解析 PyPI；原生联网重跑检出上文相同的五条基线漏洞，审计未通过 |
| Full Suite、真实钉钉、Runtime Bundle、DMG | 本轮没有执行 |

本轮没有增加依赖，没有修改 pyproject.toml、uv.lock、数据库表结构或进程生命周期。实现删除了白名单过滤和单聊开关，也删除了手填机器人、组织 ID 的校验。实现继续使用已有的官方 Stream SDK、httpx2 和 Pydantic。旧配置只在 Repository 读取边界转换，新的 DTO 和生成式双端契约保持严格。

## 漏回复排查记录（2026-10-16）

用户报告同一群里部分 @ 消息没有回复。首次只读检查发现，已有接收记录的问候消息均完成 Run 并取得发送成功回执。用户报告的另一组消息没有接收记录，所以当时只能将排查范围缩小到平台投递、Stream 或本地接收解析，不能断言具体根因。

原实现对回调字段校验失败直接返回 ignored，而且关闭了全部 SDK 日志。实现现已增加脱敏的 SDK 去重前收帧、回调到达、解析、过滤和落库日志。SDK 日志通过标准库 logging.Handler 转换为固定事件。该转换不转发原始参数、格式化响应或异常值。日志中的帧与消息关联值只保留摘要。定向测试验证诊断字段可用，并检查正文、昵称、消息 ID、Webhook、ticket、Secret 和异常内容不会进入日志。SDK 去重测试确认重复帧仍有诊断，但保持上游只处理一次并返回两次 ACK 的行为。

首次手机复现中，问候消息形成了回调、Run 和成功回复。另一条文字消息没有形成回调日志，也没有字段校验错误。该结果仍不能单独区分平台未投递和 SDK 内部分发，所以实现又增加了去重前的收帧诊断。根因仍需对照新增的真实消息帧证据确认。维护者不能把这一诊断补充记为漏回复已修复。

后续真实验证得到以下结果：

- 原群的第二次复现没有产生机器人回调消息帧。本机当时只有一个 Eidos Runtime，设置页显示已连接。用户确认同一应用没有接入其他服务。钉钉后台显示机器人使用 Stream 模式，应用已有上线版本。
- 手机单聊的两条文字消息都到达 SDK、通过解析、完成普通 Run，并得到 sent 回执。这次验证包含实际的工作区文件查看。
- 用户在原群重新输入 @，从列表选择机器人后发送同一句请求。消息于本地时间 14:58:32 到达 SDK，回调标记 mentioned=True，Run 执行成功，回复于 14:58:49 得到 sent 回执。

本次问题已缩小到原群消息的 @ 提及或平台投递环节。重新选择机器人后，真实群聊链路恢复。缺失消息没有到达本机，所以本机无法还原其原始 @ 元数据；现有证据不足以断言具体的手机端或平台故障。维护者不应把推测写成已确认根因，也不应把诊断代码描述为修改了群消息投递规则。

最终 test_channels.py 的 34 项定向测试、Ruff、Deptry 和 git diff --check 通过。两项新增测试覆盖脱敏诊断和 SDK 重复帧 ACK 行为。Eidos 已正常重启并保持运行。实现没有新增依赖、数据库表、协议字段或权限规则。本轮没有运行 Full Suite、Bundle 或 DMG。此前记录的主依赖审计问题没有在本轮修改。

## 渠道完全访问（2026-10-16）

用户要求渠道审批模式支持完全访问，并复用 Session、定时任务的危险确认交互。实现复用现有 ConfirmDialog。用户选择完全访问后，表单先保留原模式。取消或 Escape 会保留原模式，确认后才选中完全访问。确认不会自动保存，用户需要继续点击“保存渠道配置”。弹窗说明授权覆盖该渠道今后的消息，并提醒用户信任可向机器人发消息的用户。

channel/save 增加可选 fullAccessConfirmation，值固定为 full-access-v1。首次开启必须提供该确认，Runtime 在写凭证和连接之前核对。ChannelConnection 将确认版本与审批模式一起持久保存。已有完全访问配置的普通保存可以复用确认；降为其他模式会清除确认，再次开启必须重新确认。缺少确认的完全访问连接无法通过 Domain 校验。公开读取仍不返回凭证引用。

渠道准备从冻结配置读取模式和确认，传入既有 RunStartRequestDto。RunApplication 再核对请求是否与该消息冻结的渠道配置一致，后续事务继续检查当前配置版本。消息文本不能决定模式。完全访问使用已有的直接执行流程，manual 与 auto_review 继续使用原流程。来源上下文按 Run 的实际模式给出指令，完全访问不再提示模型逐项请求审批。渠道来源仍不进入本机授权证据或自动记忆，原有取消、Durable Intent、版本核验、结果核验和 Reconciliation 保持生效。

本轮没有新增依赖，没有修改数据库表结构或 Schema v30。确认保存在连接 JSON 和接收快照 JSON 中。旧的 manual、auto_review 配置没有该字段，继续按原模式读取。Desktop、生成式双端契约和 Runtime 需要配套更新。旧 Runtime 不能读取新的完全访问配置，程序回退仍需恢复备份。

| 本轮验证 | 结果 |
| --- | --- |
| 原生 Runtime 渠道与审批模式 | 53 项通过，其中渠道 36 项、审批模式 17 项 |
| Renderer 渠道行为 | 12 项通过，覆盖取消、确认、清除确认和已有配置 |
| Main / 真实 Runtime | 2 项通过，覆盖拒绝未确认请求、保存、重启恢复和降级后重新确认 |
| 原生 Electron | 弹窗危险按钮、默认取消焦点、取消与保存、四种窗口宽度通过 |
| Main / Renderer 类型、Desktop 契约、Ruff、Deptry | 通过 |
| 本轮文件 git diff --check | 通过；工作区全局检查另报 AgentPanel.behavior.test.tsx 的已有行尾空行，本轮保留该文件的其他修改 |
| Main、Preload、Renderer 构建 | 通过，Renderer 保留既有大 chunk 提示 |
| pip-audit | 仍检出已记录的 Pydantic AI 五条基线漏洞，未通过 |
| Full Suite、真实钉钉完全访问、Bundle、DMG | 本轮未执行 |

完全访问集成用例实际执行工作区外的临时 Shell 写入，确认工具的 sandbox_type 为 none，没有 Approval 记录，并且保存了 Durable Intent。用例也核对远端来源、模型上下文和审批模式，防止消息侧请求升级权限。真实渠道配置没有在测试中切换为完全访问。

## 待补充与真实验收

实际表结构、RPC、默认值、身份字段、消息限额、配置变更语义、迁移兼容性和测试结果已经补齐。以下内容仍需要真实应用或后续独立工作：

- [ ] 用户在本机保存真实应用凭证，并确认租户的发送权限、应用发布和可用范围。
- [x] 已验证真实单聊与从列表重新选择机器人的群 @，并通过回调身份完成 Session 隔离和发送回执。
- [ ] 维护者继续观察原群消息的 @ 提及和平台投递，必要时结合钉钉侧原始记录定位未投递原因。
- [ ] 用户验证真实 Token 续期、平台限流和长任务回复。
- [ ] 用户验证断网、休眠唤醒、关窗后继续接收与长时间重连。
- [ ] 维护者单独处理基线 Pydantic AI 依赖漏洞，并重新审计。
- [ ] 维护者补充 Python 3.11 的实际运行验证；本次原生 Bundle 使用 Python 3.12。

后续可选范围包括图片、文件、卡片流式更新、原生引用回复、群内审批和多组织应用分发。这些内容未进入首版实现。

真实平台验收需要用户创建应用并提供本机配置。验收覆盖群 @、默认单聊、不同用户和群、连续提问、取消、审批、长任务、断网、重启、休眠唤醒、关窗后接收和完全退出。没有真实凭证时，这些项目保持未验证。Full Suite 只在用户明确要求时执行。
