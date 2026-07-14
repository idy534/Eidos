# 功能需求

版本：v0.4

范围说明：本文件的 P0 是完整目标态优先级。第一期必须项和延期项以 [MVP Lite](../mvp-lite.md) 为准。

MVP Lite 当前实现状态：✅ F001 桌面骨架；✅ Session/Run/Item/ToolCall 最小事实；✅ 串行 Agent Loop；✅ `list_files/read_file/search_text`；✅ 模型流与基础 Execution Feed；✅ Cancel 与异常 `interrupted`；✅ 本机私有 DeepSeek 配置。文件写入审批与 `run_shell` 仍在 L2/L3，不能把这些 ✅ 外推为本文件完整目标态条目已全部满足。

## 1. P0 功能清单

实施标记只表示对应完整功能已经通过当前范围的自动化与实机验收。F019 尚未完成，但其 Seatbelt 静态策略、路径参数和基础 fail-closed smoke test 已作为 MVP Lite 前置风险验证通过；审批、输出、manifest、资源监管和 ToolCall 主链路仍待实现。F003/F004 也尚未整体完成，但其私有数据目录、SQLite Session 元数据和 `session/create|list|read` Runtime/Main Client 链路已通过跨进程重启测试；文件夹选择 UI、Workspace 持久身份和 Run/Item 仍待实现。

