# Eidos 目标态设计决策记录

版本：v0.4
范围：Grilling Q1-Q160

本文件记录已经由产品与技术共同确认的目标态设计结论。它们仍可能被后续显式决策覆盖；只有进入 [MVP Lite](mvp-lite.md) 或阶段实施清单的条目才构成当前交付范围。PRD/TDD 与本文件冲突时应先统一文档再实现。

| 编号 | 已确认决策 | 主要落点 |
|---|---|---|
| Q1 | `run_shell` 即使获批也必须受硬隔离；审批不能替代 `active_root` 边界。 | PRD 安全；TDD 沙箱 |
| Q2 | MVP 仅支持 macOS。 | PRD 范围；TDD 架构 |
| Q3 | Shell 默认禁止外网。 | PRD 安全；TDD 网络策略 |
| Q4 | `run_shell` 默认运行在 `workspace_write` 沙箱；可修改 active root 和 Eidos 临时目录，不能突破沙箱。 | PRD 审批；TDD 工具契约 |
| Q5 | System Runtime 只读；active root 与 Eidos 管理目录可写；其他用户数据拒绝。 | TDD Seatbelt 策略 |
| Q6 | Shell 使用清洗后的独立环境，不继承真实 Shell 配置、环境变量或凭证。 | PRD 安全；TDD 环境 |
| Q7 | 获批外网访问只能通过 Eidos 代理访问本次声明的域名白名单。 | TDD 网络代理 |
| Q8 | 可创建多个 Run，但采用全局单执行器；等待态不占执行槽；单 Run 内严格串行。 | PRD 调度；TDD 状态机 |
| Q9 | 单次模型响应可包含多个只读 ToolCall，按声明顺序执行；有副作用工具必须独占响应。 | TDD ToolCall 批次 |
| Q10 | 非法 ToolCall 组合整批原子拒绝，零执行。 | TDD Runtime 校验 |
| Q11 | 合法只读批次中，单项失败不阻断后续调用。 | TDD Runtime 校验 |
| Q12 | 每个 Execution Segment 最多 20 Steps/30 分钟；整个 Run 最多 80 Steps/120 分钟。 | PRD 限制；TDD 预算 |
| Q13 | Run 达到硬上限后进入终态 `stopped`，记录结构化 `stop_reason`。 | PRD 状态；TDD 状态机 |
| Q14 | 硬停止前允许一次最长 60 秒、无工具权限的 Finalization Call；失败时降级为结构化摘要。 | TDD Finalization |
| Q15 | 所有写操作在获批后、执行前复检目标版本；冲突使审批失效并返回 `file_version_conflict`。 | PRD 审批；TDD 文件事务 |
| Q16 | 新增 `delete_file`；仅删除 active root 内单个普通文件，禁止目录、递归、通配符和批量删除。 | PRD 工具；TDD 工具契约 |
| Q17 | 明确删除必须优先使用 `delete_file`，但这不是 `workspace_write` Shell 的硬禁止项。 | PRD Agent 规则；TDD 风险说明 |
| Q18 | 新增显式 `publish_artifact`；普通写入不会自动成为 Artifact。 | PRD Artifact；TDD 工具契约 |
| Q19 | Artifact 是发布时的不可变快照；再次发布生成新版本。 | PRD Artifact；TDD 存储 |
| Q20 | 意外中断不自动重放；Run 转为 `waiting_user_input/runtime_interrupted`，运行中的 ToolCall 标记 `interrupted`。 | PRD 恢复；TDD 状态机 |
| Q21 | 模型流实时转发，合并后持久化，完成后保存最终响应。 | TDD 模型与 Events |
| Q22 | MVP API Key 明文保存在 `~/.eidos/config.toml`；Keychain 延后。 | PRD 风险接受；TDD 配置安全 |
| Q23 | 所有持久化内容统一脱敏；Shell 输出在返回模型前也脱敏。 | PRD 隐私；TDD 脱敏层 |
| Q24 | MVP 使用 Python sidecar 编排 `/usr/bin/sandbox-exec` Seatbelt，不引入 Rust 组件。 | TDD 沙箱实现 |
| Q25 | 沙箱不可用时 Shell 不可用，不提供无沙箱回退。 | PRD 安全；TDD fail closed |
| Q26 | `run_shell.command` 为字符串，固定由 `/bin/zsh -f -c` 执行。 | TDD Shell 契约 |
| Q27 | MVP 不支持持久服务；删除静默 90 秒终止规则。 | PRD 非目标；TDD timeout |
| Q28 | 内嵌 Workspace Terminal 移到 P1；MVP 只提供在系统 Terminal 打开 Workspace。 | PRD 范围；TDD Desktop |
| Q29 | 状态表是事实来源；Events 是与状态同事务提交的追加式 Timeline/Outbox。 | TDD 事件与存储 |
| Q30 | P0 实现确定性的有界上下文裁剪；智能摘要式 compaction 延后。 | PRD P0；TDD Context Builder |
| Q31 | 敏感文件对文件工具和 Shell 使用同一不可绕过的拒绝规则。 | PRD 安全；TDD Seatbelt carve-out |
| Q32 | `.git` 对 Agent Shell 只读，设置 `GIT_OPTIONAL_LOCKS=0`。 | PRD 安全；TDD 沙箱环境 |
| Q33 | 每个文件写工具 ToolCall 只作用于一个文件。 | PRD 审批；TDD 工具契约 |
| Q34 | Reject 计数在获批状态变更成功或用户开启新 Segment 时重置；达到 2 次暂停。 | TDD Approval 状态机 |
| Q35 | 全局执行队列不可抢占、持久化、FIFO；MVP 不支持优先级和手动调序。 | PRD 调度；TDD 队列 |
| Q36 | 取消是协作式的；已经进入原子文件提交区的操作完成后再取消 Run。 | PRD 取消；TDD 状态机 |
| Q37 | 模型 HTTP/SSE 仅在首个 delta 前对瞬时错误最多重试 2 次；只读工具瞬时错误重试 1 次；写和 Shell 不自动重试。 | TDD 重试策略 |
| Q38 | 写或 Shell 失败后强制事实确认屏障；完成只读核验前不能再次执行有副作用工具。 | TDD Runtime 状态机 |
| Q39 | localhost 默认禁止；`local_network=true` 需单次审批；Unix Socket 在 MVP 中始终禁止。 | PRD 安全；TDD 网络策略 |
| Q40 | 只读工具可批量；写、删除、Shell 独占且审批；`publish_artifact` 独占但自动执行。 | TDD 工具分类 |
| Q41 | 不保存、不展示 raw reasoning；只展示 `assistant_progress` 和 `final_answer`，可保存 reasoning token 用量。 | PRD Feed；TDD 模型流与 Desktop |
| Q42 | 首个 delta 后模型流中断不自动重试；Run 进入 waiting_user_input，部分文本标记未完成，未解析 ToolCall 丢弃。 | PRD 恢复；TDD 模型流与状态机 |
| Q43 | 模型认证、模型不存在和确定性请求配置错误直接使 Run failed；不暂停、不 Finalize、不修改原 Run 快照。 | PRD 错误；TDD 模型与状态机 |
| Q44 | 首个 delta 前瞬时模型错误重试耗尽后暂停 Run；多个 Attempt 只计一个失败 Step。 | PRD 恢复；TDD 模型重试 |
| Q45 | 无效模型协议响应允许纠正一次；连续两次后 waiting_user_input，合法响应清零计数。 | PRD Agent Loop；TDD 协议状态 |
| Q46 | 副作用工具先持久化 durable intent，再执行并保存结果；崩溃后只对账、不重放。 | PRD 可靠性；TDD 状态与存储 |
| Q47 | 网络代理只记录 host/port/decision/bytes 等审计元数据，不记录 URL、Header、Body，不做 TLS MITM。 | PRD 隐私；TDD 网络代理 |
| Q48 | 网络白名单只接受精确 host 与显式端口，并校验解析 IP；拒绝通配符、私网映射和跨 host redirect。 | PRD 网络；TDD 代理策略 |
| Q49 | Writable Workspace 禁止多链接普通文件；文件工具要求 `st_nlink=1`，Shell 前置检查失败则不可用。 | PRD 文件边界；TDD Workspace Guard |
| Q50 | 修改已有文件必须保留 mode、ACL 和 extended attributes；复制验证失败则不替换，新文件默认 0644。 | PRD 写入；TDD 文件提交 |
| Q51 | 敏感内容按 `deny` / `redact` / `allow_with_audit` 分级；文件硬拒绝与输出片段脱敏区分处理，文件名硬拒绝不可绕过。 | PRD 隐私；TDD Redaction Service |
| Q52 | MVP 使用版本化确定性规则注册表，不用模型或通用熵阈值判断敏感性。 | TDD 规则契约；测试向量 |
| Q53 | 高置信度内容 `deny` 不可通过 Approval 绕过；只返回无原文的结构化错误。 | PRD 安全；TDD 错误模型 |
| Q54 | 敏感扫描必须位于截断、摘要、展示、模型观察和持久化之前；文件全量流式扫描，输出支持跨 chunk 匹配。 | TDD 扫描管线；测试 |
| Q55 | 单文件扫描上限 32 MiB；单次搜索上限 256 MiB/15 秒；跨块规则最大 8 KiB，超限或扫描失败均 fail closed。 | PRD 容量；TDD 工具限制 |
| Q56 | 写入、Patch、Shell 参数命中 `deny`/`redact` 时整个 ToolCall 拒绝且不创建 Approval；Artifact 不做静默脱敏发布。 | PRD 工具；TDD ToolCall 扫描 |
| Q57 | 用户输入在创建 Message/Run/Segment 前扫描；命中 `deny`/`redact` 时整次提交拒绝且不占用 idempotency key。 | PRD 输入；TDD API 事务 |
| Q58 | 模型普通文本命中时脱敏后继续；敏感 ToolCall 拒绝，连续两次后暂停，不计入模型协议错误。 | PRD 恢复；TDD 模型流与状态机 |
| Q59 | 脱敏占位符统一为 `[REDACTED:<rule_id>]`，不包含长度、摘要或稳定关联标识；结构化数据只替换字符串叶子。 | TDD 脱敏格式；存储 |
| Q60 | 规则集作为只读应用资源随版本发布，MVP 不远程更新或热加载；升级后继续的 Run 记录规则版本变更，应用回滚不得降级已生效规则。 | TDD 启动自检；Events |
| Q61 | `read_file` 仅对 <=256 KiB 文件完整返回；256 KiB..2 MiB 返回最多 256 KiB head+tail，>2 MiB 改用 range。 | PRD 读取；TDD 容量 |
| Q62 | `read_file_range` 使用 1-based 闭区间，单次最多 2,000 行/256 KiB，不返回半行。 | TDD 读取契约；测试 |
| Q63 | MVP 文件工具只支持严格 UTF-8/可选 BOM；二进制与其他编码分类拒绝，新文件 UTF-8 无 BOM。 | PRD 文件；TDD 编码 |
| Q64 | `search_text` 使用单行 literal，ASCII 大小写折叠、稳定路径/行/列排序和 100/500 结果上限。 | PRD 搜索；TDD 工具契约 |
| Q65 | `list_files` 默认深度 2/最大 5，默认 500/最大 2,000 项；不跟随 symlink，隐藏敏感条目。 | PRD 文件树；TDD 遍历 |
| Q66 | write 最大 512 KiB，patch 最大 256 KiB/5,000 行/200 hunks，候选文件最大 32 MiB；超大 Diff 不允许截断审批。 | PRD 审批；TDD Diff |
| Q67 | `write_file` 不隐式创建父目录；MVP 不新增目录工具，目录操作使用受审批 Shell。 | PRD 工具；TDD 路径 |
| Q68 | `write_file` 覆盖已有文件仅适用于 <=256 KiB 且当前 Run 已完整读取同 hash 的文件。 | PRD 写入；TDD 读取证据 |
| Q69 | `apply_patch` 必须引用当前 Run 同 hash 的读取证据，hunk 原文范围全覆盖且零 fuzz/offset。 | TDD Patch 契约；存储 |
| Q70 | `delete_file` 无需读取证据，但只删除可生成完整受控 Diff 的 <=512 KiB 普通 UTF-8 文件；崩溃后只对账。 | PRD 删除；TDD Durable Intent |
| Q71 | 安全排除不可绕过；性能排除使用固定目录/lock 集，MVP 不解析 `.gitignore` 或提供自定义规则。 | PRD 文件树；TDD 排除策略 |
| Q72 | 只读工具保证单文件一致，不提供跨文件 Workspace 快照；并发变化文件丢弃结果并有界重试。 | PRD 一致性；TDD 只读工具 |
| Q73 | `apply_patch` 只接受严格单文件 unified diff；统一 LF/CRLF 可保留，mixed newline 拒绝，无 Git 扩展或 fuzz。 | PRD Patch；TDD 解析器 |
| Q74 | `write_file` 新文件只接受 LF；覆盖文件必须显式匹配原 LF/CRLF，不静默规范化换行。 | PRD 写入；TDD 编码 |
| Q75 | 已有文件候选字节完全相同时不创建 Approval/durable intent，ToolCall 以 `skipped/no_changes` 结束。 | PRD 审批；TDD 状态机 |
| Q76 | Shell Approval 不绑定 Workspace 快照，精确绑定命令/环境/权限；批准后 5 分钟未开始则失效。 | PRD Shell 审批；TDD Approval |
| Q77 | Writable Shell 在 durable intent 前后生成 Workspace manifest，记录执行窗口内观察变化；不声称因果或自动回滚。 | PRD 审计；TDD Shell 对账 |
| Q78 | 默认仅系统工具链；`/opt/homebrew` 和 `/usr/local` 需用户启用 Toolchain Profile，用户 Home 工具链不进入 MVP。 | PRD Settings；TDD Shell 环境 |
| Q79 | Shell 固定限制 64 进程、256 fd、2 GiB 内存、1 GiB 单文件和 2 GiB 净磁盘增长；限制不可单次放宽。 | PRD NFR；TDD 资源监控 |
| Q80 | Shell 输出先脱敏再按 100ms/4 KiB 合并；stderr 优先的 1 MiB head+tail 持久化，慢订阅者不得阻塞管道。 | PRD Execution Feed；TDD 流式输出 |
| Q81 | Model Profile 必须通过不携带用户任务数据的显式能力探测后才能被 Session 选择；探测生成版本化 capability snapshot，Run 固化创建时快照，运行时能力漂移按结构化模型错误处理。 | PRD Model Profile；TDD Model Gateway/API/存储 |
| Q82 | Capability snapshot 不设时间 TTL 或后台自动探测；Profile 配置、Gateway contract version 变化或确定性 capability drift 使其失效，重新用于新 Session/Run 前必须显式测试。 | PRD Model Profile；TDD snapshot 状态 |
| Q83 | MVP 支持编辑已有 Model Profile；连接/协议字段变化使 capability snapshot 失效，纯展示字段变化不失效，既有 Run 保持原快照。 | PRD Models；TDD API/版本 |
| Q84 | Model Profile 只支持 Archive/恢复，不做物理删除；历史 Session、Run、Timeline 和 snapshot 引用永久保留。 | PRD Models；TDD 生命周期/存储 |
| Q85 | 每个 Model Profile 独占 API Key 凭证槽位；密钥不共享、不回显，替换只使该 Profile 的 snapshot 失效。 | PRD 隐私；TDD 配置安全 |
| Q86 | Model `base_url` 不按公网/私网类别限制，但只接受 HTTP(S)、禁止 URL 内嵌凭证和跨 Origin 重定向。 | PRD 兼容性/安全；TDD Gateway 网络 |
| Q87 | HTTPS 必须校验证书、主机名和信任链，使用 macOS 系统信任库；MVP 不提供忽略 TLS 错误的通道。 | PRD 安全；TDD TLS |
| Q88 | Model Profile 仅支持 `bearer`、`api_key_header`、`none` 三种固定认证模式，不允许任意自定义 Header。 | PRD Models；TDD 请求构造 |
| Q89 | Provider 扩展参数可以透传，但不得覆盖 Runtime 核心字段或注入传输层配置；参数变化使 snapshot 失效。 | PRD Models；TDD 参数校验 |
| Q90 | `context_window_tokens` 与 `max_output_tokens` 由用户显式填写，Eidos 不按模型名自动推断；明确的上下文上限不匹配使 Run 失败并使 snapshot 失效。 | PRD Models；TDD Context Builder |
| Q91 | 既有 Run 不固化 API Key；每次模型请求从 Profile 专属凭证槽读取当前有效密钥，轮换后使用新密钥但不改写 Run 的非密钥配置快照。 | PRD 凭证轮换；TDD Gateway/凭证版本 |
| Q92 | MVP 不依赖 Provider token 统计做发送前预算；使用版本化 canonical payload 的 UTF-8 字节保守估算、固定协议开销和固定公式 safety margin。 | PRD 上下文预算；TDD Context Builder |
| Q93 | 模型网络阶段使用固定超时：建连 15 秒、首 delta 180 秒、流空闲 120 秒；完整请求周期受共享总时限约束。 | PRD 响应体验；TDD Model Gateway |
| Q94 | Runtime 调用远端模型固定使用 HTTP 请求和 SSE 响应流；首 delta 前瞬时错误最多重试 2 次，首 delta 后禁止重放。 | PRD 模型韧性；TDD Model Gateway |
| Q95 | Model Profile 不探测或记录 WebSocket；重试保持相同 wire API、endpoint、请求语义、认证和 TLS 策略，瞬时故障不使 capability snapshot 失效。 | PRD Model Profile；TDD Model Gateway |
| Q96 | 每次实际模型网络发送都创建独立 ModelAttempt；同一 Step 共享逻辑请求 ID，不假设 Provider 幂等，分别保存已报告 usage，未知 usage 不按零计算。 | PRD 用量透明；TDD Attempt/可观测性 |
| Q97 | 输入估算固定为 canonical payload UTF-8 字节数加协议开销；safety margin 为 context window 的 2%，下限 1,024、上限 8,192。 | PRD 上下文预算；TDD 估算公式 |
| Q98 | 普通/纠正请求使用 Profile 的 `max_output_tokens`；Finalization 上限 4,096，Test Connection 探测上限 512；实际请求值参与预算并写入 Attempt。 | PRD 输出预算；TDD 请求构造 |
| Q99 | 不可裁剪输入在本地预算阶段已超限时零 Provider 请求，Run 直接 `failed/context_input_too_large` 且不使 capability snapshot 失效。 | PRD 错误体验；TDD Context Builder/状态机 |
| Q100 | 同一 Step 的完整模型请求周期共享 10 分钟 deadline，覆盖全部 HTTP/SSE Attempt、退避和 Retry-After；Finalization 仍为独立 60 秒。 | PRD 时间预算；TDD 重试时钟 |
| Q101 | 版本化 `model_request_contract_version` 固化序列化、预算、输出预留、传输重试与 timeout；新版本使 Profile snapshot 失效，既有 Run 继续使用创建时版本。 | PRD 升级兼容；TDD 版本路由/恢复 |
| Q102 | MVP 同时支持显式 `wire_api=responses|chat_completions`；只测试和运行所选协议，不自动猜测、跨协议回退或建立厂商分支。 | PRD Model Profile；TDD Protocol Adapter |
| Q103 | `base_url` 只表示 API 根；Adapter 固定追加 `/responses` 或 `/chat/completions`，完整 endpoint 输入拒绝，结构化拼接并保留安全 query。 | PRD Endpoint；TDD URL 构造 |
| Q104 | 两种协议都必须提供完整非负 usage；Chat 固定请求 `stream_options.include_usage=true`，缺失或非法 usage 使探测失败，运行时属于 capability drift。 | PRD 用量；TDD usage 归一化 |
| Q105 | 两种协议严格归一化流式 ToolCall；Provider call ID 必须完整唯一，内部 UUID 与 Provider ID 分离，缺失/冲突不得合成 ID。 | PRD 工具可靠性；TDD ToolCall assembler |
| Q106 | ToolCall assembler 固定 16 calls、单 call 1 MiB、合计 2 MiB、16,384 deltas、JSON 深度 16/成员 2,048 等内存硬上限。 | PRD 资源边界；TDD 流式解析 |
| Q107 | 模型可见文本按 output tokens 动态限制且最大 4 MiB；discarded reasoning 2 MiB、单 Event 1 MiB、总流 8 MiB，超限暂停且不重试。 | PRD 输出安全；TDD Stream limiter |
| Q108 | Responses 只以 `response.completed` 完成；Chat 必须有合法 finish reason、完整分片、usage 和 `[DONE]`；截断/过滤暂停 Run，不执行任何 ToolCall。 | PRD 输出状态；TDD 完成判定 |
| Q109 | MVP 固定语义无状态模型请求：Responses `store=false`，不依赖 previous response/conversation；每个 Step 从本地状态重建完整上下文。 | PRD 隐私/可恢复性；TDD Context Adapter |
| Q110 | Responses 固定 `max_output_tokens`；Chat 仅在 Test Connection 中按确定性错误协商 `max_completion_tokens`/`max_tokens` 并把结果固化到 snapshot。 | PRD 兼容性；TDD 参数协商 |
| Q111 | 工具集非空的普通/协议纠正请求在两种 wire API 中固定 `parallel_tool_calls=true`；这只允许模型同响应提出多调用，Runtime 仍串行执行并强制批次规则。 | PRD 工具效率；TDD 请求控制 |
| Q112 | Test Connection 使用严格两阶段 ToolCall/ToolResult probe：首阶段单一工具 `required` 且禁止 parallel，次阶段回传固定结果并使用 `tool_choice=none`。 | PRD 能力测试；TDD probe 协议 |
| Q113 | 工具集非空的普通/纠正请求固定 `tool_choice=auto`；空工具集与 Finalization 不发 tools、固定 `tool_choice=none` 且不发 `parallel_tool_calls`；Profile 不得覆盖。 | PRD Agent Loop；TDD 请求构造 |
| Q114 | 两种 wire API 的 function tool 定义都显式发送 `strict=false`；Provider strict 不是安全边界，Runtime 本地 schema 校验始终是执行授权依据。 | PRD 兼容性；TDD Tool schema |
| Q115 | 所有 function tool 输入 schema 递归封闭 object/array object，显式 `additionalProperties=false`；MVP 无自由 map，未知字段按 Q45 整批零执行。 | PRD 工具可预期性；TDD 参数校验 |
| Q116 | ToolCall 只产生一套 effective arguments；Runtime 补齐 schema 静态默认值后完成校验、组合与敏感扫描，再用于 hash、审批、持久化和执行。 | PRD 审批一致性；TDD 参数归一化 |
| Q117 | MVP 引入独立 `tool_contract_version` 固化 Run 的可观察工具语义；旧契约不得降级当前安全底线，缺失或不再安全时原 Run 暂停且不执行。 | PRD 升级可恢复性；TDD 工具版本路由 |
| Q118 | MVP 固定 `Eidos Tool Schema Dialect v1`，只允许内置工具使用受控 JSON Schema 子集；Dialect 是 Gateway capability contract 子版本，扩展时 Profile 必须重测。 | PRD 兼容性；TDD schema dialect/probe |
| Q119 | 每个 Step 按固化工具契约与可审计 Runtime 门限生成 available tool set，同一逻辑请求的 Attempt 不变；隐藏工具不替代收到 ToolCall 后的安全复检。 | PRD 工具可用性；TDD Context Builder |
| Q120 | 所有会进入后续模型上下文的已创建 ToolCall 都生成恰好一个版本化、协议无关的 canonical ToolResult envelope，两种 Adapter 只负责 Provider call ID 关联。 | PRD 工具恢复；TDD ToolResult/Context |
| Q121 | ToolResult 顶层 `schema_version=1`；仅顶层不兼容变化递增。Canonical JSON 使用稳定紧凑 UTF-8、递归 key 排序和数组保序，字节规则变化同时递增 model request contract。 | PRD 可恢复性；TDD 序列化/版本 |
| Q122 | ToolResult `summary` 只允许由 contract/tool/outcome/code 唯一决定的单行固定模板，最大 1,024 UTF-8 bytes，不携带调用级动态内容。 | PRD 结果安全；TDD 结果投影 |
| Q123 | `success` 的 ToolResult code 必须为 null；其他 outcome 使用闭合 snake_case 枚举，Provider/OS 原始错误必须先映射且不得直接进入模型。 | PRD 错误一致性；TDD code registry |
| Q124 | ToolResult data 始终是按 contract/tool/outcome/code 选择的递归闭合 object；可选字段省略而非 null，数组具有容量和确定顺序。 | PRD 结果可预期性；TDD result schema |
| Q125 | 无法构造合法 ToolResult 时禁止 fallback：Run 以 `tool_result_contract_violation` failed，保留真实副作用状态并持久隔离对应工具或整个 ToolResult capability。 | PRD 失败体验；TDD quarantine/状态机 |
| Q126 | `list_files` success data 使用闭合稳定条目与计数；工具截断、Context 投影裁剪和 Workspace 非快照状态严格分离，敏感隐藏项不返回条目或计数。 | PRD 文件树；TDD list result schema |
| Q127 | 每个 ToolCall 有唯一 immutable base ToolResult；Context Builder 每 Step 只生成冻结、可审计且单调减少的预算 projection，不产生第二个业务结果。 | PRD 上下文一致性；TDD projection/storage |
| Q128 | `read_file` success data 使用有界 segments 与精确读取证据；完整性、脱敏、源字节省略和 Context 投影分别表达，行号按固定 LF 规则计算。 | PRD 文件读取；TDD read result schema |
| Q129 | `read_file_range` 永远 `complete=false`；空范围、省略字段、截断原因、下一完整行和双上限优先级采用确定契约。 | PRD 范围读取；TDD range result schema |
| Q130 | `search_text` success data 使用稳定有界 matches、资源计数与明确截断语义；敏感文件不进入计数，长匹配 preview 按固定规则裁剪。 | PRD 搜索；TDD search result schema |
| Q131 | write/apply/delete success data 只返回提交后复检的安全文件事实；读取证据失效和 Approval/intent/OS 详情留在 Runtime。 | PRD 写入结果；TDD file result schema |
| Q132 | write/apply 的 no-op 固定为 `skipped/no_changes` 与 `{path,base_sha256}`；零 Approval、零 intent、零文件接触且不失效读取证据。 | PRD no-op；TDD skipped result |
| Q133 | `publish_artifact` success 返回稳定 Artifact 身份、源相对路径、展示信息、版本、快照 hash 与逻辑字节数，不暴露内部路径或 URL。 | PRD Artifact；TDD artifact result schema |
| Q134 | 已启动 Shell 的终态结果使用闭合进程、脱敏双流 observation 与 Workspace 对账字段；原始流字节和完整 manifest 不进入模型。 | PRD Shell 结果；TDD shell result schema |
| Q135 | Shell 进程终态、Runtime 中断、输出/manifest 故障映射为固定 outcome/code 与优先级；`.git`/protected 变化只触发事实确认，不篡改进程结果。 | PRD Shell 恢复；TDD code mapping |
| Q136 | `side_effects_may_exist` 表示结果尚未完整确认的物质性副作用，不表示工具类别；它与 `reconciliation_required` 相关但不互相等价。 | PRD 恢复；TDD 状态机 |
| Q137 | 共享非成功 data 默认最小；Reject 可选返回已扫描有界反馈，已启动 Shell 的 interrupted 仍保留工具专属终态事实。 | PRD Reject/恢复；TDD result schema |
| Q138 | ToolResult error 禁止通用 details/message/cause；仅 code 专属闭合 schema 可携带恢复必需事实，JSON-RPC business error 与之独立。 | PRD 错误安全；TDD error registry |
| Q139 | 只读错误仅在文件/行容量超限时返回必要的安全数值；其他错误使用 code 与原调用恢复，始终零正文和零证据。 | PRD 读取错误；TDD read error schema |
| Q140 | 写/Patch 错误只返回 Diff、hunk 或候选容量的必要有界事实；版本冲突和 Artifact 错误不返回动态 hash、路径或格式详情。 | PRD 写入错误；TDD write error schema |
| Q141 | ToolResult 的所有 JSON integer 限于 JavaScript safe integer；canonical 整数只用十进制、无指数/小数点/负零，真实测量溢出映射为 `tool_result_numeric_limit_exceeded`。 | PRD 结果兼容；TDD serializer/code registry |
| Q142 | MVP 使用 Eidos Canonical JSON v1 而非 JCS：Unicode scalar 原样保留、key 按未转义 UTF-8 bytes 排序、固定转义且拒绝孤立 surrogate 与递归重复 key。 | PRD 可恢复性；TDD canonical serializer |
| Q143 | SQLite 迁移只向前；启动时独占锁、先做一致备份与 manifest，再以受限事务迁移并校验，未知/更新 revision、备份或迁移失败均保持 health-only。 | PRD 数据安全；TDD migration/recovery |
| Q144 | 每个 Shell 由最小原生 guardian 直接托管；Main/sidecar 失联、deadline 或取消时按进程身份有界清理受控进程组与已识别后代，不承诺抵御三进程同时被强杀。 | PRD Shell 生命周期；TDD guardian |
| Q145 | Runtime 只在状态目录、独占锁、DB、配置、契约、自检和恢复完成后通过 `initialize` 进入 ready；此前仅开放闭合诊断 method，安全核心失败保持 diagnostic，局部 capability 可降级。 | PRD 启动体验；TDD initialize gate |
| Q146 | Workbench 以同一 SQLite 读事务取得闭合 RunSnapshot v1 与 `through_event_id`，原子安装后只应用更高水位 notification；缺口通过 `run/readEvents` 补齐，Approval detail 通过权威 method 按 nonce 复检。 | PRD Workbench 一致性；TDD snapshot/protocol |
| Q147 | Workspace 持久身份为 volume UUID、inode 与 birthtime，路径也是授权边界；身份不可用时 fail closed，移动/替换不自动 rebind，旧 Approval 永久失效。 | PRD Workspace 安全；TDD identity/lifecycle |
| Q148 | Electron 单实例锁约束 UI；sidecar 在任何 DB 操作前取得并全生命周期持有状态目录独占 OS lock，禁止删锁、按 PID 抢占或 attach 旧进程。 | PRD 单实例；TDD execution ownership |
| Q149 | `allowed_actions` 是 Runtime 计算的闭合状态机提示；可恢复性由 status、pause reason、Workspace 和 Approval 事实共同决定，所有持久化 RPC 在事务内重算。 | PRD 状态操作；TDD protocol/state matrix |
| Q150 | 事实确认使用持久化递增 epoch；只有 epoch 后正常完成的只读 Step 至少提交一个 success 才按 epoch 清除，新的不确定副作用不能被旧观察误清除。 | PRD 事实确认；TDD state/audit |
| Q151 | 所有 Renderer 发起的持久化 JSON-RPC 操作使用全局 operation ID 与绑定 method/scope/params 的 canonical hash；重放前重新校验授权事实，保留 nonce/version/gesture guard，外部不确定操作不自动重放。 | PRD 写操作可靠性；TDD idempotency |
| Q152 | JSON-RPC/IPC DTO 递归闭合并由同一 schema 与 fixture 验证；分页使用 method-bound opaque keyset cursor 与 Runtime 单调集合水位，不使用 offset、时间或 UUID 推断稳定顺序。 | PRD 协议一致性；TDD DTO/pagination |
| Q153 | Event 使用闭合 envelope 与 per-type payload schema；仅兼容 contract 中明确可忽略的未知 type 可安全占位推进，已知语义版本不匹配必须 snapshot 恢复或阻断。 | PRD Timeline 兼容；TDD Event/reducer |
| Q154 | DB/Protocol/Event 时间统一为 UTC Unix 毫秒 safe integer；duration 使用 monotonic clock，Shell 授权另持久化 boot-session/continuous deadline，同 boot 延续、时基不可证明时失效，墙钟变化不得延长授权。 | PRD 时间可靠性；TDD TimeProvider |
| Q155 | SQLite/state storage 不可可靠提交时 Runtime 进入 health-only；有界应急 reserve 仅辅助空间耗尽恢复，Workspace 写失败按提交阶段对账，任何副作用均不自动重放或自动删数据。 | PRD 存储故障；TDD storage recovery |
| Q156 | 第二期将 RuntimeLoop 演进为一个对外仅暴露 `run(run_id, cancel)` 的 RuntimeEngine；内部按状态机、模型运行、工具调度、审批、事件和错误映射拆分职责，不为文件拆分增加浅层转发接口。 | 第二期清单；TDD 架构/状态机 |
| Q157 | 持久 Run status 与内存 RuntimeState 分层：Scheduler 持有 queued，StateMachine 独占执行态合法迁移；重启只从持久事实重建，不恢复内存中的模型或工具执行。 | 第二期清单；TDD 状态机 |
| Q158 | 第二期引入 Pydantic v2，用于 JSON-RPC DTO、ApprovalDecision、Run、Item、ToolSpec、ToolResult 和 Event 的 strict/closed 校验与安全投影；不引入 FastAPI，也不以 Pydantic 取代 Tool Schema、状态机或安全授权。 | 第二期清单；TDD 架构/协议/工具 |
| Q159 | ToolSpec 的 side_effect 保持 `none|workspace|eidos_state|shell` 分级枚举而非 bool；requires_approval、timeout 与闭合 input/result schema 为 Registry 固定元数据。 | 第二期清单；TDD 工具契约 |
| Q160 | 本地控制面永久固定为 Electron Main 与 Python Runtime 之间的标准 JSON-RPC 2.0 over stdio/JSONL，不开放本地 HTTP/SSE/WebSocket/Unix Socket、随机端口、Bearer Token 或 FastAPI；Runtime 调用远端模型只使用 HTTP 请求与 SSE 响应流。 | PRD/TDD 总览；协议、模型与 Desktop |
| Q161 | 左侧任务导航按 canonical `workspace_root` 分组；同一 Workspace 的所有 Session 作为任务放在同一项目节点下，不再重复平铺 Workspace 名。 | PRD Workbench；TDD Session 投影 |
| Q162 | Session 标题由首次 `userInput` 触发一次独立、无工具的模型命名；标题持久化后不随后续 Run 改写，生成失败时退回首次输入的有界安全短文本。 | PRD 任务命名；TDD Model/Session/Event |

## 文件契约统一收敛

以下是在 Q61–Q80 已授权边界内的实现细化，无需再作为独立问题确认：

- 工具路径使用文件系统真实目录项名称，不小写化或静默 Unicode 规范化；输入必须与逐段打开的实际目录项唯一对应。
- 二进制判定使用 NUL、固定 magic signature 和受控 C0 字节阈值；严格 UTF-8 解码是独立校验。
- 文件工具只修改当前用户拥有的普通文件；保留 gid、mode、ACL、xattr 和可保留 flags，immutable/append-only flags 拒绝。
- 文件容量上限按逻辑字节计算；Shell 磁盘增长按已分配 blocks 计算，稀疏文件两者分开记录。
- MVP `publish_artifact` 只发布 <=32 MiB 的严格 UTF-8 文本快照；二进制、压缩包、加密容器和需格式解析才能扫描的 Artifact 延后。

后续敏感信息细节由上述安全原则统一收敛：不降级、不静默改写有副作用输入、不持久化原始命中，且不增加用户可绕过通道。
