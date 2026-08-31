# Eidos 本地开发与验证

本文只描述当前仓库的安装、启动、测试和打包命令。当前行为请阅读 [Current 文档](docs/README.md)、生产代码和自动化测试。

## 1. Prerequisites

产品和原生 Sandbox 验证需要 macOS。DMG 和 bundled Runtime 需要 Apple Silicon macOS。

- Node.js `22.12.0` 或更高版本；
- pnpm `11`；
- Python `3.11` 或 `3.12`；
- uv；
- 原生 Seatbelt 验证需要可用的 `/usr/bin/sandbox-exec`。

可以先检查版本：

```bash
node --version
pnpm --version
python3 --version
uv --version
```

## 2. Install

在仓库根目录执行：

```bash
pnpm install
uv sync --locked
```

`uv sync --locked` 使用提交的 `uv.lock` 创建仓库 `.venv`。源码开发的 Electron Main 默认使用 `.venv/bin/python`。修改 Runtime 生产依赖时，维护者必须同时更新 `pyproject.toml` 和 `uv.lock`。

仓库不再使用 `pip install -r runtime/requirements.txt`。Electron 首次安装可能需要下载 Electron 官方二进制。`pnpm install` 还会构建 `node-pty`，并通过受测的 postinstall 脚本检查 macOS spawn helper 的执行权限。
`package:mac` 默认使用仓库 `.venv/bin/python` 运行 node-gyp。这个设置也覆盖了仓库路径包含空格的情况。需要自定义 Python 时，可以先设置可执行的 `npm_config_python`。

## 3. Run from source

```bash
pnpm start
```

`pnpm start` 会先构建 Preload、Main 和 Renderer，再启动 Electron。源码 Runtime 使用仓库 `runtime/` 和 `.venv/bin/python`。

如果需要指定开发 Python，可以设置绝对路径：

```bash
EIDOS_PYTHON=/absolute/path/to/python3 pnpm start
```

如果需要隔离多库持久化、JSONL、Blob、模型配置和其他 Runtime-owned 数据，可以设置数据目录：

```bash
EIDOS_DATA_DIR=/private/tmp/eidos-dev-data pnpm start
```

Runtime stdout 只承载 JSON-RPC。Runtime 日志写入启动终端的 stderr，也写入数据目录内的有界 JSONL segment。Renderer 不直接读取 Runtime stdout。

## 4. Test

开发过程中优先运行受影响测试：

```bash
pnpm test:affected
```

`test:affected` 根据当前分支相对 `main` 的变化选择已有 focused tests。无法可靠映射时回退到 Fast，而不是直接运行完整项目测试。

快速收尾测试：

```bash
pnpm test:fast
pnpm test:runtime:fast
```

Runtime Fast 排除 `integration`、`slow`、`platform` 和 `large_repository`。Fast 不启动 Electron，不构建 Runtime Bundle，不创建 DMG，也不执行原生 Seatbelt smoke。它用于开发反馈，不代表完整回归。

Runtime 集成层：

```bash
pnpm test:integration
```

完整 Runtime 和完整项目回归：

```bash
pnpm test:runtime:full
pnpm test:full
pnpm test
```

`pnpm test` 保持为 `test:full` 的兼容入口。PR 最终验证、Runtime 核心生命周期、Persistence/Protocol、Tool Runtime、Sandbox、Git/Worktree 或全局测试配置变化时，应执行 Full。普通开发循环不要因为一个失败反复运行 Full。Full 不包含独立调度的 `large_repository` Scale 层。

Repository Scale 单独运行：

```bash
pnpm test:runtime:scale
```

只有 Repository Map/Index/Retrieval 的规模行为、large-repository fixture 本身变化或明确要求规模验收时才需要运行 Scale。

Python 静态检查：

```bash
pnpm check:python
```

如果需要 Python 静态检查和完整 Runtime 测试的一次性入口：

```bash
pnpm check:python:full
```

完整 Desktop 测试和构建：

