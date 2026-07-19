# Eidos MVP 第三期实施范围

版本：v0.3

状态：✅ 已完成（P3-00 至 P3-08 已验收）

## 1. 定位与优先级

第三期目标是把第二期已经固化的静态 Tool Registry 扩展为一个**可从本地 Plugin 加载 Skill 与 MCP Tool、可按 Step 冻结能力、并继续复用现有审批、安全扫描、Canonical ToolResult、Event 与恢复合同**的工具生态基础。

本期只选择“可扩展工具系统”这一条纵向链路：

```text
导入本地 Plugin
  -> 发现 Skill 与 MCP Server 声明
  -> 构建有来源、可搜索的 Tool Registry
  -> 为 Run/Step 冻结能力快照
  -> 模型读取 Skill 或调用 MCP Tool
  -> Runtime 校验、审批、沙箱执行
  -> Canonical ToolResult / Event / SQLite / UI
  -> 下一模型 Step
```

第一期 [MVP Lite](mvp-lite.md) 和已完成的 [第二期清单](mvp-phase-2.md) 仍是回归基线。P3-00 已将本期边界同步到 PRD、TDD 与 `decisions.md`；本文现在是第三期唯一实施清单。

## 2. 已确认前提

用户以“完成全部第三期的内容”确认以下最小边界：

1. Plugin v1 仅支持用户显式导入本地目录，不提供市场、远程下载、自动更新或分享。
2. Plugin 是 Skill 与 MCP Server 配置的包，不提供任意 Python/JavaScript 插件入口、Hook 或 App。
3. Skill v1 支持 `SKILL.md` 与包内只读资源；Skill 不自动执行脚本，也不能获得绕过现有 Tool/Approval/Sandbox 的执行通道。
4. MCP v1 仅支持 stdio Transport 和 Tools capability；不支持 Streamable HTTP、OAuth、Resources、Prompts、Sampling、Elicitation 或 Tasks。
5. MCP Tool annotation 不作为授权事实；全部 MCP Tool 默认视为外部副作用、单调用、逐次审批且不自动重试。
6. 本期新增一个官方 MCP Python SDK 依赖，固定稳定 v1 范围 `mcp>=1.27,<2`，不自行维护 MCP JSON-RPC 状态机。
7. 本期继续使用单执行器；不增加并行 ToolCall、多 Agent 或后台 daemon。

任一前提改变，都必须先调整本文范围、验收条件和非目标，再进入实现。

## 3. 第三期边界

### 本期交付

- 将内置工具、Skill 工具和 MCP 工具统一注册到同一个 Tool Registry；所有调用继续经过 RuntimeEngine 与 ToolDispatcher。
- 为 ToolSpec 增加稳定来源与执行 Adapter，消除模型工具定义、参数校验和执行映射之间的静态双重事实。
- 为 Run 固化 Plugin/Skill/MCP 配置快照，为每个 Step 固化实际可见工具集合与 hash。
- 支持本地 Plugin 的导入、列表、启用、禁用和移除；安装内容使用私有权限、原子提交和内容 hash。
- 支持 Skill 元数据发现、显式调用、正文读取和包内只读资源读取。
- 支持 MCP stdio Server 的初始化、工具发现、工具调用、工具列表变化和有界生命周期管理。
- 当外部工具数量超过直接暴露预算时，通过 `tool_search` 延迟发现，不把全部 Tool schema 注入每次模型请求。
- 为 MCP Server 启用和 MCP Tool 调用提供完整可见的用户审批、专用 Seatbelt profile、敏感扫描、输出限制和安全错误映射。
- Desktop Settings 展示 Plugin、Skill、MCP Server、工具来源、启用状态和安全错误；Execution Feed 展示 MCP ToolCall 生命周期。

### 本期不交付

