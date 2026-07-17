# 验收标准

版本：v0.4（探索草案）

范围说明：本文是目标态验收集合，不是当前阶段退出清单。第一期和第二期验收分别见 [MVP Lite](../mvp-lite.md) 与 [第二期实施范围](../mvp-phase-2.md)。

## 1. 产品闭环

| 编号 | 验收项 | 标准 |
|---|---|---|
| A001 | macOS 启动 | Electron 启动并拉起 sidecar；非 macOS 明确拒绝 |
| A002 | 两种模式 | Workspace/Public Session 均可创建 Run |
| A003 | Model Profile | 创建只保存配置；Test Connection 不携带用户任务数据并验证认证、模型、streaming、ToolCall、usage；失败 Profile 不可选，Run 固化不含密钥的版本化能力快照 |
| A004 | 多 Run | 多个 Run 可创建、查看、排队、暂停和取消 |
| A005 | 单执行器 | 任意时刻最多一个 Run 调模型或执行工具 |
| A006 | FIFO | 新建和恢复按 `enqueued_at` 排队，重启后顺序保持 |

## 2. 工具与审批

| 编号 | 验收项 | 标准 |
|---|---|---|
| A010 | 只读批次 | 多个只读调用按顺序执行，单项失败不阻断后续 |
| A011 | 非法批次 | 混合副作用或多个副作用调用时整批零执行 |
| A012 | 单文件写入 | write/apply/delete 每次只作用于一个普通文件 |
| A013 | Diff 审批 | write/apply/delete 审批展示实际目标与 diff |
| A014 | 版本复检 | 审批等待期间文件变化会返回 `file_version_conflict`，不写入 |
| A015 | 删除工具 | delete_file 拒绝目录、递归、通配符和批量 |
| A016 | Artifact 发布 | 只有 publish_artifact 产生可见 Artifact |
| A017 | Artifact 不可变 | 修改/删除源文件不影响已发布快照；重复发布产生新版本 |
| A018 | Reject | 连续两次 Reject 后等待用户；计数按约定重置 |

## 3. Shell 安全

| 编号 | 验收项 | 标准 |
|---|---|---|
| A020 | Seatbelt | Shell 通过 `/usr/bin/sandbox-exec` 启动，子进程继承策略 |
| A021 | Fail closed | 沙箱缺失、自检或策略失败时 Shell 不可执行 |
| A022 | Workspace 写 | 可在 active root 和 Eidos temp 创建、修改、删除文件 |
| A023 | 外部拒绝 | 不能读写其他用户数据路径 |
| A024 | System Runtime | 系统和批准工具链只读可执行 |
| A025 | 敏感文件 | 文件工具和 Shell 都无法读取不可审批绕过的敏感路径 |
| A026 | Git 保护 | 可执行只读 Git 命令，无法写 `.git` |
| A027 | 环境隔离 | 不加载用户 rc，不继承宿主凭证和真实 HOME |
| A028 | 网络默认拒绝 | 默认不能访问外网、localhost 或 Unix Socket |
| A029 | 域名网络 | 只可通过代理访问本次审批域名，其他域名失败 |
| A030 | localhost | 仅 `local_network=true` 获批 ToolCall 可访问 loopback |
| A031 | 进程终止 | timeout/cancel 后整个 Shell 进程组被终止 |
| A032 | 无长驻服务 | ToolCall 结束后没有 Agent Shell 服务进程遗留 |

## 4. 状态、恢复与预算

| 编号 | 验收项 | 标准 |
|---|---|---|
| A040 | Segment | 20 Steps 或 30 分钟后进入 waiting_user_input |
| A041 | Segment 恢复 | 用户补充信息后新建 Segment 并进入队尾 |
| A042 | stopped | 80 Steps 或 120 分钟后进入不可恢复 `stopped` |
| A043 | Finalization | 硬停止前最多一次无工具收尾；失败有降级摘要 |
| A044 | 崩溃恢复 | 运行中 ToolCall 变为 interrupted，Run 不自动重放 |
| A045 | Approval 恢复 | waiting_approval 重启后仍可决定，工具未提前执行 |
| A046 | 事实确认 | 写/Shell 失败后，未只读核验前副作用调用被拒绝 |
| A047 | 协作式取消 | 原子提交如实完成；其他可取消工作被中断 |

