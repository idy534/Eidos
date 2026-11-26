# Windows 沙盒适配设计

状态：2026-11-26 修订为 V2，尚未实施，第 0 期实机验证尚未开始。V1（2026-11-17）选定 MXC ProcessContainer 作为 Windows 后端，本版沿用该方向，并根据 [Agent Harness 调研](../research/windows-sandbox-agent-harnesses.md) 更新 MXC 发布状态与系统要求，确定接入方式、读取语义和进程模型，补充实机验证项，把分期细化到文件、测试和验收。当前 Windows 行为以 [限制说明](../current/limitations.md)、[架构说明](../current/architecture.md)、源码和测试为准。

## 1. 目标与范围

在非完全访问的 Run 中，让以下能力在 Windows 上受系统沙盒约束地运行：

- `run_shell` 的 PIPE 和 PTY；
- 命令 Hook；
- 本地 stdio MCP 的四种权限档：`connector`、`workspace_read`、`browser`、`browser_extension`；
- 经 Shell 运行的文档 Skill。

不在范围内：

- 文件工具继续在 Runtime 进程内用 NTFS 句柄提交（[windows_file_commit.py](../../runtime/eidos_runtime/sandbox/windows_file_commit.py)）。Copilot CLI、VS Code 和 Codex 的文件工具也都在宿主进程内做软件校验；
- Runtime 自身的 Git（`HardenedGitRunner`）、BrowserHost 启动的 Chrome，与 macOS 一致，不进入沙盒；
- `full_access` Run 不进入沙盒，与现状一致。

约束：

- macOS 的 Seatbelt 路径和测试保持不变；
- 权限物化和策略编译由 Eidos 负责，系统机制只负责强制执行（AGENTS.md 第 8、15 节）；
- 沙盒不可用时 fail closed，保留现有的审批后无沙盒运行和完全访问路径；
- 不建多后端框架，只抽出 Shell 启动需要的一个窄接口。

## 2. 现状

| 位置 | Windows 当前行为 |
|---|---|
| [seatbelt.py:521](../../runtime/eidos_runtime/sandbox/seatbelt.py) `run_seatbelt_self_test` | 直接返回 `unsupported_platform`，[server.py:2556](../../runtime/eidos_runtime/protocol/server.py) 因此上报 `runShell=false` |
| [shell.py:476](../../runtime/eidos_runtime/sandbox/shell.py) `prepare_shell_launch` | 只认 `SandboxType.MACOS_SEATBELT`，需要 `SeatbeltProfile`，否则抛 `SeatbeltUnavailableError`，结果为 `sandbox_unavailable` |
| [engine.py:1893](../../runtime/eidos_runtime/runtime/engine.py) | Windows 上关闭附加权限申请（`allow_additional_permissions`） |
| [mcp.py:574](../../runtime/eidos_runtime/extensions/mcp.py) `_prepare_parameters` | `sandbox=True` 时返回 `mcp_sandbox_unavailable`；只有完全访问 Run 以 `sandbox=False` 启动（[run_resources.py:154](../../runtime/eidos_runtime/runtime/run_resources.py)） |
| [hooks.py:244](../../runtime/eidos_runtime/runtime/hooks.py) | 非完全访问时固定选择 `MACOS_SEATBELT`，Windows 上无法运行 |
| [tool_orchestrator.py:268](../../runtime/eidos_runtime/runtime/tool_orchestrator.py) | 只为 Seatbelt 记录 `profileHash` |
| [permissions.py:433](../../runtime/eidos_runtime/sandbox/permissions.py) `SandboxType` | 只有 `macos_seatbelt` 和 `none` |

可直接复用的 Windows 基础设施：

- [windows_processes.py](../../runtime/eidos_runtime/infrastructure/windows_processes.py) 的 `ProcessJob`（`KILL_ON_JOB_CLOSE`）与 `JobProcess`；
- [windows_process_launcher.py](../../runtime/eidos_runtime/infrastructure/windows_process_launcher.py) 的启动闸门：父进程把子进程放进 Job 后才放行；
- [windows_terminal.py](../../runtime/eidos_runtime/infrastructure/windows_terminal.py) 的 `ConPtyProcess`；
- [build-windows-runtime.py](../../scripts/build-windows-runtime.py) 按固定版本和 SHA-256 打包 ripgrep、MinGit，并写入运行时清单。

