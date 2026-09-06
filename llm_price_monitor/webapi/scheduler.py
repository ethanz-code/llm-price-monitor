"""统一后台调度器：价格 / 渠道状态 / 站点公告 / 厂商定价四项定时采集。

间隔读自设置文档 settings.schedule（分钟，0 = 关闭该项定时），常驻线程每
CHECK_INTERVAL_SECONDS 检查一轮：改配置下一轮即生效；到点且对应任务无运行中
实例时提交任务注册表，正忙或提交失败等下一轮。上次提交时间持久化到
schedule_state 文档，重启后按真实间隔计算，不会重启即全量打站点。
"""
from __future__ import annotations

import threading
import time

from llm_price_monitor.config import schedule_from_raw
from llm_price_monitor.store import Store
from llm_price_monitor.webapi import tasks
from llm_price_monitor.webapi.deps import load_config
from llm_price_monitor.webapi.jobs import catalog_refresh_job, notice_scan_job, price_scan_job, status_scan_job

CHECK_INTERVAL_SECONDS = 30

_STOP = threading.Event()


def stop_scheduler() -> None:
    """请求调度线程退出（幂等）；下一次 start_scheduler 会自动复位。测试 teardown 用它确保不留后台线程。"""
    _STOP.set()

# 调度项 → 任务注册表 kind；任务体在到点时现取最新配置
_KIND_BY_KEY = {
    "price": "collect-price",
    "status": "collect-status",
    "notice": "collect-notice",
    "catalog": "catalog-refresh",
}


def _job_for(key: str, store: Store):
    if key == "price":
        return price_scan_job(load_config(store), store)
    if key == "status":
        return status_scan_job(load_config(store), store)
    if key == "notice":
        return notice_scan_job(load_config(store), store)
    return catalog_refresh_job(store)


def _run_due(store: Store) -> None:
    """跑一轮到期检查：提交所有已到期的调度项，并把提交时间记入 schedule_state。"""
    settings = store.get_document("settings") or {}
    schedule = schedule_from_raw(settings.get("schedule"))
    state = dict(store.get_document("schedule_state") or {})
    now = time.time()
    changed = False
    for key, minutes in schedule.items():
        if minutes <= 0:
            continue  # 0 = 关闭该项定时，只保留手动触发
        kind = _KIND_BY_KEY[key]
        if tasks.is_running(kind):
            continue  # 手动触发或上一轮任务还在跑，顺延到下一轮
        if now - float(state.get(key) or 0) < minutes * 60:
            continue
        try:
            tasks.submit(kind, _job_for(key, store))
        except RuntimeError:
            continue
        state[key] = now
        changed = True
    if changed:
        store.set_document("schedule_state", state)


def start_scheduler(store: Store) -> None:
    """启动常驻调度线程（应用创建时调用一次）；线程内兜底所有异常，保证不死掉。"""
    _STOP.clear()

    def _loop() -> None:
        while not _STOP.is_set():
            try:
                _run_due(store)
            except Exception:
                pass  # 坏配置、存储异常等下一轮再试
            _STOP.wait(CHECK_INTERVAL_SECONDS)  # wait 而非 sleep：stop 时立即退出，不拖到间隔结束

    threading.Thread(target=_loop, daemon=True, name="webapi-scheduler").start()
