"""无头浏览器环境自检：启动时确认 Chromium 可用，缺失时自动执行 playwright install。

仅当已有站点启用 network.headless 时才检查，避免无关部署在启动时联网下载浏览器。
"""
from __future__ import annotations

import subprocess
import sys
import time

from llm_price_monitor.tasklog import emit

_INSTALL_TIMEOUT_SECONDS = 600


def _site_needs_browser(store) -> bool:
    """任一启用站点配置了 network.headless.enabled=true 时返回 True。"""
    for raw in store.list_site_configs():
        if not isinstance(raw, dict):
            continue
        if not raw.get("enabled", True):
            continue
        network = raw.get("network")
        headless = network.get("headless") if isinstance(network, dict) else None
        if isinstance(headless, dict) and headless.get("enabled") is True:
            return True
    return False


def _chromium_launches() -> bool:
    """真启动一次 headless Chromium 验证浏览器与系统库齐全；成功即关掉。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def _install_chromium() -> None:
    """执行 playwright install chromium（含系统依赖），失败抛 RuntimeError 带原始输出。"""
    emit("检测到无头浏览器组件缺失，正在自动安装 Chromium（首次约 1~2 分钟，视网络而定）…")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
        capture_output=True,
        text=True,
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-5:]
        raise RuntimeError("Chromium 自动安装失败：" + "；".join(tail))


def ensure_browser_ready(store) -> None:
    """启动自检入口：需要浏览器但起不来时自动安装一次，再起不来就把原因写进任务日志。"""
    if not _site_needs_browser(store):
        return
    started = time.time()
    if _chromium_launches():
        emit(f"无头浏览器自检通过（{time.time() - started:.1f}s）")
        return
    try:
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise RuntimeError("还没安装 playwright 组件，请先在部署环境执行 pip install playwright")
        _install_chromium()
        if _chromium_launches():
            emit(f"Chromium 安装完成，无头浏览器自检通过（共 {time.time() - started:.1f}s）")
            return
        raise RuntimeError("Chromium 已安装但仍无法启动，通常是系统依赖不全，请执行 playwright install-deps chromium")
    except Exception as exc:
        emit(f"无头浏览器自检失败：{exc}", "error")
