# Eidos MVP 设计决策记录

版本：v0.4
范围：Grilling Q1-Q80

本文件记录已经由产品与技术共同确认的结论。PRD 描述产品承诺，TDD 描述实现约束；若正文与本文件冲突，应先停止实现并统一文档。

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
| Q37 | 模型仅在首个 delta 前对瞬时错误最多重试 2 次；只读工具瞬时错误重试 1 次；写和 Shell 不自动重试。 | TDD 重试策略 |
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

## 文件契约统一收敛

以下是在 Q61–Q80 已授权边界内的实现细化，无需再作为独立问题确认：

- 工具路径使用文件系统真实目录项名称，不小写化或静默 Unicode 规范化；输入必须与逐段打开的实际目录项唯一对应。
- 二进制判定使用 NUL、固定 magic signature 和受控 C0 字节阈值；严格 UTF-8 解码是独立校验。
- 文件工具只修改当前用户拥有的普通文件；保留 gid、mode、ACL、xattr 和可保留 flags，immutable/append-only flags 拒绝。
- 文件容量上限按逻辑字节计算；Shell 磁盘增长按已分配 blocks 计算，稀疏文件两者分开记录。
- MVP `publish_artifact` 只发布 <=32 MiB 的严格 UTF-8 文本快照；二进制、压缩包、加密容器和需格式解析才能扫描的 Artifact 延后。

后续敏感信息细节由上述安全原则统一收敛：不降级、不静默改写有副作用输入、不持久化原始命中，且不增加用户可绕过通道。

## 待答问题

### Q81：OpenAI-compatible Model Profile 的能力验证

状态：待回答，尚未形成设计决策，不得作为已批准规则写入 PRD/TDD。

问题：Model Profile 是否必须在可被 Session 选择前，通过一次不携带用户任务数据的显式能力探测，验证认证、模型存在、streaming、ToolCall 和 usage 契约？

当前推荐答案：必须。Profile 创建只保存配置，显式 Test Connection 产生版本化 capability snapshot；未通过的 Profile 不可用于新 Session/Run，Run 继续固化当时 snapshot，运行时能力漂移按结构化模型错误处理。
