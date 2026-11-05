# 与用户共用 Chrome（标签组模式）设计

状态：S0 验证完成（第 7 节）；P1（Runtime、协议与 Main）与 P2（设置界面）已于 2026-11-04 实施，记录见第 8、9 节；P3 未开始。当前行为以 [浏览器操作](../current/features/browser.md) 为准。当前浏览器能力见[浏览器操作](../current/features/browser.md)，原方案见[浏览器操作能力设计方案](browser-automation-design.md)。

## 1. 目标

Agent 直接使用用户正在用的 Google Chrome，而不是另外启动一个空白的 Eidos 浏览器：

- 共用用户的登录状态、扩展和窗口；
- Agent 只操作一个带颜色的标签组里的标签页，用户一眼能看出哪些标签页归 Agent；
- 用户把标签页拖进组，就交给 Agent；拖出组，Agent 立即失去控制；
- 不同 Session 使用不同的组，互不干扰。

独立的 Eidos 浏览器仍保留，并继续作为默认选项。“使用我的 Chrome（标签组）”是用户主动开启的可选模式。

## 2. 调研结论

### 2.1 不需要自研扩展

我们固定的 `@playwright/mcp@0.0.84`（底层 `playwright-core 1.65.0-alpha-2026-10-30`）已经带有 `--extension` 模式。配套的官方扩展是 Chrome 应用商店中的 “Playwright Extension”：

- 扩展 ID 为 `mmlmfjhmonkocbjadbfplnigmagldckm`；
- 2026-11-03 发布的版本为 0.5.0，约 10 万用户，Apache-2.0 许可；
- 权限为 `debugger`、`tabs`、`tabGroups`、`activeTab`。

源码位于 `microsoft/playwright` 的 `packages/extension`。阅读源码后确认，它已经实现了本方案需要的全部标签组行为（`connectedTabGroup.ts`）：

- 每个连接的客户端有一个独立标签组，标题为 `Playwright · <客户端名>`，同名时自动加序号，颜色依次为绿、蓝、紫、橙等；
- **标签组是 Agent 可控标签页的唯一来源**：拖入组就连接调试器，拖出组就断开；Agent 新开的标签页和弹窗会被自动放进组；
- 连接断开时解散该组，标签页保留在浏览器中；
- 扩展的服务进程重启后，会清理遗留的 Playwright 组。

### 2.2 连接链路

```text
Eidos Runtime ──启动──▶ Playwright MCP（--extension，Seatbelt 内）
                          │ 本机 WebSocket 中继：127.0.0.1:随机端口/随机 UUID 路径
                          │ 打开 chrome-extension://<扩展 ID>/connect.html?mcpRelayUrl=…&token=…
                          ▼
                    用户的 Chrome ── Playwright Extension（chrome.debugger、chrome.tabGroups）
```

- 中继与扩展之间是一个简单的 RPC：`chrome.tabs.create/remove`、`chrome.debugger.attach/sendCommand`，以及 `chrome.tabs.onCreated/onRemoved`、`chrome.debugger.onEvent/onDetach` 事件。**中继只能驱动扩展上报的标签页**，所以组的边界由扩展执行，不依赖模型自觉。
- 中继在 MCP 进程内运行，监听 `127.0.0.1` 的随机端口，路径带随机 UUID。扩展只接受回环地址。
- **令牌**：扩展在自己的存储里生成一个随机令牌，在扩展的状态页显示。客户端通过环境变量 `PLAYWRIGHT_MCP_EXTENSION_TOKEN` 传入相同令牌时，连接页跳过“选择标签页并允许”的步骤，连接页本身成为 Agent 的第一个标签页。没有令牌时，每次连接都要用户在连接页手动允许。
- **打开连接页**：中继直接执行 Chrome 程序，把连接页 URL 作为参数，并带上 `--profile-directory`。指定了 `--executable-path` 时，它不会读取 Chrome 的配置目录来检查扩展是否已安装。
- 组标题中的客户端名来自 MCP 初始化时的 `clientInfo.name`。Eidos 目前使用 MCP SDK 的默认值 `mcp`。

### 2.3 安全边界：标签组不等于账号隔离

上游连接页明确警告，允许连接会把整个浏览器暴露给客户端。原因如下：

