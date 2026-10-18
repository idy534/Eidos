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

开发者可以在 `pnpm build` 后运行 `pnpm test:renderer-startup`。该定向检查使用临时 user-data，加载生产 HTML、CSP、Renderer bundle 和 sandboxed preload，并确认 React 启动页已经挂载。该检查只模拟初始 Runtime 状态，不启动 Python Runtime，也不读取真实 Session。定时任务契约更新后，开发者需要运行 `node scripts/generate-automation-contracts.mjs`，同步 DTO、Schema 和 Ajv standalone 静态校验器。

如果需要指定开发 Python，可以设置绝对路径：

```bash
EIDOS_PYTHON=/absolute/path/to/python3 pnpm start
```

如果需要隔离多库持久化、JSONL、Blob、模型配置和其他 Runtime-owned 数据，可以设置数据目录：

```bash
EIDOS_DATA_DIR=/private/tmp/eidos-dev-data pnpm start
```

Projectless 工作空间默认位于 `~/Eidos/YYYY-MM-DD/可读名称`。`EIDOS_DATA_DIR` 不控制这个位置。开发者需要隔离工作空间时，应同时设置：

```bash
EIDOS_DATA_DIR=/private/tmp/eidos-dev-data EIDOS_PROJECTLESS_ROOT=/private/tmp/eidos-dev-workspaces pnpm start
```

Schema v23 是不兼容旧 Projectless Session 的 Alpha 更新。新版 Runtime 启动时会废弃旧数据目录内的无项目 Session 和关联历史，旧工作空间文件原地保留。该更新不会迁移目录，也不会清理有项目的 Session。Runtime 定向测试通过自动 fixture 隔离 Projectless 根目录；其他测试进程需要显式设置 `EIDOS_PROJECTLESS_ROOT`，不能向真实 HOME 写入。

Runtime 不设置跨 Session 的 Run 或驻留 Worker 总量上限，也不使用工作区共享副作用门控。`EIDOS_MAX_ACTIVE_RUNS` 与 `EIDOS_MAX_RUN_WORKERS` 不再作为配置读取。每个父 Run 最多同时执行 8 个子 Agent Run，名额不与其他父 Run 共享；这不是单 Run 内工具线程池大小。同一 Session 仍按 FIFO 顺序执行，父任务仍可主动等待子任务结果。同目录并发修改可能冲突，现有文件版本核验、权限、取消和副作用对账继续生效；需要修改隔离时使用独立 Worktree。

Runtime stdout 只承载 JSON-RPC。Runtime 日志写入启动终端的 stderr，也写入数据目录内的有界 JSONL segment。Renderer 不直接读取 Runtime stdout。

MCP Connection 使用标准库 `tempfile.mkdtemp()` 在 macOS 的 canonical `/private/tmp` 下创建 `eidos-mcp-<随机值>/`，不再使用数据目录下的长运行路径，也不从主进程继承临时目录根路径。连接的默认 HOME、TMPDIR 和 PID 文件分别位于短目录内的 `home/`、`tmp/` 和 `server.pid`。服务显式配置或透传的环境变量仍然优先。连接在进程组、异步 Service 和 callback 停止后核对目录身份并清理自身目录；系统临时目录和其他连接不受清理影响。

创建或编辑手动 MCP 时，工作目录留空会创建并使用 `~/Eidos/eidos-mcp/<serverId>/`，初始权限为 `0700`。服务 ID 对应界面的服务名称，例如 `playwright/`。默认路径拒绝符号链接目录。填写的工作目录仍须已经存在，且继续使用原有权限校验。保存后的 cwd 会持久化，已有配置不自动迁移；编辑时清空工作目录即可选择新默认值。cwd 控制服务的相对参数和相对产物位置，但不会隐式改变 HOME，也不会增加沙盒写权限。MCP 工具描述继续保留服务原文，不追加输出目录提示，不改写工具文件名参数。

Runtime 启动日志包含 `Runtime startup phase=... duration_ms=... outcome=...`，以及 `Runtime initialized duration_ms=...`。存储阶段是 `storage` 的子阶段，开发者不能把父阶段与子阶段耗时相加。初始化总计时不包含 Python 模块导入、Desktop 构建和 Renderer 首屏请求。启动不执行 Blob 全量清理和仓库库全库扫描；现有受管后台任务会在任务创建至少 60 秒后且 Runtime 空闲时执行仓库库完整性检查、有界空闲页回收和 Blob 清理，之后每小时检查一次。Session 删除和隐私撤销仍保留即时清理。

## 4. Test

定时任务的 Runtime 定向入口是 `uv run --locked pytest runtime/tests/test_automations.py`。Desktop 在 `pnpm build:main` 后运行 `node --test dist/main/runtime-client-automations.test.js` 验证真实跨进程链路。页面入口是 `pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/components/automations/AutomationPage.behavior.test.tsx`。`node_modules/.bin/electron scripts/test-automations.cjs` 使用临时 user-data 和隔离页面验证生产组件、键盘与四种窗口宽度，不读取真实任务。EIDOS_AUTOMATION_SCREENSHOT 可以指定截图位置。