## 5. 模型、事件与数据

| 编号 | 验收项 | 标准 |
|---|---|---|
| A050 | 模型流 | delta 实时可见、分块持久化、最终响应完整保存 |
| A051 | 有界上下文 | 长 Run 不会因无限累积历史而超过配置窗口 |
| A052 | 重试边界 | 模型和只读工具只按瞬时错误规则重试，副作用零自动重试 |
| A053 | Event 事务 | 状态与对应 Event 同时成功或同时回滚 |
| A054 | Event 续接 | JSON-RPC notification 可从最后 event id 续接，不重复改变业务状态 |
| A055 | 配置权限 | `.eidos`/`config.toml` 权限不合规时拒绝加载 API Key |
| A056 | 密钥不落库 | SQLite、Event、日志和 Run 快照中不出现已配置 API Key |
| A057 | 统一脱敏 | 模型可见 Shell 结果及所有持久化 payload 均通过脱敏层 |
| A058 | 模型流中断 | 首 delta 后中断时无 ToolCall 执行；未完成进度可见，Run 等待用户输入 |
| A059 | 模型配置错误 | 认证、模型不存在或确定性请求错误直接 failed，不执行 Finalization，可用新 Profile 创建新 Run |

## 6. Desktop

| 编号 | 验收项 | 标准 |
|---|---|---|
| A060 | Renderer 隔离 | Renderer 无法访问 sidecar stdio/进程句柄或调用未列入白名单的 IPC |
| A061 | 无内嵌 Terminal | MVP 不启动 PTY；用户按钮可打开系统 Terminal |
| A062 | 前台退出 | 运行中 Run 退出前要求等待或取消；等待/排队状态可恢复 |
| A063 | 推理内容边界 | Feed、SQLite、Event 和日志中没有 raw reasoning；只展示 progress/final answer，可保存 token 用量 |

## 7. 可靠性与边界补充

| 编号 | 验收项 | 标准 |
|---|---|---|
| A064 | 瞬时模型故障 | 首 delta 前重试耗尽后 waiting_user_input；多个 Attempt 只计一个 Step |
| A065 | 模型协议错误 | 无效响应零工具执行；连续两次后暂停，合法响应清零计数 |
| A066 | Durable Intent | 副作用前意图已持久化；模拟结果提交失败时恢复只对账不重放 |
| A067 | 代理零内容 | 代理审计中没有 URL、Header、Body 或 TLS 明文 |
| A068 | Host 防绕过 | 通配符、私网解析、非批准端口和跨 host redirect 均被拒绝 |
| A069 | Hardlink 防护 | 文件工具拒绝 `st_nlink>1`；Writable Shell 在存在多链接文件时不可执行 |
| A070 | 元数据保留 | 修改保留 mode/ACL/xattr；元数据失败零替换；新文件为 0644 |

## 8. 敏感内容边界

| 编号 | 验收项 | 标准 |
|---|---|---|
| A071 | 分级规则 | 固定输入在各入口一致得到 `deny`/`redact`/`allow_with_audit`、rule id/version 及 ruleset version |
| A072 | 全文件拒绝 | 文件任意位置命中 `deny` 时 read/range/search 不返回该文件任何正文 |
| A073 | 输入阻断 | 任务/补充命中时不落库、不发模型、不创建/恢复 Run 且不占用 idempotency key；审批 feedback 命中时决策保持 pending |
| A074 | 副作用阻断 | 敏感写/Patch/Shell/Artifact 零执行、零 Approval，不以脱敏内容代替执行 |
| A075 | 跨块脱敏 | 跨 output chunk 的凭证不会在 UI、模型观察、Event、SQLite 或日志泄露 |
| A076 | 扫描顺序 | 原始内容先扫描再截断/摘要；扫描失败或超限不释放未确认正文 |
| A077 | 占位符 | 脱敏统一为 `[REDACTED:<rule_id>]`，无原值长度、摘要、哈希或跨记录关联标识 |
| A078 | 敏感重试 | 模型连续两次生成敏感 ToolCall 后 waiting_user_input，且不增加协议错误计数 |
| A079 | 规则集失效 | 规则自检失败时不存在未扫描降级通道；用户无法热加载、白名单或审批绕过 |
| A080 | 规则版本变更 | 升级后恢复旧 Run 使用新规则并记录版本变更；旧内容读取时按当前规则扫描；旧应用不能降级已生效规则 |

