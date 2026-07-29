# 用户流程与界面

版本：v0.4（探索草案）

范围说明：本文描述目标态用户体验草案。第一期只实现 [MVP Lite](../mvp-lite.md) 定义的 Workspace Mode、单活动 Run 和最小 Execution Feed。

## 1. Workbench 信息架构

MVP 保留三栏工作台，但不内嵌 Terminal：

```text
┌──────────────┬──────────────────────────────┬──────────────────────┐
│ 导航区        │ 核心交互区                    │ 上下文与产物区          │
├──────────────┼──────────────────────────────┼──────────────────────┤
│ Sessions     │ 对话与 Execution Feed         │ 文件树 / Artifacts      │
│ Workspaces   │ 工具与审批卡片                 │ Diff / 预览 / 日志       │
│ Models       │ Run 状态与输入框               │ 打开系统 Terminal       │
│ Settings     │ 队列状态                       │                      │
└──────────────┴──────────────────────────────┴──────────────────────┘
```

模式差异：

| 区域 | Workspace Mode | Public Mode |
|---|---|---|
| 文件树 | 显示 | 不显示 |
| 文件预览 | 显示 active root 文件 | 只预览 Artifact |
| Artifact | 显示 | 显示且是唯一产物入口 |
| 系统 Terminal | 可由用户点击打开 | 不提供 |

Workspace 文件树是面向本机用户的视图，可显示受保护条目并标记不可预览；Agent 的 `list_files` 使用更严格的可见性投影，敏感文件名不进入模型上下文。

左侧任务导航采用 `Workspace -> 任务` 两级结构：canonical Workspace 路径相同的 Session 必须归入同一个项目节点，节点标题显示目录 basename，完整路径只作为辅助信息；Session 作为该 Workspace 下的任务展示，不再为每个 Session 重复显示一条 Workspace。项目按首次 Session 创建时间倒序排列，向已有项目增加 Session 不改变项目顺序；项目行使用展开/折叠两种文件夹图标，可折叠任务列表，并在右侧提供加号以当前 Workspace 直接创建 Session。导航区“项目”、项目名称和任务标题均使用常规字重，不以加粗区分层级。任务行改为紧凑单行，不再在标题下方显示“已完成”等状态文字；标题右侧只显示 Runtime 返回的状态标识：未读完成为绿色实心点、进行中为转圈、失败为红色实心点，新任务、已取消任务和已读完成任务不显示彩色标识。点击进入完成任务即视为已读；新的 Run 开始后重新计算下一次完成的未读状态。

展开或折叠项目只更新 Renderer 本地状态，不读取 SessionSnapshot。点击当前任务不得重新读取或替换当前快照；切换到其他任务时先即时更新左侧选中态，后台读取目标快照后原子替换内容区，期间保留当前内容且不得用全局 `disabled/opacity` 让整个导航闪烁。切换失败时回退到原选中态并展示安全错误。

左下角固定显示齿轮配置入口与当前模型摘要，例如 `DeepSeek · deepseek-v4-flash 已配置`；原对话区顶部不再显示模型配置横幅。第一版配置页只提供模型列表与 API Key 配置，不提前加入未实施的通用设置；Toolchain 等设置在对应能力进入阶段清单后再增加。

新 Session 在首次提交任务前显示“新任务”。首次 `userInput` 通过本次选定模型执行一次无工具标题生成，使用输入语言产出不超过 60 字符的单行标题；标题与首个 Run 原子持久化，后续补充和新 Run 不自动改名。模型命名失败或返回空/非法标题时，使用首次输入的有界安全短文本，不阻断原任务执行。用户手动改名后，自动命名不得再次覆盖。

Session 内容区顶部只显示本次任务标题和紧邻标题的紧凑三点菜单，标题字号低于页面级标题。内容区标题右键、三点菜单和左侧任务标题右键使用同一组操作：重命名、删除任务；删除必须二次确认，只删除 Eidos 中的任务与运行历史，不修改 Workspace 文件。`Developer Preview · Phase 2`、`Eidos Workspace`、Workspace 绝对路径和通用审批/敏感扫描说明不再占用 Session 内容区；Workspace 路径保留在左侧项目辅助信息中，具体权限与风险在实际 Approval 卡片中说明。

