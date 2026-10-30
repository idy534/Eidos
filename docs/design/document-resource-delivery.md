> 本文记录体积分析与实施方案。2026-10-30 已开始实施。最终行为、限制和验收入口见[当前功能说明](../current/features/document-skills.md)。第 7、8 节保留为实施前的边界分析。

# 文档资源裁剪与按需下载方案

日期：2026-10-29。状态：资源梳理与设计建议，尚未实现。

本方案针对新增文档 Skill 与其运行资源。当前已实现行为见[内置文档 Skill](../current/features/document-skills.md)。本轮只增加分析文档，不修改生产代码、依赖或安装包。

## 1. 结论

App 保留启动和核心 Agent 功能所需的资源。四个 Skill 的说明、脚本与校验资源继续内置。文档库、独立 Node、Office 引擎、OCR 和额外字体改为 Eidos 管理的下载资源。安装过程先复用本地已有的兼容资源，再下载缺失部分。整个流程不阻塞 Eidos 启动与普通聊天、编码任务。

部分文件没有运行用途，应直接从发布产物删除，不放进下载包。下载不会自动减少用户使用全部功能后的总磁盘占用；裁剪无用文件和消除重复才会减少这部分占用。

## 2. 当前体积证据

本地旧 DMG 为 279.38 MiB，新 DMG 为 533.50 MiB。旧 App 位于 `release/mac-arm64/Eidos.app`，新 App 位于 `release/document-skills/mac-arm64/Eidos.app`。旧包日期为 2026-10-21，新包日期为 2026-10-29，两者也包含这段时间的其他代码差异。

下表统计常规文件的逻辑字节数，不重复统计符号链接。MiB 表示 1,048,576 字节。下表是解包后的大小，不能直接作为 DMG 压缩体积相加。

| 资源 | 当前大小 | 与旧 App 的关系 |
| --- | ---: | --- |
| Electron Frameworks | 272.70 MiB | 未增加 |
| Desktop app.asar | 219.19 MiB | 增加 3.84 MiB，不能全部归因于文档功能 |
| Python 解释器与标准库 | 61.99 MiB | 未增加 |
| 独立 Node 可执行文件 | 116.26 MiB | 未增加，但也可以移出最小安装包 |
| 文档 Python 依赖 | 149.57 MiB | 增加 128.90 MiB |
| 文档 Node 包 | 522.83 MiB | 增加 513.75 MiB |
| 额外字体 | 19.56 MiB | 本次新增 |
| Runtime app | 108.53 MiB | 增加 3.37 MiB，主要是 Skill 资源 |

文档 Node 包中，Office 原生包为 153.87 MiB，Office WASM 包为 145.31 MiB，完整 react-icons 为 84.19 MiB，OCR 引擎为 43.17 MiB，中英 OCR 数据为 34.10 MiB。文档 Python 环境中，pandas 为 41.14 MiB，NumPy 为 36.68 MiB。这些数字存在包含关系，不能再次与上表求和。

## 3. App 必须保留的内容

| 内容 | 保留理由 |
| --- | --- |
| Electron、Renderer、Main、preload 与必要原生桥接 | 桌面界面和控制通道需要这些内容 |
| 受管 Python、Runtime 生产代码与生产依赖 | Agent、Provider、SQLite、协议和生命周期在这里运行 |
| Seatbelt 资源、受控文件 helper、随包 ripgrep、语法资源与必要词表 | 核心工具、安全边界和本地代码能力需要这些内容 |
| 四个 Skill 的 SKILL.md、脚本、元数据和 OOXML 校验资源 | 内容体积较小，模型需要在资源下载前发现能力并了解用法 |
| 精简资源目录、固定版本与平台声明、下载地址和校验信息 | 产品需要在未联网时知道各项资源的用途与兼容版本 |

Pillow、cryptography 等已经属于主 Runtime 的生产依赖。不能因为文档包也使用它们，就从主 Runtime 中删除它们。本次不扩大到主 Runtime 依赖重构。

独立 Node 在当前源码中用于文档依赖环境，Desktop 自身使用 Electron 的 Node。独立 Node 可以随文档 JavaScript 资源下载；这需要同步调整当前打包清单和 smoke 的强制依赖约定。第一版不替换为宿主机 Node，也不另建 Electron 运行脚本的入口。

## 4. 可以按需下载的内容

以下是功能分组，不要求每个分组都建立一套安装器。资源之间共享解释器与公共依赖，构建器按锁文件生成固定依赖范围。

