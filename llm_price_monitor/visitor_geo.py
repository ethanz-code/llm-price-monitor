"""访客 IP 归属地解析：批量调用 ip-api.com，把访问记录里的 IP 归到省份。

与 geoip.py（站点 IP 定位）分开：访客 IP 数量不可控，走 ip-api 的批量接口
（单批最多 100 个，免费限额 45 请求/分钟），结果由 Store.ip_geo 表持久缓存，
解析失败只影响地图展示，不阻塞统计主流程。
"""
from __future__ import annotations

import ipaddress
import json
import urllib.request
from typing import Any

# 批量接口上限 100 个/批；一次请求解析太多会撞免费限额，每次统计最多补 2 批
_BATCH_SIZE = 100
GEO_API = "http://ip-api.com/batch?fields=status,country,regionName,city,lat,lon&lang=zh-CN"

# ip-api 对国内省份可能返回全称（如"黑龙江省"），地图用简称，这里统一归一
_REGION_SUFFIXES = ("特别行政区", "维吾尔自治区", "回族自治区", "壮族自治区", "自治区", "省", "市", "盟")
# 全称截前两个字即简称的自治区（内蒙古/广西/宁夏/新疆/西藏），其余按后缀剥离
_ABBR_AUTONOMOUS = ("内蒙古", "广西", "宁夏", "新疆", "西藏")


def normalize_region(country: str, region: str) -> str:
    """归一化为省份简称；非中国大陆（含港台澳以外的国家）归入"海外"。"""
    if country != "中国":
        return "海外"
    name = (region or "").strip() or "未知"
    for abbr in _ABBR_AUTONOMOUS:
        if name.startswith(abbr):
            return abbr
    for suffix in _REGION_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _fetch_batch(ips: list[str], timeout: float = 6.0) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        GEO_API,
        data=json.dumps(ips).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_regions(ips: list[str]) -> dict[str, dict[str, Any]]:
    """批量解析 IP 归属地；返回 {ip: {country, region, province, city, lat, lon}}，失败的 IP 不出现在结果里。

    内网/保留地址跳过解析，直接记为 province="本地网络"。
    """
    results: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    for ip in ips:
        try:
            if not ipaddress.ip_address(ip).is_global:
                results[ip] = {"country": "中国", "region": "", "province": "本地网络", "city": "", "lat": None, "lon": None}
                continue
        except ValueError:
            continue
        pending.append(ip)

    for offset in range(0, len(pending), _BATCH_SIZE):
        chunk = pending[offset : offset + _BATCH_SIZE]
        try:
            rows = _fetch_batch(chunk)
        except Exception:
            continue  # 单批失败放弃该批，缓存里没有的 IP 下次统计再试
        for ip, row in zip(chunk, rows):
            if row.get("status") != "success":
                continue
            country = str(row.get("country") or "")
            region = str(row.get("regionName") or "")
            results[ip] = {
                "country": country,
                "region": region,
                "province": normalize_region(country, region),
                "city": str(row.get("city") or ""),
                "lat": row.get("lat"),
                "lon": row.get("lon"),
            }
    return results
