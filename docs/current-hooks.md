# Plugin Hooks 与 Codex 兼容范围

本文记录当前源码。实现入口包括 `extensions/codex_hooks.py`、`extensions/hook_sources.py`、`runtime/hooks.py`、`runtime/hook_lifecycle.py` 和 `persistence/hooks.py`。内部 `LoopLifecycle` 继续只提供诊断观察点。SQLite 继续保存业务事实。

## 安装与信任

用户可以在设置的 Plugins 页面导入本地目录。Runtime 接受三类入口：

| 格式 | 入口 | 当前处理 |
| --- | --- | --- |
| Eidos | `plugin.json`，包含 `schemaVersion: 1` | Runtime 保留原 Skill/MCP 声明，并允许增加 `hooks`。 |
| Codex 兼容格式 | `.codex-plugin/plugin.json` | Runtime 使用 `name` 作为插件身份，并解析 Skills 和 Hooks。 |
| Portable Agent Plugin | 根目录 `plugin.json` | 根清单决定身份。`extensions.com.openai` 存在时，它整体替代 Codex 兼容清单的设置。 |

用户取消目录选择时，Main 返回 `null`，Renderer 结束等待，不显示导入成功或失败提示。Renderer 只有在收到插件记录并完成扩展列表刷新后，才显示导入成功提示。

清单没有声明 `hooks` 时，Runtime 发现 `hooks/hooks.json`。显式声明可以是相对路径、路径数组或内联 Hook 配置。显式声明整体替代默认发现，Runtime 不把两者合并。路径必须使用 `./`，且必须留在包内。Runtime 拒绝重复 JSON 键、重复来源、不支持的正则表达式、符号链接和多硬链接文件。Runtime 保留包内源文件，并安装到 `~/.eidos/extensions/plugins/<pluginId>/<version>/`。目录不包含内容 Hash；SQLite、快照、内容核验和 Hook 信任继续保存并使用完整 Hash。带 shebang 的脚本具有执行权限。

本次目录调整不兼容旧的 `<pluginId>/<version>/<contentHash>/` 布局。旧安装需要移除并重新导入，不提供旧目录回退或旧快照路径迁移。正在被任务引用的资源仍遵循原有延后清理规则。安装使用同级临时目录发布，遇到已有版本目录时拒绝覆盖；发布失败不会删除其他安装，记录提交失败只清理本次新建的目录。

卸载会保留插件的历史记录。用户重新导入相同 ID、版本和内容时，Runtime 可以恢复记录，插件默认未启用。Runtime 在同一事务中清除旧 Hook 信任和 MCP 授权，并记录导入事件。仍被非终态 Run 引用的同版本已卸载包不能重装。v37 还支持显式导入另一个版本，保留活动 Run 的旧文件，并清除旧授权；同版本不同内容继续冲突。版本更新和清理规则见 [current-plugins.md](current-plugins.md)。

Runtime 在导入和读取已安装包时忽略各层目录中的普通 `.DS_Store` 文件。这些文件不进入安装内容和内容 Hash。Runtime 仍然检查符号链接、文件所有者和多硬链接，也继续核验其他文件的内容变化。

外部格式没有声明版本时，Runtime 使用内部版本 `0.0.0`，Desktop 显示“未声明版本”。这些包仍受现有版本冲突规则限制。用户不能用同名同版本的不同内容静默替换已安装包。

导入、启用和信任是三件事。导入不会执行脚本。Desktop 在首次启用可执行 Hook 前，展示事件、匹配器、命令、安装目录和私有数据目录。用户确认后，Runtime 才为该内容 Hash 保存信任。已经启用但未信任的插件仍可提供原有 Skills，Runtime 不执行它的 Hooks。用户可以撤销信任。

用户可以点击插件名称打开资源详情。详情展示该插件的 Skill、已接入的 MCP 服务、Hook 定义和兼容信息。用户可以查看 Skill 正文和调整开关，也可以查看 MCP 工具并单独审阅授权。独立技能页和 MCP 页不再重复列出插件资源。聊天引用、Skill Catalog、Run 快照和运行权限继续使用原有完整资源集合。只打开插件详情不会授予 Hook 信任或 MCP 授权。

