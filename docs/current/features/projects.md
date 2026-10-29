# 多目录项目

Eidos 的项目保存稳定的 Project ID、名称、多个文件夹、一个主要文件夹和配置版本。文件夹可以是 Git 仓库、已有 Git Worktree、仓库子目录或普通目录。项目可以共用文件夹；文件系统内容仍然共享。

用户可以在侧栏项目的右键菜单打开“编辑项目”。用户可以修改名称、添加或移除文件夹、设置主要文件夹，并保存或取消。创建项目和编辑项目使用同一个表单。表单至少保留一个文件夹。用户移除主要文件夹时，表单会把剩余的第一个文件夹设为主要。保存失败后，表单保留输入。相同内容的重试复用同一个操作 ID。并发修改返回 `PROJECT_CONFIGURATION_CONFLICT`。

项目移除继续使用现有规则。正式会话、未结束的 Worktree 生命周期和需要恢复的快照会阻止移除。移除项目只删除 Eidos 元数据，不删除用户文件夹。

## 会话与文件

新会话固定 Project ID、主要文件夹、文件夹顺序、目录身份和 Git 来源。项目编辑只影响之后创建的会话。已有会话和运行中的 Run 保留原绑定。项目改名仍会刷新侧栏名称。系统不会因为修改主要文件夹而把旧会话改成无项目会话。

保存项目时打开的草稿会同步新的文件夹、主要文件夹和 Git 可用状态。Session 创建时会把已核验的 Git 来源写回仍匹配当前项目配置的根。并发项目编辑不会用 Session 的旧根快照覆盖新配置。

创建会话和草稿浏览都会按项目保存的绝对路径逐级使用 `O_NOFOLLOW` 核验目录。父目录被替换成符号链接时，Runtime 会拒绝该目录，不会把链接目标改成新的项目根。

主要文件夹提供 Agent Shell 的默认 cwd。Agent 可以用绝对路径访问其他已绑定文件夹，也可以把 Shell cwd 设到这些文件夹及其子目录。`run_shell.cwd` 省略或使用 `.` 时，每次都从主要文件夹开始。相对 cwd 参数也以主要文件夹为起点。命令中的相对路径以本次 cwd 为起点。Shell 内的 `cd` 不改变下一次调用的默认 cwd。文件读取、Patch、图片、Shell 和 workspace_read MCP 使用这些根。系统保留身份、文件版本、Durable Intent、审批和结果核验。默认沙盒保护各根的 `.git`、`.agents`、`.eidos` 和实际 Git metadata；附加根没有单独的逐次写入审批。普通 Skill 和受保护路径继续使用原审批规则。完全访问模式继续遵循其已确认的权限语义。

`gitWriteAccess=request` 选择本次 cwd 所属的仓库，并要求 `justification`。Runtime 从 Session 固定的 Git metadata 目录身份创建 Run 快照，并在审批前、启动前和命令结束后核对身份。普通 Shell 也会在启动前核对所有冻结根的 Git metadata 身份。Git metadata 被同路径替换时，Shell 不会启动；命令运行时身份发生变化会要求后续对账。项目的主要文件夹可以没有 Git；次文件夹仍可独立申请 Git 写入。多个已绑定文件夹相互嵌套时，cwd 解析后优先使用最深目录的身份。Runtime 不解析 Shell 命令中的 `cd` 或 `git -C` 来推断授权仓库。Agent 需要把 cwd 设到目标仓库或其子目录。Shell 启动前会在环境准备前后核对所有冻结的根；目录被替换时，命令返回 `workspace_identity_changed`，并且不会启动。

Files 面板提供文件夹选择器。文件预览、资源读取、文件内查找和通知按具体根处理。文件引用候选包含各个根，并用路径区分同名文件。文件链接携带根身份。MCP 在启动和调用前核验固定的目录身份。Hook 的改动观察复用现有 fd 扫描，并在扫描前后核对根。Shell 的改动观察同时覆盖各根；嵌套根中同一文件只报告一次，并沿用主要根的相对路径；不完整观察继续报告未知范围。

首次 Shell 调用可能没有完整的执行前清单。命令仍可执行，结果通过 `workspaceChangeState=unknown` 和不完整清单字段说明观察范围。调用者不能仅凭此时的 `workspaceChanged=false` 判断文件没有变化。次目录中的长命令继续使用同一个 Run 的 `write_stdin`、取消和退出结果落库流程。Runtime 重启不会重放这些命令。

## Git 与 Worktree

Review 面板按 checkout 选择 Git 目标。每个 checkout 保留独立状态、Diff、分支操作和评论。系统不会因为两个 checkout 共用 Git common directory 就合并它们。Git 控件作用于所选的整个仓库，包括只挂载仓库子目录的情况。文件和 Agent Shell 的基础权限仍限制在已添加的目录；Git 目标不会扩大这些权限。界面显示 Git 操作对应的仓库路径。

