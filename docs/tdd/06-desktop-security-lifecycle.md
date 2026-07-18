# 桌面端安全与生命周期

版本：v0.4（探索草案）

范围说明：本文描述目标态 Desktop 生命周期。Main/Runtime 通信跨阶段固定使用 [MVP Lite](../mvp-lite.md) 的 stdio JSON-RPC 边界。

MVP Lite 当前实施状态：✅ Electron single-instance lock；✅ 默认 1180×800、最小 800×600 窗口；✅ Renderer 仅通过类型化 Preload 调用 Runtime；✅ Runtime stdout 协议/stderr 日志隔离；✅ Composer 固定在窗口底部、Execution Feed 独立滚动；✅ Run 完成后权威 SessionSnapshot 刷新与安全业务错误提示；✅ 退出时有界 shutdown/terminate。

## 1. Electron Main

职责：

- 启动和停止 Python sidecar，独占其 stdin/stdout/stderr 生命周期。
- 启动最早阶段取得 Electron single-instance lock；第二实例只激活已有窗口，不启动第二 sidecar。
- 实现双向 JSON-RPC：发送 Main 请求、响应 Runtime 主动请求、校验并转发 notifications。
- 执行协议版本、消息大小、request id 和闭合 DTO 校验。
- 调用 macOS 文件夹选择器。
- 响应用户点击，在系统 Terminal 中打开 Workspace。
- 处理窗口关闭、应用退出和 sidecar 异常。

Main 不实现 Agent Loop，不执行 Agent Shell，也不向 Renderer 暴露通用文件系统或进程 API。

## 2. Preload API

禁止：

```ts
window.eidos.invoke(channel: string, payload: unknown)
```

采用类型化 API：

```ts
window.eidos.sessions.create(input)
window.eidos.sessions.rename(sessionId, title)
window.eidos.sessions.delete(sessionId)
window.eidos.runs.create(sessionId, input)
window.eidos.runs.cancel(runId)
window.eidos.runs.getSnapshot(runId)
window.eidos.runs.addUserInput(runId, input)
window.eidos.approvals.get(approvalId)
window.eidos.approvals.approve(approvalId, input)
window.eidos.approvals.reject(approvalId, input)
window.eidos.events.subscribe(runId, handler)
window.eidos.workspaces.chooseFolder()
window.eidos.workspaces.openInSystemTerminal(workspaceId)
window.eidos.artifacts.open(artifactId)
window.eidos.models.create(input)
window.eidos.models.list()
window.eidos.models.status()
window.eidos.models.configure(apiKey)
window.eidos.models.get(profileId)
window.eidos.models.update(profileId, input)
window.eidos.models.testConnection(profileId, userGestureNonce)
window.eidos.models.archive(profileId, input)
window.eidos.models.restore(profileId, input)
window.eidos.toolchains.list()
window.eidos.toolchains.enable(profileId, userGestureNonce)
window.eidos.toolchains.disable(profileId, userGestureNonce)
```

每个方法固定 IPC channel、请求 schema 和响应 schema。Main 再次验证所有 Renderer 参数，不能信任 TypeScript 类型。

所有修改 sidecar 持久状态的方法额外接收由 Renderer 为单次用户意图生成的 `operationId`，Main 将它放入对应 JSON-RPC params；断线重试只能复用同一 method/params/operationId，新的用户手势必须新建 ID。decision nonce、expected version 和 user gesture nonce 仍分别传递。只读 method、notifications、folder picker 与 open Terminal 不持久化 operation，Main 不自动重放后两者。

Pydantic 导出的版本化 schema、协议 fixture 和生成/校验后的 TypeScript contract 约束 Preload/Renderer type 与 runtime validator。Main 必须验证 sidecar 的 closed result/error DTO，分页 cursor 只允许原样透传；`runtime_contract_mismatch` 时停止对应通知流，不把未知字段或原 payload 交给 Renderer。

## 3. Renderer 安全

```text
contextIsolation: true
nodeIntegration: false
sandbox: true
webSecurity: true
```

- CSP 默认拒绝脚本、远程 frame 和任意导航。
- Markdown 禁止 raw HTML 或使用严格 sanitizer。
- 外部链接只允许用户点击后通过系统浏览器打开，并显示目标域名。
- 文件预览不允许执行脚本、宏或任意本地 URL。
- Workspace 文件树可向用户显示 protected 文件名，但 Preload/Main 不得把这些条目注入 Agent Tool 结果或模型上下文。
- Sidecar 错误响应经 Main 字段白名单后返回 Renderer。
- 敏感错误卡只显示 ruleset version、rule id/version、action、命中数和安全字段路径/行号；不显示原值、长度、摘要或哈希。

