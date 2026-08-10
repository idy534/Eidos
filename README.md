# Eidos

Eidos 是一个面向 macOS 的桌面 Agent Runtime。Electron Renderer 通过 typed IPC 连接 Electron Main。Electron Main 通过 JSON-RPC 2.0、JSONL 和 stdio 连接 Python Runtime。Python Runtime 负责 Model、Context、Tool、Approval、Sandbox、SQLite 事实和运行恢复。

## 系统组成

```text
Electron Renderer
    ↓ typed IPC
Electron Main
    ↓ JSON-RPC 2.0 / JSONL over stdio
Python Runtime
    ├── Run orchestration
    ├── Context and Model Gateway
    ├── Tool / Approval / Sandbox
    ├── Plugin / Skill / MCP
    └── SQLite persistence and events
```

## 开发启动

开发机需要 macOS、Node.js 22.12+、pnpm 11、Python 3.11 或 3.12，以及 [uv](https://docs.astral.sh/uv/)。

```bash
pnpm install
uv sync --locked
pnpm start
```

如果需要隔离开发数据，可以设置 `EIDOS_DATA_DIR`：

```bash
EIDOS_DATA_DIR=/private/tmp/eidos-dev-data pnpm start
```

## 验证

```bash
pnpm check:python
pnpm test
pnpm build
pnpm test:seatbelt-native
pnpm test:electron-smoke
```

## 生成 DMG

开发者在 Apple Silicon macOS 上运行以下命令生成本地未签名 DMG：

```bash
pnpm package:mac
```

正式打包需要 Apple Developer signing 和 notarization credentials：

```bash
pnpm package:mac:release
```

## 文档入口

- [文档导航](docs/README.md)
- [当前架构](docs/current-architecture.md)
- [当前能力](docs/current-capabilities.md)
- [当前限制](docs/current-limitations.md)
- [宏观架构图](docs/architecture-overview.html)
- [本地开发与验证](DEVELOPMENT.md)
