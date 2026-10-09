"""中转站候选发现与探测：从公开聚合源拉站点清单，探测 /api/pricing 可用性，产出可导入站点配置。

三个命令组成一条管线（只请求公开 JSON 端点，与采集主链路无关）：

    uv run price-discover harvest                  # zuiquanapi（默认单源）→ var/discovery/candidates.json
    uv run price-discover harvest --only all       # 全部 6 个源一起跑（隔一两周补一次独家站）
    uv run price-discover probe [--take N]         # 候选 → var/discovery/{probed,importable}.json
    uv run price-admin import-sites var/discovery/importable.json [--apply --take 30]

聚合源（新增源在 SOURCES 里加一个返回 list[Candidate] 的函数即可）：
    - zuiquanapi.com              默认单源：awesome 导航的在线实时版，单页 4400+ 域名，覆盖可用站 96%
    - daheiai/awesome-api-proxy   GitHub README 表格，596+ 站点，带在线状态/可用率/口碑分（快照易死站）
    - apisou.com                  竞品导航站，sitemap 全量 129 个站点详情页（h1=站名、首个外链=站点地址）
    - panxunying/ai-coding-welfare  福利站导航仓库的 data/sites.json，机器可读
    - aiapipk.com                 中转站竞技场首页静态链接（约 39 站）
    - GitHub 导航仓库             三个网友维护的中转站导航项目 README（bubblevv/ai-api-gongyi-nav 等）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

OUT_DIR = Path("var/discovery")
# 与 admin_cli 一致：PRICE_MONITOR_DB 可把库指到别处，比对库内已有站点用
DB_PATH = Path(os.getenv("PRICE_MONITOR_DB") or "var/monitor.db")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
# 详情页/表格里会混入的公共链接，提取站点地址时排除
NOISE_HOSTS = {
    "hm.baidu.com",
    "schema.org",
    "beian.miit.gov.cn",
    "github.com",
    "raw.githubusercontent.com",
    "linux.do",
    "t.me",
    "qq.com",
    "docs.qq.com",
}
# id 归一化时去掉的纯基础设施前缀（保留业务域名本身）
INFRA_LABELS = {"www", "api", "new-api", "newapi", "console", "app", "open", "chat", "go", "sub", "fast", "vip", "pro", "auth"}
# 项目对外请求统一不走环境代理，与 egress/http_retry 一致；probe --proxy 显式指定时才走代理


@dataclass
class Candidate:
    host: str
    url: str
    name: str
    sources: list[str] = field(default_factory=list)
    note: str = ""
    meta: dict = field(default_factory=dict)


def is_noise_host(host: str) -> bool:
    """导航/徽章类公共域名（含其子域）不算候选站点。"""
    return any(host == noise or host.endswith(f".{noise}") for noise in NOISE_HOSTS)


def normalize_host(host: str) -> str:
    """域名比对口径：剥掉 www/api/console 等基础设施前缀后整体比较（cun.ai == www.cun.ai）。"""
    labels = [label for label in host.lower().split(".") if label]
    while labels and labels[0] in INFRA_LABELS:
        labels = labels[1:]
    return ".".join(labels)


def origin_of(url: str) -> str | None:
    """候选链接归一到 scheme://host（去掉 /register?aff=xxx 之类的推广路径）。

    先剥掉转义 payload 里的反斜杠（zuiquanapi 页面是 JSON 转义存储，斜杠写成反斜杠加斜杠），
    否则 urlsplit 会把尾部反斜杠当进 netloc，生成 https://x.y\\/api/pricing 这类废链。
    """
    parts = urlsplit(url.replace("\\", "").strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if is_noise_host(host) or "." not in host:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def host_to_id(host: str, taken: set[str]) -> str:
    """域名 → 站点 id：去掉基础设施前缀与末级 TLD，点连字改连字符，撞名追加序号。"""
    labels = [label for label in urlsplit(f"https://{host}").hostname.split(".") if label]
    if len(labels) >= 3 and labels[0] in INFRA_LABELS:
        labels = labels[1:]
    core = labels[:-1] if len(labels) > 1 else labels
    base = "-".join(core) or host.replace(".", "-")
    site_id = base
    n = 2
    while site_id in taken:
        site_id = f"{base}-{n}"
        n += 1
    taken.add(site_id)
    return site_id


# ---------- 网络空间测绘（sweep）：不依赖收录名单，按面板指纹直接搜全网部署实例 ----------

# 指纹取自各面板仓库的静态 index.html（一手来源），站长改站名也不影响静态页特征：
# new-api（QuantumNous/new-api）、one-api（songquanpeng/one-api）、Veloera（Veloera/Veloera）
PANEL_FINGERPRINTS = {
    "new-api": 'body="Unified AI API gateway and admin dashboard."',
    "one-api": 'body="OpenAI 接口聚合管理，支持多种渠道包括 Azure"',
    "veloera": 'title="Veloera"',
}

ENGINE_KEYS = {"fofa": ("FOFA_EMAIL", "FOFA_KEY"), "quake": ("QUAKE_TOKEN",), "hunter": ("HUNTER_KEY",)}


def _scheme_of(host: str, port: object) -> str:
    return "https" if str(port) == "443" else "http"


def parse_fofa(payload: dict) -> list[Candidate]:
    """FOFA /api/v1/search/all 响应：fields=host,port,title 时 results 为列表的列表。"""
    out: list[Candidate] = []
    for row in payload.get("results") or []:
        if isinstance(row, dict):  # 部分账号/字段组合返回对象
            host, port, title = row.get("host"), row.get("port"), row.get("title")
        else:
            host, port, title = (list(row) + ["", "", ""])[:3]
        if not host:
            continue
        if "://" in str(host):  # host 字段偶发带协议
            origin = origin_of(str(host))
        else:
            origin = origin_of(f"{_scheme_of(host, port)}://{host}")
        if origin is None:
            continue
        out.append(Candidate(urlsplit(origin).hostname or "", origin, str(title or ""), ["fofa"]))
    return out


def parse_quake(payload: dict) -> list[Candidate]:
    """Quake 360 /api/v3/search/quake_service 响应：data[].service.http.host / hostname。

    只收有域名/主机名的条目，纯 IP 命中多为噪音（裸 IP 上跑面板且无域名的极少）。
    """
    out: list[Candidate] = []
    for item in payload.get("data") or []:
        service = item.get("service") or {}
        http = service.get("http") or {}
        host = http.get("host") or (item.get("hostname") or [""])[0] or ""
        if not host:
            continue
        origin = origin_of(str(host) if "://" in str(host) else f"{_scheme_of(host, item.get('port'))}://{host}")
        if origin is None:
            continue
        out.append(Candidate(urlsplit(origin).hostname or "", origin, str(http.get("title") or item.get("http_title") or ""), ["quake"]))
    return out


def parse_hunter(payload: dict) -> list[Candidate]:
    """鹰图 /openApi/search 响应：data.arr[].url 自带协议，最省事。"""
    out: list[Candidate] = []
    for item in (payload.get("data") or {}).get("arr") or []:
        origin = origin_of(str(item.get("url") or ""))
        if origin is None:
            continue
        out.append(Candidate(urlsplit(origin).hostname or "", origin, str(item.get("http_title") or item.get("domain") or ""), ["hunter"]))
    return out


def sweep(engine: str, panel: str, query: str | None, size: int) -> list[Candidate]:
    """调测绘引擎 API 搜面板实例；引擎与账号 key 由环境变量提供。"""
    import base64
    import os

    import httpx

    keys = ENGINE_KEYS[engine]
    missing = [name for name in keys if not os.getenv(name)]
    if missing:
        raise SystemExit(
            f"缺少环境变量 {'、'.join(missing)}——{engine} 需要注册账号拿 API key"
            f"（fofa.info / quake.360.net / hunter.qianxin.com 都有免费额度）。"
        )
    if query is None:
        query = PANEL_FINGERPRINTS[panel]
    source = f"{engine}:{panel}"
    if engine == "fofa":
        qbase64 = base64.b64encode(query.encode()).decode()
        response = httpx.get(
            "https://fofa.info/api/v1/search/all",
            params={"email": os.environ["FOFA_EMAIL"], "key": os.environ["FOFA_KEY"], "qbase64": qbase64, "fields": "host,port,title", "size": size},
            timeout=30,
        )
        candidates = parse_fofa(response.json())
    elif engine == "quake":
        response = httpx.post(
            "https://quake.360.net/api/v3/search/quake_service",
            headers={"X-QuakeToken": os.environ["QUAKE_TOKEN"]},
            json={"query": query, "start": 0, "size": size},
            timeout=30,
        )
        candidates = parse_quake(response.json())
    else:
        qbase64 = base64.urlsafe_b64encode(query.encode()).decode()
        response = httpx.get(
            "https://hunter.qianxin.com/openApi/search",
            params={"api-key": os.environ["HUNTER_KEY"], "search": qbase64, "page": 1, "page_size": min(size, 100), "is_web": 3},
            timeout=30,
        )
        candidates = parse_hunter(response.json())
    # 引擎给的是 host:port，探测只认 origin；去重后合并来源标注
    for cand in candidates:
        cand.sources = [source]
    return merge_candidates([candidates])


# ---------- 聚合源 ----------

# raw.githubusercontent.com 国内直连经常超时，全部 jsDelivr 镜像优先、raw 兜底
AWESOME_README_URLS = (
    "https://cdn.jsdelivr.net/gh/daheiai/awesome-api-proxy@main/README.md",
    "https://raw.githubusercontent.com/daheiai/awesome-api-proxy/main/README.md",
)
WELFARE_SITES_URLS = (
    "https://cdn.jsdelivr.net/gh/panxunying/ai-coding-welfare@main/data/sites.json",
    "https://raw.githubusercontent.com/panxunying/ai-coding-welfare/main/data/sites.json",
)
APISOU_BASE = "https://www.apisou.com"
AIAPIPK_URL = "https://www.aiapipk.com"
ZUIQUAN_URL = "https://zuiquanapi.com"
# 站点监控快照（7 天可用率/平均响应/最新响应，按站点数字 id 索引），与首页条目联表
ZUIQUAN_BOOTSTRAP_URL = "https://www.zuiquanapi.com/api/bootstrap"

# 导航仓库：README 里的站点 markdown 链接，通用解析（2026-10 实测外链数：46/26/33）
GITHUB_NAV_READMES: dict[str, tuple[str, ...]] = {
    "bubblevv-ai-api-gongyi-nav": (
        "https://cdn.jsdelivr.net/gh/bubblevv/ai-api-gongyi-nav@main/README.md",
        "https://raw.githubusercontent.com/bubblevv/ai-api-gongyi-nav/main/README.md",
    ),
    "ai-welfare-hub": (
        "https://cdn.jsdelivr.net/gh/wynx1123/ai-welfare-hub@main/README.md",
        "https://raw.githubusercontent.com/wynx1123/ai-welfare-hub/main/README.md",
    ),
    "ai-api-zhongzhuan": (
        "https://cdn.jsdelivr.net/gh/1sh1ro/ai-api-zhongzhuan@main/README.md",
        "https://raw.githubusercontent.com/1sh1ro/ai-api-zhongzhuan/main/README.md",
    ),
}

# 表格行尾的「状态 | 可用率 | 评价净值」三列
_AWESOME_TAIL = re.compile(r"\|\s*(在线|离线|未知)\s*\|\s*([\d.]+%)\s*\|\s*([+\-\d]+)\s*\|\s*$")


def harvest_awesome_api_proxy(text: str) -> list[Candidate]:
    """解析 awesome-api-proxy README 的站点表格（名称/URL/描述/状态/可用率/口碑分）。"""
    out: list[Candidate] = []
    for line in text.splitlines():
        link = re.match(r"^\|\s*\d+\s*\|\s*\[([^\]]+)\]\((https?://[^)\s]+)\)\s*\|\s*[^|]*\|\s*([^|]*)\|", line)
        if not link:
            continue
        origin = origin_of(link.group(2))
        if origin is None:
            continue
        tail = _AWESOME_TAIL.search(line)
        meta: dict = {"description": clean_desc(link.group(3))}
        if tail:
            meta.update({"status": tail.group(1), "uptime7d": tail.group(2), "rating": int(tail.group(3))})
        out.append(Candidate(urlsplit(origin).hostname or "", origin, link.group(1).strip(), ["awesome-api-proxy"], meta=meta))
    return out


def harvest_welfare(text: str) -> list[Candidate]:
    """解析 ai-coding-welfare 的 data/sites.json。"""
    out: list[Candidate] = []
    for item in json.loads(text).get("sites", []):
        url = item.get("homeUrl") or item.get("signupUrl") or ""
        origin = origin_of(url) if isinstance(url, str) else None
        if origin is None:
            continue
        out.append(Candidate(urlsplit(origin).hostname or "", origin, str(item.get("name") or ""), ["ai-coding-welfare"]))
    return out


def harvest_aiapipk(text: str) -> list[Candidate]:
    """解析 aiapipk.com 首页里的站点外链（页面没有站名结构化字段，先用域名当名字）。"""
    return harvest_html_links(text, source="aiapipk", own_host="aiapipk.com")


def harvest_html_links(text: str, *, source: str, own_host: str) -> list[Candidate]:
    """从导航站 HTML/JS payload 里扒站点外链，域名当名字。"""
    out: list[Candidate] = []
    for url in re.findall(r'https?://[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}[^"\'<> ]*', text):
        origin = origin_of(url)
        if origin is None or own_host in origin:
            continue
        host = urlsplit(origin).hostname or ""
        out.append(Candidate(host, origin, host, [source]))
    return out


def _unesc(s: str) -> str:
    """解开 RSC payload 的双层 JSON 转义（outer \\\" → 内层 \" → 字符），解不动就原样返回。"""
    for _ in range(2):
        try:
            s = json.loads(f'"{s}"')
        except Exception:
            break
    return s


