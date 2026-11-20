# Windows 沙盒适配调研与设计

状态：2026-11-17 完成调研与方案，尚未实施。第 0 期实机验证尚未开始。当前 Windows 行为以 [限制说明](../current/limitations.md)、[架构说明](../current/architecture.md)、源码和测试为准。

## 1. 目标与范围

Windows 预览已经接入 Shell、文件提交、MCP 和文档 Skill，但缺少与 macOS Seatbelt 对应的隔离后端。本方案为 Windows 选择一个系统级隔离后端，使以下能力在非完全访问的 Run 中也能受控运行：

- `run_shell` 的 PIPE 和 PTY；
- 命令 Hook；
- 本地 stdio MCP 的四种权限档：`connector`、`workspace_read`、`browser`、`browser_extension`；
- 经 Shell 运行的文档 Skill。

约束如下：

- macOS 的 Seatbelt 路径保持不变；
- 权限物化和策略编译继续由 Eidos 负责，系统机制只负责强制执行（AGENTS.md 第 8、15 节）；
- 未通过自检时继续 fail closed，保留现有的审批后无沙盒运行和完全访问路径；
- 不为假想需求提前构建多后端框架。

## 2. 现状

### 2.1 macOS 上依赖 Seatbelt 的能力

| 使用方 | macOS 边界 | 入口 |
|---|---|---|
| `run_shell`、Hook | 默认拒绝。全局可读，数据目录和保密路径禁止读。只能写工作区（不含 Git 元数据）、沙盒临时目录和获批路径。默认禁网。只能向同一沙盒内的进程发信号 | [seatbelt.sbpl](../../runtime/eidos_runtime/sandbox/seatbelt.sbpl)、[seatbelt_policy.py](../../runtime/eidos_runtime/sandbox/seatbelt_policy.py)、[hooks.py](../../runtime/eidos_runtime/runtime/hooks.py) |
| MCP `connector` | 不能读工作区，可读插件与 Python 运行时，只写私有 home/tmp，可以联网 | [mcp_connector.sbpl](../../runtime/eidos_runtime/sandbox/mcp_connector.sbpl) |
| MCP `workspace_read` | 只读工作区，只写私有 home/tmp，禁网 | [mcp_workspace_read.sbpl](../../runtime/eidos_runtime/sandbox/mcp_workspace_read.sbpl) |
| MCP `browser` | 只读工作区与 Node、浏览器运行时，只允许本机回环和私有 tmp 下的 Unix socket | [mcp_browser.sbpl](../../runtime/eidos_runtime/sandbox/mcp_browser.sbpl) |
| MCP `browser_extension` | 在 `browser` 之上允许监听本机回环 | [mcp_browser_extension.sbpl](../../runtime/eidos_runtime/sandbox/mcp_browser_extension.sbpl) |
| 文件提交 helper | 工作区外的获批写入由 Seatbelt 内的 helper 提交，作为纵深防御 | [seatbelt.py](../../runtime/eidos_runtime/sandbox/seatbelt.py) 的 `secure_workspace_move` |
| 启动自检 | `run_seatbelt_self_test` 失败时 `runShell=false` | [server.py](../../runtime/eidos_runtime/protocol/server.py) |

### 2.2 Windows 当前行为

- 启动自检直接返回 `unsupported_platform`，`runShell` 为 `false`。
- 未申请提权的 `run_shell` 返回 `sandbox_unavailable`。模型申请 `require_escalated` 后按当前模式审批，再无沙盒运行 PowerShell。完全访问 Run 直接运行。
- [engine.py](../../runtime/eidos_runtime/runtime/engine.py) 在 Windows 上关闭附加权限申请（`allow_additional_permissions`）。
- 本地 MCP 在 `sandbox=True` 时返回 `mcp_sandbox_unavailable`。只有完全访问 Run 会以 `sandbox=False` 启动（[run_resources.py](../../runtime/eidos_runtime/runtime/run_resources.py)）。
- 非完全访问的 Hook 走 Seatbelt 路径，无法运行。
- 文件工具在 Runtime 进程内用 NTFS 句柄提交（[windows_file_commit.py](../../runtime/eidos_runtime/sandbox/windows_file_commit.py)），不经过沙盒。

