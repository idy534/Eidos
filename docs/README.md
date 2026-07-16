# Eidos Agent Runtime 文档

当前版本：MVP Lite v0.1（第一期已完成）/ 第二期实施范围 v0.2 / 完整目标态 v0.4

## 阅读顺序

1. [第一期实现基线：MVP Lite](mvp-lite.md)
2. [第二期实施范围清单](mvp-phase-2.md)
3. [完整目标态 PRD 索引](prd/README.md)
4. [完整目标态 TDD 索引](tdd/README.md)
5. [设计决策记录 Q1-Q159](decisions.md)

## 范围分层

- `mvp-lite.md` 是第一期实现范围的最高优先级文档，固定 stdio JSON-RPC 和 `Session -> Run -> Item/ToolCall`。
- `mvp-phase-2.md` 是第二期的唯一实施清单。它把完整目标态中本期要落地的条目按依赖拆分；完成实现和验收后才可把对应 `- [ ]` 改为 `- [x]`。
- `prd/`、`tdd/` 与 `decisions.md` 保存完整目标态和后续加固契约。
- 三层发生范围、协议、实体或里程碑冲突时，已完成的第一期以 `mvp-lite.md` 为准；第二期以 `mvp-phase-2.md` 为准；未列入第二期的能力仍以完整目标态文档为准。

## 文档职责

- `mvp-lite.md` 描述第一期必须做、明确延期、首期协议、最小领域模型和交付里程碑。
- `mvp-phase-2.md` 描述第二期的交付边界、依赖、逐项验收与状态。
- `prd/` 描述完整目标态的产品目标、用户行为、范围、安全承诺和验收标准。
- `tdd/` 描述完整目标态的架构、状态机、工具契约、沙箱、API、存储和测试。
- `decisions.md` 保存评审中已经确认的设计结论，避免后续文档修改丢失上下文。

旧入口 `agent_runtime_mvp_prd.md` 与 `agent_runtime_mvp_tdd.md` 保留为兼容索引，不再承载重复正文。
