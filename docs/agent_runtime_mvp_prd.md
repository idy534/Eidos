# Eidos Agent Runtime MVP 需求文档 PRD

版本：v0.3
产品形态：桌面端 Agent Workbench
运行方式：本地前台执行型 Agent
数据空间：用户级 `~/.eidos`
范围：独立个人 Agent Runtime，不接入现有平台资源

---

## 1. 产品名称与 Slogan

项目名称：Eidos

Slogan：

> 让想法拥有可执行的形态。

Eidos 是一个面向未来工作的 Agent Runtime，帮助智能体理解任务、调用工具、操作工作区，并将每一步执行过程清晰记录下来。

---

## 2. 背景

当前目标是先打造一个个人本地 Agent。第一阶段只做 MVP，不接入知识库、术语库、数据库问数、本体等平台资源。

Eidos 的核心不是“聊天机器人”，而是一个可以围绕任务持续执行的本地 Agent Runtime：

```text
用户选择项目工作空间，或使用 Eidos 公共空间
  ↓
用户输入任务
  ↓
Eidos Runtime 创建 Run
  ↓
模型判断下一步动作
  ↓
调用工具 / 请求审批 / 生成回复
  ↓
记录完整执行轨迹
  ↓
完成任务，或暂停等待用户决策
```

参考设计理念：

- Codex CLI：强调本地工作区、工具执行、终端集成和任务运行体验。
- Codex Agent Loop：强调 sandbox、approval mode、cwd、工具列表、上下文压缩和 prompt caching。
- LangGraph / LangChain Human-in-the-Loop：强调工具调用前中断、人工 approve / edit / reject / respond，并通过持久化状态实现恢复。

---

## 3. 产品定位

### 3.1 一句话定位

Eidos 是一个本地桌面端、可执行、可审批、可追踪、可恢复的个人 Agent Runtime。

### 3.2 产品原则

- Eidos 是前台执行型 Agent，不是后台任务守护器。
- Eidos 只内置一个默认 Agent：Eidos；MVP 不暴露多 Agent 管理。
- system prompt 是内部运行协议，不对用户暴露，不可查看、不可编辑。
- 用户可以按 Session 选择模型；Run 创建后固化模型配置快照。
- 所有 Eidos 自身数据统一保存在用户级 `~/.eidos`，不默认污染用户项目目录。
- Public Mode 的底层 files 不作为 UI 暴露对象，用户只看到 Artifacts。
- 右栏 Terminal 是用户个人操作区，每个 Workspace 只维护一个实例，不绑定 Session、Run、Step 或 ToolCall。
- Terminal 不进入 Execution Feed，不生成 ToolCall，不触发审批，不自动进入 Agent 上下文。
- Agent 自动执行的敏感权限都必须经过审批；用户在 Terminal 中自行执行的命令由用户负责。

### 3.3 MVP 不解决的问题

MVP 不做：

- 平台知识库接入
- 术语库改写
- Text2SQL / 数据库分析
- 本体检索
- 多 Agent 协作或多 Agent UI
- system prompt 编辑器
- 长期记忆
- Skill 自动生成
- MCP 插件市场
- 浏览器自动化
- 企业级权限体系
- 分布式任务调度
- 后台 daemon / tray 常驻执行
- 文件写入前快照、一键回滚和完整 diff 编辑器

### 3.4 MVP 要解决的问题

MVP 只解决：

- 用户可以启动 Eidos 桌面端。
- 首次启动自动创建默认 Agent：Eidos。
- 用户可以创建 Workspace Mode Session 或 Public Mode Session。
- 用户可以为 Session 选择一个 model profile。
- 用户可以提交一次任务 Run。
- Agent 可以循环思考和执行工具。
- Agent 可以读取、写入当前 active root 内的文件。
- Agent 可以使用 `search_text` 搜索 Workspace 文本。
- Agent 可以使用 `read_file_range` 按行读取局部文件。
- Agent 修改已有文件默认使用 `apply_patch`。
- Agent 可以请求执行受控 Shell 命令。
- 所有写操作和 shell 执行都需要人工审批。
- 审批通过后可以继续执行原工具调用。
- 审批拒绝后可以把拒绝原因返回给模型重新规划一次。
- 所有执行过程可持久化追踪。
- Public Mode 的所有产物默认长期保存。
- 用户可以取消 running 状态任务。
- waiting_approval Run 可以在下次启动后恢复审批。
- waiting_user_input Run 可以在用户补充指令后继续同一个 Run。