| 编号 | 功能 | 要求 |
|---|---|---|
| F001 | ✅ macOS 桌面应用 | 启动 Electron 应用并拉起本地 sidecar |
| F002 | 默认 Agent | 首次启动创建默认 Eidos Agent；不提供多 Agent UI |
| F003 | Eidos Home | 初始化并校验 `~/.eidos` 权限与目录结构 |
| F004 | Workspace Mode | 选择本地目录并创建 Workspace Session |
| F005 | Public Mode | 不选择项目目录也能创建 Session 和 Run |
| F006 | Model Profile | 配置多个 OpenAI-compatible Profile；显式无任务数据能力测试通过后才可选择；Run 固化版本化配置与能力快照 |
| F007 | Run 队列 | 多 Run 可保留；全局单执行器；持久化 FIFO |
| F008 | Execution Segment | 单段最多 20 Steps/30 分钟；恢复创建新 Segment |
| F009 | Run 硬上限 | 最多 80 Steps/120 分钟；到限进入 `stopped` |
| F010 | Agent Loop | 模型调用、ToolCall、观察结果严格串行 |
| F011 | Tool 批次校验 | 只读工具可批量；有副作用工具独占；非法组合零执行 |
| F012 | list_files | 列出 active root 内受控文件结构 |
| F013 | read_file | 分级读取 active root 内普通文本文件 |
| F014 | read_file_range | 按行读取局部内容 |
| F015 | search_text | literal、大小写不敏感的受控搜索 |
| F016 | write_file | 创建单个文件或完整生成单个文件；审批并展示 diff |
| F017 | apply_patch | 修改单个已有文件；审批并展示 diff |
| F018 | delete_file | 删除单个普通文件；禁止目录、递归、通配符与批量 |
| F019 | run_shell | macOS Seatbelt `workspace_write` Shell；每次调用审批 |
| F020 | publish_artifact | 显式发布单个现有文件，生成不可变快照 |
| F021 | Approval | 记录请求参数、权限、diff/命令与用户决定 |
| F022 | 文件版本复检 | 审批后执行前验证 hash、目标不存在或删除前版本 |
| F023 | Reject 恢复 | Reject 返回 Agent；连续 2 次后等待用户输入 |
| F024 | 事实确认屏障 | 写或 Shell 失败后先只读核验，再允许下一次副作用 |
| F025 | waiting_user_input | 用户补充信息后继续同一 Run 并重新排队 |
| F026 | 崩溃恢复 | 不自动重放；运行中 ToolCall 标记 interrupted |
| F027 | Cancel | 排队/等待立即取消；运行中协作式取消 |
| F028 | Model Stream | 实时输出、分块持久化、保存最终响应 |
| F029 | Execution Feed | 展示消息、工具、审批、状态、错误和 Artifact |
| F030 | Event Timeline | 所有关键事件与状态变更同事务持久化 |
| F031 | Context Budget | P0 提供确定性的有界上下文裁剪 |
| F032 | Redaction | 截断、展示、返回模型与持久化前按确定性规则分级处理 |
| F033 | 有限重试 | 已验证 WebSocket 首 delta 前最多重连 5 次并降级 HTTP(S)，HTTP(S) 瞬时错误最多 2 次；只读瞬时错误 1 次 |
| F034 | Finalization | 硬停止前一次最长 60 秒、无工具的收尾调用 |
| F035 | 系统 Terminal | Workspace 中只提供用户点击打开系统 Terminal |
| F036 | 模型流中断恢复 | 首 delta 后中断不重试，保留未完成进度并等待用户输入 |
| F037 | 模型配置错误 | 认证、模型不存在和确定性请求错误直接终止 Run |
| F038 | 模型瞬时故障 | 首 delta 前重试耗尽后暂停，同一 Step 内 Attempt 不重复计步 |
| F039 | 模型协议纠正 | 连续两次无效响应后暂停；合法响应清零连续错误计数 |
| F040 | Durable Intent | 副作用执行前持久化意图，恢复时对账而不重放 |
| F041 | 代理审计 | 只记录域名级元数据，不记录请求内容或解密 TLS |
| F042 | Host 校验 | 精确 host/port、解析 IP 校验、拒绝通配符和跨 host redirect |
| F043 | Hardlink 防护 | 写工具拒绝多链接文件；Writable Shell 前置扫描 fail closed |
| F044 | 文件元数据 | 修改已有文件保留 mode、ACL、xattr；新文件默认 0644 |
| F045 | 敏感规则 | 版本化 `deny`/`redact`/`allow_with_audit`；MVP 不使用模型判断 |
| F046 | 入口扫描 | 用户输入、文件/搜索、Shell/模型输出和持久化入口统一扫描 |
| F047 | 副作用阻断 | 写/Patch/Shell/Artifact 敏感命中整个拒绝，不静默改写后执行 |
| F048 | 扫描失败关闭 | 扫描失败、超时、超限或编码无法安全处理时不释放正文 |
| F049 | 敏感重试暂停 | 模型连续两次提出敏感 ToolCall 后等待用户输入 |
| F050 | 读取分级 | read_file 完整/head+tail/range 按固定字节阈值切换 |
| F051 | 范围读取 | 1-based 闭区间；2,000 行/256 KiB 上限；不返回半行 |
| F052 | 文本编码 | 仅严格 UTF-8/可选 BOM；二进制与其他编码分类拒绝 |
| F053 | 稳定搜索 | 单行 literal、ASCII 不区分大小写、稳定排序和受控 preview |
| F054 | 受控文件树 | 有界深度/条目、不跟随 symlink、隐藏敏感条目 |
| F055 | 写入/Diff 容量 | write/patch/候选文件和审批 Diff 均有硬上限 |
| F056 | 目录边界 | write 不创建父目录；MVP 目录操作使用受审批 Shell |
| F057 | 完整覆盖证据 | 仅完整读取的 <=256 KiB 同版本文件可被 write_file 覆盖 |
| F058 | Patch 读取证据 | hunk 原文范围必须由当前 Run 同 hash 的读取结果覆盖 |
| F059 | 受控删除 | 只删除可展示完整 Diff 的 <=512 KiB 普通 UTF-8 文件 |
| F060 | 统一排除策略 | 安全排除不可绕过；固定性能排除；MVP 不解析 `.gitignore` |
| F061 | 单文件一致读取 | 并发变化时丢弃单文件结果并有界重试；无 Workspace 快照 |
| F062 | 严格 Unified Diff | 单文件、路径一致、零 Git 扩展、零 offset/fuzz、保留统一换行 |
| F063 | 写入换行 | 新文件 LF；覆盖显式匹配原 LF/CRLF；mixed 拒绝 |
| F064 | 文件 no-op | 候选字节相同时 `skipped/no_changes`，零 Approval/intent/文件接触 |
| F065 | Shell 审批时效 | 不绑定 Workspace 快照；参数/环境精确绑定；批准后 5 分钟过期 |
| F066 | Shell 变化 Manifest | 执行前后对账文件变化；隐藏敏感名；不声称因果/事务/回滚 |
| F067 | Toolchain Profile | 系统根默认；Homebrew/本地根用户启用；真实 Home 工具链禁止 |
| F068 | Shell 资源限制 | 进程、fd、内存、单文件与磁盘增长上限 fail closed |
| F069 | Shell 有界输出 | 双流脱敏、合并、交错序号、stderr 优先 head+tail 和慢订阅者回放 |
| F070 | 文件名称身份 | 使用实际目录项名称/文件系统语义，禁止静默 case/Unicode 规范化 |
| F071 | 文件元数据补全 | 仅当前用户所有文件；保留 gid/flags；immutable/append-only 拒绝 |
| F072 | 稀疏文件计量 | 工具容量按逻辑字节，Shell 磁盘增长按 allocated blocks，同时记录两者 |
| F073 | Artifact 文本边界 | 只发布 <=32 MiB 严格 UTF-8 不可变快照，不发布无法完整扫描的容器 |
| F074 | Profile 生命周期 | 支持编辑和 Archive/恢复，不物理删除；连接配置变化使能力快照失效，历史 Run 保持固化快照 |
| F075 | Profile 凭证 | 每个 Profile 独占不回显的 API Key 槽位；固定 bearer/api_key_header/none 认证，不支持任意 Header |
| F076 | Model Endpoint | base_url 地址类别不受限但仅 HTTP(S)、无内嵌凭证、无跨 Origin Redirect；HTTPS 不可绕过证书校验 |
| F077 | Provider 参数 | 允许有界扩展参数透传，禁止覆盖 Runtime 核心字段或注入传输层配置 |
| F078 | Model 容量声明 | 用户必填 context/output token 上限；不按模型名推断；明确不匹配使 Run failed 并使 snapshot 失效 |
| F079 | 凭证轮换 | Run 不固化密钥；既有 Run 每次模型调用读取 Profile 当前凭证，但保持创建时非密钥配置与能力快照 |
| F080 | 模型传输降级 | WebSocket 是可选优化；瞬时失败使当前 Run 粘滞 HTTP(S)，明确不支持时按 snapshot 抑制后续 WebSocket，不影响 Profile 可选性 |
| F081 | 模型请求周期 | 一个 Step 的初始请求、重试、降级和退避共享 10 分钟 deadline；首 delta 后禁止重放 |
| F082 | 模型尝试透明度 | 每次网络发送独立记录 Attempt；已报告 usage 累计，未知 usage 明示，不假设服务端幂等 |
| F083 | 上下文预检 | 使用版本化 UTF-8 字节与固定开销公式估算；发送前必须满足输入、输出预留和 safety margin 总预算 |
| F084 | 本地输入超限 | 不可裁剪输入超限时零 Provider 请求并以 `context_input_too_large` 终止 Run，不失效 Profile snapshot |
| F085 | 请求契约版本 | 序列化、预算、传输和 timeout 语义版本化；新版本重测 Profile，既有 Run 保持创建时版本 |
| F086 | 双 wire API | Profile 显式选择 Responses 或 Chat Completions；不自动推断、跨协议回退或按厂商分支 |
| F087 | Endpoint 构造 | base_url 是 API 根；Adapter 结构化追加固定 endpoint，拒绝已含 endpoint 的输入 |
| F088 | usage 契约 | 完整响应必须报告合法 usage；Chat 固定请求流式 usage，缺失不补查、不估算、不填零 |
| F089 | ToolCall 归一化 | 内部 UUID 与 Provider call ID 分离；严格归并分片，缺失/冲突 ID 零 ToolCall |
| F090 | ToolCall 流上限 | 单响应最多 16 calls，单 call 1 MiB、合计 2 MiB，并限制 delta 数、名称和 JSON 结构 |
| F091 | 模型输出上限 | 可见文本、reasoning、单 Event 和总流量均有硬上限；超限保留安全进度并暂停，不重试 |
| F092 | 完成语义 | 仅完整协议终态可完成；token 截断和内容过滤不执行 ToolCall并进入 waiting_user_input |
| F093 | 无状态模型请求 | 每个 Step 从本地状态重建上下文；不依赖 Provider conversation、previous response 或服务端 history |
| F094 | 输出字段协商 | Responses 固定字段；Chat 只在 Test Connection 有界协商并把结果固化到 snapshot |
| F095 | 工具控制字段 | 工具集非空的普通/纠正请求固定 auto + parallel，空集固定 none；probe 两阶段受控；Finalization 固定无工具 |
| F096 | 非 strict 生成 | 两种 wire 显式 `strict=false`；Provider strict 不替代 Runtime 本地校验 |
| F097 | 封闭工具输入 | 根/嵌套/数组 object 递归禁止未知字段；MVP 无自由 map |
| F098 | 生效参数 | Runtime 只补齐 schema 静态默认值，生成唯一 effective arguments 用于审批、审计和执行 |
| F099 | 工具契约版本 | Run 固化可观察工具语义；升级不静默改写旧 Run，也不能降低当前安全底线 |
| F100 | Tool Schema Dialect | 内置 function tool 只使用固定受控 JSON Schema 子集；Dialect 扩展后 Profile 必须重测 |
| F101 | 当步工具集 | 每个 Step 确定性生成 available tool set；同一逻辑请求的重试完全一致 |
| F102 | 工具可用性复检 | 调用未暴露工具是协议错误；已暴露工具后续不可用返回零副作用 observation |
| F103 | Canonical ToolResult | 进入后续模型上下文的已创建 ToolCall 恰好有一个版本化、协议无关的结果 envelope |
| F104 | ToolResult 关联 | 内部 UUID 不进入模型内容；Adapter 只以 Provider call ID 关联相同 canonical JSON 结果 |
| F105 | 结果序列化 | ToolResult 顶层 schema 与 canonical UTF-8 JSON 版本化；双 Adapter 和重放使用确定字节规则 |
| F106 | 安全摘要 | summary 只使用有界固定模板，不携带路径、输出、用户反馈或原始错误 |
| F107 | 结果 code | success 使用 null；其他 outcome 使用按工具契约封闭的稳定 code，不透传 Provider/OS 原值 |
| F108 | 封闭结果 data | data 始终是按 contract/tool/outcome/code 选择的递归封闭 object；字段容量和顺序明确 |
| F109 | 结果契约故障 | 无法生成合法 ToolResult 时零 fallback、零模型续接，Run 失败并隔离故障能力 |
| F110 | 结果事实与投影 | ToolCall 保存唯一不可变 base result；每 Step 冻结只会减少内容的有界投影 |
| F111 | 文件树结果 | list_files 使用稳定闭合条目、计数和明确工具截断语义；敏感隐藏项不进入计数 |
| F112 | 文件读取结果 | read_file 分离完整性、脱敏、源字节省略、读取证据和上下文投影状态 |
| F113 | 范围读取结果 | read_file_range 永不授予完整读取资格；空结果、截断原因和下一行语义确定 |
| F114 | 搜索结果 | search_text 使用稳定有界 matches、资源计数和长匹配 preview 裁剪；敏感文件不进入计数 |
| F115 | 文件变更结果 | write/apply/delete 成功只返回提交后复检的目标事实；内部审批、意图和临时文件不进入模型结果 |
| F116 | No-op 结果 | 相同候选字节返回最小 skipped/no_changes 结果，明确零审批、零意图、零文件接触 |
| F117 | Artifact 发布结果 | 发布成功返回稳定 Artifact 身份、版本和快照事实，不暴露内部快照路径或不稳定 URL |
| F118 | Shell 终态结果 | 已启动 Shell 返回有界进程终态、脱敏双流 observation 和安全 Workspace 对账；完整日志与 manifest 留在审计层 |
| F119 | Shell 错误映射 | 退出、超时、资源限制、中断、输出和 manifest 故障使用固定 outcome/code 与确定优先级 |
| F120 | 副作用不确定性 | side_effects_may_exist 只表示结果尚未完整确认的物质性副作用；事实确认由状态机结合结果判定 |
| F121 | 共享非成功结果 | Reject 可把已扫描、有界的用户原因返回 Agent；不可用和未启动中断不附加内部恢复细节 |
| F122 | ToolResult 错误边界 | 模型错误结果不提供通用 details/message/cause，只允许 code 专属的封闭安全字段 |
| F123 | 只读错误事实 | 文件或单行容量超限返回恢复必需的安全边界；其他只读错误不返回正文、证据或动态内部详情 |
| F124 | 变更错误事实 | Patch/Diff/候选容量错误只返回必要有界事实；版本冲突与 Artifact 错误不把动态 hash 当作读取证据 |
| F125 | 结果数值兼容 | ToolResult 整数跨 Python/JavaScript 精确一致；超出可表达范围时安全失败，不发送失真结果 |
| F126 | Canonical Unicode | ToolResult 使用版本化、跨实现一致的 Unicode 与转义规则，不静默规范化用户内容 |
| F127 | 安全迁移 | 数据库只向前迁移；迁移前保留一致可验证备份，失败时不进入业务 Runtime |
| F128 | Shell 异常清理 | Main 或 sidecar 异常退出后，有界清理受控 Shell 进程组和已识别后代，不把 Agent Shell 留作后台服务 |
| F129 | Ready 屏障 | Runtime 完成状态、契约、安全自检与恢复后才开放业务 API 和调度；此前仅提供安全 health |
| F130 | Workbench 一致启动 | Run 页面从同一事实快照与 Event 水位启动，重载不漏状态或把旧审批当作当前事实 |
| F131 | Workspace 持久身份 | Workspace 移动、替换或身份不可验证时不继承旧授权；恢复必须由用户显式选择 |
| F132 | 唯一执行权 | 同一用户状态目录任一时刻只有一个 sidecar 可以迁移、恢复、调度或执行 |
| F133 | 服务端可执行动作 | UI 操作由服务端当前状态事实决定；不可恢复 Run 不显示或接受继续操作 |
| F134 | 事实确认 episode | 每次新副作用不确定性都要求之后至少一次成功只读观察，旧观察不能解除新的确认屏障 |
| F135 | 写 API 幂等 | Renderer 写操作在断线、超时和重启重试时返回原提交结果或明确不确定，不重复产生领域状态或 Event |
| F136 | 闭合 API/IPC | HTTP、Preload 与 Renderer 使用同源闭合 DTO；未知字段、非法分页和 Runtime 契约不匹配安全失败 |
| F137 | Event 前向兼容 | Timeline 新增可忽略事件不阻断旧 UI；状态语义不兼容时由版本握手和 snapshot 恢复阻断误解释 |
| F138 | 统一时间 | 持久化/API 时间统一 UTC 毫秒，运行时预算和 deadline 不受系统墙钟回拨延长 |
| F139 | 存储故障恢复 | 状态无法可靠提交时停止业务且不虚报成功；释放空间后先校验、对账再 ready，不自动删除或重放副作用 |

