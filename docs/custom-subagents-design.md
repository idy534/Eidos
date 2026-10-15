# 自定义子 Agent 设计方案

日期：2026-10-15。

状态：核心能力已在 `codex/custom-subagents` 分支实现。本文记录配置规范、实际实现与待补充项。功能尚未发布；验证范围与限制见第 15 节。

## 1. 目标与已确认约定

Eidos 允许用户通过 TOML 文件定义新的子 Agent 类型。用户可以配置模型，并通过 `instructions` 字段定义该类型独有的职责、约束和任务背景。

自定义类型与 `default`、`worker`、`explorer` 使用相同的运作方式。父 Agent 继续通过 `spawn_agent` 创建子任务，并通过现有工具发送消息、续派、等待和停止子任务。

用户可以从 Session 输入框左下角的 `+` 显式引用自定义 Agent。父 Agent 会在本轮上下文中收到这项选择，再通过现有工具调用该类型。Runtime 不新增自动派发流程，也不新增“主要任务必须由某个 Agent 完成”的检查。

本方案采用以下约定：

- 一个 TOML 文件定义一个可重复使用的子 Agent 类型。
- TOML 的 `instructions` 保存完整自定义指令。用户不需要单独维护 `agent.md`。
- 自定义类型继续使用现有 Agent Loop、RunSupervisor、Session、Run、SQLite 和 Event/Outbox。
- 子 Agent 继续继承父任务的权限、审批模式、Workspace 绑定和扩展范围。
- 同一个类型可以创建多个独立任务实例。Runtime 不把类型名称当作运行实例 ID。

## 2. 实施前的实现与本次扩展位置

| 范围 | 实施前的源码事实 | 本次变化 |
| --- | --- | --- |
| 类型 | `AgentRole` 是三个名称组成的固定枚举 | 调用参数接受内置名称或已登记的自定义名称 |
| 默认值 | `SpawnAgent.role` 默认为 `default` | 旧调用保持原行为 |
| 模型 | 子 Run 创建时复制父 Run 的模型与模型快照 | 自定义类型可以选择已有模型配置 |
| 指令 | `InstructionResolver` 根据持久角色生成内置职责指令 | 子 Run 额外获得冻结的自定义指令 |
| 执行 | 子任务复用普通文件、Shell、Skill、图片和启用的扩展工具 | 自定义类型复用相同执行链 |
| 协作 | 父任务使用现有六个协作工具 | 自定义类型复用相同工具和状态转换 |
| 输入 | 输入引用支持 Skill、MCP、Plugin、会话等类型 | 输入引用增加 `agent` |
| 展示 | AgentPanel 按三个内置名称生成标签 | 面板显示自定义名称、基础职责和实际模型 |
| 持久化 | `agent_delegations.role` 受固定名称 CHECK 约束 | 数据库接受合法类型名，并保存定义快照关联 |

当前实现入口：

- [类型与调用参数](../runtime/eidos_runtime/domain/collaboration.py)。
- [协作工具](../runtime/eidos_runtime/tools/collaboration.py)。
- [协作应用](../runtime/eidos_runtime/application/collaboration.py)。
- [子 Run 创建与结果摘要](../runtime/eidos_runtime/persistence/collaboration.py)。
- [指令组装](../runtime/eidos_runtime/model/instructions.py)。
- [子任务工具范围](../runtime/eidos_runtime/runtime/run_resources.py)。
- [输入引用](../runtime/eidos_runtime/domain/input_reference.py)、[输入引用应用](../runtime/eidos_runtime/application/input_context.py)。
- [输入候选列表](../desktop/renderer/src/components/InputPicker.tsx)、[子任务面板](../desktop/renderer/src/components/AgentPanel.tsx)。

## 3. 定义目录与身份

定义目录采用 `~/.eidos/agents/`。该目录与现有 Eidos 数据目录保持一致。

Runtime 应从现有数据目录派生 Agent 目录。用户设置 `EIDOS_DATA_DIR` 后，Runtime 使用 `<EIDOS_DATA_DIR>/agents/`，不同时读取真实 HOME 下的定义。