def clean_desc(text: str, limit: int = 200) -> str:
    """描述清洗：去 HTML 标签/markdown 痕迹、压空白、截断。"""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("**", "").replace("🎁", "").replace("✨", "").replace("🚀", "").replace("💰", "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def harvest_zuiquan(text: str) -> list[Candidate]:
    """解析 zuiquanapi.com（awesome-api-proxy 的在线版）。

    页面 RSC payload 里有结构化站点条目（name/url/description/tag），优先按结构解；
    结构没命中的再用裸链接兜底，保证覆盖不缩水。
    """
    out: list[Candidate] = []
    seen_urls: set[str] = set()
    pattern = re.compile(
        r'\{\\"id\\":(?P<sid>\d+),\\"subcategory_id\\":\d+,\\"name\\":\\"(?P<name>.*?)\\",\\"url\\":\\"(?P<url>.*?)\\"'
        r'(?P<body>.*?)\\"description\\":\\"(?P<desc>.*?)\\",\\"monitor_tier',
        re.S,
    )
    for m in pattern.finditer(text):
        origin = origin_of(_unesc(m["url"]))
        if origin is None:
            continue
        seen_urls.add(origin)
        out.append(
            Candidate(
                urlsplit(origin).hostname or "",
                origin,
                _unesc(m["name"]).strip(),
                ["zuiquanapi"],
                meta={"description": clean_desc(_unesc(m["desc"])), "source_id": int(m["sid"])},
            )
        )
    if len(out) < 100:  # 结构解析失灵（站点改版）时退回裸链接模式，宁可少描述不可少站点
        out.extend(cand for cand in harvest_html_links(text, source="zuiquanapi", own_host="zuiquanapi.com") if cand.url not in seen_urls)
    return out


def harvest_markdown_links(text: str, *, source: str) -> list[Candidate]:
    """通用导航仓库 README 解析：抓站点 markdown 链接，github/linux.do 等噪声由 origin_of 过滤。"""
    out: list[Candidate] = []
    for name, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text):
        origin = origin_of(url)
        if origin is None:
            continue
        out.append(Candidate(urlsplit(origin).hostname or "", origin, name.strip(), [source]))
    return out


