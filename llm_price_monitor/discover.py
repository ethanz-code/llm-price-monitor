"""中转站候选发现与探测：从公开聚合源拉站点清单，探测 /api/pricing 可用性，产出可导入站点配置。

三个命令组成一条管线（只请求公开 JSON 端点，与采集主链路无关）：

    uv run price-discover harvest                  # 聚合源 → var/discovery/candidates.json
    uv run price-discover probe [--take N]         # 候选 → var/discovery/{probed,importable}.json
    uv run price-admin import-sites var/discovery/importable.json [--apply --take 30]

聚合源（新增源在 SOURCES 里加一个返回 list[Candidate] 的函数即可）：
    - daheiai/awesome-api-proxy   GitHub README 表格，596+ 站点，带在线状态/可用率/口碑分
    - apisou.com                  竞品导航站，sitemap 全量 129 个站点详情页（h1=站名、首个外链=站点地址）
    - panxunying/ai-coding-welfare  福利站导航仓库的 data/sites.json，机器可读
    - aiapipk.com                 中转站竞技场首页静态链接（约 39 站）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

OUT_DIR = Path("var/discovery")
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


def origin_of(url: str) -> str | None:
    """候选链接归一到 scheme://host（去掉 /register?aff=xxx 之类的推广路径）。"""
    parts = urlsplit(url.strip())
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


# ---------- 聚合源 ----------

AWESOME_README_URL = "https://raw.githubusercontent.com/daheiai/awesome-api-proxy/main/README.md"
WELFARE_SITES_URL = "https://raw.githubusercontent.com/panxunying/ai-coding-welfare/main/data/sites.json"
APISOU_BASE = "https://www.apisou.com"
AIAPIPK_URL = "https://www.aiapipk.com"

# 表格行尾的「状态 | 可用率 | 评价净值」三列
_AWESOME_TAIL = re.compile(r"\|\s*(在线|离线|未知)\s*\|\s*([\d.]+%)\s*\|\s*([+\-\d]+)\s*\|\s*$")


def harvest_awesome_api_proxy(text: str) -> list[Candidate]:
    """解析 awesome-api-proxy README 的站点表格。"""
    out: list[Candidate] = []
    for line in text.splitlines():
        link = re.match(r"^\|\s*\d+\s*\|\s*\[([^\]]+)\]\((https?://[^)\s]+)\)", line)
        if not link:
            continue
        origin = origin_of(link.group(2))
        if origin is None:
            continue
        tail = _AWESOME_TAIL.search(line)
        meta: dict = {}
        if tail:
            meta = {"status": tail.group(1), "uptime7d": tail.group(2), "rating": int(tail.group(3))}
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
    out: list[Candidate] = []
    for url in re.findall(r'https?://[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}[^"\'<> ]*', text):
        origin = origin_of(url)
        if origin is None or "aiapipk.com" in origin:
            continue
        host = urlsplit(origin).hostname or ""
        out.append(Candidate(host, origin, host, ["aiapipk"]))
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
    """按 host 合并去重：多源收录的合并 sources，保留信息最全的一条。"""
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
            if not existing.meta:
                existing.meta = cand.meta
    return sorted(by_host.values(), key=lambda c: c.host)


async def harvest(proxy: str | None) -> list[Candidate]:
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

        async def simple(fn, url: str, timeout: float = 20.0) -> list[Candidate]:
            # 大文件（596 站的 README 有 137KB）首拉容易超时，重试一次
            for attempt in (1, 2):
                try:
                    return fn(await get_text(url, timeout=timeout))
                except Exception as exc:
                    if attempt == 2:
                        print(f"  源 {url} 拉取失败：{type(exc).__name__} {exc}")
            return []

        awesome, welfare, aiapipk, apisou = await asyncio.gather(
            simple(harvest_awesome_api_proxy, AWESOME_README_URL, timeout=60.0),
            simple(harvest_welfare, WELFARE_SITES_URL),
            simple(harvest_aiapipk, AIAPIPK_URL),
            apisou_all(),
        )
        print(f"  awesome-api-proxy {len(awesome)}、ai-coding-welfare {len(welfare)}、aiapipk {len(aiapipk)}、apisou {len(apisou)}")
    return merge_candidates([awesome, welfare, aiapipk, apisou])


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
    result: dict = {
        "name": cand.name,
        "url": cand.url,
        "sources": cand.sources,
        "note": cand.meta.get("status", ""),
        "new_api": False,
        "pricing_ok": False,
        "models": 0,
        "auth_required": False,
        "error": "",
    }
    try:
        status_resp = await client.get(f"{cand.url}/api/status")
        if status_resp.status_code == 200:
            payload = status_resp.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and ("system_name" in data or "version" in data):
                result["new_api"] = True
                result["system_name"] = str(data.get("system_name") or "")
    except Exception:
        pass
    try:
        pricing_resp = await client.get(f"{cand.url}/api/pricing")
        if pricing_resp.status_code in (401, 403):
            result["auth_required"] = True
        elif pricing_resp.status_code == 200:
            models, ok = classify_pricing(pricing_resp.json())
            result["models"], result["pricing_ok"] = models, ok
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:120]
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


