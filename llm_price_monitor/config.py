"""价格监控的强类型配置：配置文件 JSON → dataclass。

所有站点、AI、UA 设置在 load_config 里完成校验，下游模块只面对
MonitorConfig，不再接触原始字典。
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from llm_price_monitor.store import Store
from llm_price_monitor.tracker import DEFAULT_NEWAPI_RATIO_BASE_PRICE
from llm_price_monitor.units import number_or_none
from llm_price_monitor.useragent import DEFAULT_BROWSER_USER_AGENT

PriceStatus = Literal["confirmed", "candidate", "rule_only", "unavailable"]
ChangeKind = Literal["new", "changed", "unchanged", "recovered", "status_changed"]


class PriceMonitorError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelTarget:
    name: str
    group: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SiteSpec:
    id: str
    adapter: str = "browser"  # 兼容旧配置；browser 与 network 均使用 HTTP 请求
    model_list_url: str | None = None
    models: tuple[ModelTarget, ...] = ()
    auth_token: str | None = None
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    cookie: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    request_headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    note: str | None = None
    preferred_response_url_patterns: tuple[str, ...] = ("price", "model")
    ratio_base_price: float = DEFAULT_NEWAPI_RATIO_BASE_PRICE
    currency: str = "CNY"
    network: dict[str, Any] = field(default_factory=dict)


class AIResultCache(Protocol):
    """AI 抽取结果缓存后端：由存储层实现（document 表），文件缓存已退役。"""

    def cache_get(self, key: str) -> dict[str, Any] | None: ...

    def cache_put(self, key: str, result: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class MonitorSettings:
    timeout: float = 20.0
    webhook: str | None = None
    tavily_api_key: str | None = None
    user_agent: str = DEFAULT_BROWSER_USER_AGENT
    random_user_agent: bool = False
    user_agent_platforms: tuple[str, ...] = ()
    user_agent_chrome_versions: tuple[str, ...] = ()
    user_agent_version_window: int = 2


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = True
    base_url: str = ""
    model: str = ""
    models: tuple[str, ...] = ()
    api_key: str | None = None
    timeout: float = 60.0
    max_input_chars: int = 60000
    max_tokens: int = 4000
    enable_thinking: bool = False
    dry_run: bool = False
    cache: AIResultCache | None = None

    def pick_model(self) -> str:
        # 每次调用随机选一个模型，models 与旧字段 model 合并去重后作为候选池。
        pool = [item for item in dict.fromkeys((*self.models, self.model)) if item]
        return secrets.choice(pool) if pool else ""


@dataclass(frozen=True)
class MonitorConfig:
    settings: MonitorSettings
    ai: AIConfig
    sites: tuple[SiteSpec, ...]


def settings_from_raw(raw: dict[str, Any], *, resolve_env: bool) -> MonitorSettings:
    raw = raw if isinstance(raw, dict) else {}
    values: dict[str, Any] = {key: raw[key] for key in MonitorSettings.__dataclass_fields__ if key in raw}
    if resolve_env:
        # 文件种子路径的旧字段与 env 兜底：webhook_env → webhook 直填、TAVILY_API_KEY
        if "webhook" not in values and raw.get("webhook_env"):
            values["webhook"] = os.getenv(str(raw["webhook_env"]).strip())
        if "tavily_api_key" not in values:
            values["tavily_api_key"] = os.getenv("TAVILY_API_KEY")
    for key in ("user_agent_platforms", "user_agent_chrome_versions"):
        if key in values:
            value = values[key]
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"settings.{key} 必须是字符串数组")
            values[key] = tuple(value)
    return MonitorSettings(**values)


def ai_from_raw(raw: dict[str, Any], *, resolve_env: bool, cache: AIResultCache | None) -> AIConfig:
    raw = raw if isinstance(raw, dict) else {}
    raw_models = raw.get("models", [])
    if not isinstance(raw_models, list) or any(not isinstance(item, str) for item in raw_models):
        raise ValueError("配置文件 ai.models 必须是字符串数组")
    api_key = raw.get("api_key")
    if not api_key and resolve_env:
        api_key = os.getenv(str(raw.get("api_key_env", "")).strip())
    return AIConfig(
        enabled=bool(raw.get("enabled", True)),
        base_url=str(raw.get("base_url", "")).strip(),
        model=str(raw.get("model", "")).strip(),
        models=tuple(dict.fromkeys(item.strip() for item in raw_models if item.strip())),
        api_key=api_key or None,
        timeout=float(raw.get("timeout", 60)),
        max_input_chars=int(raw.get("max_input_chars", 60000)),
        max_tokens=int(raw.get("max_tokens", 4000)),
        enable_thinking=bool(raw.get("enable_thinking", False)),
        dry_run=bool(raw.get("dry_run", False)),
        cache=cache,
    )


def sites_from_raw(values: list[Any]) -> tuple[SiteSpec, ...]:
    sites = []
    for value in values:
        site_id = value.get("id", "<unknown>")
        if "base_url" in value:
            raise ValueError(f"站点 {site_id} 不再支持 base_url；请在 network.url 配置接口地址")
        removed_fields = {
            key
            for key in (
                "source_url",
                "endpoint",
                "endpoint_candidates",
                "model_field",
                "input_field",
                "output_field",
                "unit_field",
                "endpoint_pattern",
                "usage_records_url",
            )
            if key in value
        }
        if removed_fields:
            names = ", ".join(sorted(removed_fields))
            raise ValueError(f"站点 {site_id} 不再支持 {names}；请统一使用 network 配置请求和字段映射")
        network = value.get("network", {})
        if not isinstance(network, dict):
            raise ValueError(f"站点 {site_id} 的 network 必须是对象")
        network_url = network.get("url")
        if network_url is not None:
            if not isinstance(network_url, str) or not network_url.strip():
                raise ValueError(f"站点 {site_id} 的 network.url 必须是非空 URL")
            parsed_network_url = urlsplit(network_url)
            if parsed_network_url.scheme not in {"http", "https"} or not parsed_network_url.netloc:
                raise ValueError(f"站点 {site_id} 的 network.url 必须是完整的 http(s) URL")
        network_method = str(network.get("method", "GET")).strip().upper()
        if network_method not in {"GET", "POST", "PUT", "PATCH"}:
            raise ValueError(f"站点 {site_id} 的 network.method 不支持: {network_method}")
        for network_field in ("params", "headers"):
            if network_field in network and not isinstance(network[network_field], dict):
                raise ValueError(f"站点 {site_id} 的 network.{network_field} 必须是对象")
        body_type = str(network.get("body_type", "json")).strip().lower()
        if body_type not in {"json", "form", "raw"}:
            raise ValueError(f"站点 {site_id} 的 network.body_type 必须是 json、form 或 raw")
        request_headers = value.get("request_headers", {})
        if not isinstance(request_headers, dict) or any(
            not isinstance(key, str) or not isinstance(header_value, str)
            for key, header_value in request_headers.items()
        ):
            raise ValueError(f"站点 {site_id} 的 request_headers 必须是字符串键值对象")
        preferred_response_url_patterns = value.get("preferred_response_url_patterns", ["price", "model"])
        if not isinstance(preferred_response_url_patterns, list) or any(not isinstance(item, str) for item in preferred_response_url_patterns):
            raise ValueError(f"站点 {site_id} 的 preferred_response_url_patterns 必须是字符串数组")
        ratio_base_price = number_or_none(value.get("ratio_base_price", DEFAULT_NEWAPI_RATIO_BASE_PRICE))
        if ratio_base_price is None or ratio_base_price <= 0:
            raise ValueError(f"站点 {site_id} 的 ratio_base_price 必须是正数")
        currency = str(value.get("currency", "CNY")).strip().upper()
        if currency not in {"CNY", "USD"}:
            raise ValueError(f"站点 {site_id} 的 currency 必须是 CNY 或 USD")
        models = tuple(
            ModelTarget(
                item if isinstance(item, str) else item["name"],
                None if isinstance(item, str) else item.get("group"),
                () if isinstance(item, str) else tuple(item.get("aliases", [])),
            )
            for item in value.get("models", [])
        )
        site_values = {
            **value,
            "models": models,
            "request_headers": request_headers,
            "preferred_response_url_patterns": tuple(preferred_response_url_patterns),
            "ratio_base_price": ratio_base_price,
            "currency": currency,
            "network": {**network, "method": network_method, "body_type": body_type},
        }
        if set(value) <= {"id"}:
            site_values["enabled"] = False
        sites.append(SiteSpec(**site_values))
    return tuple(sites)


def config_from_raw(
    raw: dict[str, Any], *, resolve_env: bool = True, cache: AIResultCache | None = None
) -> MonitorConfig:
    """raw dict → 强类型配置；文件种子路径（resolve_env=True）负责 env 兜底与空站点校验。"""
    settings = settings_from_raw(raw.get("settings", {}), resolve_env=resolve_env)
    ai = ai_from_raw(raw.get("ai", {}), resolve_env=resolve_env, cache=cache)
    sites = sites_from_raw(raw.get("sites", []))
    if resolve_env and not sites:
        raise ValueError("配置中没有启用站点")
    return MonitorConfig(settings, ai, sites)


def load_config(path: Path) -> MonitorConfig:
    return config_from_raw(json.loads(path.read_text(encoding="utf-8")))


def config_from_store(store: Store) -> MonitorConfig:
    """数据库 → 强类型配置；文档在种子导入与面板保存时已完成 env 解析和校验。

    站点表允许为空（新装状态，由管理面板添加第一个站点）。
    """
    return config_from_raw(
        {
            "settings": store.get_document("settings") or {},
            "ai": store.get_document("ai") or {},
            "sites": store.list_site_configs(),
        },
        resolve_env=False,
        cache=store,
    )
