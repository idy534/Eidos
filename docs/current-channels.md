# 当前渠道接入

Eidos 在本机运行钉钉、飞书和企业微信机器人。用户可以在“设置 → 助理”为每个平台配置一个连接。各连接分别保存凭证、模型、执行位置、审批模式和回复记录。机器人默认支持单聊，群聊由平台的机器人提及规则触发。Eidos 不设置额外的用户或群白名单。应用的发布状态和可用范围决定哪些用户可以接触机器人。当前微信入口是企业微信，个人微信扫码登录尚未实现。

## 飞书与企业微信

飞书自建应用使用 App ID 和 App Secret。用户需要开启机器人、长连接和 im.message.receive_v1 事件，开通单聊读取、群聊 @ 消息与机器人发送权限，并发布应用。Runtime 查询机器人真实 open_id，并据此判断群消息的 mentions。回复使用原 message_id 的 reply 接口。Eidos 不要求用户填写 Tenant ID 或 Bot Open ID。

企业微信智能机器人使用 Bot ID 和 Secret。用户需要选择 API 模式和长连接，并设置可用范围。普通群 Webhook 不属于此接入。Runtime 仅在鉴权成功后显示已连接。企业微信回调没有独立的 @ 标记，Runtime 按平台已路由的群机器人回调处理，不套用钉钉的字段。回复使用同一长连接主动发送到 userid 或 chatid，并等待回执。系统不使用临时 response_url。

飞书适配使用 lark-channel-sdk 1.4.0。企业微信适配使用 wecom-aibot-python-sdk 1.0.2。官方 SDK 处理帧和回执；Eidos 的薄适配管理现有 AnyIO Kernel 内的 socket、任务、超时与关闭。SDK 内部接口的使用集中在适配器中，并由分片、失败 ACK、超时和关闭测试约束。Secret 继续使用现有受保护文件存储。AccessToken 只在内存保留。SDK 原始日志和异常值不进入协议 stdout 或用户界面。

channel/read 与 channel/save 增加可选 channel。省略时仍为 dingtalk，也可以传 feishu 或 wecom。连接 JSON 保存 channel；缺少该字段的旧记录按钉钉读取。现有表支持多个连接，因此本次没有改变 Schema v30。旧钉钉连接 ID、去重 ID 和 Session 路由保持不变。三个渠道的版本、会话和最近回复分别处理。新 Desktop 与 Runtime 需要配套更新。旧 Runtime 无法解析新连接格式，回退前需要恢复旧数据备份。

当前适配只执行文字消息。飞书使用群内真实机器人提及；企业微信使用平台投递的群回调。系统忽略其他机器人或不相关事件，并对收到的未支持消息发送文字提示。回复内容仍限制为约 24 KiB；钉钉和飞书每段不超过 6000 字节，企业微信 Markdown 每段不超过 3000 字节。unknown 记录继续禁止自动重发。一个渠道连接失败时，其他渠道继续连接。

完整调研、配置步骤、依赖选择、个人微信的二维码与长轮询方案，以及待补充的真实平台验收见 [飞书与微信渠道方案](feishu-wechat-channel-design.md)。本次代码实现的是企业微信。个人微信仍需要独立的扫码登录、Token、持久游标和 context_token 处理，不能仅替换凭证字段。

## 钉钉配置与通用运行规则

用户需要先在钉钉开发者后台创建企业内部应用，开启机器人，选择 Stream 模式，发布应用，并将机器人加入所属组织的内部群。用户还需要为应用开通发送群聊或单聊消息所需的权限。具体权限和可用范围以当前钉钉后台为准。

用户在本机选择已经配置的模型、独立工作区或一个现有 Project。Project 使用 Local 执行，不自动创建 Worktree。渠道审批模式默认 manual，也可以选择 auto_review 或 full_access。用户选择“完全访问”时，Desktop 展示与 Session、定时任务相同的危险确认弹窗。取消会保留原模式；确认后，用户仍需保存渠道配置。

完全访问授权适用于该渠道今后收到的消息。弹窗会说明系统不再逐项请求批准，任务可以使用当前 macOS 用户的文件、命令和网络权限，也可能修改 Eidos 自身的数据。用户需要同时信任渠道和可向机器人发送消息的用户。Runtime 要求首次开启时提交 fullAccessConfirmation=full-access-v1，并将确认版本保存到连接及消息配置快照中。已确认的完全访问配置可以继续保存；切换回其他模式会清除确认，再次开启需要重新确认。渠道消息不能代替这个 Desktop 确认，也不能自行切换模式。

