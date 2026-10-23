# Eidos 当前限制

## 冗余代码清理验证（2026-10-23）

- 本轮只删除未接入生产调用链的代码、闲置组件及专用样式，并收窄 ExecutionFeed 的内部参数。协议、SQLite Schema、权限模式、实际重试和压缩验证保持原样。依赖声明和锁文件没有修改。
- Runtime 定向测试通过 161 项，覆盖模型契约与重试、文件发现与读取、类型化持久化、压缩验证、事件恢复、审批、调度和 Shell 输出。Renderer 行为测试分两组通过 170 项和 63 项，Feed 与回答操作的 Node 测试通过 55 项。测试已同步删除的组件与参数。
- Ruff、Deptry、Renderer 未使用代码检查、Renderer 测试类型检查、`pnpm build` 和 `git diff --check` 均通过。构建仍有部分 Chunk 超过 500 kB 的提示。`pnpm test:renderer-styles` 通过，覆盖 1100px 与 420px 的 8 种布局、控件状态和 13 处滚动区域；本次没有执行清理前后样式基线对照。
- 主 Python 依赖审计未通过：现有锁定版本的 certifi、opentelemetry-api 和 Pillow 合计报告 37 条漏洞。隔离工具 Python 依赖审计通过。Desktop 和 Runtime Node 的生产依赖审计均未通过，各报告 3 条漏洞（1 条低危、1 条高危、1 条严重），高危与严重项来自 Mermaid 的 DOMPurify 依赖。本轮没有混入依赖升级，也不能据此声明全部质量门禁通过。
- 本轮没有运行 Full Suite、真实模型请求、完整 Desktop 人工验收、Electron 生命周期 smoke 或 DMG 打包验证。


## 低风险操作审批（2026-10-22）

- MCP 免批只接受已审阅启用服务的明确 `readOnlyHint=true`。Runtime 信任该声明，但声明不能证明服务实际只读。缺失或非只读声明继续审批。该策略不扩大 Server 的文件和网络权限，也不改变 MCP 的并发与未知结果语义。
- 默认网页搜索不再逐次审批。查询会上传到用户选择的服务，请求也可能计费。搜索不会产生 Shell 网络 Grant。
- Shell 普通宿主文件读取的免批不扩大内置文件工具范围。受保护读取、额外写入和沙盒外执行继续按原规则处理。Skill 下载仅在已有网络权限时跳过网络审批，安装审批保留。旧版可安全恢复的待审批记录仍处理原决定；存在未完成 Intent 时不自动重放操作。
- 本次没有修改 UI、记忆、协议结构、SQLite Schema 或依赖。真实 MCP 服务声明的准确性和真实搜索账户费用不由模拟测试证明。
- Runtime 核心定向验证通过 196 项，既有 Skill 创建与安装回归通过 6 项，真实 Seatbelt 验证通过 15 项，Desktop 契约验证通过 4 项。Ruff、Deptry 和 git diff --check 通过。测试覆盖三种模式的免批、取消、旧审批重启恢复、严格工具契约校验、受保护路径和已有网络 Grant。系统只兼容 MCP 与内置搜索的审批标记变化，其他契约变化仍拒绝恢复。
- 当前 Python 生产依赖审计未通过：已锁定的 certifi 2026.7.22 和 Pillow 12.3.0 合计 25 项已知漏洞。独立 Runtime Python 依赖审计通过。开发者本次未升级依赖，未运行 Full Suite、真实外部服务验收、Desktop 构建或打包验证。

## 内置网页搜索（2026-10-21）

网页搜索第一版接入 DeepSeek Search 和 LangSearch。用户需要自行取得 Key，并在设置页手动勾选默认服务。保存只验证格式和本地文件，不验证远端账号、余额、额度或实时可用性。系统没有自动切换、通用网页抓取或独立 `web_fetch`。现有 Run 使用冻结配置，修改设置不会中途换服务。

本轮使用模拟服务、临时 SQLite 和真实 Main/Runtime 通道进行定向验证。隔离 Electron 页面覆盖 320、768、1024、1440px。真实账户 Key、正式应用人工验收、Full Suite 和打包验证尚未执行。Python 生产依赖审计发现已锁定的 certifi、Pillow 合计 9 项已知漏洞，审计没有通过。本次没有修改依赖和锁文件。实现细节见[当前网页搜索](current-web-search.md)。

## 聊天搜索与文件查找（2026-10-20）

本轮修改覆盖 Runtime 搜索服务、Schema v33 迁移、生成契约、typed IPC、侧栏入口和两个正文查找浮层。搜索相关改动约为 49 个文件、增加 2,400 行、删除 32 行，统计包含生成文件、测试与文档。系统采用 SQLite FTS5、标准字符串与正则 API，并把已锁定的 `markdown-it-py` 声明为直接生产依赖。系统删除了来源定位最多逐页累积 20,000 条记录的旧路径。SQLite 事实来源、只读文件权限、身份和版本核验继续保留。系统没有修改 Run 执行语义。工作区中的其他未提交改动保持原样。

- 全局搜索只覆盖普通会话标题、持久用户消息与助手消息，以及冻结的分叉继承历史。内部子 Agent Session、工具参数和输出、思考正文、图片内容、附件正文、长期记忆和未提交的流式草稿不进入该索引。系统使用忽略大小写的字面量匹配，不做语义或模糊搜索。
- 首次搜索会分批回填索引。后台刷新保留现有结果，并用 `aria-busy` 标记请求状态。索引变化会重置分页。短中文查询会扫描完整投影；超大历史库的响应时间还需要规模验收。
- 当前 Session 查找按页显示命中数量，数量后的 `+` 表示还有后续结果或回填。搜索定位读取命中附近的有界消息窗口，不会一次把全部历史传到 Renderer。
- 文件查找支持 UTF-8 文本、代码、Markdown 可见文字和 HTML 源码。系统最多读取 2 MiB，最多返回 2,000 处；界面会显示截断提示。正则 Worker 在 1 秒后终止并提示超时。文件版本变化时，系统拒绝把新内容与旧预览混用。Files 仍只向有项目的会话开放。
- 超长助手回答仍保留现有的 64 KiB 预览和完整回答分页。全文索引可以命中未展示的正文；自动高亮只作用于当前已展示的文字。
- 系统还没有文件名搜索、跨文件内容搜索、执行详情搜索、Diff、终端、网页或 PDF 查找。当前接口需要 Desktop 与 Runtime 一起升级。旧代码不识别 Schema v33；回退时需要恢复升级前备份。

定向验证包括 56 项 Runtime 测试、147 项 Renderer 行为测试、45 项 Feed 纯渲染测试和 1 项 Main 到真实 Runtime 的跨进程测试。共享 Fixture、Ruff、Deptry、Python 依赖审计、Desktop 构建、macOS Runtime Bundle 与 smoke 均通过。真实 Electron 检查了四种宽度、索引刷新、旧消息定位、文件 Worker、单行浮层、不挤动正文、标题栏下方的位置和聚焦无蓝框。Electron 启动退出 smoke 通过。本轮没有运行 Full Suite、DMG 安装包或超大聊天库性能验收。构建仍有大 Chunk 提示，以及 CSS 压缩器不识别 `::highlight` 的提示；Electron 已验证高亮实际可用。

## Session 展开与持续输出滚动（2026-10-18）

- Feed 已分开管理跟随意图和底部位置，并用原生 ResizeObserver 跟踪内容与视口。处理过程和嵌套工具仍保留原有折叠动画；Run 结束后仍自动折叠。用户向上浏览历史时暂停跟随，折叠引起的滚动范围收缩不会恢复跟随。到底按钮改为使用统一的下一帧滚动入口。
- 定向行为测试通过 55 项，ExecutionFeed 静态测试通过 45 项。隔离的真实 Electron 页面在 320、768、1024、1440 四种宽度下通过展开、持续输出、历史阅读、延迟图片和结束折叠检查，最终底部距离均为 0.5px。原生向上滚轮移动 180px 后，后续内容更新造成的视口偏移均为 0。既有 Composer 原生布局验证在 1100px、420px 下通过，单行输入、全选删除和等高编辑的历史滚动偏移仍为 0。
- Renderer 类型检查、构建和 git diff --check 通过。构建仍有 Chunk 超过 500 kB 的提示。本次没有运行 Full Suite、Runtime/Main 测试、Seatbelt、启动退出 smoke、打包或真实 Provider 长期输出验收。测试页面使用模拟数据，不代表正在运行的真实 Session 已加载新版本。该改动不新增依赖、协议字段或数据库迁移。

钉钉渠道首版只接入一个企业内部应用，支持文字输入和 Markdown 回复。真实单聊与有效群 @ 已验证，长时间重连仍待验收。渠道完全访问需要在 Desktop 二次确认并保存，授权覆盖此后收到的消息；本轮没有在真实机器人上开启该模式。普通模式的审批和所有模式的表单输入仍需本机操作。系统不保证补收离线消息，不自动重发结果未知的回复。官方 Stream SDK 锁定预发布版 0.24.4b1。渠道实现时发现的 pydantic-ai-slim 2.43.0 五条漏洞已由 2026-10-17 的依赖升级处理，当前审计结果见文末更新。完整说明见 [方案与实现记录](dingtalk-channel-design.md)。

## 本地定时任务（2026-10-15）

- Eidos 完全退出或电脑休眠时，任务不会执行。当前版本不检测漏跑，不补跑，也没有云端执行或额外守护进程。固定间隔只允许 10 分钟、30 分钟和 1 小时。当前实现见 [当前本地定时任务](current-automations.md)。
- 任务默认使用 auto_review，仍可能等待人工审批。用户在页面手动创建或编辑计划时可以选择 full_access；用户必须在危险弹窗中确认，授权适用于该计划今后的每次执行。模型工具不能开启完全访问，也不能改用已保存的完全访问授权来修改任务或重新启用任务。系统不复制旧 Run Grant。原执行及其重新生成存在未核验副作用时，所属计划会暂停。同计划仍有执行时，系统跳过新时刻；原 Session 忙碌时，系统也跳过本次执行。
- Schema v28 新增计划、触发和来源三张表。Desktop 与 Runtime 需要一起升级。旧版本不能读取 v28；回退时需要恢复升级前的数据库备份。删除计划不会删除会话、Workspace 或历史。
- Runtime 定向测试通过 106 项。Renderer 定向行为测试通过 53 项，模型默认值纯测试通过 6 项，Main 与真实 Runtime 的跨进程及共享 Fixture 测试通过 2 项。隔离的真实 Electron 页面验证了创建、默认审批、会话省略、键盘和四种窗口宽度。Desktop 构建、原生启动退出 smoke、macOS arm64 Runtime Bundle 与其 smoke、Ruff、Deptry 和两份 Python 依赖审计通过。Renderer 仍有大于 500 kB 的 Chunk 提示。本次没有运行 Full Suite、DMG 打包或真实 Provider 的长期无人值守验收。

## 历史图片预览缺失（2026-10-14）

- Desktop 会把预览文件不存在处理为“暂无预览”，不会恢复被删除的文件。Runtime 仍返回 `WORKSPACE_FILE_NOT_FOUND`，并保留原有 Workspace、权限和版本校验。Desktop 的 `prepareWorkspacePreview` 现在可能返回 `null`；Main 和 Renderer 需要一起更新。
- 本次 Renderer 定向测试通过 66 项，Main 预览边界测试通过 3 项，Runtime 工作区测试通过 10 项，共享契约测试通过 4 项。Main 和 Renderer 构建及 `git diff --check` 通过。Renderer 构建仍有 Chunk 超过 500 kB 的提示。本次没有执行 Full Suite、真实产品窗口人工验收、Electron smoke、Seatbelt 或打包验证，也没有修改数据库、依赖或锁文件。

## Session 标题提示词（2026-10-14）

- 标题请求现在把原始任务放在 `<task>` 中。系统和用户提示词都要求模型只命名，不回答或执行任务，并输出一行纯文本标题。提示词仍属于模型指令，不能保证模型永远遵循格式；当前标题清洗和非空结果验收没有变化。已有 Session 标题不会自动重新生成。
- 本次标题相关定向测试通过 16 项，覆盖独立指令、关闭工具、多行与 JSON 原文保留、标题事件、人工标题保留和任务生命周期。Responses Adapter 的离线请求检查确认同一模板和空工具列表。相关 Ruff、Deptry、两份 Python 依赖审计和 `git diff --check` 均通过。本次没有执行 Full Suite、真实 Provider 回放、Desktop 构建或原生验收，不能据此确认偶发偏离的概率已经下降。

## 回答级聊天分叉（2026-10-14）

- 分叉创建后立即作为正常 Session 出现在侧栏。名称沿用来源名称，名称末尾的分叉图标在悬停和键盘焦点时显示。首次对话不覆盖该名称。v26→v27 会修复旧的未命名分叉；来源已删除且名称未保存时，旧分叉使用“新会话”。
- 本次即时显示和命名修正通过 Runtime 定向测试 48 项、侧栏行为测试 24 项和 Main/Runtime 测试 1 项。真实 Electron 已验证无 Run 分叉的显示、图标悬停与移开行为。构建、契约检查、Ruff、Deptry、依赖审计和原生启动/退出 smoke 均通过。本次没有执行 Full Suite。
- 分叉的悬停气泡显示直接来源会话的当前名称，并支持点击跳转。来源不在当前 Session 列表中时，气泡显示“来源会话不可用”。该显示不保存新的标题快照，也不修改分叉历史。
- 本次图标和来源气泡修改通过 89 项 Renderer 定向行为测试、45 项 ExecutionFeed 静态测试和 Renderer 构建。测试覆盖普通侧栏与置顶分叉、嵌套来源、来源重命名、来源缺失、禁用导航和键盘焦点。真实 Electron 的隔离页面已验证新图标悬停、气泡停留和来源名称点击跳转；该结果不代表真实产品数据的人工验收。本次没有执行 Full Suite、Runtime/Main 套件、Seatbelt、启动/退出 smoke 或打包验证。

