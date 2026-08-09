# Eidos 本地开发与阶段验证

本文面向第一次参与桌面端开发的维护者。MVP Lite L0-L3 的代码与离线闭环已经完成：桌面端可以选择 Workspace、持久化 Session/Run/Item/ToolCall、配置多个本地模型、执行只读工具、审批文件修改与 Shell，并展示流式 Feed。最终退出还需要在本机原生环境运行一次完整测试，并由用户在界面输入真实 API Key 完成联网验收。

## 1. 环境要求

- macOS
- Node.js 22.12 或更高版本
- pnpm 11
- Python 3.11 或 3.12
- [uv](https://docs.astral.sh/uv/)

先确认本机环境：

```bash
node --version
pnpm --version
python3 --version
```

如果缺少 Node.js、pnpm 或 Python，可使用 Homebrew 安装：

```bash
brew install node pnpm python uv
```

## 2. 第一次安装

在 Eidos 仓库根目录执行：

```bash
pnpm install
uv sync --locked
```

`uv` 会在仓库根目录创建 `.venv`，Electron 的源码开发路径会刻意使用其中的 `.venv/bin/python`。`uv.lock` 必须提交；修改 Runtime 生产依赖时，先修改 `pyproject.toml`，再有意更新锁文件。`pip install -r runtime/requirements.txt` 已废弃。

Electron 第一次安装或启动时需要从官方源下载对应 macOS 架构的 Chromium 二进制，耗时会明显长于普通前端依赖。后续启动会复用本地缓存。

## 3. macOS Runtime distribution

需要验证独立 Runtime 时，在 arm64 macOS 构建机执行：

```bash
pnpm build:runtime:mac
pnpm test:runtime:bundled
pnpm test:runtime:bundled-seatbelt
```

Builder 使用固定的 uv managed CPython 3.12.13（可通过显式 `EIDOS_PYTHON_VERSION` override），从 `pyproject.toml` 与 `uv.lock` 导出 locked production dependencies，并生成 `build/macos-runtime/`。最终 Bundle 不依赖仓库 `.venv`、目标机 Python、uv 或 Xcode Command Line Tools Python；开发者不需要先构建 Bundle 才能运行 `pnpm start`。当前只构建 `Darwin + arm64`。

## 4. macOS App / DMG packaging

打包构建机必须是 Apple Silicon macOS，并安装 Node.js 22.12+、pnpm 11 和 uv。安装后的 Eidos App 不需要这些工具；Runtime、Python 3.12.13、production Python dependencies 和 Ripgrep 都位于 App 的 `Contents/Resources/runtime/`。

本地打包命令：

```bash
pnpm package:mac
```

`scripts/package-macos.sh local` 会执行 frozen JavaScript/Python dependency install、项目与 Runtime 验证、`build/macos-runtime`、Electron application build、native icon 生成、electron-builder 26.15.3、DMG layout 检查，以及从 DMG 复制到临时目录后的 packaged smoke。输出为：

```text
release/Eidos-<version>-mac-arm64-local.dmg
```

正式发行命令：

```bash
pnpm package:mac:release
```

Release 模式在构建开始阶段要求 Developer ID Application signing credentials 和完整的 Apple notarization credentials；随后启用 hardened runtime、签名、notarization、stapling，并执行：

```bash
codesign --verify --deep --strict --verbose=2 Eidos.app
spctl --assess --type execute --verbose=2 Eidos.app
xcrun stapler validate Eidos.app
```

支持的 credentials 通过 electron-builder 标准环境变量提供，例如 `CSC_LINK` / `CSC_KEY_PASSWORD`、`APPLE_API_KEY` / `APPLE_API_KEY_ID` / `APPLE_API_ISSUER`，或 Apple ID app-specific password 组合。不要把证书或 secret 写入仓库。

开发者只有在有意跳过项目验证时才设置 `EIDOS_PACKAGE_SKIP_TESTS=1`；该 override 会打印 warning，Release 模式拒绝它。旧的 `release/`、`build/macos-runtime/` 和生成的 `packaging/icon.icns` 不提交到 Git。

## 5. 自动化验证

运行全部当前阶段测试：

```bash
pnpm check:python
pnpm test
pnpm build
pnpm test:seatbelt-native
pnpm test:electron-smoke
```

当前 Ruff 只启用语法、未定义名称和未使用导入等正确性规则；格式化、导入排序扩展和类型检查留给后续专门的工程 PR。

预期结果：

- Python 协议测试全部通过。
- macOS Seatbelt 策略与 fail-closed smoke test 全部通过。
- TypeScript Main Client 测试全部通过。
- 测试会真实拉起 Python 子进程，不使用 Runtime mock。
- 非协议 stdout 测试会证明 Main 能终止损坏的 Runtime。
- `protocol/fixtures/v1.json` 固定代表性协议向量：Python 验证初始化/错误 envelope，TypeScript 验证完整向量解析，真实审批/通知由双进程集成测试覆盖；分块超限与慢通知消费者也有回归。
- Session 测试会创建隔离 SQLite，验证 Runtime 重启后的 `session/list|read` 结果。
- Runtime Loop 测试会用确定性 Fake Model 完成 `read_file -> ToolResult -> final answer` 两轮循环。
- OpenAI-compatible Chat Completions Adapter 测试覆盖 SSE 文本、reasoning 隐藏、ToolCall 跨 chunk 归并，以及 DeepSeek、MiniMax、Kimi 的 Provider 构造与 `0600` 私有配置；不会产生真实 API 费用。

### Pydantic Model Conventions

Runtime 模型必须从 `eidos_runtime.models` 选择明确的基础类，而非直接继承 `pydantic.BaseModel`：普通 DTO 使用 `EidosModel`，需要拒绝隐式类型转换时使用 `EidosStrictModel`，不可变配置、快照和值对象使用 `EidosFrozenModel`，同时需要两者时使用 `EidosFrozenStrictModel`。不要把运行中的可变状态改为 Frozen。

`to_internal_dict()` 用 snake_case 为 Runtime 与持久化边界生成 JSON-compatible 数据；`to_wire_dict()` 用 camelCase 为 JSON-RPC、Renderer 和外部 JSON 边界生成 JSON-compatible 数据。两者默认保留 `None`，只有调用者显式传入 `exclude_none=True` 才删除。

跨 JSON-RPC 或 Renderer 的整数只有在 JavaScript 安全范围已是既有契约时才使用 `JsonSafeInt`；不要批量替换普通 `int`。基础类集中保持 alias、未知字段和默认值验证规则，避免各领域模型重复或悄然偏离这些协议边界。

验证桌面端可以完整构建：

```bash
pnpm build
```

预期结果：TypeScript 类型检查通过，Vite 在 `dist/renderer/` 生成 Renderer 资源。

### Repository、Context 与长任务 focused tests

Phase E-F 的新基础设施使用当前锁定依赖：Tree-sitter Python/TypeScript/JavaScript/Go
grammars、`charset-normalizer`、`watchfiles` 和 RapidFuzz；SQLite FTS5 按持久化
Index Snapshot generation 查询。扫描、索引、Watcher、检索、ContextPlan、压缩验证和长任务状态都必须
在完整快照上工作，Watcher 只发失效信号，取消时保留上一个完整 generation。

可先运行定向门槛：

```bash
uv run --locked pytest runtime/tests -k "repository or inventory or watcher or index or retrieval or context or compaction or pause or resume or restart or cancel"
```

`LongTaskRepository` 将进度写入现有 `operations` 表的
`scope=long_task/control`；authoritative baseline 是 schema v10，v9 启动时执行原子
migration。暂停/恢复通过 typed JSON-RPC 和 RunSupervisor 接入。恢复前必须重新核验 Workspace、Git、规则、索引、
Context Plan、permission snapshot 和 side-effect reconciliation；不确定副作用不自动重放。

当前仍未完成的接线路径见 [当前限制](docs/current-limitations.md)：Repository/Context
默认在线组装、穷尽式 Restart Verification、Checkpoint rewind/fork 的完整 Context/
Git Worktree 重建，以及兼容 compactor 自动切换，不应在手工验收中被误认为已完成。

100,000-entry fixture 独立运行，不进入默认快测：

```bash
uv run --locked pytest -m large_repository runtime/tests/test_repository_large_scale.py
```

## 6. 手动界面验证

启动应用：

```bash
pnpm start
```

如需做不会触碰真实 `~/.eidos` 的开发验收，可使用隔离数据目录：

```bash
EIDOS_DATA_DIR=/private/tmp/eidos-smoke-data EIDOS_FAKE_MODEL=1 pnpm start
```

窗口中应依次看到：

1. `正在完成 Python Runtime 协议握手…`
2. 左侧 Session 列表与右侧 Eidos Workspace。
3. 尚未配置模型时 Composer 禁用发送，并显示进入“模型”设置的引导。

在“模型”设置中选择 DeepSeek、MiniMax 或 Kimi 的内置模型并输入 API Key。模型 ID、URL 和能力标记由内置目录填写，用户不能编辑；配置以 JSON 数组写入 `~/.eidos/models.json`，文件权限应为 `0600`。编辑模型时 API Key 留空会保持原值；界面、Runtime stdout、SQLite 和日志都不应回显 Key。曾经发到聊天或其他第三方系统的 Key 建议先轮换，再作为长期配置使用。

添加至少两个模型后，在同一 Session 完成一个 Turn，切换 Composer 模型再启动下一个 Turn；两个 Run 应分别记录所选 `modelId`。活动 Run 期间选择器必须禁用，删除当前选中模型后应回退到配置列表第一个模型。重启 Eidos 后设置页和 Composer 应从同一 `models.json` 恢复。

然后按以下步骤验收 L1：

1. 点击“新建 Session”，选择一个不含敏感数据的测试项目。
2. 输入“请先列出文件，读取 README.md，然后概括如何启动这个项目”，按 `Enter`。
3. Feed 应依次出现用户消息、`list_files`/`read_file` 工具项、模型可见文本和最终回答。
4. 退出并重新启动 Eidos，左侧应保留 Session；打开后应看到已完成的 Run 与 Item。
5. 再发起一个任务并立即点击“取消 Run”，状态应停止，不能在稍后被迟到模型结果改写为成功。

验证文件审批时，请使用一个可丢弃的测试 Workspace：

1. 让 Eidos “读取 README.md，然后只在末尾追加一行 `Eidos approval test`”。
2. Feed 必须先展示完整 diff；此时磁盘文件不能变化。
3. 点击“拒绝”，确认文件零变化。
4. 再次发起同样任务并点击“批准并写入”，确认文件与 diff 一致。
5. 版本冲突验证：审批卡出现后，先在编辑器手动修改该文件，再点击批准；Eidos 必须返回 `file_version_conflict`，保留你的手动修改。

文件工具只有在单次 diff 审批后才会修改 Workspace。Shell 也只在每次审批后运行；审批卡必须完整展示 command、cwd、`network: disabled` 和 timeout。可用“运行 `python3 -m unittest`”验证：拒绝时不执行；批准后 Feed 应流式显示输出，命令只能写 Workspace（`.git` 与敏感路径除外），不能访问网络或继承宿主 API Key/HOME。

Shell 启动前会有界扫描 Workspace：常见凭证文件、特殊文件或多硬链接文件会使本次命令 fail closed。MVP Lite 只承诺同一进程组的清理；刻意使用 `setsid`/double-fork 创建守护进程不受支持，因此只应批准短生命周期的构建、测试和检查命令。

启动应用的终端中应出现类似日志：

```text
[runtime] ... INFO eidos.runtime Runtime initialized
```

在支持的 macOS 环境还应先看到：

```text
[runtime] ... INFO eidos.runtime Seatbelt self-test passed
```

日志应只出现在终端，不应出现在协议 stdout 或界面正文中。

## 7. 关闭验证

使用 `Command + Q` 退出 Eidos。终端应结束 Electron 进程，Python Runtime 不应残留。

可选检查：

```bash
pgrep -af eidos_runtime
```

没有输出表示 Runtime 已随桌面端退出。

## 8. 常见失败

### 窗口显示 Runtime 启动失败

先确认：

```bash
uv --version
pnpm test:runtime
```

如果系统 Python 不在默认路径，可显式指定：

```bash
EIDOS_PYTHON=/绝对路径/python3 pnpm start
```

### Electron binary is not installed

重新执行 `pnpm install`，并等待 Electron 官方二进制下载完成。不要从无法核验来源的镜像下载并直接执行二进制。

### 自动测试通过但界面未就绪

保留启动终端中的 `[runtime]` 日志与窗口错误文案。L0 的错误状态不会自动降级或跳过 Runtime，可以直接根据这两处信息定位。