macOS 的 Seatbelt 语义是 Windows 需要对齐的基线：全局可读（[seatbelt.sbpl](../../runtime/eidos_runtime/sandbox/seatbelt.sbpl) 的 `allow file-read*`），由动态策略扣除数据目录和保密路径；只能写工作区（不含 Git 元数据）、沙盒临时目录和获批路径；HOME 只读；默认禁网。

## 3. 决策

### D1 只做 MXC ProcessContainer

- MXC 稳定契约 1.0.0 已于 2026-10 发布，最新版 v1.1.0 删除了 ProcessContainer 的回退，执行只走系统原生 PSEC。GitHub Copilot CLI、VS Code、Codex（推荐模式）、Antigravity 都以它为 Windows 后端。
- MXC 不创建账户、不改宿主 DACL、不需要管理员，与 Eidos 对数据目录所有者和私有 ACL 的核验不冲突。
- 自建账户方案（Codex `elevated`、srt-win）需要一次 UAC 安装、改写用户文件 ACL、保管账户密码，并有 Schannel TLS、DNS、SMB 等缺口。Windows 10 消费版 ESU 和 Windows 11 24H2 家庭版、专业版都已在 2026-10 结束服务，兜底收益有限。V1 的“档位 C”取消；只有在实际用户数据表明大量机器停在旧构建时才重新评估。

### D2 自建随包 helper，基于 MXC V1 Rust SDK

新增原生 helper `eidos-sandbox-win.exe`，用固定版本的 MXC V1 Rust SDK 构建。它只负责把 Python 编译好的 MXC 1.0.0 请求交给 SDK、启动并等待工作负载，不做任何权限判断。这与 Codex 的 `mxc-sandbox` 做法相同。

其他接入方式不采用：

| 方式 | 不采用的原因 |
|---|---|
| 随包 `wxc-exec.exe` | 只有 JSON schema 受版本承诺约束；CLI 的退出码映射、stdin、超时和进程树行为都没有文档；发布包中的签名二进制标注为测试用途。第 0 期用它做验证 |
| Runtime 进程内用 ctypes 调 `mxc_ffi` | 子进程会以 Runtime 为父进程，容易继承协议 stdout；FFI 的执行语义没有文档；崩溃会带倒 Runtime |
| Electron Main 用 Node SDK | 命令执行属于 Runtime 的 Tool 链路，放到 Main 会形成第二个执行权威（AGENTS.md 第 3、5 节） |

按 AGENTS.md 第 9、10 节，自研范围限于约 400 行的 Rust 启动壳，维护成本是跟随 MXC 小版本升级，测试由跨平台单元测试加 Windows 实机冒烟组成（第 10 节）。

### D3 读取与 macOS 对齐：宽读加禁止项

- 把每个本地固定 NTFS 卷的根目录展开为直接子项，作为 `readonlyPaths`；顶层的 junction 和符号链接不展开，避免 Codex 报告的经 junction 别名产生授权。用户目录由此只读。
- 数据目录、保密路径、永久禁止项进入 `deniedPaths`。这一档要求 PSEC 报告 `PSE_SUPPORT_FS_DENY`，没有该能力时整个后端判为不可用，不降级为“无禁读”。
- 不采用 Copilot 的白名单读取。白名单需要维护工具链清单，会让 PATH 之外的工具和配置失效，也与 macOS 行为不一致。
- 授权路径数控制在约 100 条以内，避免外部报告 #1468 中按路径线性增长的环境创建开销。

### D4 能力按运行时探测判定

- 构建号只用于快速排除。建议支持线为 2026 年 9 月累积更新：24H2 26100.9550、25H2 26200.9550、26H2 26300.9550、26H1 28000.3086。这条线修复了沙盒内 PowerShell 把盘符根当作当前目录的问题，并支持容器内回环监听，与 Copilot 和 VS Code 的要求一致。
- 最终以 helper `probe` 和启动自检（第 7 节）为准。微软仍在向设备逐步推送所需能力，构建号满足不代表可用。