- 分叉入口只接受已结束 Run 的完整最终回答。Runtime 拒绝进行中、未完成的回答和子 Agent Session。源 Session 存在待确认副作用时，Runtime 拒绝创建分叉。
- 单次分叉最多保留 20,000 条历史记录。记录与独立引用资源的未压缩内容合计最多为 128 MiB。Runtime 超限时返回 `SESSION_FORK_HISTORY_LIMIT`，不会发布部分 Session。
- 分叉保留完整聊天前缀，但模型上下文继续受既有预算约束。模型可以读取预算内的近期历史，或使用新 Session 自己生成的压缩摘要。Runtime 不复制截止点之后的旧摘要。
- 共享目录分叉使用当前文件。独立 Worktree 保存分叉时的 HEAD、staged、unstaged 和 untracked 状态。该功能不恢复历史回答时的文件状态，也不复制 ignored 文件。Managed Session 只允许独立 Worktree 分叉。
- 历史记录不授权新 Run。Runtime 不复制 Approval、Run Grant、写入证据、Worker 或子 Agent。Desktop 不从继承 Run 恢复权限模式。用户的新输入仍走现有 Run 创建流程。
- Worktree 创建后、历史提交前的失败会保留 durable lifecycle 和快照。相同 `operationId` 的重试复用原目标身份与快照。Runtime 不发布半份历史。来源已删除或环境需要对账时，操作明确失败。
- 来源回答定位最多自动读取 20,000 条展示记录。Session 正文向上滑动时自动分页加载更早历史。
- 本次 Runtime 定向测试通过 75 项。Renderer 行为测试通过 75 项，ExecutionFeed 静态测试通过 42 项。Main/Runtime 分叉与共享 Fixture 测试通过 2 项，现有 Desktop 契约检查通过。测试覆盖长历史、响应分组、工具回放、来源删除、重启、Worktree 重试、迁移回滚、界面分页与迟到响应。
- 本次 `pnpm test` 全量通过：Runtime 2,404 项通过、2 项按配置排除；Desktop 合约 4 项、Renderer 状态 166 项、Main 84 项、Renderer 行为 533 项通过。首次运行中有 6 项 Schema 26 迁移夹具和固定版本断言失败。我更新了旧版数据库夹具并改为核对实际迁移版本；6 项定向复测、相关 Ruff 检查和第二次全量运行都通过。本次没有重新执行构建、Seatbelt 原生测试或打包测试，也没有做真实产品窗口的人工验收。

## 项目和 Session 置顶验证（2026-10-13）

- 置顶功能已经接通右键菜单、typed IPC、Runtime 方法和 SQLite。项目与 Session 共用固定置顶顺序；首版不提供拖拽排序或排序设置。Runtime 最多保存 1000 个引用，未物化草稿和内部子会话不可置顶。
- Runtime 定向测试覆盖置顶、幂等重试、标题与 Run 状态独立、重启、Outbox 重投、失败回滚、删除清理、内部子会话排除，以及 v24 → v25 和历史存储升级。置顶、Schema、Project 生命周期、Event/Operation、协议路由和 Run 终态测试通过 64 项；新增失败与子会话用例及受影响的 Worktree/Session 删除用例通过 20 项，33 项非删除用例未选择；存储布局测试通过 24 项。
- Renderer 定向行为验证通过 68 项。置顶、侧栏和请求竞争的 33 项在最终焦点处理后再次通过。Session 分组、置顶投影和共享 DTO 纯测试通过 26 项。Main → 真实 Runtime 的定向测试确认混合置顶、无项目 Session、幂等、删除清理和重启恢复。Desktop 契约检查通过。
- `pnpm test:sidebar-pinning` 通过。隔离的真实 Electron 页面覆盖项目右键、Session 的 Shift+F10、混合显示、唯一高亮、状态图标、移动后的键盘焦点、取消置顶、项目浏览滚动位置，以及 1100px 和 420px 的菜单边界。取消置顶到已折叠的项目时，焦点回到项目标题，项目保持折叠。页面沿用禁止 JavaScript eval 的 CSP，并使用测试 API。这个结果与 Main/Runtime 实联结果分别证明 UI 交互和持久化链路，不代表真实产品窗口的人工验收。
- Main、Preload 和 Renderer 构建通过。Electron smoke 正常初始化 Runtime 并退出。Python Ruff、Deptry 和两份 Python 依赖审计通过；本次没有新增依赖或修改锁文件。Renderer 构建仍报告部分 Chunk 超过 500 kB。
- 本次 `pnpm test` 全量通过：Runtime 2,382 项通过、2 项按配置排除；Desktop 合约 4 项、Renderer 纯状态 165 项、Main 83 项、Renderer 行为 521 项通过。`pnpm build`、`pnpm lint:python`、`pnpm deps:python` 和 `ruff check scripts/evaluate-memory-learning.py` 通过。Electron smoke 和 `pnpm test:sidebar-pinning` 在本机环境下通过；受限环境中的 Electron 进程会在启动时收到 `SIGABRT`。Renderer 构建仍报告部分 Chunk 超过 500 kB。本轮没有执行 Seatbelt 原生测试或打包测试，也没有新增依赖或修改锁文件。Desktop 与 Runtime 需要一同更新。旧 Runtime 不识别 Schema v25；回退旧代码时需要恢复升级前的数据库备份。

## 项目选择器搜索框焦点样式修复（2026-10-13）

- 项目选择器（`ProjectPicker`）搜索输入框在获取焦点时清除了公共控件样式带有的 `box-shadow`、`outline`、圆角和多余内边距，点击搜索项目时仅保留文字输入光标，不再出现外层高亮框。
- 定向行为测试 `pnpm test:renderer-behavior ProjectPicker`（7 项）全部通过，包含新增的焦点边框与阴影移除断言。Renderer 构建通过。

## 项目列表自动定位修复（2026-10-13）

- Renderer 已把会话定位的触发条件与项目展开状态分开。用户展开、收起项目或刷新列表时，侧边栏不会重新滚到当前高亮会话。会话切换与输入引用查看其他会话仍使用原有定位链路。
- SessionSidebar 与 InputContext 定向测试共 27 项通过。新增回归用例在修复前复现了项目展开状态变化导致的两次额外滚动，修复后覆盖其他项目与当前项目的展开、收起和列表刷新。
- 隔离 Electron 页面使用真实 Renderer 组件与样式验证了 22 个项目的列表。展开其他项目的滚动偏移为 0；导航会展开折叠的目标项目，并在动画结束后让目标会话进入可见范围。导航后再次浏览其他项目也没有被拉回。
- Renderer 类型检查与构建通过，构建仍有 Chunk 体积提示。Node 生产依赖审计报告 1 项低危漏洞，按 high 门槛退出码为 0。`git diff --check` 通过。本轮没有执行 Full Suite 或真实产品窗口的人工验收。测试不读取真实 Session 数据；本次没有修改协议、SQLite、依赖或锁文件。

## 滚动条透明与无边框修复（2026-10-13）

- 公共滑块颜色变量已从错误的自引用恢复为有效 RGBA 颜色。Renderer 已统一使用自定义滚动条外观，并清除模块内会覆盖该外观的标准滚动条属性。标签栏隐藏滚动条与代码、Diff 的悬停滑块行为保留。
- Renderer 定向测试通过 65 项。`pnpm test:renderer-styles` 通过，原生截图像素检查覆盖 13 处滚动区域的横向轨道、纵向轨道和角落；这些部位没有独立底色。计算样式检查确认滚动条无边框和阴影，滑块颜色有效，尺寸保持为 6px。
- `pnpm test:composer-layout` 通过，单行输入、全选删除和等高编辑的历史滚动偏移均为 0。Renderer 类型检查与构建通过，构建仍有现有 Chunk 体积提示。
- 本轮没有执行 Full Suite 或真实产品窗口的人工验收。测试使用隔离 Electron 页面，不读取真实 Session 数据。

## Renderer 样式统一与拆分验证（2026-10-13）

- `styles.css` 已改为 31 行的统一导入入口。公共基础与功能模块分别维护样式，现有聊天专用排版保留。审批与计划模块已移除对公共 Button 颜色和交互状态的单独覆盖。
- 本轮 17 个 Renderer 定向测试文件共 240 项通过。最后一次公共样式整理后，相关 6 个文件共 63 项复测通过。新增样式模块检查确认全部本地 CSS 只通过入口导入一次。
- `pnpm test:renderer-styles` 通过。真实 Electron 检查公共、审批、计划和澄清按钮的状态，检查 10 类选中控件与 3 类菜单。测试覆盖 1100px 和 420px 下的 8 种侧栏、Dock 组合，并与改动前的构建 CSS 对照正文、代码、文件预览和布局的最终计算样式。开发页面与本轮构建生成的 CSS 均通过了这组检查。
- `pnpm test:composer-layout` 通过。单行输入、全选删除、等高编辑和原生键盘编辑的历史滚动偏移均为 0。窄窗口换行和 56px 至 168px 的自动高度范围保留。
- Renderer 类型检查与构建通过，构建仍报告部分 Chunk 超过 500 kB。Node 生产依赖审计报告 1 项低危漏洞，按 high 门槛退出码为 0。本次没有新增依赖。
- 本轮没有执行 Full Suite 或真实产品窗口的人工验收。隔离 Electron 页面验证了源码样式，不证明当前已安装 App 已加载这些修改。

## 聊天正文排版验证（2026-10-13）

- 本轮 Renderer 定向行为测试通过 60 项，Markdown 和 Execution Feed 纯测试通过 57 项。Renderer 类型检查与构建通过；构建仍报告部分 Chunk 超过 500 kB 的体积警告。
- 隔离的原生 Electron 排版检查覆盖 900px 与 420px 宽度。正文保持 14px，代码块保持 12.5px，标题与行内代码字号、背景、文字颜色、代码工具栏和非聊天 Markdown 样式均与修改前一致。现有 Composer 原生布局测试覆盖 1100px 与 420px，单行编辑、全选删除和等高编辑的历史滚动偏移均为 0。
- 本轮没有执行 Full Suite 或真实产品窗口的人工验收。隔离页面的结果不能证明当前已安装 App 已经使用这些源码，也不能证明排版与其他产品逐像素一致。

## 子 Agent 全量记录显示的验证状态（2026-10-12）

- 生产代码已移除子 Agent 详情中的“加载更早记录”按钮，并自动读取全部历史页。Runtime 会补带历史页记录对应的更早 Run，Renderer 会保留历史并按顺序刷新。
- `pnpm test` 通过：Runtime Full 2,298 项通过、2 项按配置跳过；Desktop 合约 4 项、Renderer 状态 153 项、Main 80 项、Renderer 行为 473 项通过。全量测试完成后新增的 AgentPanel 定向用例通过 18 项。Runtime Session Run 定向测试通过 16 项。
- `pnpm test:composer-layout` 通过。隔离 Electron 窗口覆盖 1100px 和 420px 宽度，历史记录滚动偏移为 0。多行文本会随窄窗口变高。
- `pnpm check:python` 和 `pnpm build` 通过。Python Ruff、Deptry 和 Python 依赖审计没有发现问题。Node 运行时依赖审计报告 1 项低危漏洞；审计门槛为 high，命令退出码为 0。构建报告 Renderer 主 Chunk 超过 500 kB 的大小警告。
- Runtime 定向测试验证了第 101 个 Run 的分页 Item 会带上对应 Run。Renderer 定向测试覆盖两页记录顺序、页内 Run 信息、当前 Session 通知过滤、通知合并、读取期间的后续刷新、分页失败提示和取消订阅。
- 真实产品窗口中的完整长历史显示和性能尚未人工验收。Renderer 测试使用两页记录；它没有测量大量历史记录的真实窗口性能。

## 文件引用规则与验证状态（2026-10-12）

- 本轮补齐了 Renderer 和 Runtime 自动化测试。测试覆盖显式绝对链接、路径边界、URL 编码、中文与空格、行列号校验、行片段格式、文档相对链接、裸文件名、同文件重复定位、Shiki 异步高亮、Markdown 块定位、截断预览、项目提示词条件、Memory 提示词重建和 Finalizer 回退。
- 定向 Runtime 测试通过 15 项；Renderer 纯测试通过 153 项；Renderer 行为测试通过 448 项；Main 快速测试通过 50 项；Desktop 合约测试通过 4 项。`pnpm build`、Ruff、Deptry 和 Python 依赖审计通过。Node 生产依赖审计发现 1 个低危项，因门槛为 high，命令退出码为 0。
- `pnpm test:runtime:full` 通过 2,256 项，排除 2 项，耗时 746.78 秒。`pnpm test:fast` 通过：Runtime 1,245 项、合约 4 项、Renderer 纯测试 153 项、Main 50 项、Renderer 行为 448 项。`pnpm check:python`、`pnpm build` 和 `git diff --check` 通过。Node 生产依赖审计发现 1 个低危项；命令的严重级别门槛为 high，因此检查退出码为 0。
- 本轮没有执行真实 Desktop 人工验收。自动化结果不能确认真实窗口中的焦点、布局和滚动效果。
- Conversation 中的历史裸文件名、行内代码和相对文件链接会显示为文字。系统不改写历史消息，也不通过同名搜索补造目标。显式绝对路径必须属于当前 Session 的 execution root。旧 Worktree 路径不会自动替换为新 Worktree 下的同名文件。
- 文件链接读取当前文件内容，不提供历史字节快照。文件删除、路径移动、版本变化或预览不支持时，系统继续使用已有读取失败与预览限制提示。绝对路径解决寻址歧义，不保证文件长期存在。
- 普通文本、代码和 HTML 源码支持行定位与选区。列号按浏览器文本的 UTF-16 偏移解释，超出行尾时选区落在行尾。渲染后的 Markdown 只能定位源文本块，不能精确对应源文本的某个字符列；空白源行会定位到最近块。图片、PDF 和其他非文本预览不应用源码行列位置。超出文件或截断预览的行会显示提示。
- 自动化测试覆盖了项目 Session 的提示词条件、Memory 提示词重建、Finalizer 回退、子目录和带空格或中文的路径、行列位置、同文件重复定位、异步高亮、Markdown 块定位、截断提示、文档相对链接和现有文件打开入口。本轮尚未执行真实 Desktop 人工验收。

