"""采集任务体工厂：手动触发端点与后台调度器共用同一套任务实现与结果摘要。"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from llm_price_monitor.catalog import vendor_sources
from llm_price_monitor.catalog.intros import attach_ai_intros
from llm_price_monitor.catalog.modelsdev import fetch_catalogs
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


def auto_append_latest_models(store: Store, catalog: dict[str, Any]) -> list[str]:
    """目录刷新后把各厂商最新发布的模型补进 settings.monitor_models。

    每个厂商取发布日期最新的一批（同日并列全收），没标日期的条目不参与；
    手动移除过的模型记在 monitor_models_dismissed（settings 保存路由负责记录），这里跳过。
    模型名取条目的 model 字段（标准显示名）：dict 键是匹配用的归一化形式（去横线、
    casefold），拿键进清单界面上就丢横线。返回本次追加的模型名。
    """
    settings = store.get_document("settings") or {}
    monitor = [str(item) for item in settings.get("monitor_models") or [] if str(item).strip()]
    dismissed = {str(item) for item in settings.get("monitor_models_dismissed") or []}
    entries = catalog.get("models") if isinstance(catalog, dict) else None
    latest: dict[str, str] = {}
    for entry in (entries or {}).values():
        if not isinstance(entry, dict):
            continue
        vendor = str(entry.get("vendor") or "")
        released = str(entry.get("release_date") or "")
        if vendor and released and (vendor not in latest or released > latest[vendor]):
            latest[vendor] = released
    newest: set[str] = set()
    for entry in (entries or {}).values():
        if not isinstance(entry, dict):
            continue
        vendor = str(entry.get("vendor") or "")
        model = str(entry.get("model") or "").strip()
        if vendor and model and str(entry.get("release_date") or "") == latest.get(vendor):
            newest.add(model)
    missing = sorted(newest - set(monitor) - dismissed)
    if not missing:
        return []
    store.set_document("settings", {**settings, "monitor_models": [*monitor, *missing]})
    return missing


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
        {**settings, "monitor_models": monitor_new, "monitor_models_dismissed": dismissed_new},
    )
    return renamed


def catalog_refresh_job(store: Store) -> Callable[[], dict[str, Any]]:
    """厂商定价同步任务体：一次拉取 models.dev 快照，合并厂商定价源，落两份目录。"""
    def _run() -> dict[str, Any]:
        try:
            monitor_config = config_from_store(store)
            ai_config = monitor_config.ai
            fetch_timeout = float(monitor_config.settings.timeout)
        except ValueError:
            ai_config = None  # 配置缺失或非法：目录照常落盘，只是没有中文简介
            fetch_timeout = PAGE_FETCH_TIMEOUT
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
        # 先归正历史遗留的坏名字（旧版自动补模型存过归一化键），再按标准名补最新模型
        renamed = canonicalize_monitor_models(store, output)
        if renamed:
            tasklog.emit("监控清单名字按目录修正：" + "；".join(renamed))
        auto_appended = auto_append_latest_models(store, output)
        if auto_appended:
            tasklog.emit(f"自动新增监控模型（各厂商最新发布）：{'、'.join(auto_appended)}")
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
