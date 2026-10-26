# Eidos 当前网页搜索

更新日期：2026-10-26。

## 用户入口

用户通过“设置 → 网页搜索”配置 DeepSeek Search、LangSearch 和豆包搜索 Custom 版。页面沿用现有 SettingSection、SettingRow、Button 和 StatusBadge。三家配置可以同时保存。用户通过每家旁的“默认使用”勾选项选择一家。用户也可以取消全部勾选。

系统没有自动选择、自动切换、结果合并和备用服务。用户没有选择默认服务，或所选服务没有可读取的本地 Key 时，新 Run 不发布 `web_search`。配置保存本身不触发搜索，也不验证远端账号和额度。

页面读取配置时回显本机保存的真实 Key。输入框默认使用密码掩码，用户点击眼睛按钮后才能查看明文。页面不再提供“清除已保存的 Key”按钮，也不再使用留空保留原值的逻辑。页面按输入框当前值自动保存；用户清空输入框后，页面删除对应 Key，并同时取消该服务的默认选择。页面保存成功后保留当前值，保存失败或旧响应返回时保留后续编辑。刷新配置后，输入框重新显示保存值并恢复掩码。页面保留官方 Key、文档、价格入口。用户可以配置结果条数、超时、正文和摘录长度。LangSearch 和豆包搜索另有时间范围（支持不限、一天内、一周、一月、一年等常用预设与自定义日期区间）、包含域名和排除域名。DeepSeek 不暴露本地化 IP 设置入口。

## 调用链

```text
设置：Renderer → typed preload IPC → Main → webSearch/read 或 webSearch/save
     → WebSearchApplication → WebSearchRepository / SecretFiles

搜索：RunRepository 创建 Run 和搜索快照
     → RunResources 按快照发布 web_search
     → ToolExecutionController 参数校验、配置授权和 Durable Intent
     → WebSearchToolRuntime → RuntimeAsyncKernel 托管任务
     → httpx2.AsyncClient → 官方固定 HTTPS 端点
     → 有界响应解析、结果校验、通用投影
     → ToolResult / Event / Outbox 同事务提交
```

用户选择默认搜索服务并配置凭证后，三种审批模式都直接使用该服务，不再逐次请求人工审批或模型审查。查询会上传给所选服务，也可能计费。系统仍在请求前提交 Durable Intent，并保留取消、结果校验和未知结果记录。搜索不会向 Shell 写入通用网络 Grant。系统只在现有 Run 副作用窗口中启动异步任务；网络等待不占用该窗口，也不设置跨 Run 限额。升级前尚未执行请求、且可安全恢复的搜索审批继续通过原协调器处理，系统不会伪造用户批准或重放未知结果的请求。

## 配置与数据

Schema v34 增加 `web_search_settings` 和 `run_web_search_snapshots`。前者保存单条严格类型配置、修订号和凭证 UUID 引用。后者保存每个已启用 Run 的搜索快照。两个既有升级入口都包含 v33 → v34 迁移。凭证引用索引支持清理时定位相关快照。旧 Run 没有搜索快照，因此升级不会给旧任务增加搜索工具。

`webSearch/save` 使用 `expectedRevision` 做比较后保存。配置和 `web_search.settings_changed` Event/Outbox 在同一事务提交。事件只有 `id: web-search`。页面保存失败或未收到确认时保留草稿。用户可以刷新配置并核对后重新保存。系统不透明重试保存。

普通 Run 和排队 Run 在创建事务中固定搜索服务、参数和凭证版本。相同 Run 创建操作的重放返回原 Run 和原快照。子 Agent Run 继承父 Run 的搜索快照，父 Run 的关闭状态也会继承。分叉 Session 之后创建的新普通 Run 使用创建时的当前设置。

搜索 Key 保存在 `<EIDOS_DATA_DIR>/web-search-secrets/<UUID>`。目录为 0700，文件为 0600。系统核对 UUID、所有者、普通文件、单硬链接和权限，并拒绝符号链接。文件和目录写入经过 fsync。文件内容是受本机权限保护的明文；它没有进入 Keychain，也没有被加密。

