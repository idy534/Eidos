# Desktop test policy

本文件补充根目录 `AGENTS.md`，仅约束 `desktop/` 下的修改。与根规则冲突时，本文件作为更具体规则优先。

## 开发过程

不要因为普通 Renderer 或 Main 修改默认反复运行完整 `pnpm test`。

优先顺序：

1. 执行者先运行直接相关的 Renderer/Main 测试。执行者只有在确认 `pnpm test:affected` 会选择定向测试时才使用该入口；该入口若会运行完整 Desktop/Main 层或无关的 Fast 套件，执行者应改用更窄的测试。
2. 测试失败后只重跑失败测试或所属 Desktop 领域。
3. 执行者在收尾时运行覆盖本次改动的定向测试。若没有合适的定向入口，执行者再考虑受影响层的 Fast 测试；`pnpm test:fast` 不是默认要求。
4. 共享 DTO、Main/Runtime 协议、启动/退出生命周期和全局 Build/Test 配置变化时，运行对应的跨层或集成定向测试。普通 Renderer 修改只运行相关 Renderer 测试。

`pnpm test`、`pnpm test:full`、`pnpm test:runtime:full`、`pnpm test:runtime`、`pnpm check:python:full`、`pnpm test:main` 和 `pnpm test:desktop` 会运行完整套件或完整层级。用户明确要求 Full Suite 或明确要求执行完整套件命令时，执行者才运行这些命令。改动范围或风险本身不触发 Full。

`pnpm test:electron-smoke` 只在 Electron 启动、Runtime 子进程、Quit/Shutdown、preload 或窗口生命周期相关修改时运行。

macOS Runtime Bundle、Electron Builder、DMG 和 Packaging smoke 不属于普通 Desktop 开发循环；只有修改 Packaging、bundled Runtime、发布配置或执行 Release 验证时运行。

最终回复必须明确列出运行过的测试、未运行的关键验证，以及是否执行 Full Suite。
