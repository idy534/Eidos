# 浏览器操作

本文描述当前源码中的 Agent 浏览器能力。设计过程与选型见[浏览器操作能力设计方案](../../design/browser-automation-design.md)。Workspace Dock 里的 Browser Tab 仍是用户自己操作的预览面板，和本文的 Agent 浏览器相互独立。

## 入口与准备

浏览器能力由内置插件 `eidos-browser`（`runtime/eidos_runtime/resources/plugins/browser/`）提供。用户在 Settings → Plugins 的内置插件中安装并启用插件，再在插件资源中授权其 MCP 服务。插件行显示 Google Chrome 检测结果、资源包状态，以及浏览器是否正在运行。

- 浏览器工具由固定版本的 `@playwright/mcp@0.0.84` 执行。它和依赖位于 `browser` 资源包，并依赖已有的托管 Node（`node` 资源包）。安装或启用插件时，以及 Runtime 启动时插件已启用的情况下，资源包都在后台准备。Run 启动不会下载资源；未就绪时，该 Run 不提供浏览器工具；插件行显示资源状态，下载失败或缺失时提供“重试下载”或“下载资源”按钮。插件资源中的工具列表与 Run 使用同一套启动参数，资源未就绪时显示具体原因（如 `browser_runtime_not_ready`），不会显示笼统的 `mcp_protocol_error`。启动命令显示为“Eidos 托管 Node · Playwright MCP”，不显示内部占位变量。
- Eidos 不随包提供浏览器，需要用户已安装 Google Chrome（`/Applications` 或 `~/Applications`）。未安装时，浏览器工具在审批前返回 `browser_unavailable`，Run 不会终止。Eidos 不下载 Chrome for Testing，也不改用其他浏览器。
- 插件行提供“打开浏览器登录”（只启动 Chrome，不连接 Agent）、“关闭浏览器”、“清除浏览器数据”（需要确认），以及“使用我的 Chrome”（需要高危确认）。

## 调用链

```text
Model → ToolCallRuntime → ExternalToolHandler（浏览器授权）
  → McpToolAdapter（mcp__browser__*）
  → Playwright MCP（托管 Node，browser Seatbelt 配置，cwd 为 Workspace）
        │ CDP，127.0.0.1
        ▼
     Chrome：Eidos 专用 profile（BrowserHost 启动）或用户自己的 Chrome（用户开启远程调试）
```

- `BrowserHost`（`infrastructure/browser_host.py`）由 RuntimeServer 创建，作为持久受管任务运行，所有 Session 共用。它在 Seatbelt 外启动 Chrome。Chrome 保留自身的渲染沙盒，使用 `<数据目录>/browser/profiles/default`（`0700`）。CDP 端口在 MCP 启动前分配，Chrome 运行期间固定。Chrome 只在第一次浏览器操作时启动，30 分钟无调用、用户关闭或 Runtime 退出时停止。Runtime 重启后，BrowserHost 通过私有状态文件和 Chrome 的 `SingletonLock` 接管仍在运行的 Chrome。
- BrowserHost 为每个 Chrome 保持一条控制用的 CDP 连接，并设置 `Browser.setDownloadBehavior: deny`。Chrome 在设置方断开后会恢复默认，所以连接必须保持打开；断开后，下一次工具调用前会重建。无法设置拒绝下载时，Chrome 会被停止，并返回 `browser_start_failed`。
- MCP 进程按 Run 启动。Run 结束只关闭 MCP，不关闭 Chrome，后续 Run 可以继续使用已打开的页面。启动参数固定 `--no-webmcp`，网页不能注册工具；参数还固定 `--ignore-roots`，上传根目录为 Workspace。
- 只有内容 Hash 与随应用发布版本一致的 `eidos-browser` 包，才能使用 `browser` 沙盒配置和浏览器启动变量。同名的本地包会被拒绝，错误码为 `mcp_permission_profile_unsupported`。

## 沙盒

`sandbox/mcp_browser.sbpl` 只允许以下访问：

- 连接本机地址（Chrome CDP）；
- 只读访问 Workspace、插件包和托管 Node 与浏览器资源；
- 写入该连接私有的 HOME 和 TMPDIR。

外部网络、Workspace 写入和用户目录读取都会被拒绝，真实 Seatbelt 测试覆盖了这些规则。Seatbelt 的 localhost 规则也覆盖本机的局域网地址，但只能到达本机。`full_access` 沿用现有例外，MCP 进程不使用 Seatbelt。

## 工具与审批