### D5 必需遥测

MXC 在受支持的构建上发送四个必需诊断事件，SDK 设置、用户同意和管理策略都不能关闭，这与 AGENTS.md 的“依赖不隐式联网”“默认诊断不依赖远程遥测”冲突。MXC 文档说明从源码构建的发布版本可以把 `MXC_TELEMETRY_COMPONENT_PATH` 设为空值以退出遥测路由。

维护者已于 2026-11-26 确认以下做法：

- Eidos 从源码构建 helper，构建时不打包微软签名的遥测组件，运行时把该变量设为空；
- 第 0 期用抓包和 ETW 确认事件确实不再发出（V10）；
- 若无法关闭，由维护者决定是否接受，并在用户文档中说明。未决定前不进入第一期。

### D6 不可用时保持现状

不可用时的行为与今天相同：受限 Shell、Hook 和本地 MCP 拒绝启动；模型可以申请 `require_escalated`，按当前模式审批后无沙盒运行；完全访问 Run 直接运行。不提供“沙盒不可用时自动无沙盒运行”的开关。

## 4. 架构

### 4.1 调用链

```text
ToolOrchestrator → shell_orchestration → prepare_shell_launch
  → SandboxLauncher(windows_mxc)
      MxcPolicyCompiler(EffectivePermissionProfile, purpose) → MXC 1.0.0 JSON
  → host_processes.start                       Eidos ProcessJob（KILL_ON_JOB_CLOSE）
      → eidos-sandbox-win.exe run --request-handle R --status-handle S
          → MXC V1 SDK → PSEC 环境（AppInfo 服务）+ kill-on-close Job
              → powershell.exe -NoLogo -NoProfile -EncodedCommand …
```

### 4.2 `SandboxLauncher`

在 [shell.py](../../runtime/eidos_runtime/sandbox/shell.py) 中，`prepare_shell_launch` 和 `ShellLaunchSpec.terminal_profile` 目前直接依赖 `SeatbeltProfile.command(argv, terminal_device=...)`。把这一个调用抽成协议：

```python
class SandboxLauncher(Protocol):
    sandbox_type: SandboxType
    profile_hash: str
    def command(self, argv: Sequence[str], *, terminal_device: Path | None = None) -> Sequence[str]: ...
```

`SeatbeltProfile` 原样满足该协议；新增 `MxcLaunchProfile`。`sandboxed` 的判断改为 `attempt.sandbox is not SandboxType.NONE`。不再增加其他抽象。

### 4.3 `MxcPolicyCompiler`

- 新文件 `runtime/eidos_runtime/sandbox/mxc_policy.py`，与 `SeatbeltPolicyCompiler` 并列，输入同一份 `EffectivePermissionProfile` 和用途（`shell`、`hook`、`mcp:<profile>`），输出 MXC 1.0.0 JSON。
- 随源码保存 MXC 稳定 schema `mxc-config.schema.1.0.0.json` 并固定哈希，编译结果用 `jsonschema` 校验（AGENTS.md 第 19 节）。
- 输出按路径排序，`profile_hash` 取规范化 JSON 的 SHA-256。
- 编译器是纯函数，可以在 macOS 和 Linux 的单元测试中运行。卷根展开、路径存在性等宿主查询通过注入的探测接口完成。

### 4.4 `eidos-sandbox-win.exe`

