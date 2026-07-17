# Eidos

让想法拥有可执行的形态。

Eidos 是一个面向未来工作的 Agent Runtime，帮助智能体理解任务、调用工具、操作工作区，并将每一步执行过程清晰记录下来。

## 定位

Eidos 的第一阶段目标不是做一个聊天机器人，而是先打造一个本地可运行、可审批、可追踪、可恢复的个人 Agent Runtime。

MVP 会优先打通这一条闭环：

```text
选择 Workspace 并创建 Session
  -> 创建 Run
  -> 模型选择回复或调用工具
  -> Runtime 执行只读工具或请求文件/Shell 审批
  -> approve / reject 后恢复执行
  -> 持久化 Run、Item 与 ToolCall 历史
  -> 输出最终结果
```

## 文档

- [第一期实现基线：MVP Lite](docs/mvp-lite.md)
- [第二期实现与回归清单](docs/mvp-phase-2.md)
- [文档总索引](docs/README.md)
- [完整目标态 PRD](docs/prd/README.md)
- [完整目标态 TDD](docs/tdd/README.md)
- [Q1-Q155 设计决策](docs/decisions.md)

## 第一阶段：MVP Lite

第一期只验证一个 Workspace 中的最小 Agent Runtime 闭环：

- Electron Main 通过 stdio JSON-RPC 双向协议管理 Python Runtime，Runtime 日志只写 stderr。
- 首期领域模型固定为 `Session -> Run -> Item/ToolCall`，不引入 Execution Segment。
- 只支持 Workspace Mode、固定 DeepSeek `deepseek-v4-flash` Chat Completions HTTP SSE、单活动 Run 和最小工具/审批闭环。
- 未完成 Run 在重启后标记 interrupted，不自动恢复或重放。

当前 MVP Lite 已完成自动化、macOS 原生 Seatbelt 和真实 DeepSeek 联网验收；开发启动与人工验证方式见 [MVP Lite](docs/mvp-lite.md)。

第一期的详细范围、延期项、协议、状态和里程碑以 [MVP Lite](docs/mvp-lite.md) 为准。

第二期已完成持久 FIFO、暂停/继续、Event/operation 水位、安全存储迁移、Durable Intent/对账、敏感内容边界、完整文件工具契约与 Desktop 恢复状态；详细证据以 [第二期清单](docs/mvp-phase-2.md) 为准。

## 开发运行与验证

环境要求：macOS、Node.js 22+、pnpm 11、Python 3。首次运行：

```bash
pnpm install
python3 -m venv .venv
.venv/bin/pip install -r runtime/requirements.txt
pnpm start
```

桌面端验证建议按一条纵向链路完成：

1. 点击“新建 Session”，选择一个不含敏感信息且包含 `README.md` 的测试目录。
2. 在界面配置 DeepSeek API Key；确认状态只显示“已配置”，不回显 Key。
3. 提交“阅读 README 并说明项目用途”；预期 Feed 出现 `read_file` 和最终回答，底部输入框始终可见。
4. 要求创建一个测试文件；预期先出现完整 diff，Reject 时文件不变，Approve 后按 diff 写入。
5. 要求运行一个短测试命令；预期先展示 command、cwd、断网状态和 timeout，批准后显示有界输出与终态。
6. 退出并重新启动；预期已完成 Session/Run/Item 可读取，未完成 Run 不自动重放。

完整自动化与 macOS Seatbelt 回归：

```bash
pnpm test
```

## 完整目标态边界

现有 v0.4 PRD/TDD 保留为完整目标态与后续安全加固依据，包括：

- macOS 桌面端、Agent / Session / Run 管理
- 可编辑/Archive 的 OpenAI-compatible Model Profile、显式 Responses/Chat Completions 协议、能力测试与版本化快照
- 版本化工具契约、封闭 schema、动态可用工具集、canonical ToolResult 与确定性上下文投影
- 多 Run 持久化 FIFO 队列与全局单执行器
- ReAct-style Agent Loop
- 只读工具批次与逐文件 `write_file` / `apply_patch` / `delete_file`
- 显式 `publish_artifact` 与不可变 Artifact 快照
- macOS Seatbelt `workspace_write` Shell
- 文件、敏感路径、网络、localhost、Toolchain 和环境隔离
- 文件写入和 Shell 审批、版本复检、资源/输出限制、Workspace 变化审计与事实确认屏障
- approve / reject 后恢复执行
- SSE 实时事件和持久化 Run Timeline
- Execution Segment、Run 硬上限、cancel、崩溃恢复和基础测试

完整目标态暂不做内嵌 Terminal、持久服务、跨平台、知识库、长期记忆、多 Agent、MCP 插件市场、浏览器自动化、企业权限和分布式调度。
