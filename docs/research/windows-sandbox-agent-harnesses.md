# Windows 沙盒：Agent Harness 调研

状态：2026-11-26 调研。外部产品的行为、系统要求和发布状态变化很快，读者不能把本文当成当前事实。Eidos 的方案见 [Windows 沙盒适配设计](../design/windows-sandbox-design.md)。

## 1. 结论

- 2026 年下半年，原生 Windows 上的 Agent 命令沙盒已经收敛到微软 MXC 的 ProcessContainer 后端。系统机制是 PSEC（`CreateProcessSecurityEnvironment`），GitHub 文档也称它为 BaseContainer。GitHub Copilot CLI、VS Code Copilot Agent、OpenAI Codex（推荐模式）、Google Antigravity 都把它作为 Windows 默认或推荐后端。Anthropic sandbox-runtime 把它作为实验后端。
- 自建沙盒账户的方案（Codex `elevated`、Anthropic srt-win）出现在 MXC 之前。它们需要一次管理员安装，会改写宿主 ACL、保管沙盒账户密码，还有 Schannel TLS、DNS、SMB 等已知缺口。Codex 已把 `elevated` 和 `unelevated` 都列为 legacy。
- Claude Code 和 Cursor 目前不在原生 Windows 上沙盒化命令，官方路径是 WSL2。Gemini CLI 给可写目录打 Low 完整性标签，隔离较弱，标签还会留在用户文件上。
- 各产品都只把 Shell 及其子进程（部分产品还有本地 MCP、LSP 和搜索子进程）放进系统沙盒。读写文件的内置工具仍在宿主进程内做软件校验。这与 Eidos 当前的分层一致：文件工具在 Runtime 进程内用 NTFS 句柄提交。
- MXC 只支持 Windows 11。进程隔离的最低构建是 24H2 26100.9278、25H2 26200.9278。Copilot 和 VS Code 实际要求 2026 年 9 月的累积更新（25H2 26200.9550、26H1 28000.3086）。Windows 10 和 Windows 11 24H2 家庭版、专业版都已结束服务，旧系统兜底的收益在下降。

## 2. Windows 可用的隔离原语

| 原语 | 能隔离什么 | 管理员 | 采用者 | 对 Eidos |
|---|---|---|---|---|
| MXC ProcessContainer（PSEC） | 按路径授予读写、只读、禁止；出站 WFP 过滤（CIDR 与端口）；UI 限制；不改宿主 DACL；子进程放进 kill-on-close Job | 不需要 | Copilot CLI、VS Code、Codex `mxc`、Antigravity、srt（实验） | 首选 |
| 写受限令牌 + 合成 SID + ACL（Codex `unelevated`） | 只限制写；读与用户相同；网络只能靠环境变量劝阻 | 不需要 | Codex legacy | 不满足禁读和禁网 |
| 专用本地账户 + 写受限令牌 + WFP（Codex `elevated`、srt-win） | 写；读需要为沙盒账户补授权；按账户 SID 过滤网络 | 每台机器一次 | Codex legacy、srt alpha | 成本高，暂不做 |
| 自研 AppContainer | 能力模型；要改用户文件 ACL；回环豁免需要管理员 | 部分需要 | 无（Antigravity 在 PSEC 内使用） | OpenAI 评估后放弃，不自研 |
| 低完整性标签（Low IL） | 只限制写；被标记的目录对全机所有低完整性进程可写 | 不需要 | Gemini CLI | 不采用 |
| Windows Sandbox（`wsb`） | 整机虚拟机 | 需要启用功能 | 无 | 没有宿主工具链，家庭版不可用 |
| Agent Workspace 与 Agent 账户 | 独立会话和账户，面向操作 UI 的 Agent | 需要开启实验功能 | Copilot Actions | 面向 UI Agent，不适合 Shell |
| WSL2 + Linux 沙盒 | Linux 命名空间与 Landlock 等 | 需要安装 WSL | Claude Code、Cursor 的建议路径 | 换了操作系统，工作区和工具链都不同 |

## 3. 各产品做法