- Plugin 市场、远程仓库、推荐、评分、搜索、下载、自动安装、自动更新、签名、分享和跨设备同步。
- Plugin Hook、App、任意 Runtime 代码入口和安装脚本。
- Workspace 内自动发现 Plugin；Plugin 只来自用户显式导入的受管目录。
- 无逐次审批的 Skill 自动生成、修改已有 Skill、创建资源文件、隐式执行脚本、独立包管理器和远程 Skill 安装。
- MCP Streamable HTTP、旧 HTTP+SSE、OAuth、动态客户端注册和 Token 管理。
- MCP Resources、Resource Templates、Prompts、Sampling、Elicitation、Tasks、Roots 写权限和服务端 Agent Loop。
- 信任 MCP `readOnlyHint` 等 annotation、永久免审批、按 Session 批准或自动批准。
- MCP Workspace 写权限、同时拥有 Workspace 读取和网络权限的 MCP Server profile。
- 并行 ToolCall、并行 MCP 调用、动态代码生成式 ToolCall 和完整 Codex Plugin 产品能力。
- Model Profile 重构、Responses Adapter、Public Mode、Artifact、managed proxy、Shell guardian、多 Agent、跨平台和后台运行。

## 4. 固定架构边界

第三期不改变现有进程和传输边界：

```text
React Renderer
  -> typed Preload API
  -> Electron Main
  <-> stdio JSON-RPC 2.0 / JSONL
  -> Python Runtime
       -> RuntimeEngine
       -> Extension Catalog
            -> Plugin Catalog
            -> Skill Catalog
            -> MCP Connection Manager
       -> Tool Registry Snapshot
            -> Builtin Tool Adapter
            -> Skill Resource Adapter
            -> MCP Tool Adapter
       -> Approval / Redaction / Sandbox / Canonical ToolResult
       -> SQLite State + Event
       -> DeepSeek Chat HTTP request / SSE response
```

- Electron Main 仍只拥有 sidecar 生命周期、双向 JSON-RPC、类型化 IPC、系统文件夹选择和审批 UI。
- Plugin、Skill、MCP 生命周期及所有工具执行均由 Python Runtime 管理，不在 Renderer 或 Main 重建第二套 Agent Loop。
- Eidos 本地控制面仍不开放 HTTP、WebSocket、Unix Socket 或随机端口。
- MCP stdio 是 Runtime 管理外部工具进程的内部连接，不是新的 Eidos 本地控制面。
- 模型只接收当前 Step 的不可变工具定义；重试必须复用完全相同的 tool set 与 hash。

## 5. 实施顺序与检查点

```text
P3-00 契约与基线
  -> P3-01 动态 Tool Registry
  -> P3-02 Run/Step 能力快照
  -> P3-03 Plugin v1
  -> P3-04 Skill v1
  -> P3-05 MCP Tools v1
  -> P3-06 外部工具安全与恢复
  -> P3-07 Tool Search 与上下文预算
  -> P3-08 Desktop 收口与第三期验收
```

- **检查点 A（P3-01 至 P3-02）**：所有现有内置工具通过统一 Registry 与 Adapter 执行；模型定义、Runtime 校验、执行入口和 Step 快照只有一个事实来源；一期和二期回归不变。
- **检查点 B（P3-03 至 P3-04）**：可以导入一个只含 Skill 的本地 Plugin；显式 `@skill` 和模型主动读取都能获得有界、可追溯内容，且不能执行包内代码。
- **检查点 C（P3-05 至 P3-07）**：一个 fixture MCP Server 可以安全发现、审批、调用并返回结果；大量工具通过搜索延迟暴露；崩溃和超时零自动重放。
- **发布检查点（P3-08）**：本地 Plugin -> Skill -> MCP Tool -> Approval -> ToolResult -> 下一 Step -> 重启读取的完整闭环通过。

## 6. 契约追溯

第三期从既有编号末尾追加 Q166-Q185、F146-F156、A185-A193，不改写一期、二期历史编号。