旧版完整 ContextSnapshot 仍可读取，但新共享块格式只用于新快照。当前版本不会自动重写历史 Blob，所以升级后不会立刻释放既有快照占用的空间。

- 内部 `LoopLifecycle` 仍是非权威诊断观察点。用户 Hook 已通过独立服务接入五个常用事件，并提供内容版本信任与持久化。完整 Codex 插件兼容、其他生命周期事件、async/MCP/prompt/agent 处理器和 transcript 导出尚未实现。详细边界见 [current-hooks.md](current-hooks.md)。

> 权限模式范围：manual 与 auto_review 默认保留路径和 Seatbelt 保护。原路径硬拒绝及显式沙盒外执行可经高危人工审批开放；auto_review 不能代替用户批准这类请求。full_access 直接执行，不创建逐项授权、Approval 或等待状态。所有模式仍保留参数、身份、版本、取消、Durable Intent、结果校验和未知结果记录。审批不能赋予 macOS 管理员权限，也不能解决 ACL、TCC 和只读卷限制。

- `session/read` 不提供 Step Resolution Review 内容，兼容字段 `stepResolutions` 固定为空数组。Desktop 当前不展示这些信息，Runtime 也未新增按需详情 RPC。完整执行快照仍持久化并由执行读取入口校验；打开 Session 不承担这些 Blob 的完整性检查。本项代码修订尚未验证，不能据此宣称 UI 打开耗时已经达标。

本文只记录 Stage 4+ 后续能力、其他平台支持，以及没有 Apple credentials 时无法验证的真实签名结果。本文不记录已经解决的问题，也不记录历史阶段。

## 启动维护

- 启动已取消 Blob 全量清理，历史快照检查已改为分批读取。未引用 Blob 会保留到启动宽限期后的空闲清理；清理检查发现的目录或 Context 清单错误也会延后报告，执行读取仍保留原有 fail-closed 校验。Bundle 完整文件 Hash 只在构建与发布执行。仓库索引库的全库检查和有界空闲页回收在空闲维护阶段执行；主库、其他辅助库、Worktree 与 Seatbelt 检查仍在启动等待链内。存储样本对照不代表整个 Desktop 的冷启动耗时。
- 隔离样本包含 2,001 条 ContextSnapshot 和 200 个未引用 Blob。三次存储初始化的中位数从 203.64 ms 降至 13.29 ms；本测量不包含模块导入、Bundle 校验或 Desktop 首屏。持久化、Memory 和计时定向测试通过 62 项，Context 与回放通过 22 项，协议初始化与关闭通过 5 项。本机 Electron smoke 通过。两个原有审批测试曾在受限环境中失败，并在未修改的 HEAD 源码中复现；这两项随后在本机原生环境通过。本轮没有执行 Full Suite 或真实 Desktop 冷启动计时。

- 日常 Runtime 不再检测依赖文件内容与发布清单之间的 Hash 差异。清单与绑定 Hash 标识构建声明及路径，不能当作当前磁盘文件的内容证明。仓库缓存损坏检查可能延后；SQLite 损坏或必要表缺失会停用缓存并重建。未知版本、权限异常和 Busy 不触发破坏性重建。隔离文件保留在数据目录内，本轮不自动删除这些诊断文件。旧缓存不会自动执行全量 VACUUM 转换；它仍可复用空闲页。

- 本轮已删除 Python Runtime 的全量文件 Hash、inventory 递归覆盖扫描、逐包线性路径查找和严格模式分支。构建脚本的完整校验仍通过打包测试。最终定向验证通过：Runtime 137 项、原生依赖绑定 23 项、协议启动/关闭 5 项、原生审批生命周期 2 项、Node 打包与 loader 8 项；Electron smoke 通过。当前本地 Bundle 的三次 Catalog 加载中位数为 270.60 ms，绑定复查中位数为 316.38 ms。当前 735.11 MiB 仓库库的独立副本初始化从约 2062 ms 降到约 0.55 ms。样本缓存、系统负载和总启动范围不同，这些数字不能直接推导完整 Desktop 冷启动时间。

## 平台与分发

- Eidos 当前只支持 macOS arm64 Desktop、Runtime Bundle、App 和 DMG。
- Linux、Windows、macOS x64、Universal binary 和其他平台的 bundled Runtime 与 Ripgrep artifact 尚未实现。
- Auto Update、GitHub Release 发布闭环和其他平台 artifact 尚未实现。
- Release packaging 的签名、notarization、stapling 和 Gatekeeper 命令已经接入脚本，但这些步骤需要 Apple credentials。没有 credentials 时不能证明已生成可发布的 Release artifact。

## Stage 4+

### Model Provider

- ModelConfigStore 以 `models.json` 为事实来源。内置 Catalog 覆盖 DeepSeek、MiniMax、Kimi 和火山引擎 Coding Plan，共十个推荐 Model ID。设置页支持添加、编辑和删除自定义 OpenAI 兼容模型。连接测试只获取自定义接口的模型列表，不能证明生成、工具、图像或思考档位可用。部分接口只返回模型 ID，用户需要手动补全元数据。自定义连接目前按模型配置保存，尚未增加独立的提供商批量管理或共享凭据实体。
- 当前内置 Model Catalog 没有启用 Responses API 或 native Custom Tool capability。
- 当前内置模型的 wire API 固定为 OpenAI-compatible Chat Completions/SSE。Runtime 已有按 ModelProfile capability 路由的 Responses native adapter，但没有未经验证地为内置模型打开该路径。
- Schema v22 以前已经丢弃的思考正文无法从 token 数恢复。思考模式下，这些历史模型响应会作为标注来源的历史资料回传；新响应使用原始思考记录。各 Provider 的生产端点仍需分别联调，mock wire 验证不代表真实端点已通过。轨迹界面尚未实现。
- 思考强度选项和默认值按模型配置。火山引擎 `deepseek-v4.1-flash` 已在 Runtime 映射关闭、低、中、高、最高五档到顶层 `reasoning_effort`，默认高。Coding Plan `/api/coding/v3` 对该模型是否接受这些值尚未通过受控请求验证。MiniMax M3 直连 API 只确认支持思考开关，没有已确认的离散 effort 档位；Kimi K2.7 Code HighSpeed 固定开启思考，没有已确认的 effort 档位。
- Chat Completions 的多工具图片结果编码已调整为同批次共享一个 `ModelRequest`，并增加调用 ID、工具名和回复完整性检查。定向测试覆盖图片结果位置、多图批次、MockTransport 请求顺序、完整性错误、思考回放和日志脱敏。MockTransport 不代表真实 Provider endpoint 已通过。
- 旧图片工具结果只保留路径等元数据，缺失的历史图片正文无法补回。当前回放会明确说明缺失，不重读当前文件。新工具内容快照保存已取得的文字或二进制内容，不保存全部生成文件。单个工具内容原始字节总量最多 10 MiB；工具内容与用户图片引用共用 16 MiB 编码内容预算，超出时优先保留最近的工具内容，并保留省略说明。音频等二进制内容可以使用同一内部保存边界，但当前 MCP 内容支持和模型音频能力没有扩展。不支持的媒体只提供说明。
- Provider 请求拒绝诊断只记录固定原因和有界 request ID。未知错误继续显示通用拒绝原因；日志不保留原始错误正文。原始失败 Run 的缺失正文不会因本次代码修改而补齐。
- Chat Completions 没有原生的 Assistant `phase` 字段。Adapter 只根据 ToolCall 做 `commentary` 分类，并保留 Provider 的 `finish_reason`。`MessagePhase` 可以是 `commentary`、`final_answer`、`unknown` 或 `None`，但它不控制 Agent Loop。Agent Loop 使用 normalized response 的 `needs_follow_up` 决定继续采样还是完成当前 Turn。
- 回答流式输出的代码修订尚未编写或执行测试。敏感扫描仍按完整行释放文本，无换行的长段落会等到响应结束。尚未闭合的尖括号文本会等待闭合或完整响应校验，以避免跨片段的 Provider 控制标记进入 Feed。当前实现不是逐 token 输出。
- Assistant 文本在完整响应校验前可以作为 `in_progress` Item 显示。Runtime 只有在校验成功后才确认它；失败草稿不会进入后续模型上下文。Runtime 不会额外调用模型来提前判断最终回答。如果响应随后包含 ToolCall，现有 Feed 会把前面的文本归入过程区。
- Runtime 已移除响应结束后的额外模型完成审查。旧 profile 的 `completion_check_version=1` 不会重新启用它。Chat Completions 缺少原生结束信号时，Runtime 使用正常采样与 Tool follow-up 语义；Runtime 不用另一模型判断任务目标是否完成。
- 已发布文本会参与 transport retry 的安全判断。normalization 的 `protocol_error` 和 `length` 仍走现有有界 protocol repair；失败草稿先结束，新的 Attempt 使用新 Item。`content_filter`、cancel 和 authentication failure 不进入该 repair。旧 Event 缺少 offset 时不能获得新的偏移去重保证。增量缺口会等待现有 Run 结束快照刷新来修复。
- 本次未修改 SQLite 表结构，但新 delta Event 会保存 offset。旧 Runtime 的严格 Event 校验不接受这个字段，所以产生新 Event 后不能直接把同一数据目录交给旧 Runtime；回退需要恢复升级前备份。协议 Fixture 和回归测试按用户要求留到确认后的测试阶段。
- Context Usage 的 estimated 值是有界 fallback，不是 tokenizer 精确值。它不能单独证明 Provider 已拒绝请求。

### Run 并发与资源模型

- 执行门控整改的 Linux 定向验证与当时的完整回归结果记录在 PR #103。PR #103 所用快照中的 Runtime Full 和 Desktop Main 测试失败可在原始 main 上复现。2026-10-14 的 `pnpm test` 通过：Runtime 2,475 项、Desktop 合约 4 项、Renderer state 171 项、Main 88 项和 Renderer behavior 599 项；2 项 `large_repository` 按配置排除。这个历史结果不代表后续改动已通过完整回归。正常完成检查覆盖运行时控制事实，不自动判定测试覆盖率、交付物质量或用户目标是否全部满足。模型辅助压缩与首批同步用户 Hooks 现已接入；PTY、剩余 Hook 事件和流式工具调度仍属后续工作。

- 普通 Run 没有并发上限。Runtime 按 Session 分别维护持久 FIFO，同一 Session 同时只运行一个 Run，因此一个 Session 可以排队多个 Run；不同 Session 可以并行，且不区分 Workspace、Local checkout 或 Managed Worktree。每个 Run 有独立的 `ToolConcurrencyGate` 和 `ShellProcessManager`，所以不同 Session 可以在同一个 Workspace 并行执行 Shell 和其他普通副作用。同一 Run 的长 Shell 不再占有执行门控，其他文件变更、命令和协作调用可以继续；`write_stdin` 继续管理原 Shell。等待 Approval 不会占用其他 Run 的执行资源。
- 当前每个活动 Run 使用一个 Worker Thread。模型异步 I/O、MCP、Managed Task 和安全只读批次由唯一 RuntimeAsyncKernel 管理。当前没有把整个 RuntimeEngine/RunSupervisor 改成原生 async 的实现。
- Managed Task 的提交与事件循环清理之间存在过同步锁循环等待。当前代码已将完整任务生命周期移到 worker，测试按用户要求延后。事故没有保留线程栈，因此该代码缺陷与事故之间尚未完成动态验证。该修订不解决所有慢 Git、stdout/stderr 背压或遥测关闭问题，也不改变既有 RPC deadline。
- Workspace 写入、Shell、MCP、external 和 Eidos-state 的实际副作用窗口只在各自 Run 内独占。不同 Session 的 Run 可以并行，即使共享同一个 Workspace。安全只读的 `parallel_safe` 批次保留自身的有界并行。同一个 Workspace 的并发修改可能让 Workspace observation 或 diff 包含其他 Session 的变化。
- 当前没有用户可配置的统一 Run 成本、模型步数或有效时长上限。现有 step、segment 和 effective time 字段主要用于 telemetry 和 operational lifecycle。

### Workspace 与工具

