# AI 部署必读（Server Runbook）

> **给 AI 助手**：当用户把一台可 SSH 登录的服务器交给你，让你部署或维护 llm-price-monitor 时，先把本手册完整读完再动手。目标有两个：把项目部署好；不碰坏面板上已有的任何东西。

## 0. 项目事实卡（直接采信，不要重新推断）

| 事实 | 值 |
| --- | --- |
| 架构 | `api`（FastAPI + 内置定时采集，容器内 8437）+ `web`（Next.js，容器内 3000），`docker compose` 编排；另有可选 `proxy`（mihomo 机场兜底，`profiles: ["proxy"]` 默认不启动，`.env` 加 `COMPOSE_PROFILES=default,proxy` 启用） |
| 镜像版本 | compose 引用 `llm-price-monitor/{api,web}:${APP_TAG:-local}`——`.env` 里 `APP_TAG=<git 短 SHA>` 就是线上版本号，`up -d --build` 构建即带标签，**回滚 = 改回上一版 APP_TAG + up -d**；不设置回退 local |
| 对外端口 | 仅宿主 3000（前端）。8437 永不发布、永不放行公网 |
| 数据 | `./var/monitor.db`（SQLite，bind mount）。备份它 = 备份一切 |
| 密钥 | 管理面板（/setup、系统设置）填写，存 `./var/monitor.db`；`.env` 只放可选的读接口封锁令牌 |
| 健康检查 | `GET /api/health`（api 容器内返回 200）；compose 已自带 healthcheck。读接口封锁开启后 `/api/meta` 对匿名请求返回 401，不能再用来探活 |
| 首次启动 | 库中无账号时访问管理页跳 `/setup` 创建管理员，**没有默认账号密码** |
| 忘记密码 | `docker compose exec api price-admin` 重置 |
| 反代烘焙 | 前端 `/api` 反代目标在 `next build` 时固定为 `http://api:8437`；改服务名需同步改 Dockerfile 的 ARG 与 compose 的 environment |
| 构建资源 | 内存 ≥ 2 GB，不足先加 swap |

## 1. 固定约定

- 部署目录统一 `/opt/llm-price-monitor`（用户已指定其他目录时从其指定；已存在的部署不要挪动）。
- 编排命令统一 `docker compose`（v2 语法）。
- 全程不改应用代码；允许改动仅限三类：compose 端口映射、反向代理、防火墙规则。

## 2. 动手前先探测（按序执行并记录结果）

```bash
cat /etc/os-release | head -2; free -h; df -h /
docker -v && docker compose version          # 面板装的 Docker 同样算数
ss -tlnp | grep -E ':(80|443|3000|8437)\s'   # 目标端口是否已被占
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
3. 8437 永不发布到宿主机、永不写进任何防火墙放行列表。
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
- **1Panel 面板不开/要自动化时**，可用其 OpenAPI 建站（v2，要点见第 11 节）；不可行时手写 `/opt/1panel/www/conf.d/<域名>.conf`（模板抄既有站点，证书放 `/opt/1panel/www/sites/<域名>/ssl/`）并 `docker exec <openresty容器> nginx -s reload`——注意手写 conf 不进面板界面，面板建站会与之域名冲突，二选一。
- **域名或解析没就绪**：先问用户、等用户；确实暂时给不了域名，才回退 `http://服务器IP:3000` 验收（临时放行安全组 3000），并在汇报里标注「未完成事项：HTTPS 待域名」。

## 6. 防火墙三层模型（逐层确认）

| 层 | 谁控制 | 期望 |
| --- | --- | --- |
| 云厂商安全组 | 用户在控制台操作（AI 改不了，缺什么明确告诉用户去开） | 放行 22/80/443；不放 3000/8437 |
| 面板防火墙页 | 1Panel「主机安全」/ 宝塔「系统防火墙」 | 同上 |
| 系统 firewalld / ufw | AI 可查：`ufw status` / `firewall-cmd --list-all` | 同上 |

## 7. 验收清单（全部通过才算完成）

