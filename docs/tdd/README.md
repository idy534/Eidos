# Eidos Agent Runtime 完整目标态 TDD

版本：v0.4

第一期实现以 [MVP Lite](../mvp-lite.md) 为准，使用 stdio JSON-RPC 双向协议和 `Session -> Run -> Item/ToolCall`。第二期以 [第二期实施范围清单](../mvp-phase-2.md) 为准：继续沿用该已验证的 Desktop/Main/sidecar 边界，优先实现持久化、调度、恢复、安全扫描与文件契约。HTTP/SSE、Model Profile 双 wire、Public Mode 和 Artifact 仍是后续目标态能力。

## 文档目录

1. [总体架构](01-architecture.md)
2. [Runtime、队列与状态机](02-runtime-state-machine.md)
3. [工具、审批与沙箱](03-tools-approval-sandbox.md)
4. [模型、上下文与流式输出](04-model-context-streaming.md)
5. [API、事件与存储](05-api-events-storage.md)
6. [桌面端安全与生命周期](06-desktop-security-lifecycle.md)
7. [测试与里程碑](07-testing-and-milestones.md)

## 完整目标态实现原则

- 安全边界故障时 fail closed，不允许降级为无沙箱 Shell。
- 规范化业务表是当前状态来源，Events 是同事务写入的追加式 Timeline/Outbox。
- 多个 Run 可以存在，但模型调用和工具执行由一个持久化 FIFO 执行器串行调度。
- 对有副作用操作不做自动重放；失败后先确认事实，再允许下一次变更。
- 所有不可信内容在截断、展示、模型观察和持久化之前经过同一版本化敏感规则。
- 文件读取证据、完整 Diff、真实路径身份和元数据复检共同约束逐文件修改；Shell 由固定 Toolchain、前后 Manifest、资源监控和有界双流输出共同约束。
- Model Profile 的编辑/Archive、独占凭证、Responses/Chat 双 Adapter、Endpoint/TLS/认证/参数校验、显式能力探测、WebSocket 到 HTTP(S) 的有界降级、无状态上下文、流容量、上下文预算和 snapshot 失效由 Gateway、API 与存储共同约束。
- 工具能力由独立 `tool_contract_version`、递归闭合的 Tool Schema Dialect v1、本地 effective arguments 校验、确定性 Step tool set 和协议无关 canonical ToolResult 共同约束；Provider `strict=false` 不替代 Runtime 授权。
- ToolResult 使用版本化 deterministic JSON、唯一 immutable base、每 Step 冻结 projection 和跨重启故障 quarantine；list/read/range/search、文件变更、Artifact 与 Shell 的模型结果均有闭合字段、容量、排序和错误映射契约。
- Q131-Q140 固定副作用工具 success/no-op、Shell observation/outcome、`side_effects_may_exist`、Reject feedback 以及 code 专属 error data；API Error 与模型 ToolResult Error 分层。
- Q141-Q150 固定 ToolResult 数值与 Unicode canonical 规则、SQLite 迁移、Shell guardian、ready gate、RunSnapshot、水位续接、Workspace 持久身份、唯一执行权、allowed actions 与 reconciliation epoch。
- Q151-Q155 固定持久化 operation 幂等、闭合 API/IPC DTO 与水位 keyset、Event contract、Unix 毫秒/monotonic 时间和 storage fail-closed 恢复。
- Q156-Q159 固定第二期 RuntimeEngine 的职责 seam、持久状态与执行态分层、Pydantic 闭合契约模型，以及 ToolSpec 的分级副作用语义。
- MVP Lite 的代码和自动化验收已落地；完整目标态中未列入第二期实施范围的条目仍只是技术契约，不代表能力已经落地。

模型原始 reasoning 内容不进入持久化或 UI；模型文本按 `assistant_progress` 与 `final_answer` 分类。
