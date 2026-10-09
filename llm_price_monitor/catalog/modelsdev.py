"""models.dev 目录同步：拉取 api.json 全量快照，映射为内部官方价目录。

models.dev（https://models.dev）是开源的 AI 模型规格与定价数据库，api.json 由
Cloudflare CDN 分发；价格统一为 USD / 1M tokens。同一模型会在多个渠道条目
（官方 lab、-cn 国内站、vertex 等平台）重复出现，官方价目录只保留白名单内的
官方 lab 条目。models.dev 的国内价格一律不取（-cn 国内站条目是时段缺失的
他方换算价，实测与官方人民币标价偏差可达数倍）：国内厂商目录里只保留国际站
条目作为基准，国内基准价由「厂商定价源」抓取官方定价页后合并覆盖，原国际价
退居 `list_global` 参考价（同行双价）。

`fetch_catalogs` 同时产出第二份「全量渠道价」目录：不限白名单，models.dev 所有
国际渠道的带价模型都进表，按 `provider:model` 键保留各渠道自己的条目，仅供
展示；国内渠道条目（-cn 后缀与国内平台）同样整体剔除。meta 里的 `providers`
是快照厂商清单（含未带价厂商），供「厂商定价源」做覆盖检测。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from . import fx, normalize

MODELSDEV_API_URL = "https://models.dev/api.json"

# 厂商显示顺序（与前端厂商清单一致）由元组顺序决定；region 标注条目口径，
# 白名单只收国际站条目（global）：models.dev 的国内站条目不进目录，国内基准
# 由「厂商定价源」抓官方定价页后合并覆盖，原国际价退居 list_global 参考价
# （同行双价）。deepseek 无国际站条目，配国内定价源前官方目录暂缺该厂商。
DEFAULT_PROVIDERS: tuple[tuple[str, str, str], ...] = (
    ("openai", "OpenAI", "global"),
    ("anthropic", "Anthropic", "global"),
    ("google", "Google", "global"),
    ("xai", "xAI", "global"),
    ("zhipuai", "Zhipu AI", "global"),
    ("moonshotai", "Moonshot AI", "global"),
    ("alibaba", "Alibaba Cloud", "global"),
)


# 国内渠道 id 清单（与 vendor_sources.DOMESTIC_BRANDS 的 cn_provider_ids 人工
# 对齐，models.dev 很多国内平台渠道没有 -cn 后缀）：models.dev 的国内价格不进
# 系统，全量渠道目录靠这份清单整体剔除国内渠道；-cn 后缀兜底快照未来新增的
# 国内站渠道。
DOMESTIC_PROVIDER_IDS: frozenset[str] = frozenset({
    "deepseek", "moonshotai-cn",
    "alibaba-cn", "alibaba-token-plan-cn", "alibaba-coding-plan-cn",
    "minimax-cn", "minimax-cn-coding-plan",
    "volcengine", "volcengine-coding-plan",
    "tencent-tokenhub", "tencent-coding-plan", "tencent-token-plan",
    "stepfun", "stepfun-step-plan", "siliconflow-cn", "sensenova",
    "modelscope", "iflowcn", "xiaomi-token-plan-cn",
})


def _is_domestic_provider(provider_id: str) -> bool:
    """models.dev 渠道 id 是否国内口径：精确清单命中或 -cn 后缀。"""
    return provider_id.endswith("-cn") or provider_id in DOMESTIC_PROVIDER_IDS


def _release_date(model: dict[str, Any]) -> str:
    return str(model.get("release_date") or model.get("last_updated") or "")


def _provider_inventory(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """models.dev 快照 → 厂商清单（含未带价厂商），供厂商定价源做覆盖检测。"""
    inventory: list[dict[str, Any]] = []
    for provider_id, provider in snapshot.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        model_items = models.items() if isinstance(models, dict) else ()
        priced = sum(
            1
            for _, model in model_items
            if isinstance(model, dict)
            and isinstance(model.get("cost"), dict)
            and (model["cost"].get("input") is not None or model["cost"].get("output") is not None)
        )
        inventory.append({
            "id": str(provider_id),
            "name": str(provider.get("name") or provider_id),
            "doc": provider.get("doc"),
            "models_total": len(model_items),
            "models_priced": priced,
        })
    return inventory


def _fetch_snapshot(
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[dict[str, Any], float, str]:
    """拉取 models.dev api.json 快照与 USD→CNY 汇率；快照为空视为异常。"""
    with httpx.Client(follow_redirects=True, timeout=30, transport=transport) as client:
        response = client.get(MODELSDEV_API_URL)
        response.raise_for_status()
        snapshot = response.json()
        rate, rate_source = fx.get_usd_cny_rate(client)
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("models.dev 返回了空目录")
    return snapshot, rate, rate_source


def _entry(
    model_id: str,
    model: dict[str, Any],
    vendor: str,
    region: str,
    rate: float,
    source_url: str,
    logo: str | None = None,
) -> dict[str, Any]:
    """models.dev 模型条目 → 目录条目（价格统一 USD，人民币按快照汇率换算）。

    长上下文分档（list_tiers）与音频价（list_audio）上游仅部分模型带：基础档
    照常入库，分档/音频缺失时对应字段为 null，与 cache 字段同口径。
    """
    cost = model.get("cost") or {}
    input_price, output_price = cost.get("input"), cost.get("output")
    cache_read, cache_write = cost.get("cache_read"), cost.get("cache_write")
    audio_input, audio_output = cost.get("input_audio"), cost.get("output_audio")
    # 长上下文分档（如 >200K 翻倍档）：只留带价的档位；上游仅部分模型带，缺失为 None
    tiers = [
        t for t in cost.get("tiers") or []
        if isinstance(t, dict) and (t.get("input") is not None or t.get("output") is not None)
    ]
    release_date = _release_date(model)
    return {
        "found": True,
        "model": model_id,
        "name": model.get("name"),
        "vendor": vendor,
        "region": region,
        "logo": logo,
        "currency": "USD",
        "list": {"input": input_price, "output": output_price},
        "list_cny": {
            "input": normalize.round2(input_price * rate) if input_price is not None else None,
            "output": normalize.round2(output_price * rate) if output_price is not None else None,
        },
        # 缓存读/写价：models.dev 只覆盖部分模型（读约六成、写约两成），缺失为 null 由调用方决定回落
        "cache": {"read": cache_read, "write": cache_write},
        "cache_cny": {
            "read": normalize.round2(cache_read * rate) if cache_read is not None else None,
            "write": normalize.round2(cache_write * rate) if cache_write is not None else None,
        },
        "source_url": source_url,
        "list_tiers": tiers or None,
        "list_tiers_cny": [
            {
                "input": normalize.round2(t["input"] * rate) if t.get("input") is not None else None,
                "output": normalize.round2(t["output"] * rate) if t.get("output") is not None else None,
                "cache_read": normalize.round2(t["cache_read"] * rate) if t.get("cache_read") is not None else None,
                "tier": t.get("tier"),
            }
            for t in tiers
        ] if tiers else None,
        "list_audio": {"input": audio_input, "output": audio_output}
        if audio_input is not None or audio_output is not None else None,
        "list_audio_cny": {
            "input": normalize.round2(audio_input * rate) if audio_input is not None else None,
            "output": normalize.round2(audio_output * rate) if audio_output is not None else None,
        }
        if audio_input is not None or audio_output is not None else None,
        "description": model.get("description"),
        "family": model.get("family"),
        "modalities": model.get("modalities"),
        "limit": model.get("limit"),
        "release_date": release_date or None,
    }


def fetch_catalogs(
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """一次快照拉取，产出两份目录：官方价基准（白名单）与全量渠道价。

    官方目录里同一模型多渠道冲突时主条目优先；全量目录按 `provider:model` 键
    保留每个渠道自己的条目，跨渠道天然不冲突。
    """
    snapshot, rate, rate_source = _fetch_snapshot(transport=transport)
    now = time.time()

    official: dict[str, dict[str, Any]] = {}
    # 厂商块按元组顺序（决定前端厂商清单顺序）；块内国内站条目优先处理，
    # 先到先得使 -cn 成为折扣基准，同厂商被顶掉的国际站条目退居 list_global 参考价。
    # 跨厂商同名模型仍按厂商块顺序先到先得（如 kimi-k3 取月之暗面而非阿里转售价）。
    blocks: dict[str, list[tuple[str, str]]] = {}
    for provider_id, vendor, region in DEFAULT_PROVIDERS:
        blocks.setdefault(vendor, []).append((provider_id, region))
    for vendor, providers in blocks.items():
        for provider_id, region in sorted(providers, key=lambda item: item[1] != "cn"):
            provider = snapshot.get(provider_id)
            provider_models = provider.get("models") if isinstance(provider, dict) else None
            if not isinstance(provider_models, dict):
                continue
            source_url = provider.get("doc") or "https://models.dev"
            # 厂商内按发布时间倒序（最新在前）
            ordered = sorted(provider_models.items(), key=lambda item: _release_date(item[1]), reverse=True)
            for model_id, model in ordered:
                cost = model.get("cost") if isinstance(model, dict) else None
                if not isinstance(cost, dict):
                    continue
                if cost.get("input") is None and cost.get("output") is None:
                    continue  # 没有官方定价（已下线/未公布），无法作为折扣基准
                input_price, output_price = cost.get("input"), cost.get("output")
                key = normalize.model_key(str(model_id))
                if not key:
                    continue
                existing = official.get(key)
                if existing is not None:
                    # 同厂商国内站条目已占位：国际站条目保留为参考价，同行双价
                    if existing["vendor"] == vendor and existing.get("region") == "cn" and region == "global":
                        existing["list_global"] = {"input": input_price, "output": output_price}
                        existing["list_global_cny"] = {
                            "input": normalize.round2(input_price * rate) if input_price is not None else None,
                            "output": normalize.round2(output_price * rate) if output_price is not None else None,
                        }
                    continue
                official[key] = _entry(str(model_id), model, vendor, region, rate, source_url)

    everything: dict[str, dict[str, Any]] = {}
    for provider_id, provider in snapshot.items():
        if not isinstance(provider, dict):
            continue
        # models.dev 的国内渠道价格不进系统（时段分档缺失、他方汇率换算，
        # 实测与官方人民币标价偏差大）：国内价一律以「厂商定价源」抓取为准
        if _is_domestic_provider(str(provider_id)):
            continue
        provider_models = provider.get("models")
        if not isinstance(provider_models, dict):
            continue
        label = str(provider.get("name") or provider_id)
        source_url = provider.get("doc") or "https://models.dev"
        ordered = sorted(provider_models.items(), key=lambda item: _release_date(item[1]), reverse=True)
        for model_id, model in ordered:
            cost = model.get("cost") if isinstance(model, dict) else None
            if not isinstance(cost, dict):
                continue
            if cost.get("input") is None and cost.get("output") is None:
                continue  # 无定价条目（免费网关、规格未公布等）不进表
            key = f"{normalize.model_key(str(provider_id))}:{normalize.model_key(str(model_id))}"
            if key in everything:
                continue
            logo = f"https://models.dev/logos/{provider_id}.svg"
            everything[key] = _entry(str(model_id), model, label, "global", rate, source_url, logo)

    meta = {
        "generated_at": now,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "models.dev",
        "source_url": "https://models.dev",
        "usd_cny_rate": normalize.round2(rate),
        "rate_source": rate_source,
        # 厂商清单：厂商定价源的覆盖检测用（provider id / 名称 / 文档 / 模型数 / 带价模型数）
        "providers": _provider_inventory(snapshot),
    }
    return {**meta, "models": official}, {**meta, "models": everything}


def fetch_catalog(
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """拉取 models.dev api.json 并映射为官方价目录 dict；不落盘，持久化由调用方决定。"""
    official, _ = fetch_catalogs(transport=transport)
    return official
