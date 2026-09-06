"""站点公告采集：GET 公告接口（new-api/one-api 系默认 /api/notice），提取公告正文，与价格采集同周期顺带执行。

地址解析：配置 notice.url 优先；未配置时从 network.url 推导站点根地址拼 /api/notice。
解析两层：new-api 包装 {success, message, data}（data 为 Markdown 正文）直接取 data；
非 JSON 响应按文本原样保留——notice.url 也可以指向纯文本/Markdown 公告页。
new-api 系的多条公告（后台"公告"管理发布）走公开的 /api/status → data.announcements
数组；拿得到就按"标题 + 日期 + 正文"分节拼进公告正文（置顶公告在前，最新在前），
拿不到（非 new-api、接口 404/失败）就回落到只存 /api/notice 的单条公告。
正文为空视为站点未设置公告，调用方不入库；变化检测由调用方对正文做文本比较生成事件。
404 分两种：未配置 notice.url（自动推导）时视为站点没有公告接口，返回 None 由调用方
静默跳过；显式配置了 notice.url 的 404 是配置错误，照常抛错暴露给采集错误列表。
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from llm_price_monitor.adapters import build_request_kwargs, resolve_endpoint
from llm_price_monitor.config import PriceMonitorError, SiteSpec
from llm_price_monitor.evidence import redact_url


def resolve_notice_url(spec: SiteSpec) -> str | None:
    """公告地址：显式配置优先，否则从 network.url 推导 new-api 系默认的 /api/notice。"""
    configured = spec.notice.get("url")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    network_url = str(spec.network.get("url") or "").strip()
    if not network_url:
        return None
    parsed = urlsplit(network_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/api/notice"

def _resolve_status_url(spec: SiteSpec) -> str | None:
    """new-api 系公开状态接口（自带公告列表）：从 network.url 推导根地址拼 /api/status。"""
    network_url = str(spec.network.get("url") or "").strip()
    parsed = urlsplit(network_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/api/status"


def _fetch_announcements(
    spec: SiteSpec, client: httpx.Client, timeout: float, user_agent: str
) -> list[dict[str, Any]]:
    """拉取 /api/status 的 announcements 公告列表（new-api 系的多条公告）。

    接口缺失、非 JSON、字段不符或请求失败一律返回空列表——公告列表拿不到时
    回落到只存 /api/notice 的单条公告，不让公告采集整体失败。
    """
    url = _resolve_status_url(spec)
    if url is None:
        return []
    try:
        entry = resolve_endpoint({"url": url}, spec=spec, label="notice")
        response = client.get(entry.url, **build_request_kwargs(entry, spec, user_agent, timeout))
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, PriceMonitorError):
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return []
    items = payload["data"].get("announcements")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and str(item.get("content") or "").strip()]


def _announcement_markdown(items: list[dict[str, Any]]) -> str:
    """公告数组渲染成 Markdown 分节：标题（extra）+ 日期（publishDate）+ 正文，最新在前。"""

    def order(item: dict[str, Any]) -> tuple[str, str]:
        return (str(item.get("publishDate") or ""), str(item.get("id") or ""))

    sections: list[str] = []
    for item in sorted(items, key=order, reverse=True):
        title = str(item.get("extra") or "").strip() or "公告"
        date = str(item.get("publishDate") or "").strip()[:10]
        heading = f"## {title}" + (f"（{date}）" if date else "")
        sections.append(f"{heading}\n\n{str(item.get('content') or '').strip()}")
    return "\n\n".join(sections)


def fetch_site_notice(
    spec: SiteSpec, client: httpx.Client, timeout: float, user_agent: str
) -> dict[str, Any]:
    """采集单个站点的通知公告，返回含来源与解析方式的记录；content 为公告正文文本。

    未配置 notice.url 且自动推导地址返回 404 时返回 None（站点没有公告接口，静默跳过）。
    """
    url = resolve_notice_url(spec)
    if url is None:
        raise PriceMonitorError(f"站点 {spec.id} 未配置公告地址，且 network.url 缺失无法推导")
    explicit = isinstance(spec.notice.get("url"), str) and bool(str(spec.notice.get("url")).strip())
    notice_config = dict(spec.notice)
    notice_config.setdefault("url", url)
    entry = resolve_endpoint(notice_config, spec=spec, label="notice")
    response = client.get(entry.url, **build_request_kwargs(entry, spec, user_agent, timeout))
    if response.status_code == 404:
        if not explicit:
            return None  # 自动推导地址 404 = 站点没有公告接口，属常态，静默跳过
        raise PriceMonitorError("公告地址返回 HTTP 404，请检查 notice.url 是否正确")
    if response.status_code in {401, 403}:
        raise PriceMonitorError(f"公告地址返回 HTTP {response.status_code}，可能需要认证")
    response.raise_for_status()
    content = ""
    parse = "json"
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        if payload.get("success") is False:
            raise PriceMonitorError(f"公告接口返回失败: {payload.get('message') or '未知错误'}")
        data = payload.get("data")
        if isinstance(data, str):
            content = data.strip()
    elif payload is None:
        parse = "text"
        content = response.text.strip()
    # new-api 系的多条公告走 /api/status：拿得到就按分节 Markdown 拼进正文（置顶公告在前）
    announcement_md = _announcement_markdown(_fetch_announcements(spec, client, timeout, user_agent))
    if announcement_md:
        content = "\n\n".join(part for part in (content, announcement_md) if part)
        parse = f"{parse}+status"
    return {
        "site_id": spec.id,
        "captured_at": time.time(),
        "source_url": redact_url(str(response.url)),
        "http_status": response.status_code,
        "parse": parse,
        "content": content,
    }
