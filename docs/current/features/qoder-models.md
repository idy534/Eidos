# Qoder 模型接入

Eidos 通过 Qoder Agent SDK 1.0.17 和随包 CLI 1.1.79 使用 Qoder 国际版账号的模型。这个入口没有配置 OpenAI 兼容地址。用户也不需要填写 API Key。账号订阅、可用模型和收费由 Qoder 决定。Eidos 不把普通个人账号显示成 Teams 账号。

## 用户入口

用户在“设置 → 模型 → 添加模型”中选择 Qoder。页面会在资源就绪后检查一次本地登录状态。Runtime 只检查固定 SDK 的 `.auth` 与 `oauth_creds.json` 是否存在，不读取或解析凭据内容。有本地登录信息时，Runtime 会通过现有 SDK 读取账号模型目录。读取成功后，页面显示“已登录”，并自动填写模型选项。“重新登录”按钮允许用户主动重新授权或切换账号。没有本地凭据时，页面直接显示“未登录”，不会启动 SDK 或打开浏览器。网络错误与认证失败分别显示，文件存在本身不代表账号有效。

新用户先准备模型资源，再点击“浏览器登录”。CLI 会打开系统浏览器。用户完成登录后，Eidos 会读取模型目录。用户也可以主动刷新目录。浏览器只在用户点击登录按钮后打开。页面每 1.5 秒读取本地任务进度，不会重复发起 SDK 检查。用户重新切换到 Qoder 时会重新检查；已经运行的检查、刷新或登录任务会被复用。目录请求不会生成回复，也不会测试模型能力。

Qoder 使用统一提供商图标组件。设置列表、提供商选择框和聊天模型选择器使用同一个 SVG 图标，图标颜色跟随当前文字颜色。

用户选择账号实际返回的模型。Eidos 用目录中的展示名显示设置列表和聊天模型选择器，用模型 ID 发送请求。展示名不使用本地别名表。用户登录或主动刷新目录后，Eidos 会更新已保存的 Qoder 展示名。更新只改名称，不改模型 ID、输入能力、窗口和思考配置。目录暂时不可用时，Eidos 保留已经保存的名称；没有已知名称的新配置才显示模型 ID。

Eidos 同时读取目录中的上下文选项、默认窗口、最大输出、思考档位和图片能力。切换模型时，设置页自动填写这些值，并清除上一模型的档位。Qoder 和自定义提供商共用“输入类型”复选框。文本始终选中。图像默认采用目录声明；目录缺少声明时，图像默认不选。工具调用默认选中。用户可以调整图像与工具调用选项。切换模型会重新填写默认值。关闭工具调用后，Adapter 不向 SDK 发送工具声明。用户手动输入的值必须符合当前模型的已知上限，输出必须小于上下文。

思考档位沿用现有 Model Profile 和每个 Run 的选择。Worker 把上下文窗口和思考档位通过 SDK 的 `parameters.contextWindow` 与 `parameters.reasoningEffort` 传入。图片能力默认由目录声明，用户配置可以覆盖。用户 PNG/JPEG 和已保存的工具图片通过 SDK 的用户消息图片块传入，图片字节不混入文本历史。真实账号的 Auto 模型已经完成一次纯图片颜色识别验证。目录声明和用户勾选都不保证所有模型的图片理解质量。第一版仍不声明自定义语法工具或原生历史回放能力。

Qoder 的窗口和输出预设使用整数 K 标签，例如 `200K、400K、1000K` 和 `8K、16K、32K`。页面只简化选项标签，输入框和保存配置仍使用实际 Token 数值，例如 `32K` 输出选项保存目录返回的 `32000`。自定义提供商保留原有显示和输入规则。

## 执行边界

```text
Eidos SamplingRuntime → Model Gateway → QoderModelClient
→ 私有 stdio → 隔离 Worker → Qoder SDK / CLI → 模型
→ 完整工具请求批次 → Eidos 原有 Tool Runtime
```

Eidos 继续管理 Loop、Run、Session、Step、上下文压缩、工具、审批、Sandbox、Durable Intent、SQLite、Outbox 和恢复。Worker 不执行 Eidos 工具，也不持久保存 Eidos 业务状态。

每个 Model Lease 持有一个 Worker。Worker 使用一个事件循环。每次采样都建立新的 SDK 会话。SDK 的内置工具、项目设置、Skill、Plugin、Memory 和文件 Checkpoint 均关闭。SDK 只接收当前请求的虚拟工具声明。PreToolUse 在执行前停止工具请求。虚拟处理器意外运行、未声明工具、重复 ID、额外模型生成、缺失结束消息或不完整响应都会导致请求失败。Eidos 只有在完整批次通过校验后才执行工具。