---

## 4. 运行模式

### 4.1 Workspace Mode

Workspace Mode 面向用户选择的真实项目文件夹。

```text
用户选择本地项目文件夹
  ↓
Eidos 创建 workspace 记录
  ↓
Session 绑定该 workspace
  ↓
Agent 在该文件夹内读写文件和执行命令
```

约束：

- 用户项目目录不默认写入 `.eidos/`。
- Eidos 运行记录、events、artifacts 索引统一保存在 `~/.eidos/workspaces/{workspace_id}/`。
- UI 显示项目文件树和只读预览。
- UI 提供基于当前工作目录打开的 Workspace 级交互式 Terminal，类似 IDE 内置终端。
- 每个 Workspace 只维护一个 Terminal 实例；切换 Session 不重启 Terminal。
- Workspace Terminal 是用户个人操作区，不属于 Agent Run。
- `write_file` 和 `apply_patch` 必须审批。
- `run_shell` 必须审批。

### 4.2 Public Mode

Public Mode 面向不指定项目文件夹的通用任务、想法整理、脚本草稿、报告生成等场景。

```text
用户选择使用 Eidos 公共空间
  ↓
Eidos 在 ~/.eidos/public/sessions/{session_id}/files 下创建内部执行空间
  ↓
Agent 在该空间内读写文件
  ↓
用户只通过 Artifacts 看到产物
```

约束：

- Public Mode 不显示底层文件树。
- Public Mode 的 `files/` 是内部执行空间，不作为 UI 暴露对象。
- Public Mode 只展示 Artifacts 列表、预览、日志和执行流。
- Public Mode 产物默认长期保存，不做自动清理。
- Public Mode 下 `write_file` 和 `apply_patch` 也必须审批。
- Public Mode 下 `run_shell` 仍必须审批。

---

## 5. 用户角色

| 角色 | 说明 |
|---|---|
| 开发者用户 | 使用 Eidos 完成代码生成、脚本生成、文件整理、项目初始化等任务 |
| 个人工作用户 | 使用 Eidos 在公共空间中生成报告、计划、脚本草稿和可执行产物 |
| Eidos Runtime | 负责模型调用、工具调用、状态管理、审批控制和事件输出 |

MVP 阶段不做多用户体系，默认单用户本地使用。

---

## 6. 产品界面

### 6.1 三栏 Agent Workbench

Eidos MVP 采用三栏 Agent Workbench 布局。

```text
┌──────────────┬──────────────────────────────┬──────────────────────┐
│ 导航区        │ 核心交互区                    │ 上下文与产物区          │
├──────────────┼──────────────────────────────┼──────────────────────┤
│ 会话          │ 对话                          │ 文件树 / Artifacts      │
│ 工作区        │ 执行流                        │ Terminal                │
│ 模型          │ 时间线事件摘要                  │ Diff / 预览              │
│ 设置          │ 审批卡片                       │ 日志                    │
└──────────────┴──────────────────────────────┴──────────────────────┘
```

左栏是导航区，统一承载会话、工作区、模型和设置。

中栏是核心交互区，承载对话、执行流、时间线和审批。MVP 中“执行流”和“时间线”合并为一个 Execution Feed，不单独做复杂 Timeline 页面。

右栏是上下文与产物区，承载文件树、产物、Workspace Terminal、预览和日志。写操作审批必须展示 diff。

### 6.2 模式差异

| 区域 | Workspace Mode | Public Mode |
|---|---|---|
| 左栏 | 会话、工作区、模型、设置 | 会话、公共空间、模型、设置 |
| 中栏 | 对话、Execution Feed、审批 | 对话、Execution Feed、审批 |
| 右栏 | 文件树、预览、Workspace Terminal、Artifacts、日志 | Artifacts、预览、日志 |