- 定位：helper 本身就是沙盒启动器，地位相当于 macOS 的 `/usr/bin/sandbox-exec`。它在容器外运行，只启动容器内的工作负载，不接受模型或项目文件的参数来改变策略。
- 位置：`native/windows-sandbox/`，Rust，`Cargo.lock` 入库；MXC 依赖固定到发布 tag 的 git 修订；工具链跟随 MXC 的 `rust-toolchain.toml`（当前 1.93）。
- `probe`：stdout 输出一行 JSON，包含 helper 版本、MXC 版本、系统构建号、PSEC 是否可用、`fsDeny`、`loopbackServer`。
- `run --request-handle R --status-handle S [--pty]`：
  - 从继承的句柄 R 读取请求。Eidos 在把 helper 放进 Job 之后才写入请求，句柄 R 因此兼任启动闸门，沙盒启动不再经过 Python 启动器；
  - 请求不落盘，沙盒内进程无法读取；
  - 工作负载继承 helper 的 stdio（PIPE）或控制台（ConPTY）；
  - 工作负载恢复运行后，向句柄 S 写 `{"event":"started"}`；启动前失败时写 `{"event":"failed","code":…}` 后退出。`code` 取值为 `request_invalid`、`backend_unavailable`、`policy_rejected`、`launch_failed`；
  - 启动后以工作负载退出码退出；
  - 不实施超时，不重试，不回退到其他后端。
- Python 根据状态通道区分“未启动”和“已启动”：未启动的失败映射为 `sideEffectsMayExist=false` 的结果；已启动后 helper 异常退出，按现有规则记录未知结果。退出码本身不承担区分职责，因此不会与工作负载的退出码冲突。
- 打包：[build-windows-runtime.py](../../scripts/build-windows-runtime.py) 调用 `cargo build --locked --release`，产物放入 `resources/bin/windows-sandbox/`，SHA-256 写入运行时清单；Runtime 使用前按清单校验哈希。代码签名暂不纳入本方案，后续随 Windows 安装包签名单独处理（第 12 节）。

## 5. 权限映射

### 5.1 Shell 与 Hook

| Eidos 权限 | MXC 字段 |
|---|---|
| 本地固定 NTFS 卷根的直接子项（跳过顶层重解析点） | `readonlyPaths` |
| 工作区根和附加根 | `readwritePaths` |
| 工作区内的 Git、`.agents`、`.eidos` 等 `protected_metadata_paths` | `readonlyPaths`（更具体，覆盖工作区读写） |
| `protected_write_paths`（Runtime、Python、受管资源、系统 Skill） | `readonlyPaths` |
| `permanent_denies`、`hard_confidentiality_denies`（数据目录、凭据等） | `deniedPaths` |
| 获批的附加 READ、WRITE、DENY | `readonlyPaths`、`readwritePaths`、`deniedPaths` |
| 沙盒 home 和 tmp | `readwritePaths`，`TEMP`、`TMP` 指向沙盒 tmp |
| `network_enabled=false` | `egress.default: deny`，`ingress.default: deny`，`ingress.hostLoopback: deny` |
| `network_enabled=true` | `egress.default: allow`，`ingress.default: deny`（公网可达，局域网不可达，见 V11） |
| UI | `ui.disable: false`（保留 PowerShell 启动所需的 Win32k，做法同 Codex）、`clipboard: none`、`injection: false` |
| 环境 | 沿用现有 Shell 环境继承策略；补齐 `SYSTEMROOT`；`LOCALAPPDATA` 固定为宿主值 |

与 macOS 的差异：

- macOS 的禁止规则对不存在的路径同样生效。MXC 的处理需要实测（V1）：Copilot 会把缺失的禁止路径创建为目录，Eidos 不接受这种副作用（缺失的 `.git` 被创建为目录会让工作区看起来像仓库）。在 V1 有结论前，编译器只为已存在的路径生成例外项，并依靠现有的工作区清单差异检测发现新建的受保护元数据。
- 局域网访问在 V11 有结论后再决定是否开放。

### 5.2 本地 MCP

| 档位 | 文件 | 网络 |
|---|---|---|
| `connector` | 插件根和 Python 运行时只读；私有 home/tmp 读写；不做卷根宽读 | `egress.default: allow` |
| `workspace_read` | 在 `connector` 之上加工作区只读 | 全部拒绝 |
| `browser` | 在 `workspace_read` 之上加 Node 和浏览器运行时只读 | 只允许 `127.0.0.1/32`、`::1/128` 上 BrowserHost 分配的端口 |
| `browser_extension` | 同 `browser` | 另开放 `ingress.hostLoopback`，需要 9550 支持线 |

### 5.3 附加权限