| 产品 | Windows 后端 | 系统要求 | 读 | 写 | 网络 | 状态 |
|---|---|---|---|---|---|---|
| OpenAI Codex | `mxc`（推荐）；legacy `elevated`、`unelevated` | MXC：24H2 26100.9278 / 25H2 26200.9278 起 | 宽读：只读卷根会递归，排除无关网络共享 | 工作区；`.git`、`.codex`、`.agents` 只读 | 允许回环与代理监听，直连出站和入站被拒 | Desktop 对个人账户自动优先 MXC；CLI 需 `features.prefer_mxc` |
| GitHub Copilot CLI | MXC BaseContainer | 25H2 KB5124010 或 26H1 KB5124006 起 | 默认拒绝；系统目录、PATH 工具目录和工具变量只读；不授予整个用户目录 | 当前目录、临时目录、`.git`；部分包缓存 | 默认允许出站，局域网关闭；凭据代理只给批准的 HTTPS 主机换入真实凭据 | 2026-06 公开预览，第三方报道已 GA |
| VS Code Copilot Agent | BaseContainer | 与 Copilot 相同的 KB | 默认开放开发工具目录与缓存 | 当前目录 | 默认允许出站；域名规则和凭据遮蔽需要开启本地网络 | Windows 为实验功能，默认关闭 |
| Google Antigravity | PSEC 中的 AppContainer | 24H2 及 2026-08 更新起 | 用户目录外可读；用户目录中其他文件不可见 | 工作区与写入工具涉及的路径 | 默认禁网；`read_url` 的域名进出站白名单 | App 端为预览 |
| Anthropic srt | 默认 srt-win（专用账户）；实验 MXC 后端 | srt-win 需一次 UAC 安装 | 与用户相同，另为沙盒账户补授工具目录读取 | 按会话添加 NTFS ACE | 按账户 SID 的 WFP 过滤，配合域名代理 | alpha |
| Claude Code | 原生 Windows 不沙盒 | — | — | — | — | 文档建议 WSL2；`sandbox.failIfUnavailable` 可改为启动失败 |
| Gemini CLI | `icacls` 设置 Low 完整性标签；或 Docker/Podman | — | 未说明 | 可写目录打标签，标签会保留 | 未说明 | 未标注成熟度 |
| Cursor | 官方文档未描述 Windows 沙盒 | — | — | — | — | 社区与第三方称原生 Windows 无沙盒，建议 WSL2 或 Dev Container |

### 3.1 OpenAI Codex

- 演进：OpenAI 先做了不需要管理员的 `unelevated`。它创建合成 SID `sandbox-write`，在工作区和 `writable_roots` 上授予该 SID 写权限，对 `<cwd>/.git`、`.codex`、`.agents` 显式拒绝写，再以写受限令牌启动命令。读取与用户相同；网络只是把代理变量指向 `127.0.0.1:9`、在 PATH 前放置 ssh 存根，进程可以绕过。
- `elevated` 改为两个本地账户 `CodexSandboxOffline` 和 `CodexSandboxOnline`，仍用写受限令牌，防火墙规则阻断 Offline 账户的全部出站。账户密码用 DPAPI 加密保存。沙盒账户本身读不到用户文件，安装程序异步地为用户目录、`C:\Windows`、`Program Files` 等补授读取。只有 `codex-windows-sandbox-setup.exe` 跨越 UAC；`codex.exe` 用 `CreateProcessWithLogonW` 以沙盒账户启动 `codex-command-runner.exe`，runner 再构造受限令牌启动命令。
- 被否决的方案：AppContainer 的能力模型不适合开放式开发工具；Windows Sandbox 无法直接操作真实检出和工具链，家庭版也没有；低完整性标签会改变工作区对全机的信任模型。
- MXC 模式：`codex-rs/mxc-sandbox` 把 Codex 可执行文件本身作为 MXC V1 SDK 的原生 helper，命令继承 stdio 或 ConPTY。它不创建账户、不改 ACL、不申请提权。权限由 Codex 的 PermissionProfile 生成，保留受保护元数据的例外。MXC 以挂起方式创建子进程，并在恢复前放进 kill-on-close Job；前台命令退出时剩余后代会被结束，后台常驻的开发服务器因此无法存活。`SystemDrive`、`LOCALAPPDATA` 固定为宿主值。显式选择 `mxc` 时不回退；命令失败也不会触发后端切换。
- README 列出的已知问题：硬链接的不同名字各自保留权限；卷根展开可能经由只读 junction 别名产生可写授权；禁止路径依赖系统能力探测；命令行长度限制仍然存在；ConPTY、进程树取消和 junction 需要冒烟测试；要实测 `powershell.exe` 和 `pwsh.exe`，不能只测 `cmd.exe`。
- 0.169 到 0.172（2026-11）的变更集中在 MXC 迁移到公开 V1 SDK、启动诊断、Win32 错误码和 legacy 沙盒服务的修复。