替换 Key 会创建不可变文件。原文件仍供引用它的未结束 Run 使用。成功保存之后，系统在共享配置锁内进行一次有界清理。每次最多扫描 256 个目录项。系统保留当前配置和未结束 Run 引用的版本。失败保存产生的孤立文件可以在之后的成功保存中清理。清理不是后台服务。

`webSearch/read` 和 `webSearch/save` 响应通过 `credentialValues` 返回本机保存的 Key，仅供 Desktop 设置表单使用。凭证文件不可读时，响应保留 unavailable 状态，并返回空输入值。配置对象的文本表示隐藏 Key。模型工具参数、搜索快照对外投影和事件都不包含 Key。SQLite 只保存凭证引用。模型 Key 与搜索 Key 独立。

## 官方协议

| 服务 | 固定端点 | 实际请求字段 |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1/tools/search` | `query`、`count`、`need_content`、可选 `localization` |
| LangSearch | `https://api.langsearch.com/v1/web-search` | `query`、`count`、`freshness`、可选 `contents.text.maxCharacters`、`includeDomains`、`excludeDomains` |
| 豆包搜索 Custom 版 | `https://open.feedcoopapi.com/search_api/web_search` | `Query`、`SearchType: web`、`Count`、`Filter.NeedUrl`、可选 `Filter.TimeRange/Sites/BlockHosts`、`QueryControl.QueryRewrite: false`、`ContentFormats: text` |

