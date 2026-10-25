# 当前专题架构

本目录保存需要与当前源码同步维护的专题结构说明。设计过程与实施计划放在 [设计目录](../design/README.md)，旧版本技术方案放在 [历史归档](../archive/README.md)。

## 从整体到机制

- [Harness 组成与阅读地图](harness-overview.md)：十二个组成部分、事实与权限边界、官方文档准备范围。
- [离线 Harness 架构图集](harness-atlas.html)：21 张 SVG 结构图、算法图与时序图，支持离线查看、原图和打印。
- [整体架构](../current/architecture.md)：进程、模块职责、持久状态与真实调用链。
- [已有交互式进程架构图](overview.html)：系统总览、单次 Run、权限与恢复、数据和源码入口。

## Harness 专题

| 组成 | 机制与图 |
| --- | --- |
| 任务执行 | [Durable Agent Loop](runtime-loop.md) |
| 上下文管理 | [请求构建、预算与压缩算法](context-compaction.md) |
| 长期记忆 | [索引、主题笔记、发布与撤销](memory.md) |
| 多提供商模型 | [Profile、协议路由、断流与重试](model-gateway.md) |
| 工具运行 | [执行链、文件提交与 Shell](tool-runtime.md) |
| 权限隔离 | [模式、审批与 Sandbox](permissions-sandbox.md) |
| 持久化恢复 | [事务、Outbox、重启与关闭](persistence-recovery.md) |
| 代码与工作空间 | [检索、Workspace 与版本恢复](workspace-retrieval.md)；[Git Worktree Kernel 细节](git-worktree.md) |
| 扩展 | [Skill、Plugin、MCP 与 Hook](extensions-hooks.md) |
| 人机与多 Agent 协作 | [Plan、子任务依赖、消息与结果](collaboration-planning.md) |
| 持续任务入口 | [定时任务与渠道](automation-channels.md) |
| 质量与交付 | [日志、Trace、评测与发布证据](observability-delivery.md) |

## 图源维护

本轮专题按 2026-10-25 的源码基线 `58ba5ea9` 核对。各专题列出生产源码、相关测试和已知限制。测试链接不表示本轮执行过该测试。

维护者编辑 [Mermaid 图源](diagrams/)和[图目录](diagrams/catalog.json)，再使用[本地渲染脚本](render-diagrams.cjs)生成 SVG 与离线图集。维护命令见[组成总览](harness-overview.md#图源与维护)。脚本复用已有依赖，不请求网络。

当前事实仍以生产代码和自动化测试为准。

返回 [文档入口](../README.md)。
