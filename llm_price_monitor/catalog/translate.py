"""模型简介中文翻译：把目录条目的英文简介交给 AI 批量翻译，结果随目录持久化。

翻译结果与简介指纹（desc_fp）一起保存在条目里（description_zh / desc_fp），
简介没变的条目不重复翻译；AI 未配置、调用失败或返回不可解析时静默跳过，
条目保持英文原文，下一轮重试。全量渠道目录条目数以千计，用 budget 限制
单轮新增翻译量，随每日刷新逐步补齐。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx

from llm_price_monitor.ai import AIExtractionError
from llm_price_monitor.config import AIConfig

from .classify import _chat_json, ai_available

SYSTEM_PROMPT = """\
你是科技文档翻译。给你一批 AI 模型的英文简介（JSON：models 数组，每项含 model 标识与
description 英文简介）。把每条 description 翻译成简洁、自然的简体中文：模型名与专有名词
（token、context、benchmark 等）保留通用写法；数字、价格、单位原样保留；不逐词直译，
输出读起来像人写的介绍。只输出 JSON：
{"translations": [{"model": "<原样返回 model>", "zh": "<中文简介>"}]}，
translations 必须覆盖每个 model。"""

# 单次 AI 请求的简介条数上限：简介普遍几百字，控制请求体积避免截断
BATCH_SIZE = 30


def description_fingerprint(entry: dict[str, Any]) -> str:
    """简介翻译的输入指纹：简介原文变了才需要重新翻译。"""
    raw = str(entry.get("description") or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def fingerprint_translations(doc: dict[str, Any] | None) -> dict[str, str]:
    """从已存目录提取 指纹 -> 中文简介 映射，供其他目录复用已有译文。"""
    out: dict[str, str] = {}
    for entry in (doc.get("models", {}) if isinstance(doc, dict) else {}).values():
        if isinstance(entry, dict) and entry.get("description_zh") and entry.get("desc_fp"):
            out[str(entry["desc_fp"])] = str(entry["description_zh"])
    return out


def attach_zh_descriptions(
    output: dict[str, Any],
    previous: dict[str, Any] | None,
    config: AIConfig,
    client: httpx.Client | None = None,
    budget: int | None = None,
    seed: dict[str, str] | None = None,
) -> int:
    """给目录条目补中文简介，返回本轮新翻译的模型数。

    指纹未变的条目沿用上一轮译文；其余按批次调 AI，budget 限制单轮新增条数
    （先到先得，剩余留给下一轮）。简介指纹相同的条目只翻一次，译文回填给全部
    重复条目；seed 允许复用其他目录已有的译文（如官方目录与全量渠道目录互济）。
    AI 不可用或某批失败时静默跳过，单批失败不影响其余批次。
    """
    if not ai_available(config):
        return 0
    reuse: dict[str, str] = dict(seed or {})
    previous_models = previous.get("models", {}) if isinstance(previous, dict) else {}
    pending: dict[str, tuple[str, dict[str, Any]]] = {}  # 指纹 -> (模型标识, 代表条目)
    for key, entry in output.get("models", {}).items():
        if not entry.get("description"):
            continue
        fingerprint = description_fingerprint(entry)
        old = previous_models.get(key)
        if isinstance(old, dict) and old.get("desc_fp") == fingerprint and old.get("description_zh"):
            reuse.setdefault(fingerprint, str(old["description_zh"]))
        zh = reuse.get(fingerprint)
        if zh is not None:
            entry["description_zh"] = zh
            entry["desc_fp"] = fingerprint
            continue
        if budget is not None and len(pending) >= budget:
            continue  # 本轮额度用完，剩余条目下一轮再翻
        pending.setdefault(fingerprint, (key, entry))
    translated = 0
    representatives = list(pending.values())
    for start in range(0, len(representatives), BATCH_SIZE):
        try:
            translated += _translate_batch(config, representatives[start : start + BATCH_SIZE], client)
        except (AIExtractionError, httpx.HTTPError, ValueError):
            continue  # 这批保持原文下一轮重试，其余批次照常
    for _key, entry in representatives:  # 本轮新翻的译文也入池，供同指纹条目复用
        if entry.get("description_zh"):
            reuse[description_fingerprint(entry)] = str(entry["description_zh"])
    # 已有译文的指纹（含本轮新翻的）回填给同指纹的其余条目
    for entry in output.get("models", {}).values():
        if not isinstance(entry, dict) or entry.get("description_zh") or not entry.get("description"):
            continue
        zh = reuse.get(description_fingerprint(entry))
        if zh is not None:
            entry["description_zh"] = zh
            entry["desc_fp"] = description_fingerprint(entry)
    return translated


def _translate_batch(
    config: AIConfig,
    batch: list[tuple[str, dict[str, Any]]],
    client: httpx.Client | None,
) -> int:
    """一个批次的 AI 翻译：返回成功落盘的条数；失败抛异常由调用方兜底。"""
    payload = [{"model": key, "description": entry.get("description")} for key, entry in batch]
    translations = _chat_json(
        config,
        SYSTEM_PROMPT,
        f'{{"models": {json.dumps(payload, ensure_ascii=False)}}}',
        client,
    ).get("translations")
    if not isinstance(translations, list):
        raise ValueError("AI 翻译返回缺少 translations 数组")
    by_key = dict(batch)
    translated = 0
    for item in translations:
        if not isinstance(item, dict):
            continue
        key = item.get("model")
        zh = item.get("zh")
        located = by_key.get(str(key)) if isinstance(key, str) else None
        if located is None or not isinstance(zh, str) or not zh.strip():
            continue  # 清单外条目或空译文：丢弃，下一轮重试
        entry = located
        entry["description_zh"] = zh.strip()
        entry["desc_fp"] = description_fingerprint(entry)
        translated += 1
    return translated