| 清单分组 | 决策/PRD | TDD 落点 | 自动化主位置 |
|---|---|---|---|
| P3-00 | Q166-Q185；F146-F156；A185-A193 | TDD 总览与各模块第三期小节 | `mvp-phase-3-baseline.md`、全量回归 |
| P3-01 | Q170-Q172；F146；A185 | 架构 §8；工具 §13 | `test_tool_registry.py`、`test_runtime_seams.py`、`test_deepseek.py` |
| P3-02 | Q171-Q173；F147；A186 | 模型 §11；协议/存储 §12 | `test_extension_storage.py`、`test_phase3_runtime.py` |
| P3-03 | Q166-Q167/Q173；F148；A187 | 架构 §8；协议/存储 §12 | `test_plugins.py`、`test_server.py` |
| P3-04 | Q168；F149；A188 | 工具 §13；模型 §11 | `test_skills.py`、`test_phase3_runtime.py` |
| P3-05 | Q169/Q174；F150；A189 | 工具 §13；协议/存储 §12 | `test_mcp.py`、MCP fixture |
| P3-06 | Q174-Q178；F151-F153；A190-A191 | 工具 §13；Desktop 第三期小节 | `test_mcp_sandbox.py`、`test_mcp.py` |
| P3-07 | Q179；F154；A192 | 模型 §11 | `test_tool_registry.py`、`test_phase3_runtime.py` |
| P3-08 | Q180；F155；A193 | Desktop 第三期小节；测试 §9 | Renderer/Main/sidecar tests、vertical fixture |

## 7. 详细实施清单

### P3-00：冻结第三期契约与回归基线

- [x] P3-00-01 记录第三期开工 commit、Runtime/SQLite/protocol/tool contract 版本和 `pnpm test`、macOS Seatbelt smoke 结果。
- [x] P3-00-02 在 `decisions.md` 追加 Plugin、Skill、MCP、外部副作用分类、审批和 Sandbox 决策；不修改 Q1-Q165 历史。
- [x] P3-00-03 更新 PRD：将“Plugin 市场、远程 Skill 安装和无审批生成”继续保留为非目标，将“本地 Plugin/Skill/MCP Tools v1”加入第三期实施目标。
- [x] P3-00-04 更新 TDD：定义 Extension Catalog、Tool Adapter、能力快照、MCP 生命周期、Skill 注入和 Desktop 合同。
- [x] P3-00-05 固定 Plugin Manifest v1、Skill Metadata v1、MCP Server Config v1、Tool provenance、Run extension snapshot 与 Step tool snapshot 的闭合 schema。
- [x] P3-00-06 为本文每项补齐正式 PRD 编号、TDD 小节和自动化测试位置。

验收：PRD、TDD、决策和本文对 Plugin/Skill/MCP 的范围、信任边界和非目标没有相反描述；未开始实现前所有新增协议与状态已冻结。

### P3-01：统一动态 Tool Registry

- [x] P3-01-01 将 ToolSpec、执行 Adapter 和 provenance 注册为单个不可变 Registry entry；provenance 至少包含 `builtin|skill|mcp`、source ID、source version 和 content hash。
- [x] P3-01-02 让现有八个内置工具通过 Builtin Tool Adapter 注册；删除 `ToolExecutor.validate_arguments()`、工具名集合和模型 `TOOL_SPECS` 之间可漂移的重复分支。
- [x] P3-01-03 ToolDispatcher 只从 Registry entry 取得 schema、effective arguments、side effect、approval、timeout、batch 和 executor，不按工具名猜测类别。
- [x] P3-01-04 Tool name 使用稳定 namespace：内置工具保留原名；MCP 工具使用 `mcp__<server_id>__<tool_name>`；Plugin/Skill 名称使用独立命名空间，不与 Tool name 混用。
- [x] P3-01-05 Registry 启动时验证名称唯一、schema dialect、默认值、result schema、Adapter 存在和 provenance 完整性；单个外部工具非法只隔离该工具，内置工具非法仍使 Runtime unavailable。
- [x] P3-01-06 ModelClient/ModelRunner 显式接收当前 Step 的 model-visible Tool definitions；DeepSeek Adapter 不再导入全局工具列表。

验收：现有文件与 Shell 工具的 ToolCall、审批、ToolResult 和 protocol fixture 字节语义保持一致；加入一个内存测试 Adapter 无需修改 RuntimeEngine 分支。

### P3-02：Run Extension Snapshot 与 Step Tool Snapshot

