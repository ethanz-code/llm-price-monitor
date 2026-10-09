# Docker Compose 部署

面向「有一台 Linux 服务器、用 Docker 把项目跑起来」的部署手册。把服务器交给 AI 助手部署时，让它先读 [ai-runbook.md](ai-runbook.md)。

## 架构

```
浏览器
  │  http/https（80/443，由面板 Nginx 或自有 Nginx 反代）
  ▼
宿主机 3000 端口 ──► web 容器（Next.js :3000，唯一对外端口）
                        │  容器网络内 /api 反代
                        ▼
                     api 容器（FastAPI :8437 + 内置调度，不对外发布）
                        │
                        ▼
                     ./var/monitor.db（SQLite，bind mount 到宿主机）
```

| 组件 | 端口 | 说明 |
| --- | --- | --- |
| web 容器 | 宿主 `3000` | Next.js 生产服务，页面与 `/api` 反代入口 |
| api 容器 | 仅容器网络 `8437` | FastAPI 后端 + 内置定时采集，永不直接暴露公网 |
| `./var/` | — | SQLite 库、官方价目录、事件流等全部运行数据（密钥也在其中，见下） |

## 前置条件

- Linux 服务器（x86_64 / arm64），**内存 ≥ 2 GB**：Next.js 构建阶段吃内存，1 GB 机器先加 swap（见 FAQ）。
- Docker Engine 24+ 与 Compose v2（`docker compose version` 能出版本号即可；1Panel / 宝塔安装的 Docker 同样可用）。

## 首次部署

```bash
# 1. 拉代码（约定 /opt 目录，可自定义）
cd /opt
git clone https://github.com/ethanz-code/llm-price-monitor.git
cd llm-price-monitor

# 2.（可选）首次启动前预置站点：编辑 config/default-seed.json

# 3. 构建并启动（首次构建几分钟）
docker compose up -d --build

# 4. 跟日志确认两个容器都健康
docker compose logs -f
```

打开 `http://服务器IP:3000`：数据库里还没有账号时会进入 `/setup` 创建管理员 → 填 AI 密钥 → 按指引添加站点、触发首次采集。

## 数据接口封锁（推荐）

默认情况下，浏览器可以直接访问 `http://站点/api/overview` 这类读接口拿走整份 JSON 数据。设置内网令牌后，这些读接口只对 Next 服务端渲染请求和管理员会话开放——访客照常看页面，但别人调不到你的数据 API：

```bash
# 在 docker-compose.yml 同目录准备 .env（compose 会自动读取；变量说明见 .env.example）
cp .env.example .env
nano .env   # PRICE_WEB_INTERNAL_TOKEN 填入 openssl rand -hex 32 的输出
docker compose up -d
```

- 令牌只放在服务器 `.env` 里，两个容器共用同一个值；不设置则不启用封锁（本地开发不需要）。
- 保持公开的例外：`/api/health`（健康检查）、`/api/auth/state`（登录页跳转判断）、`/api/assistant/status`（AI 助手浮球）；写接口与管理接口原有鉴权不变。

## 反向代理与 HTTPS（推荐）

3000 端口只应内网可达，对外统一走 80/443，按你的环境三选一：

- **宝塔面板**：网站 → 添加站点（只填域名，不建数据库）→ 站点设置 → 反向代理 → 目标 URL `http://127.0.0.1:3000`；「SSL」页申请 Let's Encrypt 证书并开启强制 HTTPS。
- **1Panel**：网站 → 创建网站 → 反向代理 → 代理地址 `http://172.17.0.1:3000`（OpenResty 是容器，**不能写 127.0.0.1**）；证书在「网站 → 证书」申请。
- **无面板裸 Nginx**：

```nginx
server {
    listen 80;
    server_name price.example.com;
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**防火墙三层都要确认**（详见 [ai-runbook.md](ai-runbook.md) 第 6 节）：云厂商安全组、面板防火墙页、系统 firewalld/ufw，均只放行 22/80/443；3000 与 8437 不对公网开放。

## 验证

| 检查 | 命令 / 方式 | 期望 |
| --- | --- | --- |
| 容器状态 | `docker compose ps` | 两个容器 `Up (healthy)` |
| 前端 | `curl -I http://127.0.0.1:3000` | 200 |
| API | `docker compose exec api python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8437/api/meta').status)"` | 200 |
| 公网 | 浏览器打开域名 | 能看到首页并完成 `/setup` |

## 日常运维