- 内置只读文件工具可以处理当前 Workspace 和 active Skill root 内受支持的普通 UTF-8 文件。写入工具支持普通 Workspace 写入和经审批的外部普通文件写入；普通 Skill 需要明确写入授权，`skills/.system` 始终禁写。已有文件使用受控原地写入，新文件使用排他提交；工具不处理 hardlink、symlink、特殊文件、特殊 mode 或文件 flags。
- 普通 Workspace 读取、列目录和文本搜索不按敏感文件名或可识别敏感内容拒绝。`.git`、`.agents` 和 `.eidos` 默认发现时隐藏，但显式只读路径仍可列举、搜索和读取；这三个目录默认受到写入保护。获批的 `run_shell.gitWriteAccess` 可以写当前 repository 的 Git metadata；普通文件工具或其他受保护路径变更需要高危人工确认。Workspace 外的 Eidos data 和配置的保密路径默认拒绝，用户可以批准明确范围。
- `apply_patch` 统一使用 Codex Patch：支持 Custom 和 Grammar 的相应 profile 直接提交原文；Function profile 提交 `{ "patch": "..." }`。旧的 `{ "changes": [...] }` 不再接受。两条路径的 Patch 原文预算统一为 8 MiB，Function 外层 JSON 另留转义空间。`apply_patch` 不再按内容模式或敏感文件名拒绝 Patch。单文件读取、准备和提交校验上限统一为 16 MiB；一次 Patch 的准备内容预算为 64 MiB，Diff 和结果各有 64 MiB 上限。预算约束 Runtime 资源，工具说明不规定文件拆分、补丁大小偏好或分批时机。JSON 类型、Patch 语法、当前文件匹配和最终写入验证是不同阶段；单个阶段通过不代表编辑成功。离线回归验证契约和安全行为，不能代表真实 Provider/模型的成功率；模型成功率需要另行使用相同任务集测量。
- Custom Tool 的 input delta 在 Responses adapter 内部重组。Desktop 在完整参数通过解析并完成 Prepare 后显示持久补丁更新；它不展示尚未校验的逐 token 补丁。Prepare 完成不代表文件提交成功。
- Add 内容会统一规范化为 LF，并按 Codex 行语义补尾部 LF。Add File 可以没有内容行；显式的 `+` 表示一条空内容行。因此空字符串、单个换行和两个换行会保持不同的解析结果。Parser 接受 CRLF 和外层空白，但不会猜测缺失的 envelope、marker 或行前缀。
- 本地 grammar 将上游 `add_line+` 改为 `add_line*`，以对齐 Codex Rust streaming parser 的空 Add 行为。Lark 对上游零宽文本正则的写法也使用了兼容 token。其他 Workspace 边界和文件安全限制不变。
- `apply_patch` 支持 Add、Update、Delete、Move 和多文件 Patch，但不提供通用二进制编辑、浏览器自动化或 Artifact 发布工具。
- Desktop Terminal 是用户直接操作的临时 PTY。它不属于 Agent Tool，不经过 Runtime Approval 或 Seatbelt，也不会写入 SQLite、Checkpoint、Conversation 或恢复状态。关闭 Terminal Tab、切换 Session execution binding、删除 Session、关闭窗口或退出应用都会终止对应 PTY。Review 和 Files 各只打开一个工具 Tab；Terminal 可以打开多个 Tab，Files 可以同时预览多个文件。
- Projectless Conversation 不创建 Project，也不提供 Git status、Git diff 或 Repository Intelligence。它使用系统私有锚点作为 workspace，并提供文件工具、Shell、Skill、MCP、Plugin、Files 和网页预览。它只支持 Local execution。
- Workspace Explorer 支持有界 UTF-8 text/code、Markdown、常见图片、PDF 和 HTML。其他二进制、Office、archive 和 database 文件不做内嵌预览。Explorer 支持已打开文本文件内查找，但不提供文件名或跨文件搜索、编辑、文件拖放、重命名或删除。当前的拖动交互只用于调整文件树与预览区的宽高。
- Non-Git Project 已经支持 Local Execution Session。Non-Git Project 不支持 Git status、Git diff、Managed Worktree 或 Git-based Fork。Local Checkpoint Fork 共享真实 workspace，不提供 directory snapshot、copy-on-write 或 filesystem rewind。
- Git Project 可以创建 Local 或 Worktree Execution Session，也可以在同一个 Session 中执行 Local ↔ Managed Worktree Handoff。Local Session 支持切换 local branch，也支持基于当前 branch 创建并切换到新 branch。该操作要求 workspace clean 且没有任何共享该 workspace 的 active Run。Managed Worktree 默认是 detached HEAD。Runtime 已提供 structured status、compare ref、精确文本行统计、file-scoped diff、stage、unstage、discard、commit、fetch、fast-forward-only pull、push、merge、rebase 和对应 continue/abort typed API。Desktop Review 已提供文件手风琴、展开或折叠全部、Stage、Unstage、tracked/untracked Discard、Open in Editor、inline Review Comment，以及 Commit、Fetch、Pull、Push、Merge、Rebase 和 Local branch 控制。二进制文件没有可用的文本行统计，`statsIncomplete` 会明确标记。Stash 尚未实现。
- Desktop Remote Git 不提供 credential 配置或 PAT 输入。它只复用系统 Git credential helper 和 SSH Agent。Advanced Git target 当前只列出已观察到的 local branch。它不接受任意 revision 文本，也不提供 remote ref browser。
- Inline Review Comment 当前只支持单条行级 Comment、删除、精确 anchor 失效和 active Comment 批量发送。它不支持 thread、reply、mention、reaction、云同步或模糊 re-anchor。stale Comment 只保留为历史提示，不会自动进入 Agent feedback。
- Git diff 对已记录的 submodule 只观察 Gitlink HEAD 和 submodule workspace 缺失。submodule 内部的 nested working-tree dirtiness 尚未向父 repository diff 暴露。
- Workspace discovery 只读取 Workspace root 的 `.gitignore` 和 `.eidosignore`。当前不支持 nested `.gitignore`。
- Ignore 规则只影响普通 `list_files`/`search_text` 发现结果。`.git`、`.agents` 和 `.eidos` 等 hard discovery 目录默认隐藏，但显式只读路径仍可读取。Ignore 规则不是权限。
- `search_text` 没有 LSP、AST 查询和基于 Repo Intelligence 的默认搜索路径。它仍然使用受管 Ripgrep，结果、preview、单文件和查询大小都有界。首次等待和后续等待不会结束搜索进程，但搜索不能跨 Run 或 Runtime 重启恢复。每个 Run 最多同时运行 4 个搜索，最多保留 16 个未领取的搜索会话；单个搜索进程最多运行 600 秒。
- 已声明 Tool 的参数错误会返回 `invalid_arguments` Tool Result。Runtime 只保留有界字段路径、稳定原因码和有限数值约束，例如 `field=maxResults, reason=less_than_equal, maximum=100, actual=101`，不返回原始参数值。敏感或超大的结果在 projection 重建为错误时仍保留显式的 `reconciliationRequired=false`，不会把它改成 unknown。
- 延迟工具仍须先通过 `tool_search` 激活，再在后续 Step 使用。当前目录中的未激活工具会返回可恢复的 `tool_not_activated`，Runtime 不自动激活或重放该调用。同一批次内的搜索结果不会修改该 Step 已固定的可用工具集合。
- Tool 的准备异常与授权成功分别记录。只有实际执行已开始且没有可信结果时，系统才记录未知副作用；该记录不建立执行屏障。未知事实不阻塞后续执行。可信结果之后的格式校验、投影、输出过滤和迟到取消不单独改变执行确定性。完全访问 Shell 的绝对 cwd 在准备和启动两层遵循同一模式，目录身份仍需核验。
- 无改动文件操作不会创建新的副作用屏障，但它不能清除其他操作的未知结果。Runtime 不修改历史 Run 的错误标记，也不根据工具返回的 `no_changes` 字符串推断已经安全跳过执行。
- MCP 的有效响应表示调用已经返回，不证明外部业务目标已经达成。Runtime 在收到响应后做有界内容和固定输出 Schema 校验；校验失败仍报告工具错误，并保留可能已经发生的副作用。SDK 调用已进入但没有有效响应时，Runtime 仍按未知结果处理。当前提交标记是 SDK 调用边界，不是远端业务执行的确认。
- 2026-10-09 测试记录：`pnpm test` 在默认外层沙盒下运行 Runtime Full。Runtime Full 结果为 90 failed、2112 passed、31 skipped、2 deselected。
- 一个代表性失败的错误码是 `sandbox_unavailable`。Runtime Full 没有在提权环境重跑。受限环境中的 90 个失败没有全部逐项复测，因此不能全部归因于环境限制。全量结果仍未通过。
- `pnpm test:desktop` 的构建、契约和 Renderer 测试通过。Renderer 行为测试通过 416 项。Main 测试通过 77 项，失败 2 项，跳过 1 项。系统批准的执行权限下，两个失败的 Main 用例复测通过 2 项。
- 系统批准的执行权限下，Native Seatbelt 测试通过 15 项。Patch、Collaboration 和 Workspace Manifest 测试文件共通过 102 项。pytest 失败缓存筛选复测通过 52 项，另有 563 项被筛选器排除。`pnpm check:python` 通过。
- Shell post-execution observation 在超时或不完整 Workspace manifest 时可能是 `unknown`。这类 observation 不能替代 Runtime 明确报告的执行 uncertainty，也不会单独限制已明确退出的 Shell。对于仍由 Runtime 管理、结果带有有效 `sessionId` 且明确报告 `reconciliationRequired=false` 的 `executionStatus=running` Shell，Workspace observation 不完整不会单独建立 reconciliation。Runtime 仍会保留 `workspaceChangeState=unknown`、`workspaceDiffIncomplete=true` 和 `sideEffectsMayExist=true`。完整的 Workspace 状态与安全事实仍需要后置核验。
- `gitWriteAccess=request` 授权的是一条获批 Shell 命令对当前 repository Git metadata 的写入。Runtime 不解析或重写 Shell 命令。原生 Git 仍可能读取用户 Git 配置，并可能执行 repository hooks、credential helper 或命令中显式启动的程序。需要禁用这些机制的产品 Git 操作继续使用独立的 `HardenedGitRunner` typed API。首次 `gh` 登录和凭据配置不由 `run_shell` 自动完成。

### Agent Shell

- Agent zsh/bash 使用原生 `pipefail`，管道前段的失败会影响管道退出码；POSIX sh 保持原行为。`head` 提前关闭管道可能导致 SIGPIPE，调用方应优先使用工具的有界输出。分号后命令的成功仍不能证明前面所有阶段通过。Runtime 不解析任意命令正文来推断业务成功。
- Agent zsh 在用户启动脚本加载后关闭 `NOMATCH`。没有匹配的文件模式默认以字面参数交给程序，不代表文件存在或命令成功；调用方仍应引用需要原样传递的模式。Runtime 只在启动时设置该选项，命令正文仍可显式修改 Shell 选项。bash、sh 和 Desktop Terminal 保持原行为。
- Runtime 的依赖、TLS 和视觉验收指引约束模型决策，但它们不是任意 Shell 程序的静态安全证明。系统不会自动安装缺失的 Office/渲染工具，也不会把结构检查当作逐页视觉验收。`succeeded` 表示 Run 正常结束，不表示所有人工验收已完成。

- `outputComplete=false` 表示输出尚未完整获得，原因可能是仍在运行、捕获失败或原始输出截断。旧结果缺少该字段时，系统不能推断输出完整。`outputCaptureError` 只保存捕获原因码，不能恢复已经丢失的内容；当前 Shell 聚合输出不会因可识别凭据被拒绝。本次历史 Run 的具体捕获子原因不会被新代码补写。
- 对账恢复不设固定轮数；语义无进展仍会触发恢复或收尾，并保留未解决的事实。系统不自动重放未知操作。模型的测试报告指引要求区分收集数与完成数，也要求标明缺失汇总和未执行阶段；这项指引不等于系统能够自动证明任意测试结论。

- Agent Shell 按 Run 独立管理，支持同一 Run 内的管道 stdin 和分段等待，但不提供 PTY，也不跨 Run 或 Runtime 重启恢复进程。不同 Session 的 Shell 可以并行，即使共享同一个 Workspace；同一 Run 的长 Shell 不再阻止其他普通副作用和协作调用。并发修改仍需协调文件范围；最后退出会清理尚未结束的命令。
- Runtime 会检测并清理 background child，但 Agent Shell 不能管理持久后台进程。
- ShellEnvironmentSnapshot 不恢复 aliases、functions 或其他 shell state。
- Skill 依赖自动绑定只适用于现有识别器能识别的直接脚本调用，包括 `$RUNTIME_PYTHON` 和 `$RUNTIME_NODE`。Runtime 不会从任意 Shell 包装、变量脚本路径或已激活 Skill 列表推断普通命令的依赖，也不会自动安装缺失包。
- Shell cwd 必须解析到 Workspace 内。调用方可以使用 Workspace-relative 路径或 Workspace 内的 canonical absolute 路径。Workspace 外的 absolute cwd 会返回 Tool Error。
- Agent Shell 的 raw stdout/stderr 仍有 256 KiB 上限。它不提供无限输出流。
- Agent Shell 会对 stdout 和 stderr 做 UTF-8 增量解码。Desktop Execution Feed 在运行中和终态保留两条流的展示脱敏片段顺序，并把 ANSI/OSC 控制序列按纯文本处理；聚合结果仍保留原始 stdout/stderr。旧 Item 缺少或为空的 `content` 时，Feed 使用结果中的 stdout/stderr 回退，并在结果存在时展示 `attemptCount`、`sandboxed` 和 `escalated` 事实。
- 模型收到的 `run_shell` 输出每条 stdout/stderr 流最多 16 KiB，整个结果 JSON 最多 48 KiB。模型投影保留每条流的首尾，并用独立的 `modelProjectionTruncated`、`modelProjectionOmittedBytes` 和 `modelProjectionContinuation` 说明模型省略的 stdout/stderr UTF-8 字节。原始 `truncated` 和 `omittedBytes` 仍表示 Shell 原始输出限制，已丢失的原始字节不能恢复。
- `read_tool_output` 可读取当前 Run 活动 Shell 的累计快照和同 Session 历史 Run 的持久终态输出。跨 Run 活动 Shell、跨 Session、缺失或歧义 ID 仍拒绝。provider tool call ID 不能换成 Shell session ID。`maxBytes` 范围为 4 字节至 16 KiB，按 UTF-8 边界分页，调用方按 `nextOffset` 继续。该工具不消费等待游标，不重放 Shell，也不清除 reconciliation。
- Desktop Terminal 是另一条 Main-owned PTY 路径。Agent Shell 的限制不会改变 Desktop Terminal 的现有说明。

### Repository Intelligence

