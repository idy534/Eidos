# Eidos 当前限制

本文只回答“当前 main 还不能做什么，或者哪些能力还没有形成完整闭环”。本文不记录已经解决的问题，也不记录历史 Phase。

## 平台与分发

- Eidos 当前只支持 macOS arm64 Desktop、Runtime Bundle、App 和 DMG。
- Linux、Windows、macOS x64、Universal binary 和其他平台的 bundled Runtime 与 Ripgrep artifact 尚未实现。
- Auto Update、GitHub Release 发布闭环和其他平台 artifact 尚未实现。
- Release packaging 的签名、notarization、stapling 和 Gatekeeper 命令已经接入脚本，但这些步骤需要 Apple credentials。没有 credentials 时不能证明已生成可发布的 Release artifact。

## Model Provider

- ModelConfigStore 只接受内置 DeepSeek、MiniMax 和 Kimi Catalog 中的五个 Model ID。
- 当前不支持 arbitrary custom provider、arbitrary base URL、arbitrary model ID、Responses API、连接测试或主动 capability probe。
- 当前 wire API 固定为 OpenAI-compatible Chat Completions/SSE。
- Context Usage 的 estimated 值是有界 fallback，不是 tokenizer 精确值。它不能单独证明 Provider 已拒绝请求。

## Run 并发与资源模型

- Runtime 同一时间只运行一个活动 Run。当前没有 parallel Run 或 parallel Agent。
- 当前每个活动 Run 使用一个 Worker Thread。模型异步 I/O、MCP、Managed Task 和安全只读批次由唯一 RuntimeAsyncKernel 管理。当前没有把整个 RuntimeEngine/RunSupervisor 改成原生 async 的实现。
- Workspace 写入、Shell、Eidos-state、MCP 和 external Tool 不支持并发执行。
- 当前没有用户可配置的统一 Run 成本、模型步数或有效时长上限。现有 step、segment 和 effective time 字段主要用于 telemetry 和 operational lifecycle。

## Workspace 与工具

- 内置文件工具只处理当前 Workspace 内受支持的普通 UTF-8 文件。当前没有通用二进制编辑、内嵌 Terminal、浏览器自动化或 Artifact 发布工具。
- Workspace discovery 只读取 Workspace root 的 `.gitignore` 和 `.eidosignore`。当前不支持 nested `.gitignore`。
- Ignore 规则只影响普通 `list_files`/`search_text` 发现结果。Ignore 规则不是权限，也不会缩小 Shell security scan 或副作用 evidence 范围。
- `search_text` 没有 LSP、AST 查询和基于 Repo Intelligence 的默认搜索路径。它仍然使用受管 Ripgrep，结果、preview、单文件和查询大小都有界。
- Shell post-execution observation 在扫描超时、敏感条目或不完整 Workspace manifest 时可能是 `unknown`。这类 observation 不能替代 Runtime 明确报告的执行 uncertainty。完整的 Workspace 状态与安全事实仍需要后置核验。

## Repository Intelligence

- Inventory、Repository generations、Tree-sitter Index、symbols/imports/references/chunks、Repository Map、SQLite FTS5、Retrieval Snapshot、ContextPlan 和 ContextSnapshot 已经有 typed infrastructure、persistence 和 focused tests。
- 这些基础设施还没有全部进入 RuntimeEngine 的默认 online Run path。默认 Model Attempt 前不会强制执行完整的 Inventory → Index → Map → Retrieval → ContextPlan → ContextSnapshot 组装。
- 因此，当前不能把 Repository Intelligence 描述成“没有实现”，也不能把它描述成“每次 Run 都自动使用”。正确状态是 implemented infrastructure、partially wired、not yet product-complete。
- Watcher 只提供缓存失效信号。Watcher 事件不是 Workspace 安全事实，也不会静默修改当前 Run 的 immutable snapshot。

## Recovery 与 Checkpoint

- Runtime 可以持久化 Long Task 控制、pause/resume/cancel、restart verification 结果和 reconciliation 状态，但 Restart Verification 尚未覆盖完整的 Git diff、credential、MCP、Seatbelt、pending Approval、unfinished ToolCall、Durable Intent 和 Checkpoint 兼容性集合。
- Checkpoint create/list 和 rewind/fork lineage 已持久化并暴露 typed RPC。Rewind 尚未重建完整逻辑 Context。Fork 尚未验证或复制全部 immutable snapshots，也没有创建和校验独立 Git Worktree。
- 当前不支持 Git Worktree 产品闭环。Checkpoint 不能被当作完整的 branch/worktree fork。
- Runtime 不会恢复内存中的 Model request、Process 或 ToolCall。可能有副作用的未确认执行必须先进入 reconciliation，Runtime 不会自动重放。

## Compaction 与 Context

- 默认 ContextCompactor 是 deterministic bounded extraction。当前没有 model-assisted compaction。
- ContextCompactionVerifier、VerifiedCompaction persistence、ContextPlan 和 ContextSnapshot 已经存在，但兼容的默认 ContextCompactor 尚未自动切换到完整 verified compaction write path。
- Context Usage Desktop 只展示当前选中 Model 对应的最近 Run。新 Run 在拿到首个 Provider Usage 或可用 projection 前会显示无数据。
- Provider 明确 `context_exceeded` 后，如果没有新的可压缩历史或 Context projection 没有进展，Runtime 会以 `context_still_over_budget` 停止。

## Extension 与 MCP

- Plugin 当前只支持本地受管 Plugin v1。当前没有远程 Plugin marketplace、OAuth 安装或任意运行时动态 import 用户 Plugin 的能力。
- MCP 当前只支持 stdio Tools。当前没有 Streamable HTTP、远程 MCP transport、OAuth、Resources、Prompts、Sampling 或 Tasks。
- MCP ready connection 是长生命周期 Service，但 startup、Tool call、Tool list、cancel 和 shutdown 都有各自的有界等待。已经开始的 Tool List Changed bookkeeping callback 可能在关闭时需要等待完成。

## Application 边界

- `application/` 已建立 Session、Run、Response Action、Model、Extension、Repository、Context、Checkpoint 和 TaskLifecycle 的部分边界。
- 部分 RuntimeServer handler 仍通过 SessionStore 兼容入口执行。所有顶层 use case 尚未完成统一 Application migration。
- `Run.runtimeState` 是可选跨语言 DTO 字段，不是恢复权威。当前恢复权威仍然是 SQLite 中的 Run status、Approval、Step、ToolCall、Durable Intent 和 reconciliation 事实。

## Implementation Anchors

- `runtime/eidos_runtime/model/config.py`
- `runtime/eidos_runtime/model_gateway/`
- `runtime/eidos_runtime/runtime/supervisor.py`
- `runtime/eidos_runtime/runtime/engine.py`
- `runtime/eidos_runtime/context/compactor.py`
- `runtime/eidos_runtime/context/verified_compaction.py`
- `runtime/eidos_runtime/application/repository.py`
- `runtime/eidos_runtime/application/context.py`
- `runtime/eidos_runtime/repo_intelligence/`
- `runtime/eidos_runtime/persistence/checkpoints.py`
- `runtime/eidos_runtime/persistence/repository_intelligence.py`
- `runtime/eidos_runtime/extensions/`
- `runtime/eidos_runtime/sandbox/`
- `runtime/eidos_runtime/db/schema.py`
- `runtime/eidos_runtime/db/database.py`
