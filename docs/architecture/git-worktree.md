# Runtime Git Worktree Kernel

本文描述当前 Runtime 的 Project、Session、Worktree、Run、Git 和 Sandbox 关系。本文以生产代码和测试为事实来源。

## Project、Thread 和 Run

Project 是用户选择的 filesystem workspace。Project 的基础事实是 `workspace_root`。Git 是 Project 的 optional capability，不是 Project 的类型前提。

```text
Project
 ├── workspace_root
 └── optional Git capability
          └── optional Managed Worktree
                    └── Thread / Session → Run
```

Project 有两条当前执行路径：

```text
Non-Git directory
  → Project
  → Direct Workspace Session (worktree_id = NULL)
  → Run in Project.workspace_root

Git repository
  → Project with Git capability
  → Managed Worktree Session
  → Run in Worktree.worktree_root
```

Direct Session 是正式的一等执行模式。它可以使用文件、Shell、Skill、MCP、Context、Long Task、Sandbox 和 Checkpoint。它不提供 Git status、Git diff、Managed Worktree 或 Git-based Fork。

Direct Run 和 Managed Run 都冻结 `RunResolutionSnapshot.workspace_identity`。Runtime 在 Run admission、resume 和 restart 时继续验证 canonical path、device、inode 和 owner。没有 Git 不会跳过 Workspace boundary 或 Sandbox。

## Project resolution

`session/create` 先通过 Project resolution boundary 校验用户目录、canonicalize path 和检测可选 Git capability。不存在、不是目录、违反 symlink workspace policy、权限拒绝或 filesystem identity failure 才是 resolution error。`not_a_git_repository` 表示没有 Git capability，不是 Session create failure。

Project 持久化字段如下：

```text
id
workspace_root NOT NULL UNIQUE
git_repository_root NULL
git_common_dir NULL
created_at
updated_at
```

Git metadata 字段必须同时有值或同时为空。Direct Project 以 canonical `workspace_root` 作为 identity。已有 Direct Project 后目录获得 Git capability 时，Project 可以保留原有 id 并补充 Git metadata。

## Git authority and backend

- `WorktreeManager` 是 Eidos 的 Project、Worktree、Session binding、Run identity、durable operation、recovery、compensation 和 Sandbox boundary authority。
- `GitBackend` 是 Git mechanics seam。它不拥有 Session、Run、Checkpoint 或 SQLite lifecycle。
- `DulwichGitBackend` 优先处理 repository discovery、repository/common dir、HEAD/ref、status、diff、worktree list/remove/prune 等 Git mechanics。
- `NativeGitFallback` 只处理当前需要 native hardened path 的 Worktree create。Dulwich 的相关工作树写入路径可能触发配置的 clean/process filter，因此该 fallback 保留现有 hook、fsmonitor、textconv、external diff、clean/process filter、credential、timeout、bounded output 和进程组 hardening。

Dulwich 类型不会传播到 Application、Domain、Protocol、SQLite 或 Desktop。Backend 输出 Eidos-owned 的 `GitRepositoryDiscovery`、`GitStatusSnapshot`、`GitDiffSnapshot` 和 `GitWorktreeEntry`。

## Session create

Runtime 先解析 Project。Git Project 会在 Git side effect 前确定 `project_id`、`worktree_id`、`worktree_root`、`branch` 和 `base_commit`，然后写入 durable lifecycle intent：

```text
prepare
  → durable session/create intent
  → GitBackend worktree add
  → exact Git/filesystem validation
  → Worktree persistence
  → Session persistence
  → completed lifecycle intent
  → operation result
```

Direct Project 不进入这条 Git lifecycle。Runtime 直接创建 `worktree_id = NULL` 的 Session，并把 Run execution root 设为 Project workspace root。

Managed retry 使用 intent 中相同的 `worktree_id`、`worktree_root`、`branch` 和 `base_commit`。如果真实 Worktree 与 intent 完全一致，Runtime 可以 adopt 缺失的 SQLite record。冲突会进入 `cleanup_required`。Runtime 不 force adopt。

## Managed Checkpoint Fork

Managed Fork v1 使用 `checkpoint.gitHead` 作为新 Worktree 的 `base_commit`。Runtime 为同一个 durable operation 准备固定的 Worktree、Session、Run 和 checkpoint action identity：

```text
prepared
  → worktree_created
  → session_created
  → run_created
  → checkpoint_action_created
  → completed
```

