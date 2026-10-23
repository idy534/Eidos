# Plugin Hooks 与 Codex 兼容范围

本文记录当前第一阶段实现。实现入口是 `extensions/plugin_package.py`、`extensions/codex_hooks.py`、`runtime/hooks.py` 和 `persistence/hooks.py`。内部 `LoopLifecycle` 继续只提供诊断观察点。

## 安装与信任

用户可以在设置的 Plugins 页面导入本地目录。Runtime 接受三类入口：

| 格式 | 入口 | 当前处理 |
| --- | --- | --- |
| Eidos | `plugin.json`，包含 `schemaVersion: 1` | Runtime 保留原 Skill/MCP 声明，并允许增加 `hooks`。 |
| Codex 兼容格式 | `.codex-plugin/plugin.json` | Runtime 使用 `name` 作为插件身份，并解析 Skills 和 Hooks。 |
| Portable Agent Plugin | 根目录 `plugin.json` | 根清单决定身份。`extensions.com.openai` 存在时，它整体替代 Codex 兼容清单的设置。 |

清单没有声明 `hooks` 时，Runtime 发现 `hooks/hooks.json`。显式声明可以是相对路径、路径数组或内联 Hook 配置。显式声明整体替代默认发现，Runtime 不把两者合并。路径必须使用 `./`，且必须留在包内。Runtime 拒绝重复 JSON 键、重复来源、不支持的正则表达式、符号链接和多硬链接文件。Runtime 保留包内源文件，并按内容 Hash 安装。带 shebang 的脚本具有执行权限。

Runtime 在导入和读取已安装包时忽略各层目录中的普通 `.DS_Store` 文件。这些文件不进入安装内容和内容 Hash。Runtime 仍然检查符号链接、文件所有者和多硬链接，也继续核验其他文件的内容变化。

外部格式没有声明版本时，Runtime 使用内部版本 `0.0.0`，Desktop 显示“未声明版本”。这些包仍受现有版本冲突规则限制。用户不能用同名同版本的不同内容静默替换已安装包。

导入、启用和信任是三件事。导入不会执行脚本。Desktop 在首次启用可执行 Hook 前，展示事件、匹配器、命令、安装目录和私有数据目录。用户确认后，Runtime 才为该内容 Hash 保存信任。已经启用但未信任的插件仍可提供原有 Skills，Runtime 不执行它的 Hooks。用户可以撤销信任。

用户可以点击插件名称打开资源详情。详情展示该插件的 Skill、已接入的 MCP 服务、Hook 定义和兼容信息。用户可以查看 Skill 正文和调整开关，也可以查看 MCP 工具并单独审阅授权。独立技能页和 MCP 页不再重复列出插件资源。聊天引用、Skill Catalog、Run 快照和运行权限继续使用原有完整资源集合。只打开插件详情不会授予 Hook 信任或 MCP 授权。

Run 开始时，Runtime 把已启用、已信任且本版支持的 Hook 定义固定进扩展快照。新安装或新信任的 Hook 在后续 Run 生效。Runtime 在每次写入 Hook Intent 时重新检查启用状态和信任，撤销会阻止后续调用。Runtime 在启动命令前重新核对安装内容。损坏的包仍可停用、撤销信任和移除。

## 已接入的事件

| Codex 事件 | Eidos 触发点 | 可用结果 |
| --- | --- | --- |
| `UserPromptSubmit` | 父 Run 的初始输入及后续消费的引导输入，采样前 | Runtime 可以附加上下文，或按明确拒绝结果停止本次 Run。 |
| `PreToolUse` | 参数首次有效后，Prepare、审批和副作用之前 | Hook 可以拒绝工具或返回改写参数。Runtime 重新校验改写后的参数、权限和审批。 |
| `PostToolUse` | 工具结果提交后；长 Shell 在逻辑退出后 | Hook 可以附加上下文或替换模型看到的结果。原始执行结果继续保留。 |
| `Stop` | 父 Run 正常完成前，包括提交待审阅计划的出口 | `decision: block` 要求模型在同一个 Run 继续。`continue: false` 明确停止。 |
| `SubagentStop` | 子 Run 正常完成前 | Runtime 使用子 Agent 类型匹配，并执行与 Stop 相同的继续语义。 |

`write_stdin` 不产生新的 Pre/Post Hook。它属于原 Shell 的后续观察。长 Shell 的 reader 只提交最终结果和待触发记录。Run Worker 在安全边界执行 PostToolUse。它不会在 Shell reader 内运行用户脚本。最终 Shell 输出的反馈也作用于同一 Shell 的 `write_stdin` 模型投影。

