# Eidos

让想法拥有可执行的形态。

Eidos 是一个面向未来工作的 Agent Runtime，帮助智能体理解任务、调用工具、操作工作区，并将每一步执行过程清晰记录下来。

## 定位

Eidos 的第一阶段目标不是做一个聊天机器人，而是先打造一个本地可运行、可审批、可追踪、可恢复的个人 Agent Runtime。

MVP 会优先打通这一条闭环：

```text
创建 Eidos Agent
  -> 创建 Session 和 workspace
  -> 创建 Run
  -> 模型选择回复或调用工具
  -> Runtime 执行文件工具或请求 shell 审批
  -> approve / reject 后恢复执行
  -> 持久化完整 timeline
  -> 输出最终结果
```

## 文档

- [第一期实现基线：MVP Lite](docs/mvp-lite.md)
- [文档总索引](docs/README.md)
- [完整目标态 PRD](docs/prd/README.md)
- [完整目标态 TDD](docs/tdd/README.md)
- [Q1-Q155 设计决策](docs/decisions.md)

## 第一阶段：MVP Lite

第一期只验证一个 Workspace 中的最小 Agent Runtime 闭环：

- Electron Main 通过 stdio JSON-RPC 双向协议管理 Python Runtime，Runtime 日志只写 stderr。
- 首期领域模型固定为 `Session -> Run -> Item/ToolCall`，不引入 Execution Segment。
- 只支持 Workspace Mode、Responses HTTP SSE、单活动 Run 和最小工具/审批闭环。
- 未完成 Run 在重启后标记 interrupted，不自动恢复或重放。

第一期的详细范围、延期项、协议、状态和里程碑以 [MVP Lite](docs/mvp-lite.md) 为准。

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