`domain/browser.py` 是固定上游版本的工具白名单。不在表中的工具（包括 `browser_run_code_unsafe`、`browser_evaluate`、`browser_emulate_media` 和上游新增工具）不会注册。上游的 `readOnlyHint` 不能改变 Eidos 的分类。

| 分类 | 工具 | 授权 |
| --- | --- | --- |
| 读取 | snapshot、screenshot、find、console、network、wait_for | 不审批 |
| 页面操作 | navigate、back、click、type、fill_form、select、press_key、hover、drag、drop、tabs、dialog、resize、close | Session 浏览器授权 |
| 本地文件 | `browser_file_upload`（带 `paths`） | 每次审批；只允许 Workspace 内的普通文件 |

- Session 中第一次页面操作会生成 `browser_access` 审批（`grantScope: session`）。批准后，Runtime 写入 `session_browser_grants`，该 Session 后续的 Run 不再询问。`auto_review` 对每个 Session 只审查这一次。用户可以在 Session 顶部的“浏览器已授权”处撤销授权。
- 授权绑定插件内容 Hash。插件内容变化、清除浏览器数据或撤销后，下一次页面操作会重新询问。Fork 不继承授权。子任务在父 Run 活跃时使用父 Session 的授权。
- `browser_file_upload` 不带 `paths` 时只取消文件选择框，只需要 Session 授权。
- `full_access` 不创建授权，直接执行。
- Chrome 只会在授权并提交 Durable Intent 之后启动。启动失败是已知的工具错误，不会记录为未知结果。

## 使用用户自己的 Chrome

用户在插件行选择“使用我的 Chrome”并确认后，目标切换为 `user_chrome`。用户需要先在 Chrome 中打开 `chrome://inspect/#remote-debugging` 开启远程调试。Runtime 读取 Chrome 写入的 `~/Library/Application Support/Google/Chrome/DevToolsActivePort`，确认端口可用后，把浏览器 WebSocket 地址交给同一个 Playwright MCP。Chrome 会对每次连接弹窗请用户确认。

- Eidos 不启动、不配置用户的 Chrome，也不为其设置下载规则。
- 在 `manual` 和 `auto_review` 下，每个 Session 第一次操作时，都需要高危人工确认（`requiresHumanConfirmation`）。`auto_review` 不能代替这次确认。授权使用独立的 `user` profile，Eidos 浏览器的授权不覆盖用户的 Chrome。`full_access` 沿用现有例外，不逐次确认；选择“使用我的 Chrome”本身仍需要用户在 Desktop 中确认。
- 改回 Eidos 浏览器时，所有 Session 对用户 Chrome 的授权都会撤销。
- 远程调试未开启或端口失效时，该 Run 不提供浏览器工具；已启动的调用返回 `browser_attach_unavailable`。

## 结果与上下文