```bash
pnpm test:desktop
pnpm build
```

修改 Terminal、Main 进程生命周期或原生 Desktop 依赖时，还需要运行：

```bash
pnpm test:packaging
pnpm test:electron-smoke
pnpm package:mac
pnpm test:electron-packaged
```

当前关键行为也可以直接运行 focused tests：

```bash
uv run --locked pytest runtime/tests -k "schema or migration or instruction or context or response or loop or checkpoint or long_task or repository or mcp or telemetry"
pnpm test:packaging
```

`pnpm test:electron-smoke` 会使用临时 `EIDOS_DATA_DIR` 和临时 Electron user-data 目录。该测试不需要真实 Model API Key。

## 5. Native Seatbelt test

```bash
pnpm test:seatbelt-native
```

该命令使用 `.venv/bin/python` 执行 `scripts/test-seatbelt-native.py`。Seatbelt unavailable、原生权限不足或测试跳过都不能当作通过。

## 6. Runtime bundle

macOS arm64 构建机可以生成独立 Runtime Bundle：

```bash
pnpm build:runtime:mac
pnpm test:runtime:bundled
pnpm test:runtime:bundled-seatbelt
```

输出目录是 `build/macos-runtime/`。Bundle 使用 managed CPython `3.12.13`、locked production dependencies、Eidos Runtime、Seatbelt 资源和受管 Ripgrep。locked production dependencies 包含 Workspace artifact 任务使用的 `python-docx`。打包 Runtime 不依赖目标机的系统 Python、uv、仓库 `.venv` 或 Xcode Command Line Tools Python。

## 7. DMG packaging

本地未签名 DMG：

```bash
pnpm package:mac
```

该命令要求 Apple Silicon macOS。普通本地模式会执行 locked dependency 安装、packaging config、Python 检查、完整项目测试、native Seatbelt、Electron smoke、Runtime Bundle smoke、Electron Builder 和 packaged App smoke。脚本还会挂载最终 DMG，并验证从 DMG 复制到临时目录的 App。

CI 如果已经完成 Full、build、Seatbelt、Electron smoke、Runtime Bundle 和 bundled smoke，可以使用：

```bash
EIDOS_PACKAGE_SKIP_TESTS=1 pnpm package:mac
```

该模式复用已经生成并验证的 Runtime Bundle 和 Desktop build，只继续执行 Packaging/DMG 组装和 packaged/copy smoke，避免在同一个 CI job 中再次执行完整验证。Release 模式禁止使用这个变量。

本地 artifact 是：

```text
release/Eidos-<version>-mac-arm64-local.dmg
```

## 8. Release packaging

```bash
pnpm package:mac:release
```

Release 模式在构建开始时要求 Developer ID Application signing credentials 和 Apple notarization credentials。脚本随后启用 hardened runtime、签名、notarization 和 stapling，并执行：

```bash
codesign --verify --deep --strict --verbose=2 Eidos.app
spctl --assess --type execute --verbose=2 Eidos.app
xcrun stapler validate Eidos.app
xcrun stapler validate Eidos-<version>-mac-arm64.dmg
```

脚本支持 electron-builder 使用的 `CSC_LINK`、`CSC_KEY_PASSWORD`、`CSC_NAME`、Apple API Key 组合、Apple ID app-specific password 组合和 keychain profile。证书和 secret 不应写入仓库。

Release 模式拒绝 `EIDOS_PACKAGE_SKIP_TESTS=1`。本地模式只有在维护者明确接受验证缺失时才允许使用该变量，并且脚本会输出 warning。

## 9. Isolated development data

`EIDOS_DATA_DIR` 可以把 SQLite、Runtime lock、reserve file、Extension 数据和其他 Runtime-owned 数据放到独立目录。目录应当是明确的私有临时目录，不要把 Workspace 根目录作为数据目录。