Run 开始时，Runtime 把已启用、已信任且本版支持的 Hook 定义固定进扩展快照。新安装或新信任的 Hook 在后续 Run 生效。Runtime 在每次写入 Hook Intent 时重新检查启用状态和信任，撤销会阻止后续调用。Runtime 在启动命令前重新核对安装内容。损坏的包仍可停用、撤销信任和移除。

## 已接入的事件

| Codex 事件 | Eidos 触发点 | 可用结果 |
| --- | --- | --- |
| `SessionStart` | 根 Session 在当前 Runtime 首次执行 Run，或压缩成功后 | `source` 使用 startup、resume、fork、compact。Runtime 可以附加上下文或处理明确停止。 |
| `SessionEnd` | 已执行过 Run 的根 Session 被删除，或 Runtime 正常关闭 | `reason` 使用 other。Runtime 同步执行清理，结果不能阻止关闭。 |
| `UserPromptSubmit` | 父 Run 的初始输入及后续消费的引导输入，采样前 | Runtime 可以附加上下文，或按明确拒绝结果停止本次 Run。 |
| `PreToolUse` | 参数首次有效后，Prepare、审批和副作用之前 | Hook 可以拒绝工具或返回改写参数。Runtime 重新校验改写后的参数、权限和审批。 |
| `PermissionRequest` | Eidos 将要请求审批，且没有待处理审批时 | Hook 可以决定普通请求的 allow/deny。高危人工确认继续保留。 |
| `PostToolUse` | 工具结果提交后；长 Shell 在逻辑退出后 | Hook 可以附加上下文或替换模型看到的结果。原始执行结果继续保留。 |
| `PreCompact` | Runtime 实际开始压缩前 | Runtime 使用 manual 或 auto 匹配；明确停止可以终止当前 Run。 |
| `PostCompact` | 压缩成功提交后 | Runtime 使用 manual 或 auto 匹配；结果可以附加上下文或明确停止。 |
| `SubagentStart` | 子 Run 开始执行时 | Runtime 按 Agent 类型匹配，并把上下文交给子 Agent。`continue: false` 不阻止启动。 |
| `Stop` | 父 Run 正常完成前，包括提交待审阅计划的出口 | `decision: block` 要求模型在同一个 Run 继续。`continue: false` 明确停止。 |
| `SubagentStop` | 子 Run 正常完成前 | Runtime 使用子 Agent 类型匹配，并执行与 Stop 相同的继续语义。 |
| `Interrupt` | 用户取消正在执行的根 Run，资源关闭前 | Runtime 尝试执行清理。结果不能撤销取消或启动新 Run。 |

`write_stdin` 不产生新的 Pre/Post Hook。它属于原 Shell 的后续观察。长 Shell 的 reader 只提交最终结果和待触发记录。Run Worker 在安全边界执行 PostToolUse。它不会在 Shell reader 内运行用户脚本。最终 Shell 输出的反馈也作用于同一 Shell 的 `write_stdin` 模型投影。

Eidos 没有 Codex 的聊天关闭、归档和闲置卸载流程，也没有独立的 clear 启动来源。切换聊天不会触发 SessionEnd。异常退出无法保证执行清理。子 Agent 不触发 SessionEnd 和 Interrupt。Runtime 继续解析并跳过 `prompt` 和 `agent` 处理器；Codex 当前同样保留这些类型。

## 个人与项目配置

Runtime 同时发现 `<data>/hooks.json`、`<data>/config.toml`、`<workspace>/.eidos/hooks.json` 和 `<workspace>/.eidos/config.toml`。默认 `<data>` 是 `~/.eidos`。Runtime 合并同一层的两个文件，并合并个人、项目和插件的定义。TOML 只读取 `[hooks]` 部分。Runtime 不读取 `.codex` 下的个人或项目配置。

Plugins 页展示个人与当前项目的配置来源。用户可以审阅定义、信任当前内容、调整来源开关、撤销信任和查看执行记录。配置默认未信任。Runtime 把同一来源的信任绑定到两个文件的完整内容。文件变化会使原信任失效，用户需要刷新并重新审阅。新定义只在后续 Run 生效。无效配置、符号链接、非普通文件、所有者错误或单文件超过 64 KiB 时，Runtime 跳过该来源并显示错误。

