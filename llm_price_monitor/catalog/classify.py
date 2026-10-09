"""AI 档位判定：把目录模型清单交给 AI 分级（flagship 顶级 / mainstream 主流）。

判定结果与输入指纹一起持久化在目录条目里（tier / tier_fp），刷新时指纹未变的模型
不重复调用 AI；AI 未配置、调用失败或返回不可解析时静默跳过，页面不亮旗舰。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any

import httpx

from llm_price_monitor.ai import AIExtractionError, ai_content, ai_request, json_content
from llm_price_monitor.config import AIConfig

SYSTEM_PROMPT = """\
你是 AI 模型市场分析师。给你某厂商的在售模型清单（JSON 对象：today 今天日期、vendor 厂商名、models 数组），
每个条目含 model（模型 ID）、name（展示名）、description（官方简介）、family（产品线家族）、
release_date（发布日期，可能缺失）、input_price/output_price（USD 每百万 token）、
context（上下文 token 上限，可能缺失）、output_modalities（输出模态）。

请对清单中每个模型输出档位：
- "flagship"：该厂商当前综合能力最强的旗舰模型（当前代最强主力档，通常也是定价最高的主力）；
- "mainstream"：当前在售、被广泛使用的主力系列（旗舰之外的主力档位，如 pro / max / 标准版）；
- null：预览与实验模型、图像/音频/embedding/实时等专用产物、小参数低成本档、已过时的旧代模型。

