# Eidos 本地开发与阶段验证

本文面向第一次参与桌面端开发的维护者。当前已完成 MVP Lite L0，并完成 L1 的离线代码闭环：桌面端可以选择 Workspace、持久化 Session/Run/Item/ToolCall、配置 DeepSeek、执行只读工具并展示流式 Feed。L1 只剩一次真实 API 联网验收。

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
- Runtime Loop 测试会用确定性 Fake Model 完成 `read_file -> ToolResult -> final answer` 两轮循环。
- DeepSeek Adapter 测试覆盖 SSE 文本、reasoning 隐藏、ToolCall 跨 chunk 归并与 `0600` 私有配置；不会产生真实 API 费用。

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

1. `正在完成 Python Runtime 协议握手…`
2. 左侧 Session 列表与右侧 Eidos Workspace。
3. 尚未配置模型时出现“连接 DeepSeek”区域。

在“连接 DeepSeek”中输入 API Key 并保存。Key 只写入 `~/.eidos/model.json`，文件权限应为 `0600`；界面、Runtime stdout、SQLite 和日志都不应回显 Key。曾经发到聊天或其他第三方系统的 Key 建议先轮换，再作为长期配置使用。

然后按以下步骤验收 L1：

1. 点击“新建 Session”，选择一个不含敏感数据的测试项目。
2. 输入“请先列出文件，读取 README.md，然后概括如何启动这个项目”，按 `Command + Enter`。
3. Feed 应依次出现用户消息、`list_files`/`read_file` 工具项、模型可见文本和最终回答。
4. 退出并重新启动 Eidos，左侧应保留 Session；打开后应看到已完成的 Run 与 Item。
5. 再发起一个任务并立即点击“取消 Run”，状态应停止，不能在稍后被迟到模型结果改写为成功。

当前只读工具不会修改 Workspace。文件写入与 Shell 尚未开放；它们必须等 L2/L3 的 diff/命令审批闭环完成后才能启用。

启动应用的终端中应出现类似日志：

```text
[runtime] ... INFO eidos.runtime Runtime initialized
```

在支持的 macOS 环境还应先看到：

```text
[runtime] ... INFO eidos.runtime Seatbelt self-test passed
```

日志应只出现在终端，不应出现在协议 stdout 或界面正文中。

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