def harvest_apisou(html: str) -> Candidate | None:
    """解析 apisou.com 单个站点详情页：h1 是站名，第一个非导航外链是站点地址。"""
    name_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if not name_match:
        return None
    for url in re.findall(r'href="(https?://[^"]+)"', html):
        origin = origin_of(url)
        if origin is None:
            continue
        desc = re.search(r'name="description" content="([^"]*)"', html)
        return Candidate(
            urlsplit(origin).hostname or "",
            origin,
            re.sub(r"<[^>]+>", "", name_match.group(1)).strip(),
            ["apisou"],
            meta={"description": desc.group(1) if desc else ""},
        )
    return None


def merge_candidates(groups: list[list[Candidate]]) -> list[Candidate]:
    """按 host 合并去重：多源收录的合并 sources，名字取先出现的非空值，描述取最长的一份。"""
    by_host: dict[str, Candidate] = {}
    for group in groups:
        for cand in group:
            existing = by_host.get(cand.host)
            if existing is None:
                by_host[cand.host] = cand
                continue
            for source in cand.sources:
                if source not in existing.sources:
                    existing.sources.append(source)
            if not existing.name:
                existing.name = cand.name
            for key in ("description", "status", "uptime7d", "rating", "source_id", "monitor_online", "uptime_7d", "avg_ms", "last_ms", "checked_at"):
                existing_val, cand_val = existing.meta.get(key), cand.meta.get(key)
                if key == "description":
                    if not existing_val or (cand_val and len(str(cand_val)) > len(str(existing_val))):
                        existing.meta["description"] = cand_val
                elif cand_val is not None and existing_val is None:
                    # 监控指标也是缺了才补：fresh 组在前（本轮新值占位），源断供时保留旧值不闪空
                    existing.meta[key] = cand_val
    return sorted(by_host.values(), key=lambda c: c.host)