## 9. 文件工具精确契约

| 编号 | 验收项 | 标准 |
|---|---|---|
| A081 | read_file 分级 | <=256 KiB 完整；256 KiB..2 MiB 返回 <=256 KiB head+tail；>2 MiB 拒绝并引导 range |
| A082 | range 语义 | 1-based 闭区间；2,000 行/256 KiB 截断可继续；不返回半行 |
| A083 | 编码边界 | 严格 UTF-8/可选 BOM 成功；二进制与其他编码错误可区分；修改保留 BOM |
| A084 | 搜索稳定 | literal/ASCII case-fold、路径-行-列排序、重叠匹配、preview 和首个停止原因符合契约 |
| A085 | 文件树有界 | 深度/条目上限、隐藏敏感条目、`.git` 不展开、symlink 不跟随且不泄露 target |
| A086 | 完整 Diff | write/apply/delete Diff 由 Runtime 生成；超出 512 KiB/5,000 行时零 Approval，UI 无截断审批 |
| A087 | 父目录 | 父目录不存在时零创建；审批期间父目录变化使 Approval 失效 |
| A088 | 覆盖证据 | write_file 只覆盖 <=256 KiB 且当前 Run 已完整、无脱敏地读取同 hash 的文件 |
| A089 | Patch 证据 | 每个 hunk 原文都被引用的同 hash 非脱敏读取区间覆盖；无 offset/fuzz；变更后旧证据失效 |
| A090 | 删除对账 | 仅 <=512 KiB 普通 UTF-8 文件可完整 Diff 审批删除；崩溃后不重删同路径新文件 |

## 10. 文件与 Shell 闭环

| 编号 | 验收项 | 标准 |
|---|---|---|
| A091 | 排除分层 | 安全排除无绕过；固定目录/lock 精确匹配；`.gitignore` 不影响 MVP 结果 |
| A092 | 单文件一致 | 读取中变化零正文/零证据；搜索变化文件零匹配但继续其他文件；无跨文件快照假象 |
| A093 | Unified Diff | 仅单文件标准 hunk；路径/行号/原文精确；Git 扩展/mixed newline/offset/fuzz 均拒绝 |
| A094 | write 换行 | 新文件只 LF；覆盖保留原 LF/CRLF/BOM/末尾换行语义；无静默规范化 |
| A095 | 文件 no-op | 相同候选字节零 Approval、零 intent、零文件接触，ToolCall `skipped/no_changes`，Reject 计数不清零 |
| A096 | Shell 授权时效 | 审批参数/环境精确绑定；开始前超过 5 分钟失效；UI 明示不绑定 Workspace 快照 |
| A097 | Shell Manifest | 前置超限零执行；后置 created/deleted/content/metadata/type 变化可审计；敏感名隐藏；崩溃不重放 |
| A098 | Toolchain Profile | 默认系统 PATH；Homebrew/本地根须用户启用且替换后自动禁用；无真实 Home/rc/任意根回退 |
| A099 | Shell 资源保护 | fork、fd、RSS、单文件、磁盘增长和监控器故障均终止进程组，sidecar 保持可用 |
| A100 | Shell 输出 | 双流交错序可回放；100ms/4 KiB 合并；stderr 优先 1 MiB head+tail；慢订阅者不阻塞子进程 |
| A101 | 路径名称稳定 | case-sensitive/insensitive 卷均以真实目录项唯一定位，Unicode/case 别名不能迁移 Approval |
| A102 | 二进制判定 | NUL、已知 magic、控制字节阈值与严格 UTF-8 错误稳定可测，无替换字符降级 |
| A103 | 所有权与 flags | 非当前 uid、immutable/append-only 文件零修改；成功修改保留 gid/mode/ACL/xattr/可保留 flags |
| A104 | 稀疏文件 | 文件工具按 logical size 限制；Shell 磁盘保护按 allocated blocks；审计同时展示两种变化 |
| A105 | Artifact 发布边界 | <=32 MiB 严格 UTF-8 文本发布为不可变快照；二进制/压缩/加密/格式不可扫描时零 Artifact |

