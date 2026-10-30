# Eidos 本地开发

本文说明开发环境、安装、启动、常用检查与构建入口。专题配置、详细验证和人工验收见 [开发参考与专题验收](docs/development/README.md)。当前产品行为见 [文档入口](docs/README.md)。

## 环境要求

- 开发者需要 macOS、Node.js 22.12.0 或更高版本、pnpm 11、Python 3.11 或 3.12，以及 uv。
- 原生 Sandbox 验证需要 `/usr/bin/sandbox-exec`。
- Runtime Bundle 与 DMG 构建需要 Apple Silicon macOS。

## 安装与启动

开发者在仓库根目录运行：

```bash
pnpm install
uv sync --locked
pnpm start
```

`uv sync --locked` 创建仓库 `.venv`。`pnpm start` 先构建 Preload、Main 和 Renderer，再启动 Electron。源码 Runtime 默认使用 `.venv/bin/python`，开发者不使用全局 Python 安装项目依赖。

开发者调试文档 Skill 脚本时，可以运行 `uv sync --locked --group document-skills` 安装对应的开发依赖。正式 App 保留核心解释器，文档扩展依赖由 Eidos 准备到 `~/Eidos/Resource/`。开发者在 Eidos 内验收这四个 Skill 前，需要先运行 `pnpm build:runtime:mac`，以生成依赖清单与资源索引；开发依赖 group 本身不提供发布绑定。详细构建与验证见[内置文档 Skill](docs/current/features/document-skills.md)。

## 隔离开发数据

开发者可以同时指定独立的数据目录与 Projectless 工作空间，避免测试写入日常使用目录：

```bash
EIDOS_DATA_DIR=/private/tmp/eidos-dev-data EIDOS_PROJECTLESS_ROOT=/private/tmp/eidos-dev-workspaces pnpm start
```

| 变量 | 用途 |
| --- | --- |
| `EIDOS_DATA_DIR` | 指定 Runtime 数据目录。 |
| `EIDOS_PROJECTLESS_ROOT` | 指定无项目工作空间根目录；它不受 `EIDOS_DATA_DIR` 控制。 |
| `EIDOS_PYTHON` | 用绝对路径覆盖源码开发使用的 Python。 |

## 常用检查

开发者优先选择与改动直接相关的测试文件。[专题验证入口](docs/development/README.md#专题配置与定向验证) 列出各功能的命令与覆盖范围。

| 命令 | 用途 |
| --- | --- |
| `pnpm check:python` | 执行 Python 静态检查与依赖检查、审计。 |
| `pnpm test:contracts` | 检查 Python / TypeScript 协议契约。 |
| `pnpm build` | 构建 Desktop，并执行对应类型检查。 |
| `git diff --check` | 检查 Diff 中的空白错误。 |

执行者只有在用户明确要求时，才运行 `pnpm test` 等完整项目或完整层级测试。`pnpm test:affected` 可能选择完整层级，执行者需要先核对映射。详细范围见 [验证选择](docs/development/README.md#验证选择与执行环境)。

开发者根据实际改动补充原生或生命周期检查：

```bash
pnpm test:seatbelt-native
pnpm test:electron-smoke
```

## 构建与打包

| 命令 | 用途 |
| --- | --- |
| `pnpm build` | 构建源码 Desktop。 |
| `pnpm build:runtime:mac` | 构建独立 macOS arm64 Runtime Bundle。 |
| `pnpm package:mac` | 生成本地未签名 DMG；流程包含完整回归与原生检查。 |
| `pnpm package:mac:release` | 生成签名发布包；流程需要 Apple 签名与公证凭据。 |

开发者在 [构建打包与原生验证](docs/development/README.md#构建打包与原生验证) 中查看 Bundle 校验、签名顺序、CI 复用条件与产物位置。

## 详细参考

- [专题配置与定向验证](docs/development/README.md#专题配置与定向验证) 提供插件、渠道、模型、搜索与上下文等功能的检查入口。
- [人工验收](docs/development/README.md#人工验收) 提供 Shell、文件、权限、输入引用和记忆的操作步骤。
- [运行配置与诊断](docs/development/README.md#运行配置与诊断) 提供启动补充、数据与迁移背景、故障检查和 Trace 配置。