Public Mode 不展示文件树，也不展示 `~/.eidos/public/.../files` 底层目录。

### 6.3 Workspace Terminal

Workspace Terminal 是右栏的 Workspace 级交互式终端，也是用户个人操作区。

规则：

- Terminal 基于当前 Workspace 工作目录打开，体验类似 IDE 内置终端。
- 每个 Workspace 只维护一个 Terminal 实例。
- 切换 Session 不重启 Terminal；切换 Workspace 时切换到对应 Workspace Terminal。
- Terminal 不绑定 Session、Run、Step 或 ToolCall。
- Terminal 中用户手动输入的命令不进入 Agent Loop，也不作为 ToolCall。
- Terminal 不进入 Execution Feed，不触发审批，不自动进入 Agent 上下文。
- MVP 不围绕 Terminal 设计复杂交互。
- Agent 通过 `run_shell` 执行的命令仍然作为 ToolCall 展示在中栏 Execution Feed 中。
- Public Mode 不展示 Workspace Terminal。

### 6.4 Execution Feed

Execution Feed 中穿插展示：

- 用户输入
- 模型输出
- 模型计划或思考摘要
- 工具调用卡片
- 审批卡片
- 工具结果摘要
- 错误
- 最终回答

完整 raw events 仍然持久化，但 MVP 先通过 Execution Feed 产品化呈现。

`run_shell` 输出由 Runtime 实时采集，并通过事件系统流式传递。Execution Feed 不直接实时刷完整 stdout/stderr，而是展示一个可更新的 `run_shell` ToolCall 卡片，包括命令、状态、运行时长、最近输出摘要、stdout/stderr 大小、终止原因和 exit code。完整 stdout/stderr 默认折叠，用户可在 ToolCall Detail / Logs 中查看。

---

## 7. 使用场景

### 7.1 Workspace Mode：生成代码项目

用户选择项目文件夹后输入：

```text
帮我创建一个 FastAPI demo，包含 /health 接口和 README。
```

Eidos 应该：

1. 创建 Session 和 Run。
2. 规划需要生成的文件。
3. 请求写入 `main.py` 和 `README.md` 的审批。
4. 审批通过后写入文件。
5. 请求执行 `python -m py_compile main.py` 或类似命令的审批。
6. 审批通过后执行命令。
7. 保存命令、输出、退出码、耗时。
8. 根据结果修正文件或输出完成说明。

### 7.2 Workspace Mode：修改已有文件

用户输入：

```text
帮我把这个项目里的日志改成标准 logging，并补充注释。
```

Eidos 应该：

1. 读取工作区文件列表。
2. 使用 `search_text` 定位相关代码。
3. 使用 `read_file_range` 或 `read_file` 读取目标文件。
4. 生成修改方案。
5. 通过 `apply_patch` 生成局部修改。
6. 展示待修改路径、修改理由和 diff。
7. 请求写入审批。
8. 审批通过后应用 patch。
9. 审批拒绝后把拒绝原因返回给 Agent 重新规划。
10. 必要时请求执行测试命令的审批。

### 7.3 Public Mode：生成通用产物

用户不选择项目文件夹，直接输入：

```text
帮我整理一个本周个人工作复盘模板，并生成 Markdown 文件。
```

Eidos 应该：

1. 创建 Public Mode Session。
2. 准备在 `~/.eidos/public/sessions/{session_id}/files` 中生成文件。
3. 展示新文件内容 diff 并请求写入审批。
4. 审批通过后写入文件。
5. 将最终 Markdown 登记为 Artifact。
6. 在右栏 Artifacts 列表展示该产物。

### 7.4 审批拒绝后继续

用户拒绝 shell 命令后，Eidos 应该：

1. 记录审批拒绝。
2. 把拒绝原因作为工具结果返回给模型。
3. 让模型重新选择方案。
4. 可以直接输出手动执行建议，或改为只生成文件。

---

## 8. 功能范围

### 8.1 P0 功能