Runtime 随现有启动运行受管调度服务，并随 Supervisor 关闭，无需系统 cron、launchd 或额外守护进程。v27→v28 升级新增任务、触发和来源表。croniter 与 tzdata 已进入 pyproject.toml 和 uv.lock，打包沿用现有流程。

Agent 在 Eidos Shell 内执行本仓库测试时，应先核对实际执行权限。部分集成测试需要启动原生 Seatbelt，外层沙箱可能阻止嵌套启动。测试者不能仅凭 `sandbox_unavailable` 排除代码回归。需要原生执行时，Agent 应使用系统提供的明确审批入口；文件路径授权不等于 unsandboxed 授权。

uv 缓存写入失败时，Agent 应选择已有可写缓存或临时目录，或申请具体路径权限。Agent 不应默认放开整个 HOME。缓存清理不能解除未知 Shell Intent。测试报告应记录退出码、实际完成的阶段和最终汇总；输出不完整时，测试者必须把总数和未核验结论标为未验证。

Agent Shell 的 zsh/bash 默认开启 `pipefail`。验证命令优先直接输出到工具的有界捕获，不要用 `head` 提前关闭管道。多个有依赖关系的阶段使用 `&&`；分号后最后一条命令的成功不能证明前面阶段通过。POSIX sh 保持原来的退出码行为。

开发过程中，执行者优先运行与改动行为直接相关的 focused tests。执行者只有在确认 `test:affected` 的映射范围合适时才运行该入口：

```bash
pnpm test:affected
```

`test:affected` 根据当前分支相对 `main` 的变化运行配置好的测试命令。该入口不保证只运行定向测试。当前部分 Desktop 路径会映射到完整的 `pnpm test:main` 或 `pnpm test:desktop`；未映射的 Runtime 生产路径会回退到 `pnpm test:runtime:fast`；其他未映射路径可能会回退到 `pnpm test:fast`。执行者应先查看 `scripts/test-affected.mjs` 中适用于当前路径的映射。若映射会运行完整层级或超出改动范围的 Fast 套件，执行者应直接运行更窄的测试。执行者只有在没有合适的定向入口时才考虑 Fast。Fast 不是每项任务的默认要求。

需要较宽反馈时可以运行 Fast：

```bash
pnpm test:fast
pnpm test:runtime:fast
```

Runtime Fast 排除 `integration`、`slow`、`platform` 和 `large_repository`。Fast 不启动 Electron，不构建 Runtime Bundle，不创建 DMG，也不执行原生 Seatbelt smoke。它用于开发反馈，不代表完整回归。

Runtime 集成层：

```bash
pnpm test:integration
```

以下命令会运行完整 Runtime 或完整项目回归：

```bash
pnpm test:runtime:full
pnpm test:full
pnpm test
```

`pnpm test` 保持为 `test:full` 的兼容入口。`pnpm check:python:full` 会运行 Runtime Full 和静态检查。用户明确要求 Full Suite 或要求执行完整套件命令时，执行者才运行这些完整套件。核心生命周期、Persistence/Protocol、Tool Runtime、Sandbox、Git/Worktree 或全局测试配置变化本身不触发 Full；执行者按影响范围运行定向测试和必要的集成测试。普通 Renderer 修改只需要相关 Desktop 定向验证。执行者不要在普通开发循环中因为一个失败反复运行 Full。Full 不包含独立调度的 `large_repository` Scale 层。

Repository Scale 单独运行：

```bash
pnpm test:runtime:scale
```

只有 Repository Map/Index/Retrieval 的规模行为、large-repository fixture 本身变化或明确要求规模验收时才需要运行 Scale。

Python 静态检查：

```bash
pnpm check:python
```

用户明确要求 Runtime Full，且需要同时运行 Python 静态检查时，可以使用以下入口：

```bash
pnpm check:python:full
```

完整 Desktop 测试和构建入口：

```bash
pnpm test:desktop
pnpm build
```

`pnpm test:desktop` 和 `pnpm test:main` 会运行完整 Desktop 或 Main 测试层。用户明确要求 Full Suite 或要求执行完整套件命令时，执行者才运行完整层级。普通 Desktop 修改优先运行相关测试文件；没有合适的定向入口时，执行者再运行 Fast。

`pnpm test:composer-layout` 使用真实 Electron、隔离的临时 user-data 和本地 Vite 测试页面。该定向测试检查输入框自动高度和历史记录滚动位置，不启动 Runtime，也不读取真实 Session 数据。测试记录包含代码块和图片，预览上下文随草稿编辑更新。测试覆盖单行输入、全选删除、等高编辑、多行删除、组合输入事件、窄窗口换行、窗口宽度变化和输入框内部滚动。该入口不属于完整测试套件。