- 浏览器级 CDP 命令（`Storage.*`、`Network.getAllCookies` 等）会经任意一个已连接的标签页转发，可以读取所有网站的 Cookie。
- 组内标签页可以被导航到任何网站。用户在这些网站上已经登录，Agent 也就可以使用这些账号。
- 扩展不能拦截下载，Eidos 的“拒绝下载”规则在这个模式下无法生效。
- 调试器连接后，Chrome 会在窗口顶部显示“Playwright Extension 正在调试此浏览器”的提示条。这是 Chrome 的强制提示，不能隐藏，也正好告诉用户浏览器正被控制。

因此，标签组限定的是 **Agent 操作哪些标签页**，并不限定它能接触哪些账号。这个模式沿用现有“使用我的 Chrome”的高危规则：每个 Session 第一次使用时需要用户人工确认，`auto_review` 不能代替；确认文案必须写清楚上述范围。

## 3. 方案

### 3.1 目标浏览器

`browser_settings.target` 现有 `eidos`（默认）和 `user_chrome`（CDP 远程调试）。新增 `chrome_extension`：

| 目标 | 连接方式 | 标签组 | 状态 |
| --- | --- | --- | --- |
| `eidos` | BrowserHost 启动独立配置的 Chrome，CDP | 无 | 默认，保留 |
| `chrome_extension` | 用户的 Chrome 加 Playwright Extension | 有 | 新增，推荐的共用方式 |
| `user_chrome` | 用户在 Chrome 中开启远程调试，CDP | 无 | 保留；新方式稳定后在界面中隐藏 |

### 3.2 运行时启动（`runtime/browser_launch.py`）

选择 `chrome_extension` 时：

- 插件启动参数从 `--cdp-endpoint ${BROWSER_CDP_ENDPOINT}` 换成 `--extension`、`--executable-path ${BROWSER_OPEN_HELPER}` 和 `--profile-dir-name ${BROWSER_PROFILE_DIR}`。`mcp.json` 增加按目标选择参数的能力。插件包仍然只有一份，内容 Hash 校验不变。
- 令牌通过环境变量 `PLAYWRIGHT_MCP_EXTENSION_TOKEN` 传入，由 Runtime 从凭据存储读取（见 3.4）。它不会出现在命令行、日志、SQLite 或 Event 中。
- 不调用 BrowserHost，也不启动 Eidos 浏览器。`chrome_installed` 检查保留。
- MCP 客户端名设为 `Eidos · <Session 标题，最多 20 字>`，标签组显示为 `Playwright · Eidos · 修复登录…`。“Playwright”前缀由上游固定，暂不修改。

### 3.3 打开连接页：本机代理

沙盒内的 MCP 进程不能读取用户的 Chrome 配置目录，也不能启动 Chrome；这两项权限都不应开放。做法是：

- `--executable-path` 指向随插件发布的小脚本 `open-connect-page`。中继把连接页 URL 交给它执行。
- 脚本不启动 Chrome，只把 URL 写入沙盒 TMPDIR 内的 Unix socket，并等待 Runtime 回复。沙盒已允许连接 TMPDIR 内的 Unix socket。
- 沙盒内无法解析 `localhost` 这个名字（系统 DNS 服务被 Seatbelt 拦截），而中继监听时使用的正是这个名字。插件随附一个预加载脚本（`node --require loopback-shim.cjs`），只把 `localhost` 解析为 `127.0.0.1`，其他名字仍走被拦截的正常路径。不开放 DNS，因为 DNS 查询本身可以把数据带出沙盒。
- Runtime 侧的代理只接受一种格式：`chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/connect.html`，参数只能是 `mcpRelayUrl`（必须是 `ws://127.0.0.1:<端口>/extension/<UUID>`）、`client`、`protocolVersion` 和 `token`。代理用 URL 解析校验，不用正则（客户端名中的空格会被编码为 `+`）。校验通过后，代理在沙盒外执行 `open -b com.google.Chrome --args --profile-directory=<配置> <URL>`；其他请求一律拒绝，并记录脱敏后的诊断信息。
- “扩展是否已安装”由 Runtime 在沙盒外检查：查看 `~/Library/Application Support/Google/Chrome/<配置>/Extensions/<扩展 ID>`，或配置中 `Preferences` 的扩展记录。这与上游的检查方式相同，只读取这两个位置。

代理由 Run 的资源注册表持有，随 MCP 连接一起关闭。

### 3.4 设置与令牌

Settings → 插件 → 浏览器 → 目标浏览器，新增“使用我的 Chrome（标签组）”。开启时显示一个三步引导：

