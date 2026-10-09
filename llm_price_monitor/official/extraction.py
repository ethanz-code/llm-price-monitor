"""官方价提取：把 Tavily 搜索结果交给 AI，并把提取结果规范化成统一条目。

条目同时携带原币价格与按当日汇率换算的 USD/CNY 双口径；
存在仍在有效期的官方促销价时优先采用促销价。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from llm_price_monitor.ai import AIConfig, ai_endpoint, chat_content, json_content

from .normalize import round2

def _ai_json(ai: AIConfig, system: str, user: str, *, timeout: float) -> dict[str, Any]:
    ai_model = ai.pick_model()
    if not ai_model:
        raise ValueError("AI 配置没有可用模型")
    response = httpx.post(
        ai_endpoint(ai.base_url),
        headers={"authorization": f"Bearer {ai.api_key}", "content-type": "application/json"},
        json={
            "model": ai_model,
            "temperature": 0,
            "max_tokens": 8000,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    extracted = json_content(chat_content(response.json()))
    if not isinstance(extracted, dict):
        raise ValueError("AI 返回的不是 JSON 对象")
    return extracted


def _snippets(results: list[dict[str, Any]], limit: int = 1500) -> str:
    return "\n\n".join(
        f"[{index}] 标题: {item.get('title', '')}\nURL: {item.get('url', '')}\n内容: {str(item.get('content', ''))[:limit]}"
        for index, item in enumerate(results, start=1)
    )


def extract_official_prices(
    ai: AIConfig,
    vendor: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """AI 提取官方价：subject 是厂商名，返回其旗下全部模型。"""
    today = time.strftime("%Y-%m-%d")
    scope = "models 数组必须覆盖证据中能确认官方价的全部模型，每个模型一个条目；不能只挑旗舰。"
    system = (
        "你是官方模型价格提取器。目标：找出 厂商 " + vendor + " 旗下 API 所有模型的官方定价。"
        "只接受厂商官方页面或官方文档中明确标注的价格；三方转售站、中转站、聚合表的价格一律不算。"
        "价格按页面标注的币种填写，currency 必须声明是 USD 还是 CNY；单价均为每 1M tokens。"
        f"今天是 {today}：如果存在官方促销价/限时优惠价，同时给出促销价和有效期（promo_ends_at，YYYY-MM-DD）；已过期的促销不算。"
        "如果搜索结果里没有官方定价，found 必须为 false，禁止猜测。"
        '只返回 JSON：{"found": bool, "models": [{"model": "官方模型ID或名称", "currency": "USD|CNY", '
        '"list_input_price": number|null, "list_output_price": number|null, '
        '"promo_input_price": number|null, "promo_output_price": number|null, '
        '"promo_ends_at": string|null, "notes": string}], "source_url": string}。'
        f"{scope}"
    )
    return _ai_json(ai, system, f"搜索结果：\n{_snippets(results)}", timeout=max(ai.timeout, 180))


def build_entry(raw: dict[str, Any], vendor: str, source_url: str) -> dict[str, Any]:
    """把 AI 提取的单模型条目规范化；缺少可用价格时抛 ValueError。

    价格统一保存页面标注币种（currency）的原币值，换算由消费方用文件顶层的
    `usd_cny_rate` 完成，避免三套口径并存。结构：
    {"model", "vendor", "currency", "list": {input, output}, "promo": {input, output, ends_at, valid} | null,
     "effective": {input, output, basis: "promo"|"list"}, "source_url", "notes", "searched_at"}
    """
    currency = str(raw.get("currency") or "USD").strip().upper()
    if currency not in {"USD", "CNY"}:
        currency = "USD"

    def price(value: Any) -> float | None:
        return round2(value) if isinstance(value, (int, float)) else None

    list_prices = {"input": price(raw.get("list_input_price")), "output": price(raw.get("list_output_price"))}
    promo_prices = {"input": price(raw.get("promo_input_price")), "output": price(raw.get("promo_output_price"))}
    promo_ends = raw.get("promo_ends_at")
    has_promo = promo_prices["input"] is not None or promo_prices["output"] is not None
    promo_valid = has_promo and (not promo_ends or str(promo_ends) >= time.strftime("%Y-%m-%d"))
    effective = promo_prices if promo_valid else list_prices
    if effective["input"] is None and effective["output"] is None:
        raise ValueError("AI 返回的官方价缺少可用价格字段")

    return {
        "found": True,
        "model": str(raw.get("model") or "").strip(),
        "vendor": str(raw.get("vendor") or vendor),
        "currency": currency,
        "list": list_prices if list_prices["input"] is not None or list_prices["output"] is not None else None,
        "promo": {
            "input": promo_prices["input"],
            "output": promo_prices["output"],
            "ends_at": promo_ends,
            "valid": promo_valid,
        } if has_promo else None,
        "effective": {
            "input": effective["input"],
            "output": effective["output"],
            "basis": "promo" if promo_valid else "list",
        },
        "source_url": source_url,
        "notes": str(raw.get("notes") or ""),
        "searched_at": time.time(),
    }
