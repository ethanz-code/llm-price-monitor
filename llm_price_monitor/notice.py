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
from llm_price_monitor.ai import AIConfig, extract_notice_content
from llm_price_monitor.config import PriceMonitorError, SiteSpec
from llm_price_monitor.evidence import redact_url


def _site_headers(spec: SiteSpec) -> dict[str, str]:
    """站点 network.headers 里的认证/Cookie 头：公告请求与价格采集共用同一套凭据。"""
    headers = spec.network.get("headers")
    return {str(key): str(value) for key, value in headers.items()} if isinstance(headers, dict) else {}


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
        entry = resolve_endpoint({"url": url, "headers": _site_headers(spec)}, spec=spec, label="notice")
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
    spec: SiteSpec,
    client: httpx.Client,
    timeout: float,
    user_agent: str,
    ai: AIConfig | None = None,
) -> dict[str, Any] | None:
    """采集单个站点的通知公告，返回含来源与解析方式的记录；content 为公告正文文本。

    未配置 notice.url 且自动推导地址返回 404 时返回 None（站点没有公告接口，静默跳过）。
    公告请求继承 network.headers 的认证/Cookie 头；固定解析拿不到正文（或正文是 HTML）时
    交给 AI 从原始响应中提取，AI 未启用或失败则保留固定解析结果。
    """
    url = resolve_notice_url(spec)
    if url is None:
        raise PriceMonitorError(f"站点 {spec.id} 未配置公告地址，且 network.url 缺失无法推导")
    explicit = isinstance(spec.notice.get("url"), str) and bool(str(spec.notice.get("url")).strip())
    notice_config = dict(spec.notice)
    notice_config.setdefault("url", url)
    entry_headers = notice_config.get("headers")
    notice_config["headers"] = {**_site_headers(spec), **{str(k): str(v) for k, v in entry_headers.items()}} if isinstance(entry_headers, dict) else _site_headers(spec)
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
        elif isinstance(data, dict) and isinstance(data.get("announcements"), list):
            # /api/status 型响应（data 携带 announcements）：直接结构化解析成公告正文，
            # 不再交给 AI，也不再向 /api/status 重复发起第二次请求。
            content = _announcement_markdown(
                [item for item in data["announcements"] if isinstance(item, dict) and str(item.get("content") or "").strip()]
            )
            parse = "status"
    elif payload is None or isinstance(payload, list):
        # 非 JSON、或顶层是 JSON 数组：没有可结构化解析的公告对象，按原文处理
        parse = "text"
        content = response.text.strip()
    # 固定解析拿不到正文，或正文是 HTML 片段时交给 AI 提取——AI 认得出任意响应结构里
    # 真正要拿的公告数据；未启用或失败时保留固定解析结果。
    if ai is not None and parse != "status" and (not content or content.lstrip().startswith("<")):
        extracted = extract_notice_content(ai, response.text)
        if extracted is not None:
            content = extracted
            parse = "ai" if extracted else f"{parse}+ai-empty"
    # new-api 系的多条公告走 /api/status：拿得到就按分节 Markdown 拼进正文（置顶公告在前）；
    # 公告地址本身已返回 announcements（parse == "status"）时不重复请求。
    announcement_md = (
        ""
        if parse == "status"
        else _announcement_markdown(_fetch_announcements(spec, client, timeout, user_agent))
    )
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