```text
~/.eidos/
├── models.json
└── agents/
    ├── luna-work.toml
    ├── reviewer.toml
    └── docs-researcher.toml
```

首版只扫描该目录的直接子级 `*.toml` 文件。首版不增加项目级目录、Plugin 分发、目录递归和定义继承链。

`name` 是类型身份。文件名建议与 `name` 相同，但 Runtime 以字段值为准。文件改名且 `name` 不变时，类型身份不变。用户修改 `name` 时，Runtime 将其视为另一个类型。

名称使用 `^[a-z][a-z0-9_-]*$`，长度为 1～64 个字符。`default`、`worker`、`explorer` 是保留名称。重复名称的定义都应标记为不可用，Runtime 不按扫描顺序选择其中一个。

## 4. TOML 字段规范

| 字段 | 类型 | 必填 | 默认值与含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 首版固定为 `1` |
| `name` | string | 是 | 子 Agent 类型名称，也是 `spawn_agent.role` 的取值 |
| `description` | string | 是 | 简短说明该类型适合处理什么任务 |
| `enabled` | boolean | 否 | 默认 `true`；用户可以停用该类型 |
| `base_role` | string | 否 | 默认 `default`；只能是三个内置角色之一 |
| `instructions` | string | 是 | 非空多行文本，保存职责、约束和任务背景 |
| `model` | table | 否 | 整表省略时，子 Run 完整继承父 Run 的模型快照 |
| `model.provider_id` | string | 与 `config_id` 成对 | 提供商 ID，例如 `volcengine`；Runtime 校验实际配置的提供商 |
| `model.config_id` | string | 与 `provider_id` 成对 | 引用 Eidos 已有模型配置的 `id` |
| `model.reasoning_selection` | string | 否 | 使用目标模型支持的 Eidos 思考档位 |

Runtime 拒绝未知字段和不支持的 `schema_version`。`model` 表出现时，至少包含一个受支持的设置。`provider_id` 和 `config_id` 必须同时填写；仅调整继承模型的思考档位时，这两个字段都应省略。

首版不支持 `instructions_file`、`agent_md_path`、远程 include、环境变量替换或模板执行。TOML 解析器将 `instructions` 作为普通文本读取。模型可以按该字段中的任务要求使用工具，但配置加载过程不执行其中的命令或代码。

`base_role` 只提供基础职责。`instructions` 在此基础上补充该类型的具体工作方式。`base_role = "explorer"` 不会建立强制只读权限。

## 5. 完整 TOML 示例

以下示例包含首版的全部配置字段。示例文件名是 `luna-work.toml`。类型名称本身不决定模型。

示例引用当前源码 Catalog 中的 `deepseek-v4.1-flash`。用户需要先在 Eidos 中保存该模型配置，并确认配置支持 `high` 档位。该示例不表示用户已经配置该模型，也不表示真实 Provider 请求已经验证通过。用户可以将 `config_id` 换成自己已有的配置 ID。