- [x] P3-02-01 Run 创建时固化 enabled Plugin ID/version/hash、Skill catalog hash、MCP Server config hash 和 extension contract version；后续全局启停不改写该 Run。
- [x] P3-02-02 每个 Step 在模型请求前持久化有序 available tool names、完整 ToolSpec hash、direct/deferred 集合、activated tool names 和 tool set hash。
- [x] P3-02-03 同一逻辑 ModelAttempt 重试必须复用相同 Step snapshot；若无法重建一致工具集，零 Provider 请求并进入安全失败。
- [x] P3-02-04 已经暴露但调用时不可用的工具生成 `tool_unavailable` Canonical ToolResult，不把调用重新解释成未知模型协议错误。
- [x] P3-02-05 Plugin 禁用只影响新 Run；移除先标记不可用于新 Run，非终态 Run 不再引用后才删除执行资源，历史仅保留安全元数据与 hash。
- [x] P3-02-06 snapshot、ToolCall provenance、ToolResult 和 Event 在一次执行及重启后可互相追溯，不依赖当前 Plugin 目录仍存在。

验收：Run 排队后修改全局 Plugin 状态不改变该 Run 的能力快照；MCP 工具列表在 Step 间变化不会改变已开始 Step 或其重试请求。

### P3-03：本地 Plugin v1

- [x] P3-03-01 定义 strict/closed Plugin Manifest v1，只接受 `schemaVersion,id,name,version,description,skills,mcpServers`；未知字段、重复 ID、非法路径和非法版本安全拒绝。
- [x] P3-03-02 通过系统目录选择显式导入本地 Plugin；Runtime 验证 owner、普通文件、非 symlink、文件数量、单文件/总容量和 manifest 后再原子安装到私有受管目录。
- [x] P3-03-03 导入不执行 `npm/pnpm/npx/uv/uvx/pip`、Shell 或包内安装脚本，不联网解析依赖。
- [x] P3-03-04 MCP command 只允许结构化 executable + argv；禁止 shell 字符串、命令替换、环境展开和导入时启动。
- [x] P3-03-05 提供闭合、幂等的 `plugin/list`、`plugin/import`、`plugin/setEnabled`、`plugin/remove` Runtime methods 与 Event；敏感错误不泄露真实内部存储路径。
- [x] P3-03-06 重复导入相同 ID/version/hash 返回原结果；相同 ID/version 不同内容明确冲突，不静默覆盖。
- [x] P3-03-07 禁用或移除 Plugin 不删除 Session、Run、Item、ToolCall、ToolResult 或 Event 历史。

验收：合法 Plugin 可跨重启保留；路径穿越、symlink、超限、重复冲突、未知字段和导入中断均不会留下半安装包或启用状态。

### P3-04：Skill v1

- [x] P3-04-01 从已启用 Plugin 的 skill root 加载 `SKILL.md`；解析有界 `name`、`description` 和可选展示元数据，记录 Plugin provenance 与 content hash。
- [x] P3-04-02 Skill name 在 Plugin namespace 内唯一；跨 Plugin 同名使用限定名，不按加载顺序静默覆盖。
- [x] P3-04-03 当前 Step 只注入有界 Skill catalog 元数据，不默认注入全部正文；catalog 超出预算时确定性裁剪并保留检索入口。
- [x] P3-04-04 用户显式 `@skill` 时在模型请求前加载对应 `SKILL.md`；模型可通过内置 `skill_read` 和 `skill_read_resource` 获取正文或包内资源。
- [x] P3-04-05 Skill/resource 读取严格限制在对应 system/user/Plugin source root，拒绝绝对路径、`..`、symlink、特殊文件、二进制、非 UTF-8 和容量超限。
- [x] P3-04-06 Skill 内容按不可信上下文处理：先敏感扫描、有来源标签和大小上限，不能覆盖 Eidos system/runtime 安全规则。
- [x] P3-04-07 `scripts/` 只可作为文本资源读取；本期不提供脚本执行 Adapter。Workspace 内脚本需要执行时使用现有 `run_shell` 并遵守审批与沙箱；需要网络的安装 helper 只能由用户在系统 Terminal 显式运行。
- [x] P3-04-08 将内置 Skill 作为 Runtime 资源随应用发布并原子部署到 `${EIDOS_DATA_DIR}/skills/.system`；用户 Skill 固定为 `${EIDOS_DATA_DIR}/skills/<name>`，不支持单数 `skill/` 根。
- [x] P3-04-09 Catalog 合并 `system:`、`user:` 和 Plugin namespace，并把完整本地 Skill 树 hash 固化进 Run；运行中修改资源不会静默生效。
- [x] P3-04-10 将 `skill-installer`、`skill-creator`、`plugin-creator`、`review-agent` 的 Codex 专属路径和合同替换为 Eidos v0.3 合同；远程安装 helper 由系统 Terminal 显式运行，不新增 Agent Shell 越权通道。
- [x] P3-04-11 新增 direct、single、approval-required 的 `skill_create` Eidos-state Tool；只接受 `name`、`description`、`instructions`，拒绝路径注入与 system/user 同名覆盖，以 diff 审批、Durable Intent 和原子 `0700/0600` 写入创建 `${EIDOS_DATA_DIR}/skills/<name>/SKILL.md`，拒绝审批时零副作用。
- [x] P3-04-12 系统 Skill 完整树继续要求当前 owner 与精确 `0700/0600`；用户 Skill 只要求完整树归当前用户所有，不因手工复制产生的 `0755/0644` 等 mode 被过滤，symlink、特殊文件和内容边界不放宽。

