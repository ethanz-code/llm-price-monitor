"""报告数据模型与变更判定：MonitorReport / SectionScan、价格口径指纹与事件分类。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from llm_price_monitor.config import ChangeKind
from llm_price_monitor.tracker import PriceRecord


@dataclass(frozen=True)
class PriceEvent:
    site_id: str
    model: str
    kind: ChangeKind
    previous: dict[str, Any] | None
    current: dict[str, Any] | None
    detected_at: float


@dataclass
class MonitorReport:
    started_at: float
    finished_at: float
    records: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    status_records: list[dict[str, Any]] = field(default_factory=list)
    status_events: list[dict[str, Any]] = field(default_factory=list)
    notice_records: list[dict[str, Any]] = field(default_factory=list)
    notice_events: list[dict[str, Any]] = field(default_factory=list)
    notice_results: list[dict[str, Any]] = field(default_factory=list)
    # 逐站价格采集状态（ok/inferred/no_data/auth_required/error + 原因）：
    # 401 这类"整站没价但不算错误"的情况只在这里，测试采集要靠它把真实原因报给用户
    site_status: dict[str, dict[str, Any]] = field(default_factory=dict)


def record_dict(site_id: str, record: PriceRecord) -> dict[str, Any]:
    return {"site_id": site_id, **asdict(record)}


# 变更检测纳入的 metadata 键（价格语义契约）：任一键变化才算一次价格变化。
# 展示型键（observed_model/aliases/source_url/error/notes/calculation_error 等）不参与。
# 新增承载价格语义的 metadata 键必须同步到这里，否则变化检测会静默漏报；
# 生产方：tracker.py 的 metadata 写入点、adapters.py 的直采/倍率记录、ai.py 的 _records。
FINGERPRINT_METADATA_KEYS = (
    "pricing_kind", "model_ratio", "completion_ratio", "group_ratio", "billing_mode",
    "billing_expr", "pricing_rules", "group",
    "cache_read_price", "cache_create_price", "cache_create_1h_price",
)


def fingerprint(value: dict[str, Any]) -> str:
    """价格口径指纹：只含价格相关字段，用于判定"价格是否真的变了"。

    AI 抽取每次输出的上下文边界、备注文本、证据引文都会漂移，不参与指纹；
    pricing_rules 里的 context 边界同理剔除。数值统一整值浮点归一（14.0 → 14），
    避免 int/float 表示差异被误判成变化。值为 None 的键与键缺失视为等价——
    同一价格有的抽取轮次显式写 null、有的直接省略键（cache_create_price 等），
    不归一的话每轮表示法抖动都会刷出一条"价格没变的变更"事件。
    """

    def normalize(item: Any) -> Any:
        if isinstance(item, bool):
            return item
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, list):
            return [normalize(entry) for entry in item]
        if isinstance(item, dict):
            return {
                key: normalize(entry)
                for key, entry in item.items()
                if key not in ("context_min", "context_max") and entry is not None
            }
        return item

    comparable = {key: normalize(value.get(key)) for key in ("model", "input_price", "output_price", "unit", "price_status", "requires_auth")}
    metadata = value.get("metadata") or {}
    comparable["metadata"] = normalize({key: metadata.get(key) for key in FINGERPRINT_METADATA_KEYS})
    return hashlib.sha256(json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def classify(previous: dict[str, Any] | None, current: dict[str, Any]) -> ChangeKind:
    if previous is None:
        return "new"
    if fingerprint(previous) == fingerprint(current):
        return "unchanged"
    if previous.get("price_status") == "unavailable" and current.get("price_status") == "confirmed":
        return "recovered"
    if previous.get("price_status") != current.get("price_status"):
        # 屏蔽掉状态字段再比一次：价格本体（数值/档位/缓存价）也变了就是真实价格变化，
        # 不能当成纯状态抖动丢事件；requires_auth 属于状态语义，一并归一
        def without_status(item: dict[str, Any]) -> dict[str, Any]:
            return {**item, "price_status": None, "requires_auth": None}

        if fingerprint(without_status(previous)) != fingerprint(without_status(current)):
            return "changed"
        return "status_changed"
    return "changed"


def site_status_from_records(records: list[PriceRecord]) -> dict[str, Any]:
    """从一次采集成功的记录推导站点级状态；整站请求失败由调用方直接填 error。

    ok=抓到确认/候选价；inferred=只有 AI 推断价（已回填进快照但未经页面交叉验证）；
    no_data=流程跑完但没抓到任何价格数据；auth_required=需要登录才能看到价格。
    """
    authed = [record for record in records if record.requires_auth]
    if authed:
        reason = next(
            (
                str(record.metadata.get("error"))
                for record in authed
                if record.metadata and record.metadata.get("error")
            ),
            None,
        )
        return {"status": "auth_required", "error": reason}
    if any(record.price_status in {"confirmed", "candidate"} for record in records):
        return {"status": "ok", "error": None}
    if any(record.price_status == "rule_only" for record in records):
        return {"status": "inferred", "error": None}
    reason = next(
        (
            str(record.metadata.get("error") or record.metadata.get("notes"))
            for record in records
            if record.metadata and (record.metadata.get("error") or record.metadata.get("notes"))
        ),
        None,
    )
    return {"status": "no_data", "error": reason}


@dataclass
class SectionScan:
    """单项扫描结果：records / events / errors 三段，拆分采集的任务体直接用它做摘要。"""

    records: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    # 逐站结果摘要（当前仅公告扫描填充），供测试采集等场景无条件回传各站 outcome
    site_results: list[dict[str, Any]] = field(default_factory=list)