### 3.2 GitHub Copilot CLI

- 沙盒内：Shell 命令、内置搜索（ripgrep 子进程），默认还有本地 MCP 和 LSP 服务器。沙盒外：内置读写文件工具在 CLI 进程内按同一份策略做软件检查。
- 文件策略默认拒绝，每条路径为读写、只读或禁止，禁止优先，重叠时更具体的路径优先。“开发工具访问”默认开启，只读授予 PATH 目录和 `GOPATH`、`JAVA_HOME` 等变量指向的目录，Windows 上还有 `%LOCALAPPDATA%\Programs`。
- 禁止路径不存在时，Copilot 会先把它创建成目录（缺失的 `.env` 会变成目录），并保留下来；创建失败则不启动进程。
- 网络默认允许出站。Git 与 `gh` 的凭据换成占位值，本机代理只对批准的 HTTPS 主机换入真实凭据。Windows 上信任代理证书需要管理员，并会影响其他应用。
- Windows 已知问题：未安装 2026 年 9 月更新时，沙盒内 PowerShell 可能把盘符根当作当前目录，`Set-Location` 和依赖当前目录的 Git 命令会失败。

### 3.3 VS Code Copilot Agent

- Windows 沙盒为实验功能，默认关闭，要求与 Copilot CLI 相同的系统更新。macOS 要求 15 起；Linux 与 WSL2 使用 bubblewrap 和 slirp4netns。
- 沙盒覆盖 Agent 终端命令及其子进程，以及会话管理的 MCP 和语言服务器。沙盒以用户账户运行，官方明确它不是账户边界。
- 文件设置分读写、只读、禁止三类，优先级为禁止、只读、读写，另有默认开启的开发工具访问。网络默认允许出站，可配置域名白名单和黑名单；Windows 上使用代理、域名规则或凭据遮蔽时必须打开本地网络。
- 默认允许 Agent 申请在沙盒外运行，批准范围可以是单次或整个会话。

### 3.4 Google Antigravity

- Windows 上命令运行在 PSEC 中的 AppContainer，要求 Windows 11 24H2 及 2026 年 8 月更新起；Windows 10 和更早的 Windows 11 不可用。
- 工作区可读写；用户目录之外的文件夹可读不可写；用户目录中的其他文件不可见。默认禁网，`read_url` 允许的域名进入出站白名单。
- 沙盒不可用时，`proceed-in-sandbox` 退化为请求审查。在沙盒中失败的命令可以申请到沙盒外运行。

### 3.5 Anthropic sandbox-runtime 与 Claude Code

- srt-win（alpha）：一次自提权的 `windows-install` 创建 `srt-sandbox` 账户、`sandbox-runtime-users` 组和按账户 SID 生效的全机 WFP 过滤器，密码经 DPAPI 加密存入 HKLM。命令以写受限令牌运行在 Job Object 中。写入通过可继承 ACE 授予，从不替换已有安全描述符；会话结束时移除本会话的 ACE，崩溃后在下次初始化时清理。
- srt 文档列出的网络缺口：系统解析器的 DNS 不受限；经 SMB 重定向器的文件访问和 `wsl.exe` 不受网络过滤器约束；模拟 `ANONYMOUS LOGON` 的线程可以绕过过滤。文档说明该过滤器只能拦截直接连接的命令，拦不住专门绕过它的代码。
- 已知兼容问题：在受限令牌下，Schannel 客户端（`curl.exe`、`Invoke-WebRequest`、.NET `HttpClient`、NuGet、`winget`、使用 Schannel 的 Git）无法完成 TLS，沙盒内没有变通办法；自带 TLS 栈的 Node、Python、Go 不受影响。
- srt 的实验 MXC 后端不需要安装、沙盒账户和 ACL 改动；已知 MSYS2 程序（Git Bash、`sh.exe`）启动即崩溃（`0xC0000005`），原生 `git.exe` 正常。
- Claude Code 文档：沙盒支持 macOS、Linux 和 WSL2；原生 Windows 不沙盒化命令。

