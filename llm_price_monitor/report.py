"""运行编排与报告：监控运行、事件分类、持久化、汇总输出。

采集拆成三类独立扫描：价格（scan_prices）、渠道状态（scan_statuses）、站点公告（scan_notices），
可分别按各自周期定时执行；run_once 是全量入口，共享一个连接串行跑三类。
每类扫描：选 UA → 逐站点采集 → 与上次快照比对生成事件 → 写历史/快照 → 输出摘要。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

from llm_price_monitor import tasklog
from llm_price_monitor.adapters import ADAPTERS
from llm_price_monitor.config import ChangeKind, MonitorConfig, PriceMonitorError, SiteSpec
from llm_price_monitor.tracker import PriceRecord
from llm_price_monitor.units import round2, tier_unit_per_1m
from llm_price_monitor.useragent import choose_user_agent
from llm_price_monitor.catalog import discount as catalog_discount, fx as catalog_fx
from llm_price_monitor.notice import fetch_site_notice
from llm_price_monitor.status import diff_status, fetch_site_status
from llm_price_monitor.store import Store
from llm_price_monitor.token_refresh import needs_refresh, refresh_and_recollect
from llm_price_monitor import wxpusher


def _push_notifications(
    config: MonitorConfig,
    *,
    price_events: list[dict[str, Any]] | None = None,
    status_events: list[dict[str, Any]] | None = None,
    notice_events: list[dict[str, Any]] | None = None,
) -> None:
    """变化事件汇总推送到 WxPusher；未配置 appToken 或推送失败只记日志，不影响采集结果。"""
    if not config.settings.wxpusher_app_token:
        return
    try:
        wxpusher.send_change_digest(
            app_token=config.settings.wxpusher_app_token,
            uid=config.settings.wxpusher_uid,
            price_events=price_events or [],
            status_events=status_events or [],
            notice_events=notice_events or [],
        )
    except Exception as exc:  # 推送是旁路能力，失败不能让采集任务标失败
        tasklog.emit(f"WxPusher 推送失败：{exc}", "error")


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


def record_dict(site_id: str, record: PriceRecord) -> dict[str, Any]:
    return {"site_id": site_id, **asdict(record)}


def fingerprint(value: dict[str, Any]) -> str:
    """价格口径指纹：只含价格相关字段，用于判定"价格是否真的变了"。

    AI 抽取每次输出的上下文边界、备注文本、证据引文都会漂移，不参与指纹；
    pricing_rules 里的 context 边界同理剔除。数值统一整值浮点归一（14.0 → 14），
    避免 int/float 表示差异被误判成变化。
    """

    def normalize(item: Any) -> Any:
        if isinstance(item, bool):
            return item
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, list):
            return [normalize(entry) for entry in item]
        if isinstance(item, dict):
            return {key: normalize(entry) for key, entry in item.items() if key not in ("context_min", "context_max")}
        return item

    comparable = {key: normalize(value.get(key)) for key in ("model", "input_price", "output_price", "unit", "price_status", "requires_auth")}
    metadata = value.get("metadata") or {}
    comparable["metadata"] = normalize({key: metadata.get(key) for key in (
        "pricing_kind", "model_ratio", "completion_ratio", "group_ratio", "billing_mode",
        "billing_expr", "pricing_rules", "group",
        "cache_read_price", "cache_create_price", "cache_create_1h_price",
    )})
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


def _merge_collect_status(store: Store, config: MonitorConfig, site_status: dict[str, dict[str, Any]]) -> None:
    """把本轮各站点的价格采集状态并入 collect_status：单站点采集不能冲掉其他站点的状态。"""
    previous_status = store.get_document("collect_status")
    merged_status = dict(previous_status) if isinstance(previous_status, dict) else {}
    for spec in config.sites:
        if spec.id in site_status:
            merged_status[spec.id] = site_status[spec.id]
        elif not spec.enabled:
            merged_status[spec.id] = {"status": "disabled", "error": None, "checked_at": None}
    store.set_document("collect_status", merged_status)


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


def _scan_prices(
    config: MonitorConfig,
    client: httpx.Client,
    user_agent: str,
    store: Store | None,
    latest: dict[str, dict[str, Any]],
    persist: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]], set[str]]:
    """价格采集主体：逐站点走适配器，与上次快照比对生成事件。

    返回 (records, history_rows, events, errors, site_status, removed_keys)；
    history_rows 只含本次真正取到价的记录——沿用上次价的占位行与无数据的跳过行都不写历史；
    removed_keys 是本轮分组下线从快照摘除的 key，需要从数据库显式删行。
    """
    records: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    site_status: dict[str, dict[str, Any]] = {}
    removed_keys: set[str] = set()
    enabled_count = sum(1 for spec in config.sites if spec.enabled)
    scan_started = time.time()
    tasklog.emit(f"开始价格采集：{enabled_count} 个站点")
    for spec in config.sites:
        if not spec.enabled:
            continue
        site_started = time.time()
        adapter = ADAPTERS.get(spec.adapter)
        if adapter is None:
            errors.append({"site_id": spec.id, "error": f"未知适配器: {spec.adapter}"})
            site_status[spec.id] = {"status": "error", "error": f"未知适配器: {spec.adapter}", "checked_at": time.time()}
            tasklog.emit(f"[{spec.id}] 价格采集失败：未知适配器 {spec.adapter}", "error")
            continue
        # 多地址站点：主地址 + networks 附加地址依次采集；同一模型重复命中时主地址优先
        def collect_all(site_spec: SiteSpec) -> tuple[list[PriceRecord], list[str]]:
            site_records: list[PriceRecord] = []
            site_by_key: dict[tuple[str | None, str | None], PriceRecord] = {}
            site_errors: list[str] = []
            for index, endpoint in enumerate([site_spec.network, *site_spec.networks]):
                endpoint_spec = replace(site_spec, network=endpoint, networks=())
                try:
                    endpoint_records = adapter.collect(endpoint_spec, client, config.settings.timeout, user_agent, config.ai)
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

        collected, collect_errors = collect_all(spec)
        # 有任一"需认证"且配了续签接口：续签 token、回写数据库并用新 token 重采一次
        if spec.token_refresh and needs_refresh(collected):
            tasklog.emit(f"[{spec.id}] 采集返回需认证，尝试续签 token…")
            collected, refresh_error = refresh_and_recollect(
                spec,
                collected,
                collect=collect_all,
                client=client,
                timeout=config.settings.timeout,
                user_agent=user_agent,
                store=store,
            )
            if refresh_error:
                collect_errors.append(refresh_error)
                tasklog.emit(f"[{spec.id}] {refresh_error}", "error")
            else:
                collect_errors = []
                tasklog.emit(f"[{spec.id}] token 已续签并重新采集：{len(collected)} 条价格")
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
        for record in collected:
            current = _backfill_rule_price(record_dict(spec.id, record))
            # 分组归一：metadata 缺失时兜底 default，保证事件键跨扫描稳定
            group = (record.metadata or {}).get("group") or "default"
            key = f"{spec.id}:{record.model}:{group}"
            site_keys.add(key)
            previous = latest.get(key)
            if not _has_price(current):
                if previous is None or not _has_price(previous):
                    # 本次没拿到数据、上次也没有可用价：不新增占位记录，避免快照与历史重复膨胀
                    continue
                # 上次有价、本次没拿到：沿用上次价格并标"需认证"，但不写历史（价格没有新观测）
                current = _carry_last_price(current, previous)
                latest[key] = current
                records.append(current)
                kind = classify(previous, current)
                events.append(asdict(PriceEvent(spec.id, record.model, kind, previous, current, time.time())))
                site_changed += kind != "unchanged"
                continue

            current["fingerprint"] = fingerprint(current)
            current["captured_at"] = record.captured_at
            records.append(current)
            # 指纹去重：价格口径与上一轮一致就不写历史行，趋势表只保留真实变化点
            if previous is None or previous.get("fingerprint") != current["fingerprint"]:
                history_rows.append(current)
            kind = classify(previous, current)
            events.append(asdict(PriceEvent(spec.id, record.model, kind, previous, current, time.time())))
            site_changed += kind != "unchanged"
            latest[key] = current
        # 出现"需认证"占位行（401/403）时是我方凭证问题，不代表分组真的下线，
        # 跳过缺失计数，避免 token 过期把分组刷成下线事件；
        # 测试采集（persist=False）同样不计数——不完整的测试轮次不得污染正式下线阈值
        if store is not None and persist and not needs_refresh(collected):
            removed_keys |= _detect_removed_groups(store, latest, spec.id, site_keys, events)
        tasklog.emit(f"[{spec.id}] 价格采集成功：{len(collected)} 条价格，{site_changed} 处变化，{time.time() - site_started:.1f}s")
    tasklog.emit(f"价格采集完成：{len(records)} 条记录，{len(errors)} 个错误，{time.time() - scan_started:.1f}s")
    return records, history_rows, events, errors, site_status, removed_keys


# 分组连续缺失这么多次才判定"下线"：容忍单轮抓取抖动，避免误报刷屏
GROUP_REMOVED_MISSES = 2


def _detect_removed_groups(
    store: Store, latest: dict[str, dict[str, Any]], site_id: str, seen_keys: set[str], events: list[dict[str, Any]]
) -> set[str]:
    """本轮采集成功的站点里，快照中存在但本轮没出现的分组视为一次缺失：
    连续 GROUP_REMOVED_MISSES 次缺失记 group_removed 事件并从快照摘除；中途恢复则清零。
    缺失计数持久化到 group_miss 文档，跨轮次累计；返回本轮摘除的快照 key。"""
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
            model = key[len(site_prefix):].rsplit(":", 1)[0]
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
) -> None:
    """写回最新快照；replace_latest 只做 upsert，分组下线摘除的 key 需要显式删行。
    顺带清理历史遗留的无价占位行——现行采集不再产出占位记录，
    快照里残留的无价行都是旧版本（或旧库）留下的，保留只会在定价页造成同模型重复。"""
    stale_keys = [key for key, row in latest.items() if isinstance(row, dict) and not _has_price(row)]
    if stale_keys:
        for key in stale_keys:
            del latest[key]
        store.remove_latest(stale_keys)
    if removed_keys:
        store.remove_latest(sorted(removed_keys))
    # 增量写入：与库中现有快照比对，内容没变的行不重写
    existing = store.latest_all()
    changed = {key: row for key, row in latest.items() if existing.get(key) != row}
    store.replace_latest(changed)


def _scan_statuses(
    config: MonitorConfig, client: httpx.Client, user_agent: str, store: Store | None
) -> SectionScan:
    """渠道状态采集：只处理配置了 status.url 的站点；与上次记录 diff，变化写入 status 事件。"""
    scan = SectionScan()
    scan_started = time.time()
    tasklog.emit("开始渠道状态采集")
    for spec in config.sites:
        if not spec.enabled or not spec.status.get("url"):
            continue
        try:
            status_record = fetch_site_status(spec, client, config.settings.timeout, user_agent, config.ai)
        except (PriceMonitorError, httpx.HTTPError, ValueError) as exc:
            scan.errors.append({"site_id": spec.id, "error": f"渠道状态采集失败: {exc}"})
            tasklog.emit(f"[{spec.id}] 渠道状态采集失败：{exc}", "error")
            continue
        scan.records.append(status_record)
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
    client = client or httpx.Client(follow_redirects=True)
    latest = store.latest_all() if store is not None else {}
    try:
        records, history_rows, events, errors, site_status, removed_keys = _scan_prices(config, client, selected_user_agent, store, latest, persist)
        # price_status 在确认/规则/无数据之间抖动不代表价格真的变了，这类事件不落库
        changed_events = [event for event in events if event["kind"] not in ("unchanged", "status_changed")]
        if persist and store is not None:
            store.append_history(history_rows)
            store.append_events(changed_events)
            _persist_latest(store, latest, removed_keys=removed_keys)
            _merge_collect_status(store, config, site_status)
            _push_notifications(config, price_events=changed_events)
        return MonitorReport(started, time.time(), records, changed_events, errors)
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
    client = client or httpx.Client(follow_redirects=True)
    try:
        scan = _scan_statuses(config, client, selected_user_agent, store)
        if persist and store is not None:
            store.append_status_records(scan.records)
            store.append_status_events(scan.events)
            _push_notifications(config, status_events=scan.events)
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
    client = client or httpx.Client(follow_redirects=True)
    try:
        scan = _scan_notices(config, client, selected_user_agent, store)
        if persist and store is not None:
            store.append_notice_records(scan.records)
            store.append_notice_events(scan.events)
            _push_notifications(config, notice_events=scan.events)
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
    client = client or httpx.Client(follow_redirects=True)
    latest = store.latest_all() if store is not None else {}
    try:
        records, history_rows, events, errors, site_status, removed_keys = _scan_prices(config, client, selected_user_agent, store, latest, persist)
        status_scan = _scan_statuses(config, client, selected_user_agent, store)
        notice_scan = _scan_notices(config, client, selected_user_agent, store)
        errors = [*errors, *status_scan.errors, *notice_scan.errors]
        changed_events = [event for event in events if event["kind"] not in ("unchanged", "status_changed")]
        if persist and store is not None:
            store.append_history(history_rows)
            store.append_events(changed_events)
            _persist_latest(store, latest, removed_keys=removed_keys)
            store.append_status_records(status_scan.records)
            store.append_status_events(status_scan.events)
            store.append_notice_records(notice_scan.records)
            store.append_notice_events(notice_scan.events)
            _merge_collect_status(store, config, site_status)
            _push_notifications(config, price_events=changed_events, status_events=status_scan.events, notice_events=notice_scan.events)
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
        "status_reason": metadata.get("error") or metadata.get("notes") or None,
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
