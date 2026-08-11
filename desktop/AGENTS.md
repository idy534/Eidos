# Desktop test policy

本文件补充根目录 `AGENTS.md`，仅约束 `desktop/` 下的修改。与根规则冲突时，本文件作为更具体规则优先。

## 开发过程

不要因为普通 Renderer 或 Main 修改默认反复运行完整 `pnpm test`。

优先顺序：

1. 先运行 `pnpm test:affected` 或直接相关的 Renderer/Main 测试。
2. 测试失败后只重跑失败测试或所属 Desktop 领域。
3. 任务收尾至少运行 `pnpm test:fast`。
4. 只有涉及共享 DTO、Main/Runtime 协议、启动/退出生命周期、全局 Build/Test 配置或 PR 最终验证时补充 `pnpm test:full`。

`pnpm test:electron-smoke` 只在 Electron 启动、Runtime 子进程、Quit/Shutdown、preload 或窗口生命周期相关修改时运行。

macOS Runtime Bundle、Electron Builder、DMG 和 Packaging smoke 不属于普通 Desktop 开发循环；只有修改 Packaging、bundled Runtime、发布配置或执行 Release 验证时运行。

最终回复必须明确列出运行过的测试、未运行的关键验证，以及是否执行 Full Suite。