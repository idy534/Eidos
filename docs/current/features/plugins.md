# 插件、远程连接与内置 GitHub

本文描述当前源码。插件沿用 Settings → typed preload IPC → Main → stdio JSON-RPC → ExtensionApplication → PluginCatalog → SkillCatalog / McpManager / HookService。SQLite 仍保存安装、授权、版本和运行事实。

## 包格式与能力

Runtime 接受 Eidos v1、Portable Agent Plugins 和 `.codex-plugin/plugin.json` 三种本地目录。Portable 包以根清单决定身份，以 `skills/` 发现技能，以 `mcp.json` 声明 MCP。`extensions.com.openai` 存在时，它整体替代旧 Codex overlay 的设置。旧 Codex 包可以用 `skills` 和 `mcpServers` 声明目录、内联配置或配置文件；没有显式 MCP 声明时，Runtime 发现 `.mcp.json`。重复 JSON 键、越界路径、链接、特殊文件、过量内容继续拒绝。

插件可以同时提供 Skill、command/MCP Hook、本地 stdio MCP 和远程 Streamable HTTP MCP。导入不启动服务，也不执行脚本。用户需要启用插件，并分别确认 MCP 授权和 Hook 信任。远程服务需要用户主动连接账号或连接无认证服务。MCP Hook 只调用当前 Run 已连接且仍有授权的服务。插件详情提供 Hook 执行历史。Hook 的事件、后台命令、权限和恢复规则见 [docs/current/features/hooks.md](hooks.md)。

Runtime 创建新的扩展快照时会核验插件包内容身份。内容身份不匹配或包不可读时，Runtime 会从新快照中省略该插件，并继续加载其他可用扩展。旧快照仍按已固定的版本和内容身份读取，已被修改的包内容不会被使用。

外部 MCP 配置支持以下形状：

```json
{
  "mcpServers": {
    "example": {
      "type": "stdio",
      "command": "${RUNTIME_PYTHON}",
      "args": ["${PLUGIN_ROOT}/server.py"],
      "env": {"API_TOKEN": "${EXAMPLE_API_TOKEN}"},
      "extensions": {"com.eidos": {"permissionProfile": "connector"}}
    }
  }
}
```

`command`、`args`、环境变量名称映射和 `disabled` 进入统一配置。Runtime 在启动时解析 `${PLUGIN_ROOT}`、`${CLAUDE_PLUGIN_ROOT}` 和 `${RUNTIME_PYTHON}`，并为 `python`/`python3` 选择随 Runtime 使用的 Python。其他命令仍须由调用环境提供；Runtime 不下载依赖。环境变量映射只保存名称，值在启动时读取。缺少引用值时，服务报告 `mcp_environment_missing`。Eidos 内部环境变量仍由现有 sanitizer 排除。

`extensions.com.eidos` 可以声明现有 `permissionProfile`、`startupTimeoutSeconds` 和 `toolTimeoutSeconds`。未声明时使用 `workspace_read`、15 秒和 60 秒。`connector` 与 `workspace_read` 继续使用现有真实 Seatbelt 配置，`full_access` 沿用现有例外。MCP 的 SDK、资源所有者、超时、取消、审批、Durable Intent、结果校验和未知结果语义保持不变。

内嵌环境变量值、旧 SSE transport 和暂不支持的启动设置不会注册为可调用工具。插件详情会逐项显示兼容信息。正常的 MCP 可以与这些声明共存。当前工具名称仍使用 `mcp__<serverId>__<tool>`；导入会检查手动 MCP 和其他已安装插件的服务 ID，拒绝冲突，避免工具被静默隔离。

远程服务声明示例：

```json
{"mcpServers":{"example":{"type":"streamable-http","url":"https://example.com/mcp","extensions":{"com.eidos":{"auth":{"type":"oauth"}}}}}}
```

远程地址必须使用 HTTPS；明确配置的本机回环地址可以使用 HTTP。地址不能包含用户信息、查询参数或 fragment。远程配置不能携带命令、环境变量或静态认证 Header。`extensions.com.eidos.auth.type` 支持 `none`、`bearer`、`oauth` 和 `github-device`。`bearer` 由用户在账号面板输入 token。标准 OAuth 使用官方 MCP SDK 发现认证服务、注册客户端和执行 PKCE；可以提供公开 `clientId` 或 `clientMetadataUrl`。包不能包含客户端密钥。仅有 OpenAI 认证声明的包不会自动复用其账号或客户端注册。