- Inventory、Repository generations、Tree-sitter Index、symbols/imports/references/chunks、Repository Map、SQLite FTS5、Retrieval Snapshot、ContextPlan 和 ContextSnapshot 已经有 typed infrastructure、persistence 和 focused tests。
- Repository Generation readiness 已经进入 Runtime。Workspace 激活只 fast restore 和启动 watcher。`RuntimeEngine.run()` 会在第一个 Model Step 前执行一次 `ensure_ready()`。首次 build、cold-start reconciliation 和 watcher-invalidated 的下一个 Run 会执行 bounded Inventory scan。Clean Run 和同 Run 后续 Model Step 不会重复 scan。
- v1 数据库中的旧 generation 没有 persisted RepositoryMap。v6 拆库迁移会把它们标为 incomplete，不会用当前文件系统回填旧 Map。Runtime 只读取它们的 generation watermark。首次新 build 会生成更高的 complete generation。
- Cold start 仍然不能只凭旧 Inventory 证明仓库 clean，所以第一个 Run 会 reconcile。当前实现使用一次 bounded full Inventory scan。它没有 partial directory index、filesystem journal、Base Index + Worktree Overlay 或增量 Map 算法。
- Repository build 是增强能力。Canceled、incomplete、manifest verification failure 或 Git state change 不会替换旧 active generation。没有旧 complete generation 时，Snapshot 仍可为空，Agent 继续依赖 Workspace tools。
- 当前默认 online Run 已经自动执行一次 grounded Repository Retrieval，并通过 ContextBuilder 注入 Repository overview 和 evidence。每个 ModelAttempt 也会绑定精确 ContextSnapshot。
- 模型请求前缀仍有两条中途可变的来源。其一，resolved instructions 的 `runtime-permissions` 层渲染了 `Available tools` 清单和 rejected approval 计数，Run 中途连接 MCP 或出现被拒审批会改写这一层。其二，`user_context_layers` 中的 `selected-skill:*` 位于历史之前，Run 中途激活 Skill 会在全部历史之前插入新消息。两者都会让该 Step 的指令哈希变化并使后续历史无法命中缓存。
- Protocol 与 Desktop 目前都不暴露 cache token：`cache_read_tokens` / `cache_write_tokens` 只存在于 `usage_json` 与 tracing，因此 Prompt Cache 命中率无法从 UI 或协议观测，也没有 `structuralReuseTokens` / cache break 归因字段。
- 当前 Retrieval Query 只使用可以从用户目标、Inventory、Index、已有 Tool Result、dirty path 和 committed change 直接确认的信号。它没有 embedding、Vector Search、复杂 query rewrite、Base Index + Worktree Overlay，也没有 cross-worktree sharing。
- Watcher 事件不是 Workspace 安全事实。Watcher 不会静默修改当前 Run 的 immutable snapshot。

### Recovery 与 Checkpoint

- Runtime 可以持久化 Long Task 控制、pause/resume/cancel、restart verification 结果和 reconciliation 状态。启动时，没有未完成 Tool 执行或不确定副作用的 active Run，且同时没有 cancel request、没有 reconciliation barrier、没有未决有副作用 Durable Intent 和没有 running ToolAttempt 时，才可以重新排队。更广泛的 Restart Verification 尚未覆盖完整的 Git diff、credential、MCP、Seatbelt、pending Approval、unfinished ToolCall、Durable Intent 和 Checkpoint 兼容性集合。
- Checkpoint create/list 和 rewind/fork lineage 已持久化并暴露 typed RPC。Managed 和 Local Git Checkpoint 会保存 HEAD、staged、unstaged 和 untracked Git 状态。Managed Fork 会恢复独立 Worktree 的完整 checkpoint Git 状态。Managed 和 Local Rewind 会恢复原 checkout 的完整 checkpoint Git 状态。Local Rewind 只允许用户显式调用。Rewind 尚未重建完整逻辑 Context。Fork 仍不会复制全部非 Git immutable snapshots。Ignored 文件不进入 Checkpoint artifact。
- Worktree Session create、Session delete、managed Checkpoint Fork、managed Checkpoint Rewind、Create Branch Here、retention cleanup 和 Restore 使用 durable lifecycle intent。Session Handoff 使用 durable operation、strict HandoffPlan 和 startup recovery。Create Branch Here 使用 attach 时冻结的 `expected_head`，不使用创建时的 `base_commit` 判断当前 branch HEAD。Runtime 仍会拒绝 dirty Worktree delete，并保留无法证明安全的目录和 legacy attached branch。Retention 只处理 managed Worktree，不处理 adopted Worktree、Permanent Worktree 或按 bytes 的 disk quota。User Branch handoff 给 Local 后只释放 Eidos Worktree metadata，不删除 Git ref；Session delete 仍会保留这个普通用户 branch。Local Session delete 不删除用户 workspace。当前仍不提供 Permanent Worktree、Pinned Chat、Archive Chat、multi-Session shared Worktree、dependency cache Snapshot 或 Pull Request UI。
- Linked Worktree 的 Git metadata read 和获批后的精确 write 已在真实 macOS Seatbelt 中验证。默认 write、原始 repository working-tree access 和不匹配的 Worktree recovery 仍会被拒绝。Desktop dirty indicator 只使用 `project/gitContext` 和当前 Session status，不做所有 Thread 的持续轮询。Non-Git Local Workspace Checkpoint 仍不保存或恢复 filesystem state。
- 子 Agent 第一阶段复用现有 Agent Loop、审批和扩展工具。每个父 Run 最多有 16 个子 Session，同时最多运行 8 个子 Run；名额只在该父 Run 内计数，不影响其他父 Run。独立 Worktree 交付与合并、跨 Worktree Repository Intelligence 共享尚未实现。完整回归与真实 macOS 验证结果见当前 PR。
- Runtime 不恢复已消失的 Model request、Process 或 ToolCall。重启时，系统记录实际中断与未知结果，并禁止自动重放不确定操作。旧未知结果不再成为后续 Run、工具、正常完成或取消的核验门。

### Compaction 与 Context

- 自动摘要阈值为当前 Run 冻结模型窗口的 85%，系统使用 Provider 实测值及新内容估算后的预计输入进行判断。系统不再按固定 64K 或新增历史数量触发摘要。界面百分比经过取整，显示 85.0% 不代表未取整值已经达到阈值。请求输出预留、安全余量、内部投影限制或 Provider 超限仍可能更早触发保护性压缩。手动压缩不受 85% 限制。Runtime 保留用户消息、Skill 正文和近期证据，因此压缩不保证固定压后比例。候选摘要未通过验证时，Runtime 保留原投影；同一上下文状态不会重复尝试尚未超出输入预算的自动摘要。旧工具正文清理是独立步骤，不产生摘要压缩记录。
- 在线历史投影仍有 8 MiB / 2,000 条资源保护；768 KiB / 200 条只限制摘要候选批次。超出资源预算不再让 Run 直接失败。系统无法通过摘要恢复时会暂停，不会静默删除必需事实。损坏的回放和真实存储错误继续按错误事实处理。

- 在线 ContextCompactor 在规则候选之上请求结构化模型说明。模型只使用有界来源，因此它可能没有看到全部历史正文。关键事实继续由规则与持久事实提供。Schema、引句与来源校验不能证明所有语义完整，系统仍需要真实任务评测。
- 离线 cl100k 计数是参考估算，不是所有自定义模型的真实 tokenizer。消息协议、图片和 Provider 私有开销仍有估算误差。词表缺失或损坏时，Runtime 使用保守字节上界，不隐式联网。真实端点的误差分布和长期任务质量仍需单独评测。
- 旧工具清理只作用于模型输入。系统保留近期完整响应、失败与不确定结果，因此它不保证将所有请求压到目标值。内部候选数量或字节上限仍可能先触发摘要恢复。回读入口只覆盖明确允许的工具，不是任意 Session 或任意工具结果的读取能力。
- 明确约束仍有规则提取，当前系统没有完整语义证明。模型说明不决定授权或操作完成状态。连续压缩、重启、取消、分页回读与分叉范围有定向测试。合成长历史回放与受控真实模型续接只能验证指定样本；它们不代表真实仓库任务成功率、所有模型或长期费用目标。当前系统没有 Session 级摘要复用。
- 手动压缩由当前 Run 或下一 Run 在安全边界执行。系统不会在任务空闲时另建后台模型请求。原任务中断后，用户可以重新安排压缩。系统在压缩后发布新的 ContextPlan，并通过既有 Event/Outbox 刷新当前统计。
- 处理过程展示已加载历史页中的压缩时间点。单页最多返回 500 条记录，并继续受 Session 响应字节预算限制；事件元数据读取最多 2,000 行。旧事件缺少触发来源时，界面使用“上下文已压缩”，不推断自动或手动。过程记录表示摘要提交状态，不证明压后达到了某个 Token 目标。旧的 `soft_target` 记录继续可读，新自动摘要使用 `window_pressure` 来源。
- 候选摘要必须通过 `state.sqlite` 事实验证，才能原子写入 verified record 和权威摘要。Tool provenance 从 summary 的 source Item IDs 对应到真实 ToolCall IDs，并支持 pre-turn 跨 Run 历史。当前 deterministic compactor 不吸收 Event 内容或 Retrieval evidence 正文，所以不会虚假附加这些 provenance。验证失败时，Runtime 保留上一份 verified summary。原始 Item 和 Tool 事实不会被删除。Thread history JSONL 当前是 Event projection，不是独立的全量 Conversation authority。
- MemoryStore 已经提供独立 `memories.sqlite` 和 content-addressed Markdown 存储，但当前 ContextCompactor、用户长期记忆和跨 Session 检索尚未接入这个 Store。
- Context Usage 展示当前 Run 最新已构建投影的大小。系统优先使用这个投影对应请求的 Provider 输入量；系统不会使用累计 Tokens。压缩后，Engine 会发布新投影估算，下一请求返回后再使用其 Provider 实测值。分项是按当前总量分配的估算，不是 Provider 提供的精确分项。系统在重建投影失败时显示暂无统计，但同一 Session、同一模型的新 Run 在首次取得有效统计前会保留上一轮读数。系统没有为尚未构建的新输入预先生成读数。旧 Plan 没有分项时，系统不会补造各项数值。
- Provider 明确 `context_exceeded` 后，如果没有新的可压缩历史或 Context projection 没有进展，Runtime 会以 `context_still_over_budget` 停止。

### Extension 与 MCP

- MCP 设置页只对已启用、已授权的服务执行工具目录发现。首次查询会启动受现有 Seatbelt 控制的短连接，使用官方 SDK 初始化并取得有界目录，随后关闭连接；该入口不会调用工具。页面按 10 条分页读取 Runtime 的展示缓存。缓存最多 8 个目录，每个目录最多 512 KiB，有效期为 5 分钟，不写 SQLite，也不作为 Run 工具或授权的事实来源。Runtime 重启、缓存淘汰、配置变化或到期后，旧游标会失效，页面提示重新加载。目录描述最多展示 4096 个字符；不受支持的工具 Schema 仍可查看，但会标为不可用。设置页连接启动期限最多为 15 秒，关闭弹窗会忽略迟到结果。
- Plugin 支持三种本地包、显式更新、旧版本保留、远程 Streamable HTTP 和独立账号连接。应用随附 GitHub 插件，用户需要主动安装和授权。GitHub 浏览器登录仍需发行方注册应用、启用 Device Flow 并配置公开 Client ID；当前仓库未填入真实标识，PAT 入口可用。Figma 未内置。当前没有远程 marketplace、Git/npm 下载器、Codex App 连接映射或动态 import 用户 Plugin。服务 ID 仍须全局唯一。详见 [current-plugins.md](current-plugins.md)。
- Skill Catalog 当前只投影 `SKILL.md` frontmatter 中的顶层 `name` 和 `description`。`agents/eidos.yaml` 的 `runtimeDependencies` 使用严格 typed parser。其他字段可以保留在原始 Skill 内容中，但不会进入 Catalog、协议或权限判断。
- Settings 的手动 MCP 编辑器仍只支持本地 stdio。远程地址和认证方式由插件声明；用户可以通过账号面板连接。通用 OAuth 只接受公开客户端注册，或 SDK 的 DCR/CIMD，不提供机密 Client Secret 录入。远程响应上限为 2 MiB，要求不压缩传输。MCP 的 Resources、Prompts、Sampling、Tasks 和 MCP Apps 尚未提供产品入口；非文本工具内容仍遵循现有不支持规则。
- MCP ready connection 是长生命周期 Service，但 startup、Tool call、Tool list、cancel 和 shutdown 都有各自的有界等待。已经开始的 Tool List Changed bookkeeping callback 可能在关闭时需要等待完成。

### Observability / OpenTelemetry

- 当前 OpenTelemetry 集成只配置 Traces。Runtime 的本地 JSONL 日志不等于 OTel Logs pipeline。Runtime 没有建立 OTel Metrics 或 Logs exporter，也没有把 Trace 或本地日志作为业务事实或恢复依据。
- `OTEL_TRACES_EXPORTER` 默认是 `none`，因此默认不会把 Trace 导出到外部 Observability 后端。需要显式配置 `console` 或 `otlp` 才会导出。
- 当前 Trace 覆盖 stdio 入口、Run、Model Attempt、Tool Call，以及 Direct Model API 原生模型请求；同进程子 Agent 可以继承 Trace 上下文。它尚未覆盖 Renderer/Main 操作、OpenAI Responses 自定义请求的原生传输 Span、SQLite transaction、Repository Intelligence、Approval 或 Sandbox 内部阶段。Runtime 重启后等待中的 Run 会产生新的 Trace，仍可通过 `eidos.run.id` 对照 SQLite 事实。
- 较短的默认导出预算和较长的批次间隔只限制遥测开销，不能修复不可用的 collector。SDK 在持续失败或队列耗尽时仍可能丢弃 spans；本地日志、SQLite 和 Outbox 保持独立。维护者的显式 OTEL 配置可以改变默认预算。

### Application 边界

- `application/` 已建立 Session、Run、Response Action、Model、Extension、Repository、Context、Checkpoint 和 TaskLifecycle 的部分边界。
- 部分 RuntimeServer handler 仍通过 SessionStore 兼容入口执行。所有顶层 use case 尚未完成统一 Application migration。
- `Run.runtimeState` 是可选跨语言 DTO 字段，不是恢复权威。当前恢复权威仍然是 SQLite 中的 Run status、Approval、Step、ToolCall、Durable Intent 和 reconciliation 事实。

## Implementation Anchors

