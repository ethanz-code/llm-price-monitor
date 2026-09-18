# llm-price-monitor

<p align="left">
  <img src="web/app/icon.svg" width="72" alt="LLM 价格监控 Logo" />
</p>

**LLM（AI 大模型）价格监控器**：盯住各中转站（API 中转服务）的模型价格，记录每一次变化，并用厂商官方价校准它贵不贵。

价格事实只来自直接请求得到的 HTTP 响应（JSON、页面内嵌价格表及其引用的 JS），不启动浏览器、不从页面文字猜价格；AI 只做兜底解析，无法确认时返回 `candidate` / `unavailable`，绝不编造。

## 🚀 快速开始

要求：Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)；使用 Web 界面另需 Node.js 与 npm。

```bash
git clone https://github.com/ethanz-code/llm-price-monitor.git
cd llm-price-monitor
uv sync

# 开发模式：前端热加载 + Python 改动自动重启，日常用这个
uv run price-web --dev

# 生产模式：先构建前端，再一条命令同时拉起 API + 前端
cd web && npm install && npm run build && cd ..
uv run price-web --with-frontend
```

打开 http://localhost:3000 ，首次启动会进入 `/setup` 向导：创建管理员账号 → 填 AI 密钥（可选）→ 添加站点、触发首次采集。