# harvest 源的开关名（--only 逗号分隔；缺省只用 zuiquanapi，all=全部）
SOURCE_KEYS = ("zuiquanapi", "awesome-api-proxy", "apisou", "aiapipk", "ai-coding-welfare", "github-nav")
DEFAULT_SOURCES = ("zuiquanapi",)


async def fetch_zuiquan_status(client: httpx.AsyncClient) -> dict[str, dict]:
    """拉 zuiquanapi 全量监控快照：站点数字 id → {online, ms, uptime, avgMs, checkedAt}。"""
    response = await client.get(ZUIQUAN_BOOTSTRAP_URL)
    response.raise_for_status()
    status = response.json().get("status")
    return status if isinstance(status, dict) else {}


def merge_zuiquan_status(candidates: list[Candidate], status: dict[str, dict]) -> int:
    """把监控快照并进带 source_id 的候选 meta（7 天可用率/平均响应/最新响应），返回命中条数。"""
    hit = 0
    for cand in candidates:
        sid = cand.meta.get("source_id")
        row = status.get(str(sid)) if isinstance(sid, int) else None
        if not isinstance(row, dict):
            continue
        cand.meta.update(
            {
                "monitor_online": bool(row.get("online")),
                "uptime_7d": row.get("uptime"),
                "avg_ms": row.get("avgMs"),
                "last_ms": row.get("ms"),
                "checked_at": row.get("checkedAt"),
            }
        )
        hit += 1
    return hit