`node_modules/.bin/electron scripts/test-feed-scroll.cjs` 使用隔离的真实 Electron 页面验证 Session 滚动。该定向测试覆盖 320、768、1024、1440 四种宽度，检查空列表首次加载、处理过程和嵌套工具展开、动画期间持续输出、历史阅读、折叠后的暂停状态、向下滚动、到底按钮、延迟图片和 Run 结束自动折叠。测试不启动 Runtime，不读取真实 Session，也不调用模型。定向行为测试入口是 `pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/components/useFeedScroll.behavior.test.tsx desktop/renderer/src/components/ExecutionFeed.behavior.test.tsx`。

`node_modules/.bin/electron scripts/test-queued-messages.cjs` 使用真实 Electron 和隔离页面检查输入框上方的消息队列。该定向测试覆盖 1100px、600px、420px 下的对齐、长消息、队列内部滚动、编辑、删除和主草稿保留，不读取真实 Session。`EIDOS_QUEUE_SCREENSHOT_DIR` 可以指定截图目录。Runtime 定向入口是 `uv run --locked pytest runtime/tests/test_queued_runs.py`；Main → Runtime 验证在 `pnpm build:main` 后运行 `node --test dist/main/runtime-client-queued-runs.test.js`。这项跨进程测试使用临时工作区和测试模型，原生环境需要可用的 Seatbelt，测试不会放行等待中的网络 Shell。

`pnpm test:sidebar-pinning` 使用真实 Electron、隔离的临时 user-data 和生产侧栏组件。该定向测试检查项目右键置顶、Session 的 Shift+F10 置顶、混合显示、无 Run 分叉的即时显示和悬停图标、分叉气泡的来源名称与点击跳转、取消置顶、唯一高亮、状态图标、项目浏览时的滚动位置，以及 1100px 和 420px 窗口中的菜单边界。测试页沿用禁止 JavaScript eval 的 CSP。该页面使用测试 API，不启动 Runtime，也不读取真实 Project 或 Session。Main → Runtime 持久化验证使用 `pnpm build:main` 后的 `node --test dist/main/runtime-client-sidebar.test.js`。开发者可以通过 `EIDOS_SIDEBAR_SCREENSHOT` 指定置顶截图输出路径，也可以通过 `EIDOS_FORK_HOVER_SCREENSHOT` 指定分叉来源气泡截图路径。这两个入口不属于完整测试套件。

`pnpm test:renderer-styles` 使用真实 Electron 和隔离页面加载 Renderer 的统一样式入口。该定向测试检查公共按钮与审批、计划按钮的默认、悬停、按下、禁用和键盘焦点状态，检查列表选中项的悬停颜色与菜单外观，并覆盖 1100px、420px 下的侧栏和 Dock 组合。测试还检查聊天、代码和非聊天正文的字号，以及 Composer 的高度范围。测试不启动 Runtime，也不读取真实 Session 数据。开发者可以通过 `EIDOS_STYLE_BASELINE_CSS` 指定改动前的构建 CSS 文件，让测试对照正文排版和布局；该路径只用于本次测试。该入口不属于完整测试套件。

滚动条检查覆盖 13 处滚动区域。测试读取最终计算样式，并比较原生截图中的横向轨道、纵向轨道与角落像素，确认这些部位直接显示容器底色。测试也检查滚动条没有边框、阴影，滑块颜色变量有效，公共尺寸保持为 6px，以及代码块与 Diff 视图在静止时隐藏滑块、悬停时显示滑块并绘制可见像素。

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

输出目录是 `build/macos-runtime/`。Bundle 使用 managed CPython `3.12.13`、locked production dependencies、Eidos Runtime、Seatbelt 资源和受管 Ripgrep。locked production dependencies 包含 Workspace artifact 任务使用的 `python-docx` 和 `lark`。打包 Runtime 会复制 `eidos_runtime/workspace/apply_patch.lark`，并检查这个资源存在。打包 Runtime 不依赖目标机的系统 Python、uv、仓库 `.venv` 或 Xcode Command Line Tools Python。

`pnpm test:runtime:bundled` 会在 Bundle 内导入 `lark`，检查 grammar 文件来自 Bundle，并同时验证结构化 `ApplyPatchInput → CodexPatchEncoder → Lark parser` compatibility 链路和 raw Custom `Codex Patch → parse_patch → verified Workspace action` 链路。这个 smoke 不执行真实 Provider 请求，也不代表 Provider capability 或 wire schema 已经验证。Responses native wire contract 由 Runtime mocked provider tests 验证。

Runtime dependency contract 使用 Bundle 根目录下的 `runtime.json`。文件固定使用 `schemaVersion: 1` 和 `target: darwin-arm64`。文件记录 Bundle ID、固定版本、可执行文件、Python 包、Node 包和相对路径。文件和可执行文件记录 SHA-256。构建和发布流程通过 `scripts/generate-runtime-manifest.mjs` 执行完整文件 Hash 与 inventory 覆盖校验。Python Runtime 不保留这套全量检查及其模式开关。日常 Runtime 初始化读取清单，检查格式、版本、包 metadata 和必要路径；它不计算依赖文件 Hash，也不递归扫描 Bundle。Runtime 将 Catalog 传给每个 Run，并在 Shell 启动前复查清单与必要执行路径。Runtime 读取后会固定 manifest hash 和 Catalog snapshot hash；后者标识构建声明及路径，不证明当前文件内容与发布时一致。Run binding 还会固定 requirement hash 和 `dependencyBindingId`。