| 编号 | 功能 | 说明 | 优先级 |
|---|---|---|---|
| F001 | 桌面端启动 | 用户可以启动 Eidos 桌面端应用 | P0 |
| F002 | 默认 Agent | 首次启动自动创建默认 Agent：Eidos；UI 不暴露多 Agent 管理 | P0 |
| F003 | Eidos Home | 初始化并使用用户级 `~/.eidos` 数据空间 | P0 |
| F004 | Workspace Mode | 用户可以选择本地文件夹作为项目工作空间 | P0 |
| F005 | Public Mode | 用户可以不指定项目文件夹，使用公共空间创建任务 | P0 |
| F006 | Session 管理 | Session 支持 `workspace` / `public` 两种 mode | P0 |
| F007 | Model Profiles | 用户可以配置多个 OpenAI-compatible model profile | P0 |
| F008 | Session 模型选择 | 每个 Session 可以选择 model profile | P0 |
| F009 | Run 模型快照 | Run 创建时固化 model_config_snapshot | P0 |
| F010 | Agent Loop | 模型输出文本或工具调用，Runtime 循环执行 | P0 |
| F011 | Tool Registry | 注册内置工具，暴露工具 schema 给模型 | P0 |
| F012 | list_files | 查看 active root 目录结构 | P0 |
| F013 | read_file | 读取 active root 内文件 | P0 |
| F014 | read_file_range | 按行号范围读取 active root 内文件 | P0 |
| F015 | search_text | Workspace 内受控 literal text search | P0 |
| F016 | write_file | 创建新文件或生成完整文件，必须审批并展示 diff | P0 |
| F017 | apply_patch | 修改已有文件的默认方式，必须审批并展示 diff | P0 |
| F018 | run_shell | 在 active root 内执行受控命令，必须审批 | P0 |
| F019 | Approval | 写操作和 shell 执行前暂停等待 Approve / Reject | P0 |
| F020 | Resume | 审批通过或拒绝后继续 Run，且不能重复创建已存在 Step | P0 |
| F021 | waiting_user_input | 连续拒绝、Step 上限或运行超时后等待用户补充指令 | P0 |
| F022 | Run Limits | 固定 `max_steps=20`，默认 `run_timeout=30min`，硬上限 120min | P0 |
| F023 | Tool Timeout | 所有工具调用都有默认 timeout | P0 |
| F024 | Execution Feed | 中栏展示对话、工具调用、审批、结果和错误 | P0 |
| F025 | Event 持久化 | 所有关键 runtime event 必须写入数据库 | P0 |
| F026 | Artifacts | Public Mode 只展示 Artifacts；Workspace Mode 也可展示 Artifacts | P0 |
| F027 | Workspace 文件树 | 仅 Workspace Mode 显示文件树和只读预览 | P0 |
| F028 | Workspace Terminal | 每个 Workspace 一个用户交互式终端，不绑定 Agent Run | P0 |
| F029 | Cancel Run | 用户可以取消 running 状态任务 | P0 |
| F030 | 前台生命周期 | 关闭窗口时 running Run 必须取消或等待完成 | P0 |
| F031 | 启动恢复 | 启动时恢复最近 active session，并显示 waiting_approval / waiting_user_input Run | P0 |

### 8.2 P1/P2 功能

| 编号 | 功能 | 说明 | 优先级 |
|---|---|---|---|
| F101 | Edit then Approve | 用户在审批界面编辑 patch / 命令后再批准 | P1 |
| F102 | 写入快照 | write_file 前保存 before / after 快照 | P1 |
| F103 | 文件级恢复 | 支持从指定 tool_call 恢复文件 | P1 |
| F104 | 上下文压缩 | 历史过长时压缩为摘要 | P1 |
| F105 | Timeout 配置 UI | 在 Settings / Session 中配置 Run 和 Tool timeout | P1 |
| F106 | Regex Search | 开启 `search_text.use_regex` 或新增 `search_regex` | P1 |
| F107 | 重试策略 | 模型调用和工具调用支持有限重试 | P1 |
| F109 | 多 Agent | 多 Agent 配置、模板、权限策略和专属工具集 | P2 |
| F110 | 后台执行 | tray、通知、任务守护和后台继续执行 | P2 |
| F111 | PostgreSQL | 服务化部署时支持 PostgreSQL | P2 |

