# Runtime Git Worktree Kernel

本文描述当前 Runtime Git Worktree Kernel 和 Session binding 的实现。本文不描述 Desktop UI、Checkpoint Fork 或 Parallel Agent。

## Project

Runtime 只从已验证的 Git repository 创建 Project。

`repository_root` 是 Git 返回的 canonical working-tree root。`git_common_dir` 是 Git 返回并由 Runtime canonicalize 的 common metadata directory。Runtime 不使用目录 basename 作为 Project identity。

Project ID 由 verified `git_common_dir` 的 SHA-256 生成。SQLite 对 `repository_root` 和 `git_common_dir` 都设置唯一约束。

## Worktree

Worktree 持久化以下生命周期事实：

- `project_id`、`worktree_root` 和 `git_dir`；
- 创建时的 `base_ref` 和 immutable `base_commit`；
- Runtime 生成的 `branch`；
- `ownership`：`managed` 或 `adopted`；
- `state`：`active`、`missing`、`invalid` 或 `deleted`；
- 创建和更新时间。

Runtime 不把动态 HEAD 或 dirty state 当作持久事实。Runtime 每次通过固定 Git command 查询这些状态。

当前创建流程只生成 `managed` Worktree。`adopted` 记录用于后续旧 Session 迁移。普通 managed lifecycle 不会删除 adopted workspace。

## Runtime ownership

Runtime 决定 managed root。默认 root 是 Runtime data directory 的 sibling。默认 `~/.eidos/` 对应 `~/.eidos-worktrees/`。测试可以通过 `WorktreeManager` constructor 注入临时 root。

Managed root 不得和 Runtime data directory overlap。模型不能提供 managed absolute path。模型也不能调用 WorktreeManager。

Runtime 生成 `eidos/<short-worktree-id>` branch 和 `managed_root/<worktree-id>` path。Runtime 在 `git worktree add` 前检查 branch collision。SQLite 的 `(project_id, branch)` unique constraint 负责最后一道保护。

## Session binding

新的 `session/create(workspaceRoot)` 把 `workspaceRoot` 作为 repository seed path。Runtime 先解析 canonical repository root，再创建一个 managed Worktree。`sessions.worktree_id` 只保存 Worktree 外键。`sessions.workspace_root` 继续保存 repository root，保持现有 Desktop wire contract 的兼容语义。

已有 Session 的 `worktree_id` 可以是 NULL。Legacy Session 继续使用 `sessions.workspace_root` 作为执行 Workspace。Runtime 不在 migration 中执行 Git，也不为旧 Session 自动创建 Worktree。

Session 创建的 application compensation 会在 Session persistence 失败时调用不带 `--force` 的 `WorktreeManager.delete`。Worktree dirty、branch ownership 或 Git observation 不满足安全条件时，Runtime 保留用户数据并写入 recovery-needed 日志。

Run、Tool、Shell cwd、Project Rules 和 Repository Intelligence 通过统一 execution workspace resolver 选择 root。Managed Session 使用 `worktrees.worktree_root`，Legacy Session 使用 `sessions.workspace_root`。

## Lifecycle and compensation

`WorktreeManager` 是统一生命周期接口。它提供 `create`、`open`、`validate`、`list`、`recover`、`cleanup`、`delete`、`status` 和 `diff`。

`create` 使用以下顺序：

```text
discover repository
  → resolve base_ref and base_commit
  → allocate Runtime id, branch and root
  → git worktree add
  → validate root, git_dir, common_dir, branch and HEAD
  → persist Project/Worktree
```

Git 和 SQLite 没有真正的原子事务。如果 Git 已创建而 SQLite persistence 失败，Runtime 只尝试删除本次创建、已验证为 clean 的 Worktree。Runtime 会再次确认没有 Worktree 使用本次生成的 branch，并用 `git update-ref -d` 加 expected base commit 条件删除本次生成的 branch。branch 已变化、仍被使用或无法确认时，Runtime 会保留 branch 并写入 recovery-needed 日志。Runtime 不使用 `--force`。Runtime 如果无法确认删除成功，就保留目录。下一次 `recover()` 会把没有 SQLite record 的 Git Worktree 返回为 orphan candidate。

