# Runtime test policy

本文件补充根目录 `AGENTS.md`，仅约束 `runtime/` 下的修改。与根规则冲突时，本文件作为更具体规则优先。

## 开发过程

不要因为一次 Runtime 修改默认反复运行完整 `pnpm test` 或 `pnpm test:runtime`。

优先顺序：

1. 执行者先运行直接相关的测试文件。执行者只有在确认 `pnpm test:affected` 会选择定向测试时才使用该入口；该入口若会运行 Runtime Fast 或完整其他层，执行者应改用更窄的测试。
2. 测试失败后，只重跑失败测试或对应领域，不要立刻重跑 Full Suite。
3. 执行者在收尾时运行覆盖本次改动的定向测试。若没有合适的定向入口，执行者再考虑 Runtime Fast；Runtime Fast 不是默认要求。
4. 涉及多组件边界时，执行者运行对应的定向集成测试。只有没有合适的定向入口时，执行者才考虑更广的集成层。

## Full Suite 的运行条件

用户明确要求完整回归或要求执行完整套件命令时，执行者才运行 Full Suite。Runtime Full 命令包括 `pnpm test:runtime:full`、它的兼容入口 `pnpm test:runtime` 和包含 Runtime Full 的 `pnpm check:python:full`。项目级入口包括 `pnpm test` 和 `pnpm test:full`。完整 Desktop/Main 套件包括 `pnpm test:desktop` 和 `pnpm test:main`。

核心生命周期、持久化、协议、Tool、Sandbox、Git/Worktree 或全局配置变化时，按影响范围选择定向测试和必要的集成测试。这些变化本身不要求 Full Suite。

`large_repository` 不属于普通 Full。只有 Repository Map/Index/Retrieval 的规模行为、large-repository fixture 本身变化，或用户明确要求规模验收时，额外运行：

```bash
pnpm test:runtime:scale
```

Seatbelt native、bundled Runtime、Electron smoke 和 macOS Packaging 只在相关边界修改或 Release 验证时运行，不作为普通 Runtime 开发循环默认步骤。

## 测试分层

`runtime/tests/conftest.py` 是当前 Runtime suite 分层的集中声明处。Fast 是默认层；跨 SQLite/Runtime/Git/Process/Sandbox 等边界的 suite 显式进入 `integration`，真实平台依赖进入 `platform`，明显高成本 suite 进入 `slow`。`large_repository` 继续作为独立 Scale 层调度。

新增测试时优先使用产品领域名称，不新增 `test_phase*`、`test_r*`、`test_p*`、`test_hotfix*` 或 `test_corrective*` 这类实施阶段命名。

最终回复必须明确列出运行过的测试、未运行的关键验证，以及是否执行 Full Suite。