## 2. ToolCall 组合规则

| 类型 | 工具 | 同响应数量 | 审批 |
|---|---|---:|---|
| 只读 | list_files/read_file/read_file_range/search_text | 1..N，按声明顺序串行 | 否 |
| Workspace 副作用 | write_file/apply_patch/delete_file | 必须唯一 | 是 |
| Shell 副作用 | run_shell | 必须唯一 | 是 |
| Eidos 本地副作用 | publish_artifact | 必须唯一 | 否 |

其他规则：

- 模型必须先观察读取结果，再在下一 Step 中提出写入或 Shell。
- 非法组合在执行前整体拒绝，一个 ToolCall 都不执行。
- 合法只读批次中单项失败不阻断后续项；取消、总超时和安全异常除外。
- 明确删除必须优先使用 `delete_file`，但获批 Shell 产生的间接删除不属于沙箱禁止项。

## 3. 时间与重试限制

| 项目 | 默认 | 上限/规则 |
|---|---:|---|
| Segment Steps | 20 | 固定 |
| Segment 有效时间 | 30 分钟 | 固定 |
| Run 总 Steps | 80 | 硬上限 |
| Run 总有效时间 | 120 分钟 | 硬上限 |
| Finalization Call | 60 秒 | 无工具 |
| run_shell | 120 秒 | 单次最大 600 秒 |
| Shell Approval 授权时效 | 5 分钟 | 从 approved_at 到开始执行 |
| Shell 前/后 manifest | 200,000 项 / 30 秒 | 前置超限不启动；后置不完整进屏障 |
| Shell 进程/fd | 64 / 256 | 固定 |
| Shell 内存 | 2 GiB | 进程树聚合 RSS |
| Shell 单文件/净磁盘增长 | 1 GiB / 2 GiB | 磁盘增长按 allocated blocks |
| 文件/搜索工具 | 10–15 秒 | 工具固定上限 |
| read_file 完整正文 | 256 KiB | 256 KiB..2 MiB 返回最多 256 KiB head+tail |
| read_file_range | 2,000 行 / 256 KiB | 不返回半行 |
| search_text 结果 | 默认 100 | 最大 500 |
| list_files | 默认深度 2 / 500 项 | 最大深度 5 / 2,000 项 |
| write_file content | 512 KiB | 单次参数上限 |
| apply_patch | 256 KiB / 5,000 行 / 200 hunks | 任一先到即拒绝 |
| 文件审批 Diff | 512 KiB / 5,000 行 | 不允许截断审批 |
| Shell 输出持久化 | stdout 768 KiB / stderr 512 KiB / 合计 1 MiB | stderr 优先，流内 head+tail |
| 候选文件 | 32 MiB | 超限改用受审批 Shell |
| 单文件敏感扫描 | 32 MiB | 超限不返回正文 |
| 单次搜索扫描 | 256 MiB / 15 秒 | 可返回已完成整文件扫描的安全结果 |
| 模型请求周期 | 10 分钟 | 含建连、流式、全部重试、退避和传输降级 |
| 模型建连/首 delta/流空闲 | 15/180/120 秒 | 每次 Attempt，且不得越过请求周期 deadline |
| WebSocket/HTTP(S) 重试 | 5/2 次 | 仅首个 delta 前；WebSocket 耗尽后降级原 endpoint 的 HTTP(S) streaming |
| 模型可见文本 | 64 KiB..4 MiB | `CLAMP(request_max_output_tokens*16,64 KiB,4 MiB)` |
| discarded reasoning | 2 MiB | 只计数后丢弃，超限终止流 |
| 单 Event/总模型流 | 1 MiB / 8 MiB | 按协议解码后 payload 字节 |
| ToolCall 响应批次 | 16 calls / 单个 1 MiB / 合计 2 MiB | 另受各工具 schema 更小上限约束 |
| 只读工具重试 | 1 次 | 仅瞬时错误 |
| 写/Shell 重试 | 0 | 必须先确认事实 |

排队、waiting_approval 和 waiting_user_input 时间不计入有效执行时间。

## 4. P1/P2

| 优先级 | 功能 |
|---|---|
| P1 | 内嵌 Workspace Terminal |
| P1 | Edit then Approve |
| P1 | 文件写入前快照与文件级恢复 |
| P1 | 智能摘要式上下文 compaction |
| P1 | Timeout 与预算配置 UI |
| P1 | Regex Search |
| P1 | UTF-16/GB18030 等额外文本编码与字节范围读取 |
| P1 | macOS Keychain 密钥存储 |
| P1 | Artifact/Session 数据管理与自动清理策略 |
| P1 | 二进制、PDF/Office、压缩包和加密容器 Artifact 的格式感知扫描/发布 |
| P1 | `.gitignore`/自定义排除、Workspace 快照与文件变更自动回滚 |
| P1 | 用户 Home 内受限工具链、项目环境自动激活和按可执行文件授权 |
| P1 | 敏感规则升级后的历史数据重扫、Artifact 隔离与安全迁移 |
| P2 | Windows/Linux 支持 |
| P2 | 后台执行、tray 和通知 |
| P2 | 多 Agent、多执行器和并行工具 |
| P2 | PostgreSQL 与服务化部署 |