JSON 沿用插件的 `{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"..."}]}]}}` 结构。等价 TOML 使用 `[[hooks.SessionStart]]` 和 `[[hooks.SessionStart.hooks]]`。个人脚本的 `PLUGIN_ROOT` 是 `<data>/hooks/`，项目脚本的 `PLUGIN_ROOT` 是 `<workspace>/.eidos/`。脚本的私有可写目录位于 `<data>/hook-data/`。信任不会开放整个 Eidos 数据目录。

## 输入与输出

命令从 stdin 读取有界 JSON。公共字段包含 `session_id`、`turn_id`、`cwd`、`hook_event_name`、`model`、`permission_mode` 和 `transcript_path`。子 Run 使用父 Session 的 `session_id`，并增加 `agent_id`、`agent_type` 和 `agent_transcript_path`。其中 `agent_id` 对应子 Session ID。

Runtime 从 SQLite 生成 `<data>/hook-transcripts/<sessionId>/transcript.jsonl`，并向脚本开放只读权限。投影使用 `response_item` 消息结构，包含当前 Session 最近的消息和工具记录。投影最多保留 1,024 条记录和 8 MiB，每个正文字段最多 32 KiB。Runtime 会脱敏，并在元信息中标明截断。投影不包含分叉继承历史，也不保证完整复刻 Codex transcript。写入失败时，路径为 `null`。投影不参与恢复，也不替代 SQLite。

工具事件增加 `tool_name`、`tool_use_id` 和 `tool_input`。Post 还包含 Eidos 的实际 `tool_response`。Runtime 把 `run_shell` 映射为 `Bash`。Runtime 为 `apply_patch` 提供 `apply_patch`、`Edit` 和 `Write` 匹配名，并把补丁映射到 `tool_input.command`。Runtime 为 `spawn_agent` 增加 `Agent` 匹配名。其他工具保留 Eidos 名称。Hook 仍需理解 Eidos 的工具参数和结果结构。

Runtime 注入 `PLUGIN_ROOT`、`PLUGIN_DATA`、`CLAUDE_PLUGIN_ROOT` 和 `CLAUDE_PLUGIN_DATA`。Runtime 复用当前打包依赖绑定，并提供 `RUNTIME_PYTHON`。包声明的其他命令依赖不会自动下载或安装。

脚本可以输出空内容或 JSON。Runtime 支持 `decision`、`reason`、`continue`、`stopReason`、`systemMessage`，以及 `hookSpecificOutput` 中的 `hookEventName`、`additionalContext`、`permissionDecision`、`permissionDecisionReason` 和 `updatedInput`。PreToolUse 的 `permissionDecision: allow` 只允许进入 Eidos 原执行链。它不批准扩权。`updatedInput` 必须配合 `allow`，且必须通过工具参数校验。

PermissionRequest 使用 `hookSpecificOutput.decision.behavior: "allow" | "deny"` 和可选的 `message`。任意 deny 优先于 allow。普通 allow 会保存来源为 `hook` 的审批记录，再继续原有 Durable Intent 和核验。Hook 不能替代 `requiresHumanConfirmation` 的高危人工确认。Runtime 不用新 Hook 决定已存在的待处理审批，也不覆盖已保存的拒绝。SessionStart 和 SubagentStart 的普通 stdout 可以进入上下文；Pre/PostCompact 的普通 stdout 不进入上下文。

UserPromptSubmit、Stop 和 SubagentStop 与 Codex 当前行为一致，接受但不实现 `suppressOutput`。Pre/Post 工具事件返回这个字段会记录为不支持的输出。

退出码 2 使用 stderr 作为理由。Pre/UserPrompt 的含义是拒绝，Stop/SubagentStop 的含义是继续，Post 的含义是模型反馈。其他非零退出、超时、无效输出和不支持的输出字段会成为明确 Hook 错误。普通 Hook 错误不会自动结束 Run，也不会伪造成通过检查。只有明确的拒绝或停止结果才改变执行路径。UserPrompt 的拒绝和显式停止使用 `HOOK_REQUESTED_STOP`。Stop 的继续记录会使后续 `stop_hook_active` 为 true。

