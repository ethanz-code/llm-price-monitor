"""采集任务体工厂：手动触发端点与后台调度器共用同一套任务实现与结果摘要。"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from llm_price_monitor.catalog.classify import attach_ai_tiers
from llm_price_monitor.catalog.modelsdev import fetch_catalogs
from llm_price_monitor.catalog.translate import attach_zh_descriptions, fingerprint_translations
from llm_price_monitor.config import MonitorConfig, config_from_store
from llm_price_monitor import tasklog
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


def catalog_refresh_job(store: Store) -> Callable[[], dict[str, Any]]:
    """厂商定价同步任务体：一次拉取 models.dev 快照，落官方价与全量渠道价两份目录。"""
    def _run() -> dict[str, Any]:
        tasklog.emit("开始刷新厂商定价：抓取 models.dev 快照")
        output, full = fetch_catalogs()
        providers = len({entry["vendor"] for entry in full["models"].values()})
        tasklog.emit(f"快照抓取完成：{providers} 个厂商，{len(full['models'])} 个模型")
        previous = store.get_document("catalog")
        previous_all = store.get_document("catalog_all")
        try:
            ai_config = config_from_store(store).ai
        except ValueError:
            ai_config = None  # 配置缺失或非法：目录照常落盘，只是没有 AI 档位与中文简介
        # 两份目录的译文按简介指纹互济：官方目录翻过的全量渠道直接复用，反之亦然
        seed_official = {**fingerprint_translations(previous_all), **fingerprint_translations(previous)}
        classified = attach_ai_tiers(output, previous, ai_config) if ai_config is not None else 0
        translated = (
            attach_zh_descriptions(output, previous, ai_config, seed=seed_official)
            if ai_config is not None
            else 0
        )
        # 全量渠道目录仅供展示，不做 AI 档位判定；简介翻译按轮次限额逐步补齐
        seed_all = {**seed_official, **fingerprint_translations(output)}
        translated_all = (
            attach_zh_descriptions(full, previous_all, ai_config, budget=ALL_CATALOG_TRANSLATE_BUDGET, seed=seed_all)
            if ai_config is not None
            else 0
        )
        if ai_config is not None:
            tasklog.emit(f"AI 档位判定 {classified} 条，中文简介翻译 {translated + translated_all} 条")
        store.set_document("catalog", output)
        store.set_document("catalog_all", full)
        tasklog.emit(f"厂商定价已更新：官方目录 {len(output['models'])} 个模型")
        return {
            "models_total": len(output["models"]),
            "models_found": sum(1 for entry in output["models"].values() if entry.get("found")),
            "ai_classified": classified,
            "zh_translated": translated,
            "all_providers": providers,
            "all_models_total": len(full["models"]),
            "all_zh_translated": translated_all,
        }

    return _run