```bash
cd /opt/llm-price-monitor
docker compose ps                                                   # 两容器 Up (healthy)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/     # 200
docker compose exec api python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8437/api/health').status)"  # 200
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
| 首页能开、`/api` 全 404 | `docker compose exec web env \| grep PRICE` | 反代烘焙值不对 → 确认 Dockerfile ARG 与 compose environment 都是 `http://api:8437`，`docker compose build web` 后重新 up |
| 反代 502 | `curl -I http://127.0.0.1:3000` | 容器挂了先看 logs；容器活着则是反代目标写错（1Panel 场景多半是写了 127.0.0.1） |
| 构建卡死 / 被 OOM 杀 | `free -h` | 加 swap 后重新 build |
| 磁盘满 | `docker system df` | `docker image prune -f`；`journalctl --vacuum-size=100M` |
| 时间 / 时区不对 | `docker compose exec api date` | 镜像默认 `TZ=Asia/Shanghai`；不符时改 Dockerfile / compose 的 TZ 后重建 |
| 容器健康但域名打不开：80 被 302 到 `dnspod.qcloud.com/.../webblock.html`、443 TLS 握手后 RST，同机其他域名正常 | 用同 IP 上已备案域名对照访问；服务器本机/跨云内网路径可能仍 200 | 域名未备案被云厂商境内入口拦截，部署侧无解；办备案（1~3 周），或经用户确认后临时换已备案子域名过渡（2026-10-03 llmprices.cn 实锤） |

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


## 11. 大陆服务器构建排障（境内机器必读）

境内机器构建常见三层坑，逐层排查：

1. **基础镜像拉取慢/卡死**：确认 `/etc/docker/daemon.json` 的 registry-mirrors；腾讯/阿里云机器用自家内网 mirror（如 `mirror.ccs.tencentyun.com`）秒级。个别镜像 mirror 没有时用镜像源前缀直拉再 retag（如 `ghcr.nju.edu.cn/<org>/<img>` → `docker tag` 回原名）。
2. **容器内下载龟速（几 KB/s）但宿主机 curl 正常**：几乎必然是 **MTU 不匹配**——`ip link show eth0` 若 MTU < 1500 而默认 bridge 是 1500，容器大包全被丢。修法（二选一）：构建加 `--network host`；运行期在 compose 里给本项目网络显式降 MTU：
   ```yaml
   networks:
     default:
       driver_opts:
         com.docker.network.mtu: "1000"   # 与宿主机 eth0 一致
   ```
3. **pypi / apt 源慢**：pypi 要改**两处**——`uv.lock` 里 `registry = ` 与所有 `https://files.pythonhosted.org` 包文件 URL（整体替换为 `https://mirrors.aliyun.com/pypi/...`，hash 不变）；apt 在 `RUN apt-get update` 前插一行改源（注意镜像源路径结构，别拼出重复 `/debian/debian`）；playwright 浏览器下载加 `ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright`。

排障注意：
- 服务器本地补丁（uv.lock/Dockerfile/override）**不要提交**，`git pull` 后按上面清单重放；runbook 里应留重放脚本。
- 停后台构建进程时 `pkill -f` 的模式**不要出现在同一条命令的正文里**（会自匹配把 SSH 会话一起杀掉，表现为 exit 255），用字符类技巧如 `"docker [b]uild"` 并拆开执行。
- 对同一台机器的高频 SSH 可能触发连接频率限制（connection reset），间隔几十秒重试即可；长任务合并成单次 SSH 里的脚本执行。

## 12. 1Panel v2 OpenAPI 要点（自动化建站/反代/HTTPS）

- 凭证：面板设置里开启 API 后，key 存 `/opt/1panel/db/core.db` 的 `settings.ApiKey`；鉴权头 `1Panel-Token = md5("1panel" + ApiKey + 时间戳)` + `1Panel-Timestamp`（秒级）。接口走 `http://127.0.0.1:<面板端口>/api/v2/...`。
- 建反代站：`POST /api/v2/websites`，**字段契约易错点**：域名放 `domains: [{domain, port: 80, ssl: false}]` 数组（不是 primaryDomain）；分组键名是 `webSiteGroupID`（大小写怪癖，查 `groups` 表 type=website 的默认组）；`appType` 必须是 `installed`；`type: "proxy"`、`proxy: "http://172.17.0.1:<端口>"`。
- 绑证书：先 `POST /api/v2/websites/ssl/upload`（`type: "paste"`， privateKey/certificate 全文）→ 从 `POST /api/v2/websites/ssl/search` 拿 SSL id → `POST /api/v2/websites/<id>/https`（`type: "existed"`、`websiteSSLId`、`httpConfig: "HTTPToHTTPS"`）。
- 加反代规则：`POST /api/v2/websites/proxies/update`（`operate: "create"`，不是 add；`proxyHost` 必须给具体值如域名）。创建网站接口默认**不带**反代规则，不加这步会 403。
- 排错：HTTP 200 里带 `code: 400/500` 的业务错误；表单校验错误会把缺失字段全列出来，照着补；500 空信息多半是字段名拼错导致服务层空指针，回源码 dto 对照（agent/app/dto/request/website.go）。