`delete` 只接受 `managed` Worktree。Runtime 会先 validate，再查询 status。dirty 或 conflict Worktree 会被拒绝。Runtime 使用不带 `--force` 的 `git worktree remove`。Runtime 成功 remove 后才把 SQLite state 更新为 `deleted`。Git branch 默认保留。

`deleted` 是 Runtime 的 terminal state。原路径之后重新出现时，Runtime 只把它显示为已删除记录的外部重现，不会恢复 managed ownership，也不会自动删除该目录。`cleanup` 只执行 `git worktree prune`，并把已经确认不存在的 managed stale record 收敛为 `deleted`。它不删除 dirty Worktree、未知目录、adopted workspace 或 branch。

## Status and diff

Git status 使用 `git status --porcelain=v2 -z`。Runtime 只有在机器输出没有截断且完整解析时才返回精确的 staged、unstaged、untracked、conflict counts、当前 HEAD、branch 和 observed timestamp。输出失败、超时、截断或解析不完整时，Runtime 返回 typed observation error，不把部分输出当作完整事实。

HEAD diff 使用：

```text
HEAD → working tree
```

Baseline diff 使用创建时冻结的：

```text
base_commit → current working tree
```

Baseline diff 不使用当前 branch name。Diff 返回 scope、base commit、HEAD、dirty、changed files、unified diff 和 truncation metadata。完整 Diff 不会写入日志。

两种 Diff 都加入 tracked changes 和 `git ls-files --others --exclude-standard -z` 返回的 untracked files。Runtime 使用只读的 bounded `git diff --no-index` 生成 untracked 内容，不修改 Git Index。Diff 总输出受统一 byte budget 限制。

## Validation and recovery

`validate` 同时检查 filesystem 和 Git：

- root 存在并且是 working tree；
- Git dir 与 SQLite record 一致；
- Git common dir 与 Project 一致；
- Worktree path 属于 Project 的 `git worktree list --porcelain -z`，并且机器输出完整；
- branch 与 SQLite record 一致；
- HEAD 可以解析。

`recover` 结合 SQLite records、filesystem 和 `git worktree list --porcelain -z`：

| 观察 | Runtime 结果 |
| --- | --- |
| DB record、Git metadata、filesystem 都存在且一致 | `active` |
| DB record 存在，但 Git metadata 或 filesystem 缺失 | `missing` |
| DB record 存在，但 Project common dir、Git dir、root 或 branch 不一致 | `invalid` |
| Git Worktree 存在，但没有 DB record | orphan candidate，不自动删除 |

如果 Git observation 失败、超时、输出截断或解析不完整，`validate`、`list`、`recover` 和 `delete` 不更新 lifecycle state。`recover` 只更新可以证明的 state。它不丢弃用户修改，也不执行 force remove。

## Sandbox boundary

Runtime Git lifecycle、status 和 diff 通过可信 `GitProcess` 执行固定 argv。Git command 使用 `shell=False`、显式 cwd、有界 timeout、有界 stdout/stderr 和受控环境。

Agent 的 Workspace root 对 Managed Session 是 Worktree root，对 Legacy Session 是 Session 的 `workspace_root`。本 PR 不把 repository root、git common dir 或 `.git/worktrees` 加入 Seatbelt writable roots。linked worktree 的 `.git` pointer file 仍由现有 Agent Sandbox 视为不可写。Runtime Git Service 不等于 Agent filesystem permission。

## Known incomplete lifecycle

Session delete 还没有完整的 dirty detection、active Run race 和 Worktree cleanup 闭环。Git worktree add 成功而 Session persistence 尚未提交的 restart crash window 还没有 durable provisioning recovery。当前也没有 Desktop Project UI、Checkpoint Fork Worktree 或 Parallel Agent。