```toml
# 文件位置：~/.eidos/agents/luna-work.toml
# 用户设置 EIDOS_DATA_DIR 后，文件应位于 <EIDOS_DATA_DIR>/agents/。

schema_version = 1

# name 是父 Agent 调用这个类型时使用的名称。
name = "luna-work"
description = "负责范围明确的代码实现、问题修复和定向验证。"
enabled = true

# base_role 提供基础职责，不改变权限和审批模式。
base_role = "worker"

# instructions 保存这个子 Agent 独有的约束、执行背景和交付要求。
# 多行单引号字符串会保留正文中的反斜杠，便于书写路径和代码片段。
instructions = '''
你是负责实现与验证的子 Agent。
父 Agent 会向你提供具体任务、修改范围、必要背景和验收要求。
你的默认工作方式是完成范围明确的实现任务，并向父 Agent 返回可核对的结果。

任务背景：
你参与的是已有软件项目。项目可能包含用户和其他 Agent 尚未提交的修改。
你需要根据当前源码判断事实。文档和历史对话可以提供背景，但不能替代源码证据。
你需要保留父 Agent 提供的目标、阶段限制和验收标准。

执行约束：
1. 你需要先理解父 Agent 的分工，再读取直接相关的代码。
2. 你需要在分配的文件和职责范围内工作。
3. 你需要保留用户和其他 Agent 的修改，并适应已有改动。
4. 如果父 Agent 只要求分析，你不能修改代码。
5. 如果父 Agent 要求先实现、后确认测试，你需要遵守该阶段要求。
6. 如果项目规则没有要求完整回归，你应优先选择覆盖改动行为的定向验证。
7. 如果你发现范围外问题，你需要记录证据并报告父 Agent。
8. 如果你缺少任务信息或需要额外权限，你需要通过 send_message 联系 parent。
9. 你需要遵守当前 Run 的权限、审批和文件版本核验结果。
10. 如果你遇到不确定副作用，你需要如实报告，并遵守 Runtime 的恢复要求。

交付要求：
你需要说明实际完成的工作和修改的文件。
你需要说明运行过的验证、验证结果和未运行的重要检查。
你需要区分源码事实、推断和仍未确认的行为。
你需要说明未完成事项及其具体原因。
你需要使用简短、明确的中文向父 Agent 报告。
'''

[model]
# provider_id 是 Eidos 提供商 ID，不是展示名称。
provider_id = "volcengine"

# config_id 引用该提供商的已有模型配置，不保存 API Key 或请求地址。
config_id = "deepseek-v4.1-flash"

# 目标配置需要支持该档位。Runtime 会校验实际配置和模型能力。
reasoning_selection = "high"
```

用户希望完整继承父 Run 模型时，可以删除整个 `[model]` 表。用户只想调整父模型的思考档位时，可以仅保留 `model.reasoning_selection`。

## 6. 模型解析与优先级

| 定义内容 | 子 Run 的模型行为 |
| --- | --- |
| 没有 `model` 表 | 子 Run 继承父 Run 的模型与思考档位 |
| 同时指定 `provider_id` 和 `config_id` | 子 Run 校验提供商后使用该配置，并采用目标模型的默认思考档位 |
| 只有 `reasoning_selection` | 子 Run 使用父模型，并校验指定档位 |
| 指定提供商、配置 ID 和思考档位 | 子 Run 校验提供商后使用指定配置和指定档位 |

思考档位使用 Eidos 当前的配置类型：`none`、`thinking`、`low`、`medium`、`high`、`max`。每个模型实际支持的集合仍由现有模型配置与 Catalog 解析结果决定。Runtime 不能把这份总集合当作每个模型的能力声明。

首版不增加 `spawn_agent` 的临时模型覆盖参数。父 Agent 选择类型后，Runtime 按该定义解析模型。这个规则可以避免“定义配置”和“调用参数”同时竞争优先级。

自定义文件不保存 API Key、Base URL、HTTP 参数和 Provider 实现。Runtime 复用现有 ModelConfig、Model Gateway、Run 模型快照和 Model Lease。不同类型可以引用不同 Provider 的已配置模型。

Runtime 应在创建子 Run 前检查配置可用性、工具能力和思考档位。模型选择错误应作为普通工具错误返回父 Agent。Runtime 不静默改用父模型或其他模型。

Run 快照只保存无凭证的模型选择与参数。凭证继续由现有 Model Gateway 管理。恢复时如果配置缺失，Runtime 应明确报告；恢复不能默默选择另一个模型。首版不承诺跨配置编辑精确复现历史 Provider 行为。

## 7. 调用方式与显式引用

### 7.1 统一调用链

```text
父 Run 获得固定的 Agent 目录
  → 父 Agent 选择类型
  → spawn_agent(role="luna-work", taskName=..., message=...)
  → Runtime 解析本轮固定的定义和模型
  → 既有权限与 Durable Intent 流程
  → 创建内部子 Session 和子 Run
  → 既有 RunSupervisor / RuntimeEngine
  → 父 Agent 读取结果并继续任务
```

旧调用仍然有效。调用省略 `role` 时仍使用 `default`。调用内置名称时保留原有职责和模型继承行为。

自定义类型也继续使用 `send_message`、`followup_task`、`list_agents`、`wait_agents` 和 `stop_agent`。`required_for_completion` 保留现有含义，不因类型变化而增加成功判定。

