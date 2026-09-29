# 子 Agent：Codex 对照与 Eidos 实现

本文件记录 PR #95 对照的公开行为和当前实现边界。Codex 的具体 UI 和运行模式会随版本变化；Eidos 的真实行为以本仓库代码和测试为准。

| 功能 | Codex 参考 | Eidos 实现 |
| --- | --- | --- |
| 角色 | 官方文档列出 default、worker、explorer；worker 偏执行，explorer 偏探索 | `SpawnAgent.role` 默认为 worker，explorer 使用读取工具白名单 |
| 权限 | 子 Agent 继承父任务当前沙箱与审批设置；角色可收紧 | 子 Run 固定父 Run 的审批模式与扩展快照，自身重新生成权限快照；父 Run 仍有效时可使用它已批准的 Run 范围 Grant |
| 工具 | 子 Agent 运行独立线程，按配置获得工具 | worker 复用现有文件、Shell、Skill、MCP 工具执行链；禁止再派生和主动申请额外 Grant |
| 状态与导航 | 后台 Agent 面板可查看状态、停止或打开线程 | 环境信息列出子任务，右侧工作区打开详情、审批、记录和停止入口 |
| 上下文 | 子 Agent 有独立上下文；Codex 可选择 fork_turns | Eidos 仅传递明确的委派任务，尚未支持上下文 fork |
| 写入隔离 | Codex 并行写入需要协调；Claude 可选独立 worktree | 目前共享父目录，下一阶段实现子编码线程独立 worktree 和变更交付 |

参考：

- Codex 文档：https://developers.openai.com/codex/subagents
- Codex V2 spawn 实现：https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs
- Codex 多 Agent 指令：https://github.com/openai/codex/blob/main/codex-rs/prompts/src/model_messages/multi_agent.rs
- Claude 子 Agent 工具和权限：https://code.claude.com/docs/en/sub-agents
- Claude Worktree：https://code.claude.com/docs/en/worktrees

下一阶段首先复用现有 managed Worktree 创建与生命周期服务，为 worker 分配自己的工作目录和变更审查入口。父任务和子任务的变更需要在明确的基准提交上比较；父目录中的未提交修改不能假定会自动进入子 Worktree。之后再处理上下文继承、模型选择、多层委派和整组预算。
