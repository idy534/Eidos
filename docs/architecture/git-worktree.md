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
- `DulwichGitBackend` 直接返回 Eidos-owned 的 `GitRepositoryDiscovery`、`GitRepositoryContext`、`GitStatusObservation`、`GitDiffObservation` 和 `GitWorktreeEntry`。它负责 repository discovery、HEAD/ref、local branch、status、diff、worktree add/list/remove/prune、switch、patch apply、snapshot hidden ref 和 legacy compare-and-delete branch。
- Dulwich 是唯一的 Git semantic implementation。`GitRefValidator` 只把用户 branch name 转换为 `refs/heads/*`，并调用 Dulwich 的 `local_branch_name`、`is_local_branch` 和 `check_ref_format`。`GitWorkingTreeState` 使用 Dulwich Index/Object primitives 保存 binary、symlink、mode、deleted、untracked 和 staged/index 状态。`GitCliFallback` 只保留两个窄操作：Dulwich 1.2.12 无法在公开 `worktree_add` API 中关闭 executable filter 时使用安全 Worktree Add；Dulwich 没有 `clean -fdx` 的公开参数时使用 destructive clean。`HardenedGitRunner` 只承载这两个 fallback 的 timeout、bounded output、进程组清理和 hook/fsmonitor、credential/prompt、pager hardening。

Dulwich 类型不会传播到 Application、Domain、Protocol、SQLite 或 Desktop。Backend 只输出 Eidos-owned typed Git models。

## Session create

`session/create` 接收 `workspaceRoot`、`executionMode`、可选的 `baseRef` 和显式的 `includeLocalChanges`。`executionMode` 的协议默认值是 `local`。Desktop 在 Git Project 中显式发送 `worktree`，并通过 `project/gitContext` 读取当前 branch、HEAD、local branches 和 dirty file count。Non-Git Project 请求 `worktree` 会返回 `WORKTREE_REQUIRES_GIT`。

Runtime 先解析 Project。Local Session 不创建 Worktree。Worktree Session 会在 Git side effect 前解析 `baseRef` 为 immutable `base_commit`，确定 `project_id`、`worktree_id`、`worktree_root` 和 `branch = NULL`，然后写入 durable lifecycle intent：