## 3. 调研

### 3.1 候选机制

| 机制 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| MXC 进程容器（PSEC `CreateProcessSecurityEnvironment` 与 WFP） | 系统强制执行；不需要管理员安装、额外账户或防火墙规则；有稳定 JSON 契约 1.0.0、`wxc-exec.exe` 和 C 接口 `mxc_ffi`；Codex 在兼容设备上推荐该模式 | 需要 Windows 11 24H2 起的特定构建，不支持 Windows 10；没有 Python SDK；网络规则只到 CIDR 和端口；有四个不可关闭的必需诊断事件 | 主后端 |
| 专用本地用户 + 写受限令牌 + WFP（Codex `elevated`、Anthropic srt-win） | 支持较旧的 Windows；读取宽松，工具链兼容性较好 | 每台机器需要一次管理员安装；要在用户文件 ACL 上添加授权并处理残留；Schannel TLS 失效（`curl.exe`、`Invoke-WebRequest`、.NET）；DNS 与 SMB 不受 WFP 拦截 | 可选的第三档，暂缓 |
| 仅写受限令牌（Codex `unelevated`） | 无需管理员 | 网络隔离弱；不支持禁读路径；已知 `os.replace` 失败和 `--add-dir` 授权缺失 | 不采用 |
| 自研 AppContainer | 系统原生 | 需要改写用户文件 ACL；回环豁免需要管理员；用户目录中的 Python、Node、git 和 HKCU 访问容易失败；MXC 已封装同一层 | 不自研 |
| Windows Sandbox（`wsb`） | 虚拟机级隔离 | 没有宿主工具链；需要专业版或企业版；`wsb exec` 不提供进程输出 | 不适用 |
| WSL | 可复用 Linux 沙盒 | 换了操作系统，工作区路径、工具链和文件身份都不同 | 不适用 |

### 3.2 MXC 要点

以下内容来自 microsoft/mxc 仓库与 Microsoft Learn，以第 0 期实机结果为准：

- Windows 默认后端是 `processcontainer`，基于 PSEC（BaseContainer）。出站网络由 PSEC 在系统提升上下文中下发的 WFP 过滤器控制。
- 最低构建：24H2 26100.9278、25H2 26200.9278。26100.9550 和 26200.9550 起才支持容器内回环服务和低完整性监听方。文档没有列出 Windows 10。
- 策略字段：
  - `filesystem`：`readwritePaths`、`readonlyPaths`、`deniedPaths`。同一目录同时出现在读写和禁止中时按禁止处理。部分后端会拒绝嵌套禁止。
  - `network`：`egress.default`、`egress.allow`、`egress.deny` 按 CIDR 和协议端口；另有 `ingress.default` 和 `ingress.hostLoopback`。不支持持久的域名规则。
  - `process`：`commandLine`、`cwd`、`env`、`inheritDefaultEnv`、`timeout`。环境中必须有 `SYSTEMROOT` 和 `LOCALAPPDATA`，PSEC 可能把 `LOCALAPPDATA` 换成容器配置目录。
  - `ui`：默认禁用 UI，剪贴板与输入注入可单独控制。
- 读取是显式授权：只能读列出的路径，没有“全部可读再扣除”的写法。
- 文件授权不改写宿主 DACL：`fallback.allowDaclMutation` 只被接受，不生效。文档没有直接写明，这一点由字段说明推断。
- 接入方式：Rust、.NET、Node SDK；没有 Python SDK。另有可执行文件 `wxc-exec.exe`，接收 JSON 配置并把 stdin、stdout、stderr 留给被执行的程序；还有 C 接口 `mxc_ffi`，接收 UTF-8 JSON 字符串。
- 稳定契约是 1.0.0，1.1.0-alpha 仍可能变化。`windows_sandbox`、`microvm`、`hyperlight` 后端是实验性的。
- 有四个 Required 诊断事件。文档说明它们不需要用户同意，`telemetry.enabled` 也不能关闭它们。

