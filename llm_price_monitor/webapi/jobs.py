"""采集任务体工厂：手动触发端点与后台调度器共用同一套任务实现与结果摘要。"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from llm_price_monitor.catalog import vendor_sources
from llm_price_monitor.catalog.general import is_general_llm
from llm_price_monitor.catalog.intros import attach_ai_intros
from llm_price_monitor.catalog.modelsdev import DEFAULT_PROVIDERS, fetch_catalogs
from llm_price_monitor.catalog.normalize import model_key
from llm_price_monitor.catalog.rankings import fetch_rankings
from llm_price_monitor.catalog.translate import attach_zh_descriptions, fingerprint_translations
from llm_price_monitor.config import MonitorConfig, config_from_store
from llm_price_monitor import tasklog
from llm_price_monitor.page_price import DEFAULT_TIMEOUT as PAGE_FETCH_TIMEOUT
from llm_price_monitor.report import scan_notices, scan_prices, scan_statuses, summary_row
from llm_price_monitor.store import Store

# 全量渠道目录每轮新增翻译的简介条数上限：条目数以千计，随每日刷新逐步补齐
ALL_CATALOG_TRANSLATE_BUDGET = 300

# 自动补模型的国内厂商每家保留数：国内定价页不标发布日期，按页面顺序（靠前即较新）
# 取前 N 个滚动，厂商发新模型时最旧的自动挤出
DOMESTIC_APPEND_LIMIT = 4


def auto_append_latest_models(store: Store, catalog: dict[str, Any]) -> list[str]:
    """目录刷新后把各厂商最新的通用模型补进 settings.monitor_models。

    白名单厂商（models.dev 官方 lab）按发布日期取最新一批（同日并列全收）；国内定价源
    厂商的条目没有发布日期，按目录插入序（即定价页顺序，靠前即较新）每家取前
    DOMESTIC_APPEND_LIMIT 个滚动。特殊领域模型（生图/音频/OCR 等）一律跳过
    （is_general_llm）；没标日期的 models.dev 条目不参与白名单线。
    手动移除过的模型记在 monitor_models_dismissed（settings 保存路由负责记录），这里跳过。
    模型名取条目的 model 字段（标准显示名）：dict 键是匹配用的归一化形式（去横线、
    casefold），拿键进清单界面上就丢横线。返回本次追加的模型名。
    """
    settings = store.get_document("settings") or {}
    monitor = [str(item) for item in settings.get("monitor_models") or [] if str(item).strip()]
    dismissed = {str(item) for item in settings.get("monitor_models_dismissed") or []}
    entries = catalog.get("models") if isinstance(catalog, dict) else None
    official_vendors = {vendor for _, vendor, _ in DEFAULT_PROVIDERS}
    latest: dict[str, str] = {}
    domestic_ranked: list[tuple[str, str]] = []  # 国内厂商 (vendor, model)，保持目录插入序
    for entry in (entries or {}).values():
        if not isinstance(entry, dict) or not is_general_llm(entry):
            continue
        vendor = str(entry.get("vendor") or "")
        model = str(entry.get("model") or "").strip()
        if not vendor or not model:
            continue
        released = str(entry.get("release_date") or "")
        if vendor in official_vendors:
            if released and (vendor not in latest or released > latest[vendor]):
                latest[vendor] = released
        elif not released:
            domestic_ranked.append((vendor, model))
    newest: set[str] = set()
    for entry in (entries or {}).values():
        if not isinstance(entry, dict) or not is_general_llm(entry):
            continue
        vendor = str(entry.get("vendor") or "")
        model = str(entry.get("model") or "").strip()
        if vendor in official_vendors and model and str(entry.get("release_date") or "") == latest.get(vendor):
            newest.add(model)
    per_vendor: dict[str, int] = {}  # 国内厂商已取个数：每家独立计数，互不挤占
    for vendor, model in domestic_ranked:
        if per_vendor.get(vendor, 0) >= DOMESTIC_APPEND_LIMIT:
            continue
        per_vendor[vendor] = per_vendor.get(vendor, 0) + 1
        newest.add(model)
    missing = sorted(newest - set(monitor) - dismissed)
    if not missing:
        return []
    store.set_document("settings", {**settings, "monitor_models": ordered_monitor_models([*monitor, *missing], catalog)})
    return missing


def price_scan_job(config: MonitorConfig, store: Store) -> Callable[[], dict[str, Any]]:
    """价格采集任务体：records 为 summary 行，与全量采集的摘要同构。"""
    def _run() -> dict[str, Any]:
        report = scan_prices(config, store=store)
        # 顺带执行保留清理：趋势点只留配置的天数；价格事件属于变更记录，永不清理
        cutoff = time.time() - config.settings.retention_price_days * 86400
        purged_points = store.purge_history(cutoff)
        if purged_points:
            tasklog.emit(f"保留清理：移除 {purged_points} 个过期趋势点")
        return {
            "records": [summary_row(row) for row in report.records],
            "events": [event["kind"] for event in report.events],
            "errors": report.errors,
            "persisted": True,
            # 逐站价格采集状态：需认证/无数据这类"没价但不算错误"的情况在这里
            "site_price_status": [
                {"site_id": site_id, "status": value.get("status"), "error": value.get("error")}
                for site_id, value in report.site_status.items()
            ],
        }

    return _run


def status_scan_job(config: MonitorConfig, store: Store) -> Callable[[], dict[str, Any]]:
    """渠道状态采集任务体：只拉 status.url，diff 变化写入状态事件。"""
    def _run() -> dict[str, Any]:
        scan = scan_statuses(config, store=store)
        # 顺带执行保留清理：状态时序只留配置的天数；状态事件永不清理
        cutoff = time.time() - config.settings.retention_status_days * 86400
        purged = store.purge_status(cutoff)
        if purged:
            tasklog.emit(f"保留清理：移除 {purged} 条过期状态记录")
        return {
            "records": scan.records,
            "events": [event["kind"] for event in scan.events],
            "errors": scan.errors,
            "persisted": True,
        }

    return _run


def notice_scan_job(config: MonitorConfig, store: Store) -> Callable[[], dict[str, Any]]:
    """站点公告采集任务体：正文变化记版本并发事件。"""
    def _run() -> dict[str, Any]:
        scan = scan_notices(config, store=store)
        return {
            "records": scan.records,
            "events": [event["kind"] for event in scan.events],
            "errors": scan.errors,
            "persisted": True,
        }

    return _run


def _release_timestamp(released: str) -> float | None:
    """目录发布日期（YYYY-MM-DD 或 YYYY-MM 月粒度）→ 时间戳；解析不了返回 None。"""
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return time.mktime(time.strptime(released, fmt))
        except ValueError:
            continue
    return None


def ordered_monitor_models(monitor: list[str], catalog: dict[str, Any]) -> list[str]:
    """监控清单按厂商分组排序：同厂商的模型挨在一起，不隔几家插一个。

    厂商顺序用目录里厂商首次出现的顺序（与前端下拉同一数据源）；厂商内发布日期
    倒序、同日期按名称，没日期的国内条目按目录插入序垫在该厂商后部，
    目录外自定义模型垫底。写清单的三处（自动补、清理、名字归正）统一走它。
    """
    entries = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(entries, dict) or not entries:
        return list(monitor)
    vendor_rank: dict[str, int] = {}
    catalog_index: dict[str, int] = {}
    # 显示名 → (目录键, 条目)：目录键是归一化形式，清单里存的是标准显示名，两边都要能查
    by_model: dict[str, tuple[str, dict[str, Any]]] = {}
    for i, (key, entry) in enumerate(entries.items()):
        catalog_index[key] = i
        if not isinstance(entry, dict):
            continue
        by_model[str(entry.get("model") or "")] = (key, entry)
        vendor = str(entry.get("vendor") or "")
        if vendor and vendor not in vendor_rank:
            vendor_rank[vendor] = len(vendor_rank)

    def sort_key(name: str):
        found = by_model.get(name)
        entry = (found or (None, None))[1] or entries.get(model_key(name))
        if not isinstance(entry, dict):
            return (1 << 30, 2, name)  # 目录外自定义模型垫底
        vendor = str(entry.get("vendor") or "")
        released = str(entry.get("release_date") or "")
        released_at = _release_timestamp(released) if released else None
        if released_at is not None:
            return (vendor_rank.get(vendor, 1 << 29), 0, -released_at, name)
        key = found[0] if found else model_key(name)
        return (vendor_rank.get(vendor, 1 << 29), 1, catalog_index.get(key, 1 << 30), name)

    return sorted(monitor, key=sort_key)


def prune_stale_monitor_models(store: Store, catalog: dict[str, Any], max_age_months: int) -> list[str]:
    """监控清单自动清理，两条规则：

    - 超期：官方目录里发布超过 max_age_months 的模型移出（只动有发布日期的条目，
      自定义模型不参与）；
    - 国内滚动：国内定价源厂商的条目没有发布日期，清单里滚出该厂商前
      DOMESTIC_APPEND_LIMIT 名的模型随之移出，与 auto_append 的「前 N 滚动」对称。

    移除不记 dismissed：超期模型不会是厂商最新发布、滚出模型不在前 N，自动补模型
    都不会再加回；用户想继续监控，在界面上手动加回即可。返回移除的模型名。
    """
    if max_age_months <= 0:
        return []
    settings = store.get_document("settings") or {}
    monitor = [str(item) for item in settings.get("monitor_models") or []]
    entries = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(entries, dict) or not entries or not monitor:
        return []
    official_vendors = {vendor for _, vendor, _ in DEFAULT_PROVIDERS}
    # 国内厂商的前 N 集合：按目录插入序（即定价页顺序，靠前即较新）数出来
    domestic_top: set[str] = set()
    per_vendor: dict[str, int] = {}
    for entry in entries.values():
        if not isinstance(entry, dict) or not is_general_llm(entry):
            continue
        vendor = str(entry.get("vendor") or "")
        if not vendor or vendor in official_vendors or str(entry.get("release_date") or ""):
            continue
        model = str(entry.get("model") or "").strip()
        if not model:
            continue
        if per_vendor.get(vendor, 0) >= DOMESTIC_APPEND_LIMIT:
            continue
        per_vendor[vendor] = per_vendor.get(vendor, 0) + 1
        domestic_top.add(model)
    cutoff = time.time() - max_age_months * 30 * 86400  # 时限按每月 30 天近似
    kept: list[str] = []
    removed: list[str] = []
    for name in monitor:
        entry = entries.get(model_key(name))
        released = str(entry.get("release_date") or "") if isinstance(entry, dict) else ""
        released_at = _release_timestamp(released) if released else None
        vendor = str(entry.get("vendor") or "") if isinstance(entry, dict) else ""
        if released_at is not None and released_at < cutoff:
            removed.append(name)  # 发布超过时限
        elif vendor and vendor not in official_vendors and released_at is None and name not in domestic_top:
            removed.append(name)  # 国内厂商条目滚出前 N
        else:
            kept.append(name)
    if not removed:
        return []
    store.set_document("settings", {**settings, "monitor_models": ordered_monitor_models(kept, catalog)})
    return removed


def canonicalize_monitor_models(store: Store, catalog: dict[str, Any]) -> list[str]:
    """目录刷新后把监控清单与移除名单里匹配到目录的名字换成标准显示名，返回「旧名 → 新名」记录。

    历史遗留：清单里存过目录的归一化键（去横线、casefold），按 model_key 对照条目
    换回标准显示名；匹配不到目录的（手动添加的自定义模型）保留原样。移除名单同步
    换名，否则删过的模型换名后不再被拦住，会被自动补模型加回来。
    """
    settings = store.get_document("settings") or {}
    monitor = [str(item) for item in settings.get("monitor_models") or []]
    dismissed = [str(item) for item in settings.get("monitor_models_dismissed") or []]
    entries = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(entries, dict) or not entries:
        return []

    renamed: list[str] = []

    def canonicalize(names: list[str]) -> list[str]:
        out: list[str] = []
        for name in names:
            stripped = name.strip()
            if not stripped:
                continue  # 空项没有监控意义，顺手清掉
            entry = entries.get(model_key(stripped))
            standard = str(entry.get("model") or "").strip() if isinstance(entry, dict) else ""
            if standard and standard != stripped:
                renamed.append(f"{stripped} → {standard}")
            final = standard or stripped
            if final not in out:
                out.append(final)
        return out

    monitor_new = canonicalize(monitor)
    dismissed_new = sorted(canonicalize(dismissed))
    if monitor_new == monitor and dismissed_new == dismissed:
        return renamed
    store.set_document(
        "settings",
        {**settings, "monitor_models": ordered_monitor_models(monitor_new, catalog), "monitor_models_dismissed": dismissed_new},
    )
    return renamed


def catalog_refresh_job(store: Store) -> Callable[[], dict[str, Any]]:
    """厂商定价同步任务体：一次拉取 models.dev 快照，合并厂商定价源，落两份目录。"""
    def _run() -> dict[str, Any]:
        try:
            monitor_config = config_from_store(store)
            ai_config = monitor_config.ai
            fetch_timeout = float(monitor_config.settings.timeout)
            max_age_months = int(monitor_config.settings.monitor_model_max_age_months)
        except ValueError:
            ai_config = None  # 配置缺失或非法：目录照常落盘，只是没有中文简介
            fetch_timeout = PAGE_FETCH_TIMEOUT
            max_age_months = 12
        tasklog.emit("开始刷新厂商定价：抓取 models.dev 快照")
        output, full = fetch_catalogs()
        providers = len({entry["vendor"] for entry in full["models"].values()})
        tasklog.emit(f"快照抓取完成：{providers} 个厂商，{len(full['models'])} 个模型")
        # 厂商定价源：先逐源抓取国内定价页（单源失败不中断，沿用上次缓存结果），
        # 再把国内价合并进官方目录——在翻译之前合并，新增条目也能拿到译文
        source_summary = vendor_sources.refresh_all_sources(store, timeout=fetch_timeout, ai_config=ai_config)
        sources_matched = sources_added = 0
        if source_summary["sources"]:
            tasklog.emit(
                f"厂商定价源抓取：{source_summary['ok']} 成功 / {source_summary['empty']} 空 / "
                f"{source_summary['failed']} 失败，共 {source_summary['models']} 个模型"
            )
            rate = float(output.get("usd_cny_rate") or 0)
            if rate > 0:
                sources_config = vendor_sources.load_sources(store)
                output, merge_summary = vendor_sources.merge_sources_into_catalog(output, sources_config, rate)
                sources_matched, sources_added = merge_summary["matched"], merge_summary["added"]
                if merge_summary["skipped"]:
                    tasklog.emit("定价源合并跳过：" + "；".join(merge_summary["skipped"]))
                # 国内渠道价补位进全量渠道目录（models.dev 有意不收国内渠道），一家一条可对比
                full, channel_summary = vendor_sources.merge_sources_into_channel_catalog(full, sources_config, rate)
                if channel_summary.get("skipped"):
                    tasklog.emit("全量渠道目录跳过：" + "；".join(channel_summary["skipped"]))
                if channel_summary["added"] or channel_summary["replaced"]:
                    tasklog.emit(
                        "全量渠道目录并入国内定价源："
                        f"新增 {channel_summary['added']}、覆盖 {channel_summary['replaced']}"
                    )
        previous = store.get_document("catalog")
        previous_all = store.get_document("catalog_all")
        # 两份目录的译文按简介指纹互济：官方目录翻过的全量渠道直接复用，反之亦然
        seed_official = {**fingerprint_translations(previous_all), **fingerprint_translations(previous)}
        translated = (
            attach_zh_descriptions(output, previous, ai_config, seed=seed_official)
            if ai_config is not None
            else 0
        )
        # 全量渠道目录仅供展示；简介翻译按轮次限额逐步补齐
        seed_all = {**seed_official, **fingerprint_translations(output)}
        translated_all = (
            attach_zh_descriptions(full, previous_all, ai_config, budget=ALL_CATALOG_TRANSLATE_BUDGET, seed=seed_all)
            if ai_config is not None
            else 0
        )
        if ai_config is not None:
            tasklog.emit(f"中文简介翻译 {translated + translated_all} 条")
        # models.dev 未收录的条目（国内定价页抓进来的）没有现成简介，按模型名 AI 补一句
        ai_intros = attach_ai_intros(output, previous, ai_config) if ai_config is not None else 0
        if ai_intros:
            tasklog.emit(f"AI 补简介 {ai_intros} 条（models.dev 未收录的模型）")
        store.set_document("catalog", output)
        store.set_document("catalog_all", full)
        tasklog.emit(f"厂商定价已更新：官方目录 {len(output['models'])} 个模型")
        # 先归正历史遗留的坏名字（旧版自动补模型存过归一化键），再按标准名补最新模型；
        # 清理在补模型之前跑：本轮刚补进来的新模型不会被同一轮的老化清理误伤
        renamed = canonicalize_monitor_models(store, output)
        if renamed:
            tasklog.emit("监控清单名字按目录修正：" + "；".join(renamed))
        stale_removed = prune_stale_monitor_models(store, output, max_age_months)
        if stale_removed:
            tasklog.emit(f"监控清单超期清理（发布超过 {max_age_months} 个月）：{'、'.join(stale_removed)}")
        auto_appended = auto_append_latest_models(store, output)
        if auto_appended:
            tasklog.emit(f"自动新增监控模型（各厂商最新发布）：{'、'.join(auto_appended)}")
        # 清单维护完做一次数据对账：不再监控的模型（手动删/超期/滚出前 N/清单外遗留）
        # 的价格快照、趋势点与事件一并清掉，数据列表不再出现"早已不存在"的幽灵模型
        monitor_now = store.get_document("settings").get("monitor_models") or []
        purged_rows = store.purge_price_models({str(item) for item in monitor_now})
        if purged_rows:
            tasklog.emit(f"清理不再监控的模型数据：{purged_rows} 条价格快照")
        return {
            "models_total": len(output["models"]),
            "models_found": sum(1 for entry in output["models"].values() if entry.get("found")),
            "zh_translated": translated,
            "ai_intros": ai_intros,
            "all_providers": providers,
            "all_models_total": len(full["models"]),
            "all_zh_translated": translated_all,
            "source_models": source_summary["models"] if source_summary["sources"] else 0,
            "source_failed": source_summary["failed"],
            "source_matched": sources_matched,
            "source_added": sources_added,
        }

    return _run


def vendor_source_refresh_job(store: Store, vendor: str) -> Callable[[], dict[str, Any]]:
    """单厂商定价源抓取任务体：抓页、更新源文档、就地重合并官方价目录。"""
    def _run() -> dict[str, Any]:
        tasklog.emit(f"抓取厂商定价源：{vendor}")
        try:
            monitor_config = config_from_store(store)
            ai_config = monitor_config.ai
            fetch_timeout = float(monitor_config.settings.timeout)
        except ValueError:
            ai_config = None
            fetch_timeout = PAGE_FETCH_TIMEOUT
        summary = vendor_sources.refresh_and_merge(store, vendor, timeout=fetch_timeout, ai_config=ai_config)
        tasklog.emit(
            f"厂商定价源 {vendor}：{summary.get('status')}，{summary.get('model_count')} 个模型"
            + (f"（{summary.get('error')}）" if summary.get("error") else "")
        )
        return summary

    return _run


def rankings_refresh_job(store: Store) -> Callable[[], dict[str, Any]]:
    """AA 模型榜单同步任务体：抓页解析，整体落 rankings 文档。"""
    def _run() -> dict[str, Any]:
        tasklog.emit("开始刷新模型榜单：抓取 Artificial Analysis 榜单页")
        doc = fetch_rankings()
        models = doc["models"]
        tasklog.emit(f"榜单解析完成：{len(models)} 个模型，覆盖 {len({e['creator'] for e in models if e['creator']})} 个厂商")
        store.set_document("rankings", doc)
        tasklog.emit(f"模型榜单已更新：第 1 名 {models[0]['name']}")
        return {"models_total": len(models), "generated_at": doc["generated_at"]}

    return _run