## 2. Execution Feed

Execution Feed 合并对话、执行流和 Timeline，至少展示：

- 用户输入与后续补充信息。
- 模型文本、工具调用和最终回答。
- ToolCall 状态、参数摘要和结果摘要。
- Approval 请求、决定、拒绝原因和版本冲突。
- Shell 运行状态、最近输出、输出大小、退出码和终止原因。
- Shell 执行窗口内观察到的文件变化分类、manifest 完整性和资源上限触发原因。
- stdout/stderr 中间省略标记、命令结束后的 tail 回放和断线后一致的持久化回放。
- 中断的模型流保留已显示进度文本，并明确标记“输出未完成”。
- Run 排队、运行、暂停、停止、失败、取消与完成状态。
- Artifact 发布与版本。
- 安全拒绝、事实确认屏障和降级摘要。
- 敏感输入、敏感 ToolCall、扫描资源超限和扫描服务故障的结构化提示。
- 模型认证或配置错误的终态失败，以及更换 Profile 后创建新 Run 的操作入口。

Execution Feed 不保存或展示模型供应商的 raw reasoning：

- 有 ToolCall 的普通文本展示为 `assistant_progress`。
- 无 ToolCall 且响应完成的普通文本展示为 `final_answer`。
- reasoning token 数量可以作为用量元数据展示，但 reasoning 内容不进入消息、Timeline 或回放。
- UI 不使用“思维链”“内部思考”等表述。

用户发送任务后，若尚未收到模型文本或结构化执行项，Feed 显示带 reduced-motion 回退的“正在思考”动态文字，不显示独立 Run 卡片。只要该轮出现工具、文件读写、搜索、Shell 或其他结构化执行项，Feed 就把最终回答之前的 `assistant_progress` 与执行项合并为一个过程组：执行期间默认展开并显示“正在处理 {duration}”，Run 终态后默认折叠并显示“已处理 {duration}”，用户仍可展开回看；最终回答位于过程组之后并继续流式输出。若该轮没有任何结构化执行项，则不创建过程组，模型正文直接流式输出。成功终态不显示“Run 已完成”标签或展示框；暂停、失败、取消和副作用不确定仍保留必要的结构化状态提示。

过程组中的工具行按类型显示读取、搜索、编辑和运行命令摘要，具体结果默认可折叠。Shell 行摘要显示“正在运行/已运行 {command}”；展开后以 Shell 结果卡显示完整 `$ command`、stdout/stderr（空结果显示“无输出”）以及成功或失败状态。Feed 只从 ToolResult canonical envelope 的白名单字段读取展示内容。

Session 中的模型正文按 CommonMark Markdown 与 GFM 表格展示，至少支持标题、段落、强调、列表、引用、分隔线、表格、行内代码和代码块；用户消息保持纯文本，工具、审批和运行状态继续使用结构化组件。Markdown 原始 HTML 不执行，图片不自动加载，正文链接不在 Session 内直接导航，避免模型内容触发脚本、远程请求或窗口跳转。

右侧 Session 使用统一排版 token：UI 字体为 macOS/Windows 中文系统字体栈，代码字体为系统等宽字体栈；正文 `14px/22px`、代码 `13px/20px`、标题 `16px/24px`、caption `12px`、辅助小字 `11px`。Session 标题、正文、Composer 和工具代码必须使用对应 token，不再混用页面级衬线标题或任意 rem 字号。

## 3. Model Profile 流程

