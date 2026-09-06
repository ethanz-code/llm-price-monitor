"""运行编排与报告：一次监控运行、事件分类、持久化、汇总输出。

run_once 是唯一入口：选 UA → 逐站点采集 → 与上次快照比对生成事件 →
写历史/快照 → 输出 MonitorReport 与面向外部的 summary 行。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from llm_price_monitor.adapters import ADAPTERS
from llm_price_monitor.ai import AIDryRun
from llm_price_monitor.config import ChangeKind, MonitorConfig, PriceMonitorError, SiteSpec
from llm_price_monitor.tracker import PriceRecord
from llm_price_monitor.units import round2, tier_unit_per_1m
from llm_price_monitor.useragent import choose_user_agent
from llm_price_monitor.official import discount as official_discount, fx as official_fx
from llm_price_monitor.store import Store


@dataclass(frozen=True)
class PriceEvent:
    site_id: str
    model: str
    kind: ChangeKind
    previous: dict[str, Any] | None
    current: dict[str, Any]
    detected_at: float


@dataclass
class MonitorReport:
    started_at: float
    finished_at: float
    records: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    ai_previews: list[dict[str, Any]] = field(default_factory=list)


def record_dict(site_id: str, record: PriceRecord) -> dict[str, Any]:
    return {"site_id": site_id, **asdict(record)}


def fingerprint(value: dict[str, Any]) -> str:
    comparable = {key: value.get(key) for key in ("model", "input_price", "output_price", "unit", "price_status", "requires_auth")}
    metadata = value.get("metadata") or {}
    comparable["metadata"] = {key: metadata.get(key) for key in (
        "pricing_kind", "model_ratio", "completion_ratio", "group_ratio", "billing_mode",
        "billing_expr", "pricing_rules", "group", "context_min", "context_max",
        "cache_read_price", "cache_create_price", "cache_create_1h_price",
    )}
    return hashlib.sha256(json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def classify(previous: dict[str, Any] | None, current: dict[str, Any]) -> ChangeKind:
    if previous is None:
        return "new"
    if fingerprint(previous) == fingerprint(current):
        return "unchanged"
    if previous.get("price_status") == "unavailable" and current.get("price_status") == "confirmed":
        return "recovered"
    if previous.get("price_status") != current.get("price_status"):
        return "status_changed"
    return "changed"


def run_once(
    config: MonitorConfig,
    *,
    store: Store | None = None,
    client: httpx.Client | None = None,
    user_agent: str | None = None,
    persist: bool = True,
) -> MonitorReport:
    started = time.time()
    selected_user_agent = choose_user_agent(config, user_agent)
    own = client is None
    client = client or httpx.Client(follow_redirects=True)
    latest = store.latest_all() if store is not None else {}
    records: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    ai_previews: list[dict[str, Any]] = []
    try:
        for spec in config.sites:
            if not spec.enabled:
                continue
            adapter = ADAPTERS.get(spec.adapter)
            if adapter is None:
                errors.append({"site_id": spec.id, "error": f"未知适配器: {spec.adapter}"})
                continue
            try:
                collected = adapter.collect(spec, client, config.settings.timeout, selected_user_agent, config.ai)
            except AIDryRun as exc:
                ai_previews.append(exc.preview)
                continue
            except (PriceMonitorError, httpx.HTTPError, ValueError) as exc:
                errors.append({"site_id": spec.id, "error": str(exc)})
                continue
            for record in collected:
                current = record_dict(spec.id, record)
                group = (record.metadata or {}).get("group")
                key = f"{spec.id}:{record.model}:{group or ''}"
                previous = latest.get(key)
                current["fingerprint"] = fingerprint(current)
                current["captured_at"] = record.captured_at
                records.append(current)
                kind = classify(previous, current)
                events.append(asdict(PriceEvent(spec.id, record.model, kind, previous, current, time.time())))
                latest[key] = current
        changed_events = [event for event in events if event["kind"] != "unchanged"]
        if persist and store is not None:
            store.append_history(records)
            store.append_events(changed_events)
            store.replace_latest(latest)
        return MonitorReport(started, time.time(), records, changed_events, errors, ai_previews)
    finally:
        if own:
            client.close()


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
        "group": (row.get("metadata") or {}).get("group"),
        "tiers": tiers,
        "price_status": row.get("price_status"),
        "requires_auth": row.get("requires_auth"),
        "discount": row.get("discount"),
    }


def attach_official_discounts(output: dict[str, Any], official_report: dict[str, Any] | None) -> dict[str, Any]:
    """为输出记录附加相对官方价的折扣（仅输出层，不写入历史/快照，不影响指纹与事件）。"""
    context: dict[str, Any] = {"enabled": False}
    models = official_report.get("models") if isinstance(official_report, dict) else None
    if isinstance(models, dict) and models:
        try:
            with httpx.Client(follow_redirects=True) as client:
                rate, rate_source = official_fx.get_usd_cny_rate(client)
        except ValueError as exc:
            context["reason"] = str(exc)
        else:
            context.update({
                "enabled": True,
                "usd_cny_rate": round2(rate),
                "rate_source": rate_source,
                "generated_at": official_report.get("generated_at_iso"),
            })
            for row in output["records"]:
                entry, _reason = official_discount.build_discount(summary_row(row), models, rate)
                row["discount"] = entry.as_dict() if entry is not None else None
    output["official_prices"] = context
    return output
