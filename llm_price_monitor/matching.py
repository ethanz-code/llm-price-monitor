"""目标模型的别名匹配：判断一段文本/URL/JSON 值是否指向配置中的模型。

大小写、空格、连字符、供应商前缀（openai/xxx）差异都在这里归一化，
是 AI 抽取和网页证据筛选共同依赖的纯函数层。
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


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
    folded = model.strip().casefold()
    exact = next((expected for expected in expected_models if expected.strip().casefold() == folded), None)
    if exact is not None:
        return exact
    for expected in expected_models:
        if contains_model_alias(model, expected) and contains_model_alias(expected, model):
            return expected
    return next(
        (expected for expected in expected_models if version_omitted_match(model, expected)),
        None,
    )


# 版本号段：v4 / 4 / 1 / 0731 这类只有数字（可带 v 前缀）的词；其余词是模型家族名。
_VERSION_SEGMENT = re.compile(r"^(?:v?\d+(?:\.\d+)*|\d{4,})$", re.IGNORECASE)


def _name_words(model: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """模型名 →（家族词, 版本词）；"deepseek-v4.1-flash" → (("deepseek", "flash"), ("v4", "1"))。"""
    words = [word for word in re.split(r"[^a-z0-9]+", model.casefold()) if word]
    family = tuple(word for word in words if not _VERSION_SEGMENT.match(word))
    version = tuple(word for word in words if _VERSION_SEGMENT.match(word))
    return family, version


def version_omitted_match(candidate: str, model: str) -> bool:
    """一侧完全省略了版本号、另一侧带版本号，且家族词完全一致时视为同一模型。

    站点常把 "DeepSeek V4.1 Flash" 写成 "deepseek-flash"，这里让两者能对上。
    故意收窄成"恰好一侧没有版本号"：`deepseek-v4-flash` 与 `deepseek-v4.1-flash`
    两侧都有版本号，是两个模型，不能合并。
    """
    candidate_family, candidate_version = _name_words(candidate)
    model_family, model_version = _name_words(model)
    if not candidate_family or candidate_family != model_family:
        return False
    return bool(candidate_version) != bool(model_version)


def resolve_site_names(
    site_names: Sequence[str],
    accepted: Mapping[str, Sequence[str]],
) -> dict[str, str]:
    """站点侧模型名 → 目标模型名：精确命中优先，其次"一侧省略版本号"，歧义宁缺勿猜。

    `accepted` 是 目标模型名 → 该目标可接受的名字（目标名本身 + AI 解析出的别名）。
    第二趟只在唯一候选时采信，因此站点同时挂着 v4 与 v4.1 两版时会放弃匹配，
    而不是随便挑一个把价格挂到错的模型上。返回 {目标名: 站点侧名字}。
    """
    unique: dict[str, str] = {}
    for name in site_names:
        unique.setdefault(name.casefold(), name)

    resolved: dict[str, str] = {}
    claimed: set[str] = set()
    for target, names in accepted.items():
        wanted = {name.casefold() for name in names}
        hit = next((original for key, original in unique.items() if key in wanted), None)
        if hit is not None:
            resolved[target] = hit
            claimed.add(hit.casefold())
    for target, names in accepted.items():
        if target in resolved:
            continue
        candidates = [
            original
            for key, original in unique.items()
            if key not in claimed
            and any(version_omitted_match(key, name) for name in names)
        ]
        if len(candidates) == 1:
            resolved[target] = candidates[0]
            claimed.add(candidates[0].casefold())
    return resolved
