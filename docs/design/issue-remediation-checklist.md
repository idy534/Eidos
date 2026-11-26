# Eidos 问题修复与清零清单（按 Batch 归类）

> **基线分支**：`main`（`v0.5.7` / `1cc92af4`）
> **整理日期**：2026-11-24
> **使用规则**：每完成一项修复并通过定向验证后，将该项 `- [ ]` 改为 `- [x]`，在项末补充提交哈希与验证范围，并同步更新下方「总进度汇总表」，直到所有待修项清零。

> **规则优先级声明**：按 [AGENTS.md](../../AGENTS.md#L1-L8) 第 1 节与 [自动恢复与模型上下文](../../AGENTS.md#L490-L491) 约束，普通工具错误、容量与输出上限不得直接结束普通交互 Run；而 [execution-gates.md](../current/execution-gates.md#L138)、[capabilities.md](../current/capabilities.md#L159) 与 [goals.md](../current/features/goals.md#L41) 仍保留“LoopGuard 停止 Run 并标记 Goal 受阻”的旧描述。本清单 **B1-1** 与 **B6-2** 按 `AGENTS.md` 高优先级语义设计修复并同步修正过时文档。

---

## 总进度汇总表

| Batch | 主题 | 总项数 | 已完成 | 待处理 | 状态 |
|---|---|---:|---:|---:|---|
| Batch 1 | Runtime 正确性、并发锁与设置页护栏 | 4 | 4 | 0 | 已完成 |
| Batch 2 | Windows 立即修复（审批、Hooks、Git SSH 与平台文案） | 4 | 4 | 0 | 已完成 |
| Batch 3 | macOS 打包签名、远程 MCP 网络与运维诊断 | 3 | 3 | 0 | 已完成 |
| Batch 4 | 长会话性能、桌面稳态与半成品接线 | 7 | 7 | 0 | 已完成 |
| Batch 5 | 编码核心闭环（编辑、缓存、回滚、审阅） | 4 | 4 | 0 | 已完成 |
| Batch 6 | 架构技术债与文档-代码漂移清理 | 2 | 2 | 0 | 已完成 |
| **合计** | **本期纳入清零追踪的待修项** | **24** | **24** | **0** | **已清零** |

> 另有 **6 项**已根据产品与个人学习研究定位明确决策**暂不修复 / 延后或不适用**，存档于文末「[暂不修复 / 延后或不适用项](#暂不修复--延后或不适用项不计入本期清零分母)」，不计入上述 24 项清零分母。

---

## Batch 1 — Runtime 正确性、并发锁与设置页护栏（4 项）

- [x] **B1-1 `[P0][Bug]` 修复 `engine.py` 死循环空转与无退避问题（含文档规则对齐）**
  - **代码范围**：
    - [engine.py](../../runtime/eidos_runtime/runtime/engine.py#L327-L339)、[L895-L906](../../runtime/eidos_runtime/runtime/engine.py#L895-L906)、[L969-L1050](../../runtime/eidos_runtime/runtime/engine.py#L969-L1050)、[L1208-L1213](../../runtime/eidos_runtime/runtime/engine.py#L1208-L1213)、[L1315-L1321](../../runtime/eidos_runtime/runtime/engine.py#L1315-L1321) — 关键词：`CapacityExceeded`、`context_recovery_level`、`str(error) == "length"`、`protocol_repair_exhausted`、`repeated_tool_call_recovery`、`LoopAction.PAUSE`、`_loop_recovery_context`
    - [builder.py](../../runtime/eidos_runtime/context/builder.py#L655-L658) — 关键词：`min(level, 7)`
    - [execution-gates.md](../current/execution-gates.md#L138)、[capabilities.md](../current/capabilities.md#L159)、[goals.md](../current/features/goals.md#L41)
  - **修复建议**：
    1. 当 `context_recovery_level` 达到上限（7）仍触发 `CapacityExceeded` 时，不再用相同预算原地空转：改为执行确定性降级（强制只保留最小安全上下文/摘要），若单条超限输入确实无法装入模型窗口则返回明确错误止损。
    2. 遵守 `AGENTS.md` 不自动结束普通交互 Run 的前提，对连续 `length`、`SamplingCapacityExceeded`、耗尽 `validation_failed` 及重复无进展恢复增加可取消的指数退避与界面告警事件。
    3. 同步更新 3 处 `docs/current/` 文档中的 LoopGuard 与自动恢复旧表述，消除规则冲突。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：`test_context_capacity_recovery.py` 5/5 通过；`test_model_persistence.py` + `test_run_capacity_continuation.py` 40 通过（排除既有 Windows `/usr/bin/printf` 失败用例 `test_oversized_shell_wait_is_capped_and_run_completes`）；Ruff 通过。

- [x] **B1-2 `[P0][能力]` 设置页新增 Goal / Automation 成本、Token 与无进展轮次护栏（暂停支持恢复并重新计数）**
  - **明确产品约束**：
    - 护栏配置**只放在设置页**，**不要在 Run 执行界面中展示**。
    - 触发任一护栏阈值（成本、Token 或无进展轮次）后执行**暂停**，且暂停后支持用户**恢复**。
    - 用户点击恢复后，成本、Token、无进展轮次等护栏计数**从 0 重新计算**；新一轮执行如果再次超限，则再次触发暂停。
  - **代码范围**：
    - [engine.py](../../runtime/eidos_runtime/runtime/engine.py)、[goals.py](../../runtime/eidos_runtime/application/goals.py)、[automations.py](../../runtime/eidos_runtime/application/automations.py)、[persistence/goals.py](../../runtime/eidos_runtime/persistence/goals.py)、[persistence/automations.py](../../runtime/eidos_runtime/persistence/automations.py)、[automation_scheduler.py](../../runtime/eidos_runtime/runtime/automation_scheduler.py)、[schema.py](../../runtime/eidos_runtime/db/schema.py) — 关键词：`session_goals`、`goal_runs`、`AutomationScheduler`、`idle`
    - [SettingsPage.tsx](../../desktop/renderer/src/components/settings/SettingsPage.tsx)、[RuntimeSettings.tsx](../../desktop/renderer/src/components/settings/RuntimeSettings.tsx)（或设置页新增护栏区块；不得在 Run 界面渲染护栏配置）
  - **修复建议**：
    1. 在设置配置与 SQLite 中持久化 Goal / Automation 护栏阈值（成本上限、Token 上限、无进展轮次上限）以及本轮周期基准点（如 `cycle_started_at` / `cycle_tokens` / `cycle_cost` / `cycle_idle_rounds`）。
    2. 执行期仅累计当前周期增量；达到阈值时安全将 Goal / Automation 置为暂停状态并落盘原因（不丢失原有历史累计展示统计，但护栏仅按本轮增量判定）。
    3. 恢复执行时记录新的周期重置基准点使本轮计数清零，新周期若再次触发阈值则继续进入暂停等待下一次恢复。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：Schema 升级至 v57；`test_guardrails.py` 5/5、`test_storage_schema.py` 18/18、`test_data_backup.py` v45–v57 迁移通过（该文件另有 3 类既有 Windows-only 用例失败）；Desktop `pnpm build:main`、`tsc`、Vitest（41/41）通过；Ruff 通过。

- [x] **B1-3 `[P1][Bug]` 修复 `supervisor.py` 重启与恢复状态恒等自检（5/6 核验失效）**
  - **代码范围**：
    - [supervisor.py](../../runtime/eidos_runtime/runtime/supervisor.py#L440-L504) — 关键词：`resume_run`、`verify_restart_state`、`ResumeVerifier.verify`、`expected_git_head`、`expected_rule_snapshot_id`、`expected_index_snapshot_id`、`expected_context_plan_id`、`expected_permission_snapshot_hash`
  - **修复建议**：恢复与重启核验时实时重新采集磁盘及数据库中的当前真实 `git_head`、规则快照、索引快照、上下文计划与权限哈希作为 `current_*` 参数传入 `ResumeVerifier.verify`，禁止再将 `current.<field>` 同时作为预期值和当前值；补充外部变动后的失败检测回归测试。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：`test_supervisor_resume_verification.py` 9/9、`test_long_task_lifecycle.py` 10/10、`test_model_attempt_context_snapshot.py` 3/3、`test_task_lifecycle_application.py` 4/4 通过；`test_run_workspace_identity.py` 6/7 通过（1 项为既有 Windows `Worktree` POSIX 路径校验失败）；Ruff 通过。

- [x] **B1-4 `[P1][Bug][并发]` 补全 64/152 个未分类 RPC 的并发分类并扩展读连接池**
  - **代码范围**：
    - [dispatch.py](../../runtime/eidos_runtime/protocol/dispatch.py#L47-L220) — 关键词：`classify_request`、`SESSION_READS`、`INDEX_READS`、`CONFIG_READS`、`FREE_READS`、`SESSION_WRITES`、`RUN_WRITES`、`EXCLUSIVE`
    - [server.py](../../runtime/eidos_runtime/protocol/server.py#L2055-L2060)、[response_server.py](../../runtime/eidos_runtime/protocol/response_server.py#L36) — 关键词：`team/`
    - [database.py](../../runtime/eidos_runtime/db/database.py) — 关键词：`ReadConnectionPool`
  - **修复建议**：
    1. 为全部 152 个 RPC 补齐显式并发分类，尤其把 `team/read`、`team/messages`、`team/memberChanges` 及 `agent/changes`、`plan/read`、`skill/detail`、`mcp/tools`、`hook/configuration`、`memory/export`、`model/discoverCustom` 归入并发读或资源分片写通道。
    2. 添加协议测试断言：所有已注册 RPC 必须存在显式分类，禁止新增方法静默回落到全局 `EXCLUSIVE`。
    3. 将高频只读路径逐步接入 `ReadConnectionPool`，缓解单 `Database.lock` 争抢。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：全部 154 个已注册 RPC 显式分类并通过 `test_request_dispatch.py` 13/13、`test_state_read_access.py` 12/12、`test_teams.py` 30/30、`test_planning.py` 17/17；Ruff 通过。

---

## Batch 2 — Windows 立即修复（审批、Hooks、Git SSH 与平台文案）（4 项）

- [x] **B2-1 `[P0][Bug][Windows]` 修复 Main 审批路径校验写死 POSIX `/` 导致 Windows 审批失败（`-32601`）**
  - **代码范围**：
    - [runtime-client.ts](../../desktop/main/runtime-client.ts#L2868-L2878)、[L3009-L3018](../../desktop/main/runtime-client.ts#L3009-L3018) — 关键词：`projectApprovalFileSystemAccess`、`approvalRequestFrom`、`entry.path.startsWith("/")`、`permission_request`
    - [permissions.py](../../runtime/eidos_runtime/sandbox/permissions.py#L35-L45) — 关键词：`FileSystemPermissionEntry`
  - **修复建议**：抽取跨平台绝对路径校验函数 `isAbsoluteFsPath(p)`，同时兼容 POSIX `/...`、Windows 盘符 `^[A-Za-z]:[\\/]` 与 UNC `^\\\\`（保持禁用 `\0` 校验），补充 Windows 盘符路径协议 Fixture 与 Main 单测。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：`pnpm build:main`、`tsc`、`node --test dist/main/approval-path-validation.test.js`（2/2）通过。

- [x] **B2-2 `[P1][Bug][Windows]` 修复 Codex Hooks 解析 `commandWindows` 后被静默丢弃**
  - **代码范围**：
    - [codex_hooks.py](../../runtime/eidos_runtime/extensions/codex_hooks.py#L20)、[L81-L88](../../runtime/eidos_runtime/extensions/codex_hooks.py#L81-L88) — 关键词：`commandWindows`、`parse_hooks`、`hook_command_missing`
    - [domain/hooks.py](../../runtime/eidos_runtime/domain/hooks.py)、[runtime/hooks.py](../../runtime/eidos_runtime/runtime/hooks.py)
  - **修复建议**：在 `HookDefinition` 保留 `command_windows` 字段，校验改为要求 `handler.command or handler.commandWindows`（至少存在其一，或按平台选取），在 Windows 执行 Hook 时优先使用 `commandWindows` 并补充跨平台单测。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：已重新生成 Hook 与 SessionFork TS 契约；`test_hooks_windows.py` 3/3、Ruff 及 `runtime-client-hooks.test.js`（Hook fixtures / Plugin presentation 2/2 契约项；同文件两条直接 spawn `.venv/bin/python` 的集成项因 POSIX 路径不适用于 Windows 未测）通过。

- [x] **B2-3 `[P1][Bug][Windows]` 修复 MinGit 覆盖 `GIT_SSH_COMMAND` 并删除 `SSH_AUTH_SOCK` 破坏原生 OpenSSH Agent**
  - **代码范围**：
    - [git/native.py](../../runtime/eidos_runtime/git/native.py#L97-L105)、[L234-L239](../../runtime/eidos_runtime/git/native.py#L234-L239) — 关键词：`GIT_SSH_COMMAND`、`SSH_AUTH_SOCK`、`ssh.exe`
  - **修复建议**：Windows 上优先探测系统原生 `%SystemRoot%\System32\OpenSSH\ssh.exe` 并保留宿主 OpenSSH Agent 通道（仅附带必要的非交互安全参数，不再强行替换为 MSYS2 `ssh.exe` 并清空 `SSH_AUTH_SOCK`）。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：`test_windows_git_ssh.py` 2/2（含空命名管道前缀边界修正）、`test_windows_platform.py::test_bundled_windows_git_and_ripgrep` 1/1 及 Ruff 通过。

- [x] **B2-4 `[P1][体验][Windows]` 修复无沙箱审批提示硬编码 `"macOS"` 文案**
  - **明确产品约束**：本期**只修复到平台文案按 OS 正确渲染**的程度；Windows OS 进程沙箱需重新设计与开发，已单列延后（见 **D-3**）。
  - **代码范围**：
    - [tool_runtime.py](../../runtime/eidos_runtime/runtime/tool_runtime.py#L1314)、[L1365](../../runtime/eidos_runtime/runtime/tool_runtime.py#L1365) — 关键词：`"macOS"`、`require_escalated`
  - **修复建议**：将两处硬编码 `"macOS"` 改为根据运行平台动态拼接平台名称与对应风险提示文案，并补充 Windows 提示文本单元测试。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：`test_tool_runtime_platform_wording.py` 3/3（覆盖 `win32`、`darwin` 及 `_host_platform_label`）及 Ruff 通过。

---

## Batch 3 — macOS 打包签名、远程 MCP 网络与运维诊断（3 项）

- [x] **B3-1 `[P0][发布]` 补齐 `entitlements.mac.plist` 并跑通 macOS Hardened Runtime 签名公证冒烟**
  - **代码范围**：
    - [entitlements.mac.plist](../../packaging/entitlements.mac.plist#L1-L11)、[entitlements.mac.inherit.plist](../../packaging/entitlements.mac.inherit.plist#L1-L11) — 关键词：`allow-jit`、`allow-unsigned-executable-memory`、`disable-library-validation`
    - [package-macos.sh](../../scripts/package-macos.sh#L151-L153)、[macos-sign.mjs](../../scripts/macos-sign.mjs)、[electron-builder.yml](../../electron-builder.yml#L33-L34) — 关键词：`hardenedRuntime`、`notarize`、`entitlements`
  - **修复建议**：补齐 Electron V8 JIT、内置 CPython native wheels、`node-pty` 及动态加载外部二进制所需最小 Hardened Runtime entitlements（`allow-jit`、`allow-unsigned-executable-memory`、`disable-library-validation` 等，主应用与子二进制按需区分），并在签名环境中完成端到端签名、公证及原生包启动冒烟验证。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：已配置最小 3 项 Hardened Runtime entitlements 并通过 `packaging-config.test.mjs` / `macos-sign.test.mjs` 配置校验（`node-pty` `darwin-arm64` 权限项在 Windows 跳过）；实体 macOS arm64 Developer ID 签名、Apple 公证及原生 DMG 启动冒烟需在 macOS 签名构建机上执行。

- [x] **B3-2 `[P1][Bug][网络]` 修复远程 MCP 禁代理/拒收压缩响应，并完善内置 GitHub OAuth `clientId` 配置**
  - **代码范围**：
    - [mcp_auth.py](../../runtime/eidos_runtime/infrastructure/mcp_auth.py#L60-L195) — 关键词：`BoundedMcpTransport`、`EIDOS_MCP_TRUST_ENV`、`_BoundedDecodedStream`、`mcp_compressed_response_unsupported`
    - [models/mcp_connections.py](../../runtime/eidos_runtime/models/mcp_connections.py)、[application/mcp_connections.py](../../runtime/eidos_runtime/application/mcp_connections.py)、[McpConnectionPanel.tsx](../../desktop/renderer/src/components/settings/McpConnectionPanel.tsx) — 关键词：`clientId`
  - **修复建议**：`BoundedMcpTransport` 支持继承系统代理环境变量（可配置），发送 `Accept-Encoding: identity` 或在解压流层施加有界字节计数以兼容启用 gzip/br 的主流远程 MCP 服务；为内置 GitHub 插件补充可用的 OAuth App `clientId` 或允许界面内便捷配置。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：`test_mcp_auth.py` 中 `-k "not test_oauth_loopback_callback_and_close"`（含新增代理路由、gzip/deflate 压缩响应与压缩炸弹截断、`clientId` 校验）10/10 通过（既有 `test_oauth_loopback_callback_and_close` 因 Windows 环回 RST 超时 1s 报 `ConnectTimeout`）；`McpConnectionPanel.behavior.test.tsx` 5/5、Ruff 与 TS 契约校验通过。

- [x] **B3-3 `[P2][打包/运维]` 补全打包资源校验、崩溃落盘日志与设置页诊断/恢复入口**
  - **代码范围**：
    - [build-macos-runtime.sh](../../scripts/build-macos-runtime.sh#L140-L166) — 关键词：`required_resources`
    - [windows.yml](../../.github/workflows/windows.yml)、[packaged-electron-smoke.mjs](../../scripts/packaged-electron-smoke.mjs)
    - [app-logger.ts](../../desktop/main/app-logger.ts)、[data-restore.ts](../../desktop/main/data-restore.ts)、[DataSettings.tsx](../../desktop/renderer/src/components/settings/DataSettings.tsx)、[RuntimeSettings.tsx](../../desktop/renderer/src/components/settings/RuntimeSettings.tsx)、[ManagedResourceSettings.tsx](../../desktop/renderer/src/components/settings/ManagedResourceSettings.tsx)、[main.ts](../../desktop/main/main.ts)
  - **修复建议**：
    1. `build-macos-runtime.sh` 的 `required_resources` 补齐 `mcp_browser*.sbpl` 与 `plugins/browser/*` 断言。
    2. Main 进程接入轮转日志文件落盘与 Electron `crashReporter`（本地 minidump）。
    3. 在设置页补齐：一键脱敏诊断包导出、Runtime 手动重启按钮、旧版 `~/Eidos/Resource/` 清理按钮及应用内备份恢复向导（明确恢复前提示与失败回滚保护）。
    4. 逐步将 `packaged-electron-smoke` 与核心 Main/Renderer/Runtime 测试接入 Windows CI。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：`test_managed_resources_cleanup.py` 2/2、`test_every_classified_method_is_registered` 1/1、Ruff、`packaging-config.test.mjs` / `macos-sign.test.mjs` / `clean-renderer-assets.test.mjs`（12 通过，1 项仅 macOS 跳过）、`app-logger.test.js` / `data-restore.test.js` / `log-redaction.test.js`（19/19）、`DataSettings` / `ManagedResourceSettings` / `ShellEnvironmentSettings` / `MemorySettings` Vitest 全部通过。

---

## Batch 4 — 长会话性能、桌面稳态与半成品接线（7 项）

- [x] **B4-1 `[P1][性能]` 消除长会话全历史重拉风暴、修复 `memo(ExecutionFeed)` 失效与流式卡顿并引入 Feed 虚拟化**
  - **代码范围**：
    - [repositories/sessions.py](../../runtime/eidos_runtime/db/repositories/sessions.py)、[protocol/methods.py](../../runtime/eidos_runtime/protocol/methods.py) — `session/read` 返回 `declaredOutputs`
    - [TurnResults.tsx](../../desktop/renderer/src/components/TurnResults.tsx) — `useCompleteSessionItems`（`enabled` 按需拉取）、`mergeOutputArtifacts`
    - [AppShell.tsx](../../desktop/renderer/src/app/AppShell.tsx) — `ArtifactProvider`/`InputContextProvider`/`handleOpenModelSettings` 稳定化、`requestAnimationFrame` `item/delta` 批处理
    - [ExecutionFeed.tsx](../../desktop/renderer/src/components/ExecutionFeed.tsx)、[useFeedVirtualization.ts](../../desktop/renderer/src/components/useFeedVirtualization.ts)、[ExecutionFeed.css](../../desktop/renderer/src/components/ExecutionFeed.css)
    - [session-state.ts](../../desktop/renderer/src/session-state.ts) — `applyNotifications`
  - **修复建议**：由 Runtime 提供轻量产物索引 RPC（或在 `session/read` 返回声明产物摘要）以移除 `useCompleteSessionItems` 全量循环拉取；稳定 `ArtifactProvider` 与回调引用、memoize `collectOutputArtifacts`；对高频 `item/delta` 按 `requestAnimationFrame` 批处理合并；为 `ExecutionFeed` 引入虚拟滚动列表。
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：`test_session_declared_outputs.py` 1/1、Ruff、`tsc`、`session-state.test.js` + `ExecutionFeed.test.js`（共 103/103 Node 测试含此两项）、`TurnResults.behavior.test.tsx`（25/25）、`ExecutionFeed.behavior.test.tsx`（81/81，含 >24 组虚拟化测试）、`ConversationRunRail.behavior.test.tsx`（16/16）、`AppShell.behavior.test.tsx`（43/43）全部通过。

- [x] **B4-2 `[P1][Bug]` 修复 Runtime 重启脏状态泄漏、崩溃卸载 `<AppShell>` 与硬编码客户端版本 `"0.3.0"`**
  - **代码范围**：
    - [main.ts](../../desktop/main/main.ts)、[runtime-process-state.ts](../../desktop/main/runtime-process-state.ts) — 关键词：`restartRuntime`、`clearRuntimeProcessState`、`activeRunProjectionMap`、`pendingApprovals`
    - [App.tsx](../../desktop/renderer/src/App.tsx)、[AppShell.tsx](../../desktop/renderer/src/app/AppShell.tsx)、[useSessionController.ts](../../desktop/renderer/src/app/useSessionController.ts) — 关键词：`RuntimeGate`、`AppShell`、`eidos.lastActiveSessionId`
    - [runtime-client.ts](../../desktop/main/runtime-client.ts) — 关键词：`buildInitializeParams`、`clientVersion`
  - **修复建议**：`restartRuntime()` 清理残留的 `activeRunProjectionMap` 与 `pendingApprovals` 并通知 Renderer 重同步；Runtime 异常退出时保留 `<AppShell>` 工作台上下文（改为顶部/局部恢复条，且持久化上次活跃会话）；客户端版本改读 `app.getVersion()`，错误提示指向落盘日志目录。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：`pnpm build:main`、`tsc`、`runtime-client-version.test.js`（2/2）、`runtime-shutdown.test.js`、`app-logger.test.js`、`data-restore.test.js`（共 15/15）、`runtime-lifecycle.behavior.test.tsx`（16/16）、`AppShell.behavior.test.tsx`（全通过）验证通过；说明：`runtime-paths.test.js` 中 3 条未传 `platform` 参数的 POSIX 路径断言在 Windows 失败（`Windows` 分支测试通过）。

- [x] **B4-3 `[P2][体验]` 切换会话/模式/Worktree 保活 PTY 终端与 Dock 标签，并去除 Shell 硬编码**
  - **代码范围**：
    - [AppShell.tsx](../../desktop/renderer/src/app/AppShell.tsx) — 按会话持久化 `dockBySession`、透传 `keepAlive`/`tabKey`、删除会话清理终端、放开 Projectless `terminal`
    - [TerminalPanel.tsx](../../desktop/renderer/src/components/TerminalPanel.tsx)、[terminal-shell-preference.ts](../../desktop/renderer/src/components/terminal-shell-preference.ts) — `keepAlive` 注册表、Shell 偏好设置
    - [terminal-manager.ts](../../desktop/main/terminal-manager.ts)、[main.ts](../../desktop/main/main.ts)、[preload.ts](../../desktop/main/preload.ts) — `resolveTerminalShell`、Projectless 根目录终端
    - [ShellEnvironmentSettings.tsx](../../desktop/renderer/src/components/settings/ShellEnvironmentSettings.tsx) — `默认终端 Shell`
  - **修复建议**：将 `openTabs` 与活跃 PTY 实例按 `sessionId` 缓存保活（切换仅隐藏 UI，手动关闭标签或删除会话时销毁）；`TerminalManager` 优先读取 `$SHELL`（macOS）或自动探测 `pwsh.exe`/Git Bash/WSL（Windows）并支持设置项指定默认 Shell，放开 Projectless 根目录终端。
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：`pnpm build:preload`、`pnpm build:main`、`tsc`、`terminal-manager.test.js`（11/11，含 Windows/macOS Shell 探测与 Projectless 测试）、`TerminalPanel.behavior.test.tsx`（3/3）、`ShellEnvironmentSettings.behavior.test.tsx`（6/6）、`AppShell.behavior.test.tsx`（43/43）全部通过。

- [x] **B4-4 `[P2][半成品]` 接通 `BrowserPanel` 前进/后退与 `IPC.BROWSER_ANNOTATE`，消除 3 处常驻轮询**
  - **代码范围**：
    - [BrowserPanel.tsx](../../desktop/renderer/src/components/BrowserPanel.tsx)、[ArtifactPreview.css](../../desktop/renderer/src/components/ArtifactPreview.css) — 事件驱动状态、前进/后退、`标注页面`
    - [artifact-preview.ts](../../desktop/main/artifact-preview.ts)、[main.ts](../../desktop/main/main.ts)、[preload.ts](../../desktop/main/preload.ts)、[ipc-channels.ts](../../desktop/shared/ipc-channels.ts)、[ipc-api.ts](../../desktop/shared/ipc-api.ts)
    - [channel_service.py](../../runtime/eidos_runtime/runtime/channel_service.py)、[server.py](../../runtime/eidos_runtime/protocol/server.py)、[ChannelSettings.tsx](../../desktop/renderer/src/components/settings/ChannelSettings.tsx) — `channel/updated` 推送
    - [ManagedResourceSettings.tsx](../../desktop/renderer/src/components/settings/ManagedResourceSettings.tsx) — 资源就绪后停止空闲轮询
  - **修复建议**：接通浏览器历史前进/后退导航与页面元素标注（标注结果作为引用芯片插入 Composer）；将 3 处常驻轮询迁移为 Runtime 通知推送（或仅在组件可见时按需刷新）。
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：`test_channels.py` 新增长效推送与 `channel/save` 测试 2/2 通过（排除既有 Windows 权限/Win32 运行时 7 项失败）、`test_every_classified_method_is_registered` 1/1、`ipc-contracts.test.js`（3/3）、`ArtifactPreview.behavior.test.tsx`（12/12）、`ChannelSettings.behavior.test.tsx`（17/17）、`ManagedResourceSettings.behavior.test.tsx`（9/9）、`git diff --check` 通过。

- [x] **B4-5 `[P2][半成品]` 审批 UI 补齐“拒绝并附带反馈（`feedback`）”输入框与快捷键**
  - **代码范围**：
    - [ApprovalComposer.tsx](../../desktop/renderer/src/components/ApprovalComposer.tsx)、[useApprovalController.ts](../../desktop/renderer/src/app/useApprovalController.ts)、[AgentPanel.tsx](../../desktop/renderer/src/components/AgentPanel.tsx)、[TeamPanel.tsx](../../desktop/renderer/src/components/teams/TeamPanel.tsx)、[AppShell.tsx](../../desktop/renderer/src/app/AppShell.tsx)
    - [main.ts](../../desktop/main/main.ts#L1765)、[preload.ts](../../desktop/main/preload.ts#L480) — 关键词：`feedback`
  - **修复建议**：在拒绝按钮旁补充可选 `feedback` 输入框及批准/拒绝快捷键，透传至现有 `main.ts`/`preload.ts` 审批响应接口。
  - **验证记录（2026-11-24，Windows 11 x64，未提交工作区）**：`tsc`、`ApprovalComposer.behavior.test.tsx` 19/19（含附带 `feedback` 拒绝与 `Ctrl+Enter`/`Ctrl+Backspace` 快捷键）、`ApprovalComposer.details.behavior.test.tsx` 9/9 通过。

- [x] **B4-6 `[P2][体验]` 升级 `@` 递归文件模糊搜索、统一斜杠命令注册表、移除前端硬编码模型元数据**
  - **代码范围**：
    - [reader.py](../../runtime/eidos_runtime/workspace/reader.py)、[workspace.py](../../runtime/eidos_runtime/application/workspace.py)、[methods.py](../../runtime/eidos_runtime/protocol/methods.py)、[dispatch.py](../../runtime/eidos_runtime/protocol/dispatch.py)、[server.py](../../runtime/eidos_runtime/protocol/server.py) — `workspace/findFiles` BFS + RapidFuzz 递归检索与工作区隔离边界
    - [InputPicker.tsx](../../desktop/renderer/src/components/InputPicker.tsx)、[slash-commands.ts](../../desktop/renderer/src/components/slash-commands.ts)、[Composer.tsx](../../desktop/renderer/src/components/Composer.tsx)、[AppShell.tsx](../../desktop/renderer/src/app/AppShell.tsx) — 统一斜杠命令注册表及 `/compact` `/fork` `/review` 接通
    - [config.py](../../runtime/eidos_runtime/model/config.py)、[ModelSelector.tsx](../../desktop/renderer/src/components/ModelSelector.tsx)、[domain-contracts.ts](../../desktop/shared/domain-contracts.ts)、[runtime-client.ts](../../desktop/main/runtime-client.ts) — 下沉 `inputModalities`/`contextWindowTokens` 至 Runtime 目录，移除前端静态硬编码表
  - **修复建议**：`@` 文件选择改为调用后端递归路径检索（结合已有工作区文件索引与模糊匹配）；`Composer` 与 `InputPicker` 共用同一份斜杠命令注册表并补齐 `/compact` `/fork` `/review`；移除 `ModelSelector.tsx` 静态模型上下文硬编码，完全由 Runtime 配置驱动。
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：`test_workspace_find_files.py`（2/2 通过）、`test_request_dispatch.py`（13/13）、`test_model_config_store.py`（14/14，排除 Windows NTFS `st_mode == 0o600` 断言 `test_create_update_delete_round_trip_uses_the_documented_json_array`）、`test_custom_model_providers.py`（49/49）、`test_model_gateway_protocol.py`（3/3）、Ruff 检查、`InputPicker.behavior.test.tsx`（11/11）、`Composer.behavior.test.tsx`（40/40）、`ModelSelector.behavior.test.tsx`（13/13）、`AppShell.behavior.test.tsx`（43/43）全部通过。

- [x] **B4-7 `[P2][可访问性/桌面]` 恢复 `:focus-visible` 焦点环、打包菜单移除 DevTools/Reload、补托盘/通知/窗口记忆/暗色主题**
  - **代码范围**：
    - [tokens.css](../../desktop/renderer/src/styles/tokens.css)、[controls.css](../../desktop/renderer/src/styles/controls.css)、[theme.ts](../../desktop/renderer/src/theme.ts)、[main.tsx](../../desktop/renderer/src/main.tsx)、[PersonalizationSettings.tsx](../../desktop/renderer/src/components/settings/PersonalizationSettings.tsx) — 恢复 `--focus-ring` 焦点指示、`data-theme="dark"` + `prefers-color-scheme: dark` 暗色主题与主题偏好设置
    - [desktop-shell.ts](../../desktop/main/desktop-shell.ts)、[desktop-shell.test.ts](../../desktop/main/desktop-shell.test.ts)、[main.ts](../../desktop/main/main.ts) — `window-state.json` 窗口尺寸/位置持久化、打包态隐藏 `reload`/`toggleDevTools`、原生 `Notification` 与 Windows/Linux System Tray 后台常驻
  - **修复建议**：恢复清晰克制的 `:focus-visible` 键盘焦点指示并补齐暗色主题变量；打包构建隐藏 `reload`/`toggleDevTools` 菜单项；持久化窗口大小与位置，引入系统原生 `Notification` 与 System Tray 后台常驻（避免 Windows 关窗后误杀定时任务与 IM Channels）。
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：`pnpm build:preload` + `pnpm build:main` + `tsc -p tsconfig.renderer.json --noEmit` 通过；`node --test` 运行 `desktop-shell.test.js`、`ipc-contracts.test.js`、`quit-flow.test.js`、`app-command-dispatch.test.js`（24/24 通过）；`node scripts/check-desktop-contracts.mjs` 通过（注：`check-generated-contracts.mjs` 与 `check-desktop-contracts.test.mjs` 的 1 项因脚本硬编码 `.venv/bin/python` 在 Windows 未跑）；`styles.behavior.test.ts`（34/34）、`McpDetailDialog.behavior.test.tsx`（15/15）、`PersonalizationSettings.behavior.test.tsx`（19/19）全部通过。

---

## Batch 5 — 编码核心闭环（编辑、缓存、回滚、审阅）（4 项）

- [x] **B5-1 `[P1][核心]` 为 `apply_patch` 增加分层容错行匹配**
  - **代码范围**：
    - [codex_patch.py](../../runtime/eidos_runtime/workspace/codex_patch.py#L473-L736) — `_find_line_sequence_tiered`（精确 → `rstrip` → `strip` → Unicode 标点/空白归一化，仅唯一匹配时生效）、`PatchError.hint`、`format_patch_error` 与 `_nearby_snippet` 邻近行号提示
    - [tools/workspace.py](../../runtime/eidos_runtime/tools/workspace.py#L227-L275)、[model/config.py](../../runtime/eidos_runtime/model/config.py#L521-L580) — `gpt-6.2-astra` / `gpt-6.2-sol` / `gpt-6-luna`（原 `gpt-5.2-codex` / `gpt-5.2` 已按后续修订移除）启用 `supports_custom_tools` + `supports_tool_grammar`
  - **修复建议**：参照 Codex Rust 实现 4 级确定性行匹配回退（精确匹配 → 忽略行尾空白 `rstrip()` → 忽略首尾空白 `strip()` → Unicode 引号/破折号归一化），仅在唯一匹配时应用，失配时返回附近行号片段帮助模型一次修正；对支持的模型开放 Grammar 配置。
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：`test_codex_apply_patch.py`（`-k "test_tiered_tolerant_matching or test_openai_codex_profile"` 5/5 通过；说明：Windows 环境下 `commit_patch` 既有安全写文件用例因缺少 macOS descriptor 写权限未在本地提交写盘）、`test_codex_patch_parser.py`（21/21 通过）、Ruff 通过。

- [x] **B5-2 `[P2][核心]` 补齐原生 Prompt Cache 控制与海外主流内置 Provider（OpenAI / Anthropic / Gemini）**
  - **代码范围**：
    - [pyproject.toml](../../pyproject.toml)、[uv.lock](../../uv.lock)、[request_scope.py](../../runtime/eidos_runtime/model/request_scope.py)、[sampling.py](../../runtime/eidos_runtime/runtime/sampling.py)、[native_custom.py](../../runtime/eidos_runtime/model/native_custom.py)、[pydantic_factory.py](../../runtime/eidos_runtime/model/pydantic_factory.py)、[gateway.py](../../runtime/eidos_runtime/model/gateway.py)、[pydantic_ai_client.py](../../runtime/eidos_runtime/model/pydantic_ai_client.py)、[client.py](../../runtime/eidos_runtime/model/client.py)、[replay.py](../../runtime/eidos_runtime/model/replay.py)、[retry.py](../../runtime/eidos_runtime/model/retry.py)、[gateway_types.py](../../runtime/eidos_runtime/model/gateway_types.py)、[model_runner.py](../../runtime/eidos_runtime/runtime/model_runner.py) — 会话级 `prompt_cache_key`、Anthropic 原生 Messages API（`wire_api="anthropic_messages"`、`pydantic-ai-slim[openai,anthropic,retries]==2.57.0` + `anthropic==1.17.0`、原生 `cache_control` 断点、`adaptive` 思考 + `output_config.effort`、带签名 `ThinkingPart` 回放、`529`/`prompt is too long` 错误映射）以及 Gemini `xhigh` 透传
    - [model/config.py](../../runtime/eidos_runtime/model/config.py)、[methods.py](../../runtime/eidos_runtime/protocol/methods.py)、[domain-contracts.ts](../../desktop/shared/domain-contracts.ts)、[runtime-client.ts](../../desktop/main/runtime-client.ts)、[ProviderLogo.tsx](../../desktop/renderer/src/components/ProviderLogo.tsx)、[openai.svg](../../desktop/renderer/src/assets/providers/openai.svg)、[anthropic.svg](../../desktop/renderer/src/assets/providers/anthropic.svg)、[gemini.svg](../../desktop/renderer/src/assets/providers/gemini.svg)、[ProviderLogo.behavior.test.tsx](../../desktop/renderer/src/components/ProviderLogo.behavior.test.tsx)、[architecture.md](../current/architecture.md) — `openai` / `anthropic` / `gemini` 正式 SVG 图标（`openai` 单色遮罩、`anthropic`/`gemini` 彩色 `<img>`）与前沿内置模型目录（`gpt-6.2-astra`/`gpt-6.2-sol`/`gpt-6-luna`、`claude-fable-5-2`/`claude-opus-5-5`/`claude-sonnet-5-5`/`claude-haiku-5-5`、`gemini-4-carbon`/`gemini-4-neon`/`gemini-4-hydrogen`，移除全部 6 个旧模型）
    - [domain/context_management.py](../../runtime/eidos_runtime/domain/context_management.py)、[persistence/context_management.py](../../runtime/eidos_runtime/persistence/context_management.py)、[generate-context-contracts.mjs](../../scripts/generate-context-contracts.mjs)、[ContextDetailsPanel.tsx](../../desktop/renderer/src/components/ContextDetailsPanel.tsx) — `cacheReadTokens` 与 `cacheHitRatePercent` 契约及展示
  - **修复建议**：为 OpenAI Responses 适配器透传稳定会话级 `prompt_cache_key`；按 `pyproject.toml` 扩展 `pydantic-ai-slim` 接入 Anthropic 原生适配器（带 `cache_control` 前缀断点）与 Google Gemini Provider；在 Usage 与 `ContextDetailsPanel` 透出 `cache_read_tokens` 命中率。
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：`test_prompt_cache_and_providers.py`（4/4）、`test_context_metrics.py`（8/8）、`test_model_config_store.py`（14/14，排除 Windows NTFS `st_mode == 0o600` 断言）、`test_model_gateway_protocol.py`（3/3）、`test_custom_model_providers.py`（49/49）、Ruff 检查、`generate-context-contracts.mjs`、`build:preload`、`build:main`、`tsc -p tsconfig.renderer.json --noEmit`、`context-management.test.js` + `ipc-contracts.test.js`（4/4）、`check-desktop-contracts.mjs`、Vitest `useContextUsageController`（12/12）+ `ContextIndicator`（6/6）+ `ModelSelector`（13/13）全部通过。
  - **后续修订记录（2026-11-25，Windows 11 x64，未提交工作区）**：
    - 完成 3 家海外 Provider 图标替换（移除临时内联 SVG，接入 `openai.svg` 单色遮罩与 `anthropic.svg`/`gemini.svg` 彩色 `<img>`）、将 6 个旧模型替换为 10 个前沿编码模型并同步 [architecture.md](../current/architecture.md)，同时将 Anthropic 切至 Pydantic AI 原生 `AnthropicModel` / `/v1/messages`（锁定 `anthropic==1.17.0`，规避 `anthropic>=2.0.0` `BetaMessageParam` 变为 `typing.Union` 导致 `pydantic-ai-slim==2.57.0` 报错的兼容性问题；删除原兼容层注入文件 `prompt_cache.py`）、放行 Gemini `xhigh` 并补齐 `529`/`prompt is too long` 错误与重试映射。
    - 修订后验证：Vitest `ProviderLogo.behavior.test.tsx`（3/3）+ `ModelSelector.behavior.test.tsx`（13/13）+ `SettingsPage.behavior.test.tsx`（10/10）及 `pnpm build:renderer` 通过；pytest `test_pydantic_ai_client.py` + `test_prompt_cache_and_providers.py`（`-k "not materialize_and_build_clients and not context_details"` 42/42）、`test_model_config_store.py`（`-k "presets or catalog or endpoint_and_limits or missing"` 4/4）、`test_codex_apply_patch.py`（`-k "openai_codex_profile"` 1/1）、`test_model_reasoning_replay.py`（纯内存编码/回放用例 23/23）通过；`ruff`、`deptry`、`pip-audit`（139 个包 0 漏洞）、`check-desktop-contracts.mjs` 及 `check-desktop-contracts.test.mjs` / `check-generated-contracts.test.mjs`（设 `EIDOS_PYTHON` 后 8/8）通过；注：当前 Windows 提权终端（`High Mandatory Level`，默认所有者 `BUILTIN\Administrators`）触发 `tmp_path` 落盘 NTFS 所有者 fail-closed 校验的用例，以及硬编码 `.venv/bin/python` 的契约生成脚本未计入本轮通过范围。

- [x] **B5-3 `[P2][核心]` 恢复轻量 Workspace 级 Checkpoint & Rewind（或清理残留死表）**
  - **代码范围**：
    - [schema.py](../../runtime/eidos_runtime/db/schema.py#L809-L833) — 关键词：`checkpoints`、`checkpoint_actions`
    - [checkpoints.py](../../runtime/eidos_runtime/persistence/checkpoints.py)、[application/checkpoints.py](../../runtime/eidos_runtime/application/checkpoints.py)、[snapshot_artifacts.py](../../runtime/eidos_runtime/git/snapshot_artifacts.py)、[engine.py](../../runtime/eidos_runtime/runtime/engine.py)、[supervisor.py](../../runtime/eidos_runtime/runtime/supervisor.py)
    - [methods.py](../../runtime/eidos_runtime/protocol/methods.py)、[dispatch.py](../../runtime/eidos_runtime/protocol/dispatch.py)、[server.py](../../runtime/eidos_runtime/protocol/server.py)、[ExecutionFeed.tsx](../../desktop/renderer/src/components/ExecutionFeed.tsx)、[AppShell.tsx](../../desktop/renderer/src/app/AppShell.tsx)
  - **修复建议**：复用已有 Git Snapshot 能力实现轻量 Turn 级 Checkpoint/Rewind（在每次修改前记录 Git tree OID，暴露回退 RPC 与 Feed 入口）；若确认不恢复文件级回滚，则在下一次 Schema 迁移移除相关空表与死代码。
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：已恢复轻量 Workspace 级 Checkpoint & Rewind（复用 `WorktreeSnapshotService`、`checkpoints`/`checkpoint_actions` 表、修复 Windows `_fsync_directory` 并接入 `checkpoint/list`、`checkpoint/rewind` 与 Feed 回退入口）；Ruff 检查与联合 pytest（含 `test_checkpoints.py` 与 `test_request_dispatch.py` 共 21/21 通过）、`build:preload`、`build:main`、`tsc -p tsconfig.renderer.json --noEmit`、`check-desktop-contracts.mjs`、`ipc-contracts.test.js`（3/3）、Vitest `review-findings.behavior.test.tsx`（2/2）+ `AppShell.behavior.test.tsx`（43/43）全部通过。

- [x] **B5-4 `[P2][体验]` 升级 `GitChangesPanel`（Split Diff / 语法高亮 / 单文件与 Hunk 暂存）与 `review-agent`**
  - **代码范围**：
    - [GitChangesPanel.tsx](../../desktop/renderer/src/components/GitChangesPanel.tsx)、[diff-syntax.ts](../../desktop/renderer/src/components/diff-syntax.ts)、[review-findings.ts](../../desktop/renderer/src/components/review-findings.ts)、[GitReview.css](../../desktop/renderer/src/components/GitReview.css)
    - [native.py](../../runtime/eidos_runtime/git/native.py)、[backend.py](../../runtime/eidos_runtime/git/backend.py)、[git_workflow.py](../../runtime/eidos_runtime/application/git_workflow.py)、[review-agent/SKILL.md](../../runtime/eidos_runtime/resources/skills/.system/review-agent/SKILL.md)
  - **修复建议**：支持 Unified / Side-by-side 双视图切换、Diff 语法高亮、单文件与单 Hunk 暂存/丢弃以及 AI 生成 Commit Message；重构内置 `review-agent` 与 `/review` 触发流，输出可一键采纳的结构化行级审阅评论。
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：已实现 Unified/Split 切换、Diff 语法高亮、单文件 Stage/Unstage/二次确认 Discard、单 Hunk cached patch 暂存/取消暂存、Commit Message 生成及 `[P0-P3]` 结构化审阅流；Ruff 检查与联合 pytest（含 `test_git_staging_and_hunks.py` 与 `test_request_dispatch.py` 共 21/21 通过）、`build:preload`、`build:main`、`tsc -p tsconfig.renderer.json --noEmit`、`check-desktop-contracts.mjs`、Vitest `GitChangesPanel.behavior.test.tsx`（24/24）+ `GitWorkflowControls.behavior.test.tsx`（23/23）+ `review-findings.behavior.test.tsx`（2/2）全部通过。

---

## Batch 6 — 架构技术债与文档-代码漂移清理（2 项）

- [x] **B6-1 `[P3][技术债]` 渐进拆分核心巨石文件、收敛裸 `dict`/线程残留、补齐死锁回归与最小真实 Eval**
  - **代码范围**：
    - Python：[commit_message.py](../../runtime/eidos_runtime/git/commit_message.py)、[git_workflow.py](../../runtime/eidos_runtime/application/git_workflow.py)、[supervisor.py](../../runtime/eidos_runtime/runtime/supervisor.py)、已清理空目录 `runtime/eidos_runtime/contracts/`
    - 死锁测试与评测：[test_async_kernel_deadlock.py](../../runtime/tests/test_async_kernel_deadlock.py)、[baseline.json](../../eval/cases/baseline.json)、[runner.py](../../eval/runner.py)、[test_eval_harness.py](../../runtime/tests/test_eval_harness.py)
  - **修复建议**：遵守 [AGENTS.md](../../AGENTS.md#L322-L328) 不做纯搬迁大重构，在推进上述 Batch 时按领域分拆触及到的巨石模块与裸 `dict`（并将 `runtime-client.ts` 手写校验收敛至生成契约）；补充 `start_managed_task` 死锁回归测试；基于 `evaluation-system-design.md` 落地最小真实模型多轮 Eval 基准。
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：已移除空包 `eidos_runtime.contracts`、抽离 `git/commit_message.py`、补齐 `start_managed_task` 并发提交/取消排空死锁回归并落地最小多轮 Eval 框架（支持失败判定与凭据脱敏）；Ruff 检查与联合 pytest（含 `test_async_kernel_deadlock.py` 与 `test_eval_harness.py` 共 21/21 通过）。

- [x] **B6-2 `[P3][文档漂移]` 同步修正 6 处已确认的文档与前端文案漂移**
  - **代码范围**：
    1. [docs/development/README.md](../development/README.md#L485)、[memory.md](../current/features/memory.md#L13)（同步 Schema v57）
    2. [plugins.md](../current/features/plugins.md#L74)（内置 GitHub 插件版本更新为 `1.0.2`）
    3. [architecture.md](../current/architecture.md#L621)、[capabilities.md](../current/capabilities.md#L372)（对齐最新 Schema v57）
    4. [capabilities.md](../current/capabilities.md#L19)、[architecture.md](../current/architecture.md#L78)、[goals.md](../current/features/goals.md#L50)（同步 Checkpoint/Rewind 与死锁测试状态）
    5. [execution-gates.md](../current/execution-gates.md#L138)、[capabilities.md](../current/capabilities.md#L159)、[goals.md](../current/features/goals.md#L41)（同步自动恢复与护栏约束规则）
    6. [RuntimeSettings.tsx](../../desktop/renderer/src/components/settings/RuntimeSettings.tsx)、[GitChangesPanel.tsx](../../desktop/renderer/src/components/GitChangesPanel.tsx)、[GitWorkflowControls.tsx](../../desktop/renderer/src/components/GitWorkflowControls.tsx)（前端中英混杂文案统一收口为中文）
  - **验证记录（2026-11-25，Windows 11 x64，未提交工作区）**：已完成文档与前端文案同步；`tsc -p tsconfig.renderer.json --noEmit` 与 Vitest `ShellEnvironmentSettings.behavior.test.tsx`（6/6）+ `GitChangesPanel.behavior.test.tsx`（24/24）+ `GitWorkflowControls.behavior.test.tsx`（23/23）全部通过。

---

## 评审后修复（2026-11-26，macOS arm64，未提交工作区）

评审发现的 P0、P1 与 CI 失败已在本分支修复：

- **B3-3（P0）**：Desktop 轮转日志从 Runtime 私有的 `<data>/logs` 移到 `<data>/desktop-logs`（目录 0700、文件 0600）。原实现以 0755 创建 `logs/`，全新安装时 Runtime 报 `state_security_invalid` 并进入 `health_only`。
- **B5-3（P0）**：删除会话前释放其 Checkpoint、快照行、隐藏 ref 与快照文件；删除事务同时解除残留快照的会话引用。原实现中跑过 Run 的 Git 会话无法删除（`FOREIGN KEY constraint failed`）。
- **B1-3**：Git HEAD 基线在每个模型步骤和暂停点刷新；核验不再激活 Repository 索引；规则、Context plan、索引变化允许恢复；其他阻断结果退回 `PAUSED`，不再卡在 `RESUME_REQUESTED`；移除裸 `dict` 传参。
- **B4-5**：审批快捷键只作用于持有焦点的审批卡片，并忽略输入框内的按键；替换输入框时卡片只在焦点丢失时自动聚焦。原实现一次 Cmd/Ctrl+Enter 会批准所有可见审批。
- **B1-2**：成本上限改为“Credits 上限”，并注明目前仅 Qoder 返回 Credits；定时任务的成功执行计为进展，只有连续未成功执行计入无进展上限；升级前的定时任务从下一次执行开始计算周期。
- **B2-2**：三处 Hook 信任审阅界面同时显示 `command` 与 `commandWindows`。
- **B5-2**：Anthropic 协议不再强制 `tool_choice`（Claude 5.x 返回 400），模型辅助压缩改由指令点名工具。
- **B1-1**：最高降级等级的硬保底与 `recovery_context` 一致，原样保留个性化、项目规则、所选 Skill、自定义 Agent 和完整当前目标；原实现会丢掉它们（`test_personalization` 的 level 8 用例失败）。
- **CI**：`eval` 测试导入路径、协议向量的客户端版本、审批拒绝调用参数、新建会话被“恢复上次会话”抢回、Hook/查找文件测试在 macOS 的夹具问题、runtime-paths 用例未固定平台、方法注册表测试未登记 `GuardrailsResponse` 这一读写共用的响应类型。
- **B6-1 说明更正**：`eval/runner.py` 使用 `ScriptedModel`，是确定性的多轮回归框架，不是真实模型 Eval。

---

## 暂不修复 / 延后或不适用项（不计入本期清零分母）

以下 6 项已按用户明确决策归档，暂不列入当前清零目标：

| 编号 | 原议题 | 关联代码范围 | 本期处理决策 |
|---|---|---|---|
| **D-1** | 文档 Skills 剥离上游许可证整改 | [runtime/eidos_runtime/resources/skills/.system/](../../runtime/eidos_runtime/resources/skills/.system/)（`docx`、`pdf`、`pptx`、`xlsx`） | **不适用 / 暂不处理**：Eidos 为个人学习研究产品，不涉及商用对外分发。 |
| **D-2** | 应用自动更新与 Tag Release 自动化 | [package.json](../../package.json)、[electron-builder.yml](../../electron-builder.yml)、[.github/workflows/](../../.github/workflows/) | **暂不修复**：后续版本再行考虑。 |
| **D-3** | Windows 原生 OS 进程沙箱 | [permissions.py](../../runtime/eidos_runtime/sandbox/permissions.py)、设计参考 [Windows 沙盒适配调研与设计](windows-sandbox-design.md) | **延后单独立项**：Windows 沙箱需单独重新设计与开发；本期仅在 **B2-4** 修正硬编码 `"macOS"` 提示文案。 |
| **D-4** | Qoder 驱动跨平台适配、缓存复用与锁文件审计 | [qoder_model_driver.py](../../runtime/eidos_runtime/infrastructure/qoder_model_driver.py)、[qoder_context.py](../../runtime/eidos_runtime/model/qoder_context.py)、[audit-python.sh](../../scripts/audit-python.sh)、[uv.lock](../../resources/runtime-dependencies/qoder/uv.lock) | **暂不修复**。 |
| **D-5** | Skills 项目级目录发现与安装源扩展 | [skills.py](../../runtime/eidos_runtime/extensions/skills.py) | **本期暂不修改**。 |
| **D-6** | Repo Intelligence 模型工具暴露、多根目录索引与中文分词 | [runtime/eidos_runtime/repo_intelligence/](../../runtime/eidos_runtime/repo_intelligence/) | **暂不考虑**。 |
