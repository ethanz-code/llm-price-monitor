"""运行编排入口：scan_prices / scan_statuses / scan_notices 独立入口与 run_once 全量编排。"""
from __future__ import annotations

import time

import httpx

from llm_price_monitor.config import MonitorConfig
from llm_price_monitor.http_retry import build_client
from llm_price_monitor.store import Store
from llm_price_monitor.useragent import choose_user_agent

from .events import MonitorReport, SectionScan
from .health import _merge_collect_status, _merge_price_health, _merge_site_health, _section_health_entries
from .persist import _persist_scan_results
from .scans import _scan_notices, _scan_prices, _scan_statuses


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
        records, events, errors, site_status = _scan_prices(config, client, selected_user_agent, store, latest, persist)
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
        records, events, errors, site_status = _scan_prices(config, client, selected_user_agent, store, latest, persist)
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