## 11. Model Profile 生命周期与连接

| 编号 | 验收项 | 标准 |
|---|---|---|
| A106 | Snapshot 失效 | 无时间 TTL/后台探测；连接配置、Gateway/model request contract 版本或确定性能力漂移使 snapshot 失效，新 Session/Run 被拒绝，既有 Run 不变 |
| A107 | Profile 编辑 | 展示字段编辑保持可选；连接/认证/参数/容量变化后立即不可选，重新测试通过后恢复 |
| A108 | Archive | Archive/恢复可用且无物理删除；Archived Profile 不可用于新执行，所有历史引用完整 |
| A109 | 凭证隔离 | 每个 Profile 独占密钥槽位，明文不回显；替换只使本 Profile snapshot 失效 |
| A110 | Endpoint 边界 | 公网/loopback/局域网/私网 HTTP(S) 均可配置；URL 凭证和跨 Origin Redirect 拒绝，HTTP 有明文警告 |
| A111 | TLS | HTTPS 使用系统信任链验证证书与主机名；证书错误结构化失败且无绕过入口 |
| A112 | 认证与参数 | 仅 bearer/api_key_header/none；无任意 Header；扩展参数可透传但不能覆盖核心字段或传输配置 |
| A113 | Token 容量 | context/output 上限必填且关系合法；不按模型名推断；明确 context mismatch 终止 Run、失效 snapshot 并要求重测 |

## 12. 模型传输与上下文预算

| 编号 | 验收项 | 标准 |
|---|---|---|
| A114 | 轮换凭证 | 既有 Run 的下一次请求使用 Profile 当前密钥；Run 非密钥快照保持不变，Attempt 记录实际凭证 revision |
| A115 | 传输探测 | Test Connection 必须证明 HTTP 请求、SSE 完整终态、ToolCall/ToolResult 续接和 usage；不探测 WebSocket |
| A116 | 传输重试 | 首 delta 前 HTTP/SSE 瞬时错误最多重试 2 次且请求语义不变；首 delta 后禁止重放 |
| A117 | 重试作用域 | 重试只属于当前逻辑模型请求；不会切换协议或传输，瞬时故障不使 Profile capability snapshot 失效 |
| A118 | Attempt 与 usage | 每次网络发送独立 Attempt、共享逻辑请求 ID；usage 逐次保存，缺失标记 unknown 且汇总不按零计算 |
| A119 | 输入估算 | 稳定 canonical payload 的 UTF-8 字节、固定协议开销和 2% 有界 margin 按确认公式计算，发送前不得超预算 |
| A120 | 输出预留 | 普通/纠正请求使用 Profile 上限，Finalization 最多 4,096，探测最多 512；重放值不变且 Attempt 可审计 |
| A121 | 本地超限 | 不可裁剪输入超限时零 Provider 请求、Run failed/context_input_too_large、snapshot 保持有效并展示安全预算明细 |
| A122 | 请求周期 | 同一 Step 全部 HTTP/SSE Attempt 和退避共享 10 分钟；每次建连/首 delta/空闲不超过 15/180/120 秒；Finalization 独立 60 秒 |

## 13. 模型协议归一化