### 7.2 输入框中的 Agent 引用

`+` 候选列表新增 Agent 分类。候选列表显示名称、描述、模型、启用状态和不可用原因。首版展示自定义类型；父模型仍能通过原工具描述使用三个内置角色。

用户可以同时选择多个自定义类型。界面按类型去重。每个标签都关联一份不可变输入引用，引用类型为 `agent`。用户点击标签时可以查看完整配置预览。

`input/prepare` 接受类型名称，Runtime 负责解析定义。Renderer 不能通过引用参数提交任意指令正文、模型参数或权限配置。

父 Run 上下文只获得 Agent 目录摘要和本轮显式选择。系统提供的说明应表达：用户本轮选择了这些子 Agent，父 Agent 应根据任务分工通过现有工具调用它们。父 Agent 不会因此被替换成所选类型。

本轮选择与历史引用应分开处理。历史用户消息中的 Agent 标签继续作为历史事实展示，但不会自动激活本轮选择。运行中追加消息继续走现有输入流程；新 Agent 引用首版只通过新 Run 的输入接纳，不增加运行中切换定义的特殊路径。

### 7.3 实例与续派

`name` 标识可复用类型，`taskName` 标识本次分工，`agentId` 标识具体实例。父 Agent 续派时继续使用 `agentId`。

忙碌实例收到 `followup_task` 时仍接收消息。空闲实例收到续派时仍创建新子 Run。续派沿用该实例原有定义快照、基础职责和无凭证模型参数。

## 8. 指令内容与上下文

Runtime 只向实际创建的子 Run 注入完整 `instructions`。父 Agent 发现类型时只需要名称、描述、基础职责、模型摘要和可用状态。

子任务上下文由以下内容组成：

1. Runtime 现有的系统约束、执行规则和实际权限说明。
2. `base_role` 对应的内置职责。
3. 当前定义快照中的 `instructions`。
4. 父 Agent 提供的任务、范围、背景和验收标准。
5. 按既有规则取得的项目规则、Skill 和协作消息。

这份列表说明内容来源，不单独重写现有指令优先级。自定义正文应作为来源明确的用户配置指令层注入，不能因为字段名或文件位置而获得 Runtime 策略权限。用户本轮要求和项目规则优先于 Agent 的默认工作习惯。

Agent 定义描述长期职责和常用背景。`spawn_agent.message` 描述本次具体任务。Runtime 不把每次任务回写到 TOML。自定义类型首版不自动复制父任务的完整对话，也不增加跨类型共享的独立记忆。

定义正文必须进入现有上下文预算与 ContextSnapshot。Runtime 不能静默截断约束后继续运行；必要指令超出预算时，Runtime 应返回明确错误。

## 9. 定义发现、校验与快照

一个 AgentCatalog 模块集中负责扫描、TOML 解析、字段校验、模型可用性诊断和定义快照。调用方只使用列目录与解析冻结定义的接口，不各自读取文件。

解析采用 Python 标准库 `tomllib`，字段校验采用现有 Pydantic v2。首版不增加生产依赖。

建议首版设置以下有界输入限制：每个 TOML 最多 64 KiB，`instructions` 最多 48 KiB UTF-8，`description` 最多 1,000 字符，每次最多扫描 128 个定义文件。文件大小上限可以让完整配置预览继续使用现有输入正文预算。扫描超限时，Runtime 应明确报告，不能把目录中的未扫描定义当作不存在。实际投影还需要遵守现有协议响应大小和上下文预算。

Runtime 应沿用现有文件安全要求，核验目录范围、普通文件类型、owner、符号链接和读取期间的文件身份。配置加载不执行代码，也不扩展子任务的文件权限。普通 Agent 工具修改配置仍受现有数据目录保护和审批规则约束。

单个定义损坏时，其他定义继续可用。列表保留出错文件与可读原因。用户显式选择一个不可用定义时，Runtime 拒绝该选择。

刷新与冻结规则如下：