持久化布局包含 `state.sqlite`、`repository.sqlite`、`thread_history.sqlite`、`logs.sqlite`、`memories.sqlite`，以及 `blobs/`、`history/`、`logs/` 和 `memories/`。`state.sqlite` 是唯一业务状态权威。其他数据库保存 projection、可重建索引或文件 metadata。旧 `eidos.db` 会在启动时自动升级。迁移前应退出其他 Eidos Runtime，并保留整个数据目录的备份。

Runtime 创建的 projectless 私有锚点和默认 Managed Worktree 根目录也位于 `EIDOS_DATA_DIR` 内。默认路径分别是 `~/.eidos/.eidos-projectless/<session_id>` 和 `~/.eidos/.eidos-worktrees/<worktree_id>`。

ModelConfigStore 默认使用 `~/.eidos/models.json`。显式数据目录由 Runtime 传给 ModelConfigStore 时，模型配置会跟随该 Runtime-owned 数据位置。模型配置文件保持 owner-only 权限。

API Key 会经过本地模型配置写入链路：Renderer typed IPC → Electron Main → `model/create` / `model/update` JSON-RPC request → Runtime。Runtime 最终把 Key 写入受保护的 `models.json`。Key 不应进入模型列表/读取响应、SQLite、Event/Execution Feed 或正常日志。

## 10. Diagnostics

Runtime 初始化失败时，先检查启动终端中的 stderr 日志和 `runtime/health` 状态。Runtime 会把协议错误映射为稳定 code，不会把第三方 Provider exception 直接发送到 Renderer。

常见检查如下：

- `uv` 或锁文件错误：重新确认 Python 版本在 `3.11` 到 `3.12` 范围内，并运行 `uv sync --locked`；
- Runtime 启动路径错误：源码开发检查 `.venv/bin/python`，也可以设置绝对路径 `EIDOS_PYTHON`；
- Electron binary 缺失：重新运行 `pnpm install`；
- Seatbelt unavailable：在原生 macOS 环境运行 `pnpm test:seatbelt-native`；
- Packaged Runtime 错误：检查 `build/macos-runtime/` 的 bundled Python、`eidos_runtime`、Seatbelt 资源和 Ripgrep manifest；
- Release packaging 错误：检查 signing、notarization、stapling credentials。脚本不会用 unsigned artifact 冒充 Release；
- 非协议 stdout：RuntimeClient 会终止违反 JSON-RPC stdout 契约的 Runtime，并在 Main 侧报告协议失败。

## 11. OpenTelemetry tracing

Runtime 默认初始化 OpenTelemetry SDK，但 `OTEL_TRACES_EXPORTER` 默认值是 `none`，所以默认不会导出 Trace。

在开发环境把 Trace 打到 Runtime stderr：

```bash
OTEL_TRACES_EXPORTER=console pnpm start
```

发送到 OTLP HTTP Trace endpoint：

```bash
OTEL_TRACES_EXPORTER=otlp \
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318/v1/traces \
pnpm start
```

可用环境变量：

- `OTEL_SDK_DISABLED=1`：关闭 OpenTelemetry SDK；
- `OTEL_SERVICE_NAME=<name>`：覆盖默认 `eidos-runtime` 服务名；
- `OTEL_TRACES_EXPORTER=none|console|otlp`：选择 Trace exporter；
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<url>`：设置 OTLP HTTP Trace endpoint。

当前 Trace 主要覆盖 `eidos.run`、`eidos.model.attempt` 和 `eidos.tool.call`。它用于诊断，不是 SQLite 业务事实来源，也不改变 Run、Approval、Tool 或 Reconciliation 行为。

## 12. Shell manual test

Shell 手工验收应使用临时 Workspace。正常 Finder 启动时，Agent Shell 的 `HOME` 应与用户 Terminal 的真实 HOME 一致。测试者不要直接在真实 HOME 创建探针文件。默认 Workspace Shell 不需要 Approval，additional write、network 和 unsandboxed attempt 仍需要 Approval。

先启动应用，并把待测 Session 指向临时 Workspace。然后在 Agent Shell 中执行以下环境检查：

```bash
echo "shell=$SHELL"
echo "home=$HOME"
echo "path=$PATH"
command -v python3
command -v node
command -v npm
command -v pnpm
command -v uv
command -v git
command -v rg
git config --global user.name
```

这些命令应使用 resolved host shell。`HOME`、Host tool 路径和 Git 用户配置应与用户 Terminal 尽量一致。`PATH` 应在末尾提供 bundled `rg`。Shell 不应从 `models.json` 注入 API Key，也不应强制禁用用户 Git 配置。

然后执行文件系统安全验收：

```bash
outside_file="$(mktemp /tmp/eidos-shell-outside.XXXXXX)"
printf 'outside-read\n' > "$outside_file"
cat "$outside_file"
rm -f "$outside_file"

