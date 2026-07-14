# Eidos 本地开发与阶段验证

本文面向第一次参与桌面端开发的维护者。MVP Lite L0-L3 的代码与离线闭环已经完成：桌面端可以选择 Workspace、持久化 Session/Run/Item/ToolCall、配置 DeepSeek、执行只读工具、审批文件修改与 Shell，并展示流式 Feed。最终退出还需要在本机原生环境运行一次完整测试，并由用户在界面输入真实 API Key 完成联网验收。

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
- `protocol/fixtures/v1.json` 固定代表性协议向量：Python 验证初始化/错误 envelope，TypeScript 验证完整向量解析，真实审批/通知由双进程集成测试覆盖；分块超限与慢通知消费者也有回归。
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

如需做不会触碰真实 `~/.eidos` 的开发验收，可使用隔离数据目录：

```bash
EIDOS_DATA_DIR=/private/tmp/eidos-smoke-data EIDOS_FAKE_MODEL=1 pnpm start
```

窗口中应依次看到：

1. `正在完成 Python Runtime 协议握手…`
2. 左侧 Session 列表与右侧 Eidos Workspace。
3. 尚未配置模型时出现“连接 DeepSeek”区域。

已经保存过 Key 时，顶部会显示“更换 API Key”。错误、过期或撤销的 Key 可直接替换；应用不会展示旧值。窗口在等待审批时关闭并重新打开，待审批卡会从 Electron Main 的内存状态重新载入。

在“连接 DeepSeek”中输入 API Key 并保存。Key 只写入 `~/.eidos/model.json`，文件权限应为 `0600`；界面、Runtime stdout、SQLite 和日志都不应回显 Key。曾经发到聊天或其他第三方系统的 Key 建议先轮换，再作为长期配置使用。

然后按以下步骤验收 L1：

1. 点击“新建 Session”，选择一个不含敏感数据的测试项目。
2. 输入“请先列出文件，读取 README.md，然后概括如何启动这个项目”，按 `Command + Enter`。
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