| 时机 | Runtime 行为 |
| --- | --- |
| 用户打开候选列表 | Runtime 刷新目录和诊断 |
| 用户选择引用 | Runtime 保存定义版本与预览快照 |
| Runtime 接纳新 Run | Runtime 固定有界目录及定义正文，校验显式引用是否仍匹配 |
| 父 Agent 调用类型 | Runtime 从父 Run 固定目录解析定义，并保存子任务实际配置 |
| 用户修改、停用或删除文件 | 后续新 Run 使用新目录；已有 Run 仍使用原快照 |
| 子任务续派或 Runtime 恢复 | Runtime 使用已保存快照，不重新读取当前 TOML |

AgentCatalog 可以缓存当前目录，但缓存不能成为执行事实来源。SQLite 与现有 Blob 存储保存执行快照。定义正文已经保存后，运行中的子任务不需要持有 TOML 文件租约。

首版不增加文件监听。目录刷新与 Run 接纳已经覆盖必要更新点。

## 10. 权限、模式和生命周期

自定义定义只能设置本方案列出的字段。Runtime 应拒绝 `approval_mode`、`sandbox_mode`、额外路径、网络授权、环境变量和嵌套委派开关等未知字段。

所有类型继续使用现有权限物化、审批、Seatbelt、Durable Intent、版本检查、结果核验和 Reconciliation。用户已经通过 Desktop 确认的 `full_access` 继续按现有规则继承；定义文件本身不能开启或关闭该模式。

子任务继续共享父任务的 execution root。子任务不拥有父 Worktree，不增加独立 Worktree 和自动合并流程。父 Agent 继续负责并行修改的文件分工。

子任务不能再次派生后代，也不能直接调用 `request_user_input` 或 `request_permissions`。子任务需要通过 `send_message` 联系父 Agent。工具执行中的既有审批继续在现有界面展示。

每个父 Run 最多创建 16 个子 Session，最多同时执行 8 个子 Run。计数只作用于该父 Run。该设计不新增跨 Session、跨父 Run 的全局限制。

父任务取消、孤立子任务清理、持久等待、Shutdown 和重启恢复继续使用现有机制。定义编辑不构成重新执行副作用的理由。迟到结果不能覆盖取消，不确定副作用仍进入 Reconciliation。

实施前的子 Run 创建路径没有传递 `work_mode`。本次已统一补齐父任务模式传递。父任务处于 Plan 模式时，子任务应保留相同阶段约束。子任务可以返回调查和计划草稿。子任务工具集合不暴露 `write_plan`，主 Session 继续负责用户确认与计划提交流程。

## 11. 协议、数据与 Desktop 范围

### 11.1 协议

`spawn_agent.role` 从固定枚举扩展为有格式限制的类型名称。Runtime 再依据父 Run 固定目录校验名称是否存在。内置基础职责仍使用三个值组成的枚举。

新增 `agent/catalog` 读取目录和诊断。现有 `agent/read` 与 `agent/stop` 继续管理运行实例。Agent 引用继续使用 `input/prepare`、`input/read`、草稿和现有 `run/start.references`，不新增第二条输入接纳链。

AgentSummary 保留现有 `role` 字段，并将其解释为实际类型名称。摘要补充 `baseRole`、`modelId` 和 `definitionVersion`，便于界面展示。旧历史记录没有自定义快照时，Runtime 按原内置类型解释，不推测不存在的自定义指令。摘要还包含 `providerId`，用于显示实际提供商。

Python DTO 继续作为 Schema 来源。实现需要更新输入引用与协作契约生成器、生成的 TypeScript DTO、边界校验和双端 Fixture。

### 11.2 持久化

父 Run 保存 Agent 目录快照引用。子任务保存实际类型、定义版本、完整指令快照及已解析的无凭证模型参数。大正文使用现有内容寻址 Blob 存储，SQLite 保存关联。Run 继续使用现有模型快照字段。

`agent_delegations.role` 需要迁移固定名称 CHECK。首版保留该字段与 `spawn_item_id`，所有创建仍来自真实 `spawn_agent` ToolCall。方案不增加来自输入引用的第二种自动派发来源。

