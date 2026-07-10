# 桌面端安全与生命周期

版本：v0.4

## 1. Electron Main

职责：

- 生成 runtime token，启动和停止 Python sidecar。
- 读取唯一 ready JSON 行并保存随机端口。
- 代理 Renderer 的 HTTP API 和 SSE。
- 调用 macOS 文件夹选择器。
- 响应用户点击，在系统 Terminal 中打开 Workspace。
- 处理窗口关闭、应用退出和 sidecar 异常。

Main 不实现 Agent Loop，不执行 Agent Shell，也不向 Renderer 暴露通用文件系统或进程 API。

## 2. Preload API

禁止：

```ts
window.eidos.invoke(channel: string, payload: unknown)
```

采用类型化 API：

```ts
window.eidos.sessions.create(input)
window.eidos.runs.create(sessionId, input)
window.eidos.runs.cancel(runId)
window.eidos.runs.addUserInput(runId, input)
window.eidos.approvals.approve(approvalId, input)
window.eidos.approvals.reject(approvalId, input)
window.eidos.events.subscribe(runId, handler)
window.eidos.workspaces.chooseFolder()
window.eidos.workspaces.openInSystemTerminal(workspaceId)
window.eidos.artifacts.open(artifactId)
```

每个方法固定 IPC channel、请求 schema 和响应 schema。Main 再次验证所有 Renderer 参数，不能信任 TypeScript 类型。

## 3. Renderer 安全

```text
contextIsolation: true
nodeIntegration: false
sandbox: true
webSecurity: true
```

- CSP 默认拒绝脚本、远程 frame 和任意导航。
- Markdown 禁止 raw HTML 或使用严格 sanitizer。
- 外部链接只允许用户点击后通过系统浏览器打开，并显示目标域名。
- 文件预览不允许执行脚本、宏或任意本地 URL。
- Sidecar 错误响应经 Main 字段白名单后返回 Renderer。

## 4. Sidecar 启动与认证

Main：

1. 生成高熵 token。
2. 以 `EIDOS_RUNTIME_TOKEN` 注入 sidecar。
3. 等待带 schema 的 ready 行，其他 stdout 作为日志处理。
4. 验证 port 为本地有效端口。
5. 所有后续请求添加 Bearer token。

Sidecar：

- 只 bind `127.0.0.1`，不 bind `0.0.0.0` 或 `::`。
- 除 `/internal/health` 外都验证固定时间 token compare。
- CORS 不作为认证手段；Renderer 不直连。
- 日志 formatter 必须删除 Authorization header。

## 5. SSE 代理

```text
sidecar committed Events
  -> Main authenticated SSE connection
  -> IPC event stream
  -> Renderer per-run reducer
```

- Main 为每个活跃 Run 维护至多一个 sidecar SSE 连接。
- Renderer 重载后按最后 event id 恢复。
- IPC listener 必须在组件卸载时释放。
- Main 可批量转发高频 model/tool chunks，避免压垮 Renderer。

## 6. Workspace 选择

- 只接受用户通过系统文件夹选择器返回的目录。
- 保存 canonical path 和设备/文件标识元数据，用于检测目录移动或替换。
- 每次 Session/Run 启动前确认 root 存在且仍是目录。
- Root 不可用时 Workspace 标记 unavailable，不自动选择相似路径。
- active root 传给 sidecar 后仍由 Workspace Guard 和 Seatbelt 二次校验。

## 7. 打开系统 Terminal

- MVP 不依赖 `node-pty`，不创建内嵌 Terminal。
- 只能由受信任用户手势调用 `openInSystemTerminal`。
- Main 根据 workspace_id 查询已验证路径，不接受 Renderer 直接提交路径。
- 使用参数数组调用系统打开能力，不构造 Shell 字符串。
- Agent、模型、sidecar API 和 ToolCall 均不能触发该能力。

## 8. 窗口关闭

关闭行为：

- 无 running Run：停止 sidecar 并退出。
- 有 running Run：显示“等待完成”或“取消并退出”。
- 选择等待：窗口保持可见，Run 完成/暂停后再次允许退出。
- 选择取消：按协作式取消规则等待完成，再停止 sidecar。
- queued/waiting Run 留在数据库，下次启动恢复。

不提供“窗口关闭但后台继续执行”。

## 9. Sidecar 异常退出

Main：

- 停止向 Renderer 声称 Run 仍在执行。
- 显示 Runtime disconnected，并禁用新 Run/审批操作。
- 可由用户重新启动 sidecar。

Sidecar 重启后执行崩溃恢复：running -> waiting_user_input/runtime_interrupted；finalizing 使用结构化降级摘要进入 stopped；两者都不自动重放工具。

## 10. UI 状态要求

Renderer 必须明确区分：

```text
queued
running
waiting_approval
waiting_user_input
finalizing
succeeded
failed
stopped
canceled
runtime_disconnected
```

审批卡必须展示：

- 工具名和完整目标。
- 文件 diff 或完整 Shell command。
- Workspace write、外网 host、localhost 等权限。
- 请求创建时间和当前文件版本状态。
- Approve/Reject，不提供 Edit then Approve。

Q41 未确认前，不实现 raw reasoning 专用 UI。
