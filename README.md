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

- [PRD：Agent Runtime MVP 需求文档](docs/agent_runtime_mvp_prd.md)
- [TDD：Agent Runtime MVP 技术设计文档](docs/agent_runtime_mvp_tdd.md)

## MVP 边界

P0 只覆盖能让 Eidos 真正跑起来的能力：

- Agent / Session / Run 管理
- ReAct-style Agent Loop
- `list_files` / `read_file` / `write_file` / `run_shell`
- workspace 文件隔离
- shell 和中高风险工具审批
- approve / reject 后恢复执行
- SSE 实时事件和持久化 Run Timeline
- cancel、错误记录、基础测试

MVP 暂不做知识库、长期记忆、多 Agent、MCP 插件市场、浏览器自动化、企业权限和分布式调度。