| 编号 | 验收项 | 标准 |
|---|---|---|
| A123 | 契约版本 | Run 固化 model request contract；规则升级使 Profile 重测，既有 Run 不被静默迁移，不支持版本时零模型请求 |
| A124 | Wire API | Responses 与 Chat Completions 均可显式配置并通过同一内部事件运行；不存在自动推断或跨协议 fallback |
| A125 | Endpoint | API root 正确追加固定 endpoint；已有 endpoint 输入拒绝；path prefix/query/Origin 规则保持 |
| A126 | Usage | 两种协议完整响应均有合法非负 input/output/total；Chat 请求 include_usage；缺失在探测失败、运行时 drift |
| A127 | ToolCall identity | 分片按协议索引稳定归并，内部/Provider ID 分离；缺失、重复、冲突或非法 JSON 零 ToolCall/Approval |
| A128 | ToolCall limits | 16 calls、1/2 MiB、16,384 deltas、名称与 JSON 深度/成员边界逐一强制，超限仅产生安全错误摘要 |
| A129 | Stream limits | 动态可见文本、2 MiB reasoning、1 MiB Event 和 8 MiB 总流边界生效；超限无重试且部分文本保持 incomplete |
| A130 | 完成判定 | Responses completed 与 Chat finish/usage/[DONE] 条件严格；EOF、length、filter 和未知终态分别正确映射 |
| A131 | Stateless | Responses store=false 且无 previous response；Chat 发送本地完整消息；重启、轮换密钥和 HTTP/SSE 重试不依赖 Provider history |
| A132 | 输出字段 | Responses 使用 max_output_tokens；Chat 探测并固化 max_completion_tokens 或 max_tokens，运行期不切换 |

## 14. 工具请求与结果契约

