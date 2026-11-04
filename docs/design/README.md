# 设计与实施记录

本目录回答“功能怎样实现、各层怎样配合”。文档保留设计过程、分支状态、实施记录和后续范围。维护者需要按原文的时间和验证范围阅读，不能把整份方案当成当前实现。

用户目标与范围可以从 [需求索引](../requirements/README.md) 进入。当前行为以 [当前说明](../README.md#当前说明)、源码和测试为准。

## Agent 与 Session

- [自定义子 Agent](custom-subagents-design.md)：TOML 定义、模型选择、引用、权限与分阶段实施记录。
- [Session 聊天分叉](session-conversation-fork-design.md)：产品规则、继承历史、文件环境、持久化与界面方案。

## 本地定时任务

- [本地定时任务整体方案 V3](scheduled-tasks-research-and-design.md)：任务范围、触发行为、架构、权限与实施顺序。
- [automation 工具详细设计 V3](automation-tool-design.md)：工具发现、参数、绑定、幂等、权限与失败处理。
- 方案采用前的检查见 [延迟工具与缺口复核](../research/scheduled-tasks-gap-review.md)。

## 渠道

- [钉钉渠道接入方案与实施记录](dingtalk-channel-design.md)：消息接收、Session 路由、普通 Run、回复与恢复。
- [飞书与微信渠道方案](feishu-wechat-channel-design.md)：飞书、企业微信的接入，以及个人微信的独立调研与后续边界。

## 搜索

- [聊天与文件搜索方案](search-research-and-design.md)：侧栏搜索、Session 查找、文件查找与后续阶段。
- [内置网页搜索实现方案](web-search-implementation-design.md)：服务选择后的设置、协议、凭据与工具执行设计。
- 服务选择前的材料见 [网页搜索调研与筛选](../research/web-search-research-and-selection.md)。

## 浏览器操作

- [浏览器操作能力设计方案](browser-automation-design.md)：选型、BrowserHost、沙盒、工具面、授权、上下文与分阶段实施；当前为设计方案。
- [MCP 工具调用展示优化](mcp-tool-display-design.md)：截图可见、MCP 工具行的标题、结果、参数与错误展示，以及浏览器结果的结构化投影；当前为设计方案。
- [与用户共用 Chrome（标签组模式）](shared-chrome-tab-group-design.md)：基于 Playwright Extension 的标签组共用、本机代理、令牌、授权与分阶段实施；P1、P2 已实施。

## 文档资源

- [文档资源裁剪与按需下载](document-resource-delivery.md)：安装包资源分类、本地复用、后台准备与现有依赖绑定的调整范围；当前为设计方案。

## 相关入口

- [当前专题架构](../architecture/README.md) 描述需要与现行源码同步维护的结构。
- [历史 TDD](../archive/design-v0.4/tdd/README.md) 保存旧版本的技术设计。
- [验证与评估](../evaluations/README.md) 单独保存验证范围和证据。

返回 [文档入口](../README.md)。