## 4. Sidecar 启动与通道所有权

Main：

1. 取得 Electron single-instance lock，创建私有 stdin/stdout/stderr pipes 并拉起 sidecar。
2. 立即发送 `initialize`，携带 client/runtime protocol 与 Event contract 版本；初始化完成前不发送业务 method。
3. 只接受当前 child stdout 上的 JSON-RPC envelope。非法 JSON、未知 response id、方向错误或超限消息使当前 sidecar fail closed 退出。
4. 验证 initialize result 属于当前 child，且 protocol/Event contract 与 Main/Renderer registry 兼容，才开放业务 IPC。
5. `mode=diagnostic` 时只开放 status/recheck/shutdown；`mode=ready` 时才开放业务 method 和 notifications。

Sidecar：

- 在打开 DB 前验证状态目录并取得 `runtime.lock` 的独占 OS advisory lock，持有至进程退出。lock fd 在 open 时使用 `O_CLOEXEC` 并复检 `FD_CLOEXEC`，不得通过 spawn file actions、fd duplication 或显式传递进入 guardian、sandbox-exec、Shell 或其他子进程。锁内容的 PID/start identity/instance nonce/protocol version 只用于诊断；不得删锁文件或按 PID 抢占。
- 不创建本地 listener；除继承的 stdin/stdout/stderr 外，不接受其他客户端 attach。
- ready 前的 method gate 必须早于业务 params 解析和 operation 占用；只有 initialize/status/recheck/shutdown 可达。
- stdout 禁止日志；stderr formatter 必须删除 API Key、认证 Header、用户正文和未扫描输出。

Main stdin EOF 或父进程退出后，sidecar 立即停止接收新业务，提交可恢复中断事实、驱动 guardian 清理并退出，由 OS 释放 lock。新 Main 发现 lock 仍占用只做有界等待，随后报告 `runtime_already_active`；锁释放后只能启动新 sidecar 并执行完整恢复，不能 attach 旧 sidecar。

## 5. Event notification 桥接

```text
Runtime committed Events
  -> same-transaction RunSnapshot + through_event_id
  -> Renderer atomically installs snapshot
  -> Runtime JSON-RPC notifications after watermark
  -> Main validated IPC projection
  -> Renderer per-run reducer
```

- Main 只维护当前 sidecar 的一条 stdout 消息流，不为每个 Run 建立额外连接。
- Renderer 首次加载/重载都从 RunSnapshot 的 `through_event_id` 恢复；本地缓存水位不能替代权威 snapshot。
- IPC listener 必须在组件卸载时释放。
- Main 可批量转发高频 model/tool chunks，避免压垮 Renderer。
- Main 对未知但 contract 声明可忽略的 event type 丢弃原 payload，只发送闭合 unsupported-event 占位并推进 id；已知 type 未知 version/非法 payload 停止对应 reducer 并触发一次 snapshot 恢复。发现 event id 缺口时调用 `run/readEvents` 补齐；snapshot 仍未覆盖时显示 contract mismatch，禁止循环。

## 6. Workspace 选择

- 只接受用户通过系统文件夹选择器返回的目录。
- Main 以 `O_DIRECTORY|O_NOFOLLOW` 打开 root，sidecar 通过 fd `fstat` 与 volume resource metadata 保存 `canonical_root_path,volume_uuid,inode,birthtime_ns,last_seen_dev`。`fileResourceIdentifier`/`fileIdentifier` 只能作进程内补充，不能作为跨重启持久身份。
- volume UUID 或可靠 birthtime 不可用时拒绝选择 `workspace_volume_identity_unsupported`。`last_seen_dev` 只用于当前挂载期辅助判断。
- 创建/恢复 Run 和每次工具调用前都重新以 fd 验证路径、目录类型、volume UUID、inode 与 birthtime。消失、权限丢失、symlink、类型或身份变化时 Workspace -> unavailable，所有 pending/approved Approval 永久 invalidated，非终态 Run -> `waiting_user_input/workspace_unavailable`，零模型/工具续接。
- 同路径新身份创建新 Workspace ID；同身份换路径也创建新 Workspace ID，不自动 rebind 或迁移历史。旧身份在原路径精确重现时，只能由用户显式重新选择恢复旧 Workspace；旧 Approval 不复活，Run 仍需显式继续。
- 数据库同时保证 available canonical path 唯一和 available persistent identity 唯一；不可用历史仍可只读查看。
- active root 传给 sidecar 后仍由 Workspace Guard 和 Seatbelt 二次校验。

