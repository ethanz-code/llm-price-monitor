<div align="center">
  <img src="web/app/icon.svg" width="88" alt="llmprices.cn" />

  <h1>llmprices.cn</h1>

  <p><strong>LLM API 中转站价格监控器</strong><br/>
  盯住各中转站的模型价格，记录每一次变化，用厂商官方价校准它贵不贵。<br/>
  每条数据都附来源链接，点开就能核对。</p>

  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white" />
    <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" />
    <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white" />
    <img alt="SQLite" src="https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white" />
    <img alt="License" src="https://img.shields.io/badge/License-PSAL_1.0-blue" />
  </p>

  <p>
    <a href="https://llmprices.cn">在线体验</a> ·
    <a href="#快速开始">快速开始</a> ·
    <a href="#功能">功能</a> ·
    <a href="docs/pricing.md">价格口径</a> ·
    <a href="docs/deploy/docker-compose.md">部署</a>
  </p>
</div>

## 功能

- **多站点监控**：定时采集各中转站的价格、渠道状态与公告，新增 / 涨价 / 降价 / 恢复自动记成事件。
- **确定性计价**：one-api / new-api 响应本地公式精确计算；价格事实只来自直接请求得到的 HTTP 响应（JSON、页面内嵌价格表及其引用的 JS），不启动浏览器、不从页面文字猜价格。
- **AI 兜底**：非标准格式交给 AI 做证据抽取，结果过模型名、URL、价格数值三重校验，无法闭环就降级，绝不编造。
- **官方价审计**：官方目录来自 [models.dev](https://models.dev)，国内厂商以官方定价页抓取为基准，站点价相对官方价输出折扣率。
- **站点发现**：从公开聚合源拉取候选中转站并探测可导入性，管理面板或公开的发现页一键导入；访客也能提交站点检测申请。
- **模型榜单**：Artificial Analysis 排名、智能指数、速度与延迟，每日定时同步。
- **AI 助手**：全站悬浮球，用自然语言问价格、比价、折扣与渠道状态（可选，流式输出）。
- **AI 友好**：站点自带 [`llms.txt`](https://llmprices.cn/llms.txt) 与全量版 `llms-full.txt`，AI 应用可直接读懂本站数据口径。

## 快速开始

要求 Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)；使用 Web 界面另需 Node.js 与 npm。

```bash
git clone https://github.com/ethanz-code/llmprices.cn.git
cd llmprices.cn
uv sync

# 开发模式：前端热加载 + Python 改动自动重启，日常用这个
uv run price-web --dev

# 生产模式：先构建前端，再一条命令同时拉起 API + 前端
cd web && npm install && npm run build && cd ..
uv run price-web --with-frontend
```

打开 http://localhost:3000 ，首次启动会进入 `/setup` 向导：创建管理员账号 → 填 AI 密钥（可选）→ 添加站点、触发首次采集。

> [!TIP]
> - AI 密钥在 Setup 向导或管理面板「系统设置」里填，保存在数据库，不走环境变量；还没有 Key？推荐[阿里云百炼免费模型](https://help.aliyun.com/zh/model-studio/new-free-quota)，[获取 API Key](https://help.aliyun.com/zh/model-studio/get-api-key)。
> - 忘记密码：仓库根目录运行 `uv run price-admin` 重置。
> - 要核对某厂商官方定价页：`uv run price-page <定价页URL>`，输出该页全部模型的结构化价格。
> - 想首次启动就带上站点：编辑 [`config/default-seed.json`](config/default-seed.json)；不加也能跑，之后在管理面板里添加。

## 配置

日常配置在**管理面板**完成，保存即写入 SQLite（`var/monitor.db`）并立即生效；`config/default-seed.json` 仅在首次启动（空库）时作种子导入。

站点默认单地址直采；"基准价在前端 JS、倍率在接口"的站点加配倍率接口，实售价 = 基准价(USD) × 端点倍率：

```json
{
  "id": "example",
  "network": {
    "url": "https://example.com/dashboard/pricing",
    "ratio_url": "https://example.com/api/public/model-pricing",
    "headers": { "Authorization": "Bearer <站点令牌>" }
  }
}
```

> [!WARNING]
> 站点凭据（cookie、token、请求头）与 AI 密钥以明文保存在 SQLite（`var/monitor.db`），本设计面向单管理员自部署场景。请确保数据库文件不对外暴露：不要把 `var/` 目录放进公开存储或镜像。

检测模型清单、认证与续签（new-api 会话）、无头浏览器、采集出口代理等进阶配置见 [docs/pricing.md](docs/pricing.md)「配置文件」。

## 命令行

| 命令 | 用途 |
| --- | --- |
| `price-web` | 启动服务（`--dev` 开发模式，`--with-frontend` 同时拉起前端） |
| `price-admin` | 管理员工具：重置密码、整理站点配置、导入发现的站点 |
| `price-page` | 拉取厂商定价页，输出全部模型的结构化价格 |
| `price-discover` | 从公开聚合源发现候选中转站并探测可导入性 |

前后端也可分开跑：`uv run price-web` 起 API，`cd web && npm run start` 起前端；反代目标默认 `http://127.0.0.1:8437`，用 `PRICE_WEB_API_URL` 修改。`price-web` 另支持 `--host` / `--port` / `--config`。

## Web 界面

Next.js 16（App Router）+ React 19 服务端渲染，支持浅色 / 深色 / 跟随系统三态主题。

| 页面 | 内容 |
| --- | --- |
| `/` | 品牌首页：实时统计、监控地球、最新事件流 |
| `/overview` | 模型数据：全部站点 × 模型的最新单价，单站渠道状态与公告 |
| `/calculator` | 花费计算：按输入 / 输出 / 缓存单价估算 token 总花费，可分享链接 |
| `/history` | 价格趋势图与变化事件列表 |
| `/rankings` | 模型榜单：排名、智能指数、速度与延迟 |
| `/catalog` · `/discount` | 官方价库与站点折扣对比 |
| `/discover` | 站点发现：公开的候选中转站清单与导入入口 |
| `/articles` | 站内文章：中转站行业观察与避坑（静态维护） |
| `/admin` | 管理面板：站点管理（含站点发现）、厂商定价源、采集任务、系统设置 |

## API

| 端点 | 鉴权 | 说明 |
| --- | --- | --- |
| `GET /api/overview` `latest` `history` `feed` `status` `notice` `catalog` `discount` `rankings` `geo` | 公开 | 数据读取：最新单价、事件流、渠道状态、公告、官方目录、折扣、榜单、IP 归属地 |
| `GET /api/assistant/status` · `POST /api/assistant/ask` | 公开（限次） | AI 助手答问，支持流式 |
| `POST /api/site-submissions` | 公开（限次） | 访客提交站点检测申请 |
| `POST /api/setup` · `POST /api/auth/login` / `logout` | 公开 | 首次设置、会话登录 |
| `GET /api/tasks` | 公开 | 后台任务列表与进度 |
| `POST /api/collect` | 管理员 | 触发采集，结果附带官方价折扣 |
| `POST /api/catalog/refresh` · `POST /api/rankings/refresh` | 管理员 | 同步官方价目录 / 抓取榜单 |
| `GET/POST/PUT/DELETE /api/sites` · `/api/vendor-sources` | 管理员 | 站点与厂商定价源管理 |
| `GET/PUT /api/settings` | 管理员 | 系统设置（AI、通知、采集出口） |

## 项目结构与部署

| 路径 | 内容 |
| --- | --- |
| `llm_price_monitor/` | 后端：采集、计价、事件、官方价审计、API（FastAPI） |
| `web/` | 前端：站点页面与管理面板（Next.js） |
| `var/monitor.db` | 站点配置、采集历史、快照、事件、官方价——唯一真相源 |
| `var/fx-cache.json` | 汇率缓存（在线汇率源全部失败时回退） |

云服务器部署走 Docker Compose，见 [docs/deploy/docker-compose.md](docs/deploy/docker-compose.md)；交给 AI 助手部署时让它先读 [docs/deploy/ai-runbook.md](docs/deploy/ai-runbook.md)。

## 测试与文档

```bash
uv run pytest tests -q   # HTTPX mock，不请求真实站点
```

| 文档 | 内容 |
| --- | --- |
| [docs/pricing.md](docs/pricing.md) | 计价规则、采集方式、认证续签、模块结构与完整配置说明 |
| [docs/design-guide.md](docs/design-guide.md) | UI 规范与视觉语言 |
| [docs/lessons.md](docs/lessons.md) | 历史踩坑速查 |
