# Eidos Agent Runtime MVP 需求文档 PRD

版本：v0.2  
语言：Python  
数据库：PostgreSQL，本地 Docker 部署  
范围：独立 Agent Runtime，不接入现有平台资源  

---

## 1. 产品名称与 Slogan

项目名称：Eidos

Slogan：

> 让想法拥有可执行的形态。

Eidos 是一个面向未来工作的 Agent Runtime，帮助智能体理解任务、调用工具、操作工作区，并将每一步执行过程清晰记录下来。

---

## 2. 背景

当前目标是先手搓一个类似 Codex / Hermes 方向的个人 Agent，但第一阶段只做 MVP，不接入知识库、术语库、数据库问数、本体等平台资源。

MVP 的核心不是“聊天机器人”，而是一个可以围绕任务持续执行的 Agent Runtime：

```text
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
完成任务或暂停等待用户决策
```

参考设计理念：

- Codex CLI：强调本地工作区、工具执行、终端集成和任务运行体验。
- Codex Agent Loop：强调 sandbox、approval mode、cwd、工具列表、上下文压缩和 prompt caching。
- LangGraph / LangChain Human-in-the-Loop：强调工具调用前中断、人工 approve / edit / reject / respond，并通过持久化状态实现恢复。

---

## 3. 产品定位

### 3.1 一句话定位

Eidos 是一个本地可部署、可执行、可审批、可追踪、可恢复的轻量级个人 Agent Runtime。

### 3.2 MVP 不解决的问题

MVP 不做：

- 平台知识库接入
- 术语库改写
- Text2SQL / 数据库分析
- 本体检索
- 多 Agent 协作
- 长期记忆
- Skill 自动生成
- MCP 插件市场
- 浏览器自动化
- 企业级权限体系
- 分布式任务调度

### 3.3 MVP 要解决的问题

MVP 只解决：

- 用户可以创建默认 Agent：Eidos
- 用户可以创建 Session
- 用户可以提交一次任务 Run
- Agent 可以循环思考和执行工具
- Agent 可以读取、写入工作区文件
- Agent 可以请求执行受控 Shell 命令
- 中高风险工具调用需要人工审批
- 审批通过后可以继续执行原工具调用
- 审批拒绝后可以把拒绝原因返回给模型重新规划
- 所有执行过程可持久化追踪
- 用户可以取消任务
- 最终产物可以保存到工作区

---

## 4. 用户角色

| 角色 | 说明 |
|---|---|
| 开发者用户 | 使用 Eidos 完成代码生成、脚本生成、文件整理、项目初始化等任务 |
| 系统管理员 | 本地部署和维护 Eidos Runtime |
| Eidos Runtime | 负责模型调用、工具调用、状态管理、审批控制和事件输出 |

MVP 阶段不做多用户体系，默认单用户本地使用。

---

## 5. 使用场景

### 5.1 生成代码项目

用户输入：

```text
帮我创建一个 FastAPI demo，包含 /health 接口和 README。
```

Eidos 应该：

1. 创建任务 Run。
2. 规划需要生成的文件。
3. 写入 `main.py`。
4. 写入 `README.md`。
5. 请求执行 `python -m py_compile main.py` 或类似命令的审批。
6. 审批通过后执行命令。
7. 保存命令、输出、退出码、耗时。
8. 根据结果修正文件或输出完成说明。

### 5.2 修改已有文件

用户输入：

```text
帮我把这个项目里的日志改成标准 logging，并补充注释。
```

Eidos 应该：

1. 读取工作区文件列表。
2. 读取目标文件。
3. 生成修改方案。
4. 写入或 patch 文件。
5. 展示变更记录。
6. 必要时请求执行测试命令的审批。

### 5.3 执行受控命令

用户输入：

```text
帮我运行测试，看有没有问题。
```

Eidos 应该：

1. 生成需要执行的命令和理由。
2. 判断 `run_shell` 为高风险工具。
3. 暂停任务并请求用户审批。
4. 用户批准后执行。
5. 保存命令、输出、退出码、耗时。
6. 把结果返回给模型继续分析。

### 5.4 审批拒绝后继续

用户拒绝 shell 命令后，Eidos 应该：

1. 记录审批拒绝。
2. 把拒绝原因作为工具结果返回给模型。
3. 让模型重新选择方案。
4. 可以直接输出手动执行建议，或改为只生成文件。

---

## 6. 功能范围

### 6.1 P0 功能

| 编号 | 功能 | 说明 | 优先级 |
|---|---|---|---|
| F001 | 创建 Agent | 支持名称、描述、system prompt、模型配置；默认 Agent 名称为 Eidos | P0 |
| F002 | 创建 Session | 一个会话绑定一个 Agent 和一个 workspace | P0 |
| F003 | 创建 Run | 用户每次提交任务创建一个 Run | P0 |
| F004 | Agent Loop | 模型输出文本或工具调用，Runtime 循环执行 | P0 |
| F005 | Tool Registry | 注册内置工具，暴露工具 schema 给模型 | P0 |
| F006 | list_files | 查看工作区目录结构 | P0 |
| F007 | read_file | 读取工作区文件 | P0 |
| F008 | write_file | 写入工作区文件 | P0 |
| F009 | run_shell | 在工作区内执行受控命令 | P0 |
| F010 | Approval | 中高风险工具调用前暂停等待审批 | P0 |
| F011 | Resume | 审批通过或拒绝后继续 Run，且不能重复创建已存在 Step | P0 |
| F012 | SSE 事件流 | 前端实时展示模型输出、工具调用、审批状态 | P0 |
| F013 | Run Timeline | 展示 Run / Step / ToolCall / Approval / Event 轨迹 | P0 |
| F014 | Workspace 隔离 | 工具只能访问当前 workspace | P0 |
| F015 | Cancel Run | 用户可以取消 running 或 waiting_approval 状态的任务 | P0 |
| F016 | 错误记录 | 模型错误、工具错误、审批错误需要持久化 | P0 |
| F017 | Event 持久化 | 所有关键 runtime event 必须写入数据库，用于断线重连和回放 | P0 |