资源分为三层。Skill 内容位于 `runtime/eidos_runtime/resources/skills/`，包括 `SKILL.md`、`agents/eidos.yaml`、脚本和其他资源。产品依赖位于 `resources/runtime-dependencies/`，构建后进入 Bundle 的 `dependencies/` 目录。Bundle 根目录的 `runtime.json` 是由构建脚本生成并校验的依赖清单。系统 Python、Node 和包文件不放进 Skill 目录。`agents/eidos.yaml` 只声明 typed requirements。Skill 不能安装包，也不能写入受保护的 Bundle 或 Skill root。默认 Agent Shell 仍然使用 Workspace cwd、默认禁网和现有 Approval/Sandbox 流程。没有 dependency binding 的普通 Shell 行为不变。

`run_shell` 会为已识别的 Skill 脚本自动选择该 Skill 声明的已验证依赖；模型无需查询或传入 binding ID。普通项目命令不会因为 Skill 已激活而切换环境。`dependencyBindingId` 输入仍保留兼容，显式值仍校验 Run 和 Skill 归属。plugin-creator 等 Skill 脚本使用 `$RUNTIME_PYTHON`、catalog 提供的 canonical absolute Skill root 和 Workspace cwd `.`。源码环境中的 `.venv` 只能用于源码开发。它不代表目标 App 的 bundled dependency。

构建和签名的顺序必须保持：先复制并校验 Bundle 文件，再生成并校验 `runtime.json`；然后运行 bundled smoke 和 Seatbelt smoke；再把 Bundle 放进 App resources；最后执行签名、notarization、stapling 和签名后校验。签名会改变嵌套文件，所以签名后的 manifest refresh 和 hash 校验必须在最终 `codesign` 校验前完成。没有 Apple signing 或 notarization credentials 时，不能把真实签名结果写成已验证。

Runtime dependency focused tests 可以使用以下入口。命令是否通过要以当前实现为准：

```bash
uv run --locked pytest runtime/tests/test_skill_runtime_requirements.py runtime/tests/test_skill_manifest.py runtime/tests/test_system_skills.py
node --test runtime/tests/test_dependency_runtime_loader.mjs scripts/runtime-dependency-packaging.test.mjs
```

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

Schema v22 在 ModelAttempt 上新增内部思考回放 Blob 引用。v21 数据库会通过事务新增 nullable 列；迁移不会改写历史 Item 或补造思考正文。Blob 清理保留这些 Blob。思考回放验证应覆盖多厂商 mocked wire、同一响应中的正文和多工具调用、重启、取消后的迟到结果、旧会话缺少思考、上下文部分保留、升级失败回滚和 Session Feed 隔离。

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

启用 OTLP 且没有显式参数时，Runtime 使用 3 秒 exporter timeout 和 30 秒 batch schedule delay，主动 flush 最多等待 5 秒。维护者可以使用标准 `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT`、`OTEL_EXPORTER_OTLP_TIMEOUT` 和 `OTEL_BSP_SCHEDULE_DELAY` 覆盖导出参数。持续导出失败需要排查 collector；本地执行和持久化不依赖 collector 可用。

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

Trace 包括入口 `eidos.rpc`、`eidos.run`、`eidos.model.attempt` 和 `eidos.tool.call`。配置 exporter 后，Pydantic AI Direct Model API 还会产生原生模型 Span，且关闭消息内容、二进制内容和 Tool Schema 采集；OpenAI Responses 自定义请求仍由 Eidos 的 Attempt Span 表示。子 Agent 在同一 Runtime 进程内继承 spawn/followup 时的 Trace 上下文；暂停后恢复的 Run 沿用进程内保存的上下文；本地 JSONL 日志在活跃 Span 内带 `traceId`、`spanId`。可以把 OTLP HTTP endpoint 指向本机 Phoenix 等支持 OTLP 的查看器；查看器单独启动，不参与 Eidos 的运行和打包。Trace 用于诊断，不是 SQLite 业务事实来源，也不改变 Run、Approval、Tool 或 Reconciliation 行为。

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

网络默认应被拒绝。测试者可以在 Agent Shell 外启动已知的 localhost listener，再在默认 profile 中使用可用的本地客户端连接，并确认连接失败。测试者随后应让 Agent 使用 `networkAccess=request` 和非空 justification 执行同一连接。Desktop 应在进程启动前展示 network-enabled Approval。用户批准后，同一命令应在 expanded macOS Seatbelt profile 中成功，而不是转成 unsandboxed。测试者还应确认用户拒绝时没有 Tool attempt 或进程副作用。旧的 additional network 参数仍可用于兼容性验收。additional write 和 unsandboxed 也必须先经过对应 Approval。Unsandboxed 需要高危人工确认，窗口说明本次命令及其子进程会失去原有路径和网络保护。明确的 network denial 不得触发 unsandboxed retry。

