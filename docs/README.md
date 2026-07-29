# Eidos 文档

## 当前实现

| 文档 | 内容 | 权威性 |
|---|---|---|
| [当前架构](current-architecture.md) | 进程、状态、调用链和职责边界 | 当前实现 |
| [当前能力](current-capabilities.md) | 已落地的用户与 Runtime 能力 | 当前实现 |
| [当前限制](current-limitations.md) | 当前未实现或受限的边界 | 当前实现 |

判断 Eidos 当前行为时，以代码、自动化测试和以上 `current-*` 文档为准。

## 设计与历史

| 文档 | 用途 |
|---|---|
| [PRD](prd/README.md) | 产品目标与候选验收，不保证已实现 |
| [TDD](tdd/README.md) | 目标技术设计与长期约束，不保证已实现 |
| [设计决策](decisions.md) | 决策背景和演进记录 |
| [历史 Phase](archive/phases/README.md) | 已归档的 MVP/Phase 基线、清单和验收记录 |

旧 Phase 文档不再作为当前实现依据。旧入口 `agent_runtime_mvp_prd.md` 与 `agent_runtime_mvp_tdd.md` 仅保留链接兼容。

## 推荐阅读顺序

1. 先读 `current-architecture.md`、`current-capabilities.md` 和 `current-limitations.md`。
2. 需要理解目标设计时再读 PRD/TDD。
3. 需要追溯阶段交付或旧决策时再读 `decisions.md` 与 `archive/phases/`。

## 稳定边界

- 本地控制面是 `Electron Main <-> Python Runtime` 的 stdio JSON-RPC 2.0/JSONL；stdout 只承载协议，stderr 承载日志。
- SQLite 是业务状态、事件和恢复事实的唯一权威来源；内存对象只负责运行中协调与诊断。
- Runtime 对工具参数、审批、沙箱、结果投影和副作用状态独立校验，模型输出不是执行授权。