子任务创建、定义快照关联、Run 入队和 Event/Outbox 应在同一事务提交。重复 ToolCall 需要复用原结果；幂等比较应包括实际定义版本与模型选择。

迁移需要覆盖新库、升级、备份恢复入口、外键检查和失败回滚。迁移不能改写历史类型名称。本次 Schema 从 v28 升级到 v29。迁移同时保留旧角色、索引和触发器，并执行外键检查。旧 Runtime 不能直接读取 v29 数据；回退时需要恢复升级前的备份。

Blob GC、Session 删除、备份和恢复需要保留或释放对应快照。用户配置备份需要包含 `agents/*.toml`。历史执行备份需要包含被引用的定义快照。恢复配置时不自动覆盖同名本地文件，恢复流程应展示冲突。

### 11.3 Desktop

Renderer 继续通过 preload typed IPC 访问 Main 和 Runtime。Renderer 不读取 TOML 文件。

输入候选列表、引用卡片和引用预览增加 Agent 类型。AgentPanel 使用实际类型名称，并展示基础职责和模型。界面需要去除对三种标签的固定映射假设。

首版提供文件驱动的配置方式、目录读取、引用预览和逐项错误提示。首版不增加独立的完整 Agent 编辑器、安装市场或配置生成 Agent。

错误提示应给出具体原因，例如“模型尚未配置”“思考档位不受支持”“定义名称重复”或“TOML 第 N 行格式错误”。Runtime 日志与界面不能回显凭证。

## 12. 重发、Fork 与相关补充项

| 场景 | 建议行为 |
| --- | --- |
| 新一轮选择同一类型 | 新 Run 使用当前定义 |
| 重新生成 | 新 Run 沿用源 Run 已固定的 Agent 选择与定义正文；当前权限继续按既有重发规则处理 |
| 编辑重发且保留原 Agent 引用 | 新 Run 复用原冻结定义；用户重新选择标签时才采用当前版本 |
| Fork | Fork 保留历史引用和所需快照，但不复制活跃子任务、Grant 或权限；新输入独立接纳 |
| 选择后、首次提交前定义已变化 | Runtime 提示用户重新选择 |
| 旧 Run 恢复时文件已删除 | Runtime 使用原快照；模型配置缺失时明确失败 |

重发对历史快照的复用应由 Runtime 根据已验证的源 Run 解析。Renderer 不能提交任意快照来绕过定义校验。这条路径需要补齐现有重发应用的引用校验逻辑。

以下问题来自既有子 Agent 能力。它们值得补充，但不作为自定义类型首版的全部前置条件：

| 问题 | 当前情况 | 后续统一补充方向 |
| --- | --- | --- |
| 引用传递 | 子 Run 创建只传任务文字，不传父输入引用 | `spawn_agent` 增加可选引用 ID，供所有类型按需传递文件、图片、Skill 等；旧默认行为保持不变 |
| 图片与模型能力 | 不同子模型可能具有不同能力 | 传递图片时按实际子模型校验，不默认依赖父模型能力 |
| 完整结果读取 | 父模型当前收到的结果摘要最多 2,000 字符 | 父模型获得统一的分页结果读取工具，继续先读有界摘要 |
| 长任务背景 | 当前委派文字最多 8,000 字符 | 后续用有界引用提供更多证据，不直接复制完整 Provider 对话 |
| Agent 常用 Skill | 本方案只通过正文描述职责 | 后续如有明确需求，再增加基于已有 Catalog 和授权的 Skill 预选字段 |

首版文档与界面不能暗示父输入中的全部资料已经自动传入子任务。父 Agent 仍需提供完整的任务说明。子任务使用 Skill、MCP 等能力时继续遵守现有目录、发现和激活机制。

## 13. 实施边界与验收

本次核心能力可以使用同一分支和一个 PR。相关修改可以拆成独立 Commit：

1. 定义解析、目录诊断、严格类型和快照存储。
2. 现有委派链中的自定义名称、模型解析、指令注入、续派和恢复。
3. typed IPC、`+` 引用、AgentPanel 展示和当前文档同步。

