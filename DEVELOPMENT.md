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

仓库不再使用 `pip install -r runtime/requirements.txt`。Electron 首次安装可能需要下载 Electron 官方二进制。

## 3. Run from source

```bash
pnpm start
```

`pnpm start` 会先构建 Preload、Main 和 Renderer，再启动 Electron。源码 Runtime 使用仓库 `runtime/` 和 `.venv/bin/python`。

如果需要指定开发 Python，可以设置绝对路径：

```bash
EIDOS_PYTHON=/absolute/path/to/python3 pnpm start
```

如果需要隔离 SQLite、模型配置和 Runtime-owned 数据，可以设置数据目录：

```bash
EIDOS_DATA_DIR=/private/tmp/eidos-dev-data pnpm start
```

Runtime stdout 只承载 JSON-RPC。Runtime 日志写入启动终端的 stderr。Renderer 不直接读取 Runtime stdout。

## 4. Test

完整 Python 检查：

```bash
pnpm check:python
```

完整项目测试：

```bash
pnpm test
```

完整 Desktop 测试和构建：

```bash
pnpm test:desktop
pnpm build
```

当前关键行为可以先运行 focused tests：

```bash
uv run --locked pytest runtime/tests -k "schema or migration or instruction or context or response or loop or checkpoint or long_task or repository or mcp or telemetry"
node --test scripts/packaging-config.test.mjs scripts/package-macos.test.mjs
```

Repository scale fixture 单独运行：

```bash
uv run --locked pytest -m large_repository runtime/tests/test_repository_large_scale.py
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

输出目录是 `build/macos-runtime/`。Bundle 使用 managed CPython `3.12.13`、locked production dependencies、Eidos Runtime、Seatbelt 资源和受管 Ripgrep。打包 Runtime 不依赖目标机的系统 Python、uv、仓库 `.venv` 或 Xcode Command Line Tools Python。

## 7. DMG packaging

本地未签名 DMG：

```bash
pnpm package:mac
```

该命令要求 Apple Silicon macOS。脚本会执行 locked dependency 安装、packaging config、Python 检查、项目测试、build、native Seatbelt、Electron smoke、Runtime Bundle smoke、Electron Builder 和 packaged App smoke。脚本还会挂载最终 DMG，并验证从 DMG 复制到临时目录的 App。

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

Shell 手工验收应使用可丢弃的 Workspace。当前 Shell 流程如下：

1. Tool 先验证 Workspace identity 和 cwd，并创建 Approval request。
2. Approval 卡展示 command、cwd、timeout、network 和 sandbox permissions。
3. Approval 前不启动 Shell，也不修改 Workspace。
4. 默认 attempt 通过 macOS Seatbelt 启动受控进程。Shell 使用最小环境、有界输出和进程组终止。
5. 命令结束后，Runtime 进行 Workspace manifest observation，并记录 diff、退出状态和 reconciliation 状态。

Shell 启动不要求先完整扫描整个 Workspace。Workspace-wide observation 属于 post-execution evidence。`unknown` observation 不等于 Runtime 已经证明了不确定副作用；Runtime 明确报告的 execution uncertainty 仍必须进入 reconciliation。