"""轻量 User-Agent 解析：正则识别浏览器、操作系统与设备类型，不引第三方依赖。

识别精度优先覆盖主流浏览器/系统，识别不出时返回「其他」；爬虫与脚本类 UA
统一归为 device="bot"，方便统计时单独剔分。
"""
from __future__ import annotations

import re
from typing import NamedTuple

_BOT_PATTERN = re.compile(
    r"bot|spider|crawler|slurp|curl|wget|python-requests|httpx|go-http-client|headless|monitoring",
    re.IGNORECASE,
)

# 顺序敏感：Edge/Opera 的 UA 里也含 Chrome，Chrome 的 UA 里也含 Safari，必须先判壳再判内核
_BROWSER_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/")),
    ("Opera", re.compile(r"OPR/|Opera")),
    ("Firefox", re.compile(r"Firefox|FxiOS")),
    ("Chrome", re.compile(r"Chrome|CriOS")),
    ("Safari", re.compile(r"Safari")),
    ("IE", re.compile(r"MSIE|Trident/")),
]

_OS_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Windows", re.compile(r"Windows")),
    ("iOS", re.compile(r"iPhone|iPad|iPod")),
    ("Android", re.compile(r"Android")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("Linux", re.compile(r"Linux|X11")),
]

_TABLET_PATTERN = re.compile(r"iPad|Tablet|Android(?!.*Mobile)", re.IGNORECASE)
_MOBILE_PATTERN = re.compile(r"Mobile|iPhone|Android.*Mobile", re.IGNORECASE)


class UAInfo(NamedTuple):
    browser: str
    os: str
    device: str  # desktop / mobile / tablet / bot


def parse_user_agent(user_agent: str | None) -> UAInfo:
    ua = (user_agent or "").strip()
    if not ua:
        return UAInfo("其他", "其他", "desktop")
    if _BOT_PATTERN.search(ua):
        return UAInfo("爬虫/脚本", "—", "bot")
    browser = next((name for name, pattern in _BROWSER_RULES if pattern.search(ua)), "其他")
    os_name = next((name for name, pattern in _OS_RULES if pattern.search(ua)), "其他")
    if _TABLET_PATTERN.search(ua):
        device = "tablet"
    elif _MOBILE_PATTERN.search(ua):
        device = "mobile"
    else:
        device = "desktop"
    return UAInfo(browser, os_name, device)
