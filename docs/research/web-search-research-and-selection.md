# Eidos 内置 web_search 方案调研与筛选

> 文档分类：调研与对照。本文保留调研时的事实、建议和缺口，不作为当前实现或最终决策的证明。当前行为请以 [当前说明](../README.md#当前说明)、源码和测试为准。

调研日期：2026-10-21（北京时间）。

本文核对了服务商的官方价格页、接口文档、服务条款，以及 Eidos 当前源码。本文只提出选型建议，不代表这些服务已经接入。本文没有使用用户的 API Key，也没有执行搜索质量、延迟或中国大陆网络连通性实测。厂商公布的速度和准确率不作为本次排名依据。

## 1. 选型结论

我建议 Eidos 首批只接入 **DeepSeek Search 和 Parallel Search**。Eidos 可以用同一个 `web_search` 工具承接这两个服务。用户在设置中选择服务商和填写自己的 API Key。

| 使用需求 | 建议方案 | 选择理由 | 当前限制 |
| --- | --- | --- | --- |
| 用户主要看低价，或需要人民币付费 | **DeepSeek Search** | 官方中文价为 5 元/千次；服务提供独立的 Search 和 Fetch 接口 | 该接口在 2026-10-17 开放，长期稳定性和搜索效果仍需验证 |
| 用户愿意绑卡，希望有明确月度免费额度 | **Parallel Search `fast`** | 标准价为 1 美元/千次；符合条件的组织每月获得 5 美元额度 | 免费额度有账户资格要求；中文效果和所在网络可用性仍需验证 |
| 用户希望免费使用，且愿意接受额度变化 | **LangSearch，作为可选试用方案** | 官方当前把搜索和正文输出都列为免费功能 | 日额度按 Token 计算，实际额度需要在账户中查看；免费条款不保证持续服务 |
| 用户不愿绑卡，希望有明确的月度额度 | **Exa Lite，作为后续候选** | 官方每月提供 10 美元额度；Lite 价为 4 美元/千次 | 持续付费价格高于前两项；新增 Lite 能力刚发布 |

以上价格和免费条件分别来自 [DeepSeek 定价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[DeepSeek 开放公告](https://api-docs.deepseek.com/news/news261017/)、[Parallel 定价](https://docs.parallel.ai/getting-started/pricing)、[Parallel 月度额度说明](https://parallel.ai/blog/free-tier-parallel)、[LangSearch 定价](https://langsearch.com/pricing)、[LangSearch 使用条款](https://langsearch.com/terms-of-service)和 [Exa 定价](https://exa.ai/pricing)。

我建议暂时保留 Exa、Tavily 和 Perplexity 作为效果对照。Eidos 不需要在第一版同时实现所有服务。Brave 需要先确认搜索结果的长期保存授权。Serper 适合明确需要 Google 搜索结果的用户。自建 SearXNG 适合已有服务的用户，不适合成为桌面版的默认依赖。

这里的“入选”表示公开接口和成本适合进入接入阶段。这里的“入选”不表示质量、商用合同和发布验收已经完成。

## 2. Eidos 当前实现和选择标准

### 2.1 当前源码事实

- 当前 `runtime/` 和 `desktop/` 源码中没有 `web_search` 或 `web_fetch` 实现。
- [`tools/workspace.py`](../../runtime/eidos_runtime/tools/workspace.py) 的 `builtin_tool_registry()` 已经通过 `ToolRegistryEntry` 注册内置工具。
- [`tools/registry.py`](../../runtime/eidos_runtime/tools/registry.py) 已经定义输入契约、结果契约、执行策略、取消策略和并发策略。
- [`runtime/tool_execution.py`](../../runtime/eidos_runtime/runtime/tool_execution.py) 会校验工具结果，并提交 canonical、model 和 UI 三种结果。因此，搜索服务的结果保存条件会直接影响 Eidos 的历史回放。
- [`pyproject.toml`](../../pyproject.toml) 已经包含 `httpx2`、AnyIO、Tenacity、Pydantic 和官方 MCP SDK。搜索接入不需要再引入 Agent 框架。

相关说明见 [`docs/current/architecture.md`](../current/architecture.md) 的 Tool Execution 部分和 [`docs/current/capabilities.md`](../current/capabilities.md) 的 Tools 部分。本文以源码作为当前实现的依据。

### 2.2 本次筛选标准

1. 服务必须提供可以直接调用的搜索接口。搜索结果至少需要有标题、URL 和摘要。
2. 服务应允许 Eidos 继续使用用户选择的生成模型。
3. 服务应有可核对的计费单位。免费额度需要区分按月刷新、按日刷新和一次性试用。
4. 服务应支持直接接入 Python Runtime，且不要求用户运行额外的常驻服务。
5. 服务的结果保存、展示和再分发条件需要符合桌面会话历史的使用方式。
6. 第一版应优先减少服务商数量、账户门槛和长期维护工作。

本次没有给搜索质量打分。服务商之间的质量差异需要用同一组 Eidos 任务验证。

## 3. 通用搜索 API 对比

下表中的美元价格保留原币种。下表没有使用临时汇率换算人民币。表中的千次价格通常指 1,000 次请求；表中另有说明的项目除外。价格不包含 Eidos 自己调用生成模型产生的 Token 费用。

| 方案 | 免费政策 | 常用付费价格 | 对 Eidos 的判断 | 官方依据 |
| --- | --- | --- | --- | --- |
| **DeepSeek Search / Fetch** | 独立接口没有公布长期固定免费配额；特定 Agent 中的服务端调用有短期免费安排 | Search、Fetch 各 **5 元/千次成功请求**；英文页为各 **$0.75/千次** | **首批接入**。成本低，接口独立。两种币种采用各自官方价，不能视为汇率换算 | [中文指南](https://api-docs.deepseek.com/zh-cn/guides/search/)、[英文指南](https://api-docs.deepseek.com/guides/search/)、[定价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) |
| **Parallel Search** | 符合条件且已绑卡的组织每月获得 **$5**，额度由各 API 共用 | `fast` / `turbo` **$1/千次**；`basic` / `advanced` **$5/千次**，基础价含 10 条结果 | **首批接入**。第一版显式使用 `fast`，避免默认进入更贵模式 | [定价](https://docs.parallel.ai/getting-started/pricing)、[模式](https://docs.parallel.ai/search/modes)、[免费条件](https://parallel.ai/blog/free-tier-parallel) |
| **LangSearch** | 当前搜索功能免费；新账户从 **5 RPS** 开始，另有每分钟和每天 Token 限额 | 当前输入、输出 Token 价格均为 **$0** | **可选试用**。免费优势明显，但不能把额度写成无限或沿用旧的“每天 1,000 次” | [定价](https://langsearch.com/pricing)、[额度](https://docs.langsearch.com/limits/api-limits) |
| **Exa** | 每月 **$10**，另有 **$10** 开通奖励；不要求支付方式 | Lite **$4/千次**；Auto **$7/千次**，基础价含最多 10 条结果 | **后续候选**。明确月度免费额度适合轻度用户。Lite 和新版 Highlights 已在 10 月 19 日公告 | [定价](https://exa.ai/pricing)、[Exa 3.0 公告](https://exa.ai/blog/exa-3) |
| **Tavily** | 每月 **1,000 credits**；不要求信用卡 | 按量 **$8/千 credits**；Basic 每次 1 credit，Advanced 每次 2 credits。月套餐单 credit 更便宜 | **效果对照和兼容候选**。接入直接，但持续按量使用的费用较高 | [计费](https://docs.tavily.com/documentation/api-credits)、[Search 接口](https://docs.tavily.com/documentation/api-reference/endpoint/search) |
| **Perplexity Search** | 本次没有确认长期固定免费搜索额度 | Fast **$1/千次**；标准 **$5/千次**。独立 Search 不另收生成 Token 费 | **低价后备候选**。它提供原始搜索 API，不必使用其问答模型 | [定价](https://docs.perplexity.ai/docs/getting-started/pricing)、[Fast Search](https://docs.perplexity.ai/docs/search/fast-search) |
| **Brave Search** | Search 计划每月 **$5**；官网说明开户需要信用卡 | Search **$5/千次**，包含 Web 和 LLM Context 等搜索能力 | **有条件保留**。价格合理，但长期保存结果需要核对包含存储权的计划 | [定价](https://api-dashboard.search.brave.com/documentation/pricing)、[官网问答](https://brave.com/search/api/)、[条款](https://api-dashboard.search.brave.com/documentation/resources/terms-of-service) |
| **Serper** | 注册提供 **2,500 次**试用；官网没有把它写成月度刷新额度 | 入门包 **$50/50,000 credits**，即 **$1/千次**；额度有效期 **6 个月** | **Google 结果专用候选**。单价低，但预付金额和到期损耗会影响轻度用户 | [定价与说明](https://serper.dev/) |
| **You.com** | 本次没有确认持续免费额度 | Web Search **$5/千次**，最多 100 条结果；实时正文提取另计 | **保留比较**。多结果需求有价值，第一版暂不增加一个同价位服务商 | [接口和价格说明](https://you.com/resources/you-com-web-search-api)、[接入说明](https://you.com/docs/quickstart) |
| **Linkup** | 官网宣传 **4,000 次免费**，但当前页面未说明每月刷新 | 官网当前列 Search **$0.005–$0.006/次** | **后续候选**。免费宣传不能直接折算为持续月度额度 | [定价](https://www.linkup.so/pricing) |

### 3.1 几个会改变实际费用的细节

**DeepSeek 的直接 API 不能默认按免费计算。** 官方定价页只把特定 Agent 里的服务端 Search/Fetch 调用列为当前未计费，并给出北京时间 2026-11-01 00:00 的计费时间。Eidos 自己请求独立端点时，预算应按公开单价计算。Search 只在 HTTP 200 且结果非空时收费。请求失败、空结果和超时不收费。返回条数不改变单次价格。两个端点各有账户级 5 QPS 限制。[定价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[计费与限速规则](https://api-docs.deepseek.com/zh-cn/guides/search/)。

**Parallel 的免费额度需要绑卡，而且它按组织发放。** 同一张卡只能为一个组织激活月度额度。额度在月底到期。Marketplace 和后付费组织目前不符合该活动条件。`fast` 的基础价含 10 条结果，额外结果会增加费用。接口未传 `mode` 时默认使用 `advanced`，所以 Eidos 必须显式传值。官方目前只为 `turbo` 列出英语、日语支持，并建议更广的多语言需求使用 `basic` 或 `advanced`。因此，中文场景不能直接照搬最低档的效果承诺。[免费条件](https://parallel.ai/blog/free-tier-parallel)、[价格](https://docs.parallel.ai/getting-started/pricing)、[模式说明](https://docs.parallel.ai/search/modes)。

**Exa 的新版 Highlights 不应被重复计费。** 10 月 19 日公告明确说明，新版 Highlights 和扩展数据源包含在 Lite/Auto 的价格中。独立 Contents、额外结果和 AI 页面总结有各自价格。Eidos 需要按实际使用的功能记账，不能给每次搜索机械地再加一笔正文费。[发布公告](https://exa.ai/blog/exa-3)、[价格表](https://exa.ai/pricing)。

**Tavily 的自动参数可能提高费用。** `auto_parameters=true` 可以把 `search_depth` 提升到 Advanced。第一版若接入 Tavily，应显式使用 Basic，并把自动参数和生成答案关闭。模型需要更多信息时，再发起一次明确调用。[Search 参数说明](https://docs.tavily.com/documentation/api-reference/endpoint/search)。

**Perplexity 的批量查询有不同的计费口径。** 其官方价格页写明，一次成功的 `POST /search` 最多可携带 5 个查询，并按一次请求收费。成功但结果为空仍会收费。这个口径可能降低批量任务的费用，但本文预算表按一条查询对应一次请求计算。[计费说明](https://docs.perplexity.ai/docs/getting-started/pricing)。

## 4. 中文服务和国内平台

| 方案 | 免费或试用条件 | 正常价格 | 对 Eidos 的判断 | 官方依据 |
| --- | --- | --- | --- | --- |
| **智谱 Web Search API** | 当前指南没有把 `search_std` 列为永久免费 | `search_std` **10 元/千次**；`search_pro` **30 元/千次**；搜狗、夸克各 **50 元/千次** | **国内后备候选**。独立接口可接，不能沿用旧文章的免费说法 | [官方指南及价格表](https://docs.bigmodel.cn/cn/guide/tools/web-search) |
| **百度搜索 API** | **每天 100 次免费**；账户需要完成相关开通流程 | **36 元/千次**；免费与付费共用日调用上限 | **中文轻度使用候选**。按日额度适合稳定的小流量，但不能作为可集中使用的月度额度 | [官方搜索计费](https://ai.baidu.com/ai-doc/AppBuilder/Imca18xc6) |
| **博查 Web Search** | 免费试用包 **1,000 次/3 个月**；另有 **3.6 元/1,000 次**体验包 | 按量 **36 元/千次**；较大资源包折合 **25–32 元/千次** | **中文效果对照候选**。试用有价值，但体验包不能代表长期价格 | [官网链接的 API 定价文档](https://aq6ky2b8nql.feishu.cn/wiki/JYSbwzdPIiFnz4kDYPXcHSDrnZb) |
| **阿里云 OpenSearch 联网搜索** | 本次没有确认当前长期免费额度 | Lite **12 元/千次**；Pro **36 元/千次**；Max **46 元/千次** | **已有阿里云账户时考虑**。2026-08-31 后的新价格已替代旧的按次加改写 Token 计费 | [计费变更公告](https://help.aliyun.com/en/open-search/search-platform/product-overview/web-search-billing-change-announcement) |

上表中的百度每日免费额度、博查试用包和体验包属于不同产品条件。Eidos 不应把它们统一显示为“每月免费”。阿里云 OpenSearch 独立搜索、百炼 MCP 搜索和模型对话内搜索也不是同一个计费产品。[百炼对产品差异的说明](https://help.aliyun.com/en/model-studio/web-search)。

我没有把“国内服务”直接等同于“所有国内网络都可用”。接口连通性、账户开通条件和实际账单仍需实测。

## 5. 免费、自建和网页提取方案

| 方案 | 可以提供什么 | 成本和维护情况 | 筛选结果 | 官方依据 |
| --- | --- | --- | --- | --- |
| **SearXNG 自建** | 多搜索引擎聚合和 JSON 搜索接口 | 软件没有按次 API 费；用户仍需承担部署、上游限流和维护工作 | **支持用户已有实例；不随桌面版默认启动一套服务** | [Search API](https://docs.searxng.org/dev/search_api.html)、[官方仓库](https://github.com/searxng/searxng) |
| **公共 SearXNG 实例** | 部分实例可提供搜索 | 官方文档明确提醒，许多公共实例关闭 JSON 等输出格式，调用会返回 403 | **不作为默认服务，也不维护公共实例轮换池** | [官方格式限制](https://docs.searxng.org/dev/search_api.html) |
| **DDGS** | Python 元搜索库，可从多个搜索服务聚合结果 | 库本身采用 MIT 许可；项目仍有上游 403、限流和接口变化问题 | **可做开发实验；不承担默认搜索的稳定性承诺** | [源码与许可](https://github.com/deedy5/ddgs)、[当前问题](https://github.com/deedy5/ddgs/issues) |
| **Jina Reader / Search** | `r.jina.ai` 提取网页；`s.jina.ai` 搜索并整理结果 | 官方当前速率表要求 Search 使用 Key，并注明每次从 10,000 tokens 起计。公开套餐检索快照列过免费非商用试用；当前实时价格页未完整展示套餐卡片 | **保留为正文提取和低价搜索候选；本次不把未完整复核的套餐单价写入最终成本表** | [当前价格与速率页](https://jina.ai/api-dashboard/pricing/)、[Reader](https://jina.ai/reader/) |
| **Firecrawl** | 搜索、正文提取和站点抓取 | 每月免费 1,000 credits。搜索每 10 条结果消耗 2 credits；抓取结果正文会另用页面 credits。Hobby 月付 $19/5,000 credits | **正文抓取候选；首批不增加它的搜索适配** | [价格](https://www.firecrawl.dev/pricing)、[官方计费文档](https://github.com/firecrawl/firecrawl-docs/blob/main/billing.mdx) |
| **Valyu** | Web、学术和专有数据源的检索与内容输出 | 注册有试用额度；搜索按数据源和 retrieval 计费。官网当前的低 CPM 展示不能直接当作千次普通搜索请求价格 | **专用数据需求出现后再考虑** | [价格和计费单位](https://www.valyu.ai/pricing)、[Search 接口](https://docs.valyu.ai/api-reference/endpoint/search) |
| **SerpApi** | 多引擎搜索结果和垂直搜索 | 每月免费 250 次；入门 $25/1,000 次，Developer $75/5,000 次 | **当前不优先**。它的入门成本高于本次入选方案 | [官方价格](https://serpapi.com/pricing) |

我没有找到同时满足“免 Key、零服务成本、无需维护、可长期保证稳定”的默认全网搜索方案。这个结论是本次筛选判断，不是对所有市场产品的绝对断言。LangSearch 仍然需要账户和 Key。自建软件免费也不等于服务运行免费。

## 6. 本次排除的旧路线

- **微软 Bing Search API** 已于 2025-08-11 退役。微软给出的迁移方向是 Azure AI Agents 中的 Grounding with Bing Search。旧 Bing v7 不是新工具可继续接入的方案。[微软退役公告](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement)。
- **Google Custom Search JSON API** 已经关闭新客户接入。现有客户需要在 2027-01-01 前迁移。旧文档里的每天 100 次免费不能作为 Eidos 新产品的选型依据。[Google 官方说明](https://developers.google.com/custom-search/v1/overview)。
- **模型对话内的搜索能力** 与独立搜索接口应分开评估。前者可能同时收取模型费用，并且限制生成模型。Eidos 需要一个独立搜索工具，因此本次优先原始结果 API。智谱和阿里云官方文档都区分了这两类产品。[智谱服务划分](https://docs.bigmodel.cn/cn/guide/tools/web-search)、[百炼产品和计费说明](https://help.aliyun.com/en/model-studio/web-search)。

## 7. 结果保存和桌面分发条件

### 7.1 Brave 有明确的保存条件

Brave 当前条款限制搜索结果的保存和缓存，并为应用运行所需的临时保存保留例外。官网问答另行说明，保存结果需要选择包含存储权的计划。Eidos 会把工具结果写入 SQLite，并在之后的会话中回放。因此，我建议先确认所选账户是否有相应存储权，再决定是否接入。本文没有把普通 Search 价格直接视为包含长期存储授权的报价。[使用条款第 3 节](https://api-dashboard.search.brave.com/documentation/resources/terms-of-service)、[官网保存结果问答](https://brave.com/search/api/)。

这个判断针对 Eidos 当前的结果持久化方式。这个判断不表示 Brave 不能用于 Agent。

### 7.2 Parallel 需要限制跨用户复用

Parallel 当前客户条款允许把 API 接入客户应用。条款也要求一次查询产生的输出主要用于一个最终客户，并限制把输出提供给其他最终客户、建立数据销售服务，或用历史输出替代后续服务请求。Eidos 的首次接入应按用户 Key 和本地会话使用，不应建立跨用户共享的搜索结果缓存。[客户条款第 2、4 节](https://parallel.ai/customer-terms)。

我把个人会话保存和建立跨用户结果库视为不同使用方式。具体导出、共享和保存范围仍应按最终账户合同核对。这是接入建议，不是已经取得的额外授权。

### 7.3 其余候选仍需按实际使用方式核对

DeepSeek 开放平台协议对服务输入和输出列出了权利归属约定。但这不能直接推出第三方网页内容可以无限复制或再分发。[开放平台协议第 4 节](https://cdn.deepseek.com/policies/zh-CN/deepseek-open-platform-terms-of-service.html)。

LangSearch 的免费条款没有保证不间断服务，也保留调整和停止服务的权利。公开简版条款未把 Eidos 所需的长期保存和商业分发场景逐项写明。因此，我把它放在可选试用位置。[使用条款](https://langsearch.com/terms-of-service)。

“用户自带 Key”可以减少 Eidos 统一承担 API 费用的需要。这个模式本身不会解除服务商的结果保存和展示限制。若以后要由 Eidos 统一提供额度，团队需要服务端管理凭证和账单，不能把统一付费 Key 放进安装包。

## 8. 可直接比较的费用例子

以下金额是计算结果，不是实测账单。假设每条查询对应一次请求，每次最多取 10 条结果，且不额外调用正文、研究或生成答案接口。美元行采用公开按量价格。表中不计一次性开通奖励、税费和生成模型 Token。表中也不寻找各家所有月套餐的最低价。

| 方案 | 1,000 次/月 | 10,000 次/月 | 说明 |
| --- | --- | --- | --- |
| DeepSeek Search | **5 元** | **50 元** | 不假设有持续免费额度 |
| Parallel `fast` | **$0** | **$5** | 账户符合 $5/月条件，且额度全部用于搜索 |
| Parallel `basic` / `advanced` | **$0** | **$45** | 同上；两种模式的基础请求价相同 |
| Exa Lite | **$0** | **$30** | 只抵扣每月 $10；不使用一次性奖励 |
| Exa Auto | **$0** | **$60** | 同上 |
| Tavily Basic 按量 | **$0** | **$72** | 先用每月 1,000 free credits，剩余按 $0.008/credit 计算 |
| Perplexity Fast | **$1** | **$10** | 一条查询对应一次请求，不使用批量查询优化 |
| Serper 入门包 | **$1 用量摊销** | **$10 用量摊销** | 付费包需要实际先付 $50；未用完且到期的额度会提高实际成本 |
| LangSearch | **额度内 $0** | **无法仅凭公开资料保证** | 日 Token 配额和输出长度决定可完成多少次搜索 |

计算依据见 [DeepSeek](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[Parallel](https://docs.parallel.ai/getting-started/pricing)、[Parallel 免费条件](https://parallel.ai/blog/free-tier-parallel)、[Exa](https://exa.ai/pricing)、[Tavily](https://docs.tavily.com/documentation/api-credits)、[Perplexity](https://docs.perplexity.ai/docs/getting-started/pricing)、[Serper](https://serper.dev/)和 [LangSearch](https://docs.langsearch.com/limits/api-limits)。

DeepSeek 的正文成本也可以直接估算：如果 10,000 次搜索后，每次再独立 Fetch 两页，搜索费用为 50 元，20,000 次 Fetch 为 100 元，合计 **150 元**。这只是请求次数的算例。实际不必对每条结果都抓全文；Search 已经可以返回摘要，并可请求页面内容。[Search/Fetch 字段和计费](https://api-docs.deepseek.com/zh-cn/guides/search/)。

## 9. Eidos 后续接入建议

下面内容是设计建议，不是当前已实现能力。

### 9.1 第一版范围

第一版可以提供一个独立 `web_search`，并同时提供一个按 URL 读取正文的 `web_fetch`。前者负责找到来源，后者负责按需读取来源。工具返回结果应保留标题、URL、摘录、可用的发布时间和抓取时间。Eidos 不应把厂商生成的总结当成逐字网页原文。

第一版模型参数可以先保持为 `query` 和有上限的 `count`。设置页负责服务商、Key 和成本模式。模型不需要知道 Key，也不需要选择账单账户。Eidos 后续确实需要时间和域名过滤时，再增加可映射的公共参数；服务不支持的限制不能被静默忽略。

DeepSeek 的查询上限是 128 个 Unicode 字符。Eidos 应在对应适配器中校验，并提示模型缩短查询。Eidos 不应静默截断查询，特别是不能截掉 `site:` 或日期条件。[Search 契约](https://api-docs.deepseek.com/api/search/)。

### 9.2 最小调用链

```text
模型调用 web_search
  → ToolRegistry 输入校验和 Step Snapshot
  → 现有 ToolCallRuntime / ToolExecutionController
  → 联网权限与当前 Run 策略
  → 薄搜索适配器
  → 官方 HTTPS API
  → 有界结果校验和统一结果模型
  → SQLite ToolResult / Event / Model 与 UI projection
```

Eidos 可以复用已有 HTTP、AnyIO 和重试依赖。适配器只映射供应商输入、结果和错误。Eidos 继续拥有工具调用、权限、取消、结果提交和恢复规则。原生 API 比额外启动一个搜索 MCP 子进程更符合这次“内置工具”的目标。用户已有的搜索 MCP 仍然可以走现有扩展机制。

### 9.3 第一版需要保留的行为

- Eidos 应明确记录实际使用的服务商和模式。后备服务只使用用户已经配置和启用的账户。
- Eidos 应把 401、余额不足、429、超时、空结果和不合法响应分开处理。工具错误应回到模型循环。
- Eidos 应区分“没有查到”和“没有查成”。空结果不应被自动判定为服务故障。
- Eidos 应限制响应字节数、结果条数和模型可见摘录。长结果可以沿用现有结果分页读取能力。
- Eidos 应把网络请求放进当前受管理的取消范围。迟到的响应不能覆盖取消结果。
- Eidos 应保留已经提交的搜索结果用于恢复。Runtime 不应因为重启自动重发可能已经收费的请求。客户端超时也不能直接证明服务端未处理请求。
- Eidos 应把网页内容当作外部资料。网页里的指令不能改变 Run 权限或执行模式。
- Eidos 应保持现有 Shell 网络默认值。搜索 API 的联网能力不应顺带放开 Shell 网络权限。

## 10. 发布前的验证范围

我建议用同一组 40 个查询对比 DeepSeek、Parallel、Exa 和 Tavily。验证人员可以先检查搜索结果，再选少量任务检查生成答案。这样可以分清搜索召回问题和模型回答问题。

| 查询类型 | 数量 | 检查重点 |
| --- | --- | --- |
| 英文官方技术文档 | 10 | 官方来源是否进入前五，版本是否正确 |
| 中文技术资料和中文网站 | 10 | 中文召回、原始来源和结果可读性 |
| 近期发布和变更记录 | 10 | 发布时间和信息是否过期 |
| 小众错误、精确短语和域名限制 | 10 | 空结果、约束遵守和无关结果 |

验证人员需要记录成功率、前五结果的相关性、官方来源命中、空结果、P50/P95 延迟、实际费用，以及进入模型的 Token 数。相同查询需要采用相同结果数和相近摘录预算。最终评价应看“完成一次任务花多少”，不能只看每千次标价。

接入测试还应覆盖请求取消、429、余额不足、错误响应、超大响应、结果持久化和重启回放。正文读取需要额外覆盖重定向、页面失败和内容大小边界。本次没有执行这些验证。

## 11. 本次交付和证据范围

本次只新增本调研文档。本次没有修改生产代码、工具契约、数据库、设置页、依赖和启动链路。本次没有运行 Runtime、Desktop 或 Full Suite。上述验证计划不代表测试已通过。

博查官方价格页通过官网链接定位，再由 `feishu-wiki` 技能只读获取。读取结果如下：标题为“API 定价”；类型为 `docx`；规范 token 为 `BDmzdmRM7o51k7x7LxLcySLtnae`；revision 为 `1408`；正文可读；未解决评论为 `0`。本次没有修改该文档。原始读取文件位于 `/private/tmp/eidos-web-search-research/bocha-pricing/`，该临时目录不属于仓库交付。

其他价格和条款引用均指向服务商官方页面。本文没有用第三方测评的准确率或速度替代实测。服务商可能调整价格、额度和条款，接入和发布时需要再次核对。