## 7. 打开系统 Terminal

- MVP 不依赖 `node-pty`，不创建内嵌 Terminal。
- 只能由受信任用户手势调用 `openInSystemTerminal`。
- Main 根据 workspace_id 查询已验证路径，不接受 Renderer 直接提交路径。
- 使用参数数组调用系统打开能力，不构造 Shell 字符串。
- Agent、模型、sidecar API 和 ToolCall 均不能触发该能力。

## 8. 窗口关闭

关闭行为：

- 无 running Run：停止 sidecar 并退出。
- 有 running Run：显示“等待完成”或“取消并退出”。
- 选择等待：窗口保持可见，Run 完成/暂停后再次允许退出。
- 选择取消：按协作式取消规则等待完成，再停止 sidecar。
- queued/waiting Run 留在数据库，下次启动恢复。

不提供“窗口关闭但后台继续执行”。

## 9. Sidecar 异常退出

Main：

- 停止向 Renderer 声称 Run 仍在执行。
- 显示 Runtime disconnected，并禁用新 Run/审批操作。
- 可由用户重新启动 sidecar。

Sidecar 重启后执行崩溃恢复：running -> waiting_user_input/runtime_interrupted；finalizing 使用结构化降级摘要进入 stopped；两者都不自动重放工具。

每个已启动 Shell 另由 Q144 guardian 持有绝对 deadline、双控制通道和进程身份 lease。Main/sidecar EOF、取消或 deadline 触发 TERM -> 2 秒 -> KILL 的受控 PGID/已识别后代清理；UI 不显示“后台仍在运行”。若 guardian 自检失败，Shell capability unavailable，但模型/只读文件工具可按 ready capability 状态继续。

## 10. UI 状态要求

Renderer 必须明确区分：

```text
queued
running
waiting_approval
waiting_user_input
finalizing
succeeded
failed
stopped
canceled
runtime_disconnected
```

Renderer 只能从 snapshot 的稳定排序 `allowed_actions` 渲染 Continue/Cancel/Approve/Reject；不能仅凭 `waiting_user_input` 或本地 Event 猜测。提交后若 Runtime 返回状态竞态，立即重新获取 snapshot。终态 snapshot 不应用实时 notifications；未知 snapshot schema 显示兼容错误，未知 Event schema 先停止 reducer 再 resnapshot。

所有 `*_at` 作为 UTC Unix 毫秒 ApiTimestampV1 接收，Renderer 只负责本地化展示。Approval 倒计时以服务端 `approval_expires_at` 展示，但授权判断只在 Runtime；客户端墙钟、休眠或回拨不能延长授权。

`storage_unavailable` 时 Main 关闭业务 IPC 并显示 health-only 诊断：安全 reason、Eidos 数据根、释放空间和“重新检查/重启”入口。重新检查是用户手势触发的新 sidecar 恢复流程，不是重试原写操作；UI 不提供自动删除、自动恢复 backup 或“忽略后继续”。

Workbench 布局额外满足：

