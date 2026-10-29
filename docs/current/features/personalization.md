# 个性化设置

用户可以在“设置 → 个性化”中管理三个分组。所有字段均可使用默认值或留空。

| 分组 | 字段 | 默认值 |
| --- | --- | --- |
| 语言与表达 | 回答语言、语气、回答长度、输出格式偏好 | 自动跟随提问、默认、自动、自动 |
| 关于你 | 称呼、角色、熟悉的领域 | 空 |
| 自定义指令 | Markdown 正文 | 空 |

回答语言支持简体中文、繁体中文、英语、日语、韩语及自定义语言。用户选择其他语言后必须填写名称。语气支持默认、直接、友好和正式。回答长度支持自动、简短、适中和详细。输出格式支持自动、先给结论、偏好段落和偏好列表。当前任务明确指定的语言和输出格式优先。

页面复用 `SettingSection`、`SettingRow`、`ConfirmDialog` 和 `MarkdownEditor`。页面采用实时自动保存，选项行高紧凑，编辑区细节无生硬感并支持隐藏多余标题与计数徽标，无需手动点击保存或还原。页面在读取期间禁用编辑，并在失败后提供重试。保存失败或版本冲突会保留草稿；用户重新读取前需要确认丢弃草稿。页面在切换分类或返回时保护未保存的修改。页面不提供生效指令查看入口。

下拉选择会立即保存。文字修改在停止输入 400ms 后保存，普通输入框失焦时也会保存。保存串行执行；用户在保存期间继续输入时，迟到结果只更新已保存基准，不覆盖新草稿，后续请求继续保存最新内容。失败后不会反复自动重放同一份内容，用户修改后可以重试。

Markdown 指令编辑器也在失焦时立即保存。离开确认打开期间，页面暂停尚未发出的自动保存；用户取消离开时再恢复。已发出的保存会先完成，页面不会把已经提交的内容误称为已丢弃。同一窗口会记录在途保存的内容和版本，页面卸载后也能在核对 Runtime 结果后接续保存较新的草稿。

Runtime 存储暂不可用时，当前编辑器保持挂载并禁用保存。Runtime 退出导致 AppShell 卸载，或用户通过全局搜索离开后，同一 Renderer 的内存仍保留未保存表单。用户重新打开个性化时，页面先读取 Runtime 的当前版本并保留草稿；远端版本不同会提示冲突，不会自动覆盖它。若离开期间的保存已经提交且内容相同，页面会采用已确认的最新版本。成功保存或用户明确丢弃时清除缓存。该缓存只保存表单草稿，不成为已保存配置来源，也不会写入浏览器存储；关闭窗口或重启整个应用后不会保留它。

## 保存与协议

SQLite 的 `personalization_settings` 单例记录是唯一配置来源。设置页中的全局指令正文保存在这条记录中；本期不创建或同步另一份磁盘 `AGENTS.md`。个性化不会改写项目里的规则文件或已有记忆。

`personalization/read` 返回当前 revision 和 preferences。`personalization/save` 接受完整偏好与 `expectedRevision`，使用条件版本检查。旧 revision 返回 `PERSONALIZATION_CONFLICT`。相同配置不增加 revision。事务同时提交设置、`personalization.settings_changed` 事件及 Outbox。事件只记录设置身份，不包含用户背景或指令正文。该事件不投影为聊天消息。

Python 的严格 Pydantic 模型生成 Desktop 类型和 JSON Schema。Main、RuntimeClient 和 Runtime 分别校验边界。JSON Schema 保留自定义语言的跨字段校验；TypeScript 生成只省略无法表达为类型的 if/then 条件。双方共同验证 `protocol/fixtures/personalization-v1.json`。

称呼最多 100 字符，角色最多 300 字符，熟悉的领域最多 2,000 字符，自定义语言最多 80 字符，自定义指令最多 16,384 字符。接口拒绝额外字段，个性化不能接收权限模式或其他执行配置。

保存前，Application 复用 Runtime 的敏感内容检查。已识别的凭据或需要脱敏的文本会返回 `SENSITIVE_CONTENT_REJECTED`，检查不可用时返回 `SENSITIVE_SCAN_FAILED`。系统不会静默改写指令，不会在拒绝时写入设置或事件。页面保留草稿，用户可以移除相关内容后重试。

## 任务中的使用

Runtime 在创建 Run 的同一事务中把配置固定到 `RunResolutionSnapshot.personalization`。排队任务也使用创建时的配置。后续保存只影响新建的任务；当前任务、后续模型步骤、恢复和传输重试继续使用原快照。子 Agent 继承父 Run 的快照。升级前创建的 Run 缺少此字段时保留原有默认行为。

`ContextBuilder` 从持久快照构建表达偏好、用户背景和自定义指令。各部分使用有来源的 user context，不进入 system/developer 权限层。当前用户要求、项目规则、已选 Skill 和任务所需的结构化输出优先。自定义指令可以覆盖普通表达预设。记忆不能覆盖用户明确设置的偏好。用户背景按资料处理，模型只在任务相关时使用它。

个性化不授予文件、Shell、网络或外部工具权限。Runtime 继续执行原有审批、Sandbox、身份、版本、取消和结果核验。最终化和兼容的 Step 创建路径也使用固定偏好。