- AI Key 不走环境变量：在 Setup 向导或管理面板「系统设置」里填写，保存在数据库 `var/monitor.db`。还没有 AI Key？推荐阿里云百炼免费模型，[开通即送免费额度](https://help.aliyun.com/zh/model-studio/new-free-quota)，[获取 API Key](https://help.aliyun.com/zh/model-studio/get-api-key)。
- 想首次启动就带上站点：编辑 [`config/default-seed.json`](config/default-seed.json)；不加也能跑，之后在管理面板里添加。
- 云服务器部署：Docker Compose 见 [docs/deploy/docker-compose.md](docs/deploy/docker-compose.md)；交给 AI 助手部署时让它先读 [docs/deploy/ai-runbook.md](docs/deploy/ai-runbook.md)。
- 忘记密码：仓库根目录运行 `uv run price-admin` 重置。
- 要核对某个厂商官方定价页的价格：仓库根目录运行 `uv run price-page <定价页URL>`，输出该页全部模型的结构化价格（静态解析优先、AI 兜底防幻觉），用法见 [docs/pricing.md](docs/pricing.md)「拉取厂商定价页」。

## ⚙️ 配置

日常配置在**管理面板**完成，保存即写入 SQLite（`var/monitor.db`）并立即生效。仓库自带的 `config/default-seed.json` 仅作为首次启动（空库）的种子导入，之后改动不再自动生效，可在系统设置页「种子导入」手动重新写入。

站点默认 `standard` 单地址直采；"基准价在前端 JS、倍率在接口"的站点加配 `network.ratio_url`，实售价 = 基准价(USD) × 端点倍率：

```json
{
  "id": "example",
  "models": ["gpt-5.6-sol"],
  "network": {
    "url": "https://example.com/dashboard/pricing",
    "ratio_url": "https://example.com/api/public/model-pricing",
    "headers": { "Authorization": "Bearer ${EXAMPLE_SITE_TOKEN}" }
  }
}
```

请求头（含 `ratio_url.headers`）与 headless 登录态（cookies/localStorage 的值）都支持 `${ENV_VAR}` 注入，敏感值不落盘；环境变量需在进程环境中提供（如 `docker compose` 的 `environment` 或 shell `export`），缺失时采集会直接报错指明变量名，不会静默发空值。可选字段：

> **安全说明**：站点凭据（cookie、access_token、refresh_token、请求头）与 AI 密钥以明文保存在 SQLite（`var/monitor.db`），本设计面向单管理员自部署场景，换取配置即改即生效的简单性。请确保数据库文件本身不对外暴露：不要把 `var/` 目录放进公开存储或镜像，服务器上做好文件权限即可。

- `status`：渠道状态数据地址，与价格同一次采集顺带执行，存时序 `status_records`，变化写入 `status_events`
- `notice`：公告地址，默认自动请求站点根地址的 `GET /api/notice`（new-api/one-api 系标配），多版本公告存 `notice_records`
- `network.headless`：网页需登录才能看到价格时，启用无头浏览器（Playwright Chromium）渲染后再解析，打开网页前注入 cookies 和 localStorage 登录态：

```json
"network": {
  "url": "https://example.com/dashboard/pricing",
  "headless": {
    "enabled": true,
    "cookies": [{ "name": "session", "value": "abc" }],
    "localStorage": { "token": "xxx" },
    "wait_seconds": 3
  }
}
```

cookies 和 localStorage 的归属域自动取 `network.url`，不用填；`wait_seconds` 是页面渲染等待秒数（0~60，默认 3）。部署环境需安装：`pip install playwright && playwright install chromium`。

计价规则、采集方式、AI 抽取约束与数据结构见 [docs/pricing.md](docs/pricing.md)。

## 🔌 API

| 端点 | 鉴权 | 说明 |
| ---- | ---- | ---- |
| `GET /api/overview` / `latest` / `history` / `feed` / `status` / `notice` / `catalog` / `discount` / `meta` | 公开 | 数据读取（`feed` 为价格事件+站点公告合并的统一事件流；`status` 另有 `/api/status/latest`、`/api/status/events`） |
| `GET /api/geo` | 公开 | 逐站点解析公网 IP 归属地（带缓存），供首页监控地球使用 |
| `GET /api/assistant/status`、`POST /api/assistant/ask`（含 `/stream` 流式） | 公开（按 IP 每日限次） | AI 智能助手：基于平台采集的站点、价格与状态数据答问，未配置 AI 时入口隐藏 |
| `POST /api/site-submissions` | 公开（按 IP 限次） | 访客提交监控站点申请；管理员经 `GET /api/admin/site-submissions` 查看，配置了 WxPusher 时每条新提交推送到微信 |
| `POST /api/setup` | 公开 | 首次设置：创建管理员账号（仅库里没有账号时可用） |
| `POST /api/auth/login` / `POST /api/auth/logout` | 公开 | 登录签发 30 天会话 cookie / 登出清除 |
| `GET /api/tasks`、`GET /api/tasks/{id}` | 公开 | 后台任务列表与进度 |
| `POST /api/collect` | 管理员 | 触发采集（AI 兜底始终启用），结果附带官方价折扣 |
| `POST /api/catalog/refresh` | 管理员 | 从 models.dev 同步官方价目录（免密钥，秒级） |
| `GET /api/settings` / `PUT /api/settings` | 管理员 | 系统设置（AI、WxPusher 通知） |
| `GET /api/sites`、`POST /api/sites`、`PUT/DELETE /api/sites/{id}` | 管理员 | 站点配置增删改 |

## 🌐 Web 界面

Next.js 16（App Router）+ React 19 服务端渲染，自研轻量 UI kit，支持浅色 / 深色 / 跟随系统三态主题。

| 页面 | 内容 |
| ---- | ---- |
| `/` | 品牌首页：实时统计条、监控地球、最新事件流与快照预览 |
| 右下角 AI 助手 | 全站悬浮球：用自然语言问价格、比价、折扣与渠道状态（需在管理面板配置 AI，流式输出） |
| `/overview` | 中转站检测：全部站点 × 模型的最新单价；`/overview/status/{siteId}` 看单站渠道状态、公告与访问统计 |
| `/history` | 历史与事件：价格趋势图 + 变化事件列表 |
| `/catalog` | 官方价库：各厂商模型官方原价，可切「全量渠道」页签 |
| `/discount` | 折扣对比：站点价相对官方价的折扣率 |
| `/setup` / `/login` | 首次设置向导 / 管理员登录 |
| `/admin` | 管理面板：访问统计 / 站点管理 / 采集任务 / 使用文档 / 系统设置 |

`price-web` 支持 `--host`、`--port`、`--config` 参数；也可以分开跑：`uv run price-web` 起 API，`cd web && npm run start`（或 `npm run dev`）起前端。前端反代目标默认 `http://127.0.0.1:8000`，用 `PRICE_WEB_API_URL` 修改。

## 📁 输出

| 文件 | 内容 |
| ---- | ---- |
| `var/monitor.db` | 站点配置、采集历史、最新快照、变化事件、官方价与 AI 缓存的唯一真相源 |
| `var/fx-cache.json` | 汇率缓存（在线汇率源全部失败时回退） |

## 🧪 测试

```bash
uv run pytest
```
