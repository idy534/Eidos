# Eidos 当前架构

网页搜索使用 `typed IPC → webSearch/read、webSearch/save → WebSearchApplication → WebSearchRepository / SecretFiles`。Schema v34 保存配置和 Run 搜索快照。RunResources 按用户明确选择的默认服务发布内置 `web_search`。搜索经过现有 Controller 的审批和 Durable Intent，再由 RuntimeAsyncKernel 托管官方 HTTPS 请求。网络等待不持有 Run 的提交窗口，搜索不增加 Shell 网络 Grant，也不自动切换服务。具体字段、迁移和结果边界见[当前网页搜索](current-web-search.md)。

钉钉渠道使用 `Stream SDK → ChannelApplication → SQLite 接收记录 → SessionApplication / RunApplication → 普通 Run → 持久化回复 → HTTPS OpenAPI`。配置沿用 typed IPC 和 stdio JSON-RPC。渠道服务由现有 AnyIO Kernel 与 Supervisor 管理，准备使用现有 managed task。Schema v30 保存渠道配置、映射、来源和投递事实。具体边界见 [当前渠道接入](current-channels.md)。

本地定时任务采用 `typed IPC → schedule/execute → AutomationApplication → AutomationRepository → SQLite → 普通 Run`。Schema v28 新增计划、触发与执行来源。调度由现有 AnyIO Kernel 和 Supervisor 管理，不增加常驻线程、额外执行器或全局 Run 配额。具体持久化、工具分类、恢复和来源规则见 [当前本地定时任务](current-automations.md)。

> 权限模式范围：manual 与 auto_review 默认保留路径和 Seatbelt 保护。原路径硬拒绝及显式沙盒外执行可经高危人工审批开放；auto_review 不能代替用户批准这类请求。full_access 直接执行，不创建逐项授权、Approval 或等待状态。所有模式仍保留参数、身份、版本、取消、Durable Intent、结果校验和未知结果记录。审批不能赋予 macOS 管理员权限，也不能解决 ACL、TCC 和只读卷限制。

本文只描述当前代码中的系统结构和职责。生产代码和自动化测试是本文的事实来源。本文不描述历史 Phase，也不描述尚未接入默认 Run 的目标态设计。

当前 Model Gateway 使用 `pydantic_ai_client.map_model_error → ModelRequestFailure`，Usage 使用 `client.ModelUsage`。源码已移除未接入调用链的旧 Gateway 事件、Usage/Cost 定义和 HTTP 错误转换器。重试判断仍保留 `EidosModelError` 基类。实际压缩仍由 `ContextCompactor → VerifiedCompactionRepository.verify_and_persist` 执行验证与持久化；Application 保留已有验证接口，移除无人调用的持久化包装与依赖注入。

Renderer 的新建会话继续使用 `AppShell → useSessionController` 的草稿流程。源码已移除无人使用的 CreateSessionDialog、AgentSourceDialog、EmptySettingsState 和仅由组件测试使用的 Badge。ExecutionFeed 只接收审批展示所需的数据，不再逐层传递未使用的审批操作回调、响应状态、projectless 和 stepResolutions。审批交互继续由现有审批输入区及控制器处理；`session/read` 的 stepResolutions 兼容字段和双端校验保持原样。

回答级分叉经过 Renderer → typed preload IPC → Main → `session/fork` → `SessionForkApplication` → `SessionHistoryRepository`。Application 复用现有 Supervisor 托管任务，历史捕获和 Git IO 不占用协议输入循环。Repository 使用 SQLite WAL 只读视图捕获历史前缀。JsonBlobStore 保存严格类型的不可变记录。Runtime 在短事务中创建目标 Session，提交历史清单、资源引用、来源、Event/Outbox 和幂等结果。Runtime 在提交前重新检查回答、来源、引用与 Reconciliation。

`session/read` 将继承记录放在 `inheritedHistory` 中，并与真实 Item 共用分页游标。Main 在验证协议后把历史记录加入展示列表，并添加仅供界面使用的 `inherited` 标记。历史 Run 不进入 `runs` 表、调度、恢复和授权。Context Repository 将继承事实与自有事实合并，继续使用现有预算和压缩校验。模型响应回放只进入模型上下文。界面看不到私有回放。

Local 与 Projectless 分叉可以共享当前目录。独立 Worktree 分叉复用 Git Backend、WorktreeManager、WorktreeSnapshotService 和 durable lifecycle。快照固定分叉时的 Git 状态，并在新 Worktree 中恢复和核验。该流程不改变来源目录。来源删除不会删除目标持有的历史资源。原 `checkpoint/fork` 继续保留自己的 Checkpoint、Run 和重放语义。

分叉是已持久化的正常 Session。创建事务同时保存来源标题和 Session created Event。分叉没有自己的 Run 时，侧栏也会展示它，空 Session 清理也不会删除它。v26→v27 是数据迁移。迁移按创建顺序补齐未命名分叉的标题，并在同一事务中提交 Title updated Event/Outbox。嵌套分叉会使用已补齐的父标题。来源已删除时，迁移使用“新会话”。现有非空标题不变。

Renderer 的 `ForkIcon` 统一侧栏、回答操作栏和分叉弹窗的图标。侧栏会话行常驻显示分叉图标，并根据 `forkedFrom.sessionId` 从已有 Session 列表读取直接来源的当前名称。气泡通过既有 `onSelect` 跳转到来源，并关闭气泡。来源缺失时，气泡不提供跳转。该显示和导航变化不修改 RPC、SQLite Schema 或历史记录。

## 聊天与文件查找（2026-10-20）

聊天搜索经过 `ChatSearch → typed preload IPC → Main → search/sessions → SearchApplication → SearchRepository → SQLite`。空搜索复用 useSessionController 已读取全部分页的普通会话列表，先按项目过滤，再按 updatedAt、createdAt 和 ID 排序并取前 10 条。该路径不调用搜索接口，不增加协议或持久化状态。时间复用 SidebarTooltip 的 formatSessionTime，并按分钟刷新。`session/readAround` 复用 SessionRepository 的有界分页，从命中位置附近读取最多 40 条记录。Main 继续合并继承历史展示项。Renderer 按 Item ID 定位、展开所在处理过程并用 CSS Custom Highlight 标记正文。这些操作不创建 Run，也不激活或切换工作环境。

Schema v33 增加 `search_documents`、`search_fts`、`search_pending` 和 `search_index_state`。源消息的插入、正文更新和删除通过同事务 Trigger 登记待处理项或删除旧投影。搜索请求推进最多 40 项、目标 80 ms 的投影批次；一个正在解析的消息可能超过该时间目标。队列保存在 SQLite，Runtime 重启后由后续搜索继续处理。界面在后台继续查询并保留现有结果，不显示单独的索引说明。系统不增加后台线程。`markdown-it-py` 提取可展示的正文，保留代码和链接文字。分叉历史继续从冻结的历史记录读取，来源删除不会删除目标的索引来源。

三个及以上字符的字面量查询先通过 FTS5 trigram 选出候选，再核对正文包含关系。一两个字符的查询直接在完整正文投影上使用 SQLite `instr`，不把侧栏前 50 条作为范围。标题和项目名称直接读取业务表。系统先按会话归组再分页；当前 Session 查找则按继承历史、自有消息和消息内命中位置分页。游标绑定查询、范围和索引版本。版本变化会重置分页，避免沿用旧偏移。搜索索引是派生数据，消息、Session 和冻结历史仍是事实来源。

当前文件查找经过 `FileFind → 可终止 Web Worker → DOM Range / CSS Custom Highlight`。普通预览截断时，Renderer 经过 typed IPC 调用 `workspace/readFileForFind`。Runtime 重新核验当前 execution root、文件身份与版本，并通过现有 WorkspaceReader 每次读取最多 192 KiB。Main 校验分块进度、合并并解码最多 2 MiB，保持原有 1 MiB 单行 JSON-RPC 边界。Renderer 不直接访问文件系统。聊天查找条使用 absolute 定位；文件查找条放在零高度的 sticky 容器中。两个浮层均保持单行，不改变正文布局。其他面板和跨文件搜索没有接入。

## 1. System Boundary

Eidos 是一个面向 macOS 的本地桌面 Agent Runtime。系统包含 Electron Desktop、Python Runtime、SQLite、本地 Workspace 和远端 Model Provider。

Renderer 是展示边界。Renderer 不能直接访问 Node 系统能力，也不能直接连接 Python Runtime。Electron Main 是 Desktop 和 Runtime 之间的控制边界。Python Runtime 是 Session、Run、Item、ToolCall、Approval、事件和副作用状态的业务协调边界。

聊天排版沿用 `ExecutionFeed → MarkdownContent → react-markdown / CodeBlock`。ExecutionFeed 只给用户正文、过程文字和最终回答添加 `.eidos-chat-prose`；MarkdownContent 的可选 `className` 让其他复用方保持原样式。正文字体定义为 `--font-prose` token 并与侧栏项目名、Session 名共享；CSS 只覆盖正文字体、行距、字重和 Markdown 间距，字号、主题颜色、背景和容器布局继续沿用原规则。用户正文仍按原始换行显示；代码块仍保留空白和横向滚动。Renderer 没有增加字体文件、依赖、协议字段或持久化数据。

`ExecutionFeed → useFeedScroll` 分开保存底部跟随意图和实际底部位置。原生 `ResizeObserver` 同时观察 `.feed-content` 和 Feed 视口，覆盖处理过程与工具的展开动画、延迟图片、代码渲染和视口高度变化。内容更新与尺寸变化共用一个合并到下一帧的滚动入口。跟随开启时，Feed 关闭浏览器滚动锚定；用户浏览历史时，Feed 恢复浏览器锚定，并停止自动到底。向上滚轮、触摸、键盘和滚动条操作可以中断跟随。用户手动回到底部或点击“滚动到最新内容”后恢复跟随。折叠引起的滚动范围收缩不改变用户暂停跟随的选择。“滚动到最新内容”使用同一滚动入口，不再启动独立的平滑滚动。空列表首次出现内容时会挂载观察器，卸载时会断开观察器并取消待执行帧。该状态只用于 Renderer 交互，不增加协议或持久化字段。

远端模型只提供不可信的文本和 ToolCall 提案。Runtime 负责验证模型输出、构建 Context、执行 Tool、处理 Approval、应用 Sandbox Policy 和提交持久事实。

## Workspace model

Eidos 把用户选择的目录建模为 Project。Project 的基础事实是 `name` 和 `workspace_root`。Project 可以有 Git capability，但 Git 不是 Project 存在的前提。Project 由 `project/create` 显式写入 SQLite，不依赖 Session 创建。`name` 可以省略，Runtime 会使用 canonical Workspace 的文件夹名。

```text
Project
 ├── workspace_root
 └── optional Git capability
          └── Thread / Session
                    ├── Local Execution → Project.workspace_root
                    └── Worktree Execution → Managed Worktree → Run
```

Non-Git Project 只能创建 Local Execution Session。Git Project 可以创建 Local 或 Worktree Execution Session。Desktop 的新建会话入口先保留本地草稿，首次提交时才物化 Session。Workspace Dock 的 Review 只对 Git Project 展示，Terminal 和 Files 只对有 Project 的会话展示，Browser 对所有会话展示。Local Session 的 `execution_mode` 是 `local`，`worktree_id` 是 NULL，Run 直接使用 `Project.workspace_root`。Worktree Session 的 `execution_mode` 是 `worktree`，它绑定 Managed Worktree，Run 使用该 Worktree root。Git Project 的 Session Git status 和 diff API 会读取当前 execution root。

文件读写、Shell、Skill、MCP、Context、Long Task、Sandbox 和 Checkpoint 属于 Workspace 或 Runtime 能力。它们不因为 Project 没有 Git 而失效。Git status 和 Git diff 在 Git Project 的当前 execution root 上提供。Managed Worktree 和 Git-based Fork 仍然只在 Git Project 中提供。

Desktop Workspace Explorer 也只读取当前 Session execution root。`WorkspaceExplorerApplication` 先解析 Local root 或验证 Managed Worktree identity，再调用共享 `WorkspaceReader`。Draft 在 Session 尚未物化时使用请求中的 Project binding；Runtime 从持久化 Project 解析 root，并在请求同时提供 root 时核对 workspace identity。Workspace Dock 不对 Projectless 会话展示 Files。`WorkspaceReader` 与 Agent 文件工具共用 fd-relative、`O_NOFOLLOW`、hard discovery directory 和 root ignore 规则。普通敏感文件名和内容可以读取；`.git`、`.agents`、`.eidos` 只在写入侧保护，显式路径可以列举、搜索和读取，默认发现仍隐藏这些元数据目录。`workspace/listDirectory` 只返回一层子项。`workspace/readFilePreview` 返回有界 UTF-8 预览或图片/PDF/HTML 元数据。`workspace/readAsset` 按块读取受控资源。Projectless 的 Runtime 文件能力仍经过同一个 Reader。Renderer 对 Conversation 的文件打开请求检查工作区相对路径格式。Runtime 负责核验当前 execution root、文件身份和实际存在性；文件树缓存不替代这些核验。Renderer 不直接读取 filesystem。

Conversation 文件引用沿用 `MarkdownContent → ArtifactLink → AppShell → WorkspaceExplorer → typed IPC → WorkspaceExplorerApplication → WorkspaceReader`。`workspaceFileLink` 从显式 Markdown 链接目标解析绝对路径和可选行列号，并把当前 execution root 内的目标转为工作区相对路径。显示标签不参与路径解析。行内代码不再自动变成文件链接。文档预览通过 `documentPath` 保留相对链接语义。`ArtifactActions.openFile` 和 Renderer 内部的 `WorkspaceFileOpenRequest` 携带可选 `line`、`column`；位置只控制预览滚动与选区，不改变 Runtime 文件读取请求、权限或文件身份。Shiki 的 line transformer 和普通文本行节点提供源行标记。Markdown 的 rehype 节点位置提供源文本块标记。代码高亮完成后，预览会再次应用当前定位请求；同文件的不同请求用 `requestId` 区分。

AppShell 的文件打开回调跟随当前工具打开函数更新。工具打开函数在 React 状态更新中按 Tab ID 去重，连续文件链接点击会复用同一个 Files 面板，并在面板内部添加或选中对应文件 Tab。这个规则也适用于工具菜单已打开的 Files 面板；Terminal 和 Browser 仍使用独立 ID 创建多个工具 Tab。

WorkspaceExplorer 在侧栏布局中记录实际宽度，并通过 ResizeObserver 跟随侧栏调整。Files 展开时，文件树默认使用展开前的侧栏宽度，并按当前可用空间限制大小。用户在展开布局中调整过的宽度会保留，侧栏布局的文件树高度单独保存。预览区在未选择文件时显示“选择文件以查看预览”；只有当前文件确实处于读取中且没有可显示的预览时，界面才显示“正在读取文件…”。

`ContextBuilder` 根据持久化的 Session 项目绑定判断是否加入 `project-file-references` 指令层。`InstructionResolver` 只在 `has_project` 为真时添加该层，Memory 投影触发提示词重建时保留这个条件。无项目的 Session 不增加文件引用提示词。已有指令快照仍保留原文；新构建的提示词才包含新规则。Finalizer 沿用已有指令，缺少指令时也按 Run 的项目绑定构建默认提示词。本轮不修改协议 DTO、SQLite Schema 或依赖。定向提示词测试覆盖项目绑定、Memory 重建和 Finalizer 回退；验证结果见 `current-limitations.md`。

Desktop 会让 Conversation 始终保持挂载。Session header 右侧固定显示环境信息入口；用户可以通过工作区开关打开或关闭右侧 Workspace Dock。Dock 使用本地 Renderer 状态管理 Review、Terminal、Files 和 Browser Tab。Review 和 Files 各只有一个工具 Tab，Terminal 和 Browser 可以同时打开多个 Tab。Files Tab 内可以同时预览多个文件。文件 Tab 保留在预览栏中，预览区不显示当前路径、文件大小或手动刷新入口，侧栏布局默认给预览区更多空间。产物卡的图片会直接进入全屏预览，HTML 会直接打开隔离网页面板，PDF 会进入 Files 的内置预览，系统应用打开入口保持可用。Dock 普通展开时环境信息入口仍在 Session header，完全展开时入口显示在 Dock header。Session 对话的回答和提问框共用固定最大宽度并保持居中；回答右边与提问框右边对齐，窗口变宽时不会继续拉伸。Dock 支持 Tab 切换、关闭、关闭最后一个 Tab 时自动折叠工作区、空状态选择工具、全侧栏展开和关闭。Dock 与 Conversation 之间的分隔条可以拖动调整宽度。Files 的文件树与预览区也有独立的可拖动分隔条。Dock 关闭时，Conversation 内容在可用宽度内居中。Session 或 execution binding 变化时，Renderer 会关闭旧 Dock，并用新的 execution key 重新加载 Workspace 数据。

## 2. Process Architecture

当前默认调用链如下：

```text
Renderer
  ↓ typed IPC through context-isolated preload
Electron Main
  ↓ JSON-RPC 2.0 / JSONL over stdio
RuntimeServer
  ↓ typed Method Registry and Application boundaries
RunSupervisor
  ↓ per-Session FIFO admission and Run worker control
RuntimeEngine
  ↓ Model Attempt / ToolCall / finalization coordination
SamplingRuntime / Model Gateway
ToolCallRuntime / ToolExecutionController
  ↓
Tool handlers / ToolOrchestrator / Seatbelt / Process / Filesystem
  ↓ SQLite transaction
Event / Outbox
  ↓ JSON-RPC notification
Electron Main → Renderer
```

Python Runtime 的 stdout 只输出有界的 JSON-RPC 行。Runtime 日志写入 stderr 或受控本地日志。RuntimeServer 的 `initialize`、`runtime/health` 和 `runtime/shutdown` 负责握手、健康状态和有序关闭。

`RunSupervisor.start_managed_task()` 在 AnyIO worker 中完成登记等待、目标执行、错误记录和资源清理。任务清理不再在 AnyIO 事件循环中获取 Supervisor 的同步锁。提交线程仍在锁内通过 BlockingPortal 登记任务；worker 中的清理等待不会挡住 portal 的调度。受保护的等待保留已接纳任务的资源所有权，取消继续通过 `threading.Event` 协作传播，系统不会丢弃仍在执行的线程来冒充资源静止。这项死锁修订尚未进入测试阶段。

`runtime/eidos_runtime/runtime/loop.py` 只导出 `RuntimeEngine` 的兼容名称。它不是当前 Runtime Loop 的第二套实现。

## 3. Desktop Boundary

Renderer 通过 context-isolated preload 使用 typed IPC。Preload 使用 `contextBridge` 暴露受限的 `window.eidosRuntime` API。Renderer 不直接读取 Runtime stdout，也不直接访问文件系统、子进程和 API Key。

Renderer 的本地 CSS 由 `desktop/renderer/src/styles.css` 统一导入。该文件只维护加载顺序，不再包含业务样式。`styles/` 保存设计变量、页面基础、公共控件、浮层、提示和滚动条。`app/AppShell.css` 保存窗口与应用布局；组件旁的 CSS 保存各模块的样式、响应式规则和动画。组件不再各自导入本地 CSS，第三方 CSS 保留现有库入口。

滚动条外观统一由 `styles/scrollbars.css` 管理。Renderer 使用 Chromium 的 `::-webkit-scrollbar-*` 定义 6px 滑块、透明轨道和角落；这些部位不显示边框、阴影和箭头。公共层将标准 `scrollbar-width` 与 `scrollbar-color` 保持为 `auto`，避免它们覆盖自定义外观。模块只保留隐藏滚动条和代码、Diff 悬停显示滑块的规则。灰色滑块与悬停颜色来自有效的颜色变量，变量不引用自身。

公共控件负责 Button 的颜色、交互状态、焦点和禁用样式。产品不绘制焦点环：`--focus-ring` 统一为 `none`，键盘与鼠标聚焦都不出现绿色轮廓。Chromium 会把弹窗还原的焦点判成 `:focus-visible`，所以这个 token 是移除残留绿圈的唯一入口，各模块只引用它，不再各自定义轮廓。焦点位置改由悬停、选中和边框颜色表达。审批和计划模块只补充按钮排列与尺寸。Composer 的模型与审批模式选择器共用触发按钮和浮层基础规则。列表与选择卡使用 `--surface-selected`，普通悬停使用 `--surface-hover`；危险操作继续使用危险颜色。代码与 Mermaid 共用标题标签和复制控件。聊天专用 `.eidos-chat-prose` 保留独立范围，文件预览与其他 Markdown 不启用该范围。

Workspace Dock 独立维护 Session 内容列和环境浮层。居中 Session 沿用原构建实际生效的单列布局，并由 `--session-content-width` 限制回答与 Composer 宽度。规划样式不再重复定义环境浮层，技能样式不再修改整个设置窗口。样式测试按统一入口读取模块；原生 Electron 定向检查验证最终计算样式和响应式布局。

Electron Main 负责以下职责：

- 创建启用 `contextIsolation`、禁用 `nodeIntegration` 和启用 sandbox 的 BrowserWindow；
- 解析开发 Runtime 和打包 Runtime 的路径；
- 启动、读取和关闭 Python Runtime 子进程；
- 校验 JSON-RPC response、notification 和协议行大小；
- 为 stdio RPC 请求记录超时状态。超时请求的迟到 response 会被安全丢弃，真正未知的 response id 仍然会触发协议错误；
- 管理 Runtime 启动代次和 Runtime 重启。Runtime 进程意外退出时，Desktop 会发布启动错误，Renderer 可以通过 typed IPC 请求重新启动 Runtime；
- 转发 Session、Run、Context Usage、Model、Extension 和 Approval 的 typed API；
- 将 Runtime 主动发起的 Approval 请求投影到 Renderer；
- 为用户直接操作的 Terminal 创建和回收 Main-owned PTY；
- 在 Quit 时取消活动 Run，等待 Runtime 收敛，并在必要时报告关闭失败。

Response Action 使用同一条 typed IPC 边界。Main 把 `responseAction/state`、`item/setFeedback` 和 `run/revise` 映射到 Runtime。

Terminal 不经过 Python Runtime，也不是 Agent Shell Tool。Renderer 只通过 typed IPC 发送 Session id、输入和尺寸。Main 会重新读取 Session，并从 Local workspace 或 active Managed Worktree 得到权威 cwd。Main 使用 `/bin/zsh -l` 和受限环境创建 PTY。每个 PTY 绑定创建它的 WebContents。Main 会在窗口销毁、Session Handoff、Worktree Restore、Session Delete 和应用退出时终止对应 PTY。Projectless Session 会在 Renderer 和 Main 两层拒绝 Terminal。

## 4. Runtime Transport & Application Layer

`RuntimeServer` 维护 SQLite Store、ModelConfigStore、RunSupervisor、RuntimeAsyncKernel 和 Method Registry。Method Registry 负责方法注册、typed request/response 校验、重复注册检查和稳定错误映射。慢 remote（fetch/pull/push）与慢只读 Git 观察（`project/gitContext`、`session/gitStatus`、`session/gitDiff`）使用 `DeferredMethodResult` 与 supervisor managed task offload，后台经线程安全 `send` 回包，输入循环不等待；draining/重配调度失败时只读观察同步回退。

应用层为 Session、Run、Response Action、Model、Extension、Repository、Context、Checkpoint 和 Long Task 提供边界。RuntimeServer 仍保留少量兼容入口，但它不会建立第二套状态权威。

`ResponseActionRuntimeServer` 在默认 `__main__` 入口上扩展 Response Action 方法。它与基础 RuntimeServer 共用初始化、健康检查、关闭、Supervisor 和 SQLite。

## 5. Run Orchestration

RunSupervisor 负责按 Session 维护持久 FIFO、Run Worker、Approval 等待、取消、暂停、恢复和关闭收敛。同一 Session 同时只运行一个 Run，因此一个 Session 可以排队多个 Run。不同 Session 的 Run 可以并行，且不区分 Workspace、Local checkout 或 Managed Worktree。普通 Run 没有并发上限。每个 Run 独立持有 `ToolConcurrencyGate` 和 `ShellProcessManager`。不同 Session 的 Run 可以在同一个 Workspace 并行执行 Shell 和其他普通副作用。一个 Run 的长 Shell 仍会阻止这个 Run 启动新的副作用，但 `write_stdin` 可以继续管理原 Shell。等待 Approval 不会占用其他 Run 的执行资源。

RunSupervisor 把同步 Durable Runtime Core 与进程级 `RuntimeAsyncKernel` 连接起来。RuntimeAsyncKernel 持有一个 AnyIO Blocking Portal。Model 异步 I/O、MCP Connection、Managed Task 和安全只读并行批次通过这个 Kernel 执行。Run Worker 仍是当前 Run 的同步控制边界。

RuntimeEngine 在每个模型 Step 前生成不可变的 Step Resolution、Rule、Context、Model 和 Tool 快照。它按以下顺序协调一次循环：

```text
consume input → resolve rules → build context → sample model
→ validate text and ToolCalls → execute or finalize
→ commit result and facts → inspect cancellation, context and progress
→ continue, compact, recover, pause or finish
```

模型响应完成后，`LoopDecisionEngine` 先给出动作，`RuntimeEngine._settle_sample_boundary` 再提交对应的 Step、Assistant 和 Run 状态。该边界返回“重建上下文、执行工具或返回”三种控制结果。协议修复由同一个决策入口限制次数；循环保护命中时不再尝试修复。这个拆分不改变当前数据库事实或终态语义。

RuntimeEngine 下的主要职责是：

- `ContextBuilder` 和 `InstructionResolver` 构建本次模型请求；
- `ProjectRuleResolver` 固化项目规则快照；
- `SamplingRuntime` 和 Model Gateway 执行一次模型请求；
- `ToolCallRuntime`、`ToolExecutionController` 和 `ToolOrchestrator` 处理 Tool；
- `ApprovalCoordinator` 处理可持久化的 Approval 暂停和恢复；
- `ContextCompactor` 处理 Context 压力；
- `LoopGuard` 处理语义状态收敛；
- `RunFinalizer` 生成有界、无 Tool 的最终回答。

`LoopLifecycle` 提供内部诊断观察点：模型采样前后、工具批次前后、上下文压缩前，以及进入执行范围的 RuntimeEngine 离开时。Runtime 默认写入结构化 debug 日志，也可接入进程内观察者。事件只携带 Run/Step/Attempt ID、工具数量和固定原因，不携带工具参数或模型文本。观察者失败只写本地日志，不改变 SQLite 事实或权限决定。用户配置的 Hook 由独立 `HookService` 执行。该服务复用原 Shell 沙盒和资源所有权，并在 SQLite v36 中保存信任、调用和待触发 Post 事件。当前入口与兼容范围见 [current-hooks.md](current-hooks.md)。

Model Step、Segment Step 和 effective time 在当前实现中是 telemetry 和 operational segment 信息。健康 Run 不会因为固定 model-step、Run duration 或固定 repeated-call counter 自动终止。LoopGuard 通过语义 fingerprint 判断是否收敛，不是固定步数限制。Segment 达到 operational quantum 时可以 rollover，但 rollover 不是 Run 终态。

Chat Completions Adapter 根据结构化响应事实把有 ToolCall 的响应归类为 `commentary`，并保留模型文本、可选 MessagePhase 和 Provider `finish_reason`。Chat Completions 没有原生的 Assistant phase，所以没有 phase 的响应使用 `unknown` 或 `None` 表示。RuntimeEngine 不读取 MessagePhase 或 `finish_reason=stop` 作为完成门控。每次 normalized sampling response 都得到 `needs_follow_up`：ToolCall 或待消费的当前 Turn 输入需要继续采样，assistant-only response 可以结束当前 Turn。可返回给模型的 Tool Result 和 Tool Error 都会进入 Context，再触发下一次 Sampling。Sampling 收到的 provisional text 在完整响应通过校验前不会持久化。可重试的 transport failure 即使已经收到 provisional text，也会复用同一个 frozen ContextSnapshot 创建新的 Model Attempt。已收到的文本或 ToolCall 不会自动重放。normalization 的 `protocol_error` 和 `length` 都进入同一条已有的有界 protocol repair，连续错误合计最多触发一次。`content_filter`、cancel 和 authentication failure 不进入该 repair，直接终止当前模型流程。

