# Runtime Git Worktree Kernel

本文描述当前 Runtime 的 Project、Session、Worktree、Run、Git 和 Sandbox 关系。本文以生产代码和测试为事实来源。

## Project、Session 和 Run

当前对象关系如下：

```text
Project
 ├── Session / Thread
 │      └── Managed Worktree
 │              └── Runs
```

`Session.workspace_root` 是 repository root compatibility field。

Managed Session 的 execution root 是 `worktree_root`。Legacy Session 没有 `worktree_id`，所以继续使用 `Session.workspace_root`。

Run 的 execution identity 是冻结的 `RunResolutionSnapshot.workspace_identity`。Runtime 在 Run admission 和 resume 时重新验证 managed Worktree。Runtime 不会把 managed Run fallback 到 repository root。

## Git authority

- `WorktreeManager` 是 Git Worktree lifecycle authority。它负责 Project discovery、prepare、create、validate、recover、cleanup、delete、status 和 diff。
- `GitProcess` 是 bounded fixed-command Git process seam。它负责固定 argv、超时、进程组、环境和有界输出。
- Desktop 只使用 Session-oriented Git read API。Desktop 不创建或删除 Worktree。
- Model 只能提出普通 ToolCall。Model 不能管理 Worktree lifecycle。

Runtime 只从已验证的 Git repository 创建 Project。Project 保存 canonical repository root 和 canonical `git_common_dir`。Worktree 保存 `worktree_root`、per-worktree `git_dir`、base ref、immutable base commit、Runtime branch、ownership 和 state。

## Session create

`session/create` 把 `workspaceRoot` 作为 repository seed path。Runtime 先解析 Project 和 base commit，再在 Git side effect 前确定以下 identity：

```text
project_id
worktree_id
worktree_root
branch
base_commit
```

Runtime 将 identity 写入 v17 `worktree_lifecycle_operations`，状态为 `prepared`。之后 Runtime 才运行 `git worktree add`。

成功路径如下：

```text
prepare
  → durable session/create intent
  → git worktree add
  → exact Git/filesystem validation
  → Worktree persistence
  → Session persistence
  → completed lifecycle intent
  → operation result
```

重试会继续使用 intent 中的相同 `worktree_id`、`worktree_root`、`branch` 和 `base_commit`。如果真实 Worktree 与 intent 完全一致，Runtime 可以 adopt 并继续写入缺失的 SQLite record。冲突会进入 `cleanup_required`。Runtime 不会 force adopt。

## Managed Checkpoint Fork

Fork v1 使用 `checkpoint.gitHead` 作为新的 Worktree `base_commit`。Runtime 为同一个 durable operation 准备固定的 Worktree、Session、Run 和 checkpoint action identity。

成功路径如下：

```text
prepared
  → worktree_created
  → session_created
  → run_created
  → checkpoint_action_created
  → completed
```

相同 `checkpointId` 和 `operationId` 的 retry 会读取已有的 Worktree、Session、Run 和 action。Runtime 不会创建第二套资源。Fork v1 不恢复 checkpoint 时刻完整的未提交 working-tree patch，也不复制全部 immutable snapshot 内容。

## Session delete

Session delete 在 Git side effect 前写入 `session/delete` durable intent。Runtime 只删除 `managed` Worktree。Runtime 会先验证 Worktree，再检查 dirty state。Dirty、冲突或无法证明 identity 的 Worktree 会被拒绝。

删除路径如下：

```text
durable session/delete intent
  → git worktree remove
  → Worktree state = deleted
  → Session delete
  → lifecycle intent = completed
```

Runtime 可以在 Worktree removal 与 Session delete 之间重启后继续执行。没有对应 durable delete intent 时，Runtime 不会因为 Worktree 已经是 `deleted` 或 `missing` 就自动删除 Session。

Git branch 默认保留。Runtime 不执行 branch `-D`，也不执行未知路径的递归删除。

## Schema and lifecycle states