验收：一个 Skill-only Plugin 可被导入、列出、显式调用和读取引用资源；恶意路径或指令不能读取 Plugin 根外内容或绕过工具审批。

### P3-05：MCP Tools v1

- [x] P3-05-01 使用官方 Python SDK 的稳定 v1 Client；实现 stdio `initialize`、`initialized`、分页 `tools/list`、`tools/call`、`notifications/tools/list_changed` 和有界 shutdown。
- [x] P3-05-02 MCP Server Config v1 固定 server ID、executable、argv、显式 env names、permission profile、startup timeout、tool timeout 和 enabled 状态；配置不接受 shell command。
- [x] P3-05-03 MCP Server 只在 Plugin 已启用且用户完成 Server 启用同意后启动；可选 Server 启动失败只标记该能力 unavailable，不使内置 Runtime health-only。
- [x] P3-05-04 MCP `tools/list` 分页有总页数、工具数、schema bytes 和 deadline 上限；名称映射确定且碰撞时隔离冲突工具。
- [x] P3-05-05 MCP input schema 只在完整兼容 Eidos Tool Schema Dialect 时注册；不得静默删除 `$ref`、组合 schema、自由 map 或其他不支持关键字。
- [x] P3-05-06 MCP ToolResult v1 只接受有界 text 与 `structuredContent`；Image、Audio、Embedded Resource、Resource Link 和未知 content type 返回明确不支持结果，不进入模型或 UI。
- [x] P3-05-07 MCP `isError=true` 映射为工具错误结果而非传输失败；协议错误、stdout 污染、未知 response ID、超限和进程退出使用闭合安全 code。
- [x] P3-05-08 `tools/list_changed` 只使下一 Step 重新构建 Registry snapshot，不修改当前 Step 或正在重试的 ModelAttempt。

验收：fixture Server 覆盖初始化、分页发现、调用成功、`isError`、列表变化、超时、取消、非法 schema、stdout 污染和崩溃；所有路径均产生唯一 ToolResult 或在 ToolCall 创建前安全拒绝。

### P3-06：外部工具审批、Sandbox 与恢复

- [x] P3-06-01 将 ToolSpec `side_effect` 显式扩展为 `external`；所有 MCP Tool 固定 `external`、禁止 batch、逐次审批，不信任 Server annotation 降级权限。
- [x] P3-06-02 启用本地 MCP Server 前展示完整 executable、argv、Plugin ID/version/hash、env names 和 permission profile；用户取消时零进程启动、零凭证释放。
- [x] P3-06-03 增加专用 `connector` Seatbelt profile：允许受控运行时和 Plugin 内容只读、私有临时 HOME/TMP 与网络；拒绝 Workspace、真实 Home 和 `~/.eidos` 读取。
- [x] P3-06-04 增加专用 `workspace_read` Seatbelt profile：只读当前 Workspace、受控运行时和 Plugin 内容；拒绝网络、Workspace 写入、真实 Home 和 `~/.eidos`。
- [x] P3-06-05 两个 profile 互斥；本期没有 Workspace 写 MCP profile，也没有同时获得 Workspace 读取与网络的 profile。
- [x] P3-06-06 MCP ToolCall 参数在 Approval 和发送前完成 effective arguments、敏感扫描和 hash 绑定；Approval 不能修改参数、Server、profile、timeout 或 env。
- [x] P3-06-07 MCP 结果在进入 ToolResult、模型、UI、Event、SQLite 和日志前统一扫描、递归限深、限项和限字节；Server stderr 只生成有界安全诊断，不作为 ToolResult。
- [x] P3-06-08 `tools/call` timeout、取消、连接断开或结果提交失败均不自动重试；结果标记 `side_effects_may_exist=true` 并让 Run 进入 `waiting_user_input`，不得由 Workspace 只读工具自动清除外部不确定性。
- [x] P3-06-09 sidecar 退出时有界终止 MCP 进程组；失联进程、迟到 response 和旧 Approval 不得进入新 Run 或新 Step。

