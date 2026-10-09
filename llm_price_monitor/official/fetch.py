"""Tavily 搜索各厂商官方模型原价，生成官方价总结 dict。

由 webapi 的官方价刷新任务调用；持久化（文件或数据库）由调用方决定。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from llm_price_monitor.config import MonitorConfig
from llm_price_monitor.official import fx, normalize, search, tavily


def fetch_official(
    config: MonitorConfig,
    *,
    previous: dict[str, Any] | None = None,
    tavily_key: str | None = None,
    vendors: list[search.VendorSpec] | None = None,
) -> dict[str, Any]:
    """搜索各厂商官方价并返回完整输出 dict；不落盘。"""
    key = tavily.resolve_tavily_key(tavily_key)
    if not key:
        raise ValueError("未找到 Tavily key（显式传入 / TAVILY_API_KEY / tvly 登录态）")

    previous_models: dict[str, Any] = previous.get("models", {}) if isinstance(previous, dict) else {}

    with httpx.Client(follow_redirects=True) as client:
        rate, rate_source = fx.get_usd_cny_rate(client)

    models: dict[str, dict[str, Any]] = {}
    for spec in vendors if vendors is not None else search.DEFAULT_VENDORS:
        result = search.search_vendor(config.ai, key, rate, vendor=spec.vendor, domains=spec.domains)
        for entry in result.entries:
            model_key = normalize.model_key(str(entry.get("model") or ""))
            if model_key:
                models[model_key] = entry

    # 全局补齐：本次没搜到、但上一轮已有的模型原样保留，避免一次波动丢数据。
    for old_key, old_entry in previous_models.items():
        if old_key not in models and isinstance(old_entry, dict) and old_entry.get("found"):
            models[old_key] = {**old_entry, "from_previous_run": True}

    return {
        "generated_at": time.time(),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "search_engine": "tavily",
        "usd_cny_rate": normalize.round2(rate),
        "rate_source": rate_source,
        "models": models,
    }