1. **安装扩展**：检测结果实时显示；未安装时提供“打开 Chrome 应用商店”按钮（由 Main 用系统浏览器打开固定地址）。
2. **选择 Chrome 配置**：读取 `Local State` 中的配置列表，只列出已安装扩展的配置。
3. **粘贴令牌**：说明在哪里找到令牌（点击扩展图标打开状态页并复制）。扩展的复制按钮复制的是整行 `PLAYWRIGHT_MCP_EXTENSION_TOKEN=<令牌>`，输入框必须同时接受整行和单独的令牌，保存前去掉前缀。令牌保存到 `<数据目录>/plugin-credentials/`（目录 `0700`，文件 `0600`），与 MCP OAuth 凭据使用相同的存储与脱敏规则。不保存令牌也可以使用，但每个 Run 第一次使用浏览器时，都要在 Chrome 里手动允许连接。

开启这个模式本身需要用户在 Desktop 中确认（高危样式），文案包含 2.3 中的范围说明。

### 3.5 授权

- `session_browser_grants` 新增 profile 值 `extension`。它与 `default`、`user` 相互独立，互不覆盖。
- `browser_access` 审批对 `extension` 设置 `requiresHumanConfirmation: true`。审批卡片显示：“Agent 将在你的 Chrome 中操作「Playwright · Eidos · …」标签组，并可以使用其中网站的登录状态……”。
- 读取类工具不需要审批，页面操作需要 Session 授权，上传文件每次审批。这些规则与现在相同。`full_access` 沿用现有例外。
- 下载无法拦截。审批文案和 `docs/current` 都要写明。

### 3.6 生命周期

Eidos 现在为每个 Run 启动一个 MCP 进程，所以：

- Run 第一次使用浏览器时，中继启动，连接页打开并进组，成为 Agent 的第一个标签页；
- Run 结束时，MCP 关闭，扩展解散该组，标签页保留在浏览器中，但不再归 Agent；
- 下一个 Run 会新建一个组，看不到上一个 Run 的标签页（目前 Eidos 浏览器模式可以跨 Run 复用标签页）。

第一阶段接受这个限制，并在 Skill 中告诉模型：新 Run 需要重新打开页面。若要让组在整个 Session 内保持，需要让这个 MCP 连接按 Session 持有，这会改变现有“MCP 按 Run 启动”的资源模型，作为第三阶段单独设计。

### 3.7 错误

| 情况 | 错误码 | 显示 |
| --- | --- | --- |
| 扩展未安装或所选配置中没有扩展 | `browser_extension_missing` | 打开设置，提供安装入口 |
| 30 秒内扩展没有连上（Chrome 未运行、用户未允许、令牌错误） | `browser_extension_not_connected` | 提示检查 Chrome 和令牌 |
| 扩展与 Playwright 协议版本不一致 | `browser_extension_version_mismatch` | 提示更新 Eidos 或扩展 |
| 用户在扩展中断开连接、关掉组内全部标签页 | 现有 `mcp_connection_lost` | 下一次调用重新连接 |

中继的英文错误由 Runtime 映射为上述稳定错误码，不直接透传。

## 4. 分阶段实施

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| S0 验证（先做） | 不改产品代码。用真实 Chrome 和应用商店扩展，在沙盒外手动运行固定版本的 `--extension`（带令牌）。确认：建组与上色；拖入拖出；两个客户端分别建两个组；断开时解散组；提示条；`Network.getAllCookies` 能否读取所有网站的 Cookie；连接页是否抢焦点。再在 Seatbelt 内验证 3.3 的代理与本机监听权限。 | 一份记录，写入本文第 6 节；任何一项不符就先调整方案 |
| P1 Runtime | `chrome_extension` 目标；`mcp.json` 按目标选择参数；连接页代理；新的沙盒配置 `browser_extension`（在 `browser` 基础上允许监听 `127.0.0.1`）；令牌凭据；`extension` 授权；错误码映射；MCP 客户端名 | 真实 Seatbelt 测试：监听只限回环地址，代理拒绝非法 URL；授权与审批测试；凭据不进入日志和 SQLite |
| P2 Desktop | 设置引导、配置选择、令牌输入、高危确认、审批文案、Session 顶部显示当前标签组 | Renderer 行为测试；截图验收 |
| P3 生命周期（可选） | 让该 MCP 连接按 Session 持有，标签组跨 Run 保留 | 需要单独的架构决策 |

## 5. 不采用的方案