```text
prepare
  → durable session/create intent
  → GitBackend resolve baseRef
  → Dulwich porcelain.worktree_add(detach=True, force=False)
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

当 `includeLocalChanges = true` 时，Runtime 要求 source `HEAD` 等于选择的 `baseCommit`。Runtime 在 Git side effect 前捕获 source repository identity、HEAD、branch、status 和 Dulwich working state，并在 Worktree checkout 后重新核验这些事实。Source Workspace 不执行 stash、reset、checkout、add 或其他写入。Runtime 先读取 source root 的 `.worktreeinclude`，再用 Dulwich diff APIs 生成展示 patch，并用 Dulwich Index/Object primitives transfer tracked modified、deleted、staged、unstaged、binary、symlink、mode 和 untracked state。最终 local state 覆盖 `.worktreeinclude` 的同路径内容。Base mismatch、source changed、patch conflict 和 include 安全错误都会失败并触发已有 compensation；无法证明 Worktree 已清理时，lifecycle 会进入 `cleanup_required`。

`.worktreeinclude` 只位于 source repository root。Runtime 不读取 managed Worktree 内的同名文件作为 authority。模式使用 `pathspec.GitIgnoreSpec.from_lines()` 和 `match_file`。Runtime 只保留 Eidos 的绝对路径、`..`、`.git` 和 symlink boundary checks。Runtime 不重新实现 `*`、`**`、negation 或 precedence。缺失匹配不报错。复制只允许普通文件和经过 target 验证的内部 symlink。

`Worktree` 创建后仍然是 detached execution resource。`session/createBranch` 只允许 active、valid、`branch = NULL` 的 Worktree。Runtime 先写 `worktree/attach-branch` durable intent，再在同一个 Worktree cwd 执行 `dulwich.porcelain.switch(target="HEAD", create=branch, force=False)`，核验 HEAD 没有变化，最后把 `branch` 和 `branch_ownership = user` 写入 SQLite。已有 branch 或在其他 Worktree checkout 的 branch 都会拒绝。Branch attach 的 `prepared`、`branch_attached`、`completed` 和 `cleanup_required` 状态支持 startup recovery。用户 branch 随 Worktree 删除保留；旧 attached branch 仍按 `legacy_managed` 兼容语义处理。

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

## Managed Worktree retention、Snapshot 和 Restore

Session 与 Worktree directory 使用不同的生命周期。Retention 可以删除 Eidos-owned 的 managed Worktree directory，但它不会删除 Session、Run、Event、Checkpoint 或 `associated_worktree_id`。Worktree row 会保留为 `state = deleted` 的历史 identity。

Runtime 只把 `ownership = managed` 且 `state = active` 的 Worktree 放入 retention candidate。Runtime 不自动处理 adopted Worktree。Runtime 默认保留最近 15 个 Worktree。`runtime_settings` 保存 `automatic_cleanup` 和 `managed_worktree_limit`，用户可以在 Settings 中关闭自动清理或把数量改为 1 到 100。关闭自动清理时，Runtime 不执行数量清理。

`worktrees.last_used_at` 是 retention 的 durable recency authority。Runtime 在 Worktree 创建、Worktree Handoff 成功、Worktree Run admission、Create Branch Here 和 Restore 成功后更新它。Runtime 不在 status、diff 或 UI polling 时更新它。v21 → v22 migration 把旧 `updated_at` 回填到 `last_used_at`。

Retention 使用 `last_used_at DESC` 选择最近的 N 个 Worktree。Retention 从最旧的 candidate 开始处理超出部分。Runtime 在以下情况跳过 candidate：active Run、unfinished Handoff、unfinished Worktree lifecycle、`cleanup_required`、invalid validation、无法证明 Git/filesystem identity、legacy managed branch 或 Snapshot 不能完成。Runtime 记录 skipped reason。Runtime 不为了满足数量上限 force delete 不安全目录。

Retention 只在 Runtime startup recovery 完成后、Managed Worktree 创建成功后和 Restore 成功后执行一次 bounded reconciliation。Retention 不启动持续 polling，也不进入 Agent Loop。

`WorktreeSnapshot` 保存一次 disposable execution directory 的 durable metadata：

```text
id
worktree_id
session_id
project_id
base_ref
base_commit
head
branch
checkout_branch
branch_ownership
dirty paths
source_fingerprint
artifact_path
artifact_sha256
state = ready | restored | invalid
created_at / restored_at
```

SQLite 只保存 Snapshot metadata。Snapshot artifact 位于：

```text
<EIDOS_DATA_DIR>/worktree-snapshots/<snapshot_id>/
  full.patch.gz
  staged.patch.gz
  working-state.json.gz
  manifest.json
