# 当前本地定时任务

Eidos 的定时任务在本机运行。SQLite 是计划、触发和执行来源的事实来源。实际执行使用普通 Run，继续遵守现有权限、取消、恢复和结果核验规则。Eidos 完全退出或电脑休眠时，任务不会执行。系统不检测漏跑，不补跑，不使用云端或额外守护进程。

## 管理与执行目标

侧栏的“定时任务”页面提供创建、查看、编辑、暂停、恢复、立即执行、删除和分页执行历史。历史关联普通 Run，展示等待、执行、终态与核验状态。用户可以打开对应 Session 查看回答、文件和审批。计划软删除保留 Session、Workspace 和历史。

左侧常驻“即将执行”和“已完成与暂停”两个分组。前者展示启用中的周期任务，以及尚未执行的一次性任务。后者展示暂停任务，以及已经触发的一次性任务。系统依据保存的 status 和 one_shot_fired 来源分组，不依据日期猜测执行结果。“已结束”表示一次性计划不再触发，具体执行结果仍以 Run 历史为准。搜索同时作用于两个分组，分页继续加载同一任务列表。用户暂停、恢复或收到任务变化通知后，列表会重新分组，并保留当前详情。已经删除的计划继续遵循现有隐藏规则。

模型只使用一个 deferred 工具 automation。模型先通过 tool_search 激活，下一 Step 再取得完整定义。mode 支持 view / create / update / delete / preview。view 有 ID 时读详情，无 ID 时分页查找。模型不能通过描述或配置文件代替保存操作。

创建时提供 sessionId，任务复用已有普通 Session。不提供时，任务每次触发新建 Session。projectId 字符串指定项目，null 明确无项目，省略则继承创建对话的项目。已有 Session 的项目从该 Session 解析，额外 projectId 必须一致。新会话支持 Local 或 Git Worktree，无项目任务使用独立私有 Workspace。工作树不默认带入未提交修改或联网 fetch。

任务保存已有模型 ID 与思考选择。每次执行重新验证配置并固定本次快照。approvalMode 默认 auto_review（替我审批），也可显式使用 manual（请求审批）。更新省略字段时保留原值。未来 Run 不继承旧 Grant 或 full_access。自动审查失败、未知风险和高危人工请求继续沿用现有流程。

自动 Run 在聊天中标为“定时任务自动执行”。它不改变用户下一轮的模型、工作模式或审批默认值。原会话使用已有上下文预算和压缩；新会话使用自足任务正文，不复制创建对话的完整历史或临时附件。

定时任务新建的 Session 只使用任务名作为标题，并遵守现有会话标题的长度限制。标题不再拼接时间戳、manual 标识或内部 ID。历史会话在读取时会对照当次计划快照和触发记录；标题仍与旧生成格式完全一致时，页面显示规范后的任务名。其他自定义名称继续保留，工作区目录不受显示名称影响。

Session 的可选 automationOrigin 复用现有自动执行来源契约。Runtime 通过原始自动 Run 与触发记录确认会话由定时任务新建，列表、详情和快照共用同一投影。已有普通 Session 被任务复用时，来源仍保持原样。会话行在悬停或键盘焦点时显示 0.8rem 的定时任务图标，图形与侧栏定时任务入口相同。会话提示提供“会话来源：定时任务”链接，用户点击后直接打开对应计划的配置和执行历史。该入口支持不在当前任务列表页内的计划。

## 时间与占用

周期规则使用受限五段 Cron。固定间隔只允许 */10、*/30 和整点每小时三种。日历规则支持每天、每周指定日、每月指定日的一个时刻。系统拒绝秒、年份、月份限制，以及日期与星期同时限制。一次性任务使用带 UTC offset 的 at 时间，创建或重新启用时必须在未来。

时间模块使用 croniter、标准库 zoneinfo 与打包 tzdata。Eidos 只判断允许的规则形态和候选墙上时间。夏令时跳过不存在的本地时间。触发按 UTC 去重。预览、保存和调度使用同一时间计算。

Runtime 运行一个受 Supervisor 管理的 AnyIO 调度任务。它每五秒检查未来缓存，慢环境准备使用已有 managed task。系统不创建每计划常驻线程或额外事件循环。启动、唤醒、重配置、时钟回拨或长检查间隔都会重算未来时间，不执行旧时刻。

同计划未完成时不重叠。已有 Session 活动或排队时，本次触发跳过。任务在准备后发生版本变化时，旧准备不能入队。已接受 Run 保留自己的快照。一次性任务在 Run 入队的同一事务中停用，失败不重新触发。原执行或其重新生成有未解决的 Reconciliation 时，只暂停所属计划。重新生成的自动 Run 也计入同计划占用。

## 持久化与工具链

Schema v28 新增 scheduled_tasks、scheduled_occurrences 和 automation_run_origins。普通启动和历史状态库升级都接入 v27→v28 迁移，失败回滚 DDL 与版本。任务配置采用严格类型，更新使用 expectedRevision。触发唯一键使用计划 ID 与计划时刻，立即执行使用稳定 operationId。

Renderer 经过 preload typed IPC 和 Main 调用 schedule/execute。command.mode 处理模型工具相同的管理用例，contextSessionId 只提供创建上下文。schedule/history、schedule/runNow 和 schedule/refresh 分别读取历史、手动触发和唤醒重算。Python Schema 通过 scripts/generate-automation-contracts.mjs 生成 Desktop DTO 与验证 Schema。