渠道 Run 只冻结语言与表达设置，不包含本机用户的称呼、角色、熟悉领域或自由指令。Runtime 同时检查已复制的渠道来源和 Session 的渠道绑定，所以重新生成、编辑和继续渠道对话也保留此限制。渠道子 Agent 继续继承这份已经限制范围的快照。该限制不新增共享开关，也不改变已有记忆系统的渠道读取规则。

恢复投影保留三条个性化消息，并先从历史压缩预算扣除它们的字节数。若长指令使实际请求超出模型输入预算，ContextBuilder 会复用首尾文本预览，保留各配置层与指令末尾，并明确标记本次裁剪。该流程按实际预算最多调整八次；保存配置和 Run 快照中的完整原文不变。最终 ContextBudget 继续计算完整请求，系统不会通过隐藏配置体积来声称请求能够放入模型窗口。Finalizer 把偏好保留在 user 消息中，只在缺少已解析指令时读取 Run 快照；读取失败仍会结束终结尝试并取消 Timer。

## 数据升级与回退

v43→v44 在事务中新增个性化表。升级不修改聊天、记忆、项目和已有设置。迁移失败会回滚。常规初始化、旧数据目录迁移和离线备份恢复均接入此迁移。完整备份包含个性化设置，并继续接受 v40/v41/v42/v43 备份。

旧 Runtime 不能直接打开 v44 数据。开发者回退代码前应保留当前数据；需要回退数据时，应使用旧版恢复工具把升级前备份恢复到新目录。Git revert 不能降低数据库版本。

## 定向验证入口

- Runtime：`uv run --locked pytest runtime/tests/test_personalization.py -q`。
- Renderer：`pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/components/settings/PersonalizationSettings.behavior.test.tsx`。
- Main/真实 stdio：构建后运行 `node --test dist/main/runtime-client-personalization.test.js`。
- 隔离页面：`pnpm exec electron scripts/test-personalization-settings.cjs`。开发者可以用 `EIDOS_PERSONALIZATION_SCREENSHOT_DIR` 保存四种宽度、错误状态和编辑区截图。

定向脚本不证明真实 Provider 对语言和风格的遵循质量。开发者仍需按模型分别进行人工验证。

## 2026-10-28 验证记录

本次功能涉及设置页、typed IPC、Runtime 方法、SQLite 增量迁移、模型上下文、生成协议、测试与当前文档。实现使用现有 React、Pydantic、SQLite、AJV、敏感检查和 Markdown 编辑器，没有新增生产依赖或修改锁文件。执行者保留了工作区原有改动及用户在本轮调整的样式和自动保存交互。

本轮个性化及相关集成涉及 60 个文件，包含生成契约、验证脚本和迁移回归。部分共享文件还包含其他任务的并行改动，因此整个工作区的 Diff 行数不能视为本功能独立改动量。本次没有提交或推送。

测试智能体在最终共享源码上执行以下定向检查，退出码均为 0：

- `uv run --locked pytest runtime/tests/test_personalization.py -q`：41 项通过。测试覆盖保存、冲突、敏感内容、重启、迁移、备份、旧快照、子 Agent、渠道重新生成与编辑、小窗口模型的长指令及最终化故障。
- `pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/components/settings/PersonalizationSettings.behavior.test.tsx desktop/renderer/src/components/SettingsPage.behavior.test.tsx desktop/renderer/src/components/settings/SettingsNavigation.behavior.test.tsx`：33 项通过。测试覆盖自动保存、连续编辑、离开确认、跨卸载的在途保存、失败与重试、版本变化和草稿保护。
- `pnpm build`；构建后的 `node --test dist/main/runtime-client-personalization.test.js`：构建及 2 项真实 stdio 测试通过。
- `EIDOS_PERSONALIZATION_SCREENSHOT_DIR=/private/tmp/eidos-personalization-screenshots pnpm exec electron scripts/test-personalization-settings.cjs`：320、768、1024、1440px 控件、键盘操作、读写失败重试和控制台检查通过。执行者检查了宽屏与窄屏截图。

此前的定向检查还覆盖上下文恢复、协作、持久化、旧版升级与备份、两端契约和 Electron 启动/退出。三轮独立审查提出的问题已逐项修复，并添加对应回归。审查在第三轮后停止，最后修复由定向测试和执行者复核验证。

Python 依赖审计未发现已知漏洞。Node 生产依赖审计退出 0，仍报告 2 项 low 和 2 项 moderate。构建保留大 chunk 提示，Runtime 定向集保留两条 websockets 弃用警告。本次没有运行 Full Suite、真实 Provider 风格质量评测、日常数据人工验收或安装包验证。运行验证使用隔离数据；本次没有手动改写用户日常数据。

最后一次共享源码检查中，Deptry 与 `git diff --check` 通过。Ruff 未通过：并行的项目工作空间改动在 `db/repositories/sessions.py`、`db/schema.py`、`persistence/project_workspaces.py` 和 `protocol/schemas.py` 中留下 4 项未用变量、未用导入或导入顺序问题。执行者没有修改这些范围外问题，也没有把 Ruff 写成通过。