def build_importable(cands_by_host: dict[str, Candidate], probed: list[dict]) -> list[dict]:
    """探测通过的站点生成配置（默认停用，导入后在面板按批启用）。"""
    taken: set[str] = set()
    configs: list[dict] = []
    ranked = sorted(probed, key=lambda r: (not r["new_api"], -r["models"], r["url"]))
    for row in ranked:
        if not row["pricing_ok"]:
            continue
        cand = cands_by_host[urlsplit(row["url"]).hostname or ""]
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


async def run_harvest(proxy: str | None) -> None:
    print("正在拉取聚合源…")
    candidates = await harvest(proxy)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / "candidates.json"
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(candidates),
        "candidates": [cand.__dict__ for cand in candidates],
    }
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"共 {len(candidates)} 个候选站点（按 host 去重）→ {out_file}")


async def run_probe(take: int, concurrency: int, timeout: float, proxy: str | None) -> None:
    raw = json.loads((OUT_DIR / "candidates.json").read_text(encoding="utf-8"))
    candidates = [Candidate(**item) for item in raw["candidates"]]
    if take > 0:
        candidates = candidates[:take]
    print(f"开始探测 {len(candidates)} 个候选（并发 {concurrency}，超时 {timeout}s）…")
    started = time.monotonic()
    probed = await probe(candidates, concurrency, timeout, proxy)
    ok = [r for r in probed if r["pricing_ok"]]
    auth = [r for r in probed if r["auth_required"]]
    new_api = [r for r in probed if r["new_api"]]
    print(
        f"完成（{time.monotonic() - started:.0f}s）：价格接口可用 {len(ok)}"
        f"（其中 new-api 系 {len([r for r in ok if r['new_api']])}）"
        f"、需登录 {len(auth)}、不可达/失败 {len(probed) - len(ok) - len(auth)}"
    )

    cands_by_host = {cand.host: cand for cand in candidates}
    importable = build_importable(cands_by_host, probed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "probed.json").write_text(
        json.dumps({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "results": probed}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (OUT_DIR / "importable.json").write_text(json.dumps(importable, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"明细 → {OUT_DIR / 'probed.json'}；可导入配置 {len(importable)} 条（默认停用）→ {OUT_DIR / 'importable.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="中转站候选发现与探测（在仓库根目录运行）")
    sub = parser.add_subparsers(dest="command", required=True)
    p_harvest = sub.add_parser("harvest", help="从聚合源拉取候选站点清单")
    p_harvest.add_argument("--proxy", help="可选代理，如 http://127.0.0.1:7890")
    p_probe = sub.add_parser("probe", help="探测候选站点的 /api/pricing 可用性")
    p_probe.add_argument("--take", type=int, default=0, help="只探测前 N 个（0=全部）")
    p_probe.add_argument("--concurrency", type=int, default=12)
    p_probe.add_argument("--timeout", type=float, default=10.0)
    p_probe.add_argument("--proxy", help="可选代理，如 http://127.0.0.1:7890")
    args = parser.parse_args()
    if args.command == "harvest":
        asyncio.run(run_harvest(args.proxy))
    else:
        asyncio.run(run_probe(args.take, args.concurrency, args.timeout, args.proxy))


if __name__ == "__main__":
    main()