### 3.6 Gemini CLI 与 Cursor

- Gemini CLI 的 Windows 原生沙盒用 `icacls` 给需要写入的文件和目录设置 Low 完整性级别，会跳过 `C:\Windows` 等系统目录。标签在会话结束后保留，文档给出手工恢复为 Medium 的命令。其他选项是 Docker 或 Podman。
- Cursor 的 macOS 沙盒使用 Seatbelt，Linux 使用 Landlock 和 seccomp，内核不满足时退回到逐条审批。官方 Run Modes 文档没有描述 Windows 沙盒。社区和第三方材料称原生 Windows 命令不在沙盒中运行，建议 WSL2 或 Dev Container；论坛还有 Windows 沙盒拦截子进程、命令无输出等报告，两类说法互相矛盾。

### 3.7 微软平台

- 2026-06 Build 发布 MXC SDK 早期预览，称其为面向 Agent 的跨平台、策略驱动执行层。合作方包括 GitHub Copilot CLI（已采用）、OpenClaw 和 NVIDIA OpenShell。
- MXC 的 Session Isolation 以独立用户账户运行，初版只支持非交互会话，需要更新的构建（24H2 26100.9550 起）。
- Agent Workspace 与 Agent 账户服务于 Copilot Actions 这类操作界面的 Agent，属于实验功能且默认关闭。Agent 365 与 Entra Agent ID 负责企业的发现、身份和治理，与本机命令隔离无直接关系。

## 4. MXC 技术要点

### 4.1 发布与兼容承诺

- 版本：v1.0.0（10 月 7 日）提供稳定契约 1.0.0；v1.0.1（10 月 17 日）；v1.1.0（11 月 15 日，最新）删除了 ProcessContainer 的回退，执行和拒绝捕获都要求系统原生 PSEC。发布页只显示月日，按上下文推断为 2026 年。
- 许可证 MIT。SDK 有 Rust（`mxc-sdk`）、.NET、Node 三种，没有 Python SDK。另有原生执行器 `wxc-exec.exe`（读取 JSON 请求）和 C 接口 `mxc_ffi`（接收 UTF-8 JSON）。v1.0.x 的 `mxc-release-binaries.zip` 中的签名二进制标注为“可用于测试”。
- 契约：SDK 遵循 SemVer，修复不回移到旧版本线；请求的 schema 版本必须精确匹配，只接受 0.9.0-alpha、1.0.0、1.1.0-alpha；已发布的稳定 schema 不可变；V1 的破坏性变更需要新的 V2 命名空间。V1 SDK 内部固定使用 1.0.0。

### 4.2 系统要求

| 版本 | 进程隔离 | 会话隔离 |
|---|---|---|
| 24H2 | 26100.9278 | 26100.9550 |
| 25H2 | 26200.9278 | 26200.9550 |
| 26H2 | 26300.9550 | 26300.9550 |
| 26H1 | 28000.2804 | 28000.3086 |

- 26100.9278、26200.9278 支持容器连接宿主的中完整性监听方；26100.9550、26200.9550 起支持容器内的回环服务和低完整性监听方。
- MXC 文档只列出 Windows 11，没有提到 Windows 10、Windows Server 或家庭版的限制。拒绝捕获等能力靠运行时探测决定，不按构建号表判断。Codex 文档说明微软仍在向设备逐步推送所需能力。

### 4.3 策略语义

