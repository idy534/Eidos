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

- [文档总索引](docs/README.md)
- [PRD：Agent Runtime MVP](docs/prd/README.md)
- [TDD：Agent Runtime MVP](docs/tdd/README.md)
- [Q1-Q110 设计决策](docs/decisions.md)

## MVP 边界

P0 只覆盖能让 Eidos 真正跑起来的能力：

- macOS 桌面端、Agent / Session / Run 管理
- 可编辑/Archive 的 OpenAI-compatible Model Profile、显式 Responses/Chat Completions 协议、能力测试与版本化快照
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

MVP 暂不做内嵌 Terminal、持久服务、跨平台、知识库、长期记忆、多 Agent、MCP 插件市场、浏览器自动化、企业权限和分布式调度。
