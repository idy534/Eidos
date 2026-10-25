# 文档与源码核对记录（2026-10-25）

## 结论与范围

本轮发现当前文档存在旧能力说明、同页前后矛盾，以及图文不同步。维护者已按当前源码修正下表中的差异。主调用链仍是 Renderer → typed preload IPC → Main → stdio JSON-RPC → Application → Supervisor / Engine → 模型与工具 → SQLite / Event / Outbox。

本轮核对的源码基线是 `c3d24633`。核对范围包括中英文 README、开发入口、`docs/current/`、`docs/architecture/`、文档导航，以及设计、调研和评估资料的用途边界。本轮还检查仓库文档中的本地链接、章节锚点和命令入口。源码核对按下列十二组机制展开，重点检查能力、权限、状态、版本和调用链；本轮没有对每个句子建立形式化证明。

历史方案、归档和带日期的测试数字保留原语境。旧设计描述当时的计划，旧验证只证明当时执行的范围。维护者没有把这些历史记录改写成当前测试结果。

## 已修正的差异

| 问题 | 文档中的旧说法 | 当前源码与修正 |
| --- | --- | --- |
| 扩展能力与路线 | README 只列 stdio MCP，并把远程 MCP、OAuth 和用户 Hooks 全部列为未来工作。 | [MCP](../../runtime/eidos_runtime/extensions/mcp.py) 已接入 Streamable HTTP，[账号连接](../../runtime/eidos_runtime/application/mcp_connections.py) 已接入认证，[Hook 定义](../../runtime/eidos_runtime/domain/hooks.py) 已支持 12 个事件。中英文 README 现在区分已实现能力与真实服务验收。 |
| 渠道范围与入口 | 能力页和架构概要只介绍钉钉，并指向“设置 → 渠道”。 | [渠道类型](../../runtime/eidos_runtime/domain/channels.py) 包含 dingtalk、feishu、wecom。当前入口是“设置 → 助理”。概要现在说明三平台与不同回复传输。 |
| 完全访问来源 | 权限专题只列普通 Desktop Run 和定时计划。 | [ChannelApplication](../../runtime/eidos_runtime/application/channels.py) 也保存、校验和传递用户确认。专题现在包含渠道来源。 |
| 主库版本与升级 | 能力页写 v39，Worktree 专题写 v1 且不升级旧库。 | [schema.py](../../runtime/eidos_runtime/db/schema.py) 的版本是 41，[database.py](../../runtime/eidos_runtime/db/database.py) 包含旧版本迁移。两处现在说明 v41、受支持迁移与未知版本的 health-only 行为。 |
| 记忆存储与维护 | 架构正文和交互图仍列“旧记忆辅助库”与 Memory 后台任务。 | [存储布局](../../runtime/eidos_runtime/db/layout.py) 与 [StorageMaintenance](../../runtime/eidos_runtime/db/maintenance.py) 已使用主库记忆元数据和存储维护服务。v40 清理旧 memories.sqlite，当前正文由私有 Markdown 文件保存。 |
| Worktree 生命周期 | 操作枚举遗漏 checkpoint/rewind。 | [WorktreeLifecycleScope](../../runtime/eidos_runtime/domain/worktree.py) 包含该操作。专题现在同步枚举。 |
| Git、置顶与 Checkpoint | Worktree 专题把置顶、并行 Agent、Git UI、完整 Git 状态回退等已实现能力列为缺失。 | [置顶](../../runtime/eidos_runtime/application/sidebar.py)、[Git Workflow](../../runtime/eidos_runtime/application/git_workflow.py) 和 [Checkpoint](../../runtime/eidos_runtime/persistence/checkpoints.py) 已有实现。专题和限制页现在保留真正缺失的 Worktree 隔离、PR UI、逻辑 Context 回退等边界。 |
| Git metadata 权限 | Worktree 专题写 metadata 只能只读。 | [权限物化](../../runtime/eidos_runtime/sandbox/permissions.py) 支持获批的精确写入和 full_access。专题现在说明默认权限与获批例外。 |
| 长 Shell 门控 | 架构主流程写长 Shell 会阻止同 Run 的新副作用。 | [Tool Runtime](../../runtime/eidos_runtime/runtime/tool_runtime.py) 只在提交与启动窗口互斥。正文现在与后续章节保持一致。 |
| Agent Shell PTY | 工具专题写 ShellProcessManager 管理 PTY。 | [ShellProcessManager](../../runtime/eidos_runtime/runtime/shell_process_manager.py) 使用管道；[Main Terminal](../../desktop/main/terminal-manager.ts) 单独管理 PTY。专题现在区分两条路径。 |
| Shell cwd | 能力页与限制页一律拒绝 Workspace 外的绝对 cwd。 | [Shell 启动边界](../../runtime/eidos_runtime/sandbox/shell.py) 对 full_access 提供例外，同时核验目录身份。两页现在按模式说明。 |
| 受保护 Skill 写入 | 部分段落写系统 Skill 永久禁写、不能接受授权。 | [PermissionPolicyEvaluator](../../runtime/eidos_runtime/runtime/permission_policy.py) 与[权限物化](../../runtime/eidos_runtime/sandbox/permissions.py) 支持高危人工确认的具体例外。文档现在说明默认拒写、高危人工确认与 full_access 的范围。 |
| 未知结果门控与取消 | 门控表和旧段落仍按未知 Intent 阻止文件、子 Agent 或非空 stdin，并把取消写成 interrupted。 | [Tool Runtime](../../runtime/eidos_runtime/runtime/tool_runtime.py)、[协作](../../runtime/eidos_runtime/application/collaboration.py) 与 [Run Repository](../../runtime/eidos_runtime/db/repositories/runs.py) 保留未知事实，但不建立这类后续执行门；取消使用 canceled。文档现在保留当前身份、版本、所有权、Intent 和不重放规则。 |
| Repository readiness | 限制页写第一个 Model Step 前同步 ensure_ready。 | [Engine](../../runtime/eidos_runtime/runtime/engine.py) 调用 available_for_run，[Repository Runtime](../../runtime/eidos_runtime/application/repository.py) 由既有 watcher 构建索引。文档现在说明缺少 Snapshot 时继续使用普通工具。 |
| 流式草稿持久化 | 架构写完整响应校验前不会持久化 provisional text。 | [SamplingRuntime](../../runtime/eidos_runtime/runtime/sampling.py) 经 AssistantStreamWriter 保存 in_progress Item，失败草稿不进入后续模型上下文。正文现在区分持久化和确认。 |
| 子 Agent 能力 | 限制页仍写没有自定义角色，也不能选择不同模型。 | [AgentDefinition](../../runtime/eidos_runtime/domain/agents.py) 支持自定义定义、模型与思考设置。限制页现在保留单层委派、共享 Workspace 和缺少自动合并等真实边界。 |
| Hook 图文 | 交互图只列五个事件和 command Handler，限制页仍写剩余事件待实现。 | [HookEvent](../../runtime/eidos_runtime/domain/hooks.py) 列出 12 个事件，[HookService](../../runtime/eidos_runtime/runtime/hooks.py) 与 [HookLifecycle](../../runtime/eidos_runtime/runtime/hook_lifecycle.py) 支持同步、后台与已连接 MCP 处理。图文现在同步，prompt/agent Handler 仍不执行。 |
| 其他前后矛盾 | 文档仍写容量失败会暂停、普通审批等待中能编辑普通 Composer，并遗漏 Skill 可选 metadata 的其他作用。 | 文档现在说明容量恢复投影、专用审批/澄清输入区与保留草稿，以及 agents/eidos.yaml 的界面、依赖和隐式激活策略。文档还删除重复段落，并把无日期的“待测试”标题改成行为标题。 |