实现不需要目录重组，也不需要新增 Agent Loop、通用调度框架或 Provider 协议。维护范围主要是声明文件解析、现有协议扩展和快照关联。

定向验收应覆盖：

- TOML 示例可以解析，必填字段、未知字段、版本、重复名称和大小限制按规范处理。
- 三个内置类型、默认角色、原调用参数和历史数据保持兼容。
- 父 Agent 可以按目录选择类型；用户也可以通过 `+` 选择同一类型。
- Runtime 在实际子 Run 中使用正确模型、思考档位和完整 `instructions`。
- 缺失模型、无效档位和模型能力不足返回明确错误，不产生静默替代。
- 定义修改、停用、删除以及目录刷新不会改变已有 Run 的冻结内容。
- 重复 ToolCall、忙碌续派、空闲续派和重启恢复不会创建重复任务或更换定义。
- 自定义类型遵守父权限、Plan 阶段、未知副作用屏障和现有并发计数。
- 父任务取消、迟到结果、等待恢复和 Shutdown 继续满足原有资源清理要求。
- 升级、失败回滚、备份恢复和 Blob 保留覆盖新增快照关联。
- Desktop 正确显示名称、模型、不可用原因、历史引用和完整配置预览。

实现阶段先执行相关 Runtime、协议和 Renderer/Main 定向测试。开发者不因跨层修改自动执行 Full Suite。具体命令遵循 [DEVELOPMENT.md](../DEVELOPMENT.md) 和相应目录的 `AGENTS.md`。

本次设计文档不宣称上述测试已经通过。生产实现完成后，开发者需要同步 `docs/current-*`，再依据实际验证报告能力与限制。

## 14. Codex 参考与 Eidos 选择

当前官方资料支持独立 TOML 自定义 Agent、模型配置和自定义指令。Codex 子 Agent 继续继承父任务权限。这些做法可以作为声明方式的参考，见 [Codex 官方子 Agent 文档](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents)。

Eidos 的字段采用本地规范：`instructions` 保存完整正文，`model.provider_id` 与 `model.config_id` 共同指定已有模型配置，`role` 沿用现有工具参数。Eidos 保留三个内置名称，并继续使用自己的持久化、权限、取消、恢复和协作语义。


## 15. 本次实现与待补充内容

### 15.1 本次已经实现的内容

- Runtime 从数据目录的 `agents/*.toml` 读取定义，并校验字段、文件身份、文件大小和数量。
- `instructions` 是定义正文的唯一入口。父任务只看到类型摘要和本轮选择，子任务获得完整指令。
- 模型配置增加 `provider_id`。Runtime 校验它与 `config_id` 对应配置的实际提供商一致，不按名称猜测或静默换模型。
- 父 Run 在接纳时固定目录。`run_agent_catalogs` 保存目录 Blob 关联，子任务保存实际定义和原始创建请求。
- Runtime 继续使用 `spawn_agent.role`。消息、续派、等待、取消和子任务工具范围沿用原执行链。
- 续派保留定义和已解析的模型快照。重放创建请求使用单独保存的原请求校验，不再依赖续派更新后的任务文字。
- 输入框增加 Agent 分类、引用标签、错误原因和冻结定义预览。子任务面板展示类型和实际提供商、模型。
- Python DTO 生成 Desktop 契约。`custom-agents.json` 同时供两端验证。
- Schema v29 覆盖新库与 v28 升级；现有升级链和离线恢复入口继续接通该迁移。
- Blob GC 保留目录快照，现有备份保存 `agents/*.toml`。恢复保持新目录策略，模型配置与凭证仍不包含在备份内。
- 重发通过已验证的源 Run 复用保留引用的冻结定义。重新选择标签时，新引用采用当前定义。

### 15.2 实施中明确的边界