档位 A 可用时撤掉 [engine.py:1893](../../runtime/eidos_runtime/runtime/engine.py) 中的 Windows 例外。附加 READ、WRITE、DENY 和联网申请沿用现有的审批与 Run Grant 流程，物化后重新编译 MXC 请求，`profile_hash` 随之变化。

## 6. 进程、取消与恢复

- 归属：Eidos 的 `ProcessJob` 持有 helper；MXC 在 helper 内把工作负载放进嵌套的 kill-on-close Job。结束父 Job 会同时结束嵌套 Job 中的进程，取消和超时继续由 Runtime 现有逻辑结束 Eidos Job（V3 验证）。
- PIPE 的 Ctrl-C 沿用现有行为，结束整个进程树。PTY 由 `ConPtyProcess` 启动 helper 并加 `--pty`，Ctrl-C 交给控制台（V7 验证）。
- 前台命令结束后，剩余后代进程会被结束，后台常驻服务无法存活。这与 macOS 结束整个进程组的行为一致，写入用户文档。
- 每次执行都是一次性容器，没有常驻容器、ACL 残留或沙盒账户，Runtime 重启时不需要额外清理。PSEC 环境是否会因 helper 被强杀而残留，由 V3 确认。
- `-EncodedCommand` 受 Windows 32767 字符命令行限制。该限制在无沙盒时已经存在，本方案不改变，超长时返回明确错误。

## 7. 自检与能力判定

新增 `run_mxc_self_test`，在启动阶段 `mxc_self_test` 中运行，结构对齐 `run_seatbelt_self_test`：

1. 校验 helper 哈希；
2. 运行 `probe`，检查支持线、PSEC 可用和 `fsDeny`；
3. 在临时工作区、临时数据目录和临时 Git 仓库上执行功能检查，检查项名称与 macOS 一致：`system_execute`、`workspace_write`、`external_read_allowed`、`home_read_allowed`、`home_write_denied`、`sandbox_tmp_write`、`external_write_denied`、`data_models_read_denied`、`data_private_token_read_denied`、`sensitive_read_allowed`、`git_read_allowed`、`git_write_denied`、`symlink_escape_denied`（Windows 上覆盖 junction）、`child_inherits_policy`、`loopback_denied`、`timeout_enforced`。

任一项失败即判为不可用，日志记录失败项，`runShell` 为 `false`，本地 MCP 继续返回 `mcp_sandbox_unavailable`。检查合并到尽量少的容器中执行，目标总耗时在 2 秒以内；Windows 的 `initialize` 限时为 60 秒，不需要调整。

## 8. 协议、持久化与界面

- `SandboxType` 新增 `windows_mxc`。该值写入持久化的 `SandboxAttempt` 和审批载荷中的 `sandbox` 字段，[tool_orchestrator.py:268](../../runtime/eidos_runtime/runtime/tool_orchestrator.py) 同时为它记录 `profileHash`。
- 没有 SQLite 表结构变化。需要确认旧版本读取 `windows_mxc` 时的行为，以及 Desktop 审批卡片对未知 `sandbox` 值的展示。
- `initialize` 的能力字段第一期不变。第二期新增不可用原因（例如 `os_update_required`、`fs_deny_unsupported`），Desktop 据此提示用户更新 Windows，同步协议 Fixture、TypeScript 契约和 `docs/current/`。

## 9. 第 0 期实机验证

在 Windows 11 25H2 和 26H2（均已安装 2026 年 9 月或更新的累积更新）实机上，以普通用户账户、临时工作区进行，另找一台 24H2 或未更新的机器验证判定。先用 `wxc-exec.exe` 和临时 Python 脚本，不改产品代码。每项记录构建号、命令、结果和耗时。