- 新任务首次提交前，Composer 操作栏在“开始”按钮左侧显示模型选择器。选项由 Runtime 有序返回，当前至少包含 `deepseek-v4-flash` 与 `deepseek-v4-pro`；默认选择第一个可用模型，当前返回顺序将 `deepseek-v4-flash` 放在首位。
- 用户可在开始前切换并确认本次任务使用的模型；首个 Run 创建后模型锁定，后续 Segment、恢复和重试继续使用该 Run 的模型快照，不在执行中切换。
- 模型列表为空或没有可用模型时禁用“开始”，并引导用户从左下角齿轮进入模型配置。
- 创建 Profile 只保存 OpenAI-compatible 连接配置和显式 `responses|chat_completions` wire API，不会自动使其可用于 Session。
- 用户必须显式执行 Test Connection；探测不携带用户任务、Session 消息、Workspace 内容或 Artifact 正文。
- 探测必须验证认证、模型存在、streaming、ToolCall 和 usage 契约，并生成版本化 capability snapshot。ToolCall 探测使用一个固定无副作用工具完成受控调用与 ToolResult 续接，不执行真实工具。
- 只有最近一次能力探测成功且存在有效 snapshot 的 Profile 才能被 Session 选择或用于创建新 Run；失败项显示安全的分类结果，不回显 API Key。
- Run 始终使用创建时固化的 Profile 与 capability snapshot。运行期间发现能力漂移时显示结构化模型错误，不静默修改原 Run 或切换 Profile。
- Snapshot 不按时间自动过期，也不在后台自动探测；连接/协议配置变化、Gateway 契约升级或运行中确认能力漂移时失效，重新使用前必须由用户再次 Test Connection。
- 用户可以编辑 Profile。名称等展示字段变化不影响 snapshot；连接、认证、参数和上下文上限变化会立即使 Profile 不可选择，既有 Run 不受影响。
- 用户可以 Archive 和恢复 Profile，但不能物理删除。Archived Profile 不可用于新 Session/Run，历史 Session、Run、Timeline 和 snapshot 保留；恢复后仍需满足有效 snapshot 条件。
- 每个 Profile 独占 API Key 凭证槽位；保存后不回显明文。替换密钥只影响该 Profile，不存在跨 Profile 共享或选择凭证。
- Run 不复制 API Key；既有 Run 的后续模型请求使用该 Profile 凭证槽中的当前密钥。轮换密钥不会修改 Run 的非密钥配置和能力快照。
- 认证模式只提供 `bearer`、`api_key_header` 和 `none`，不接受任意自定义 Header。
- `base_url` 可以指向公网、loopback、局域网或其他私网 HTTP(S) 服务；不允许 URL 内嵌凭证，跨 Origin Redirect 被拒绝。HTTP 端点明确标记为非加密连接。
- `base_url` 是 API 根地址；UI 展示 Adapter 追加后的最终 `/responses` 或 `/chat/completions` URL。用户填写完整 endpoint 时拒绝，不自动去重或猜测。
- HTTPS 始终校验证书、主机名和系统信任链，不提供忽略证书错误的继续入口。
- Provider 扩展参数可以透传，但不能覆盖 Runtime 管理的模型、消息、工具、streaming、认证、传输或输出上限字段。
- `context_window_tokens` 与 `max_output_tokens` 由用户显式填写，Eidos 不按模型名称自动推断；明确的上下文上限不匹配会使 Run 失败并要求修改 Profile 后重新测试。
- HTTP 请求与 SSE streaming 是唯一模型传输。Test Connection 必须验证 SSE 终态、ToolCall/ToolResult 关联和 usage；不探测或协商 WebSocket。
- Test Connection 分别展示 usage、ToolCall 分片关联、工具控制/Schema Dialect、无状态 ToolResult 续接和输出 token 字段结果；Chat 只在显式测试中协商兼容字段，Run 不临时试错。

## 4. Workspace 主流程

```text
选择 Workspace
  -> 创建 Session，并从 Runtime 返回的可用模型列表选择本次模型
  -> 首次提交任务，生成并固化任务标题，Run 进入 FIFO 队列
  -> 执行器获取 Run
  -> 模型通过只读工具收集上下文
  -> 模型单独提出一个写工具或 Shell
  -> 用户查看 diff/命令和权限范围
  -> Approve 或 Reject
  -> Runtime 复检前置条件并执行
  -> Agent 观察结果并继续
  -> 输出最终回答或进入暂停/终态
```

文件修改规则：

