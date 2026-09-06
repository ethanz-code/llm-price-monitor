# llm-price-monitor Pricing

> [!NOTE]
> 价格相关的全部能力都在 [`llm_price_monitor/`](../llm_price_monitor/) 包内：多站点价格监控、单站点采集、官方原价搜索与折扣率计算。采集与折扣核心只依赖 `httpx`，API 层基于 FastAPI，通过 `price-web` 启动。

输出统一 CNY 口径、实时汇率、促销价优先，每条记录自带相对官方价的折扣率，可直接用于看板渲染。

## 特性

- **多站点监控**：通过 API 触发采集各站点价格接口，AI 自动归一化不规范模型名，历史/快照/变化事件齐备，可推送 webhook。
- **确定性计价**：one-api / new-api 响应本地精确计算（`输入价 = model_ratio × ratio_base_price × group_ratio`）；`billing_denomination_version: 2` 新格式直接读官方标注价。
- **完整阶梯**：`tiered_expr` 与 `pricing_rules.tiers` 解析为完整上下文阶梯（tiers），保留缓存读/写价与 `request_rules`，不压缩成固定单价。
- **AI 兜底**：非 New API 格式交给 AI 做证据抽取，结果过模型名、URL、价格数值三重校验，无法闭环时降级，不猜测。
- **分组展开**：多分组（`enable_groups`）逐分组输出；分组倍率未公开时直接跳过，不伪造价格。
- **官方原价对比**：Tavily 搜索厂商官方定价页，AI 提取旗下全部模型的官方价，自动采用仍在有效期的促销价，计算折扣率。
- **证据脱敏**：Authorization、Cookie、各类 token 自动脱敏；配置敏感值支持 `${ENV_VAR}` 引用。

## 模块结构

```
llm_price_monitor/
├── tracker.py            # 单站点确定性计价核心（PriceRecord、newapi_price_record）
├── adapters.py           # 站点适配器：直接请求价格接口并计算价格记录
├── report.py             # 运行编排与报告（run_once、事件分类、持久化）
├── store.py              # SQLite 存储层（站点配置、历史/快照/事件/文档的唯一真相源）
├── ai.py                 # AI 价格抽取与结果校验
├── config.py             # 强类型配置（JSON/数据库 → MonitorConfig）
├── evidence.py / matching.py / units.py / useragent.py / env.py
├── webapi/
│   ├── app.py            # FastAPI 应用（只读端点 + 采集/官方价刷新后台任务）
│   └── tasks.py          # 进程内后台任务注册表（线程执行、轮询状态）
└── official/
    ├── fetch.py          # 官方价抓取编排（不落盘，持久化由调用方决定）
    ├── tavily.py         # Tavily key 解析与搜索（HTTP API + tvly CLI 兜底）
    ├── extraction.py     # AI 提取官方价与条目规范化（促销价优先）
    ├── search.py         # 按厂商的搜索编排
    ├── fx.py             # USD→CNY 汇率多源容灾
    ├── discount.py       # 折扣计算纯函数
    └── jsonio.py / normalize.py
```

## 命令行入口

已注册到 pyproject `[project.scripts]` 的唯一命令：

| 命令 | 用途 |
| --- | --- |
| `price-web` | 启动 Web 服务（FastAPI API 层），`--with-frontend` 同时拉起 Next.js 前端 |

监控、官方价搜索、折扣计算等能力全部通过 HTTP API 使用
（写接口需要管理员 session，见 README「鉴权」说明）：

| API | 用途 |
| --- | --- |
| `POST /api/collect` | 多站点批量监控，输出含折扣率 |
| `POST /api/official/refresh` | Tavily 搜索官方原价，生成总结 JSON |
| `GET /api/discount` | 官方价 vs 监控价格，输出折扣率 |

## 快速开始

### 1. 多站点监控

在 [`config/price-monitor.json`](../config/price-monitor.json) 中填写站点、目标模型和接口（`price-web` 首次启动时导入数据库）：

```json
{
  "sites": [
    { "id": "example-site", "models": ["gpt-5.6-sol"], "network": { "url": "https://example.com/api/pricing" } }
  ]
}
```

```bash
curl -X POST http://127.0.0.1:8000/api/collect -H 'Content-Type: application/json' -d '{"persist": true}'
```

请求体常用字段：`site_id`（单站测试）、`persist`（默认 `false`，`true` 时把历史/快照/事件写入数据库并推送 webhook）、`dry_run`（AI 只输出请求预览，不落库）。响应是后台任务 id，用 `GET /api/tasks/{task_id}` 轮询结果。

### 2. 官方原价搜索

```bash
curl -X POST http://127.0.0.1:8000/api/official/refresh -H 'Content-Type: application/json' -d '{}'
```