## 账号连接与凭据

`mcp/connection` 提供读取、连接、取消和断开。`plugin/builtin` 提供内置目录和安装。两者都经过生成的 Python/TypeScript 契约、typed preload IPC 和原有 stdio JSON-RPC。Main 只在用户发起登录后打开系统浏览器。标准 OAuth 的临时 HTTP listener 只监听随机端口的 `127.0.0.1`，只接收授权码；本地业务控制面仍是 stdio。SDK 负责 PKCE、state 和 issuer 核验。GitHub Device Flow 使用浏览器短码，不需要回调服务。

SQLite schema v38 增加 `mcp_connections`。该表只保存配置身份、连接版本、状态、账号显示名、到期时间和凭据引用。access token、refresh token 和 OAuth 客户端信息保存到 `<data>/plugin-credentials/<uuid>`，目录为 `0700`，文件为 `0600`，每条最多 32 KiB。凭据不会进入清单、SQLite、Event/Outbox、普通响应或模型上下文。账号连接事件只包含服务标识、状态和时间。

每次新登录、取消和断开都更新连接版本。凭据发布使用 SQLite 条件更新，迟到结果不能覆盖取消或新的账号。刷新锁由当前 AnyIO 执行内核持有，多个 Run 的目录视图共用同一账号版本的刷新锁；锁不会覆盖 MCP 工具执行。Authlib 处理 Device Flow 的令牌请求和 OAuth 刷新。Eidos 处理等待、取消、权限、凭据保存和版本核验。正常工具请求不会打开浏览器，也不会自动重新登录或重放失败的工具。

Runtime 只在启动时恢复中断的登录，将其标为需要重新连接。新建 Run 或 Hook 的目录视图不会触发恢复。断开会取消等待、删除凭据引用并清理凭据文件。插件升级和卸载会撤销旧账号连接。运行中的连接在请求前再次核对插件状态、配置、consent 和账号版本。已发出的请求仍可能产生副作用，现有 Durable Intent 和未知结果规则继续生效。

远程传输和 Session 使用官方 MCP SDK。HTTPX Adapter 为响应设置 2 MiB 上限，并请求不压缩的响应；不支持服务器忽略此要求而返回压缩内容。Adapter 也要求认证发现和令牌请求使用 HTTPS，明确的本机回环地址除外。SDK 仍负责解析 JSON 和 SSE。Ready 连接的 HTTP read 没有独立的 15 秒超时，Eidos 继续执行配置中的启动和单次工具超时。服务不获得本地文件访问能力，Shell 的网络权限不因此改变。工具继续经过原有 Approval、Durable Intent、结果校验和未知结果记录。

## 内置 GitHub

应用随附 `resources/plugins/github/`。设置页展示内置 GitHub，用户点击安装后，Runtime 复用同一包解析、版本管理和安装入口。安装会启用这个受管插件，但不会自动登录或取得账号权限。GitHub 的 MCP 地址是 `https://api.githubcopilot.com/mcp/`，包同时提供 GitHub Skill。连接不依赖用户预装的 Node、Docker、GitHub CLI 或 MCP 二进制。

用户可以在 GitHub 的资源详情中输入 PAT，或使用浏览器 Device Flow。浏览器方式需要发行方先注册 GitHub OAuth App / GitHub App 并启用 Device Flow。发行方可以在包的 `auth.clientId` 中提供公开标识；开发环境也可以设置 `EIDOS_GITHUB_CLIENT_ID`。当前仓库没有填入真实 Client ID，界面因此提供 PAT 和“创建 GitHub 令牌”入口，不把浏览器登录显示为可用。修改随包 Client ID 时必须更新插件版本，遵循同版本不可改内容的规则。

GitHub 授权范围、组织审批、SSO 和账号权限仍由 GitHub 控制。Eidos 不复用其他应用的登录，不在聊天中索取令牌。断开 Eidos 连接只移除本机凭据；用户可以在 GitHub 设置中另外撤销应用或 PAT。

