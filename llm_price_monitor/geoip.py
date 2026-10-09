"""站点 IP 定位：域名解析 → IP 归属地（ip-api.com），供首页监控星空摆放站点节点。

结果按站点缓存（DNS 1 小时 / 归属地 7 天、失败 30 分钟）；归属地走批量接口，
一轮只发一次请求把缓存失效的 IP 全查完（≤100 个/请求），避免逐个查询把免费
接口的每分钟配额打爆——首页每次加载都会为失败站点重查，逐个查必然自我限流。
批量请求传输层失败（限流/超时）按 2 分钟短缓存退避；定位失败不影响其他站点，
返回里只带成功解析的站点。部署在国内时 ip-api 免费接口为 HTTP 明文，仅作展示用途。
"""
from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from typing import Any

_DNS_TTL = 3600.0
_DNS_FAIL_TTL = 300.0
GEO_TTL = 7 * 86400.0
GEO_FAIL_TTL = 1800.0
# 批量请求整个失败（限流/超时）属于瞬时故障：短退避后即可重试，不随单 IP 失败记 30 分钟
_GEO_TRANSIENT_TTL = 120.0
_BATCH_SIZE = 100
GEO_BATCH_API = "http://ip-api.com/batch?fields=status,message,country,city,lat,lon&lang=zh-CN"
# 本机开代理（fake-IP DNS）时系统解析返回 198.18.0.0/15 保留段，拿不到真实 IP；
# 统一走公共 DoH 拿真实 A 记录，对生产环境同样适用。
DOH_ENDPOINTS = (
    "https://dns.alidns.com/resolve?name={host}&type=A",
    "https://cloudflare-dns.com/dns-query?name={host}&type=A",
)

# 缓存值为 (到期时刻 monotonic, 值)；值为 None 表示上次查询失败，到期前直接复用失败结论
_dns_cache: dict[str, tuple[float, str | None]] = {}
_geo_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _cached(entry: tuple[float, Any] | None) -> tuple[bool, Any]:
    """返回 (是否在有效期内, 缓存值)。"""
    if not entry:
        return False, None
    return time.monotonic() < entry[0], entry[1]


def _fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
    data: bytes | None = None,
    method: str = "GET",
) -> Any:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resolve_host(host: str) -> str | None:
    """解析域名为公网 IP：先公共 DoH，失败再退回系统解析；私有/保留地址一律舍弃。"""
    live, value = _cached(_dns_cache.get(host))
    if live:
        return value

    now = time.monotonic()
    ip: str | None = None

    def acceptable(addr: str) -> bool:
        try:
            return ipaddress.ip_address(addr).is_global
        except ValueError:
            return False

    for endpoint in DOH_ENDPOINTS:
        try:
            data = _fetch_json(endpoint.format(host=host), headers={"accept": "application/dns-json"})
            for answer in data.get("Answer", []):
                if answer.get("type") == 1 and acceptable(str(answer.get("data", ""))):
                    ip = str(answer["data"])
                    break
            if ip:
                break
        except (OSError, ValueError):
            continue

    if not ip:
        try:
            addr = socket.gethostbyname(host)
            if acceptable(addr):
                ip = addr
        except OSError:
            ip = None

    _dns_cache[host] = (now + (_DNS_TTL if ip else _DNS_FAIL_TTL), ip)
    return ip


def _locate_ips(ips: list[str]) -> None:
    """批量补查缓存失效的 IP 并写入归属地缓存。

    响应数组与请求顺序一一对应（条目不带 ip 字段，必须按位置对回）；
    传输层失败或长度对不上时整批按短 TTL 记失败退避，已有缓存不受影响。"""
    now = time.monotonic()
    for start in range(0, len(ips), _BATCH_SIZE):
        chunk = ips[start : start + _BATCH_SIZE]
        answers: Any = None
        try:
            answers = _fetch_json(
                GEO_BATCH_API,
                headers={"Content-Type": "application/json"},
                timeout=8.0,
                data=json.dumps(chunk).encode("utf-8"),
                method="POST",
            )
        except OSError:
            answers = None

        if not isinstance(answers, list) or len(answers) != len(chunk):
            for ip in chunk:
                _geo_cache[ip] = (now + _GEO_TRANSIENT_TTL, None)
            continue

        for ip, answer in zip(chunk, answers):
            result: dict[str, Any] | None = None
            try:
                if isinstance(answer, dict) and answer.get("status") == "success":
                    result = {
                        "ip": ip,
                        "lat": float(answer["lat"]),
                        "lon": float(answer["lon"]),
                        "country": answer.get("country", ""),
                        "city": answer.get("city", ""),
                    }
            except (KeyError, TypeError, ValueError):
                result = None
            _geo_cache[ip] = (now + (GEO_TTL if result else GEO_FAIL_TTL), result)


def _host_of(config: dict[str, Any]) -> str | None:
    """从站点配置里取第一个可用域名：network.url 优先，source_url 兜底。"""
    for url in (config.get("network", {}).get("url"), config.get("source_url")):
        if isinstance(url, str) and url.strip():
            host = urlsplit(url).hostname
            if host:
                return host
    return None


def resolve_site_geo(configs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """逐站点解析「域名 → IP → 归属地」，失败的站点不出现在结果里。"""
    hosts = {config["id"]: _host_of(config) for config in configs if config.get("id")}

    with ThreadPoolExecutor(max_workers=8) as pool:
        resolved = dict(zip(hosts.keys(), pool.map(lambda h: _resolve_host(h) if h else None, hosts.values())))

    ips = {site_id: ip for site_id, ip in resolved.items() if ip}
    missing = sorted({ip for ip in ips.values() if not _cached(_geo_cache.get(ip))[0]})
    if missing:
        _locate_ips(missing)

    located = {site_id: _cached(_geo_cache.get(ip)) for site_id, ip in ips.items()}
    return {site_id: geo for site_id, (_, geo) in located.items() if geo}