### 6.2 P1 功能

| 编号 | 功能 | 说明 | 优先级 |
|---|---|---|---|
| F101 | apply_patch | 对文件做局部 patch，而不是整体覆盖 | P1 |
| F102 | Diff 展示 | 展示文件修改前后差异 | P1 |
| F103 | Artifact 管理 | 保存最终产物，如代码、报告、脚本 | P1 |
| F104 | 上下文压缩 | 历史过长时压缩为摘要 | P1 |
| F105 | Tool Timeout 配置 | 每个工具支持超时配置 | P1 |
| F106 | Run Timeout | 每个 Run 支持最大执行时长 | P1 |
| F107 | Step Limit | 每个 Run 限制最大 step 数 | P1 |
| F108 | 重试策略 | 模型调用和工具调用支持有限重试 | P1 |

---

## 7. 非功能需求

### 7.1 本地部署

MVP 必须支持本地部署：

- Python 3.11+
- PostgreSQL Docker 部署
- 本地文件系统 workspace
- 单进程后端服务
- 前端可选，MVP 可以先用 API + 简单 Web UI

### 7.2 安全性

| 项目 | 要求 |
|---|---|
| 文件隔离 | 工具不得访问 workspace 之外的文件 |
| 路径防逃逸 | 禁止 `../`、绝对路径、符号链接等方式逃逸到 workspace 外 |
| Shell 审批 | shell 命令默认必须审批 |
| Shell cwd 限制 | 命令只能在 workspace 下执行 |
| Shell 超时 | 默认 30 秒 |
| Shell 输出限制 | 默认最多保存 20000 字符 |
| 敏感路径保护 | 禁止读取 `.env`、`.ssh`、系统目录等 |
| 危险命令提示 | 对明显危险命令拦截或提升审批提示，不把黑名单作为唯一安全边界 |
| 子进程控制 | Shell 超时或取消时必须终止进程组 |

### 7.3 可观测性

必须记录：

- run_id
- session_id
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

### 7.4 可恢复性

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

---

## 8. 数据库选型

MVP 数据库选择 PostgreSQL。

| 对比项 | PostgreSQL | SQLite |
|---|---|---|
| 本地部署 | Docker 一条命令即可 | 文件级，无需服务 |
| 并发写入 | 更好 | 较弱 |
| JSON 字段 | JSONB 强 | JSON 能力有限 |
| 状态事务 | 强 | 中等 |
| 后续扩展 | 适合演进到服务化 | 更适合单机小工具 |
| 迁移生产 | 平滑 | 后期可能迁移成本高 |

Agent Runtime 有大量状态写入：Run、Step、ToolCall、Approval、Event、Artifact。即使 MVP 是本地部署，也建议直接使用 PostgreSQL，避免后续迁移。

---

## 9. 验收标准

| 编号 | 验收项 | 标准 |
|---|---|---|
| A001 | 创建 Agent | 可以通过 API 创建默认 Agent：Eidos |
| A002 | 创建 Session | 可以创建独立 workspace |
| A003 | 创建 Run | 用户输入后生成 Run 记录 |
| A004 | 模型回复 | 不调用工具时可以正常流式回复 |
| A005 | 读取文件 | Agent 可以读取 workspace 文件 |
| A006 | 写入文件 | Agent 可以写入 workspace 文件 |
| A007 | Shell 审批 | 执行 shell 前必须进入 waiting_approval |
| A008 | 审批恢复 | approve 后执行原 tool call，并从下一 step 继续 |
| A009 | 拒绝恢复 | reject 后模型能基于拒绝原因重新生成方案 |
| A010 | 路径隔离 | `../`、绝对路径、符号链接和前缀路径逃逸会被拒绝 |
| A011 | 取消任务 | running / waiting_approval 状态可以取消 |
| A012 | Run Timeline | 可以查看完整 step、tool call、approval 和 event |
| A013 | 错误可见 | 失败原因保存并可查询 |
| A014 | SSE 回放 | SSE 断线后可以从已持久化 event 继续读取 |

---

## 10. MVP 交付物

| 交付物 | 说明 |
|---|---|
| 后端服务 | FastAPI 应用 |
| 数据库迁移脚本 | PostgreSQL schema |
| Docker Compose | PostgreSQL 本地部署 |
| 内置工具 | list_files / read_file / write_file / run_shell |
| Runtime Engine | Agent Loop + 状态机 |
| Approval Resume | approve / reject 后可恢复执行 |
| SSE 接口 | 运行事件实时输出和断线回放 |
| Run Timeline API | 查询完整执行轨迹 |
| API 文档 | OpenAPI 自动生成 |
| 示例任务 | 代码生成、文件修改、命令执行审批 |

---

## 11. 后续演进方向

MVP 稳定后再补：

1. apply_patch 和 diff。
2. MCP Client。
3. Skill 系统。
4. 长期记忆。
5. 多模型路由。
6. 多 Agent 协作。
7. 浏览器工具。
8. 企业资源接入。
9. 分布式 Runtime Worker。
10. 评估与回放系统。
