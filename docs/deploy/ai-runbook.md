# AI 部署必读（Server Runbook）

> **给 AI 助手**：当用户把一台可 SSH 登录的服务器交给你，让你部署或维护 llm-price-monitor 时，先把本手册完整读完再动手。目标有两个：把项目部署好；不碰坏面板上已有的任何东西。

## 0. 项目事实卡（直接采信，不要重新推断）

| 事实 | 值 |
| --- | --- |
| 架构 | 双容器：`api`（FastAPI + 内置定时采集，容器内 8000）+ `web`（Next.js，容器内 3000），`docker compose` 编排 |
| 对外端口 | 仅宿主 3000（前端）。8000 永不发布、永不放行公网 |
| 数据 | `./var/monitor.db`（SQLite，bind mount）。备份它 = 备份一切 |
| 密钥 | 管理面板（/setup、系统设置）填写，存 `./var/monitor.db`；不使用 `.env` 文件 |
| 健康检查 | `GET /api/meta`（api 容器内返回 200）；compose 已自带 healthcheck |
| 首次启动 | 库中无账号时访问管理页跳 `/setup` 创建管理员，**没有默认账号密码** |
| 忘记密码 | `docker compose exec api price-admin` 重置 |
| 反代烘焙 | 前端 `/api` 反代目标在 `next build` 时固定为 `http://api:8000`；改服务名需同步改 Dockerfile 的 ARG 与 compose 的 environment |
| 构建资源 | 内存 ≥ 2 GB，不足先加 swap |

## 1. 固定约定

- 部署目录统一 `/opt/llm-price-monitor`（用户已指定其他目录时从其指定；已存在的部署不要挪动）。
- 编排命令统一 `docker compose`（v2 语法）。
- 全程不改应用代码；允许改动仅限三类：compose 端口映射、反向代理、防火墙规则。

## 2. 动手前先探测（按序执行并记录结果）

```bash
cat /etc/os-release | head -2; free -h; df -h /
docker -v && docker compose version          # 面板装的 Docker 同样算数
ss -tlnp | grep -E ':(80|443|3000|8000)\s'   # 目标端口是否已被占
docker ps -a                                  # 已有哪些容器，避免重名
which 1pctl 2>/dev/null && echo HAS_1PANEL    # 1Panel
test -d /www/server/panel && echo HAS_BT      # 宝塔
```

结论分支：

- **有 Docker**（含面板自带）→ 直接用，禁止再装第二个 Docker。
- **没有 Docker** → 先向用户确认，同意后官方脚本安装：`curl -fsSL https://get.docker.com | bash`（国内机器慢时换阿里云镜像源）。
- **内存 < 2 GB** → 先加 swap（见 [docker-compose.md](docker-compose.md) FAQ）再构建。
- **3000 被占** → compose 端口映射改 `"其他端口:3000"`，反代目标同步改。
- **部署目录已存在且是本项目** → 走「更新」流程（第 10 节），不要重新 clone。

## 3. 红线（任何一步都不得越过）

1. 不改、不停、不删面板已有的网站、容器、Nginx 配置、证书、计划任务。
2. 不占用 80 / 443 / 22；这三个端口只让面板或既有 Nginx 接管。
3. 8000 永不发布到宿主机、永不写进任何防火墙放行列表。
4. 3000 不对公网放行，反代可达即可。
5. 不删除或覆盖 `var/`；密钥只在数据库里，不贴进对话记录、不上传任何外部服务。
6. 不 kill 1Panel / 宝塔自身进程；不执行 `docker system prune -a` 这类会连面板容器一起清掉的命令。
7. 拿不准就停下来问用户，不要猜。

## 4. 标准部署流程

按 [docker-compose.md](docker-compose.md)「首次部署」一节逐步执行：clone → `docker compose up -d --build` → `docker compose logs -f`。命令以该文档为准，此处不重复。

## 5. 反向代理与 HTTPS（域名 + 证书是默认要求）

对外访问一律走 `https://域名`，证书用 Let's Encrypt，并确认**自动续签**已启用。缺信息就问用户：不知道用哪个域名时先问「用哪个域名？解析到这台服务器了吗」，不要自作主张用 IP 部署收工。

