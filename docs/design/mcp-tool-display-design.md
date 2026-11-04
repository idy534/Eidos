# MCP 工具调用展示优化设计

状态：P1–P3 已于 2026-11-04 实施，P4 未实施。当前行为以源码、[浏览器操作当前说明](../current/features/browser.md) 和 [插件说明](../current/features/plugins.md#feed-展示) 为准；实施与设计的差异见第 8 节。

## 1. 背景

用户在 0.5.5 中让 Agent 用浏览器扫码登录哔哩哔哩，暴露了两个问题：

1. Agent 调用 `browser_take_screenshot` 后，执行流里看不到截图。
2. 所有 MCP 工具行展开后只显示 `Plugin eidos-browser · Server browser · 1414ms`。这行信息对用户几乎没有用：插件和服务器是内部 ID，毫秒耗时也不说明发生了什么。用户真正想知道的是：做了什么、作用在哪个对象、返回了什么、是否成功。

第三方 MCP 工具的问题更严重：标题是 `已运行 mcp__github__create_issue`，展开后只有 `MCP tool completed` 和上述来源行，参数和返回内容都看不到。

## 2. 现状与根因

### 2.1 截图不显示

调用链（源码位置为 2026-11-04 的 `main`）：

```text
Playwright MCP ImageContent
  → extensions/mcp.py _verified_image → ToolContentSnapshot
  → db/repositories/execution.py：写入 json_blobs("tool-result-content")
  → 只把引用写入 model_result_json.modelContentSnapshot
  → context/builder.py 读取它，作为模型输入
```

`ui_result_json` 只有 Playwright 的文本，Renderer 也没有读取工具图像的 RPC。截图字节已经持久化，但只有模型能读。本地数据库也证实了这一点：两次截图的 Blob 分别是 945 KB 和 469 KB，都只被 `model_result_json` 引用。

另外还有三个相关缺陷：

- 当前模型不支持图像时，`browser_launch.py` 会传入 `--image-responses omit`。这时 Playwright 根本不返回图像，界面无论怎么改都没有截图可显示。
- 界面文本里的截图链接是 `../../../../../private/tmp/eidos-mcp-*/tmp/browser-output/page-*.png`。这个路径在 MCP 沙盒的私有临时目录里，Workspace 外不可读，连接关闭后还会被删除，模型和用户都打不开。
- JSON-RPC 单条消息上限是 1 MiB（`desktop/main/runtime-client.ts` 的 `MAX_MESSAGE_BYTES`），一张截图就可能超过，不能一次性传给 Renderer。

### 2.2 展开内容没有信息量

`ExecutionFeed.tsx` 对 MCP 工具的展开内容：

- 浏览器工具的 `MCP tool completed` 被当作冗余摘要隐藏，所以只剩来源行。
- 非浏览器 MCP 工具：摘要固定为 `MCP tool completed`；`data.text` 和 `structuredContent` 只在超过内联大小、生成 `resultHash` 时才能通过“查看完整结果”看到；参数从不展示。
- 来源行对所有 MCP 工具都显示 `Plugin {pluginId} · Server {serverId} · {ms}ms`。
- MCP `isError: true` 只显示错误码 `mcp_tool_error`，看不到服务端返回的错误文本。

浏览器工具的标题已经比较好（`点击 · www.bilibili.com`），但 Renderer 是用正则从 Playwright 文本里提取 `Page URL:` 得到的，比较脆弱。

## 3. 设计目标

- 截图类结果在执行流中直接可见，与模型是否支持图像无关。
- 每个 MCP 工具行展开后回答四个问题：做了什么、作用对象、返回什么、是否成功。
- 内部 ID、原始 Playwright 代码和沙盒路径默认不打扰用户，需要时能查到。
- 不新增状态权威：图像与结果仍以 SQLite 和现有 JSON Blob 为唯一事实来源，Renderer 只经 Runtime 读取。
- 保持现有安全边界：Renderer 不获得文件权限，读取有界、分块、校验 SHA-256。

非目标：不改 MCP 授权、审批和沙盒语义；不做 computer use；不为第三方 MCP 写按服务器定制的渲染器。

## 4. 方案

### 4.1 展示内容与模型内容分离（Runtime）

一次工具结果持久化时，图像只写入一个 Blob，但区分两种引用：

| 字段 | 位置 | 用途 |
| --- | --- | --- |
| `contentSnapshot` | `ui_result_json` | 界面展示；不受模型能力影响 |
| `modelContentSnapshot` | `model_result_json` | 模型输入；仅当本次 Run 的模型支持图像时写入 |

两个字段引用同一个 `tool-result-content` Blob，不重复存储。Blob GC 已按引用保留，两个字段都需要计入引用。

`ui_result_json` 中增加有界的附件元数据，供 Renderer 直接渲染占位和尺寸：

```json
"attachments": [
  { "index": 0, "kind": "image", "mime": "image/png", "bytes": 709312, "sha256": "…" }
]
```

内置浏览器始终以 `--image-responses allow` 启动。`supports_images` 只决定是否写入 `modelContentSnapshot`；模型不支持图像时，模型文本补一句“截图已显示给用户，当前模型无法查看图像”，避免模型反复截图。

沙盒路径改写：对内置浏览器服务器，Runtime 在投影结果时把指向 `--output-dir` 的 Markdown 链接替换为 `[截图 1]` 这类占位。模型和界面都不再看到不可访问的私有路径；原始文本保留在 `result_json`，供诊断使用。

历史数据：已有的截图行没有 `contentSnapshot`。读取时如果 `ui_result_json` 缺少该字段，但 `model_result_json` 有 `modelContentSnapshot`，就回退使用后者，因此旧会话的截图也能显示，无需 Migration。

Fork、Rewind 和 Session 历史复制工具行时，要同时复制两个引用（`persistence/session_history.py` 目前只处理 `modelContentSnapshot`）。

上下文投影的“只保留最新 2 张截图”只影响模型输入，界面保留全部截图。

### 4.2 读取工具附件（协议）

沿用 `readInputAsset` 和 `eidos-preview` 的现有模式，新增：

- RPC `toolCall/readContent { sessionId, toolCallId, index, sha256?, offset }` → `{ data(base64), mimeType, sizeBytes, nextOffset, complete, sha256 }`。单块不超过 512 KiB，满足 1 MiB 消息上限。Runtime 校验工具行属于该 Session、附件序号存在且 SHA-256 一致；不一致时返回稳定错误码 `tool_content_changed`。
- Main 的 `ArtifactPreviewManager` 增加第三种授权 `kind: "toolContent"`。`prepareToolContent(sessionId, toolCallId, index, sha256)` 返回 `eidos-preview://<token>/screenshot.png`，协议处理器按块调用上述 RPC 并流式返回。
- Renderer 用这个 URL 作为 `<img src>`，复用 `ViewImageViewer` 的缩略图和灯箱。

需要同步：Python DTO、生成的 TypeScript 类型和校验器、双端 Fixture、preload 类型、IPC 通道。

### 4.3 MCP 工具行通用展示（Renderer）

#### 标题

| 情况 | 现在 | 改为 |
| --- | --- | --- |
| 内置浏览器 | `点击 · www.bilibili.com` | 保持现有格式，主机名改由 Runtime 投影提供 |
| 第三方 MCP，有 `title` | `已运行 mcp__github__create_issue` | `GitHub · Create issue` |
| 第三方 MCP，无 `title` | 同上 | `GitHub · create_issue`（去掉 `mcp__` 前缀与服务器段） |

服务器显示名来自插件清单中的名称（Renderer 已加载插件列表）。工具 `title` 来自 MCP Tool 定义：Runtime 在 `_tool_entry` 中把 `tool.title` 或 `annotations.title` 写入 `ToolProvenance.displayTitle`（可选，最长 120 字符）。这项协议改动向后兼容。

标题后附一个关键参数：取 Schema 中第一个必填的字符串参数，截断到 40 字符。

#### 展开内容

按以下顺序分区，空区不显示：

1. **结果**
   - 图像附件：缩略图（最高 240px，点击放大），多张横向排列。
   - 文本：Markdown 渲染，默认显示前 12 行，可“展开全部”。超过内联上限的内容仍走现有 `ToolTextView` 分页。
   - `structuredContent`：折叠的 JSON 树，标题为“结构化结果”。
2. **参数**：键值表，默认显示前 4 个字段，长值截断，可展开完整 JSON。字段名使用 Schema 中的 `title`，没有时用原字段名。
3. **页脚**：一行弱化文字，只放有用的信息：
   - 来源：`GitHub（插件）`、`浏览器（内置）`。用显示名，不用 ID。
   - 耗时：只在 ≥ 2 秒时显示，格式为 `耗时 25 秒`，不显示毫秒。
   - 副作用状态：`sideEffectsMayExist` 且结果不确定时，显示 `结果未知：操作可能已执行`。
   - 原始 `pluginId / serverId / toolName` 放在页脚的悬停提示里，供排查用。

#### 错误

- MCP 返回 `isError: true` 时，用红色显示服务端的错误文本（前 6 行），而不是 `mcp_tool_error`。
- Eidos 错误码使用中文说明，并在可恢复时给出操作。新增一个映射表，例如：

| 错误码 | 显示 | 操作 |
| --- | --- | --- |
| `mcp_connection_lost` | MCP 服务器连接中断 | 打开 MCP 设置 |
| `mcp_tool_canceled` | 已取消 | — |
| `mcp_result_too_large` | 返回内容超过 10 MB 上限 | — |
| `browser_unavailable` | 未找到 Google Chrome | — |
| `browser_profile_in_use` | 浏览器配置正被其他程序占用 | — |
| `browser_runtime_not_ready` | 浏览器组件尚未下载 | 下载资源 |

未知错误码回退显示原值。

### 4.4 内置浏览器的结构化结果（Runtime 投影）

Renderer 解析 Playwright 文本过于脆弱。Playwright MCP 版本固定为 0.0.84，因此改由 Runtime 解析：`runtime/browser_tools.py` 只对内置浏览器服务器，把文本投影为 `ui_result_json.data.browser`：

```json
"browser": {
  "page": { "url": "https://www.bilibili.com/zh-Hans", "title": "bilibili" },
  "tabs": [{ "index": 1, "title": "bilibili", "url": "…", "current": true }],
  "result": "Found 1 match …",
  "code": "await page.getByRole('link', { name: '登录' }).click();",
  "snapshotLines": 312,
  "console": { "errors": 0, "warnings": 2 }
}
```

解析失败时不写该字段，界面回退到 4.3 的通用展示。解析器带固定样例测试，覆盖 0.0.84 的每个已开放工具；Playwright 升级时这些测试会提醒同步。

各工具的展开内容：

| 工具 | 展开内容 |
| --- | --- |
| 截图 | 截图缩略图 + 页面卡片 |
| 打开网页、返回 | 页面卡片（标题 + 完整 URL） |
| 点击、悬停、拖动、选择、按键 | 操作描述（`点击「登录」`）+ 操作后的页面卡片（URL 变化时高亮） |
| 输入、填写表单 | 字段与输入内容；目标为密码框或字段名包含密码时显示为 `••••` |
| 读取页面、查找 | 页面卡片 + `页面结构 312 行` + 折叠的匹配结果或快照预览 |
| 等待 | 标题写成 `等待 25 秒` 或 `等待「登录成功」出现` |
| 标签页 | 标签列表，当前页加标记 |
| 控制台、网络请求 | 条数统计 + 前 10 条预览 |
| 上传文件 | 文件名列表 |

所有浏览器工具都可以展开“技术详情”：Playwright 代码、元素 ref 和原始文本，默认折叠。

**页面卡片**：一行标题加一行灰色 URL。点击时把 Eidos 的 Chrome 窗口切到前台并激活对应标签页，由现有 BrowserHost 处理，不在系统默认浏览器中打开。

### 4.5 工具组标题（可选）

连续的浏览器调用被折叠为“调用 3 项工具”。可以改为 `浏览器 · 打开网页、点击、截图 · bilibili.com`：最多列 3 个动作，再显示最后的主机名；组内有截图时，在标题末尾显示一张 20px 缩略图。这一项依赖 4.4，可以放在最后。

## 5. 分阶段实施

| 阶段 | 内容 | 主要改动 | 验收 |
| --- | --- | --- | --- |
| P1 截图可见 | 4.1 + 4.2 + 截图缩略图 | `extensions/mcp.py`、`db/repositories/execution.py`、`persistence/session_history.py`、`runtime/browser_launch.py`、协议与 Fixture、`artifact-preview.ts`、`ExecutionFeed.tsx` | 模型支持和不支持图像时都能显示截图；旧会话截图可见；Fork 后截图仍可见；读取时校验 SHA 和 Session；超过 1 MiB 的截图能分块读取 |
| P2 通用 MCP 行 | 4.3：标题、结果、参数、页脚、错误映射 | `_tool_entry`（`displayTitle`）、`ToolProvenance` DTO、`ExecutionFeed.tsx` 拆出 `McpToolBody` 组件 | 第三方 MCP 能显示参数、文本结果、结构化结果和服务端错误；页脚不再显示内部 ID |
| P3 浏览器结构化 | 4.4 投影与按工具展开 | `runtime/browser_tools.py`、`ExecutionFeed.tsx` 的 `BrowserToolBody` | 0.0.84 各工具有样例测试；解析失败回退到通用展示；密码输入被遮蔽 |
| P4 组标题（可选） | 4.5 | 工具组折叠逻辑 | 窄窗口下截断正确 |

P1 修复的是缺陷，可以单独发版；P2 和 P3 是体验改进。按 `AGENTS.md` §24，三者围绕同一目标，可以在一个分支中分 Commit 提交。

## 6. 测试

- Runtime：
  - 模型不支持图像时，`contentSnapshot` 存在，而 `modelContentSnapshot` 和模型上下文中都没有图像；
  - 旧数据回退读取；
  - Fork 复制两个引用，Blob GC 不会删除仍被引用的截图；
  - `toolCall/readContent` 的越权 Session、错误序号、SHA 不匹配和分块边界；
  - 沙盒路径改写；
  - 浏览器文本投影的固定样例。
- 协议：双端 Fixture 覆盖新 RPC 和 `displayTitle`。
- Main：`toolContent` 授权的所有者校验、数量上限、窗口关闭后失效。
- Renderer 行为测试：
  - 截图缩略图和灯箱；
  - 加载失败时显示重试；
  - 第三方 MCP 的参数表和错误文本；
  - 页脚只在 ≥ 2 秒时显示耗时；
  - 密码遮蔽。
- 人工验收：重跑哔哩哔哩扫码登录流程，检查截图显示、窄窗口和深色模式。

## 7. 风险与取舍

- **存储**：截图原本就已写入 Blob，改为始终请求图像后，只有不支持图像的模型会多存截图。单张受 10 MB 上限约束；单次结果最多 `MAX_RESULT_IMAGES` 张。如果后续需要，可以在设置中提供“清理工具截图”，本期不做。
- **隐私**：截图可能包含扫码二维码、个人信息等页面内容。截图本来就保存在本地 SQLite Blob 中，本方案只让它在本机界面上可见，不新增外发路径。
- **Playwright 文本格式变化**：结构化投影只针对固定版本，解析失败时回退通用展示，不影响执行。
- **不采用的方案**：
  - 把截图另存到 Workspace 再用 `view_image` 的路径预览：这会在用户项目中产生文件，而且 Workspace 外的会话无法使用。
  - 用 base64 内联进 `ui_result_json`：会超过 JSON-RPC 消息上限，也会拖慢 Feed 加载。

## 8. 实施记录（2026-11-04）

与设计的差异：

- **没有新增 `contentSnapshot` 字段**。截图 Blob 原本就由 `model_result_json.modelContentSnapshot` 引用，并已随 Fork 历史一起保留，Renderer 读取时直接使用它。模型能否看到图片仍由 `model/tool_content.py` 按当前模型能力决定，因此不需要第二个引用。`ui_result_json` 只增加 `attachments` 元数据。
- **旧数据回退**：只对 `browser_take_screenshot` 行补出附件元数据（该工具每次只返回一张图），其他旧 MCP 图片结果不补。
- **读取协议**：首块不需要 SHA-256，回复带回 Blob SHA-256，后续分块用它固定内容。
- **沙盒路径改写**发生在结果投影阶段，`result_json` 保留原文；模型结果和 UI 结果都使用改写后的文本。
- **文本结果**用等宽纯文本显示，不做 Markdown 渲染。
- **错误说明**只有中文文本，没有“打开设置”“下载资源”等操作按钮。插件行已有下载与重试入口。
- **页面卡片**只显示，不能点击切回 Chrome 标签页；BrowserHost 目前没有激活标签页的接口。
- **插件显示名**由 AppShell 通过 `McpPluginNamesContext` 提供，没有写入扩展快照。
- **新发现**：Playwright 0.0.84 在点击、输入等操作后把页面快照写入私有文件，模型实际拿不到。投影现在会提示模型调用 `browser_snapshot`；是否改为内联快照另行评估。

验证：

- Runtime：`test_browser_result`、`test_browser_tools`（含不支持图片的模型、分块读取、越权 Session、SHA 不一致、旧数据回退）、`test_mcp_display_title`，以及受影响的 MCP、上下文、Fork 和工具契约测试。
- Main：`runtime-client-tool-content.test.ts`（共享 Fixture `protocol/fixtures/tool-content-v1.json`）、`runtime-client-capacity`、`runtime-client-browser`、`workspace-preview`。
- Renderer：`McpToolDetails.behavior.test.tsx`、`ExecutionFeed.behavior.test.tsx`、`ExecutionFeed.test.tsx`、`ApprovalComposer.behavior.test.tsx`。
- 未运行 Full Suite、Electron smoke 和真实 Chrome 端到端测试；界面没有在运行中的 App 里人工验收。

### 展示精简（2026-11-04，实施后调整）

在样例中查看后，原来的页脚和“技术详情”在每行都会出现，但信息量不大，因此做了以下调整：

- 去掉来源行（“浏览器（内置）”“GitHub（插件）”）：行标题已经写明来源。不再在悬停提示里放内部 ID。
- 页脚改为一行弱化的附加信息，只在有内容时出现：控制台错误数、2 秒及以上的耗时，以及“原始输出”或“结构化结果”切换按钮。点击后在下方展开，按钮变为“收起…”。
- 去掉“技术详情”折叠框和单独的 Playwright 代码字段（Runtime 不再生成 `data.browser.code`），代码仍在原始输出中。截图行不提供原始输出。
- 页面卡片改为单行：页面标题加去掉协议的短地址，不加边框。
- 点击、悬停、按键和等待的动作描述与标题重复，已删除；输入、选择、拖动和表单字段仍然显示。
