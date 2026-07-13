# Eidos Agent Runtime MVP TDD

版本：v0.4

## 文档目录

1. [总体架构](01-architecture.md)
2. [Runtime、队列与状态机](02-runtime-state-machine.md)
3. [工具、审批与沙箱](03-tools-approval-sandbox.md)
4. [模型、上下文与流式输出](04-model-context-streaming.md)
5. [API、事件与存储](05-api-events-storage.md)
6. [桌面端安全与生命周期](06-desktop-security-lifecycle.md)
7. [测试与里程碑](07-testing-and-milestones.md)

## 实现原则

- 安全边界故障时 fail closed，不允许降级为无沙箱 Shell。
- 规范化业务表是当前状态来源，Events 是同事务写入的追加式 Timeline/Outbox。
- 多个 Run 可以存在，但模型调用和工具执行由一个持久化 FIFO 执行器串行调度。
- 对有副作用操作不做自动重放；失败后先确认事实，再允许下一次变更。
- 所有不可信内容在截断、展示、模型观察和持久化之前经过同一版本化敏感规则。
- 文件读取证据、完整 Diff、真实路径身份和元数据复检共同约束逐文件修改；Shell 由固定 Toolchain、前后 Manifest、资源监控和有界双流输出共同约束。
- Model Profile 的编辑/Archive、独占凭证、Responses/Chat 双 Adapter、Endpoint/TLS/认证/参数校验、显式能力探测、WebSocket 到 HTTP(S) 的有界降级、无状态上下文、流容量、上下文预算和 snapshot 失效由 Gateway、API 与存储共同约束。
- 当前仓库尚无实现代码，本 TDD 是后续实现契约，不代表能力已经落地。

模型原始 reasoning 内容不进入持久化或 UI；模型文本按 `assistant_progress` 与 `final_answer` 分类。
