"""价格行的采集侧变换：占位沿用上次价、规则价回填、分组白名单与合理性作废。"""
from __future__ import annotations

from typing import Any

from llm_price_monitor import tasklog
from llm_price_monitor.catalog import discount as catalog_discount, fx as catalog_fx
from llm_price_monitor.store import Store
from llm_price_monitor.tracker import PriceRecord

from .summary import representative_tier, summary_tiers


def _has_price(row: dict[str, Any]) -> bool:
    return row.get("input_price") is not None or row.get("output_price") is not None


def _carry_last_price(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    """本次只产出无价占位（需认证/无数据）而上次快照有价时，沿用上次的价格字段：
    定价页继续展示上次已知价，不因一次采集失败把价格打成 "-"；状态按占位本来的性质标注
    （接口 401/403 才标"需认证"），last_price_at 记录上次实际取到价的时间。
    metadata 以本次为准叠加：保留上次的 pricing_rules（阶梯价展示依赖），带上本次的失败原因 notes。
    """
    # 只有占位本身是"需认证"（接口 401/403）才沿用需认证；AI/解析没映射出来的占位
    # 如实保持 unavailable，不得把"没数据"误标成"需认证"
    current_auth = bool(current.get("requires_auth")) or (current.get("metadata") or {}).get("pricing_kind") == "auth_required"
    return {
        **previous,
        "price_status": current["price_status"],
        "requires_auth": current_auth,
        "captured_at": current["captured_at"],
        "last_price_at": previous.get("last_price_at") or previous.get("captured_at"),
        "metadata": {**(previous.get("metadata") or {}), **(current.get("metadata") or {})},
    }


def _backfill_rule_price(row: dict[str, Any]) -> dict[str, Any]:
    """rule_only 行价格字段为空但 pricing_rules 有推断价时，把代表档价格回填到顶层：
    推断价得以进快照与定价页（price_status 保持 rule_only，前端标注"规则价"）；
    真正无数据的行（pricing_rules 也为空）保持无价，不进快照。"""
    if row.get("price_status") != "rule_only" or _has_price(row):
        return row
    representative = representative_tier(summary_tiers(row), row)
    if not representative:
        return row
    return {
        **row,
        "input_price": row.get("input_price") if row.get("input_price") is not None else representative.get("input_price"),
        "output_price": row.get("output_price") if row.get("output_price") is not None else representative.get("output_price"),
    }


def _filter_price_groups(records: list[PriceRecord], groups: list[str]) -> list[PriceRecord]:
    """按站点分组白名单过滤采集到的价格记录：分组名忽略大小写，metadata 缺分组视为 default；
    一个都没匹配上时保留原记录，避免白名单写错把整站价格清空（与状态侧口径一致）。"""
    targets = {group.strip().casefold() for group in groups if group.strip()}
    if not targets:
        return records

    def group_of(record: PriceRecord) -> str:
        return str((record.metadata or {}).get("group") or "default").strip().casefold()

    matched = [record for record in records if group_of(record) in targets]
    return matched if matched else records


def _sanity_context(store: Store | None) -> tuple[dict[str, Any] | None, float | None]:
    """价格合理性校验的判据：官方目录模型表与快照汇率；目录缺失时返回 (None, None) 表示无法校验。"""
    catalog = store.get_document("catalog") if store is not None else None
    if not isinstance(catalog, dict):
        return None, None
    models = catalog.get("models")
    if not isinstance(models, dict) or not models:
        return None, None
    return models, catalog_fx.resolve_rate(catalog.get("usd_cny_rate"))[0]


def _apply_price_sanity(
    current: dict[str, Any], official_models: dict[str, Any] | None, rate: float | None
) -> dict[str, Any]:
    """站点价对厂商价离谱时作废本次观测：价格清空、状态转 unavailable、原因写入 metadata.error。

    作废后走既有的"本次没拿到价"路径（上次有价则沿用并带出原因），保证错误数值
    永远进不了快照与历史；校验判据缺失（无目录/无汇率）时不拦，不构成兜底。
    """
    if official_models is None or rate is None or not _has_price(current):
        return current
    reason = catalog_discount.sanity_violation(
        {**current, "tiers": summary_tiers(current)}, official_models, rate
    )
    if reason is None:
        return current
    tasklog.emit(f"[{current.get('site_id')}] 价格异常作废：{current.get('model')} {reason}", "error")
    return {
        **current,
        "input_price": None,
        "output_price": None,
        "price_status": "unavailable",
        "metadata": {**(current.get("metadata") or {}), "error": reason},
    }
