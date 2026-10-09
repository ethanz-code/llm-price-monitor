"""任务过程日志：采集代码调用 emit 逐行写出，webapi 任务线程绑定出口收进任务记录。

出口按线程隔离：只有 tasks.submit 起的后台线程绑定了 sink；CLI 与测试直跑采集函数时
没有绑定，emit 静默跳过，采集流程不受影响。
"""
from __future__ import annotations

import threading
from collections.abc import Callable

# sink 签名：(message, level)；level 取 "info" | "error"
_local = threading.local()


def bind(sink: Callable[[str, str], None]) -> None:
    """绑定当前线程的日志出口。"""
    _local.sink = sink


def unbind() -> None:
    """解除当前线程的日志出口绑定。"""
    _local.sink = None


def emit(message: str, level: str = "info") -> None:
    """写一行任务日志；当前线程未绑定出口时（如 CLI 直跑）静默跳过。"""
    sink = getattr(_local, "sink", None)
    if sink is not None:
        sink(message, level)