### 3.3 同类产品

- Codex：Windows 有 `mxc`、`elevated`、`unelevated` 三种模式，兼容设备上推荐 `mxc`。`elevated` 需要管理员批准的安装，会创建 `CodexSandboxOffline`、`CodexSandboxOnline` 等本地账户，用防火墙规则控制网络。`unelevated` 用当前用户派生的受限令牌和 ACL，网络隔离较弱。
- Anthropic sandbox-runtime：Windows 版是 alpha。它用专用 `srt-sandbox` 账户、写受限令牌、Job Object、按会话添加的 NTFS ACE 和按账户 SID 的 WFP 出站过滤，需要一次提升的 `windows-install`。README 记录了 MSYS2 程序（如 Git Bash）在 MXC 后端下崩溃、Schannel TLS 在受限令牌下失效等问题。
- Claude Code：官方文档说明原生 Windows 不启用沙盒，需要沙盒时使用 WSL2。

## 4. 方案

### 4.1 档位

| 档位 | 条件 | 行为 |
|---|---|---|
| A | Windows 11 构建满足 MXC 要求，随包 executor 校验通过，MXC 自检通过 | Shell、Hook、本地 MCP 和文档 Skill 在 MXC 容器内运行 |
| B | 不满足 A | 保持现状：受限 Shell 和本地 MCP 拒绝启动，可审批后无沙盒运行，或在完全访问 Run 中运行 |
| C（暂缓） | 用户确有 Windows 10 或旧版 Windows 11 的需求 | 评估复用 srt-win 或自研专用账户方案，需要一次管理员安装 |

档位在启动时由自检决定，Run 中不切换。所有档位都保留参数校验、Durable Intent、文件身份与版本核验、取消、结果验证和未知结果记录。

### 4.2 权限映射

新增与 `SeatbeltPolicyCompiler` 并列的 `MxcPolicyCompiler`，输入同一份 `EffectivePermissionProfile`，输出 MXC 1.0.0 JSON：

| Eidos 权限 | MXC 字段 |
|---|---|
| 工作区根 | `readwritePaths` |
| Git、`.agents`、`.eidos` 等受保护元数据 | 工作区内嵌套的只读或禁止项（依赖 V1） |
| `permanent_denies`、`hard_confidentiality_denies`（数据目录、凭据等） | `deniedPaths` |
| `protected_write_paths`（Runtime、Python、受管资源、系统 Skill） | `readonlyPaths` |
| `active_skill_roots`、`runtime_roots` | `readonlyPaths` |
| 获批的附加 READ、WRITE、DENY | `readonlyPaths`、`readwritePaths`、`deniedPaths` |
| 沙盒 home 和 tmp | `readwritePaths`，并把 `TEMP`、`TMP` 指向该目录 |
| `network_enabled` | `true` 时 `egress.default: allow`；否则出站、入站和宿主回环全部拒绝 |
| `full_access` | 不进入容器，与现状一致 |

读取语义与 macOS 不同。macOS 默认全部可读再扣除禁止项；MXC 需要显式列出可读路径。Windows 版需要列出系统目录、工具链目录、工作区，以及用户目录减去禁止项。这比 macOS 更严格，用户工具链兼容性要由 V2、V4 实测确定。

MCP 档位映射：

| 档位 | 文件 | 网络 |
|---|---|---|
| `connector` | 只读插件根与 Python 运行时，读写私有 home/tmp | 出站放行 |
| `workspace_read` | 再加工作区只读 | 全部拒绝 |
| `browser` | 再加 Node 与浏览器运行时只读 | 只允许 `127.0.0.1/32`、`::1/128` 指定端口 |
| `browser_extension` | 同 `browser` | 另允许本机回环监听，需要 26100.9550 或 26200.9550 起的构建 |