- 文件：默认拒绝，`readwritePaths`、`readonlyPaths`、`deniedPaths` 都要求完整路径。优先级为禁止、只读、读写，更具体的路径覆盖祖先。`deniedPaths` 只有在 PSEC 报告 `PSE_SUPPORT_FS_DENY` 时才生效，否则请求被拒绝。授予卷根（如 `C:\`）不会自动递归到子目录。向上查找标记文件的工具需要父目录授权。授予 `ALL_APPLICATION_PACKAGES` 的宿主权限也会对容器生效，显式禁止可以覆盖。
- 性能：外部报告 #1468 测得环境创建时间随授权路径数线性增长，约每条 1 ms（1 个目录 40 ms，1000 个文件约 1.1 s），主要耗时在 `GetVolumePathNameW`。该报告未经微软确认。
- 网络：出站默认拒绝，规则只到 CIDR、协议和端口，不支持域名。`internetClient` 能力对应公网；`ingress.default: allow` 会启用 `privateNetworkClientServer`，私网出站和入站只能一起打开。回环由 `ingress.hostLoopback` 控制。PSEC 在系统提升上下文中下发 WFP 过滤器，每次启动不弹 UAC。
- 进程：`commandLine` 必填；`env` 按原样使用；PSEC 要求环境中有 `SYSTEMROOT` 和 `LOCALAPPDATA`，并可能把 `LOCALAPPDATA` 换成容器配置目录。MXC 不实施容器级超时，由各后端处理。生命周期文档说明执行器意外退出时工作负载可能继续运行，沙盒状态不确定。
- UI：`ui.disable` 默认开启，对应 Win32k 系统调用禁用；剪贴板和输入注入可分别控制。Codex 为了 PowerShell 能启动而保留 Win32k 和桌面句柄。
- 已知兼容问题：PowerShell 在策略不允许枚举盘符根时可能启动失败，微软不建议用整盘读取规避；MSYS2 程序在 MXC 下崩溃（srt 报告）。
- 学习模式：`captureDenials` 输出 `denials.json`，可用于诊断拒绝；`wxc-exec --audit` 会关闭全部隔离，不能用于不可信代码。

### 4.4 `wxc-exec` 与遥测

- `wxc-exec.exe` 以工作负载的退出码退出；取消时非零退出码替换为 130；请求解析失败等返回 1。诊断写 stderr。MXC 自身错误的退出码映射、stdin 处理和超时哨兵值在文档中没有说明。
- 在受支持的 Windows 构建上，MXC 会发送四个必需诊断事件（`MXC.ExecutionStart`、`MXC.ExecutionEnd`、`MXC.Error`、`MXC.ProcessExited`），内容为诊断字段和随机调用标识，不需要用户同意，`telemetry.enabled`、同意撤回和管理策略都不能关闭。文档说明从源码构建的发布版本可以把 `MXC_TELEMETRY_COMPONENT_PATH` 设为空值来退出遥测路由。

## 5. 对 Eidos 的启示

1. 后端只选 MXC ProcessContainer。自建账户方案的管理员安装、ACL 改写和 TLS 缺口都与 Eidos 已有的文件身份与私有 ACL 核验冲突，目标用户的系统也已普遍满足 MXC 要求。
2. Python 没有 MXC SDK。Codex 的做法（把 V1 SDK 编进一个原生 helper，继承 stdio 或 ConPTY）比依赖 `wxc-exec` 的命令行契约更可控。
3. 读取语义可以选宽读（Codex 的卷根展开，接近 Eidos 在 macOS 上的“全局可读、扣除禁止项”）或白名单（Copilot 的开发工具目录）。Eidos 应与 macOS 保持一致。
4. 文件工具继续留在 Runtime 进程内，与各产品一致。
5. 能力以运行时探测和自检为准，构建号只做快速排除。
6. 需要实测的已知问题：PowerShell 当前目录与盘符根、MSYS2 `sh.exe`、`LOCALAPPDATA` 替换、TLS、后台进程被结束、缺失禁止路径的处理、硬链接与 junction。
7. 不可用时的行为：Copilot 和 VS Code 允许经批准后在沙盒外重试，Antigravity 退回审查，Claude Code 可配置为启动失败。Eidos 现有的“审批后无沙盒运行”和完全访问模式可以保留。

## 6. 资料来源

- Codex：[Windows sandbox 文档](https://learn.chatgpt.com/docs/windows/windows-sandbox)、[Building a safe, effective sandbox to enable Codex on Windows](https://openai.com/index/building-codex-windows-sandbox/)、[codex-rs/mxc-sandbox](https://github.com/openai/codex/tree/main/codex-rs/mxc-sandbox)、[codex-rs/windows-sandbox-rs](https://github.com/openai/codex/tree/main/codex-rs/windows-sandbox-rs)、[Changelog](https://learn.chatgpt.com/docs/changelog)、[InfoQ 报道](https://www.infoq.com/news/2026/06/codex-windows-sandbox-design/)
- MXC：[README](https://github.com/microsoft/mxc)、[schema](https://github.com/microsoft/mxc/blob/main/docs/schema.md)、[OS 版本支持](https://github.com/microsoft/mxc/blob/main/docs/backends/process-container/os-version-support.md)、[网络](https://github.com/microsoft/mxc/blob/main/docs/backends/process-container/networking.md)、[文件系统](https://github.com/microsoft/mxc/blob/main/docs/backends/process-container/filesystem.md)、[排障](https://github.com/microsoft/mxc/blob/main/docs/troubleshooting.md)、[学习模式](https://github.com/microsoft/mxc/blob/main/docs/learning-mode.md)、[遥测](https://github.com/microsoft/mxc/blob/main/docs/telemetry.md)、[版本策略](https://github.com/microsoft/mxc/blob/main/docs/development/architecture/versioning.md)、[生命周期](https://github.com/microsoft/mxc/blob/main/docs/container-lifecycle.md)、[Releases](https://github.com/microsoft/mxc/releases)、[issue #1468](https://github.com/microsoft/mxc/issues/1468)、[Windows 开发者博客](https://blogs.windows.com/windowsdeveloper/2026/06/02/windows-platform-security-for-ai-agents/)、[Microsoft Learn：MXC](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/microsoft-execution-containers)
- GitHub：[本地沙盒概念](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/understanding-local-sandboxing)、[使用本地沙盒](https://docs.github.com/en/copilot/how-tos/cloud-and-local-sandboxes/using-local-sandboxing)、[Changelog 2026-06-02](https://github.blog/changelog/2026-06-02-cloud-and-local-sandboxes-for-github-copilot-now-in-public-preview/)、[Microsoft Command Line 博客](https://commandline.microsoft.com/local-models-sandboxed-tools-github-windows/)
- VS Code：[Agent sandboxing](https://code.visualstudio.com/docs/agents/run/agent-sandboxing)
- Google：[Antigravity 终端沙盒](https://antigravity.google/docs/sandbox?tab=cli)、[Gemini CLI 沙盒](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/sandbox.md)
- Anthropic：[sandbox-runtime](https://github.com/anthropics/sandbox-runtime)、[Claude Code 沙盒文档](https://code.claude.com/docs/en/sandboxing)
- Cursor：[Run Modes](https://cursor.com/docs/agent/security/run-modes)、[论坛：Windows 沙盒无法运行命令](https://forum.cursor.com/t/cannot-run-any-commands-due-to-sandbox-on-windows-in-2-5/152040/6)
- 微软平台与生命周期：[Experimental agentic features](https://support.microsoft.com/en-us/windows/ai/ai-features/experimental-agentic-features)、[Agent governance](https://learn.microsoft.com/en-us/windows/security/application-security/agent-governance)、[BleepingComputer：24H2 家庭版和专业版结束支持](https://www.bleepingcomputer.com/news/microsoft/microsoft-windows-11-24h2-home-and-pro-reached-end-of-support/)

抓取说明：OpenAI 工程文章、MXC schema 后段、MXC 版本策略后段、srt README 中段和 `wxc-exec` 源码后段来自抓取工具的摘要，不是逐字原文。Jonny Johnson 的 Codex 沙盒分析（Medium）返回 403，未读取。Codex 的 MXC 细节来自 crate README，未逐行核对 Rust 源码。
