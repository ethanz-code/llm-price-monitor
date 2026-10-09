"""USD→CNY 汇率获取：多源容灾，按顺序探测直到成功。

源顺序：
1. open.er-api.com —— ExchangeRate-API 免费端点（海外，国内直连可能不通）
2. jsDelivr CDN 分发的 fawazahmed0 currency-api —— CDN 分发，国内可达性最好
3. Frankfurter —— 欧央行数据

全部在线源失败时回退本地缓存，再失败用 fallback 兜底值；`rate_source`
如实记录实际使用的源，方便排查部署环境的网络状况。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import httpx

from .jsonio import read_json_object, write_json

FX_CACHE_FILE = Path("var/fx-cache.json")

_ER_API_URL = "https://open.er-api.com/v6/latest/USD"
_CURRENCY_API_URLS = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
    "https://latest.currency-api.pages.dev/v1/currencies/usd.json",
)
_FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=CNY"


def _from_er_api(client: httpx.Client) -> float:
    return float(client.get(_ER_API_URL, timeout=15).json()["rates"]["CNY"])


def _from_currency_api(client: httpx.Client) -> float:
    last_error: Exception | None = None
    for url in _CURRENCY_API_URLS:
        try:
            return float(client.get(url, timeout=15).json()["usd"]["cny"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
    raise ValueError(f"currency-api 各端点均失败: {last_error}") from last_error


def _from_frankfurter(client: httpx.Client) -> float:
    return float(client.get(_FRANKFURTER_URL, timeout=15).json()["rates"]["CNY"])


_SOURCES: tuple[tuple[str, Callable[[httpx.Client], float]], ...] = (
    ("open.er-api.com 实时", _from_er_api),
    ("jsDelivr currency-api（CDN）", _from_currency_api),
    ("frankfurter.dev（ECB）", _from_frankfurter),
)


def get_usd_cny_rate(client: httpx.Client, fallback: float | None = None) -> tuple[float, str]:
    """返回 (汇率, 来源说明)。在线源按顺序探测，全部失败回退缓存或兜底值。"""
    for source_name, fetch in _SOURCES:
        try:
            rate = fetch(client)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            continue
        write_json(FX_CACHE_FILE, {"CNY": {"rate": rate, "source": source_name, "fetched_at": time.time()}}, indent=None)
        return rate, source_name
    cached = read_json_object(FX_CACHE_FILE).get("CNY")
    if isinstance(cached, dict) and cached.get("rate"):
        return float(cached["rate"]), f"在线源均失败，使用缓存（原源：{cached.get('source', '未知')}）"
    if fallback is not None:
        return fallback, "在线源均失败，使用 --usd-cny 兜底"
    raise ValueError("所有汇率在线源均失败，且无缓存或 --usd-cny 兜底可用") from None
