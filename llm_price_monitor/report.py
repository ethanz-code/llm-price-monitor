"""运行编排与报告：监控运行、事件分类、持久化、汇总输出。

采集拆成三类独立扫描：价格（scan_prices）、渠道状态（scan_statuses）、站点公告（scan_notices），
可分别按各自周期定时执行；run_once 是全量入口，共享一个连接串行跑三类。
每类扫描：选 UA → 逐站点采集 → 与上次快照比对生成事件 → 写历史/快照 → 输出摘要。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import httpx

from llm_price_monitor import tasklog
from llm_price_monitor.adapters import ADAPTERS
from llm_price_monitor.config import AuthRequiredError, ChangeKind, MonitorConfig, PriceMonitorError, SiteSpec
from llm_price_monitor.http_retry import build_client
from llm_price_monitor.tracker import PriceRecord
from llm_price_monitor.units import round2, tier_unit_per_1m
from llm_price_monitor.useragent import choose_user_agent
from llm_price_monitor.catalog import discount as catalog_discount, fx as catalog_fx
from llm_price_monitor.notice import fetch_site_notice
from llm_price_monitor.status import diff_status, fetch_site_status
from llm_price_monitor.store import Store, latest_key, latest_site_prefix, split_latest_key
from llm_price_monitor.token_refresh import needs_refresh, refresh_and_recollect, refresh_and_retry_once


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


# 测试采集与全局采集并行后，两路扫描可能同时读-改-写 collect_status / site_collect_health
# 两个整文档，后写者会覆盖先写者的站点条目；进程内互斥让合并串行，双方条目都不丢
_MERGE_LOCK = threading.Lock()

# 事件/历史/快照落库的复查-写入原子锁：并行采集各自拿旧快照比对，若不加锁，
# 后复查的一路看不到先落库一路的结果，同一变化会写成两条事件（见 _persist_scan_results）
_PERSIST_LOCK = threading.Lock()


def _merge_collect_status(store: Store, config: MonitorConfig, site_status: dict[str, dict[str, Any]]) -> None:
    """把本轮各站点的价格采集状态并入 collect_status：单站点采集不能冲掉其他站点的状态。"""
    with _MERGE_LOCK:
        previous_status = store.get_document("collect_status")
        merged_status = dict(previous_status) if isinstance(previous_status, dict) else {}
        for spec in config.sites:
            if spec.id in site_status:
                merged_status[spec.id] = site_status[spec.id]
            elif not spec.enabled:
                merged_status[spec.id] = {"status": "disabled", "error": None, "checked_at": None}
        store.set_document("collect_status", merged_status)


def _merge_site_health(store: Store, section: str, entries: dict[str, dict[str, Any] | None]) -> None:
    """把一类采集的逐站结果并入 site_collect_health：entry 为 None 表示该站本轮正常，清掉旧记录。"""
    with _MERGE_LOCK:
        doc = dict(store.get_document("site_collect_health") or {})
        for site_id, entry in entries.items():
            site_doc = dict(doc.get(site_id) or {})
            if entry is None:
                site_doc.pop(section, None)
            else:
                site_doc[section] = entry
            if site_doc:
                doc[site_id] = site_doc
            else:
                doc.pop(site_id, None)
        store.set_document("site_collect_health", doc)


_TRANSPORT_ERROR_RE = re.compile(r"ssl\b|\beof\b|timed out|timeout|connection|disconnect", re.IGNORECASE)

# 渠道状态连续多少轮传输抖动后才升级为错误：状态 5 分钟一轮，3 轮约 15 分钟。
# 秒级/分钟级的线路抖动每轮都刷错误卡片等于噪声，持续宕机仍会在容忍窗口内报警
STATUS_TRANSPORT_TOLERANCE = 3
_TRANSPORT_STREAK_DOC = "status_transport_streak"


def _bump_transport_streak(store: Store | None, site_id: str) -> int:
    """渠道状态传输抖动的连续失败轮数 +1，返回新值；无库可记时按首轮处理。"""
    if store is None:
        return 1
    with _MERGE_LOCK:
        doc = dict(store.get_document(_TRANSPORT_STREAK_DOC) or {})
        streak = int(doc.get(site_id) or 0) + 1
        doc[site_id] = streak
        store.set_document(_TRANSPORT_STREAK_DOC, doc)
    return streak


def _reset_transport_streak(store: Store | None, site_id: str) -> None:
    """站点采集恢复正常（或换成了非传输类失败）后清零抖动计数。"""
    if store is None:
        return
    with _MERGE_LOCK:
        doc = dict(store.get_document(_TRANSPORT_STREAK_DOC) or {})
        if site_id in doc:
            del doc[site_id]
            store.set_document(_TRANSPORT_STREAK_DOC, doc)


def is_transport_error(message: str) -> bool:
    """网络传输层故障（SSL 握手中断、超时、连接被重置等）：多为环境抖动，不值得进健康档案惊动用户。"""
    return bool(_TRANSPORT_ERROR_RE.search(message))


def _merge_price_health(store: Store, config: MonitorConfig, site_status: dict[str, dict[str, Any]]) -> None:
    """价格采集的逐站异常进健康档案：采集失败红、需认证/无数据黄，正常或停用清除。"""
    entries: dict[str, dict[str, Any] | None] = {}
    for spec in config.sites:
        value = site_status.get(spec.id)
        status = str(value.get("status")) if value else ""
        if status == "error":
            message = str(value.get("error") or "价格采集失败")
            entries[spec.id] = (
                None
                if is_transport_error(message)
                else {
                    "level": "error",
                    "message": message,
                    "time": float(value.get("checked_at") or time.time()),
                }
            )
        elif status in {"auth_required", "no_data"}:
            fallback = "需要登录才能看到价格" if status == "auth_required" else "本轮没抓到任何价格数据"
            entries[spec.id] = {
                "level": "warn",
                "message": str(value.get("error") or fallback),
                "time": float(value.get("checked_at") or time.time()),
            }
        else:
            entries[spec.id] = None  # 正常或停用：不保留旧异常
    _merge_site_health(store, "price", entries)


def _section_health_entries(config: MonitorConfig, scan: SectionScan, fallback: str) -> dict[str, dict[str, Any] | None]:
    """渠道状态/公告采集共用的逐站异常条目：本轮业务报错的进档案，传输层抖动与其余（正常/停用/未配置接口）清除。"""
    errored = {str(item.get("site_id")): str(item.get("error") or fallback) for item in scan.errors}
    errored = {site_id: message for site_id, message in errored.items() if not is_transport_error(message)}
    return {
        spec.id: ({"level": "error", "message": errored[spec.id], "time": time.time()} if spec.id in errored else None)
        for spec in config.sites
    }


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


# 单站采集的硬上限（秒）：适配器内有 per-request 超时，但实测经本地代理的半死连接
# 会在 SSL 抖动重试后无限挂起、超时不触发——单站卡死不能拖垮整轮和后续站点的落库
SITE_HARD_TIMEOUT_SECONDS = 900.0


def _run_network_phase(
    phase: Callable[[], tuple[list[PriceRecord], list[str]]], site_id: str
) -> tuple[list[PriceRecord], list[str]]:
    """在 daemon 子线程里跑单站采集（含续签重采），到硬上限仍未返回就放弃该站。

    Python 线程杀不掉：超时后工作线程变孤儿，仍占着那条挂死的连接，但它只采不写库、
    不阻塞后续站点（共享的 httpx.Client 线程安全），daemon 属性也不挡进程退出；
    它最终的产出被丢弃，本轮以超时错误记档。子线程转发主线程的日志出口，
    否则适配器与重试层的过程日志会因 thread-local 出口缺失而静默丢失。
    """
    sink = tasklog.current_sink()
    box: dict[str, Any] = {}

    def _run() -> None:
        if sink is not None:
            tasklog.bind(sink)
        try:
            box["result"] = phase()
        except BaseException as exc:  # 采集线程的异常转交主线程按原语义处理
            box["error"] = exc

    worker = threading.Thread(target=_run, name=f"price-scan-{site_id}", daemon=True)
    worker.start()
    worker.join(SITE_HARD_TIMEOUT_SECONDS)
    if worker.is_alive():
        message = f"采集超过 {SITE_HARD_TIMEOUT_SECONDS:g}s 硬上限仍未返回，本轮放弃该站（连接可能卡死在代理隧道上）"
        tasklog.emit(f"[{site_id}] {message}", "error")
        return [], [message]
    if "error" in box:
        raise box["error"]
    result = box.get("result")
    if isinstance(result, tuple) and len(result) == 2:
        return result
    return [], ["采集线程未返回有效结果"]


def _scan_prices(
    config: MonitorConfig,
    client: httpx.Client,
    user_agent: str,
    store: Store | None,
    latest: dict[str, dict[str, Any]],
    persist: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]], set[str], set[str]]:
    """价格采集主体：逐站点走适配器，与上次快照比对生成事件。

    返回 (records, history_rows, events, errors, site_status, removed_keys, touched_keys)；
    history_rows 只含本次真正取到价的记录——沿用上次价的占位行与无数据的跳过行都不写历史；
    removed_keys 是本轮分组下线从快照摘除的 key，需要从数据库显式删行；
    touched_keys 是本轮实际写过快照的 key，落库时只写这些，防止与并行采集互相回写覆盖。
    """
    records: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    site_status: dict[str, dict[str, Any]] = {}
    removed_keys: set[str] = set()
    touched_keys: set[str] = set()
    enabled_count = sum(1 for spec in config.sites if spec.enabled)
    scan_started = time.time()
    tasklog.emit(f"开始价格采集：{enabled_count} 个站点")
    done_count = 0
    for spec in config.sites:
        if not spec.enabled:
            continue
        done_count += 1
        site_started = time.time()
        # 每站开始即落一行：整轮拖长时能直接看出卡在哪个站、卡在站点内哪一步之后
        tasklog.emit(f"[{spec.id}] 开始采集（第 {done_count}/{enabled_count} 站）")
        adapter = ADAPTERS.get(spec.adapter)
        if adapter is None:
            errors.append({"site_id": spec.id, "error": f"未知适配器: {spec.adapter}"})
            site_status[spec.id] = {"status": "error", "error": f"未知适配器: {spec.adapter}", "checked_at": time.time()}
            tasklog.emit(f"[{spec.id}] 价格采集失败：未知适配器 {spec.adapter}", "error")
            continue
        # 多地址站点：主地址 + networks 附加地址依次采集；同一模型重复命中时主地址优先
        # 分组白名单：status.groups 配置了就只保留选中分组的记录（含续签重采路径）
        whitelist = [str(item) for item in spec.status["groups"]] if isinstance(spec.status.get("groups"), list) else []

        def collect_all(site_spec: SiteSpec) -> tuple[list[PriceRecord], list[str]]:
            site_records: list[PriceRecord] = []
            site_by_key: dict[tuple[str | None, str | None], PriceRecord] = {}
            site_errors: list[str] = []
            for index, endpoint in enumerate([site_spec.network, *site_spec.networks]):
                endpoint_spec = replace(site_spec, network=endpoint, networks=())
                try:
                    endpoint_records = _filter_price_groups(
                        adapter.collect(endpoint_spec, client, config.settings.timeout, user_agent, config.ai),
                        whitelist,
                    )
                except (PriceMonitorError, httpx.HTTPError, ValueError) as exc:
                    site_errors.append(f"{'主地址' if index == 0 else f'附加地址{index}'}: {exc}")
                    continue
                # 同一模型+分组多地址命中时主地址优先；但主地址的"需认证/无数据"占位
                # 不得挡掉附加地址的真价格（401 只说明该地址要登录，不代表分组没数据）
                for record in endpoint_records:
                    key = (record.model, (record.metadata or {}).get("group"))
                    existing = site_by_key.get(key)
                    if existing is not None and (existing.price_status != "unavailable" or record.price_status == "unavailable"):
                        continue
                    site_by_key[key] = record
                    if existing is None:
                        site_records.append(record)
                    else:
                        # 用下标原位替换：PriceRecord 值相等，按值 index 可能命中另一个相等对象
                        for idx, item in enumerate(site_records):
                            if item is existing:
                                site_records[idx] = record
                                break
            return site_records, site_errors

        def network_phase() -> tuple[list[PriceRecord], list[str]]:
            phase_records, phase_errors = collect_all(spec)
            # 有任一"需认证"且配了续签接口：续签 token、回写数据库并用新 token 重采一次
            if spec.token_refresh and needs_refresh(phase_records):
                tasklog.emit(f"[{spec.id}] 采集返回需认证，尝试续签 token…")
                # 重采结果整体替换本轮记录与地址错误；续签失败或重采无数据时拿到的是续签前的
                # "需认证"占位记录，保住该状态下面才会跳过分组下线判定（401 是我方凭证问题，
                # 不代表分组真的下线）。错误列表不再被静默清空，重采阶段的地址失败照常上报。
                phase_records, phase_errors = refresh_and_recollect(
                    spec,
                    phase_records,
                    collect=collect_all,
                    client=client,
                    timeout=config.settings.timeout,
                    user_agent=user_agent,
                    store=store,
                )
                if not phase_errors:
                    tasklog.emit(f"[{spec.id}] token 已续签并重新采集：{len(phase_records)} 条价格")
            return phase_records, phase_errors

        collected, collect_errors = _run_network_phase(network_phase, spec.id)
        if collect_errors and not collected:
            message = "；".join(collect_errors)
            errors.append({"site_id": spec.id, "error": message})
            site_status[spec.id] = {"status": "error", "error": message, "checked_at": time.time()}
            tasklog.emit(f"[{spec.id}] 价格采集失败：{message}", "error")
            continue
        for message in collect_errors:
            errors.append({"site_id": spec.id, "error": message})
            tasklog.emit(f"[{spec.id}] 部分地址采集失败：{message}", "error")
        site_status[spec.id] = {"checked_at": time.time(), **site_status_from_records(collected)}
        site_changed = 0
        site_keys: set[str] = set()
        # 站点切片标记：本站新增的历史行与事件从这两个下标起，供站内立即落库
        history_mark = len(history_rows)
        events_mark = len(events)
        # 本轮开始前快照里已有的模型：这些模型冒出新分组记 group_added，全新模型仍记 new
        known_models = {
            split_latest_key(key, spec.id)[0]
            for key in latest
            if key.startswith(latest_site_prefix(spec.id))
        }
        # 快照里没有任何本站数据 = 建档轮：逐模型"新增"事件只会刷屏，静默入库
        site_is_new = not known_models
        # sanity 判据每站现读：一轮采集可能被卡死站点或机器休眠拖上数小时，
        # 目录中途修正后要用新判据，启动时的坏目录不得贯穿整轮
        sanity_models, sanity_rate = _sanity_context(store)
        for record in collected:
            current = _apply_price_sanity(_backfill_rule_price(record_dict(spec.id, record)), sanity_models, sanity_rate)
            # 分组归一：metadata 缺失时兜底 default，保证事件键跨扫描稳定
            group = (record.metadata or {}).get("group") or "default"
            key = latest_key(spec.id, record.model, group)
            site_keys.add(key)
            previous = latest.get(key)
            if not _has_price(current):
                if previous is None or not _has_price(previous):
                    # 本次没拿到数据、上次也没有可用价：不新增占位记录，避免快照与历史重复膨胀
                    continue
                # 上次有价、本次没拿到：沿用上次价格并标"需认证"，但不写历史（价格没有新观测）
                current = _carry_last_price(current, previous)
                latest[key] = current
                touched_keys.add(key)
                records.append(current)
                kind = classify(previous, current)
                events.append(asdict(PriceEvent(spec.id, record.model, kind, previous, current, time.time())))
                site_changed += kind != "unchanged"
                continue

            current["fingerprint"] = fingerprint(current)
            current["captured_at"] = record.captured_at
            records.append(current)
            # 指纹去重：价格口径与上一轮一致就不写历史行，趋势表只保留真实变化点。
            # previous 的指纹现场重算而非读存量哈希——历史行落库时的口径可能比当前代码旧，
            # 直接比存量哈希会让口径升级后的第一轮全量多写一遍趋势行
            if previous is None or fingerprint(previous) != current["fingerprint"]:
                history_rows.append(current)
            if previous is None and record.model in known_models:
                kind = "group_added"  # 老模型的新分组：与分组下线对称，区别于全新模型的"新增"
            else:
                kind = classify(previous, current)
            if not site_is_new:
                events.append(asdict(PriceEvent(spec.id, record.model, kind, previous, current, time.time())))
                site_changed += kind != "unchanged"
            latest[key] = current
            touched_keys.add(key)
        # 出现"需认证"占位行（401/403）时是我方凭证问题，不代表分组真的下线，
        # 跳过缺失计数，避免 token 过期把分组刷成下线事件；
        # 有地址采集失败时本轮记录同样不完整（没采到 ≠ 分组下线），一并跳过；
        # 测试采集（persist=False）同样不计数——不完整的测试轮次不得污染正式下线阈值
        site_removed: set[str] = set()
        if store is not None and persist and not needs_refresh(collected) and not collect_errors:
            site_removed = _detect_removed_groups(store, latest, spec.id, site_keys, events)
            removed_keys |= site_removed
        # 每站采完立即落库：单站卡死被放弃、或整轮中途被重启时，已完成的站点不随内存丢失。
        # 落库范围用站点切片限定（touched ∩ site_keys 恰为本站写过的快照 key），
        # latest 仍传完整内存快照，由 touched 限定只比对本站 key。
        if store is not None and persist:
            _persist_scan_results(
                store,
                latest=latest,
                history_rows=history_rows[history_mark:],
                events=[
                    event
                    for event in events[events_mark:]
                    if event["kind"] not in ("unchanged", "status_changed")
                ],
                removed_keys=site_removed or None,
                touched_keys=touched_keys & site_keys,
            )
        if site_is_new:
            tasklog.emit(f"[{spec.id}] 站点建档：{len(collected)} 条价格入库（首轮不产生变化事件），{time.time() - site_started:.1f}s")
        else:
            tasklog.emit(f"[{spec.id}] 价格采集成功：{len(collected)} 条价格，{site_changed} 处变化，{time.time() - site_started:.1f}s")
    tasklog.emit(f"价格采集完成：{len(records)} 条记录，{len(errors)} 个错误，{time.time() - scan_started:.1f}s")
    return records, history_rows, events, errors, site_status, removed_keys, touched_keys


# 分组连续缺失这么多次才判定"下线"：站点换分组清单、临时调整常态发生，
# 短阈值会把改版刷成下线事件；采集间隔 1 小时，6 轮即容忍半天的窗口期
GROUP_REMOVED_MISSES = 6


def _detect_removed_groups(
    store: Store, latest: dict[str, dict[str, Any]], site_id: str, seen_keys: set[str], events: list[dict[str, Any]]
) -> set[str]:
    """本轮采集成功的站点里，快照中存在但本轮没出现的分组视为一次缺失：
    连续 GROUP_REMOVED_MISSES 次缺失记 group_removed 事件并从快照摘除；中途恢复则清零。
    缺失计数持久化到 group_miss 文档，跨轮次累计；返回本轮摘除的快照 key。
    group_miss 是并行采集（全量 + 单站测试）共享的读-改-写文档，全程持 _MERGE_LOCK，
    否则并发轮次会互相丢计数或对同一次下线重复发事件。"""
    with _MERGE_LOCK:
        watch = dict(store.get_document("group_miss") or {})
        removed: set[str] = set()
        changed = False
        site_prefix = f"{site_id}:"
        for key in [key for key in latest if key.startswith(site_prefix)]:
            if key in seen_keys:
                if watch.pop(key, None) is not None:
                    changed = True
                continue
            if not _has_price(latest[key]):
                continue  # 无价占位行不属于"分组下线"
            count = int(watch.get(key) or 0) + 1
            if count >= GROUP_REMOVED_MISSES:
                previous = latest.pop(key)
                removed.add(key)
                watch.pop(key, None)
                model = split_latest_key(key, site_id)[0]
                events.append({
                    "site_id": site_id,
                    "model": model,
                    "kind": "group_removed",
                    "previous": previous,
                    "current": None,
                    "detected_at": time.time(),
                })
                tasklog.emit(f"[{site_id}] 分组下线：{model}")
            else:
                watch[key] = count
            changed = True
        for key in [key for key in watch if key not in latest]:
            watch.pop(key, None)  # 快照里已经没有的 key 不再计数（站点被删/已被摘除）
            changed = True
        if changed:
            store.set_document("group_miss", watch)
        return removed


def _persist_latest(
    store: Store,
    latest: dict[str, dict[str, Any]],
    *,
    removed_keys: set[str] | None = None,
    touched_keys: set[str] | None = None,
) -> None:
    """写回最新快照；replace_latest 只做 upsert，分组下线摘除的 key 需要显式删行。
    顺带清理历史遗留的无价占位行——现行采集不再产出占位记录，
    快照里残留的无价行都是旧版本（或旧库）留下的，保留只会在定价页造成同模型重复。
    touched_keys 限定本轮真正写过的 key：本轮开始时读到的其他站点行可能已被并行采集更新，
    拿旧值比对"内容变了"会把这些行回写覆盖，也可能把并发轮次刚摘除的分组复活。"""
    stale_keys = [key for key, row in latest.items() if isinstance(row, dict) and not _has_price(row)]
    if stale_keys:
        for key in stale_keys:
            del latest[key]
        store.remove_latest(stale_keys)
    if removed_keys:
        store.remove_latest(sorted(removed_keys))
    # 增量写入：与库中现有快照比对，内容没变的行不重写
    existing = store.latest_all()
    changed = {
        key: row
        for key, row in latest.items()
        if (touched_keys is None or key in touched_keys) and existing.get(key) != row
    }
    store.replace_latest(changed)


# 含 ":" 的模型/分组名解码时会并段（group 恒取最后一段），每个名字只提醒一次
_COLON_KEY_WARNED: set[str] = set()


def _event_snapshot_key(event: dict[str, Any]) -> str:
    """事件对应的快照 key（store.latest_key，site:model:group）；分组下线事件从 previous 取分组。"""
    record = event.get("current") or event.get("previous") or {}
    group = (record.get("metadata") or {}).get("group") or "default"
    model = event["model"]
    if (":" in model or ":" in group) and model not in _COLON_KEY_WARNED:
        _COLON_KEY_WARNED.add(model)
        tasklog.emit(f"[{event['site_id']}] 模型或分组名含冒号，快照 key 解析会把多余段并进模型名：{model} / {group}", "warn")
    return latest_key(event["site_id"], model, group)


def _drop_persisted_events(
    events: list[dict[str, Any]], fresh_latest: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """丢掉已被并发轮次落库的价格事件：测试采集与全量采集并行时（tasks.COLLECT_KINDS 特意放行），
    两路可能对同一份旧快照各自检出同一变化——快照里该 key 的指纹已经等于事件 current，
    或分组下线时 key 已被摘除，都说明另一路记录过同一变化，再写只会在事件流里出重复卡片。"""
    kept: list[dict[str, Any]] = []
    for event in events:
        key = _event_snapshot_key(event)
        if event.get("current") is not None:
            concurrent = fresh_latest.get(key)
            if concurrent is not None and fingerprint(concurrent) == fingerprint(event["current"]):
                continue
        elif key not in fresh_latest:
            continue
        kept.append(event)
    return kept


def _drop_persisted_notice_events(events: list[dict[str, Any]], store: Store) -> list[dict[str, Any]]:
    """公告事件同口径去重：库里该站点最新公告正文已等于事件正文，说明并发轮次已记录。"""
    kept: list[dict[str, Any]] = []
    for event in events:
        notice = store.latest_notice(event["site_id"])
        if isinstance(notice, dict) and notice.get("content") == event.get("content"):
            continue
        kept.append(event)
    return kept


def _persist_scan_results(
    store: Store,
    *,
    latest: dict[str, dict[str, Any]],
    history_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    removed_keys: set[str] | None,
    touched_keys: set[str],
    notice_records: list[dict[str, Any]] | None = None,
    notice_events: list[dict[str, Any]] | None = None,
) -> None:
    """价格/公告采集结果统一落库。
    复查与写入在同一把锁内完成：后落库的一路必然看到先落库一路的快照与事件，
    同一变化只入库一次；锁外再做的只有 collect_status / 健康档案合并（自有锁）。
    入参 events 只被去重后的结果写入库里，报告仍带本轮原始观测（弹窗如实回显）。"""
    with _PERSIST_LOCK:
        fresh_latest = store.latest_all()
        # 公告去重必须在本轮 notice_records 入库前做：比对对象是"库里已有的最新公告"，
        # 先写后比会拿本轮刚落的记录跟自己比对，把真实事件误判成并发重复
        kept_notice_events = (
            _drop_persisted_notice_events(notice_events, store) if notice_events else notice_events
        )
        store.append_history(history_rows)
        store.append_events(_drop_persisted_events(events, fresh_latest))
        if notice_records:
            store.append_notice_records(notice_records)
        if kept_notice_events:
            store.append_notice_events(kept_notice_events)
        _persist_latest(store, latest, removed_keys=removed_keys, touched_keys=touched_keys)


def _scan_statuses(
    config: MonitorConfig, client: httpx.Client, user_agent: str, store: Store | None
) -> SectionScan:
    """渠道状态采集：只处理配置了 status.url 的站点；与上次记录 diff，变化写入 status 事件。"""
    scan = SectionScan()
    scan_started = time.time()
    tasklog.emit("开始渠道状态采集")

    def _record_status_failure(site_id: str, exc_text: str) -> None:
        """状态采集失败落账：传输抖动（SSL/超时/连接重置）连续不足容忍轮数只留 warn 日志、
        不刷错误卡片；达到容忍轮数或非传输类失败照旧报错。成功与非传输失败会清零抖动计数。"""
        if is_transport_error(exc_text):
            streak = _bump_transport_streak(store, site_id)
            if streak < STATUS_TRANSPORT_TOLERANCE:
                tasklog.emit(
                    f"[{site_id}] 渠道状态采集失败：{exc_text}（网络抖动连续第 {streak}/{STATUS_TRANSPORT_TOLERANCE} 轮，暂不报警）",
                    "warn",
                )
                return
            tasklog.emit(f"[{site_id}] 渠道状态采集失败：{exc_text}（网络抖动已连续 {streak} 轮）", "error")
        else:
            _reset_transport_streak(store, site_id)
            tasklog.emit(f"[{site_id}] 渠道状态采集失败：{exc_text}", "error")
        scan.errors.append({"site_id": site_id, "error": f"渠道状态采集失败: {exc_text}"})

    for spec in config.sites:
        if not spec.enabled or not spec.status.get("url"):
            continue
        try:
            status_record = fetch_site_status(spec, client, config.settings.timeout, user_agent, config.ai)
        except AuthRequiredError as exc:
            # 站点配了续签就换一次新 token 重试；没配续签的按普通失败处理
            if not spec.token_refresh:
                _record_status_failure(spec.id, str(exc))
                continue
            tasklog.emit(f"[{spec.id}] 渠道状态返回需认证，尝试续签 token…")
            status_record, retry_error = refresh_and_retry_once(
                spec,
                client=client,
                timeout=config.settings.timeout,
                user_agent=user_agent,
                store=store,
                attempt=lambda fresh: fetch_site_status(fresh, client, config.settings.timeout, user_agent, config.ai),
            )
            if retry_error is not None:
                _record_status_failure(spec.id, str(retry_error))
                continue
            tasklog.emit(f"[{spec.id}] token 已续签并重新采集渠道状态")
        except (PriceMonitorError, httpx.HTTPError, ValueError) as exc:
            _record_status_failure(spec.id, str(exc))
            continue
        scan.records.append(status_record)
        _reset_transport_streak(store, spec.id)
        # diff 基准用未裁剪的参照快照：库里的快照只存时间线增量，直接拿会误报大量删除
        previous_record = store.status_reference(spec.id) if store is not None else None
        previous_data = previous_record.get("data") if isinstance(previous_record, dict) else None
        if previous_data is None:
            changes: list[dict[str, Any]] = [{"op": "init", "path": "$"}]
            status_kind = "status_init"
        else:
            changes = diff_status(previous_data, status_record["data"])
            status_kind = "status_changed"
        if len(changes) > 80:
            changes = changes[:80] + [{"op": "truncated", "path": "$", "count": len(changes) - 80}]
        if changes:
            scan.events.append({
                "site_id": spec.id,
                "kind": status_kind,
                "detected_at": status_record["captured_at"],
                "changes": changes,
            })
            tasklog.emit(f"[{spec.id}] 渠道状态有变化")
        else:
            tasklog.emit(f"[{spec.id}] 渠道状态无变化")
    tasklog.emit(f"渠道状态采集完成：{len(scan.records)} 个站点，{len(scan.errors)} 个错误，{time.time() - scan_started:.1f}s")
    return scan


def _scan_notices(
    config: MonitorConfig, client: httpx.Client, user_agent: str, store: Store | None
) -> SectionScan:
    """站点公告采集：未配置 notice.url 的站点自动抓根地址 /api/notice，404 视为没有公告接口静默跳过。

    正文与上一版本比较：空正文不入库；内容不变不重复存版本、不发事件。
    """
    scan = SectionScan()
    scan_started = time.time()
    tasklog.emit("开始站点公告采集")
    for spec in config.sites:
        if not spec.enabled:
            continue
        try:
            notice_record = fetch_site_notice(spec, client, config.settings.timeout, user_agent, ai=config.ai)
        except AuthRequiredError as exc:
            # 同渠道状态：配了续签就换新 token 重试一次，否则照常记错误
            if not spec.token_refresh:
                scan.errors.append({"site_id": spec.id, "error": f"站点公告采集失败: {exc}"})
                scan.site_results.append({"site_id": spec.id, "outcome": "error", "content": ""})
                tasklog.emit(f"[{spec.id}] 站点公告采集失败：{exc}", "error")
                continue
            tasklog.emit(f"[{spec.id}] 公告地址返回需认证，尝试续签 token…")
            notice_record, retry_error = refresh_and_retry_once(
                spec,
                client=client,
                timeout=config.settings.timeout,
                user_agent=user_agent,
                store=store,
                attempt=lambda fresh: fetch_site_notice(
                    fresh, client, config.settings.timeout, user_agent, ai=config.ai
                ),
            )
            if retry_error is not None:
                scan.errors.append({"site_id": spec.id, "error": f"站点公告采集失败: {retry_error}"})
                scan.site_results.append({"site_id": spec.id, "outcome": "error", "content": ""})
                tasklog.emit(f"[{spec.id}] 站点公告采集失败：{retry_error}", "error")
                continue
            tasklog.emit(f"[{spec.id}] token 已续签并重新采集公告")
        except (PriceMonitorError, httpx.HTTPError, ValueError) as exc:
            scan.errors.append({"site_id": spec.id, "error": f"站点公告采集失败: {exc}"})
            scan.site_results.append({"site_id": spec.id, "outcome": "error", "content": ""})
            tasklog.emit(f"[{spec.id}] 站点公告采集失败：{exc}", "error")
            continue
        if notice_record is None:
            scan.site_results.append({"site_id": spec.id, "outcome": "none", "content": ""})
            continue
        if not notice_record["content"]:
            # 站点没发布公告是常态：带上库里最近一条公告，测试弹窗回显给用户看
            latest = store.latest_notice(spec.id) if store is not None else None
            scan.site_results.append({
                "site_id": spec.id,
                "outcome": "empty",
                "content": "",
                "latest": str(latest.get("content") or "") if isinstance(latest, dict) else "",
            })
            continue
        previous_notice = store.latest_notice(spec.id) if store is not None else None
        previous_content = previous_notice.get("content") if isinstance(previous_notice, dict) else None
        if previous_content is None:
            notice_kind: str | None = "notice_init"
        elif previous_content != notice_record["content"]:
            notice_kind = "notice_changed"
        else:
            notice_kind = None
        scan.site_results.append({"site_id": spec.id, "outcome": notice_kind or "unchanged", "content": notice_record["content"]})
        if notice_kind:
            notice_record["kind"] = notice_kind
            scan.records.append(notice_record)
            scan.events.append({
                "site_id": spec.id,
                "kind": notice_kind,
                "detected_at": notice_record["captured_at"],
                "content": notice_record["content"],
            })
            # 公告无变化与未配置公告接口的站点不逐站记日志，避免刷屏
            tasklog.emit(f"[{spec.id}] 公告{'新增' if notice_kind == 'notice_init' else '更新'}")
    tasklog.emit(f"站点公告采集完成：{len(scan.records)} 条公告，{len(scan.errors)} 个错误，{time.time() - scan_started:.1f}s")
    return scan


def scan_prices(
    config: MonitorConfig,
    *,
    store: Store | None = None,
    client: httpx.Client | None = None,
    user_agent: str | None = None,
    persist: bool = True,
) -> MonitorReport:
    """独立价格采集：只采价格并比对，不触渠道状态与站点公告。"""
    started = time.time()
    selected_user_agent = choose_user_agent(config, user_agent)
    own = client is None
    client = client or build_client()
    latest = store.latest_all() if store is not None else {}
    try:
        records, history_rows, events, errors, site_status, removed_keys, touched_keys = _scan_prices(config, client, selected_user_agent, store, latest, persist)
        # price_status 在确认/规则/无数据之间抖动不代表价格真的变了，这类事件不落库
        changed_events = [event for event in events if event["kind"] not in ("unchanged", "status_changed")]
        if persist and store is not None:
            # 价格已在 _scan_prices 内逐站落库，这里只合并渠道状态与健康档案
            _merge_collect_status(store, config, site_status)
            _merge_price_health(store, config, site_status)
        return MonitorReport(started, time.time(), records, changed_events, errors, site_status=site_status)
    finally:
        if own:
            client.close()


def scan_statuses(
    config: MonitorConfig,
    *,
    store: Store | None = None,
    client: httpx.Client | None = None,
    user_agent: str | None = None,
    persist: bool = True,
) -> SectionScan:
    """独立渠道状态采集：与价格采集解耦，可按自己的周期定时执行。"""
    selected_user_agent = choose_user_agent(config, user_agent)
    own = client is None
    client = client or build_client()
    try:
        scan = _scan_statuses(config, client, selected_user_agent, store)
        if persist and store is not None:
            store.append_status_records(scan.records)
            store.append_status_events(scan.events)
            _merge_site_health(store, "status", _section_health_entries(config, scan, "渠道状态采集失败"))
        return scan
    finally:
        if own:
            client.close()


def scan_notices(
    config: MonitorConfig,
    *,
    store: Store | None = None,
    client: httpx.Client | None = None,
    user_agent: str | None = None,
    persist: bool = True,
) -> SectionScan:
    """独立站点公告采集：与价格采集解耦，可按自己的周期定时执行。"""
    selected_user_agent = choose_user_agent(config, user_agent)
    own = client is None
    client = client or build_client()
    try:
        scan = _scan_notices(config, client, selected_user_agent, store)
        if persist and store is not None:
            # 单站公告事件与全量采集共用同一把复查锁：并行的测试采集不会重复落同一条公告事件
            _persist_scan_results(
                store,
                latest={},
                history_rows=[],
                events=[],
                removed_keys=None,
                touched_keys=set(),
                notice_records=scan.records,
                notice_events=scan.events,
            )
            _merge_site_health(store, "notice", _section_health_entries(config, scan, "站点公告采集失败"))
        return scan
    finally:
        if own:
            client.close()


def run_once(
    config: MonitorConfig,
    *,
    store: Store | None = None,
    client: httpx.Client | None = None,
    user_agent: str | None = None,
    persist: bool = True,
) -> MonitorReport:
    """全量采集：价格、渠道状态、站点公告串行跑一遍，互不阻断；三类也可经 scan_prices 等单独触发。"""
    started = time.time()
    selected_user_agent = choose_user_agent(config, user_agent)
    own = client is None
    client = client or build_client()
    latest = store.latest_all() if store is not None else {}
    try:
        records, history_rows, events, errors, site_status, removed_keys, touched_keys = _scan_prices(config, client, selected_user_agent, store, latest, persist)
        status_scan = _scan_statuses(config, client, selected_user_agent, store)
        notice_scan = _scan_notices(config, client, selected_user_agent, store)
        errors = [*errors, *status_scan.errors, *notice_scan.errors]
        changed_events = [event for event in events if event["kind"] not in ("unchanged", "status_changed")]
        if persist and store is not None:
            # 价格已在 _scan_prices 内逐站落库；这里只补公告与渠道状态（公告与 scan_notices 同口径）
            _persist_scan_results(
                store,
                latest={},
                history_rows=[],
                events=[],
                removed_keys=None,
                touched_keys=set(),
                notice_records=notice_scan.records,
                notice_events=notice_scan.events,
            )
            store.append_status_records(status_scan.records)
            store.append_status_events(status_scan.events)
            _merge_collect_status(store, config, site_status)
            _merge_price_health(store, config, site_status)
            _merge_site_health(store, "status", _section_health_entries(config, status_scan, "渠道状态采集失败"))
            _merge_site_health(store, "notice", _section_health_entries(config, notice_scan, "站点公告采集失败"))
        return MonitorReport(
            started,
            time.time(),
            records,
            changed_events,
            errors,
            status_scan.records,
            status_scan.events,
            notice_scan.records,
            notice_scan.events,
            notice_scan.site_results,
            site_status,
        )
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
