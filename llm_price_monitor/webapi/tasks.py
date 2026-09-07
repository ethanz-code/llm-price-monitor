"""进程内后台任务注册表：采集与官方价抓取耗时较长，提交到线程执行并轮询状态。

任务记录连同过程日志经 attach_store 持久化到 SQLite 文档表，服务重启后历史仍可查看；
重启加载时把中断的 running 任务统一标记为失败。
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from llm_price_monitor import tasklog

# 持久化的任务条数与单任务日志行数上限：文档随任务累积，超出即淘汰最旧/截断
MAX_TASK_RUNS = 100
MAX_LOG_LINES = 500

_lock = threading.Lock()
_tasks: dict[str, dict[str, Any]] = {}
_RUNNING_KINDS: dict[str, str] = {}
_store: Any = None


def attach_store(store: Any) -> None:
    """接入持久化并加载历史任务；create_app 组装时调用一次。"""
    global _store
    _store = store
    runs = (store.get_document("tasks") or {}).get("runs", [])
    with _lock:
        for entry in runs:
            if entry["status"] == "running":
                entry.update(status="failed", error="服务重启，任务中断", finished_at=time.time())
            entry.setdefault("logs", [])
            _tasks[entry["id"]] = entry


def _persist() -> None:
    """把最近的任务记录（含日志）写入存储；未接入存储时跳过。"""
    store = _store
    if store is None:
        return
    with _lock:
        items = sorted(_tasks.values(), key=lambda item: str(item["started_at"]), reverse=True)
        snapshot = [dict(item, logs=list(item["logs"])) for item in items[:MAX_TASK_RUNS]]
    try:
        store.set_document("tasks", {"runs": snapshot})
    except Exception:  # 落盘失败不阻断采集：日志属辅助信息，任务状态仍在内存可用
        pass


def _append_log(task_id: str, message: str, level: str) -> None:
    with _lock:
        entry = _tasks.get(task_id)
        if entry is None or len(entry["logs"]) >= MAX_LOG_LINES:
            return
        entry["logs"].append({"time": time.time(), "message": message, "level": level})
    _persist()


def submit(kind: str, fn: Callable[[], Any]) -> str:
    """提交后台任务并立即返回 task_id；同一 kind 同时只允许一个运行中的任务。"""
    with _lock:
        if kind in _RUNNING_KINDS:
            raise RuntimeError(f"已有运行中的 {kind} 任务: {_RUNNING_KINDS[kind]}")
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
    """该 kind 是否有运行中的任务；调度器据此跳过正忙的一轮，避免与手动触发撞车。"""
    with _lock:
        return kind in _RUNNING_KINDS


def get(task_id: str) -> dict[str, Any] | None:
    with _lock:
        task = _tasks.get(task_id)
        return dict(task) if task else None


def reset() -> None:
    """清空任务注册表并解除持久化（仅测试使用）：注册表进程内共享，测试间需要显式清场。"""
    global _store
    with _lock:
        _tasks.clear()
        _RUNNING_KINDS.clear()
    _store = None


def recent(limit: int = 20) -> list[dict[str, Any]]:
    """最近的任务列表（新任务在前）；日志不随列表下发，详情接口单独返回。"""
    with _lock:
        items = sorted(_tasks.values(), key=lambda item: str(item["started_at"]), reverse=True)
        return [
            {key: value for key, value in item.items() if key != "logs"} | {"log_count": len(item["logs"])}
            for item in items[:limit]
        ]