- 左侧项目按最早保留 Session 的 `createdAt` 倒序，组内 Session 按 `createdAt` 倒序；向已有项目调用现有 `session/create(workspaceRoot)` 不改变项目排序。项目行以 `aria-expanded` 按钮和开/关文件夹图标控制本地折叠状态，右侧加号复用同一创建 RPC，不新增项目 API。
- 左侧导航标签、项目名称和任务标题使用 `font-weight: 400`。任务项使用单行紧凑布局，标题与状态标识同排，不再渲染第二行状态文字。Renderer 消费 Session DTO 的 `taskStatus`：未读 `completed` 为小绿色实心点，`in_progress` 为小转圈，`failed` 为小红色实心点，`new|canceled` 和已读 `completed` 不显示彩色点。点击完成任务记录已读；新的活动 Run 清除已读标记。状态图形提供可访问名称；转圈在 `prefers-reduced-motion` 下停用动画但保留状态语义。
- Workspace 展开状态完全由 `SessionSidebar` 本地维护，不触发 IPC。Session 选择先更新独立的 navigation selection；重复选择当前 Session 直接返回，切换选择通过 `SnapshotReadCoordinator` 后台读取并只接受最新 token 的闭合快照。读取期间保留旧内容且不得把 snapshot refresh 纳入全局 `disabled` 状态；失败恢复当前 snapshot 的选中态。Run 完成后的权威 refresh 同样不使导航或 Composer 整体降 opacity。
- 左下角固定一个齿轮按钮和当前 `Provider · model · configured` 摘要。点击后打开 Settings；第一版只渲染 `model/list` 与 `model/configure` 的模型目录和 API Key 表单，不显示尚未进入阶段清单的占位设置。
- 新任务首次 `run/start` 前，Composer 操作栏在“开始”按钮左侧渲染 Runtime 返回的模型选项。默认选中 `defaultModelId`；无可用模型时禁用“开始”并链接到 Settings。提交成功后选择器锁定，Reducer 不允许 Event 或本地状态切换该 Run 的 `modelId`。
- Session 内容区 header 只以低于页面标题的字号渲染权威 `title` 和紧邻标题的紧凑三点按钮。标题 `contextmenu`、三点按钮与左侧任务按钮 `contextmenu` 打开同一组“编辑标题、删除任务”动作；左侧右键菜单支持 `Shift+F10`，重命名成功后以 Runtime result 更新，删除先显示不可逆确认，`session_has_active_run` 时引导先结束任务。
- 删除成功后清理选中态并导航到剩余第一个任务或空状态；不得调用文件 API。header 不渲染 Developer Preview、Workspace 名称/绝对路径和通用审批/敏感扫描说明；Workspace 路径只在左侧项目辅助信息中显示，具体安全范围由 Approval 卡片展示。
- Renderer 在 `:root` 固定 `--font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif` 与 `--font-code: ui-monospace, "SFMono-Regular", SFMono-Regular, Menlo, Monaco, Consolas, monospace`；字号 token 为 `11/12/13/13/14/16px`（xs/caption/sidebar/code/body/title），代码、正文和标题行高分别为 `20/22/24px`。Session header、Feed、Composer、工具输出和 Diff 只引用这些 token。
- `assistant_message` 交给同步 CommonMark Renderer 生成 React element；`user_message` 保持纯文本。Renderer 不使用 `dangerouslySetInnerHTML`，启用 `skipHtml`，并将 Markdown `a/img` 替换为不导航、不加载资源的文本组件；流式 item 每次以当前已提交安全文本重新解析，未闭合语法不得阻塞后续 delta。工具、Approval 和 Run 状态不经过 Markdown。
- `ExecutionFeed` 按 Run、再按 `user_message` 分段；一段中存在结构化工具 Item 时，以最后一个工具 ordinal 为边界，将此前 assistant Item 与工具 Item 投影为过程、此后 assistant Item 投影为最终回答。过程用原生 `details/summary`：非终态默认展开并用 Run `startedAt/createdAt` 到当前时间显示“正在处理”，终态以 `completedAt` 固化耗时并通过状态 key 重挂载为默认折叠；无工具段不生成过程组。尚无 assistant/tool Item 的活动段显示 CSS 渐变“正在思考”，`prefers-reduced-motion` 下退回静态文本。`succeeded` 不渲染 Run notice，其他终态、等待输入和副作用不确定仍渲染安全提示。
- 工具 Item 在过程组内使用可折叠摘要。`run_shell` 从 RunSnapshot 展示白名单中的 `argumentsJson.command|cwd|timeoutSeconds` 与 ToolResult envelope `data.stdout/stderr/exitCode` 构建 Shell 卡；完整命令和输出使用代码字体，双流为空时显示“无输出”，状态只由 Item status 与 exit code 推导。读取、搜索、编辑和删除使用工具名与参数白名单生成本地化摘要，未知或非法 JSON 安全退回结构化状态，不渲染任意 HTML。

审批卡必须展示：

- 工具名和完整目标。
- Runtime 生成的完整文件 diff 或完整 Shell command；不存在截断 Diff 审批模式。
- Workspace write、外网 host、localhost 等权限。
- 请求创建时间和当前文件版本状态。
- 文件 size、encoding/BOM、父目录稳定性，以及 write/apply 引用的读取证据行范围。
- Approve/Reject，不提供 Edit then Approve。

Shell 卡额外展示：