### 4.3 使用方接入

- Shell PIPE：在 [shell.py](../../runtime/eidos_runtime/sandbox/shell.py) 的 `prepare_shell_launch` 中，把 Seatbelt 的 `profile.command(...)` 换成一个后端接口，Windows 返回 `wxc-exec.exe <config.json>` 的 argv。进程仍由 `host_processes` 的 Job Object 持有。
- Shell PTY：`ConPtyProcess` 启动 `wxc-exec.exe`。可行性取决于 V7。
- Hook：复用 Shell 路径。[hooks.py](../../runtime/eidos_runtime/runtime/hooks.py) 改为按平台选择沙盒类型。
- 本地 MCP：[mcp.py](../../runtime/eidos_runtime/extensions/mcp.py) 的 `_prepare_parameters` 在 Windows 上生成 MXC 配置，启动链为 `mcp_launcher.py` → `wxc-exec.exe` → 服务器。去掉 Windows 上的 `mcp_sandbox_unavailable` 早退。
- 文档 Skill：随 Shell 档位生效，移除 Windows 必须完全访问的限制。
- 文件提交 helper：第一、二期保持 Runtime 进程内的 NTFS 句柄提交，第三期再评估是否放入容器。
- Runtime 自身的 Git（`HardenedGitRunner`）：与 macOS 一致，不进入沙盒。

### 4.4 自检与 fail closed

新增 `run_mxc_self_test`，对应 `run_seatbelt_self_test`，逐项验证：

- 构建号与 executor 哈希；
- 工作区可写；
- 工作区外写入被拒绝；
- 数据目录读取被拒绝；
- 受保护元数据写入被拒绝；
- 默认禁网；
- 取消后整个进程树退出。

任一项失败即进入档位 B，并在日志中记录失败项。不得为了开发便利默认无沙盒。

### 4.5 协议与持久化

- `SandboxType` 新增 `windows_mxc`。该值会写入持久化的 `SandboxAttempt` 和审批证据，需要同步协议 DTO、Fixture、TypeScript 契约和 `docs/current/`。
- 旧版本读取新值时的兼容性需要单独确认。macOS 记录不受影响。
- 第一期跨 Sandbox、持久化和协议三层，按 AGENTS.md 第 31 节属于高风险改动，需要独立评审。

### 4.6 打包与依赖

- `wxc-exec.exe`（或 `mxc_ffi`）作为 Windows sidecar 随包发布，固定版本并校验哈希，纳入 `runtime.json` 清单（AGENTS.md 第 10 节）。
- 发布前需要确认许可证、分发条款和签名。
- MXC 依赖系统 PSEC，不需要额外服务。安装和升级时是否需要管理员权限，需在 V10 中确认。

### 4.7 待决：必需遥测

MXC 在受支持的 Windows 构建上会发送四个 Required 诊断事件，`telemetry.enabled` 不能关闭。这与 AGENTS.md“依赖不隐式联网”“默认诊断不依赖远程遥测”的要求冲突。这些事件属于 Windows 自身的诊断数据管道，不经过 Eidos，但仍需维护者明确决定是否接受，并在用户文档中说明。

## 5. 第 0 期实机验证清单

验证在 Windows 11 24H2 或 25H2 实机上进行，使用普通用户账户和临时工作区。每项记录构建号、命令和结果。