草稿聊天可以打开审查，切换项目中的 Git 仓库，查看当前工作区状态和文件 Diff。草稿请求通过 `projectId` 和 `rootId` 绑定已保存的项目。Runtime 从项目配置核对所选根；打开审查不会创建 Session、Worktree 或额外项目。草稿选择 Worktree 模式时，审查仍预览源目录。正式 Worktree 在创建聊天时建立。草稿不请求会话评论或远程 Git 工作流；界面在创建聊天后再开放评论、Git 写操作和编辑器入口。正式会话继续使用冻结的根，不受草稿参数影响。

主要文件夹属于 Git 仓库时，新会话可以选择 Eidos 管理的 Worktree。系统会把同一源 checkout 中的挂载目录映射到新 Worktree 的对应相对位置，并用 fd-relative、拒绝符号链接的方式核验映射目录。替换映射路径的任一目录组件会返回 `WORKSPACE_IDENTITY_CHANGED`，不会把 Worktree 外的目录加入 Run 权限或 Workspace Explorer 的读取范围。其他 checkout 和普通目录继续共享原位置。界面显示“共享”。Desktop Terminal 默认使用会话主要目录的实际执行位置。

主要文件夹只是仓库子目录时，Shell 使用已核验仓库的 `.git` 标记。Fork 会把来源 Session 的挂载子目录映射到新 Worktree 中对应的位置。受保护数据目录内的 Worktree 允许读取已核验的 Git metadata 和 checkout 目录本身。这个目录权限不递归开放未挂载文件的内容或写入。默认 Git metadata 写入仍被拒绝；用户批准的 Git 写入继续使用原有权限流程。用户配置的保密路径拒绝规则继续生效。

Worktree、Session 根快照、保留快照和生命周期记录固定源 repository root 与 common directory。Session 根快照也保存 Git metadata 目录身份；按根读取 Git 状态或 Diff 前会核对身份，替换 metadata 会返回 `WORKSPACE_IDENTITY_CHANGED`。切换项目主要文件夹后，旧 Worktree 的运行、Fork、Handoff、恢复和清理仍使用原仓库。新会话会重新发现 Git，所以项目保存后才初始化的仓库也能被新会话识别。

Checkpoint 和 Rewind 继续覆盖主仓库现有 Git 能力可保存的内容。其他仓库、共享文件夹、ignored 文件和非 Git 文件夹不属于该恢复范围。本版本不提供多仓库 Worktree 组、多仓库原子提交、统一 PR 或全项目原子 Rewind。Repo Intelligence 的自动索引仍以主要目录为范围；Agent 可以显式搜索其他根。

## 持久化与兼容

SQLite Schema v45 增加多目录模型，v46 固定未完成 Worktree 创建操作的目录快照，并为旧 Project Session 回填 Git metadata 目录身份。v47 保存 Worktree 私有 gitdir 的目录身份，并为有效的旧 Worktree 回填。系统可以从 v44、v45 或 v46 升级。迁移保留原 Project、Session、Worktree 和快照 ID，并为原单目录项目补齐文件夹记录。已经有 Session 的旧创建记录会从该 Session 的已保存目录快照补齐。未完成 Fork 和 Checkpoint Fork 会从有效 Git 快照关联的源 Session 读取根快照。缺少有效源快照或根快照的旧 Worktree 创建会进入 `cleanup_required`，重试会返回 `WORKTREE_RECOVERY_REQUIRED`；系统不会按升级后的项目配置静默绑定。旧 Session 和 Worktree 的 Git metadata 只有在路径、目录身份和仓库绑定都能核实时才会回填；无法核实的根会让 Git 操作 fail closed。会话通过 `project_id` 保留项目归属。Worktree 和快照保存独立来源字段。迁移失败会回滚 Schema；系统不会把部分迁移当作成功。

旧的单目录创建接口仍可使用。新建多目录项目使用 `project/createFolders`；读取和修改使用 `project/read` 与 `project/update`。Python Schema 生成 Desktop 的项目与 Git 请求契约。项目更新和 Event/Outbox 在同一事务提交。项目会话的本地创建操作按 Project ID 去重，不按可变的主要目录去重。旧版已完成操作会先核对持久 Session 的 Project 绑定再重放。会话创建在准备时固定根快照，Worktree 创建期间的项目编辑不会改变它。重启后的操作重试也使用已保存的快照。同一创建操作的恢复和首次请求使用同一把操作锁。Git 创建前后核对源目录和 Git metadata 身份。显式切换同仓库中的主子目录会被视为不同请求。定时任务与渠道通过 Project ID 创建新会话。Hook 的改动观察覆盖所有根；观察不完整时，系统使 Run 的 Workspace 缓存失效。旧 Run 的单根快照继续可读。

实现者通过多目录专题测试、相关 Git/Worktree/恢复测试、两端协议检查、真实 Seatbelt 和隔离 Electron 界面检查验证本次改动。测试记录只说明实际执行范围，不表示 Full Suite、真实 Provider/MCP 服务或发布包已经通过。
