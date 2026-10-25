# 需求索引

本目录回答“用户需要什么、功能范围是什么”。当前仓库的专题材料通常把需求与技术方案写在同一份文档中。本索引直接链接已有需求章节，保留原文的上下文，不复制第二份正文。

这些章节保留记录时的需求与范围，不表示其中每项都已实现。当前能力与限制以 [当前说明](../current/README.md)、源码和测试为准。

## 专题需求

| 专题 | 需求与范围 | 技术方案 | 当前说明 |
| --- | --- | --- | --- |
| 自定义子 Agent | [目标与已确认约定](../design/custom-subagents-design.md#1-目标与已确认约定) | [配置、模型、引用与实施记录](../design/custom-subagents-design.md) | [当前能力](../current/capabilities.md)、[当前限制](../current/limitations.md) |
| Session 聊天分叉 | [第一版产品规则](../design/session-conversation-fork-design.md#3-第一版产品规则) | [历史保存、接口与交互](../design/session-conversation-fork-design.md) | [当前架构](../current/architecture.md)、[当前限制](../current/limitations.md) |
| 钉钉渠道 | [目标与首版范围](../design/dingtalk-channel-design.md#目标与首版范围) | [渠道方案与实施记录](../design/dingtalk-channel-design.md) | [当前渠道](../current/features/channels.md) |
| 飞书与企业微信渠道 | [结论与范围](../design/feishu-wechat-channel-design.md#结论与范围) | [平台接入与配置](../design/feishu-wechat-channel-design.md) | [当前渠道](../current/features/channels.md) |
| 本地定时任务 | [用户可以创建什么任务](../design/scheduled-tasks-research-and-design.md#3-用户可以创建什么任务) | [整体方案](../design/scheduled-tasks-research-and-design.md)、[automation 工具设计](../design/automation-tool-design.md) | [当前定时任务](../current/features/automations.md) |
| 聊天与文件搜索 | [产品范围与交互](../design/search-research-and-design.md#4-产品范围与交互) | [分阶段实施方案](../design/search-research-and-design.md) | [当前能力](../current/capabilities.md)、[当前限制](../current/limitations.md) |
| 内置网页搜索 | [最终产品决定](../design/web-search-implementation-design.md#1-最终产品决定) | [设置、协议与工具执行](../design/web-search-implementation-design.md) | [当前网页搜索](../current/features/web-search.md) |

## 历史产品需求

- [v0.4 目标态 PRD](../archive/design-v0.4/prd/README.md) 保存当时的产品定位、用户流程、功能、安全与验收标准。它不代表当前版本的需求承诺。
- [历史阶段范围与基线](../archive/phases/README.md) 保存 MVP 和 Phase 的原始计划与验收记录。

维护者后续新增独立 PRD 时，应将文件放在本目录，并说明需求状态、范围、验收条件和对应设计。维护者不能仅凭目录位置把旧方案标成已确认需求。

返回 [文档入口](../README.md)。
