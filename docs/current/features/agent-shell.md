# Agent Shell

Agent Shell 由 Python Runtime 按 Run 管理。`run_shell` 默认继续使用 PIPE。模型可以设置 `tty=true`，为需要终端的程序创建 PTY。Desktop Terminal 是用户操作的另一条路径。

## 工具

模型可以按以下顺序使用工具：

```json
{"command":"python interactive.py","tty":true,"columns":100,"rows":30,"yieldTimeMs":1000}
```

`run_shell` 会返回 `sessionId`、`executionStatus` 和输出。模型可以使用这个 ID 发送输入：

```json
{"sessionId":"<返回的 ID>","chars":"回答\r","yieldTimeMs":1000}
```

`write_stdin` 的空 `chars` 只等待和读取新输出。PTY 会把 `\u0003` 交给终端处理。程序可以处理 Ctrl-C 后继续运行。PIPE 保留原有的 Ctrl-C 发送和退出清理行为。

工具说明会提示模型在每次调用 `run_shell` 时填写 `display_intent`，包括同一轮里的后续调用。例如，模型可以把 `git status --short` 说明为「检查当前工作区有哪些未提交的改动」。Runtime 保存最多 120 个字符的单行说明。Runtime 先对完整说明脱敏，再把超长部分截断并以「…」结尾。执行记录优先显示说明和实际运行状态。用户展开记录后可以查看并复制命令。

给模型的工具定义把 `display_intent` 列为第一个参数，并标为必填。执行校验仍然接受缺少说明的调用。说明缺失、为空、不是字符串、含换行或控制字符、超过 2000 个字符时，执行记录沿用命令摘要。旧记录也使用同一规则。模型填写了说明但 Runtime 没有采用时，Runtime 写入一条 `shell_display_intent_dropped` 本地日志，日志包含 Run、Provider Call ID 和原因，不包含说明内容。

Runtime 在后续请求中把已保存的说明放回该次 `run_shell` 调用参数的最前面。模型因此能看到自己之前写过说明。执行参数、审批依据和重复调用判断仍然使用不含说明的参数。Fork 继承的历史记录使用同一规则。

人工审批卡在完整命令旁显示「模型说明」。申请原因继续来自 `justification`。Runtime 依据实际参数和权限证据处理审批。说明单独保存在 ToolCall 中，实时通知、会话快照和历史记录都使用这份记录。Hook 改写命令或工作目录时，Runtime 会清除原说明，并在命令被改写时显示改写后的命令。

SQLite v53 为 Shell 展示增加可空字段。升级和备份恢复都保留旧命令及待审批记录。旧记录无需补写说明。

模型可以使用 `control_shell` 明确控制命令：

```json
{"sessionId":"<返回的 ID>","action":"resize","columns":120,"rows":40}
{"sessionId":"<返回的 ID>","action":"terminate","yieldTimeMs":1000}
{"sessionId":"<PIPE 返回的 ID>","action":"close_stdin","yieldTimeMs":1000}
```

`resize` 只接受活动 PTY。宽度和高度均为 2～500，默认值分别为 100 和 30。`terminate` 终止已有进程组，并等待有界输出和结果提交；结果用 `termination=terminated` 区别于 Run 取消。`close_stdin` 只关闭 PIPE 的输入。PTY 的 Ctrl-D 是终端输入，不是关闭单向管道。模型不能传入任意 PID，也不能控制其他 Run 的 Shell。

## 启动、权限和退出

Runtime 使用标准库 `openpty` 分配终端。一个独立 Python helper 使用 `login_tty` 建立控制终端，再执行原有 Shell 命令。Runtime 不在线程中的 `preexec_fn` 里调用 fork 后的 Python 代码。helper 只依赖随应用打包的 Python 标准库。

helper 会先确认终端准备完成，再等待 Runtime 放行。Runtime 会先登记资源所有者并记录执行开始，然后才允许用户命令执行。准备失败或系统在放行前发现取消时，helper 不会执行用户命令。