- “将在执行时的当前 Workspace 上运行，不绑定审批时快照”。
- 完整 PATH、Toolchain Profile 及版本、环境模板版本、timeout、网络权限、64 进程/256 fd/2 GiB RSS/1 GiB 单文件/2 GiB allocated growth 限制。
- approved 后的 5 分钟倒计时；过期时卡片标记 invalidated，不自动再审批。

`approval_diff_too_large` 在创建审批卡前已返回，Renderer 只显示“请拆分修改或使用受审批 Shell”，不能在客户端截断后自行构造审批卡。

文件读取错误必须区分 `file_too_large_for_read_file`、`invalid_line_range`、`line_too_large`、`binary_file_not_supported` 和 `unsupported_text_encoding`，并给出 range、受审批 Shell 或 P1 能力的对应引导。

`tool_call_skipped/no_changes` 渲染为非审批信息卡，不渲染空 Diff。`workspace_changed`/`changed_during_scan_count` 显示为“结果可能不是 Workspace 快照”，不将跳过变化文件显示为零匹配。

write/apply/delete success 卡只从闭合 data 展示提交后 path/change/hash/size/postcondition；不得显示 Approval、intent、临时路径或 OS metadata。Artifact success 卡通过 canonical artifact_id 调用受控 open API，不接受 ToolResult 提供 URL，也不显示 snapshot_path。

Shell 输出视图按 `chunk_index` 处理 stdout/stderr 交错，识别中间省略和 `tail_replay`，不在 Renderer 端重新计算截断。Workspace change 卡展示分类计数、manifest 完整性和前 200 个安全路径，文案始终使用“执行窗口内观察到”。模型结果只使用脱敏 observation bytes；Renderer 不把内部 raw byte counts、完整 manifest 或敏感路径拼入 ToolResult 详情。

ToolResult error 卡按 outcome/code 和 code 专属 data 本地化；不能渲染 generic message/details/cause。Reject 卡可显示已扫描的可选 user_feedback；unavailable 和未启动 interrupted 不伪造 retry 建议。`side_effects_may_exist` 只显示“可能有未确认副作用”，事实确认按钮与 gate 必须读取 Run 的 reconciliation_required，不能直接由该 flag 推导。

Artifact UI 在 MVP 只接受 text/markdown/json/csv/html/code；不支持格式显示安全拒绝而不创建卡片。快照 hash 失配时标记 corrupted 并禁用正文预览。

Renderer 不实现 raw reasoning 专用 UI；只渲染 `assistant_progress`、`final_answer` 和 reasoning token 用量元数据。

Model Profile UI 必须区分未测试、测试中、已通过、已失效、失败和 Archived 状态。用户必须显式选择 Responses 或 Chat Completions。Test Connection 只能由用户手势触发，不接受任务或自定义 probe 文本；结果展示 snapshot/Gateway/model request/Tool Schema Dialect version、认证、模型、HTTP/SSE、`tool_choice`/`parallel_tool_calls` 控制、`strict=false` schema 模式、ToolCall/ToolResult 分片与无状态续接、usage、output token parameter 和安全错误分类。任一必需能力未通过、失效或 Archived Profile 禁用 Session 选择和新 Run 创建，既有 Run 仍展示其固化 snapshot。

Profile 编辑表单不回显 API Key，只提供保持、替换或在 `none` 认证下清除。连接/协议字段修改前提示“保存后需要重新 Test Connection”；纯名称修改不显示失效提示。Archive 使用确认卡且无 Delete 操作，恢复后根据 snapshot 有效性决定是否可选。

Endpoint 表单接受任意 HTTP(S) 地址类别，拒绝 URL 内嵌凭证和已包含固定 endpoint 的 base_url，并展示规范化 API root、Origin 和根据 wire API 生成的最终 URL。HTTP 显示 API Key 与任务内容非加密传输警告；HTTPS 证书错误只显示 `model_tls_validation_failed`，没有忽略错误按钮。认证只显示 bearer/api_key_header/none，参数编辑器明确标记 Runtime 保留字段；context/output 上限由用户输入，不根据模型名自动回填覆盖。

运行时 `model_capability_drift|model_context_limit_mismatch` 显示为终态模型兼容性错误，Profile 同时变为已失效，并引导编辑或重新测试后创建新 Run。

模型流中断时，已显示的 assistant_progress 保留并显示“输出未完成”；Renderer 不把它并入最终回答，也不渲染部分 ToolCall 参数。

