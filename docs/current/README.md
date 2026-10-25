# 当前实现说明

本目录说明 Eidos 当前已经实现的行为。生产代码与自动化测试是事实来源；方案、调研和历史验证需要按各自的范围阅读。

维护者在 [2026-10-25 文档与源码核对记录](../evaluations/documentation-source-audit-2026-10-25.md) 中记录本轮发现、源码依据和修正范围。各页按日期保留的测试数字只说明当次验证，不表示当前工作区已重新通过这些检查。

## 整体说明

| 文档 | 内容 |
| --- | --- |
| [整体架构](architecture.md) | 进程、主要职责、调用链和运行机制。 |
| [当前能力](capabilities.md) | 已接入的能力与对应实现入口。 |
| [当前限制](limitations.md) | 尚未形成闭环的范围、已知限制与验证边界。 |
| [执行门控](execution-gates.md) | 审批、执行、恢复与资源检查的保留和调整规则。 |

## 功能与机制

- [当前功能说明](features/README.md) 集中列出扩展、记忆、渠道、定时任务和网页搜索。
- [架构专题](../architecture/README.md) 按 Harness 组成解释结构、算法、时序、源码与测试入口。
- [进程架构图](../architecture/overview.html) 展示 Desktop、Runtime、模型、工具与持久事实的关系。
- [Harness 离线图集](../architecture/harness-atlas.html) 提供各机制的结构图与时序图。

维护者把具体功能的当前行为写入 features/，把设计过程写入 [设计目录](../design/README.md)。这些文档中的既有验证记录保留其时间与范围，不能代替当前回归。

返回 [文档入口](../README.md)。