Runtime 使用现有 AnyIO 内核并行执行同一事件的匹配命令，每次分发最多占用 8 个受所有者管理的阻塞执行名额。没有异步内核时，Runtime 顺序执行。输出按配置顺序汇总。多个有效改写以最后完成的改写为准，任意拒绝优先于改写。这个限制不构成跨 Session 或跨 Run 配额。

每个包或配置来源最多包含 128 个定义，单份解析定义最多 64 KiB。命令默认超时为 600 秒，计时不包含包核验和环境准备。SessionEnd 和 Interrupt 默认 1 秒，单个处理器最多 3 秒，整次清理分发最多 3 秒。单次输入最多 1 MiB，Shell 捕获的 stdout/stderr 总量最多 256 KiB。Runtime 会保留脱敏后的捕获输出。`additionalContextLimit` 当前使用字符估算，本地软限额约为该值的 4 倍字符；值 0 仍受 65,536 字符的硬限额约束。

长上下文使用有界预览，并携带 `hook:<id>` 读取标识。模型可以通过 `read_tool_output(callId="hook:<id>", stream="stdout" | "stderr" | "result")` 分页读取。读取遵循当前 Session 边界，不重新执行脚本。Shell 硬限额丢弃的原始字节无法恢复。后置反馈是模型展示规则，它不回滚副作用，也不充当数据防泄漏隔离层。

## 后台命令与 MCP 处理器

`command` 可以设置 `async: true`。Runtime 在提交 Intent 后排队，不等待命令完成。每个 Session 最多同时运行 8 个后台 Hook，排队与运行中的记录合计最多 256 个。超出容量会保存 Hook 错误。这个限制不作用于其他 Session 的 Run。SessionEnd 总是同步执行。

后台结果只提供信息。Runtime 忽略后台结果的拒绝、停止、参数改写和审批决定。结果在当前或后续 Run 的安全采样边界读取一次。超出本次上下文预算的记录继续等待后续读取；被截短的记录带有分页读取标识。后台完成不会自行恢复或新建 Run，也不会让 Stop 再次继续。根 Session 结束会取消自己和子 Session 的后台进程，等待资源关闭，丢弃未送入上下文的结果，并清理临时 transcript。重启后的未完成后台任务记为 unknown，Runtime 不重放它们。

`mcp_tool` 使用 `server`、`tool` 和可选的 `input`。Runtime 只查询当前 Run 已经连接且仍有授权的服务。Hook 不启动新服务、不登录、不重连。`input` 支持 `${field}` 和 `${nested.field}`；整个值是占位符时，Runtime 保留原 JSON 类型。缺失字段、未连接服务和无效参数会成为 Hook 错误。

MCP Hook 复用现有工具校验、审批、Durable Intent、调用、核验和未知结果记录。只读提示继续遵循 MCP 原有审批规则。普通外部调用仍可能请求审批。Runtime 优先解析 `structuredContent`，否则解析文本内容。MCP Hook 不递归触发 PermissionRequest 或 Pre/PostToolUse。Runtime 不执行后台 MCP Hook，也不在 SessionEnd 调用 MCP。

## 权限、状态与恢复

Hook 命令在进程外执行。Runtime 不把插件 import 到 Python 主进程。命令复用 Shell 环境继承策略、Seatbelt、取消、进程组清理、敏感内容处理和资源所有权。命令使用 Run 的基础权限和已有 Grant。用户的 Hook 信任只额外开放本插件安装目录的读取，以及本插件私有数据目录的写入。信任不增加网络权限，也不允许修改权限模式。`full_access` 沿用用户已确认的模式例外。

命令启动窗口复用当前 Run 的工具门控。活动进程不持续占有门控，其他 Run 不共享这把锁。Runtime 在命令前后记录有界工作区观察。确认的工作区变化会推进现有 `workspace_version`。

SQLite schema v36 增加三张表：

- `plugin_hook_trust` 保存插件和内容 Hash 的信任绑定。
- `hook_invocations` 保存事件身份、Intent、运行状态、输入、有效权限、结果、脱敏输出和完成事件游标。
- `hook_pending_events` 保存与工具结果同事务提交的待触发 Post 事件。

