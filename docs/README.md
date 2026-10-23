# Eidos 文档入口

本目录把当前说明、需求、设计、调研、验证和历史资料分开组织。生产代码和自动化测试仍是当前实现的事实来源。

## 按问题找文档

| 你要了解什么 | 阅读入口 | 使用边界 |
| --- | --- | --- |
| 现在已经实现什么 | [当前说明](#当前说明) | 当前说明需要与源码和测试一起核对。 |
| 某个功能要解决什么问题 | [需求索引](requirements/README.md) | 索引指向已有需求章节，并区分历史 PRD。 |
| 某个功能准备怎么做、当时怎么实现 | [设计与实施记录](design/README.md) | 方案、分支状态和待办保留记录时的含义。 |
| 方案为什么这样选 | [调研与对照](research/README.md) | 调研建议不等于最终决定或已实现能力。 |
| 某次验证实际覆盖了什么 | [验证与评估](evaluations/README.md) | 记录只证明当时的范围，不替代当前回归。 |
| 怎样安装、启动和构建 | [本地开发指南](../DEVELOPMENT.md) | 指南只保留基础环境、流程与常用命令。 |
| 怎样配置专题功能、执行详细验证 | [开发参考与专题验收](development/README.md) | 手册按主题保存配置、验证、打包细节和诊断步骤。 |
| 旧版本当时怎样规划 | [历史归档](archive/README.md) | 历史资料不作为当前实现依据。 |

## 当前说明

当前说明只描述当前代码已经实现的行为。维护者应先核对生产代码和自动化测试，再更新这些文档。

权威顺序如下：

```text
Production Code + Tests
        ↓
current-architecture.md
current-capabilities.md
current-limitations.md
architecture-overview.html
```

- [当前架构](current-architecture.md)：说明进程、边界、职责和真实调用链。
- [Runtime Git Worktree Kernel](architecture/git-worktree.md)：说明 Project、Worktree、恢复和 Sandbox 边界。
- [当前能力](current-capabilities.md)：说明当前 main 已经能做什么。
- [当前插件](current-plugins.md)：说明插件格式、版本更新、MCP 接入和后续内置插件的边界。
- [当前 Hooks](current-hooks.md)：说明已接入的 Hook 事件、信任和兼容边界。
- [当前记忆](current-memory.md)：说明作用域、来源、发布、使用和撤销。
- [当前渠道接入](current-channels.md)：说明钉钉、飞书和企业微信配置、收发消息、权限、会话与恢复。
- [当前本地定时任务](current-automations.md)：说明时间规则、执行目标、审批、工具、持久化和验证边界。
- [当前网页搜索](current-web-search.md)：说明已接入的搜索服务、设置、工具和结果限制。
- [当前限制](current-limitations.md)：说明当前 main 还没有形成哪些完整闭环。
- [执行门控清单](current-execution-gates.md)：列举移除、调整和保留的检查及其依据。
- [交互式架构图](architecture-overview.html)：按总览、Run、权限与恢复、数据与目录、源码索引阅读；HTML 支持离线打开和打印。

## 需求与设计

- [需求索引](requirements/README.md) 按功能指向已有的目标、交互和范围章节。现有专题文档同时包含需求与技术方案时，索引直接指向原章节。
- [设计与实施记录](design/README.md) 收录子 Agent、聊天分叉、渠道、定时任务和搜索方案。
- [当前专题架构](architecture/README.md) 收录源码对应的专题结构说明。

## 调研与验证

- [调研与对照](research/README.md) 收录竞品对照、服务选型和方案缺口复核。
- [验证与评估](evaluations/README.md) 收录带日期的交付、评估与证据文件。

## 开发

- [本地开发指南](../DEVELOPMENT.md)：说明基础环境、安装、启动、常用检查和构建入口。
- [开发参考与专题验收](development/README.md)：按需查阅功能配置、定向命令、人工验收、打包细节和诊断。

## 历史归档

- [历史文档归档](archive/README.md)：说明历史 PRD、TDD、Decision、参考资料和 Phase 文档的使用边界。

归档目录按版本和阶段保留旧 PRD、TDD、Decision 与参考资料。旧的 [PRD 兼容入口](agent_runtime_mvp_prd.md) 和 [TDD 兼容入口](agent_runtime_mvp_tdd.md) 继续保留原路径。

## 维护规则

- 维护者把当前行为写入根目录的 current-* 文档。DEVELOPMENT.md 只保留基础开发指南；专题配置与详细验证写入 development/README.md。
- 维护者先判断新文档属于需求、设计、调研、验证还是历史资料，再放入对应目录，并更新该目录的 README。
- 维护者在设计中区分需求、建议、实现记录和未完成事项。文档位于 design/ 不代表功能已经实现或尚未实现。
- 维护者可以保留同一专题中的完整上下文，通过需求索引定位章节，不复制出第二份需求正文。
- 维护者只有在确认资料已经被替代或冻结时，才把它归档。专题已经有代码实现，不代表其中所有后续设计都应该归档。
- 维护者移动文件时，需要同步修改入站、出站链接，并保留原有日期、验证范围与证据文件。