async def harvest(proxy: str | None, only: set[str] | None = None) -> list[Candidate]:
    only = only or set(DEFAULT_SOURCES)
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        headers=headers, timeout=httpx.Timeout(20.0), follow_redirects=True, trust_env=False, proxy=proxy, limits=httpx.Limits(max_connections=8)
    ) as client:

        async def get_text(url: str, timeout: float = 20.0) -> str:
            response = await client.get(url, timeout=httpx.Timeout(timeout))
            response.raise_for_status()
            return response.text

        async def apisou_all() -> list[Candidate]:
            sitemap = await get_text(f"{APISOU_BASE}/sitemap.xml")
            slugs = sorted(set(re.findall(rf"{re.escape(APISOU_BASE)}/site/([a-z0-9\-]+)", sitemap)))
            sem = asyncio.Semaphore(8)

            async def one(slug: str) -> Candidate | None:
                async with sem:
                    try:
                        return harvest_apisou(await get_text(f"{APISOU_BASE}/site/{slug}"))
                    except Exception:
                        return None

            results = await asyncio.gather(*(one(slug) for slug in slugs))
            return [cand for cand in results if cand is not None]

        async def simple(fn, urls: tuple[str, ...], timeout: float = 20.0) -> list[Candidate]:
            """逐个镜像尝试，全挂才报错返回空（大文件如 596 站 README 给更长超时）。"""
            last_exc: Exception | None = None
            for url in urls:
                try:
                    return fn(await get_text(url, timeout=timeout))
                except Exception as exc:
                    last_exc = exc
            print(f"  源 {urls[0]} 拉取失败：{type(last_exc).__name__} {last_exc}")
            return []

        async def nav_repos() -> list[Candidate]:
            async def one(source: str, urls: tuple[str, ...]) -> list[Candidate]:
                return await simple(lambda text: harvest_markdown_links(text, source=source), urls)

            groups = await asyncio.gather(*(one(source, urls) for source, urls in GITHUB_NAV_READMES.items()))
            return [cand for group in groups for cand in group]

        async def noop() -> list[Candidate]:
            return []

        awesome, welfare, aiapipk, apisou, zuiquan, repos = await asyncio.gather(
            simple(harvest_awesome_api_proxy, AWESOME_README_URLS, timeout=60.0) if "awesome-api-proxy" in only else noop(),
            simple(harvest_welfare, WELFARE_SITES_URLS) if "ai-coding-welfare" in only else noop(),
            simple(harvest_aiapipk, (AIAPIPK_URL,)) if "aiapipk" in only else noop(),
            apisou_all() if "apisou" in only else noop(),
            simple(harvest_zuiquan, (ZUIQUAN_URL,), timeout=60.0) if "zuiquanapi" in only else noop(),
            nav_repos() if "github-nav" in only else noop(),
        )
        if zuiquan:
            # 站点监控指标（7 天可用率/平均响应/最新响应）是 zuiquanapi 自家监测数据，
            # 顺手一起拿：拉失败只少这批加分字段，不挡站点清单
            try:
                hit = merge_zuiquan_status(zuiquan, await fetch_zuiquan_status(client))
                print(f"  zuiquanapi 监控快照：{hit}/{len(zuiquan)} 个站点带可用率/响应数据")
            except Exception as exc:
                print(f"  zuiquanapi 监控快照拉取失败，本轮不带监控指标：{type(exc).__name__} {exc}")
        ran = [key for key in SOURCE_KEYS if key in only]
        print(
            f"  本次源 {('、'.join(ran))}：awesome-api-proxy {len(awesome)}、zuiquanapi {len(zuiquan)}、ai-coding-welfare {len(welfare)}、"
            f"aiapipk {len(aiapipk)}、apisou {len(apisou)}、GitHub 导航仓库 {len(repos)}"
        )
    return merge_candidates([awesome, zuiquan, welfare, aiapipk, apisou, repos])


# ---------- 探测 ----------


def classify_pricing(payload: object) -> tuple[int, bool]:
    """new-api 系 /api/pricing 响应 → (模型数, 是否公开可用)。"""
    if not isinstance(payload, dict):
        return 0, False
    data = payload.get("data")
    if isinstance(data, list):
        return len(data), True
    if isinstance(data, dict):
        return len(data), True
    return 0, False


