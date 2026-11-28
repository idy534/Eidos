# Eidos 评测程序与执行方案（macOS）

日期：2026-11-07 起草，2026-11-18 按当前源码修订，2026-11-28 改为只在 macOS 上实施的方案，并补充分层运行和机制消融；同日再次修订：以带答案的题库为主，外部产品对照改为只在首个基线时运行一次，并确定本期受测模型。状态：设计方案，尚未实现评测程序，也没有产生真实模型成绩。

本方案回答三个问题：Eidos 能完成哪些真实任务？Eidos 在哪一步失败？一次产品改动是否带来了可重复的改善？

本期只在 macOS（Apple Silicon）上实施。Linux 容器、Docker 任务环境和公开评测原环境不在本期范围，原因见第 15 节。

本文是实施规格。第 3、4、5、6、13 节给出目录、环境、格式、流程和命令，第 14 节给出每个阶段的交付物和验收条件，第 16 节列出实施者必须遵守的约束。文中的 `eidos-eval` 命令和 `eval/` 下的新文件在实施前都不存在。

## 1. 评测范围与结果边界

评测程序维护三条结果线。

| 结果线 | 主要问题 | 本期处理 |
| --- | --- | --- |
| 本地真实任务 | 用户交给 Eidos 的工作能否完成？ | 程序通过生产 stdio 协议驱动完整 Runtime，外部程序检查答案和产物。 |
| 产品可靠性 | 取消、审批、重启、长任务和资源清理是否正确？ | 程序在可丢弃环境中执行有明确预期的场景。 |
| 外部产品对照 | 同一模型、同一任务下，Eidos 与其他 harness 的差距在哪里？ | 只在首个基线时运行一次，对照对象是本机的 DeepSeek Harness；日常优化不运行。 |

日常优化只运行 Eidos，并按题库已有的答案判分。代码题的答案是隐藏测试，问答题的答案是参考要点和评分条目，判分不需要另一个 harness 参与。外部产品对照会让模型消耗至少翻倍，因此只在首个基线时把首批任务各运行一次，结果保存后供后续批次引用；之后只在大版本对比时重新运行。

