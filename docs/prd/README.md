# Eidos Agent Runtime MVP PRD

版本：v0.4

## 文档目录

1. [产品定位与范围](01-product-scope.md)
2. [用户流程与界面](02-user-flows-and-ui.md)
3. [功能需求](03-functional-requirements.md)
4. [安全与非功能需求](04-security-and-nfr.md)
5. [验收标准](05-acceptance-criteria.md)

## 当前结论

Eidos MVP 是仅面向 macOS 的本地前台执行型 Agent Runtime。它通过全局单执行器、工具审批、macOS Seatbelt 沙箱、可恢复状态机和持久化 Timeline，完成 Workspace Mode 与 Public Mode 的核心闭环。

内嵌 Workspace Terminal、后台服务、跨平台支持、文件快照恢复、智能上下文摘要、多 Agent 和企业能力不进入 MVP。

模型原始推理内容不保存、不展示；Execution Feed 只呈现模型主动输出的进度说明和最终回答。

用户输入、工具参数、文件、输出和持久化数据统一经过版本化敏感规则；硬拒绝不可审批绕过，有副作用内容不会被静默脱敏后执行。
