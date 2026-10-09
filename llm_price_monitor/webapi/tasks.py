"""进程内后台任务注册表：采集与官方价抓取耗时较长，提交到线程执行并轮询状态。"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

_lock = threading.Lock()
_tasks: dict[str, dict[str, Any]] = {}
_RUNNING_KINDS: dict[str, str] = {}


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
        }
        _RUNNING_KINDS[kind] = task_id

    def _run() -> None:
        try:
            result = fn()
        except Exception as exc:  # 后台线程兜底：任何异常都落到任务状态里供前端展示
            with _lock:
                # reset() 可能已把任务清出注册表（测试清场）；条目不存在时静默退出
                entry = _tasks.get(task_id)
                if entry is not None:
                    entry.update(status="failed", error=str(exc), finished_at=time.time())
                _RUNNING_KINDS.pop(kind, None)
        else:
            with _lock:
                entry = _tasks.get(task_id)
                if entry is not None:
                    entry.update(status="done", result=result, finished_at=time.time())
                _RUNNING_KINDS.pop(kind, None)

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
    """清空任务注册表（仅测试使用）：注册表在进程内存中，测试间共享需要显式清场。"""
    with _lock:
        _tasks.clear()
        _RUNNING_KINDS.clear()


def recent(limit: int = 20) -> list[dict[str, Any]]:
    """最近的任务列表（新任务在前）；任务注册表在进程内存中，重启即清空。"""
    with _lock:
        items = sorted(_tasks.values(), key=lambda item: str(item["started_at"]), reverse=True)
        return [dict(item) for item in items[:limit]]
