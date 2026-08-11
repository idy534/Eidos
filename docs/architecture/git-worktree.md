# Runtime Git Worktree Kernel

本文描述当前 Runtime 的 Project、Session、Worktree、Run、Git 和 Sandbox 关系。本文以生产代码和测试为事实来源。

## Project、Thread 和 Run

Project 是用户选择的 filesystem workspace。Project 的基础事实是 `workspace_root`。Git 是 Project 的 optional capability，不是 Project 的类型前提。

```text
Project
 ├── workspace_root
 └── optional Git capability
          └── Thread / Session
                    ├── Local Execution → Project.workspace_root
                    └── Worktree Execution → Managed Worktree → Run
```

Project 在创建 Thread / Session 时选择 execution mode。Git capability 不再隐式决定 execution mode。

```text
Project + Local
  → Local Execution Session (execution_mode = local)
  → Run in Project.workspace_root

Git Project + Worktree
  → Worktree Execution Session (execution_mode = worktree)
  → Managed Worktree with detached HEAD
  → Run in Worktree.worktree_root
```

Non-Git Project 只能创建 Local Execution Session。Git Project 可以创建 Local 或 Worktree Execution Session。Local Session 可以使用文件、Shell、Skill、MCP、Context、Long Task、Sandbox 和 Checkpoint，但没有 Worktree binding。Worktree Session 提供 Git status、Git diff、Managed Worktree 和 Git-based Fork。

Local Run 和 Worktree Run 都冻结 `RunResolutionSnapshot.workspace_identity`。Runtime 在 Run admission、resume 和 restart 时继续验证 canonical path、device、inode 和 owner。没有 Git 不会跳过 Workspace boundary 或 Sandbox。

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

Git metadata 字段必须同时有值或同时为空。Local Project 以 canonical `workspace_root` 作为 identity。已有 Local Project 后目录获得 Git capability 时，Project 可以保留原有 id 并补充 Git metadata。

## Git authority and backend

- `WorktreeManager` 是 Eidos 的 Project、Worktree、Session binding、Run identity、durable operation、recovery、compensation 和 Sandbox boundary authority。
- `GitBackend` 是 Git mechanics seam。它不拥有 Session、Run、Checkpoint 或 SQLite lifecycle。
- `DulwichGitBackend` 直接返回 Eidos-owned 的 `GitRepositoryDiscovery`、`GitRepositoryContext`、`GitStatusObservation`、`GitDiffObservation` 和 `GitWorktreeEntry`。它负责 repository discovery、HEAD/ref、local branch、status、diff、worktree list/remove/prune 和 legacy compare-and-delete branch。
- `NativeWorktreeCreator`、`NativeWorktreeChangeTransfer` 和 `NativeBranchAttacher` 是受控的 native Git 写入 seam。它们分别负责 Worktree checkout、Git patch/index 语义的 dirty transfer 和 detached Worktree 的新 branch attach。`NativeWorktreeCleaner` 只用于创建失败 compensation。`HardenedGitRunner` 负责 timeout、bounded output、进程组清理、禁用 hook/fsmonitor、credential/prompt 和 pager。

Dulwich 类型不会传播到 Application、Domain、Protocol、SQLite 或 Desktop。Backend 只输出 Eidos-owned typed Git models。

## Session create

`session/create` 接收 `workspaceRoot`、`executionMode`、可选的 `baseRef` 和显式的 `includeLocalChanges`。`executionMode` 的协议默认值是 `local`。Desktop 在 Git Project 中显式发送 `worktree`，并通过 `project/gitContext` 读取当前 branch、HEAD、local branches 和 dirty file count。Non-Git Project 请求 `worktree` 会返回 `WORKTREE_REQUIRES_GIT`。

Runtime 先解析 Project。Local Session 不创建 Worktree。Worktree Session 会在 Git side effect 前解析 `baseRef` 为 immutable `base_commit`，确定 `project_id`、`worktree_id`、`worktree_root` 和 `branch = NULL`，然后写入 durable lifecycle intent：

```text
prepare
  → durable session/create intent
  → GitBackend resolve baseRef
  → NativeWorktreeCreator: git worktree add --detach
  → materialize source .worktreeinclude
  → optionally apply source Git working-tree patch and index state
  → exact Git/filesystem validation
  → Worktree persistence
  → Session persistence
  → completed lifecycle intent
  → operation result
```

Local Session 不进入这条 Git lifecycle。Runtime 创建 `execution_mode = local`、`worktree_id = NULL` 的 Session，并把 Run execution root 设为 Project workspace root。Worktree Session 保存创建时的 `base_ref` 和解析后的 `base_commit`。`baseRef` 缺省时，Runtime 使用当前 branch；如果 repository 处于 detached HEAD，Runtime 使用 `HEAD`。

