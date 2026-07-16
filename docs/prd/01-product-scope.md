# 产品定位与范围

版本：v0.4

范围说明：本文描述完整目标态。第一期交付范围以 [MVP Lite](../mvp-lite.md) 为准；本文中的 MVP P0 不再等同于第一期全量实现。

第二期实施以 [第二期实施范围清单](../mvp-phase-2.md) 为准。本期只把完整目标态中的存储安全、Event、FIFO 调度、暂停/继续、Durable Intent、事实确认、敏感扫描和文件工具契约推进到可验收状态；Model Profile 重构、Public Mode、Artifact 和传输架构迁移不在本期实施。

## 1. 产品定位

Eidos 是一个仅面向 macOS 的本地桌面端个人 Agent Runtime，让 Agent 能围绕任务持续读取信息、修改文件、执行命令，并将审批和执行过程完整记录下来。

Slogan：

> 让想法拥有可执行的形态。

Eidos MVP 不是聊天机器人，也不是后台任务守护器。它首先验证下面这条前台执行闭环：

```text
创建 Session
  -> 创建并排队 Run
  -> 模型回复或调用工具
  -> 只读工具自动执行
  -> 文件写入或 Shell 等待审批
  -> 获批后在安全边界内执行
  -> 暂停、恢复、取消或完成
  -> 持久化 Timeline 和 Artifact
```

## 2. 目标用户

| 用户 | 主要任务 |
|---|---|
| 开发者用户 | 阅读和修改代码、生成文件、执行测试与检查命令 |
| 个人工作用户 | 在公共空间生成报告、计划、模板和其他文件产物 |

MVP 为单用户、单机应用，不建立账号、组织、租户或企业权限体系。

## 3. 产品原则

- 仅支持 macOS；Windows 与 Linux 不进入 MVP。
- Eidos 是前台执行型 Agent，不在窗口关闭后作为 daemon 继续运行。
- MVP 只内置一个默认 Agent：Eidos，不提供多 Agent 管理。
- system prompt 是内部运行协议，不向用户开放查看或编辑。
- Model Profile 必须先通过不携带用户任务数据的显式能力测试，才能被 Session 选择；Run 创建时固化已验证的模型配置与能力快照。
- Model Profile 显式选择 Responses 或 Chat Completions；Eidos 不按厂商、模型名或错误响应猜测协议，也不在 Run 中跨协议回退。
- 模型调用优先使用已验证的 WebSocket，必要时有界降级到原 endpoint 的 HTTP(S) streaming；传输恢复不会改变模型、输入、参数或 Run 的非密钥快照。
- 模型请求语义由本地 Timeline 与 Context Builder 重建，不依赖 Provider 保存会话或 previous response。
- Provider 生成的工具参数始终不可信；Eidos 本地封闭 schema、安全规则与执行前复检是唯一授权依据。
- 持久化 Run 固化工具契约；每个 Step 只向模型暴露当时可用的确定性工具子集，但隐藏不是安全边界。
- 每个已创建 ToolCall 只有一个不可变结构化结果事实；Context Builder 只能生成有界、可审计的模型投影，不得把裁剪或脱敏后的视图变成第二个业务结果。
- 多个 Run 可以创建和保留，但任意时刻最多只有一个 Run 调用模型或执行工具。
- 所有有副作用操作都必须显式建模；审批不能突破沙箱边界。
- Eidos 自身数据保存在 `~/.eidos`，不默认向用户项目写入 `.eidos/`。
- Public Mode 的底层文件空间不直接暴露给用户，最终产物通过 Artifact 展示。

## 4. 运行模式

### 4.1 Workspace Mode

用户选择真实项目目录，Session 绑定 Workspace，Agent 在该目录中完成任务。

- 文件工具只能访问 active root。
- Workspace 文件树和只读预览可见。
- Agent 文件写入必须审批并展示 diff。
- Agent Shell 必须审批，并始终运行在 `workspace_write` 硬沙箱中。
- MVP 不提供内嵌 Terminal，只提供“在系统 Terminal 中打开 Workspace”。

