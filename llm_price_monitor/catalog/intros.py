"""AI 生成模型简介：官方目录里 models.dev 未收录的条目（国内定价页抓进来的）没有现成
简介，按模型名让 AI 补一句中文简介。

与 translate 的指纹机制衔接：生成的简介直接写 description_zh 并登记 desc_fp（指纹取
模型键，模型名不变不重新生成），另标 desc_source="ai" 供界面区分「models.dev 简介译文」
与「AI 生成」。只处理 description 与 description_zh 都缺的条目——有英文原文的条目走
translate 的翻译队列。AI 未配置、调用失败或返回不可解析时静默跳过，下一轮重试。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from llm_price_monitor.ai import AIExtractionError
from llm_price_monitor.config import AIConfig

from .translate import ai_available, chat_json, description_fingerprint_text

SYSTEM_PROMPT = """\
你是大模型产品文案。给你一批 AI 模型（JSON：models 数组，每项含 model 标识、name 模型名、
vendor 所属厂商）。为每个模型写一句简体中文简介，30 字以内：按模型名与厂商推断它的定位
（所属系列、模态、规模档位、适用场景），客观克制；不确定的细节不编造，不写价格和跑分数字；
模型名与专有名词保留通用写法。只输出 JSON：
{"intros": [{"model": "<原样返回 model>", "zh": "<一句话简介>"}]}，
intros 必须覆盖每个 model。"""

# 与 translate 批量翻译同款请求体积上限
BATCH_SIZE = 30


def attach_ai_intros(
    output: dict[str, Any],
    previous: dict[str, Any] | None,
    config: AIConfig,
    client: httpx.Client | None = None,
) -> int:
    """给官方目录缺简介的条目生成一句中文简介，返回本轮新生成的条数。

    上一轮已生成且模型名未变的条目直接复用，不再发请求；单批失败不影响其余批次。
    """
    if not ai_available(config):
        return 0
    previous_models = previous.get("models", {}) if isinstance(previous, dict) else {}
    pending: list[tuple[str, dict[str, Any]]] = []
    for key, entry in output.get("models", {}).items():
        if not isinstance(entry, dict) or entry.get("description") or entry.get("description_zh"):
            continue
        fingerprint = description_fingerprint_text(key)
        old = previous_models.get(key)
        if isinstance(old, dict) and old.get("desc_fp") == fingerprint and old.get("description_zh"):
            entry["description_zh"] = old["description_zh"]
            entry["desc_fp"] = fingerprint
            entry["desc_source"] = "ai"
            continue
        pending.append((key, entry))
    generated = 0
    for start in range(0, len(pending), BATCH_SIZE):
        try:
            generated += _generate_batch(config, pending[start : start + BATCH_SIZE], client)
        except (AIExtractionError, httpx.HTTPError, ValueError):
            continue  # 这批本轮放弃，下一轮同步重试，其余批次照常
    return generated


def _generate_batch(
    config: AIConfig,
    batch: list[tuple[str, dict[str, Any]]],
    client: httpx.Client | None,
) -> int:
    """一个批次的 AI 简介：返回成功落盘的条数；失败抛异常由调用方兜底。"""
    payload = [
        {"model": key, "name": entry.get("name") or key, "vendor": entry.get("vendor") or ""}
        for key, entry in batch
    ]
    intros = chat_json(
        config,
        SYSTEM_PROMPT,
        f'{{"models": {json.dumps(payload, ensure_ascii=False)}}}',
        client,
    ).get("intros")
    if not isinstance(intros, list):
        raise ValueError("AI 简介返回缺少 intros 数组")
    by_key = dict(batch)
    generated = 0
    for item in intros:
        if not isinstance(item, dict):
            continue
        key = item.get("model")
        zh = item.get("zh")
        located = by_key.get(str(key)) if isinstance(key, str) else None
        if located is None or not isinstance(zh, str) or not zh.strip():
            continue  # 清单外条目或空简介：丢弃，下一轮重试
        entry = located
        entry["description_zh"] = zh.strip()
        entry["desc_fp"] = description_fingerprint_text(str(key))
        entry["desc_source"] = "ai"
        generated += 1
    return generated


__all__ = ["attach_ai_intros"]