调用的幂等键包含 Run、Plugin、Hook 定义和事件位置。Runtime 先提交 Intent，再启动进程，最后提交结果和 Event/Outbox。完成游标使用提交事件顺序，避免并行调用完成乱序时漏掉反馈。重启会把未完成 Intent/运行记录标为 unknown。Runtime 不重放这些调用，也不要求用户核验，不限制后续工具或正常完成。迟到结果不能覆盖已恢复的 unknown 记录。尚未开始的 Post 事件可继续分发，已经开始过的定义会按幂等记录跳过。

SQLite schema v39 增加 `hook_sources`，保存个人、项目配置与内容信任。`hook_invocations` 增加绑定定义、后台标记、上下文接收 Run 和丢弃时间。现有迁移链保留旧记录，并覆盖迁移失败的事务回滚。

Desktop 继续通过 preload typed IPC、Main 和 stdio JSON-RPC 调用 Runtime。Hook 使用 `plugin/setHookTrust`、`hook/configuration` 和 `hook/history`。共享 Hook 类型与验证 Schema 由 Python 模型生成。命令定义留在私有 Run 扩展快照，普通 Desktop Run 投影不发送这些定义。

插件详情和配置来源提供分页执行记录。每页最多 20 条，每路输出预览最多 4,096 字符。聊天 Feed 显示当前 Run 最近 8 条活动的状态与 `systemMessage`。后台任务在 Run 结束后完成时，Runtime 可以更新诊断信息，但不会改变 Run 的终态。

Hook 表从 schema v36 引入，插件版本记录从 v37 引入。升级前应沿用现有备份流程。旧 Runtime 不能打开较新的 Schema，回滚代码时需要恢复兼容的升级前备份。Runtime 不自动删除表来降级。移除插件沿用版本目录清理规则，私有数据目录继续保留。

## 兼容范围

插件系统已经提供本地包、Skills、本地 stdio MCP、远程 Streamable HTTP MCP 和通用 OAuth。MCP Hook 沿用同一连接和授权边界。远程连接与账号授权的当前范围见 [current-plugins.md](current-plugins.md)。Codex App 连接映射仍未实现，Eidos 不复用 Codex 的账号连接。

Eidos 不实现 PreToolUse 的 `ask`、旧 `approve`、`updatedMCPToolOutput`，也不实现 PermissionRequest 的 `updatedInput`、`updatedPermissions` 或 `interrupt`。Runtime 不把 Codex 官方暂未实现的输出扩展成新的权限规则。插件命令所需的其他依赖不会自动安装。

参考来源是 [Codex Hooks](https://learn.chatgpt.com/docs/hooks) 和 [Plugin 构建文档](https://developers.openai.com/plugins/build/plugins)。实现事实以本仓库当前代码与定向测试为准。

## 本次验证记录（2026-10-24）

- Hook 主链路定向组通过 77 项。测试覆盖 12 个事件、MCP SDK 子进程与 Streamable HTTP fixture、普通审批与高危人工确认、后台并发和队列、取消、子会话资源清理、预算与分页、重启 unknown、不重放、配置审阅、协议和迁移回滚。
- 存储 Schema 与多库存储组通过 45 项。旧版迁移与 v38 → v39 升级均有覆盖。审批、应用、资源关闭组通过 78 项，MCP 与协议组通过 61 项。各组存在重叠，不相加作为唯一测试总数。
- Renderer 设置、Hook 历史和 Feed 定向组通过 78 项。Main 的 Hook、IPC 和 Runtime shutdown 组通过 12 项。Desktop 结构合约通过。
- 独立 Electron 页面通过 320、768、1024、1440px 下的 Hook 历史分页、配置审阅、嵌套弹窗、键盘和焦点检查。测试使用临时 user-data 与 fixture，并保存截图检查。Electron smoke 和 15 项原生 Seatbelt 检查通过。
- 构建、Ruff、Deptry 和 diff 空白检查通过。本次没有新增依赖，也没有修改依赖锁。
- 主 Runtime 的 pip-audit 报告原锁定版本的 39 项已知漏洞，涉及 certifi、opentelemetry-api 和 Pillow。隔离工具 Python 的审计通过。主 Runtime 审计仍未通过，本次没有混入无关依赖升级。
- 本次没有运行 Full Suite、macOS Bundle/DMG 或真实第三方账号与插件认证。远程 MCP 和登录验证使用测试服务，不代表真实 GitHub 账号验收。