- **宝塔**（Nginx 在宿主机）：网站 → 添加站点（只填域名，不建数据库）→ 设置 → 反向代理 → `http://127.0.0.1:3000`；「SSL」页选 Let's Encrypt 申请证书，确认自动续签开启，再开强制 HTTPS。
- **1Panel**（OpenResty 是容器）：网站 → 创建网站 → 反向代理 → `http://172.17.0.1:3000` 或 `http://服务器内网IP:3000`；**不能写 127.0.0.1**（容器内指向它自己）。「网站 → 证书」用 Let's Encrypt 申请，确认该证书的自动续签开启后绑定到站点。
- **无面板**：写 `/etc/nginx/conf.d/price.conf`（配置见 docker-compose.md），用 certbot 签发并自动续签：`certbot --nginx -d 域名`，然后 `systemctl list-timers | grep certbot`（或 `certbot renew --dry-run`）确认续签定时器存在。
- **域名或解析没就绪**：先问用户、等用户；确实暂时给不了域名，才回退 `http://服务器IP:3000` 验收（临时放行安全组 3000），并在汇报里标注「未完成事项：HTTPS 待域名」。

## 6. 防火墙三层模型（逐层确认）

| 层 | 谁控制 | 期望 |
| --- | --- | --- |
| 云厂商安全组 | 用户在控制台操作（AI 改不了，缺什么明确告诉用户去开） | 放行 22/80/443；不放 3000/8000 |
| 面板防火墙页 | 1Panel「主机安全」/ 宝塔「系统防火墙」 | 同上 |
| 系统 firewalld / ufw | AI 可查：`ufw status` / `firewall-cmd --list-all` | 同上 |

## 7. 验收清单（全部通过才算完成）

```bash
cd /opt/llm-price-monitor
docker compose ps                                                   # 两容器 Up (healthy)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/     # 200
docker compose exec api python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/meta').status)"  # 200
curl -sI https://域名 | head -1                                      # 200/2xx，证书有效
```

并确认证书自动续签已开启（面板证书页的续签开关，或 certbot 续签定时器存在）。

然后走域名打开站点：`/setup` 创建管理员 → 登录管理面板 → 添加一个站点 → 触发一次采集，能看到数据即验收完成。AI 没有浏览器时，把这几步交给用户点一遍并回报结果。

## 8. 收尾汇报模板

部署完成后向用户输出，缺项如实标注：

1. 访问地址（`https://域名`）、证书来源与自动续签状态
2. 管理员账号状态（用户在 `/setup` 自行创建，或已用 `price-admin` 重置）
3. 部署目录与数据目录位置
4. 备份命令、更新命令各一条（从 docker-compose.md 摘）
5. 未完成 / 需要用户处理的事项（如安全组放行、域名解析、AI Key 填写）

## 9. 故障速查

| 症状 | 先执行 | 处置 |
| --- | --- | --- |
| api 容器重启循环 / unhealthy | `docker compose logs --tail=100 api` | 看日志定位；最近一次部署若是旧版本编排仍挂载 `.env`，先 `git pull` 再重新 up |
| 首页能开、`/api` 全 404 | `docker compose exec web env \| grep PRICE` | 反代烘焙值不对 → 确认 Dockerfile ARG 与 compose environment 都是 `http://api:8000`，`docker compose build web` 后重新 up |
| 反代 502 | `curl -I http://127.0.0.1:3000` | 容器挂了先看 logs；容器活着则是反代目标写错（1Panel 场景多半是写了 127.0.0.1） |
| 构建卡死 / 被 OOM 杀 | `free -h` | 加 swap 后重新 build |
| 磁盘满 | `docker system df` | `docker image prune -f`；`journalctl --vacuum-size=100M` |
| 时间 / 时区不对 | `docker compose exec api date` | 镜像默认 `TZ=Asia/Shanghai`；不符时改 Dockerfile / compose 的 TZ 后重建 |

## 10. 更新与回滚

```bash
# 更新（先备份再动）
cd /opt/llm-price-monitor
sqlite3 var/monitor.db ".backup 'var/backup-$(date +%F).db'"
git pull
docker compose up -d --build

# 回滚到上一版：数据在 var/，回滚代码不动数据
git log --oneline -5
git checkout <上一提交>
docker compose up -d --build
```