## 按机制核对的范围

| 机制 | 本轮读取的主要依据 | 核对结果 |
| --- | --- | --- |
| 进程与协议 | protocol/server.py、protocol/methods.py、Desktop 共享契约与 package.json | stdio 控制面与 typed IPC 的说明保留；渠道与服务概要已更新。 |
| Run、工具与 Shell | runtime/engine.py、sampling.py、tool_runtime.py、shell_process_manager.py | 门控、草稿持久化、PTY 与 cwd 的差异已修正。 |
| 权限与 Sandbox | runtime/permission_policy.py、sandbox/permissions.py、sandbox/shell.py | 默认保护与用户确认例外已同步。 |
| 存储与恢复 | db/schema.py、database.py、layout.py、recovery.py、maintenance.py | v41、旧库迁移与维护服务的说明已同步。 |
| Context 与压缩 | context/budget.py、context/compactor.py、runtime/engine.py | 预算公式保留；容量暂停与旧停止原因的说法已修正。 |
| Model Gateway | model 适配入口、runtime/sampling.py、test_model_retry_recovery.py | 两类协议路径保留；有限重试与目标规则的差距单独列出。 |
| Repository Intelligence | application/repository.py、runtime/engine.py | 首次索引不阻塞模型；文档已同步。 |
| Git、Worktree 与 Checkpoint | domain/worktree.py、application/git_workflow.py、persistence/checkpoints.py | 操作集合、现有能力与未实现边界已更新。 |
| Skill、Plugin、MCP 与 Hook | extensions/、domain/hooks.py、runtime/hooks.py、hook_lifecycle.py | transport、账号、事件与信任边界已同步。 |
| Plan 与子 Agent | domain/agents.py、application/collaboration.py、相关当前专题 | 自定义模型与单层委派的说明已统一。 |
| 定时任务、渠道与搜索 | automation_scheduler.py、application/channels.py、domain/web_search.py、protocol/web_search.py | 当前配置与凭据回显说明保留；概要补齐三渠道，搜索授权表述已同步。 |
| 开发、观测与交付 | package.json、pyproject.toml、uv.lock、telemetry/provider.py、db/backup.py | 基础命令、锁定依赖、默认关闭远程 Trace 导出和备份格式说明保留；历史验证不作为当前发布证据。 |