```bash
# 更新到最新代码（APP_TAG=镜像标签=线上版本号，见 .env）
cd /opt/llm-price-monitor
git pull
echo "APP_TAG=$(git rev-parse --short HEAD)" >> .env
docker compose up -d --build

# 回滚到上一版：数据在 var/，改回上一版 APP_TAG 即可（旧镜像仍在本地），不动数据
echo "APP_TAG=<上一版短 SHA>" >> .env
docker compose up -d --no-build

# 日志
docker compose logs -f api    # 或 web

# 重启 / 停止（down 不加 -v 就不会动 ./var 数据）
docker compose restart
docker compose down

# 忘记管理员密码：重置（按提示输入用户名与新密码）
docker compose exec api price-admin
```

### 备份与恢复

```bash
# 在线备份 SQLite（推荐，不停服务）
sqlite3 var/monitor.db ".backup 'var/backup-$(date +%F).db'"

# 或停机冷备：运行数据一起打包（密钥在数据库里，随 var/ 一起备份）
docker compose stop api
tar czf price-backup-$(date +%F).tgz var/
docker compose start api
```

恢复：停掉容器，把备份的 `var/` 覆盖回 `./var`，再 `docker compose up -d`。

## 数据与目录

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `./var/monitor.db` | 站点配置、采集历史、事件、管理员账号 | 唯一真相源，定期备份 |
| `./var/` 其余文件 | 官方价目录、汇率缓存、事件流 | 可随库一起备份 |
| 镜像内 `config/default-seed.json` | 首次启动种子 | 只在空库时生效；改它需要重新 build api 镜像，或放开 compose 里注释的 config 挂载 |

## FAQ

- **3000 端口被占用**：把 `docker-compose.yml` 里的 `"3000:3000"` 改成 `"8080:3000"`，反代目标同步改。
- **构建卡死 / 内存不足**：1 GB 机器先加 2 GB swap：
  `fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`，然后重新 build。
- **首页能开但数据请求 404**：前端镜像构建时反代目标不对。确认 Dockerfile 的 `PRICE_WEB_API_URL` ARG 与 compose 里 web 的 environment 都是 `http://api:8437`，然后 `docker compose build web && docker compose up -d`。
- **磁盘越来越满**：`docker system df` 看占用，`docker image prune -f` 清掉旧构建的悬空镜像。
- **时间不对**：镜像默认 `TZ=Asia/Shanghai`，需改时区时调整 Dockerfile / compose 里的 TZ。
- **能否多开几个 api 副本扩容**：不能。定时调度与任务状态都在 api 进程内存里，多副本会重复采集、任务状态错乱，只允许单实例。
- **部分站点直连采集不通（被墙或限制大陆访问）**：宿主机跑一个代理客户端，在管理面板「系统设置 → 采集出口」填备用代理地址；容器内指向宿主机写 `http://172.17.0.1:7890`（Docker 桥接网关，不是 127.0.0.1）。采集直连优先，只有直连失败的域名自动走代理，面板保存后从下一轮采集生效。

## 采集兜底代理（机场订阅）

境内服务器直连不了的站点，在服务器上跑一个 mihomo（Clash Meta 内核）吃机场订阅，
给「系统设置 → 采集出口」提供本地代理端口。代理服务已写在 `docker-compose.yml`
里（profile 名 `proxy`，默认不启动），两步启用：

```bash
# 1. 机场配置：复制模板、填入订阅链接（V2Board 系末尾加 &flag=clash）
cp mihomo.example.yaml mihomo/config.yaml && nano mihomo/config.yaml
chmod 600 mihomo/config.yaml   # 链接自带 token，别让别的用户读到

# 2. .env 里加一行启用 profile（没有 .env 就先 cp .env.example .env，模板里也有这行可取消注释）
echo "COMPOSE_PROFILES=default,proxy" >> .env

# 3. 拉起（已在跑的部署重新 up 一次即可）
docker compose up -d
```

然后管理面板「系统设置 → 采集出口」填 `http://proxy:7890` → 点「测试连通」→
出口 IP 是机场节点即成功 → 保存。侧栏状态条变绿即生效。

- 停用：`.env` 删掉那行 COMPOSE_PROFILES 再 `docker compose up -d`（proxy 容器随之移除）。
- 订阅拉取失败：给 mihomo 配代理或手动下载 Clash 格式订阅放到
  `mihomo/providers/airport.yaml`（`path` 指向的本地文件存在时跳过在线拉取）。
- 安全边界：proxy 服务不发布任何端口，仅在 compose 容器网络内可达，公网摸不到；
  订阅链接自带 token，`mihomo/config.yaml` 已被 `.gitignore` 排除，不要提交进仓库。
- 宿主机端口要按自家规范重映射（如 web 改绑 172.17.0.1:20002）时，用
  `docker-compose.override.yml`（Compose v2.24+ 支持 `ports: !override` 整体覆盖）。

## 卸载

```bash
docker compose down --rmi local   # 停容器并删本地构建的镜像
# 数据在 ./var，确认不要了再手动删除该目录
```