---

## 9. 审批策略

| 工具 | Public Mode | Workspace Mode |
|---|---|---|
| list_files | 自动 | 自动 |
| read_file | 自动，敏感文件拒绝 | 自动，敏感文件拒绝 |
| read_file_range | 自动，敏感文件拒绝 | 自动，敏感文件拒绝 |
| search_text | 自动，敏感文件不参与搜索 | 自动，敏感文件不参与搜索 |
| write_file | 审批 | 审批 |
| apply_patch | 审批 | 审批 |
| run_shell | 审批 | 审批 |

审批规则：

- MVP 审批只支持 Approve / Reject，不支持 Edit then Approve。
- 所有写操作审批必须展示 diff。
- `write_file` 用于创建新文件或生成完整文件。
- `apply_patch` 用于修改已有文件，并作为默认修改方式。
- Agent 不允许在未读取原文件的情况下覆盖已有文件。
- Reject 后自动把拒绝原因作为工具结果返回给 Agent，让 Agent 继续规划一次。
- 同一个 Run 连续 Reject 2 次后，Run 进入 `waiting_user_input`，等待用户补充下一步指令。
- `waiting_user_input` 后用户补充指令时继续同一个 Run。

MVP 不做文件写入前快照，因此“可恢复”只指审批恢复、用户输入恢复和 Run 状态恢复，不承诺文件内容一键恢复。

---

## 10. Run 和 Tool 限制

### 10.1 Run 限制

- MVP 固定 `max_steps = 20`，UI 暂不开放配置。
- 达到 `max_steps` 后，Run 进入 `waiting_user_input`。
- 默认 `run_timeout = 30 分钟`。
- 系统硬上限为 `120 分钟`。
- `waiting_approval` 和 `waiting_user_input` 的等待时间不计入 `run_timeout`。
- 达到 `run_timeout` 后，Run 进入 `waiting_user_input`，不是直接 `failed`。
- 用户可以选择继续、总结当前结果或取消任务。

### 10.2 Tool Timeout

MVP 中每个工具调用都必须设置 timeout。

默认值：

| 工具 | 默认 timeout | 最大 timeout |
|---|---:|---:|
| list_files | 10s | 10s |
| read_file | 10s | 10s |
| read_file_range | 10s | 10s |
| search_text | 10s | 10s |
| write_file | 10s | 10s |
| apply_patch | 15s | 15s |
| run_shell | 120s | 600s |

工具超时后：

- ToolCall 状态标记为 `timeout`。
- 保存已产生的输出和错误信息。
- Tool timeout 不直接导致 Run 失败，而是作为工具结果返回给 Agent，由 Agent 重新规划。
- `run_shell` 超时必须终止对应进程，避免遗留后台任务。

### 10.3 run_shell 自定义 timeout

`run_shell` 允许 Agent 请求自定义 `timeout_seconds`。

规则：

- 默认 120s。
- 请求不超过 300s 时正常处理。
- 请求 301~600s 时视为长时间命令，审批卡片高亮展示。
- 超过 600s 直接拒绝，要求 Agent 缩短 timeout 或拆分任务。
- 最终实际 timeout 还必须受 Run 剩余时间预算约束。

### 10.4 长驻服务类命令

MVP 允许 Agent 执行长驻服务类命令，但不做后台服务管理。

规则：

- Runtime 只负责短期观测命令输出。
- 如果命令连续 3 个 30 秒窗口没有 stdout/stderr 输出，Runtime 自动终止进程。
- 如果命令达到 timeout 或 Run 剩余时间预算，Runtime 自动终止进程。
- 终止后保存已捕获输出，并把结构化结果返回给 Agent。
- Eidos 不负责后台保活、端口管理、停止/重启面板。

### 10.5 run_shell 输出限制

`run_shell` stdout/stderr 必须有保存上限。

规则：

- stdout 最多保存 768KB。
- stderr 最多保存 512KB。
- 二者合计最多 1MB。
- 输出超限不终止命令。
- 超限后采用 head + tail 裁剪。
- 标记 `truncated=true`。
- Execution Feed 只展示最近输出摘要和执行状态。
- 完整输出在 ToolCall Detail / Logs 中查看。
- 返回给 Agent 的 observation 最多 32KB，只包含结构化状态、关键 stdout/stderr head/tail 和截断信息。