同一生成脚本使用已锁定的 Ajv standalone 生成静态校验器。Main 和 Renderer 共用这组校验器，保留字段限制、Unicode 字符长度、未知字段拒绝和禁止类型转换的规则。Renderer 加载定时任务工具卡片时不再动态编译 Schema，页面 CSP 继续禁止 JavaScript `unsafe-eval`。生产页面启动定向验证加载实际构建 HTML、CSP、bundle 和 sandboxed preload，并检查 React 启动页与控制台错误。

ToolSpec 保留 eidos_state 最大声明与 single 批次策略。可信内置 AutomationToolRuntime 在严格校验后解析本次实际类别；view / preview 使用 none，不提交 Intent，其余使用原写入流程。分类进入 DispatchPlan，不修改冻结 Registry。preserve_omitted_arguments 只对该工具开启并进入契约指纹，其他工具保留原默认参数行为。

写模式先准备候选配置，再通过 Controller 保存 Durable Intent。计划、操作回执和领域 Event / Outbox 原子提交，最终 ToolResult 仍由 Controller 提交。重启先检查可信内置来源与回执，恢复已知 SQLite 结果，不重新写入。取消后的已保存计划仍回显真实 ID、版本和副作用。未知外部操作继续使用原 Reconciliation。

触发接纳在同一事务中复核版本、启用状态、占用、持久目标绑定和核验事实，并关联普通 Run 与自动来源。文件系统和 Git 准备在事务外完成。任务配置不成为另一套 Run 状态机。

## 来源与记忆

自动来源随触发入队原子保存，并供 Context、权限、UI 和记忆共用。自动输入不成为当下用户的新授权，不进入用户授权证据。SQLite 触发器将自动 Run 的已有和新建 Item 排除出自动学习。自动 Run 没有记忆写工具，读取仍遵守当前设置。同 Session 后续真实用户输入保持原行为。

周期自动 Run 只能暂停自身计划的原版本，不能创建计划、改正文、改时间或修改审批模式。版本冲突时不自动重试。子 Agent 不注册该工具。重新生成的逻辑请求与自动来源随新 Run 原子保存，创建防重沿用原逻辑请求。Fork 和 Checkpoint 不复制或撤销全局计划变更。

目标 Session 或 Project 删除时，系统在删除事务中暂停关联计划。独立任务不会因创建者 Session 删除而消失。缺失项目不会被解释为无项目。Worktree 继续使用现有 Snapshot、Retention 和 Restore，计划删除和结果已读均不清理产物。

## 验证与边界

本次实现覆盖 Runtime、数据库迁移、工具契约、typed IPC、管理页面与当前文档。分支为 codex/local-automations。实现新增 croniter 6.2.4 与 tzdata 2026.5，并更新 uv.lock。实现复用普通 Run、AnyIO Kernel、权限、Durable Intent、Outbox 和恢复机制，调度事实只保存在 SQLite。v28 不支持由旧 Runtime 直接读取，Desktop 与 Runtime 需要一起升级。

定向入口见 DEVELOPMENT.md。验证包括时间限制、夏令时、默认审批、目标归属、原子接纳、占用、版本竞争、取消、回执恢复、重新生成、自动来源、Schema 升级、真实 Runtime 循环、跨进程契约和页面交互。真实 Electron 隔离页面验证键盘与 1440、1024、768、320 像素宽度。

初版发布验证覆盖 `pnpm test` 的全部测试层：Runtime Full 2,522 项通过、2 项跳过；协议契约 4 项通过；Renderer 状态 173 项通过；Main 90 项通过；Renderer 行为 620 项通过。Runtime Full 和完整 Renderer 行为套件在修复旧 Schema 测试夹具与 CSS 导入清单后通过。Python Ruff、Deptry、Python 与 Node 依赖审计、Desktop 构建、真实 Electron 页面、macOS arm64 Runtime Bundle、Bundle smoke 和 Bundled Seatbelt smoke 均通过。Repository Scale 套件不属于 `pnpm test`，该次没有运行。

2026-10-16 的列表分组修改涉及七个文件，包含页面、列表组件、样式、测试与本文档。页面移除了循环切换的状态筛选，复用现有 React 与任务查询，不新增依赖，不修改协议、执行逻辑或数据库。页面定向测试通过 15 项，覆盖任务分类、暂停恢复、自动触发通知、搜索与分页。隔离的真实 Electron 页面验证了两个分组、已结束任务、搜索、键盘、新建默认值和四种窗口宽度。Renderer 构建与 git diff --check 通过，构建仍有大于 500 kB 的 Chunk 提示。该次未运行 Full Suite、Runtime/Main 测试、启动退出 smoke 或打包验证，也未读取真实任务数据。

2026-10-16 的会话名称与来源修改涉及 Runtime、Session 投影、共享契约、Main 校验、侧栏、详情导航、测试与本文档，共 22 个文件。实现复用现有自动来源类型和 SQLite 记录，无新依赖或 Schema 迁移。Desktop 与 Runtime 需要一起更新并重启，以加载新命名和来源投影。Runtime 定向测试通过 93 项，Renderer 行为测试通过 60 项，Main 跨进程与 Fixture 测试通过 3 项。真实 Electron 隔离页面验证了图标默认隐藏、悬停显示、紧凑尺寸、来源提示和实际点击后打开任务详情。Main 与 Renderer 构建、共享契约检查、Ruff、Deptry，以及本次相关文件的 diff 检查通过。工作区全局 diff 检查另有 ChannelSettings.behavior.test.tsx 尾部空行提示，该文件属于其他未提交修改。该次未运行 Full Suite、启动退出 smoke 或打包验证，也未读取真实任务数据。

本次没有做真实 Provider 的长时间无人值守验收。自动审批仍可能等待用户，计划不能保证所有工作都无人值守。原生打包和启动验证只证明测试中覆盖的边界，不等于所有项目和外部扩展都已验收。