| 分组 | 内容 | 建议触发时间 | 准备期间的影响 |
| --- | --- | --- | --- |
| 基础文档 Python | python-docx、python-pptx、openpyxl、pypdf、lxml、defusedxml 及必要间接依赖 | 首次启动后台准备 | 基础 Word/PPT/Excel/PDF 脚本等待；普通任务可继续 |
| JavaScript 文档生成 | 受管 Node、docx、pptxgenjs 与必要间接依赖 | 首次生成对应文档时 | 相关生成操作等待 |
| Office 转换与重算 | LibreOfficeKit API、当前平台原生引擎与必要资源 | 首次转 PDF、生成 Office 缩略图或重算公式时 | 对应操作等待；读取和编辑不必一起等待 |
| PDF 增强 | pdfplumber、ReportLab、PDFium 及必要间接依赖 | 首次进行表格提取、程序化 PDF 生成或页图渲染时 | 已就绪的 pypdf 合并、拆分等功能可继续 |
| 数据分析 | pandas、NumPy 与必要间接依赖 | 首次需要 DataFrame 或相关数值处理时 | openpyxl 的基础读写不依赖这组资源 |
| 图像转换 | sharp 与当前平台 libvips | 首次需要 SVG 栅格化或相应图片处理时 | 普通文字、图形和已有 PNG/JPEG 的文档生成可继续 |
| OCR | Tesseract.js、匹配的 OCR 内核 | 首次处理扫描件时 | 普通 PDF 文本提取可继续 |
| OCR 语言 | eng、chi_sim，后续按支持范围增加 | 首次选择对应语言时 | 产品只下载这次需要且尚未安装的语言 |
| 额外字体 | Noto Sans SC、Carlito | 首次要求相应字体的生成或转换时；与所需文档资源一起准备 | 原文提取和文件拆合不需要这些字体 |

基础 Python 包中的 Pillow、cryptography、charset-normalizer 等，优先引用 Eidos 自身已验证且兼容的生产包。复用的是 Eidos 的环境，不是用户的全局 Python。构建器需要核对版本约束与完整间接依赖，不能凭直接 import 列表删包。例如，python-pptx 的锁定依赖包含 xlsxwriter。

默认 PPT 图标可以使用小组静态 PNG/SVG 资源。完整 react-icons、React、ReactDOM 可以从默认文档依赖中移出。产品如果保留完整图标集合能力，可以把它作为显式扩展下载，而不让所有 PPT 操作都要求这组资源。

## 5. 应直接裁剪或合并的内容

| 内容 | 处理建议 | 已确认的范围或限制 |
| --- | --- | --- |
| Office WASM 引擎 | 当前 macOS arm64 版本直接移除，也不后台下载 | 约 145.31 MiB；已安装兼容原生引擎时，上游解析器返回原生引擎 |
| Python 测试目录与测试数据 | 从运行资源中裁剪，构建和开发环境保留 | 当前约 21.09 MiB、1,636 个文件；裁剪后要验证运行导入与文档操作 |
| 文档环境与主 Runtime 相同的包文件 | 在验证版本兼容后共享，避免重复发布 | 当前逐文件相同比较合计约 25.65 MiB；这不是可以直接删除的包清单 |
| 无关 CPU/操作系统的预构建产物 | 当前平台资源包不包含它们 | LibreOfficeKit API 包内还带多平台 frame-forwarding 开发产物 |
| 当前功能未引用的开发 SDK、示例、构建工具和头文件 | 在确认运行路径与分发要求后移出运行产物 | frame-forwarding 目录合计约 3.23 MiB；不能把整个 Office program 目录当作 SDK 删除 |
| 未使用的 OCR 模型变体 | 每种语言只发布选定模型 | 当前入口使用 4.0.0，不使用额外 best_int 数据；两种语言的 best_int 合计约 4.46 MiB |
| OCR 内核模式 | 按实际调用保留 | 验证发现 Tesseract.js 7.0.0 的 Node adapter 会选择完整内核，因此首版同时保留完整与 LSTM 内核，不按 OEM 参数直接裁剪 |

OOXML XSD、主 Runtime 生产依赖、执行必需的动态库、引擎数据文件和必要第三方声明都不属于可按文件名直接删除的内容。117 份 XSD 用于现有文档校验，文件数量大不代表占用是主要问题。

## 6. 本地检查、下载与初始化行为

“本地已有”首先指 Eidos 自己安装过的资源。产品检查资源名称、版本、macOS 平台、CPU 架构、Python ABI 与就绪记录，并核对必要文件。兼容且完整的资源直接复用。产品只下载缺失或不兼容的资源，不因重启或普通 App 升级重新下载全部内容。

产品不默认扫描全盘或复用任意 Homebrew、全局 pip、npm 和 PATH。已有桌面 LibreOffice 也不能直接当作 LibreOfficeKit 的原生包使用。宿主机资源适配需要独立验证，第一版不把它作为主要路径。

建议采用以下行为：