- **自己编写扩展**：上游已经实现，并且中继把扩展 ID 写死了。自己的扩展要么得改 Playwright，要么得冒用上游 ID，两者都不可取。如果需要 Eidos 自己的组名前缀，应向上游提交可配置项。
- **只用现有的 CDP 远程调试（`user_chrome`）**：CDP 没有标签组接口，Agent 能看到全部标签页，Chrome 每次连接都会弹窗。
- **让沙盒内进程直接启动 Chrome**：需要开放用户 Chrome 配置目录的读取权限和 LaunchServices，范围太大。

## 6. 风险与待验证

- **扩展自动更新**：应用商店会自动更新扩展。协议版本升级时，固定版本的 Playwright 会连接失败（连接页显示版本不一致）。Playwright 升级需要同步跟进，并显示明确的错误。
- **企业管理的 Chrome** 可能禁止安装扩展或禁止 `chrome.debugger`。这种情况下回退到 Eidos 浏览器，由用户在设置中切换，不自动回退。
- **令牌在扩展状态页中明文显示**，重新生成后，Eidos 中保存的令牌会失效。
- **Chrome 没有运行时**，`open` 会启动 Chrome，需要验证这时连接页能否正常打开。
- **不支持 Edge**（上游支持，但本期只考虑 Chrome）。

## 7. S0 验证记录（2026-11-04）

环境：macOS、Google Chrome 156.0.8078.80（`Default` 配置）、应用商店中的 Playwright Extension 0.5.0、`@playwright/mcp@0.0.84`、Node 24.20.0 官方构建（与托管 Node 相同）。测试只访问 `example.com/.org/.net`，Cookie 只统计数量。测试脚本放在会话临时目录，没有进入仓库。

| 项目 | 结果 |
| --- | --- |
| 令牌免确认连接 | 通过。连接用时约 1 秒，不需要点击。复制按钮带 `PLAYWRIGHT_MCP_EXTENSION_TOKEN=` 前缀，未去掉前缀时连接页报 “Invalid token provided”（已写入 3.4） |
| 按客户端隔离 | 通过。三个客户端同时连接，各自只能看到并操作自己组内的标签页 |
| 标签组与颜色 | 通过。每个客户端一个组，标题为 `Playwright · <客户端名>`，颜色不同；用户确认显示正常 |
| 拖入、拖出 | 通过。用户把别的标签页拖进组后，下一次列出时 Agent 可控列表中出现该页；拖出后消失（日志间隔 15 秒） |
| 断开连接 | 通过。组解散，标签页保留；用户确认 |
| 提示条与焦点 | 用户确认表现正常 |
| 账号范围 | **证实风险**。每个客户端都能读取整个 Chrome 的 1,889 个 Cookie、568 个域名，与上游警告一致（2.3） |
| 沙盒外直接启动 Chrome | 失败。启动 Chrome 的进程如果不能访问用户 Chrome 配置目录，就无法把 URL 交给正在运行的 Chrome；改用 `open -b com.google.Chrome` 后正常，证实需要 3.3 的代理 |
| Seatbelt 内运行 | 通过。在 `mcp_browser.sbpl` 基础上只增加 `network-bind` 和 `network-inbound`（`localhost:*`），配合 `localhost` 预加载脚本和 Unix socket 代理，完整流程正常 |
| 沙盒外网 | 仍被拦截：连接公网 IP 返回 `EPERM`，域名解析失败 |
| 代理拒绝伪造请求 | 通过。非连接页地址、非回环中继地址都被拒绝 |
| **监听范围** | **发现问题**。增加 `network-inbound localhost:*` 后，沙盒内进程可以监听 `0.0.0.0`，并接受从本机局域网地址发起的连接。Seatbelt 的 `localhost` 规则覆盖了本机的所有地址。中继本身只监听回环地址，但沙盒不能保证这一点 |

### 对 P1 的影响

1. **监听问题需要先定方案**，二选一：
   - **A. Runtime 转发（推荐）**：沙盒配置保持现状，不开放任何监听。预加载脚本把中继的 `listen` 改为监听沙盒 TMPDIR 内的 Unix socket（沙盒已允许），并把中继报告的地址改为 Runtime 提供的 `127.0.0.1:<端口>`；Runtime 在沙盒外只在回环地址监听，转发到这个 Unix socket。安全边界不变，代价是预加载脚本依赖 Playwright 中继的内部实现，需要随固定版本一起测试。
   - **B. 接受并记录**：开放 `network-inbound localhost:*`，在文档中说明沙盒内进程可以接受本机局域网地址的连接。实现最简单，但削弱了现有沙盒边界。