`SamplingRuntime.accept_validated_response` 是模型尝试完成与有效文本提交的单一入口。它只接受通过工具批次校验的 `ready` 或有文本的 `no_tools` 响应。工具批次随后按模型声明的顺序记录与执行；只读批次可以并行执行，但结果仍按原顺序汇总。失败的模型输出不会通过该入口进入下一次模型请求。

确定的 Tool Error 只表示本次尝试失败，不会单独把 Run 置为终态。Runtime 会把失败事实交给下一次模型决策。模型可以修正参数、选择替代 Tool，或者在没有安全路径时结束。等价重复且没有新事实时，LoopGuard 负责收敛。

串行和并行批次都只把成功结果计入新的进展事实。失败调用仍保留在 SQLite 和模型历史中，但不会仅因参数变化而重置进展。真实 Workspace 变化、权限变化和用户输入仍参与已有的收敛判断。

正常 assistant-only 完成调用在 SQLite 写事务中检查取消、input_mailbox、活动子 Run 与原采样协作投影。事务拒绝过期完成后，Engine 保存有效回答并重新构建 Context，或进入已有 waiting_agents；这些控制事实不依赖额外模型判分。正常无工具 follow-up 先完成 Item/Step，取消在采样边界优先处理。合法受管轮询对 LoopGuard 为中性，不覆盖先前收敛证据。

正常完成前，Runtime 仍清理该 Run 的自有 Shell。清理成功后，Run 和执行段保持完成状态，不因停止了存活命令而降为中断或失败。工具结果继续保留命令终止情况和未知副作用；真实清理失败仍交给基础设施错误路径。启动恢复会修正旧版仅因 `active_shell_stopped` 中断、最终完整答复已提交且没有取消、待消费输入或活动子任务的记录。修正与 Event/Outbox 在同一事务提交，保留原结束时间和工具事实，不重新执行操作。

Runtime 不再发送 `_eidos_assess_completion` 模型审查。完成审查的请求、候选答复去重、反馈注入和 `completion_unconfirmed` 生产路径已移除。Loop 保留 Provider 的 `needs_follow_up`、`end_turn` 和消息 phase；文件交付仍来自 `declare_outputs` 的实际核验结果。旧 ModelProfile 的 `completion_check_version` 与旧 completionCheck Event 只用于兼容读取，不能重新启用审查。

Run 的失败、停止和中断使用统一的 `termination` 记录解释结束原因。记录只保存阶段、原因码，以及必要的 Step、ModelAttempt、工具名称、参数位置和 HTTP 状态。Runtime 在结束状态与 Event/Outbox 的同一事务中保存该记录。模型失败绑定当前 Step 的最后一次失败 Attempt；Repository 在提交时核对所属 Run 和状态，不能引用其他任务或已成功的请求。该记录不增加第二套状态机，也不改变审批、取消、重试和 Reconciliation 决策。

Desktop 的主任务提示和子 Agent 提示使用同一份中文文案规则。界面优先使用具体原因，再使用已有错误码能证明的通用原因；未知原因不会被猜测为网络错误或工具执行失败。Session 正文只展示中文结束原因，不提供诊断信息区块。Runtime 和协议继续保留技术错误码、步骤和请求标识。子 Agent 摘要分别保留错误码和停止原因，不再混用字段。界面只为当前原因维护专用文案，已移除的完成检查和执行预算原因使用通用停止提示。操作结果未确认只在对应工具记录中说明，不追加到任务结束提示，也不改变正常完成状态。

Schema v23→v24 为 `runs` 增加可空的 `termination_json`。迁移在事务内完成，失败回滚；已有记录保持原值，不推断或补写结束原因。Python 的 `RunTermination` 通过 `scripts/generate-run-termination-contracts.mjs` 和协作契约生成器提供 TypeScript 类型。两端使用 `protocol/fixtures/run-termination-v1.json` 验证有界字段与未知字段拒绝规则。

ToolExecutionController 区分 Durable Intent 授权和实际执行开始。文件与外部操作在执行回调开始时记录执行事实；Shell 在 Popen 成功后记录启动，stdin 在实际写入后记录输入事实。准备异常、进程未启动和未送出的输入返回普通工具错误。Controller 保留可信 Handler 的已知结果，结果格式校验、输出屏蔽和投影失败不再仅凭工具类型或授权记录建立 reconciliation。取消继续控制 Run 生命周期，已取得的结果确定性不被迟到取消或后置扫描超时覆盖。

文件 Handler 在准备后确认内容已经相同时，通过内部 `HandlerOutcome.execution_skipped` 记录没有进入执行。Controller 允许这类成功结果不创建 Durable Intent，不把它改成执行契约错误。这个内部字段不来自模型参数或外部工具结果。Controller 仍检查实际执行开始、文件变化和未知副作用证据。单文件与多文件的无改动 Update Patch 都返回 `no_changes`；空文件创建、移动和混合真实修改仍使用原有提交流程。该结果不清除其他操作留下的 reconciliation。

MCP 分别记录 SDK tool call 已进入调用和已收到有效 CallToolResult。进入调用前的连接、内核拒绝或取消不会建立副作用屏障。调用之后失去结果继续标记为未知。已收到结果后的取消、超时或连接故障保留执行确定性；响应可能有副作用，但不要求对账。SDK 的传输和响应解析保持原实现。薄 ClientSession Adapter 使用 SDK 的公开输出校验入口，把重复校验延后到 Eidos 的结果处理阶段；McpToolAdapter 使用固定快照的有界 Schema 和既有 jsonschema/referencing 校验器检查成功响应。输出 Schema 错误、内容不支持和输出超限可以使 Tool Result 失败，但不会把有效响应改成未知执行。

已知 Workspace 文件操作的恢复使用 WorkspaceReader 对实际目标执行有界读取、缺失确认和版本复核，随后在 SQLite 中核对 reconciliation epoch 和目标集合。Runtime 不要求无关目录的 manifest 完整。Shell、MCP、外部操作与未知范围不能通过文件观察清除；健康的受管 Shell 不作为未知操作来源。恢复只确认当前文件状态，不重放原操作，也不把原失败结果改为成功。

RunFinalizer 为 context pressure、loop guard 等需要提前停止的路径生成有界、无 Tool 的回答。它不改变普通 Agent Loop 的 `needs_follow_up` 判定。Finalization Attempt 仍然记录自己的 timeout、model failure 和 output item 状态。

## 6. Context & Instructions

默认在线 Run 使用 `ContextBuilder` 从持久状态事实、当前 Run、Model Profile、Rule Snapshot、Selected Skill、历史 Item、Tool Result、Workspace State 和额外事实构建模型 Context。

Schema v32 为 `steps(run_id, ordinal)` 增加非唯一索引。上下文历史中的响应回放和响应阶段查询使用这个索引定位 Step，再通过已有 `model_attempts(step_id, ordinal)` 索引读取 Model Attempt。系统不再为每个历史 Item 反复扫描全库的 Model Attempt，减少后台上下文查询占用共享数据库锁的时间。主库启动与持久化布局升级入口都支持 v31→v32；索引创建和版本更新在同一事务中完成，失败后回滚。系统继续接受 v31 备份，恢复后的数据库在启动时升级。该迁移不改写历史记录、回放选择或协议。共享连接的所有者锁仍覆盖查询和结果读取。

`ProjectRuleResolver` 从 Workspace root 到 effective cwd 逐层读取每个目录中优先级最高的非空候选文件。候选顺序是：

```text
EIDOS.override.md
EIDOS.md
AGENTS.override.md
AGENTS.md
CLAUDE.md
```

每层只选择一个候选。Resolver 使用共享的 32 KiB UTF-8 byte budget。Resolver 记录 shadowed candidate、读取或预算 warning、原始 content hash、实际包含字节数、directory level、Workspace root 和 effective cwd。Run 使用 immutable rule snapshot，Step Resolution 保存 resolved instruction hash 和 effective cwd。

`InstructionResolver` 按 System Safety、Base Agent、Runtime Policy、Project Rules 和 Selected Skill 形成分层 instructions。Skill Catalog 属于 developer capability context。真正加载的第三方 `SKILL.md` 属于较低权限的 user context。Project Rules 和 Selected Skill 保留来源与 hash。 模型可见的 Skill 目录只保留唯一调用名称、说明和入口路径；完整身份、版本与 hash 留在 Runtime 的冻结快照中。skill_read 继续使用现有参数读取该名称对应的 Skill。它们不具备修改 Runtime Permission、Approval 或 Sandbox 的权限。 通用 Runtime 提示不包含具体审批模式的扩权流程。当前权限层按 manual、auto_review、full_access 分别生成；完全访问层不混入沙盒扩权、嵌套沙盒或自动审查指引。可用工具由当前 Tool Schema 暴露，权限文本不重复整份名称列表。

Context Budget 使用 `projected_input_tokens` 判断下一次模型请求是否适合当前窗口。圆环、详情和兼容的 Context Usage RPC 使用同一份当前 ContextPlan 元数据。Runtime 只接受与这个 Plan 绑定的已完成 ModelAttempt 的 Provider `input_tokens`，否则系统显示当前投影估算。Provider 数值不是累计消费；分母来自 Run 的冻结 ModelProfile，用户调小窗口后，系统不使用 Catalog 上限替代它。系统保留超过 100% 的真实比例，只把圆环绘图限制在一圈内。

压缩成功后，Engine 使用同一批工具定义、规则、Skill 和仓库上下文重新构建投影，并把新的 ContextPlan 与 Run Updated Event/Outbox 一起保存。系统不为统计伪造 ModelAttempt，也不等待下一次模型响应才刷新。新的 Provider Usage 只覆盖它实际对应的请求。若摘要已经提交，但新投影无法构建，系统显示暂无统计，旧请求总量不能重新冒充压缩后用量。ContextPlan 的原始分项仍保存参考 Token 数；详情按这个构成估算分项，并使分项 Token 合计严格等于当前总量。界面分项与总占用均使用设置上限作为百分比分母，十分之一百分点的舍入也保持合计一致。分项仍属于估算，Provider 没有返回逐项实测。

统计读取不加载 Snapshot 正文或 Blob，内存撤销不要求读取已撤销内容。同一 Session、同一模型的新 Run 在提供有效统计前，Renderer 保留上一轮的圆环和详情读数；这份展示缓存不会作为新 Run 的统计写回 Runtime。新 Run 提供有效统计后，Renderer 使用新读数，并恢复无数据时清空的规则。切换 Session、模型、移除 Run 或 Runtime 未就绪时，Renderer 清空展示缓存。Context pressure、Provider `context_exceeded` 和 projection overflow 会触发经验证的有界压缩或安全恢复。没有新的可压缩历史或投影没有进展时，Runtime 使用更小的历史证据视图继续可取消恢复；系统不创建容量暂停，也不重放不确定操作。

Runtime 在预计输入达到当前 Run 冻结模型窗口的 85% 时尝试自动摘要压缩。系统直接比较未取整的 Token 数量，不使用界面四舍五入后的百分比。系统不再使用固定 65,536 Tokens 或新增 32 条 Item 作为摘要触发条件。这个路径在输入仍符合请求预算、没有待处理 Approval 或 reconciliation 时执行，并按 Token 预算保留完整近期响应、用户消息和 Skill 读取正文。系统复用现有事实验证与事务提交，不删除原始历史。自动摘要失败后，Runtime 保留原投影；同一上下文状态不会重复尝试这个提前压缩路径。输入预算已经不足时，系统继续执行原有窗口压力保护。

在线历史投影继续使用有界数据库读取与完整响应分组。摘要候选批次和在线资源预算保持独立；系统不会删除原始 Item、Blob 或冻结历史。投影资源不足时，Engine 尝试摘要和更小的证据视图，并保留取消入口。当前延续记录有独立的资源上限，系统不会通过恢复预览悄悄截断有效记录。

ContextBuilder 在摘要前独立清理旧工具正文。清理阈值是 65,536 与可用输入预算 75% 中的较小值，目标是阈值的 75%；这个阈值只控制工具正文清理，不触发摘要请求或压缩过程记录。Builder 按可用输入预算的 1/8 保留近期证据，参考 Token 目标限制在 1,024～16,384。系统按完整模型响应保留记录，至少保留最后一个响应，即使该响应本身超过目标。系统只清理较大的、已完成且成功的文件读取、文件列表、搜索和已结束 Shell 结果。失败、运行中、需要 reconciliation、Skill、记忆、MCP 和带图片正文的结果保持原样。模型输入保留结果状态、文件版本、来源 Item、原文 Hash 和 `read_tool_output` 回读参数。系统不修改 SQLite 原始结果，也不重新执行原操作。内部候选投影超限仍沿用已有摘要恢复。旧的直接调用仍支持按数量保留近期记录；在线百分比摘要使用 Token 预算。

`read_tool_output(stream="result", itemId=..., callId=...)` 对允许的历史工具结果提供 UTF-8 字节分页。读取范围只包含当前 Session 和该 Session 已冻结的分叉历史。源 Session 的原始 Item ID 不授权访问其他 Session。原有 stdout/stderr 和活动 Shell 读取保持兼容；记忆、Skill、MCP 和写入工具正文不进入这个回读出口。

Token 计量优先使用 Provider 返回的实际 Usage。该值只代表对应的已发请求，不能直接当作缩减或增长后的新请求大小。下一请求的预算以最近一次匹配请求的 Provider 输入量为起点，再估算内容变化量。增长部分至少采用一倍参考 Token 变化量；缩减部分采用已观察的 Provider/参考值比例。缺少 Provider Usage 时，系统才完全使用词表估算。所有未来请求的推算仍属于 estimated。

本地辅助计数使用已锁定的 `tiktoken==0.13.0` 和随应用提供、带固定 SHA-256 的 cl100k 词表。Runtime 不下载词表，也不按模型名称猜测 tokenizer。系统按完整模型响应只计一次思考回放。校准读取同一已完成 ModelAttempt 的 Provider Usage 和冻结 Plan 估算，并核对估算器版本及完整 ModelProfile Hash。新快照还保存连接配置指纹，指纹不包含 API Key 或展示名称。同 Session 的新 Run 可以复用同配置请求的配对；不同模型、端点、协议、推理和请求配置不能混用。缺少连接指纹的旧快照只在当前 Run 内配对。预览、重复构建和重启不改变配对。旧估算器的 Plan 仍可读取，但不参与新校准。新 Plan 的 diagnostics 记录估算器版本，omissions 记录清理的工具调用。Plan 的可选计量来源与分项预算在缺省时不写入序列化，因此旧 Plan/Snapshot Hash 保持兼容。

在线 compactor 先生成确定性的有界候选，再通过当前 Run 已持有的 ModelClient 请求模型辅助说明。这个独立请求只暴露 `submit_context_summary` 输出 Schema，不提供可执行工具。请求最多等待 60 秒，请求中的输出参数最多为 4,096 Tokens，输入有 96 KiB 和模型窗口双重限制。Provider 对思考 Tokens 的统计方式仍以实际 Usage 为准。模型逐条返回分类、来源 Item ID 与精确引句。事实、决定和验证说明必须摘取原文；模型只能改写明确标记的提议、计划和未验证报告。Runtime 拒绝未知来源、无效引句和不符合 Schema 的结果。Runtime 会把 Assistant 自述、失败工具的确认声明和来自非用户消息的决定降为未验证说明。模型请求失败、超时、不支持结构化工具输出或预算不够时，Runtime 使用规则候选。

摘要模型的单条来源使用最多 8 KiB UTF-8 预览。较小资料完整提供，长资料保留头尾和明确的省略标记；全部来源继续受 64 KiB 预算约束。相同路径的不同内容版本分别取材。后续压缩先为已有延续记录重新加载原始来源，再选择新增资料。请求包含有界的当前用户目标；旧摘要只提供背景，不成为独立证据。模型不能引用只存在于预览省略标记中的句子。

模型接受的说明保存为 `CompactContinuityNote`，字段包含说明类型、来源类型、精确引句，以及 Runtime 从原记录派生的工具名称和 Call ID。确认类说明只保存引句；模型改写文本只用于提议、下一步和未验证说明。文档、源码和用户报告不能成为实际执行验证。`verification` 必须引用已完成、成功退出且结果完整的 Shell 输出；这仍是当时执行的历史证据，不证明当前文件状态或完整测试套件已经通过。持久化验证器会再次检查来源、引句、类型和回读引用。

延续记录独立于可裁剪的过程叙述。后续摘要必须保留上一份有效记录和用户约束，系统不会用“保留前四条与最后十二条”的规则裁剪它们。模型未选中的 Assistant 历史报告继续留在规则叙述中，随后仍受普通叙述预算约束。旧版字符串模型说明继续保留，但模型投影会将它们标为未重新验证的历史报告。长用户历史的预览保留头尾和 `read_tool_output(stream="context")` 原文入口。

延续记录仍计入 512～4,096 参考 Tokens 与 24 KiB 的摘要正文预算，并限制为最多 128 条；单独的模型展示部分不超过 32 KiB。系统不会为了接纳新摘要而删除已接受的记录。超限时，本次候选不提交，上一份摘要和原始历史保持不变，Engine 继续使用原有恢复策略。普通模型输入和更小的恢复视图都保留有界的延续记录，ContextPlan 将其计入摘要分项。

新记录复用 `summary_metadata_json`，其 `continuity_version=1`；对应验证记录使用 `compaction_version=2`。缺省字段不写入旧摘要，因此旧版摘要和已保存 Hash 继续读取。未知版本或无效记录 fail closed。系统没有新增表、SQLite Schema、公开 RPC 字段、依赖、后台 Worker 或第二套状态权威；旧 Runtime 不支持新增延续格式的降级读取。

模型不能改写确定性的目标、明确约束、成功工具、工作区变更、失败记录、待审批和不确定副作用字段。候选摘要不会直接成为模型事实。Runtime 会从 `state.sqlite` 重载事实，再执行 `ContextCompactionVerifier`。Tool provenance 由 source Item IDs 解析到真实 ToolCall IDs。只有验证通过的 `VerifiedCompactSummary` 才会在同一个事务中写入权威 `compact_summaries`、增加计数并产生 `context.compacted` Event。Outbox 会投影 `context/compacted` 通知，Desktop 随后重新读取 Context Usage。事务再次核对取消、工作区版本、reconciliation epoch、上一摘要计数和新用户输入。原始历史与上一份有效摘要会在验证失败、取消或来源过期时保留。

压缩的处理过程记录复用同一批 SQLite Event/Outbox。`ContextCompactionActivity` 是显示投影，不进入模型 Item 历史。`context/compactionUpdated` 发送开始和结果；Engine 在提交开始事件后、请求摘要模型前交付 Outbox，让 Desktop 能及时显示正在压缩。`session/read.contextCompactions` 从当前历史页对应的事件重建记录，并以原始开始时间排序。Renderer 按 Run 和 request ID 合并，使用 event ID 拒绝迟到状态，终态不会被重复开始通知覆盖。压缩行与既有工具行共用样式，但没有交互或详情区。系统没有新增数据库表或后台 Worker。

`context/details` 返回计量来源、参考分项、近期请求误差样本及最近压缩记录。分项按参考词表构成分配当前总量，并明确标为估算，不冒充 Provider 的分项实测。`context/compact` 把手动请求写入既有 SQLite Event/Outbox，同 Session 的重复待处理请求会去重。Engine 在下一次安全采样前处理；终态 Run 的请求由同 Session 下一 Run 处理。请求不创建额外 Run 或 Worker。开始与结束记录包含触发原因、策略、回退原因、前后完整投影预算、耗时和摘要模型 Usage。成功摘要与成功结果在同一个事务内提交；摘要已提交、后续统计未完成时，系统仍显示成功，后预算可以为空。Runtime 中断后，未提交摘要不会重放。Desktop 会把终态 Run 的未提交记录显示为中断，并允许重新请求。以上信息复用 Event JSON，不增加 DB Schema 或第二套状态机。

Compactor 只向验证层提供摘要覆盖的 ToolCall 与工作区变更来源，并按完整响应保留近期记录。摘要的 completed_actions 只记录已成功的工具结果；Assistant 的决定自述明确标记为报告，普通将来计划不会成为已完成事项。不确定工具副作用使用原始 Item/ToolCall 的短引用，系统从持久来源重建这些引用，不反复复制旧错误正文。明确的用户历史报告不会仅因模型没有选择它而被删除。摘要预算随输入窗口调整到 512～4,096 个参考 Tokens，并受 24 KiB 正文上限约束。裁剪优先保留新事实，不通过删除明确约束、未解决问题、待审批或不确定副作用来强行满足预算。关键事实放不下时，系统保留上一份摘要。持久化事务再次检查取消请求，迟到摘要不能在取消后提交。软压缩失败日志只包含 Run ID 和受控原因。

## 7. Model Gateway

Runtime 锁定 `pydantic-ai-slim[openai,retries]==2.57.0` 和 `openai==3.26.0`。OpenAI SDK 版本满足 Pydantic AI 的最低要求。锁文件只同步升级必需的 `pydantic-graph` 与 `genai-prices`。新建 `ModelProfileSnapshot` 记录 Pydantic AI 2.57.0；读取历史快照时，Runtime 保留其中已记录的版本。该升级不修改 SQLite Schema、模型配置格式或 Eidos Loop。

ModelConfigStore 使用受保护的 `models.json` 保存本地配置。默认位置是 `~/.eidos/models.json`，显式数据目录会改变 Runtime-owned 数据位置。文件按 owner-only 权限保存。

API Key 会经过本地模型配置写入链路：Renderer 通过 typed IPC 把配置交给 Electron Main，Main 再通过 `model/create` 或 `model/update` JSON-RPC request 把 `apiKey` 发送给 Runtime。Runtime 将 Key 写入受保护的 `models.json`。Key 不应出现在模型列表/读取响应、SQLite、Event/Execution Feed 或正常日志中。

当前 Model Catalog 只包含以下 Provider 和 Model ID：

```text
deepseek: deepseek-flash
minimax:  MiniMax-M3
kimi:    kimi-k3, kimi-k2.7-code-highspeed
volcengine: deepseek-v4.1-flash,
            deepseek-v4-pro-ga-260813, deepseek-v4-flash-ga-260731,
            glm-5.3, glm-5.3-flash, minimax-m3
```

Catalog 为每款模型声明思考控制项和默认值。`关闭`表示不启用 thinking；`思考`表示启用 thinking，但不提供强度档位。表中的档位是 Eidos 的选择项，不代表不同 Provider 使用相同的请求字段。

| Provider | Model ID | 可选项 | 默认值 |
|---|---|---|---|
| DeepSeek | `deepseek-flash` | 关闭、Low、High、Max | High |
| MiniMax | `MiniMax-M3` | 关闭、思考 | 思考 |
| Kimi | `kimi-k3` | Low、High、Max | Max |
| Kimi | `kimi-k2.7-code-highspeed` | 固定思考，无强度选择 | 思考 |
| Volcengine Coding Plan | `deepseek-v4.1-flash` | 关闭、Low、Medium、High、Max | High |
| Volcengine Coding Plan | `deepseek-v4-pro-ga-260813` | 关闭、Low、High、Max | High |
| Volcengine Coding Plan | `deepseek-v4-flash-ga-260731` | 关闭、Low、High、Max | High |
| Volcengine Coding Plan | `glm-5.3-flash` | Low、High、Max；thinking 固定开启 | Max |
| Volcengine Coding Plan | `glm-5.3` | 固定思考，无强度选择 | 思考 |
| Volcengine Coding Plan | `minimax-m3` | 不提供 Eidos 控制 | Provider 默认值 |