1. 现有 ModelConfigStore 仍以配置 ID 为唯一索引。本次增加提供商校验，但没有把整个模型存储改成复合主键。同一个原始模型 ID 在多个提供商下同时保存，仍受现有模型配置系统约束。
2. 目录快照冻结定义中的提供商、配置 ID 和档位选择。Runtime 在创建子任务时解析当前模型配置，并把无凭证模型快照写入子 Run。续派使用该已解析快照。凭证与连接配置仍由现有 Model Gateway 获取，本次不保证编辑连接配置后精确复现旧请求。
3. 一个不可用文件不会阻止其他定义。目录本身不可读或文件数量超限时，目录读取返回具体错误；普通新 Run 仍可使用内置类型。显式选择不可用定义时，引用准备或 Run 接纳会失败。
4. 配置预览显示名称、描述、基础职责、模型选择和完整指令。预览不会重新读取当前文件，也不把指令应用到父任务。
5. 配置错误目前给出固定中文原因，不回显任意异常和正文。TOML 语法错误目前不提供行列定位。
6. `enabled=false` 对后续新 Run 生效。已有 Run 的固定目录和续派保持原定义。
7. 首版保持显式任务上下文。父输入的图片、文件和 Skill 引用不会自动转交子任务。

### 15.3 仍待补充的能力

| 内容 | 当前状态与原因 |
| --- | --- |
| `spawn_agent` 可选引用传递 | 后续统一扩展全部类型，并按实际子模型校验图片能力 |
| 父模型分页读取完整子任务结果 | 现有 2,000 字符结果摘要保留；Desktop 仍可查看完整子会话 |
| 项目级定义与 Plugin 分发 | 首版只使用用户数据目录，尚未定义跨来源覆盖规则 |
| Agent 配置编辑器与自动生成 | 首版由用户直接维护 TOML，界面提供选择和预览 |
| TOML 行列错误定位 | 当前只提供固定错误原因，后续可补充无正文的行列信息 |
| 独立 Worktree、多层委派、整组预算 | 本次保持原有执行和并发语义 |
| 真实多 Provider 验证 | 本地配置与脚本模型验证不代表真实 Provider 请求已经通过 |
| 真实 Electron 中的人工交互验收 | 自动化组件验证与 typed IPC 验证不能替代真实窗口操作 |

### 15.4 验证记录

以下结果来自本分支的本地定向验证。测试使用隔离数据目录和脚本模型，没有调用真实 Provider。

| 验证 | 结果 |
| --- | --- |
| Runtime：自定义类型、既有协作、输入引用、Run 接纳、重发、方法注册、Schema、旧角色升级及三项备份恢复 | 129 项通过；9 项原生平台用例按筛选未执行 |
| 历史升级补充检查：侧边栏、Run 结束信息和 Session Fork 的升级相关用例 | 8 项通过；25 项无关用例未选择 |
| Renderer：InputPicker、InputContext、AgentPanel 行为 | 34 项通过 |
| Main：共享 Fixture、真实 stdio 目录/引用/重启 | 2 项通过 |
| Renderer/共享状态：协作、输入与 Run 结束信息 | 5 项通过 |
| Desktop 结构与 IPC 契约检查 | 4 项通过，静态契约检查通过 |
| 应用构建 | `pnpm build` 通过，包含 preload、Main 和 Renderer |
| Python 静态与依赖检查 | Ruff、Deptry 通过 |
| Python 依赖审计 | 两组锁定依赖的 pip-audit 均未发现已知漏洞 |
| Electron smoke | 原生环境通过，Runtime 正常初始化并退出 |
| 文档示例 | 完整 TOML 通过生产 AgentDefinition 校验，本地链接存在 |
| Diff 空白检查 | `git diff --check` 通过 |

受限环境中的依赖审计曾因 DNS 失败，联网重试后通过。受限环境中的 Electron 曾以 SIGABRT 退出，原生重跑后通过。上述首次失败没有被写成通过结果。

本次没有执行 Full Suite、完整 Runtime/Desktop 层、完整 Seatbelt 套件、DMG 打包、真实 Provider 请求或真实窗口中的人工交互验收。

实现过程中，开发者同步修正了历史测试夹具的降级步骤，使其在构造旧版本时移除 v29 对象。生产迁移仍严格检查预期旧结构，并在失败时回滚。

本次没有新增生产依赖，`pyproject.toml` 和 `uv.lock` 保持不变。现有循环、协作工具、权限、审批、取消、恢复和并发计数继续由 Eidos 管理。
