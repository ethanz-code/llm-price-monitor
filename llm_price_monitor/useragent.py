"""浏览器 User-Agent 构造与轮换。

一次运行只选一个 UA，同一运行内的请求之间不轮换；random 模式从平台
token 与有界 Chrome 版本区间构造 UA，避免任意 OS 与任意 UA 混搭。
"""
from __future__ import annotations

import re
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_price_monitor.config import MonitorConfig, MonitorSettings

BROWSER_PLATFORM_TOKENS = {
    "mac": "Macintosh; Intel Mac OS X 10_15_7",
    "windows": "Windows NT 10.0; Win64; x64",
    "linux": "X11; Linux x86_64",
}
_CHROME_VERSION_PATTERN = re.compile(r"Chrome/(\d+(?:\.\d+){0,3})", re.IGNORECASE)
DEFAULT_CHROME_VERSION = "151.0.0.0"


def build_browser_user_agent(platform_name: str, chrome_version: str) -> str:
    platform_token = BROWSER_PLATFORM_TOKENS[platform_name]
    return f"Mozilla/5.0 ({platform_token}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"


BROWSER_USER_AGENTS = tuple(
    build_browser_user_agent(platform_name, DEFAULT_CHROME_VERSION)
    for platform_name in ("mac", "windows", "linux")
)
DEFAULT_BROWSER_USER_AGENT = BROWSER_USER_AGENTS[0]


def infer_user_agent_platform(user_agent: str) -> str:
    if "Windows NT" in user_agent:
        return "windows"
    if "X11; Linux" in user_agent:
        return "linux"
    if "Macintosh" in user_agent:
        return "mac"
    raise ValueError("无法从 user_agent 推断平台；请配置 user_agent_platforms")


def user_agent_platforms(settings: "MonitorSettings") -> tuple[str, ...]:
    platforms = settings.user_agent_platforms or (infer_user_agent_platform(settings.user_agent),)
    if any(platform not in BROWSER_PLATFORM_TOKENS for platform in platforms):
        allowed = ", ".join(sorted(BROWSER_PLATFORM_TOKENS))
        raise ValueError(f"user_agent_platforms 只能包含: {allowed}")
    return tuple(dict.fromkeys(platforms))


def configured_chrome_major(user_agent: str) -> int:
    match = _CHROME_VERSION_PATTERN.search(user_agent)
    if not match:
        raise ValueError("user_agent 中缺少 Chrome/版本；请配置 user_agent_chrome_versions")
    return int(match.group(1).split(".")[0])


def validate_chrome_version(value: str) -> str:
    version = value.strip()
    if not re.fullmatch(r"\d+(?:\.\d+){3}", version):
        raise ValueError(f"无效的 Chrome 版本: {value}")
    return version


def user_agent_chrome_versions(settings: "MonitorSettings") -> tuple[str, ...]:
    if settings.user_agent_chrome_versions:
        versions = tuple(validate_chrome_version(value) for value in settings.user_agent_chrome_versions)
        return tuple(dict.fromkeys(versions))
    base_major = configured_chrome_major(settings.user_agent)
    window = settings.user_agent_version_window
    if isinstance(window, bool) or not isinstance(window, int) or not 0 <= window <= 5:
        raise ValueError("user_agent_version_window 必须是 0 到 5 之间的整数")
    return tuple(f"{major}.0.0.0" for major in range(base_major - window, base_major + window + 1) if major > 0)


def weighted_chrome_versions(versions: tuple[str, ...], base_major: int) -> tuple[str, ...]:
    weighted: list[str] = []
    for version in versions:
        distance = abs(int(version.split(".")[0]) - base_major)
        weighted.extend([version] * max(1, 3 - distance))
    return tuple(weighted)


def choose_user_agent(config: "MonitorConfig", override: str | None = None) -> str:
    if override:
        return override
    if not config.settings.random_user_agent:
        return config.settings.user_agent
    platforms = user_agent_platforms(config.settings)
    versions = user_agent_chrome_versions(config.settings)
    base_major = configured_chrome_major(config.settings.user_agent)
    platform = secrets.choice(platforms)
    version = secrets.choice(weighted_chrome_versions(versions, base_major))
    return build_browser_user_agent(platform, version)