Runtime 识别 `SessionStart`、`SessionEnd`、`PermissionRequest`、`PreCompact`、`PostCompact`、`SubagentStart` 和 `Interrupt`，但本阶段不触发它们。Runtime 在插件兼容信息中列出这些定义。Runtime 同样列出不执行的 `async`、`mcp_tool`、`prompt` 和 `agent` 处理器。

## 输入与输出

命令从 stdin 读取有界 JSON。公共字段包含 `session_id`、`turn_id`、`cwd`、`hook_event_name`、`model`、`permission_mode` 和 `transcript_path`。Eidos 当前没有导出 Codex transcript 文件，所以路径字段为 `null`。子 Run 使用父 Session 的 `session_id`，并增加 `agent_id`、`agent_type` 和 `agent_transcript_path`。其中 `agent_id` 对应子 Session ID。

工具事件增加 `tool_name`、`tool_use_id` 和 `tool_input`。Post 还包含 Eidos 的实际 `tool_response`。Runtime 把 `run_shell` 映射为 `Bash`。Runtime 为 `apply_patch` 提供 `apply_patch`、`Edit` 和 `Write` 匹配名，并把补丁映射到 `tool_input.command`。其他工具保留 Eidos 名称。Hook 仍需理解 Eidos 的工具参数和结果结构。

Runtime 注入 `PLUGIN_ROOT`、`PLUGIN_DATA`、`CLAUDE_PLUGIN_ROOT` 和 `CLAUDE_PLUGIN_DATA`。Runtime 复用当前打包依赖绑定，并提供 `RUNTIME_PYTHON`。包声明的其他命令依赖不会自动下载或安装。

脚本可以输出空内容或 JSON。Runtime 支持 `decision`、`reason`、`continue`、`stopReason`、`systemMessage`，以及 `hookSpecificOutput` 中的 `hookEventName`、`additionalContext`、`permissionDecision`、`permissionDecisionReason` 和 `updatedInput`。PreToolUse 的 `permissionDecision: allow` 只允许进入 Eidos 原执行链。它不批准扩权。`updatedInput` 必须配合 `allow`，且必须通过工具参数校验。

UserPromptSubmit、Stop 和 SubagentStop 与 Codex 当前行为一致，接受但不实现 `suppressOutput`。Pre/Post 工具事件返回这个字段会记录为不支持的输出。

退出码 2 使用 stderr 作为理由。Pre/UserPrompt 的含义是拒绝，Stop/SubagentStop 的含义是继续，Post 的含义是模型反馈。其他非零退出、超时、无效输出和不支持的输出字段会成为明确 Hook 错误。普通 Hook 错误不会自动结束 Run，也不会伪造成通过检查。只有明确的拒绝或停止结果才改变执行路径。UserPrompt 的拒绝和显式停止使用 `HOOK_REQUESTED_STOP`。Stop 的继续记录会使后续 `stop_hook_active` 为 true。

Runtime 使用现有 AnyIO 内核并行执行同一事件的匹配命令，每次分发最多占用 8 个受所有者管理的阻塞执行名额。没有异步内核时，Runtime 顺序执行。输出按配置顺序汇总。多个有效改写以最后完成的改写为准，任意拒绝优先于改写。这个限制不构成跨 Session 或跨 Run 配额。

每个包最多包含 128 个定义，定义总量最多 64 KiB。命令默认超时为 600 秒，计时不包含包核验和环境准备。单次输入最多 1 MiB，Shell 捕获的 stdout/stderr 总量最多 256 KiB。Runtime 会保留脱敏后的捕获输出。`additionalContextLimit` 当前使用字符估算，本地软限额约为该值的 4 倍字符；值 0 仍受 65,536 字符的硬限额约束。

长上下文使用有界预览，并携带 `hook:<id>` 读取标识。模型可以通过 `read_tool_output(callId="hook:<id>", stream="stdout" | "stderr" | "result")` 分页读取。读取遵循当前 Session 边界，不重新执行脚本。Shell 硬限额丢弃的原始字节无法恢复。后置反馈是模型展示规则，它不回滚副作用，也不充当数据防泄漏隔离层。

## 权限、状态与恢复

Hook 命令在进程外执行。Runtime 不把插件 import 到 Python 主进程。命令复用 Shell 环境继承策略、Seatbelt、取消、进程组清理、敏感内容处理和资源所有权。命令使用 Run 的基础权限和已有 Grant。用户的 Hook 信任只额外开放本插件安装目录的读取，以及本插件私有数据目录的写入。信任不增加网络权限，也不允许修改权限模式。`full_access` 沿用用户已确认的模式例外。