规则：flagship 从严，每厂商通常 1-2 个，证据不足就给 mainstream 或 null；只依据清单内信息判断。
发布日期是判定"当前代"的硬依据，也决定主流档：同一厂商内已有明显更新的代次在售时（两代发布间隔很长），
发布日期较久的旧代模型一律给 null，不得 flagship 也不得 mainstream——哪怕它仍在售、曾是当年的主力
（如 gpt-4-turbo 之于更新的 GPT 代次）。仅当整个清单都属于旧代、厂商没有更新替代在售时，旧代主力才可给 mainstream。
只输出 JSON：{"verdicts": [{"model": "<清单中的 model 原文>", "tier": "flagship" | "mainstream" | null}]}，
verdicts 必须覆盖清单中每个模型。"""

# 单次 AI 请求的模型数上限：控制输出体积，避免 max_tokens 截断
BATCH_SIZE = 40

VALID_TIERS = ("flagship", "mainstream", None)

# 同厂商已有约 18 个月内的代次在售时，发布超过该期限的旧代不给 flagship/mainstream
STALE_DAYS = 550


def _is_stale(entry: dict[str, Any], cutoff: date) -> bool:
    """发布日期早于 cutoff 视为旧代；缺失或无法解析的日期不参与硬校验。"""
    raw = entry.get("release_date")
    if not raw:
        return False
    try:
        released = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return False
    return released < cutoff


def ai_available(config: AIConfig) -> bool:
    return bool(config.enabled and config.base_url and config.api_key and config.models)


def tier_fingerprint(entry: dict[str, Any]) -> str:
    """档位判定的输入指纹：名称/描述/厂商/发布日期变了才需要重新判定。"""
    raw = json.dumps(
        [entry.get("model"), entry.get("name"), entry.get("description"), entry.get("vendor"), entry.get("release_date")],
        ensure_ascii=False,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def attach_ai_tiers(
    output: dict[str, Any],
    previous: dict[str, Any] | None,
    config: AIConfig,
    client: httpx.Client | None = None,
) -> int:
    """给目录条目补 AI 档位，返回本轮新判定的模型数。

    指纹未变的条目沿用上一轮档位；其余按厂商分批调 AI，校验通过才落盘；
    AI 不可用或某批失败时静默跳过（失败批条目无 tier，下一轮重试）。
    """
    if not ai_available(config):
        return 0
    previous_models = previous.get("models", {}) if isinstance(previous, dict) else {}
    pending: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, entry in output.get("models", {}).items():
        fingerprint = tier_fingerprint(entry)
        old = previous_models.get(key)
        if isinstance(old, dict) and old.get("tier_fp") == fingerprint:
            entry["tier"] = old.get("tier")
            entry["tier_fp"] = fingerprint
            continue
        pending.setdefault(str(entry.get("vendor", "")), []).append((key, entry))
    classified = 0
    for vendor, items in pending.items():
        for start in range(0, len(items), BATCH_SIZE):
            try:
                classified += _classify_batch(config, vendor, items[start : start + BATCH_SIZE], client)
            except (AIExtractionError, httpx.HTTPError, ValueError):
                break  # 该厂商本轮失败，条目保持无 tier，下一轮重试
    _demote_stale_models(output)
    return classified


def _demote_stale_models(output: dict[str, Any]) -> None:
    """硬校验兜底：同厂商已有新一代（约 18 个月内发布）在售时，旧代档位一律降为 null。

    覆盖 AI 判定与指纹沿用两条路径；整条产品线都旧或缺日期时不干预，交给 AI 判定。
    """
    cutoff = date.today() - timedelta(days=STALE_DAYS)
    by_vendor: dict[str, list[dict[str, Any]]] = {}
    for entry in output.get("models", {}).values():
        if isinstance(entry, dict):
            by_vendor.setdefault(str(entry.get("vendor", "")), []).append(entry)
    for entries in by_vendor.values():
        has_recent = any(
            entry.get("release_date") and not _is_stale(entry, cutoff) for entry in entries
        )
        if not has_recent:
            continue
        for entry in entries:
            if _is_stale(entry, cutoff) and entry.get("tier") in ("flagship", "mainstream"):
                entry["tier"] = None


def _classify_batch(
    config: AIConfig,
    vendor: str,
    batch: list[tuple[str, dict[str, Any]]],
    client: httpx.Client | None,
) -> int:
    """一个厂商批次的 AI 判定：返回成功落盘档位的模型数；失败抛异常由调用方兜底。"""
    payload = [
        {
            "model": entry.get("model"),
            "name": entry.get("name"),
            "description": entry.get("description"),
            "family": entry.get("family"),
            "release_date": entry.get("release_date"),
            "input_price": (entry.get("list") or {}).get("input"),
            "output_price": (entry.get("list") or {}).get("output"),
            "context": (entry.get("limit") or {}).get("context"),
            "output_modalities": (entry.get("modalities") or {}).get("output"),
        }
        for _, entry in batch
    ]
    verdicts = _chat_json(
        config,
        SYSTEM_PROMPT,
        json.dumps({"today": date.today().isoformat(), "vendor": vendor, "models": payload}, ensure_ascii=False),
        client,
    ).get("verdicts")
    if not isinstance(verdicts, list):
        raise ValueError("AI 档位返回缺少 verdicts 数组")
    by_model = {str(entry.get("model")): (key, entry) for key, entry in batch}
    classified = 0
    for item in verdicts:
        if not isinstance(item, dict):
            continue
        located = by_model.get(str(item.get("model")))
        tier = item.get("tier")
        if located is None or tier not in VALID_TIERS:
            continue  # 清单外模型或非法档位：丢弃，该模型下一轮重试
        key, entry = located
        entry["tier"] = tier
        entry["tier_fp"] = tier_fingerprint(entry)
        classified += 1
    return classified


def _chat_json(config: AIConfig, system: str, user: str, client: httpx.Client | None) -> dict[str, Any]:
    """一次 JSON 对话请求（与 ai.AIPriceExtractor 同一套响应解析）。"""
    ai_model = config.pick_model()
    if not config.base_url or not ai_model:
        raise AIExtractionError("ai.base_url 或 ai.models 未配置")
    own = client is None
    client = client or httpx.Client(timeout=config.timeout)
    try:
        try:
            endpoint, headers, request_body = ai_request(config, ai_model, system, user)
            response = client.post(endpoint, headers=headers, json=request_body, timeout=config.timeout)
            response.raise_for_status()
            return json_content(ai_content(config.api_format, response.json()))
        except (httpx.HTTPError, ValueError) as exc:
            raise AIExtractionError(f"AI 档位判定请求失败: {exc}") from exc
    finally:
        if own:
            client.close()
