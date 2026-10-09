"""主流水线：拉取一个定价页，静态解析 → 同名 .md 探测 → Headless 渲染 → AI 兜底。"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

import llm_price_monitor.page_price as _pp
from llm_price_monitor.ai import AIExtractionError
from llm_price_monitor.config import AIConfig, PriceMonitorError
from llm_price_monitor.useragent import DEFAULT_BROWSER_USER_AGENT

from .ai_fallback import _AI_PAGE_BUDGET_SECONDS, _ai_extract, _records_suspicious
from .tables import _parse_text


def fetch_page_prices(
    url: str,
    *,
    ai_config: AIConfig | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = _pp.DEFAULT_TIMEOUT,
    headless: bool = False,
    user_agent: str = DEFAULT_BROWSER_USER_AGENT,
) -> dict[str, Any]:
    """拉取一个定价页并解析出全部模型价格；返回 {"url","final_url","method","models","warnings"}。"""
    warnings: list[str] = []
    with httpx.Client(
        follow_redirects=True,
        timeout=timeout,
        transport=transport,
        headers={"User-Agent": user_agent},
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        final_url = str(response.url)
        text = response.text

        parsed = _parse_text(text, final_url, "static")
        # Mintlify 系文档站每个页面都有同名 .md 原文；页面把定价表做成客户端组件时
        # HTML 里没有表格，探测同名 .md 拿结构化原文再解析一遍，比渲染/AI 都稳，
        # 成本只在整条静态链路落空后的一次 GET
        probe_source = urlsplit(final_url)
        if (
            parsed is None
            and not probe_source.path.lower().endswith((".md", ".json", ".txt"))
            and probe_source.path.rstrip("/")
        ):
            probe_url = urlunsplit(probe_source._replace(path=probe_source.path.rstrip("/") + ".md"))
            try:
                probe = client.get(probe_url)
                if probe.status_code == 200 and probe.text:
                    parsed = _parse_text(probe.text, final_url, "md-source")
                    if parsed:
                        text = probe.text  # 后续 AI 复核也优先看这份带表格结构的原文
            except httpx.HTTPError:
                pass  # 探测只是兜底路径，失败照常走渲染/AI
        rendered: str | None = None
        if parsed is None and headless:
            try:
                rendered = _pp.fetch_page_html(url, {}, user_agent=user_agent)
                parsed = _parse_text(rendered, final_url, "headless")
            except PriceMonitorError as exc:
                warnings.append(str(exc))
        # 解析"有结果但可疑"（档位注释/合并行/重复档价）不能当可信基准：交给 AI 复核，
        # AI 没有产出时整页降级为 candidate，宁可多人工复核也不静默采脏价
        suspect = parsed is not None and _records_suspicious(parsed[0])
        ai_enabled = ai_config is not None and ai_config.enabled
        if (parsed is None or suspect) and ai_enabled:
            try:
                # 渲染出过正文（无论解析成败）就优先给 AI 看渲染后的正文——原始 text 可能是 JS 空壳
                ai_text = rendered or text
                records, ai_warnings = _pp._ai_extract(
                    ai_text, final_url, ai_config, client, deadline=time.monotonic() + _AI_PAGE_BUDGET_SECONDS,
                )
                warnings.extend(ai_warnings)
                if records:
                    parsed = (records, "ai")
                    suspect = False
            except (AIExtractionError, httpx.HTTPError, PriceMonitorError) as exc:
                warnings.append(f"AI 兜底失败：{exc}")
        elif ai_config is not None and not ai_config.enabled and (parsed is None or suspect):
            warnings.append("AI 配置已禁用，跳过 AI 兜底")
        if parsed is not None and suspect:
            for record in parsed[0]:
                record["price_status"] = "candidate"
            warnings.append("确定性解析结果可疑（模型名带档位注释或同模型多价），已整页标为待复核")

    records, method = parsed if parsed else ([], "none")
    if parsed is None:
        warnings.append("静态解析、Headless 渲染与 AI 兜底都没有拿到价格")
    return {
        "url": url,
        "final_url": final_url,
        "method": method,
        "models": records,
        "warnings": warnings,
    }
