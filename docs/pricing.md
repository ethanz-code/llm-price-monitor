# llm-price-monitor Pricing

> [!NOTE]
> 价格相关的全部能力都在 [`llm_price_monitor/`](../llm_price_monitor/) 包内：多站点价格监控、单站点采集、官方价目录同步与折扣率计算。采集与折扣核心只依赖 `httpx`，API 层基于 FastAPI，通过 `price-web` 启动。

输出统一 CNY 口径、实时汇率，每条记录自带相对官方列表价的折扣率，可直接用于看板渲染。

## 特性

- **多站点监控**：通过 API 触发采集各站点价格接口，AI 自动归一化不规范模型名，历史/快照/变化事件齐备。
- **确定性计价**：one-api / new-api 响应本地精确计算（`输入价 = model_ratio × 2 × group_ratio`，基准价固定 2）；`billing_denomination_version: 2` 新格式直接读官方标注价。
- **多地址采集**：`networks` 数组给同一站点配置多个采集地址，逐个采集后合并价格，同一模型主地址优先。
- **完整阶梯**：`tiered_expr` 与 `pricing_rules.tiers` 解析为完整上下文阶梯（tiers），保留缓存读/写价与 `request_rules`，不压缩成固定单价。
- **AI 兜底**：非 New API 格式交给 AI 做证据抽取，结果过模型名、URL、价格数值三重校验，无法闭环时降级，不猜测。
- **分组展开**：多分组（`enable_groups`）逐分组输出；分组倍率未公开时直接跳过，不伪造价格。
- **站点公告存档**：随价格采集顺带抓取各站点公告（new-api 系默认 `/api/notice`，可配 `notice.url` 覆盖），并顺带读 `/api/status` 的 `announcements` 列表把多条公告分节拼入正文（拿不到则回落单条）；正文变化才存新版本并发事件；历史版本在站点检测页回看。
- **官方原价对比**：官方列表价来自开源模型目录 [models.dev](https://models.dev)（Cloudflare CDN 直连，免搜索、免 AI、免密钥），折扣率以列表价为基准。
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
│   ├── app.py            # 应用组装：种子导入、鉴权中间件、按域挂载 APIRouter、CLI 入口
│   ├── deps.py           # 路由共享小工具（配置加载、登录态、目录读取、汇率取值）
│   ├── auth.py           # 管理员账号与会话（scrypt 哈希、签名 cookie）
│   ├── tasks.py          # 进程内后台任务注册表（线程执行、轮询状态）
│   └── routes/           # 按域拆分的端点：auth / catalog / collect / sites /
│                         # settings / data / status(含公告) / analytics(含建议)
└── catalog/
    ├── modelsdev.py      # models.dev api.json 同步与官方价目录映射（不落盘，持久化由调用方决定）
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
| `POST /api/catalog/refresh` | 从 models.dev 同步官方价目录与全量渠道价目录，秒级完成 |
| `GET /api/catalog/all` | 全量渠道价目录：models.dev 所有厂商的带价模型，仅供浏览 |
| `GET /api/discount` | 官方价 vs 监控价格，输出折扣率 |

## 快速开始

### 1. 多站点监控

在 [`config/default-seed.json`](../config/default-seed.json) 中填写站点、目标模型和接口（仓库自带版本只含默认设置、无站点；`price-web` 首次启动（空库）时导入数据库）：

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

请求体常用字段：`site_id`（单站测试）、`persist`（默认 `false`，`true` 时把历史/快照/事件写入数据库）。响应是后台任务 id，用 `GET /api/tasks/{task_id}` 轮询结果。

### 2. 官方价目录同步

```bash
curl -X POST http://127.0.0.1:8000/api/catalog/refresh
```

- 数据来自 [models.dev](https://models.dev) 的 `api.json` 全量快照（Cloudflare CDN 分发，国内外服务器均可直连），无需任何密钥，通常几秒完成。
- 定时拉取：空库启动或目录已过期（超过 24 小时未更新）时立即同步一次，之后每 24 小时定时重新拉取，无需手动维护。
- 内置八家厂商白名单（OpenAI、Anthropic、Google、xAI、Zhipu AI、DeepSeek、Moonshot AI、Alibaba Cloud）：官方 lab 条目优先，`-cn` 国内渠道条目只为补缺（如 `moonshotai-cn` 独有的模型）。
- 白名单外的平台条目（openrouter、vertex、coding-plan 等转售/托管渠道）不进官方价目录，坚持一手来源；但同一次同步会另存一份**全量渠道价目录**（`GET /api/catalog/all`），models.dev 所有厂商的带价模型都收录，在厂商定价页的「全量渠道」页签展示（`/catalog?view=all` 可直达），仅供比价，不参与折扣计算。
- 无请求体；数据源不可达时任务失败，旧目录原样保留。

### 3. 折扣率计算

```bash
curl http://127.0.0.1:8000/api/discount
```

- 常规：读数据库中的最新快照与官方价目录，零站点请求；汇率统一用目录快照自带值（与页面展示口径一致），快照缺失时才实时拉取。
- 官方价目录不存在时返回 404，所以**必须先触发目录同步**。

## 监控输出结构

`POST /api/collect` 任务结果中 `records` 的每条记录（任务结果同时给出 `events`、`errors` 与 `catalog`）：

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
    "input": 0.03,                 // 折扣率 = 站点价 CNY ÷ 官方列表价 CNY
    "output": 0.03,
    "official_input_cny": 33.7,    // 官方列表价的 CNY 口径
    "official_output_cny": 202.2,
    "source_url": "https://openai.com/index/gpt-5-6"
  }
}
```

顶层 `catalog` 记录本次使用的汇率与来源：

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

官方价目录存在数据库文档 `catalog` 中（由 `POST /api/catalog/refresh` 写入），顶层记录同步时间、数据来源与汇率，`models` 以归一化模型名为键（`GPT-5.6 Sol` 与 `gpt-5.6-sol` 同键）：

```jsonc
{
  "generated_at": 1788012031.0,
  "generated_at_iso": "2026-08-30T10:00:00+0800",
  "source": "models.dev",          // 数据来源标识
  "source_url": "https://models.dev",
  "usd_cny_rate": 6.74,            // 全表唯一汇率，刷新时点锁定
  "rate_source": "open.er-api.com 实时",
  "models": {
    "gpt5.6sol": {
      "found": true,
      "model": "gpt-5.6-sol",
      "name": "GPT-5.6 Sol",       // models.dev 展示名
      "vendor": "OpenAI",
      "currency": "USD",           // models.dev 统一 USD 口径
      "list": { "input": 5.0, "output": 30.0 },        // 官方列表价，折扣对比唯一基准
      "list_cny": { "input": 33.7, "output": 202.2 },  // 快照汇率换算的人民币价，仅供展示
      "source_url": "https://platform.openai.com/docs/models",  // 厂商官方文档
      "description": "Flagship multimodal model ...",  // models.dev 收录的英文简介
      "family": "gpt-sol",         // models.dev 产品线家族
      "limit": { "context": 1050000, "input": 922000, "output": 128000 },  // 上下文/最大输入/最大输出 token 上限
      "tier": "flagship"           // AI 档位：flagship 顶级（整行高亮）/ mainstream 主流 / null 其他；AI 不可用时缺省
    }
  }
}
```

- 只保留白名单厂商的官方 lab 条目，`-cn` 渠道只为补缺；平台转售条目（openrouter、vertex 等）不算折扣基准，见 `GET /api/catalog/all` 的全量渠道价目录。
- 折扣基准统一为官方列表价；`list_cny` 与各处折扣计算共用目录快照汇率，保证页面展示与折扣口径一致。
- `models` 顺序即展示顺序：厂商按权威清单排序（OpenAI → Anthropic → Google → xAI → Zhipu AI → DeepSeek → Moonshot → Alibaba Cloud），厂商内按发布时间倒序，最新在前。
- `release_date` 是发布日期（缺失为 null），既在全量价格页展示，也是 AI 旗舰档位判定的输入：同产品线有更新代次或发布超过约 18 个月的旧代模型不会被高亮。
- `description_zh` 是 AI 随同步翻译的中文简介（带 `desc_fp` 指纹，简介没变不重复翻译；AI 未配置或未轮到时缺省，页面回落英文原文）。官方目录每轮全量翻译，全量渠道目录条目数以千计，按每轮 300 条限额随刷新逐步补齐。
- 输入输出标价都为 0 的条目是渠道免费档（如 OpenRouter 的 `:free` 模型），页面直接标注「免费」。
- 每次刷新全量替换目录；models.dev 不可达时任务失败，旧目录原样保留，不丢数据。
- `GET /api/discount` 依赖本数据，所以**必须先触发 `POST /api/catalog/refresh`**。

## 汇率多源容灾

`official/fx.py` 按顺序探测，任一成功即返回并如实记录来源：

1. [open.er-api.com](https://www.exchangerate-api.com/docs/free)（ExchangeRate-API 免费端点，海外）
2. [jsDelivr CDN](https://github.com/fawazahmed0/exchange-api) 分发的 currency-api（国内可达性最好）
3. [Frankfurter](https://frankfurter.dev/)（欧央行数据）

全部失败时回退本地缓存 `var/fx-cache.json`（`GET /api/discount` 场景再退到官方价数据里缓存的汇率）。因此**国内外服务器均可部署**，输出的 `rate_source` 会告诉你实际用了哪个源。

> [!WARNING]
> 填入真实站点后的 `config/default-seed.json` 属于本地业务配置，不要提交到公共仓库。Cookie、Bearer token 和 AI key 应放在环境变量中，通过 `${ENV_VAR}` 在请求头里引用；发给 AI 的证据会自动脱敏，但原始配置文件不会。

## 输出文件

数据统一落在 SQLite 数据库 `var/monitor.db`；`var/` 下的存量 JSON/JSONL 文件仅在首次启动（空库）时作为种子导入。

| 文件 | 说明 |
| --- | --- |
| `var/monitor.db` | 唯一真相源：每次采集的完整记录、每个 `站点:模型:分组` 的最新快照、`new` / `changed` / `recovered` / `status_changed` / `notice_*` 事件、官方价目录与 AI 抽取结果缓存 |
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

### sites

每个站点一个对象，常用字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 站点标识 |
| `adapter` | 采集方式，只有 `standard`；旧值 `browser` / `network` / `rate_base` 载入时自动归一为 `standard`（rate_base 双地址退化为单地址） |
| `models` | 目标模型名字符串数组；俗称由 AI 从接口响应自动解析 |
| `network.url` | 价格接口完整 URL |
| `network.ratio_url` | 可选倍率接口，返回 `{pricing: [{provider, model_display, rate}]}`；命中内嵌基准价表后按 实售价 = 基准价(USD) × rate 折算（模型级倍率优先，缺失回退厂商级），输出人民币（CNY） |
| `network.params` / `network.headers` | 请求细节；header 值支持 `${ENV_VAR}` 展开 |
| `networks` | 附加采集地址数组，每项 `{url, params, headers}`（url 必填），与 `network` 同构 |
| `enabled` | `false` 跳过该站点；只写 `{"id": "..."}` 的站点自动禁用 |

配置只填标准模型名即可：AI 会从接口原始 `model_name` 自动解析别名（`gpt-5.6-sol` → `GPT-5.6 Sol` → `openai/gpt-5.6-sol`）。模型的 `enable_groups` 分组全部展开输出（分组价来自接口，非配置）。

## 工作原理

### 采集流程总览

每次运行逐站点发 HTTP 请求（每次运行选定一个 UA，不中途轮换）；`401/403` 记为 `unavailable` 且 `requires_auth=true`。采集到的每个响应都进证据链：URL（脱敏）、状态码、内容 sha256。站点用 `adapter` 字段选择采集方式，不同方式的请求次数与价格判定逻辑不同：

### standard：标准接口（默认；旧配置值 `browser` / `network` 等价）

默认请求 `network.url`（支持 `method` / `params` / `headers` / `body`），按响应类型分三条路；`networks` 数组可配置多个附加地址，逐个采集后合并价格（同一模型主地址优先）：

1. **JSON 且形如 one-api/new-api**（`data[].model_name` + `model_ratio` 等）→ **本地公式确定性计价**：`输入价 = model_ratio × 2 × group_ratio`（基准价固定 2）；`tiered_expr` / `pricing_rules.tiers` 解析为完整上下文阶梯；`billing_denomination_version=2` 的站点按 USD 基准处理并乘以站点声明的 `pricing_cny_rate`（如有）。**AI 不参与算价**——若启用了 AI，仅用于把配置里的俗称解析成证据中的正式模型名（别名，如 `GPT-5.6 Sol` → `gpt-5.6-sol`），解析后仍用本地公式重算；AI 失败时直接输出公式结果。
2. **其它格式的 JSON** → 交给 AI 做证据抽取（未配置可用 AI 时报错，不猜测）。
3. **HTML / 文本** → 先按结构特征识别页面内嵌的基准价表条目（`{category, provider, name, models[], input, output, …}`），命中目标模型即直接确定性计价（条目价为 USD，不经过 AI）；页面里没有目标模型时，自动发现页面引用的 JS chunk（最多 15 个）逐个查找，命中即确定性计价——chunk 文件名带内容哈希，每次采集重新发现，站点改版也能跟上；仍未命中再交给 AI 抽取，无 AI 时报错。

AI 兜底并不是"每个响应都丢给 AI 分析"：

- 每站每次采集**最多调用一次** AI；证据先按目标模型名递归预筛（只留含目标模型的对象），再截断到 `max_input_chars`
- 同样的证据有缓存命中，不重复调用；管理面板"测试"默认干跑，完全不调 AI、零费用
- 结果过三重校验，不闭环就降级：模型名必须出现在证据中（否则 `unavailable`）；`confirmed` 要求网络证据与价格数值同时闭环（否则 `candidate`）；`rule_only` 必须携带真实计费规则（否则视为 `unavailable`）

### 折扣与事件

1. **折扣**：官方价目录存在时，用目录快照汇率把站点价与官方列表价统一折算 CNY 后相除（快照缺失时才实时拉取）。
2. **事件**：对比数据库中的旧快照生成事件（新增/涨价/降价/恢复/状态变化），公告正文变化生成 `notice_init` / `notice_changed` 事件，一并写入数据库供事件审计查询；指纹只含价格口径字段（单价、缓存价、计费规则数值、币种、状态、认证），AI 抽取每次会漂移的上下文边界与备注文本不参与，避免价格没变也发"价格变化"事件。

## 测试

```bash
uv sync --dev --all-extras
uv run pytest tests/test_price_monitor.py tests/test_price_tracker.py tests/test_catalog.py tests/test_webapi.py -q
```

测试使用 HTTPX mock transport 和进程内替身，不请求真实站点。
