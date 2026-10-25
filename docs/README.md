# Eidos 文档入口

本目录按用途组织文档。生产代码和自动化测试是当前实现的事实来源。

## 当前说明

读者从 [当前实现说明](current/README.md) 查看整体架构、能力、限制与执行规则，再按需进入 [当前功能说明](current/features/README.md)。

读者从 [架构专题](architecture/README.md) 查看 Harness 的组成、机制与源码入口，也可以直接打开 [进程架构图](architecture/overview.html) 或 [Harness 离线图集](architecture/harness-atlas.html)。

## 按用途查找

| 目录或入口 | 内容 |
| --- | --- |
| [current/](current/README.md) | 当前实现的整体说明；具体功能集中在 features/。 |
| [architecture/](architecture/README.md) | 系统组成、算法、时序、源码入口和架构图源。 |
| [requirements/](requirements/README.md) | 用户目标、需求范围和已有需求章节的索引。 |
| [design/](design/README.md) | 具体功能的方案、设计过程和实施记录。 |
| [research/](research/README.md) | 服务选型、产品对照和实施前的缺口分析。 |
| [evaluations/](evaluations/README.md) | 带日期的验证、评估与证据。 |
| [development/](development/README.md) | 专题配置、定向检查、人工验收、打包细节与诊断。 |
| [archive/](archive/README.md) | 已冻结的旧 PRD、TDD、Decision、阶段与参考资料。 |
| [DEVELOPMENT.md](../DEVELOPMENT.md) | 基础环境、安装、启动、常用检查与构建入口。 |

## 分类边界

- current/ 说明当前实现。architecture/ 解释它的结构和机制。两类文档都需要对照源码与测试维护。
- current/features/ 保存插件、Hooks、记忆、渠道、定时任务和网页搜索的当前说明。design/ 保存这些功能的设计过程，两者不混作同一种证据。
- requirements/ 直接指向已有的需求章节，不复制第二份需求正文。
- evaluations/ 只证明记录时的验证范围。历史通过结果不代表当前版本已经通过相同检查。
- archive/ 按版本和阶段保留原始资料。历史 PRD 与 TDD 统一从对应归档目录进入。

## 维护规则

- 维护者在 docs/ 根目录只保留本索引，把新文档放入对应子目录。
- 维护者把整体能力、限制和运行规则放入 current/，把具体功能说明放入 current/features/。
- 维护者修改功能行为时，同步相关当前说明和测试；设计、调研与历史记录不能代替当前事实。
- DEVELOPMENT.md 只保留基础开发指南。维护者把专题配置与详细验证写入 development/README.md。
- 维护者移动文件时，同时更新入站、出站链接和章节引用，并保留原有日期、正文与验证范围。
