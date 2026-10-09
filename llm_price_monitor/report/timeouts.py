"""单站采集的硬上限保护：daemon 子线程跑采集，超时放弃该站不让整轮被拖死。"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from llm_price_monitor import tasklog
from llm_price_monitor.config import PriceMonitorError
from llm_price_monitor.tracker import PriceRecord

# 运行时经包命名空间回查：tests 对 `llm_price_monitor.report.SITE_HARD_TIMEOUT_SECONDS`
# 的 monkeypatch 必须对本模块的读取生效（与 store/_MAX_ROW_LIMIT 同一套路）。
from llm_price_monitor import report as _report_pkg  # noqa: F401

# 单站采集的硬上限（秒）：适配器内有 per-request 超时，但实测经本地代理的半死连接
# 会在 SSL 抖动重试后无限挂起、超时不触发——单站卡死不能拖垮整轮和后续站点的落库
SITE_HARD_TIMEOUT_SECONDS = 900.0

# 站点网络阶段并行度：站点彼此独立，串行时一个站卡满硬上限整轮跟着等；
# 4 路并发下最坏耗时 ≈ ⌈站数/4⌉ × 硬上限。共享 httpx.Client 线程安全，
# 快照比对与落库仍在主线程按站点顺序串行，不改数据语义。
SITE_FETCH_CONCURRENCY = 4
_FETCH_SLOTS = threading.BoundedSemaphore(SITE_FETCH_CONCURRENCY)


class _NetworkPhaseHandle:
    """已开工的单站采集：线程即起、信号量限流，result() 从实际开工时刻计硬上限。"""

    def __init__(self, phase: Callable[[], tuple[list[PriceRecord], list[str]]], site_id: str) -> None:
        self._site_id = site_id
        self._box: dict[str, Any] = {}
        self._started_at: float | None = None
        sink = tasklog.current_sink()

        def _run() -> None:
            if sink is not None:
                tasklog.bind(sink)
            with _FETCH_SLOTS:
                self._started_at = time.monotonic()
                try:
                    self._box["result"] = phase()
                except BaseException as exc:  # 采集线程的异常转交主线程按原语义处理
                    self._box["error"] = exc

        self._worker = threading.Thread(target=_run, name=f"price-scan-{site_id}", daemon=True)
        self._worker.start()

    def result(self) -> tuple[list[PriceRecord], list[str]]:
        """等该站采完：排队等槽位不计入硬上限，开工后超硬上限仍未返回就放弃该站。"""
        hard_timeout = _report_pkg.SITE_HARD_TIMEOUT_SECONDS
        while self._worker.is_alive():
            now = time.monotonic()
            if self._started_at is not None and now - self._started_at >= hard_timeout:
                message = f"采集超过 {hard_timeout:g}s 硬上限仍未返回，本轮放弃该站（连接可能卡死在代理隧道上）"
                tasklog.emit(f"[{self._site_id}] {message}", "error")
                return [], [message]
            self._worker.join(0.2)
        if "error" in self._box:
            raise self._box["error"]
        result = self._box.get("result")
        if isinstance(result, tuple) and len(result) == 2:
            return result
        return [], ["采集线程未返回有效结果"]


def start_network_phase(
    phase: Callable[[], tuple[list[PriceRecord], list[str]]], site_id: str
) -> _NetworkPhaseHandle:
    """立即并行开工一个站点的采集；调用方稍后对句柄 result() 取结果并落库。"""
    return _NetworkPhaseHandle(phase, site_id)


def _run_network_phase(
    phase: Callable[[], tuple[list[PriceRecord], list[str]]], site_id: str
) -> tuple[list[PriceRecord], list[str]]:
    """单站采集并等结果（串行入口）：在 daemon 子线程里跑（含续签重采），
    到硬上限仍未返回就放弃该站。线程杀不掉：超时后工作线程变孤儿，仍占着那条
    挂死的连接，但它只采不写库、不阻塞后续站点，daemon 属性也不挡进程退出。"""
    return start_network_phase(phase, site_id).result()


def _run_site_fetch(fetch: Callable[[], Any], site_id: str) -> Any:
    """单站状态/公告请求的硬上限兜底：与价格腿同因，半死代理连接上 per-request
    超时可能不触发，此前这两条腿没有保护，单站挂死会把整轮任务无限拖住。
    超时按 PriceMonitorError 抛出，走各扫描段已有的失败落账路径。"""
    hard_timeout = _report_pkg.SITE_HARD_TIMEOUT_SECONDS
    sink = tasklog.current_sink()
    box: dict[str, Any] = {}

    def _run() -> None:
        if sink is not None:
            tasklog.bind(sink)
        try:
            box["result"] = fetch()
        except BaseException as exc:  # 采集线程的异常转交主线程按原语义处理
            box["error"] = exc

    worker = threading.Thread(target=_run, name=f"site-fetch-{site_id}", daemon=True)
    worker.start()
    worker.join(hard_timeout)
    if worker.is_alive():
        raise PriceMonitorError(
            f"采集超过 {hard_timeout:g}s 硬上限仍未返回，本轮放弃（连接可能卡死在代理隧道上）"
        )
    if "error" in box:
        raise box["error"]
    return box.get("result")