Managed retry 使用 intent 中相同的 `worktree_id`、`worktree_root`、`branch = NULL` 和 `base_commit`。如果真实 Worktree 与 intent 完全一致，Runtime 可以 adopt 缺失的 SQLite record。冲突会进入 `cleanup_required`。Runtime 不 force adopt。

默认创建的 Managed Worktree 是 detached HEAD。验证规则要求 persisted `branch = NULL` 时 observed branch 也为 NULL。外部 attach branch 会被视为 identity changed / recovery required。已有 branch 非 NULL 的 legacy Worktree 仍要求 observed branch 与 persisted branch 相同。

当 `includeLocalChanges = true` 时，Runtime 要求 source `HEAD` 等于选择的 `baseCommit`。Runtime 在 Git side effect 前捕获 source repository identity、HEAD、branch、status 和 Git patch，并在 Worktree checkout 后重新核验这些事实。Source Workspace 不执行 stash、reset、checkout、add 或其他写入。Runtime 先读取 source root 的 `.worktreeinclude`，再用只读的 Git native patch、staged diff 和 `git diff --no-index` patch transfer tracked modified、deleted、staged、unstaged、binary 和 untracked regular files。最终 local state 覆盖 `.worktreeinclude` 的同路径内容。Base mismatch、source changed、patch conflict 和 include 安全错误都会失败并触发已有 compensation；无法证明 Worktree 已清理时，lifecycle 会进入 `cleanup_required`。

`.worktreeinclude` 只位于 source repository root。Runtime 不读取 managed Worktree 内的同名文件作为 authority。Pathspec 使用项目已有的 `pathspec` Git ignore 风格语义。Runtime 拒绝绝对路径、`..`、`.git` 和指向 Project workspace 外部的 symlink。缺失匹配不报错。复制只允许普通文件和经过 target 验证的内部 symlink。

`Worktree` 创建后仍然是 detached execution resource。`session/createBranch` 只允许 active、valid、`branch = NULL` 的 Worktree。Runtime 先写 `worktree/attach-branch` durable intent，再在同一个 Worktree cwd 执行受控 `git switch -c`，核验 HEAD 没有变化，最后把 `branch` 和 `branch_ownership = user` 写入 SQLite。已有 branch 或在其他 Worktree checkout 的 branch 都会拒绝。Branch attach 的 `prepared`、`branch_attached`、`completed` 和 `cleanup_required` 状态支持 startup recovery。用户 branch 随 Worktree 删除保留；旧 attached branch 仍按 `legacy_managed` 兼容语义处理。

## Managed Checkpoint Fork

Managed Fork v1 使用 `checkpoint.gitHead` 作为新 Worktree 的 `base_commit`，并使用 detached HEAD。新 Fork 的 `branch = NULL`。Runtime 为同一个 durable operation 准备固定的 Worktree、Session、Run 和 checkpoint action identity：

```text
prepared
  → worktree_created
  → session_created
  → run_created
  → checkpoint_action_created
  → completed
```

相同 `checkpointId` 和 `operationId` 的 retry 会读取已有的 Worktree、Session、Run 和 action。Runtime 不会创建第二套资源。Fork v1 不恢复 checkpoint 时刻完整的未提交 working-tree patch，也不复制全部 immutable snapshot 内容。

Local Fork 使用同一个 Project 和同一个 `workspace_root` 创建新的 Local Session、Run 和 checkpoint action。两个 Local Thread 共享真实目录。Local Fork 是 conversation/runtime fork，不是 filesystem snapshot；当前不提供 copy-on-write、directory snapshot 或 filesystem rewind。

## Session delete

Managed Session delete 在 Git side effect 前写入 `session/delete` durable intent。Runtime 验证 Worktree，再检查 dirty state、冲突和 identity：

```text
durable session/delete intent
  → GitBackend worktree remove
  → Worktree state = deleted
  → Session delete
  → lifecycle intent = completed
```

Runtime 可以在 Worktree removal 与 Session delete 之间重启后继续执行。Detached Worktree 不执行 branch cleanup。Phase 3B 显式创建的 user branch 不会随 Session 删除；已有 branch 非 NULL 的 legacy Worktree 仍使用现有的安全 compare-and-delete cleanup。Runtime 不执行 force remove、未知路径递归删除或 branch `-D`。

Local Session delete 不创建 Git lifecycle intent。它只删除 Eidos Session 数据。它不会删除 Project workspace root 或用户文件。