命令结束后，Runtime 会记录 Workspace manifest observation、diff、退出状态和 reconciliation 状态。Workspace-wide observation 不要求在 Shell 启动前完整扫描 Workspace。`unknown` observation 不等于 Runtime 已经证明了不确定副作用。Runtime 明确报告的 execution uncertainty 仍必须进入 reconciliation。

PTY 和后台进程 follow-up：Agent Shell 支持同一 Run 内的管道 stdin 和分段等待，但不提供 PTY，也不跨 Run 或 Runtime 重启恢复进程。测试者应使用 Desktop Terminal 验证交互式 PTY。测试者还应验证 Agent Shell 会检测并清理 background child。

新 Run 的 Shell 不再设置默认进程总期限。`run_shell` 首次等待默认 10 秒，范围 250 毫秒至 30 秒；模型随后使用 `write_stdin` 等待，默认 30 秒，范围 250 毫秒至 60 秒。等待窗口到期只返回 `shell_running` 和 `sessionId`，不会结束进程或触发 reconciliation。ToolSpec 的 600 秒 watchdog 只限制单次启动、审批重试或跟进调用，审批等待不计入预算。历史 3600 秒 ToolSpec 仍可读取，但新工具不会用它限制进程寿命。


### Shell 分段等待验收

测试者应覆盖短命令、超过首次窗口的静默命令、持续输出、空输入轮询、stdin、Ctrl-C 及忽略中断的进程组清理。测试者应检查命令运行期间的排他副作用限制、只读工具可用、有效轮询不会触发 LoopGuard，以及退出结果在模型没有继续轮询时也会持久化。测试者应检查原命令卡片持续更新且没有 `write_stdin` 名称或正常轮询卡片。测试者还应覆盖审批重试、跨 Run session 拒绝、跨输出片段敏感扫描、取消竞争、存储失败和 Runtime 重启后的不确定 Intent。

## 文件编辑提交验证

已有文件使用原地写入，新文件使用排他创建。相关定向用例位于 `runtime/tests/test_file_commit_helper.py`、`runtime/tests/test_file_write_approval.py`、`runtime/tests/test_runtime_loop.py` 和 `runtime/tests/test_seatbelt.py`。macOS 验收必须覆盖 inode 不变、ACL 拒绝、写前版本冲突、部分写入、取消、外部路径拒批/批准和无沙盒审批。Runtime Bundle 必须重新构建并执行 bundled smoke；源码检查不能代表已安装 App 的行为。

Projectless 文件提交和 Skill 写入权限的定向回归位于 `runtime/tests/test_file_commit_helper.py`、`runtime/tests/test_file_write_approval.py`、`runtime/tests/test_permission_policy.py` 和 `runtime/tests/test_seatbelt.py`。验证者需要执行真实 macOS Seatbelt 用例，覆盖受保护数据目录内的 Projectless 新建、更新、删除，普通 Skill 的精确写入授权，以及系统 Skill 的高危批准与拒绝、相邻数据文件的拒写；mock 通过不能替代这些原生结果。


## 权限模式验收

相关修改需要覆盖三种模式的协议和 UI、旧请求默认值、无确认的完全访问拒绝、Run 模式不可变、v12 → v13 升级与事务回滚。审批测试需要覆盖所有原有入口、模型批准与拒绝、无效输出、未知风险、输出和证据超限、60 秒超时、取消与迟到结果、重启中断、拒绝去重及人工模式回归。测试还需要证明普通 Workspace 操作不产生额外审查请求，明确风险拒绝不会触发人工窗口；不可用、超时和未知风险会转人工接管。

macOS 原生验收需要覆盖完全访问的网络、外部 cwd、外部普通文件、Git metadata 和原 Eidos 保护路径。测试只能在隔离数据目录和临时文件上验证这些写入，不能破坏真实 Eidos 数据。验收还需要检查 manual/auto_review 的原有 Seatbelt deny、文件身份与版本冲突、Durable Intent、部分写入、取消、MCP 生命周期和 Reconciliation。真实 Provider 的结构化审查与拒绝理由需要单独验证。

测试阶段沿用本文现有命令和 AGENTS.md 的门槛：协议、Runtime、Main、Desktop、Python 依赖检查、构建、Seatbelt native 和 Electron smoke。测试阶段还需要同步受影响的协议 Fixture 与旧 Schema 断言。当前代码没有增加生产依赖，也没有改变安装或启动命令。

## 输入引用验收