---

## 11. 模型配置

MVP 支持多个 OpenAI-compatible model profile。DeepSeek 等兼容 OpenAI 协议的模型通过用户配置 `base_url / api_key / model` 接入，不做特殊 provider 分支。

Model Profile 至少包含：

- name
- base_url
- api_key_ref
- model
- parameters

规则：

- 每个 Session 可以选择一个 model profile。
- 切换 Session 模型时提示：切换模型只影响后续 Run，不影响历史 Run 和正在运行的 Run。
- Run 创建时固化模型配置快照。
- running / waiting_approval / canceling 状态的 Run 不允许切换模型。
- approval resume 必须继续使用原 Run 的模型快照。
- model_config_snapshot 不保存明文 API key。

---

## 12. 本地数据空间

Eidos 使用用户级 `~/.eidos` 作为应用数据根目录。

```text
~/.eidos/
  eidos.db
  config.toml
  agents/
  model_profiles/
  public/
    sessions/
      {session_id}/
        files/
        artifacts/
        runs/
        events/
  workspaces/
    {workspace_id}/
      workspace.toml
      sessions/
        {session_id}/
          artifacts/
          runs/
          events/
  logs/
  cache/
```

说明：

- Public Mode 的 `files/` 是内部执行空间，用户不通过文件树浏览。
- Public Mode 产物通过 `artifacts/` 展示。
- Workspace Mode 的真实业务文件仍在用户选择的项目目录中。
- Workspace Mode 的运行记录、events、artifacts 索引保存在 `~/.eidos/workspaces/{workspace_id}/`。
- MVP 不主动清理 Public Mode 产物、Artifacts、Events 或 Logs。

---

## 13. 前台执行生命周期

Eidos 是前台执行型 Agent，不是后台任务守护器。窗口关闭即准备退出 Runtime。

规则：

- 没有 running Run 时，关闭窗口会正常退出 sidecar。
- 有 running Run 时，用户必须选择等待完成或取消任务并退出。
- waiting_approval Run 可以持久化；下次启动后仍显示 waiting_approval，用户可继续 approve / reject。
- waiting_user_input Run 可以持久化；下次启动后仍显示等待用户补充指令。
- succeeded / failed / canceled Run 正常保留 timeline 和 artifacts。
- sidecar 不作为后台 daemon 存活。

---

## 14. 非功能需求

### 14.1 本地部署

MVP 必须支持桌面端本地运行：

- Electron + React 桌面应用
- Python FastAPI sidecar
- SQLite 数据库
- 本地文件系统 workspace
- 单用户、单机、单进程 Runtime

### 14.2 安全性

| 项目 | 要求 |
|---|---|
| 文件隔离 | 工具不得访问 active root 之外的文件 |
| 路径防逃逸 | 禁止 `../`、绝对路径、符号链接等方式逃逸到 active root 外 |
| 敏感文件保护 | 敏感文件默认直接拒绝读取，不提供审批后读取能力 |
| 普通读取 | 普通项目文件读取不需要审批，但受 active root、大小和敏感内容扫描限制 |
| 写入审批 | `write_file` / `apply_patch` 必须审批并展示 diff |
| Shell 审批 | shell 命令默认必须审批 |
| Shell cwd 限制 | 命令只能在 active root 下执行 |
| Shell 超时 | 默认 120 秒，最大 600 秒，受 Run 剩余时间预算约束 |
| Shell 输出限制 | stdout 最多 768KB，stderr 最多 512KB，合计最多 1MB |
| 子进程控制 | Shell 超时或取消时必须终止进程组 |
| Renderer 隔离 | Renderer 不持有 sidecar token，不直接访问 sidecar |

敏感文件包括：

