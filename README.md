# Eidos

让想法拥有可执行的形态。

Eidos 是一个 macOS 桌面 Agent Runtime。Electron Desktop 通过 stdio JSON-RPC 管理 Python Runtime；Runtime 调用模型、执行受控工具，并将 Session、Run、Item、ToolCall、审批与事件事实持久化到 SQLite。

## 当前实现

- [当前架构](docs/current-architecture.md)
- [当前能力](docs/current-capabilities.md)
- [当前限制](docs/current-limitations.md)
- [文档索引与权威性](docs/README.md)

当前代码支持 DeepSeek Chat Completions、Session/Run 管理、持久 FIFO、文件与 Shell 工具、审批、macOS Seatbelt、Plugin/Skill/stdio MCP、事件投影和恢复。模型可在一次响应中声明多个 ToolCall；Runtime 只并发执行标记为 `parallel_safe` 的安全只读工具，Shell、副作用工具和外部工具保持独占，结果按模型声明顺序返回。Phase E-F 还建立了 typed persistence/Method Registry、bounded Repository Inventory/Tree-sitter Index、确定性检索、ContextPlan/ContextSnapshot、compaction verification 和 long-task control seams；这些新增边界的未接入路径见[当前限制](docs/current-limitations.md)。

## 开发运行

要求 macOS、Node.js 22+、pnpm 11、Python 3.11 或 3.12，以及 [uv](https://docs.astral.sh/uv/)。

```bash
pnpm install
uv sync --locked
pnpm start
```

`uv` 在仓库根目录创建 `.venv`；Electron 在源码开发时刻意从该路径使用 `.venv/bin/python`。`uv.lock` 已提交，只有在有意修改 `pyproject.toml` 中的生产依赖后才应更新它；`pip install -r runtime/requirements.txt` 已废弃。

## 验证

```bash
pnpm check:python
pnpm test
pnpm build
pnpm test:seatbelt-native
pnpm test:electron-smoke
```

`test:seatbelt-native` 必须在可用的原生 macOS Seatbelt 环境执行；不可用或发生跳过会失败。Electron smoke 使用临时 Eidos 数据目录和临时 Electron user-data 目录，不访问真实模型。

## 文档权威性

`docs/current-*.md` 只描述当前代码已经实现的行为，是判断当前实现的文档入口。`docs/prd/`、`docs/tdd/`、`docs/decisions.md` 和 `docs/archive/phases/` 用于目标设计、决策背景或历史追溯；它们不能替代当前代码、测试和 `current-*` 文档。