- 一次文件写 ToolCall 只操作一个普通文件。
- 创建使用 `write_file`，修改已有文件优先使用 `apply_patch`，明确删除使用 `delete_file`。
- 文件工具只处理严格 UTF-8 文本；二进制、其他编码和超大文件使用受审批 Shell。
- `write_file` 不隐式创建父目录；目录创建和删除使用受审批 Shell。
- 覆盖已有文件必须先完整读取；Patch 只能修改 Agent 已读取的行区间。
- 审批通过后、执行前必须重新验证文件版本。
- `file_version_conflict` 不执行原写入，由 Agent 重新读取并重新申请。
- 每个 Step 的模型请求只包含当时合法可用的工具；事实确认屏障期不暴露副作用工具，Shell capability 不可用时不暴露 `run_shell`。
- 所有继续进入模型上下文的工具结果使用同一结构化 envelope，明确区分成功、错误、跳过、拒绝、中断和不可用。
- Execution Feed 与模型上下文共享同一结果事实，但分别使用安全字段白名单与有界投影；上下文裁剪不会改变工具实际是否完成、工具自身是否截断或是否可能产生副作用。
- Runtime 无法形成合法结构化工具结果时，Run 直接失败并展示 Runtime 契约故障；不发送自由文本替代结果，也不把故障误报为 Provider 或用户输入问题。
- 文件变更成功卡只展示提交后复检的路径、操作和版本事实；no-op 卡明确未审批、未接触文件。Artifact 卡通过稳定 Artifact id 打开，不展示内部 snapshot 路径或模型生成的 URL。
- Shell 结果卡分开展示进程终态、脱敏输出和“执行窗口内观察到”的 Workspace 变化；原始输出长度、完整 manifest、受保护路径名称和 OS 原始错误不进入模型结果。
- `side_effects_may_exist` 表示结果存在未确认副作用，不等于“工具可写”；是否进入事实确认以 outcome/code、对账完整性和安全异常共同决定。

## 5. Public Mode 主流程

```text
创建 Public Session
  -> Agent 在内部 files/ 生成中间文件
  -> 写入仍逐文件审批
  -> Agent 对最终文件调用 publish_artifact
  -> Runtime 创建不可变快照
  -> 右栏展示 Artifact
```

普通写入、中间草稿和临时文件不会自动显示为 Artifact。同一源文件再次发布会产生新版本，不覆盖旧快照。

MVP Artifact 只支持最大 32 MiB 的严格 UTF-8 文本；二进制、压缩包、加密容器或需专用格式解析才能完成敏感扫描的文件不能发布。

## 6. Approval 流程

### 6.1 需要审批

- `write_file`
- `apply_patch`
- `delete_file`
- `run_shell`

文件审批卡必须展示 Runtime 根据当前文件和候选内容生成的完整 Diff。超出展示上限时整个操作拒绝，不提供“截断展示仍审批”。

已有文件候选字节与当前版本完全相同时，Execution Feed 显示 `skipped/no_changes`，不创建 Approval。

Shell 审批卡必须说明命令作用于执行时的当前 Workspace，并展示完整 command、PATH/Toolchain Profile、网络权限、timeout 与固定资源上限。用户批准后 5 分钟未开始执行时，授权失效并需重新审批。

### 6.2 不需要审批

- 四个只读文件工具。
- `publish_artifact`；它必须独占模型响应，但只写 Eidos 本地 Artifact 索引和快照。

### 6.3 Reject

- Reject 原因通过扫描且不超过容量边界后，作为封闭 ToolCall data 返回 Agent；未提供原因时不伪造文案。
- 首次 Reject 后，同一 Run 的后续审批请求自动拒绝；Agent 必须改走无需审批路径，或给出用户可自行执行的策略后结束。
- 获批状态变更成功后，Reject 计数清零。
- 只读调用、模型重规划和失败写入不清零。

### 6.4 敏感内容

