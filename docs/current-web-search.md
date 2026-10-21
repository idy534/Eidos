# Eidos 当前网页搜索

更新日期：2026-10-21。

## 用户入口

用户通过“设置 → 网页搜索”配置 DeepSeek Search 和 LangSearch。页面沿用现有 SettingSection、SettingRow、Button 和 StatusBadge。两家配置可以同时保存。用户通过每家旁的“默认使用”勾选项选择一家。用户也可以取消全部勾选。

系统没有自动选择、自动切换、结果合并和备用服务。用户没有选择默认服务，或所选服务没有可读取的本地 Key 时，新 Run 不发布 `web_search`。配置保存本身不触发搜索，也不验证远端账号和额度。

页面提供 Key 密码输入、显隐、替换、明确清除和官方 Key、文档、价格入口。空输入表示保留已存 Key。清除默认服务的 Key 会同时取消默认选择。用户可以配置结果条数、超时、正文和摘录长度。DeepSeek 另有本地化 IP。LangSearch 另有时间、包含域名和排除域名。

## 调用链

```text
设置：Renderer → typed preload IPC → Main → webSearch/read 或 webSearch/save
     → WebSearchApplication → WebSearchRepository / SecretFiles

搜索：RunRepository 创建 Run 和搜索快照
     → RunResources 按快照发布 web_search
     → ToolExecutionController 参数校验、审批和 Durable Intent
     → WebSearchToolRuntime → RuntimeAsyncKernel 托管任务
     → httpx2.AsyncClient → 官方固定 HTTPS 端点
     → 有界响应解析、结果校验、通用投影
     → ToolResult / Event / Outbox 同事务提交
```

`manual` 和 `auto_review` 使用现有审批协调器。`full_access` 使用已确认的 Run 权限直接执行。搜索审批只授权当前调用。系统不会因此向 Shell 写入通用网络 Grant。系统只在现有 Run 副作用窗口中启动异步任务；网络等待不占用该窗口，也不设置跨 Run 限额。

## 配置与数据

Schema v34 增加 `web_search_settings` 和 `run_web_search_snapshots`。前者保存单条严格类型配置、修订号和凭证 UUID 引用。后者保存每个已启用 Run 的搜索快照。两个既有升级入口都包含 v33 → v34 迁移。凭证引用索引支持清理时定位相关快照。旧 Run 没有搜索快照，因此升级不会给旧任务增加搜索工具。

`webSearch/save` 使用 `expectedRevision` 做比较后保存。配置和 `web_search.settings_changed` Event/Outbox 在同一事务提交。事件只有 `id: web-search`。页面保存失败或未收到确认时保留草稿。用户可以刷新配置并核对后重新保存。系统不透明重试保存。

普通 Run 和排队 Run 在创建事务中固定搜索服务、参数和凭证版本。相同 Run 创建操作的重放返回原 Run 和原快照。子 Agent Run 继承父 Run 的搜索快照，父 Run 的关闭状态也会继承。分叉 Session 之后创建的新普通 Run 使用创建时的当前设置。

搜索 Key 保存在 `<EIDOS_DATA_DIR>/web-search-secrets/<UUID>`。目录为 0700，文件为 0600。系统核对 UUID、所有者、普通文件、单硬链接和权限，并拒绝符号链接。文件和目录写入经过 fsync。文件内容是受本机权限保护的明文；它没有进入 Keychain，也没有被加密。

替换 Key 会创建不可变文件。原文件仍供引用它的未结束 Run 使用。成功保存之后，系统在共享配置锁内进行一次有界清理。每次最多扫描 256 个目录项。系统保留当前配置和未结束 Run 引用的版本。失败保存产生的孤立文件可以在之后的成功保存中清理。清理不是后台服务。

公开读取、模型工具参数、搜索快照对外投影和事件都不包含 Key。SQLite 只保存凭证引用。模型 Key 与搜索 Key 独立。

## 官方协议