Runtime 读取 `interface.displayName`、`shortDescription`、`developerName`、`websiteURL`、`composerIcon` / `logo`，以及作者、主页和 License。图标只读取包内不超过 64 KiB 的 PNG/JPEG。可选的 `onboardingSkill` 必须指向本包声明的 Skill。详情页中查看 Skill 只展示 Skill 内容，不执行设置脚本，也不授予权限。展示信息通过 Python 生成的 TypeScript 类型和 JSON Schema 进入 Desktop。

## 更新、重装与旧任务

内置 GitHub 的图标包版本为 `1.0.1`。旧 `1.0.0` 安装可以通过“更新”切换到新版本。包内容变化必须同时更新版本，图标和展示清单也属于包内容。内置安装入口会将同版本内容冲突返回为 `PLUGIN_VERSION_CONFLICT`，其他包不可用错误仍返回 `PLUGIN_UNAVAILABLE`。升级继续撤销旧账号连接和 MCP 授权，用户需要重新连接账号。

用户可以通过现有“导入本地 Plugin”入口导入同 ID 的另一个版本。Runtime 也允许用户显式导入已知旧版本进行回退。安装路径继续是 `<data>/extensions/plugins/<id>/<version>/`，路径不加入 Hash。

SQLite schema v37 增加 `plugin_versions`，保存每个已知版本的内容身份。v36 升级会回填当前版本。新版本与当前指针的切换、停用、清除 Hook 信任、清除 MCP 授权和导入 Event/Outbox 在同一事务内提交。切换使用旧版本、内容身份和安装状态校验，防止并发导入覆盖已变化的记录。发布或记录提交失败时，Runtime 只清理本次新建的目录。

同版本不同内容继续返回 `PLUGIN_VERSION_CONFLICT`。相同内容的重复导入保持幂等。已卸载包的重装继续默认停用，并清除旧授权。新包不会继承旧版本的 Hook 信任和 MCP 授权。

旧 Run 通过快照中的版本和内容身份读取旧目录。活动引用继续保护旧文件。现有扩展清理入口在引用结束后清理退役版本；版本身份记录继续保留，防止旧版本名称被换成其他内容。正在运行的 MCP 连接保留其启动配置；恢复时仍进行当前配置和授权核验，不静默恢复已经撤销的授权。Hook 更新仍沿用撤销旧信任的规则。

v37、v38 不改变已有 v36 的短目录布局。更早的 `<id>/<version>/<hash>/` 布局仍遵循上一阶段的限制。旧 Runtime 不能打开 v38。回退代码需要恢复升级前的数据库备份。

创建器仍生成小型 Eidos v1 包。其独立脚本只做文件安全、容量和基本格式预检查。正式导入使用 Runtime 的统一 Skill YAML parser 和包声明模型，避免创建器拒绝 Runtime 已支持的丰富元数据。预检查通过不等于正式导入或运行验收通过。

## 与 Codex 桌面端的差距