async def probe_one(client: httpx.AsyncClient, cand: Candidate) -> dict:
    """探测一个候选站。判「可用」的口径是站在线（公开接口响应正常）即可，
    价格接口只是顺带探明：有没有、公开还是需登录、多少模型——供展示与导入参考，
    不作为筛站门槛（价格接口不可用可以走网页模式或 AI 提取采集）。"""
    result: dict = {
        "name": cand.name,
        "url": cand.url,
        "sources": cand.sources,
        "note": cand.meta.get("status", ""),
        "new_api": False,
        "online": False,
        "pricing_ok": False,
        "models": 0,
        "auth_required": False,
        "error": "",
    }
    try:
        status_resp = await client.get(f"{cand.url}/api/status")
        if status_resp.status_code == 200:
            result["online"] = True
            payload = status_resp.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and ("system_name" in data or "version" in data):
                result["new_api"] = True
                result["system_name"] = str(data.get("system_name") or "")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:120]
    if not result["online"]:
        return result
    try:
        pricing_resp = await client.get(f"{cand.url}/api/pricing")
        if pricing_resp.status_code in (401, 403):
            result["auth_required"] = True
        elif pricing_resp.status_code == 200:
            models, ok = classify_pricing(pricing_resp.json())
            result["models"], result["pricing_ok"] = models, ok
    except Exception as exc:
        result["error"] = result["error"] or f"{type(exc).__name__}: {exc}"[:120]
    return result


async def probe(candidates: list[Candidate], concurrency: int, timeout: float, proxy: str | None) -> list[dict]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        trust_env=False,
        proxy=proxy,
        limits=httpx.Limits(max_connections=concurrency),
    ) as client:
        sem = asyncio.Semaphore(concurrency)

        async def one(cand: Candidate) -> dict:
            async with sem:
                return await probe_one(client, cand)

        return await asyncio.gather(*(one(cand) for cand in candidates))


def build_importable(cands_by_host: dict[str, Candidate], probed: list[dict], exclude_hosts: set[str] | None = None) -> list[dict]:
    """探测通过的站点生成配置（默认停用，导入后在面板按批启用）。

    「通过」的口径是站在线即可——价格接口不可用的站照样能导（网页模式/AI 提取兜底），
    排序时价格接口公开可用的排前面。exclude_hosts 是库内已有站点的归一化域名：
    候选池可能收录了你手动加过的站，且池子按域名生成的 id 与手填 id 可能只差大小写，
    导入前必须排除防重复。
    """
    excluded = {normalize_host(host) for host in (exclude_hosts or set())}
    taken: set[str] = set()
    configs: list[dict] = []
    ranked = sorted(probed, key=lambda r: (not r["pricing_ok"], not r.get("online", False), -r["models"], r["url"]))
    for row in ranked:
        if not row.get("online"):
            continue
        cand = cands_by_host[urlsplit(row["url"]).hostname or ""]
        if normalize_host(cand.host) in excluded:
            continue
        site_id = host_to_id(cand.host, taken)
        origin = row["url"]
        configs.append(
            {
                "id": site_id,
                "network": {"url": f"{origin}/api/pricing"},
                "notice": {"url": f"{origin}/api/status"},
                "enabled": False,
            }
        )
    return configs


def existing_site_hosts() -> dict[str, str]:
    """库内已有站点：归一化域名 → 站点 id。库不在/没站点时返回空。"""
    try:
        from llm_price_monitor.store import Store  # 采集侧模块按需拉起，harvest/probe 不依赖库

        configs = Store(DB_PATH).list_site_configs()
    except Exception:
        return {}
    hosts: dict[str, str] = {}
    for config in configs:
        host = urlsplit(config.get("network", {}).get("url", "")).hostname or ""
        if host:
            hosts[normalize_host(host)] = str(config.get("id") or "")
    return hosts


