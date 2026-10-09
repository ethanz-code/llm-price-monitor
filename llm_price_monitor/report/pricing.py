"""价格行的采集侧变换：占位沿用上次价、规则价回填、分组白名单、合理性作废、占位组跳过与渠道倍率合成。"""
from __future__ import annotations

import time
from typing import Any

from llm_price_monitor import tasklog
from llm_price_monitor.catalog import discount as catalog_discount, fx as catalog_fx
from llm_price_monitor.catalog.normalize import model_key
from llm_price_monitor.store import Store
from llm_price_monitor.store.schema import latest_key
from llm_price_monitor.tracker import PriceRecord

from .summary import representative_tier, summary_tiers


def _has_price(row: dict[str, Any]) -> bool:
    """是否拿到真实价格。0/0 双零只有 confirmed（渠道真免费档）才算有价：
    AI 抽不到价时可能照提示词模板把数值抄成 0，这种占位行若被当成"有价"入库，
    会以 ¥0 假免费价进快照并被首页最低价挑选选中。"""
    input_price, output_price = row.get("input_price"), row.get("output_price")
    if input_price is None and output_price is None:
        return False
    if input_price == 0 and output_price == 0:
        return row.get("price_status") == "confirmed"
    return True


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


def _channel_rate_records(
    store: Store,
    site_id: str,
    official_models: dict[str, Any] | None,
    source_url: str,
) -> list[PriceRecord]:
    """渠道倍率价合成：价格腿零命中、而本站渠道状态快照（status_ref:{site_id}，渠道监测
    接口响应）的渠道带 rate_multiplier 与模型名时，按 sub2api 倍率语义
    「实付 = 官方价 × 渠道倍率」合成价格记录（1for 案：售卖渠道接口只剩订阅制 swe，
    ChatGPT/Claude/Grok 渠道的倍率价目只挂在渠道状态页）。

    判据零配置、天然通用：任何"渠道倍率型"站价格腿空时自动走这里；渠道模型在官方目录
    查无基准价、或渠道无倍率则跳过该渠道；同模型多渠道各按渠道名成组出价。倍率价是
    跨源合成（官方目录 × 状态快照），整批标 candidate 待复核；快照是上一轮状态腿采集
    的结果，首建轮无状态快照时自然不触发，下一轮起生效。"""
    if official_models is None:
        return []
    try:
        ref = store.get_document(f"status_ref:{site_id}") or {}
    except Exception:
        return []
    items = _channel_monitor_items(ref)
    if not items:
        return []
    records: list[PriceRecord] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        model = str(item.get("primary_model") or "").strip()
        rate = item.get("rate_multiplier")
        if not model or not isinstance(rate, (int, float)) or rate <= 0:
            continue
        group = str(item.get("group_display_name") or item.get("name") or "default")
        key = (model, group)
        if key in seen:
            continue
        catalog_entry = official_models.get(model_key(model))
        if not isinstance(catalog_entry, dict) or not catalog_entry.get("found"):
            continue
        list_prices = catalog_entry.get("list") or {}
        catalog_currency = str(catalog_entry.get("currency") or "USD").upper()
        official_input, official_output = list_prices.get("input"), list_prices.get("output")
        if official_input is None and official_output is None:
            continue
        seen.add(key)
        channel_name = str(item.get("name") or "")
        metadata: dict[str, Any] = {
            "adapter": "status_rate",
            "pricing_kind": "channel_rate",
            "group": group,
            "currency": catalog_currency,
            "channel_rate": rate,
            "channel_name": channel_name,
            "formula": "实付 = 官方价 × 渠道倍率（渠道状态快照合成，sub2api 倍率语义）",
            "network_evidence": [],
            "page_evidence": [],
            "notes": f"渠道 {channel_name} 倍率 {rate}×，官方价 {official_input}/{official_output} {catalog_currency}/1M 合成；渠道价目来自状态页。",
        }
        records.append(PriceRecord(
            model,
            official_input * rate if official_input is not None else None,
            official_output * rate if official_output is not None else None,
            f"{catalog_currency}/1M tokens",
            source_url,
            time.time(),
            metadata,
            "candidate",
        ))
    return records


def _channel_monitor_items(ref: Any) -> list[Any]:
    """状态快照文档 → 渠道 items 数组：兼容 {data:{data:{items}}} 原始响应包裹与扁平 {items}。"""
    if not isinstance(ref, dict):
        return []
    data = ref.get("data")
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("items"), list):
            return inner["items"]
        if isinstance(data.get("items"), list):
            return data["items"]
    if isinstance(ref.get("items"), list):
        return ref["items"]
    return []