- **默认按厂商搜索**：内置 OpenAI、Anthropic、Google、xAI、DeepSeek、Moonshot AI、Alibaba Cloud、Zhipu AI 八家，收敛到官方域名后由 AI 提取**旗下全部模型**的官方价；请求体 `{"vendors": ["厂商1", "厂商2"]}` 可覆盖。
- Tavily key 解析顺序：配置 `settings.tavily_api_key`（种子导入时可由 `TAVILY_API_KEY` 环境变量解析）> `TAVILY_API_KEY` 环境变量 > tvly CLI 登录态。
- AI 设置（`base_url` / `model` / `api_key_env`）来自数据库中的配置，`ai` 段未启用或未配置时接口直接返回 400。

### 3. 折扣率计算

```bash
curl http://127.0.0.1:8000/api/discount
```

- 常规：读数据库中的最新快照与官方价数据，零站点请求，实时拉取汇率（失败时回退官方价数据里缓存的汇率）。
- 官方价数据不存在时返回 404，所以**必须先触发官方价刷新**。

## 监控输出结构

`POST /api/collect` 任务结果中 `records` 的每条记录（任务结果同时给出 `events`、`errors`、`ai_previews` 与 `official_prices`）：

```jsonc
{
  "site_id": "sudocode",
  "model": "gpt-5.6-sol",
  "input_price": 1.0,              // 站点标价币种下的单价（每 1M tokens）
  "output_price": 6.0,
  "unit": "CNY/1M tokens",
  "group": "default",              // 计费分组；New API 站点每个分组一条记录
  "tiers": [                       // 统一阶梯数组；普通模型是单档
    {
      "group": "default",
      "name": "standard",
      "context_min": null, "context_max": 272000,   // 上下文阶梯边界
      "input_price": 6.25, "output_price": 37.5,
      "cache_read_price": 0.63, "cache_create_price": 7.81,
      "unit": "CNY/1M tokens"
    }
  ],
  "price_status": "confirmed",     // 见下表
  "requires_auth": false,
  "discount": {                    // 官方价数据存在时自动附加
    "input": 0.04,                 // 折扣率 = 站点价 CNY ÷ 官方价 CNY
    "output": 0.04,
    "official_input_cny": 26.97,   // 官方采用价（促销价或列表价）的 CNY 口径
    "official_output_cny": 134.84,
    "basis": "promo",              // 官方采用价来源：promo（促销）| list（列表价）
    "source_url": "https://openai.com/index/gpt-5-6"
  }
}
```

顶层 `official_prices` 记录本次使用的汇率与来源：

```json
{ "enabled": true, "usd_cny_rate": 6.74, "rate_source": "open.er-api.com 实时", "generated_at": "..." }
```

`price_status` 语义：

| 状态 | 含义 |
| --- | --- |
| `confirmed` | 价格有明确证据，或 New API 倍率计算成功 |
| `candidate` | 有价格证据但不完整 |
| `rule_only` | 只有计费规则（阶梯/表达式/倍率），单价按规则折算，`tiers` 里有每档价格 |
| `unavailable` | 无法取得可靠价格；`requires_auth` 为 `true` 表示接口需要登录 |

## 官方价数据结构

官方价总结存在数据库文档中（由 `POST /api/official/refresh` 写入），顶层记录搜索时间与汇率，`models` 以归一化模型名为键（`GPT-5.6 Sol` 与 `gpt-5.6-sol` 同键）：

```jsonc
{
  "generated_at": 1788012031.0,
  "generated_at_iso": "2026-08-30T10:00:00+0800",
  "search_engine": "tavily",
  "usd_cny_rate": 6.74,            // 全表唯一汇率，CNY 换算统一用它
  "rate_source": "open.er-api.com 实时",
  "models": {
    "gpt5.6sol": {
      "found": true,
      "model": "gpt-5.6-sol",
      "vendor": "OpenAI",
      "currency": "USD",           // 厂商页面标价币种，价格均为该币种原值
      "list": { "input": 5.0, "output": 30.0 },                                            // 官方列表价
      "promo": { "input": 4.0, "output": 20.0, "ends_at": "2026-11-21", "valid": true },  // 促销价，无则 null
      "effective": { "input": 4.0, "output": 20.0, "basis": "promo" },                    // 实际采用价
      "source_url": "https://openai.com/index/gpt-5-6",
      "notes": "...",
      "searched_at": 1788012031.0
    }
  }
}
```

- 只接受厂商一手来源；三方转售站、聚合表的价格一律不算。
- 存在**仍在有效期**的官方促销价时，`effective` 自动采用促销价（`basis: "promo"`）；促销过期后重新刷新会切回列表价。
- 每次刷新全量重搜；某模型本次失败时保留上一轮结果（标记 `from_previous_run`），不因网络波动丢数据。
- `GET /api/discount` 依赖本数据，所以**必须先触发 `POST /api/official/refresh`**。