printf 'workspace-write\n' > ./eidos-shell-workspace-probe
cat ./eidos-shell-workspace-probe
rm -f ./eidos-shell-workspace-probe

tmp_file="$TMPDIR/eidos-shell-tmp-$$"
printf 'tmp-write\n' > "$tmp_file"
cat "$tmp_file"
rm -f "$tmp_file"

system_tmp_file="/tmp/eidos-shell-system-tmp-$$"
printf 'system-tmp-write\n' > "$system_tmp_file"
cat "$system_tmp_file"
rm -f "$system_tmp_file"
```

临时文件和 Workspace 文件应写入成功。`outside_file` 应证明 Workspace 外普通文件可读取。`pnpm test:seatbelt-native` 会使用隔离目录验证 HOME 写入被拒绝。如果测试者还要手工验证，测试者应先从 Terminal 在 HOME 下创建专用的可丢弃目录，并在测试后从 Terminal 清理。测试者不应直接对真实 HOME 根目录执行写入探针。

```bash
home_file="$HOME/eidos-shell-disposable-probe/.eidos-shell-home-write-$$"
if printf 'must-be-denied\n' > "$home_file"; then
  rm -f "$home_file"
  echo "unexpected HOME write success"
  exit 1
else
  echo "expected HOME write denial"
fi
```

测试者还应使用已存在的 Eidos data 路径执行 `cat "<EIDOS_DATA_DIR>/models.json" >/dev/null`，并确认读取被拒绝。测试者应在可丢弃的 Git Workspace 中尝试写入 `.git/config`，或在可丢弃的 Non-Git Workspace 中尝试创建 `workspace/.git`，并确认 Git metadata 写入被拒绝。测试者不应修改真实项目的 Git metadata。

网络默认应被拒绝。测试者可以在 Agent Shell 外启动已知的 localhost listener，再在默认 profile 中使用可用的本地客户端连接，并确认连接失败。测试者随后应让 Agent 使用 `networkAccess=request` 和非空 justification 执行同一连接。Desktop 应在进程启动前展示 network-enabled Approval。用户批准后，同一命令应在 expanded macOS Seatbelt profile 中成功，而不是转成 unsandboxed。测试者还应确认用户拒绝时没有 Tool attempt 或进程副作用。旧的 additional network 参数仍可用于兼容性验收。additional write 和 unsandboxed 也必须先经过对应 Approval。Unsandboxed 仍受现有 hard confidentiality deny 约束。明确的 network denial 不得触发 unsandboxed retry。

命令结束后，Runtime 会记录 Workspace manifest observation、diff、退出状态和 reconciliation 状态。Workspace-wide observation 不要求在 Shell 启动前完整扫描 Workspace。`unknown` observation 不等于 Runtime 已经证明了不确定副作用。Runtime 明确报告的 execution uncertainty 仍必须进入 reconciliation。

PTY 和后台进程 follow-up：Agent `run_shell` 不提供 PTY、stdin、interactive session 或 persistent/background process manager。测试者应使用 Desktop Terminal 验证交互式 PTY。测试者还应验证 Agent Shell 会检测并清理 background child。