验收：macOS 原生测试证明两个 profile 的文件/网络矩阵、子进程继承、真实 Home/状态目录 deny、timeout/cancel 清理和零自动重放；安全核心失败时 MCP unavailable，但内置只读闭环仍可工作。

### P3-07：Tool Search 与有界上下文

- [x] P3-07-01 Registry 将工具分为 direct 和 deferred；内置工具、`skill_read`、`skill_read_resource`、`skill_create`、`tool_search` 始终 direct，外部工具按确定性预算进入 direct 或 deferred。
- [x] P3-07-02 `tool_search` 对 name、description、Plugin、Server 和 Skill dependency metadata 做本地有界检索，返回稳定排序、来源和截断原因，不调用模型或外部网络。
- [x] P3-07-03 search 命中的工具从下一 Step 起加入 activated tool set；当前 Step 不能在收到搜索结果的同一模型响应中调用尚未暴露的工具。
- [x] P3-07-04 activated tool set 持久化在 Run/Step snapshot 中并受数量、单 schema bytes、总 schema bytes 和模型输入预算约束。
- [x] P3-07-05 显式 Plugin/Skill mention 可为下一 Step 提供确定性候选，但不能绕过 enabled 状态、schema 校验、审批或 Sandbox。
- [x] P3-07-06 Context Builder 统计 Skill metadata、加载的 Skill 正文、Tool schema 和 ToolResult 投影的实际 UTF-8 bytes；超限时确定性裁剪 deferred 能力，不能裁掉安全指令。
- [x] P3-07-07 同一 Step 的 available tools 顺序、serialized schema 和 hash 在重启与重试后可复算一致。

验收：包含 100+ fixture MCP Tools 的目录不会把全部 schema 注入模型；模型可搜索、在下一 Step 调用目标工具，且请求预算、顺序和 hash 确定可测。

### P3-08：Desktop 收口与第三期发布验收

- [x] P3-08-01 Settings 增加 Plugins、Skills、MCP Servers 三个受控区域；支持本地导入、启停、移除、状态和安全错误展示，不提供市场入口。
- [x] P3-08-02 Renderer/Preload/Main 对 Plugin、Skill、MCP、Tool provenance 和 Server enable approval DTO 使用闭合校验；未知字段和旧响应不进入 UI 状态。
- [x] P3-08-03 Execution Feed 展示 MCP Tool 的 Plugin、Server、工具名、审批、运行状态、耗时和安全结果；不展示 env value、内部安装路径、原始 stderr 或协议错误详情。
- [x] P3-08-04 Plugin 启停、MCP Server 状态、工具列表变化和 ToolCall 生命周期写入有界 Event；页面从 snapshot + Event 水位恢复。
- [x] P3-08-05 完成一个真实纵向 fixture：导入包含一个 Skill 和一个 MCP Server 的 Plugin，Skill 引导模型搜索并调用 MCP Tool，用户审批后模型使用结果完成 Run。
- [x] P3-08-06 重启后可以读取 Plugin 状态、Run extension snapshot、Step tool snapshot、ToolCall provenance 和 Canonical ToolResult；不恢复或重放未完成 MCP 调用。
- [x] P3-08-07 通过 Runtime、协议 fixture、Renderer state、真实 sidecar、MCP fixture、存储迁移、崩溃注入和 macOS Seatbelt 原生回归。
- [x] P3-08-08 更新 PRD/TDD 实施状态、测试里程碑和本文复选框；确认所有非目标仍标记未实现。

