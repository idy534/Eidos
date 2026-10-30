# 内置文档 Skill

Eidos 提供 `docx`、`pptx`、`xlsx`、`pdf` 四个系统 Skill。新安装默认启用。用户可以在设置的“系统”分类中停用它们。普通卸载入口不能删除系统 Skill。

## App 与资源目录

App 保留核心 Python、Runtime 生产依赖、ripgrep、Seatbelt 资源、四个 Skill 的说明和脚本。文档扩展依赖由 Eidos 下载到 `~/Eidos/Resource/`。目标机器不需要预装 Python、Node、Office、uv、npm、Homebrew 或编译器。产品不扫描或复用任意全局环境。

四个 Skill 的图标位于各自的 `assets/<skill-name>-small.svg`。现有设置页按文件名自动读取 SVG，无需新增前端逻辑或图标声明。

源码内容位于 `runtime/eidos_runtime/resources/skills/.system/<name>/`。启动部署沿用系统 Skill 入口，将内容写入 `<data>/skills/.system/`。暂存目录、部署目录及中间层目录使用私有权限。依赖不放在 Skill 目录，也不写入已安装的 App。

资源按用途、版本和平台分开：

```text
~/Eidos/Resource/
├── runtimes/node/<Node 版本>/darwin-arm64/
├── packages/python/<分组>/<资源版本>/cp312-darwin-arm64/
├── packages/node/<分组>/<资源版本>/
├── native/office/<资源版本>/darwin-arm64/
├── models/ocr/<语言>/<资源版本>/
├── fonts/document/<资源版本>/
└── .staging/
```

核心 Python 继续来自 App 的 `runtime/python/`。下载的 Python 库使用同一 CPython 3.12 ABI。独立 Node 位于上述资源目录。普通聊天和编码不要求先下载文档资源。

`runtime/resource-index.json` 随 App 发布，固定平台、地址、版本、大小、完整性信息、依赖关系和文件范围。地址来自 `idy534/Eidos` 的 GitHub Releases。模型与公开 RPC 不能传入自定义下载地址。就绪资源的 `runtime.json` 是固定清单的回执；SQLite 的现有 Async Operation 记录安装结果。回执和内存进度不构成另一套业务状态机。

## 准备时机

| 资源 | 内容 | 触发时间 |
| --- | --- | --- |
| document-python | python-docx、python-pptx、openpyxl、pypdf、lxml 等 | 核心 Runtime 初始化完成后后台准备 |
| node | 独立 Node | 首次使用依赖 Node 的操作 |
| document-node | docx、pptxgenjs、薄转换 API 等 | 首次执行相应 JavaScript 操作 |
| office | LibreOfficeKit macOS arm64 原生引擎 | 首次 Office 转换、缩略图或公式重算 |
| pdf | ReportLab、pdfplumber、PDFium 等 | 首次 PDF 增强或页面渲染 |
| data | pandas、NumPy 等 | 首次数据分析 |
| images | sharp 的当前平台原生库 | 首次相应图片处理 |
| ocr | Tesseract.js 内核 | 首次 OCR |
| ocr-eng、ocr-chi-sim | 对应语言数据 | 首次使用对应语言 |
| fonts | Noto Sans SC、Carlito | 首次需要这组字体的转换或生成 |

产品会先检查本地的相同版本资源。完整且兼容的资源直接复用。它不会因为重启或普通 App 升级重新下载全部资源。新资源版本使用新目录，已冻结的 Run 保留原版本声明。

首次操作会等待其资源准备。其他 Session 仍可使用。设置的系统 Skill 分类显示各组资源的准备状态和下载进度，并提供准备、重试入口。准备失败不会停用 Skill。用户退出 Eidos 并开始关闭 Runtime 后，资源状态查询返回不可用，设置页随即停止轮询；已经发出的只读查询也按关闭状态结束。关闭后不再接收新的资源准备请求。运行期间的异常退出仍返回错误。离线、取消、磁盘不足和完整性错误会返回明确的工具错误。模型可以修正操作或使用其他工具。用户可以稍后重试。

下载先写入私有暂存目录。安装器只接受受信 Release 主机的 HTTPS 跳转。安装器限制压缩包和展开大小，拒绝链接、特殊文件、重复文件、越界路径和清单外文件，并核对每个文件后原子发布。未完成资源不能进入执行绑定。同一进程内的同资源请求共享准备工作。退出使用现有 Runtime 所有者与取消流程，不增加全局 Run 配额。当前版本没有自动清理旧资源的界面；维护者不应在活跃任务期间手动删除资源。

## Skill 的使用约定

最小调用链是：

```text
skill_read → 工作区脚本 → run_shell
→ 识别 Skill launcher 与资源配置 → 准备固定资源
→ 创建并复检 Run 依赖绑定 → 原有权限与 Durable Intent
→ Seatbelt → scripts/run.py → 受管解释器
```