def _merge_into_pool(fresh: list[Candidate]) -> tuple[int, int]:
    """把本轮结果与已有候选池增量合并并落盘，返回（合并后总数, 新增数）。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / "candidates.json"
    # 与已有候选池增量合并：某源拉挂只影响本轮新拉到的站，不会把上轮的站从池子里挤掉
    previous: list[Candidate] = []
    if out_file.exists():
        try:
            for item in json.loads(out_file.read_text(encoding="utf-8"))["candidates"]:
                cand = Candidate(**item)
                origin = origin_of(cand.url)  # 顺带清洗：旧池里可能有带转义反斜杠的 host/url
                if origin is not None:
                    previous.append(Candidate(urlsplit(origin).hostname or cand.host, origin, cand.name, cand.sources, cand.note, cand.meta))
        except Exception as exc:
            print(f"  已有 {out_file} 解析失败，按空池处理：{type(exc).__name__} {exc}")
    candidates = merge_candidates([fresh, previous])
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(candidates),
        "candidates": [cand.__dict__ for cand in candidates],
    }
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(candidates), max(len(candidates) - len(previous), 0)


async def run_harvest(proxy: str | None, only: str | None) -> None:
    if only is None:
        only_set = set(DEFAULT_SOURCES)
    elif only.strip().lower() == "all":
        only_set = set(SOURCE_KEYS)
    else:
        only_set = {key.strip() for key in only.split(",")}
    bad = only_set - set(SOURCE_KEYS)
    if bad:
        raise SystemExit(f"未知源：{'、'.join(sorted(bad))}（可选：{'、'.join(SOURCE_KEYS)}、all）")
    print("正在拉取聚合源…" + (f"（源：{'、'.join(sorted(only_set))}）" if only_set != set(SOURCE_KEYS) else "（全部）"))
    fresh = await harvest(proxy, only=only_set)
    total, added = _merge_into_pool(fresh)
    print(f"本轮拉到 {len(fresh)}、合并去重后 {total}（新增 {added}）→ {OUT_DIR / 'candidates.json'}")


def run_sweep(engine: str, panel: str, query: str | None, size: int) -> None:
    print(f"正在用 {engine} 搜 {panel} 实例（{query or PANEL_FINGERPRINTS[panel]}，取 {size} 条）…")
    candidates = sweep(engine, panel, query, size)
    total, added = _merge_into_pool(candidates)
    print(f"引擎返回 {len(candidates)} 条有效站点，合并去重后池子 {total}（新增 {added}）→ {OUT_DIR / 'candidates.json'}；跑 probe 检测价格接口。")


async def refresh_pool(proxy: str | None = None) -> dict:
    """管理台「刷新发现」任务体：拉默认源（zuiquanapi）合并进候选池，只拿站点与简介，不探测。

    刷新的定位是「拿站点清单与简介」：秒级完成，定时跑零负担。探测（在线/价格接口可用性/
    模型数）是可选的富化，只走 CLI probe（--retry-failed 给失联站翻案），结果落 probed.json
    档案，展示接口自动联表显示——默认路径里不藏对几千个候选逐一发请求的重活。
    """
    fresh = await harvest(proxy, only=set(DEFAULT_SOURCES))
    pool_total, pool_added = _merge_into_pool(fresh)
    return {"pool": pool_total, "pool_added": pool_added}


PROBE_CANDIDATE_PROXIES = (
    # 本机常见代理端口（clash/mihomo 7890、clash verge 7897、surge 6152/6153、通用 1087/8118）
    "http://127.0.0.1:7890",
    "http://127.0.0.1:7897",
    "http://127.0.0.1:6152",
    "http://127.0.0.1:1087",
    "http://127.0.0.1:8118",
    "http://127.0.0.1:8889",
)


async def detect_local_proxy(timeout: float = 3.0) -> str | None:
    """探测本机代理：端口有服务且能代理到 gstatic 204 才算可用，返回第一个通的地址。"""
    for proxy in PROBE_CANDIDATE_PROXIES:
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=httpx.Timeout(timeout), trust_env=False) as client:
                response = await client.get("https://www.gstatic.com/generate_204")
                if response.status_code == 204:
                    return proxy
        except Exception:
            continue
    return None


async def run_probe(take: int, concurrency: int, timeout: float, proxy: str | None, retry_failed: bool) -> None:
    raw = json.loads((OUT_DIR / "candidates.json").read_text(encoding="utf-8"))
    all_candidates = {cand.url: cand for cand in (Candidate(**item) for item in raw["candidates"])}
    if retry_failed:
        # 只重测上轮没通过的（不可达/需登录大概率是直连被墙或超时），通过的保留不重复打扰
        probed_file = OUT_DIR / "probed.json"
        if not probed_file.exists():
            raise SystemExit("没有 probed.json，先跑一轮不带 --retry-failed 的 probe")
        previous = json.loads(probed_file.read_text(encoding="utf-8"))["results"]
        failed_urls = {row["url"] for row in previous if not row.get("online")}
        candidates = [cand for url, cand in all_candidates.items() if url in failed_urls]
        stale_by_url = {row["url"]: row for row in previous if row.get("online")}
        print(f"重测模式：上轮在线 {len(stale_by_url)} 个保留，重测失联 {len(candidates)} 个…")
    else:
        candidates = list(all_candidates.values())
        stale_by_url = {}
        if take > 0:
            candidates = candidates[:take]
    if proxy == "auto":
        proxy = await detect_local_proxy()
        print(f"本机代理探测：{'使用 ' + proxy if proxy else '没找到可用代理，继续直连'}")
    print(f"开始探测 {len(candidates)} 个候选（并发 {concurrency}，超时 {timeout}s）…")
    started = time.monotonic()
    probed = await probe(candidates, concurrency, timeout, proxy)
    online = [r for r in probed if r["online"]]
    auth = [r for r in probed if r["auth_required"]]
    print(
        f"本轮完成（{time.monotonic() - started:.0f}s）：在线 {len(online)}"
        f"（其中价格接口公开可用 {len([r for r in online if r['pricing_ok']])}、需登录 {len(auth)}）"
        f"、失联/不可达 {len(probed) - len(online)}"
    )
    # 重测模式下与上轮已通过的合并落盘，importable 始终基于全量结果生成
    merged = list(stale_by_url.values()) + probed
    recovered = len([r for r in probed if r["online"]]) if retry_failed else 0
    if retry_failed:
        print(f"重测捞回 {recovered} 个（累计在线 {len(stale_by_url) + recovered}）")

    # --take 部分探测只预览：别拿 20 个站的结果覆盖全量 probed.json/importable.json
    if take > 0 and not retry_failed:
        print(f"部分探测只预览，不落盘；全量结果文件原样保留（{OUT_DIR / 'probed.json'}）")
        for row in sorted(online, key=lambda r: -r["models"])[:10]:
            print(f"  {urlsplit(row['url']).hostname:32} new-api={row['new_api']} 模型数={row['models']}")
        return

    cands_by_host = {cand.host: cand for cand in all_candidates.values()}
    in_library = existing_site_hosts()
    importable = build_importable(cands_by_host, merged, exclude_hosts=set(in_library))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "probed.json").write_text(
        json.dumps({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "results": merged}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (OUT_DIR / "importable.json").write_text(json.dumps(importable, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"明细 → {OUT_DIR / 'probed.json'}；可导入配置 {len(importable)} 条（默认停用，已排除库内已有 {len(in_library)} 站）→ {OUT_DIR / 'importable.json'}")


def run_diff() -> None:
    """只比对不探测：候选池 vs 库内已有站点，看哪些收录重合、哪些是纯新增。"""
    raw = json.loads((OUT_DIR / "candidates.json").read_text(encoding="utf-8"))
    pool_by_norm: dict[str, Candidate] = {normalize_host(cand.host): cand for cand in (Candidate(**item) for item in raw["candidates"])}
    in_library = existing_site_hosts()
    if not in_library:
        print("库读不到或没有站点，只统计候选池。")
    overlap = 0
    for norm_host, site_id in sorted(in_library.items(), key=lambda kv: kv[1]):
        cand = pool_by_norm.get(norm_host)
        if cand:
            overlap += 1
            print(f"  已收录 {site_id:24} {cand.host:30} 来源：{'、'.join(cand.sources)}")
        else:
            print(f"  不在池 {site_id:24} {norm_host:30}（聚合源没收录，仅你手动加的）")
    print(f"候选池 {len(pool_by_norm)} 站：库内已有 {len(in_library)} 站中重合 {overlap} 个，纯新增候选 {len(pool_by_norm) - overlap} 个（只发现未探测，跑 probe 才会检测）。")


def main() -> None:
    parser = argparse.ArgumentParser(description="中转站候选发现与探测（在仓库根目录运行）")
    sub = parser.add_subparsers(dest="command", required=True)
    p_harvest = sub.add_parser("harvest", help="从聚合源拉取候选站点清单")
    p_harvest.add_argument("--proxy", help="可选代理，如 http://127.0.0.1:7890")
    p_harvest.add_argument("--only", help="只用指定源（逗号分隔），缺省只用 zuiquanapi；all=全部源")
    p_probe = sub.add_parser("probe", help="探测候选站点的 /api/pricing 可用性")
    p_probe.add_argument("--take", type=int, default=0, help="只探测前 N 个（0=全部）")
    p_probe.add_argument("--concurrency", type=int, default=12)
    p_probe.add_argument("--timeout", type=float, default=10.0)
    p_probe.add_argument("--proxy", help="可选代理，http://127.0.0.1:7890 或 auto=自动探测本机常见代理端口")
    p_probe.add_argument("--retry-failed", action="store_true", help="只重测上轮未通过的站（配 --proxy auto 给被墙站翻案）")
    sub.add_parser("diff", help="只比对不探测：候选池里哪些站库里已有、哪些是新增")
    sw = sub.add_parser("sweep", help="网络空间测绘检索面板实例（不依赖收录名单，需引擎 API key 环境变量）")
    sw.add_argument("--engine", default="fofa", choices=sorted(ENGINE_KEYS), help="测绘引擎（key 从环境变量读）")
    sw.add_argument("--panel", default="new-api", choices=sorted(PANEL_FINGERPRINTS), help="面板指纹")
    sw.add_argument("--query", help="覆盖预设查询语法（引擎原样语法）")
    sw.add_argument("--size", type=int, default=200, help="取回条数（fofa body 查询上限 500）")
    args = parser.parse_args()
    if args.command == "harvest":
        asyncio.run(run_harvest(args.proxy, args.only))
    elif args.command == "sweep":
        run_sweep(args.engine, args.panel, args.query, args.size)
    elif args.command == "diff":
        run_diff()
    else:
        asyncio.run(run_probe(args.take, args.concurrency, args.timeout, args.proxy, args.retry_failed))


if __name__ == "__main__":
    main()
