"""折扣计算：站点价格 vs 官方价的对比、折扣率与跨站点汇总。

计算为纯函数，输入输出都是 dict/list；文件 IO 由 CLI 层完成。
站点价按记录 unit 折算 CNY，官方价按条目 currency 折算 CNY 后相除，
汇率在分子分母同时出现，因此折扣率与汇率口径无关。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .normalize import model_key, round2


@dataclass(frozen=True)
class DiscountEntry:
    """一条 (站点, 模型, 分组) 的对比结果。"""

    site_id: str
    model: str
    group: Any
    input_discount: Any
    output_discount: Any
    official_input_cny: Any
    official_output_cny: Any
    basis: str
    source_url: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "input": self.input_discount,
            "output": self.output_discount,
            "official_input_cny": self.official_input_cny,
            "official_output_cny": self.official_output_cny,
            "basis": self.basis,
            "source_url": self.source_url,
        }


def build_discount(
    row: dict[str, Any],
    official_models: dict[str, Any],
    rate: float,
) -> tuple[DiscountEntry | None, str | None]:
    """单条记录的折扣计算；返回 (条目, None) 或 (None, 跳过原因)。全部折算成 CNY 后相除。"""
    official_entry = official_models.get(model_key(str(row.get("model", ""))))
    if not isinstance(official_entry, dict) or not official_entry.get("found"):
        return None, "官方价文件中没有该模型的可用官方价"
    site_input, site_output = _site_prices(row)
    if site_input is None and site_output is None:
        return None, "站点未拿到可用价格"

    effective = official_entry.get("effective") or {}
    official_currency = str(official_entry.get("currency") or "USD").upper()

    def to_cny(value: Any, *, currency: str = "CNY", unit: str | None = None) -> Any:
        """官方价按条目 currency 换算；站点价按记录 unit 换算。"""
        if value is None:
            return None
        is_usd = ("USD" in unit.upper()) if unit is not None else currency == "USD"
        return round2(value * rate) if is_usd else round2(value)

    official_input_cny = to_cny(effective.get("input"), currency=official_currency)
    official_output_cny = to_cny(effective.get("output"), currency=official_currency)
    site_input_cny = to_cny(site_input, unit=row.get("unit"))
    site_output_cny = to_cny(site_output, unit=row.get("unit"))

    entry = DiscountEntry(
        site_id=row.get("site_id"),
        model=row.get("model"),
        group=row.get("group"),
        input_discount=round2(site_input_cny / official_input_cny) if site_input_cny and official_input_cny else None,
        output_discount=round2(site_output_cny / official_output_cny) if site_output_cny and official_output_cny else None,
        official_input_cny=official_input_cny,
        official_output_cny=official_output_cny,
        basis=str(effective.get("basis") or "list"),
        source_url=official_entry.get("source_url") or "",
    )
    return entry, None


def compute_discounts(
    rows: list[dict[str, Any]],
    official_models: dict[str, Any],
    rate: float,
) -> tuple[list[DiscountEntry], list[dict[str, Any]]]:
    """逐条对比站点价格与官方价，返回 (折扣明细, 跳过原因列表)。"""
    discounts: list[DiscountEntry] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        entry, reason = build_discount(row, official_models, rate)
        if entry is not None:
            discounts.append(entry)
        else:
            skipped.append({"model": row.get("model"), "group": row.get("group"), "reason": reason})
    return discounts, skipped


def summarize(discounts: list[DiscountEntry]) -> dict[str, Any]:
    """按模型汇总跨站点折扣区间（min / avg / max）与参与站点数。"""
    buckets: dict[str, dict[str, Any]] = {}
    for entry in discounts:
        key = model_key(str(entry.model))
        bucket = buckets.setdefault(key, {"model": entry.model, "input": [], "output": []})
        if entry.input_discount is not None:
            bucket["input"].append(entry.input_discount)
        if entry.output_discount is not None:
            bucket["output"].append(entry.output_discount)
    summary: dict[str, Any] = {}
    for key, bucket in buckets.items():
        stats: dict[str, Any] = {"model": bucket["model"]}
        for kind, values in (("input_discount", bucket["input"]), ("output_discount", bucket["output"])):
            if values:
                stats[kind] = {"min": round2(min(values)), "max": round2(max(values)), "avg": round2(sum(values) / len(values))}
        stats["sites_compared"] = len({entry.site_id for entry in discounts if model_key(str(entry.model)) == key})
        summary[key] = stats
    return summary


def _site_prices(row: dict[str, Any]) -> tuple[Any, Any]:
    """顶层价格优先；阶梯记录顶层为空时取第一档（standard）价格。"""
    site_input, site_output = row.get("input_price"), row.get("output_price")
    tiers = row.get("tiers") or []
    if (site_input is None or site_output is None) and tiers:
        first = tiers[0]
        site_input = site_input if site_input is not None else first.get("input_price")
        site_output = site_output if site_output is not None else first.get("output_price")
    return site_input, site_output