应用凭证只需要 Client ID 和 Client Secret。用户不需要填写 RobotCode、CorpID、UserID 或 ConversationID，也不需要开启单聊开关。Stream 使用 Client ID 和 Secret 建立连接。Runtime 从该连接的消息回调读取 robotCode、senderCorpId、senderStaffId 和 conversationId。回复使用消息里的 robotCode，Session 使用消息里的 senderCorpId 区分组织。系统不通过 Client ID 猜测 RobotCode。字段来源见[官方 Stream 协议](https://open-dingtalk.github.io/developerpedia/docs/learn/stream/protocol/)与[官方 SDK 回调解析](https://github.com/open-dingtalk/dingtalk-stream-sdk-python/blob/main/dingtalk_stream/chatbot.py)。

Client Secret 留空会保留同一 Client ID 已保存的凭证。更换 Client ID 必须填写新 Secret。Runtime 把 Secret 保存在数据目录的 channel-secrets 下，目录权限为 0700，文件权限为 0600。SQLite 只保存 UUID 引用。API 读取响应不返回 Secret 或其文件引用。SDK 原始日志被隔离，Eidos 只记录经过固定映射的连接事件和异常类型。AccessToken 只在内存缓存。

系统不开放本地 HTTP 服务。Desktop 仍通过 typed IPC 和 stdio JSON-RPC 管理 Runtime。Runtime 主动连接各平台。Eidos 完全退出、电脑休眠或网络不可用时，系统不能保证接收消息。平台不能保证补投机器人离线消息。同一应用应只在一台 Eidos 中启用。

## 会话与执行

渠道连接、Client ID、组织、钉钉会话、发送者和 Project 共同决定 Session 映射。同一人在同一群中的连续消息复用上下文。不同群、不同人和不同项目使用独立 Session。群内回复仍然对群成员可见。

渠道消息只创建普通 Run。既有 Session FIFO、模型快照、Durable Intent、取消、Verification 和 Reconciliation 继续生效。manual 与 auto_review 使用既有 Sandbox 和审批；已确认的 full_access 使用既有完全访问流程，不创建逐项审批。Run 接纳前，Runtime 核对审批模式和确认版本是否与冻结的渠道配置一致。渠道准备通过现有 Supervisor managed task 执行，模型重配置不会与这类准备同时进行。系统不增加跨 Session 的 Run 配额。

Run 与渠道来源在同一 SQLite 事务中保存。Desktop 的回答会标记“钉钉消息”（或“飞书消息”、“企业微信消息”）。渠道输入不会进入本机用户授权证据，不参与自动记忆学习，也没有记忆写工具和定时任务管理工具。重新生成保留来源以维持这些约束，但不会自动向钉钉发送新生成的本地回答。

模型需要人工审批或表单输入时，机器人提示用户在本机 Eidos 继续。系统不会把下一条钉钉消息解释为审批或表单答复。已经接纳的 Run 不会因为渠道被停用而自动取消；用户仍可以在本机停止它们。配置变化会阻止旧准备提交新的 Run。执行位置变化后，新消息使用新的 Session 映射。

## 回复与恢复

系统使用官方 Stream SDK 接收消息，使用 HTTPS OpenAPI 获取 AccessToken，并向群或用户发送 Markdown。回复不依赖临时 sessionWebhook。传输有明确超时，HTTP 响应最多读取 64 KiB，Stream 单帧最多 128 KiB。文字输入最多 16,000 字符。第一版不读取图片、文件或富文本附件。

系统在任务成功后读取已提交的最终回答。失败、取消、中断和等待状态使用简明中文说明。系统不发送思考或工具原始输出。回答最多投递约 24 KiB，分成每段不超过 6,000 字节的消息，超长内容提示用户在本机查看。

channel_inbound 使用由连接和原消息 msgId 得到的稳定 ID 去重。重启后，pending 消息继续准备；submitted 消息不会重新创建 Run。普通 Run 的重启行为仍由现有 Recovery 决定。渠道不会自动重放已中断的工具操作。

发送前，系统将回复记为 sending。成功回执会将记录改为 sent，该状态表示平台已接受发送，不代表用户已读。明确的权限或目标错误记为 failed。限流、凭据刷新和获取 Token 的暂时错误最多尝试五次，间隔一分钟。发送超时、服务端异常、无法确认的回执、取消时的发送和重启遗留的 sending 记录记为 unknown。系统不会自动重发 unknown。多段回复的前一段未确认时，后续段停止。Desktop 显示最近 20 条回复的状态和原因。

## 漏回复诊断

维护者应先核对 channel_inbound、Run 和 channel_deliveries。接收记录不存在时，维护者需要继续核对 Stream 回调日志，不能直接归因为模型未回答。接收记录存在时，维护者再检查 Run 是否等待、失败或结束，以及回复是否被平台接受。

Runtime 的 eidos.dingtalk 日志记录 SDK 去重前的回调消息帧、回调到达、规范化结果和落库阶段。帧与消息标识均为 SHA-256 摘要的前 12 位，只用于关联诊断。日志还记录配置版本、单聊或群聊、@ 标记、格式支持状态和文字长度。校验失败时，日志只列出失败字段名；配置改变、停用和群聊未 @ 也有固定原因。SDK 日志只转换为连接、断开、无效帧、未知主题、回调处理失败等固定事件，以及异常类型。日志不保留消息正文、昵称、完整平台 ID、Webhook、连接 ticket、Secret、SDK 原始异常值或格式化响应。

## 数据与验证

Schema v30 增加 channel_connections、channel_sessions、channel_inbound、channel_run_origins 和 channel_deliveries。v29→v30 迁移包含 DDL 与版本回滚。普通旧库升级和持久化布局升级均接入该迁移。旧 Runtime 不能读取 v30，回退程序前需要恢复升级前的数据备份。

配置精简没有改变表结构或 Schema 版本。Repository 在读取旧连接和旧消息的配置快照时，移除已停用的 RobotCode、CorpID、白名单和单聊开关字段。旧开关和列表不再限制消息。系统保留连接 ID、版本、凭证引用和 Session 映射。下一次保存会写入精简后的配置。公开的 channel/save 不再接受这些旧字段，其他未知字段仍被拒绝。Desktop 与 Runtime 需要配套更新；回退到要求旧字段的 Runtime 前需要恢复备份。

完全访问增加了连接 JSON 中的可选确认字段，没有修改表结构或 Schema v30。已有 manual 和 auto_review 配置继续使用原值。缺少确认或确认与模式不一致的持久配置会被拒绝。新 Desktop 与 Runtime 需要配套更新；旧 Runtime 不能解析完全访问配置。

协议增加 channel/read 和 channel/save。Python DTO 与 Domain 模型分别保存；TypeScript DTO 和验证 Schema 由 scripts/generate-channel-contracts.mjs 生成。共享 Fixture 为 protocol/fixtures/channels-v1.json。

实现使用 dingtalk-stream 0.24.4b1。该版本是预发布版，包含上游的取消、关闭与重连修复。Stream 在既有 asyncio 后端 AnyIO Kernel 内运行。Eidos 没有使用 start_forever 或 SDK 自带的线程池 Handler。发送使用已有 httpx2。具体方案、验证结果和未完成的真实平台验收见 [方案与实现记录](dingtalk-channel-design.md)。

## 公共模型选择

Session 输入框、定时任务和渠道配置共用 `ModelSelector`。组件统一显示提供商图标、模型列表和当前模型支持的思考强度。组件使用已有 React、React DOM 和 ProviderLogo，没有新增依赖。弹层通过 Portal 显示，避免被表单滚动区域裁切。

渠道配置增加可选 `reasoningSelection`。旧配置缺少该字段时继续使用模型默认值。用户切换模型时，表单清除旧思考设置；未切换时，表单保留已保存设置。配置和消息快照继续保存在原有 JSON 中，没有新增表或 Schema 迁移。渠道启动 Run 时传递该设置，既有模型校验和不可变 Run 快照继续生效。Desktop 与 Runtime 需要一起更新。

公共模型选择的定向验证通过：Renderer 行为与样式共 102 项，渠道 Runtime 44 项，Main 跨进程与 Fixture 2 项。隔离 Electron 页面覆盖渠道和定时任务的四种窗口宽度、图标、弹层范围和 Escape 焦点恢复；Composer 布局与通用样式也单独验证。Ruff、Deptry 与 Main / Renderer 构建通过。本次未运行 Full Suite、打包或真实钉钉及 Provider 验收。该次依赖审计没有通过：当时锁定的 pydantic-ai-slim 2.43.0 报告五项已知漏洞；该次没有修改依赖。工作区全局 diff 检查另有 AgentPanel.behavior.test.tsx 的既有尾部空行提示，该文件不属于本次修改。


## 飞书和企业微信扩展的验证

本次代码与文档涉及 38 个路径，包含旧钉钉表单到共享表单的复用。最终定向验证通过 Runtime 74 项、Renderer 15 项和 Main/Runtime 2 项。Ruff、Deptry、Desktop 契约检查、Main/Renderer 构建、三个渠道四种宽度的 Electron 交互、Electron 启动关闭与 macOS arm64 Bundle 验证均通过。测试使用模拟平台网络，不使用真实飞书或企业微信凭证。两个 SDK 的 macOS arm64 依赖随 Runtime Bundle 构建，并通过隔离导入与启动关闭检查。生产依赖审计仍有 pydantic-ai-slim 2.43.0 的 5 个已有漏洞；新增 SDK 没有新增审计发现。隔离工具依赖审计通过。真实账号收发、发布范围和平台限频仍待人工验收。开发过程未运行 Full Suite。

## 依赖审计更新（2026-10-17）

Runtime 已升级至 pydantic-ai-slim 2.57.0，并同步其必需依赖。主 Python、隔离工具 Python 与 Runtime Node 审计均通过，以上历史记录中的五条漏洞已不再报告。升级后的定向测试与 Bundle 验证见 [当前限制](current-limitations.md#model-gateway-依赖升级2026-10-17)。真实渠道账号的验收范围保持原样。