相关修改需要覆盖 `input/*` 与 Run 引用的协议 Fixture、v13 → v14 迁移及回滚、旧数据库升级、引用 Blob GC、512 MiB 总量限制、草稿恢复和写入失败、跨 Session 迟到结果，以及提交后继续输入时的草稿保留。测试还需要覆盖不支持图片的模型、图片编码和预算、历史引用截断、扩展失效、编辑重发、重新生成、Fork 与压缩后的来源保留。

文件边界测试需要覆盖文件替换竞争、符号链接、硬链接、特殊文件、超限文件、Projectless 与 Managed Worktree，以及 Eidos 私有数据目录的拒绝读取。权限回归需要证明引用不会产生额外写权限，目录和位置引用不会被当作已读取正文。

Desktop 验收需要覆盖 `+`、`@`、`$`、`/`、中文输入法、键盘焦点、原生右键菜单、Finder 多文件拖放、剪贴板图片、批量部分失败、引用预览与移除、行范围和历史消息选择，以及 Run 执行时修改下一轮草稿。macOS 真机行为不能由 Renderer mock 代替。

测试阶段沿用本文和 AGENTS.md 的现有协议、Runtime、Main、Desktop、构建、Seatbelt native 和 Electron smoke 门槛。开发者还需要同步旧 Schema 版本断言和现有调用 Fixture。本批没有增加生产依赖，也没有改变安装与启动命令。

## 执行门控与 CI 验证

运行、审批和收尾期间可提交下一轮请求，由同 Session FIFO 队列执行。模型、推理、权限和 Plan 设置属于下一轮草稿；当前 Run 保留创建时的固定配置。取消与排队提交可同时使用。普通 Execute Run 也能请求澄清，计划写入和确认仍属于 Plan 模式。

子任务支持 `default`、`explorer`、`worker`，省略角色时使用 `default`。三个角色继承父 Run 的普通工具和权限，`explorer` 能执行 Shell，不提供强制只读保证。自定义类型使用数据目录下的 `agents/*.toml`，其中 `instructions` 保存指令，模型选择包含 `provider_id` 和 `config_id`。Schema 20→21 迁移只扩展角色 CHECK，保留历史委派、消息与等待。定向验证需要覆盖三种角色在三种审批模式下执行真实 Shell、续派保持角色、取消、等待恢复、迁移失败回滚，以及 Desktop 角色与审批显示。

子任务默认是必要依赖；明确设置 `requiredForCompletion=false` 的补充任务在父任务完成时取消，并等待进程和资源清理。必要依赖仍等待真实结果。协作消息优先投递最早未读的 16 条；只有已完成模型请求的固定上下文会建立投递回执，失败、重启和上下文预览不消费消息。Schema 17 事务迁移增加投递回执、Skill 使用租约及必要依赖标记，Schema 16 及更旧数据可升级。

新 Run 持久固定 Skill 元数据，实际读取或激活时原子取得租约并核验源版本。删除未使用的 Skill 不阻塞无关 Run；实际使用者或无法证明引用范围的旧快照继续保护目录。删除后不能新取得租约，已有租约在 Run 终态后不再阻塞清理。

Workspace 恢复扫描只在受影响目标或需要解除屏障的新副作用上进行，不在普通读取、协作消息或无关文件操作前强制扫描。单批次的连续安全只读片段可以并行，片段之间按模型声明顺序执行。搜索保留 4 个运行名额，额外请求进入该执行器的有界队列，不新增排队线程。

CI 将质量、依赖审计、Runtime、Desktop 和原生打包拆为独立 Job。PR 按实际路径选择 Runtime、Desktop 和 native；main push 执行所有边界。静态和审计检查保持独立，不因审计失败跳过回归。汇总 `test` Job 要求所有已选检查成功，无法判定改动范围时不跳过验证。原生 Job 只在 Desktop build、Seatbelt、Electron 和 Bundle 验证通过后复用本 Job 的产物组装 DMG；其他 Job 的测试结果仍由汇总门禁判定。

## 记忆系统开发与验收

契约修改后运行 `node scripts/generate-memory-contracts.mjs` 并提交三份 `desktop/shared/memory*.generated.ts`。定向验证：

```bash
uv run --locked pytest runtime/tests/test_memory.py runtime/tests/test_memory_jobs.py runtime/tests/test_memory_runtime.py runtime/tests/test_memory_fact_review.py
pnpm test:desktop
```

人工验收使用新的数据目录和已配置模型：

1. 设置 → 记忆：默认使用开启、生成关闭；保存中文偏好，在另一会话检索、读取并跳转来源。
2. 确认自动记忆，在当前任务表达一次明确的长期做法，检查保存结果是否忠实保留适用条件和真实来源；重复陈述不应新增条目，更正应更新版本。关闭再开启应跳过关闭期间资料，普通新对话不创建后台模型任务。
3. 检查不同 Project、Projectless、Child 的授权范围，以及临时模式禁用使用/学习。
4. 纠正、固定、归档、遗忘；请求途中遗忘时旧响应应丢弃，后续请求重建；删除或修订来源也应撤销相关正文。
5. 显式选择历史整理，确认只处理选择时已经存在的资料；自动记忆关闭时仍可选择，临时会话不能选择。删除或重配置整理任务模型，检查 blocked_model 与显式重试；调整历史预算并检查 paused_budget、重启恢复。
6. 导出当前页 Markdown，保存完整 ZIP；退出 Runtime 后导入新目录，核对正文、版本、隐私状态，并确认自动学习关闭。