PIPE 和 PTY 共用参数校验、路径身份核验、权限、Durable Intent、结果核验和 SQLite 提交。普通权限模式继续使用有效 Seatbelt。PTY 只增加对当前已分配终端及 `/dev/tty` 的数据写入许可，不开放整个设备目录、其他路径或网络。`full_access` 沿用用户确认的既有规则。输入和控制操作继续通过 Runtime 创建各自的 Intent。

PTY 将 stdout 和 stderr 合并到 `stdout`，并返回 `transport=pty`、`outputMerged=true`、`columns` 和 `rows`。PIPE 返回 `transport=pipe` 和 `outputMerged=false`。PTY 设置 `TERM=dumb`、`NO_COLOR=1`、`PAGER=cat` 和 `GIT_PAGER=cat`。每次命令仍启动独立 Shell，系统不会保留 aliases、functions 或 Shell 变量状态。

Run 取消、收尾和关闭继续清理进程组及终端描述符。Runtime 不跨 Run 或重启恢复进程。系统会保留已提交结果和未知 Intent，且不会自动重放命令。

## 后台进程

命令可以在前台启动服务，也可以用 `&` 把进程放到后台。主进程退出而同一进程组仍有成员时，Runtime 不会立即终止它们。会话保持 `executionStatus=running`，本次 `run_shell` 或 `write_stdin` 立即返回，结果带有 `backgroundProcesses=true` 和主进程的 `leaderExitCode`。之后的等待按普通运行中会话处理，继承了输出管道的后台进程输出仍会被捕获。

后台进程全部退出、模型调用 `control_shell` 的 `terminate`，或 Run 收尾清理时，系统才提交该命令的最终结果，最终 `exitCode` 是主进程的退出码。Run 收尾停止这些后台进程时，结果与显式 `terminate` 一样记为 `termination=terminated`，不记为未知结果。仍在执行的前台命令在 Run 收尾被停止时，继续记为 `canceled` 和未知结果。最终结果之后不会再出现该命令的后台写入。通过 setsid 或 daemon 模式脱离进程组的进程不在 Run 所有权内，Runtime 无法跟踪或停止它们；工具说明要求模型不要这样启动服务。

## 输出边界

Runtime 为 stdout 和 stderr 各保留最多 1 MiB，合计最多 2 MiB。后台会话和一次性执行使用同一套保留规则：每条流保留前半部分和最新后半部分，系统会在缺口处插入省略字节数。PTY 只有一条合并流，所以最多保留 1 MiB。系统会按 UTF-8 边界返回文字。增量等待使用原始流的字节位置，已读内容不会因为尾部滚动而重新返回。

Feed 的实时追加预览合计最多 256 KiB。模型可以继续轮询最新尾部。命令退出后，Feed 会在实时预览不足时展示结果中保留的首尾。模型投影每条流最多 32 KiB，按约 1:3 保留首尾，Shell 结果整份最多 64 KiB；两条流都很长时按比例收缩。原始捕获已省略的内容无法恢复。

`read_tool_output` 继续使用原 `run_shell` 的 provider call ID。活动输出返回 `outputRevision`。模型在后续分页时应传回该值；快照变化时，工具会返回 `output_snapshot_changed`，模型应从零或 `fromEnd=true` 重新读取。活动输出已经截断时，系统拒绝没有版本的非零偏移分页。终态输出仍可从 SQLite 分页读取。

## 当前范围

第一版支持终端检测、无换行提示、输入、Unicode、尺寸调整、终端 Ctrl-C、显式停止和 PIPE EOF。Feed 会移除 ANSI/OSC 控制序列，并按纯文本展示输出。第一版没有完整终端画面、屏幕光标状态、用户接管、跨 Run 常驻服务或重启接回进程的能力。后台进程最长存活到所属 Run 结束。