- 环境变量文件，如 `.env`、`.env.local`。
- 私钥、证书和密钥文件，如 `id_rsa`、`*.pem`、`*.key`、`*.p12`。
- 云厂商和平台凭证，如 `~/.aws/credentials`、kubeconfig、docker config。
- 包管理器 token 文件，如 `.npmrc`、`.pypirc`、`.netrc`。
- 文件名包含 `secret`、`token`、`credentials` 的文件。
- 内容中疑似包含 private key、api key、access token、password 的文件。

被拒绝时，Runtime 返回 `sensitive_file` 错误，并提示用户手动提供脱敏后的必要片段。内容扫描命中疑似敏感信息时，不保存命中内容，只记录拒绝原因、路径、规则 ID、时间等审计元数据。

### 14.3 文件读取策略

`read_file` 采用分级读取策略：

- `<= 512KB`：完整读取。
- `512KB ~ 2MB`：返回 head + tail 裁剪内容，标记 `truncated=true`。
- `> 2MB`：拒绝整文件读取，提示使用 `read_file_range`。
- 单次返回内容最多 256KB。
- 二进制、密钥、敏感文件默认拒绝普通读取。

`read_file_range` 按行号范围读取，参数为 `path + start_line + end_line`。

### 14.4 搜索策略

`search_text` 是 Workspace 内受控文本搜索工具：

- 使用 literal text search。
- 默认大小写不敏感。
- 工具参数可预留 `use_regex`，但 MVP 固定关闭或拒绝 `use_regex=true`。
- 默认限制在 active root / workspace 内。
- 默认排除依赖目录、构建产物、lock 文件和二进制文件。
- 返回路径、行号、列号、命中行预览和少量上下文。
- 设置 `max_results`。
- 敏感文件不参与搜索。
- 疑似敏感命中内容必须脱敏。

Agent 需要进一步理解或修改时，再通过 `read_file_range` 读取局部上下文。

### 14.5 可观测性

必须记录：

- run_id
- session_id
- workspace_id
- mode
- model_config_snapshot
- step_id
- tool_call_id
- approval_id
- event_id
- model input 摘要
- model output
- tool name
- tool arguments
- tool result
- 状态变化
- 错误信息
- started_at / finished_at

### 14.6 可恢复性

MVP 至少支持审批恢复和用户输入恢复：

```text
Run running
  ↓
ToolCall pending_approval
  ↓
Run waiting_approval
  ↓
用户 approve / reject
  ↓
Runtime 锁定 Run / Approval / ToolCall
  ↓
执行原 ToolCall 或写入 reject result
  ↓
完成原 Step
  ↓
从下一 Step 继续执行
```

连续 Reject、达到 `max_steps` 或达到 `run_timeout` 时：

```text
Run running / waiting_approval
  ↓
触发暂停条件
  ↓
Run waiting_user_input
  ↓
用户补充下一步指令
  ↓
追加到同一个 Run
  ↓
从下一 Step 继续执行
```

恢复过程必须满足：

- 不重复创建已有 `step_index`。
- 不重复执行已经 succeeded / failed / rejected 的 tool call。
- approve / reject 接口幂等。
- cancel 与 approve / reject 并发时只允许一个状态转换成功。
- resume 使用原 Run 的 model_config_snapshot。
- waiting_user_input 后用户补充指令时继续同一个 Run，不创建新 Run。

---

## 15. 数据库选型

MVP 数据库选择 SQLite。

原因：

- Eidos 是个人桌面端应用，用户不应该为了启动应用先安装 Docker。
- MVP 是单用户、单机、单进程 Runtime，SQLite 足够承载状态持久化。
- `~/.eidos/eidos.db` 符合桌面应用的数据管理习惯。
- Run / Step / ToolCall / Approval / Event / Artifact 都可以落 SQLite。

PostgreSQL 作为后续服务化、多用户、多 worker 部署选项，不进入 MVP。

---

## 16. 验收标准

