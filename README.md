# llm-price-monitor

**LLM（AI 大模型）价格监控器**：从中转站（API 中转服务）的价格接口直接取证，
对模型输入/输出单价做确定性计价、历史监控、变化告警，并与厂商官方价对比
计算折扣率。

做的事一句话说清：**盯住各中转站的模型价格，记录每一次变化，并用官方价
校准它贵不贵。**

## 计价原则

- 价格事实只来自直接请求得到的 JSON 响应，不读取 DOM、不从页面文字猜价格。
- 识别为 one-api/new-api 的响应按 `model_ratio * ratio_base_price * group_ratio`
  计算输入价，再乘 `completion_ratio`、`cache_ratio`、`create_cache_ratio`。
- 无法确认格式的响应交给 AI 分析；AI 无法确认字段或模型归属时必须返回
  `candidate` / `unavailable`，不能猜测。
- 站点实际售价与"官方价"同页出现时，只认站点价；官方价只用于折扣对比。

## 命令

| 命令 | 用途 |
| --- | --- |
| `price-monitor` | 多站点批量价格监控（历史/快照/事件/官方价折扣输出） |
| `price-tracker` | 单站点价格采集（价格页面 / JSON 接口 / New API） |
| `fetch-official-prices` | Tavily 搜索 + AI 提取各厂商官方模型原价 |
| `price-discount` | 对比站点价与官方价，计算折扣率 |
| `scripts/usage_cost.py` | 渠道 Token 统计与价格快照关联，估算请求成本 |

## 快速开始

```bash
uv sync

# 1) 配置密钥：复制 .env.example 为 .env 并填写，或用 dotenvx 加密管理（推荐）：
cp .env.example .env
npx @dotenvx/dotenvx set PRICE_MONITOR_AI_API_KEY sk-xxx
npx @dotenvx/dotenvx set TAVILY_API_KEY tvly-xxx

# 2) 配置站点：复制真实配置模板并填写你要监控的站点
cp config/price-monitor.example.json config/price-monitor.json

# 3) 运行
uv run price-monitor --config config/price-monitor.json --summary
```

CLI 入口会自动通过 `python-dotenvx` 加载 `.env`（支持 dotenvx 加密值）。
`config/price-monitor.json` 与 `.env` 含真实凭据，已被 .gitignore 排除，不会提交。

## 配置与字段语义

站点、目标模型、分组、倍率基准、network 请求配置统一写在
`config/price-monitor.json`；计价规则、AI 抽取约束、输出文件说明见
[docs/pricing.md](docs/pricing.md)。

## 输出

- `var/price-history.jsonl`：每次采集的完整记录
- `var/price-latest.json`：每个站点和模型的最新快照
- `var/price-events.jsonl`：新增、变化、恢复和状态变化事件

## 来源

从 Proxy-SmartAven 网关项目的价格监控子包拆分而来，与网关完全解耦，
可独立运行。

## 测试

```bash
uv run pytest
```

## 许可证

本项目采用 [llm-price-monitor 源码可见许可证](LICENSE)：任何人可以查看、下载、修改源码用于个人、学习、测试、非商业用途或组织内部评估和部署；未经版权方事先书面授权，不得销售、再许可、白标、对外分发或用于商业化服务。
