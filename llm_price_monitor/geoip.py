"""站点 IP 定位：域名解析 → IP 归属地（ip-api.com），供首页监控地球摆放站点节点。

结果按站点缓存（DNS 1 小时 / 归属地 7 天），定位失败不影响其他站点，
返回里只带成功解析的站点；部署在国内时 ip-api 免费接口为 HTTP 明文，仅作展示用途。
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
GEO_API = "http://ip-api.com/json/{ip}?fields=status,message,country,city,lat,lon&lang=zh-CN"
# 本机开代理（fake-IP DNS）时系统解析返回 198.18.0.0/15 保留段，拿不到真实 IP；
# 统一走公共 DoH 拿真实 A 记录，对生产环境同样适用。
DOH_ENDPOINTS = (
    "https://dns.alidns.com/resolve?name={host}&type=A",
    "https://cloudflare-dns.com/dns-query?name={host}&type=A",
)

_dns_cache: dict[str, tuple[float, str | None]] = {}
_geo_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _cached(cached: tuple[float, Any] | None, ttl: float, fail_ttl: float) -> tuple[bool, Any]:
    """命中缓存时返回 (True, 值)；成功值用 ttl、失败值用更短的 fail_ttl，避免瞬时故障被长期记住。"""
    if not cached:
        return False, None
    age = time.monotonic() - cached[0]
    live = age < (ttl if cached[1] is not None else fail_ttl)
    return live, cached[1]


def _fetch_json(url: str, headers: dict[str, str] | None = None, timeout: float = 5.0) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resolve_host(host: str) -> str | None:
    """解析域名为公网 IP：先公共 DoH，失败再退回系统解析；私有/保留地址一律舍弃。"""
    now = time.monotonic()
    live, value = _cached(_dns_cache.get(host), _DNS_TTL, _DNS_FAIL_TTL)
    if live:
        return value

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

    _dns_cache[host] = (now, ip)
    return ip


def _geolocate(ip: str) -> dict[str, Any] | None:
    """查询 IP 归属地；接口失败或限流时返回 None，调用方下次过 TTL 再试。"""
    now = time.monotonic()
    live, value = _cached(_geo_cache.get(ip), GEO_TTL, GEO_FAIL_TTL)
    if live:
        return value
    result: dict[str, Any] | None = None
    try:
        data = _fetch_json(GEO_API.format(ip=ip))
        if data.get("status") == "success":
            result = {
                "ip": ip,
                "lat": float(data["lat"]),
                "lon": float(data["lon"]),
                "country": data.get("country", ""),
                "city": data.get("city", ""),
            }
    except (OSError, ValueError, KeyError):
        result = None
    _geo_cache[ip] = (now, result)
    return result


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
    with ThreadPoolExecutor(max_workers=8) as pool:
        located = dict(zip(ips.keys(), pool.map(_geolocate, ips.values())))

    return {site_id: located[site_id] for site_id in located if located[site_id]}