这次一次性对照仍然必要。Terminal-Bench 维护者测到，同一模型只因 harness 不同，分数相差约 20 个百分点；放宽时间预算后，大部分差距会收回。[Terminal-Bench 持续维护论文](https://arxiv.org/abs/2611.08417) 对照用来确认同一模型下，Eidos 的工具、上下文和提示词有没有明显拖后腿。SWE-bench 题目另有公开的其他系统轨迹（第 5.1 节），可以免费用于定性对照。

Runtime 评测覆盖协议、应用服务、调度、模型、工具、权限和持久化。它不覆盖 Renderer、preload、Electron 生命周期和真实用户交互。程序另外保留少量 Desktop 验收案例。

报告按仓库理解、代码交付、终端工作流和产品可靠性分别报分。程序不把这四类结果合成一个成熟度分数。

## 2. 当前可以复用的内容

以下事实依据 2026-11-28 的源码核对。正式基线需要另外冻结受测构建。

| 当前入口 | 已有作用 | 评测程序的使用方式 |
| --- | --- | --- |
| [`desktop/main/runtime-client.ts`](../../desktop/main/runtime-client.ts) | 启动 `python -u -m eidos_runtime`，校验协议，处理通知和 Runtime 发起的审批请求。它只依赖 Node 内置模块与 `desktop/shared`，不导入 Electron。构造参数包括 `pythonExecutable`、`runtimeRoot`、`dataDirectory`、`environment`、`environmentPolicy`、`onNotification`、`onApprovalRequest` 和 `onStderr`。 | Eidos Adapter 加载 `pnpm build:main` 编译出的 `dist/main/runtime-client.js` 和 `response-runtime-client.js`，不另写协议客户端或 DTO。 |
| [`runtime/eidos_runtime/__main__.py`](../../runtime/eidos_runtime/__main__.py) | 进入 `ResponseActionRuntimeServer`，沿用 RuntimeServer 的业务方法。 | 使用正常启动入口，不直接实例化 RuntimeEngine 作为正式成绩入口。 |
| `initialize`、`runtime/health`、`runtime/shutdown` | 协议握手、存储健康检查和正常关闭。 | 每次尝试的启动、预检和收尾。 |
| `model/create` | 参数为 `provider`、`modelId`、`apiKey`，把模型配置和 API Key 写入当前数据目录的 `models.json`。 | Fixture Manager 为每个新数据目录写入模型配置，见第 4.3 节。 |
| `project/create`、`session/create` | `project/create` 接收 `workspaceRoot` 和可选 `name`；`session/create` 接收 `projectId` 或 `workspaceRoot`，以及 `executionMode`（`local` 或 `worktree`）。 | Git 仓库任务先创建 Project，再以 `local` 模式创建 Session，这样仓库索引、Git 状态和 Checkpoint 与真实使用一致。 |
| `run/start`、`run/cancel` | `run/start` 接收 `sessionId`、`userInput`、`modelId`、`approvalMode`、`reasoningSelection`、`workMode` 等字段。 | 每次显式传入批次配置，不依赖默认值。 |
| `run/updated`、`run/completed` 通知，`session/read`、`event/list` | 推送 Run 状态变化，读取 Session 快照和持久事件。协议已移除 `run/status`。 | 从通知与 `session/read` 确定终态，用 `event/list` 的 `afterEventId` 分页补读并去重。 |
| `item/requestApproval` | Runtime 发给客户端的审批请求，RuntimeClient 通过 `onApprovalRequest` 回调，以 `{ decision: "approve" \| "reject", feedback? }` 应答。 | 按批次声明的审批策略应答，见第 4.6 节。 |
| `planning/answer`、`run/steerQueued` | 回答 Agent 的用户输入请求；把运行中排队的消息转为当前 Run 的引导。 | 只发送任务卡声明的固定回复和运行中消息。 |
| `context/usage`、`context/compact`、`toolCall/readText`、`checkpoint/list` | 读取上下文用量，在明确时点请求压缩，分页读取大段工具输出，列出 Checkpoint。 | 采集诊断证据；压缩场景用 `context/compact` 触发。 |
| `EIDOS_FAKE_MODEL` | 让 Runtime 使用脚本化模型，当前提供 `goal`、`team`、`write`、`shell` 和 `network-shell`。 | 只用于预检和评测程序自测，不产生能力成绩。 |
| `EIDOS_DATA_DIR`、`EIDOS_PROJECTLESS_ROOT`、`HOME` | 隔离 Runtime 数据、无项目工作空间和 HOME 下的状态，见第 4.1 节。 | 每次尝试分别设置。 |
| `model_attempts` 表 | 保存 `usage_json`（含 `input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_write_tokens`）、`ttft_ms`、`duration_ms`、`error_code`、`http_status`、`retry_decision_json` 和 `tool_call_count`。数据库文件为 `<EIDOS_DATA_DIR>/state.sqlite`。 | 受测进程退出后，用只读导出器补充用量和轨迹诊断，见第 9.1 节。 |
| `scripts/evaluate-request-content.py`、`evaluate-context-compaction.py`、`evaluate-memory-learning.py`、`evaluate-memory-cases.py` | 请求内容、上下文压缩和记忆的专项回放。 | 保留为专项回归，不当作通用任务完成率。 |
| [`eval/runner.py`](../../eval/runner.py) 与 `eval/cases/baseline.json` | 3 个脚本化用例，直接调用 RuntimeEngine 和 `ScriptedModel`。 | 保留为组件实验，不产生能力成绩，也不作为新程序的基础。 |
| DeepSeek Harness（下称 dsh） | 本机已安装 0.2.2-rc.5，CLI 为 `/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh`。`dsh --profile headless` 无界面运行一个任务，支持 `--json`（NDJSON 事件）、`--patch`（叠加配置）、`--permission-mode`、`--approval-policy`、`--add-dir` 和 `--session-id`。工作区为进程的 cwd，配置根目录由 `DSH_HOME` 决定；`--dump-config` 和 `--dump-config-schema` 输出合成后的配置和 schema。headless 模式关闭了凭据插件，只能从环境变量读取 API Key。 | 基线对照的 Adapter，见第 6.2 节。实施时以 CLI 帮助和 schema 的实际输出为准，并把版本、完整参数和合成后的配置写入 manifest。 |
| Codex CLI | 本机已安装 0.166.0，`codex exec` 支持 `--json` 等非交互参数。 | 以后使用 OpenAI 模型时再接入，本期不实现。 |

Run 状态包括 `queued`、`running`、`waiting_approval`、`waiting_input`、`waiting_agents`、`finalizing`，以及终态 `succeeded`、`failed`、`stopped`、`canceled` 和 `interrupted`。

目前没有可直接复用的任务包、外部判分、批量对照和报告程序。

## 3. 程序结构

```text
任务导入与校准
    ↓
冻结任务清单、受测构建、模型与预算
    ↓
为每次尝试创建独立 trial 根目录（工作区、数据目录、HOME、TMPDIR）
    ↓
Eidos Adapter → RuntimeClient → stdio → Eidos 完整 Runtime
dsh Adapter → dsh --profile headless --json（只在基线对照时）
    ↓
收集最终回答、产物、事件和诊断记录
    ↓
停止受测进程，冻结产物副本
    ↓
在干净副本中外部判分 / 问答评审 / 必要的人工复核
    ↓
分项成绩、失败分类、改动前后对照、产品对照、优化清单
```

| 模块 | 职责 | 约束 |
| --- | --- | --- |
| Importer | 导入历史修复和公开任务，分离公开材料与私有判分材料。 | 保留上游版本和本地调整说明，参考答案不进入 Agent 工作区。 |
| Fixture Manager | 准备代码、依赖、黄金 HOME 和初始状态。 | 只操作评测目录，不改动用户日常仓库和日常数据目录。 |
| Runner | 执行批次、分配 trial 根目录、检查预算、收集产物、清理进程。 | 只维护评测进度，不写 Eidos 业务状态。 |
| Eidos Adapter | 通过 RuntimeClient 提交任务、读取事件、应答审批、取消 Run。 | 不更换工具，不缩短模型循环，不向模型补充隐含提示。 |
| dsh Adapter | 通过 `dsh --profile headless` 提交同一任务，收集 NDJSON 事件和最终回答。 | 不修改 dsh 的提示词、工具和默认行为，只覆盖模型、联网和遥测配置，并记录版本和合成后的配置。 |
| Grader | 在外部干净副本中检查最终结果。 | 保留判分证据和自身版本，不信任 Agent 的完成声明。 |
| Analyzer | 根据已保存证据归类失败，计算对照结果。 | 把已确认原因和待验证判断分开。 |
| Reporter | 输出 JSON、Markdown 和可离线打开的 HTML 报告。 | 展示样本、缺失数据和比较边界。 |

CLI、Runner 和 Adapter 使用 Node 22 ESM，复用编译后的 RuntimeClient 和 `desktop/shared` 契约。判分器是任务自带的可执行命令（Shell 或任务环境中的 Python、Node），由 Grader 在干净副本中调用。评测程序不向 Runtime 增加依赖，不进入发布包，也不接入 `pnpm test`。

建议目录：

```text
eval/
├── README.md               运行说明（阶段零新增）
├── runner.py、cases/       现有脚本化组件实验，保留
├── bin/eidos-eval.mjs      CLI 入口
├── lib/                    配置、批次、尝试、证据、预算、报告
├── adapters/
│   ├── eidos.mjs           RuntimeClient 驱动
│   └── dsh.mjs             DeepSeek Harness 驱动（基线对照）
├── importers/              Eidos 修复历史、SWE-bench、SWE-Atlas 等题库的导入与 macOS 校准
├── tasks/<taskId>/         公开材料：task.json、prompt.md、fixture 脚本
├── suites/                 suite.json：任务列表与分组
├── agents/                 agent.json：受测产品配置；dsh 的评测补丁也放在这里
└── test/                   评测程序自测（EIDOS_FAKE_MODEL）
```

私有材料（参考解、隐藏测试、评审条目）存放在环境变量 `EIDOS_EVAL_PRIVATE_DIR` 指向的目录，不提交到仓库。批次输出存放在 `EIDOS_EVAL_RUNS_DIR`。两者都不能位于 trial 根目录或受测构建目录内。

首期批次按顺序执行。后续并行度由 Runner 控制，它只是本次实验的资源设置，不改变 Runtime 的跨 Session 并发策略。

## 4. macOS 评测环境

### 4.1 三档隔离

| 档位 | 做法 | 可以产出 |
| --- | --- | --- |
| D：开发通道 | 当前 macOS 用户。每次尝试使用独立的 trial 根目录，内含工作区、`EIDOS_DATA_DIR`、`EIDOS_PROJECTLESS_ROOT`、`HOME` 和 `TMPDIR`。 | 开发信号和冒烟回归。不能称为基线。 |
| F：正式通道 | 专用标准用户（建议 `eidoseval`，不加入管理员组）运行 Runner 和受测产品。维护者账户保存私有材料并评分。 | 基线、改动对照和产品对照。 |
| V：虚拟机通道（可选） | 基于 Apple Virtualization 的 macOS 虚拟机，每个批次从快照恢复。 | `full_access` 批次和需要更强隔离的任务。macOS 许可通常只允许每台主机额外运行两个 macOS 虚拟机。 |

**HOME 覆盖。** 当前源码中以下状态由 HOME 决定，Runner 为每次尝试设置独立 HOME 即可隔离：

| 状态 | 位置 | 依据 |
| --- | --- | --- |
| 受管资源 | `$HOME/Eidos/Resource` | [`resource_paths.py`](../../runtime/eidos_runtime/resource_paths.py) 使用 `Path.home()` |
| 插件 MCP 目录 | `$HOME/Eidos/eidos-mcp/<server>` | [`extensions/plugins.py`](../../runtime/eidos_runtime/extensions/plugins.py) |
| Projectless 默认根目录 | `$HOME/Eidos`（设置了 `EIDOS_PROJECTLESS_ROOT` 时不用） | [`workspace/projectless.py`](../../runtime/eidos_runtime/workspace/projectless.py) |
| Shell 环境 | Runtime 通过登录 Shell `-lc` 采集环境，读取 `$HOME/.zprofile`、`$HOME/.zshrc` | [`sandbox/host_shell.py`](../../runtime/eidos_runtime/sandbox/host_shell.py) |
| Git 全局配置 | `$HOME/.gitconfig` | Git 默认行为 |

Fixture Manager 每个批次准备一份**黄金 HOME**：最小的 `.zprofile` 和 `.zshrc`、固定的 Git 身份、预装的受管资源（只装任务需要的资源包）。每次尝试用 APFS 克隆复制（`cp -c -R`），复制几乎不占时间和空间。manifest 记录黄金 HOME 的内容摘要。Desktop 验收另设独立的 Electron user-data。

**D 通道为什么不能产出基线。** Agent Shell 的 Seatbelt 基础策略包含 `(allow file-read*)`（[`sandbox/seatbelt.sbpl`](../../runtime/eidos_runtime/sandbox/seatbelt.sbpl)），只拒绝受保护路径。因此 Shell 能读取当前用户可读的任何文件，包括日常 Eidos 源码目录及其 Git 历史，而代码交付题的答案可能就在那里。D 通道的报告标注“开发信号”，并扫描协议日志中 trial 根目录之外的路径访问，作为越界证据。

**F 通道的搭建：**

1. 维护者用 `sysadminctl -addUser eidoseval` 或系统设置创建标准用户。
2. 受测构建放在 `/Users/Shared/eidos-eval/builds/<buildId>/`：从固定提交 `git archive` 导出，再执行 `uv sync --locked`、`pnpm install` 和 `pnpm build:main`。构建目录对 `eidoseval` 只读。
3. 批次输出写入 `/Users/Shared/eidos-eval/runs/<batchId>/`，`eidoseval` 可写，维护者可读。
4. 私有材料放在维护者账户下权限为 700 的目录。macOS 默认把 Documents、Desktop、Downloads 设为 700，但 HOME 根目录和其他目录可能对其他用户可读。正式批次前必须完成第 4.4 节的探针检查。
5. 维护者以 `eidoseval` 身份执行 `eidos-eval run`（切换登录，或使用 `sudo -u eidoseval -H`），再以自己的身份执行 `eidos-eval grade`。

### 4.2 冻结构建与配置

任务工作区不能与受测构建目录或日常源码目录重合。每个批次记录：

- Eidos 构建标签、源码版本、是否含未提交变更；
- macOS 版本、芯片、内存、语言、时区、Xcode Command Line Tools、uv、Python、Node 和 DeepSeek Harness 版本；
- Provider、精确模型标识、推理设置和模型配置快照；
- 权限模式、审批应答策略、自动审查模型、Shell 环境继承策略、黄金 HOME 摘要；
- Skill、Plugin、MCP、Hooks、记忆、个性化和浏览器配置；
- 任务集、判分器、评审配置、价格表、预算和评测程序各组件的版本。

基线批次使用全新的 Session 和数据目录。普通任务关闭记忆学习，并固定其余扩展配置；专项任务才启用被测功能。Provider 别名可能指向变化的模型，报告保留运行日期和服务返回的版本信息。

**本期受测模型**是火山方舟 Coding Plan 上的 DeepSeek V4.1 Flash：

| 参数 | 值 |
| --- | --- |
| 端点 | `https://ark.cn-beijing.volces.com/api/coding/v3`，使用 Chat Completions |
| 模型 | `deepseek-v4-1-flash-261017` |
| 上下文窗口与最大输出 | 1,048,576 与 393,216，最大输出使用 `max_tokens` 字段 |
| 思考强度 | `high`，作为顶层 `reasoning_effort` 发送 |
| 图片输入 | 关闭 |

Eidos 通过 `model/create` 创建自定义模型：providerId `ArkCodingPlan`，wireApi `chat_completions`，reasoningFormat `openai`，reasoningControl `configured`，可选档位 `none`、`low`、`high`、`max`，outputTokenField `max_tokens`，supportsImages 为 false。字段名以 [`model/config.py`](../../runtime/eidos_runtime/model/config.py) 的 `CustomModelSettings` 和 Desktop 创建自定义模型时实际发送的参数为准。这个模型没有默认思考档位，`run/start` 必须显式传入 `reasoningSelection`。auto_review 的审查模型也使用同一模型；做不到时在 manifest 中记录实际使用的审查模型。

`api/coding/v3` 属于订阅制的 Coding Plan，消耗套餐额度。`api/v3` 是按量计费的在线推理接口，不扣套餐额度，评测不使用。

### 4.3 凭据与进程环境

- 本期的 API Key 只从环境变量 `ARK_API_KEY` 读取。Key 不进入对话、文件、日志或证据。
- Eidos 的 API Key 当前以明文保存在 `<EIDOS_DATA_DIR>/models.json`。Fixture Manager 在 Runtime 初始化后调用 `model/create` 写入。Eidos Runtime 进程本身不继承 `ARK_API_KEY`。程序不复制日常数据目录 `~/.eidos`。冻结数据快照、打包证据和生成报告前，程序排除 `models.json`，并在预检中确认证据中没有凭据。
- dsh 每次尝试使用独立的 `DSH_HOME`：先从出厂 headless 模板准备一份黄金 `DSH_HOME`，每次尝试用 APFS 克隆复制。Key 通过 `ARK_API_KEY` 环境变量传入，遥测用 `DSH_TELEMETRY_MODE=DISABLED` 关闭。程序不读取、复制或修改用户的 `~/.dsh`，尤其是 `.credentials.yaml`、`.env` 和 `settings.yaml`。用户自己的 profile 可能指向其他网关或路由组，不能用于评测。
- Runner 为受测进程构造白名单环境，只传入运行所需变量。评分模型凭据、Runner 自身配置和其他 Provider Key 不进入受测进程。
- Shell 环境继承策略（`all`、`core`、`none`）属于受测条件，写入批次配置；比较不同策略时另开批次。

### 4.4 答案隔离与探针

- **工作区不含未来信息。** 来自 Git 历史的任务，Fixture Manager 用 `git archive <baseCommit>` 导出，再在新目录 `git init` 并提交一次，工作区里没有修复之后的提交、对象和 reflog。需要保留历史的任务，必须证明仓库中不存在可以找到后续修复的对象和引用。
- **私有材料不可读。** F 通道正式批次前，维护者在私有目录放一个无业务价值的探针文件，分别通过文件工具和 Shell 尝试读取，确认读取失败。无法证明隔离时，该批次不能标为答案隔离通过。
- **Canary 字符串。** 每道任务的公开和私有材料都写入同一个评测专用 canary 字符串，便于发现任务泄漏到训练数据或网页。
- 公开代码快照不能包含隐藏测试、答案分支和历史记忆。

### 4.5 工具链与任务环境

- 固定 Xcode Command Line Tools 的 git 与 clang、uv 管理的 Python、固定版本的 Node 和系统 sqlite3，并记录版本。
- 从 Linux 题改编的任务必须在 macOS 上重新校准。BSD 与 GNU 工具的行为不同（例如 `sed -i`、`date`、`readlink`、`grep -P`），任务和判分器都不能假设 GNU 行为。
- Agent Shell 默认禁网：Seatbelt 只在授予网络权限时允许 network-outbound、inbound 和 bind，本机回环同样受限。Fixture Manager 以 Runner 身份预装依赖（uv cache、pnpm store），Agent 运行期间不需要联网。需要联网或本机端口的任务在任务卡中声明，单独分组，因为网络扩权会触发审批，审批结果也是受测条件。
- 首期不使用 Docker、数据库服务或其他外部服务。
- Runner 为每次尝试设置磁盘配额，并在收尾时按进程组清理残留进程。

### 4.6 权限与用户输入

首期默认使用 `auto_review`，并记录审查模型及其费用。任务的原始请求说明必要的普通本地操作。程序不能替用户确认保留的高危操作，也不能自动填入完全访问确认来获取无人值守能力。

在 `auto_review` 下，两类请求会通过 `item/requestApproval` 交给客户端：需要高危人工确认的请求（`requiresHumanConfirmation=true`），以及自动审查拒绝后允许转人工的请求（带 `reviewFallback`）。Runner 不应答时，Run 会停在 `waiting_approval` 直到预算耗尽。每个批次声明审批应答策略：

- 独立完成组拒绝所有转交给客户端的审批请求，结果记为“被人工门槛阻断”，不与能力失败合并统计。
- 允许人工协助的组由操作人员应答，程序记录等待时间和决定。
- 程序保存每次审批的摘要、风险说明、自动审查结论和原因码，并单独统计 `auto_review_rejected`、`auto_review_timeout`、`auto_review_failed` 等，用于发现审查误拒。

`full_access` 只在 V 通道的单独批次中使用，成绩不与默认权限成绩混合。完全访问确认值是协议常量，Runtime 无法区分请求来自 Desktop 还是评测驱动，因此 Runner 在非 `full_access` 批次中拒绝构造这个字段，并在 manifest 中记录实际发送的权限参数。

每个任务卡把允许的用户回复写成 Runner 可以执行的规则。Agent 发起用户输入请求时，Runner 按规则匹配，通过 `planning/answer` 返回预先写好的回答；没有匹配时返回默认回复，并记录一次未预期提问。任务需要运行中追加消息时，Runner 先用 `run/start` 创建排队消息，再按任务声明调用 `run/steerQueued`。

### 4.7 预算与预检

本地小任务以 20 分钟 Agent 时间作为校准起点，中型和长任务另设预算。维护者根据参考解法和首轮校准调整预算，在正式批次前冻结。每个批次显式配置任务数、重复次数、时间预算、最大模型调用次数、费用上限和磁盘限额。程序在执行前输出预计尝试次数和预计费用：

预计费用 = 尝试次数 × 每次尝试的预计主模型费用 + 自动审查费用 + 评审模型费用。

首个单题运行完成后，维护者用实测用量替换估算值。费用数据不完整时，程序同时保留调用次数和时间限制，并标记限制来源。

本期模型按套餐额度计费，真正的限制是额度和刷新周期，而不是单价。阶段零先实测单题的 token 消耗，再据此决定每个批次的规模，使一个批次能在一个额度窗口内跑完。

预检需要确认：Runner 能启动和关闭受测 Runtime 并通过健康检查；任务的初始状态、依赖和参考解法已经校准；黄金 HOME、受管资源和 Shell profile 与批次记录一致；程序能取消 Run 并完成清理；Runner 能按策略应答审批和用户输入；证据和报告中没有凭据；私有材料没有暴露给 Agent。预检可以使用 `EIDOS_FAKE_MODEL`，但模拟模型只能证明运行链路，不能生成能力基线。

## 5. 任务库建设

### 5.1 任务来源

公开基准在 2026 年暴露出大量题目缺陷。OpenAI 在 2026 年 2 月停止报告 SWE-bench Verified：审计中 59.4% 的题目测试有问题，且存在明显污染。7 月 OpenAI 又撤回了对 SWE-bench Pro 的推荐，估计约 30% 的题目有问题。Terminal-Bench 2.0 审计出约 30% 的题目有缺陷，已修订为 2.1。因此本方案的能力结论以低污染任务为准，高污染的公开题只用于发现 harness 缺陷和做回归，并且每道题都必须通过第 5.3 节的校准。[SWE-bench Verified 说明](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)、[SWE-bench Pro 审计](https://openai.com/index/separating-signal-from-noise-coding-evaluations)

| 来源 | 类别 | 自带的材料 | 在 macOS 上的做法 | 污染风险与用途 |
| --- | --- | --- | --- | --- |
| Eidos 自身的修复历史 | 代码交付 | 题目来自 Fix 提交的说明和关联问题；答案是修复提交及其中的测试。 | 优先选只涉及 Python Runtime 的提交（环境只需 `uv sync --locked`）。工作区为父提交的 `git archive`，判分运行修复提交中的定向 pytest。 | 低。能力结论的主要来源。仓库公开，按受测模型的知识截止日期标注。 |
| 已记录的真实失败 | 代码交付、可靠性 | `docs/current/limitations.md`、`docs/evaluations/` 和 issue 中的失败记录。 | 维护者补写验收条件。 | 低 |
| SWE-bench Verified 的纯 Python 子集 | 代码交付 | 题目、标准补丁，以及必须由失败变为通过（FAIL_TO_PASS）和必须保持通过（PASS_TO_PASS）的测试。[swe-bench/experiments](https://github.com/swe-bench/experiments) 公开了其他系统的逐题轨迹和日志，部分提交的文件放在 S3。 | 只选 Django、SymPy、pytest、pylint、requests、Sphinx 等纯 Python 仓库，用 uv 按题目要求的 Python 版本重建环境，优先选环境要求较新 Python 的题。判分在干净副本中运行这两组测试。 | 高：模型可能背出标准补丁。用于发现 harness 缺陷、做回归，以及与公开轨迹做定性对照。 |
| SWE-Atlas-QnA-AA | 仓库问答 | 问题、参考答案和评分条目。[数据集](https://huggingface.co/datasets/ArtificialAnalysis/SWE-Atlas-QnA-AA) | 按固定提交克隆仓库，不使用其 Docker 镜像；沿用原评分条目和评审配置，结果标为 macOS 改编。 | 中 |
| Aider Polyglot | 代码交付（小范围） | 225 道 Exercism 练习，覆盖 C++、Go、Java、JavaScript、Python、Rust，带单元测试；各模型的公开通过率可作参考。[基准说明](https://epoch.ai/benchmarks/aider-polyglot) | 安装固定版本的各语言工具链即可运行。 | 高。适合检查 `apply_patch`、工具调用和失败后自我修正的可靠性，题目小、消耗低。 |
| 小型开源仓库的近期修复 | 代码交付 | 修复提交和测试。 | 纯 Python 或纯 TypeScript、依赖能在 macOS 上直接安装的项目，优先选受测模型知识截止日期之后的修复。 | 中 |
| 本地终端工作流 | 终端 | 维护者编写。 | 文件与数据处理、Git 冲突处理、构建与测试脚本、日志分析、SQLite、Shell 脚本。 | 低 |
| Terminal-Bench 2.1 中不依赖 Linux 的题 | 终端 | 题目、标准解脚本和测试。 | 逐题改写环境并重新校准，标为 macOS 改编，不与官方榜单比较。 | 中 |
| 第 8 节可靠性场景 | 可靠性 | 维护者编写。 | 通过公开协议、进程控制和本地代理注入故障。 | 无 |

使用这些题库时注意三点：

- **代码题的答案是测试，不是参考补丁。** 程序在干净副本中运行隐藏测试判分，不把 Agent 的补丁与标准补丁逐字比较，因为正确的实现可以有多种写法。只有问答题按参考要点和评分条目判分。
- **公开轨迹只做定性对照。** 这些轨迹来自其他模型、其他 harness 和 Linux 环境，适合用来查看同一道题上第一处分歧，不能用来比较分数。
- **高污染题库的分数会偏高。** SWE-bench 和 Aider Polyglot 的结果不作为能力结论。SWE-rebench 每月提供去污染的新题，但环境是 Linux Docker，见第 15 节。[SWE-rebench](https://nebius.com/blog/posts/introducing-swe-rebench)

### 5.2 配额

首批 10 题用来校准流程，建议包含 4 道 Eidos 修复历史题、3 道 SWE-bench Verified 纯 Python 子集题、2 道 SWE-Atlas-QnA-AA 问答题和 1 个可靠性场景。终端工作流从第二批开始加入。首批不承担成熟度判断。

后续扩展到约 40 题，四类各约 10 题。其中保留 10 题用于阶段验收，其余用于日常优化。相同缺陷的变体和同一修复链不能跨组分配。被反复用于调试的保留题转入开发组，由新题替换。

首轮基线中全部通过或全部失败的题目不能为改动对照提供信号。扩展题库时，每类都应有一部分题目的基线通过率落在约 20%～80%，同时保留少量稳定通过的题目用于发现退化（见第 10 节）。

### 5.3 一道任务的材料与校准

| 材料 | 要求 |
| --- | --- |
| 用户请求 | 尽量保留原始措辞，不把修复步骤写进题目。 |
| 初始状态 | 修复前的代码、依赖和必要数据。 |
| 参考结果 | 已验证的修复或目标状态，只用于证明任务有解。 |
| 硬性验收 | 测试或检查器直接检查用户要求的行为和限制。 |
| 诊断标签 | 语言、任务类型、难度、预计涉及模块、环境要求，以及初始状态中的 `AGENTS.md` 等项目规则文件。Runtime 会把工作区内的项目规则读入上下文，维护者不为评测删除或改写这些文件。 |
| 运行协议 | 预算、权限、网络策略、预期 Run 终态、回复规则和故障注入条件。 |
| 版本与来源 | 任务来源、改编说明、授权信息、判分版本和参考修复的日期。 |

每道题入库前必须通过以下校准，每项至少重复两次：

- 未修改的代码因目标缺陷而失败，而不是因缺少依赖、导入错误或无法启动而失败；
- 参考修复通过目标验收和相关回归；
- 明显不完整的解法被判分器拒绝；
- 存在多种合理实现时，至少一种替代实现能够通过，避免测试只接受参考补丁的内部写法；
- 问答题缺少关键要点的回答会失败，可靠性题没有实现目标恢复行为的环境会失败；
- SWE-bench 题在 macOS 上重新校准：原始代码运行 FAIL_TO_PASS 测试应失败，应用标准补丁后 FAIL_TO_PASS 和 PASS_TO_PASS 都应通过。任一条件不满足即判为 `invalid_setup` 并换题。

某道题在多次尝试中始终失败时，先审计任务本身（描述、测试是否过窄或过宽、环境是否有差异），再判断是 Agent 的问题。

### 5.4 配置格式

程序采用严格、带版本的 JSON。公开材料与私有材料分开存放。下面是格式示例，不是已入库的任务。

`eval/tasks/<taskId>/task.json`：

```json
{
  "schemaVersion": 1,
  "taskId": "eidos-history-checkpoint-routing-001",
  "taskVersion": "1",
  "kind": "code_change",
  "source": "eidos_history",
  "comparability": "native_internal",
  "split": "development",
  "promptFile": "prompt.md",
  "fixture": {
    "kind": "git_archive",
    "repository": "eidos",
    "baseCommit": "<父提交>",
    "setup": ["uv sync --locked"]
  },
  "environment": "macos_native",
  "networkPolicy": "none",
  "memoryMode": "off",
  "expectedRunStatus": ["succeeded"],
  "budget": { "agentSeconds": 1200, "maxModelCalls": 60 },
  "interactionPolicy": "fixed_inputs_only",
  "graderId": "pytest-targeted-v1",
  "canary": "<评测专用 canary>"
}
```

私有目录中对应的 `grader.json` 声明隐藏测试文件、判分命令和超时；`replies.json` 声明回复规则，由 Runner 读取，不进入 Agent 工作区。

`eval/agents/<name>.json` 固定受测产品配置：

```json
{
  "schemaVersion": 1,
  "product": "eidos",
  "buildId": "<受测构建>",
  "model": {
    "kind": "custom",
    "providerId": "ArkCodingPlan",
    "baseUrl": "https://ark.cn-beijing.volces.com/api/coding/v3",
    "wireApi": "chat_completions",
    "modelId": "deepseek-v4-1-flash-261017",
    "contextWindowTokens": 1048576,
    "maxOutputTokens": 393216,
    "apiKeyEnv": "ARK_API_KEY"
  },
  "reasoningSelection": "high",
  "approvalMode": "auto_review",
  "approvalResponsePolicy": "reject_escalations",
  "shellEnvironmentPolicy": "all",
  "extensions": { "memory": "off", "webSearch": "off", "plugins": [] }
}
```

```json
{
  "schemaVersion": 1,
  "product": "dsh",
  "dshVersion": "<dsh --version 的输出>",
  "profile": "headless",
  "patchFile": "agents/dsh/ark-flash.patch.yml",
  "permissionMode": "workspace-write",
  "approvalPolicy": "never",
  "environment": { "DSH_TELEMETRY_MODE": "DISABLED", "apiKeyEnv": "ARK_API_KEY" },
  "extraArgs": []
}
```

上面的字段名是格式示例，实际字段由实施者确定。模型参数写在 dsh 的评测补丁里，见第 6.2 节。

`suite.json` 固定任务列表和分组；`batch.json` 固定通道（D、F、V）、受测产品列表、重复次数、预算和执行顺序。程序在运行前生成完整的有效配置，避免默认值变化后无法解释成绩。

## 6. 一次尝试的完整流程

一次尝试对应一个 `trial_id`。程序不能把重新运行的结果覆盖到原记录。

### 6.1 Eidos

1. Runner 读取冻结配置，检查预算，登记尝试。
2. Fixture Manager 创建 trial 根目录：工作区、数据目录、Projectless 根目录、APFS 克隆的 HOME 和 TMPDIR。它不携带其他尝试的历史、记忆或检索缓存。
3. Fixture Manager 准备依赖和初始状态，执行健康检查，单独记录准备耗时。
4. Eidos Adapter 以白名单环境通过 RuntimeClient 启动 Runtime，完成 `initialize` 和 `runtime/health`，调用 `model/create` 写入模型配置。
5. Git 仓库任务调用 `project/create`（`workspaceRoot` 为工作区）和 `session/create`（`projectId`，`executionMode: "local"`）；非仓库任务只调用 `session/create`。
6. Adapter 调用 `run/start`，显式传入 `sessionId`、`userInput`、`modelId`、`reasoningSelection` 和 `approvalMode`，记录返回的 Session、Run 和操作标识。
7. Adapter 持续接收通知，用 `event/list` 的 `afterEventId` 补读遗漏事件并去重。Runner 观察预算、审批、用户输入和进程状态，只按预先声明的规则应答。需要验证运行中状态的任务，同时启动独立观察器保存外部证据。
8. Run 进入终态后，Adapter 根据 `run/completed` 和分页的 `session/read` 收集最终事实，用 `toolCall/readText` 分页读取需要保存的大段工具输出。Runner 保存回答、代码差异（包括新增、删除和未跟踪文件）和测试产物。
9. Adapter 调用 `runtime/shutdown`。Runner 等待资源静止，冻结工作区和数据快照（排除 `models.json`），再用只读导出器从 `state.sqlite` 的备份副本导出用量和轨迹字段。
10. Grader 在干净副本中执行外部验收，私有测试只在这一步进入评分环境。
11. Analyzer 生成判分与失败原因，Reporter 更新批次报告。
12. Runner 清理本次尝试拥有的进程、端口和临时目录，记录清理结果。

程序不能直接调用 `store.create_run`、改写 SQLite 状态或通过 RuntimeEngine 的模型包装器生成正式成绩。这些机制只能用于单独标注的组件实验。

### 6.2 DeepSeek Harness（基线对照）

1. 步骤 1～3 与 Eidos 相同，使用同一任务卡、同一 fixture 和同一预算。
2. dsh Adapter 在 trial 根目录克隆黄金 `DSH_HOME`。
3. Adapter 以工作区为 cwd、以白名单环境（`PATH`、`HOME`、`TMPDIR`、`DSH_HOME`、`DSH_TELEMETRY_MODE`、`ARK_API_KEY` 等必要变量）执行 `dsh --profile headless --patch <评测补丁> --json --permission-mode workspace-write --approval-policy never <提示词>`。实际参数以实施时 CLI 帮助的输出为准。
4. 评测补丁包含三部分：
   - `llm-pi-ai` 定义 provider `ark`：`api` 为 `openai-completions`，`baseURL` 为第 4.2 节的端点，`apiKeyEnv` 为 `ARK_API_KEY`；模型参数与第 4.2 节一致，包括 `reasoningEfforts` 映射（off、low、high、max）、`compat.supportsReasoningEffort: true` 和 `compat.maxTokensField: max_tokens`，`input` 只有 `text`。
   - `agent-default-model` 选择 provider `ark` 和该模型，`reasoningEffort` 为 `high`。
   - `tool-web` 的 `search` 和 `fetch` 都设为 false。

   字段以 `dsh --profile headless --dump-config-schema` 为准，并用 `--dump-config` 检查合成结果。用户在 dsh 界面中可能把同一模型配置为 Responses API，评测补丁统一改为 Chat Completions，与 Eidos 一致。两边都使用 Responses 的组合以后单独作为一组配置运行。
5. Adapter 保存 stdout 的 NDJSON 事件、stderr 和最终回答。预算到期时先发送中断信号，等待一段有界的清理时间，再终止进程组。
6. 后续的产物收集、判分、分析和清理与 Eidos 相同。

dsh 的 agent-instructions 插件负责读取项目说明文件。实施时确认它读取哪些文件名并记录；双方都保留读取项目规则的默认行为。dsh 在 headless 模式下遇到需要扩权的操作时会怎样（拒绝、报错还是挂起），在首次真实运行时实测并记录；如果会挂起，由 Runner 按预算超时处理。

### 6.3 超时、崩溃与断点继续

预算达到时，Eidos Adapter 先调用 `run/cancel` 并保留一段有界的清理时间；正常关闭失败时，Runner 才终止本次尝试拥有的进程树，并保留未知结果。这个机制不改变生产 Runtime 的自动恢复规则。

模型服务不可用时，Runtime 保持可取消的恢复循环，Run 不会自行失败，这类尝试通常以预算耗尽结束。程序保存期间的模型错误码和重试记录，供第 9.2 节的批次级服务异常判定使用。

Runner 中断后，继续批次只调度尚未启动的任务。对于已经启动但没有结束证据的尝试，程序先读取状态和收集产物，不自动重新执行可能已产生副作用的任务；需要新尝试时分配新编号。

恢复场景在同一次尝试中保留数据目录并重启 Runtime，普通任务每次尝试重新创建数据目录。Runtime 启动时会收敛未完成的 Run：没有未完成执行或未决副作用的活动 Run 重新排队，失去执行结果的调用记为 `interrupted`，系统不会自动重放。Desktop 界面目前不调用 `run/resume`，使用它的场景单独标注。

## 7. 判分设计

### 7.1 三种判断

- `outcome_pass`：最终答案或产物满足任务目标。
- `control_pass`：Run 生命周期、权限和资源行为满足该任务的明确要求。
- `delivery_pass`：前两项和完整性检查都通过，且没有接受任务规则之外的人工纠正。

每个任务卡声明预期的 Run 终态。取消场景可以把 `canceled` 作为正确状态，普通交付任务检查正常完成和资源清理。Agent 修好了代码但 Runtime 崩溃时，报告显示产物通过、完整交付失败；Agent 正常结束但外部测试失败时，报告显示任务未通过。

产品对照只比较 `outcome_pass`。dsh 的 `control_pass` 只检查进程正常退出和资源清理，因为 Eidos 特有的生命周期事实在 dsh 中没有对应项。

### 7.2 不同任务的判分器

| 类型 | 必要判分 | 可补充诊断 |
| --- | --- | --- |
| 代码修改 | 干净副本中的目标测试、相关回归、必要的构建和修改范围约束。 | 人工或模型检查设计、可读性和无关修改。 |
| 终端工作流 | 外部检查文件内容、数据结果、命令输出和进程状态。 | 重复调用、失败修正和工具等待。 |
| 仓库问答 | 按冻结的条目逐项判定答案。 | 引用是否存在；声称的运行是否有对应的工具证据。 |
| 产品可靠性 | 受影响的状态、资源和副作用次数。 | 恢复耗时，以及上下文是否保留目标。 |

一般代码任务不要求固定的工具调用序列，只检查结果。Agent 写入的测试可以作为产物，但不能替代外部测试。Grader 检查测试是否被删改，并使用评分环境拥有的版本执行验收，不以 Agent 声称的“测试全部通过”作为结果。

### 7.3 模型评审

本地任务优先使用程序可验证的结果，模型评审只用于开放式问答和必要的软性质量检查。每条评审输出包含判断、理由和对应证据，评审器不执行回答中的指令，也不接收产品名称和历史得分。缺少证据或无法完成投票的条目保留为未评分。

评审模型优先使用与受测模型不同的模型族。首批任务人工核对全部模型判分；后续批次至少复核新增失败、改动前后不一致项和评审分歧项，并抽查通过项。维护者用已知正确、缺少关键要点和有明显错误的样本校准评审器。SWE-Atlas-QnA-AA 的原规则（五票多数、全部条目满足）与本地附加检查分开保存。[评审配置](https://huggingface.co/datasets/ArtificialAnalysis/SWE-Atlas-QnA-AA/blob/main/rubric_evaluation_config.yaml)

### 7.4 完整性

Grader 检查测试文件和评分输出是否被操纵、Agent 是否读取参考解法、是否从外部获取题目答案。程序根据实际证据判定违规，证据不足时交给人工复核并保留未确认状态。扫描器没有发现问题，不等于已经证明所有访问都合法。

## 8. 首批可靠性场景

以下 10 项是候选类别。维护者需要为每项补齐可复现任务、故障触发点和外部验收条件后，才能加入正式任务集。“验收层级”说明外部观察器能否可靠触发故障。组件层场景的故障窗口无法从外部可靠卡住，继续由 Runtime 集成测试覆盖（`AGENTS.md` 第 27 节），评测报告引用对应测试，不另造产品开关。

| 场景 | 验收层级 | 主要验收事实 |
| --- | --- | --- |
| 取消与迟到结果竞争 | 产品层验收取消后的状态和进程清理；精确竞态属于组件层。 | 取消状态不被迟到结果覆盖，受管进程按规则清理。 |
| 审批等待期间重启 | 产品层：在 `waiting_approval` 时重启。 | 待审批操作保持未执行，原审批与操作范围仍然匹配。 |
| 副作用后、结果提交前重启 | 组件层。 | 系统保留真实或未知结果，不自动重复执行。 |
| 模型流中断、限流和服务恢复 | 产品层：通过本地故障注入代理触发。 | Runtime 保持可取消，恢复后没有丢失目标或重复副作用。 |
| 文件被外部修改 | 产品层：观察器在读取事件之后修改文件。 | 系统发现版本冲突，模型可以重新读取并修正。 |
| 后台 Shell 生命周期 | 产品层。 | 后台进程在 Run 内可用，Run 收尾后相应进程被清理。 |
| 长任务发生上下文压缩 | 产品层：用 `context/compact` 在明确时点触发。 | 最终产物仍满足原目标、用户决定和已完成的验证要求。 |
| MCP 或浏览器进程退出 | 产品层。 | 错误被反馈，后续没有误用旧连接或旧标签身份。 |
| 子 Agent 等待时收到消息 | 产品层：Runner 在 `waiting_agents` 时发送排队消息。 | 父任务能处理新输入，最终汇总与实际子任务结果一致。 |
| 跨任务的记忆与资源隔离 | 产品层。 | 普通任务不读取上一题的答案，专项记忆场景按授权使用记忆。 |

故障注入优先使用可控的测试服务、进程控制和持久事件作为触发条件，不按固定睡眠时间猜测故障窗口，也不往生产 Runtime 添加隐藏开关。模型流中断和限流使用本地故障注入代理：自定义 Provider 的接口地址指向本地代理，代理转发给真实 Provider，并按脚本注入断流、429 或延迟。相关结果只与同样使用代理的批次比较，不进入默认基线。

## 9. 数据、指标和统计

### 9.1 每次尝试的证据

```text
manifest.json              固定配置、版本和状态
protocol.jsonl             脱敏后的请求、响应和通知（Eidos）
dsh-events.jsonl           dsh 的 NDJSON 事件（DeepSeek Harness）
stderr.jsonl               本地诊断
events.jsonl               持久事件导出（Eidos）
answer.md                  最终回答
artifacts/                 输出、新文件和必要截图
workspace.patch            代码差异，包含新增、删除和未跟踪文件
checks.json                每项检查的结果与证据位置
judge.jsonl                评审条目、投票和证据
observations.jsonl         运行期外部观察与故障注入记录
trajectory.json            模型调用、重试、审批和工具调用的诊断指标
result.json                标准化结果
analysis.json              原因分类与确认程度
```

程序为文件数量、日志和产物设置可配置的采集上限。达到上限时记录缺失或截断情况，不把截断后的记录标为完整轨迹。

Eidos 的完整用量和轨迹字段无法全部从协议获得时，程序在受测进程退出后用 SQLite backup API 复制 `state.sqlite`，再用版本明确的只读导出器读取。导出器不写业务表，也不直接复制仍在写入的数据库文件。用量来源如下，无法覆盖的类别记为缺失，不能填零：

| 调用类别 | 当前来源 |
| --- | --- |
| 主循环模型调用 | `model_attempts.usage_json`，只覆盖属于某个 Step 的调用。 |
| 自动审查 | `approvals.review_json` 中的 token 字段。 |
| 上下文压缩与记忆 | 各自模块的用量记录，实施时逐字段核对。 |
| 标题生成等其他辅助调用 | 来源待核对。 |

dsh 的用量从其 NDJSON 事件中读取，字段以实施时的实际输出为准。

日志不能保存 API Key、Authorization Header、完整环境变量或用户日常敏感文件。程序只记录实际可获得的模型输出。

### 9.2 成绩与分母

| 状态 | 处理规则 |
| --- | --- |
| `pass` | 该次尝试满足已定义验收。 |
| `fail` | Agent 或受测产品已经运行，但没有完成验收，包括非预期崩溃和预算耗尽。 |
| `invalid_setup` | Agent 启动前已证明任务环境或参考验收有问题。公开列出并修复，不算作能力失败。 |
| `unscored` | 判分器故障或证据不足，暂时无法判断。列出数量与原因，不当作通过。 |
| `not_run` | 批次因预算或人工停止尚未执行，保留原始计划数量。 |

Provider 在运行期间失败属于实际交付结果，报告可以分出服务失败原因，但不能静默删掉这些尝试。维护者在运行前声明批次级服务异常的判定条件（例如某时间窗内服务错误比例超过阈值），满足条件的批次保留全部尝试并标记“服务异常”，事后不删除尝试，也不调整阈值。

按额度计费的服务在额度或限流窗口用完后会持续返回错误。Runner 识别到这类错误时暂停批次，等额度恢复后只调度尚未启动的任务；已经启动的尝试按实际结果记录，并标注额度原因，不计为 Agent 能力失败。

日常开发批次每题运行 1 次；里程碑和正式对照每题重复三次。每题通过率 = 该题通过次数 ÷ 该题尝试次数；某类通过率 = 该类各题通过率之和 ÷ 题目数。10 题各跑 3 次仍是 10 个任务、30 次尝试。程序同时报告 pass@k（k 次中至少一次通过的任务比例）和 pass^k（k 次全部通过的任务比例）。有未评分项时，报告同时给出已评分结果和上下界，并标明是未完成报告。

### 9.3 必报指标

| 指标 | 定义与用途 |
| --- | --- |
| 目标完成率 | 按任务等权汇总 `outcome_pass`。 |
| 独立交付率 | 按任务等权汇总 `delivery_pass`。 |
| 可靠性通过率 | 单独汇总可靠性场景。 |
| 错误完成声明率 | 外部验收失败但 Agent 明确宣布完成的尝试数 ÷ 明确宣布完成的尝试数，同时报告原始计数。 |
| 每解决一题的成本 | 全部尝试（含失败）的总费用 ÷ 通过的任务数，Credits 与美元分开保存。 |
| 缓存命中率 | `cache_read_tokens` ÷ 输入 token，按尝试和批次汇总，用于发现前缀缓存在运行中失效。 |
| 耗时 | 分别记录环境准备、Agent 活跃、等待人工、评分和清理时间，报告中位数、P90 和超时次数。 |
| 审批 | 审批请求数、自动审查拒绝数、转人工数、被人工门槛阻断的尝试数和等待时间。 |
| 恢复与清理 | 恢复耗时、残留进程和重复副作用。 |
| 轨迹诊断 | 模型调用次数、重试与错误码、首 token 延迟、工具调用数、工具错误率、重复的相同调用和压缩次数。只用于定位原因，不单独作为成绩。 |
| 波动 | 每题三次结果、pass@3 与 pass^3，稳定通过、稳定失败和不稳定的任务。 |

每个里程碑至少做一次**预算曲线**：同一批任务分别在约 10、20、40 分钟的预算下运行，区分 Eidos 是“慢”还是“错”。Terminal-Bench 的数据显示，同一模型的 harness 差距在放宽时间后大部分收回。

### 9.4 题量与可检出的变化

改动前后对照按任务配对，对每题两边通过率之差做任务级 bootstrap 重采样，估计总体差值的置信区间。区间跨过零时，报告写“未观察到可确认的变化”，不写“没有影响”。

粗略估计（假设每题差值的标准差约 0.3～0.4）：10 题只能可靠检出约 30 个百分点以上的变化，40 题约 15 个百分点。因此首批 10 题只用于校准流程和阅读失败记录，不用来判断某次提示词改动的效果。细小改动用针对该机制的任务子集验证。维护者在运行前声明希望检出的最小效应，据此判断题量是否足够。

## 10. 运行分层

| 层 | 规模 | 何时运行 | 用途 |
| --- | --- | --- | --- |
| 冒烟回归 | 5～8 道基线中稳定通过（pass^3 = 1）的题，每题 1 次，可用较便宜的模型 | 修改系统提示词、上下文构建、工具契约或模型适配的改动合并前 | 发现明显退化，目标接近 100% |
| 开发批次 | 20～40 题，每题 1 次；结论不稳定的题加跑到 3 次 | 每次单方向优化 | 改动前后对照 |
| 里程碑 | 保留集，每题 3 次，加上预算曲线；大版本对比时重新运行外部对照 | 阶段验收 | 判断阶段目标 |

冒烟回归属于回归评测，通过率应接近 100%；开发批次中的题目属于能力评测，通过率应有提升空间。能力题在稳定通过后可以转入冒烟回归。三层都不接入 `pnpm test`，也不改变仓库“只有用户明确要求才运行 Full Suite”的规则。

## 11. 基线、对照与消融

### 11.1 对照顺序

1. 固定 Eidos 构建、本期受测模型、任务与判分器，建立初始基线。
2. 在首个基线时，用同一模型和同一任务把首批任务在 dsh 上各运行一次，形成一次性的产品对照。结果保存后供后续批次引用，日常优化不再运行。
3. 每次只改动一个产品方向，保持模型和其余配置不变，比较改动前后。
4. 另开批次比较不同模型，保留相同的 Eidos 构建。
5. 需要时增加 Codex CLI（OpenAI 模型）或 Claude Code（Anthropic 模型）对照。

SWE-bench 题不需要另外付费运行对照：[swe-bench/experiments](https://github.com/swe-bench/experiments) 中其他系统的逐题轨迹可以直接用于查找第一处分歧。

### 11.2 产品对照的可比条件

- 相同的端点、接口（Chat Completions）、模型标识和思考强度；相同的提示词原文、fixture、预算和判分器。
- **请求一致性检查。** 首次真实运行时，两边各让一次请求经过只在本机监听的记录代理（不记录 Authorization 头），对比 `model`、`reasoning_effort`、`max_tokens`、`stream`、`temperature` 和工具数量。不一致时先修正配置，再运行对照。代理只用于这次检查，正式尝试直连端点。
- 双方都读取工作区内的项目规则文件，都不联网（dsh 关闭网页搜索和抓取），都只写工作区。
- Eidos 使用 `auto_review` 并拒绝转交的审批，dsh 使用 `workspace-write` 和 `never`。任务设计成不需要扩权，程序记录双方的扩权或沙箱拒绝次数。
- dsh 自带的系统提示词、工具、子 Agent、团队和上下文策略属于被测 harness 的一部分，不做调整。
- 无法对齐的默认值（例如 dsh 的 Shell 默认超时、Eidos 的仓库索引）写入报告的比较边界，不去拉平。

### 11.3 机制消融

Eidos 有几套复杂机制，目前没有数据证明它们对任务完成率的贡献。逐个关闭后运行同一批任务，可以决定保留、简化还是删除。

| 机制 | 切换方式 |
| --- | --- |
| 记忆读取与学习、网页搜索、MCP 与插件 | 现有设置。 |
| 审批模式（`auto_review` 与 `manual`）、思考强度、Shell 环境继承策略 | 现有的 `run/start` 参数和设置。 |
| 仓库索引注入、模型辅助压缩、子 Agent 工具、并行只读工具 | 本次核对没有确认用户可用的开关。消融前需要单独设计开关方式，不能向生产 Runtime 添加只为评测存在的隐藏开关。 |

消融结果按第 9.4 节判断是否可确认。只有在通过率不下降的前提下，费用或耗时的改善才算收益。

### 11.4 执行顺序

程序随机或交错安排新旧构建、不同产品的尝试顺序，降低服务波动和缓存带来的顺序影响。对照报告列出“旧失败新通过”“旧通过新失败”“两边都失败”和“不稳定”四组任务。统计区间按任务分组估计，不能把同一题的三次尝试当成三个独立任务。

## 12. 从评测结果形成优化方向

Analyzer 为每次失败记录：第一个已确认偏差及其事件编号、相关证据、最终影响、主要原因、次要原因、确认程度和建议的验证实验。原因使用封闭的原因码：

| 原因码 | 含义 |
| --- | --- |
| `env_setup` | Agent 运行后才发现的任务环境或依赖问题。 |
| `task_misread` | Agent 误解了用户目标或限制。 |
| `retrieval_miss` | Agent 没有找到相关代码或信息。 |
| `wrong_hypothesis` | Agent 找到了相关代码，但判断错误。 |
| `edit_error` | 方向正确，但修改本身有误。 |
| `tool_misuse` | 工具参数、调用方式或错误处理不当。 |
| `verification_gap` | Agent 没有执行或误读了必要验证。 |
| `context_loss` | 压缩或长上下文后丢失目标、决定或事实。 |
| `approval_block` | 被人工门槛阻断，或权限等待耗尽预算。 |
| `auto_review_false_reject` | 自动审查拒绝了任务所需的普通操作。 |
| `runtime_defect` | Eidos Runtime 的缺陷导致失败。 |
| `provider_error` | 模型服务错误或长时间不可用。 |
| `budget_exhausted` | 没有其他已确认原因时的预算耗尽。 |
| `grader_issue` | 判分器缺陷；修正后重新评分，并保留原结果。 |

“错误完成声明”在第 9.3 节单独统计，不作为原因码。原因码扩充时升级分类版本，旧批次保留原版本。

| 观察到的问题 | 优先检查 | 验证实验 |
| --- | --- | --- |
| 长时间找不到相关代码 | 检索结果、排序、读取范围和上下文占用。 | 同模型比较检索改动前后的完成率。 |
| 找到了代码但理解错误 | 依赖追踪、运行观察和上下文组织。 | 固定工具与环境，分别比较模型和证据组织。 |
| 反复调用失败的工具 | 错误可见性、参数契约、输出分页和替代路径。 | 相同失败条件下，检查能否修正并交付。 |
| 改动正确但验证不足 | 验收目标、命令结果和收尾判断。 | 加入外部失败条件，检查错误完成声明是否减少。 |
| 压缩后忘记要求 | 压缩前后的目标、决定、验证结果和副作用事实。 | 重复同一长任务，比较保留内容与最终产物。 |
| 因权限等待无法完成 | 实际操作、授权证据、审批理由和人工时间。 | 固定权限模式比较审批改动，另查越权行为。 |
| 与外部 harness 同模型差距明显 | 两边在同一题上的第一处分歧；SWE-bench 题另外对照公开轨迹。 | 对分歧点做单方向改动，再跑同一子集。 |
| 正确性相同但费用或耗时上升 | 重复检索、上下文长度、缓存命中率和审批调用。 | 在通过率不下降的条件下比较耗时与总费用。 |

程序不能仅凭一次失败就把原因归给模型或提示词。诊断性重跑另存为实验，不回填到原基线。维护者先处理数据、权限和重复副作用等严重问题，再处理高频交付阻断，最后处理正确性不变时的费用和交互优化。每个优化事项包含受影响任务、证据、预期收益、最小改动和对应回归。

## 13. 报告与命令设计

报告至少包含：受测配置、计划范围、实际覆盖和环境差异；四类任务的完成率、交付率、耗时和费用；逐题矩阵（可打开失败尝试的检查结果和工具时间线）；已确认问题、待验证原因和前三个优化建议；对照报告中的新增通过与新增失败；未运行、未评分、数据缺失和未覆盖的产品部分。

命令设计如下，实施前都不存在：

```text
eidos-eval doctor   --lane D|F|V
eidos-eval import   --source eidos-history|swe-bench-verified|swe-atlas-qna --ids <题目列表>
eidos-eval validate --suite suites/seed-10.json
eidos-eval plan     --suite suites/seed-10.json --agent agents/eidos-baseline.json --repeat 1 --lane D
eidos-eval plan     --suite suites/seed-10.json --agent agents/eidos-baseline.json --agent agents/dsh-baseline.json --repeat 3 --lane F
eidos-eval run      --plan plans/<planId>.json
eidos-eval grade    --batch <batchId>
eidos-eval analyze  --batch <batchId>
eidos-eval report   --batch <batchId>
eidos-eval compare  --baseline <batchId> --candidate <batchId>
```

`doctor` 检查机器、工具链、黄金 HOME、通道隔离和驱动能力；`validate` 执行第 5.3 节的校准；`plan` 冻结配置并输出消耗上限；`run` 执行任务；`grade` 独立评分，可以在不调用 Agent 的情况下重新执行，新评分保留版本和旧结果。继续批次时只调度未启动的任务，不提供覆盖原失败结果的重试入口。程序默认只保存本地报告，不上传对话、源码或评测结果。

## 14. 实施阶段与验收

| 阶段 | 要完成的工作 | 交付物 | 进入下一阶段的条件 |
| --- | --- | --- | --- |
| 零：端到端竖切（只跑 Eidos） | 在 D 通道准备 2 道题：1 道 Eidos 修复历史题、1 道在 macOS 上校准通过的 SWE-bench Verified 纯 Python 题。先用 `EIDOS_FAKE_MODEL` 跑通 Eidos Adapter，再用本期受测模型各运行一次，按已有答案判分，生成报告。 | 2 次尝试的完整证据目录、`result.json` 和报告；单题 token 消耗；凭据脱敏和 HOME 隔离的检查记录；评测程序自测。 | 凭据注入、审批应答、取消、快照脱敏、外部判分和报告在真实链路上可用；单题消耗已知，可以据此确定批次规模。 |
| 一：首批任务与正式通道 | 搭建 F 通道（`eidoseval` 用户、受测构建目录、黄金 HOME、探针检查）。按第 5.2 节选定首批 10 题并完成校准。 | 任务卡、私有判分材料、校准记录、通道搭建记录。 | 每题的未修改状态、参考结果和不完整解法都能被正确区分；探针检查通过。 |
| 二：最小评测程序 | 完成 `plan`、`run`、`grade`、`report`、`compare`，以及预算控制、额度暂停、证据采集、确定性判分和批次续跑。 | 可执行 CLI 和 `eval/README.md`。 | 一道真实任务通过生产 stdio 链路运行并完成外部判分；取消、中断和重新评分都可验证。 |
| 三：首个基线与一次性对照 | 在 F 通道用固定 Eidos 和本期受测模型运行 10 题 × 3 次。实现 dsh Adapter，完成请求一致性检查后，在 dsh 上把同样 10 题各运行 1 次。SWE-bench 题另外对照公开轨迹。阅读全部失败记录。 | 40 次尝试的证据、逐题矩阵、产品差距报告和前三项优化建议。 | 报告能解释每个失败；未评分项已处理或明确列出；没有用重跑替换失败。 |
| 四：扩展、分层与消融 | 扩展到 20～40 题并设立保留集；建立冒烟回归层；完成 2～3 个机制消融和一个模型对照；做一次预算曲线。 | 分项基线、冒烟回归清单、消融报告和优化清单。 | 结果能显示收益、退化与不稳定项，维护者据此选择下一项优化。 |
| 五：Desktop 验收 | 补充少量 Desktop 端到端案例（审批交互、长任务展示、取消）。 | Desktop 证据和验收记录。 | Desktop 结果与 Runtime 结果分开报告。 |

阶段零先暴露凭据、HOME 状态、审批应答这类只有真实运行才会出现的问题。阶段零完成后，阶段一的题库校准与阶段二的程序完善可以并行。工期和费用在阶段零的单题运行完成后估算。

第一版不要求评分仪表盘、云调度、多机器管理或自动替代人工判断。每次产品优化先运行直接受影响的评测任务，阶段性评估再运行约定的完整批次。

## 15. 本期不做

- **Linux 容器、Docker 任务环境和公开原环境（如 Harbor 驱动的 Terminal-Bench、SWE-bench 系列）。** 当前 Runtime 不能在 Linux 上运行：[`workspace/search_driver.py`](../../runtime/eidos_runtime/workspace/search_driver.py) 的 ripgrep 产物只有 `darwin-arm64` 和 `win32-x64`，其他平台返回 `search_backend_unavailable`；Linux 没有 Seatbelt，受限 Shell 不可用；Runtime 的 CI 只在 macOS 上运行。将来接入有两条路线：把 Runtime 装进任务容器并在 `full_access` 下运行（由容器负责隔离），或保留宿主机 Runtime 并为所有工具实现远程执行后端。两条路线都需要单独的架构决策，成绩也要与 macOS 产品成绩分开报告。[Harbor](https://github.com/harbor-framework/harbor)
- **Windows 赛道。** Windows 使用不同的沙箱实现和路径规则，后续作为独立赛道建设，成绩不与 macOS 合并。
- **综合指数命名。** Artificial Analysis 的综合指数包含未公开的 FrontierCode，公开子集或改编题的结果不能命名为该指数。[AA 方法说明](https://artificialanalysis.ai/zh/methodology/coding-agents-benchmarking)

## 16. 实施者约束

- 不为评测修改 Runtime 的行为、协议或 Schema。确有需要时，另做设计并按仓库规则同步测试和 `docs/current/`。
- 复用编译后的 RuntimeClient 和 `desktop/shared` 契约，不另写协议客户端或 DTO。
- 正式成绩只能通过生产 stdio 链路产生，不直接调用 RuntimeEngine、`store.create_run` 或改写 SQLite。
- 不向生产 Runtime 添加只为评测存在的隐藏开关。
- 非 `full_access` 批次不构造完全访问确认字段；`full_access` 只在 V 通道使用。
- API Key 不进入证据、日志和报告；`models.json` 在快照中排除。
- 不读取、复制或修改用户的 `~/.dsh` 和日常数据目录 `~/.eidos`。
- 评测程序不进入 `pnpm test`，不进入发布包，不向 Runtime 增加依赖。
- 评测程序自身的回归使用 `EIDOS_FAKE_MODEL`，覆盖预算取消、批次续跑、事件去重、审批应答和快照脱敏。
- 实现后在 `docs/development/README.md` 补充运行说明，把真实批次的结果记录到 `docs/evaluations/`。
- 遵循 `AGENTS.md` 第 25、26 节：运行与改动直接相关的定向测试，不默认运行 Full Suite。

## 17. 维护者操作顺序

1. 选定固定的 Eidos 构建和第 4.2 节的本期受测模型，记录权限、审批应答策略和扩展配置。
2. 实施者完成阶段零竖切，确认凭据注入、审批应答、取消、快照脱敏、判分和报告在真实链路上可用，并量出单题消耗。
3. 搭建 F 通道并完成探针检查。
4. 按第 5.2 节整理首批 10 张任务卡，不急于填满 40 题。
5. 验证参考结果、未修改结果和不完整结果，修复无效判分。
6. 实施者完成最小 CLI，并用首批任务完成真实端到端验证。
7. 生成正式执行计划，检查尝试数量和额度是否够用，再启动 Eidos 批次；首个基线时另外运行一次 dsh 对照。
8. 完成独立评分，核对全部首批模型判分，阅读每个失败的第一处偏差。
9. 选择一项证据最充分、影响较大的优化，冻结其余配置后实现，按相同规则比较改动前后。
10. 把已确认问题转成最小产品回归，再逐步扩大任务库和冒烟回归层。

## 18. 核对范围与参考

本文档的三次修改都只涉及文档，没有实现评测程序，没有运行真实模型，没有运行 Full Suite，也没有产生评测成绩。

2026-11-28 修订核对了以下事实：

- 协议方法与参数：[协议方法定义](../../runtime/eidos_runtime/protocol/methods.py)（`project/create`、`session/create`、`run/start`、`model/create`）、[协议服务](../../runtime/eidos_runtime/protocol/server.py)（`EIDOS_FAKE_MODEL` 的脚本列表）、[RuntimeClient](../../desktop/main/runtime-client.ts)（构造参数与审批应答格式）。
- HOME 与 Shell：[受管资源路径](../../runtime/eidos_runtime/resource_paths.py)、[Projectless 根目录](../../runtime/eidos_runtime/workspace/projectless.py)、[插件](../../runtime/eidos_runtime/extensions/plugins.py)、[宿主 Shell](../../runtime/eidos_runtime/sandbox/host_shell.py)。
- Seatbelt 读取范围与网络规则：[Shell 基础策略](../../runtime/eidos_runtime/sandbox/seatbelt.sbpl)、[策略编译](../../runtime/eidos_runtime/sandbox/seatbelt_policy.py)。
- Linux 限制：[搜索驱动](../../runtime/eidos_runtime/workspace/search_driver.py)、[CI 配置](../../.github/workflows/ci.yml)。
- 用量字段：[模型客户端](../../runtime/eidos_runtime/model/client.py)、[SQLite Schema](../../runtime/eidos_runtime/db/schema.py)、[数据库布局](../../runtime/eidos_runtime/db/layout.py)。
- 本机 Codex CLI 0.166.0 的 `codex exec --help` 输出。
- 本机 DeepSeek Harness 0.2.2-rc.5 的 `dsh --help`、`dsh --profile headless --help`、`--dump-default-config` 和 `--dump-config-schema` 输出（provider、模型、权限、遥测和网页工具的配置字段）。
- 用户日常 Eidos 配置中该 Ark 模型的非凭据参数（读取时已隐去 API Key）。

评测方法参考：

- [Anthropic：Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)（2026-01-09）：从 20～50 道来自真实失败的任务开始；每题需要参考解；优先确定性判分；区分能力评测与回归评测；pass@k 与 pass^k；每次尝试从干净环境开始；先读失败记录再相信分数。
- [OpenAI：Why we no longer evaluate SWE-bench Verified](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)（2026-02-23）与 [Separating signal from noise in coding evaluations](https://openai.com/index/separating-signal-from-noise-coding-evaluations)（2026-07-08）：公开题的测试缺陷与污染、任务审计方法、canary 字符串和私有任务。
- [Benchmarks as Software: Terminal-Bench and the Case for Continuous Benchmarks](https://arxiv.org/abs/2611.08417)（2026-11）：基准需要持续维护和版本化；同一模型的 harness 差距及时间预算的影响。
