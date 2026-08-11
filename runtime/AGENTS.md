# Runtime test policy

本文件补充根目录 `AGENTS.md`，仅约束 `runtime/` 下的修改。与根规则冲突时，本文件作为更具体规则优先。

## 开发过程

不要因为一次 Runtime 修改默认反复运行完整 `pnpm test` 或 `pnpm test:runtime`。

优先顺序：

1. 先运行直接相关的测试文件或 `pnpm test:affected`。
2. 测试失败后，只重跑失败测试或对应领域，不要立刻重跑 Full Suite。
3. 任务收尾至少运行 `pnpm test:runtime:fast`。
4. 涉及多组件边界时补充 `pnpm test:integration` 或对应 focused integration tests。

## 什么时候必须 Full

以下修改必须在最终验证阶段补充 `pnpm test:runtime:full`；PR 最终验证可使用 `pnpm test:full`：

- RuntimeEngine、RunSupervisor、Runtime Loop 或最终化生命周期；
- SQLite Schema、Migration、Persistence Contract、Recovery 或 Outbox；
- Protocol、共享 DTO、事件契约；
- Tool Runtime、Tool Orchestrator、Approval 或副作用提交；
- Sandbox、Seatbelt、Shell 权限模型；
- Git Backend、Worktree、Checkpoint/Fork 执行身份；
- 全局 Python/Test/Build 配置；
- 用户明确要求完整回归。

`large_repository` 不属于普通 Full。只有 Repository Map/Index/Retrieval 的规模行为、large-repository fixture 本身变化，或用户明确要求规模验收时，额外运行：

```bash
pnpm test:runtime:scale
```

Seatbelt native、bundled Runtime、Electron smoke 和 macOS Packaging 只在相关边界修改或 Release 验证时运行，不作为普通 Runtime 开发循环默认步骤。

## 测试分层

`runtime/tests/conftest.py` 是当前 Runtime suite 分层的集中声明处。Fast 是默认层；跨 SQLite/Runtime/Git/Process/Sandbox 等边界的 suite 显式进入 `integration`，真实平台依赖进入 `platform`，明显高成本 suite 进入 `slow`。`large_repository` 继续作为独立 Scale 层调度。

新增测试时优先使用产品领域名称，不新增 `test_phase*`、`test_r*`、`test_p*`、`test_hotfix*` 或 `test_corrective*` 这类实施阶段命名。

最终回复必须明确列出运行过的测试、未运行的关键验证，以及是否执行 Full Suite。