验收：本清单全部勾选，完整 Plugin -> Skill -> Tool Search -> MCP Tool -> Approval -> ToolResult -> 下一 Step -> 重启读取闭环通过，才能将第三期标记为完成。

## 8. 预期代码落点

以下是范围落点，不要求为每个名称新建文件；实现时优先复用现有模块，只有真实第二个 Adapter 或独立生命周期出现时才拆分。

```text
runtime/eidos_runtime/
  resources/skills/.system/ # 随 Runtime 发布的内置 Skill 源
  extensions/             # Plugin manifest/catalog/import；Skill catalog；MCP manager
  runtime/
    tool_dispatcher.py    # 只依赖 Registry snapshot，不按工具名分支
    model_runner.py       # 接收 StepContext/可见工具定义
  tools/
    registry.py           # ToolSpec + Adapter + provenance 的深模块
    workspace.py          # 内置 Workspace Adapter 的现有实现
  sandbox/
    mcp_connector.sbpl    # connector：网络 + Plugin 只读
    mcp_workspace_read.sbpl # workspace_read：Workspace + Plugin 只读
  db/storage.py           # Plugin/Run/Step/ToolCall provenance 与 snapshot
  protocol/schemas.py     # 闭合 Plugin/Skill/MCP DTO

desktop/
  main/                   # 类型化 Runtime methods 与 Server enable approval
  renderer/               # Settings、状态和 Execution Feed

protocol/fixtures/        # Plugin/Skill/MCP/Tool snapshot 跨语言向量
runtime/tests/            # Registry、Plugin、Skill、MCP、Sandbox、恢复测试
```

不为 `PluginService`、`SkillService`、`McpService` 各自再套一层只转发一次的 interface。外部 seam 只有：Extension Catalog 产出不可变快照，Tool Registry 将 ToolSpec 映射到执行 Adapter。

## 9. 验证命令

实现阶段至少执行：

```bash
.venv/bin/python -m unittest discover -s runtime/tests -v
pnpm test
pnpm build
git diff --check
```

发布检查必须在原生 macOS 环境执行 MCP Server Seatbelt、进程组清理、网络/文件权限矩阵和 Electron smoke；受限容器结果不能替代原生安全结论。

第三期发布验收结果（2026-07-20）：

- 全量回归：Runtime 181 项、Renderer 18 项、Main/sidecar 15 项全部通过。
- 原生 macOS 回归包含 connector/workspace_read Seatbelt 权限矩阵、官方 MCP Client fixture、进程退出、超时、进行中取消和零自动重放。
- `pnpm build` 与 `git diff --check` 通过；协议与 Runtime 版本为 `0.3.0`，SQLite schema revision 为 5。

## 10. 第三期完成标准

- 所有 P3-00 至 P3-08 条目均勾选，并具有代码、自动化和文档三方证据。
- 内置工具的一期、二期协议、审批、安全和恢复行为没有回归。
- 任一模型可见工具都能追溯到唯一 Registry entry、provenance、Step snapshot、ToolCall 和 Canonical ToolResult。
- Plugin 或 MCP 故障不会绕过审批/Sandbox，也不会让 Runtime 自动重放外部副作用。
- Plugin 的增长不要求在 RuntimeEngine 中增加工具名分支。
- Skill 的增长不要求把所有正文和 Tool schema 注入每个模型请求。
- 禁用或移除 Plugin 不破坏历史 Run 的可解释性。
- 本期非目标没有被实现状态、UI 文案或文档暗示为已支持。

## 11. 后续阶段入口

第三期完成后再单独选择后续方向：

- MCP Streamable HTTP + OAuth +安全凭证存储。
- Plugin 市场、签名、远程安装和更新。
- Skill 脚本的受控物化与执行合同。
- MCP Resources/Prompts 与更丰富的多模态 ToolResult。
- 外部工具的细粒度信任策略和可审计的免审批规则。
- Model Profile/Capability Snapshot/Responses Adapter，或 Public Mode/Artifact。

这些能力不得以“兼容未来”为由提前加入第三期接口、表结构或 UI。