本轮沿用了 Codex 的组合式包、独立服务授权、展示信息和版本安装思路。[Codex 官方文档](https://developers.openai.com/plugins/build/plugins) 同时提供市场分发、远程连接和安装引导；这些能力不能仅靠读取清单获得。

Eidos 当前没有插件市场、Git/npm 下载器、Codex `.app.json` 连接映射或 MCP Apps 界面。手动 MCP 编辑器仍仅支持本地 stdio，远程配置当前由插件包声明。当前通用 OAuth 覆盖公开客户端、DCR 和 CIMD，不提供让用户录入机密客户端 Secret 的流程。Figma 未内置。真实 GitHub 账号、组织策略和发行方 Client ID 仍需独立验收，模拟服务通过不能替代真实账号验收。

[GitHub 官方 Host 集成指南](https://github.com/github/github-mcp-server/blob/main/docs/host-integration.md) 要求客户端取得有效访问 token，可使用 OAuth 或 PAT。Eidos 不能复用 Codex 账号里的连接。[Figma 官方安装指南](https://developers.figma.com/docs/figma-mcp-server/remote-server-installation/) 要求 HTTP 连接和账号登录，并说明当前只有目录列出的客户端可以连接，新客户端需要申请。Eidos 还需要确认这个接入条件，不能仅凭 OAuth 实现宣称 Figma 可用。

## 本次远程连接验证（2026-10-23）

Runtime 的远程连接、认证、MCP、详情、沙盒、插件升级、包解析、协议和数据库定向组通过 154 项。测试覆盖两个目录视图并发刷新同一令牌、Run 内工具发现与 Durable Intent 提交、登录取消与迟到结果、重启恢复、凭据缺失、401、移除后的凭据清理、调用取消和不自动重放，以及 HTTP 明文降级与响应大小边界。

旧库降级 fixture 同步移除 v37/v38 新表，避免把新表留在模拟旧库中。会话分叉、搜索、委派、固定项、记忆和终止状态相关的旧库迁移另外通过 11 项定向检查。新远程连接和升级测试已登记为 Integration 层，测试分层仍由 `runtime/tests/conftest.py` 统一管理。

Main 的插件连接、Hook 和 MCP 详情跨进程组通过 6 项；其中连接组使用独立 Runtime 包另行通过 2 项。Renderer 的连接面板和插件设置通过 17 项。真实 Electron 页面通过 320、768、1024、1440px 下的布局、键盘、安装、PAT 连接和断开验证。上述账号与远程服务都使用测试 fixture。

`pnpm build`、macOS arm64 Runtime Bundle、Electron smoke、生成契约复现、Desktop 结构契约、Ruff、Deptry 和 `git diff --check` 通过。Bundle 包含官方 MCP SDK、Authlib 和 GitHub 包，无需系统 Python 或用户预装 MCP 服务。本次新增 `authlib==1.9.1`，锁文件同时增加其依赖 `joserfc==1.7.7`；原有 MCP 传输代码继续使用官方 SDK，没有另建 Agent Loop 或网络控制面。

SQLite 由 v37 升级到 v38，只新增连接表。旧 stdio 包的默认序列化与授权方式保留。旧 Runtime 不能打开 v38 数据。凭据不进入 SQLite，插件安装、更新和账号连接仍分别记录。旧 Run 的插件版本与工具执行中的 Approval、Sandbox、Durable Intent、结果校验和未知结果语义继续保留。

本次没有运行 Full Suite、DMG、签名发布或真实 GitHub 账号验收。仓库没有发行方 Client ID，浏览器登录仍需注册应用并启用 Device Flow，再填入公开标识验收。Python 审计在主 Runtime 阶段报告 certifi、opentelemetry-api、pillow 三个既有依赖的 37 项漏洞，因此脚本未继续隔离工具 Python 审计。新增 Authlib 和 joserfc 没有出现在该次漏洞表中。该结果不视为依赖审计或发布检查通过。

## 上一轮本地插件验证（2026-10-23）

Runtime 的插件、MCP、Skill 和 Hook 定向组通过 143 项。新增路径兼容和最后的导入并发检查另行通过插件定向组 43 项；数据库与多库存储组通过 64 项。这些组存在重叠，不相加作为唯一测试总数。测试覆盖显式更新、旧 Skill 重启读取、授权清理、并发更新、事务失败回滚、旧库迁移、目录占用和原有 Hook 行为。

Main 通过 4 项契约与真实跨进程测试，Renderer 插件页通过 12 项行为测试。包内 MCP 分别通过普通 Fixture 和真实 Seatbelt 调用；原有 5 项 MCP 原生沙盒测试也通过。Electron 页面检查通过 320、768、1024、1440px 的布局、键盘、嵌套详情和焦点返回检查，并检查了窄屏和桌面截图。

Main 与 Renderer 构建、Desktop 结构契约、Ruff、Deptry 和 `git diff --check` 通过。本轮没有新增依赖或修改锁文件，没有运行 Full Suite、Bundle/DMG 或真实 GitHub/Figma 账号验收。Python 审计脚本在主 Runtime 阶段报告 3 个既有依赖的 37 项漏洞并退出，因此该次脚本未继续隔离工具 Python 审计。该结果不视为发布检查通过。
