"""采集任务体工厂：手动触发端点与后台调度器共用同一套任务实现与结果摘要。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from llm_price_monitor.catalog.classify import attach_ai_tiers
from llm_price_monitor.catalog.modelsdev import fetch_catalogs
from llm_price_monitor.catalog.translate import attach_zh_descriptions
from llm_price_monitor.config import MonitorConfig, config_from_store
from llm_price_monitor.report import scan_notices, scan_prices, scan_statuses, summary_row
from llm_price_monitor.store import Store

# 全量渠道目录每轮新增翻译的简介条数上限：条目数以千计，随每日刷新逐步补齐
ALL_CATALOG_TRANSLATE_BUDGET = 300


def price_scan_job(config: MonitorConfig, store: Store) -> Callable[[], dict[str, Any]]:
    """价格采集任务体：records 为 summary 行，与全量采集的摘要同构。"""
    def _run() -> dict[str, Any]:
        report = scan_prices(config, store=store)
        return {
            "records": [summary_row(row) for row in report.records],
            "events": [event["kind"] for event in report.events],
            "errors": report.errors,
            "persisted": True,
        }

    return _run


def status_scan_job(config: MonitorConfig, store: Store) -> Callable[[], dict[str, Any]]:
    """渠道状态采集任务体：只拉 status.url，diff 变化写入状态事件。"""
    def _run() -> dict[str, Any]:
        scan = scan_statuses(config, store=store)
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
        output, full = fetch_catalogs()
        previous = store.get_document("catalog")
        previous_all = store.get_document("catalog_all")
        try:
            ai_config = config_from_store(store).ai
        except ValueError:
            ai_config = None  # 配置缺失或非法：目录照常落盘，只是没有 AI 档位与中文简介
        classified = attach_ai_tiers(output, previous, ai_config) if ai_config is not None else 0
        translated = attach_zh_descriptions(output, previous, ai_config) if ai_config is not None else 0
        # 全量渠道目录仅供展示，不做 AI 档位判定；简介翻译按轮次限额逐步补齐
        translated_all = (
            attach_zh_descriptions(full, previous_all, ai_config, budget=ALL_CATALOG_TRANSLATE_BUDGET)
            if ai_config is not None
            else 0
        )
        store.set_document("catalog", output)
        store.set_document("catalog_all", full)
        return {
            "models_total": len(output["models"]),
            "models_found": sum(1 for entry in output["models"].values() if entry.get("found")),
            "ai_classified": classified,
            "zh_translated": translated,
            "all_providers": len({entry["vendor"] for entry in full["models"].values()}),
            "all_models_total": len(full["models"]),
            "all_zh_translated": translated_all,
        }

    return _run
