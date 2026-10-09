"""目标模型的别名匹配：判断一段文本/URL/JSON 值是否指向配置中的模型。

大小写、空格、连字符、供应商前缀（openai/xxx）差异都在这里归一化，
是 AI 抽取和网页证据筛选共同依赖的纯函数层。
"""
from __future__ import annotations

import re


def model_aliases(model: str) -> tuple[str, ...]:
    normalized = model.strip().casefold()
    if not normalized:
        return ()
    aliases = [normalized]
    if "/" in normalized:
        aliases.append(normalized.rsplit("/", 1)[-1])
    else:
        aliases.append(f"openai/{normalized}")
    return tuple(dict.fromkeys(sorted(aliases, key=len, reverse=True)))


def model_alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9._-]){re.escape(alias)}(?![a-z0-9._-])", re.IGNORECASE)


def compact_model_text(value: str) -> str:
    """将大小写、空格、连字符和供应商分隔符差异归一化。"""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def contains_model_alias(value: str, model: str) -> bool:
    lowered = value.casefold()
    if any(model_alias_pattern(alias).search(lowered) for alias in model_aliases(model)):
        return True
    compact_value = compact_model_text(value)
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(compact_model_text(alias))}(?![a-z0-9])", compact_value)
        for alias in model_aliases(model)
    )


def canonical_target(model: str, expected_models: list[str]) -> str | None:
    for expected in expected_models:
        if contains_model_alias(model, expected) and contains_model_alias(expected, model):
            return expected
    return None