| 服务 | 固定端点 | 实际请求字段 |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1/tools/search` | `query`、`count`、`need_content`、可选 `localization` |
| LangSearch | `https://api.langsearch.com/v1/web-search` | `query`、`count`、`freshness`、可选 `contents.text.maxCharacters`、`includeDomains`、`excludeDomains` |

两个请求都是带 Bearer Key 的 POST。DeepSeek 查询最多 128 个字符。LangSearch 查询使用 Eidos 的 2048 字符上限。两家都只允许 1–50 条结果。LangSearch 的新字段统一使用 `maxCharacters`。系统不传旧的 `summary` 或 `max_characters`，也不自动发送用户的完整原始问题。协议来源分别是 [DeepSeek 官方指南](https://api-docs.deepseek.com/zh-cn/guides/search/)和 [LangSearch 官方指南](https://docs.langsearch.com/reference/search-api-guide)。

HTTP Client 使用已有 `httpx2` 和 AnyIO。系统关闭重定向和环境代理继承，并保持 TLS 校验。系统没有隐藏 POST 重试。401、403、429、5xx、超时、网络失败、业务失败和非法响应映射到稳定工具错误。429 不被直接判断为“当日额度耗尽”。这些错误返回模型，不自动结束 Run，也不触发另一家搜索。

取消会取消当前 Run 拥有的异步任务。迟到结果仍受现有取消和提交核验约束。超时或网络断开可能发生在请求发出之后，因此工具会说明可能已计费。系统不会因此自动重放请求、建立后续执行屏障或要求用户核验。

## 工具契约与结果

工具只有 `query` 和可选 `count`。模型不能填写 provider、URL、Key 或 Header。DeepSeek 使用独立的 128 字符输入 Schema。Schema 进入现有工具内容 Hash 和参数校验。

成功结果包含 `provider`、`query`、`requestedCount`、`retrievedAt`、`results` 和 `truncated`。每条来源包含 `title`、`url`、`excerpt`、`contentKind`、可选 `publishedAt` 和 `truncated`。LangSearch 的搜索用量单列在 `usage`，不计入聊天模型用量。

系统保留服务排序。系统只接受有主机、没有用户信息和控制字符的 HTTP/HTTPS 来源 URL。无效 URL 被丢弃。非空响应的来源全部无效时，工具返回 `web_search_invalid_response`。正常空数组返回成功的 `no_results`。系统不补造发布时间，不抓取图标，也不执行结果中的 HTML。

系统最多读取 2 MiB 解压后响应，并最多接受 50 条结果。摘录合计最多 100 KiB UTF-8，单条摘录按配置截断。来源元数据和摘录另受 240 KiB 总预算约束；工具 envelope 留有余量，使 canonical 结果保持在 256 KiB 内。模型首轮使用现有 16 KiB 通用投影，超长结果通过 `read_tool_output` 分页读取。网页内容在工具说明中明确属于不可信材料。

## Desktop 与验证边界

Python DTO 通过 `scripts/generate-web-search-contracts.mjs` 生成 TypeScript 类型和 JSON Schema。Main 使用现有 Ajv 校验。Renderer 只导入类型，不引入动态 Schema 编译。

`openWebSearchOfficialPage` 只接受服务名及 `keys`、`docs`、`pricing`。Main 校验来源窗口，并从固定 HTTPS 表取地址，再调用系统浏览器。Renderer 不能提交任意 URL。这个入口不依赖 Session。

定向测试覆盖配置、CAS、凭证权限、清理、快照、子任务、幂等、迁移、官方请求、结果边界、失败、审批、取消和模型继续执行。真实 Electron 隔离页面覆盖 320、768、1024、1440px，以及键盘默认选择和 Key 显隐。测试使用模拟 API 和临时数据。

本轮没有使用真实账户 Key 请求两家服务。保存成功不能视作在线认证通过。完整测试套件、正式应用人工验收和打包验证没有执行。具体执行结果见本次交付说明。仓库现有 Python 生产依赖审计发现 certifi 和 Pillow 的已知漏洞；本次没有修改依赖或锁文件。

## 本轮验证记录

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