首 delta 前 HTTP/SSE 重试时，Renderer 根据 Event 显示 `正在重新连接 x/y`。首 delta 后不得显示重连状态，而应展示流已中断和继续入口。

Run 用量区域分别显示 Provider 已报告 usage、总 Attempt 数和 usage unknown Attempt 数；unknown 不显示为 0，也不据此生成精确费用。密钥轮换只显示实际使用的 credential revision，不显示或比较密钥内容。

`context_input_too_large` 错误卡显示 estimated required、usable input budget、request output reserve 和 safety margin，引导缩短任务或选择更大上下文 Profile 后创建新 Run；不提供恢复当前 Run 的入口，也不把 Profile 标为失效。

`model_output_truncated|model_output_blocked|model_output_limit_exceeded` 分别显示“达到模型输出上限”“Provider 内容过滤”“Eidos 输出资源上限”；只保留 incomplete progress，不渲染或审批该响应 ToolCall。`runtime_contract_unsupported` 同时覆盖旧 model request/tool contract 不可安全恢复，只允许取消或复制原任务创建新 Run。

Model Profile 页面说明 Eidos 使用 stateless 请求且 Responses 设置 `store=false`，但不承诺第三方零留存；链接到用户所选 Provider 的隐私条款由用户自行确认，Eidos 不抓取或声称验证其政策。

模型认证或确定性配置错误时，Renderer 显示 failed 错误卡和“更换/修复 Model Profile 后创建新 Run”，不提供恢复原 Run 的按钮。

瞬时模型故障重试耗尽或连续两次模型协议错误时，Renderer 显示对应 pause reason，并允许用户稍后继续、补充指令或取消。

ToolCall 已在当步暴露但执行前能力退化时，Execution Feed 渲染非审批的 `unavailable/tool_unavailable` 结果卡，并明确“未执行、零副作用”；不得把它显示成模型调用了未知工具。ToolResult UI 从 canonical envelope 的 `outcome/code/summary/data` 白名单读取，不能使用内部 `result_text` 拼接另一份模型或用户结论。

`tool_result_contract_violation` 使用独立 Runtime 故障卡：展示安全的 scope=tool|global、tool name（若有）、contract/build version、真实 ToolCall 终态和 `side_effects_may_exist`，不显示 projector 原始异常或 result_text。该 Run 没有继续入口；若可能存在副作用，引导复制任务创建新 Run并先做只读核验。Settings/health 展示 active quarantine；普通重启不提供“清除并继续”按钮。

Execution Feed 的工具结果状态只来自 immutable base envelope；Renderer 可按 outcome/code 本地化。`summary` 仅作模型提示，不作为 UI 状态源。Context projection 的 `model_content_truncated` 与 omitted metadata 可以显示“模型上下文已省略部分结果”，但不得覆盖工具自身 complete/truncated/stop_reason。

敏感内容 UI 契约：

- Create Run 和 user-input 在提交失败时保留本地编辑态，但不将原文写入 Renderer 持久化缓存、遥测或 Main 日志。
- 敏感 ToolCall 不渲染 Approval 卡，只渲染安全拒绝 Event。
- 连续两次敏感 ToolCall 后显示 `repeated_sensitive_tool_input`，用户可补充无敏感指令后创建新 Segment。
- `sensitive_scan_limit_exceeded`、`sensitive_scan_incomplete` 和 `sensitive_scan_failed` 必须区分于文件本身敏感，不得统一显示为“权限拒绝”。
- Reducer 必须接受保留 event id/type 的 `content_unavailable` 安全载荷，继续推进回放水位而不尝试渲染原 payload。
- Redaction Service unavailable 时禁用新 Run、用户补充、工具详情和 Artifact 内容读取，只显示诊断和安全恢复提示。

## 第三期 Settings 与扩展 Feed

Settings 增加 Plugins、Skills、MCP Servers 三个受控区域：系统目录选择后调用 `plugin/import`，支持启停/移除、Skill metadata 与 MCP 状态展示，不出现市场、URL 安装或命令编辑器。

启用 MCP Server 的 consent 必须不截断展示 executable、逐项 argv、Plugin ID/version/hash、env names 与 permission profile；env value 永不进入 Renderer。Execution Feed 展示 Plugin、Server、映射后工具名、审批、状态和耗时，只消费闭合 Tool provenance/ToolResult/Event，不渲染内部安装路径、原始 stderr 或协议异常正文。