| 编号 | 验收项 | 标准 |
|---|---|---|
| A133 | 工具控制 | 非空工具集的普通/纠正请求固定 auto+parallel，空集固定 none 且省略 parallel；probe 单工具 required/non-parallel 后以 none 完成 ToolResult 续接；Finalization 无工具 |
| A134 | 非 strict 边界 | 两 wire 均显式 strict=false；即使 Provider 接受，本地 schema 与安全校验仍全部执行 |
| A135 | 封闭 schema | 根/嵌套/数组 object 的未知字段均被拒绝，零 ToolCall/审批/副作用；错误不回显值 |
| A136 | Effective arguments | 静态默认值仅对缺失可选字段生效；null/required 语义正确；hash、审批、存储与执行的参数完全一致 |
| A137 | Tool contract 恢复 | 旧 Run 使用固化契约且不降级当前安全底线；版本不可用时零模型/工具执行并失效待处理审批 |
| A138 | Schema Dialect | 内置 schema 只使用 Dialect v1；静态自检与代表性 probe 覆盖允许结构；Dialect 扩展使 Profile 失效并要求重测 |
| A139 | Available tool set | 每 Step 工具集只受固定可审计状态影响；工具定义 hash 与名称可追溯；同一逻辑请求的 Attempt 不变 |
| A140 | 可用性 race | 未暴露工具调用进入 Q45；已暴露工具后续不可用返回零副作用 unavailable 结果，不计协议错误 |
| A141 | ToolResult envelope | 每个进入后续上下文的已创建 ToolCall 恰好一个 canonical envelope；outcome/code/data 与数据库终态正确映射 |
| A142 | 结果关联与安全 | 两 wire 使用相同 canonical JSON；Provider ID 只用于 wire 关联；无自由文本旁路；结果扫描后才进入预算和上下文 |
| A143 | 结果版本与字节 | schema_version、递归 key 排序、数组保序、UTF-8/无 BOM/无空白及非法数字/重复键规则可用固定向量复现 |
| A144 | Summary | summary 只由 contract/tool/outcome/code 决定，单行且 <=1,024 bytes；任何调用级动态值均不进入 |
| A145 | Code registry | success code 为 null；其余 code 必填且闭合；Provider/OS 原始 code 被安全映射，消费者只精确匹配 |
| A146 | Result data | data 始终为按 contract/tool/outcome/code 选择的递归闭合 object；未知/动态 key、非法 null 和无界字段拒绝 |
| A147 | 结果故障隔离 | projector/schema/serializer 故障不发送 fallback；Run failed、真实副作用保留、Profile 不失效且 quarantine 跨重启 |
| A148 | Base 与 projection | base envelope/hash 不可变；Step projection/hash 可重建、同 Attempt 一致、只减少声明字段且记录省略量 |
| A149 | List result | entries 稳定有界；max_entries 截断与 Context 裁剪分离；根变化失败、子树变化仅标非快照；敏感项零计数 |
| A150 | Read result | 完整/head-tail/空文件 segments、脱敏后字节、源省略字节、行号与 evidence ranges 按固定规则一致 |
| A151 | Range result | complete 恒 false；空范围省略 actual/next；行数/字节双上限、stop reason 和 next line 可确定复现 |
| A152 | Search result | 稳定 matches、扫描计数、提前停止、Workspace 变化和长匹配 preview 均可复现；敏感文件不进入计数 |
| A153 | 文件变更 success | write/apply/delete 只在提交后复检通过时 success；返回安全相对路径、操作及前后版本事实，不返回内部审批/意图/OS 详情 |
| A154 | No-op result | 已存在目标的候选字节相同时返回 skipped/no_changes 与 path/base hash；新建空文件不是 no-op，竞态返回 conflict |
| A155 | Artifact success | 发布后稳定源与重开快照 hash/逻辑字节一致；结果可用稳定 id/version 引用且无 snapshot 路径或 URL |
| A156 | Shell terminal data | 已启动结果含确定进程终态、脱敏双流 observation 与安全 Workspace 变化；raw 字节、完整 manifest 和敏感路径不进入模型 |
| A157 | Shell outcome/code | exit、timeout、limit、signal、interrupt、capture 和 manifest 故障按固定映射与优先级产生唯一结果；安全变化不篡改进程事实 |
| A158 | 副作用标志 | 只读、未启动、no-op、verified success/not_applied 为 false；未知 commit 与已启动 Shell 为 true；屏障不能只由该布尔值推导 |
| A159 | 共享非成功 data | Reject 原因可选、已扫描且 <=4,096 UTF-8 bytes；unavailable 与默认 interrupted 为最小 data，已启动 Shell 保留专属事实 |
| A160 | Error data 分层 | ToolResult error 无通用 details/message/cause；JSON-RPC business error 不进入模型且按 code 闭合；内部错误不保存未扫描原文 |
| A161 | 只读 error data | 超大文件返回 actual/max size，超长行返回唯一 line/max；其他错误为零正文、零 evidence/matches/entries |
| A162 | 变更 error data | Patch hunk/reason、Diff actual/max 与候选 actual/max 可复现；版本冲突和 Artifact 错误不返回当前 hash、格式详情或内部路径 |

## 15. Runtime 启动、一致性与恢复

