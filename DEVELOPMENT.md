# Eidos 本地开发与阶段验证

本文面向第一次参与桌面端开发的维护者。当前已完成 MVP Lite L0，并进入 L1：Electron Main 已可通过 Python Runtime 创建、分页读取并跨进程重启保留 Session 元数据。

## 1. 环境要求

- macOS
- Node.js 22.12 或更高版本
- pnpm 11
- Python 3.11 或更高版本

先确认本机环境：

```bash
node --version
pnpm --version
python3 --version
```

如果缺少 Node.js、pnpm 或 Python，可使用 Homebrew 安装：

```bash
brew install node pnpm python
```

## 2. 第一次安装

在 Eidos 仓库根目录执行：

```bash
pnpm install
```

Electron 第一次安装或启动时需要从官方源下载对应 macOS 架构的 Chromium 二进制，耗时会明显长于普通前端依赖。后续启动会复用本地缓存。

## 3. 自动化验证

运行全部当前阶段测试：

```bash
pnpm test
```

预期结果：

- Python 协议测试全部通过。
- macOS Seatbelt 策略与 fail-closed smoke test 全部通过。
- TypeScript Main Client 测试全部通过。
- 测试会真实拉起 Python 子进程，不使用 Runtime mock。
- 非协议 stdout 测试会证明 Main 能终止损坏的 Runtime。
- Session 测试会创建隔离 SQLite，验证 Runtime 重启后的 `session/list|read` 结果。

验证桌面端可以完整构建：

```bash
pnpm build
```

预期结果：TypeScript 类型检查通过，Vite 在 `dist/renderer/` 生成 Renderer 资源。

## 4. 手动界面验证

启动应用：

```bash
pnpm start
```

窗口中应依次看到：

1. `正在启动 Python Runtime 并完成协议握手…`
2. `Runtime 已就绪`
3. `协议 v1 · Runtime 0.1.0 · Shell 暂未启用`

`Shell 暂未启用` 是当前阶段的正确结果。Seatbelt 自检通过只是必要条件；审批、输出上限和完整执行器完成前，Runtime 仍必须报告 Shell 不可用。

启动应用的终端中应出现类似日志：

```text
[runtime] ... INFO eidos.runtime Runtime initialized
```

在支持的 macOS 环境还应先看到：

```text
[runtime] ... INFO eidos.runtime Seatbelt self-test passed
```

日志应只出现在终端，不应出现在协议 stdout 或界面正文中。

当前 Session 能力只开放给 Main Client，尚未接入 Renderer，因此界面暂时不会出现 Session 列表或文件夹选择按钮。请以 `pnpm test` 中的跨 Runtime 重启集成测试作为本阶段验收；对应 UI 会在 Workspace 主链路增量中加入。

## 5. 关闭验证

使用 `Command + Q` 退出 Eidos。终端应结束 Electron 进程，Python Runtime 不应残留。

可选检查：

```bash
pgrep -af eidos_runtime
```

没有输出表示 Runtime 已随桌面端退出。

## 6. 常见失败

### 窗口显示 Runtime 启动失败

先确认：

```bash
python3 --version
python3 -m unittest discover -s runtime/tests -v
```

如果系统 Python 不在默认路径，可显式指定：

```bash
EIDOS_PYTHON=/绝对路径/python3 pnpm start
```

### Electron binary is not installed

重新执行 `pnpm install`，并等待 Electron 官方二进制下载完成。不要从无法核验来源的镜像下载并直接执行二进制。

### 自动测试通过但界面未就绪

保留启动终端中的 `[runtime]` 日志与窗口错误文案。L0 的错误状态不会自动降级或跳过 Runtime，可以直接根据这两处信息定位。