- MCP 适配器支持 PNG/JPEG `ImageContent`。图片按 `view_image` 的规则校验格式、大小和像素，存入 `tool-result-content` Blob，`model_result_json.modelContentSnapshot` 保存引用。无效图片返回 `mcp_image_invalid`；音频和资源内容仍返回 `mcp_content_unsupported`。这条规则适用于所有 MCP。
- 内置浏览器始终以 `--image-responses allow` 启动，截图与模型能力无关地保存。模型请求按当前模型能力编码：不支持图片的模型只收到“已省略图片”的文字说明（`model/tool_content.py`）。
- 结果提交时，`ui_result_json.attachments` 记录每张图片的序号、MIME 和字节数，不复制图片字节。2026-11-04 之前保存的截图没有该字段；读取 `browser_take_screenshot` 行时，若模型结果带图片引用，映射层补出 `[{index: 0, kind: "image"}]`。
- `toolCall/readContent` 按 Session、工具调用和序号分块读取图片（每块 512 KiB 原始字节，base64 后单条回复小于 1 MiB），返回 Blob 的 SHA-256；带 `sha256` 的后续读取在内容变化时返回 `INVALID_CURSOR`。实时行与 Fork 继承的历史行都可读取。Main 的 `eidos-preview` 协议新增 `toolContent` 授权，Renderer 通过 `prepareToolContentPreview` 取得一次性 URL，与输入引用共用释放入口。
- Playwright 把截图、快照和控制台日志写入私有 `--output-dir`，连接关闭后即删除。Runtime 的结果投影（`domain/browser_result.py`）把这些链接替换为说明文字，例如“Screenshot of viewport (attached to this result)”、“Snapshot (not retained; call browser_snapshot to read the page)”。模型和 Feed 都不再看到沙盒路径；`result_json` 保留原文。
- 同一投影为 Feed 生成 `ui_result_json.data.browser`：页面 URL 与标题、标签页、控制台错误数、`### Result` 文本、Playwright 代码和内联快照行数，各字段有长度上限。解析失败时不写该字段。结果超过 64 KiB 被分页时，`attachments` 和不超过 16 KiB 的 `browser` 视图仍保留在摘要中。
- 较早的成功浏览器结果会参与已有的旧结果清理，只保留状态、512 字符页面预览和 `read_tool_output` 回读入口。每次请求只保留最近两张浏览器截图。SQLite 中的原始结果和 Feed 中的截图不受影响。
- Feed 把浏览器工具显示为动作行，例如“点击 · 登录按钮 · example.com”、“等待页面 · 25 秒 · bilibili.com”。展开后依次显示：错误、动作描述（“点击「登录」”；字段名含密码、token 等词时输入内容显示为 `••••`）、截图缩略图（点击放大，失败可重试）、页面卡片、控制台错误数，以及默认折叠的“技术详情”（Playwright 代码和原始文本）。第三方 MCP 的展示见[插件](plugins.md#feed-展示)。

## 数据与协议

- SQLite v48 新增 `session_browser_grants`（每个 Session 和 profile 最多一条有效授权；保留来源审批与撤销原因）和 `browser_settings`（目标浏览器）。启动、旧布局升级和备份恢复都会执行 v47→v48 迁移，失败时回滚。删除 Session 时，系统在删除审批前删除其授权记录。
- 新增 Event：`browser.grant_changed`（投影为 `browser/grantChanged`）和 `browser.settings_changed`（不投影）。
- 新增方法：`browser/status`、`browser/revokeGrant`、`browser/setTarget`、`browser/open`、`browser/close`、`browser/clearProfile`。`browser/status` 与后三个方法由受管任务回复，不阻塞 JSON-RPC 输入循环：Chrome 启动期间状态查询需要等待浏览器宿主锁，资源状态只核验 browser 资源包，不再核验全部资源包。Runtime 进入 draining 时 `browser/status` 在输入循环内直接回复。DTO 由 `scripts/generate-browser-contracts.mjs` 从 Pydantic 生成，两端共用 `protocol/fixtures/browser-v1.json`。
- 审批新增 `browser_access` 类型。外部工具审批和 MCP 列表接受 `browser` 权限配置；手动 MCP 仍只能选择 `connector` 或 `workspace_read`。

## 限制

- 不同 Session 共用 Chrome，Tab 互相可见。目前只有 Skill 要求模型在自己的 Tab 中工作，Eidos 没有记录 Tab 归属。
- 授权不按来源网站限制，也不提供多个 Eidos profile。
- 下载被拒绝，`browser_evaluate` 被屏蔽。
- Playwright 0.0.84 在点击、输入等操作后把页面快照写入文件而不是内联返回，模型需要再调用 `browser_snapshot` 才能读取新页面。
- 页面卡片只显示，不能点击切换 Chrome 的标签页。
- 资源包与 App 分开发布。0.5.5 发布时遗漏了资源 Release `document-resources-2026.11.03.1`，导致浏览器和文档资源下载 404；该 Release 已于 2026-11-04 补发。打包脚本现在会检查资源索引中的每个下载地址：Release 模式下不可下载会失败，本地模式会输出警告。
- 未验证：
  - 打包后的 App 中的完整流程；
  - Runtime 启动 Chrome 时，macOS 是否弹出本地网络权限提示；
  - 用户真实 Chrome 开启远程调试后的连接与弹窗。

## 验证记录（2026-11-03）

- 定向 Runtime 测试：`test_browser_runtime`、`test_browser_sandbox`（真实 Seatbelt）、`test_browser_mcp`、`test_browser_host`（含真实 headless Chrome 启动与接管）、`test_browser_grants`、`test_browser_tools`、`test_browser_context`、`test_user_chrome`，以及受影响的 MCP、存储 Schema、备份和迁移测试。
- 端到端测试 `test_browser_e2e`（需要设置 `EIDOS_BROWSER_E2E_NODE` 和 `EIDOS_BROWSER_E2E_ENTRY`）：使用真实 Playwright MCP、`browser` Seatbelt 和 BrowserHost 启动的 Chrome，覆盖导航、快照、截图进入模型上下文、跨 Run 复用 Tab，以及下载被拒绝。
- Main：`runtime-client-browser.test.ts`（审批投影、伪造审批的拒绝、通知、真实 Runtime 往返）和 `runtime-client.test.ts`。
- Renderer：审批卡片、Session 授权指示、插件行、Feed 动作行，以及受影响的 AppShell、欢迎页和插件设置测试。
- 未运行 Full Suite、Electron smoke 和打包验证。