- 用户任务、后续补充和 Approval feedback 等自由文本在提交前扫描；疑似凭证命中 `deny` 或 `redact` 时整次提交拒绝。
- 高置信度内容拒绝、文件名/路径硬拒绝不提供 Approval 绕过。
- 模型和 Shell 普通输出中的疑似凭证在展示、持久化或返回模型前脱敏。
- 写入、Patch、Shell 或 Artifact 发布命中 `deny`/`redact` 时整个操作拒绝，不会将脱敏后的内容静默执行或发布。
- UI 只展示规则 ID/版本、规则集版本、等级、命中数和安全位置，不回显原始命中、长度、摘要或哈希。

## 7. 暂停与恢复

### 7.1 waiting_approval

工具尚未执行。下次启动后继续展示原审批，审批不能改变工具参数，也不能提升权限。

### 7.2 明确终止与收尾

- Segment 到限、循环保护或重复敏感 ToolCall 时，Runtime 最多执行一次无工具收尾，给出安全的手动策略后进入 `stopped`。
- 模型流、敏感扫描或确定性协议故障进入 `failed`。
- Runtime 重启、Workspace 身份失效、契约不兼容或副作用结果不确定进入 `interrupted`，并保留核验标记。
- 原 Run 不再等待补充输入，也不存在 Continue 入口；需要补充或核验时创建新 Run。
- “取消、Approve、Reject”只按 Runtime 返回的当前 `allowed_actions` 渲染；提交时 Runtime 仍以最新状态重新判断。

### 7.3 failed

本地预算判断发现不可裁剪输入已经超过可用上下文时，Run 以 `context_input_too_large` 失败，不发送模型请求，也不使 Model Profile 失效。UI 展示估算需求、可用输入预算、输出预留和 safety margin，并引导缩短任务后创建新 Run 或改用更大上下文的 Profile。

### 7.4 stopped

Run 达到 80 Steps 或 120 分钟有效执行时间后进入 `stopped`，不能恢复原 Run。用户可以查看收尾摘要，并基于现状创建新 Run。

## 8. 多 Run 与队列体验

- 用户可以创建和保留多个 Run。
- 任意时刻只有一个 Run 调用模型或执行工具。
- waiting_approval 不占执行槽；审批后追加到队尾。
- 当前 Run 不被新 Run 抢占。
- MVP 支持取消排队 Run，但不支持手动调序和优先级。

## 9. Toolchain Settings

- 默认只使用 macOS 系统工具目录。
- Settings 可检测但不自动启用 `/opt/homebrew` 和 `/usr/local`；用户需逐个确认 Toolchain Profile。
- 已启用根目录被替换时 Profile 自动禁用，不会退化为任意 PATH。
- MVP 不提供用户 Home 工具链或任意目录选择器。

## 10. 关闭应用

- 没有运行中 Run 时正常退出 sidecar。
- 有运行中 Run 时，用户选择等待完成或取消后退出。
- 排队和 waiting_approval Run 持久化；意外中断的非终态 Run 在下一次启动时进入 interrupted。
- 意外中断的运行中 Run 不自动重放工具。

## 11. 启动与页面恢复

- 应用启动时先显示 Runtime 初始化/诊断状态；数据库迁移、契约和安全自检、崩溃对账与队列恢复完成前，不能创建、恢复或审批 Run。
- 同一用户数据目录只允许一个 Eidos Runtime 执行；重复启动激活已有窗口，旧 sidecar 仍在退出时显示有界等待或 `runtime_already_active`，不强制接管。
- Workbench 首次打开或重载时先原子安装当前 RunSnapshot，再从其 Event 水位续接；不得仅靠历史 Event 猜测当前审批和状态。
- pending Approval 摘要必须绑定当前 nonce；完整命令、Diff 和前置条件从权威详情读取，详情过期或被替换时禁用 Approve。
- 列表翻页只透传 Runtime cursor；cursor 失效时从第一页重取，不猜 offset 或排序。旧 UI 遇到明确可忽略的新 Timeline 类型显示安全占位，遇到状态语义不兼容则停止增量并提示升级/重启。
- 存储不可用时 Workbench 进入 Runtime 诊断态，显示安全故障类别和数据目录，允许用户释放空间后显式重新检查；不提供“自动删除旧记录后继续”。