- `runtime/eidos_runtime/model/config.py`
- `runtime/eidos_runtime/model/`
- `runtime/eidos_runtime/runtime/supervisor.py`
- `runtime/eidos_runtime/runtime/engine.py`
- `runtime/eidos_runtime/context/compactor.py`
- `runtime/eidos_runtime/context/verified_compaction.py`
- `runtime/eidos_runtime/application/repository.py`
- `runtime/eidos_runtime/application/context.py`
- `runtime/eidos_runtime/repo_intelligence/`
- `runtime/eidos_runtime/persistence/checkpoints.py`
- `runtime/eidos_runtime/persistence/repository_intelligence.py`
- `runtime/eidos_runtime/extensions/`
- `runtime/eidos_runtime/sandbox/`
- `runtime/eidos_runtime/telemetry/provider.py`
- `runtime/eidos_runtime/telemetry/tracing.py`
- `runtime/eidos_runtime/db/schema.py`
- `runtime/eidos_runtime/db/database.py`
- `runtime/eidos_runtime/git/`
- `runtime/eidos_runtime/persistence/worktrees.py`

## Approval R1 的范围

权限 Grant 只覆盖当前 Run，用户不能选择 Session 或全局范围。当前权限模式代码已加入模型自动审批和完全访问。Runtime 仍不提供 Network Proxy、域名授权或持久 allowlist。路径权限只使用具体路径，不支持 glob。

旧审批或缺少完整执行事实的待批动作不会被猜测为可恢复。Runtime 会继续中断不确定执行，并保留 Reconciliation。用户批准权限不会自动重放之前失败的 Shell。

## Run 收尾与 Shell 执行期限

- 模型提交最终答复时，Runtime 在同一事务提交答复和 Run 终态。未清除的 reconciliation 会使 Run 进入 `interrupted`，不会再强制模型继续只读核验，也不会清除未知 Durable Intent 或放行新副作用。
- Workspace 文件 Intent 的自动恢复只核验实际目标和必要父目录。Runtime 最多观察 512 个目标，读取总量最多 64 MiB，并检查读取期限、取消、文件类型、链接、owner 和当前版本。SQLite 提交再次匹配 epoch 与目标集合；未知 Shell、MCP、外部操作和 Eidos-state 不通过该路径恢复。历史完整 Workspace refresh 入口继续兼容，但不再是自动恢复的前置条件。
- `interrupted` 保持稳定错误码 `RUNTIME_INTERRUPTED`，并在既有 `stopReason` 与 Event 中保留真实触发原因。主任务和子 Agent 共用结束原因提示，并区分执行中断、活动命令清理和真实未确认操作。新终态保存有界 `termination` 事实；已有记录不补写或推测原因。已移除的完成检查原因使用通用停止提示。系统没有具体证据时，界面不会断言网络故障、密钥失效或操作成功。
- 取消后 Worker 已退出时，RPC 返回 `canceled` 或 `interrupted`。系统记录取消完成时间；副作用未知不再作为取消失败。无 Worker 的 queued Run 如果已带有未确认副作用，也进入 `interrupted`。重复取消已中断 Run 返回原终态。Worker 仍存活时继续报告 `RUN_CANCEL_TIMEOUT`。
- 新 Run 的 Shell 不再设置默认进程总期限。`run_shell` 首次等待默认 10 秒，范围 250 毫秒至 30 秒；模型随后使用 `write_stdin`，非空输入默认等待 250 毫秒，空输入轮询默认等待 30 秒，范围仍为 250 毫秒至 60 秒。有效非负整数在范围外时使用对应边界值。等待窗口到期只返回 `shell_running` 和 `sessionId`，不会结束进程或触发 reconciliation。ToolSpec 的 600 秒 watchdog 只限制单次启动、审批重试或跟进调用，审批等待不计入预算。历史 3600 秒 ToolSpec 仍可读取，但新工具不会用它限制进程寿命。
- 控制器把已有 Shell 结果转成超时或取消结果时，会保留已有输出和终止信息，并继续执行结果校验和输出限额；展示片段使用 best-effort 凭据脱敏，聚合 stdout/stderr 保留原始内容。进程清理和未知副作用仍按原规则处理。此修改不恢复旧结果中已经缺失的 stdout/stderr。
- Runtime context 使用 `recentToolErrorFingerprints` 表示最近工具错误。空列表不代表对账完成。LoopGuard 不把 assistant 文本变化或最近错误列表中的错误消失单独当作新进展。

- 已有文件的原地写入不是原子内容替换。外部读者可能看到中间内容；写前版本检查不能阻止外部编辑器在检查后并发写入。异常终止可能留下部分文件；Runtime 不会自动覆盖回旧内容或重放补丁。
- 工作区外的已有文件按具体文件申请写权限。新文件使用目标最近的已存在父目录作为请求范围，审批会展示目录和具体文件。不存在的父目录可在批准后创建。受控文件工具可以在高危人工批准后修改受保护路径、`.git`、`.agents` 和 `.eidos`；链接和不支持的文件对象仍被拒绝。Git metadata 的普通单次 Shell 扩权使用 `gitWriteAccess`。无沙盒审批不会绕过操作系统 ACL、只读权限或系统隐私权限。

- 外部文件写入的不确定结果需要对外部目标另行核实。当前 Workspace 刷新不覆盖外部文件，因此不能自动清除此类 Reconciliation；Runtime 不自动重放或回滚。

- 普通 Skill 的授权不能覆盖整个数据目录或包含 `.system` 的 Skill 容器。新文件的最近已存在父目录如果包含系统 Skill，工具会拒绝该宽泛授权；新建 Skill 可以使用已有的 `skill_create` 审批流程。
- `skill_create` 可以提交超过 64 KiB 的完整文本资源，但仍保留现有存储资源边界：单文件 4 MiB、完整 Skill 8 MiB、最多 512 个文件（包含生成的 `SKILL.md`）。工具暂不接受二进制文件内容，也不提供分块续写或覆盖已有 Skill 的接口。名称、描述、相对路径、重复路径、系统 Skill 保护和审批规则继续生效。工具容量放宽不能修复模型生成的无效或未完成 JSON。新建 Skill 仍在后续 Run 的目录快照中生效；当前 Run 不会静默刷新目录。普通文件读取工具的容量和模型预览预算没有随本次修改扩大。
- 系统 Skill 的永久写入保护需要保留在执行环境中。因此任意 Shell 的无沙盒提权在存在永久写入保护时不可用；审批过的受控文件 helper 仍可对允许的具体目标进行无沙盒写入。

## 本轮产物实现的边界

- 本次后续的 `declare_outputs` 结果封装修复尚未补充或运行测试，也未运行 lint、构建或 smoke。历史契约校验失败的声明不会自动变成成功记录；修复后的 Runtime 需要重新执行声明，才能为这些文件生成产物卡。
- 2026-09-13 的产物声明修订已经补充 Renderer、Runtime、协议和 Main 测试。`pnpm test:fast` 和 `pnpm test:full` 已通过。Full 不包含独立的 `large_repository` 规模测试，也不包含真实 Desktop 人工验收；原生 Seatbelt 和 Electron smoke 未运行。
- `declare_outputs` 只声明当前 Run Workspace 内已有的普通文件，不支持外部路径、目录、远程 URL 或历史文件字节快照。工具只核验元数据，不创建、不修改、不校验文件内容，不证明文件由本轮生成。每批最多 20 项；任一文件失败会使整个批次失败，重复声明更新同一文件的声明。系统提示要求仅对用户期望接收的独立终态交付文件调用，创建或修改过不等于交付物，拿不准时默认不声明；但模型仍可能漏调或误调。漏调时只有原始文件和工具记录，界面不会猜测交付；误调仍以已提交的声明事实展示，界面不擅自隐藏。当前没有撤回声明或历史版本下载功能。
- 文件链接和产物卡打开当前 Workspace 内容。历史 ToolCall 保留执行时的 Diff；预览不提供历史文件字节快照。卡片在打开时比较声明版本与当前版本，并显示变化或打开失败信息；当前文件可能在核验后再次变化，图片和 HTML 预览仍使用已有版本授权检查。环境输出汇总已加载的成功声明，不代表完整目录或内容质量。删除不会抹除交付历史，缺失状态在点击时核验。执行目录不同的历史卡片禁止打开。Projectless 对话支持产物声明和文本修改卡。卡片标题使用声明标题或文件名。
- 旧会话没有 `declare_outputs` 记录时不会显示推断的产物卡。工具记录、最终回复中的文件链接和 Files 文件树继续可用；旧 Skill 的 `purpose="output"` 文本和普通路径不会自动补成声明。
- 支持内置预览的产物卡同时提供内置预览、系统应用打开和在 Finder 中显示。文档、演示文稿和 Excel 文件已有专属产物分类，DOC/DOCX、PPT/PPTX、XLS/XLSX/XLSM 提供受控的系统应用打开与在 Finder 中显示，暂不提供内置预览。CSV/TSV 使用文本预览，尚无电子表格网格、公式计算或幻灯片内置查看器。对应的分类、图标和 Workspace Reader 测试已经覆盖这些边界。
- 文本修改卡会区分已完成、部分失败和结果未知记录。重复修改同一路径时，卡片与审查入口显示净变化，不再相加各次补丁。缺少可用内联补丁、只有大补丁分页引用、内容衔接不一致或操作未核验时，卡片仍显示“行数未完整统计”。当前没有可精确恢复本轮修改的用户级 Checkpoint 入口，所以“撤销”按钮只显示不可用原因。
- 2026-10-13 修复了文本修改卡被 Git Review 可用性挡住的入口。33 项 Renderer 定向测试、Renderer 类型检查与构建通过。真实 Desktop 已使用非 Git `Office-Test` 会话验证文件名和“审核”按钮均可打开已有 Run 的持久化补丁；Git 和 Projectless 入口由自动测试覆盖，尚未补做人工验收。该次修复未运行 Full Suite，也未修改统计逻辑。2026-10-14 的后续修复在 Runtime 公共展示入口补充文件头，解决普通 Unified Diff 的路径丢失和行数未知问题；该修复不改写历史补丁，不改变大补丁分页边界。
- “最近一轮”展示文件工具记录的补丁。Shell 的文件观察只能给出路径变化，不能证明每条变化都由该 Shell 独占造成；完整仓库差异仍在仓库范围查看。
- 未提交文件列表为只读审查，不提供暂存、取消暂存、丢弃或按修改块操作；需要变更 Index 时使用提交弹层的暂存后提交，或在外部终端操作。
- Browser 是用户可操作的预览面板，尚未向 Agent 注册浏览器自动化 Tool。网页面板当前不提供标注入口，也没有独立的后退和前进 IPC，所以这两个导航按钮保持不可用。地址栏使用 `tldts` 识别域名、IP 和本地地址；输入框可以在已打开页面后继续编辑，无法确认是地址时会提交 Google 搜索。
- 本地 HTML 禁止外部网络资源、嵌套 frame 和表单提交。依赖外部资源或开发框架的页面应通过用户启动的 HTTP 开发服务预览。HTTP 页面可以按浏览器语义联网，但不能读取本地预览授权。页面权限申请、新窗口和下载尚未开放。
- 图片/PDF 的实际 Chromium 渲染、本地网页 CSP、开发服务热更新、原生视图遮挡和生命周期仍待 Desktop 验收。Office 预览和高级发布不属于本轮前四阶段。

- 大 Diff 和工具结果超过 64 KiB 时，Desktop 通过 `toolCall/readText` 按页读取完整的持久文本，每页最多 16,384 个 Unicode 字符。大 Diff 当前使用原文分页展示，分页内容不进入逐行评论组件。小 Diff 继续使用原有差异视图。页码偏移按字符计算，内容标识使用 UTF-8 SHA-256；Runtime 不把显示摘要当作完整修改记录。
- apply_patch 容量、流式累积和完整文本分页改动已完成单元测试和契约验证。大文本与真实 Provider 的流式端到端测试仍待真实模型调用验证。
- Step 与 Context 快照的 Blob 存储已移除统一的 32 MiB 上限，并共用消息和媒体引用。旧快照继续按原格式读取，历史的大 Blob 不会在本次升级中自动重写。模型窗口、工具输入及媒体投影仍使用各自预算；磁盘和内存资源也仍受运行环境约束。该存储修复不自动重跑已经失败的 Run。
- 本地异常诊断保留脱敏消息和有界调用位置，不记录源码行或局部变量。凭据脱敏使用模式匹配；未知格式的私有内容无法获得完整识别保证。历史日志缺失的异常正文和调用栈不会被补写。

### 技能设置管理的当前边界

- 本次设置重构已补充 Runtime 管理、数据库迁移、协议 Fixture、Main 集成和 Renderer 行为测试。定向测试、协议契约检查、Python 检查、Renderer 状态与行为测试、构建、Seatbelt 和 Electron smoke 已完成。Runtime 与 Main 全量测试首次运行发现的问题已经修正，并完成失败用例的定向复测。人工 UI 验收和真实 Provider 验证仍未完成。
- 插件技能的单独卸载是持久化的移除标记，不删除插件包内文件。当前页面不提供恢复已卸载插件技能的入口。
- 独立用户技能的物理清理保守地等待所有非终态 Run 结束。Runtime 在扩展清理回调、下次读取设置列表或启动时重试。目录内容、owner 或 inode/device 变化会保留文件并延后清理；用户需要处理这种外部变更，Runtime 不会猜测并删除新目录。
- 技能开关是独立偏好。插件关闭时，其技能仍可查看和配置，但不会进入新 Run 的可用技能目录。详情会提示所属插件尚未启用。
- 详情暂不加载技能图标资源，也不开放 Markdown 内的本地资源跳转。图标使用名称首字符；用户可通过 Finder 查看完整技能目录。


## 权限模式的验证范围