| 编号 | 验收项 | 标准 |
|---|---|---|
| A163 | ToolResult 数值 | safe integer 边界两侧、负零、指数和小数形式按固定规则接受或拒绝；真实测量溢出产生安全闭合错误且不失真 |
| A164 | Canonical Unicode | 补充平面字符、BMP/补充字符 key 排序、NFC/NFD、控制字符、U+2028/U+2029、孤立 surrogate 和递归重复 key 固定向量在 Python/JS 完全一致 |
| A165 | SQLite 迁移 | 迁移前一致备份、hash/manifest、受限事务、revision 与完整性复检全部通过才 ready；任一步失败保留原库和备份并保持 health-only |
| A166 | Shell guardian | nohup、background、double-fork、setsid、忽略 TERM 和 Main+sidecar 强杀向量均触发 guardian 有界清理；guardian 能力不可用时 Shell 不可用 |
| A167 | Ready gate | business JSON-RPC method 和调度在 ready 前不可用；启动恢复和必需安全自检完成后初始化只成功一次，局部 capability 降级被明确报告 |
| A168 | RunSnapshot 水位 | snapshot 与 through_event_id 来自同一读事务；原子安装后从水位续接无缺口，未知 schema 按规定 resnapshot 或兼容失败 |
| A169 | Workspace 身份 | 同路径替换、同身份移动、卸载重挂、卷身份缺失和显式恢复分别按身份与路径边界处理；旧 Approval 不复活 |
| A170 | 唯一 Runtime | 第二 UI 实例只激活已有窗口；第二 sidecar 无法取得状态目录 OS lock，不能删锁、按 PID 抢占或并行恢复 |
| A171 | Allowed actions | 每种 Run status、pause reason、Workspace/Approval 状态返回稳定闭合动作；所有持久化 RPC 在事务内重算并正确处理幂等与竞态 |
| A172 | Reconciliation epoch | 失败/中断/空 ToolCall 不解除屏障；正常只读 Step 的至少一个 success 可按匹配 epoch 清除，合法空结果可计，旧 Step 不能清除新 episode |

## 16. 协议、时间与存储故障

| 编号 | 验收项 | 标准 |
|---|---|---|
| A173 | Operation 幂等 | 同 operation ID/hash 重放原 result/error 且零重复状态/Event，不同 envelope 冲突；敏感拒绝不占 key，重放前重新校验授权事实，外部不确定操作不自动重发 |
| A174 | DTO 与分页 | request/response/error/IPC 递归闭合并由同源 validator 验证；cursor 绑定规范化 scope/filter/order，keyset+high-water 排除新增项且不使用 offset |
| A175 | Event contract | envelope/per-type payload 固定向量通过；重复、跳号、未知可忽略 type、已知未知 version、敏感替代和 resnapshot 防循环分别符合契约 |
| A176 | 时间与时钟 | 所有业务时间为 UTC Unix ms safe integer、duration 为 monotonic ms；同 boot 以 continuous deadline 延续，boot/timebase 不可证明或回拨时失效 timed approved Shell，重启不重置 TTL |
| A177 | Storage fail closed | state full/quota/I/O/corruption 与 Workspace 各提交阶段正确分类；reserve 仅在空间耗尽释放，恢复先 WAL/integrity/FK/intent 对账，零自动清理和副作用重放 |

## 17. Workbench 导航与模型配置

| 编号 | 验收项 | 标准 |
|---|---|---|
| A178 | 紧凑任务状态 | 每个任务只占一行且无“已完成”等第二行文字；未读完成/进行中/失败分别显示小绿点/小转圈/小红点，进入完成任务后绿点消失，新的 Run 开始后可产生下一次未读完成标识 |
| A179 | 配置入口 | 对话区不再显示模型配置横幅；左下角齿轮与当前模型摘要可打开配置页，第一版只包含模型列表和 API Key |
| A180 | 模型选择 | 新任务开始前可选择 `deepseek-v4-flash` 或 `deepseek-v4-pro`；默认使用 Runtime 返回的第一个可用模型，开始后本次任务的模型不可切换，空列表时禁止开始 |
| A181 | 标题与任务操作 | Session 顶部以较小字号显示标题和紧邻标题的紧凑三点菜单；内容区标题、三点菜单与左侧任务标题右键均提供重命名/删除；删除需确认、活动任务需先结束且 Workspace 文件保持不变；预览阶段、绝对路径和通用安全说明不出现在内容区 |
| A182 | 项目导航 | 项目使用展开/折叠文件夹图标并可折叠任务列表；按首次 Session 创建时间倒序，向已有项目新增任务不改变项目顺序；项目右侧加号直接在该 Workspace 创建 Session |
| A183 | Session 排版与 Markdown | 标题、正文、代码、caption 分别使用 16px/24px、14px/22px、13px/20px、12px token；模型正文的标题、列表、强调、引用和代码生成语义化 DOM，原始 HTML 不执行，图片不发起远程加载，链接不直接导航；用户消息仍按纯文本展示 |
