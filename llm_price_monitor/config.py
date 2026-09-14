"""价格监控的强类型配置：配置文件 JSON → dataclass。

所有站点、AI、UA 设置在 load_config 里完成校验，下游模块只面对
MonitorConfig，不再接触原始字典。
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from llm_price_monitor.store import Store
from llm_price_monitor.useragent import DEFAULT_BROWSER_USER_AGENT

PriceStatus = Literal["confirmed", "candidate", "rule_only", "unavailable"]
ChangeKind = Literal["new", "changed", "unchanged", "recovered", "status_changed", "group_removed", "group_added"]



# AI 调用的接口结构：同一套提示词按所选结构的 URL/请求体/响应格式发出
AI_FORMATS = ("chat_completions", "openai_responses", "anthropic", "gemini")


class PriceMonitorError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelTarget:
    name: str


DEPRECATED_SITE_FIELDS = frozenset({"note", "preferred_response_url_patterns", "ratio_base_price", "model_list_url", "currency"})  # 已废弃的站点字段：加载/保存时静默丢弃

# 四类采集任务的定时间隔（分钟），存 settings.schedule；0 = 关闭该项定时、只保留手动触发
DEFAULT_SCHEDULE_MINUTES = {"price": 60, "status": 5, "notice": 30, "catalog": 1440}
SCHEDULE_KEYS = tuple(DEFAULT_SCHEDULE_MINUTES)


def schedule_from_raw(raw: Any) -> dict[str, float]:
    """校验 settings.schedule：每项必须是不小于 0 的数字（分钟）；缺失项回默认值。"""
    raw = raw if isinstance(raw, dict) else {}
    schedule: dict[str, float] = {}
    for key in SCHEDULE_KEYS:
        value = raw.get(key, DEFAULT_SCHEDULE_MINUTES[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"settings.schedule.{key} 必须是不小于 0 的数字（分钟，0 表示关闭定时）")
        schedule[key] = float(value)
    return schedule


@dataclass(frozen=True)
class SiteSpec:
    id: str
    adapter: str = "standard"  # 旧值 browser/network/rate_base 载入时归一为 standard
    models: tuple[ModelTarget, ...] = ()
    auth_token: str | None = None
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    cookie: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    request_headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    network: dict[str, Any] = field(default_factory=dict)
    networks: tuple[dict[str, Any], ...] = ()  # 附加采集地址：与 network 同构，逐个采集后合并价格
    status: dict[str, Any] = field(default_factory=dict)
    notice: dict[str, Any] = field(default_factory=dict)
    token_refresh: dict[str, Any] = field(default_factory=dict)  # 认证续签请求配置：url/method/params/headers/body/refresh_token  # 公告地址：默认从 network.url 推导 /api/notice  # 可选渠道状态数据地址，GET 拉取后存自由结构 JSON


class AIResultCache(Protocol):
    """AI 抽取结果缓存后端：由存储层实现（document 表），文件缓存已退役。"""

    def cache_get(self, key: str) -> dict[str, Any] | None: ...

    def cache_put(self, key: str, result: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class MonitorSettings:
    timeout: float = 20.0
    wxpusher_app_token: str | None = None
    wxpusher_uid: str | None = None
    user_agent: str = DEFAULT_BROWSER_USER_AGENT
    random_user_agent: bool = False
    user_agent_platforms: tuple[str, ...] = ()
    user_agent_chrome_versions: tuple[str, ...] = ()
    user_agent_version_window: int = 2
    # 数据保留天数：超期由对应采集任务/接口顺带清理；价格与站点事件永不清理
    retention_price_days: int = 90
    retention_visit_days: int = 90
    retention_status_days: int = 90
    # 任务记录保留条数（清理界限，超出淘汰最旧）与单任务日志滚动行数上限
    max_task_runs: int = 100
    max_task_log_lines: int = 500
    # AI 助手每 IP 每天提问次数上限；0 表示不限制
    assistant_daily_limit: int = 10


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = True
    base_url: str = ""
    model: str = ""
    models: tuple[str, ...] = ()
    api_key: str | None = None
    api_format: str = "chat_completions"
    timeout: float = 180.0
    max_input_chars: int = 60000
    max_tokens: int = 4000
    enable_thinking: bool = False
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
    for key in ("user_agent_platforms", "user_agent_chrome_versions"):
        if key in values:
            value = values[key]
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(f"settings.{key} 必须是字符串数组")
            values[key] = tuple(value)
    for key in ("retention_price_days", "retention_visit_days", "retention_status_days"):
        if key in values and (not isinstance(values[key], int) or isinstance(values[key], bool) or values[key] < 1):
            raise ValueError(f"settings.{key} 必须是不小于 1 的整数（天）")
    for key in ("max_task_runs", "max_task_log_lines"):
        if key in values and (not isinstance(values[key], int) or isinstance(values[key], bool) or values[key] < 1):
            raise ValueError(f"settings.{key} 必须是不小于 1 的整数")
    if "assistant_daily_limit" in values and (
        not isinstance(values["assistant_daily_limit"], int) or isinstance(values["assistant_daily_limit"], bool) or values["assistant_daily_limit"] < 0
    ):
        raise ValueError("settings.assistant_daily_limit 必须是不小于 0 的整数（0 表示不限制）")
    return MonitorSettings(**values)


def ai_from_raw(raw: dict[str, Any], *, cache: AIResultCache | None) -> AIConfig:
    raw = raw if isinstance(raw, dict) else {}
    raw_models = raw.get("models", [])
    if not isinstance(raw_models, list) or any(not isinstance(item, str) for item in raw_models):
        raise ValueError("配置文件 ai.models 必须是字符串数组")
    api_key = raw.get("api_key")
    api_format = str(raw.get("api_format", "chat_completions")).strip()
    if api_format not in AI_FORMATS:
        raise ValueError("配置文件 ai.api_format 必须是 chat_completions、openai_responses、anthropic 或 gemini")
    base_url = str(raw.get("base_url", "")).strip()
    if base_url:
        parsed_base = urlsplit(base_url)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
            raise ValueError("配置文件 ai.base_url 必须是完整的 http(s) URL")
    timeout = float(raw.get("timeout", 180))
    max_input_chars = int(raw.get("max_input_chars", 60000))
    max_tokens = int(raw.get("max_tokens", 4000))
    if not timeout > 0:
        raise ValueError("配置文件 ai.timeout 必须是大于 0 的数字（秒）")
    if max_input_chars < 1 or max_tokens < 1:
        raise ValueError("配置文件 ai.max_input_chars / ai.max_tokens 必须是不小于 1 的整数")
    return AIConfig(
        enabled=bool(raw.get("enabled", True)),
        base_url=base_url,
        model=str(raw.get("model", "")).strip(),
        models=tuple(dict.fromkeys(item.strip() for item in raw_models if item.strip())),
        api_key=api_key or None,
        api_format=api_format,
        timeout=timeout,
        max_input_chars=max_input_chars,
        max_tokens=max_tokens,
        enable_thinking=bool(raw.get("enable_thinking", False)),
        cache=cache,
    )


def _endpoint_section(raw: Any, site_id: str, name: str) -> dict[str, Any]:
    """status/notice 段归一：URL 字符串简写 → {url}；校验 url 与 params/headers 类型。

    status.groups 是站点级分组白名单，只作用于价格采集时允许没有 url，
    所以仅当段里出现 url 之外的字段时才强制要求 url。"""
    if isinstance(raw, str):
        raw = {"url": raw}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"站点 {site_id} 的 {name} 必须是 URL 字符串或对象")
    if raw:
        url = raw.get("url")
        # status 只配分组白名单（groups 是站点级过滤，可只作用于价格采集）时允许没有 url
        if not (url is None and name == "status" and set(raw) == {"groups"}):
            if not isinstance(url, str) or not url.strip():
                raise ValueError(f"站点 {site_id} 的 {name}.url 必须是非空 URL")
            parsed_url = urlsplit(url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                raise ValueError(f"站点 {site_id} 的 {name}.url 必须是完整的 http(s) URL")
        for section_field in ("params", "headers"):
            if section_field in raw and not isinstance(raw[section_field], dict):
                raise ValueError(f"站点 {site_id} 的 {name}.{section_field} 必须是对象")
        if "groups" in raw:
            raw_groups = raw["groups"]
            if not isinstance(raw_groups, list) or any(not isinstance(item, str) for item in raw_groups):
                raise ValueError(f"站点 {site_id} 的 {name}.groups 必须是字符串数组")
    return raw


def _validate_token_refresh(raw: dict[str, Any], site_id: str) -> None:
    """token_refresh 续签请求配置校验：与 network 入口同风格的 URL/method/params/headers/body。"""
    url = raw.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"站点 {site_id} 的 token_refresh.url 必须是非空 URL")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"站点 {site_id} 的 token_refresh.url 必须是完整的 http(s) URL")
    method = str(raw.get("method") or "POST").upper()
    if method not in {"GET", "POST", "PUT", "PATCH"}:
        raise ValueError(f"站点 {site_id} 的 token_refresh.method 仅支持 GET/POST/PUT/PATCH")
    for field_name in ("params", "headers"):
        section = raw.get(field_name, {})
        if not isinstance(section, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in section.items()):
            raise ValueError(f"站点 {site_id} 的 token_refresh.{field_name} 必须是字符串键值对象")
    body = raw.get("body")
    if body is not None and not isinstance(body, str):
        raise ValueError(f"站点 {site_id} 的 token_refresh.body 必须是字符串（支持 ${{refresh_token}} 占位）")
    for field_name in ("access_token_field", "refresh_token_field"):
        field_value = raw.get(field_name)
        if field_value is not None and (not isinstance(field_value, str) or not field_value.strip()):
            raise ValueError(f"站点 {site_id} 的 token_refresh.{field_name} 必须是非空字符串（如 data.access_token）")
    if not str(raw.get("refresh_token") or "").strip():
        raise ValueError(f"站点 {site_id} 的 token_refresh.refresh_token 不能为空")


def _validate_headless(network: dict[str, Any], site_id: str) -> None:
    """校验站点 network.headless 段；未启用时只校验 enabled 本身，其余字段可以不填。"""
    headless = network.get("headless")
    if headless is None:
        return
    if not isinstance(headless, dict):
        raise ValueError(f"站点 {site_id} 的 network.headless 必须是对象")
    enabled = headless.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"站点 {site_id} 的 network.headless.enabled 必须是布尔值")
    if not enabled:
        return
    cookies = headless.get("cookies")
    if cookies is not None:
        if not isinstance(cookies, list):
            raise ValueError(f"站点 {site_id} 的 network.headless.cookies 必须是数组")
        for index, item in enumerate(cookies):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item.get("name", "").strip():
                raise ValueError(f"站点 {site_id} 的 network.headless.cookies[{index}].name 必须是非空字符串")
            if not isinstance(item.get("value"), str) or not item.get("value", "").strip():
                raise ValueError(f"站点 {site_id} 的 network.headless.cookies[{index}].value 必须是非空字符串")
    local_storage = headless.get("localStorage")
    if local_storage is not None:
        if not isinstance(local_storage, dict) or any(
            not isinstance(key, str) or not isinstance(storage_value, str)
            for key, storage_value in local_storage.items()
        ):
            raise ValueError(f"站点 {site_id} 的 network.headless.localStorage 必须是字符串键值对象")
    wait_seconds = headless.get("wait_seconds", 3)
    if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float)) or not 0 <= wait_seconds <= 60:
        raise ValueError(f"站点 {site_id} 的 network.headless.wait_seconds 必须是 0~60 之间的数字")


def sites_from_raw(values: list[Any]) -> tuple[SiteSpec, ...]:
    sites = []
    for raw_value in values:
        value = {key: item for key, item in raw_value.items() if key not in DEPRECATED_SITE_FIELDS}
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
        adapter = str(value.get("adapter") or "standard")
        if adapter in {"browser", "network", "rate_base"}:
            adapter = "standard"  # 旧配置值归一；rate_base 双地址退化为 network.url 单地址
        network = {key: item for key, item in network.items() if key != "base_price_url"}
        if isinstance(network_url, dict):
            # 旧 rate_base 的对象形态：归一时只保留主 URL
            inner_url = network_url.get("url")
            network_url = inner_url if isinstance(inner_url, str) and inner_url.strip() else None
            network = {**network, "url": network_url}
        if network_url is not None:
            if not isinstance(network_url, str) or not network_url.strip():
                raise ValueError(f"站点 {site_id} 的 network.url 必须是非空 URL")
            parsed_network_url = urlsplit(network_url)
            if parsed_network_url.scheme not in {"http", "https"} or not parsed_network_url.netloc:
                raise ValueError(f"站点 {site_id} 的 network.url 必须是完整的 http(s) URL")
        # ratio_url 兼容两种写法：纯 URL 字符串（旧配置）或 {url, headers} 对象（倍率接口独立请求头）
        ratio_raw = network.get("ratio_url")
        if ratio_raw is not None:
            ratio_value = {"url": ratio_raw} if isinstance(ratio_raw, str) else ratio_raw
            if not isinstance(ratio_value, dict):
                raise ValueError(f"站点 {site_id} 的 network.ratio_url 必须是 URL 字符串或对象")
            ratio_url = ratio_value.get("url")
            if not isinstance(ratio_url, str) or not ratio_url.strip():
                raise ValueError(f"站点 {site_id} 的 network.ratio_url.url 必须是非空 URL")
            parsed_ratio_url = urlsplit(ratio_url)
            if parsed_ratio_url.scheme not in {"http", "https"} or not parsed_ratio_url.netloc:
                raise ValueError(f"站点 {site_id} 的 network.ratio_url.url 必须是完整的 http(s) URL")
            if "headers" in ratio_value and not isinstance(ratio_value["headers"], dict):
                raise ValueError(f"站点 {site_id} 的 network.ratio_url.headers 必须是对象")
        for network_field in ("params", "headers"):
            if network_field in network and not isinstance(network[network_field], dict):
                raise ValueError(f"站点 {site_id} 的 network.{network_field} 必须是对象")
        _validate_headless(network, site_id)
        raw_networks = value.get("networks", [])
        if not isinstance(raw_networks, list):
            raise ValueError(f"站点 {site_id} 的 networks 必须是数组")
        normalized_networks: list[dict[str, Any]] = []
        for index, entry in enumerate(raw_networks):
            label = f"站点 {site_id} 的 networks[{index}]"
            if not isinstance(entry, dict):
                raise ValueError(f"{label} 必须是对象")
            entry_url = entry.get("url")
            if not isinstance(entry_url, str) or not entry_url.strip():
                raise ValueError(f"{label}.url 必须是非空 URL")
            parsed_entry_url = urlsplit(entry_url)
            if parsed_entry_url.scheme not in {"http", "https"} or not parsed_entry_url.netloc:
                raise ValueError(f"{label}.url 必须是完整的 http(s) URL")
            for network_field in ("params", "headers"):
                if network_field in entry and not isinstance(entry[network_field], dict):
                    raise ValueError(f"{label}.{network_field} 必须是对象")
            normalized_networks.append(entry)
        raw_status = _endpoint_section(value.get("status"), site_id, "status")
        raw_notice = _endpoint_section(value.get("notice"), site_id, "notice")
        raw_token_refresh = value.get("token_refresh", {})
        if not isinstance(raw_token_refresh, dict):
            raise ValueError(f"站点 {site_id} 的 token_refresh 必须是对象")
        if raw_token_refresh:
            _validate_token_refresh(raw_token_refresh, site_id)
        request_headers = value.get("request_headers", {})
        if not isinstance(request_headers, dict) or any(
            not isinstance(key, str) or not isinstance(header_value, str)
            for key, header_value in request_headers.items()
        ):
            raise ValueError(f"站点 {site_id} 的 request_headers 必须是字符串键值对象")
        raw_models = value.get("models", [])
        if not isinstance(raw_models, list) or any(not isinstance(item, str) for item in raw_models):
            raise ValueError(f"站点 {site_id} 的 models 必须是字符串数组；模型分组/别名字段已下线，别名由 AI 自动解析")
        models = tuple(ModelTarget(item.strip()) for item in raw_models if item.strip())
        site_values = {
            **value,
            "adapter": adapter,
            "models": models,
            "request_headers": request_headers,
            "network": network,
            "networks": tuple(normalized_networks),
            "status": raw_status,
            "notice": raw_notice,
        }
        if set(value) <= {"id"}:
            site_values["enabled"] = False
        sites.append(SiteSpec(**site_values))
    return tuple(sites)


def config_from_raw(
    raw: dict[str, Any], *, resolve_env: bool = True, cache: AIResultCache | None = None
) -> MonitorConfig:
    """raw dict → 强类型配置；文件种子路径（resolve_env=True）额外要求站点不为空。"""
    settings = settings_from_raw(raw.get("settings", {}), resolve_env=resolve_env)
    ai = ai_from_raw(raw.get("ai", {}), cache=cache)
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
