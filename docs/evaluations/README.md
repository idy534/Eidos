# 验证与评估

本目录回答“某次判断有何证据、某次改动验证了什么”。文档和证据保留记录时的版本、输入、命令、范围与限制。历史通过结果不代表当前工作区已经通过相同检查。

## 记录索引

| 日期 | 文档 | 记录范围 |
| --- | --- | --- |
| 2026-10-18 | [上下文管理验证记录](context-management-2026-10-18.md) | Context Usage、分项、压缩、排队消息和离线 / 模型评估的当次结果。 |
| 2026-10-19 | [压缩触发与 Codex 对照](context-compaction-policy-2026-10-19.md) | 一个已保存 Run 的只读诊断、源码对照与后续建议。该次记录没有运行测试。 |
| 2026-10-20 | [模型重试第一、二阶段交付记录](model-retry-validation-2026-10-20.md) | 当次实现范围、定向验证、已有失败和未验证内容。 |

## 证据文件

- [2026-10-19 压缩触发证据](context-compaction-policy-2026-10-19-evidence.json) 保存该次诊断的事件、投影与源码信息。
- 2026-10-18 的评估结果保存在 context-compaction-2026-10-18/：
  - [离线评估](context-compaction-2026-10-18/offline.json)
  - [DeepSeek 任务评估](context-compaction-2026-10-18/deepseek-free-task.json)
  - [DeepSeek 补充基线](context-compaction-2026-10-18/deepseek-free-additional-baseline.json)

维护者新增记录时，应说明日期、验证对象、实际命令、结果和未覆盖范围。评估正文与证据文件应一起维护，原始历史数字不能随当前结果改写。

返回 [文档入口](../README.md)。