当前 SQLite schema version 是 v17。v16 → v17 的 migration 只创建 durable lifecycle table。Migration 不执行 Git 或 filesystem adoption。旧 Session 和旧 Worktree 保持不变。

`worktree_lifecycle_operations` 只接受三个 scope：

```text
session/create
session/delete
checkpoint/fork
```

它使用有限状态：`prepared`、`worktree_created`、`session_created`、`run_created`、`checkpoint_action_created`、`worktree_deleted`、`completed` 和 `cleanup_required`。它不是通用 workflow engine，也不保存 arbitrary payload executor。

## Startup recovery

Runtime 的启动顺序是：

```text
SQLite initialize
  → recover runtime facts
  → construct WorktreeManager
  → WorktreeManager.recover()
  → lifecycle reconciliation
  → construct and expose applications
  → Runtime ready
```

Recovery 对每个 Project 和每个 lifecycle operation 使用现有 Git timeout 和 bounded output。坏 Project 不会永久阻塞整个 Runtime。

当 durable plan、`git worktree list`、filesystem、branch 和 HEAD 完全一致时，Runtime 可以继续或 adopt。无法证明一致时，Runtime 保留数据、目录和 branch，把 lifecycle 标记为 `cleanup_required`，并让相关 managed Session 不可用。Runtime 不执行 `git worktree remove --force`、`rm -rf unknown path` 或 `branch -D`。

Recovery 日志包含 `project_id`、`worktree_id`、`session_id`、`operation_id`、`scope`、`state`、`recovery_action`、`result`、`error_code` 和 `duration`。日志不包含 API key、完整 diff 或用户源代码。

## Git observation hardening

Runtime 的 status、HEAD、diff、name-only 和 untracked observation 使用 fixed argv、`shell=False`、明确 cwd、`GIT_OPTIONAL_LOCKS=0`、`GIT_TERMINAL_PROMPT=0`、禁用 pager 和有界 timeout/output。

Informational diff 使用 `--no-ext-diff` 和 `--no-textconv`。Runtime 读取 local `filter.*.clean` 和 `filter.*.process` 配置，并以 fixed `-c` 参数禁用 executable filter，同时把 `required` 设为 false。Runtime-owned lifecycle command 使用 `core.hooksPath=/dev/null`。Runtime 不执行 configured hook、textconv 或 executable filter。

Git observation 失败、超时、截断或解析不完整时，Runtime 不把部分输出当作事实，也不静默改变 Worktree lifecycle state。

## Linked Worktree Sandbox

Managed linked Worktree 的 `.git` 是 pointer file。真正的 per-worktree `git_dir` 和 Project `git_common_dir` 来自 `Session → Worktree → WorktreeManager.validate()` 的 verified facts。Runtime 将它们传入 Seatbelt profile。

Seatbelt 允许：

- managed `worktree_root` 的普通文件读写；
- verified `git_dir` 和 `git_common_dir` 的只读访问；
- Git read command 所需的 metadata traversal。

Seatbelt 拒绝：

- `git_dir` 和 `git_common_dir` 的写入；
- 原始 repository working tree 的读写；
- 通过命令字符串 blacklist 规避的 Git mutation。

真实 macOS native test 使用 real Git repository、real linked Worktree 和 `/usr/bin/sandbox-exec` 验证 `git status`、`git diff`、`git log`、`git rev-parse`、`git branch --show-current`、普通文件写入以及 Git metadata mutation denial。

## Desktop boundary and limits

Desktop 通过 Session-oriented `session/gitStatus` 和 `session/gitDiff` 读取 Git facts。Sidebar 展示 managed branch。Dirty indicator 使用已有 Session status cache，不启动所有 Thread 的持续轮询。

当前仍未实现：Parallel Agent、Git staging/commit/push UI、branch merge/rebase/force delete、完整未提交 patch 的 checkpoint rewind/fork，以及 cross-worktree Repository Intelligence sharing。