## 源码仍未完全达到的目标

`AGENTS.md` 要求普通重复失败、模型输出上限、超时、限流和重试预算不自动结束整个 Run。当前 SamplingRuntime 仍限制普通请求组的尝试次数和恢复期限，Engine 仍保留一次协议修复与 LoopGuard 收尾。这是实现与目标规则的差距，维护者不能通过改写文档宣称它已解决。

[test_model_retry_recovery.py](../../runtime/tests/test_model_retry_recovery.py) 的 `test_retry_budget_does_not_reenter_capacity_recovery` 明确断言六次失败后 Run 为 failed。[Engine](../../runtime/eidos_runtime/runtime/engine.py) 与 [LoopGuard](../../runtime/eidos_runtime/runtime/loop_guard.py) 也保留无进展与协议修复路径。本轮在[执行门控](../current/execution-gates.md#当前实现与目标规则的差距2026-10-25)中补充了这一点。本轮没有修改这些生产行为或测试。

## 本轮验证与改动边界

本轮只修改 Markdown 和交互式架构图中的说明文字。图的结构、Mermaid 图源与生成的 SVG 没有变化。源码、依赖、锁文件、协议 Fixture 和数据库都没有变化。本轮没有新增运行机制，也没有删除生产机制。

本轮静态检查覆盖 82 个 Markdown / HTML 文件、943 处本地链接目标及其章节锚点、81 处 Shell 代码块中的 pnpm 命令入口。检查没有发现缺失文件、缺失章节或未知命令入口。交互式架构图的内嵌 JavaScript 通过 `node --check`，`git diff --check` 通过。维护者还修复了设计文档中误用 `#L432` 的章节链接。

本轮没有运行产品测试、Full Suite、构建、依赖审计、真实平台账号或发布验收。文档中的测试引用是核对依据，不是本轮执行结果。维护者后续修改恢复策略时，仍需按实际行为补充定向测试。