## Schema and lifecycle states

当前 SQLite schema version 是 v20。v17 → v18 将旧 Git Project 的 `repository_root` 映射为 `workspace_root` 和 `git_repository_root`，保留原 Project id、Worktree FK、Session binding 和 Run。v18 → v19 为 `sessions` 增加 `execution_mode`，按旧 `worktree_id` 回填 `local` 或 `worktree`，并把 `worktrees.branch` 改为可空。v19 → v20 增加 `worktrees.branch_ownership`，并为 lifecycle operation 增加 local-change snapshot 字段和 `worktree/attach-branch` scope。旧 Worktree branch 值会映射为 `legacy_managed`，detached Worktree 映射为 `none`。对于 `worktree_id IS NULL` 的旧 Session，migration 会保留 Local 语义。

`worktree_lifecycle_operations` 从 v17 保留。当前 scope 包括 `session/create`、`session/delete`、`checkpoint/fork` 和 `worktree/attach-branch`。它使用有限状态：`prepared`、`worktree_created`、`session_created`、`run_created`、`checkpoint_action_created`、`branch_attached`、`worktree_deleted`、`completed` 和 `cleanup_required`。它不是通用 workflow engine，也不保存 arbitrary payload executor。

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

Recovery 只处理已有的 managed Worktree lifecycle operation。Local Session 不需要 Git lifecycle recovery，但它的 Run restart verification 仍验证 Workspace identity、Sandbox boundary、rules、Context、permission 和 reconciliation facts。

当 durable plan、GitBackend worktree observation、filesystem、branch 和 HEAD 完全一致时，Runtime 可以继续或 adopt。Detached Worktree 的一致条件是 branch 和 durable branch 都为 NULL，且 HEAD 与 durable base commit 相符。Branch attach recovery 只会接受实际 branch 与 durable intent branch 相同且 HEAD 未变化的情况；实际状态不一致时不会 force switch。无法证明一致时，Runtime 保留数据、目录和 branch，把 lifecycle 标记为 `cleanup_required`，并让相关 managed Session 不可用。

## Git observation hardening

GitBackend contract tests 覆盖 tracked clean、modified、staged、unstaged、untracked、deleted、conflict、Unicode filename、nested path、linked Worktree、HEAD、branch、HEAD diff 和 baseline diff。

Runtime 不允许 configured hook、fsmonitor executable、textconv、external diff、clean filter、process filter、dotted filter driver 或 worktree-specific filter 执行。Dulwich 的低层 read-only observation 不经过这些 executable paths。Worktree create、dirty transfer、branch attach 和 compensation 使用窄的 native hardening seam。Dirty transfer 不直接复制 changed files，而是用 Git patch、staged diff 和 `git diff --no-index` 保留 staged/unstaged 语义。Source Workspace 只做观察和 patch capture。

Observation failure、timeout 或 bounded diff truncation 不会更新 Worktree lifecycle state。Git status 和 changed paths 不经过 porcelain parser，也不会调用 `Index.commit` 或写入 object store。`deleted` 仍然是 terminal state。

## Sandbox

Managed Worktree 的 writable root 是 `Worktree.worktree_root`。Seatbelt 对 verified `git_dir` 和 Project 的 verified `git_common_dir` 只开放 read-only metadata access。原始 repository working tree 不属于该 Thread 的 execution workspace。

Local Workspace 的 writable root 是 `Project.workspace_root`。Local profile 不构造假的 `.git`、`git_dir` 或 `git_common_dir`。Local Run 仍使用相同的 Workspace identity、path boundary、special-file checks、approval 和 sandbox validation。

## Desktop boundary and limits

Session projection 提供 `projectId`、`workspaceRoot`、`gitAvailable` 和 `executionMode`。Desktop 创建 Thread 时显示 Local / Worktree 选择。Git Project 的 Worktree 选择器使用 Runtime `project/gitContext` 提供的 current branch、HEAD、local branches、dirty 和 changed file count。Worktree 起始 ref 等于当前 branch 且 source dirty 时，Desktop 默认勾选 `Include current changes`；Runtime 仍要求 request 显式传入 `includeLocalChanges`。Local Session 隐藏 Starting Branch；Worktree Session 显示 Starting Branch。Worktree Sidebar 在 detached 状态显示 `Detached @ <short-head>` 和 `Create Branch`，并保留 dirty status、HEAD diff 和 baseline diff UI。

当前仍未实现：Parallel Agent、Git staging/commit/push UI、branch merge/rebase/force delete、完整未提交 patch 的 checkpoint rewind/fork，以及 cross-worktree Repository Intelligence sharing。
