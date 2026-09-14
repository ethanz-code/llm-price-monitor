"""进程内后台任务注册表：采集与官方价抓取耗时较长，提交到线程执行并轮询状态。

任务记录连同过程日志经 attach_store 持久化到 SQLite 文档表，服务重启后历史仍可查看；
重启加载时把中断的 running 任务统一标记为失败。
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from llm_price_monitor import tasklog

# 持久化的任务条数与单任务日志行数默认上限：可在系统设置里用 max_task_runs / max_task_log_lines 覆盖；
# max_task_runs 是清理界限，超出即淘汰最旧任务
DEFAULT_MAX_TASK_RUNS = 100
DEFAULT_MAX_LOG_LINES = 500

_lock = threading.Lock()
_tasks: dict[str, dict[str, Any]] = {}
_RUNNING_KINDS: dict[str, str] = {}
_store: Any = None


def _limits() -> tuple[int, int]:
    """当前生效的任务条数与日志行数上限：读 store 里的系统设置，未配置时回默认值。"""
    store = _store
    if store is None:
        return DEFAULT_MAX_TASK_RUNS, DEFAULT_MAX_LOG_LINES
    raw = store.get_document("settings") or {}
    max_runs = raw.get("max_task_runs")
    max_logs = raw.get("max_task_log_lines")
    runs = max_runs if isinstance(max_runs, int) and not isinstance(max_runs, bool) and max_runs >= 1 else DEFAULT_MAX_TASK_RUNS
    logs = max_logs if isinstance(max_logs, int) and not isinstance(max_logs, bool) and max_logs >= 1 else DEFAULT_MAX_LOG_LINES
    return runs, logs

# 全量采集与三类拆分采集写同一份价格快照/状态时序，彼此互斥（读-改-写不能并发）；
# catalog-refresh 只写独立文档，不参与这组互斥
COLLECT_KINDS = frozenset({"collect", "collect-test", "collect-price", "collect-status", "collect-notice"})


def _conflict_of(kind: str) -> str | None:
    """返回与 kind 冲突的运行中任务 id；全量与拆分采集之间跨 kind 互斥。"""
    if kind in _RUNNING_KINDS:
        return _RUNNING_KINDS[kind]
    if kind in COLLECT_KINDS:
        return next((_RUNNING_KINDS[k] for k in COLLECT_KINDS if k in _RUNNING_KINDS), None)
    return None


def attach_store(store: Any) -> None:
    """接入持久化并加载历史任务；create_app 组装时调用一次。"""
    global _store
    _store = store
    runs = (store.get_document("tasks") or {}).get("runs", [])
    marked_failed = False
    with _lock:
        for entry in runs:
            if entry["status"] == "running":
                entry.update(status="failed", error="服务重启，任务中断", finished_at=time.time())
                marked_failed = True
            entry.setdefault("logs", [])
            _tasks[entry["id"]] = entry
    if marked_failed:
        _persist()


def _persist() -> None:
    """把最近的任务记录（含日志）写入存储；未接入存储时跳过。"""
    store = _store
    if store is None:
        return
    with _lock:
        max_runs, _ = _limits()
        items = sorted(_tasks.values(), key=lambda item: str(item["started_at"]), reverse=True)
        snapshot = [dict(item, logs=list(item["logs"])) for item in items[:max_runs]]
    try:
        store.set_document("tasks", {"runs": snapshot})
    except Exception:  # 落盘失败不阻断采集：日志属辅助信息，任务状态仍在内存可用
        pass


def _append_log(task_id: str, message: str, level: str) -> None:
    with _lock:
        entry = _tasks.get(task_id)
        if entry is None:
            return
        _, max_lines = _limits()
        logs = entry["logs"]
        if len(logs) >= max_lines and not entry.get("rolled"):
            # 滚动窗口前先插一条提示，避免长任务后半段日志静默丢失却无迹可查
            logs.append({"time": time.time(), "message": f"日志超过 {max_lines} 条，最早的记录将被滚动覆盖", "level": "warn"})
            entry["rolled"] = True
        logs.append({"time": time.time(), "message": message, "level": level})
        while len(logs) > max_lines:
            logs.pop(0)
    _persist()


def submit(kind: str, fn: Callable[[], Any]) -> str:
    """提交后台任务并立即返回 task_id；同一 kind 同时只允许一个运行中的任务。"""
    with _lock:
        conflict = _conflict_of(kind)
        if conflict:
            raise RuntimeError(f"已有运行中的 {kind} 任务: {conflict}")
        task_id = uuid.uuid4().hex
        _tasks[task_id] = {
            "id": task_id,
            "kind": kind,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
            "logs": [],
        }
        _RUNNING_KINDS[kind] = task_id
    _persist()

    def _write_log(message: str, level: str) -> None:
        _append_log(task_id, message, level)

    def _run() -> None:
        tasklog.bind(_write_log)
        try:
            result = fn()
        except Exception as exc:  # 后台线程兜底：任何异常都落到任务状态里供前端展示
            _append_log(task_id, f"任务失败：{exc}", "error")
            with _lock:
                # reset() 可能已把任务清出注册表（测试清场）；条目不存在时静默退出
                entry = _tasks.get(task_id)
                if entry is not None:
                    entry.update(status="failed", error=str(exc), finished_at=time.time())
                _RUNNING_KINDS.pop(kind, None)
            _persist()
        else:
            with _lock:
                entry = _tasks.get(task_id)
                if entry is not None:
                    entry.update(status="done", result=result, finished_at=time.time())
                _RUNNING_KINDS.pop(kind, None)
            _persist()
        finally:
            tasklog.unbind()

    threading.Thread(target=_run, daemon=True, name=f"webapi-{kind}-{task_id[:8]}").start()
    return task_id


def is_running(kind: str) -> bool:
    """该 kind（或与其互斥的采集类任务）是否有运行中实例；调度器据此顺延一轮。"""
    with _lock:
        return _conflict_of(kind) is not None


def get(task_id: str) -> dict[str, Any] | None:
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return None
        # logs 是后台线程滚动维护的列表，浅拷贝外层后需再拷一层，避免轮询序列化时与写线程竞态
        snapshot = dict(task)
        snapshot["logs"] = list(task.get("logs") or [])
        return snapshot


def reset() -> None:
    """清空任务注册表并解除持久化（仅测试使用）：注册表进程内共享，测试间需要显式清场。"""
    global _store
    with _lock:
        _tasks.clear()
        _RUNNING_KINDS.clear()
    _store = None


def recent(limit: int = 100) -> list[dict[str, Any]]:
    """最近的任务列表（新任务在前）；日志不随列表下发，只附带错误摘要供列表行提示。"""
    with _lock:
        items = sorted(_tasks.values(), key=lambda item: str(item["started_at"]), reverse=True)
        rows = []
        for item in items[:limit]:
            errors = [log for log in item["logs"] if log.get("level") == "error"]
            rows.append(
                {key: value for key, value in item.items() if key != "logs"}
                | {"log_count": len(item["logs"]), "error_count": len(errors), "error_summary": errors[-1]["message"] if errors else None}
            )
        return rows


# 概览页“采集异常”卡片的清除标记：被移除的单条日志键与“清空”时间点，
# 只影响该卡片的展示，不改动任务本体日志
_DISMISS_DOC = "task_error_stream"
_DISMISS_KEEP = 1000


def _dismiss_state() -> dict[str, Any]:
    store = _store
    if store is None:
        return {}
    try:
        return store.get_document(_DISMISS_DOC) or {}
    except Exception:
        return {}


def _error_key(task_id: str, log: dict[str, Any]) -> str:
    raw = f"{task_id}|{log.get('time')}|{log.get('level')}|{log.get('message')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def error_stream(limit: int = 200) -> list[dict[str, Any]]:
    """跨任务汇总 warn/error 日志（新在前），滤掉已移除/清空时间点之前的条目，供概览页异常卡片展示。"""
    state = _dismiss_state()
    cleared_at = state.get("cleared_at")
    dismissed = set(state.get("dismissed", []))
    with _lock:
        items = sorted(_tasks.values(), key=lambda item: str(item["started_at"]), reverse=True)
        entries = []
        for item in items:
            for log in item.get("logs", []):
                if log.get("level") not in ("warn", "error"):
                    continue
                if cleared_at is not None and float(log.get("time") or 0) <= float(cleared_at):
                    continue
                key = _error_key(item["id"], log)
                if key in dismissed:
                    continue
                entries.append(
                    {
                        "key": key,
                        "task_id": item["id"],
                        "kind": item["kind"],
                        "time": log.get("time"),
                        "level": log.get("level"),
                        "message": log.get("message"),
                    }
                )
        entries.sort(key=lambda entry: float(entry["time"] or 0), reverse=True)
        return entries[:limit]


def dismiss_error(key: str) -> bool:
    """移除异常卡片里的单条日志：只记入清除标记，任务本体日志保持不动。"""
    store = _store
    if store is None:
        return False
    state = _dismiss_state()
    dismissed = list(state.get("dismissed", []))
    if key not in dismissed:
        dismissed.append(key)
    state["dismissed"] = dismissed[-_DISMISS_KEEP:]
    store.set_document(_DISMISS_DOC, state)
    return True


def clear_errors() -> int:
    """清空异常卡片：以当前时间为界隐藏此前的日志，返回剩余可见条数。"""
    store = _store
    if store is None:
        return 0
    store.set_document(_DISMISS_DOC, {"cleared_at": time.time(), "dismissed": []})
    return len(error_stream())
