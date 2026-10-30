> 本文记录调研时的本机证据与实施前建议。Eidos 已按后续任务实现受管资源目录与操作配置，当前行为见[功能说明](../current/features/document-skills.md)。

# Codex 桌面端资源位置、准备时机与 Eidos 目录建议

调研日期：2026-10-30。本文是本机观察与设计建议，不表示 Eidos 已实现下载资源管理。

## 1. 调研范围与证据

本机的 Codex 界面由 `/Applications/ChatGPT.app` 提供。该 App 的 `CFBundleShortVersionString` 为 `26.1023.51421`，`CFBundleVersion` 为 `22530`。本文核对了这个已安装版本的应用代码、本地 Runtime 元数据、主进程日志和 OpenAI 官方文档。本文不把这些结果推广为全部平台、历史版本或账号的固定行为。

本轮没有清空 Codex 缓存，没有触发重装，没有下载大型资源包，也没有修改 Codex 配置。文件时间只作为辅助信息，安装触发时机主要依据日志和调用链。

来源入口：

- [官方 Skill 说明](https://developers.openai.com/codex/skills)：Skill 的内容、发现与按需加载规则。
- [官方权限与沙盒说明](https://learn.chatgpt.com/docs/agent-approvals-security)：本地操作系统沙盒、审批与网络边界。该页不说明文档 Runtime 的具体安装目录或安装时机。
- 本地应用代码：`/Applications/ChatGPT.app/Contents/Resources/app.asar`。本轮只提取相关模块到仓库忽略的 `.cache/codex-runtime-research/` 供分析，没有将第三方应用代码加入仓库。
- [本机 Runtime 元数据](/Users/xielei/.cache/codex-runtimes/codex-primary-runtime/runtime.json)。
- [2026-10-26 主进程日志](/Users/xielei/Library/Logs/com.openai.codex/2026/10/26/codex-desktop-dc000669-c85b-47c6-aadb-9f3a3b5fc350-85889-t0-i1-015323-0.log)：第 49、570、651—655 行包含安装触发、结果和解释器执行检查。
- `load_workspace_dependencies` 工具的本轮实际返回：Bundle 版本 `26.1025.11240`，并返回 Python、Node、包目录和辅助工具的绝对路径。

## 2. Codex 把资源放在哪里

本机的文档工作区资源根是：

```text
~/.cache/codex-runtimes/
└── codex-primary-runtime/
    ├── runtime.json
    ├── dependencies/
    │   ├── python/
    │   │   └── bin/python3
    │   ├── node/
    │   │   ├── bin/node
    │   │   └── node_modules/
    │   ├── native/
    │   │   ├── libreoffice-headless/
    │   │   ├── poppler/
    │   │   ├── libheif/
    │   │   ├── jxrlib/
    │   │   └── git/
    │   └── bin/
    │       ├── override/
    │       └── fallback/
    └── plugins/openai-primary-runtime/
```

这个 Runtime 的插件目录包含 documents、pdf、presentations、spreadsheets 等 Skill 内容。它与用户自行安装的 Skill 目录是不同来源。当前 `runtime.json` 声明了 bundled plugin，`bundledSkills` 数组为空，不能据此判断没有文档 Skill。

本机元数据声明 Python `3.12.14`、Node `24.22.1-cua.1`、Bundle `26.1025.11240`，并记录平台 `darwin`、架构 `arm64`。Skill 内容、解释器、语言包和原生工具分别存放，产品通过元数据组织它们。

应用自身仍有随安装包提供的资源。例如，Codex CLI 位于 App 的 `Resources/codex-cli/`，另有 `Resources/cua_node/`。不能因为文档 Runtime 在用户目录，就断言应用没有随包 Node 或其他运行资源。这两类资源有不同用途。

## 3. Codex 何时准备资源

本机存在直接安装记录，时间采用日志原有的 UTC：

| 时间 | 日志事实 | 可以得出的结论 |
| --- | --- | --- |
| 2026-10-26 01:53:29.717Z | 安装开始，`trigger=startup_missing`，目标 `darwin-arm64` | 这次安装由启动阶段的缺失检查触发 |
| 2026-10-26 01:54:43.136Z | Bundle 安装结果为 `installed`，版本 `26.1025.11240` | 下载、解包和激活流程完成 |
| 2026-10-26 01:54:48.707Z | 缓存目录内 Node 执行检查成功 | 产品检查了受管 Node 的实际可执行性 |
| 2026-10-26 01:54:49.523Z | 缓存目录内 Python 执行检查成功；完整安装调用结束 | 产品检查了受管 Python，并完成后续配置 |

当前应用代码与这些日志一致：

1. Main 的 Primary Runtime 服务启动更新检查。这个入口受本地 Host、资源配置和 workspace dependencies 功能开关约束。
2. 产品读取目标平台的资源配置，检查本地状态是 missing、outdated、current 或 unsupported。首次检查发现缺失或旧版本时，产品可以在启动后安装；后续也有更新和手动安装入口。
3. 安装器优先复用匹配版本的现有资源。需要安装时，它在 `codex-runtime-install-*` 暂存目录下载归档，验证归档、检查解压路径并验证解压后的必要文件。
4. 产品将准备好的目录切换到 `codex-primary-runtime`，失败时有恢复旧目录的处理。安装完成后还会同步 bundled plugins 或 Skill 内容并重新加载 Skill。
5. Main 广播准备进度。它没有在 App 主启动链中同步等待下载结束。本文没有在空缓存、离线和失败条件下实测该版本的全部界面行为。

`load_workspace_dependencies` 的调用链是诊断现有 Runtime 并返回路径。它没有调用安装器。资源缺失时，它返回不可用；资源已经准备好时，它返回解释器与包目录。因此，“模型在首次使用 Skill 时调用这个工具”不等于“工具在这时首次下载依赖”。

官方 Skill 文档所说的 progressive disclosure 是按需读取 Skill 指令，不能把它理解为 Python、Node 和原生引擎也一定按 Skill 分别下载。本机观察到的是共享文档 Runtime 的准备与复用。

## 4. Eidos 的统一目录建议

建议采用用户提出的 `~/Eidos/Resource/`，统一保存 Eidos 下载和管理的文档运行资源。该路径比放在 Skill 内或修改 App bundle 更适合独立更新。Eidos 不需要沿用 Codex 的 `.cache` 命名。

建议结构如下。版本与平台目录由发布清单决定，模型和 Skill 不写死它们：

```text
~/Eidos/Resource/
├── runtimes/
│   └── node/<version>/darwin-arm64/
├── packages/
│   ├── python/<pack>/<version>/cp312-darwin-arm64/
│   └── node/<pack>/<version>/
├── native/
│   └── office/<version>/darwin-arm64/
├── models/
│   └── ocr/<language>/<version>/
├── fonts/<family>/<version>/
└── .staging/
```

这里统一的是可下载资源的物理位置和管理入口，不要求把 Eidos 启动所需的一切也移出 App。Eidos 主 Runtime 依赖 Python 才能启动，所以核心 Python 继续随 App 提供。文档 Skill 复用这个受管 Python，再加载 Resource 下的已验证 Python 包即可。这样既不依赖用户全局 Python，也不额外下载一份相同解释器。

独立 Node 与文档 Node 包可以放在 Resource 下。若未来某个功能确实需要不同 Python ABI，再增加 `runtimes/python/<version>/<platform>/`。当前没有必要为统一目录形式重复安装 Python。

每次执行都应绑定具体版本目录。产品可以在新版本准备好后更新自己的资源索引，但不能把活跃 Run 指向的目录替换掉，也不能仅依靠可变的 `current` 路径维持旧任务。下载目录和资源内容说明不是第二份业务状态机；安装业务状态仍通过 Eidos 的 SQLite 与事件通道记录。

## 5. 沙盒如何使用 Resource 下的资源

当前 Eidos 已有两块可复用的能力：

- [依赖环境投影](../../runtime/eidos_runtime/sandbox/dependency_environment.py) 为单次 Shell 设置受管解释器、Python import roots 和 Node loader。它会清理冲突的解释器控制变量，设置 `PYTHONNOUSERSITE`，并保留普通 Shell 的环境继承规则。
- [Seatbelt 策略生成器](../../runtime/eidos_runtime/sandbox/seatbelt_policy.py) 为 `runtime_roots` 提供读取与可执行映射，并用 `protected_write_paths` 保护资源免于普通沙盒写入。

实现时建议遵循以下调用链：

```text
Skill 声明所需功能
  → 产品确认固定版本资源已就绪
  → Catalog 验证具体资源目录
  → Runtime 创建当前操作的依赖绑定
  → run_shell 获得受管解释器、包路径与沙盒配置
  → Skill 脚本在工作区读写文档
```

环境不靠用户的 PATH 猜测解释器。`RUNTIME_PYTHON` 指向 App 提供的 Python，`RUNTIME_NODE` 指向 Resource 下的 Node。Python 包通过明确的 import roots 提供。Node 包通过受控 loader 解析，不能仅依赖 `NODE_PATH`，因为 ESM 与 CJS 的行为需要分别处理。

字体和原生引擎的位置也需要由已验证绑定提供。调研时的文档 `scripts/run.py` 从 Python 位置反推 App 内字体目录；后续实现已改为绑定提供字体路径。多个资源包中的 Node 依赖也要形成经过验证的解析范围，确保 LibreOfficeKit 可以找到匹配的原生包，不能只把目录分别下载完成就标记整项能力可用。

普通沙盒允许读取和执行所需资源，但文档输出、临时文件、Office 用户配置、OCR 工作缓存都写入工作区或该次执行的临时目录。下载服务负责发布资源，Agent Shell 不获得安装目录的写权限，也不因资源下载自动获得网络权限。用户已确认的 full_access 与高危人工审批继续遵循现有产品例外。

调研时的 [Catalog](../../runtime/eidos_runtime/infrastructure/runtime_dependencies.py) 与 [Run 协调器](../../runtime/eidos_runtime/runtime/runtime_dependencies.py) 仍限制路径位于单一 App bundle 内。因此，当时把文件手动搬到 `~/Eidos/Resource/` 还不能直接使用。实现需要增加经过验证的资源根、版本绑定与就绪状态，保留路径身份和启动前检查；不能删除原路径校验来绕过这一步。

## 6. 建议的准备时机与验收范围

Eidos 可以采用“启动后检查和准备基础资源，重型功能首次需要时补充”的组合方式。核心界面与 Runtime 先进入可用状态。产品复用 Resource 中兼容的版本，只下载缺失资源。相关任务等待组件就绪，普通聊天、编码和其他 Session 继续运行。

Office、OCR、数据分析和额外字体的具体拆分继续采用[资源裁剪与下载方案](../design/document-resource-delivery.md)。本文没有建议复制 Codex 的完整资源清单。Eidos 仍以自己的功能与锁文件确定必要内容。

后续实现至少要在以下条件中验证：PATH 仅保留系统目录，HOME 与用户 site-packages 为空，没有宿主机 Python、Node、Homebrew、Office；资源路径含空格；下载中可以正常聊天；下载失败或首次离线不影响核心启动；真实 Seatbelt 可以执行受管 Python、Node、动态库和文档转换，并拒绝普通沙盒改写就绪资源；新版本安装不能破坏活跃 Run 的旧绑定。

调研阶段没有修改生产代码、Codex 安装状态或 Eidos 安装包。后续实现与验证以当前功能说明为准。Codex 的安装和运行状态没有被本任务改动。

## 7. Skill 如何说明依赖用法

2026-10-30 的补充核对确认，Codex 把机器事实与用法分成两层。`load_workspace_dependencies` 返回当前机器的解释器和包目录。文档 Skill 的 `Tools + Contract Requirements` 要求模型先取得这些路径，再使用受管 Node、Python 和包目录，并把 builder 或 helper 放在可写工作区或临时目录执行。这个 Skill 明确排除了系统解释器、全局 npm 包和项目内临时安装。PDF Skill 则写明优先使用 bundled workspace/runtime dependencies；它的部分工具说明仍允许系统 Poppler。因此，不能概括为所有 Codex Skill 都完全禁止系统依赖。

Eidos 的四个文档 Skill 采用统一的受管入口。`agents/eidos.yaml` 声明基础依赖与功能资源组合，Runtime 在执行前准备资源并提供真实路径，`SKILL.md` 说明命令与准备行为。Skill 不写死 `~/Eidos/Resource/` 下的具体版本，不自行运行 pip/npm，不向用户索要安装解释器的操作。

标准命令为 `"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" [--resource PROFILE] python|node SCRIPT ...`。普通 Python 读写使用默认资源，Node 生成使用 node 组合，转换与重算使用 office，PDF 渲染使用 render，数据分析使用 data，图片转换使用 images，OCR 按语言选择组合。Runtime 负责等待必要资源；普通任务和其他 Session 不受下载等待影响。四个 Skill 的说明、启动脚本、typed metadata 和实际依赖选择必须一起验证。
