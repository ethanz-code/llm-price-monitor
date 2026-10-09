"""价格数值与单位的归一化。

价格监控三处（monitor / tracker / usage_cost）共用的数值转换和单位换算，
全部是无依赖的纯函数，方便将来随价格监控整体分割出去。
"""
from __future__ import annotations

import json
import re
from typing import Any


def number_or_none(value: Any) -> float | None:
    """把任意 JSON 值安全转成 float；布尔和无法转换的返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def round2(value: Any) -> Any:
    return round(value, 2) if isinstance(value, (int, float)) else value


def tier_unit_per_1m(unit: Any) -> tuple[Any, float]:
    """把 `X/token` 之类的单价单位统一换算成 `X/1M tokens`，返回 (单位, 放大倍数)。"""
    if isinstance(unit, str):
        match = re.fullmatch(r"([A-Za-z]{3})\s*/\s*token", unit.strip(), re.IGNORECASE)
        if match:
            return f"{match.group(1).upper()}/1M tokens", 1_000_000.0
    return unit, 1.0


def merge_unique(values: list[Any]) -> list[Any]:
    """按 JSON 值去重，保留站点返回的原始顺序。"""
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def has_pricing_tiers(value: Any) -> bool:
    """pricing_rules 必须真实包含 tiers 才算规则计价，AI 回填的空壳骨架不算。"""
    if not isinstance(value, dict):
        return False
    candidates = value.get("groups") if isinstance(value.get("groups"), list) else [value]
    for group in candidates:
        if not isinstance(group, dict):
            continue
        if isinstance(group.get("tiers"), list) and group["tiers"]:
            return True
        if isinstance(group.get("unit_prices"), dict):
            return True
    return False


def merge_model_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并同一模型的多组/多梯度结果，避免只保留第一个 tier。"""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        key = str(item.get("model", "")).strip().casefold()
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(item)
            order.append(key)
            continue
        target = merged[key]
        for field_name in ("network_evidence", "page_evidence", "aliases"):
            left = target.get(field_name) if isinstance(target.get(field_name), list) else []
            right = item.get(field_name) if isinstance(item.get(field_name), list) else []
            target[field_name] = merge_unique([*left, *right])
        for field_name in ("pricing_rules", "groups", "tiers"):
            left = target.get(field_name)
            right = item.get(field_name)
            if left is None and right is not None:
                target[field_name] = right
            elif isinstance(left, list) and isinstance(right, list):
                target[field_name] = merge_unique([*left, *right])
            elif isinstance(left, dict) and isinstance(right, dict):
                combined = {**left, **right}
                if isinstance(left.get("groups"), list) and isinstance(right.get("groups"), list):
                    combined["groups"] = merge_unique([*left["groups"], *right["groups"]])
                if isinstance(left.get("tiers"), list) and isinstance(right.get("tiers"), list):
                    combined["tiers"] = merge_unique([*left["tiers"], *right["tiers"]])
                target[field_name] = combined
        for field_name in ("input_price", "output_price", "unit", "currency", "observed_model", "notes"):
            if target.get(field_name) in (None, "", []):
                target[field_name] = item.get(field_name)
        if str(item.get("status", "")) == "confirmed":
            target["status"] = "confirmed"
    return [merged[key] for key in order]