2. 预加载脚本、代理和令牌前缀处理已在验证中跑通，可以直接按 3.3、3.4 实现。
3. 令牌会出现在 `open` 的命令行参数中，同一用户的其他进程在极短时间内能通过 `ps` 看到。扩展打开页面后会立即从地址栏删除令牌。P1 需要确认 Chrome 历史记录中是否保留带令牌的地址。

用户可以删除 `~/.eidos-s0-token`；扩展状态页可以重新生成令牌，旧令牌随即失效。

## 8. 决策与 P1 实施记录（2026-11-04）

### 用户决策

1. **默认目标为 `chrome_extension`，未配置时降级**：没有保存令牌，或确定 Chrome 中没有扩展时，使用独立的 Eidos 浏览器。目标在 Run 启动浏览器连接时决定一次，中途不切换。已决定用扩展但连接失败时报错，不改用 Eidos 浏览器。
2. **标签组名称**：`Playwright · Eidos · <会话名>`。“Playwright ·”前缀由扩展固定，目前无法去掉；需要的话可以向上游提出可配置的组名。
3. **监听问题采用方案 B**：只为扩展模式单独使用 `mcp_browser_extension.sbpl`，开放回环地址监听，在文档中说明本机局域网地址的风险。Runtime 转发方案（A）留作以后加固。
4. **账号保护写入系统指令**：Run 中有浏览器工具时加入 `browser-accounts` 指令层，把已登录账号及其数据视同凭据。这是行为约束，安全边界仍由人工确认、工具白名单和沙盒保证。

### 与第 3 节设计的差异

- **不修改插件包**：已安装的插件以内容 Hash 识别为内置插件，修改 `mcp.json` 或 `SKILL.md` 会让现有安装在更新前失去浏览器工具，并撤销所有授权。因此 Runtime 用环境变量切换到扩展模式（`PLAYWRIGHT_MCP_EXTENSION` 等），把 `--cdp-endpoint` 展开为空字符串（已用真实 Playwright 验证：空值被视为未设置）；预加载脚本经 `NODE_OPTIONS` 加载；账号规则放在 Runtime 的系统指令中，而不是插件的 Skill。
- **复用 `user` 授权**：扩展模式与远程调试模式都在操作用户自己的 Chrome，风险相同，因此复用 `user` 授权类型，不新增授权 profile，也不需要修改授权表。切换目标、保存或清除令牌时撤销全部 `user` 授权。
- **目标设置**：`browser_settings` 在 v50 增加 `chrome_extension` 和令牌引用列 `extension_credential`。令牌按网页搜索密钥的做法保存在独立的 `browser-secrets` 目录，不能放在 `plugin-credentials`，因为后者启动时会清理不属于 MCP 连接的文件。
- **连接页在 Chrome 最近使用的配置中打开**（`open -b com.google.Chrome`），暂不支持选择 Chrome 配置。直接执行 Chrome 程序并传 `--profile-directory` 的方式在验证环境中无法交给正在运行的 Chrome，P2 需要在打包后的 App 中验证后再加入。
- **新增限制**：使用用户的 Chrome 时不注册 `browser_network_request`。它返回完整的请求和响应头与正文，包含会话 Cookie 和认证令牌，只靠脱敏无法覆盖正文。
- **错误码**：只新增 `browser_extension_not_connected`（中继连接失败）和 `BROWSER_EXTENSION_TOKEN_INVALID`（设置令牌时）。扩展缺失与版本不一致在中继侧都表现为连接超时，不再细分；扩展明确缺失时 Run 直接使用 Eidos 浏览器。

### 主要改动

| 层 | 文件 |
| --- | --- |
| Schema | `db/browser_extension_migration.py`（v50）、`db/schema.py`、`db/database.py`、`db/layout.py`、`db/backup.py` |
| 领域与持久化 | `domain/browser.py`、`persistence/browser_grants.py` |
| 基础设施 | `infrastructure/chrome_extension.py`（令牌、扩展检测、连接页校验、代理、辅助脚本与预加载脚本）、`sandbox/mcp_browser_extension.sbpl` |
| 启动 | `runtime/browser_launch.py`、`runtime/run_resources.py`、`extensions/browser_plugin.py`、`extensions/mcp.py` |
| 审批与错误 | `runtime/browser_tools.py`、`runtime/tool_runtime.py` |
| 系统指令 | `model/prompts.py`、`model/instructions.py` |
| 应用与协议 | `application/browser.py`、`models/browser.py`、`protocol/server.py`、`protocol/fixtures/browser-v1.json`、生成的 `desktop/shared/browser*.generated.ts` |
| Main 与 Renderer | `desktop/shared/browser.ts`、`desktop/main/runtime-client.ts`、`BrowserGrantIndicator.tsx`、`settings/BuiltinPlugins.tsx`（只按生效目标显示状态，设置界面属于 P2） |

