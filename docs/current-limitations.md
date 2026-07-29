# Eidos 当前限制

本文只描述当前代码边界，不列未来路线图。

- 仅支持 macOS Desktop。Shell 隔离依赖可用的原生 `/usr/bin/sandbox-exec` 与随包策略资源；Self-Test 失败时 Shell 能力 fail-closed。
- 模型 Provider 固定为 DeepSeek，wire API 固定为 Chat Completions；没有通用 Model Profile 编辑器或 Responses API。
- 全局同一时间只执行一个 Run。单个模型响应内只有安全只读工具可并发；Workspace 写入、Shell、Eidos-state 和 MCP/外部工具不得并发。
- 内置文件工具只处理当前 Workspace 内受支持的普通 UTF-8 文件；没有通用二进制编辑、内嵌 Terminal、浏览器自动化或 Artifact 发布工具。
- Plugin 只支持本地受管包；MCP 只支持 stdio Tools。没有远程市场、OAuth、Streamable HTTP、Resources、Prompts、Sampling 或 Tasks。
- Runtime 不恢复内存中的模型请求、进程或 ToolCall。重启从 SQLite 事实收敛，可能有副作用的未确认执行要求 reconciliation，不自动重放。
- 数据库只接受当前 schema v7 或全新数据库；当前没有通用历史 Migration 框架。
- `Run.runtimeState` 是可选跨语言契约字段，不是持久恢复权威；当前稳定权威是 `Run.status` 加 SQLite 中的审批、Step、ToolCall 和 reconciliation 事实。
- 原生 Seatbelt、进程组和 Electron 启动验证需要真实 macOS 执行环境；受限嵌套沙箱不能替代该证据。
