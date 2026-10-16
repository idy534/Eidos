# 当前渠道接入

Eidos 在本机运行钉钉应用机器人。用户可以在“设置 → 渠道”配置一个企业内部应用。机器人默认支持单聊，群聊需要 @ 机器人。Eidos 不设置额外的用户或群白名单。钉钉应用的发布状态和可用范围决定哪些用户可以接触机器人。

## 配置与运行

用户需要先在钉钉开发者后台创建企业内部应用，开启机器人，选择 Stream 模式，发布应用，并将机器人加入所属组织的内部群。用户还需要为应用开通发送群聊或单聊消息所需的权限。具体权限和可用范围以当前钉钉后台为准。

用户在本机选择已经配置的模型、独立工作区或一个现有 Project。Project 使用 Local 执行，不自动创建 Worktree。渠道审批模式默认 manual，也可以选择 auto_review。渠道不能选择 full_access。

应用凭证只需要 Client ID 和 Client Secret。用户不需要填写 RobotCode、CorpID、UserID 或 ConversationID，也不需要开启单聊开关。Stream 使用 Client ID 和 Secret 建立连接。Runtime 从该连接的消息回调读取 robotCode、senderCorpId、senderStaffId 和 conversationId。回复使用消息里的 robotCode，Session 使用消息里的 senderCorpId 区分组织。系统不通过 Client ID 猜测 RobotCode。字段来源见[官方 Stream 协议](https://open-dingtalk.github.io/developerpedia/docs/learn/stream/protocol/)与[官方 SDK 回调解析](https://github.com/open-dingtalk/dingtalk-stream-sdk-python/blob/main/dingtalk_stream/chatbot.py)。

Client Secret 留空会保留同一 Client ID 已保存的凭证。更换 Client ID 必须填写新 Secret。Runtime 把 Secret 保存在数据目录的 channel-secrets 下，目录权限为 0700，文件权限为 0600。SQLite 只保存 UUID 引用。API 读取响应不返回 Secret 或其文件引用。SDK 原始日志被隔离，Eidos 只记录经过固定映射的连接事件和异常类型。AccessToken 只在内存缓存。

系统不开放本地 HTTP 服务。Desktop 仍通过 typed IPC 和 stdio JSON-RPC 管理 Runtime。Runtime 主动连接钉钉。Eidos 完全退出、电脑休眠或网络不可用时，系统不能保证接收消息。平台不能保证补投机器人离线消息。同一应用应只在一台 Eidos 中启用。

## 会话与执行

渠道连接、Client ID、组织、钉钉会话、发送者和 Project 共同决定 Session 映射。同一人在同一群中的连续消息复用上下文。不同群、不同人和不同项目使用独立 Session。群内回复仍然对群成员可见。

渠道消息只创建普通 Run。既有 Session FIFO、模型快照、Sandbox、Approval、Durable Intent、取消、Verification 和 Reconciliation 继续生效。渠道准备通过现有 Supervisor managed task 执行，模型重配置不会与这类准备同时进行。系统不增加跨 Session 的 Run 配额。

Run 与渠道来源在同一 SQLite 事务中保存。Desktop 的回答会标记“钉钉渠道消息”。渠道输入不会进入本机用户授权证据，不参与自动记忆学习，也没有记忆写工具和定时任务管理工具。重新生成保留来源以维持这些约束，但不会自动向钉钉发送新生成的本地回答。

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

协议增加 channel/read 和 channel/save。Python DTO 与 Domain 模型分别保存；TypeScript DTO 和验证 Schema 由 scripts/generate-channel-contracts.mjs 生成。共享 Fixture 为 protocol/fixtures/channels-v1.json。

实现使用 dingtalk-stream 0.24.4b1。该版本是预发布版，包含上游的取消、关闭与重连修复。Stream 在既有 asyncio 后端 AnyIO Kernel 内运行。Eidos 没有使用 start_forever 或 SDK 自带的线程池 Handler。发送使用已有 httpx2。具体方案、验证结果和未完成的真实平台验收见 [方案与实现记录](dingtalk-channel-design.md)。