`required_tool` 只暴露指定输出工具。Adapter 要求结果恰好包含一次正确调用，并使用现有 JSON Schema 依赖校验参数。这个机制不声明 Qoder 提供原生 `tool_choice` 保证。辅助压缩失败时，现有调用方继续使用确定性回退。

## 上下文、用量和取消

`text_v1` 把 Eidos 已冻结的上下文按原顺序编码为 JSON 文本。记录保留来源和当前输入标记。系统指令单独传给 SDK。Adapter 不重新读取文件，也不让 SDK接管压缩。这个机制不是原生消息角色回放。历史回复、工具结果和用户引用仍是材料，不能授予权限。私有推理回放不会转成文本。

请求和预算共用编码器。预算额外预留 2,048 Tokens 作为 SDK 开销估计。SDK 没有提供完整的输入 Token 数据时，Eidos 继续显示估算值。文本请求仍限制为 1 MiB，含图片的完整 Worker 帧限制为 8 MiB。超限请求会进入现有上下文错误路径，Adapter 不静默截断。

SDK 已报告的请求 Credits 写入原有 `model_attempts.usage_json`。`credits_usage` 记录数值和来源。Adapter 拒绝响应或输出参数时，已收到的用量仍随原有失败记录保存。缺失值不等于零。CLI 返回零 Token 统计时，Adapter 将这些统计记为不可用。Credits 不等于美元价格，也不代替 Qoder 账单。请求取消或连接中断后，Eidos 可能拿不到最终收费数据。

Eidos 取消请求时，Driver 先通知 Worker 关闭 SDK，再清理其进程组。迟到文字和结果不能覆盖取消。CLI 退出不证明远端已经停止计费。SDK 的内部网络重试开关尚未经过可靠验证，所以这个 Adapter 不启用 Eidos 外层自动传输重试，避免叠加消费。用户可以重新发起任务。

## 依赖、凭据和发布

Qoder SDK 要求 MCP 1.x。Eidos 主 Runtime 使用 MCP 2.2.0。两者使用独立依赖锁。Worker 使用 App 的 CPython 3.12，通过 `-I -S` 只加载已验证的 Qoder 资源目录。目标机不需要安装 Python、Node、uv 或 Qoder CLI。源码开发使用 Python 3.11 时，这个资源入口不可用。

资源包 ID 为 `qoder`，资源版本为 `1.0.17.2`。构建器通过 `resources/runtime-dependencies/qoder/uv.lock` 安装依赖，校验 CLI 版本，并生成固定清单。现有 ManagedResourceManager 负责下载、校验、安装和重用。`isolated` 资源不会进入普通 Shell 或 Skill 的 Python 绑定。SDK 的二进制与 Python 依赖留在这个包中，主 Runtime 不导入 SDK。资源发布版本 `2026.11.06.2` 包含目录能力字段、思考参数和图片输入协议。旧版 Worker 不能替代新版资源。

SDK 凭据位于 `<Eidos 数据目录>/qoder/default/`。私有目录使用当前用户和 `0700` 权限。SDK 自己管理登录文件。模型配置、Run 快照、协议和日志不保存 Token。第一版使用一个本地账号引用 `default`。浏览器重新登录会影响该账号后续请求。

维护者必须先发布固定资源包，再发布使用该索引的 App。本地构建和模拟下载验证不证明 GitHub Release 已就绪。SDK 和 CLI 遵循 Qoder 自己的服务与分发条款；Python 漏洞审计不能代替二进制或分发条款检查。

## 数据兼容与回退

本次没有增加 SQLite 表或修改 SQL Schema。模型配置增加可选 `qoder` 字段。Qoder Run 使用版本 2 的 Profile，记录 `qoder_sdk`、SDK/CLI/资源版本、账号引用和 `text_v1`。原有 HTTP 模型配置和快照继续可读。恢复旧 Qoder Run 时，配置与资源版本必须匹配。系统不会静默换模型或换 SDK。

用户可以删除 Qoder 模型配置并继续使用原有 Provider。回退实现时，维护者需要保留这些新增字段的读取兼容，或者先恢复升级前的数据备份。产品不承诺更老的严格 DTO 能读取新快照。资源和登录目录不会随模型配置删除。

验证入口和当前验证范围见[开发参考](../../development/README.md#qoder-模型接入)。