直连 Provider 的公开文档分别说明了不同能力。DeepSeek 使用 `thinking` 开关和 `reasoning_effort`；Low、High、Max 是有效档位，`minimal`、`medium`、`xhigh`、`ultra` 会映射到重复的实际档位。Kimi K3 使用顶层 `reasoning_effort`，可选 Low、High、Max，默认 Max，并且不能关闭思考。Kimi K2.7 Code HighSpeed 固定开启思考，没有公开的强度档位。MiniMax M3 使用 `thinking.type` 开关，`adaptive` 表示启用，`disabled` 表示关闭，省略时默认启用；没有离散 effort 档位。MiniMax 的 `reasoning_split` 只控制思考内容的返回格式，不控制思考开关。参考：[DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)、[Kimi Reasoning Effort](https://platform.kimi.ai/docs/guide/use-reasoning-effort)、[Kimi K2.7 Code](https://platform.kimi.ai/docs/guide/kimi-k2-7-code-quickstart)、[MiniMax OpenAI SDK](https://platform.minimax.io/docs/api-reference/text-openai-api)。

火山引擎 `deepseek-v4.1-flash` 会通过 OpenAI-compatible Chat Completions 的顶层 `reasoning_effort` 发送 `none`、`low`、`medium`、`high` 或 `max`；Catalog 默认值为 `high`。方舟公开 [Chat API 文档](https://docs.volcengine.com/docs/ark/chat-api?lang=zh&redirect=1)说明了该字段和这些取值。Coding Plan `/api/coding/v3` 对此模型的实际接受情况还没有受控请求验证。其他 Coding Plan 模型的思考设置仍未映射。

`run/start` 接受所选模型允许的思考设置。Runtime 会拒绝该模型未声明的设置；请求没有设置时，Runtime 使用 Catalog 默认值。Runtime 将解析后的设置放入 Run 的不可变 `ModelProfileSnapshot`，并写入现有 `runs.model_profile_json`。这项改动不增加 SQLite 列，也不改变 schema 版本。

`models.json` 是模型配置的事实来源（遵循“用户配置 > Pydantic AI Model Profile > Eidos Provider Preset > 保守默认值”原则）。ModelConfigStore 负责从 `models.json` 读取模型配置，校验文件权限、格式与 ID 唯一性。内置 Catalog 提供新建模型的默认值。已有配置未保存思考档位时，公开模型列表会回退到 Catalog 档位；已有非空设置优先。本逻辑只影响读取，不改写 `models.json`。Catalog 不再强制全等校验。当前内置 Model Catalog 使用 OpenAI-compatible Chat Completions。Chat Completions 是不支持 Responses、Custom Tool 或 Grammar 的模型的兼容路径，不是废弃路径。Runtime 另外保留一个按 ModelProfile wire API 路由的 OpenAI Responses native adapter。Responses profile 使用这个 adapter；只有 `supports_custom_tools=true` 且 `supports_tool_grammar=true` 的 profile 才会暴露 native Custom `apply_patch`，其他 Responses profile 仍发送 Function Tool。Chat Completions profile 继续使用 Pydantic AI 的 Function Tool API。Responses stream 只有 `response.completed` 可以产生可执行的 normalized response。`response.failed`、`response.incomplete`、`error` 和没有 terminal event 的 EOF 都 fail closed。模型请求取消覆盖流式上下文建立和 SSE 等待阶段。流建立后，Runtime 关闭 Responses stream，并取消等待中的 `anext` task。两条路径都复用 RuntimeAsyncKernel 的同一 asyncio loop。Runtime 不提供后台主动 capability probe。`model/discoverCustom` 是设置页用户主动触发的自定义连接列表请求，且只接受 `provider="custom"`。该方法通过受管任务进入共享异步内核，调用官方 OpenAI SDK 的模型列表接口，并限制时间、字节数和条目数。该方法不会生成模型回复，也不测试内置 Provider 或已有模型。

自定义配置复用 `models.json` 的数组格式。`ModelConfig.id` 继续作为会话、定时任务和子任务使用的本地身份；可选 `modelId` 保存远端 ID。可选 `custom` 保存协议、Token 上限和思考参数格式，名称、URL、凭据和能力继续使用原字段。公开 DTO 将这些字段组合成可编辑的 `CustomModelSettings`，不返回凭据。新建自定义配置使用独立随机 ID；已有配置保存时保持原 ID。旧配置缺少这些可选字段时继续采用原目录行为。Run 快照的 `model_id` 保留本地身份，`remote_model_id` 保存自定义远端 ID。Gateway 的请求规格与 SDK 模型使用远端 ID。

`CustomModelSettings.providerId` 与持久化 `CustomModelRuntimeSettings.provider_id` 保存用户指定的提供商标识，名称继续由 `vendor` 保存。该字段对旧配置可缺省，不创建独立提供商实体，也不改变内部 `provider="custom"` 的适配器路由。Renderer 在新建及编辑保存时要求有效 ID，并负责名称跟随、Token 预设、紧凑能力勾选和默认思考强度。Python DTO 继续生成 TypeScript、JSON Schema 与 Ajv 静态校验器。表单移除了免 Key 勾选，通过输入修改状态区分保留旧密钥和显式清除；地址改变且没有新 Key 时，表单发送空 Key，原有 Runtime 地址与凭据检查仍然生效。

自定义模型发现仍使用原来的有界 SDK 列表请求。远端元数据模型通过 Pydantic 按字段处理无效值，模型 ID 继续严格校验。名称支持明确的显示名别名，思考设置支持已声明的配置或强度列表，包括 `supported_reasoning_levels` 中的字符串和 effort 对象；未知强度不会使整条记录退回仅有 ID。发现 DTO 的字段不变。`ModelReasoningConfig.defaultSelection` 与公开模型 DTO 允许 `null` 或省略，表示保留支持档位但不设置默认请求强度；旧配置的明确默认值仍然有效。配置文件保存空默认值，协议沿用省略空值的规则，接收端将省略与 `null` 都作为自动处理。Runtime 会校验显式选择属于支持列表，省略选择时将空默认值保留到 Run 快照，不发送 effort 参数。Renderer 选择另一个模型时重新填入元数据并采用自动默认值，刷新同一个模型时保留手动覆盖。公共模型选择器为这类配置增加自动档位，并使用已有的空请求选择语义。Token 输入框显示和编辑实际整数，预设列表保留 K 标签，选中预设后显示对应整数；显式 K/M 简写仍按倍率解析。持久化和 Run 快照继续使用整数 Token。

图像输入能力由发现解析器按 `input_modalities`、`architecture.input_modalities`、`modalities.input` 的顺序读取，已有明确列表（包括空列表）优先。缺少字段或字段无效时，解析器继续读取后续来源；缺省的 architecture 不再等同于明确的空列表。解析器只把输入列表中的 `image` 映射到现有 `DiscoveredModel.supportsImages`，不读取输出列表，也不新增协议字段。Renderer 继续通过原有模型选择回填逻辑更新创建和编辑表单，并保留同一模型的手动覆盖。

每个 Run 固化 Model Profile 和 Extension Snapshot。Model Lease 使用该快照创建 Provider Client。Model Attempt 保存 usage、响应元数据、有限的 transport retry 诊断和稳定 Eidos 错误码。Model Client 不拥有 Runtime Event Loop；共享 RuntimeAsyncKernel 负责其异步 I/O。

Session 标题生成继续走独立的 Managed Task 和 Model Lease。Chat Completions 与 Responses 都使用 `TITLE_SYSTEM_INSTRUCTIONS` 和同一用户消息模板。模板把首条任务原文放在 `<task>` 中，并在任务前后要求模型只生成标题。系统提示将任务内的指令视为命名材料，要求模型不回答、不执行任务，也不描述工具或环境。标题请求继续关闭工具，不加入正常 Run 的聊天上下文。此处只调整提示词，不改变输出预算、标题清洗、失败回退、事件或持久化规则。

Schema v22 为 ModelAttempt 增加内部 `response_replay_json`。Runtime 在完成 Attempt 的同一次条件更新中保存有界的思考回放记录；该列引用现有 gzip Blob，并参与 Blob 清理时的引用保留。记录保存原始思考内容、Provider 字段身份、签名、元数据和该响应声明的 ToolCall IDs。Responses 路径还保存返回的原生 reasoning item，包括已返回的 encrypted content。记录不成为 Session Item，也不进入普通 Feed 或公共 ModelAttempt DTO。Eidos 不把“隐藏思考 UI”当作“不保存思考”。

ContextBuilder 从 SQLite 的已完成 Attempt 读取回放记录。Model Adapter 将同一响应的思考、正文和全部保留的 ToolCall 合并，再交给 SDK 转换；工具结果跟在完整的调用声明之后。回放元数据进入现有 Context/Step Resolution 快照与预算。快照保存 Eidos 模型请求表示，不等同于 SDK 最终 HTTP payload。上下文只保留部分响应、切换不兼容 wire API，或旧记录缺少思考且当前模型启用了思考时，Adapter 将这组历史正文、调用和结果转换成明确标注的低权限历史资料。Adapter 不伪造丢失的思考，不重新执行工具。Schema v21→v22 只新增 nullable 列；旧数据保留 NULL。

Chat Completions Adapter 将同一次 assistant 响应的全部 native `ToolReturnPart` 放入一个 Pydantic AI `ModelRequest`。SDK 先输出该批次的全部 `tool` 回复，再追加图片对应的 `user` 消息。Adapter 在发送前核对调用 ID 和工具名，并拒绝缺失、重复或没有对应调用的结果；一个批次未完成时，Adapter 不接受新的普通用户消息或模型响应。缺少思考或不兼容的历史响应继续使用已有的低权限历史资料表示。Adapter 只调整请求编码，不改变工具执行、权限、结果提交或恢复流程。

Model Adapter 为本地工具批次编码失败记录固定原因。Provider 的 HTTP 400、404、422 拒绝会进入本地日志，记录状态码、异常类型、固定原因和有界 request ID。Adapter 只识别有界错误文本中的已知原因，不保存 Provider 错误正文，以免正文回显私有输入。公开错误码、重试规则和数据库结构保持原有契约。

## 8. Tool Execution

Tool Registry 保存 ToolSpec、输入和结果 Schema、Execution Policy、Concurrency Policy、Projection Policy 和 provenance。MCP 输入 Schema 接受常见 draft-07/2020-12 `$schema` 元数据，但不解析远程 `$ref`；省略 `additionalProperties` 的对象在 Eidos 内按有界闭对象处理。每个 Step 固化可见 Tool Set、Contract Hash 和 Extension Snapshot。

Runtime 对每个 ToolCall 执行以下阶段：

```text
Validate → Prepare → Permission Decision → Durable Intent
→ Execute → Verify → canonical ToolResult → Event / Context projection
```

ToolExecutionController 负责 ToolCall 的生命周期、deadline、cancel 与迟到结果仲裁、结果校验、敏感扫描、Projection 和事务提交。`apply_patch` 不执行敏感内容扫描，但仍执行输入类型、Patch 语法、Workspace 边界、权限、版本、受控写入和最终内容校验。Workspace mutation 会在 Prepare 阶段读取当前文件，并生成 Base Hash 和完整 Diff。Workspace Permission 会直接授权普通文件变更。Runtime 会先提交 Durable Intent，再复检版本并受控提交已有文件的原地写入或新文件的排他创建。Runtime 会保留并展示已应用的完整 Diff。未知副作用会保留 `sideEffectsMayExist` 和 `reconciliationRequired`。Shell process manager 在 Run 内保留进程和有界输出读取任务。`run_shell` 可以先返回运行状态，模型随后使用 `write_stdin` 继续等待。Run 取消或收尾会清理原进程组。

Runtime 在每个 Step 的 `runtime-permissions` 指令中投影 Shell 默认启动目录。该路径来自 Run 已固定的 `workspace_identity.path`，与 Shell 执行器使用的工作区一致。提示明确说明：Runtime 在执行 `command` 前已经设置目录；模型省略 `cwd` 或传入 `.` 就使用这个目录。有项目、无项目和完全访问 Run 都使用这条规则。该提示不依赖只在有项目会话中出现的 `workspace-environment` 消息。

`run_shell` 的主描述说明 Shell 启动时已经位于 `cwd`。`command` 字段说明相对路径以 `cwd` 为起点；`cwd` 字段说明默认目录、相对路径和绝对路径的规则。Runtime 提示要求模型在当前工作区直接执行命令，并用 `{"command":"pwd"}` 展示无需 `cd` 的调用。模型需要其他启动目录时使用 `cwd`；命令执行过程中确实需要切换目录时仍可以使用 `cd`。每次调用创建新的 Shell，上次调用中的目录切换不会改变下次调用的默认目录。

这次调整只改变模型收到的目录事实和调用说明。Runtime 不解析、删除或追加命令中的 `cd`。参数默认值、路径身份核验、权限判断、进程启动和持久化行为保持不变。工具说明参与既有 Tool Contract Hash，新构建的 Step 上下文使用当前说明，历史快照保持原样。提示不能保证所有模型都省略冗余 `cd`，实际效果仍需真实模型调用验证。

Shell 参数通过 Pydantic `AliasChoices` 兼容 Codex 拼写。`run_shell` 接受 `cmd`、`workdir` 和 `yield_time_ms`，并归一化为 `command`、`cwd` 和 `yieldTimeMs`；两个名称同时出现时，原有字段名优先。`write_stdin` 接受 `session_id` 和 `yield_time_ms`，并归一化为原有字段。模型可见 Schema 仍使用 Eidos 名称，原始参数继续单独保存。参数归一化后，调用继续经过现有目录身份校验、权限判断、Durable Intent 和结果提交。非负整数等待值会被限制到有效范围，负数和错误类型不被转换成有效等待值。`write_stdin` 在未指定等待值且输入非空时默认等待 250 毫秒；空输入轮询仍默认等待 30 秒。两种调用都继续使用已有进程、输出限额和清理流程。

`search_text` 在 Run 内把 Ripgrep 进程寿命和 ToolCall 等待窗口分开。首次调用默认等待 10 秒，`search_text_wait` 默认等待 30 秒。等待窗口结束只返回 `search_running` 和 `sessionId`。后台搜索继续使用原来的受管 Ripgrep、授权根、ignore 规则、文件验证、输出边界和结果解析。搜索完成后，续接调用读取同一个有界结构化结果。Run 关闭时，`ToolExecutor` 会取消并回收未完成的搜索。搜索会话不持久化，也不能跨 Run 使用。

已声明 Tool 的载荷类型正确但参数契约校验失败时，Runtime 会在 Prepare 前生成并提交 `invalid_arguments` Tool Error。该 ToolCall 仍然进入 SQLite、Event 和下一次 Model Context，但不会触发 Approval、Durable Intent 或 Tool Runtime。载荷类型错误、当前目录中不存在的 Tool、重复或无效 Call ID 等协议错误仍然进入 protocol repair。

当前目录中存在但尚未进入 Step 激活集合的 deferred Tool 返回普通 `tool_not_activated` Tool Error。结果明确说明没有执行工具，并要求模型使用工具完整名称调用 `tool_search`，收到搜索结果后在后续 Step 重试。结果复用既有校验、投影、SQLite/Event 提交和下一轮 Context 路径，不计入协议修复次数，也不单独结束 Run。该分支不执行 Tool Runtime、不调用 cleanup、不触发 Approval 或 Durable Intent，也不建立 reconciliation。未激活调用不会进入并行读取批次。Step 的工具可用集合仍固定；同一批次先搜索也不能立即执行新激活工具。未知、禁用或未授权而未进入目录的工具不被描述为“激活后可用”。等价重复仍由既有 LoopGuard 收敛。

`list_files`、`read_file`、`read_file_range` 和 `search_text` 的 Contract 只校验参数类型、大小和明显非法语法。ToolExecutor 的只读 Path Authority 再把相对路径绑定到 Workspace，把 canonical absolute path 绑定到 Workspace 或当前 Run 的 active Skill root。结果投影也遵循这个 authority：Workspace 结果保持 Workspace-relative，active Skill 结果返回 canonical absolute path，目录结果保留末尾 `/`，因此只读 Tool 结果可以直接 round-trip 到下一次只读 Tool。active Skill root 只读。未授权的 absolute path 返回普通 Tool Error，不进入 Tool 参数契约错误或协议修复。写入 Tool 使用 Workspace-relative 路径；`run_shell.cwd` 还接受 Workspace 内的 canonical absolute path，并在 shell launch boundary 归一化为 Workspace-relative 路径。active Skill root 和 Workspace 外路径不能成为 Shell cwd。

`apply_patch` 的 Function 和 Custom 路径统一使用 Codex Patch 文本。Function 的 `ApplyPatchInput` 只接受一个非空字符串字段 `patch`；Custom 直接接收 raw Patch。两条路径共用操作说明、`parse_patch` 和 Workspace prepare/commit，不再保留结构化 `changes/chunks` 输入或编码层。说明包含 Add、Update、Delete、Move、空文件、上下文、EOF 和最小示例。Function 说明明确标记历史 `changes` 格式已停用；Custom 说明明确禁止 JSON 包装。Parser 不读取 Workspace，也不执行匹配或写入；它把文本转换为 Eidos 的 Add、Update、Delete AST。统一的 Workspace write resolver 把 Workspace canonical absolute path 归一化为内部 relative path，并同时处理 source 与 move destination。Workspace 外的 canonical absolute path 会先经过现有 PermissionPolicyEvaluator 和权限物化检查，再准备完整 Diff。现有 ApprovalCoordinator 为本次路径、Diff、版本和权限请求建立持久审批；有效 Run Grant 可复用。审批通过后，作用域重新验证目录身份，提交 helper 再核验 Base Hash。现有 Workspace prepare、写前版本检查、边界和最终校验继续负责语义和安全事实。

`ModelToolCall` 的裸 `dict` 永远表示 Function payload。Custom payload 必须显式使用 `CustomToolPayload`。`tool_calls.payload_kind` 是 SQLite 中独立且受约束的类型 authority。ContextBuilder、DB mapper 和下一次 Provider projection 都读取这个字段，不再从 `arguments_json` 猜测类型。历史 Custom call/result 在 Responses 中继续投影为 native Custom item，在 Chat Completions 中投影为有界的普通历史信息。当前 Step 固化的 Tool Definition 仍然是当前输入 contract 的唯一 authority。 `apply_patch` builtin provenance 的 `sourceVersion` 为 `2`，新的输入 schema 和说明参与现有 Contract Hash；`contractVersion=1` 仍表示未变的 ToolSpec/ToolResult envelope 版本。历史参数和结果保持原样，不转换、不重放，不迁移 SQLite。旧输入重新提交时会得到有界的 `invalid_arguments` 提示，包含当前 `patch` 格式示例和无文件修改的说明。Patch 格式错误会提示修正标记，匹配失败会提示重新读取当前文件。模型通过现有 Loop 纠正输入；Runtime 不猜测参数、不放宽匹配、不自动重试写入。v7 到 v8 migration 只在迁移边界兼容旧的 native `apply_patch` envelope。

文件编辑资源预算由 `file_limits.py` 汇集；独立提交 helper 持有单文件上限。Function 校验按工具选择参数预算，避免普通工具的 64 KiB 参数上限限制 Patch。两条 Patch 路径都验证 8 MiB 原文预算；Function 包装预算允许 JSON 转义增长。Prepare、写前版本复检、受控 helper 和写后校验统一支持 16 MiB 文件。Prepare 对旧内容、新内容和操作开销累计计费，预算为 64 MiB；完整 Diff 和 canonical result 各自有 64 MiB 预算。`apply_patch` 不执行敏感内容扫描。Runtime 没有增加自动重试、自动拆文件或自动改写 Patch 的机制。

`skill_create` 的参数预算也由 `file_limits.py` 汇集。Skill 的单文件预算为 4 MiB，完整目录预算为 8 MiB，文件数量预算为 512（包含 `SKILL.md`）。Function 参数预算为 `6 × 8 MiB + 1 MiB`，用于容纳最坏情况的 JSON 转义和有界的路径、metadata 开销；这个预算不扩大实际写入内容的上限。`SkillCreateInput` 与执行 Adapter 共用 `skill_manifest.skill_document` 生成正文，字段校验按最终生成文件计费。目录加载、文本资源读取、分页 offset 和敏感内容扫描使用同一单文件预算。内置创建工具的 `sourceVersion` 更新为 2，新输入 Schema 参与原有 Contract Hash。ToolResult、公开 RPC Envelope 和 SQLite Schema 保持不变；旧工具调用不会被转换或重放。

大文本的持久事实和 UI 消息分别处理。`protocol/tool_text.py` 为超过 64 KiB 的 Diff、结果生成内容哈希和字节数，通知及 Session 快照只携带引用和摘要。完整 Diff 继续存放在 `tool_calls.approval_diff`，完整 UI 结果继续存放在 `ui_result_json`；Runtime 内部的原始 Item 不受显示投影影响。审批指纹仍覆盖原始 Diff、路径、版本和权限，审批记录以 Diff 引用避免重复存储大文本，路径和指纹仍保留。Desktop 通过 typed preload IPC、Main RuntimeClient、`toolCall/readText` 和 SessionApplication 读取每页最多 16,384 个 Unicode 字符。Repository 从同一 Session 的 ToolCall 读取文本并核对 SHA-256，拒绝过期引用和越界偏移。接口不执行文件工具，不改变审批，也不引入新表或第二份业务状态。当前分页每次读取并校验整段有界文本；性能优化留待测量，不提前增加缓存或持久索引。

`apply_patch.lark` 的语法来源是 `openai/codex` 的 `codex-rs/core/assets/tools/apply_patch.lark`。本地 grammar 将上游的 `add_line+` 改为 `add_line*`，因为 Codex Rust streaming parser 允许没有内容行的 Add File；显式的 `+` 仍表示一条空内容行。上游 grammar 中的部分空行正则不能直接交给 Lark，所以本地 grammar 也对这些 token 做了 Lark 兼容适配。Parser 可以规范化 CRLF 和外层空白，也支持首个 Update 片段不带 `@@`。Parser 不会猜测缺失的 `*** Begin Patch`、`*** End Patch` 或 `+`/`-` 前缀。Function 使用 `{ "patch": "..." }` 包装，Custom 不使用 JSON 包装。

Tool Result 的 `reconciliationRequired` 只记录本次执行的未知结果。`sideEffectsMayExist` 保留可能副作用的证据。系统不会用这两个字段阻止后续工具、正常完成或取消。系统保留原始事实，不猜测成功，也不自动重放可能已执行的操作。

Shell 每次 ToolCall 只等待一个窗口。Shell 输出会持续追加到原命令 Item。命令退出后，Runtime 更新原命令的完整 `executionStatus`、`exitCode`、`stdout`、`stderr` 和 `termination`。普通且确定的 Shell 退出会把 Item 按命令结果标记为 `completed` 或 `failed`。确定的 `shell_exit_nonzero` 会把 Item 标记为 `failed`，但会把 ToolCall 标记为 `completed`。Workspace observation 不完整只影响 observation metadata，不会把已退出的 Shell 变成只读 reconciliation。`reconciliationRequired = true`、timeout、background child 清理未完成和真正的 Shell 启动失败仍然把 ToolCall 标记为 `failed` 并保持 fail closed。

Runtime 会记录 Shell 和其他工具的未知结果。系统不会因此建立后续操作的核验门，也不会同步扫描 Workspace 来解除这类执行限制。系统不会自动重放未知操作。后续操作仍执行各自的权限、身份、版本和结果校验。

Runtime 内部维护经过校验的 Python、ripgrep、Python import roots 和受支持包版本的 Catalog。内置依赖发现工具已移除。`run_shell` 对识别到的 Skill 脚本自动选择该 Skill 声明的 binding，并复用现有校验、权限和启动前复检。普通项目命令保留原环境，激活 Skill 本身不会切换环境。`dependencyBindingId` 输入保留兼容，显式值仍校验 Run 和 Skill 归属。

Bundled Runtime dependency 使用三层资源边界。Skill 内容层保存 `SKILL.md`、`agents/eidos.yaml`、脚本和其他 Skill 资源。产品依赖层保存 `resources/runtime-dependencies/` 中的 Python 与 Node 锁定输入，并把构建结果放入 Bundle 的 `dependencies/` 目录。Runtime manifest 层在 Bundle 根目录生成并校验 `runtime.json`，文件记录固定版本、相对路径和 SHA-256 inventory。构建和发布通过 `scripts/generate-runtime-manifest.mjs` 执行逐文件 Hash 和完整 inventory 覆盖校验。Python Runtime 已删除对应的文件 Hash、递归扫描和严格模式分支，不保留第二套全量校验。Catalog 层在日常 Runtime 初始化时读取清单，并检查版本、包 metadata、必要文件类型、owner、执行位和路径范围；它不检查依赖文件内容 Hash，也不递归遍历目录。Runtime 按目录复用路径规范化结果，一次建立 canonical path 查找表，再直接查找包文件，避免重复遍历。Shell 启动前重新检查清单与必要路径，拒绝清单变化、缺失文件、路径越界或执行位丢失。Activation 层在 Run 中固定 manifest hash、Catalog snapshot hash 和 Skill requirements，并为每个 active Skill 生成 `dependencyBindingId`。Resource 层把已验证 binding 投影到现有 Shell environment 和 permission profile。它把 Bundle root 和依赖 root 设为受保护的只读路径。它把 Workspace 保持为唯一的工作目录。

`runtime.json` 只描述随 App 发布的系统依赖。Python 依赖位于 Bundle 的 `dependencies/python`，Node 依赖位于 Bundle 的 `dependencies/node`。Skill 的 `agents/eidos.yaml` 只保存 typed declaration。Skill 目录不承载依赖包。Node loader 使用 Bundle 的 Node 和 `node_modules`，并为 CJS 与 ESM 保留各自的相对导入语义。普通没有 binding 的 Shell 仍走既有 `run_shell`、Host shell snapshot、Approval 和 Seatbelt 链路。

已有普通文件的候选内容通过 stdin 有界传入受控 helper，不生成临时文件。helper 使用 `O_NOFOLLOW` 打开并核验目标，检查当前内容哈希后执行 `ftruncate`、完整写入、`fsync` 和内容/路径身份核验。Runtime 不再克隆已有文件，也不再复制或逐字节比较 xattr/ACL。文件保留原 inode，系统管理现有元数据。真实 ACL 或 mode 拒绝写入时，Runtime 不会通过替换 inode 或 chmod 绕过限制。Runtime 仍然拒绝 symlink、hardlink、特殊文件、owner 不匹配、特殊 mode 和文件 flags。

文件提交仍使用现有 Seatbelt helper；外部路径使用物化后的附加权限，Sandbox 的 Workspace Root 仍是原工作区。已有外部文件只申请该文件的写权限；创建新文件时才申请最近的已有父目录。沙盒不可用时，Runtime 只在现有策略允许且用户明确批准后启动无沙盒 helper。审批不提供系统管理员权限。Runtime 将受保护路径变更标记为高危请求；manual 与 auto_review 都由用户确认。获批后，权限物化与 Seatbelt 只为批准范围添加例外。新文件继续使用排他 rename 提交；Add 覆盖已有文件、Move 覆盖已有目标都走原地写入。多文件不提供整体事务。截断后没有普通取消点，helper 完成当前有界文件后再停止后续修改；异常、超时或非约定 helper 退出进入不确定结果。Runtime 保留已提交的前缀变化和取消后的文件结果，不会自动回滚或重放。写前哈希检查不提供跨外部编辑器的原子 CAS 保证。外部文件的 Durable Intent 使用 `mutationScope=external_file`。Workspace 刷新不能解除外部文件的不确定状态；Repository 在事务内重新检查意图范围，避免把外部修改误标为已核实。

只有同时满足 `parallel_safe`、无副作用、参数安全和共享 Kernel 条件的只读批次可以在自身的有界范围内并发执行。每个 Run 的 Workspace write、Shell、Eidos-state、MCP 和 external Tool 实际副作用窗口使用该 Run 自己的独占门，所以同一 Run 内保持串行，不同 Session 的 Run 可以并行，即使它们共享同一个 Workspace。Approval 等待不占用该 Run 的副作用门。并发结果最终按模型声明顺序提交。

工具可以随已验证结果提供 `ToolContentSnapshot`。该内部模型保存有界文字与带 MIME 的二进制内容，不增加内容 Hash 字段。Execution Repository 在既有 ToolResult、Event 与 Outbox 事务中保存 `tool-result-content` Blob 引用；引用进入 `model_result_json`，公共结果与 UI DTO 保持原契约。ContextBuilder 从引用读取内容，冻结到现有 ContextSnapshot。Runtime 优先保留最近的工具内容，并与用户图片引用共用 16 MiB 的编码内容预算；单个工具内容的原始字节总量最多 10 MiB。预算省略会保留说明，原始结果继续保存。Blob GC 保留 ToolCall 引用和既有 ContextSnapshot 共享块。此路径复用 SQLite、现有 Blob 存储、Pydantic 与标准库，不增加数据库列或生产依赖。

工具内容快照只保存已取得的内容，不备份全部生成文件。工作区临时文件被删除或替换后，历史仍回放当时取得的内容；新工具调用继续核验当前路径与权限。旧 `view_image` 结果没有保存图片正文时，模型上下文保留元数据和缺失说明，不读取当前文件来补造历史。Chat Completions 与 Responses 都从同一内容快照编码请求。不支持的二进制类型只显示省略说明；当前产品没有因此新增音频工具或音频模型能力。已有 Blob 存储的完整性与权限检查继续使用原实现。

## 9. Sandbox & Approval

ApprovalCoordinator 把扩权 Approval request、用户 decision、feedback、暂停状态和恢复状态写入 SQLite。普通 Workspace 文件变更和默认 Workspace Seatbelt Shell 不创建 Approval。模型可以用 `networkAccess=request` 表达高层联网意图，也可以用 `gitWriteAccess=request` 请求当前已验证 repository 的 Git metadata 写入。两个 intent 可以在同一条命令中合并。Shell contract 会把 intent 规范化为现有 additional permission。Runtime 会在启动进程前请求一次 Approval，获批后仍使用 Seatbelt。旧的底层权限输入继续兼容。联网、Git metadata 写入、附加路径、unsandboxed、MCP 和 Eidos-state 副作用继续使用 Approval。Approval 不能修改 Tool 参数。受保护数据、Runtime、系统 Skill 和非专用 Git 扩权入口需要高危人工确认。批准仅开放窗口中展示的范围，其他保护仍生效。

默认 Shell attempt 使用 macOS Seatbelt。Seatbelt Policy 根据 Workspace、Runtime root、Eidos 数据目录、永久拒绝、附加权限和网络权限物化为 effective permission profile，并保存 profile hash。默认 Workspace profile 在权限校验和 Durable Intent 后直接启动。显式权限升级会创建新的 Approval attempt。用户可以通过高危人工审批允许显式 unsandboxed attempt。窗口说明命令及其子进程会使用当前 macOS 用户的权限。Runtime 不因未知副作用自动重放命令。

### Shell

HostShellResolver 先读取当前账户的 login shell，再读取 `SHELL`，最后按 `/bin/zsh`、`/bin/bash`、`/bin/sh` 顺序选择 fallback。Resolver 只接受存在、可执行且 basename 为 `zsh`、`bash` 或 `sh` 的绝对路径。

ShellEnvironmentSnapshotProvider 使用 resolved shell 的 `-lc` 做一次 bounded 环境捕获。默认 attempt 会先验证 Seatbelt profile，然后在同一个 effective Seatbelt 边界内执行 trusted capture script。捕获使用 NUL 分隔的环境格式。捕获上限是 512 KiB，超时是 10 秒。缓存 key 包含 shell executable、canonical cwd、实际 capture launch identity 和环境策略标识。缓存最多保留 64 项。成功结果在用户保存设置或重新读取环境前复用；失败回退只复用 1 秒，后续调用可以再次捕获。普通命令使用 resolved shell 的 `-c`，不会使用 `-lc`。

Agent zsh 在用户启动脚本加载后，先通过不受 alias 影响的 builtin 关闭 `NOMATCH`，再执行原命令。没有匹配文件的模式默认作为字面参数交给程序，已有匹配的通配符仍然展开。Runtime 只添加固定初始化行，不解析或改写原命令片段，不自动重试。SQLite 和 Desktop 继续保存和显示模型提交的原命令。环境继承、`pipefail`、权限、Durable Intent、取消和进程组清理沿用现有链路；bash、sh 和 Desktop Terminal 不受这项初始化影响。

每步 Runtime 提示只在 `search_text` 可用时用两句话引导模型优先使用该工具做工作区文本搜索，并通过 `includeGlobs` 传递文件过滤条件。提示不列举具体后缀，也不增加工具参数或强制改写模型的工具选择。

捕获失败时，Provider 使用 sanitized parent environment，并记录有界的稳定 warning。Snapshot 不恢复 aliases、functions 或其他 shell state。

Shell 的 effective environment 保留真实 `HOME`、snapshot 的 Host `PATH`、真实 `TMPDIR`、`USER`、`LOGNAME`、`LANG` 和 `LC_*`。Runtime 只把 bundled `rg` 的目录去重后追加到 `PATH` 末尾。Provider 在启动 login shell 前移除继承的 `EIDOS_*` 和 packaged Runtime Python control environment。用户 profile 随后声明的普通开发环境仍会进入 snapshot。`run_shell` 不从 `models.json` 注入 API Key，也不强制禁用用户的 Git 配置。`HardenedGitRunner` 仍是独立的 Git 执行路径。

Shell reader 为 stdout 和 stderr 分别保留 UTF-8 增量解码器。每次收到的字节只会生成已经可以解码的文本片段，结束时再 flush 未完成的解码状态。Shell handler 对 Live Feed 片段做 best-effort 展示脱敏，再按到达顺序追加到同一个 Item content；Shell 聚合结果保留原始 stdout/stderr。Runtime 在每次等待窗口结束后把进程 session 和增量输出交给模型。模型通过 `write_stdin` 继续等待或停止命令。内部跟进 Item 保留审计记录；Renderer 把正常跟进隐藏，原命令卡片按 `executionStatus` 显示运行状态。

Desktop `ExecutionFeed` 的 Shell Item 默认折叠。用户展开后，Feed 在 Shell 运行中和终态都渲染累计的 Item content。它在已有累计内容时不再追加结果中的最终 stdout/stderr，因此不会重复输出，也不会丢失 stdout/stderr 的接收顺序。旧 Item 缺少或为空的 `content` 时，Renderer 使用结果中的 stdout 和 stderr 作为兼容回退。待审批、已批准和已拒绝的 Shell 历史都使用这个 Shell Item，审批状态单独显示。

Shell 输出在 Renderer 中使用成熟的 ANSI stripping 实现转为纯文本。ANSI 和 OSC 控制序列不会被解释，OSC 超链接也不会被激活。Shell Result 的 `attemptCount`、`sandboxed` 和 `escalated` 字段继续作为已有执行与权限事实，并在 Feed 中展示。

`run_shell` 的模型结果投影对 stdout 和 stderr 各保留首尾，每条流最多 16 KiB，整个 JSON 最多 48 KiB。原始 `truncated` 和 `omittedBytes` 保持为原始 Shell 事实，不会被模型投影覆盖。模型投影使用独立的 `modelProjectionTruncated`、`modelProjectionOmittedBytes` 和 `modelProjectionContinuation` 字段，其中 omitted bytes 只计算模型省略的 stdout/stderr UTF-8 字节。

`read_tool_output` 是只读 Shell 输出分页工具。它使用前一次 `run_shell` 的 provider tool call ID，默认读取 stdout，也可读取 stderr。当前 Run 的活动 Shell 通过其 ShellProcessManager 读取累计快照，不移动等待游标；同 Session 的历史 Run 读取持久终态结果。跨 Run 活动结果、跨 Session、缺失和歧义 ID 拒绝。`offsetBytes`、`maxBytes`（4 字节至 16 KiB）和 `fromEnd` 控制分页；返回 UTF-8 对齐的实际 `startByte`、`endByte` 和 `nextOffset`。该工具不重新执行 Shell、不清除 reconciliation，也不能恢复原始输出上限已丢失的字节。

### 沙盒环境设置

Desktop 在现有 Runtime 页面内增加“沙盒环境设置”区块。区块只有继承方式和重新读取两个设置，说明通过小问号 Tooltip 展示。用户选择继承方式后自动保存，页面不提供单独的保存按钮、变量过滤或变量覆盖。审批窗口与 Shell 卡片保持现状。`shellEnvironment/read`、`shellEnvironment/save`、`shellEnvironment/refresh` 经 typed IPC、Main RuntimeClient、Method Registry 和 ShellEnvironmentApplication 进入现有 SQLite Repository。

环境策略默认 `inherit=all`，用户可选择 `core` 或 `none`。`core` 只透传 PATH、SHELL、HOME、TMPDIR、TEMP、TMP、USER、LOGNAME、LANG、LC_ALL、LC_CTYPE。最终策略同时约束父进程继承和 profile 捕获的变量，并排除 `EIDOS_*` 内部变量。系统不按 KEY、TOKEN、SECRET 等名称统一剔除变量。受管 Skill 的可信依赖绑定仍在其后设置受管执行路径与运行控制变量。使用 core/none 时，执行 zsh 的进程禁用第二次用户启动脚本加载，避免已排除变量被重新导出；默认 all 保留原启动行为。

每次 Shell 启动准备读取当前设置，所以保存只影响后续命令，不修改活动进程或重放命令。文件与网络权限继续使用原有物化结果。重新读取环境只清除快照，下一次命令沿用其有效沙盒重新捕获。初始读取失败时，用户也可以通过重新读取恢复。

SQLite Schema v34→v35 增加 `shell_environment_settings`，只保存继承模式和递增 revision，不保存环境变量名和值。保存执行版本比较，并与 `shell_environment.settings_changed` Event/Outbox 在同一事务提交；事件不包含环境内容，也不产生新的 Renderer 通知。升级保留旧配置和任务；回退旧版本前需要恢复升级前备份。实现没有新增依赖，没有增加环境变量 Tool 参数，没有改变文件、网络或审批权限。

### Default filesystem

默认 Seatbelt profile 允许全盘 file read、普通 executable 和 dylib 的 executable mapping。永久拒绝和 hard confidentiality deny 同时拒绝 file read、file map 和 file write。

默认 profile 只允许 Workspace、snapshot `TMPDIR` 和 canonical system temp root `/tmp` 写入。macOS 的 `/tmp` 会规范化为 `/private/tmp`。真实 `HOME` 和其他位置默认只读。

Seatbelt 默认拒绝 Eidos data 和配置的保密路径的 read、write 和 map。用户高危确认后，编译器按批准的访问类型和路径增加例外，不开放相邻数据。data 内的 projectless 或 Worktree workspace 仍可读写。active Skill root 允许 read 和 execute，但拒绝 write。Workspace 的 `.git`、`.agents`、`.eidos`、linked Worktree metadata 和 Git common metadata 默认只读。`gitWriteAccess=request` 获批后只把当前 repository 已重新核验的 Git worktree/common metadata roots 加入本次 Shell 的写权限。原 repository working tree、相邻 `.git` 和其他保护路径不会因此开放。普通 Workspace `.env` 和其他敏感命名文件可以读取；命令、cwd 和审批理由在展示层使用 best-effort 凭据脱敏，Shell 聚合 stdout/stderr 保留原始内容。默认 network 继续拒绝，只有 effective profile 启用 network 时才允许连接。明确的 network denial 不会进入通用 unsandboxed retry。

Managed linked Worktree 会把 Worktree 的已验证 `git_dir` 和 Project 的已验证 `git_common_dir` 传入 Seatbelt。这两个 Git metadata root 默认只读，只有当前命令的 `gitWriteAccess=request` 获批后才会写入。原始 repository working tree 不属于该 Thread 的 execution workspace。

Shell launch boundary 只验证 Workspace identity、cwd、Approval、Seatbelt readiness 和进程边界。Workspace-wide manifest observation 在命令执行后用于变化证据和 reconciliation。观察不完整会保留 unknown observation。它不会把已知成功退出自动改成不确定副作用。

## 10. Repository Intelligence

Repository Intelligence 已实现为独立的 typed infrastructure。它包括：

- 有界、可取消的 Repository Inventory；
- content hash、encoding、generated/vendor、git status 和 generation；
- Python、TypeScript、TSX、JavaScript 和 Go 的 Tree-sitter Query 驱动 Index；
- symbols、imports、references、code chunks 和 parse diagnostics；
- Repository Map、SQLite FTS5 文档和 RapidFuzz/FTS 混合 Retrieval；
- generation-scoped persistence、完整代和 incomplete candidate 的区分；
- Retrieval Snapshot、ContextPlan 和 ContextSnapshot 的 hash 与证据绑定。

这些基础设施由 RepositoryApplication、ContextApplication 和 persistence repositories 提供。Worktree Session 的 Repository Intelligence root 使用该 Session 的 Worktree root。Local Session 使用 `Project.workspace_root`。Repository Intelligence 不要求 Git；Non-Git Project 也可以执行 inventory、文件类型识别、Tree-sitter、symbol index、search 和 retrieval。

`RepositoryWorkspaceRuntime` 是进程级的 Workspace 生命周期边界。Session create 和 existing Session read 会快速预热这个边界。Session read 的预热是 best-effort。Local root 不存在时会跳过。Worktree 只有在 state 为 `ACTIVE` 且 execution root 可用时才会预热。`MISSING`、`INVALID` 和 `DELETED` 不会阻止 Session snapshot 返回。完成 execution binding 变更的 Session handoff 也会激活新 root。Run admission 仍然负责权威 Workspace 校验。Runtime shutdown 会停止全部 watcher。

Workspace 激活只读取 SQLite 中的 latest complete generation metadata 和 recovery status。一个完整 generation 同时包含相互绑定的 persisted Inventory、Index 和 RepositoryMap。请求路径不加载或构建这三个持久事实。没有 complete generation 时，active snapshot 保持为空。已有 Workspace watcher Worker 在监听建立后恢复或构建 immutable `RepositoryAnalysisSnapshot`，不增加后台所有者。

激活不会在请求路径中逐文件校验已持久化 Inventory 的 metadata。文件 metadata 校验只在显式 recovery 或 reconciliation 阶段执行。

`RuntimeEngine.run()` 通过 `available_for_run()` 捕获当时可用的 immutable `RepositoryAnalysisSnapshot`，不把首次索引作为模型执行门槛。空 Snapshot 时仍可使用文件和搜索工具；同一个 Run 的所有 Model Step 复用捕获的 view。显式 `ensure_ready()` 和 watcher 构建复用完整 Inventory scan 与 Index 的 previous-generation reuse，clean active generation 不重复扫描。

Run worker 启动后，Runtime 先发布 `run/started` 和用户 Item，再使用可用 Snapshot 构建上下文。首次 Repository generation 不延迟模型执行；已有 Snapshot 的有界 Retrieval 保持现有流程。

Watcher 合并 dirty path、增加 invalidation epoch，并由其已有 Worker 构建完整 generation。初次空回调发生在原生监听建立后，避免索引与监听之间的窗口。事件只是失效提示，不能代替文件事实校验。构建期间的后续事件继续触发失效；仅完整 generation 可发布，Run 已捕获的对象不被原地修改。失败构建不在 idle 回调中持续重试；后续变更或显式 refresh 才重试。并发 `ensure_ready()` 由每个 active state 的 Condition 串行化。同一 Workspace 同一时刻只有一个 Repository build。

RepositoryMap 的 manifest 读取使用 Inventory 中的 device、inode、size、mtime 和 content hash 进行 verified read。Map 捕获 Git branch 和 HEAD 后，Application 会在 SQLite commit 前用 Dulwich 再读一次。Manifest 或 Git state 在关键窗口改变时，candidate 不会成为 authoritative complete generation。完整 generation 的 Snapshot、recovery status 和 dirty bookkeeping 在一个锁保护范围内发布。

v1 mapless generation 仍然不能恢复为 active Snapshot。Persistence 会单独读取 Inventory 和 Index 的 generation watermark。首次 v2 build 会从 watermark 的下一代开始。Runtime 不会把 legacy row 当成 authoritative generation，也不会直接修改 builder 的私有 counter。

`RuntimeEngine.run()` 会在 Repository Generation ready 后，在 `ActiveRepositoryState` 的同一个锁内捕获 Snapshot、dirty paths 和 invalidation epoch。Runtime 使用这个 immutable capture 构造一次有界 `RepositoryRetrievalQuery`。Query 只使用当前用户目标、Inventory/Index 中可以确认的 path 和 symbol、已有 read/search Tool Result、capture 中的 dirty path 和最近 committed change。Runtime 对该 Run 只执行一次 Retrieval，并固定同一个 `RunRepositoryContext`。capture 后的 Watcher event 只会让 active Workspace 变脏，并由下一 Run reconciliation 观察。

`RetrievalSnapshot` 是 immutable content-addressed artifact。SQLite 只保存一份 Retrieval JSON。`run_repository_retrievals` 保存 Run 对 artifact 的使用关系。ContextPlan 继续保存 attempt lineage，但 artifact identity 不承担 Run ownership。两个 Run 可以共享同一个 Retrieval Snapshot ID，并分别解析自己的 evidence lineage。

`ContextBuilder` 是默认在线 Run 的唯一模型输入投影器。它把 Project Rules、Skills、SQLite history、verified compact summary、Repository overview 和 Retrieval evidence 放入一个结构化 `ModelContextItem` 序列。Provider 按最长公共前缀命中缓存。`workspace-environment` 位于历史之前，所以它只投影 Run 内不变的 Workspace 路径和 platform。Run 级 `workspaceVersion` 继续由 Runtime 保存，但不进入模型输入。只读结果去重使用投影历史内的 Workspace 变更计数分隔状态；该计数不进入模型提示，也不引用 Run 级版本号。这样单次 Workspace 写入不会仅因版本计数变化而使其后的整段历史失去缓存。Compaction、协议修复、权限变化和动态 Skill 仍可能改变请求前缀，不能把完整模型输入视为只追加序列。每个 ModelAttempt 在 Sampling 前持久化完整的 `ContextSnapshot`。Snapshot 原样保存 model context、resolved instructions、tool definitions、Model/Rule metadata 和可空 Repository lineage。Sampling 只读取已绑定的 Snapshot。可重试的 transport failure 会先完成旧 Attempt，再创建独立 Attempt 并复用同一个 Snapshot。敏感扫描尚未释放的文本不代表可见进度；已经发布的 Assistant Item 会参与 retry safety 判断。已有文本或 ToolCall 进度不会自动重放。协议修复会建立新的 ModelAttempt 和新的 Snapshot。已声明 Tool 的参数校验错误会通过持久化的 `invalid_arguments` Tool Result 进入下一次 Model Context。该结果只保留有界的错误码和摘要，不携带原始参数。真正的协议错误仍然使用 protocol repair context。

Workspace Explorer 复用 `RepositoryWatchController`。Watcher 事件只产生 `workspace/changed` 缓存失效通知。Renderer 根据相对路径刷新已加载的父目录。Watcher 不提供路径安全事实，也不修改 Run snapshot。

## 11. Persistence & Events

`ThreadHistoryStore` 在读取主库和历史库时，分别持有对应连接所有者的数据库锁，锁覆盖查询执行和结果读取。投影文件锁只负责串行处理历史 JSONL，不能替代数据库锁。系统在释放数据库锁后执行 JSONL 写入、校验、截断和 fsync，避免文件 IO 长时间占用主库。队列查询继续使用已有主库锁，系统不增加跨 Run 执行门或全局并发配额。历史投影保留原有事件顺序、提交偏移、启动恢复和已删除会话清理。

Runtime 启动仍执行主库及历史、日志、旧记忆辅助库完整性检查、Schema 迁移、历史投影和业务恢复。仓库索引库只在启动检查路径、权限、版本和必要表，不执行全库扫描或旧库 auto-vacuum 转换。历史快照外置检查按每批 256 条读取 ID 与 payload，取消逐条 SELECT 和全量 ID 列表；Runtime 仍验证已有 Blob 引用，并保留逐记录提交和中断后重试。

启动不再执行 Blob 全量 GC。Runtime 复用现有受管 Memory 后台任务：任务创建至少 60 秒后，且 Runtime 已初始化、控制状态为 running、没有活动 Run 时，执行一次 GC；后续间隔为一小时。该受管任务通过独立只读连接执行仓库库完整性和外键检查，不长时间占用索引查询锁。支持 incremental auto-vacuum 的缓存每次回收最多 1024 个空闲页。旧缓存不自动执行整库 VACUUM 转换。SQLite progress handler 响应取消。GC 从 SQLite 流式读取引用，持有原有 State/Blob 锁，在删除前完整解析 Context 清单并保留共享块、输入引用、手工 MCP 环境和 ModelAttempt 回放。GC 在取消时停止；引用扫描未完成时不删除文件。清理失败会记录本地日志，并按一小时间隔重试。Session 删除和隐私撤销仍执行原有即时清理。

Runtime 使用标准库计时和现有 OpenTelemetry Span，记录各存储阶段、依赖校验、Worktree 恢复与保留、Skill、模型配置、异步内核、重启核验、Handoff、Seatbelt 和待投递事件的 `phase`、`duration_ms` 与完成状态。计时不写业务 Event，也不修改协议或 SQLite Schema；初始化前的记录写 stderr，日志存储接入后的记录同时写本地 JSONL。

仓库库报告 SQLite 损坏、缺少必要表或外键不一致时，Runtime 停止使用缓存。Runtime 核验私有文件身份后，把原数据库和现存 WAL/SHM 移至同目录的 `.invalid-<id>` 文件，再创建空缓存；用户可以保留这些文件排查问题。既有 Workspace watcher 会丢弃旧 Snapshot，并在下一次空闲回调构建新 generation。未知缓存版本、Busy 或不可信路径不会授权删除文件；它们只使索引不可用。重建失败保留错误日志与不可用状态，模型继续使用普通文件和搜索工具。主库事实、业务 Event、Outbox 和恢复语义保持原样。

Session 展示快照与执行快照使用不同的读取入口。`session/read` 的首屏和历史分页不查询 Step/Rule Resolution 表，也不读取或校验 Step Resolution Blob。响应保留 `stepResolutions: []`，以兼容现有 DTO 和 Main 校验。Runtime 执行与审批恢复仍通过完整快照入口读取数据，并保留原有完整性校验。这项读取路径调整已修订代码，测试与性能验收待执行。

`state.sqlite` 是可变业务状态的唯一权威。它保存 Session、Run、Item、ToolCall、Approval、Tool Attempt、Execution Segment、Step、Model Attempt、Durable Intent、Event、Outbox、Async Operation、Extension Snapshot、Context lineage、Compaction 和 Checkpoint。业务状态变化与 Event/Outbox 仍在同一个 `state.sqlite` transaction 中提交。

Runtime 按职责使用多个独立存储。`repository.sqlite` 保存可重建的 Inventory、Index、Symbol、Reference、Chunk 和 FTS5 数据。它只保留每个 Workspace identity 的最新候选与最新完整 generation，并使用 incremental auto-vacuum 回收删除页。`thread_history.sqlite` 只索引按 Session 分段的 append-only Event JSONL。Runtime 先 fsync JSONL，再提交文件 offset；启动时会截断未提交尾部并继续投影。`logs.sqlite` 只索引本地日志 JSONL，当前使用独立 schema v2。Runtime 会把使用 `content_sha256` 的旧 v1 表迁移为 `chain_sha256`，也会接纳已经使用 `chain_sha256` 的 v1 表。日志按 8 MiB 分段，默认总量约 128 MiB，Runtime 优先删除最旧的 sealed segment。旧 `memories.sqlite` / MemoryStore metadata 接口保留兼容。新记忆系统以 `state.sqlite` 为业务权威，正文使用 UUID 命名的私有 Markdown 文件，检索与摘要是可重建投影。来源、版本、后台任务、隐私撤销与快照引用见 [current-memory.md](current-memory.md)。

ContextSnapshot 和 StepResolutionSnapshot 使用 gzip content-addressed Blob。新写入的 ContextSnapshot 使用 `context-snapshot-v2` 清单，StepResolutionSnapshot 使用 `step-resolution-v2` 清单。两者复用同一组 `context-item` 和 `context-tools`。消息块引用已有的 `tool-result-content`，不再内嵌图片或文件内容；输入图片单独按内容哈希共享。Step 清单只保存一次消息引用，读取时 Runtime 重建 `context_payload_json` 与 `final_request_json`，再验证原有快照哈希与请求关系。旧版完整快照和旧消息块仍可读取，SQLite Schema 和公开协议不变。Blob 存储不再施加统一的 32 MiB 容量上限。各输入与工具保留自身预算；Blob 读取按记录中的实际大小分块，并继续核验 owner、mode、路径、压缩数据、JSON 和 checksum。相同内容再次保存时，Runtime 验证并复用已有 Blob，不重复压缩。Blob GC 在删除前递归解析两类清单及消息块的媒体引用；引用损坏或扫描取消时不会根据不完整引用集合执行删除。Session 删除后，Runtime 会删除对应 history，并回收不再引用的 Blob。JSONL、Memory 和 Repository 数据都不能改变 `state.sqlite` 中的业务状态。

本地 JSONL 日志保存脱敏后的异常消息和调用位置，包括显式 cause 与未抑制的 context。普通消息最多 16 KiB，异常消息最多 4 KiB，调用栈最多 16 KiB；每个异常最多记录 32 个调用位置，异常链最多记录 8 层。日志不保存局部变量和源码行。日志字段使用现有凭据扫描并补充私钥正文、Authorization 与密钥赋值脱敏；诊断生成失败时只保存固定占位信息。该信息不进入 Session 正文，也不参与业务状态和恢复判断。

当前 `SCHEMA_VERSION` 是 27。v25→v26 增加 `session_forks`、`session_history_entries`、`session_history_resources` 和 Worktree 生命周期请求校验值。迁移使用事务，旧 Session 没有继承历史。v16→v17 在事务内新增记忆作用域、版本、证据、任务、预算及隐私撤销表和索引。下文先记录 v8 存储拆分的升级链，后续版本的增量变更见对应章节。新主库不创建 Repository 表。Runtime 支持 v1→v2→v3→v4→v5→v6→v7→v8 顺序升级。v5→v6 先把 Repository generation 写入临时数据库，完成完整性检查和 fsync，再原子替换 `repository.sqlite`。Runtime 随后删除主库中的 Repository 表并使用持久 marker 执行 `VACUUM`。v6→v7 新增 `run_dependency_snapshots` 和 `run_dependency_bindings`。v7→v8 为 `tool_calls` 增加受约束的 `payload_kind`，历史正式数据默认为 Function，迁移边界只对旧的 native `apply_patch` envelope 做一次性兼容 backfill。两个表继续使用 `state.sqlite` 作为业务事实来源。中断后，Runtime 可以重新复制或继续压缩。旧 `eidos.db` 会先 checkpoint WAL、检查完整性，再原子改名为 `state.sqlite`。未知 revision、未来 revision、双主库冲突和损坏 Blob 都 fail closed。

Outbox 投递失败不会删除事实。Runtime 重启会从 `state.sqlite`、Outbox、Long Task 和 Resource 状态恢复或记录实际中断与未知结果。其他数据库和文件不参与跨库业务 transaction。

RuntimeEngine 在退出时会发布最后一次 effective-time mutation。计时更新和其他事件一样走 RuntimeEvents 与 Outbox，不会仅提交到数据库而等待下一次业务调用触发投递。

In-memory 对象只保存当前协调状态、缓存、活跃资源引用和诊断信息。它不是 Session、Run、Tool 或 Event 的第二个事实来源。

## 12. Observability / OpenTelemetry

Runtime 入口初始化进程级 `TelemetryProvider`。OpenTelemetry 是非权威 Observability 层，不参与 Run 状态迁移，也不替代 SQLite 业务事实。Telemetry 初始化、Span 写入、flush 或 shutdown 失败会被 Runtime 自身日志捕获，不应成为 Agent Loop 的状态来源。

当前 Trace 覆盖入口和三个主要执行边界：

```text
eidos.rpc (仅 stdio 入口接收请求时)
  └── eidos.run (异步 Run worker 继承入口上下文)
       ├── eidos.model.attempt
       │    └── Pydantic AI 原生模型 Span (仅 Direct Model API)
       └── eidos.tool.call
            └── eidos.run (同进程 spawn/followup 子 Agent)
```

Run Span 记录 Run、Session、Model 和终态。Model Attempt Span 记录配置 Provider、响应 Provider、resolved model、Provider response ID、响应状态、阶段、finish reason、Tool 数量、响应文本大小、TTFT、duration、transport retry 和 `eidos.model.usage.*` 用量。原生模型 Span 的标准 `gen_ai.usage.*` 用量不会与 Attempt 重复计入标准聚合。模型原生埋点仅在显式配置 Trace exporter 且 SDK 未关闭时启用，关闭内容、二进制内容和请求参数采集。SQLite 的 Model Attempt 还记录响应文本哈希和受限协议诊断 JSON。诊断 JSON 只包含错误路径、Tool 名称、Call ID、参数字段名和类型、参数字节数、契约指纹与 Tool Snapshot 哈希。它不保存原始响应或参数值，也不生成模型 Tool 参数哈希。Tool Call Span 记录 Tool 名称、Call ID、Tool status、Workspace changed 和异常状态。Run worker 从入口或 spawn/followup 调用方继承 OTel 上下文；同进程暂停后恢复的 Run 沿用之前保存的上下文，避免把唤醒它的子 Agent 当作父节点。JSONL 日志在活跃 Span 内记录 `traceId`、`spanId`。

`OTEL_TRACES_EXPORTER` 默认是 `none`。当前支持 `console` 和 `otlp`；console exporter 写 stderr，OTLP 使用 HTTP Trace exporter。`OTEL_SDK_DISABLED` 可以关闭 SDK，`OTEL_SERVICE_NAME` 可以覆盖默认的 `eidos-runtime`，`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 可以设置 OTLP Trace endpoint。

没有显式配置时，OTLP exporter 使用 3 秒导出预算，BatchSpanProcessor 使用 30 秒调度间隔。显式 OTEL timeout 和 schedule 环境变量继续生效。SDK 继续管理重试、队列和关闭。主动 flush 最多等待 5 秒。远端失败不会代替本地业务事实，也不会被报告成导出成功。

## 13. Runtime Git Worktree Kernel

Runtime Git Worktree Kernel 由 `runtime/eidos_runtime/git/` 和 `runtime/eidos_runtime/persistence/worktrees.py` 提供。`WorktreeManager` 管理 Project discovery、managed Worktree create/open/validate/list/recover/cleanup/delete、实时 Git status 和 HEAD/baseline diff。默认 Managed Worktree 根目录是 `~/.eidos/.eidos-worktrees/<worktree_id>`。`GitWorkflowApplication` 是窄的 Session use-case boundary。它解析当前 execution root，检查 Worktree、Local workspace 和 active Run，再把 stage、unstage、commit 和 Local branch mutation 交给 `GitBackend`。

当前对象关系是：

```text
Project
 ├── workspace_root
 └── optional Git capability
        └── Thread / Session
                ├── Local Execution → Project.workspace_root → Runs
                ├── Worktree Execution → Managed Worktree → Runs
                └── Projectless Local → private anchor → Runs with Runtime resources
```

新的 `project/create` 接收可选的 `name` 和必需的 `workspaceRoot`。Runtime 先通过 Project resolution boundary 校验并 canonicalize 用户选择的目录，再保存 Project 元数据。名称缺省时，Runtime 使用 canonical Workspace 的文件夹名。这个调用不创建 Session。新的 `session/create` 接收 `workspaceRoot`、`executionMode`、可选的 `baseRef` 和显式的 `includeLocalChanges`。Runtime 先通过 Project resolution boundary 校验并 canonicalize 用户选择的目录，再检测可选 Git capability。`executionMode = local` 时，Runtime 创建 `worktree_id = NULL` 的 Local Session，不创建 Git side effect，也不创建 Worktree lifecycle intent。`executionMode = worktree` 时，Runtime 要求 Git capability，通过 `GitBackend` 将 `baseRef` 解析为 immutable `base_commit`，确定 `project_id`、`worktree_id`、`worktree_root` 和 `branch = NULL`，写入 durable lifecycle intent，然后通过唯一的 hardened Git CLI `worktree add --detach` 创建 Worktree。Runtime 只复制 ignored 且命中 source `.worktreeinclude` 的文件、自动复制 ignored 的 `EIDOS.override.md` 和 `AGENTS.override.md`，以及可选的 Git patch bytes。没有 Git 时，Worktree 请求返回 typed `WORKTREE_REQUIRES_GIT`。没有显式 `baseRef` 时，Runtime 使用当前 branch；repository 处于 detached HEAD 时使用 `HEAD`。Local Run 使用 `Project.workspace_root`，Worktree Run 使用 Worktree root。
`workspaceRoot` 省略或为 null 时，Runtime 创建 projectless Session。默认工作空间是 `~/Eidos/YYYY-MM-DD/可读名称`，与 `EIDOS_DATA_DIR` 分离。Desktop 首次提交通过 `workspaceNameHint` 传递首行短文本；Runtime 统一清理名称并限制 UTF-8 字节长度。名称缺省时使用“新会话”，重名时使用 `-2`、`-3`，目录创建采用排他 mkdir。日期使用首次创建请求的本地日期，后续标题变化不移动目录。开发和测试可以通过 `EIDOS_PROJECTLESS_ROOT` 指定独立根目录。该 Session 不创建 Project、Worktree 或 Repository workspace，固定为 Local execution。Run 使用普通 Workspace permission profile，写权限只作用于当前工作空间。RunResources 仍创建文件工具、Shell、Skill、MCP 和 Plugin 资源。projectless Run 不注入 Project Rules、Repository Context 或 workspace-environment。Desktop 保留输出内容和文件预览，但 Workspace Dock 不展示 Files、Terminal 或 Review；Browser 仍可用。

Schema v23 的 `projectless_workspace_creations` 保存新工作空间的操作 ID、目录分配和身份。重复请求复用首次路径；已保存身份的创建可以在重启后继续。目录已经存在但没有已提交身份时，Runtime 不接管该目录，而是分配下一个后缀。Session、创建完成状态和 `session.created` Event/Outbox 在同一事务提交。失败清理只删除已核验且仍为空的本次目录。子 Agent 继续借用父 Session 的工作空间。

本次 Alpha 更新不兼容或迁移旧无项目 Session。Runtime 在持有 state lock、恢复持久事实后，废弃旧 Projectless 根目录下的无项目 Session 和父子关联记录，并清理对应历史投影。该过程不启动旧 Run、不移动或删除旧工作空间文件，也不触碰 Project/Worktree Session。新版不保留旧路径回退或历史文件映射。

### Local ↔ Managed Worktree Handoff

一个 Session 可以在 Local 和它自己的 Managed Worktree 之间切换。Handoff 不创建新 Session，不修改 items、runs、events、title 或 checkpoint lineage。`execution_mode` 和 active `worktree_id` 只描述当前执行绑定。`associated_worktree_id` 记录该 Session 之前创建的、以后还可以返回的 Managed Worktree。原生 Local Session 的两个 Worktree 字段都是 NULL。

Runtime 通过 strict `session/handoff` DTO 接收 `sessionId`、`target` 和 `operationId`。Handoff 需要 Session 没有 queued、running、waiting_approval 或 finalizing Run。Runtime 为每次 handoff 固化 immutable `HandoffPlan`，并在 `session_handoff_operations` 中记录 prepared、source captured、target materialized、session rebound、completed 或 cleanup required。

Local → Worktree 没有 associated Worktree 时复用 WorktreeManager 的 prepared creation、source snapshot 和 Git CLI full/staged patch transfer。当前 HEAD、staged、unstaged、untracked、binary、symlink、mode 和 committed changes 都进入目标 Worktree。Dirty submodule checkout 不在 transfer contract 内，Runtime 返回 typed `worktree_gitlink_unsupported`。已有 associated Worktree 时只验证并复用原来的 Worktree；缺失或 deleted 返回 `WORKTREE_RESTORE_REQUIRED`，invalid 返回 `WORKTREE_RECOVERY_REQUIRED`，Runtime 不创建第二个 Worktree。

Worktree → Local 先捕获两边的 Git common directory、HEAD、branch、status 和 dirty fingerprint。两边必须属于同一个 Git common directory。Local target 有未能证明安全的修改时返回 `HANDOFF_LOCAL_CONFLICT`，Runtime 不 reset、clean 或 force checkout 用户 Local。Attached user branch 会先在 Managed Worktree detach，再由 Local 无 force 地 acquire。确认 Local branch 和 HEAD 后，Runtime 只清除 Worktree 的 branch metadata，不删除或修改 Git ref。Detached source 会让 Local 保持 detached HEAD。Managed Worktree 只在 transfer 成功后执行受控清理。

`worktrees.branch` 保存 Eidos 当前管理的 user branch identity，`worktrees.checkout_branch` 保存当前实际 checkout branch。User Branch handoff 给 Local 后，Runtime 将这两个字段和 `branch_ownership` 一起清为 NULL、NULL、`none`；Git branch ref 仍然保留。Worktree 返回 background 时可以保持 detached checkout。Handoff 完成后，下一次 Run 按新的 execution root 重新构建 resolution；已有 Run 的 `RunResolutionSnapshot.workspace_identity` 不会更新。Inactive Worktree 的完成后 fingerprint 发生变化时，返回 `HANDOFF_TARGET_CHANGED`。

`sessions.workspace_root` 保存 Project workspace root，projectless Session 保存日期目录下的可读工作空间。Session 持久化的 `execution_mode` 是执行语义的权威字段，`worktree_id` 是 Worktree binding。Session projection 对普通 Session 提供 `projectId`、`workspaceRoot`、`gitAvailable` 和 `executionMode`；projectless Session 提供 `projectless = true` 和 `project = null`。因此 Desktop 不需要通过 `worktree_id` 推断执行模式。

当 `associated_worktree_id` 对应的 Worktree 为 deleted 且存在 ready Snapshot 时，Session projection 提供 `worktreeRestoreAvailable = true`。Desktop 显示 Restore Worktree action。Desktop 在 Worktree execution unavailable 时保持 Composer read-only；Local execution 可以继续使用 Local workspace。

### Managed Worktree Retention、Snapshot 和 Restore

Session history 与 Worktree directory 使用不同的生命周期。Managed Worktree directory 可以被 retention 删除。Session、Run、Event、Checkpoint 和 `associated_worktree_id` 继续保留。Worktree row 继续保留为 `state = deleted` 的历史 identity。

`WorktreeRetentionService` 只处理 `ownership = managed` 的 active Worktree。Service 读取 SQLite 的 `runtime_settings`，默认 `automatic_cleanup = true`、`managed_worktree_limit = 15`。Service 按 `last_used_at DESC` 保留最近 N 个，并按 oldest first 处理多余 candidate。Service 跳过 active Run、unfinished Handoff、unfinished lifecycle、`cleanup_required`、invalid validation、legacy managed branch、无法证明 Git/filesystem identity 和 Snapshot 失败的 Worktree。Service 不处理 adopted Worktree，也不使用 directory mtime、path 或 worktree id 作为 recency authority。

Runtime 在 startup recovery、Managed Worktree create 和 Restore 成功后执行一次 `WorktreeRetentionService.reconcile()`。Runtime 不执行后台 polling。Runtime 在 create、Handoff target 成功、Run admission、Create Branch Here 和 Restore 成功时更新 `worktrees.last_used_at`。Status、diff 和 UI polling 不更新该字段。

`WorktreeSnapshot` 的 SQLite metadata 与 artifact store 分离。Artifact store 的路径是 `<EIDOS_DATA_DIR>/worktree-snapshots/<snapshot_id>/`，目录内只保存 `full.patch.gz`、`staged.patch.gz` 和 `manifest.json`。`GitWorkingTreePatch` 只包含 raw `full_patch` 和 `staged_patch` bytes。Capture 使用 Git CLI 的成熟 diff semantics，并只读取 changed paths、untracked new-file patch 和必要的 patch metadata；它不读取全部 tracked content，也不序列化整个 Index，因此 snapshot artifact 接近 O(changed files)。Artifact 使用 gzip、SHA-256、temporary directory、`os.replace`、file fsync 和 directory fsync。Ignored environment 不进入 artifact。Restore 会从当前 source repository 重新 materialize `.worktreeinclude` 和 ignored rule override，再把 patch 交给 Git CLI apply。

每个 ready Snapshot 都使用 `refs/eidos/worktree-snapshots/<snapshot_id>` 指向 `snapshot.head`。该 ref 是 hidden reachability anchor，不是 branch。Runtime 使用 compare-and-set 创建 ref，使用 compare-and-delete 删除 ref。Runtime 只有在 patch artifact、checksum、hidden ref 和 SQLite ready row 都成功后才会删除 Worktree。Detached commit 因为仍被 hidden ref 引用，所以不依赖普通 branch ref。

Retention cleanup 使用 `worktree/retention-cleanup` lifecycle：`prepared → snapshot_saved → worktree_deleted → completed`。Restore 使用 `worktree/restore` lifecycle：`prepared → worktree_created → state_materialized → worktree_rebound → completed`。Restore 读取 `latest_ready_snapshot(worktree_id)`，验证 Project identity、artifact checksum、hidden ref 和 source fingerprint，然后通过 `GitBackend` 在同一个 root 创建 detached Worktree。Restore 设置 `checkout_branch = NULL`，保留原 `worktree_id` 和原 `base_commit`，所以 baseline diff 仍然使用原始 baseline。Restore 失败会清理新建 partial Worktree；不能证明安全时会进入 `cleanup_required`，并返回 `WORKTREE_RESTORE_REQUIRED`。

Session Delete 通过既有 `session/delete` lifecycle 清理 Snapshot metadata、artifact 和 hidden ref，再删除 Session。Hidden ref 删除必须匹配 Snapshot HEAD。User branch 不属于 Snapshot cleanup，因此 Session Delete 保留 user branch。Startup 会校验 ready row、artifact 和 hidden ref，并只清理有 SQLite ownership proof 的 orphan artifact/ref。

Project 和 Session 使用独立生命周期。`project/list` 从 SQLite `projects` 表读取 Project projection。`session/delete` 只删除 Session 及其允许清理的生命周期数据，不删除 Project row。`project/delete` 需要用户显式调用。Runtime 会先通过 Session 删除边界清理没有标题且没有 Run 的历史空 Session，再检查正式 Session、未完成 Worktree lifecycle、Snapshot 和 Handoff。检查通过后，Runtime 只删除 Project 与已完成的 Worktree 元数据，不删除 Workspace 文件、Git 仓库或 Git branch。Projectless Session 不创建 Project，因此不会进入 Project 列表。

Desktop 侧边栏通过 typed IPC 传递 `session/list` 的游标。Session Controller 按顺序读取所有页，直到 Runtime 不再返回 `nextCursor`，再更新列表。Runtime 每页默认返回 50 条记录，并用原有的创建序号上界固定本次分页范围。后续页读取失败或游标重复时，Desktop 保留上一次完整列表并显示错误。较早开始的刷新不能覆盖较新的刷新结果。项目分组、旧空 Session 隐藏和内部子会话排除规则继续生效；本次修复不修改 SQLite、Session 或 Project 生命周期。

`SessionSidebar` 的自动定位只随 `selectedId` 变化触发；项目展开状态和列表刷新不会重新触发定位。原有会话切换逻辑仍会展开目标项目，输入引用查看其他会话也使用这条链路。Renderer 只在当前侧边栏内查找匹配的高亮项，并保留可见范围检查与展开动画后的定位检查。项目折叠状态仍保存在原有 localStorage key；本次修复不增加协议或持久化字段。

项目和 Session 的置顶记录由 SQLite `sidebar_pins` 管理。Schema v25 从 v24 通过短事务创建该表和置顶事件索引；历史版本沿现有两条存储升级入口进入 v25。每条记录只能引用一个 Project 或 Session，数据库通过唯一约束和外键保证对象不重复、删除时清理。`pin_seq` 保存两类对象共用的固定顺序，新项追加，取消后重新置顶也追加。置顶不更新 Project/Session 的时间、绑定、Run 或权限。Runtime 最多保存 1000 个置顶引用，以限制协议结果大小。

`SidebarApplication → SidebarRepository` 提供 `sidebar/listPins` 和 `sidebar/setPinned`。写入请求包含明确的 `pinned` 布尔值和 `operationId`，并复用现有幂等操作记录。置顶变化与 `sidebar.pins_changed` Event/Outbox 在同一事务提交。删除已置顶的 Project 或 Session 也在原删除事务内记录这个全局事件。事件不绑定被删除的 Session，所以删除不会清掉置顶通知。`EventProjector` 将它投影为 `sidebar/pinsChanged`，通知只包含 `eventId`。读响应的 `throughEventId` 来自最新置顶事件，Renderer 重读后拒绝更旧的响应，并丢弃 Runtime 重启前的迟到请求。Python DTO 通过生成脚本提供 TypeScript 类型与 JSON Schema，Main 使用现有 Ajv 校验；Renderer 只导入类型，保持既有 CSP。

`SessionSidebar` 把置顶引用投影成位于“项目”上方的混合列表。项目组保留展开、折叠和新建会话入口。独立置顶的 Session 保留状态、Git 和项目提示，但不在项目或“最近”中重复出现；“最近”没有剩余 Session 时隐藏。项目和其 Session 同时置顶时，项目组只展示未独立置顶的子项。项目删除仍检查完整 Session 列表。内部子会话、未物化草稿和失效引用不进入置顶显示。取消最后一个置顶项会隐藏分组。普通列表沿用创建时间排序，自动定位仍只随 Session 导航触发。

WorktreeManager 保留 Eidos 的 Project、Worktree、Session、Run、operationId、SQLite lifecycle、recovery、compensation 和 Sandbox 语义。Git mechanics 通过 typed `GitBackend` 提供。Eidos 不实现 Git working-tree、Index、patch、commit、merge tree、rebase replay、conflict marker、fetch、fast-forward 或 push semantics。Dulwich 负责 discovery、HEAD、refs、branch metadata、revision、ignore observation 和 Worktree metadata mechanics。唯一的 `GitCli` authority 负责 status、file-scoped diff、stage、commit、fetch、merge、merge abort、rebase、rebase continue/abort、push、patch capture/apply、untracked new-file patch、Worktree Add 和 destructive `clean -fdx`。`HardenedGitRunner` 继续禁用 hooks、pager、interactive prompt 和 fsmonitor。Observation 和 Local Mutation profile 禁用 credentials。Remote profile 只开放受控 credential helper 和 SSH Agent。Observation command 继续禁用 executable filters。Stage 由 native `git add` 执行 repository 和受控 user global config 中的 clean/process filter。Commit、merge commit 和 rebase replay 只读取 repository-local identity，或从用户 global config 受控读取 `user.name` 和 `user.email`，再通过命令级配置传给 Git。Runtime 始终禁用 hooks。Runtime 不对失败的 Dulwich 或 Git CLI operation 自动切换另一套 semantics。Manager 不知道 subprocess result。

最终 Git 开发工作流使用单一且明确的权威边界。Dulwich 负责 repository discovery、refs 和 revision metadata。Native Git 负责 Working Tree、Index、diff、commit、remote、merge、rebase 和 snapshot patch semantics。共享 `WorkspaceReader` 负责 Desktop Workspace Explorer 的有界文件观察。Review 的 Accept/Unstage 结果以 Git Index 为事实。HTTPS 认证交给 Git Credential Helper，SSH 认证交给 OpenSSH 和受控 SSH Agent。Checkpoint 复用现有 Git snapshot artifact、checksum 和 hidden ref，不建立第二套文件快照格式。

`session/gitStatus` 返回文件列表和兼容 count。`session/gitDiff` 接受可选的 workspace-relative `path` 和 `compareRef`。显式 `compareRef` 会先解析为 commit。无效 ref 返回 `GIT_COMPARE_REF_INVALID`。Managed Worktree 的 baseline 使用创建时冻结的 `base_commit`，并返回保存的 `base_ref` 标签。Local Session 的 baseline 默认使用当前 upstream remote-tracking ref；本地没有可解析 upstream 时回退当前 HEAD。响应还返回 native Git `--numstat` 得到的 additions、deletions 和 `statsIncomplete`。二进制文件会把统计标为不完整。行数统计不依赖有界 unified patch 是否截断。这些 file API 使用 `GIT_LITERAL_PATHSPECS=1`，所以合法文件名中的 `:`、`*`、`?` 和 `[]` 不会变成 Git pathspec expression。`session/gitStage` 和 `session/gitCommit` 只在没有 active Run 时修改当前 execution root。两个 mutation 都接收 `operationId`。Runtime 先完成无副作用 preflight，再在现有 `operations` 表提交 `in_progress`。Git 在 SQLite transaction 外执行。Runtime 随后重新观察 Git，并在第二个短事务中保存 completed result。Completed retry 直接 replay。相同 id 和不同 request 返回 `OPERATION_ID_REUSED`。未完成 operation 返回 `OPERATION_IN_PROGRESS`，Runtime 不盲目重做 Git。Stage 和 Commit 是独立动作。Commit 只提交当前 Index 中的 staged changes。Detached managed Worktree 返回 `GIT_BRANCH_REQUIRED`，用户必须先调用 `session/createBranch`。Commit 后 Managed Worktree 的 `base_commit` 保持不变；baseline diff 仍保留为协议能力，但 Desktop 审查的“整个任务”不再使用它，而使用本 Session 全部 Run 的持久化补丁历史。

Local Git Session 还提供 `session/gitSwitchBranch` 和 `session/gitCreateBranch`。Runtime 从当前 Project workspace 读取 local branches。Runtime 只允许 clean、没有进行中 Git operation、且整个 workspace 没有 active Run 时切换或创建分支。`gitCreateBranch` 从当前 HEAD 创建分支并立即切换到新分支。两个方法都使用现有 `operations` 表和 `operationId` replay。当前分支仍以 Git HEAD 为事实，不新增 Session 或 SQLite 分支字段。Local Session 共用真实 workspace，所以不同 Local Session 在 Git 分支切换操作上共用这条 workspace 锁；这条操作锁不限制不同 Session 的 Run、Shell 或普通副作用并行。分支变化完成后，Runtime 会使 RepositoryWorkspaceRuntime 失效，下一次读取会重新观察 branch 和 HEAD。

Desktop Review 不解析 Git status，也不把 repository-wide patch 拆成文件。未提交范围使用 structured status 建立文件手风琴；最近一轮和整个任务使用 Session Item 持久化补丁，不使用 baseline `changedFiles`。全仓响应只用于列表、比较信息和总行数。文件展开时，Renderer 才调用 `session/gitDiff(path)`。展开全部会按顺序读取文件 Diff，避免同时发起无界请求。`react-diff-view` 负责解析和显示 unified patch。Stage 仍通过 Runtime 修改 Git Index（仅提交弹层的“包含未暂存的更改”使用）。Main 的 Open in Editor handler 从 Session 解析 execution root，再用 canonical path 和 regular-file 检查阻止相对路径与 symlink 越界。Commit、Push 和已有高级 Git 操作复用原 typed Runtime API，并放在同一个提交弹层中。

`HardenedGitRunner` 通过 `GitExecutionProfile` 区分 `OBSERVE`、`LOCAL_MUTATION` 和 `REMOTE`。OBSERVE 隔离 user global config、credential helper、prompt、hooks、fsmonitor 和 executable filters。LOCAL_MUTATION 只在 native `git add` 需要 clean/process filter 时读取 user config，credential 与 prompt 仍禁用。REMOTE 使用 user HOME 和 global Git config，所以 Git 可以复用 `credential.helper`、`url.*.insteadOf` 和用户 SSH config。REMOTE 只 allowlist 当前用户拥有的 Unix `SSH_AUTH_SOCK`，并设置 `/usr/bin/ssh -o BatchMode=yes`。Runtime 不读取或保存 HTTPS secret，也不继承任意 `GIT_*`、`SSH_*` 或其他 process environment。Remote URL 不进入 Desktop DTO。

`session/gitRemoteStatus` 使用 native Git 返回 remote name、current branch、upstream remote/branch 和 `HEAD...@{upstream}` 的 ahead/behind。配置中的 upstream identity 与本地 remote-tracking ref 是两个事实。upstream 已配置但 tracking ref 尚不存在时，Runtime 仍返回 upstream remote/branch，并把 ahead/behind 返回为 null。`session/gitFetch` 仍可根据该 identity 选择 remote。`session/gitFetch` 只接受 repository 已配置的 remote name。它优先选择 upstream remote，其次选择唯一 remote。Fetch 映射为 `git fetch -- <remote>`，不增加 prune、tags、all 或 force。Runtime 在 Fetch 后重新观察 HEAD、branch、upstream 和 ahead/behind。Fetch 使用 120 秒上限，并通过现有 `DeferredMethodResult`、managed task、async operation 和 process-group cleanup 在 JSON-RPC input loop 外执行。Shutdown 或 managed-resource cancellation 会设置 cancel signal。Runner 随后终止并回收 Git、SSH 和 credential helper 所在的进程组。本阶段没有新增公开的通用 operation-cancel RPC。

`session/gitPull` 只允许没有 active Run、attached branch、已配置 upstream 和 clean Workspace。Runtime 先执行 `git fetch <upstream-remote>`。Remote 没有领先时 Pull 是 no-op。本地只落后时，Runtime 执行 `git merge --ff-only --no-edit @{upstream}`。Diverged 返回 `GIT_REMOTE_DIVERGED`。Runtime 不读取 ambient `pull.rebase`、`pull.ff` 或用户默认 Pull strategy，也不自动 stash。`session/gitPush` 先 Fetch 目标 remote。已知 upstream 落后时返回 `GIT_REMOTE_BEHIND`，diverged 时返回 `GIT_REMOTE_DIVERGED`。已有 upstream 使用 `git push <remote> HEAD:<upstream-branch>`。没有 upstream 时使用 `git push --set-upstream <remote> HEAD`。Runtime 不使用 force、tags、all 或 branch deletion。Pull 和 Push 完成后都会重新观察 HEAD、structured status 和 remote status。Session 的 `base_commit` 不会改变。

Local Git mutation、Fetch、Pull 和 Push 都先完成无副作用 preflight，再 durable prepare operation。Preflight 只检查 Session、active Run、execution root、branch、Workspace cleanliness、path 和 remote/upstream metadata。Deferred external operation 的 prepare 在同一个 SQLite transaction 中写入 `operations` 的 in-progress reservation 和 `async_operations` 的 accepted lifecycle。任一写入失败会一起回滚。`operations` 表仍是 operationId replay 的结果权威。`async_operations` 只记录 deferred task 生命周期。Completed 和已确定失败的 operation 都会 replay 原结果或原错误；相同 id 和不同 request 返回 `OPERATION_ID_REUSED`。未完成外部操作返回 `OPERATION_IN_PROGRESS`，不会再次执行 Git。明确失败会把两个 operation 记录都收敛到 terminal 状态；Push 无法证明结果时保存 `GIT_REMOTE_OUTCOME_UNCERTAIN` 和 `sideEffectsMayExist`。

`session/gitMerge` 先检查 idle Session、attached branch、clean Workspace，以及没有已有 merge 或 rebase。Eidos 通过 Dulwich 把 local branch 或显式 revision 解析成固定 commit id。Native Git 执行 `git merge --no-edit <commit>`。成功后 Runtime 重新观察 HEAD、status 和 operation state。冲突不是通用失败。Runtime 返回 `operationState=merge` 和 structured `conflictFiles`，并把该结果写入 operationId replay。`session/gitMergeAbort` 只执行 native `git merge --abort`。Merge commit 使用与 commit 相同的受控 identity。Hooks 继续禁用。Session 的 `base_commit` 不会改变。

`session/gitRebase` 使用与 Merge 相同的 preflight 和 target resolution 边界。Native Git 执行 `git rebase <resolved-commit>`。冲突结果返回 `operationState=rebase` 和 structured `conflictFiles`，并写入 operationId replay。在外部终端修复文件并 Stage 后，`session/gitRebaseContinue` 执行 `git rebase --continue`。Runner 把 `GIT_EDITOR` 设为无副作用的成功程序，所以 Git 保留原 commit message，并且不会打开 editor。`GIT_SEQUENCE_EDITOR` 始终禁用。`session/gitRebaseAbort` 只执行 native `git rebase --abort`。Git repository 中的 rebase metadata 是唯一 operation state 权威。Eidos 不持久化第二份 merge/rebase 状态。Session 的 `base_commit` 不会改变。

Inline Review Comment 是 Eidos 产品语义，不进入 `GitBackend`。SQLite `review_comments` 是 Comment 的唯一事实来源。记录保存 Session、path、Diff scope、old/new side、line、body、观察到的 HEAD、Diff hash、active/stale 状态和时间。`review/createComment` 会先按 operationId 回放已完成结果，再重新请求 file-scoped native Git Diff，并核对 HEAD、changed file、SHA-256 Diff hash 和由 `unidiff` 解析出的 old/new 行 anchor。HEAD 或 Diff hash 变化返回 `REVIEW_DIFF_CHANGED`；side/line 不存在返回 `REVIEW_ANCHOR_INVALID`。`review/listComments` 会再次观察对应 Diff。只有 HEAD 和 Diff hash 都相同时，Comment 才保持 active。Runtime 不做模糊重定位。

Renderer 使用 `react-diff-view` 的 gutter event 和 widget extension 展示 Comment。Renderer 不修改第三方 Diff renderer。`review/createComment` 和 `review/deleteComment` 使用现有 `operations` 表提供 operationId replay。Comment 创建和删除不会启动 Run。显式 Send Review Feedback 只收集 active Comment，把它们格式化为普通用户输入，再调用现有 `run/start` 链路。

Desktop `GitWorkflowControls` 只编排现有 typed Git API。它读取 `session/gitRemoteStatus` 和 `project/gitContext.branches`，不解析 Git 输出。Commit、Fetch、Pull、Push、Merge、Rebase 和 continue/abort 都携带 operationId。传输未知或 operation 仍在进行时，Renderer 保留 operationId；已确定的 terminal failure 会删除缓存并让下一次显式点击生成新的 identity；`GIT_REMOTE_OUTCOME_UNCERTAIN` 会先提示用户检查远端状态。message、target、branch 或 HEAD 改变后，Renderer 会生成新的 identity。一个操作执行时，其他冲突操作会被禁用。Active Run、Session Handoff 和 branch attach 期间，AppShell 也会禁用这些控制。操作完成后，Renderer 刷新 structured status、当前 file Diff、Remote observation 和 mutation 返回的 operation state。Desktop 不保存 Git credential，也不改变 native Git policy。

Run 的执行 identity 固化在 `RunResolutionSnapshot.workspace_identity`。Runtime 会在启动和恢复 Run 时重新验证 Worktree、root、Git dir、Git common dir 和 inode/device/owner。Runtime 不会把 managed Run fallback 到 repository root。

Runtime 使用 typed Git observations，并在 observation failure、timeout 或 bounded diff truncation 时不更新 lifecycle state。`deleted` 是 terminal state。Kernel 不修改 Run 并发语义。

## 14. Extension Runtime

PluginCatalog 接收受控的本地 Eidos v1、Portable Agent Plugins 和 Codex 兼容包。Runtime 校验 manifest、Skill 元数据、文件数量、单文件和总大小、content hash，并把内容写入私有 extensions 目录。Plugin 可以声明 Skill、已支持的 command Hook、本地 stdio 和远程 Streamable HTTP MCP Server。`plugin_versions` 在 SQLite v37 保存已知版本身份；显式更新在同一事务切换当前版本、停用插件、清除 Hook 信任与 MCP 授权，并写入 Event/Outbox。旧 Run 的 Skill 按冻结版本和内容身份定位文件，退役版本在活动引用结束后由现有清理入口删除。手动 MCP 继续保存在独立的 `manual_mcp_servers` 表中，并与包内 MCP 共用 SDK、Tool Registry 和执行链。展示 metadata 采用 Python 生成的类型及 Schema。详细兼容与连接边界见 [current-plugins.md](current-plugins.md)。

Plugin 的安装根目录是 `extensions/plugins/<pluginId>/<version>/`。内容 Hash 仅作为内部内容身份参与 SQLite、快照、信任和完整性核验，不再作为目录层级。Runtime 不兼容旧的 Hash 子目录布局。旧安装需要清理后重新导入，历史快照不迁移。目录发布失败不会清理其他已存在的版本目录。

Skill 当前按以下边界工作：

```text
Discovery → Catalog Snapshot → Selection
          → selected SKILL.md → existing tools / run_shell
          → active Skill root → Seatbelt Sandbox
```

Discovery 读取 bundled system、用户和 Plugin Skill 目录。系统 Skill 的私有目录读取会忽略 Finder 生成的 `.DS_Store` 文件，但仍拒绝其他不符合所有者、类型或权限要求的文件。`SkillCatalog` 使用共享的 `parse_skill_manifest` 校验 `SKILL.md` 的 frontmatter。Catalog 只保留顶层 `name`、`description`、qualified ID、source、version、source hash 和 content hash。`license`、`compatibility`、`metadata`、`allowed-tools` 等其他 frontmatter 字段可以存在，但不会变成 Catalog、Tool Schema、Permission 或 Sandbox 权威。

Turn 开始时，Catalog Snapshot 固化可用 Skill。Selection 当前支持用户输入中的 qualified `@source:name` 和唯一的 `@name`/`$name` 引用。SelectedSkillSet 固化本 Turn 的选中 ID。选中后，Runtime 才完整读取对应的 `SKILL.md`。Catalog 不把完整 Skill tree 注入 Context。

Skill 使用 progressive disclosure。Catalog 只提供发现信息。`SKILL.md` 是选中后的主说明。`skill_read_resource` 只在说明需要时读取 `references/`、`scripts/` 或 `assets/` 下的相对路径。Resource path 必须是相对于包含该 Skill 的 `SKILL.md` 的目录，且不能包含绝对路径、`..`、symlink 或非 regular file。选中的 active Skill root 也可以供四个只读文件 Tool 使用 canonical absolute path，但它不会因此获得写入权限。Skill 脚本不是新的 Runtime Tool；模型仍然使用已有的 `skill_read`、`skill_read_resource`、文件工具、`run_shell` 和其他已注册 Tool。

`agents/eidos.yaml` 是可选的 Skill metadata 文件。Runtime 使用统一 YAML loader 读取其中的 interface、asset、tool dependency、policy 和 `runtimeDependencies` metadata。`runtimeDependencies` 使用严格的 `RuntimeRequirements` discriminated union。它只接受有界的 Python package、Node package 和 executable 声明。文件有大小、owner、regular-file、路径和字段边界。无效的可选 runtime declaration 会保留固定 error code，不会清除旧的 interface、MCP dependency 或 policy metadata。无效的整个可选 YAML 会保留现有 display fallback，并报告固定 metadata error。`allow_implicit_invocation = false` 会禁止 Shell 识别自动激活该 Skill，但不会阻止显式选择或 `skill_read` 激活。`dependencies.tools` 不会安装 Python、pip、npm、系统命令或其他运行时依赖，也不会改变 Eidos 的 Permission、Approval 或 Sandbox。

`run_shell` 会经过 `SkillAccess` 对受信任 Catalog entry 做隐式脚本识别。只有支持的 runner 调用已知 Skill root 下的相对 `scripts/` 文件时，Runtime 才记录 implicit activation，并把该 Skill root 加入本次 Shell 的权限物化。Workspace root 保持读写；active Skill root 默认只读，并额外允许脚本所需的 executable mapping。用户对普通 Skill 的具体写入授权可以成为这个只读规则的例外，系统 Skill 永久禁写。带 runtime declaration 的 Skill 脚本由 Runtime 根据已验证的脚本归属自动选择依赖 binding。识别器支持直接使用 `$RUNTIME_PYTHON` 和 `$RUNTIME_NODE` 调用 canonical absolute Skill 脚本；它不会解释任意 Shell 包装表达式，也不会根据已激活 Skill 列表替普通项目命令选择环境。无效或非 ready 声明不能按无依赖处理。隐式识别只提供 activation 证据，不会授权任意 Workspace 外路径。

Skill binary assets 会在安装和目录读取中按 bytes 保留。`skill_read_resource` 只返回有界 UTF-8 文本，因此 DOCX、PPTX、PDF、XLSX、PNG 等 binary resource 不会被当作文本注入 Context。支持图像输入的 Model 才会注册 `view_image`。`view_image` 在受控读取成功时捕获 PNG/JPEG 内容，并通过共同的工具结果提交边界保存。Provider Adapter 只把已保存的内容编码为 multimodal `BinaryContent`，不重读源路径，也不比较源文件 Hash。其他 binary asset 仍由已有 Tool 或 Skill 脚本按其自身格式处理。

Skill MCP dependency 不是 Skill 安装器。RunResources 会收集 active Skill 的 `type: mcp` 声明，并与本 Run extension snapshot 中已配置且 available 的 Plugin 或手动 MCP server 比较。Runtime 保存 installed、missing 或 unsupported 的结构化诊断。Runtime 也会把未满足项作为低权限 user context warning 提供给后续 Step。这个检查不会安装、启用或启动新的 MCP server。Plugin 和手动 MCP 都由独立的 MCP 配置、consent、官方 Python MCP SDK transport、Tool Registry、Approval 和 Sandbox 链路管理。Runtime 不负责 pip、npm、系统包或 MCP server 的自动安装。

`SkillCatalog` 管理 bundled system skills、用户和 Plugin Skill。Turn 开始时，Catalog Snapshot 和 SelectedSkillSet 固化 qualified ID、source、version、source kind、content hash、canonical `file:` locator 和 implicit policy。`SkillAccess` 只从该可信 snapshot locator 激活 canonical root。模型不能通过传入任意 absolute path 扩大 Shell 权限。显式选择、成功的 `skill_read` 和已知 `scripts/` Shell invocation 都会沿 RunResources → ToolCallRuntime → Shell → Seatbelt 使用同一份 Run-scoped activation state。

MCP 使用官方 Python MCP SDK 的 stdio 和 Streamable HTTP transport。手动编辑器配置本地进程，远程服务由插件包声明。`McpConnections` 用例维护独立的账号生命周期，SQLite v38 保存连接状态和凭据引用，受保护文件保存令牌。标准 OAuth 的发现、注册和 PKCE 交给 MCP SDK，令牌请求和刷新交给 Authlib。Runtime 启动恢复中断登录，Run/Hook 的目录视图不执行恢复。多个视图在同一 AnyIO 内核上共用账号版本的短期刷新锁，工具执行不持有该锁。临时回环 HTTP 只接收 OAuth 授权码，本地控制面仍为 stdio JSON-RPC。

MCP Server 由 RuntimeAsyncKernel 持有长生命周期连接。两种 transport 共用统一 Tool Registry、MCP provenance、Approval、Durable Intent、timeout、结果校验和未知结果记录。远程请求前核对配置、账号版本、启用状态和 consent，凭据不进入运行快照或模型上下文。本地进程继续使用受控环境、进程组和 connector 或 workspace-read Seatbelt policy。远程服务只连接明确配置的地址，不获得本地文件能力。内置 GitHub 通过受管包安装入口进入同一链路。安装、账号连接和工具调用权限分别管理，详见 [current-plugins.md](current-plugins.md)。

每个 MCP Connection 在 macOS 的 canonical `/private/tmp` 下使用标准库创建私有短目录 `eidos-mcp-<随机值>/`，不再在 `~/.eidos/extensions/mcp-runtime/<uuid>/` 下分配运行目录。默认 HOME、TMPDIR 和启动器 PID 文件位于该连接目录内，服务显式配置和环境透传仍然优先。长数据目录、长主进程 TMPDIR 和服务工作目录不参与连接临时路径拼接。目录初始权限为 `0700`。启动前、进程组终止前和清理前核对目录类型及 device、inode、owner。连接关闭仍先等待进程组、Service 和 callback 停止，再删除自建目录。目录被替换时不跟随替代路径；Runtime 保留原有关闭失败和未知结果语义。

手动 MCP 的空工作目录在创建或编辑时解析为 `~/Eidos/eidos-mcp/<serverId>/`。Runtime 复用 `ProjectlessDirectoryAllocator` 的 fd-relative 创建和身份核验，不新增目录分配基础设施。默认目录可复用，初始权限为 `0700`，拒绝符号链接路径。显式工作目录继续要求已存在、归属当前用户，并拒绝 Runtime 数据目录。选定的 canonical cwd 进入原有配置记录和内容 hash，不改变 Schema。已有记录保持原 cwd，读取配置不会静默迁移路径。HOME 仍由连接环境独立控制。相对文件由服务按 cwd 和自身规则处理，权限仍由原 Seatbelt profile 控制。Runtime 不改写产物参数、不追加工具描述，也不增加 Run 产物目录或跨 Run 锁。

## 15. Packaging & Distribution

源码开发使用仓库 `.venv/bin/python`，Runtime root 是仓库 `runtime/`。打包开发路径从 `process.resourcesPath/runtime/` 解析 bundled Python 和 `runtime/app`，不回退到系统 Python、PATH、`.venv` 或用户 `PYTHONHOME`。

`build-macos-runtime.sh` 生成 macOS arm64 的 self-contained Runtime Bundle。Bundle 包含 managed CPython 3.12.13、锁定的 production dependencies、Eidos Runtime、Seatbelt 资源、`apply_patch.lark` grammar、受管 Ripgrep、Node CJS/ESM loader 和 Node package root。构建阶段会生成并校验 Bundle 根目录的 `runtime.json`，也会检查 grammar 和依赖资源存在，并通过 bundled smoke 验证 Bundle 内导入。Electron Builder 将 Bundle 放入 App resources，DMG 目标只配置 arm64。

Renderer 构建前只清理 `dist/renderer/assets`，以移除上次构建遗留的 Vite 哈希资源。构建会保留同目录下由其他脚本生成的 `file-icons`。

`package:mac` 生成未签名本地 DMG，并执行 packaged App、Runtime、SQLite、Seatbelt 和从 DMG 复制 App 的 smoke。Release 流程必须先完成 Bundle 文件复制和 `runtime.json` 生成，再进行嵌套文件签名。签名后要刷新并重新校验 manifest hash，之后才做最终 `codesign`、notarization 和 stapling。`package:mac:release` 要求 Developer ID 和 Apple notarization credentials，随后执行 hardened runtime、签名、notarization、stapling、`codesign`、`spctl` 和 `stapler` 验证。没有对应 credentials 时，仓库不能验证真实签名结果。

## 16. Runtime Recovery

Runtime 启动时会收敛未完成的 Run、ToolCall、Approval、Outbox 和资源状态。没有未完成执行、未决副作用 Intent 或运行中 ToolAttempt 的 active Run 可以重新排队。未知结果兼容字段本身不阻止恢复。真正失去执行结果的活动调用仍记录为 interrupted，系统不会自动重放。Pending Approval 保留原有恢复规则。取消先写入 SQLite，再传播给所属 Worker、模型请求、工具进程、审批等待和异步任务。未知结果不会把用户取消改成 interrupted。

Long Task 控制事实写入 `operations` 的 `long_task/control` scope。`run/pause` 在模型、工具和 Approval 安全点生效。`run/resume` 需要重新记录 Workspace identity、规则、Repository/Context snapshot、permission snapshot、Git 和 reconciliation 检查结果。未确认副作用不会自动重放。

Runtime 会记录 Shell 和其他工具的未知结果。系统不会因此建立后续操作的核验门，也不会同步扫描 Workspace 来解除这类执行限制。系统不会自动重放未知操作。后续操作仍执行各自的权限、身份、版本和结果校验。

Checkpoint create/list 和 rewind/fork action lineage 通过 typed RPC 暴露。Checkpoint 保存规则、Repository、Context、compaction、Workspace identity、Git、permission、Model snapshot 和 reconciliation 引用。Managed 和 Local Git Checkpoint 都复用 `worktree_snapshots` metadata、Git patch artifact、checksum 和 hidden ref，保存 HEAD、staged、unstaged 和 untracked 状态。Managed Fork 创建新的 detached managed Worktree，并恢复完整 Checkpoint Git 状态。Local Fork 仍使用同一个 Project 和同一个 `workspace_root` 创建新的 Local Session、Run 和 lineage，所以两个 Local Thread 共享真实目录。Managed 和 Local Rewind 会在原 checkout 中恢复完整 Checkpoint Git 状态；Local Rewind 只允许用户显式调用。Non-Git Local Workspace 仍不提供 filesystem snapshot、copy-on-write 或 rewind。

Worktree Session create、Session delete、managed Checkpoint Fork、Create Branch Here、retention cleanup 和 Restore 使用当前 durable lifecycle intent。Session Handoff 使用同一 `session_handoff_operations` 边界。Local Session create、delete 和 Local Fork 不创建 Git lifecycle intent。Local delete 只删除 Eidos Session 数据，不删除 `workspace_root` 或用户文件。Runtime 启动时先完成 SQLite 初始化，再运行 Worktree observation、Worktree lifecycle、Snapshot storage、retention/restore 和 Session Handoff reconciliation，之后才暴露业务应用。Retry 使用原始 intent 中的同一 Worktree id、root、branch 和 base commit。新 Worktree 的 branch 是 NULL，验证要求实际 Git branch 也为 NULL。已有 legacy attached Worktree 仍要求 branch identity 完全匹配。`includeLocalChanges = true` 要求 source `HEAD == baseCommit`，并在创建前后重新核验 source identity、HEAD、branch、status 和 patch。Source Workspace 不执行 stash、reset、checkout 或 add。冲突会进入 compensation；无法证明清理完成时进入 `cleanup_required`。Create Branch Here 在 durable intent 中保存当前 `expected_head`，并用该字段完成 attach 和 recovery；`base_commit` 不会随着 commit 更新。HEAD 在 branch create 后发生外部改变时，Runtime 进入 recovery required，不 force switch。Retention cleanup 只有在 Snapshot ready、artifact checksum 和 hidden ref 都通过验证后才执行 `reset --hard`、`clean -fdx` 和 non-force Worktree remove。Restore 失败会保留 Snapshot，并在不能安全清理时进入 `cleanup_required`。显式 user branch 会随 Worktree 删除保留。Runtime 不执行 force remove、未知路径递归删除或 branch 强制删除。

## 17. Implementation Anchors

维护者核对本文时，优先从以下稳定入口读取代码：

- `desktop/main/main.ts`
- `desktop/main/runtime-client.ts`
- `desktop/main/runtime-paths.ts`
- `desktop/main/preload.ts`
- `desktop/shared/`
- `runtime/eidos_runtime/protocol/server.py`
- `runtime/eidos_runtime/protocol/response_server.py`
- `runtime/eidos_runtime/protocol/registry.py`
- `runtime/eidos_runtime/application/`
- `runtime/eidos_runtime/runtime/supervisor.py`
- `runtime/eidos_runtime/runtime/engine.py`
- `runtime/eidos_runtime/context/`
- `runtime/eidos_runtime/model/`
- `runtime/eidos_runtime/tools/`
- `runtime/eidos_runtime/sandbox/`
- `runtime/eidos_runtime/extensions/`
- `runtime/eidos_runtime/repo_intelligence/`
- `runtime/eidos_runtime/telemetry/provider.py`
- `runtime/eidos_runtime/telemetry/tracing.py`
- `runtime/eidos_runtime/git/`
- `runtime/eidos_runtime/db/schema.py`
- `runtime/eidos_runtime/db/storage.py`
- `runtime/eidos_runtime/persistence/`

## Approval R1：权限请求闭环

`PermissionPolicyEvaluator` 只根据 Base、当前 Run Grant、动作请求和硬限制返回 `allow / ask / deny`。该组件不访问 SQLite、不创建 Approval，也不执行 Tool。权限类型继续使用 `AdditionalPermissionProfile`、`NetworkPermissions` 和现有文件权限条目。

模型可以调用 `request_permissions` 申请网络和具体绝对路径权限。空请求会进入 Tool 参数校验错误。Runtime 会先规范化路径并检查保护边界。已有权限返回 `already_granted`；受保护权限返回 `permission_not_requestable`；可申请权限通过唯一的 `ApprovalCoordinator` 等待批准或拒绝。

`request_permissions` 的工具说明包含参数结构、文件权限示例和网络权限示例。工具说明和 `permissions` 字段的 Schema 描述都明确要求 JSON 对象，不能传入 JSON 编码字符串。Runtime 保持原有参数校验，不自动解析字符串形式的权限对象。

Run Grant 由当前 Run 中已批准的 `approvals.request_json` 派生。请求必须带有 `kind=permission_request` 和 `grantScope=run`。SQLite 对 Approval 的原子决策同时决定 Grant 是否存在，因此没有新的授权表和第二份状态。Run 终止后，Grant 不再生效。普通 Shell 把 Base、Run Grant 和单次动作请求一起物化为 `EffectivePermissionProfile`。Seatbelt 只消费该结果。显式 Shell 扩权仍只授权当前动作。

已知网络 denial 会进入同一个权限请求流程。Runtime 会在 Approval 中保存已完成的 Shell 结果。用户批准后，当前调用返回 `permission_granted_retry_required`，模型可以再次调用普通 Shell。Runtime 不会自动重跑这个命令。拒绝返回 `user_rejected_network`。相同请求的拒绝按持久化 fingerprint 去重，不会阻止不同命令或不同类型的审批。LoopGuard 的状态包含权限状态，权限变化后允许模型重新执行相同动作。

Runtime 重启可以恢复 R1 的结构化待批请求。恢复只接受没有不确定执行的暂停点。未执行的动作会重新准备并检查当前 Tool 契约和原审批 fingerprint。网络 denial 的恢复只交回保存的已知结果，不重放 Shell。缺少完整事实、契约变化、取消和不确定副作用继续 fail closed。恢复仍使用原 Approval、原 Run worker、ApprovalCoordinator 和 approve/reject RPC。

数据库版本为 11。v8→v9 只为 `tool_calls` 增加可空 `raw_arguments_json`。新调用保存 Provider 原始参数，`arguments_json` 继续保存执行所用的规范化参数。旧行保持 NULL，Runtime 不会伪造历史原始参数。v9→v10 将全局 `running`/`finalizing` 唯一索引改为按 `session_id` 的唯一索引；迁移不改写旧的 `waiting_approval` 重叠记录，恢复 admission 按 Session 串行处理。v10→v11 增加手动 MCP 配置表。环境变量值使用受保护 Blob 存储。旧版本逐级迁移，失败会回滚。

Desktop 的 `ComposerSlot` 在 `waiting_approval` 时用 `ApprovalComposer` 替换普通输入框。文件、Shell、网络、MCP 和权限申请共用批准、拒绝、响应中、失效和错误状态。Eidos State 的既有文件变更映射保持兼容。Feed 只显示审批历史。Session 的 `activeRunStatus` 从 Active Run 派生，不写入 Session 表；Sidebar 对等待审批显示“等待批准”。

## Run 收尾与 Shell 执行期限

`run_shell` 只有 `command` 必填。模型看到的 Schema 只声明标准参数名、类型和默认值。工具说明要求模型省略不需要的可选字段。后端保留原有 `yield_time_ms` 别名，模型看到的名称仍是 `yieldTimeMs`。

Runtime 使用 Pydantic 校验原始参数。命令缺失或无效时，Runtime 返回参数错误。未知字段和校验失败的可选字段会被移除，剩余字段继续校验，缺省字段使用默认值。Runtime 不按错误值增加转换分支，也不把字符串数字或字符串空值猜成目标类型。有效的工作目录、等待时间和其他独立参数会保留。权限字段组成一个请求；组合无效时，Runtime 整体丢弃这组请求，回到默认权限。实际路径、权限、审批、Sandbox、Durable Intent 和结果核验仍走原流程。

Runtime 在结果摘要中告诉模型哪些参数被忽略，最多列出 8 个有界字段名，不回显值。原始参数继续保存在 `raw_arguments_json`，`arguments_json` 只保存实际提交且校验通过的参数。模型历史不再回填省略字段的默认值，Shell 执行器仍在内部补齐默认值。这个规则只作用于 `run_shell`，其他工具保持原有校验。

- 模型提交最终答复时，Runtime 在同一事务提交答复和 Run 终态。未清除的 reconciliation 会使 Run 进入 `interrupted`，不会再强制模型继续只读核验，也不会清除未知 Durable Intent 或放行新副作用。
- 取消后 Worker 已退出时，RPC 返回 `canceled` 或 `interrupted`。系统记录取消完成时间；副作用未知不再作为取消失败。无 Worker 的 queued Run 如果已带有未确认副作用，也进入 `interrupted`。重复取消已中断 Run 返回原终态。Worker 仍存活时继续报告 `RUN_CANCEL_TIMEOUT`。
- 新 Run 的 Shell 不再设置默认进程总期限。`run_shell` 首次等待默认 10 秒，范围 250 毫秒至 30 秒；模型随后使用 `write_stdin` 等待，默认 30 秒，范围 250 毫秒至 60 秒。等待窗口到期只返回 `shell_running` 和 `sessionId`，不会结束进程或触发 reconciliation。ToolSpec 的 600 秒 watchdog 只限制单次启动、审批重试或跟进调用，审批等待不计入预算。历史 3600 秒 ToolSpec 仍可读取，但新工具不会用它限制进程寿命。
- 控制器把已有 Shell 结果转成超时或取消结果时，会保留已有输出和终止信息，并继续执行结果校验、输出限额和敏感扫描。进程清理和未知副作用仍按原规则处理。此修改不恢复旧结果中已经缺失的 stdout/stderr。
- Runtime context 使用 `recentToolErrorFingerprints` 表示最近工具错误。空列表不代表对账完成。LoopGuard 不把 assistant 文本变化或最近错误列表中的错误消失单独当作新进展。


### Shell 会话收尾

Shell 结果使用可选字段 `outputComplete` 和 `outputCaptureError` 分别记录输出完整性与安全原因码。`truncated`、`omittedBytes` 继续只描述原始输出上限。进程明确正常退出后的 `output_read_failed` 不单独建立对账屏障；扫描拒绝、持久化失败、进程读取循环异常和未知退出继续保留对账。结果携带原命令的 `outputCallId`，模型用它读取持久化输出，不能把 Shell `sessionId` 当成该 ID。`read_tool_output` 的分页结果保留 `outputComplete`；读取到保留内容的末尾不代表原始输出完整，旧结果的该字段保持 NULL。

Context 从未决 Durable Intent 投影最多 16 条 `reconciliationOrigins`，包含原工具、provider call ID、原因码和输出捕获原因，不注入原始命令或异常正文。空列表不代表对账已完成。LoopGuard 不再设置同一对账 epoch 的三轮恢复上限；有真实新事实可以继续，语义重复与无进展检测仍然保留。Step 进度签名保存对账与有效 Shell poll 标记，旧签名默认没有该标记。有效空输入 Shell poll 保持中性。正常完成仍检查未决 Intent，不会把未知副作用记成成功。原 Shell 退出回调已经记录的不确定结果，不会因空输入轮询再次返回而重复增加 epoch。

`ShellProcessManager` 继续使用现有 Run 资源和输出读取线程。每条输出流持有同一个敏感扫描器，轮询不会结束扫描器，也不会释放尚未完整的行。输出上限继续由原始字节计数控制。模型轮询只读取尚未交付的安全输出。

启动 ToolCall 可以完成一次 `shell_running` 观察，但原 Durable Intent 保持 `running`。Runtime 在初次结果提交后才启用退出回调。退出回调更新原命令的 canonical/UI 结果、Workspace diff 和 Intent，并在同一 SQLite 事务中产生事件。Runtime 不会改写模型已经收到的历史结果。`read_tool_output` 在命令退出后读取原命令累计输出。

当前 Run 的 `ToolConcurrencyGate` 只保护提交/启动窗口，不保留整个 Shell 生命周期的占用。删除 `shell_session_busy` 和对应 retain/release 门控；同一 Run 可以继续启动命令、执行文件变更和协调子任务。其他 Session 的 Run 使用各自的 gate，可以在同一个 Workspace 并行执行 Shell 和其他普通副作用。空输入观察不会新建 Intent，也不会重复获取该 Run 的 gate；非空输入先核对 Run 内会话，再创建输入 Intent。输入沿用原进程权限，不能借此扩权。同一个 Workspace 的并发修改可能让 Workspace observation 或 diff 包含其他 Session 的变化。权限、Sandbox、Durable Intent、取消和进程组清理仍按各自 Run 独立记录和收敛。

模型直接提交最终答复时，Runtime 先停止活跃命令并完成退出记录。Runtime 保留答复，但把提前停止命令的 Run 记为 `interrupted`。其他最终化和取消路径也会清理会话。已知退出与副作用未知分别记录；Runtime 不会把正常等待写成 reconciliation。运行中的 Intent 在重启后继续走原有恢复流程，Runtime 不会根据 PID 重连或自动重放命令。LoopGuard 仅对当前 Run 中仍运行且参数有效的空输入观察跳过重复判断。

### Projectless 文件提交与 Skill 写入权限

Seatbelt 编译器为数据目录内的当前 Workspace 保留祖先路径的 metadata 和存在性检查。Shell 因此可以使用绝对路径执行 `mkdir -p`，也可以在 Workspace 内执行 `ls -la`。该例外不开放祖先目录的内容、列举或写入，也不开放其他 Session；hard confidentiality deny 保持不变。

`secure_workspace_move` 向受控 helper 传递已核验的 Workspace 目录 fd。helper 核对目录身份，并从该 fd 向下使用 `O_NOFOLLOW` 访问目标。它不再从 `/` 逐级打开 `.eidos` 等受保护祖先。当前 Projectless Workspace 的普通文件沿原有 Workspace Permission 执行，不逐次审批；其他会话和数据文件仍受数据目录 deny 保护。helper 的新建提交按 errno 区分目标已存在与其他失败，诊断只记录固定阶段和数字 errno。

Base/Effective Permission 的 `approvalWriteRoots` 标记用户 Skill 存储目录。字段默认空，旧权限快照仍可读取；新 Run 的产品权限工厂填入数据目录的 `skills`。Skill 激活只提供读权限，普通 Skill 的写入必须来自明确的附加授权。Runtime、PermissionPolicyEvaluator 和 Seatbelt 使用同一授权范围；精确文件授权不会开放同目录其他文件。Seatbelt 只允许获批目标祖先的 metadata 检查，不开放祖先目录内容。`skills/.system` 作为独立 protected write path 永久禁写，即使它被选作 Workspace 也不能修改。

Runtime 不启动会丢失永久写入保护的裸 Shell 无沙盒执行。受控文件 helper 可在明确审批后不使用 Seatbelt，但 Runtime 会继续核验每个目标的保护范围、身份和版本。权限等待后的重物化、Durable Intent、最终内容验证和 Reconciliation 规则保持不变。Skill 文件修改不重写当前 Turn 的 Catalog Snapshot；后续 Turn 重新发现修改后的内容。

### 产物预览与反馈

本轮实现沿用 `Renderer → typed preload IPC → Main → RuntimeClient → WorkspaceExplorerApplication → WorkspaceReader`。Main 的 `ArtifactPreviewManager` 只保存短期资源授权和原生网页视图。文件权限、Workspace 身份和有界读取仍由 Runtime 判定；Renderer 和网页不直接使用 Node 文件 API。资源授权绑定 WebContents、Session、execution root、Workspace 身份和所选文件版本。每个资源块都重新检查版本。文本资源完整扫描后才释放原始字节；扫描产生脱敏替换时，原始资源会被拒绝。

图片/PDF 在主 Renderer 中作为被动资源加载。可运行的 HTML 使用没有 preload 的 sandboxed `WebContentsView`，并使用独立临时 Session。该视图不共享主应用的 IPC 或 Cookie。Main 注册资源协议、拒绝额外权限和下载，并在生命周期结束时清理视图及授权。Browser 的网页和滚动状态不是持久业务事实。网页地址识别在 Renderer 边界使用直接依赖的 `tldts`；不能识别为地址的无空格输入转为 Google 搜索。刷新通过 Renderer 状态触发一次逆时针半圈图标动画，不显示独立加载提示。当前网页面板不渲染标注入口。

`record_workspace_change` 在同一 SQLite 事务内记录准备后的 Diff 和 `item.updated` Event。Event/Outbox 将更新后的 Item 投影为 `item/updated`。Desktop 的“最近一轮”从已有 Item/ToolCall 读取，不维护另一个 Run 成功状态。准备中、完成和失败仍使用原 ToolCall 状态。原始模型参数流不作为可执行补丁展示。

Desktop 的 `TurnResults` 在每个 Run 的最终回答文本之后、回复复制和反馈操作之前投影文本修改和产物。它从分页后的 Session Item/ToolCall 记录读取，不创建 Artifact 表或第二套执行状态。Projectless Session 同样投影文本修改卡。文本卡通过独立 text-review 面板审查持久化补丁，不依赖 Git；面板不列入工作区工具菜单。文本修改按路径归组。卡片和两种审查入口共用 `projectNetFileDiff`，按补丁顺序显示范围开始到结束的净增删，不显示“累计”标签。新建 5 行后替换其中 1 行仍显示 +5/-0；已有内容改回原样显示 +0/-0。系统保留完成状态、改名后的路径和最后一条记录的定位。补丁缺失、内容衔接冲突、未完成或结果未知时，统计保持未知。产物标题来自声明中的可选标题或文件名。`OutputContent` 按执行目录和相对路径保留当前 Session 最新声明。Files 只负责文件树和预览。

Runtime 的 `project_tool_text → display_file_diff` 为内联普通 Unified Diff 补充展示文件头。适配器使用已锁定的 `unidiff` 校验补丁和定位每个文件，不依赖文件头中的时间戳。它保留原来的新增、删除、改名路径和所有修改块，并将 Eidos 换行说明移到展示文件头中，使仅换行变化也能显示零行增删。Session 快照、继承历史和实时 Item 通知共用这个入口。SQLite 中的原始补丁不变；大补丁或展示头使文本超出内联上限时，系统仍使用原始文本的大小、Hash 和分页读取。卡片没有内联补丁时仍保持统计未知。Python 和 Renderer 共用 `protocol/fixtures/text-diffs-v1.json` 验证展示格式与统计。

`persisted-file-diff.ts` 只投影已保存的补丁，不读取当前 Workspace。Node 依赖 `diff==9.0.0`（jsdiff，BSD-3-Clause）负责标准补丁解析、严格应用和最终差异生成；现有 `react-diff-view` 负责展示。Eidos 保留记录顺序、状态核验、改名归组和有界历史重建。既有文件未记录的行只使用内部稳定标识；系统只从后续补丁学习可证实的旧内容，且不把内部标识放进展示补丁。净补丁使用零上下文，避免补造未记录文本。单文件重建上限为 50,000 行、4 Mi 个字符和 512 个补丁，重建预算为 100ms，最终比较超时为 50ms；超限或不一致时保留原始记录并显示未知统计。系统不修改持久化补丁，也不调用文件写入或当前 Git Diff。

文本修改卡的增删数字复用 Git 审查的 `git-review-stat` 样式，使用相同的界面字体、13px 字号、550 字重和等宽数字。文本审查中的 Diff 行号继续共用 Git 审查的 gutter 样式。Renderer 原生样式测试在完整 CSS 加载后比较卡片与 Git 审查的最终字体属性。

产物身份由内置 `declare_outputs` 的成功 ToolResult 提供。`RunResources` 将该工具作为 direct、single-batch 的内置工具注册；系统级 Agent 指令要求模型仅对用户期望接收的独立终态交付文件调用，文件被创建或修改不等于交付物，项目内普通修改仍是工作区变更，中间文件默认不声明，Skill 不负责定义工具的适用范围。工具使用现有 Pydantic 工具 Schema 和通用结果投影。其 Adapter 使用 Run 固定的 WorkspaceIdentity，通过 WorkspaceReader 的 fd-relative 边界核验普通文件元数据，并复查整个批次。文件读取采用非阻塞打开，特殊文件不会在类型检查前阻塞；符号链接、多硬链接、文件 owner 不匹配、敏感路径和 `.git` 路径不能声明。该工具不读取文件内容，也不改变文件；声明只存在于控制器正常提交的 ToolResult 中，所以它沿用只读工具执行链、取消和结果提交语义，不增加独立持久化动作或 Durable Intent。现有控制器继续承担结果校验、敏感信息扫描以及 ToolResult/Event/Outbox 的同事务提交。

Adapter 的成功和失败出口都通过现有 `canonical_tool_result` 封装，并显式使用 `DeclareOutputsResultData`。结果在进入控制器的首次严格校验前就带有 `toolName` 和契约版本字段。失败结果保留原始错误码，控制器不放宽结果校验。本次结果封装修复尚未进入测试阶段。

声明结果包含 executionRoot、outputs（path、可选 title、sizeBytes、version）。version 沿用文件身份、大小和修改时间生成的版本标识，不是内容快照或质量证明。输入每批最多 20 项，路径沿用 512 UTF-8 字节上限，标题最多 120 个字符。任一核验失败时，整个调用没有成功声明；后续成功调用按文件新增或更新，省略其他文件不撤销其声明。Renderer 只接收 builtin/eidos.declare-outputs 的成功结果；Shell 观察、Diff、文件后缀和回复标记不产生交付事实。工具结果仍通过现有 resultJson 传递，不修改 RPC Envelope 或 SQLite Schema。

修改卡的 Review 请求携带 Run、文件路径和 Item 定位，并打开右侧的最近一轮 Diff。`AppShell.handleOpenReview` 只要求存在当前会话，不使用 Git Review 的工具可用性作为条件。文本审查面板读取持久化 Item/ToolCall 补丁，非 Git 和 Projectless 会话也可以打开；面板仍不列入工作区工具菜单。文本审查采用专属的 `TextReviewPanel.css`，工具栏深度参考 Git 审查设计，将操作控制（范围切换与展开/折叠、刷新）与增删统计合并为紧凑单行工具栏；列表结构完全向 Git 审查对齐，采用无厚重边框的简洁列表与 `修改 <span>N</span>` 分组标题，行内展示代码路径与增删统计，悬浮时呼出打开入口；针对单轮内多次工具调用，通过公共 `projectNetFileDiff` 聚合为该范围的最终净差异，避免暴露中间冗余步骤。支持内置预览的产物卡同时复用 Workspace Explorer、隔离 HTML 预览、Main 的受控系统应用打开入口和 Finder 显示能力；DOCX 等没有内置预览的 Office 产物只展示系统应用打开和在 Finder 中显示。当前没有可证明安全的本轮 Checkpoint 恢复入口，因此卡片的撤销操作保持禁用并显示原因。

本轮没有数据库迁移或第二套 Artifact 表；浏览器地址识别新增直接依赖 `tldts`。

以前产物预览修订有自动验证记录。2026-09-13 的产物声明修订已经补充并通过对应的 Runtime、协议、Main 和 Renderer 测试。具体限制和未完成的人工验收见 `current-limitations.md` 的“本轮产物实现的边界”。

### 回答流式输出（代码修订，待测试）

普通采样和 RunFinalizer 复用 `ModelRunner → StreamingSensitiveScanner → AssistantStreamWriter → SQLite Event/Outbox → item/delta → Main → typed IPC → Renderer`。Writer 立即刷新扫描器释放的安全文本块，并把大文本拆成有界片段。Writer 保留未闭合的尖括号尾部，并阻止已识别的 Provider 控制标记；完整响应校验仍由原有 Runtime 链路负责。正常完成复用当前 Item，不再把整个回答重复写入。校验失败或断流会结束当前草稿；取消和重启沿用已有 Item 终态与恢复规则。Context Repository 排除 in-progress 和 incomplete Assistant Item。

新 delta 的 `offset` 在追加内容的同一 SQLite 事务内计算，单位为 UTF-16 code unit，与 JavaScript 字符串长度一致。该字段只扩展 Event payload 和 Notification，不增加 DB 列或第二套状态。Renderer 对已有 Item 的 started 重投不再覆盖本地内容，并拒绝重复或有缺口的 offset。Run 结束时的持久快照刷新负责最终内容校正。旧 Event 不带 offset，继续兼容读取。旧 Desktop 的严格通知校验不接受新增字段，所以 Desktop 与 Runtime 必须一起更新。

本次不新增模型调用、传输连接、依赖或工具参数流式能力。现有按行敏感扫描仍限制无换行文本的显示时机。用户要求先完成代码修订，确认后再编写测试并集中验证，因此当前没有测试、lint、构建或真实 Provider 验证结果。

### Skill 设置管理

Desktop 的技能设置沿用 Renderer → typed preload IPC → Main → RuntimeClient → ExtensionApplication 链路。`SkillManagement` 复用 `SkillCatalog` 的目录发现和内容校验。`skill/list` 与 `extension/read` 返回管理列表，包含已禁用技能和未启用插件提供的技能。管理数据增加 `sourceKind`、`enabled`、`available` 与可选 `icon`。技能图标优先从技能根目录下 `assets`（及兼容 `assents`）目录读取 `<skill-name>-small.svg`，若不存在则读取 `<skill-name>.png`，并转为 base64 Data URL 随 `skill/list` 返回；两者均不存在时为 `None`，前端无缝回退为首字母占位符。`enabled` 表示技能自身开关，`available` 表示所属插件是否可用；技能开关不替代插件授权。

Runtime 的 `skill/detail` 返回完整 Markdown、去掉 frontmatter 的正文和经过目录发现校验的 canonical 技能目录。Desktop 按需读取详情，复用 Markdown 渲染、剪贴板和 Main 的 Finder 能力。详情的 Markdown 不继承当前 Session 的 Workspace 链接处理上下文。原有 `skill/read` 与 Run 内技能读取仍按可用 Catalog 工作。

SQLite schema v12 增加 `skill_states`。Runtime 将技能开关、卸载标记、待清理状态和目录身份存入该表，并在同一事务写入 `skill.state_changed` Event/Outbox。旧数据迁移后没有覆盖项，技能默认启用。系统技能允许保存开关，但不能卸载，也不会通过设置修改 `.system` 文件。

新 Turn 的 extension snapshot 保存 `excludedSkillIds`。SkillCatalog 仅按该快照排除禁用或卸载技能，不在当前 Run 读取可变设置。历史快照缺少该字段时按空清单处理。设置变更不修改已固定的 SkillCatalogSnapshot，也不撤销当前 Run 已获得的技能资源。

个人分类包含独立用户技能和插件技能。独立用户技能先持久化卸载标记，再核验目录 owner、inode/device 和完整 tree hash。Run 扩展快照记录 `skillCatalogIds`，只有实际固定该条目的非终态 Run 阻止文件清理；旧快照缺少引用集合时仍保守等待，已排除的条目不阻断。没有引用者时把目录移至确定命名的私有暂存目录，再调用标准库的 fd-relative `shutil.rmtree`。清理失败或中断时，数据库保留待清理状态。现有扩展清理回调、设置列表读取和 Runtime 启动会重试；目录身份或内容变化时，Runtime 保留文件，不删除替换后的内容。暂存目录允许重启继续部分清理。存在待清理记录时，同名工具安装会拒绝提交；清理完成后的显式工具重装会移除卸载标记。

插件技能只保存单技能卸载标记，不删除插件包内文件，因此不会破坏插件 hash 或影响其他 Skill/MCP。插件重启或开关不会移除该标记。当前实现保守地等待所有非终态 Run 结束后清理独立用户技能，不新增后台清理线程。

本次设置重构已进入测试阶段。Runtime 管理、数据库迁移、协议 Fixture、Main 集和 Renderer 行为测试已经补充。定向测试、协议契约检查、Python 检查、Renderer 状态与行为测试、构建、Seatbelt 和 Electron smoke 已完成。Runtime 全量测试首次运行发现 4 个旧 schema 断言和 1 个方法请求类型复用问题；修正后，5 个失败用例已定向通过。Main 全量测试首次运行发现 1 个错误测试期望；修正后，该场景已定向通过。人工 UI 验收和真实 Provider 验证仍未完成。


### Run 权限模式

调用链保持为 `Composer → preload → Main → run/start → Run 快照 → PermissionPolicyEvaluator → ApprovalCoordinator → 原 Tool 执行链`。权限决定仍属于 Eidos。实现复用现有 Model Gateway、Approval 事务和权限物化，没有新增依赖、独立 Agent Loop 或第二套审批状态机。

低风险操作使用同一 ToolExecutionController 的免审批入口，继续先提交 Durable Intent，再执行和核验。用户已经审阅启用的 MCP Server，其工具明确声明 `readOnlyHint=true` 时不逐次审批，也不调用自动审查模型。缺失或非只读声明继续审批；非破坏性和幂等声明不能代替只读声明。Runtime 信任已启用服务的只读声明，但该声明不是操作系统保证。声明进入现有工具契约 Hash；MCP 继续按 external 类型保留提交窗口、取消、Sandbox、结果校验和未知结果记录。此调整不把 MCP 改为并行只读工具。旧待审批工具只接受审批标记从 true 到 false 的兼容恢复，参数、结果、来源 Hash 及其他契约字段必须保持一致；新工具调用继续使用严格的当前契约绑定。

内置 `web_search` 使用用户选择的默认服务授权，不逐次审批，也不产生 Shell 网络 Grant。Skill 安装的下载阶段复用已有的基础网络权限或当前 Run 网络 Grant；没有网络授权时继续申请下载审批，最终安装仍审批。Shell 对普通宿主文件的读取已经由默认 Seatbelt 放行，显式申请相同读取权限直接返回 `already_granted`；请求涉及受保护读取范围、额外写入、联网或沙盒外执行时继续按原权限判断。文件工具的读取范围保持不变。新规则不扩展单次批准的范围，Run Grant 仍只来自 `permission_request + grantScope=run`。旧版可安全恢复的待审批记录继续处理原决定；存在未完成 Intent 时，系统保留原中断与未知结果记录，不自动重放操作。

Composer 提供 `manual`（请求审批，默认）、`auto_review`（替我审批，推荐）和 `full_access`（完全访问，风险）。选择只影响下一次创建的 Run。已有 Run 的会话从同一个最近 Run 读取模型和审批模式。Draft 会话在首个 Run 被 Runtime 接受后，Renderer 将返回 Run 的模型和审批模式保存到 `localStorage`，作为新会话默认值。Draft 和没有 Run 的会话使用这组默认值；没有有效默认值时，审批模式使用 `manual`，模型使用列表中的第一个模型。设置控件的临时修改只影响当前会话。已有会话的后续 Run 不会改写新会话默认值。

审批模式选择器位于 Composer 底栏左侧，采用无外边框的自定义下拉菜单（ApprovalModeSelector），与模型选择器保持一致的视觉风格。用户在下拉菜单切换至完全访问模式时，由 Desktop 弹出应用内风险确认对话框（ConfirmDialog）；确认后切换为完全访问，任务启动前不再重复弹窗。Main 在完全访问请求中发送 `fullAccessConfirmation=full-access-v1`；Runtime DTO 拒绝缺少确认版本的完全访问请求。保存的默认值可以是 `full_access`，后续新 Draft 会沿用该模式，载入默认值时不会再次弹出确认框。模型和 Tool 参数没有切换模式的入口。重新回答和编辑后重发会携带 Composer 当前选择的审批模式。Main 沿用普通发送的完全访问确认标记，Runtime 通过同一个 Run 创建入口固定模式和权限。旧调用方省略模式时，Runtime 从 SQLite 读取原 Run 的模式，保留自动审批；原 Run 为完全访问时，旧调用仍按人工模式创建新 Run。原 Run 的模式和权限快照保持不变。

Run 固定模式、基础权限、Sandbox Policy 和确认版本。SQLite v13 在 `runs` 增加 `approval_mode`，旧 Run 默认为 `manual`；该版本在 `approvals` 增加 `review_json`，旧审批默认为空。迁移沿用事务和失败回滚。Runtime 在执行前检查 Run 模式与权限快照一致。当前自定义权限没有配置、解析器或 UI；后续可以在既有 Run 权限快照工厂和 PermissionPolicyEvaluator 中接入 `config.toml`，本期不接受 `custom` 模式。

请求审批模式继续使用原有审批入口。自动模式只接管原本进入 ApprovalCoordinator 的动作，普通 Workspace 写入和默认沙盒 Shell 不额外调用模型。Runtime 将原路径硬拒绝和显式取消沙盒保护改为高危人工请求。auto_review 对这些请求不调用审查模型；用户通过 danger 样式的审批窗口决定。参数无效、身份变化、版本冲突、取消和结果不确定仍按实际错误处理。自动审批覆盖文件扩权、Shell 扩权、权限 Grant、网络拒绝后的申请，以及仍需审批的 MCP 和 Eidos-state 操作。Coordinator 先持久化 pending，再使用当前 Run 固定的模型进行独立请求。请求不包含主执行模型的对话状态，也不开放可执行工具。支持 Function Tool 的模型通过一个结果 Schema 返回判断；其他模型返回严格 JSON。Runtime 验证完整返回结构、风险和理由，未知风险不能批准。

审查证据包括当前 Run 及之前最近 32 条用户消息、具体 Tool 参数、权限请求和完整 Diff。Base Hash 等工程校验值仅用于内部检查，不进入审查模型上下文。策略把项目内容、工具输出和操作理由作为不可信数据。请求最多 48 KiB、答复最多 8 KiB、审查超时 60 秒、理由最多 400 字符。明确风险拒绝保留；无法完成可信审查或未知风险时通过原审批通道交由用户决定。请求指纹纳入用户证据，新用户指令可触发重新评估，模型重复相同请求仍去重。

审批决定、理由、风险、模型标识、策略及证据 Hash、可取得的 Token Usage 和耗时写入原 Approval 记录。该记录与 Tool 状态、Run 状态、Event/Outbox 同事务提交。事务验证审批来源与 Run 模式一致。取消优先于迟到判断。Runtime 重启后不重新采样未完成的自动审批，保留中断原因和未获批准事实，并交由用户决定原请求。Desktop 在自动审查时通过位于底栏上方的状态胶囊（ApprovalStatusBanner）展示“模型正在审查操作…”与动效进度，并保留取消入口；Feed 展示审批状态与拒绝理由；实际决定来源保存在 Approval 记录中。ToolResult 区分模型拒绝和人工拒绝。审查用量保存在 Approval 中，当前 Context Usage 展示仍只反映主模型上下文。

完全访问使用 `fullAccess` 权限快照，启用网络并移除 Eidos 路径 deny。Shell、受控文件 helper 和 MCP 执行不使用 Seatbelt。所有操作直接执行，不创建逐项 Approval，不进入 waiting_approval，不记录模式授权，也不调用审查模型或人工窗口。共享执行入口仍先提交 Durable Intent，再执行和核验。模型不暴露 request_permissions，并被指示使用普通参数。历史或冗余的网络、Git 和沙盒权限 request 参数按完全访问模式直接执行。历史的完全访问审批卡片不再显示。文件工具允许绝对路径访问，但仍拒绝不支持的链接和特殊文件；Shell 使用当前 macOS 用户的权限，不能绕过操作系统 ACL、TCC 或只读卷。该模式没有管理员提权，也没有无界输出或自动重放不确定副作用。

高危审批协议在原请求 JSON 中增加可选的 `requiresHumanConfirmation` 和 `riskWarning`。Main 严格验证这两个字段，再传给 Renderer。审批界面只显示一个简短风险提示框，高危与沙盒外执行提示合并为一句。标识来自 Runtime，不接受工具参数设置。SQLite 沿用原 Approval 表；auto_review 下的高危决定来源是 manual，事务核对持久请求标识后才接受该来源。普通扩权的模型审查行为保持不变。文件准备可以生成受保护目标的候选 Diff；实际提交仍要求对应人工决定或已批准的 Run Grant，并在提交前核验目录、文件身份与 Base Hash。

## 输入引用与草稿

本节描述输入引用、草稿和 Composer 布局。`pnpm test` 与 `pnpm test:composer-layout` 已通过。子 Agent 历史分页和通知刷新也有定向测试。自动化结果和未完成的人工验收范围见 `docs/current-limitations.md`。

输入链路是 `Composer / WorkspaceExplorer → InputContext → preload typed IPC → Main → input/* RPC → InputContextApplication → InputContextRepository`。Main 提供系统文件选择、剪贴板图片、右键菜单和拖放文件路径转换。Renderer 不直接读取文件。Runtime 读取选中的内容，检查文件身份、大小和敏感内容，并生成带来源、摘要和类型的不可变引用。

Schema v14 在 `items` 增加 `input_references_json`，并增加 `input_references` 与 `input_drafts`。v13 → v14 升级通过现有事务执行；此前受支持的版本沿原有升级链进入 v14。引用正文与图片使用现有 JSON Blob 存储。SQLite 保存引用元数据和 Blob 指针。Blob GC 保留这些指针。每个 Session 的草稿保存文字与引用 ID；尚未创建 Session 的输入使用 `new-conversation` 草稿。Renderer 的草稿状态只是编辑缓存，持久事实仍由 Runtime 管理。

Composer 的原生 textarea 使用 Chromium 的 `field-sizing: content` 按当前内容和宽度计算高度。CSS 保留 56px 最小高度、168px 最大高度和超出后的内部滚动。Renderer 不再临时缩小正在编辑的输入框来读取 `scrollHeight`，因此等高编辑不会通过中间布局改变 Execution Feed 的滚动位置。

Markdown 的组件映射不再依赖整个 ArtifactActions 上下文对象。链接和图片组件直接读取当前上下文，单行输入和全选删除引起的父组件更新不会重新挂载代码块或图片。实际 Session、执行目录或文件版本变化仍由原预览链路重新读取资源。真实 Electron 布局测试使用含代码块和图片的历史记录，并覆盖单行输入、全选删除、多行删除、组合输入事件、自动换行、宽度变化和高度上限。

`input/prepare` 固定引用，`input/read` 返回预览，`input/draftRead` 与 `input/draftWrite` 读写草稿。`run/start` 接收引用 ID。Runtime 在接纳 Run 前解析这些 ID、检查总量和模型图片能力，并重新核对 Skill、MCP、Plugin 的可用状态和版本。Run 的用户消息与引用元数据在同一现有事务中提交，并经 Event/Outbox 投影。编辑重发、重新生成和 Fork 保留原引用；编辑重发可以显式移除引用。引用不会复制审批或权限授权。

文本和 PNG/JPEG 图片使用内容快照。目录使用最多 200 项的一层目录摘要。未解析的二进制文件和较大的文本使用位置引用。目录和位置引用不代表子文件或二进制正文已经进入模型上下文。Runtime 不会因为引用而赋予模型额外写权限。后续工具读取和操作继续使用现有权限流程。

Context Builder 从用户消息关联的持久引用读取内容，并标记来源、SHA256 和资料边界。图片通过现有 Pydantic AI `BinaryContent` 进入支持图片的 Provider。Context 预算排除 base64 字符串的文本计数，并加入按尺寸估算的图片开销。压缩事实保留引用来源和 ID。Skill 使用现有 Catalog、Activation、Resource 流程；MCP 和 Plugin 选择激活现有 deferred tools，不安装扩展、不绕过授权，也不在运行中替换快照。

Python 的输入 DTO 通过 `node scripts/generate-input-contracts.mjs` 生成 `desktop/shared/input-context.generated.ts`。生成器使用现有 Python 环境和已锁定的 `json-schema-to-typescript`，没有引入生产依赖。Main 与 RuntimeClient 继续执行边界校验。

## Plan 模式

用户通过 Composer 的模式选择或输入开头的 `/plan` 显式选择 Plan。`run/start.workMode` 默认为 `execute`。Runtime 把模式保存在 Run 上。模型不能改变模式。Plan 与 `manual`、`auto_review`、`full_access` 权限模式独立；原有工具与权限流程继续生效。

调用链为 `Composer / PlanPanel → typed preload IPC → Main RuntimeClient → Method Registry → PlanningApplication / RunApplication → PlanningRepository / RunSupervisor → ToolExecutionController`。Python Pydantic 是新增 DTO 的定义来源。`scripts/generate-planning-contracts.mjs` 生成 Desktop 类型，`desktop/shared/planning.ts` 校验跨进程数据。

主 Run 的工具注册表提供 `request_user_input`，Plan Run 额外提供 `write_plan`。Runtime 只对计划写入检查 Plan 模式；澄清仍要求 Run 活跃。`request_user_input` 每次接收一到三个问题，支持单选、多选和文字回答；用户可以填写自定义回答或明确跳过。控制工具可在混合批次中按顺序执行；没有可执行计划时允许直接说明调查结论或阻塞原因。可审阅计划必须由 `write_plan` 保存，不能把普通文本当成已保存计划。

澄清请求、问题、答案和 ToolCall 关联保存在 SQLite，并写入 Event / Outbox。单独提问且没有活动 Shell 时，请求创建与 Run 转为 `waiting_input` 同事务提交，Worker 退出；回答后 Run 转回 `queued`，恢复原 ToolCall。混合批次或活动 Shell 下保持 Run `running`，原 Worker 暂停有效执行计时、等待答案并继续后续调用，不丢弃 Shell 所有者或剩余批次。答案提交仍检查请求和 Run 所有权，重复相同答案保持幂等。取消关闭待回答问题，启动恢复不重放未知副作用。

Schema v15 增加 Run 模式与计划版本引用、`plans`、`plan_revisions` 和 `user_input_requests`。v14 升级通过 SQLite 表重建扩展 Run 的状态 CHECK，并保留索引与外键检查。数据库是计划内容、版本和确认状态的唯一事实来源。

计划文件位于 `<EIDOS_DATA_DIR>/plans/<session-id>/<plan-id>/plan.md`，默认根目录为 `~/.eidos`。Runtime 用专用工具保存文件，不扩权开放整个数据目录。`write_plan` 经过已有 Durable Intent、结果校验与结果提交流程。文件是数据库内容的可恢复投影。Runtime 记录投影 Hash，并拒绝静默覆盖外部修改。用户可以在界面编辑、打开 MD 文件，或将外部修改载入为新版本。文件访问使用有界读取、无符号链接路径打开和临时文件替换。

`write_plan.readyForReview=true` 保存完整计划并结束当前 Run。用户通过 `plan/edit` 修改正文，或通过新的 Plan Run 提交修改意见。界面确认时提交 `planId + planRevision`。Runtime 在创建普通 Run 的事务中核验所属 Session、当前版本和待确认状态，记录确认并绑定执行 Run。已确认的版本不可修改，执行 Context 从对应的不可变版本读取正文。Main 只根据 Runtime 返回的计划路径打开文件。


Renderer 的计划状态按 Session 隔离，切换会话时不展示旧问题或计划，旧请求的迟到响应也不能覆盖当前状态。澄清表单按请求 ID 重建，答案和跳过状态不跨请求复用。计划卡片只展示已完成且成功的 `write_plan`；失败、取消和拒绝沿用普通工具结果展示。快照中的澄清参数复用 `RequestUserInput` 校验与序列化；无效参数不进入展示投影，原始 ToolCall 记录保持不变。

### Plan 工具结果与写入失败边界

`request_user_input` 和 `write_plan` 构造结果时显式传入各自的结果模型。公共 `tool_result` 将该模型交给既有 canonical 校验，不放宽通用 DTO，也不维护另一份工具名映射。

计划写入先校验 Run、计划所属 Session、版本、确认状态、外部文件修改及正文大小，再提交 Durable Intent。Repository 在事务内重复核验。已知发生在内容写入前的 `PlanWriteRejected` 返回普通工具错误；该错误不留下不确定副作用。提交计划或投影文件之后的异常仍由原有对账流程处理。Schema 明确要求新计划省略 ID 和版本，修改时使用工具返回的值。

### 澄清参数纠错与展示

`request_user_input` 的字段说明提供完整嵌套示例、题型约束和自定义输入说明，工具名称与原始描述保持不变。Pydantic 校验最多保留八项字段路径与错误原因，不回传参数值；参数错误反馈说明问题尚未提交，并给出修正方式。Runtime 的参数错误指纹区分首项字段路径和原因，不包含参数值、错误文案或问题措辞。连续相同校验错误仍受原有 LoopGuard 限制。失败或取消的澄清调用沿用普通工具结果展示，只有成功的回答结果进入澄清历史。

## 子 Agent 委派（Schema v29）

本阶段采用父任务负责分工和汇总的模式。父 Run 通过 `spawn_agent` 创建内部子 Session 和 queued Run。Runtime 复用 `RunSupervisor → RuntimeEngine → ToolCallRuntime`，不增加第二套 Agent Loop、调度服务或数据库。子 Session 复用父任务的 Workspace 身份或 Worktree 绑定，但不拥有 Worktree。子任务使用独立上下文。内置类型继承父模型，自定义类型可以使用已配置的其他模型。子任务继承父 Run 的审批模式和扩展快照；父 Run 活跃时可使用它已批准的 Run 范围 Grant。子任务不继承父任务的完整对话。

Runtime 保留三个内置角色，并支持用户 TOML 定义的自定义类型。省略 `role` 时，Runtime 使用 `default` 处理通用任务。`explorer` 专注具体的代码调查、调用链、命令统计和证据整理。`worker` 负责分配范围内的实现和验证，并保留其他 Agent 的修改。角色记录来自 SQLite；ContextBuilder 根据该记录注入独立的 developer 指令层，续派时保持原角色。自定义定义由 `AgentCatalog` 解析。`model.provider_id` 与 `model.config_id` 成对校验，省略模型设置时仍继承父模型。`instructions` 以来源明确的用户配置层进入子任务，不改变 Runtime 权限。

三个角色共用普通文件、Shell、Skill、图片、只读记忆、产物声明和已启用扩展工具的执行链。MCP 按父 Run 固定扩展快照启动，延迟工具仍通过 `tool_search` 发现。三个角色继承父 Run 的审批模式和权限；`explorer` 是任务分工，不提供强制只读保证。所有操作仍受自身权限快照、审批、Seatbelt、Durable Intent 和结果核验约束，`full_access` 继续使用既有例外。子任务只暴露父消息通道 `send_message`，不暴露委派工具、`request_permissions` 或 `request_user_input`；父任务负责用户交互和直接扩权。子 Session 不能通过普通 `run/start` 独立启动，也不能拥有 Checkpoint。内部子会话不进入普通 Session 列表。父会话删除只清理其事实，不删除借用的目录。

Schema v29 增加 `run_agent_catalogs`，它关联父 Run 与现有 Blob 存储中的冻结目录。`agent_delegations` 增加实际定义和原始创建请求，角色 CHECK 改为有界名称；名称仍由严格 DTO 和固定目录校验。子任务创建与定义关联在同一事务提交。续派更新当前任务，但创建重放继续比较独立的原请求。v28 升级保留原有数据、索引、触发器，并在提交前检查外键；新库和离线恢复接入同一版本链。

输入链路沿用 `input/prepare → run/start.references`。新引用类型为 `agent`，Runtime 保存完整定义供预览，并在 Run 接纳时复核版本。父模型只看到目录摘要与本轮选择；历史引用不会自动激活新一轮。编辑重发和重新生成通过已验证的源 Run 复用原定义。目录修改只影响新 Run，已有实例续派不重读 TOML。

Desktop 通过 `agent/catalog`、typed IPC 和现有 `+` 选择类型，子任务面板展示实际类型、提供商与模型。Blob GC 和现有明文备份保留目录快照，备份同时保存用户 TOML，模型凭证继续排除。子 Run 保留父工作模式，Plan 子任务不暴露 `write_plan`。详细规范和后续范围见 [自定义子 Agent 方案](custom-subagents-design.md)。

Desktop 的设置页和输入框共用智能体编辑表单。模型与思考强度直接使用公共 `ModelSelector`，所选配置提供实际 `provider_id` 与 `config_id`。配置管理沿用 `Renderer → preload → Main → RuntimeClient → Method Registry → AgentDefinitionsApplication`，增加 `agent/definitionRead`、`agent/definitionSave` 和 `agent/definitionRemove`。`AgentDefinitionFiles` 复用 Catalog 的有界安全读取，负责固定目录、文件身份、原始内容版本与非阻塞目录锁。新文件排他创建；已有文件在版本核验后通过受控 fd 原地写入并 fsync，结果再次读取核验。失败不自动重试；写入或删除后无法确认持久化时，接口返回明确的不确定结果。配置仍以 TOML 为来源，没有新增业务数据库或执行循环。DTO 与 Ajv Schema 从 Python 生成，Main 同时校验请求和响应。凭证仍由已有模型配置管理。

v20→v21 重建 `agent_delegations` 的角色 CHECK，允许三个内置角色。迁移保留原角色、任务、完成依赖、索引和触发器，以及其他表中的消息与等待；迁移执行外键检查，失败时回滚。历史任务不改名。旧版本不能打开 v21 数据，回退需要升级前的备份。

SQLite 新增三张表。`agent_delegations` 保存父 Run、子 Session、当前子 Run、角色、任务和创建 ToolCall。`agent_messages` 保存来源明确的 Agent 消息。`agent_waits` 保存等待目标、原 ToolCall 和到期时间。委派创建、子 Run 入队和 Event/Outbox 在同一事务提交。创建 ToolCall ID 和后续委派的确定性 Run ID 防止同一操作重复派生任务。消息不会进入用户输入邮箱，也不会成为用户授权。结果正文仍来自子 Run 的持久 Item，不额外保存第二份结果状态。

父任务可以调用 `send_message`、`followup_task`、`list_agents`、`wait_agents`、`stop_agent`、`read_agent_result` 和 `read_agent_message`。创建任务、发送消息和续派正文统一限制为 64 KiB UTF-8，三个入口的 JSON 参数预算为 512 KiB。`send_message` 返回消息 ID 和 queued 状态，只保存消息，不重启已结束任务；`followup_task` 才会启动空闲子任务。每个父 Run 最多创建 16 个子 Session，同时最多运行 8 个子 Run；名额只在该父 Run 内计数。

主模型默认读取最终结果、简要状态和明确发送的未读消息，不读取子任务的工具轨迹或中间回答。协作工具使用独立投影，只返回简要状态和结果 Item ID；`agent-evidence` 从子会话的原始结果 Item 构建正文，避免在工具返回中重复正文。单结果预览最多 8,192 个估算 token 和 64 KiB；协作正文总目标为 `min(32,768, 可用输入预算 / 4)` 个 token，序列化上限为 512 KiB。预算不足时仍返回状态、原始字节数、截断标记和结果 Item ID。大结果保留开头和结尾，父任务通过 `read_agent_result(agentId,itemId,offsetBytes,maxBytes)` 读取剩余正文，默认每页 16 KiB、最大 64 KiB，也可用 fromEnd 读取尾部。Item ID 固定具体回答，续派后仍能读取该子会话的旧回答。读取入口校验父 Run 对子任务的所有权，只读取完整助手消息，不读取工具参数或工具结果。

模型按顺序读取最早 32 条未读消息，消息也受协作正文预算控制；截断消息通过 `read_agent_message(messageId)` 读取完整正文。成功接受模型响应后，Runtime 从冻结 ContextSnapshot 的结构化消息 ID 元数据保存投递回执；只有完整正文进入请求的消息才获得回执。失败、取消和截断预览不消费消息。已读消息不在后续模型请求中反复注入。容量恢复按结构裁剪正文并重新生成有效 JSON，不再依赖解析已截坏的文本生成回执。Desktop 的状态摘要继续保留 512 字符任务、2,000 字符结果和最近 16 条消息预览；完整会话记录沿用原分页接口。本次不改变 SQLite Schema、并发调度、权限、取消、Durable Intent 或未知副作用语义，也不新增依赖。

`wait_agents` 在没有活动 Shell 且没有后续批次调用时将 Run 原子转为 `waiting_agents`，然后退出 Worker 并释放模型资源。有活动 Shell 时保留其原 Worker，以可取消、有期限的本地等待继续托管进程；等待时不持有数据库事务或执行锁，不占用其他 Run 的名额。等待范围为当前父 Run 的子任务，空列表代表全部子任务。超时范围是 1 秒到 5 分钟。Supervisor 在调度时检查持久等待记录。一个由现有 AnyIO Kernel 管理的定时任务每 500 毫秒补查到期和孤立子任务；持久挂起不占用模型调用或专属等待线程；活动 Shell 的等待只保留原有资源所有者。条件满足后，Runtime 将同一 Run 重新入队，并继续原 ToolCall。父任务结束时等待必需的子任务；`required_for_completion=false` 的补充任务会被停止并等待资源清理。默认值 true 保留原有行为。采样期间若子任务状态或消息发生变化，父任务会重新读取上下文再结束。Plan 提交只等待必需子任务，再停止可选任务并完成资源清理。

用户取消父任务时，Runtime 先保存父任务的取消请求，再取消活跃子 Run。调度器不会启动父任务已取消或结束的子 Run。每次调度都会检查并取消父任务已结束或请求取消的子 Run，即使父任务没有创建等待记录。监督任务在等待期间也会做同样的检查。重启时，只有没有不确定副作用的持久等待可以恢复。普通执行中的子 Run 继续使用现有 Recovery 规则；Runtime 不承诺恢复任意中断采样，也不会重放未知副作用。

Desktop 通过 `agent/read` 和 `agent/stop`、Main 和 preload typed IPC 访问子任务。Python DTO 是新增协议的 Schema 来源，`scripts/generate-collaboration-contracts.mjs` 生成 TypeScript DTO。环境信息显示子 Agent 列表及待审批数量；工作区关闭时，环境信息入口仍会提示待审批数。点击子任务在右侧 WorkspaceDock 展示详情、审批、停止和完整执行记录。主 Session 不嵌入子任务面板。

子 Agent 详情不再提供“加载更早记录”按钮。Renderer 通过现有 `session/read` 自动读取全部历史页，每页请求最多 500 条 Item，并继续使用 Runtime 的响应大小限制。Renderer 合并各页的 Item 和 Run，按 Item ID 去重。`session/read` 保留最近 100 个 Run，并补带当前页 Item 对应的更早 Run；新增 Run 的内容也计入响应大小限制。刷新只响应当前子 Session 的通知，同一详情页的读取按顺序执行，通知在读取期间到来时会触发一次后续刷新。刷新完成前，界面保留已显示的记录。详情关闭或切换后，旧请求不再更新界面。读取失败时，界面提示记录未能全部加载。新增的 AgentPanel 定向测试覆盖多页顺序、旧 Run 信息、通知过滤、通知合并、读取期间补刷新和取消订阅。定向测试结果与未完成人工验收范围见 `docs/current-limitations.md`。

v15→v16 迁移通过 SQLite 表重建增加 `waiting_agents` CHECK 状态，并创建上述三张表。迁移保留原索引，执行外键检查，失败时回滚。v16→v17 增加完成依赖标记、消息回执和实际 Skill 使用租约，采用事务和外键检查，失败回滚。旧版本不能直接打开 v17 数据；回退代码时需要恢复升级前的数据库备份。子编码任务目前共享父目录，独立 Worktree 和变更交付留待后续阶段。并行写入需要父任务协调文件范围并复核 Diff。完整测试结果见当前 PR。

### Loop 门控精简（PR #102）

- Runtime 保留 Durable Intent 和未知结果，但不要求用户核验，不阻止后续操作或 Run 完成，也不自动重放未知操作。
- 内置文件变更对已持久化、来源可信且范围明确的 Workspace 不确定 Intent 按真实准备路径判定冲突，覆盖多文件、移动目标与父子路径；提交窗口内再次检查。未知范围或外部文件 Intent 继续保守阻止变更；其他路径可以继续，不新增跨 Run 锁。
- `send_message`、`stop_agent` 和忙碌子任务的续派消息不因已有不确定副作用被统一阻断；三个角色均有执行工具，新子任务均受屏障约束。`spawn_agent` 和空闲子任务的 `followup_task` 在创建事务中检查屏障，避免角色名或状态竞态绕过限制。权限申请仍遵守现有审批，不清除旧 Intent。
- 所有控制工具可以同批顺序执行。混合批次、Plan 提问和子任务等待有活动 Shell 时保留 Worker 与原资源所有者。只有无剩余批次且没有活动 Shell 的控制等待才释放 Worker；不强制模型改变调用顺序。
- 取消、最终退出、进程组清理、权限/所有权、文件身份/Base Hash、Durable Intent、结果契约以及最终提交事务检查保留。没有全局 Run/Worker 上限或跨 Run Workspace 门控；单父 Run 的 8 个执行中子任务上限保持不变。
- 最终提交前的进程清理不提前关闭 Shell 管理器的接入能力；若事务因迟到输入或子任务状态而推迟完成，后续步骤仍能启动命令。真正退出 Worker 时才永久关闭管理器。带活动资源的子任务等待记录进入、超时、完成和取消日志，不记录任务正文。

### 交互与资源门禁细化（PR #103）

Runtime 按 Session FIFO 执行已接受的 Run。Run 执行期间，Composer 只显示一个操作按钮：草稿有可提交内容时显示发送并将输入排队；草稿为空且 Run 可取消时显示停止。审批和澄清期间，ApprovalComposer 或 ClarificationComposer 替换普通输入框，Session 草稿会在等待结束后继续显示。工作模式、模型、思考强度和审批模式用于下一轮草稿，当前 Run 快照保持不可变。安全的连续只读调用可以在混合批次中并行，副作用仍按顺序执行。搜索保留每 Run 四个实际执行者和十六个会话边界，超出执行槽位的请求进入可取消队列。延迟工具按最近激活顺序保留，再按稳定名称排序冻结。

`QueuedMessages → typed IPC → run/listQueued、run/updateQueued、run/deleteQueued、run/steerQueued → QueuedRunApplication → QueuedRunRepository → SQLite` 是队列列表的调用链。列表直接投影尚未开始的 queued Run 和关联用户消息，不依赖聊天记录的分页窗口。每页最多读取 100 条，并按 512 KiB 预算分页。Renderer 只保存当前投影和临时编辑内容。队列变化通过同事务的 `run.queue_changed` Event / Outbox 投影为 `run/queueChanged`，执行状态通知也会刷新队列。

AppShell 只在已持久化的 Session 中挂载 QueuedMessages。`draft-…` 是未保存草稿的 ID，不能传入只接受 Session UUID 的队列接口。首次发送沿用现有草稿物化流程；Session 创建成功后，队列组件才开始读取。切回草稿会卸载队列组件，并使旧读取结果失效。

队列修改和 FIFO claim 使用同一数据库锁与短事务。修改先检查 Session 归属、未开始状态和原消息文字；文字冲突返回 `QUEUED_RUN_INPUT_CONFLICT`，已经开始、取消或恢复的消息返回 `QUEUED_RUN_ALREADY_STARTED`。编辑同时更新 Run 输入和用户 Item，并保留引用及执行快照。删除移除未执行 Run、用户 Item、执行前快照及其旧 Run 事件，记录新的队列删除事件；删除消息不会作为用户输入进入后续 Run。两种写操作复用现有 Operation 幂等记录，不新增数据库表或第二套调度器。

运行中发送的普通消息默认创建独立 queued Run。当前 Run 的上下文和压缩候选只读取本 Run，以及创建顺序不晚于本 Run 且已经开始的历史 Run。后续排队消息不会提前进入普通采样、最终采样或摘要候选。

用户可以点击队列行的箭头，把指定消息转为当前 Run 的引导。`run/steerQueued` 要求明确的 `targetRunId` 和原消息文字，并在同一事务中检查源消息、目标 Run 和取消状态，创建 `input_mailbox` pending 记录与同 ID 的可见用户 Item，移除源 queued Run，再提交 Item 与队列 Event/Outbox。目标可以处于 running、waiting_approval、waiting_input 或 waiting_agents；finalizing、已经结束或正在取消的目标拒绝转换，原消息继续排队。重复请求复用 Operation 结果。目标 Run 的模型、权限和工作模式快照保持不变。

引导 Item 在“正在处理”内以类似正常用户输入的右侧气泡显示，上方标签为“引导对话”。Runtime 在下一次采样边界消费邮箱，并把同一 Item 标为 completed。消费会把 Item 的 ordinal 与 creation_seq 移到之前采样产生的结果之后，保持模型消息顺序。待消费或取消后仍未消费的 Item 不进入上下文。邮箱、Item 和剩余 FIFO 队列都保存在 SQLite 中，不新增 Schema。普通内容引用随消息保留；扩展 selection、已关联计划和当前模型不支持的图片会拒绝转换并保留队列消息，避免改变已冻结的运行配置。


新 Run 持久保存内部 Skill 元数据快照，公共 Run DTO 不发送该快照；内部恢复读取保持完整。只有实际选择、读取或激活的 Skill 建立租约，其他 Skill 可删除；旧 Run 继续按目录引用保护。资源激活重新检查冻结身份和内容版本，已移除且未使用的 Skill 以普通工具错误反馈，不破坏整个 Run 的恢复。

### 公共模型选择组件

Renderer 的 `components/ModelSelector.tsx` 与 `ModelSelector.css` 统一维护模型选择、ProviderLogo、思考强度和弹层交互。Composer、AutomationForm 和 DingTalkForm 使用该组件。调用方持有模型与思考强度值，组件不保存业务配置。弹层使用 React DOM Portal，并按视口和滚动位置定位。旧 ReasoningSelector 与 Composer 中对应样式已迁移到公共组件。

## 任务结束提示与模型输出截断（2026-10-19）

Desktop 的 `terminalRunPresentation` 统一呈现任务结束原因。提示优先使用持久化的 `termination.reason`，再使用 Run 错误码。文案直接说明已知原因，不重复添加“任务无法继续”，并且不以句号结尾。参数 JSON 解析失败、工具字段不符合要求、输出达到 Token 上限、请求超时、认证失败和访问被拒绝分别呈现。未知原因使用通用说明，不推断网络、凭据或重试结果。部分操作结果未知时，系统如实展示记录，不建立用户核验门。

Pydantic AI Adapter 在解析工具参数前检查响应完成状态。遇到 `length`、`content_filter`、`error` 或非完成状态时，Adapter 保留结束原因、用量与响应 ID，并且不返回可执行工具调用。SamplingRuntime 继续通过原有失败路径持久化这些信息。`length` 的一次有界纠错明确说明输出达到上限，要求模型返回更短且完整的回复，并说明前次回复的工具调用未执行。其他参数解析错误继续使用 `invalid_tool_arguments`。本次不新增依赖，不改变协议、SQLite Schema、权限或纠错次数。

## 容量恢复与未知结果（2026-10-19）

Runtime 保留未知副作用的 Durable Intent、结果和来源，但不要求用户核验，也不阻止后续工具、子任务、定时任务、分叉、完成或取消。系统不自动重放不确定操作。权限、Sandbox、文件身份、版本、取消和事务校验继续生效。

Runtime 不再因工具错误、重复失败、容量限制、模型请求超时、限流或自动重试预算用完而创建持久暂停，也不显示容量暂停与“继续任务”按钮。工具参数错误作为该调用的结果返回模型。重复失败会提示模型跳过无效路径，修正参数、缩小请求或选择其他工具。模型连接、限流与请求超时使用下文的有限重试预算；预算结束后，所属 Run 记录失败，系统不再通过容量恢复重新领取重试次数。系统不自动重放已执行工具。用户主动暂停、真实人工审批、澄清输入和明确父子依赖仍保留原有语义。

普通上下文仍使用模型 Token 预算和压缩。历史组装超限时，系统使用有界预览；原始事实保留在 SQLite。压缩无法继续时，Runtime 会用更小的历史证据视图继续请求模型。系统不会为扩容绕过权限，也不会无界加载历史。`read_tool_output` 支持用 `stream=result` 分页读取当前 Session 已保存的非 Memory 工具结果；`stream=context` 支持按消息 Item ID 分页读取当前 Session 或冻结分叉的原始用户、Assistant 正文。该入口不执行原操作。Memory 结果仍使用自身的权限与撤销路径。

工具结果的完整可接受正文与模型展示预算分开。结果在已有生产者预算和最终资源保护内保存；模型只收到短预览及 callId/itemId 分页入口。Shell 原始捕获上限外的数据仍不可恢复，文件截断正文继续使用 read_file_range 读取。模型正文达到本次输出字节预算时，Runtime 截断预览并要求后续分段继续。签名或加密的推理片段不做内容改写；超大回放片段会整体省略，原操作不会重放。

模型业务上下文不再包含 Runtime 生成的 SHA256、contentHash、pluginHash、workspaceDiffHash、错误指纹或旧结果摘要 Hash。实时工具结果、引用说明、压缩输入、旧摘要和回读标记采用同一规则。用户提供的原文、代码与有意义的 Git 提交标识保持原样。文件版本、Blob 完整性、内部快照、审批证据及 UI 分页校验继续在工程层使用 Hash；这些值不作为模型推理材料。Provider 协议必须的签名和不透明回放数据继续按协议处理。

旧版机器容量暂停通过持久 Event 顺序识别并清除；后续用户主动暂停不会被旧提示误解除。此兼容处理不重新执行任何已完成或不确定的 Tool。

JSON-RPC 仍保持有界控制帧。Runtime 对过大的 RPC 响应返回 RESPONSE_TOO_LARGE，Main 继续拒绝损坏或超限的外部协议输入。真实协议损坏、认证、权限、身份、存储和进程故障仍按既有规则记录，不伪造完成。

### 单次等待参数与真实容量的区分（2026-10-19）

`yieldTimeMs` 只控制一次工具调用等待多久，不限制命令或搜索的总执行时间。`run_shell` 和 `search_text` 将过大的整数等待值收敛到 30 秒；`write_stdin` 和 `search_text_wait` 将该值收敛到 60 秒。工具在等待结束后仍可返回运行中的 Session，模型可以继续轮询。严格类型、最小等待值和其他权限参数校验仍然生效。

普通数值约束错误和无效行范围不再被归类为全局容量不足。系统会把这些问题作为当前工具的参数反馈，模型可以修正参数。正文或结构大小超限使用短预览、分页回读和较小请求恢复，不建立 Run 暂停门。

系统保留具体容量原因用于本地诊断，不再向对话注入要求用户继续的机器暂停提示。


## 模型有限重试与断流恢复（2026-10-20）

普通 SamplingRuntime（含子 Agent）统一管理模型请求重试。新 Run 默认首次请求加最多 5 次重试；连接失败、超时、临时限流、可恢复 HTTP 错误和流中断共用同一请求组。每次尝试独立保存 ModelAttempt，同一请求组复用不可变 ContextSnapshot。前台作用域通过现有 AnyIO portal 传播，关闭 Transport 的内部重试；OpenAI SDK 仍保持 max_retries=0。审批审查、压缩、标题、记忆与特殊 RunFinalizer 沿用原有预算，未统一扩大为 5 次。

Tenacity 负责退避与随机等待，基础时间为 2、4、8、16、30 秒。Pydantic AI 的 wait_retry_after 解析秒数与 HTTP 日期。服务端等待值作为最短时间；系统不会把超长等待截短后提前发送。首次临时失败开启默认 180 秒恢复窗口，等待和后续请求共同消耗窗口，首次正常请求仍沿用模型 I/O 超时。AnyIO CancelScope 限制窗口内请求，并在返回 Engine 前关闭旧流。系统等待时不持有数据库事务或工具执行锁；其他 Session 可继续执行。旧 Run 保留已冻结的时间配置。

Adapter 先读取结构化错误 code/type，再参考 HTTP 状态。余额不足、配额或消费上限、账户停用、认证和参数拒绝不自动重试。HTTP 402 和明确的 insufficient_quota 等代码不会被 429/5xx 重试覆盖。错误正文不会进入持久化诊断。Responses 流中的明确服务繁忙与限流错误使用同一分类；流在完成事件前结束时按中断处理。容量缩减和既有有界协议纠错继续独立运行。模型传输重试耗尽不再转成 CapacityExceeded，因此不会被外层容量恢复循环重新开始。

已显示文字可以恢复，但当前失败响应不能已经执行工具。普通采样仍在完整响应及工具批次校验后才进入 Tool 执行。失败草稿以 failed/incomplete 保存，新 Attempt 创建新 Assistant Item；系统不拼接新旧回答，不把失败草稿放回模型上下文，也不自动重放此前的 Shell、文件写入或 MCP 操作。系统通过当前 Attempt 与快照的实际绑定关系校验输入；Snapshot 中的 model_attempt_id 保留创建来源。每次重试同时更新 StepContext 的当前 Attempt ID。

Schema 30→31 在 model_attempts 增加 request_group_id、retry_index、retry_deadline_at 和 assistant_item_id，并增加请求组内序号唯一索引。旧记录保留空关联，不补造来源。失败 Attempt、关联草稿终态和重试决定在同一短事务提交；草稿事件继续使用已有 Event/Outbox。Writer 拒绝旧 Attempt 的增量回调，完成记录也可按明确 Attempt ID 条件更新。

Runtime 重启只恢复现有 Recovery 允许的纯模型阶段。恢复沿用请求组、已消耗次数、原 Snapshot、恢复期限和已保存的下次请求时间；新消息保留未读，等待下一次正常采样。最后一次可用尝试已经消耗或期限已过时，恢复记录为 interrupted，不重置预算。存在未知操作的 Run 继续沿用原有恢复规则，不重放操作。

本轮只完成 Runtime 两阶段。Desktop 沿用已有草稿和结束提示，没有新增倒计时、暂停状态、继续按钮、模型切换或后台网络监听。SQLite 升级增加列，回退旧版本前需要恢复升级前备份。安装、启动和生产依赖没有变化。

## 排队消息的上下文顺序与空 reasoning 回放（2026-10-20）

排队消息在入队时就保存用户 Item。ContextRepository 按 `runs.creation_seq` 和 Run 内的 `items.ordinal` 选择、排列历史和识别最新用户输入。系统不能用 Item 的全局写入顺序，把排队消息插入上一轮仍在生成的工具记录和最终回复之间。ContextBuilder 沿用这个顺序。压缩候选继续排除当前目标和受保护的近期事实；已有引导仍在消费边界进入所属 Run。修复不改写历史 Item、已保存的 ContextSnapshot、Run 配置或 FIFO 状态，也不增加数据库字段。

思考模式下，Chat Completions Adapter 在已有 response replay、但没有 thinking part 时，使用 Pydantic AI Provider Profile 声明的 `openai_chat_thinking_field` 创建空 ThinkingPart。Pydantic AI 继续负责请求字段映射。纯文本的历史最终回复也会携带空 reasoning 字段，系统不生成或补造思考正文。没有回放记录的历史仍使用原有的历史文本降级；未开启思考或 Profile 未声明字段时，Adapter 不增加这个空 part。工具调用和结果仍按原有批次规则配对，系统不重放工具。

## MCP 设置页工具目录

`readMcpTools` 通过 typed preload IPC → Main 参数校验 → RuntimeClient → `mcp/tools` Method Registry → ExtensionApplication → McpDetails。请求和响应以 Python Pydantic 模型为源，`scripts/generate-mcp-details-contracts.mjs` 生成 TypeScript DTO 和 AJV Schema，两端共用 `protocol/fixtures/mcp-details-v1.json`。

首个请求由 Supervisor 管理的任务完成，JSON-RPC 输入循环可以继续处理其他请求。McpDetails 先读取当前 Plugin/手动 MCP 配置及授权，再使用已有 McpConnection、官方 MCP SDK、沙盒和资源登记执行初始化及工具目录发现。它只读取 `serverInfo.description` 作为服务描述，不执行 Tool。它复用现有 Tool Schema 校验判断条目是否可用，并在 finally 中关闭连接。

设置页目录是有界的短期展示缓存，不是第二套业务状态。每个目录保存本次发现的工具及获取时间，具有随机目录 ID、配置绑定和 5 分钟有效期；系统最多保留 8 个、每个 512 KiB 的目录。后续查询重新校验服务授权和配置，以游标在同一目录中搜索并返回最多 10 条。用户撤销授权时不会继续返回条目。缓存不写入 SQLite，不更改 Run/Step Extension Snapshot、Tool Registry 或审批规则。旧游标在 Runtime 重启后失效，页面允许重新获取。