## 汇率多源容灾

`official/fx.py` 按顺序探测，任一成功即返回并如实记录来源：

1. [open.er-api.com](https://www.exchangerate-api.com/docs/free)（ExchangeRate-API 免费端点，海外）
2. [jsDelivr CDN](https://github.com/fawazahmed0/exchange-api) 分发的 currency-api（国内可达性最好）
3. [Frankfurter](https://frankfurter.dev/)（欧央行数据）

全部失败时回退本地缓存 `var/fx-cache.json`（`GET /api/discount` 场景再退到官方价数据里缓存的汇率）。因此**国内外服务器均可部署**，输出的 `rate_source` 会告诉你实际用了哪个源。

> [!WARNING]
> `config/price-monitor.json` 属于本地业务配置，不要提交到公共仓库。Cookie、Bearer token 和 AI key 应放在环境变量中，通过 `${ENV_VAR}` 在请求头里引用；发给 AI 的证据会自动脱敏，但原始配置文件不会。

## 输出文件

数据统一落在 SQLite 数据库 `var/monitor.db`；`var/` 下的存量 JSON/JSONL 文件仅在首次启动（空库）时作为种子导入。

| 文件 | 说明 |
| --- | --- |
| `var/monitor.db` | 唯一真相源：每次采集的完整记录、每个 `站点:模型:分组` 的最新快照、`new` / `changed` / `recovered` / `status_changed` 事件、官方价总结与 AI 抽取结果缓存 |
| `var/fx-cache.json` | 汇率缓存（在线源全部失败时的回退） |

折扣只挂在输出层，不写入历史/快照，因此不会触发虚假的价格变化事件。

## 配置文件

配置分三块：`settings`（运行参数）、`ai`（AI 抽取）和 `sites`（站点列表）；配置文件在 `price-web` 首次启动（空库）时导入数据库，此后以数据库为准。

### ai

```json
{
  "enabled": true,
  "base_url": "https://.../compatible-mode/v1",
  "model": "qwen3.8-flash",
  "api_key_env": "PRICE_MONITOR_AI_API_KEY",
  "max_tokens": 4000
}
```

- API key 也可以直接写在 `api_key`，推荐 `api_key_env` 从环境变量读取。
- `models` 可配置多个候选模型，每次请求随机选用一个。
- AI 原始结果按证据哈希缓存在数据库中，同一接口证据不变时不重复调用。
- `dry_run: true` 时只输出请求预览不调用。

### sites

每个站点一个对象，常用字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 站点标识 |
| `models` | 目标模型。字符串或 `{"name": "...", "group": "...", "aliases": [...]}` |
| `network.url` | 价格接口完整 URL |
| `network.method` / `params` / `headers` / `body` / `body_type` / `response_path` | 请求细节；header 值支持 `${ENV_VAR}` 展开 |
| `ratio_base_price` | 倍率基准价，默认 `2`（model_ratio=1 对应 2 单位货币/1M tokens） |
| `currency` | `CNY`（默认）或 `USD`，站点标价币种 |
| `enabled` | `false` 跳过该站点；只写 `{"id": "..."}` 的站点自动禁用 |

配置只填标准模型名即可：AI 会从接口原始 `model_name` 自动解析别名（`gpt-5.6-sol` → `GPT-5.6 Sol` → `openai/gpt-5.6-sol`）。不指定 `group` 时，模型所有 `enable_groups` 分组都会展开输出。

## 工作原理

1. **采集**：逐站点发 HTTP 请求（每次运行选定一个 UA，不中途轮换）；`401/403` 记为 `unavailable` 且 `requires_auth=true`。
2. **识别**：命中 one-api/new-api 结构时走本地确定性计价；`billing_denomination_version=2` 的站点按 USD 基准处理并乘以站点声明的 `pricing_cny_rate`（如有）。
3. **AI 抽取**：非标准响应把脱敏后的 JSON 证据连同计价规则提示词交给 AI；`confirmed` 要求网络证据和价格数值同时闭环，否则降级 `candidate`。
4. **折扣**：官方价数据存在时，按实时汇率把站点价与官方采用价统一折算 CNY 后相除。
5. **事件**：对比数据库中的旧快照生成事件，推送到 `settings.webhook` 指定的 webhook（种子导入时可由 `webhook_env` 环境变量名解析）。

## 测试

```bash
uv sync --dev --all-extras
uv run pytest tests/test_price_monitor.py tests/test_price_tracker.py tests/test_official_pricing.py tests/test_webapi.py -q
```

测试使用 HTTPX mock transport 和进程内替身，不请求真实站点。