四个 `SKILL.md` 都明确说明解释器来源、资源配置、输出位置和失败处理。`agents/eidos.yaml` 使用 typed `runtimeResources.profiles` 声明每种操作的资源。模型不用拼接 `~/Eidos/Resource` 下的实际版本路径。

模型先把代码写入工作区，再调用当前 Skill 的绝对路径：

```sh
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" python generate.py
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" node generate.cjs
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" --resource office python "<skill root>/scripts/office/convert.py" input.docx output.pdf
"$RUNTIME_PYTHON" "<skill root>/scripts/run.py" --resource ocr-eng node "<skill root>/scripts/ocr.cjs" page.png eng searchable.pdf
```

`RUNTIME_PYTHON` 来自已有默认绑定。Runtime 识别 launcher 后准备资源，并在子进程中注入该操作的完整绑定。入口检查 `EIDOS_RESOURCE_PROFILE` 与实际请求一致，只使用绑定提供的绝对解释器路径。入口不创建另一套执行通道。

| 配置 | 用途 |
| --- | --- |
| python（默认） | 基础 Python 文档操作 |
| node（Node 默认） | JavaScript 文档生成 |
| office | Office 转换、Excel 重算 |
| thumbnail（PPT） | Office 转换与 PDFium 缩略图 |
| render | PDF 页面渲染 |
| pdf | PDF 增强与字体 |
| data | pandas、NumPy；PDF Skill 还包含 PDF 提取依赖 |
| images | sharp 图片处理 |
| ocr-eng / ocr-chi-sim / ocr-bilingual | OCR 与对应语言 |

每个 Skill 的实际配置以自己的 `agents/eidos.yaml` 为准。PDF Skill 不声明 Office 转换配置。说明正文中的简写命令也必须通过 launcher 执行。模型不能使用全局 pip/npm 安装或改用宿主机解释器。

Python 包路径和本地 helper 路径只影响当前子进程。CJS 与 ESM 使用同一受管 Node loader。它允许多个已验证模块根，保留工作区相对导入，并拒绝用项目或宿主机包替代受管文档包。字体路径来自绑定。

所有输出仍写入工作区。普通权限模式下，Skill、App 和整个 Resource 根目录保持拒写。产品准备服务负责受控安装，Agent Shell 不因此获得网络权限。原有 Approval、Durable Intent、文件核验、进程组清理、取消和未知结果语义保留。用户确认的完全访问模式继续遵循原有例外。

Run 的依赖快照在开始时固定完整发布声明。准备成功后，Runtime 只为具体操作创建绑定。准备完成不会替换 Run 快照，也不会把缺失资源的失败绑定永久缓存。

## 构建、裁剪与发布

Python 输入来自根 `pyproject.toml` 的 `document-skills` group 和 `uv.lock`。`scripts/lock-document-dependencies.py` 生成精确 wheel 清单与 import 映射。Node 输入保存在 `resources/runtime-dependencies/node/` 的独立 package、lock 和 workspace 配置中。字体输入来自 `resources/runtime-dependencies/fonts/files.json`。构建采用固定版本和完整性校验。目标机只安装预构建资源，不进行依赖解析或编译。

`build-macos-runtime.sh` 先在构建机准备完整依赖，再由 `build-resource-packs.py` 拆出 `build/resource-packages/`，并删除 App 中的文档依赖目录。App 生成精简 `runtime.json` 和固定资源索引。构建器通过 wheel RECORD 确定 Python 文件范围；它复用版本一致的核心生产包，并保留运行和第三方声明需要的文件。

当前裁剪包括：Office WASM、React/ReactDOM/react-icons、Python 测试与缓存、无关平台开发产物、frame-forwarding SDK 和未使用的 OCR best_int 语言模型。Tesseract.js 7.0.0 的 Node adapter 仍会选择完整内核，因此资源包保留其实际需要的完整与 LSTM 内核。产品不能仅根据入口的 OEM 参数删除它们。OOXML XSD、必要动态库和第三方声明继续保留。

资源发布配置位于 `resources/runtime-dependencies/resource-release.json`。同一发布版本的文件应保持不可变。维护者必须先上传对应 Release 资源，再发布使用该索引的 App。下载失败只影响相关功能，但不能把不存在的远端资源写成已验收。详细命令见[开发参考](../../development/README.md)。

Office 转换与重算使用固定版本 `0.2.3` 的 LibreOfficeKit。PDF 页面使用 PDFium，最多一次处理 100 页。OCR 支持 eng、chi_sim 及组合，并显式读取本地语言数据。产品不需要 Pandoc、MarkItDown、Poppler、qpdf 或 pdftk。Office 排版与公式可能与 Microsoft Office 不同，OCR 结果也需要核对。