1. App 正常显示界面并初始化核心 Runtime。产品检查资源状态，同时后台准备基础文档小包。产品不显示阻塞整个 App 的初始化页面。
2. 用户首次需要增强操作时，Runtime 准备对应资源。界面只在相关任务或设置中显示“文档组件准备中”。其他 Session 和普通操作继续执行。
3. 产品下载 Eidos 发布的固定版本预构建资源包。产品不在用户机运行 npm/pip 解析或编译，也不要求用户安装 uv、Homebrew 或编译器。
4. 下载写入暂存目录。产品完成完整性、路径范围、平台和必要原生程序检查后，原子发布到稳定的版本目录。未完成资源不能进入工具绑定。
5. 网络失败、用户取消或磁盘不足只使相关功能暂不可用。界面提供重试。已经下载并验证的资源离线可用。没有下载过的功能在首次离线使用时需要明确说明原因。
6. 同一资源的同时请求复用同一下载工作。产品不因此新增跨 Run 的执行配额，也不让等待资源的任务占用其他 Session 的执行机会。

资源目录建议统一为用户提出的 `~/Eidos/Resource/`，根目录下再按解释器、包、原生引擎、OCR 模型、字体、版本和平台分类。2026-10-30 的 [Codex 桌面端资源调研](../research/codex-desktop-runtime-resources.md) 补充了本机安装证据、目录结构和沙盒接入边界。这个位置已被用户确认。当前实现与验证以[内置文档 Skill](../current/features/document-skills.md)为准。核心 Python 仍随 App 提供，文档 Skill 可以复用它；下载的 Python 包与 Node 环境放在统一资源目录。产品不修改已签名 App bundle，也不把下载文件塞入 Skill 内容目录。普通 Shell 对就绪资源只读，安装器负责受控发布。当前版本没有资源清理界面；后续清理需要避开活跃任务引用的版本。

## 7. 当前代码需要调整的边界

当前实现没有文档资源下载器与准备进度协议。不能只从 DMG 删除文件，再让原逻辑去外部目录寻找它们。

- [依赖 Catalog](../../runtime/eidos_runtime/infrastructure/runtime_dependencies.py) 目前以单一 App bundle 为根，并要求声明路径存在。它需要区分固定版本声明与本地安装状态，并支持验证过的外部资源根。
- [Run 依赖协调器](../../runtime/eidos_runtime/runtime/runtime_dependencies.py) 目前拒绝 bundle 外和 Eidos 数据目录内的依赖路径。实现必须增加明确的受管资源根边界，不能直接放开任意路径。
- [依赖 Repository](../../runtime/eidos_runtime/db/repositories/runtime_dependencies.py) 目前每个 Run 只保存一份不可变依赖快照。方案应在 Run 中固定资源版本，下载就绪后再为具体操作创建绑定。安装完成不能偷偷替换已有绑定或整个 Run 快照；确需改变模型或存储契约时，要提供迁移与恢复测试。
- 四个 `agents/eidos.yaml` 目前将大部分库声明为整个 Skill 的必需依赖。实现需要将基础操作、Office、OCR、数据分析等依赖分开，避免 PDF 合并也等待 OCR。
- [Skill 设置投影](../../runtime/eidos_runtime/models/skill_settings.py) 目前没有资源准备状态。准备状态应与用户启停 Skill 的状态分开，避免下载失败自动停用 Skill。
- Runtime 可以复用现有 AnyIO、HTTP 客户端、SQLite、Async Operation 和资源所有者机制。安装状态通过既有业务存储与事件通道投影到 Desktop，不创建另一份 JSON 业务状态机。下载发生在产品资源服务中，不给 Agent Shell 额外网络权限。

资源发布还需要固定版本的压缩产物与可访问的托管地址。当前仓库只有构建时获取依赖的入口，没有完整的目标机资源发布和安装链路。下载地址、原生包分发验证与版本保留应在实现阶段明确。

## 8. 建议实施与验证顺序

第一步先做发布裁剪，保留当前随包能力，测量真实 DMG 变化。第二步加入一个统一的资源准备入口，先迁移 Office 或 OCR 这类边界清楚的大资源。第三步迁移基础文档环境、独立 Node 与共享 Python 包，并调整 Skill 的就绪规则。每一步都更新当前能力说明。

定向验证至少覆盖：干净机器环境、已有资源复用、版本或 ABI 不兼容、首次离线、下载中断后重试、校验失败、磁盘不足、同资源并发请求、下载中正常聊天、App 退出与重启、活跃 Run 的旧绑定保留、资源目录拒写，以及四类文档的实际读写与渲染。

本方案形成时只核对源码、锁文件和本地构建内容。后续实现的验证记录见当前功能说明；本任务没有要求 Full Suite。新安装包体积需要实施后重新测量，本方案不承诺未经实测的体积目标。