真实模型记忆验收使用合成对话和临时数据目录，不读取实际聊天或写入实际记忆。模型配置只读，API Key 不进入报告。脚本只向前台模型提供记忆工具和完成检查出口，前台保存不会另发事实审核请求。合成 Run 使用 `full_access`，避免显式保存等待人工审批；脚本包含 Projectless 的语言偏好保存用例。每次最多 36 个模型调用（包含完成检查），每个 Run 最多等待 90 秒，产生实际模型费用：

```bash
PYTHONPATH=runtime uv run --locked python scripts/evaluate-memory-learning.py --model deepseek-v4.1-flash --output /absolute/path/memory-evaluation.json
```

关闭自动学习时的明确长期设置可用 `--case projectless_language_preference --disable-automatic-learning` 单独验收。

可以通过重复的 `--case self_identity --case enduring_workflow` 参数选择样本，减少模型费用。报告包含已有身份与新增偏好分离、同一习惯换种说法后的复用、长期信息、单次要求、假设、已确认决定、未采纳提议、记忆使用、当前指令覆盖和更正样本。状态检查只验证保存路径；人工还应核对正文是否忠实、范围是否扩大、模型是否附加未声明的要求。准确描述“提议尚未采纳”的 continuity 可以是可用事实，不能仅因条目 active 就认定提议已成为项目规则。结果不能代表所有模型或所有表达。此脚本不运行连接测试或能力探测。

离线恢复（目标目录必须不存在，不能与运行中的数据合并）：

```bash
uv run --locked python -m eidos_runtime.memory.backup /absolute/path/backup.zip /absolute/path/new-eidos-data
EIDOS_DATA_DIR=/absolute/path/new-eidos-data pnpm start
```

ZIP 含私有聊天、记忆和执行状态，可能含 MCP 环境配置，未加密。Provider 配置和 Workspace 不包含在内，需另行配置/恢复路径。旧备份可能恢复之后被遗忘资料；恢复默认取消旧作业与回填授权、关闭生成，需要明确确认后重新启用。不要仅复制 `state.sqlite` 当作完整备份。

审批模式定向回归增加 `runtime/tests/test_dangerous_approvals.py`，覆盖高危人工批准、拒绝、取消、重启恢复、版本冲突、完全访问直接执行和真实 Seatbelt 的精确路径例外。完全访问不得创建新的 Approval 或进入 waiting_approval；旧版待审批记录只恢复完成原操作。


## 自定义子 Agent 配置与定向验证

用户在 `<EIDOS_DATA_DIR>/agents/` 放置独立 TOML。默认目录是 `~/.eidos/agents/`。完整字段与示例见 [自定义子 Agent 设计](docs/custom-subagents-design.md)。用户需要先通过现有模型设置保存目标模型；TOML 不保存 API Key。用户省略模型表时，子任务继承父模型。

相关定向验证入口：

```bash
uv run --locked pytest runtime/tests/test_custom_agents.py runtime/tests/test_collaboration.py runtime/tests/test_input_context.py -m "not platform"
node scripts/generate-collaboration-contracts.mjs
node scripts/generate-input-contracts.mjs
pnpm build:main
node --test dist/main/runtime-client-agents.test.js
pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/components/InputPicker.behavior.test.tsx desktop/renderer/src/components/InputContext.behavior.test.tsx desktop/renderer/src/components/AgentPanel.behavior.test.tsx
```

人工验收应使用隔离的数据目录。用户在目录中创建定义后，可以在 `+ → Agent` 选择并查看冻结预览，再检查父模型是否通过现有工具调用对应类型。验收需要覆盖实际提供商、续派、取消、定义编辑和重启恢复。模型参数校验与脚本模型测试不能替代真实 Provider 验收。


## 钉钉渠道定向开发与验证

用户可以在“设置 → 助理”的钉钉入口保存企业内部应用的机器人配置。应用凭证只需要 Client ID 和 Client Secret，机器人和组织 ID 由消息回调提供。机器人默认支持单聊，群聊需要 @。Eidos 不再提供用户、群白名单或单聊开关。应用需要选择 Stream 接收模式。Eidos 需要一直运行并能访问钉钉公网服务。渠道不开放入站监听端口。数据与凭证文件由 Runtime 管理，用户不应手工编辑 SQLite。

维护者修改渠道 DTO 后运行 `node scripts/generate-channel-contracts.mjs`。定向验证入口如下，维护者不需要因此运行 Full Suite：