按本次本地学习需求，四个 Skill 的 Anthropic `LICENSE.txt` 与 frontmatter `license` 字段未进入仓库。依赖本身的第三方声明继续保留。

## 升级与回退

启动迁移只处理四个同名个人 Skill。程序保留原目录与自定义内容，并一次性切换到系统版本。原个人 Skill 已停用时，系统版本也保持停用。状态与事件在同一 SQLite 事务中保存。失败可以在下次启动重试。后续手动选择不会被重启覆盖。旧 Run 继续使用冻结身份。

本次没有新增数据库表或 Schema 版本。公开协议新增 `resource/status`、`resource/retry`。两端类型由 Pydantic Schema 生成，共享 Fixture 验证。旧的随包依赖 Catalog 仍可读取。源代码开发环境没有生成资源索引时不启动下载服务。

回退代码后，个人 Skill 目录仍保留。用户需要在设置中重新启用个人版本。已保存的启停状态不会自动撤销。回退版本不会清理 Resource 目录；维护者可以在退出 Eidos 后手动清理不再使用的版本。

## 验证边界

定向测试覆盖 Skill 部署与迁移、冻结依赖快照、资源复用、同资源并发、取消、下载失败、完整性失败、恶意压缩路径、目录链接、磁盘不足、RPC 参数边界和界面重试。

`scripts/bundled-document-skills-smoke.py` 使用 App 的 Python、干净 HOME、仅系统目录的 PATH 和真实 Seatbelt。它从本地构建的压缩包模拟 Release 下载响应，经过生产下载校验与安装流程。它检查四类文档、CJS/ESM、图片、数据分析、字体、英中 OCR、禁网、资源拒写和超时。这个检查不证明公网 GitHub 下载、另一台机器或签名公证通过。

界面检查入口为 `scripts/test-managed-resources.cjs`，覆盖 320、768、1024、1440 像素的等待、失败和重试状态。构建成功不能替代这些交互检查。当前任务没有要求 Full Suite。真实 Provider、另一台实体机器和签名公证需要另行验收。

2026-10-30 的最终本地验证结果：

- 15 个 Runtime 定向文件共 207 项测试通过。范围包含资源安装与拒绝路径、Skill 部署迁移、依赖绑定、取消、持久化和方法路由。两个上游弃用提示不影响测试结果。
- 18 项打包定向测试、10 项 Renderer 行为测试、2 项双端契约与真实 stdio 测试通过。离线下载环境下，核心 Runtime 仍可初始化并响应 health。
- Ruff、生产 Runtime 的 Deptry、生成契约一致性、Desktop 构建与 `git diff --check` 通过。核心与文档 Python 的 pip-audit、文档 Node 的 pnpm audit 均未发现已知漏洞。
- Bundle 与最终 App 内的真实 Seatbelt 文档 smoke 通过。范围包括 Word/PPT 创建与渲染、Excel 正确值及错误公式、pandas/NumPy、PDF、中文字体、中英 OCR、CJS/ESM、禁止联网、资源拒写、超时与资源复用。
- 最终 App 的文件清单、包内 Python/Ripgrep/SQLite/JSON-RPC、Electron 启停与 DMG 完整性检查通过。
- [资源 Release](https://github.com/idy534/Eidos/releases/tag/document-resources-2026.10.30.1) 已公开发布，共 11 个压缩包和 1 份索引，未设为应用 latest Release。全部远端资源的大小与 GitHub 摘要符合固定索引。基础文档包从真实公开 URL 下载到空目录、完成跳转与安装校验，并进入 ready 状态。
- 最终 DMG 位于 `release/managed-resources/Eidos-0.5.4-mac-arm64.dmg`，大小为 233.61 MiB。此前全量文档依赖包为 533.50 MiB，2026-10-21 的旧包为 279.38 MiB。资源包合计 212.65 MiB，连同发布索引为 213.31 MiB；全部资源安装后约为 516.41 MiB。这些资源不计入 DMG，用户只准备实际需要的分组。

本次没有执行 Full Suite、真实 Provider、另一台实体机器安装或 Developer ID 签名公证。该 DMG 是本地验收产物。代码尚未提交；资源 Release 已发布，后续 App 必须保留其匹配的固定索引。

2026-10-30 的退出轮询修复另行通过 19 项 Main/协议定向测试、7 项资源面板行为测试、Desktop 构建、正常 Electron 启停和真实资源 IPC 退出竞态检查。受控子进程在关闭时保留未答复的资源查询，用于覆盖本次错误；意外退出用例仍要求查询失败。本次修复没有修改 Runtime 关闭协议、数据库或下载资源版本，也没有重新生成 DMG。