### 验证

- Runtime 定向测试：`test_chrome_extension`（令牌、扩展检测、连接页校验、代理上限、降级矩阵、令牌存储与撤销、v49→v50 升级与失败回滚）、`test_browser_tools` 中的扩展模式 Run（通过代理配对、人工确认与新提示、客户端名、不注册 `browser_network_request`、连接失败错误码）、`test_browser_sandbox`（真实 Seatbelt：默认配置不能监听，扩展配置可以监听回环地址并连接代理 socket，外网仍被拦截）、`test_instruction_resolution`，以及受影响的浏览器、存储 Schema、备份、布局、MCP 与指令测试。
- Main：`runtime-client-browser.test.ts`（共享 Fixture）；Renderer：`BrowserGrantIndicator.behavior.test.tsx`。
- 真实端到端：用正式代码路径（内置插件的 `mcp.json`、`mcp_browser_extension.sbpl`、辅助脚本、预加载脚本、代理），在 Seatbelt 中启动固定版本的 Playwright MCP，连接用户真实的 Chrome 与扩展，导航到 `example.com` 成功，客户端只看到自己的标签页，关闭后无残留资源。
- 未运行 Full Suite、Electron smoke 和打包验证；设置界面（P2）未实现，打包后的 App 中尚未验证。

## 9. P2 实施记录（2026-11-04）

- 浏览器插件行内新增 `BrowserTargetPanel`：三选一的“Agent 使用的浏览器”（标签组模式标为推荐），以及标签组模式下的两步设置（检测扩展并提供应用商店入口；粘贴令牌）。
- 与 3.4 的差异：没有“选择 Chrome 配置”这一步，连接页在 Chrome 最近使用的配置中打开（见第 8 节）。
- 高危确认：保存令牌前，以及选择远程调试前，各有一次行内确认；单选框再次点选不算确认。切换目标、保存或移除令牌后的提示会说明授权已撤销。
- 新增 IPC：`setBrowserAgentExtensionToken`、`clearBrowserAgentExtensionToken`、`openBrowserExtensionStore`（应用商店地址固定在 `desktop/shared/browser.ts`，由 Main 打开）。
- 顺带修正：插件行的确认框原先使用了不存在的 `--border` 变量，没有边框；现改用 `--line` 和 `--surface-raised`，文字与面板统一为说明字号。
- 验证：`BrowserGrantIndicator.behavior.test.tsx`（推荐与降级说明、应用商店入口、令牌确认与保存、无效令牌提示、移除令牌、远程调试的确认与改回）、`PluginSettings`、`SettingsPage` 行为测试；在临时页面中用真实组件检查了 900px 与 600px 宽度（窗口最小 800px，设置内容区约 560–600px），无横向溢出。未在打包后的 App 中检查。
- 更正（2026-11-05）：上述面板加在了 `BuiltinPlugins.tsx`，而该组件在 10 月 27 日插件市场改版（`48caf852`）后已不再挂载，所以 0.5.6 的设置页中看不到。测试和临时页面都直接渲染组件，没有发现这一点。现已移到浏览器插件详情弹窗顶部的“浏览器”一节（`BrowserSettingsSection.tsx`），删除 `BuiltinPlugins.tsx`；行为测试改为经插件设置页打开详情弹窗来验证挂载位置。
- 精简（2026-11-05）：按用户反馈，“浏览器”一节改为三行设置（浏览器下拉框、扩展、令牌），去掉推荐标签、编号步骤、状态行和底部说明；高危确认改为模态确认框，结果与错误改用全局悬浮通知；移除“打开浏览器登录”“关闭浏览器”“清除浏览器数据”按钮；扩展检测为“无法读取”时（macOS 阻止读取 Chrome 配置目录）不显示检测结论。`BrowserTargetPanel.tsx` 合并进 `BrowserSettingsSection.tsx`。