def _voided_by_sanity(current: dict[str, Any], reason: str) -> dict[str, Any]:
    """作废行统一形状：价格清空、状态转 unavailable，原因同时写 error 与 sanity_suspect。"""
    return {
        **current,
        "input_price": None,
        "output_price": None,
        "price_status": "unavailable",
        "metadata": {**(current.get("metadata") or {}), "sanity_suspect": reason, "error": reason},
    }


def _drop_phony_group_records(
    records: list[PriceRecord],
    site_id: str,
    sanity_state: dict[str, Any],
) -> list[PriceRecord]:
    """占位组跳过：分组内全部有价模型共享同一价目、且组内已有模型被 sanity 两轮
    确认作废时，整组视为站方占位目录（2000lab deepseek稳定高速案：5 个模型同一
    0.03/0.12 价目，v4-pro 对厂商价偏离 300 倍被作废实锤，同价目型号一并跳过）。

    双信号缺一不可：同价目是占位的形态证据（真实渠道不同模型成本不同，整组一个
    价几乎必是拍脑袋填的目录），作废实锤给出价格为假的决定性证据。真实的整组
    同价（各站 grok 分组：grok-4.6/4.7 官方同价打包卖、价格可比对在可信区间）因
    无作废实锤不受影响；模型各自有价的组（01tree 单位错，逐模型价格不同）不触发，
    仍走 sanity 逐模型作废。站方改价后自动恢复采集。被跳过的组本轮视为"没取到
    数据"，快照旧价走既有的连续缺失摘除路径自然消退。"""
    priced_by_group: dict[str, list[PriceRecord]] = {}
    for record in records:
        if record.input_price is None and record.output_price is None:
            continue
        group = str((record.metadata or {}).get("group") or "default")
        priced_by_group.setdefault(group, []).append(record)

    phony_groups = {
        group
        for group, members in priced_by_group.items()
        # 单模型组"全部同价"恒真，无从构成占位形态证据——不判，否则该组作废后站方改价也无法恢复
        if len(members) >= 2
        and len({(member.input_price, member.output_price) for member in members}) == 1
        and any((sanity_state.get(latest_key(site_id, member.model, group)) or {}).get("voided") for member in members)
    }
    if not phony_groups:
        return records
    tasklog.emit(
        f"[{site_id}] 分组 {'、'.join(sorted(phony_groups))} 全部模型同一价目且有作废实锤，按站方占位目录跳过",
        "info",
    )
    return [record for record in records if str((record.metadata or {}).get("group") or "default") not in phony_groups]


def _apply_price_sanity(
    current: dict[str, Any],
    official_models: dict[str, Any] | None,
    rate: float | None,
    previous: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    key: str = "",
) -> dict[str, Any]:
    """站点价对厂商价离谱时的两轮确认：首轮异常只挂 sanity_suspect 标记、价格照常入库，
    下一轮同因复现才作废——价格清空、状态转 unavailable、原因写 metadata.error。

    官方价目录自身也可能带错（如 AI 提取厂商定价页列错位），单轮偏差就把价作废会
    误杀真数据；连续两轮都越界才认定是站点侧问题。作废后走既有的"本次没拿到价"
    路径（上次有价则沿用并带出原因），保证错误数值永远进不了快照与历史；
    校验判据缺失（无目录/无汇率）时不拦，不构成兜底。

    作废行因无价被落库层清理，previous 随之消失，两轮确认的跨轮进度记在 state
    （key → {reason, voided}，由调用方按采集轮持久化到 sanity_state 文档）：
    已作废的同因复现静默维持作废，不再每轮重走两轮确认刷任务日志与异常卡片；
    站方改回正常价即清除状态，再次越界重新走两轮确认。只带旧版
    metadata.sanity_suspect 标记的存量行同因视为已复核，直接作废。
    """
    if official_models is None or rate is None or not _has_price(current):
        return current
    reason = catalog_discount.sanity_violation(
        {**current, "tiers": summary_tiers(current)}, official_models, rate
    )
    if reason is None:
        if state is not None and key:
            state.pop(key, None)
        return current
    site_id, model = current.get("site_id"), current.get("model")
    prior = (state or {}).get(key) or {}
    legacy_suspect = str(((previous or {}).get("metadata") or {}).get("sanity_suspect") or "")
    if str(prior.get("reason") or "") != reason and legacy_suspect != reason:
        tasklog.emit(f"[{site_id}] 价格异常待复核（下轮复现才作废）：{model} {reason}", "warn")
        if state is not None and key:
            state[key] = {"reason": reason}
        return {
            **current,
            "metadata": {**(current.get("metadata") or {}), "sanity_suspect": reason},
        }
    if not prior.get("voided"):
        tasklog.emit(f"[{site_id}] 价格异常作废：{model} {reason}", "error")
        if state is not None and key:
            state[key] = {"reason": reason, "voided": True}
    return _voided_by_sanity(current, reason)