```

Artifact store 使用 Python 标准库 gzip、SHA-256、temporary directory、`os.replace`、file fsync 和 directory fsync。Artifact 复用 Phase 3B 的 `GitWorkingTreePatch`、`capture_worktree_changes` 和 `apply_worktree_changes` 语义。Artifact 保存 tracked modified、tracked deleted、staged、unstaged、untracked、binary、symlink 和 mode changes。`working-state.json.gz` 使用 Pydantic `GitWorkingTreeState` 保存需要精确恢复的 Index 与 working-tree state。Ignored `node_modules`、`.venv`、`.env`、`.env.local` 和 build cache 不进入 Snapshot。Restore 会从当前 source repository 重新 materialize `.worktreeinclude` 和 ignored override，再应用 structured state；旧 artifact 仍可读取 full patch 和 staged patch。

每个 ready Snapshot 都建立专用 Git reachability anchor：

```text
refs/eidos/worktree-snapshots/<snapshot_id> = snapshot.head
```

这个 hidden ref 不是 branch。Runtime 使用 Dulwich compare-and-set 创建 ref，并使用 compare-and-delete 删除 ref。Snapshot 只有在 artifact fsync、artifact checksum、hidden ref 和 SQLite ready row 都成功后才允许 cleanup。Detached HEAD 的 commit 因为仍被 hidden ref 引用，所以不会只依赖 SQLite 中的 `head` 字段。

同一个 Worktree 只选择 `latest_ready_snapshot(worktree_id)`。新的 Snapshot ready 后，旧的 ready Snapshot 会在 compare-and-delete hidden ref 成功后清理。Restore 成功后，Runtime 将 Snapshot 标记为 `restored`，compare-and-delete hidden ref，并删除 artifact。Artifact 删除失败只会记录 deferred cleanup warning，不会回滚已验证的 Worktree。

Retention cleanup 使用 `worktree/retention-cleanup` lifecycle。它按 `prepared → snapshot_saved → worktree_deleted → completed` 执行。Runtime 只在 Snapshot ready 已验证时通过 `GitBackend` 执行 managed Worktree 的 reset、normal clean、必要的 destructive ignored clean 和非 force Worktree remove。Cleanup 失败会进入 `cleanup_required`，并保留 Session 与 Snapshot。

Restore 使用 `session/restoreWorktree` 和 `worktree/restore` lifecycle。Restore 固定原来的 `worktree_id` 和原来的 `managed_root/<worktree_id>`，然后验证 Project repository identity、artifact checksum、hidden ref 和 Snapshot fingerprint。Runtime 通过 `GitBackend` 在旧 root 创建 detached Worktree，不会创建第二个 Worktree，不会创建 branch，也不会把 `base_commit` 改成 Snapshot HEAD。Restore 后 `checkout_branch = NULL`，旧的 `base_commit` 仍作为 baseline diff 的基准。

Restore lifecycle 使用 `prepared → worktree_created → state_materialized → worktree_rebound → completed`。Runtime 会在 restart 后继续未完成的 cleanup 或 restore。Runtime 如果不能安全清理 partial restore directory，会把 operation 标记为 `cleanup_required`，并把 Run/Handoff 错误映射为 `WORKTREE_RESTORE_REQUIRED`。Runtime 不会创建 WT2 逃避恢复错误。

Runtime startup 会检查 ready Snapshot row、artifact directory 和 hidden ref。`ready row + artifact + ref` 才是 valid Snapshot。缺失 artifact 或 ref mismatch 会把 row 标记为 `invalid`。没有 SQLite ownership proof 的 snapshot artifact directory 和 hidden ref 只作为 orphan candidate 处理；Runtime 不删除陌生的其他 `refs/eidos/*`。

Session Delete 会先通过既有 `session/delete` durable operation 清理 Snapshot artifact、hidden ref 和 metadata，再删除 Session。User branch ref 不属于 Snapshot cleanup，因此 Session Delete 会保留它。Checkpoint 仍然是用户/Runtime 的任务恢复点，Worktree Snapshot 只是 disposable directory 的恢复材料。

## Schema and lifecycle states

当前 SQLite schema version 是 v22。v17 → v18 将旧 Git Project 的 `repository_root` 映射为 `workspace_root` 和 `git_repository_root`，保留原 Project id、Worktree FK、Session binding 和 Run。v18 → v19 为 `sessions` 增加 `execution_mode`，按旧 `worktree_id` 回填 `local` 或 `worktree`，并把 `worktrees.branch` 改为可空。v19 → v20 增加 `worktrees.branch_ownership`，并为 lifecycle operation 增加 local-change snapshot 字段和 `worktree/attach-branch` scope。v20 → v21 增加 `sessions.associated_worktree_id`、`worktrees.checkout_branch` 和 Session Handoff operation。v21 → v22 增加 `worktrees.last_used_at`、`runtime_settings`、`worktree_snapshots` 和 retention/restore lifecycle 字段。旧 Worktree branch 值会映射为 `legacy_managed`，detached Worktree 映射为 `none`。对于 `worktree_id IS NULL` 的旧 Session，migration 会保留 Local 语义。

`worktree_lifecycle_operations` 从 v17 保留。当前 scope 包括 `session/create`、`session/delete`、`checkpoint/fork`、`worktree/attach-branch`、`worktree/retention-cleanup` 和 `worktree/restore`。它使用有限状态：`prepared`、`worktree_created`、`session_created`、`run_created`、`checkpoint_action_created`、`branch_attached`、`snapshot_saved`、`state_materialized`、`worktree_rebound`、`worktree_deleted`、`completed` 和 `cleanup_required`。它不是通用 workflow engine，也不保存 arbitrary payload executor。

## Startup recovery

Runtime 的启动顺序是：

```text
SQLite initialize
  → recover runtime facts
  → construct WorktreeManager
  → WorktreeManager.recover()
  → WorktreeRetentionService.reconcile()
  → Session Handoff reconciliation
  → construct and expose applications
  → Runtime ready
```

Recovery 先处理已有的 managed Worktree lifecycle operation，再处理 Snapshot artifact、hidden ref、retention cleanup 和 restore。Local Session 不需要 Git lifecycle recovery，但它的 Run restart verification 仍验证 Workspace identity、Sandbox boundary、rules、Context、permission 和 reconciliation facts。

当 durable plan、GitBackend worktree observation、filesystem、branch 和 HEAD 完全一致时，Runtime 可以继续或 adopt。Detached Worktree 的一致条件是 branch 和 durable branch 都为 NULL，且 HEAD 与 durable base commit 相符。Branch attach recovery 只会接受实际 branch 与 durable intent branch 相同且 HEAD 未变化的情况；实际状态不一致时不会 force switch。无法证明一致时，Runtime 保留数据、目录和 branch，把 lifecycle 标记为 `cleanup_required`，并让相关 managed Session 不可用。

## Git observation hardening

GitBackend contract tests 覆盖 tracked clean、modified、staged、unstaged、untracked、deleted、conflict、Unicode filename、nested path、linked Worktree、HEAD、branch、HEAD diff 和 baseline diff。

Runtime 不允许 configured hook、fsmonitor executable、textconv、external diff、clean filter、process filter、smudge filter、dotted filter driver 或 worktree-specific filter 执行。Dulwich 的 status/diff 使用空配置对象，避免执行 external filter。Dulwich `worktree_add` 在没有 executable filter 时直接使用；发现 filter command 时，Runtime 预先选择唯一的 `GitCliFallback.worktree_add`，并通过 bounded native config overrides 清空 clean/process/smudge。Dirty transfer 使用 Dulwich structured state 和 `dulwich.porcelain.apply_patch` 的兼容文本路径，不使用 `git diff --no-index` 或 `git apply`。Source Workspace 只做观察和 patch capture。

Observation failure、timeout 或 bounded diff truncation 不会更新 Worktree lifecycle state。Git status 和 changed paths 不经过 porcelain parser，也不会调用 `Index.commit` 或写入 object store。`deleted` 仍然是 terminal state。

## Sandbox

Managed Worktree 的 writable root 是 `Worktree.worktree_root`。Seatbelt 对 verified `git_dir` 和 Project 的 verified `git_common_dir` 只开放 read-only metadata access。原始 repository working tree 不属于该 Thread 的 execution workspace。

Local Workspace 的 writable root 是 `Project.workspace_root`。Local profile 不构造假的 `.git`、`git_dir` 或 `git_common_dir`。Local Run 仍使用相同的 Workspace identity、path boundary、special-file checks、approval 和 sandbox validation。

## Desktop boundary and limits

Session projection 提供 `projectId`、`workspaceRoot`、`gitAvailable` 和 `executionMode`。Desktop 创建 Thread 时显示 Local / Worktree 选择。Git Project 的 Worktree 选择器使用 Runtime `project/gitContext` 提供的 current branch、HEAD、local branches、dirty 和 changed file count。Worktree 起始 ref 等于当前 branch 且 source dirty 时，Desktop 默认勾选 `Include current changes`；Runtime 仍要求 request 显式传入 `includeLocalChanges`。Local Session 隐藏 Starting Branch；Worktree Session 显示 Starting Branch。Worktree Sidebar 在 detached 状态显示 `Detached @ <short-head>` 和 `Create Branch`，并保留 dirty status、HEAD diff 和 baseline diff UI。

当前仍未实现：Permanent Worktree、Pinned Chat、Archive Chat、Parallel Agent、Git staging/commit/push UI、branch merge/rebase/force delete、Pull Request UI、完整未提交 patch 的 checkpoint rewind/fork，以及 cross-worktree Repository Intelligence sharing。
