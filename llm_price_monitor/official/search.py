"""搜索编排：按厂商 / 按模型两条路径，统一处理多查询、官方域名收敛与重试。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .extraction import build_entry, extract_official_prices
from .tavily import tavily_search


@dataclass(frozen=True)
class VendorSpec:
    """厂商名 + 其官方定价所在的域名（用于 Tavily include_domains 收敛结果）。"""

    vendor: str
    domains: list[str] = field(default_factory=list)


# 默认按厂商搜索的国内外知名厂商清单；国内厂商官方页多为 CNY 标价，AI 会声明币种。
DEFAULT_VENDORS: list[VendorSpec] = [
    VendorSpec("OpenAI", ["openai.com", "developers.openai.com"]),
    VendorSpec("Anthropic", ["anthropic.com", "platform.claude.com", "docs.anthropic.com"]),
    VendorSpec("Google", ["ai.google.dev", "cloud.google.com"]),
    VendorSpec("xAI", ["x.ai", "docs.x.ai"]),
    VendorSpec("DeepSeek", ["deepseek.com", "api-docs.deepseek.com"]),
    VendorSpec("Moonshot AI", ["moonshot.cn", "platform.moonshot.cn"]),
    VendorSpec("Alibaba Cloud", ["aliyun.com", "help.aliyun.com"]),
    VendorSpec("Zhipu AI", ["bigmodel.cn", "open.bigmodel.cn"]),
]


@dataclass
class SearchResult:
    """单次搜索编排的结果。found=True 时 entries 是规范化后的官方价条目。"""

    found: bool
    vendor: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


def _queries(vendor: str) -> list[str]:
    return [
        f"{vendor} API pricing per 1M tokens models",
        f"{vendor} 官方 API 价格 模型 每百万 tokens",
    ]


def _absorb_extracted(extracted: dict[str, Any], vendor: str) -> list[dict[str, Any]]:
    """把 AI 返回的 models 数组逐条规范化。"""
    raw_models = extracted.get("models") if isinstance(extracted.get("models"), list) else []
    source_url = str(extracted.get("source_url") or "")
    entries: list[dict[str, Any]] = []
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        entries.append(build_entry(raw, vendor, source_url))
    return entries


def search_vendor(
    ai: Any,
    tavily_key: str | None,
    rate: float,
    *,
    vendor: str,
    domains: list[str] | None = None,
) -> SearchResult:
    """搜索一个厂商的官方定价。

    每个查询都完整走一遍"搜索 -> AI 提取"，前一个查询没提取到再用下一个；
    官方域名收敛优先、无结果时放开域名；AI 瞬时失败整体重试一轮。
    """
    last_reason = "Tavily 未返回搜索结果"
    queries = _queries(vendor)
    with httpx.Client(follow_redirects=True) as client:
        for attempt in range(2):
            for query in queries:
                try:
                    results = tavily_search(client, tavily_key, query, domains if attempt == 0 else None)
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    last_reason = str(exc)
                    continue
                if not results:
                    last_reason = "Tavily 未返回搜索结果"
                    continue
                try:
                    extracted = extract_official_prices(ai, vendor, results)
                    entries = _absorb_extracted(extracted, vendor)
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    last_reason = str(exc)
                    continue
                if entries:
                    return SearchResult(found=True, vendor=vendor, entries=entries)
                last_reason = str(extracted.get("notes") or "AI 返回的模型条目均缺少可用价格")
            if attempt == 0:
                import time

                time.sleep(2)
    return SearchResult(found=False, vendor=vendor, reason=last_reason)