### 4.2 Public Mode

用户不选择项目目录，Eidos 为 Session 创建内部执行空间：

```text
~/.eidos/public/sessions/{session_id}/files
```

- 底层 `files/` 不显示文件树。
- 文件工具与 Shell 仍遵守与 Workspace Mode 相同的审批和安全规则。
- 普通写入不会自动成为 Artifact。
- Agent 必须显式调用 `publish_artifact` 发布最终产物。
- 已发布 Artifact 是不可变快照，默认长期保留。

## 5. MVP P0 范围

- Electron + React 桌面应用与 Python FastAPI sidecar。
- `~/.eidos` 初始化、SQLite 状态持久化，以及可编辑、可 Archive、显式能力验证的 Model Profile。
- Workspace/Public Session 与持久化 FIFO Run 队列。
- ReAct-style Runtime Loop、Execution Segment、版本化模型/工具请求契约、canonical ToolResult、上下文预算和确定性有界投影。
- `list_files`、`read_file`、`read_file_range`、`search_text`。
- `write_file`、`apply_patch`、`delete_file`。
- 固定文件遍历排除、单文件一致读取和严格 UTF-8 文本契约。
- macOS Seatbelt `run_shell`。
- Shell Toolchain Profile、资源上限、Workspace 变化 manifest 和有界输出流。
- `publish_artifact` 与不可变 Artifact 快照。
- Approval、Reject、版本冲突、事实确认屏障、取消与崩溃恢复。
- 单实例执行权、启动 ready 屏障、安全数据库迁移、Workbench 一致快照和持久 Workspace 身份。
- Shell guardian 在前台 Runtime 异常退出后有界清理受控进程；不承诺无法被系统同时强杀时的绝对零遗留。
- 确定性敏感规则、分级拒绝/脱敏与全入口扫描。
- SSE 实时 Execution Feed 与持久化 Timeline。
- 闭合且可跨重启幂等的本地 API/IPC、稳定分页与版本化 Event 兼容。
- Run 预算、模型传输降级、Tool timeout、有限重试和 Finalization Call。

## 6. MVP 非目标

- Windows/Linux 支持。
- 内嵌 Workspace Terminal。
- 后台 daemon、tray、通知和持久服务管理。
- 长期运行的 Agent Shell 服务进程。
- 自动接管另一路 Runtime、强制抢占状态目录锁或在 Workspace 移动后静默迁移旧授权。
- 自动清理用户数据、自动覆盖数据库备份，或在存储故障后自动重放副作用。
- 多 Agent 与并行工具执行。
- MCP 插件市场、浏览器自动化、Skill 自动生成和长期记忆。
- 平台知识库、术语库、Text2SQL、本体和企业资源接入。
- 文件写入前快照、一键回滚、跨文件原子事务和完整 diff 编辑器。
- Edit then Approve。
- 用户可配置的运行预算和工具 timeout。
- 智能摘要式上下文 compaction。
- Keychain 密钥存储；MVP 接受受限权限下的 `config.toml` 明文配置。
- 敏感规则远程更新、用户自定义、历史数据追溯重扫和 Secret 注入。
- `.gitignore`/用户自定义文件遍历排除、Workspace 读取快照和文件变更自动回滚。
- 二进制/压缩/加密 Artifact 发布与需格式解析的内容扫描。
- Git commit、checkout、reset、stash 等修改 `.git` 的操作。
- Model Profile 物理删除、共享凭证、任意自定义 Header、TLS 校验绕过、定时自动能力探测和按模型名自动推断上下文容量。
- 依赖厂商专有幂等 Header、精确 tokenizer 或把 usage 未知的失败请求按零计费。
- Provider 托管 conversation/thread、跨 wire API 自动兼容、厂商专用 Adapter 和依赖服务端 response history 的恢复。
- Provider strict function calling、自由形态工具 map、用户自定义工具 schema 和基于任务文本的启发式工具裁剪。
