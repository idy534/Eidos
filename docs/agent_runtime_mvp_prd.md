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
- Agent 可以请求执行受控 Shell 命令。
- 中高风险工具调用需要人工审批。
- 审批通过后可以继续执行原工具调用。
- 审批拒绝后可以把拒绝原因返回给模型重新规划。
- 所有执行过程可持久化追踪。
- Public Mode 的所有产物默认长期保存。
- 用户可以取消 running 状态任务。
- waiting_approval Run 可以在下次启动后恢复审批。

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
- `write_file` 默认需要审批。
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
- Public Mode 下 `write_file` 默认自动执行。
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
│ 工作区        │ 执行流                        │ 终端输出                │
│ 模型          │ 时间线事件摘要                  │ Diff 预留 / 预览         │
│ 设置          │ 审批卡片                       │ 日志                    │
└──────────────┴──────────────────────────────┴──────────────────────┘
```

左栏是导航区，统一承载会话、工作区、模型和设置。

中栏是核心交互区，承载对话、执行流、时间线和审批。MVP 中“执行流”和“时间线”合并为一个 Execution Feed，不单独做复杂 Timeline 页面。

右栏是上下文与产物区，承载文件树、产物、终端、预览和日志；Diff 在 MVP 中只预留入口，完整 Diff 展示放到 P1。

### 6.2 模式差异

| 区域 | Workspace Mode | Public Mode |
|---|---|---|
| 左栏 | 会话、工作区、模型、设置 | 会话、公共空间、模型、设置 |
| 中栏 | 对话、Execution Feed、审批 | 对话、Execution Feed、审批 |
| 右栏 | 文件树、预览、终端输出、Artifacts、日志 | Artifacts、预览、终端输出、日志 |

Public Mode 不展示文件树，也不展示 `~/.eidos/public/.../files` 底层目录。

### 6.3 Execution Feed

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
2. 读取目标文件。
3. 生成修改方案。
4. 展示待写入路径和写入理由。
5. 请求写入审批。
6. 写入或 patch 文件。
7. 必要时请求执行测试命令的审批。

### 7.3 Public Mode：生成通用产物

用户不选择项目文件夹，直接输入：

```text
帮我整理一个本周个人工作复盘模板，并生成 Markdown 文件。
```

Eidos 应该：

1. 创建 Public Mode Session。
2. 在 `~/.eidos/public/sessions/{session_id}/files` 中生成文件。
3. 将最终 Markdown 登记为 Artifact。
4. 在右栏 Artifacts 列表展示该产物。

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
| F014 | write_file | 写入 active root 内文件，按 mode 判断是否审批 | P0 |
| F015 | run_shell | 在 active root 内执行受控命令，必须审批 | P0 |
| F016 | Approval | 中高风险工具调用前暂停等待审批 | P0 |
| F017 | Resume | 审批通过或拒绝后继续 Run，且不能重复创建已存在 Step | P0 |
| F018 | Execution Feed | 中栏展示对话、工具调用、审批、结果和错误 | P0 |
| F019 | Event 持久化 | 所有关键 runtime event 必须写入数据库 | P0 |
| F020 | Artifacts | Public Mode 只展示 Artifacts；Workspace Mode 也可展示 Artifacts | P0 |
| F021 | Workspace 文件树 | 仅 Workspace Mode 显示文件树和只读预览 | P0 |
| F022 | Cancel Run | 用户可以取消 running 状态任务 | P0 |
| F023 | 前台生命周期 | 关闭窗口时 running Run 必须取消或等待完成 | P0 |
| F024 | 启动恢复 | 启动时恢复最近 active session，并显示 waiting_approval Run | P0 |

### 8.2 P1/P2 功能

| 编号 | 功能 | 说明 | 优先级 |
|---|---|---|---|
| F101 | apply_patch | 对文件做局部 patch，而不是整体覆盖 | P1 |
| F102 | Diff 展示 | 展示文件修改前后差异 | P1 |
| F103 | 写入快照 | write_file 前保存 before / after 快照 | P1 |
| F104 | 文件级恢复 | 支持从指定 tool_call 恢复文件 | P1 |
| F105 | 上下文压缩 | 历史过长时压缩为摘要 | P1 |
| F106 | Tool Timeout 配置 | 每个工具支持超时配置 | P1 |
| F107 | Run Timeout | 每个 Run 支持最大执行时长 | P1 |
| F108 | 重试策略 | 模型调用和工具调用支持有限重试 | P1 |
| F109 | 多 Agent | 多 Agent 配置、模板、权限策略和专属工具集 | P2 |
| F110 | 后台执行 | tray、通知、任务守护和后台继续执行 | P2 |
| F111 | PostgreSQL | 服务化部署时支持 PostgreSQL | P2 |

---

## 9. 审批策略

| 工具 | Public Mode | Workspace Mode |
|---|---|---|
| list_files | 自动 | 自动 |
| read_file | 自动 | 自动，敏感文件仍禁止 |
| write_file | 自动 | 审批 |
| run_shell | 审批 | 审批 |

MVP 不做文件写入前快照，因此“可恢复”只指审批恢复和 Run 状态恢复，不承诺文件内容一键恢复。

---

## 10. 模型配置

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

## 11. 本地数据空间

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

## 12. 前台执行生命周期

Eidos 是前台执行型 Agent，不是后台任务守护器。窗口关闭即准备退出 Runtime。

规则：

- 没有 running Run 时，关闭窗口会正常退出 sidecar。
- 有 running Run 时，用户必须选择等待完成或取消任务并退出。
- waiting_approval Run 可以持久化；下次启动后仍显示 waiting_approval，用户可继续 approve / reject。
- succeeded / failed / canceled Run 正常保留 timeline 和 artifacts。
- sidecar 不作为后台 daemon 存活。

---

## 13. 非功能需求

### 13.1 本地部署

MVP 必须支持桌面端本地运行：

- Electron + React 桌面应用
- Python FastAPI sidecar
- SQLite 数据库
- 本地文件系统 workspace
- 单用户、单机、单进程 Runtime

### 13.2 安全性

| 项目 | 要求 |
|---|---|
| 文件隔离 | 工具不得访问 active root 之外的文件 |
| 路径防逃逸 | 禁止 `../`、绝对路径、符号链接等方式逃逸到 active root 外 |
| 敏感路径保护 | 禁止读取 `.env`、`.ssh`、系统目录等 |
| Shell 审批 | shell 命令默认必须审批 |
| Shell cwd 限制 | 命令只能在 active root 下执行 |
| Shell 超时 | 默认 30 秒 |
| Shell 输出限制 | 默认最多保存 20000 字符 |
| 子进程控制 | Shell 超时或取消时必须终止进程组 |
| Renderer 隔离 | Renderer 不持有 sidecar token，不直接访问 sidecar |

### 13.3 可观测性

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

### 13.4 可恢复性

MVP 至少支持审批恢复：

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

恢复过程必须满足：

- 不重复创建已有 `step_index`。
- 不重复执行已经 succeeded / failed / rejected 的 tool call。
- approve / reject 接口幂等。
- cancel 与 approve / reject 并发时只允许一个状态转换成功。
- resume 使用原 Run 的 model_config_snapshot。

---

## 14. 数据库选型

MVP 数据库选择 SQLite。

原因：

- Eidos 是个人桌面端应用，用户不应该为了启动应用先安装 Docker。
- MVP 是单用户、单机、单进程 Runtime，SQLite 足够承载状态持久化。
- `~/.eidos/eidos.db` 符合桌面应用的数据管理习惯。
- Run / Step / ToolCall / Approval / Event / Artifact 都可以落 SQLite。

PostgreSQL 作为后续服务化、多用户、多 worker 部署选项，不进入 MVP。

---

## 15. 验收标准

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
| A010 | 写入文件 | Public Mode 自动写入；Workspace Mode 写入前审批 |
| A011 | Shell 审批 | 执行 shell 前必须进入 waiting_approval |
| A012 | 审批恢复 | approve 后执行原 tool call，并从下一 step 继续 |
| A013 | 拒绝恢复 | reject 后模型能基于拒绝原因重新生成方案 |
| A014 | 路径隔离 | `../`、绝对路径、符号链接和前缀路径逃逸会被拒绝 |
| A015 | 取消任务 | running 状态可以取消 |
| A016 | 启动恢复 | 下次打开恢复最近 active session 和 waiting_approval Run |
| A017 | Public Artifacts | Public Mode 只展示 Artifacts，不展示底层 files |
| A018 | Workspace 文件树 | Workspace Mode 显示文件树和只读预览 |
| A019 | 前台生命周期 | 关闭窗口时 running Run 必须取消或等待完成 |
| A020 | 错误可见 | 失败原因保存并可查询 |

---

## 16. MVP 交付物

| 交付物 | 说明 |
|---|---|
| 桌面应用 | Electron + React Agent Workbench |
| Python sidecar | FastAPI Runtime，由 Electron Main 启停 |
| SQLite schema | `~/.eidos/eidos.db` |
| Eidos Home | `~/.eidos` 目录结构初始化 |
| 内置工具 | list_files / read_file / write_file / run_shell |
| Runtime Engine | Agent Loop + 状态机 |
| Approval Resume | approve / reject 后可恢复执行 |
| Execution Feed | 对话、工具、审批、结果的统一执行流 |
| Artifacts | Public Mode 和 Workspace Mode 的产物展示 |
| API 文档 | 本地 sidecar OpenAPI，仅作为内部调试 |
| 示例任务 | 代码生成、文件修改、命令执行审批、公共空间产物生成 |

---

## 17. 后续演进方向

MVP 稳定后再补：

1. apply_patch 和 diff。
2. 写入快照和文件级恢复。
3. MCP Client。
4. Skill 系统。
5. 长期记忆。
6. 多 Agent 管理。
7. 多模型路由。
8. 浏览器工具。
9. 后台执行、tray 和通知。
10. PostgreSQL 服务化部署。
11. 分布式 Runtime Worker。
12. 评估与回放系统。