| 编号 | 验证内容 |
|---|---|
| V1 | 读写工作区内嵌套 `.git` 只读或禁止是否被强制执行，`git status` 能否运行 |
| V2 | 用户目录或系统盘整体只读、再扣除若干禁止项是否可行，启动性能如何 |
| V3 | `wxc-exec.exe` 的退出码、超时、取消语义，是否留在调用方的 Job Object 内，进程树能否整体结束 |
| V4 | PowerShell 在容器内能否启动，`LOCALAPPDATA` 被替换后模块、配置和历史是否正常 |
| V5 | MinGit 的 `git.exe` 与 MSYS2 `sh.exe`（Hook、别名）能否运行 |
| V6 | HTTPS 客户端：`git clone`、`pip install`、`npm install`、`curl.exe`、`Invoke-WebRequest` |
| V7 | `ConPtyProcess` 包裹 `wxc-exec.exe` 时的 UTF-8 输入输出、尺寸调整和 Ctrl-C |
| V8 | 本地 stdio MCP 长时间运行，`browser` 档位到 Chrome 调试端口的回环连接 |
| V9 | 自检各项（第 4.4 节）都能被正确判定 |
| V10 | 许可证、随包分发、签名、安装与升级是否需要管理员、Windows Server 上能否运行、必需遥测的具体内容 |

`windows-latest` 是 Windows Server。若 V10 确认 Server 不支持 PSEC，CI 需要一台自托管的 Windows 11 runner 才能运行沙盒测试。

## 6. 分期实施

| 阶段 | 范围 | 完成标准 |
|---|---|---|
| 第 0 期 | 实机验证（第 5 节），只写临时脚本，不改产品代码 | V1 到 V10 都有记录，必需遥测已有决定 |
| 第一期 | 后端接口、`MxcPolicyCompiler`、`windows_mxc`、自检；Shell PIPE 与 Hook；撤掉 Windows 关闭附加权限申请的分支 | macOS 行为与测试不变；Windows 档位 A 的 Shell 通过真实沙盒测试；档位 B 行为不变 |
| 第二期 | 四种 MCP 档位与文档 Skill | 设置页工具发现和受限 Run 都能在容器内运行本地 MCP |
| 第三期 | PTY；文件提交 helper 对齐；档位 C 是否实施 | 视第 0 期结论与用户需求决定 |

## 7. 风险

- MXC 发布时间短，SDK 1.0 曾整体改名，1.1.0-alpha 仍可能变化。只依赖 1.0.0 稳定契约，并在自检中校验 executor 版本。
- 显式读取授权可能让依赖用户配置的工具失败。需要按 V2、V4、V6 的结果维护工具链读取清单，不开放整个用户目录。
- 网络规则只有 CIDR，不能按域名放行。Eidos 目前只有开关语义，暂不受影响。
- 资料中有冲突：微软博客称 MXC 为早期预览，网络文档标注 GA，第三方报道称 2026 年 10 月正式发布。以实机构建和仓库版本为准。

## 8. 资料来源

- Codex：[Windows sandbox 文档](https://learn.chatgpt.com/docs/windows/windows-sandbox)、[InfoQ：Codex Windows 沙盒设计](https://www.infoq.com/news/2026/06/codex-windows-sandbox-design/)、[openai/codex#22044](https://github.com/openai/codex/issues/22044)
- MXC：[microsoft/mxc README](https://github.com/microsoft/mxc/blob/main/README.md)、[schema](https://github.com/microsoft/mxc/blob/main/docs/schema.md)、[MXC 策略示例](https://learn.microsoft.com/en-us/windows/apps/develop/mxc/examples)、[Windows 开发者博客：AI agent 平台安全](https://blogs.windows.com/windowsdeveloper/2026/06/02/windows-platform-security-for-ai-agents/)
- Anthropic：[sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime)、[Claude Code 沙盒文档](https://code.claude.com/docs/en/sandboxing)
- 其他：[Windows Sandbox CLI](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-cli)、[CreateProcessInSandbox](https://learn.microsoft.com/en-us/windows/win32/secauthz/createprocessinsandbox)

抓取说明：OpenAI 工程文章 `openai.com/index/building-codex-windows-sandbox/` 返回 403，未读取。srt README 的 Windows 部分与 MXC schema 末尾来自抓取工具的摘要，不是逐字原文。依赖这些细节的结论在第 0 期一并核对。