- 权限模式已通过组件单元与行为测试、协议校验和数据迁移测试。真实模型审查决策质量仍依赖于所配置模型的审查推理能力；静态检查不能证明所有外部第三方模型的审查决策稳定性。
- 自动审批复用当前 Run 的模型和 Provider，没有单独的审查模型设置。模型可能误判；Runtime 将受保护路径和显式取消沙盒保护交给用户高危确认；模型审查不能保证识别所有风险或提示注入。
- 自动审批只检查原本需要 Approval 的动作，默认 Workspace 操作不额外审查。证据包括最近 32 条用户消息及当前动作，仍受 48 KiB 总预算约束。超限、超时、错误、不可用、重启中断和未知风险交由人工决定，不自动批准或重放同一操作。明确风险拒绝继续生效；新用户授权可以触发重新审查。
- 完全访问会失去 Eidos 自身路径的沙盒保护。用户可以通过 Shell 改动 Eidos 数据、系统 Skill 或 Git metadata。macOS 的系统权限仍然有效，内置文件工具的普通文件约束仍然有效。
- 当前模式作用于单个 Run。用户不能在 Run 执行中切换模式。重新回答和编辑后重发会按 Composer 当前选择的模式创建新 Run。旧调用方没有传入模式时，完全访问仍回到人工模式；自动审批保留原模式。Runtime 拒绝缺少确认标记的显式完全访问请求。
- 本期不实现自定义审批规则、`config.toml` 权限解析、域名代理或持久 allowlist。审批元数据保存可取得的审查 Token Usage，当前 UI 不提供独立的审查费用汇总。

## 输入引用的当前边界

输入入口生产代码尚未经过本批测试或 UI 验收。以下限制是当前实现边界。

- 每轮最多包含 20 个引用。单个文件或图片最多 10 MiB。引用存储记录的总预算为 512 MiB；预算用尽时，界面会报告添加失败。
- Runtime 会尝试解码最多 256 KiB 的文本文件。每个文本快照最多保留约 64 Ki 字符，超过部分会显示截断标记。当前 Run 的引用正文合计最多 256 Ki 字符。
- 图片输入支持 PNG 和 JPEG。当前 Run 图片 base64 合计最多 16 MiB。图片预算使用尺寸估算，不能代表 Provider 的实际计费 token。
- 目录只保存一层、最多 200 项的摘要。PDF、Office、压缩包和无法解码的文件只保存位置及版本信息。Runtime 不复制这些二进制文件，也不承诺原路径在以后仍可访问。模型后续读取这些路径仍受当前 Run 权限约束。
- 文件选择器支持本地路径；工作区内的快捷搜索按工作区根目录列举名称，不再支持输入子目录路径查看目录。它还不是全仓模糊搜索。历史消息选择器展示最近 100 条加载结果，单次最多引用 20 条用户或助手消息。历史引用不递归导入其中的附件、工具输出或权限。
- 历史引用正文与图片受上下文预算限制。超出时，Context 会保留说明，原始快照仍在存储中。用户可以重新引用当前需要的内容。
- 尚未创建 Session 的输入共用一个持久草稿槽。Session 草稿相互独立。保存失败时，界面保留当前文字并提示重试；应用异常退出前尚未完成持久化的编辑仍可能丢失。
- Runtime 在添加新引用时清理超过 7 天、且没有被消息或草稿引用的记录。物理 Blob 回收沿用现有 GC 时机。界面暂未提供附件存储管理页。
- MCP、Plugin 选择只使用已安装、已启用且已授权的扩展。安装、配置和授权继续在设置中完成。Composer 使用现有 Run FIFO 接收下一轮输入，输入框上方的列表支持编辑排队消息文字和删除消息。队列编辑保留已有引用，目前没有修改引用或拖动排序的入口。用户可以通过队列行的箭头追加运行中引导。引导在下一次采样边界生效，不打断正在执行的模型请求、Shell 或审批。涉及扩展 selection、已关联计划或当前模型不支持的图片时，消息会保留在队列中。网页引用不在本批范围内。

## Plan 模式验证状态

本次 Plan 模式已经补充测试代码，并完成定向测试和自动化回归。`pnpm test:runtime:full` 通过 1947 个测试，`pnpm test:integration` 通过 761 个测试。新增测试覆盖计划版本、外部 MD 修改、计划确认、回答等待和答案幂等。旧版数据库升级和其他 Runtime 生命周期路径由现有回归集合覆盖。类型构建、协议契约、Python 检查、Seatbelt 与 Electron smoke 也已通过。Renderer 行为全量仍有 2 个不属于 Plan 变更的既有测试失败。人工 UI 验收和真实 Provider 工具调用仍未完成。

Plan 没有另建只读权限系统。模型通过模式指令遵守“先规划、后执行”，已有权限系统继续决定工具是否可以产生副作用。计划确认按钮是业务流程中的版本确认，不能代替权限审批。

计划正文最多 65,536 个字符和 256 KiB。计划面板展示最近记录，并按协议大小预算截取；完整版本仍保存在数据库。计划文件不会随 Session 删除自动清理。本次不增加独立的计划文件清理策略。


## 子 Agent 的阶段边界

本阶段交付父任务管理的单层委派，只支持 `default`、`explorer`、`worker`。三个角色继承父 Run 的普通工具、审批模式、权限和已启用扩展；角色指令表达通用任务、代码调查、实现与验证的分工。`explorer` 能执行 Shell，也能在已有权限允许时修改文件，因此它不提供强制只读保证。子任务共享当前工作目录。多个执行者可能修改同一文件，父任务需要分配不相交的修改范围并复核结果。子任务读取的是实时文件，不是固定提交或文件系统快照。

本阶段没有自定义角色、自动分配独立 Worktree、补丁交付与合并、多层委派、不同子任务选择不同模型、整组费用预算或自动重试中断任务。父任务沿用已有 Loop Guard；子 Session 数量上限不是费用上限。消息不打断正在执行的模型调用；消息在下一次上下文构建时生效。`followup_task` 复用子 Session 的已有上下文。已结束的父 Run 不能继续管理新委派，新用户回合需要创建新的委派关系。

Agent 消息正文最多 64 KiB UTF-8，模型每次优先提供最早 32 条未读消息，并按正文预算裁剪。Runtime 只在完整正文进入成功模型请求后保存投递回执，已读消息不重复注入；长消息的截断预览带有原始消息 ID，可通过 read_agent_message 读取。主模型默认只看结果、状态和明确消息，过程工具与中间回答留在子会话。结果正文按 8,192 个估算 token、64 KiB 单项预算和动态总预算生成预览，父任务可通过 read_agent_result 读取完整结果，分页严格限制在当前父 Run 拥有的子会话内。Desktop 的状态摘要仍采用短预览，完整会话记录可以分页查看。环境信息展示最近一组委派，当前没有跨组历史浏览器。真实 Provider 下的长结果交付和 Electron 手动验收仍需单独验证。

Runtime 重启后的完整协作恢复、Outbox 重投、真实模型端到端流程及子 Agent 在真实 macOS 上的审批链路仍需验证。完整测试结果以当前 PR 记录为准。

### 门控精简后的边界（PR #102）

Shell/外部操作的未知影响无法仅靠目录扫描证明恢复；旧 Intent 缺少可信目标时也不能按路径放行。文件冲突范围放行不提供自动合并。同 Run 的多个 Shell、不同 Run 的并发写入都需要任务分工。运行进程仍属于原 Run：等待子任务和 Plan 提问可以保持托管，最终退出会清理，不提供跨 Run 后台服务生命周期。控制调用允许混合批次，原 Worker 顺序等待后继续；取消或崩溃仍不重放不确定操作。

### 整体门控整改后的边界

首次 Repository 索引在已有 Workspace 所有者中构建，当前 Run 捕获不到 Snapshot 时依赖文件和搜索工具。索引仍然是有界的、按 generation 验证的能力，不承诺首次模型调用已经具备完整 Repository Map。

审批或澄清期间可编辑草稿，提交仍受 Run 状态、模型配置和工作区可用性约束。自动审批无法作出可信决定时可由用户接管，明确风险拒绝不能由模型自行绕过。新用户授权会重新计算拒绝指纹，不代表自动批准。

Git stage、commit、fetch 和 push 可与活动 Run 并存，Git 自身锁和已有操作幂等检查继续生效。工作树可能在操作期间变化，因此操作者仍需核对实际提交内容；切换分支、创建分支、merge、rebase 和 pull 保留工作树生命周期互斥。

Skill 清理等待实际引用它的非终态 Run；旧快照无法证明引用集合时保守等待。活动 Shell 输出只允许在同 Run 所有者中读取，跨 Run 仍只读取持久终态结果。

## 记忆

- 前台模型负责记忆改写的准确性、单条内容组织和语义匹配。Runtime 核对来源、权限和提交版本，前台保存与纠正不再调用第二个模型审核；这些检查不能证明模型改写完全符合原文。
- 无向量检索使用 FTS5、可选 trigram 和有界模糊匹配，短词/历史扫描可能 truncated；不保证任意语义改写召回。真实 Provider 知识质量、长期冲突协调和 Codex 对比尚需评测。
- 使用记录区分 served/read，尚不自动识别自由文本 cited/applied。空闲设置页不定期读取，也未接收记忆变更通知；其他会话的新记忆在手动刷新、重新打开页面或窗口恢复时显示。未结束的历史整理任务继续按状态安排读取。
- 遗忘不删除原始聊天，不追回已发送内容、外部导出或旧备份。ZIP/Markdown 未加密，旧备份可恢复后续遗忘资料；恢复默认停止自动学习，需要用户重新确认。
- 正文大小/mtime 校验不防主动保留元数据篡改。孤立文件按小时回收、保留一小时宽限，单次最多扫描十万项。
- 离线恢复仅导入新目录，Workspace 和 Provider 配置另行处理；macOS 原生沙盒、sidecar 和安装包仍需支持平台验收。

## Projectless 工作空间 Alpha 更新

新版只使用 `~/Eidos/YYYY-MM-DD/可读名称`。目录名来自首条输入的短文本，不是模型生成的最终标题。标题变化不移动目录。旧无项目 Session 在启动时废弃，旧工作空间文件原地保留；新版不提供旧 Session、旧产物链接或旧路径的兼容。

新建在排他 mkdir 后、身份提交前崩溃时，磁盘可能留下未被接管的目录。Runtime 会保留该目录并分配数字后缀，不会根据目录为空猜测它属于本次操作。数据库提交失败时，Runtime 只删除已核验且仍为空的本次目录；非空目录保留，已有身份记录可以继续创建。

2026-10-13 的定向验证通过：Runtime 63 个用例、Renderer 30 个用例、Main/JSON-RPC 重启创建用例、两端协议检查、真实 macOS Seatbelt 定向用例、Desktop 构建和 Electron smoke。Ruff、Deptry、两组锁定 Python 依赖的 pip-audit 与 `git diff --check` 通过。原生 Seatbelt、Electron smoke 和依赖联网审计在工具沙箱外完成。验证只使用临时数据，没有执行真实数据清理、Full Suite 或安装包验收。


## 自定义子 Agent 验证边界（2026-10-15）

