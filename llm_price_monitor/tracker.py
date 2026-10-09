"""价格解析与采集库函数：价格页/New API 解析、站点类型识别、价格记录构造。

被 config / ai / adapters / report 复用；命令行入口已移除，采集统一走 /api/collect。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal

import httpx

from llm_price_monitor.units import number_or_none as _number


DEFAULT_NEWAPI_RATIO_BASE_PRICE = 2.0
_TIER_CALL_PATTERN = re.compile(
    r"tier\(\s*['\"](?P<name>[^'\"]+)['\"]\s*,\s*(?P<formula>.*?)\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_TIER_CONDITION_PATTERN = re.compile(
    r"^\s*len\s*(?P<operator><=|<|>=|>)\s*(?P<limit>\d+(?:\.\d+)?)\s*\?",
    re.IGNORECASE,
)


@dataclass
class PriceRecord:
    model: str
    input_price: float | None
    output_price: float | None
    unit: str
    source_url: str
    captured_at: float
    metadata: dict[str, Any] | None = None
    price_status: Literal["confirmed", "candidate", "rule_only", "unavailable"] = "confirmed"
    requires_auth: bool = False


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def detect_site_kind(page_url: str, *, timeout: float = 20.0, client: httpx.Client | None = None) -> dict[str, Any]:
    """仅识别公开技术特征；不会假定任意站点都能公开价格。"""
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    parsed = httpx.URL(page_url)
    base = f"{parsed.scheme}://{parsed.host}" + (f":{parsed.port}" if parsed.port else "")
    try:
        page = client.get(page_url)
        html = page.text.lower() if page.is_success else ""
        status = client.get(f"{base}/api/status")
        if status.is_success:
            payload = status.json()
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict) and "HeaderNavModules" in payload["data"]:
                pricing = client.get(f"{base}/api/pricing")
                return {"kind": "newapi", "price_endpoint": f"{base}/api/pricing", "public_price_candidate": pricing.is_success, "requires_auth": pricing.status_code in (401, 403)}
        pricing = client.get(f"{base}/api/pricing")
        if pricing.is_success:
            try:
                payload = pricing.json()
            except ValueError:
                payload = None
            if looks_like_newapi_pricing(payload):
                return {"kind": "newapi", "price_endpoint": f"{base}/api/pricing", "public_price_candidate": True, "requires_auth": False, "status_endpoint_blocked": status.status_code in (401, 403)}
        if "_next/static" in html or "__next_data__" in html:
            return {"kind": "nextjs", "price_endpoint": None, "public_price_candidate": False}
        if "vendor-vue" in html or "__vite__" in html:
            return {"kind": "vue-vite", "price_endpoint": None, "public_price_candidate": False}
        if "lib-react" in html or "react" in html:
            return {"kind": "react", "price_endpoint": None, "public_price_candidate": False}
        return {"kind": "unknown", "price_endpoint": None, "public_price_candidate": False, "requires_auth": page.status_code in (401, 403)}
    finally:
        if own:
            client.close()


def extract_price(text: str, model: str, source_url: str) -> PriceRecord:
    """从页面文本中提取模型附近的输入/输出价格；复杂页面可先提供纯 JSON。"""
    normalized = " ".join(text.split())
    position = normalized.lower().find(model.lower())
    if position < 0:
        raise ValueError(f"页面中未找到模型 {model}")
    window = normalized[position + len(model): position + len(model) + 1200]
    numbers = [float(value) for value in re.findall(r"(?:\$|¥|￥|人民币)\s*(\d+(?:\.\d+)?)", window)]
    prices = numbers[:2]
    input_price = prices[0] if prices else None
    output_price = prices[1] if len(prices) > 1 else None
    if input_price is None:
        raise ValueError(f"未在页面中找到模型 {model} 附近的价格")
    unit_match = re.search(r"(\$|¥|￥|人民币)?\s*(?:/|每)\s*(\d+(?:\.\d+)?\s*[万千百百万KkMm]?\s*(?:tokens?|Token|令牌))", window, re.I)
    unit = unit_match.group(0).strip() if unit_match else "页面未说明单位"
    status: Literal["confirmed", "candidate"] = "confirmed" if unit_match and output_price is not None else "candidate"
    return PriceRecord(model, input_price, output_price, unit, source_url, time.time(), price_status=status)


def _price_fields(value: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
    """只读取有明确语义的价格字段，避免把余额、限额等数字误当成价格。"""
    official = isinstance(value.get("official_pricing"), dict)
    rules = value.get("pricing_rules")
    tier_prices = None
    if isinstance(rules, dict) and isinstance(rules.get("tiers"), list) and rules["tiers"]:
        first_tier = rules["tiers"][0]
        if isinstance(first_tier, dict) and isinstance(first_tier.get("unit_prices"), dict):
            tier_prices = first_tier["unit_prices"]
    nested = value.get("pricing") or value.get("official_pricing") or tier_prices
    source = nested if isinstance(nested, dict) else value
    input_value = next((source.get(key) for key in ("input_price", "input", "prompt_price", "prompt", "input_cost") if key in source), None)
    output_value = next((source.get(key) for key in ("output_price", "output", "completion_price", "completion", "output_cost") if key in source), None)
    unit = source.get("unit") or source.get("price_unit") or value.get("unit") or value.get("price_unit")
    if unit is None and official:
        unit = "USD/1M tokens（official_pricing）"
    if unit is None and tier_prices is not None:
        unit = "USD/1M tokens（pricing_rules）"
    return _number(input_value), _number(output_value), str(unit) if unit is not None else None


def looks_like_newapi_pricing(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return False
    return any(
        isinstance(item, dict)
        and "model_name" in item
        and any(key in item for key in ("model_ratio", "official_pricing", "billing_mode", "input_price", "pricing"))
        for item in payload["data"]
    )

def looks_like_platform_pricing(payload: Any) -> bool:
    """平台分桶定价结构（totokens 等新版接口）：data[] 按 platforms 分桶，模型带 pricing.final_prices 分组单价。"""
    if isinstance(payload, dict):
        payload = payload.get("data")
    if not isinstance(payload, list):
        return False
    for item in payload:
        platforms = item.get("platforms") if isinstance(item, dict) else None
        for platform in platforms if isinstance(platforms, list) else ():
            models = platform.get("supported_models") if isinstance(platform, dict) else None
            if isinstance(models, list) and any(
                isinstance(model, dict)
                and isinstance(model.get("pricing"), dict)
                and isinstance(model["pricing"].get("final_prices"), list)
                for model in models
            ):
                return True
    return False


class GroupRatioUnavailableError(ValueError):
    """站点未公开该分组的倍率，无法确定性计价。"""


def _fallback_group_ratios(payload: dict[str, Any]) -> dict[str, float]:
    """group_ratio 未覆盖的分组，从 group_definitions / first_topup_offer 取倍率。"""
    fallback: dict[str, float] = {}
    definitions = payload.get("group_definitions")
    if isinstance(definitions, list):
        for definition in definitions:
            if not isinstance(definition, dict):
                continue
            ratio = _number(definition.get("ratio"))
            if ratio is None:
                continue
            for key in (definition.get("id"), definition.get("label")):
                if isinstance(key, str) and key:
                    fallback.setdefault(key, ratio)
    offer = payload.get("first_topup_offer")
    if isinstance(offer, dict):
        ratio = _number(offer.get("ratio"))
        group = offer.get("group")
        if ratio is not None and isinstance(group, str) and group:
            fallback.setdefault(group, ratio)
    return fallback


def _ratio_group(
    item: dict[str, Any],
    ratios: dict[str, Any],
    group: str | None,
    fallback_ratios: dict[str, float] | None = None,
) -> tuple[str | None, float]:
    enabled_groups = [name for name in item.get("enable_groups", []) if isinstance(name, str) and name]
    selected = group
    if selected is None and enabled_groups:
        if len(enabled_groups) == 1:
            selected = enabled_groups[0]
        elif "default" in enabled_groups:
            selected = "default"
        else:
            raise ValueError(f"模型 {item.get('model_name')} 有多个可用分组，必须显式指定 group")
    if selected is None:
        # 未标注分组一律归一为 default：事件键稳定，不随 enable_groups 抖动翻转出重复"新增"
        selected = "default"
    if enabled_groups and selected not in enabled_groups:
        raise ValueError(f"模型 {item.get('model_name')} 不支持分组 {selected}")
    multiplier = _number(ratios.get(selected))
    if multiplier is None and fallback_ratios:
        multiplier = _number(fallback_ratios.get(selected))
    if multiplier is None:
        if selected == "default" or group is None:
            # 未标注分组的模型缺倍率时维持旧 1.0 基准，不因分组校验中断整站采集
            multiplier = 1.0
        else:
            raise GroupRatioUnavailableError(f"站点未公开分组 {selected} 的倍率")
    return selected, multiplier


def _tier_coefficient(formula: str, variable: str) -> float | None:
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    name = re.escape(variable)
    match = re.search(rf"(?<![\w.]){name}\s*\*\s*(?P<value>{number})(?![\w.])", formula, re.IGNORECASE)
    if match is None:
        match = re.search(rf"(?<![\w.])(?P<value>{number})\s*\*\s*{name}(?![\w.])", formula, re.IGNORECASE)
    return _number(match.group("value")) if match else None


def _tiered_rules(expression: str, multiplier: float, unit: str) -> list[dict[str, Any]] | None:
    calls = list(_TIER_CALL_PATTERN.finditer(expression))
    if not calls:
        return None
    condition = _TIER_CONDITION_PATTERN.search(expression)
    if condition is not None and len(calls) != 2:
        return None
    bounds: list[tuple[int | None, int | None]] = [(None, None)] * len(calls)
    if condition is not None:
        limit = int(float(condition.group("limit")))
        operator = condition.group("operator")
        if operator == "<=":
            bounds = [(None, limit), (limit + 1, None)]
        elif operator == "<":
            bounds = [(None, limit - 1), (limit, None)]
        elif operator == ">=":
            bounds = [(limit, None), (None, limit - 1)]
        else:
            bounds = [(limit + 1, None), (None, limit)]
    rules: list[dict[str, Any]] = []
    for call, (context_min, context_max) in zip(calls, bounds, strict=True):
        formula = call.group("formula")
        input_price = _tier_coefficient(formula, "p")
        output_price = _tier_coefficient(formula, "c")
        if input_price is None or output_price is None:
            return None
        cache_read_price = _tier_coefficient(formula, "cr")
        cache_create_price = _tier_coefficient(formula, "cc")
        rule = {
            "name": call.group("name"),
            "context_min": context_min,
            "context_max": context_max,
            "input_price": input_price * multiplier,
            "output_price": output_price * multiplier,
            "cache_read_price": cache_read_price * multiplier if cache_read_price is not None else None,
            "unit": unit,
        }
        if cache_create_price is not None:
            rule["cache_create_price"] = cache_create_price * multiplier
        rules.append(rule)
    return rules




def _tiered_pricing_rules(value: dict[str, Any], multiplier: float, unit: str) -> list[dict[str, Any]] | None:
    rules = value.get("tiers") if isinstance(value.get("tiers"), list) else None
    if not rules:
        return None
    result: list[dict[str, Any]] = []
    lower: int | None = None
    for index, tier in enumerate(rules):
        if not isinstance(tier, dict) or not isinstance(tier.get("unit_prices"), dict):
            return None
        upper: int | None = None
        conditions = tier.get("conditions")
        if isinstance(conditions, list):
            for condition in conditions:
                if not isinstance(condition, dict):
                    continue
                if condition.get("operator") in {"<=", "<"} and isinstance(condition.get("value"), (int, float)):
                    limit = int(float(condition["value"]))
                    upper = limit if condition["operator"] == "<=" else limit - 1
                    break
        prices = tier["unit_prices"]
        input_price = _number(prices.get("input"))
        output_price = _number(prices.get("output"))
        if input_price is None or output_price is None:
            return None
        result.append({
            "name": str(tier.get("label") or tier.get("name") or f"tier-{index + 1}"),
            "context_min": lower,
            "context_max": upper,
            "input_price": input_price * multiplier,
            "output_price": output_price * multiplier,
            "cache_read_price": _number(prices.get("cache_read")) * multiplier if _number(prices.get("cache_read")) is not None else None,
            "cache_create_price": _number(prices.get("cache_write")) * multiplier if _number(prices.get("cache_write")) is not None else None,
            "unit": unit,
        })
        lower = upper + 1 if upper is not None else None
    return result

def newapi_price_record(
    payload: Any,
    model: str,
    source_url: str,
    *,
    group: str | None = None,
    aliases: tuple[str, ...] = (),
    ratio_base_price: float = DEFAULT_NEWAPI_RATIO_BASE_PRICE,
    currency: str = "CNY",
) -> PriceRecord:
    """从 one-api/new-api 响应确定性计算站点单价，不读取页面 DOM。"""
    configured_currency = currency.strip().upper()
    if configured_currency not in {"CNY", "USD"}:
        raise ValueError("currency 必须是 CNY 或 USD")
    configured_unit = f"{configured_currency}/1M tokens"
    if not looks_like_newapi_pricing(payload):
        raise ValueError("不是可识别的 New API 价格响应")
    assert isinstance(payload, dict)
    names = {name.casefold() for name in (model, *aliases)}
    item = next(
        (
            value for value in payload["data"]
            if isinstance(value, dict) and str(value.get("model_name", "")).casefold() in names
        ),
        None,
    )
    if item is None:
        raise ValueError(f"New API 价格表中未找到模型 {model}")
    ratios = payload.get("group_ratio", {})
    if not isinstance(ratios, dict):
        raise ValueError("New API 响应的 group_ratio 必须是对象")
    fallback_ratios = _fallback_group_ratios(payload)
    selected_group, multiplier = _ratio_group(item, ratios, group, fallback_ratios)
    denomination_v2 = _number(payload.get("billing_denomination_version")) == 2
    cny_rate = _number(payload.get("pricing_cny_rate")) if denomination_v2 else None
    currency_multiplier = 1.0
    if denomination_v2 and configured_currency == "CNY":
        if cny_rate is None:
            # v2 基准价是 USD；站点没给汇率时不伪造 CNY 单价，改报 USD。
            configured_currency = "USD"
            configured_unit = "USD/1M tokens"
        else:
            currency_multiplier = cny_rate
    request_rules = item.get("request_rules")
    if not (isinstance(request_rules, list) and request_rules):
        pricing_rules_raw = item.get("pricing_rules")
        request_rules = pricing_rules_raw.get("request_rules") if isinstance(pricing_rules_raw, dict) else None
    metadata: dict[str, Any] = {
        "newapi": True,
        "model_ratio": item.get("model_ratio"),
        "completion_ratio": item.get("completion_ratio"),
        "cache_ratio": item.get("cache_ratio"),
        "create_cache_ratio": item.get("create_cache_ratio"),
        "billing_mode": item.get("billing_mode"),
        "billing_expr": item.get("billing_expr"),
        "group": selected_group,
        "group_ratio": multiplier,
        "enable_groups": item.get("enable_groups", []),
        "pricing_version": payload.get("pricing_version"),
        "ratio_base_price": ratio_base_price,
        "currency": configured_currency,
        "pricing_cny_rate": cny_rate,
        "request_rules": request_rules if isinstance(request_rules, list) and request_rules else None,
    }
    if item.get("billing_mode") == "tiered_expr":
        expression = item.get("billing_expr")
        rules = _tiered_rules(expression, multiplier * currency_multiplier, configured_unit) if isinstance(expression, str) else None
        if rules is None:
            pricing_rules = item.get("pricing_rules")
            if isinstance(pricing_rules, dict):
                rules = _tiered_pricing_rules(pricing_rules, multiplier * currency_multiplier, configured_unit)
        if rules is None:
            metadata.update({"pricing_kind": "tiered_expr", "pricing_rules": None, "calculation_error": "无法解析 billing_expr 或 pricing_rules.tiers"})
            return PriceRecord(model, None, None, configured_unit, source_url, time.time(), metadata, "unavailable")
        metadata.update({
            "pricing_kind": "tiered_expr",
            "pricing_rules": {"groups": [{"name": selected_group or "default", "tiers": rules}]},
        })
        return PriceRecord(model, None, None, configured_unit, source_url, time.time(), metadata, "rule_only")
    model_ratio = _number(item.get("model_ratio"))
    completion_ratio = _number(item.get("completion_ratio"))
    has_explicit_price = any(
        key in item for key in ("input_price", "input", "prompt_price", "prompt", "input_cost", "pricing")
    )
    # A complete ratio pair is the authoritative New API calculation. Some
    # deployments also expose an incomplete input_price reference alongside it.
    prefer_ratio = model_ratio is not None and (
        completion_ratio is not None or not has_explicit_price
    )
    if has_explicit_price and not prefer_ratio:
        input_price, output_price, explicit_unit = _price_fields(item)
        if input_price is not None:
            metadata["pricing_kind"] = "explicit_price"
            for field_name in ("cache_read_price", "cache_create_price", "cache_create_1h_price"):
                value = _number(item.get(field_name))
                if value is not None:
                    metadata[field_name] = value * multiplier * currency_multiplier
            return PriceRecord(model, input_price * multiplier * currency_multiplier, output_price * multiplier * currency_multiplier if output_price is not None else None, explicit_unit or configured_unit, source_url, time.time(), metadata, "confirmed" if output_price is not None else "candidate")
    if model_ratio is not None:
        cache_ratio = _number(item.get("cache_ratio"))
        create_cache_ratio = _number(item.get("create_cache_ratio"))
        input_price = model_ratio * ratio_base_price * multiplier * currency_multiplier
        output_price = input_price * (completion_ratio if completion_ratio is not None else 1.0)
        metadata.update({
            "pricing_kind": "ratio_multiplier",
            "cache_read_price": input_price * cache_ratio if cache_ratio is not None else None,
            "cache_create_price": input_price * create_cache_ratio if create_cache_ratio is not None else None,
        })
        return PriceRecord(model, input_price, output_price, configured_unit, source_url, time.time(), metadata, "confirmed")
    metadata["pricing_kind"] = "unavailable"
    return PriceRecord(model, None, None, configured_unit, source_url, time.time(), metadata, "unavailable")


def _record_from_json(payload: Any, model: str, source_url: str, *, metadata: dict[str, Any] | None = None) -> PriceRecord:
    item = _find_model_record(payload, model)
    if item is None:
        raise ValueError(f"JSON 中未找到模型 {model}")
    input_price, output_price, unit = _price_fields(item)
    if input_price is None:
        raise ValueError(f"模型 {model} 没有明确的输入价格字段")
    status: Literal["confirmed", "candidate"] = "confirmed" if output_price is not None and unit else "candidate"
    details = dict(metadata or {})
    details["matched_fields"] = [key for key in ("input_price", "output_price", "unit") if key in item]
    return PriceRecord(model, input_price, output_price, unit or "JSON未说明单位", source_url, time.time(), details or None, status)


def fetch_price(url: str, model: str, *, timeout: float = 20.0, client: httpx.Client | None = None) -> PriceRecord:
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True, headers={"user-agent": "Proxy-SmartAven-price-tracker/1.0"})
    try:
        response = client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            payload: Any = response.json()
            return _record_from_json(payload, model, url)
        else:
            parser = _TextParser()
            parser.feed(response.text)
            text = " ".join(parser.parts)
        return extract_price(text, model, url)
    finally:
        if own:
            client.close()


def fetch_newapi_price(base_url: str, model: str, *, group: str | None = None, timeout: float = 20.0, client: httpx.Client | None = None) -> PriceRecord:
    """读取 New API /api/pricing，并区分真实单价与倍率规则。"""
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    base = base_url.rstrip("/")
    if base.endswith("/api/pricing"):
        base = base[:-len("/api/pricing")]
    elif base.endswith("/pricing"):
        base = base[:-len("/pricing")]
    try:
        status = client.get(f"{base}/api/status")
        status_data = status.json() if status.is_success else None
        status_is_newapi = isinstance(status_data, dict) and isinstance(status_data.get("data"), dict) and "HeaderNavModules" in status_data["data"]
        response = client.get(f"{base}/api/pricing")
        if response.status_code in (401, 403):
            if status_is_newapi:
                return PriceRecord(
                    model,
                    None,
                    None,
                    "USD/1M tokens",
                    f"{base}/api/pricing",
                    time.time(),
                    {"newapi": True, "detected_endpoint": "/api/pricing", "error": "New API 价格接口需要认证"},
                    "unavailable",
                    True,
                )
            raise ValueError("New API 价格接口需要认证")
        response.raise_for_status()
        payload = response.json()
        if not status_is_newapi and not looks_like_newapi_pricing(payload):
            raise ValueError("不是可识别的 New API 价格响应")
        return newapi_price_record(payload, model, f"{base}/api/pricing", group=group)
    finally:
        if own:
            client.close()


def _find_model_record(value: Any, model: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        direct = value.get(model)
        if isinstance(direct, dict):
            return direct
        if str(value.get("model_name", value.get("name", ""))).lower() == model.lower():
            return value
        for child in value.values():
            found = _find_model_record(child, model)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_model_record(child, model)
            if found:
                return found
    return None
