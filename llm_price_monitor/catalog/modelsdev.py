"""models.dev 目录同步：拉取 api.json 全量快照，映射为内部官方价目录。

models.dev（https://models.dev）是开源的 AI 模型规格与定价数据库，api.json 由
Cloudflare CDN 分发；价格统一为 USD / 1M tokens。同一模型会在多个渠道条目
（官方 lab、-cn 国内站、vertex 等平台）重复出现，这里只保留白名单内的官方 lab
条目并以 `-cn` 补缺，折扣基准始终是一条：主条目优先，没有主条目的模型才用 `-cn`。

`fetch_catalogs` 同时产出第二份「全量渠道价」目录：不限白名单，models.dev 所有
厂商的带价模型都进表，按 `provider:model` 键保留各渠道自己的条目，仅供展示。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from . import fx, normalize

MODELSDEV_API_URL = "https://models.dev/api.json"

# 官方 lab 条目优先、-cn 渠道兜底；顺序同时决定展示顺序（与前端厂商清单一致）
# 和同模型多渠道冲突时的取舍。厂商显示名与前端 vendor-logos 的 VENDOR_KEY 对齐。
DEFAULT_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("google", "Google"),
    ("xai", "xAI"),
    ("zhipuai", "Zhipu AI"),
    ("deepseek", "DeepSeek"),
    ("moonshotai", "Moonshot AI"),
    ("moonshotai-cn", "Moonshot AI"),
    ("alibaba", "Alibaba Cloud"),
    ("alibaba-cn", "Alibaba Cloud"),
)


def _release_date(model: dict[str, Any]) -> str:
    return str(model.get("release_date") or model.get("last_updated") or "")


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
    rate: float,
    source_url: str,
    logo: str | None = None,
) -> dict[str, Any]:
    """models.dev 模型条目 → 目录条目（价格统一 USD，人民币按快照汇率换算）。"""
    input_price, output_price = model["cost"]["input"], model["cost"]["output"]
    release_date = _release_date(model)
    return {
        "found": True,
        "model": model_id,
        "name": model.get("name"),
        "vendor": vendor,
        "logo": logo,
        "currency": "USD",
        "list": {"input": input_price, "output": output_price},
        "list_cny": {
            "input": normalize.round2(input_price * rate) if input_price is not None else None,
            "output": normalize.round2(output_price * rate) if output_price is not None else None,
        },
        "source_url": source_url,
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
    for provider_id, vendor in DEFAULT_PROVIDERS:
        provider = snapshot.get(provider_id)
        provider_models = provider.get("models") if isinstance(provider, dict) else None
        if not isinstance(provider_models, dict):
            continue
        source_url = provider.get("doc") or "https://models.dev"
        # 厂商内按发布时间倒序（最新在前）；同键冲突时先到先得，即主条目优先于 -cn
        ordered = sorted(provider_models.items(), key=lambda item: _release_date(item[1]), reverse=True)
        for model_id, model in ordered:
            cost = model.get("cost") if isinstance(model, dict) else None
            if not isinstance(cost, dict):
                continue
            if cost.get("input") is None and cost.get("output") is None:
                continue  # 没有官方定价（已下线/未公布），无法作为折扣基准
            key = normalize.model_key(str(model_id))
            if not key or key in official:
                continue
            official[key] = _entry(str(model_id), model, vendor, rate, source_url)

    everything: dict[str, dict[str, Any]] = {}
    for provider_id, vendor in snapshot.items():
        if not isinstance(vendor, dict):
            continue
        provider_models = vendor.get("models")
        if not isinstance(provider_models, dict):
            continue
        label = str(vendor.get("name") or provider_id)
        source_url = vendor.get("doc") or "https://models.dev"
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
            everything[key] = _entry(str(model_id), model, label, rate, source_url, logo)

    meta = {
        "generated_at": now,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "models.dev",
        "source_url": "https://models.dev",
        "usd_cny_rate": normalize.round2(rate),
        "rate_source": rate_source,
    }
    return {**meta, "models": official}, {**meta, "models": everything}


def fetch_catalog(
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """拉取 models.dev api.json 并映射为官方价目录 dict；不落盘，持久化由调用方决定。"""
    official, _ = fetch_catalogs(transport=transport)
    return official
