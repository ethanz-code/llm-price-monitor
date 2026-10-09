"""档位（tiers）基准价组合：标准档挑选与输入/输出/缓存命中三件套的组合规则。

表格解析（转置规格表）与 AI 兜底共用，单独成模块避免两头互相依赖。
"""
from __future__ import annotations

import re
from typing import Any

# 标准/默认档位的名称特征：页面把折扣档（如空闲时段）标出来时，基准应取标准档
_STANDARD_TIER_PATTERN = re.compile(r"高峰|标准|默认|正常|peak|standard|default", re.IGNORECASE)

# 缓存命中档的名称特征：输入按缓存命中/未命中分两行时区分计费类别
_CACHE_HIT_TIER_PATTERN = re.compile(r"缓存命中|命中缓存|cache\s*hit|cached", re.IGNORECASE)


def _pick_baseline_tier(tiers: list[dict[str, Any]]) -> dict[str, Any]:
    """基准档：AI 标记的标准档优先，其次档位名带高峰/标准等特征，否则第一档。"""
    for tier in tiers:
        if tier["standard"]:
            return tier
    for tier in tiers:
        if tier["name"] and _STANDARD_TIER_PATTERN.search(tier["name"]):
            return tier
    return tiers[0]


def _compose_baseline(tiers: list[dict[str, Any]]) -> dict[str, Any]:
    """基准价三件套（输入/输出/缓存命中），从档位列表组合而来。

    常见形态每档自带输入+输出（如上下文分档、时段整体半价），沿用单档基准；
    分维表（如 DeepSeek：缓存命中/未命中输入与输出各自一行 × 空闲/高峰）没有
    一档同时带输入和输出，按计费类别各选标准档组合——输入取缓存未命中档、
    缓存命中价取缓存命中档、输出取输出档；页面只标缓存命中输入价时以其兜底。
    """
    cache_tiers = [
        tier for tier in tiers
        if tier["cache_read"] is not None or (tier["name"] and _CACHE_HIT_TIER_PATTERN.search(tier["name"]))
    ]
    if any(tier["input"] is not None and tier["output"] is not None for tier in tiers) or not cache_tiers:
        baseline = _pick_baseline_tier(tiers)
        if baseline["cache_read"] is None:
            cache_only = [
                tier for tier in tiers
                if tier["cache_read"] is not None and tier["input"] is None and tier["output"] is None
            ]
            if cache_only:
                baseline = {**baseline, "cache_read": _pick_baseline_tier(cache_only)["cache_read"]}
        return baseline

    def _pick(group: list[dict[str, Any]]) -> dict[str, Any] | None:
        return _pick_baseline_tier(group) if group else None

    input_tiers = [
        tier for tier in tiers
        if tier["input"] is not None and not (tier["name"] and _CACHE_HIT_TIER_PATTERN.search(tier["name"]))
    ]
    input_source = _pick(input_tiers) or _pick(cache_tiers) or tiers[0]
    cache_source = _pick(cache_tiers) or input_source
    output_source = _pick([tier for tier in tiers if tier["output"] is not None])
    return {
        "input": input_source["input"],
        "output": output_source["output"] if output_source else None,
        "cache_read": cache_source["cache_read"],
    }