- 自定义类型使用数据目录下的 TOML，指令直接写入 `instructions`。Runtime 继续由父 Agent 调用 `spawn_agent`，没有新增自动派发流程。
- 自定义模型选择包含提供商与配置 ID，但全局模型配置仍以 ID 唯一索引。同名模型跨提供商同时保存不由本次扩展解决。
- 子任务不会自动继承父输入中的文件、图片或 Skill 引用。父模型仍先接收最多 2,000 字符的子任务结果摘要。项目级定义和多层委派尚未实现。
- 桌面已支持智能体配置管理。保存会按固定字段重新输出 TOML，原文件的注释与排版不保留。名称创建后不能直接改名，用户需要复制为新智能体。格式损坏的文件继续显示具体原因，用户可以在 Finder 中定位并修复。
- 本地测试和脚本模型不代表真实 Provider 已验证。真实 Electron 的选择、预览和子任务交互仍需人工验收。定向验证结果见 [方案实施记录](custom-subagents-design.md#154-验证记录)。

- 2026-10-16 完整验证通过：Runtime 2,552 项通过、2 项按配置跳过；协议检查通过；Renderer 状态测试 173 项通过；Main 测试 92 项通过；Renderer 行为测试 624 项通过。`pnpm build`、契约生成脚本、Ruff 和 Deptry 通过。Python 依赖审计因环境无法解析 `pypi.org` 而未完成，Node 依赖审计没有运行。Full Suite 不包含 `large_repository`；本次没有运行原生 Seatbelt、DMG、真实 Provider 或真实产品窗口验收。

### 智能体桌面管理验证（2026-10-16）

- 定向验证通过：Runtime 62 项、Renderer 行为 143 项、状态与展示 69 项、Main/Runtime 集成 2 项。契约检查、Ruff、Deptry，以及 Main、preload 和 Renderer 构建通过。没有运行 Full Suite。
- Electron smoke 在系统沙盒外通过。临时原生窗口使用模拟配置验证了 320、768、1100、1440 四种宽度、表单滚动、保存按钮可见性，以及公共 `ModelSelector` 菜单的位置和提供商/模型 ID 保存。该检查不代表真实 Provider 请求已经验证。
- 本次没有新增依赖。联网 Python 生产依赖审计实际发现现有 `pydantic-ai-slim 2.43.0` 的 5 条已知漏洞：`PYSEC-2026-4285`、`PYSEC-2026-4286`、`PYSEC-2026-4284`、`PYSEC-2026-4290`、`PYSEC-2026-4291`。本次没有扩大范围升级 Model Gateway 依赖，审计没有通过。

## Model Gateway 依赖升级（2026-10-17）

Runtime 将 `pydantic-ai-slim` 从 2.43.0 升至 2.57.0，并将其要求的 `openai` 升至 3.26.0。锁文件同时更新必需的 `pydantic-graph` 2.57.0 和 `genai-prices` 0.1.13，其余依赖版本保持不变。主 Python、隔离工具 Python 和 Runtime Node 的联网依赖审计均通过，原五条漏洞已不再报告；本次没有忽略审计项。

定向验证通过 Runtime 231 项和 Main/Runtime 2 项，覆盖流式请求、思考回放、错误映射、取消、重试、工具契约、自定义 Provider、持久化快照与遥测。快照测试会核对新版本记录与历史版本保留。Ruff、Deptry、锁文件一致性、macOS arm64 Runtime Bundle 构建、内置 Runtime smoke、包内依赖版本核对、Electron 启动关闭和 `git diff --check` 均通过。测试仍有企业微信 SDK 使用旧 websockets 接口的两条弃用提示。

本次没有运行 Full Suite、DMG 安装包或真实 Provider 验收。升级没有引入新的依赖种类，没有修改模型配置格式、SQLite Schema 或 Eidos Loop，也没有删除现有运行机制。上文与渠道文档中 2.43.0 的审计失败是升级前的历史验证记录。

## 自定义模型表单收敛（2026-10-17）

表单增加提供商 ID，并减少兼容设置与说明文字。提供商 ID 是模型配置中的标识字段，尚不表示共享的连接或凭据实体。缺少该字段的旧配置仍可运行，编辑保存时需要补充。Token 预设使用 1024 倍率，接口返回的其他整数不会被改成预设值。页面使用通用 OpenAI 参数，Runtime 保留旧配置的显式兼容设置；本次没有增加依赖或 SQLite 迁移。

默认思考强度和支持强度使用一个选择框与五个勾选项。选择自动会保留支持的思考档位，并清除明确默认值；选择默认强度会同时声明该强度可用。连接成功仍只代表模型列表请求成功，真实接口的生成、工具和思考参数接受情况仍需人工验收。

定向验证通过 Renderer 19 项、Runtime 44 项和 Main/Runtime/共享契约 3 项。真实 Electron 页面覆盖 320、768、1024、1440 四种宽度、提供商名称跟随、Token 下拉键盘选择、能力勾选同行与固定底部操作区。构建、Desktop 契约检查、Ruff、Deptry 和 Diff 检查通过。旧协议测试补充 Runtime 关闭，避免临时目录清理后文件监听器继续运行；设置测试补充等待初始焦点，避免文字输入与延迟聚焦竞争。两处测试修订均重跑通过，生产 MCP 行为没有变更。本次没有运行 Full Suite、真实 Provider、Runtime Bundle 或 DMG 验收。

### 模型元数据与表单反馈更新

模型发现已改为逐字段处理无效元数据。名称别名、显式思考配置和支持强度列表均可回填；未知档位只被忽略，无效模型 ID 仍会跳过。系统不根据名称猜测缺失能力。模型列表右侧显示接口提供的名称，选择不同模型会重新回填展示名和支持强度，刷新同一模型保留手动修改。上下文和最大输出固定使用 K，1K = 1024 Tokens；小数换算保留原 Token 数，持久化格式不变。模型数量与进度放在测试按钮上方，密钥显隐使用输入框内的图标，七个必填输入统一使用 `*`。

定向验证通过 Renderer 15 项、Runtime 29 项及 Main/Runtime/共享契约 3 项；元数据解析的后续调整另按发现用例重跑通过。Electron 页面检查覆盖四种窗口宽度、名称列、思考强度回填、K 单位、必填星号、状态与按钮位置、输入框图标和键盘预设。构建、Desktop 契约检查、Ruff、Deptry 和 Diff 检查通过。本轮没有增加依赖、协议字段或数据库迁移，没有运行 Full Suite、真实 Provider、Runtime Bundle 或 DMG 验收。

### Token 下拉与连接反馈细化

测试按钮在请求期间保持原文案和宽度，并禁用重复点击。页面不再临时插入旋转图标；请求超过 200ms 后才显示获取进度。上下文窗口和最大输出将 K 直接放在数字后，例如 `256K`。预设列表只有一个当前高亮项，已选值通过勾号标记；用户输入自定义数值后按回车不会被首个预设替换。

Renderer 定向测试 18 项与 Renderer 构建通过。真实 Electron 检查通过加载期间的按钮结构、加载前后宽度、单一高亮、已选勾号、键盘选择及 320、768、1024、1440 四种宽度的布局；快速请求的延迟反馈由 Renderer 定向测试覆盖。本轮只调整表单及验证代码，没有新增依赖、协议字段或持久化变更，也没有运行 Full Suite、真实 Provider 或安装包验收。

### 思考强度回填修复

本地接口的模型列表使用 `supported_reasoning_levels: [{"effort":"low"}]` 声明强度。原解析器遗漏了这个字段。当前解析器会保留已知强度并忽略未知项，模型名称和 Token 上限仍按字段独立校验。选择模型时，表单勾选支持强度并将默认值保持为自动；选择自动不再清空勾选。最高档位的名称统一为“最高”。模型配置允许空默认值，并在协议省略空值后继续保留自动语义。已有配置的明确默认值保持不变，没有新增依赖或 SQLite 迁移。

定向验证通过 Renderer 44 项、Runtime 50 项、Main/Runtime 与共享契约 3 项，以及 Desktop 契约检查、Main/Renderer 构建、Ruff、Deptry 和 Diff 检查。测试覆盖实际响应格式、未知字段、空默认值、保存重启、Run 快照、自动时不发送思考参数、明确选择与公共组件。真实 Electron 页面在四种窗口宽度下验证勾选回填、自动默认值与“最高”文案。本轮还通过现有 Runtime 发现函数只读获取本地接口的 7 个模型，其中 6 个声明了强度并被正确解析，另一个没有强度元数据。模型生成与工具调用没有经过真实接口验收，本轮没有运行 Full Suite 或安装包验收。

### Token 输入框显示实际数值

上下文窗口和最大输出的输入框改为显示实际 Token 数量，直接输入的数字也按实际数量保存。下拉预设继续显示原来的 K 标签，选中后显示对应整数。显式 K/M 简写仍可输入，确认或失焦后会转成实际数值。此次修改没有改变预设倍率、接口数据、存储格式或校验上限，也没有新增依赖。

Renderer 定向测试 19 项、Renderer 构建和 Diff 检查通过。真实 Electron 检查覆盖数值回填、K 预设、键盘选择、单一高亮和四种窗口宽度。测试同时核对普通数字不会再乘以 1024，带 K 的小数仍能精确换算。本轮没有运行 Full Suite、Runtime 测试或安装包验收。

### 自定义模型接口地址、Key 输入框、Token 缺省与模型复制

接口地址示例值修改为 `https://api.example.com/v1`。API Key 显示隐藏图标垂直居中对齐，移除独立的清空按钮；输入框占满剩余宽度并靠近连接测试按钮。上下文窗口和最大输出改为选填项，星号与必填标记移除；用户未填时保存自动应用默认值（上下文 128,000，输出 8,192），用户输入自定义值时仍进行有效性和上限校验。保存时上下文窗口必须严格大于最大输出，若上下文窗口小于或等于最大输出，会提示“上下文窗口必须大于最大输出。”并阻止保存。已保存的自定义模型在列表项中支持“复制”按钮（位于“编辑”按钮左侧），点击后仅复制提供商配置（提供商 ID、名称、接口地址、协议），模型设置保持留空供用户配置。内置模型不显示复制按钮。

定向验证通过 Renderer 24 项、Runtime 37 项及 Desktop 契约检查通过。没有新增依赖或 SQLite 迁移。


### 上下文统计与待发送队列刷新

圆环和详情使用同一次 `context/details` 响应。两者使用 Run 冻结的窗口上限；系统不把百分比封顶为 100%，也不将累计消费作为当前占用。分项按当前总量估算，合计与总用量保持一致。Provider 对应的是已经发出的请求；修改或压缩后的新投影在取得实际 Usage 前仍是估算。自定义模型可以使用已保存的较小窗口；内置模型的窗口编辑仍属于后续设置能力，本次没有增加该入口。

待发送队列会先完成已经收到通知的新刷新，再显示旧读取的失败。当前读取仍失败时，界面保留错误和重试入口。`RESOURCE_NOT_FOUND` 在这个入口说明队列暂时无法读取，不再直接宣称 Session、Project 或 Run 已被删除。2026-10-19 的本地日志和隔离复现进一步确认：历史投影曾绕过主库锁，与自动队列刷新同时访问同一 SQLite 连接，导致现存会话被误判为不存在，或抛出 `InterfaceError`。历史投影现在对主库和历史库的查询及结果读取分别持有对应数据库锁，文件 IO 保持在数据库锁外。本次修复不改变队列 FIFO、RPC、数据库结构或 Run 并发策略。

## 任务结束提示与输出截断（2026-10-19）

任务结束提示已统一精简，句尾不使用句号。新响应同时包含不完整工具参数和 `length` 等未完成信息时，Runtime 优先记录结束原因，不再将其归为参数 JSON 错误。该调整保留用量与响应 ID，不执行不完整回复中的工具调用，并继续使用原有一次有界纠错。

历史失败记录不会自动改写。此前只保存了 `invalid_tool_arguments` 且缺失结束原因的记录，仍然按已有事实显示参数错误。修复不会提高模型输出上限，也不能保证模型在纠错后返回完整内容。原始失败参数正文仍不进入诊断记录。

定向验证通过：Runtime 45 项、Renderer 状态与静态渲染 75 项、执行流交互 47 项。回归覆盖输出截断、错误分类、一次纠错、未执行工具、持久记录重启读取、提示原因优先级及句尾无句号。Renderer 构建、Ruff、Deptry、两组 Python 锁定依赖的 pip-audit 与 `git diff --check` 通过。本轮没有运行 Full Suite、真实 Provider 验收或安装包验证。

## 自定义模型图像输入回填（2026-10-19）

本地接口使用 `modalities.input` 声明图像输入能力，原发现解析器没有读取这个字段。当前解析器补充了该来源，并保留已有 `input_modalities` 和 `architecture.input_modalities` 的优先级。空列表表示没有声明图像输入；缺少或无效的字段继续读取后续来源。系统不会根据输出图像能力或模型名称推断输入能力。创建和编辑共用原有回填逻辑，刷新同一个模型保留本次表单中的手动修改，切换模型则使用新模型的声明。

定向验证通过 Renderer 26 项、Runtime 发现 24 项、Main/Runtime 集成 2 项，以及 Main 构建、Ruff、Deptry 与 Diff 检查。回归覆盖新建、编辑同一模型、手动覆盖、模型切换、缺失与无效字段、能力来源优先级，以及图像声明保存和重启读取。本轮还使用现有发现函数只读获取本地接口的 5 个模型，均正确识别了接口声明的图像输入。修复没有新增依赖、协议字段或数据库迁移，没有修改界面布局，也没有运行 Full Suite、真实图片输入请求、Electron 界面或安装包验收。

## 容量恢复与未知结果（2026-10-19）

系统保留未知执行结果，但不要求用户核验，也不以此阻塞后续工具、子任务、定时任务、分叉、完成或取消。系统不自动重放不确定操作。工具错误和容量限制通过参数反馈、替代路径、短预览、分页和可取消重试恢复，不创建机器容量暂停或要求用户继续。权限、身份、版本和真实结果校验继续生效。模型上下文移除工程校验 Hash；原始结果、内部完整性和 UI 分页校验仍保留。详细规则见[当前架构](current-architecture.md#容量恢复与未知结果2026-10-19)。


## 模型有限重试与断流恢复的验证范围（2026-10-20）

普通模型采样已实现共享次数与恢复窗口、结构化错误分类、断流独立草稿及重启预算延续。Schema 30→31 为请求组和草稿归属增加字段。特殊收尾、审批、压缩等辅助调用仍使用原策略；前台没有新增重试倒计时或手动继续入口。

验证使用隔离 SQLite、ScriptedModel、真实 Adapter 与模拟 HTTP/SSE，不读取实际 Provider 凭据，不产生模型费用。模拟流可以证明请求次数、流关闭、草稿隔离、残缺工具不执行和已执行文件操作不重放，但不能替代真实断网、各 Provider 端点或安装包验收。测试命令和实际结果由本次交付单独记录。本轮未运行 Full Suite。

## 上下文连续压缩的首版可靠性改进（2026-10-22）

系统为已接受的模型说明增加了带类型和精确来源的延续记录。后续摘要独立保留这些记录，并从原始资料再次核对。预算不足时，系统拒绝本次候选，保留上一份有效摘要。来源读取改用 8 KiB 的有界头尾预览，相同文件的不同内容版本不会仅因路径相同而合并。文档示例、源码和用户自述中的通过声明会降为未验证说明。验证器会独立复核，系统不只依赖模型适配器的判断。

定向验证覆盖 20 轮连续压缩、模型失败回退、原始结果回读、预算不足、旧 Hash 与重启、取消和迟到结果、错误引句和来源类型、同路径不同版本、预览标记伪造、未知格式版本，以及普通和容量恢复视图中的保留。当前相关 14 个 Runtime 测试文件共 162 项通过。六类资料与五种长度的 30 组离线规则回放、共 90 次压缩通过；评测报告另外检查已有延续记录是否保留。本轮没有运行 Full Suite、真实 Provider 质量评测或安装包验收。

本版保护的是已经接受的有来源说明、既有明确约束和旧版模型说明。模型第一次没有识别出的重要资料仍可能遗漏。普通叙述和较大的历史仍采用有界预览；头尾之外的关键内容需要回读。本版没有增加事实自动失效、按当前问题检索任意中间片段或完整窗口的压后目标。延续记录达到预算上限时，系统保留原有记录，不自动决定哪些事实已经失效。有限合成回放不能证明所有真实任务都能长期保真。

新格式使用现有 JSON 元数据和摘要版本，没有数据库迁移。新 Runtime 保持旧摘要读取兼容，旧 Runtime 不支持新延续格式的降级读取。本轮没有新增依赖或改动锁文件。Ruff 和 Deptry 通过；主 Python 依赖的 pip-audit 报告 certifi 与 Pillow 共 25 项既有漏洞，审计没有通过。独立文档运行依赖的 pip-audit 通过。
