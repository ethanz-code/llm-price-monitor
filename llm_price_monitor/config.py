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
from typing import Any, Literal
from urllib.parse import urlsplit

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


@dataclass(frozen=True)
class MonitorSettings:
    timeout: float = 20.0
    history_file: str = "var/price-history.jsonl"
    latest_file: str = "var/price-latest.json"
    event_file: str = "var/price-events.jsonl"
    webhook_env: str | None = None
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
    cache_file: str | None = None

    def pick_model(self) -> str:
        # 每次调用随机选一个模型，models 与旧字段 model 合并去重后作为候选池。
        pool = [item for item in dict.fromkeys((*self.models, self.model)) if item]
        return secrets.choice(pool) if pool else ""


@dataclass(frozen=True)
class MonitorConfig:
    settings: MonitorSettings
    ai: AIConfig
    sites: tuple[SiteSpec, ...]


def load_config(path: Path) -> MonitorConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw_settings = raw.get("settings", {})
    setting_values: dict[str, Any] = {
        key: raw_settings[key] for key in MonitorSettings.__dataclass_fields__ if key in raw_settings
    }
    for key in ("user_agent_platforms", "user_agent_chrome_versions"):
        if key in setting_values:
            value = setting_values[key]
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"settings.{key} 必须是字符串数组")
            setting_values[key] = tuple(value)
    settings = MonitorSettings(**setting_values)
    raw_ai = raw.get("ai", {}) if isinstance(raw.get("ai", {}), dict) else {}
    raw_ai_models = raw_ai.get("models", [])
    if not isinstance(raw_ai_models, list) or any(not isinstance(item, str) for item in raw_ai_models):
        raise ValueError("配置文件 ai.models 必须是字符串数组")
    ai = AIConfig(
        enabled=bool(raw_ai.get("enabled", True)),
        base_url=str(raw_ai.get("base_url", "")).strip(),
        model=str(raw_ai.get("model", "")).strip(),
        models=tuple(dict.fromkeys(item.strip() for item in raw_ai_models if item.strip())),
        api_key=raw_ai.get("api_key") or os.getenv(str(raw_ai.get("api_key_env", "")).strip()),
        timeout=float(raw_ai.get("timeout", 60)),
        max_input_chars=int(raw_ai.get("max_input_chars", 60000)),
        max_tokens=int(raw_ai.get("max_tokens", 4000)),
        enable_thinking=bool(raw_ai.get("enable_thinking", False)),
        dry_run=bool(raw_ai.get("dry_run", False)),
        cache_file=str(raw_ai.get("cache_file", "var/price-ai-cache.json")),
    )
    sites = []
    for value in raw.get("sites", []):
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
    if not sites:
        raise ValueError("配置中没有启用站点")
    return MonitorConfig(settings, ai, tuple(sites))
