"""汇总输出层：阶梯平铺、代表档选取、汇总行与官方目录折扣附加（仅输出层）。"""
from __future__ import annotations

from typing import Any

from llm_price_monitor.catalog import discount as catalog_discount, fx as catalog_fx
from llm_price_monitor.units import round2, tier_unit_per_1m


def summary_tiers(row: dict[str, Any]) -> list[dict[str, Any]]:
    """把记录整理成统一的阶梯数组；普通模型是单档，阶梯模型带全部档位。"""
    metadata = row.get("metadata") or {}
    tiers: list[dict[str, Any]] = []
    pricing_rules = metadata.get("pricing_rules")
    groups = pricing_rules.get("groups") if isinstance(pricing_rules, dict) and isinstance(pricing_rules.get("groups"), list) else []
    for group in groups:
        if not isinstance(group, dict):
            continue
        raw_tiers = group.get("tiers") if isinstance(group.get("tiers"), list) else [group]
        for tier in raw_tiers:
            if not isinstance(tier, dict):
                continue
            unit, scale = tier_unit_per_1m(tier.get("unit") or row.get("unit"))
            def scaled(value: Any) -> Any:
                if not isinstance(value, (int, float)):
                    return value
                return round(value * scale, 2) if scale != 1.0 else round(value, 2)
            tiers.append({
                "group": group.get("name") or metadata.get("group"),
                "name": tier.get("name") or tier.get("label"),
                "context_min": tier.get("context_min"),
                "context_max": tier.get("context_max"),
                "input_price": scaled(tier.get("input_price")),
                "output_price": scaled(tier.get("output_price")),
                "cache_read_price": scaled(tier.get("cache_read_price")),
                "cache_create_price": scaled(tier.get("cache_create_price")),
                "unit": unit,
            })
    if not tiers and row.get("input_price") is not None:
        tiers.append({
            "group": metadata.get("group"),
            "name": "default",
            "context_min": None,
            "context_max": None,
            "input_price": round2(row.get("input_price")),
            "output_price": round2(row.get("output_price")),
            "cache_read_price": round2(metadata.get("cache_read_price")),
            "cache_create_price": round2(metadata.get("cache_create_price")),
            "unit": row.get("unit"),
        })
    return tiers


def representative_tier(tiers: list[dict[str, Any]], row: dict[str, Any]) -> dict[str, Any] | None:
    """选出可平铺到顶层价格的档位：必须是同分组、单位与记录一致的上下文阶梯。

    AI 路径可能返回跨分组的"分组型"多档（如 totokens 的各子分组单价），
    单位也可能与记录不一致；这类数据不平铺，避免顶层价格张冠李戴。
    """
    if not tiers:
        return None
    record_group = (row.get("metadata") or {}).get("group")
    for tier in tiers:
        tier_group = tier.get("group")
        if tier_group is not None and record_group is not None and str(tier_group) != str(record_group):
            return None
    row_unit = str(row.get("unit") or "").strip().casefold()
    for tier in tiers:
        if str(tier.get("unit") or "").strip().casefold() == row_unit:
            return tier
    return None


def summary_row(row: dict[str, Any]) -> dict[str, Any]:
    tiers = summary_tiers(row)
    metadata = row.get("metadata") or {}
    input_price = row.get("input_price")
    output_price = row.get("output_price")
    representative = representative_tier(tiers, row)
    if representative and (input_price is None or output_price is None):
        # 同分组的上下文阶梯把 standard（第一档）价格平铺到顶层，方便外部直接取数渲染；
        # 完整阶梯仍在 tiers 里，price_status 保持 rule_only 不变。
        input_price = input_price if input_price is not None else representative.get("input_price")
        output_price = output_price if output_price is not None else representative.get("output_price")
    return {
        "site_id": row.get("site_id"),
        "model": row.get("model"),
        "input_price": round2(input_price),
        "output_price": round2(output_price),
        "unit": row.get("unit"),
        "group": metadata.get("group"),
        "status_reason": metadata.get("error") or metadata.get("calculation_error") or metadata.get("notes") or None,
        "tiers": tiers,
        "price_status": row.get("price_status"),
        "requires_auth": row.get("requires_auth"),
        "discount": row.get("discount"),
    }


def attach_catalog_discounts(output: dict[str, Any], catalog_report: dict[str, Any] | None) -> dict[str, Any]:
    """为输出记录附加相对官方价的折扣（仅输出层，不写入历史/快照，不影响指纹与事件）。"""
    context: dict[str, Any] = {"enabled": False}
    models = catalog_report.get("models") if isinstance(catalog_report, dict) else None
    if isinstance(models, dict) and models:
        rate, rate_source = catalog_fx.resolve_rate(catalog_report.get("usd_cny_rate"))
        if rate is None:
            context["reason"] = rate_source
        else:
            context.update({
                "enabled": True,
                "usd_cny_rate": round2(rate),
                "rate_source": rate_source,
                "generated_at": catalog_report.get("generated_at_iso"),
            })
            for row in output["records"]:
                entry, _reason = catalog_discount.build_discount(summary_row(row), models, rate)
                row["discount"] = entry.as_dict() if entry is not None else None
    output["catalog"] = context
    return output