三个请求都是带 Bearer Key 的 POST。DeepSeek 查询最多 128 个字符。LangSearch 查询使用 Eidos 的 2048 字符上限。三家都只允许 1–50 条结果。豆包查询最多 100 个字符。LangSearch 的新字段统一使用 `maxCharacters`。系统不传旧的 `summary` 或 `max_characters`，也不自动发送用户的完整原始问题。协议来源分别是 [DeepSeek 官方指南](https://api-docs.deepseek.com/zh-cn/guides/search/)和 [LangSearch 官方指南](https://docs.langsearch.com/reference/search-api-guide)。

HTTP Client 使用已有 `httpx2` 和 AnyIO。系统关闭重定向和环境代理继承，并保持 TLS 校验。系统没有隐藏 POST 重试。401、403、429、5xx、超时、网络失败、业务失败和非法响应映射到稳定工具错误。429 不被直接判断为“当日额度耗尽”。这些错误返回模型，不自动结束 Run，也不触发另一家搜索。

取消会取消当前 Run 拥有的异步任务。迟到结果仍受现有取消和提交核验约束。超时或网络断开可能发生在请求发出之后，因此工具会说明可能已计费。系统不会因此自动重放请求、建立后续执行屏障或要求用户核验。

## 工具契约与结果

工具只有 `query` 和可选 `count`。模型不能填写 provider、URL、Key 或 Header。DeepSeek 使用独立的 128 字符输入 Schema，豆包使用 100 字符输入 Schema。Schema 进入现有工具内容 Hash 和参数校验。

成功结果包含 `provider`、`query`、`requestedCount`、`retrievedAt`、`results` 和 `truncated`。每条来源包含 `title`、`url`、`excerpt`、`contentKind`、可选 `publishedAt` 和 `truncated`。LangSearch 的搜索用量单列在 `usage`，不计入聊天模型用量。

系统保留服务排序。系统只接受有主机、没有用户信息和控制字符的 HTTP/HTTPS 来源 URL。无效 URL 被丢弃。非空响应的来源全部无效时，工具返回 `web_search_invalid_response`。正常空数组返回成功的 `no_results`。系统不补造发布时间，不抓取图标，也不执行结果中的 HTML。

系统最多读取 2 MiB 解压后响应，并最多接受 50 条结果。摘录合计最多 100 KiB UTF-8，单条摘录按配置截断。来源元数据和摘录另受 240 KiB 总预算约束；工具 envelope 留有余量，使 canonical 结果保持在 256 KiB 内。模型首轮使用现有 16 KiB 通用投影，超长结果通过 `read_tool_output` 分页读取。网页内容在工具说明中明确属于不可信材料。

## 豆包搜索接入（2026-10-26）

用户可以在设置页保存豆包搜索专用 Key，或填写已开启豆包搜索抵扣的 Agent Plan 专属 Key。用户选择豆包作为默认服务后，新 Run 使用该配置。服务端根据 Key 决定计费方式。Eidos 不读取余额、不计算 AFP，也不在保存时发起真实搜索。[Agent Plan 官方文档](https://docs.volcengine.com/docs/ark/agent-plan-personal-search?lang=zh)说明了 Key 和抵扣开关；[搜索 API 文档](https://docs.volcengine.com/docs/Networkedsearch/Networkedsearch-2?lang=zh)定义了请求和响应。

豆包只接入 Custom 版网页搜索。系统固定 `SearchType: web`、`Filter.NeedUrl: true`、`QueryControl.QueryRewrite: false` 和 `ContentFormats: text`。系统不接入图片、行业卡片、Global 版或独立 Fetch。模型仍只提交 query 和可选 count，不能选择服务或传入地址、Key 和 Header。

设置沿用现有结果条数、超时、正文和摘录长度。豆包支持最多 20 个包含域名、5 个排除域名。系统把域名数组转换成 `|` 分隔的字符串。时间预设映射为 `OneDay/OneWeek/OneMonth/OneYear`；单个日期映射为当天的闭区间；日期范围按原范围发送。不限时间时，系统不发送 TimeRange。

豆包的 `Filter.NeedContent` 表示仅保留有正文的结果。Eidos 不发送这个字段。用户开启正文选项时，系统优先使用返回的 Content；正文缺失时仍使用 Summary，然后使用 Snippet。用户关闭正文选项时，系统优先使用 Summary。系统把 Title、Url、PublishTime 映射到现有来源字段，并保留排序、URL 校验、凭证脱敏和现有大小上限。

系统同时检查 HTTP 状态和 `ResponseMetadata.Error`，并映射参数、鉴权、访问、额度、套餐、限流和服务错误。系统不回传服务端原始错误消息，也不自动重试或切换服务。取消、Durable Intent、结果验证和未知结果记录继续使用现有执行链。

本次没有新增依赖、表或 SQL 字段。旧配置和旧 Run 快照通过新增字段的默认值读取。DeepSeek 和 LangSearch 沿用原结果模型和工具内容 Hash，待审批恢复继续使用原契约。豆包使用扩展结果模型。新版本保存的配置和快照可能包含旧版本不认识的字段；回退应用版本前，用户应恢复升级前的数据备份。

本次 Runtime 网页搜索定向测试通过 90 项，Main 协议与真实 Runtime 重启测试通过 4 项，Renderer 行为测试通过 16 项。测试覆盖豆包请求、100 字符上限、摘要与正文、发布时间、HTTP 与业务错误、不重试、凭证保留、快照继承、三种审批模式、取消和旧契约恢复。Electron 隔离页面在 320、768、1024、1440px 下通过布局、键盘默认选择和 Key 显隐检查。Main 与 Renderer 构建、Ruff、Deptry、两组 Python 依赖审计和 git diff --check 通过。Renderer 构建保留大 Chunk 提示。本次没有执行真实账户请求、Full Suite、打包或正式窗口人工验收。

## Desktop 与验证边界

Python DTO 通过 `scripts/generate-web-search-contracts.mjs` 生成 TypeScript 类型和 JSON Schema。Main 使用现有 Ajv 校验。Renderer 只导入类型，不引入动态 Schema 编译。

`openWebSearchOfficialPage` 只接受服务名及 `keys`、`docs`、`pricing`。Main 校验来源窗口，并从固定 HTTPS 表取地址，再调用系统浏览器。Renderer 不能提交任意 URL。这个入口不依赖 Session。

首版定向测试覆盖配置、CAS、凭证权限、清理、快照、子任务、幂等、迁移、官方请求、结果边界、失败、审批、取消和模型继续执行。首版真实 Electron 隔离页面覆盖 320、768、1024、1440px，以及键盘默认选择和 Key 显隐。测试使用模拟 API 和临时数据。

本次没有使用真实账户 Key 请求三家服务。保存成功不能视作在线认证通过。Full Suite、正式应用人工验收和打包验证没有执行。2026-10-26 的生产 Python 依赖和独立 Runtime Python 依赖审计均通过。本次没有修改依赖或锁文件。旧审计结果保留在下方历史验证记录中。

## Key 回显修订验证（2026-10-22）

本次修订恢复两家服务的真实 Key 回显、默认密码掩码和显式查看，并删除独立清除按钮和留空保留逻辑。保存链路继续使用原有 set/clear 请求、修订号比较和凭证文件机制，没有增加依赖或修改 SQLite Schema。旧响应没有 credentialValues 时仍可被契约校验接受。页面自动保存会读取最新输入，并按服务合并等待中的修改，避免重复服务请求违反契约或旧响应清空新编辑。

Runtime 网页搜索定向测试通过 38 项，Main 契约与真实 Runtime 重启测试通过 3 项，Renderer 行为测试通过 14 项。测试覆盖保存值回显、两家服务的掩码、显隐、刷新、输入框清空、取消默认、保存失败和保存中继续编辑。Electron 隔离页面在 320、768、1024、1440px 下通过键盘操作、Key 回显及显隐检查。页面原有 Key 输入框为 33px，其他文本输入框为 32px；旧测试中的 36px 断言已按当前样式同步，页面尺寸未变。Main 与 Renderer 构建、Ruff、Deptry 和 git diff --check 通过。Python 依赖审计报告现有 certifi、Pillow 合计 25 条漏洞。本次未运行 Full Suite、打包或真实账户请求，也未读取或修改用户实际凭证。

## 审批修订验证（2026-10-22）

本次审批修订的 Runtime 定向验证通过 196 项，另有 6 项既有 Skill 流程回归、15 项真实 Seatbelt 验证和 4 项 Desktop 契约验证通过。搜索用例覆盖三种模式的连续免批请求、Intent、取消、未获额外 Shell 网络权限，以及旧工具契约的待审批恢复。Ruff、Deptry 和 git diff --check 通过。当前 Python 生产依赖审计报告 certifi、Pillow 合计 25 项已知漏洞；独立 Runtime Python 依赖审计通过。本次没有重跑页面、构建、打包或 Full Suite，也未使用真实账户 Key。

## 首版验证记录（2026-10-21）

| 检查 | 结果 |
| --- | --- |
| Runtime 定向：网页搜索、Schema、工具控制器及渠道凭证 | 77 通过，46 项按范围排除 |
| Main 的搜索 DTO、固定官网入口和真实 Runtime 重启配置 | 3 通过 |
| 设置页和网页搜索 Renderer 行为 | 14 通过 |
| Desktop 结构、领域和 IPC 合约 | 4 通过，合约脚本通过 |
| Electron 隔离设置页 | 四种宽度、键盘选择、Key 显隐和输入框高度通过 |
| Preload、Main、Renderer 构建 | 通过；Renderer 保留大 Chunk 提示 |
| Electron smoke | Runtime 初始化和退出通过 |
| Ruff、Deptry、git diff --check | 通过 |
| 当前 Python 生产依赖审计 | 未通过：certifi、Pillow 合计 9 项已知漏洞 |
| 独立 Runtime Python 依赖、Runtime Node 依赖审计 | 通过，未发现已知漏洞 |
| Full Suite、真实 Key、正式窗口人工验收、打包 | 未执行 |

本轮实现涉及约 50 个文件，统计包含生成契约、测试、临时数据隔离的 UI 测试入口和文档。代码继续使用现有依赖。系统将渠道凭证文件机制提取为共享 helper，并保留渠道兼容入口。系统没有删除审批、Intent、结果验证、取消或恢复机制。SQLite 增加两张表和一个索引，既有业务数据继续保留。此前的服务调研原文没有修改。
