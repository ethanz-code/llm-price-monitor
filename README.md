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
- **确定性直采**：`standard` 单地址直采；"基准价在前端 JS、倍率在接口"的站点加配 `network.ratio_url`，实售价 = 基准价(USD) × 端点倍率；HTML 页面优先识别内嵌价格表，再自动发现其引用的 JS
- **AI 兜底解析**：无法确认格式的接口交给 AI 从原始响应证据中提取，但绝不猜测
- **官方价审计**：官方原价来自开源模型目录 [models.dev](https://models.dev)，免搜索、免 AI、免密钥，每 24 小时定时拉取保持最新，折扣率全站统一用目录快照汇率
- **变化告警**：新增、涨价、降价、恢复均生成事件，可在历史页追溯
- **Web 数据站**：品牌首页 + 中转站检测、历史趋势、官方价库、折扣对比五个页面，
  页面上可直接触发采集与官方价刷新后台任务
- **完整证据链**：每条价格记录附带响应 URL、状态、载荷哈希和脱敏后的证据片段

> [!IMPORTANT]
> 价格事实只来自直接请求得到的 HTTP 响应（JSON、页面内嵌的价格表及其引用的 JS），
> 不启动浏览器、不读取 DOM、不从页面文字猜价格。
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

# 方式二：明文 .env（不要提交）
cp .env.example .env   # 编辑并填写真实值
```

> [!TIP]
> 还没有 AI Key？推荐先用阿里云百炼的免费模型起步：默认种子已预置百炼接口和
> 常用模型列表，新用户开通即送[新人免费额度](https://help.aliyun.com/zh/model-studio/new-free-quota)
> （90 天，无需实名认证），按[获取 API Key 指引](https://help.aliyun.com/zh/model-studio/get-api-key)
> 创建后填进 Setup 向导或系统设置即可。

启动（二选一）：

```bash
# 生产模式：先构建前端，再一条命令同时拉起 API + 前端
cd web && npm install && npm run build && cd ..
uv run price-web --with-frontend

# 开发模式：前端热加载 + Python 改动自动重启，日常改代码用这个
uv run price-web --dev
```

想在首次启动就带上站点，先编辑 [`config/default-seed.json`](config/default-seed.json)
填入站点和目标模型；不加也能正常跑，之后在管理面板里添加。

打开 http://localhost:3000 即可使用。首次启动（数据库里还没有管理员账号）会
进入 `/setup` 首次设置向导：创建管理员账号 → 填 AI 密钥（可选）→
跟着指引添加站点、触发首次采集。站点也可以先跑起来后在管理面板里添加。

> [!TIP]
> 启动时会自动通过 `python-dotenvx` 读取 `.env`（支持 dotenvx 加密值）。
> 填入真实站点（可能含 cookie/token）后的 `config/default-seed.json` 属于本地
> 业务配置，不要提交到公共仓库；`.env` 已被 `.gitignore` 排除。

## 📖 命令

唯一入口：

| 命令          | 用途                                                                                     |
| ------------- | ---------------------------------------------------------------------------------------- |
| `price-web`   | 启动 Web 服务（FastAPI API 层），`--with-frontend` 同时拉起 Next.js 生产前端             |
| `price-web --dev` | 开发模式：uvicorn 热重载 + Next.js dev 前端热加载（隐含 `--with-frontend`，无需先构建） |
| `price-admin` | 重置管理员账号密码（忘记密码时的找回入口，需在仓库根目录运行）                           |

采集、官方价刷新、折扣计算等能力全部通过 HTTP API 使用
（`POST /api/collect`、`POST /api/catalog/refresh`、`GET /api/discount`），
由管理面板或任意 HTTP 客户端调用。

## ⚙️ 配置

日常配置在**管理面板**完成（站点增删改、AI 等系统设置），保存即写入
SQLite（`var/monitor.db`）并立即生效。仓库自带的
[`config/default-seed.json`](config/default-seed.json) 含默认系统配置
（超时、UA、调度间隔）与预置 AI（阿里云百炼接口和常用模型，不含密钥），
无任何站点，仅作为 `price-web` 首次启动（空库）
的种子导入；想在首次启动前预置站点，直接编辑该文件即可。此后改动该文件
不再自动生效，可在系统设置页「种子导入」手动重新写入（补齐缺失 / 覆盖
写入），或删除 `var/monitor.db` 重启重新导入。

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

站点默认 `standard` 单地址直采；"基准价在前端 JS、倍率在接口"的站点
在 `network.ratio_url` 配置倍率接口，实售价 = 基准价(USD) × 端点倍率
（倍率先按模型显示名匹配，缺失时回退厂商级）：

```json
{
  "id": "example",
  "models": ["gpt-5.6-sol"],
  "network": {
    "url": "https://example.com/dashboard/pricing",
    "ratio_url": "https://example.com/api/public/model-pricing"
  }
}
```

可选的 `status` 字段为站点配置**渠道状态数据地址**，与价格同一次采集顺带执行：
GET 拉取 JSON 接口、网页 HTML 或内嵌 JS，JSON 直接采用，页面/脚本先提取内嵌
JSON，仍失败且启用 AI 时交给大模型抽取。状态以自由结构 JSON 存时序
（`status_records`），与上次快照做结构 diff，变化写入 `status_events`：

```json
{
  "id": "example",
  "models": ["gpt-5.6-sol"],
  "network": { "url": "https://example.com/api/pricing" },
  "status": { "url": "https://example.com/api/status", "headers": { "referer": "https://example.com/status" } }
}
```

可选的 `notice` 字段为站点配置**公告地址**，同样与价格同周期顺带抓取。默认无需配置：未配置时自动
请求站点根地址的 `GET /api/notice`（new-api/one-api 系站点标配，无需鉴权），取响应里 `data` 字段的
Markdown 正文；也可以把 `notice.url` 指向任意纯文本 / Markdown 公告页。new-api 系后台发布的**多条
公告**会一并抓取：从站点根地址的 `GET /api/status` 读取 `data.announcements` 列表，按"标题 + 日期 +
正文"分节拼进公告正文（置顶公告在前、列表最新在前）；`/api/status` 拿不到时回落只存单条公告。
公告正文为空不入库；内容与上一版本不同时新增一条版本（`notice_records`）并写入 `notice_changed`
事件，站点检测页可回看全部历史版本：

```json
{
  "id": "example",
  "models": ["gpt-5.6-sol"],
  "network": { "url": "https://example.com/api/pricing" },
  "notice": { "url": "https://example.com/api/notice" }
}
```

计价规则、两种采集方式的完整流程、AI 抽取约束与输出数据结构见
[docs/pricing.md](docs/pricing.md)。

## 🔌 API

| 端点                                                                                     | 鉴权   | 说明                                                        |
| ---------------------------------------------------------------------------------------- | ------ | ----------------------------------------------------------- |
| `GET /api/overview` / `latest` / `history` / `events` / `status` / `notice` / `catalog` / `discount` / `meta` | 公开   | 数据读取（`status` 另有 `/api/status/latest`、`/api/status/events`；`notice` 另有 `/api/notice/events`） |
| `POST /api/setup`                                                                        | 公开   | 首次设置：创建管理员账号（仅库里没有账号时可用）           |
| `POST /api/auth/login` / `POST /api/auth/logout`                                         | 公开   | 登录签发 30 天会话 cookie / 登出清除                        |
| `POST /api/collect`                                                                      | 管理员 | 触发采集（AI 兜底始终启用），结果附带官方价折扣 |
| `POST /api/catalog/refresh`                                                              | 管理员 | 从 models.dev 同步官方价目录（免密钥，秒级）                 |
| `GET /api/settings` / `PUT /api/settings`                                                | 管理员 | 系统设置（AI、WxPusher 通知）                      |
| `GET /api/sites`、`POST /api/sites`、`PUT/DELETE /api/sites/{id}`                        | 管理员 | 站点配置增删改                                              |
| `GET /api/tasks`、`GET /api/tasks/{id}`                                                  | 公开   | 后台任务列表与进度                                          |

## 🌐 Web 界面

Next.js 16（App Router）+ React 19 的服务端渲染数据站，自研轻量 UI kit，
不依赖重型组件库；支持浅色 / 深色 / 跟随系统三态主题。

| 页面             | 内容                                                                 |
| ---------------- | -------------------------------------------------------------------- |
| `/`              | 品牌首页：实时统计条、最新事件流与快照预览                           |
| `/overview`      | 中转站检测：全部站点 × 模型的最新单价                                |
| `/history`       | 历史与事件：价格趋势图 + 变化事件列表                                |
| `/catalog`       | 官方价库：各厂商模型官方原价，可切「全量渠道」页签看 models.dev 所有渠道价格 |
| `/discount`      | 折扣对比：站点价相对官方价的折扣率                                   |
| `/setup`         | 首次设置向导：创建管理员账号 → 配置必填密钥 → 上手指引               |
| `/login`         | 管理员登录（忘记密码可用 `price-admin` 重置）                        |
| `/admin`（含子页） | 管理面板：访问统计 / 站点管理 / 采集任务 / 使用文档 / 系统设置五个子路由 |

### 启动

| 场景 | 命令 | 说明 |
| ---- | ---- | ---- |
| 生产 | `uv run price-web --with-frontend` | 跑 `web/.next` 构建产物，需先 `npm run build` |
| 开发 | `uv run price-web --dev` | 前端 `next dev` 热加载；Python 改动 uvicorn 自动重启，无需构建 |

也可以分开跑：`uv run price-web` 起 API，`cd web && npm run start`（生产）或
`npm run dev`（开发）起前端。

> [!TIP]
> `price-web` 支持 `--host`、`--port`、`--config` 参数（`--dev` 模式下 `--config`
> 不可用，热重载子进程只按默认路径加载配置）；`--with-frontend` 检测到 `web/.next`
> 缺少构建产物时会直接提示先执行 `npm run build`。8000 端口已被旧实例占用时，
> 先停掉旧进程或用 `--port` 换端口。

环境变量（写入 `.env` 或 dotenvx）：

- `PRICE_WEB_API_URL`：前端反代目标，默认 `http://127.0.0.1:8000`

鉴权为 session 登录：管理员账号（scrypt 哈希）与登录会话签名密钥都存在
`var/monitor.db`；首次启动（库中没有账号）访问任意管理入口会进入 `/setup`
创建账号并引导配置密钥，登录态为 30 天有效的 HttpOnly cookie。旧版本通过
`PRICE_WEB_PASSWORD` / `PRICE_WEB_USERNAME` 设置的凭据会在首次启动时自动
落库为管理员账号，迁移后这两个变量不再使用；忘记密码在仓库根目录运行
`uv run price-admin` 重置。

## 📁 输出

数据统一存放在 SQLite 数据库中：

| 文件                | 内容                                                                 |
| ------------------- | -------------------------------------------------------------------- |
| `var/monitor.db`    | 站点配置、采集历史、最新快照、变化事件、官方价与 AI 缓存的唯一真相源 |
| `var/fx-cache.json` | 汇率缓存（在线汇率源全部失败时回退）                                 |

## 🧪 测试

```bash
uv run pytest
```