命令启动窗口复用当前 Run 的工具门控。活动进程不持续占有门控，其他 Run 不共享这把锁。Runtime 在命令前后记录有界工作区观察。确认的工作区变化会推进现有 `workspace_version`。

SQLite schema v36 增加三张表：

- `plugin_hook_trust` 保存插件和内容 Hash 的信任绑定。
- `hook_invocations` 保存事件身份、Intent、运行状态、输入、有效权限、结果、脱敏输出和完成事件游标。
- `hook_pending_events` 保存与工具结果同事务提交的待触发 Post 事件。

调用的幂等键包含 Run、Plugin、Hook 定义和事件位置。Runtime 先提交 Intent，再启动进程，最后提交结果和 Event/Outbox。完成游标使用提交事件顺序，避免并行调用完成乱序时漏掉反馈。重启会把未完成 Intent/运行记录标为 unknown。Runtime 不重放这些调用，也不要求用户核验，不限制后续工具或正常完成。迟到结果不能覆盖已恢复的 unknown 记录。尚未开始的 Post 事件可继续分发，已经开始过的定义会按幂等记录跳过。

Desktop 继续通过 preload typed IPC、Main 和 stdio JSON-RPC 调用 Runtime。新增方法是 `plugin/setHookTrust`。共享 Hook 类型与验证 Schema 由 Python 模型生成。命令定义留在私有 Run 扩展快照，普通 Desktop Run 投影不发送这些定义。设置页展示兼容信息、信任状态和最近一次结果。`systemMessage` 当前显示在设置页的最近结果中，聊天 Feed 尚未单独展示这类警告。

升级前应沿用现有备份流程。旧版 Runtime 不支持 v36，回滚代码时需要恢复兼容的升级前数据备份。Runtime 不自动删除 Hook 表来降级。移除插件沿用现有版本目录清理规则，私有数据目录继续保留。

## 本阶段发布范围

当前实现提供本地包、常用同步 command Hooks 和 Skills 兼容入口。它不代表 Codex 插件的完整兼容。Codex App/OAuth、插件捆绑 MCP 的自动接入、MCP Hook 处理器、异步处理器、其余生命周期事件和 transcript 导出仍未实现。外部清单的展示与连接元数据也没有完整映射。Eidos 原有 MCP 配置及授权流程继续可用。

参考来源是 [Codex Hooks](https://learn.chatgpt.com/docs/hooks) 和 [Plugin 构建文档](https://developers.openai.com/plugins/build/plugins)。实现事实以本仓库当前代码与定向测试为准。

## 本次验证记录（2026-10-23）

- 最后的 Hook 定向文件通过 38 项。它覆盖 Shell 别名规范化、短超时计时和超时副作用的 unknown 记录。
- Runtime 主链路定向组通过 136 项，范围包含 Hooks、插件、协议、存储迁移、Shell 环境、工具执行、Run 完成和输出分页。
- 补充定向组通过 81 项，范围包含 Hooks、原生 Shell、原插件行为和记忆撤销投影。两组存在重叠，不能相加作为唯一测试数量。
- Main 的 Hook 真实跨进程测试通过 2 项，共享 IPC 测试通过 3 项。Renderer 状态和完成提示通过 31 项，设置与 Tooltip 行为通过 27 项。Desktop 结构合约通过 4 项。
- 原生 Seatbelt 验证通过 15 项。独立 Hook 测试还覆盖真实沙盒私有目录、改写后高危审批、取消、进程清理、重启 unknown、幂等、并行结果乱序、长 Shell 单次 Post、子 Agent 和计划审阅出口。
- Electron Hook 弹窗检查覆盖 320、768、1024、1440 四种宽度、焦点循环、Escape、版本绑定和先信任后启用顺序。截图检查在动画结束后进行。
- `pnpm build`、Electron smoke、Ruff、Deptry 和 `git diff --check` 通过。本次没有运行 Full Suite、macOS Bundle/DMG 或第三方插件认证。
- 本次没有新增依赖，也没有修改依赖锁。主 Runtime 的 `pip-audit` 报告现有锁定版本的 37 项已知漏洞，涉及 certifi、opentelemetry-api 和 Pillow。隔离工具 Python 的审计通过。Runtime Node 审计报告 3 项漏洞，其中 low、high、critical 各 1 项。审计失败仍是发布前未通过的检查，本次没有把这些无关依赖升级混入 Hook 改动。