相同 `checkpointId` 和 `operationId` 的 retry 会读取已有的 Worktree、Session、Run 和 action。Runtime 不会创建第二套资源。Fork v1 不恢复 checkpoint 时刻完整的未提交 working-tree patch，也不复制全部 immutable snapshot 内容。

Direct Fork 使用同一个 Project 和同一个 `workspace_root` 创建新的 Direct Session、Run 和 checkpoint action。两个 Direct Thread 共享真实目录。Direct Fork 是 conversation/runtime fork，不是 filesystem snapshot；当前不提供 copy-on-write、directory snapshot 或 filesystem rewind。

## Session delete

Managed Session delete 在 Git side effect 前写入 `session/delete` durable intent。Runtime 验证 Worktree，再检查 dirty state、冲突和 identity：

```text
durable session/delete intent
  → GitBackend worktree remove
  → Worktree state = deleted
  → Session delete
  → lifecycle intent = completed
```

Runtime 可以在 Worktree removal 与 Session delete 之间重启后继续执行。Runtime 不执行 force remove、未知路径递归删除或 branch `-D`。

Direct Session delete 不创建 Git lifecycle intent。它只删除 Eidos Session 数据。它不会删除 Project workspace root 或用户文件。

## Schema and lifecycle states

当前 SQLite schema version 是 v18。v17 → v18 将旧 Git Project 的 `repository_root` 映射为 `workspace_root` 和 `git_repository_root`，保留原 Project id、Worktree FK、Session binding 和 Run。对于 `worktree_id IS NULL` 的旧 Session，migration 会按 canonical workspace root 创建确定性 Direct Project，并保留 Session 和 Run。

`worktree_lifecycle_operations` 从 v17 保留。它只接受 `session/create`、`session/delete` 和 `checkpoint/fork` scope。它使用有限状态：`prepared`、`worktree_created`、`session_created`、`run_created`、`checkpoint_action_created`、`worktree_deleted`、`completed` 和 `cleanup_required`。它不是通用 workflow engine，也不保存 arbitrary payload executor。

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

Recovery 只处理已有的 managed Worktree lifecycle operation。Direct Session 不需要 Git lifecycle recovery，但它的 Run restart verification 仍验证 Workspace identity、Sandbox boundary、rules、Context、permission 和 reconciliation facts。

当 durable plan、GitBackend worktree observation、filesystem、branch 和 HEAD 完全一致时，Runtime 可以继续或 adopt。无法证明一致时，Runtime 保留数据、目录和 branch，把 lifecycle 标记为 `cleanup_required`，并让相关 managed Session 不可用。

## Git observation hardening

GitBackend contract tests 覆盖 tracked clean、modified、staged、unstaged、untracked、deleted、conflict、Unicode filename、nested path、linked Worktree、HEAD、branch、HEAD diff 和 baseline diff。

Runtime 不允许 configured hook、fsmonitor executable、textconv、external diff、clean filter、process filter、dotted filter driver 或 worktree-specific filter 执行。Dulwich 的低层 read-only observation 不经过这些 executable paths。Worktree create 使用 NativeGitFallback，因为该写入路径需要现有 native hardening。

Observation timeout、truncation 或 incomplete parsing 不会更新 Worktree lifecycle state。`deleted` 仍然是 terminal state。

## Sandbox

Managed Worktree 的 writable root 是 `Worktree.worktree_root`。Seatbelt 对 verified `git_dir` 和 Project 的 verified `git_common_dir` 只开放 read-only metadata access。原始 repository working tree 不属于该 Thread 的 execution workspace。

Direct Workspace 的 writable root 是 `Project.workspace_root`。Direct profile 不构造假的 `.git`、`git_dir` 或 `git_common_dir`。Direct Run 仍使用相同的 Workspace identity、path boundary、special-file checks、approval 和 sandbox validation。

## Desktop boundary and limits

Session projection 提供 `projectId`、`workspaceRoot` 和 `gitAvailable`。Sidebar 按 Project 分组。Non-Git Project 不显示 branch、Git Changes，也不会请求 `session/gitStatus` 或 `session/gitDiff`。Git Project 保留当前 branch、dirty status、HEAD diff 和 baseline diff UI。

当前仍未实现：Parallel Agent、Git staging/commit/push UI、branch merge/rebase/force delete、完整未提交 patch 的 checkpoint rewind/fork，以及 cross-worktree Repository Intelligence sharing。
