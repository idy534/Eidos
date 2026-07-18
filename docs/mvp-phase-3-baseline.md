# Eidos MVP 第三期开工基线

记录日期：2026-07-18（Asia/Shanghai）

本文件冻结第三期开始前的可复现事实；第三期退出结果以 [第三期实施清单](mvp-phase-3.md) 为准。

## 1. 源码与合同基线

| 项目 | 基线值 |
|---|---|
| 开工 commit | `839ee26 ph.3.0` |
| Runtime version | `0.2.0` |
| SQLite schema revision | `4` |
| Eidos protocol version | `1` |
| Event contract version | `1` |
| Tool contract version | `1` |
| Sensitive rules version | `1` |
| 内置工具数 | `8` |

开工时工作树干净；`docs/mvp-phase-3.md` 已包含在基线 commit 中。现有工具定义仍来自静态 `TOOL_SPECS`，DeepSeek Adapter 仍直接读取全局定义，SQLite 尚无 Plugin、Run extension snapshot 或 Step tool snapshot。

## 2. 自动化与原生安全基线

在允许 pnpm 完成 registry 签名验证的原生 macOS 环境执行 `pnpm test`：

- Runtime：135 tests，全部通过，耗时 12.578 秒。
- Renderer state/component：14 tests，全部通过。
- Main/RuntimeClient/真实 sidecar：13 tests，全部通过。
- TypeScript Main、Renderer typecheck 与 Vite production build：通过。
- Seatbelt 原生 smoke：`workspace_write` fail-closed self-test、Homebrew toolchain、Git worktree pointer 均通过。
- Shell process-group：background、redirected background、timeout/TERM ignore 等回归均通过。

第一次在受限网络内执行时，pnpm 在进入项目测试前因无法访问 npm registry 完成 `pnpm@11.7.0` 签名验证而拒绝运行；没有以 `pmOnFail=ignore` 绕过验证。允许联网后原命令通过。

## 3. 第三期回归门槛

第三期不得降低上述 162 个自动化测试和原生安全用例的语义。新增能力必须同时证明：

1. 内置工具的模型定义、审批、沙箱和 ToolResult 字节语义不回归。
2. Plugin/Skill/MCP 不扩大本地控制面，不执行导入脚本，不自动重放外部副作用。
3. 新 SQLite revision 只能向前迁移，并保留 revision 4 数据。
4. 新 Renderer/Main DTO 必须继续闭合校验，未知字段不进入 UI 状态。