| 编号 | 验证内容 |
|---|---|
| V1 | 读写工作区内嵌套只读 `.git` 是否生效，`git status`、`git commit` 能否运行；禁止路径和只读例外不存在时 MXC 是拒绝、忽略还是创建 |
| V2 | 卷根展开加用户目录只读加禁止项的组合是否可行；PowerShell 5.1 和 `pwsh` 能否启动；授权 50、100、200 条路径时的环境创建耗时 |
| V3 | helper 位于 Eidos Job 内时 MXC 的嵌套 Job 是否正常；结束 Eidos Job 后工作负载进程树是否全部退出；强杀 helper 后工作负载与 PSEC 环境是否残留 |
| V4 | 沙盒内 PowerShell 的当前目录、模块加载、`LOCALAPPDATA`；超长 `-EncodedCommand` 的报错 |
| V5 | MinGit 的 `git.exe` 与 `sh.exe`（Git Hook、别名）；srt 报告 MSYS2 程序在 MXC 下崩溃 |
| V6 | `network_enabled=true` 时 `git clone https`、`pip install`、`npm install`、`curl.exe`、`Invoke-WebRequest` 的 TLS |
| V7 | `ConPtyProcess` 包裹 helper 时的 UTF-8 输入输出、尺寸调整和 Ctrl-C |
| V8 | 本地 stdio MCP 长时间运行；`browser` 档到 Chrome 调试端口的回环连接；`browser_extension` 的容器内监听 |
| V9 | 自检在受支持、缺少更新和 24H2 机器上的判定 |
| V10 | 从源码构建并去掉遥测组件后，抓包和 ETW 中是否还有 MXC 事件；未签名 helper 在启用智能应用控制（Smart App Control）的机器上能否运行；Windows Server 2025（GitHub `windows-latest`）是否支持 PSEC |
| V11 | 联网时公网可达、局域网不可达；禁网时直连、DNS（`Resolve-DnsName` 与 `getaddrinfo`）和 SMB 共享访问的结果 |
| V12 | 工作区内指向受保护路径或工作区外的 junction、符号链接和硬链接 |
| V13 | 每条命令相对无沙盒的额外耗时（p50、p95） |

完成标准：V1 至 V13 都有记录，D3、D5 得到确认或修订，并据此决定是否进入第一期。记录写入 `docs/evaluations/`。

## 10. 分期实施

每期一个 PR，按下列边界分 Commit（AGENTS.md 第 24 节）。估算以一名熟悉代码的开发者计。

### 第 0 期：实机验证（约 3 至 5 天）

范围与完成标准见第 9 节。

### 第一期：Shell 与 Hook（约 2 周）

| Commit | 内容 | 测试 |
|---|---|---|
| 1 | `native/windows-sandbox/` helper；`build-windows-runtime.py` 构建并写入哈希；[windows.yml](../../.github/workflows/windows.yml) 安装 Rust 工具链 | Rust 单元测试；Windows CI 构建并校验哈希 |
| 2 | `SandboxLauncher` 协议，`SeatbeltProfile` 适配，`sandboxed` 判断改为非 `NONE` | macOS 现有 Shell 与 Seatbelt 测试不变；`pnpm test:seatbelt-native` |
| 3 | `MxcPolicyCompiler` 与随源码保存的 schema | 跨平台单元测试：映射表每一行、优先级、路径排序与哈希、schema 校验、注入的卷根探测 |
| 4 | `SandboxType.WINDOWS_MXC`、`run_mxc_self_test`、`server.py` 能力判定、`tool_orchestrator.py` 记录 `profileHash`、`hooks.py` 按平台选择、撤掉 `engine.py` 的 Windows 例外 | 用伪 helper 的单元测试覆盖自检判定、状态通道映射和未启动结果 |
| 5 | `shell.py` 接入 `MxcLaunchProfile`（PIPE），请求与状态句柄传递 | Windows 实机：工作区写入成功，工作区外写入、数据目录读取、Git 元数据写入和联网被拒，取消后进程树退出；不可用机器上行为与现在相同 |
| 6 | 同步 `docs/current/`（limitations、architecture、agent-shell）与 `docs/development/README.md` | `git diff --check` |

完成标准：macOS 行为与测试不变；Windows 档位 A 的 Shell 和 Hook 通过真实沙盒测试；不可用时行为不变。本期跨 Sandbox、持久化值和 Tool 链路，按 AGENTS.md 第 31 节属于高风险改动，提交前做独立评审。

### 第二期：本地 MCP、文档 Skill 与提示（约 1 至 2 周）

