# Eidos 内置网页搜索实现方案

> 文档分类：设计与实施记录。本文保留记录时的方案、分支状态和验证范围。当前行为请以 [当前说明](../README.md#当前说明)、源码和测试为准。

日期：2026-10-21。状态：第一版代码已接入，定向验证通过。真实账户验证和依赖审计问题仍未完成。

本文承接[服务调研](../research/web-search-research-and-selection.md)。用户选择 DeepSeek Search 和 LangSearch。用户取消 DuckDuckGo。用户明确要求手动勾选默认服务，不需要自动切换。当前实现和限制见[当前网页搜索](../current-web-search.md)。

## 1. 最终产品决定

- 用户可以在“设置 → 网页搜索”分别配置两家服务。页面使用其他设置页的公共组件和样式。
- 每家服务旁有“默认使用”勾选项。勾选一家的同时会取消另一家。用户也可以全部取消。
- 系统不默认勾选 LangSearch。系统没有第二个启用开关、自动选择、备用服务、结果合并和自动切换。
- 用户没有选择默认服务，或默认服务没有可读取的 Key 时，新 Run 不提供 `web_search`。
- 搜索服务与聊天模型独立。DeepSeek Search 不要求使用 DeepSeek 聊天模型，也不自动复制聊天 Key。
- 本版支持搜索附带正文。本版没有独立 `web_fetch`、HTML 搜索解析、非官方代理或搜索 MCP 进程。
- 保存只验证参数和本地凭证。系统不发起后台付费探测。页面用“Key 已保存”表示本地状态。

## 2. 页面行为

页面在“模型”之后增加“网页搜索”。两家分区都包含 Key 密码框、显隐按钮、获取 Key、官方文档、费用与额度入口、默认勾选项和技术参数。

页面统一保存两家草稿。这个安排让默认选择与 Key 清除在一次保存中提交。用户留空 Key 时，Runtime 保留旧引用。用户明确点击“清除已存 Key”时，页面提交 `clear`。清除当前默认 Key 时，系统同时取消默认选择。

页面保存时冻结表单，防止重复提交。保存失败或响应不确定时，页面保留两家的草稿和新 Key。用户可以点击“刷新配置”。页面更新已知配置版本，并保留当前编辑，等待用户核对后再保存。页面不会把凭证重新回填为明文，也不会透明重试保存。用户点击“取消编辑”时，页面还原最近已读配置并清空新 Key。

页面通过现有设置 Toast 显示保存成功。页面同时说明新任务生效及远端 Key、额度未验证。配置修改不会改变已有 Run，包括排队 Run。

| 参数 | 两家共同规则 |
| --- | --- |
| `defaultCount` | 默认 5，严格整数 1–50 |
| `timeoutSeconds` | 默认 30 秒，5–120 秒 |
| `includePageContent` | 默认 false |
| `maxExcerptCharacters` | 默认 3000，100–10000 |

DeepSeek 的 `localization` 是可选 IP 字符串。Runtime 使用标准库 `ipaddress` 校验。Eidos 不主动查询用户公网 IP。

LangSearch 的 `freshness` 默认为 `noLimit`。Runtime 还接受 `oneDay`、`oneWeek`、`oneMonth`、`oneYear`、`YYYY-MM-DD` 和包含首尾日期的 `YYYY-MM-DD..YYYY-MM-DD`。Runtime 检查真实日期和起止顺序。

LangSearch 的 `includeDomains`、`excludeDomains` 各最多 20 个域名。页面接受逗号分隔输入。Runtime 统一大小写、去重并执行 IDNA 转换。Runtime 拒绝协议、路径、端口、用户信息、通配符和两组重复域名。

## 3. 官方入口与请求

| 服务 | 获取 Key | 文档 | 费用或额度 |
| --- | --- | --- | --- |
| DeepSeek | [API Keys](https://platform.deepseek.com/api_keys) | [Search Guide](https://api-docs.deepseek.com/zh-cn/guides/search/) | [Pricing](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) |
| LangSearch | [Dashboard → API keys](https://langsearch.com/dashboard) | [Search Guide](https://docs.langsearch.com/reference/search-api-guide) | [Limits](https://docs.langsearch.com/limits/api-limits) |

Renderer 通过 `openWebSearchOfficialPage({ provider, target })` 请求打开固定页面。Main 验证来源窗口和参数，并从内部表取 HTTPS 地址。这个入口不接受任意 URL，也不依赖 Session。

DeepSeek 使用 `POST https://api.deepseek.com/v1/tools/search`。请求包含 Bearer Key、`query`、`count`、`need_content` 和可选 `localization`。查询上限为 128 个 Unicode 字符。系统不自动把用户完整问题填入 `ori_question`。

LangSearch 使用 `POST https://api.langsearch.com/v1/web-search`。请求包含 Bearer Key、`query`、`count`、`freshness` 和可选域名列表。用户请求正文时，系统发送 `contents.text.maxCharacters`；用户没有请求正文时，系统省略 `contents`。本版不新增旧字段的兼容别名。

LangSearch 的 HTTP 200 响应仍需校验业务 `code`。来源数组位于 `data.webPages.value`。正文 `text` 可能代替 `snippet`。日期可能缺失。`usage.input_tokens`、`usage.output_tokens` 属于搜索服务用量，不是聊天模型用量。

## 4. 配置协议

Runtime 注册两个严格方法：`webSearch/read` 和 `webSearch/save`。Python 定义位于 `protocol/web_search.py`。生成脚本输出 TypeScript 类型和 JSON Schema。Main 复用现有 Ajv。共享 Fixture 由 Python 和 TypeScript 分别验证。

读取响应只包含 `revision`、`config` 和两家的凭证状态。状态为 `missing`、`stored` 或 `unavailable`。公开响应不返回 Key 或 UUID。现有 RPC 序列化会省略空值，所以未选择默认服务的 `defaultProvider` 可以省略或为 null。

```json
{
  "expectedRevision": 2,
  "config": {
    "defaultProvider": "deepseek",
    "deepseek": {
      "defaultCount": 5,
      "timeoutSeconds": 30,
      "includePageContent": false,
      "maxExcerptCharacters": 3000,
      "localization": null
    },
    "langsearch": {
      "defaultCount": 5,
      "timeoutSeconds": 30,
      "includePageContent": false,
      "maxExcerptCharacters": 3000,
      "freshness": "noLimit",
      "includeDomains": [],
      "excludeDomains": []
    }
  },
  "credentialChanges": [
    { "provider": "deepseek", "action": "set", "value": "USER_SUPPLIED_KEY" }
  ]
}
```

`credentialChanges` 是按 `action` 区分的联合类型。`set` 需要 `value`，`clear` 禁止 `value`。同一家不能重复出现。缺少某家的变更表示保留凭证。API Key 最多 1024 UTF-8 字节，不能有首尾空白或控制字符。

Runtime 用 `expectedRevision` 检查并发修改。冲突返回 `WEB_SEARCH_CONFIG_CONFLICT`。凭证文件失败返回 `WEB_SEARCH_CREDENTIALS_UNAVAILABLE`。参数错误由协议校验或 `INVALID_PARAMS` 表达。系统不把第三方异常或凭证值跨协议传送。

## 5. 持久化与凭证

Schema v34 增加两个表：

```text
web_search_settings
  id = 1         PRIMARY KEY
  settings_json  StoredWebSearchSettings：revision、config、credentials 引用

run_web_search_snapshots
  run_id         PRIMARY KEY / FK → runs(id)
  snapshot_json  RunWebSearchSnapshot：revision、provider、config、credential_ref
```

配置不存在时，Repository 返回两家的默认参数、空凭证和空默认选择。系统不需要在迁移时隐式启用服务。两个既有数据库升级入口都调用相同的事务迁移。旧 Run 没有快照，因此没有搜索工具。回退旧 Runtime 需要恢复升级前的数据备份。

配置和 `web_search.settings_changed` Event/Outbox 同事务提交。事件只含 `id: web-search`。Application 不执行 SQL。SQLite 仍是业务事实来源。

Key 使用 `<EIDOS_DATA_DIR>/web-search-secrets/<UUID>` 文件。目录权限是 0700，文件权限是 0600。系统使用 `O_NOFOLLOW`、所有者、文件类型、单硬链接和长度检查，并对文件和目录执行 fsync。这个文件方案复用从 `ChannelSecrets` 抽出的 `SecretFiles`；渠道目录和异常接口继续保留。本版没有新增依赖，也没有声称使用 Keychain 或加密。

保存先写不可变 Key 文件，再提交含引用的配置。系统用现有数据库锁串行化这段配置发布，避免清理误删另一个正在保存的 Key。系统只在确认提交后进行有界清理。系统每次最多扫描 256 项，并保留当前配置和未结束 Run 的引用。失败保存的孤立文件可以等待下一次成功保存清理。系统不会删除结果不确定的事务可能仍引用的文件。

## 6. Run 与工具执行

RunRepository 在 Run 创建事务里冻结配置。排队任务同样在入队时冻结。操作重放返回原 Run，不重新读取当前默认服务。子 Agent 的创建路径会用父 Run 快照替换当前全局快照；父 Run 关闭搜索时，子 Run 也关闭。分叉后新建的普通 Run 使用当前设置。

RunResources 根据快照和本地凭证可读性决定是否发布 direct `web_search`。工具只接受 `query` 和可选 `count`。模型不能选择服务或更改固定端点。DeepSeek 单独使用 128 字符输入 Schema；LangSearch 使用 Eidos 的 2048 字符上限。

```text
Validate → Prepare → Resolve Permission → Commit Durable Intent
→ 启动 Run 拥有的 AnyIO 任务 → 等待有界 HTTP 请求
→ Verify → Commit Result / Event / Outbox
```

专用 WebSearchToolRuntime 进入现有 Controller。搜索不冒充 MCP 外部工具。普通模式复用网络审批，完全访问模式复用已有确认。审批包含服务、主机、查询、条数和可能计费说明。搜索审批不写通用网络 Grant，所以它不会打开 Shell 网络权限。

系统只在现有 Run 副作用窗口内启动请求。HTTP 等待在窗口外进行。RuntimeAsyncKernel 管理任务所有者和关闭。取消会取消请求，并等待清理。Controller 继续决定迟到结果、取消和最终提交顺序。系统没有新增状态机或全局执行配额。

HTTP 使用已锁定的 `httpx2.AsyncClient`。系统保留 TLS 校验，关闭重定向、环境代理继承和隐藏 POST 重试。超时、断开和恢复不会自动重放可能计费的请求。工具错误会回到模型，Run 可以继续处理其他内容。

## 7. 返回值与上限

工具使用现有 canonical ToolResult。成功数据包含服务名、查询、请求条数、UTC 抓取时间、来源、截断标记和可选搜索用量。来源包含标题、URL、摘录、内容类型、可选发布时间和截断标记。

系统保留远端顺序。系统优先选择用户请求的正文，否则使用摘要或 snippet。系统只保留合法 HTTP/HTTPS 来源，并丢弃单项非法 URL。全部来源无效时，系统返回 `web_search_invalid_response`。正常空结果是成功的 `no_results`。系统不补造日期或加载站点图标。网页材料不具有指令权威。

| 边界 | 本版上限 |
| --- | --- |
| 解压后的 HTTP 响应 | 2 MiB，流式读取时检查 |
| 单次来源条数 | 50 |
| 单条摘录字符数 | 用户设置，最多 10000 |
| 摘录总 UTF-8 大小 | 100 KiB |
| 来源数据预算 | 240 KiB，为查询和 envelope 预留空间 |
| canonical ToolResult | 256 KiB 内 |
| 模型首轮预览 | 现有 16 KiB 通用投影，超长结果带 `read_tool_output` 入口 |

工具区分鉴权、访问拒绝、限流、服务不可用、超时、网络错误、业务失败、非法响应和响应过大。系统不把网络失败当成空结果。系统不把所有 429 都说成每日额度耗尽。请求可能已经发出时，工具如实标记可能有外部影响，并说明系统没有自动重试。这个记录不会阻止后续工具或 Run 完成。

## 8. 验收与剩余边界

定向用例覆盖配置、并发冲突、Key 权限、清理、Run 快照、队列、子任务、幂等、迁移回滚、请求字段、空结果、非法 URL、响应过大、限流、网络错误、审批、取消和错误后继续执行。跨层用例使用真实 Main/Runtime 通道。Renderer 行为用例使用测试 API。真实 Electron 隔离页面检查四种宽度、键盘勾选和密码显隐。

实现保留 SQLite、Event/Outbox、原审批模式、Durable Intent、取消、现有 ToolResult 投影和分页。实现没有引入生产依赖，也没有修改锁文件、模型 Key、Shell 网络权限或控制通道。

本轮没有使用真实账户 Key 验证两家服务。测试结果不能代替线上认证、额度和内容质量验收。本轮没有执行 Full Suite、正式应用人工验收或打包验证。依赖审计发现仓库已锁定的 certifi 和 Pillow 存在已知漏洞，本次网页搜索变更没有升级这些依赖。当前验证记录以本次交付说明为准。