```bash
uv run --locked pytest runtime/tests/test_channels.py runtime/tests/test_channel_adapters.py runtime/tests/test_storage_schema.py
pnpm build:main
node --test dist/main/runtime-client-channels.test.js
pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/components/settings/ChannelSettings.behavior.test.tsx
pnpm build
pnpm exec electron scripts/test-channels.cjs
```

`test-channels.cjs` 使用隔离的 Electron userData、模拟渠道 API 和四种窗口宽度。它检查保存、键盘操作和完全访问的二次确认，不读取真实钉钉凭证，也不发送真实消息。设置 `EIDOS_CHANNEL_SCREENSHOT=/absolute/path.png` 可以保存页面截图，设置 `EIDOS_CHANNEL_FULL_ACCESS_SCREENSHOT=/absolute/path.png` 可以保存完全访问确认截图。修改渠道服务生命周期时还需要执行既有 Electron smoke；修改 Python 生产依赖时还需要执行 Ruff、Deptry、pip-audit 和 Runtime Bundle 验证。

真实钉钉应用的创建、权限、消息投递与待验收清单见 [钉钉渠道方案](docs/dingtalk-channel-design.md)。


### 飞书与企业微信的渠道验证

“设置 → 助理”现有三个入口分别管理钉钉、飞书和企业微信。飞书需要自建应用的 App ID、App Secret 和长连接事件订阅；企业微信需要智能机器人 API 模式的 Bot ID 和 Secret。配置步骤与个人微信的方案区别见 [渠道方案](docs/feishu-wechat-channel-design.md)。本次没有增加本地 HTTP 服务或常驻外部进程。

开发者可以运行以下定向验证：

```bash
uv run --locked pytest runtime/tests/test_channels.py runtime/tests/test_channel_adapters.py
pnpm build:main
node --test dist/main/runtime-client-channels.test.js
pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/components/settings/ChannelSettings.behavior.test.tsx
pnpm build
pnpm exec electron scripts/test-channels.cjs
pnpm test:electron-smoke
```

真实 macOS Shell 的完全访问用例需要原生执行权限。SDK 测试使用模拟传输，不读取真实凭证。Electron 渠道脚本检查三个渠道在四种窗口宽度下的表单和危险确认。EIDOS_FEISHU_CHANNEL_SCREENSHOT 和 EIDOS_WECOM_CHANNEL_SCREENSHOT 可以指定本地截图路径。

两个官方 SDK 已锁定在 pyproject.toml 和 uv.lock。飞书 SDK 约束 websockets<16，当前锁定为 15.0.1。修改这些依赖时，开发者还需运行 Ruff、Deptry、pip-audit、macOS Runtime Bundle 构建与 bundled-runtime-smoke。Bundle smoke 会导入两个 SDK、飞书帧解析模块和原生 AES 扩展。Model Gateway 当前锁定 pydantic-ai-slim 2.57.0 和其要求的 OpenAI SDK 3.26.0。2026-10-17 的主 Python、隔离工具 Python 与 Runtime Node 依赖审计均通过；原 2.43.0 的五条漏洞已不再报告。审计结果会随漏洞数据库变化，开发者仍需实际运行审计并分别报告各项结果。

## 自定义模型提供商定向验证

开发者可以运行以下入口。测试使用模拟传输，不读取用户凭据，不调用真实 Provider，也不运行 Full Suite。

```bash
uv run --locked pytest runtime/tests/test_custom_model_providers.py runtime/tests/test_model_config_store.py runtime/tests/test_model_gateway_protocol.py
node scripts/generate-custom-model-contracts.mjs
pnpm build:main
node --test dist/main/runtime-client-custom-models.test.js dist/shared/custom-models.test.js
pnpm exec electron scripts/test-custom-models.cjs
pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/components/settings/CustomModelSettings.behavior.test.tsx desktop/renderer/src/components/SettingsPage.behavior.test.tsx desktop/renderer/src/components/ModelSelector.behavior.test.tsx
```

自定义模型的接口测试只获取模型列表。真实验收需要分别检查用户接口的 `/models`、Chat Completions 或 Responses、工具调用和已声明思考参数，不能把列表请求成功当作模型能力验证。

## 欢迎页与首次发送的定向验证

开发者可以运行以下定向检查。页面使用内存 IPC fixture，不读取真实会话，也不调用模型或写入用户工作区。Electron 检查覆盖 320、768、1024、1440 四种宽度、原生输入、首次发送的位移动画、输入框实例保留和再次新建会话。

```bash
pnpm exec vitest run --config vitest.renderer.config.ts desktop/renderer/src/app/welcome.behavior.test.tsx desktop/renderer/src/components/SessionCanvas.behavior.test.tsx
pnpm exec electron scripts/test-welcome-layout.cjs
pnpm test:composer-layout
```

开发者可以设置 `EIDOS_WELCOME_SCREENSHOT=/absolute/path.png` 保存欢迎页截图。行为测试另外覆盖创建失败、Run 启动失败后的草稿恢复、项目选择和系统减少动态效果设置。