- [mcp.py](../../runtime/eidos_runtime/extensions/mcp.py) 在 Windows 上为四种档位生成 MXC 请求，启动链为 `mcp_launcher.py` → helper → 服务器；去掉 `mcp_sandbox_unavailable` 早退；
- 移除文档 Skill 在 Windows 上必须完全访问的限制；
- `initialize` 增加不可用原因，Desktop 提示更新 Windows；同步协议 Fixture 和 TypeScript 契约。

完成标准：设置页的工具发现和受限 Run 都能在容器内运行本地 MCP；`browser` 档可以连接 BrowserHost 的 Chrome。

### 第三期：PTY、拒绝诊断与调优

- `tty=true` 经 `ConPtyProcess` 启动 helper（取决于 V7）；
- 用 MXC 的 `captureDenials` 输出补充 Eidos 的沙盒拒绝分类（[denial.py](../../runtime/eidos_runtime/sandbox/denial.py)），只在诊断时开启，绝不使用 `--audit`；
- 按 V13 的结果优化授权路径数量。

## 11. 测试与 CI

- 跨平台单元测试：编译器的标准 JSON 快照、schema 校验、Launcher argv、状态通道映射、自检判定（伪 helper）。
- Windows CI（`windows-latest`，即 Windows Server 2025）：单元测试、helper 构建与哈希校验。若 V10 确认 Server 不支持 PSEC，真实沙盒测试需要一台自托管的 Windows 11 runner；在 runner 就绪前，实机结果按 AGENTS.md 第 26 节记录在 `docs/evaluations/`，不把跳过写成通过。
- macOS：共享的 Shell 启动路径有改动，第一期运行 `pnpm test:seatbelt-native`。

## 12. 风险与回退

| 风险 | 应对 |
|---|---|
| MXC 发布时间短，v1.1.0 已移除回退，SDK 曾整体改名 | 只依赖 V1 稳定契约；固定 git 修订；升级时重跑实机冒烟 |
| 系统更新推送不均，构建号满足但能力缺失 | 以 `probe` 和自检为准，失败即不可用 |
| 宽读组合与 PowerShell 不兼容，或路径过多导致启动慢 | V2、V13 验证；必要时调整卷根展开粒度，但不开放数据目录或保密路径 |
| MSYS2 `sh.exe` 崩溃导致 Git Hook 失败 | V5 确认；写入限制说明；Hook 失败如实返回 |
| 必需遥测无法关闭 | D5：维护者决定前不进入第一期 |
| Windows 安装包目前未签名，helper 可能被智能应用控制拦截 | 签名已决定暂缓，后续单独处理；V10 只记录拦截情况，不阻塞第一期 |
| 局域网与 DNS 语义不同于 macOS | V11 确认后写入限制说明 |

回退：

- 运行时：helper 缺失、哈希不符或自检失败时自动进入不可用状态，行为与今天相同；
- 诊断：环境变量 `EIDOS_WINDOWS_SANDBOX=off` 强制判为不可用（只会让受限 Shell 不可用，不会让命令无沙盒运行）；
- 代码：各 Commit 可单独回退；没有 SQLite 表结构变化；已持久化的 `windows_mxc` 记录在回退后仍需能被读取，回退前确认旧版本的解析行为（第 8 节）。

## 13. 与 V1 的差异

- MXC 发布状态从“早期预览，资料冲突”更新为稳定契约 1.0.0 已发布、最新 v1.1.0。
- 接入方式从“`wxc-exec.exe` 或 `mxc_ffi`”确定为基于 V1 Rust SDK 的自建 helper，`wxc-exec` 只用于第 0 期。
- 读取语义从“待 V2 实测”确定为与 macOS 对齐的宽读加禁止项，并要求 `fsDeny` 能力。
- 支持线从 26100.9278 / 26200.9278 提高到 2026 年 9 月更新（9550 一线）。
- 取消档位 C。
- 新增状态通道、启动闸门复用、遥测退出路径、缺失禁止路径、junction 与硬链接、Server CI 等验证项。

资料来源见 [Agent Harness 调研](../research/windows-sandbox-agent-harnesses.md#6-资料来源)。
