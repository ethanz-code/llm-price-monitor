# llm-price-monitor

<p align="left">
  <img src="web/app/icon.svg" width="72" alt="LLM 价格监控 Logo" />
</p>

**LLM（AI 大模型）价格监控器**：从中转站（API 中转服务）的价格接口直接取证，
对模型输入/输出单价做确定性计价、历史监控、变化告警，并与厂商官方价对比
计算折扣率。

一句话：**盯住各中转站的模型价格，记录每一次变化，并用官方价校准它贵不贵。**

## ✨ 特性

- **多站点批量监控**：一次运行盯住多个中转站，输出完整历史、最新快照和变化事件
- **确定性计价**：识别为 one-api/new-api 的响应按倍率公式精确计算，支持分组与上下文阶梯
- **AI 兜底解析**：无法确认格式的接口交给 AI 从原始响应证据中提取，但绝不猜测
- **官方价审计**：Tavily 搜索厂商官方定价页，AI 提取官方原价，自动计算站点价折扣率
- **变化告警**：新增、涨价、降价、恢复均生成事件，可推送 webhook
- **Web 数据站**：品牌首页 + 价格总览、历史趋势、官方价库、折扣对比五个页面，
  页面上可直接触发采集与官方价刷新后台任务
- **完整证据链**：每条价格记录附带响应 URL、状态、载荷哈希和脱敏后的证据片段

> [!IMPORTANT]
> 价格事实只来自直接请求得到的 HTTP JSON 响应，不读取 DOM、不从页面文字猜价格。
> 站点实际售价与"官方价"同页出现时，只认站点价；官方价只用于折扣对比。
> AI 无法确认字段或模型归属时必须返回 `candidate` / `unavailable`，不能编造。

## 🚀 快速开始

要求：Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)；使用 Web 界面另需 Node.js 与 npm。

```bash
git clone https://github.com/ethanz-code/llm-price-monitor.git
cd llm-price-monitor
uv sync
```

配置密钥（二选一）：

```bash
# 方式一：dotenvx 加密管理（推荐）
npx @dotenvx/dotenvx set PRICE_MONITOR_AI_API_KEY sk-xxx
npx @dotenvx/dotenvx set TAVILY_API_KEY tvly-xxx

# 方式二：明文 .env（不要提交）
cp .env.example .env   # 编辑并填写真实值
```

配置站点并启动：

```bash
cp config/price-monitor.example.json config/price-monitor.json
# 编辑 config/price-monitor.json，填入你要监控的站点和目标模型

uv run price-web   # 启动 API 服务（http://127.0.0.1:8000），配合 web/ 前端使用
```

> [!TIP]
> 启动时会自动通过 `python-dotenvx` 读取 `.env`（支持 dotenvx 加密值）。
> `config/price-monitor.json` 与 `.env` 含真实凭据，已被 `.gitignore` 排除，不会提交。

## 📖 命令

唯一入口：

| 命令 | 用途 |
| --- | --- |
| `price-web` | 启动 Web 服务（FastAPI API 层），`--with-frontend` 同时拉起 Next.js 前端 |

采集、官方价刷新、折扣计算等能力全部通过 HTTP API 使用
（`POST /api/collect`、`POST /api/official/refresh`、`GET /api/discount`），
由管理面板或任意 HTTP 客户端调用。

## ⚙️ 配置

站点、目标模型、分组、倍率基准、请求配置统一写在 `config/price-monitor.json`
（模板见 [`config/price-monitor.example.json`](config/price-monitor.example.json)）。
配置在 `price-web` 首次启动（空库）时导入 SQLite（`var/monitor.db`），此后数据库是
唯一真相源；之后再改 `config/price-monitor.json` 不会生效，需删除 `var/monitor.db`
重启才会重新导入。

需要登录态的站点，请求头支持 `${ENV_VAR}` 注入，敏感值不落盘：

```json
{
  "id": "example-auth-site",
  "models": ["gpt-5.6-sol"],
  "network": {
    "url": "https://example-auth-site.com/api/pricing",
    "headers": { "Authorization": "Bearer ${EXAMPLE_SITE_TOKEN}" }
  }
}
```

计价规则、AI 抽取约束、输出数据结构等完整说明见
[docs/pricing.md](docs/pricing.md)。

## 🌐 Web 界面

Next.js 16（App Router）+ React 19 的服务端渲染数据站，自研轻量 UI kit，
不依赖重型组件库；支持浅色 / 深色 / 跟随系统三态主题。

| 页面 | 内容 |
| --- | --- |
| `/` | 品牌首页：实时统计条、最新事件流与快照预览 |
| `/overview` | 价格总览：全部站点 × 模型的最新单价 |
| `/history` | 历史与事件：价格趋势图 + 变化事件列表 |
| `/official` | 官方价库：各厂商模型官方原价 |
| `/discount` | 折扣对比：站点价相对官方价的折扣率 |
| `/admin` | 管理面板：触发"立即采集"与"刷新官方价"后台任务并轮询进度（不在公开导航） |

### 启动

```bash
# 1. 构建前端（仅首次或前端代码变更后需要）
cd web && npm install && npm run build && cd ..

# 2. 一条命令同时拉起 API 与前端
uv run price-web --with-frontend
#    API: http://127.0.0.1:8000，前端: http://localhost:3000（/api 自动反代到 API）
```

也可以分开跑：`uv run price-web` 起 API，`cd web && npm run start` 起前端；
开发调试用 `npm run dev` 替代 build + start。

> [!TIP]
> `price-web` 支持 `--host`、`--port`、`--config` 参数；`--with-frontend` 检测到
> `web/.next` 缺少构建产物时会直接提示先执行 `npm run build`。
> 8000 端口已被旧实例占用时，先停掉旧进程或用 `--port` 换端口。

环境变量（写入 `.env` 或 dotenvx）：

- `PRICE_WEB_PASSWORD` / `PRICE_WEB_USERNAME`：设置后写接口（POST/PUT/PATCH/DELETE）
  启用 Basic Auth（Next.js 代理层与 FastAPI 双层校验），读接口公开浏览；
  不设置则无鉴权，仅限本机使用
- `PRICE_WEB_API_URL`：前端反代目标，默认 `http://127.0.0.1:8000`

## 📁 输出

数据统一存放在 SQLite 数据库中：

| 文件 | 内容 |
| --- | --- |
| `var/monitor.db` | 站点配置、采集历史、最新快照、变化事件、官方价与 AI 缓存的唯一真相源 |
| `var/fx-cache.json` | 汇率缓存（在线汇率源全部失败时回退） |

> [!TIP]
> `var/price-history.jsonl`、`var/price-latest.json`、`var/price-events.jsonl`、
> `var/price-ai-cache.json` 和 `var/official-prices.json` 仅在首次启动（空库）时
> 作为存量数据导入数据库，之后新增数据只写数据库。

## 🧪 测试

```bash
uv run pytest
```

## 🔗 来源

从 Proxy-SmartAven（多中转站 AI 网关）的价格监控子包拆分而来，与网关完全
解耦，可独立运行；价格数据也可为网关的渠道选择与成本评估提供依据。