| 编号 | 验收项 | 标准 |
|---|---|---|
| A001 | 桌面端启动 | 可以启动 Eidos 桌面应用，并自动拉起 sidecar |
| A002 | 默认 Agent | 首次启动自动创建默认 Eidos Agent，UI 不暴露 system prompt |
| A003 | Public Mode | 不选择项目文件夹也可以创建 Session 和 Run |
| A004 | Workspace Mode | 可以选择本地文件夹作为 workspace |
| A005 | 模型配置 | 可以创建多个 OpenAI-compatible model profile |
| A006 | Session 模型选择 | 每个 Session 可以选择模型，切换时提示只影响后续 Run |
| A007 | Run 模型快照 | Run 创建时固化模型配置快照 |
| A008 | 模型回复 | 不调用工具时可以正常流式回复 |
| A009 | 读取文件 | Agent 可以读取 active root 内文件 |
| A010 | 范围读取 | Agent 可以通过 `read_file_range` 按行号读取局部文件 |
| A011 | 文本搜索 | Agent 可以通过 `search_text` 做 literal text search |
| A012 | 写入审批 | Public Mode 和 Workspace Mode 写入都必须审批 |
| A013 | Diff 审批 | `write_file` / `apply_patch` 审批界面必须展示 diff |
| A014 | Patch 修改 | 修改已有文件默认使用 `apply_patch` |
| A015 | Shell 审批 | 执行 shell 前必须进入 waiting_approval |
| A016 | Shell 输出卡片 | Execution Feed 展示可更新 run_shell ToolCall 卡片 |
| A017 | 审批恢复 | approve 后执行原 tool call，并从下一 step 继续 |
| A018 | 拒绝恢复 | reject 后模型能基于拒绝原因重新规划一次 |
| A019 | 连续拒绝暂停 | 同一 Run 连续 Reject 2 次后进入 waiting_user_input |
| A020 | Run 限制 | `max_steps=20`，`run_timeout=30min`，到限进入 waiting_user_input |
| A021 | Tool Timeout | 工具 timeout 后保存输出并作为结果返回 Agent |
| A022 | 敏感文件 | 敏感文件直接拒绝读取，不保存命中内容 |
| A023 | 路径隔离 | `../`、绝对路径、符号链接和前缀路径逃逸会被拒绝 |
| A024 | 取消任务 | running 状态可以取消 |
| A025 | 启动恢复 | 下次打开恢复最近 active session 和 waiting_approval / waiting_user_input Run |
| A026 | Public Artifacts | Public Mode 只展示 Artifacts，不展示底层 files |
| A027 | Workspace 文件树 | Workspace Mode 显示文件树和只读预览 |
| A028 | Workspace Terminal | 每个 Workspace 一个 Terminal，不进入 Agent Run |
| A029 | ToolCall 命令展示 | Agent `run_shell` 命令仍作为 ToolCall 展示在 Execution Feed |
| A030 | 前台生命周期 | 关闭窗口时 running Run 必须取消或等待完成 |
| A031 | 错误可见 | 失败原因保存并可查询 |

---

## 17. MVP 交付物

| 交付物 | 说明 |
|---|---|
| 桌面应用 | Electron + React Agent Workbench |
| Python sidecar | FastAPI Runtime，由 Electron Main 启停 |
| SQLite schema | `~/.eidos/eidos.db` |
| Eidos Home | `~/.eidos` 目录结构初始化 |
| 内置工具 | list_files / read_file / read_file_range / search_text / write_file / apply_patch / run_shell |
| Runtime Engine | Agent Loop + 状态机 |
| Approval Resume | approve / reject / waiting_user_input 后可恢复执行 |
| Execution Feed | 对话、工具、审批、结果的统一执行流 |
| Workspace Terminal | Workspace 级交互式终端，不绑定 Agent Run |
| Artifacts | Public Mode 和 Workspace Mode 的产物展示 |
| API 文档 | 本地 sidecar OpenAPI，仅作为内部调试 |
| 示例任务 | 代码生成、文件修改、命令执行审批、公共空间产物生成 |

---

## 18. 后续演进方向

MVP 稳定后再补：

1. Edit then Approve。
2. 写入快照和文件级恢复。
3. Timeout 配置 UI。
4. Regex Search。
5. MCP Client。
6. Skill 系统。
7. 长期记忆。
8. 多 Agent 管理。
9. 多模型路由。
10. 浏览器工具。
11. 后台执行、tray 和通知。
12. PostgreSQL 服务化部署。
13. 分布式 Runtime Worker。
14. 评估与回放系统。
