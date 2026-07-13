# Eidos Agent Runtime MVP PRD

版本：v0.4

## 文档目录

1. [产品定位与范围](01-product-scope.md)
2. [用户流程与界面](02-user-flows-and-ui.md)
3. [功能需求](03-functional-requirements.md)
4. [安全与非功能需求](04-security-and-nfr.md)
5. [验收标准](05-acceptance-criteria.md)

## 当前结论

Eidos MVP 是仅面向 macOS 的本地前台执行型 Agent Runtime。它通过全局单执行器、工具审批、macOS Seatbelt 沙箱、确定性文件契约、受控 Toolchain、Shell 资源/输出限制、可恢复状态机和持久化 Timeline，完成 Workspace Mode 与 Public Mode 的核心闭环。

内嵌 Workspace Terminal、后台服务、跨平台支持、文件快照恢复、智能上下文摘要、多 Agent 和企业能力不进入 MVP。

模型原始推理内容不保存、不展示；Execution Feed 只呈现模型主动输出的进度说明和最终回答。

用户输入、工具参数、文件、输出和持久化数据统一经过版本化敏感规则；硬拒绝不可审批绕过，有副作用内容不会被静默脱敏后执行。

Model Profile 支持编辑和 Archive/恢复，通过用户显式 Test Connection 后才能使用；凭证按 Profile 隔离，HTTP(S)/TLS、认证、双 wire API、扩展参数、传输降级、无状态上下文和输出容量遵循 Q81-Q110 的固定边界，既有 Run 保持创建时的非密钥配置与能力快照。工具控制、封闭 schema、静态默认值、版本化工具契约、当步可用工具集与 canonical ToolResult 遵循 Q111-Q120；结果序列化、安全模板、immutable base、Context projection 及 list/read/range/search 结果契约遵循 Q121-Q130；文件变更、Artifact、Shell、共享非成功与错误 data 契约遵循 Q131-Q140；数值与 Unicode canonical 规则、迁移与启动、Shell guardian、Workbench snapshot、Workspace 身份、单实例、可执行动作和事实确认遵循 Q141-Q150；写 API 幂等、闭合 DTO/分页、Event 兼容、统一时间和存储故障恢复遵循 Q151-Q155。
