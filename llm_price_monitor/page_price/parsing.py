"""页面价格解析的原子原语：币种识别、单位折算、价格单元格取数、模型名清洗与上下文窗口解析。"""
from __future__ import annotations

import re

from llm_price_monitor.units import number_or_none

# 与站点采集一致的判空单元格集合；破折号是定价页最常见的空值写法
_EMPTY_CELLS = {"", "—", "-", "–", "/", "n/a", "na", "null", "none"}
_FREE_PATTERN = re.compile(r"免费|free", re.IGNORECASE)
_NUMBER_PATTERN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_CNY_PATTERN = re.compile(r"元|¥|￥|人民币|rmb|\bcny\b", re.IGNORECASE)
_USD_PATTERN = re.compile(r"\$|美元|usd|dollar", re.IGNORECASE)


def detect_currency(*texts: str | None) -> str | None:
    """从表头/单位文本里判断币种：人民币标识优先于美元（国内定价页默认口径）。"""
    joined = " ".join(text for text in texts if text)
    if _CNY_PATTERN.search(joined):
        return "CNY"
    if _USD_PATTERN.search(joined):
        return "USD"
    return None


def unit_scale(header_text: str) -> float:
    """页面标价换算成"每百万 tokens"要乘的系数；识别不出按国内定价页惯例的百万口径。"""
    text = header_text.replace(" ", "")
    if re.search(r"百万|1M|/M(?![A-Za-z])|million", text, re.IGNORECASE):
        return 1.0
    if re.search(r"千|1K|每千|thousand", text, re.IGNORECASE):
        return 1000.0
    if re.search(r"每token|/token|per-token", text, re.IGNORECASE):
        return 1_000_000.0
    return 1.0


def price_value(cell: str) -> tuple[float | None, bool]:
    """单元格 → (数值, 是否可解析)。免费按 0 处理；空/破折号视为没有这个价格。"""
    text = cell.strip()
    if text.casefold() in _EMPTY_CELLS:
        return None, False
    if _FREE_PATTERN.search(text):
        return 0.0, True
    match = _NUMBER_PATTERN.search(text.replace(" ", ""))
    if match is None:
        return None, False
    return number_or_none(match.group(0).replace(",", "")), True


# 国内定价页惯用「模型名（200K）」把上下文档位写进名称单元格（如 GLM-4.7-Flash（200K）），
# 尾部的括号限定符不是模型名的一部分，剥掉后才能与厂商目录的模型键对上
_TRAILING_QUALIFIER_PATTERN = re.compile(r"[（(][^（）()]*[)）]\s*$")
# 阿里云百炼惯用「模型ID/中文注解」同格写法（如 qwen3.8-max/Batch调用半价），
# 注解段带 CJK 才当注解剥掉——ZHIPU/GLM-5.3 这类路径式模型名不含 CJK，保留原样
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def clean_model_name(name: str) -> str:
    """剥掉模型名单元格里的注解（斜杠/换行后的中文说明、尾部括号限定符），保留原始大小写。

    阿里云百炼把「Batch调用半价」这类注解跟在模型 ID 后面，分隔既有斜杠也有换行；
    注解段带 CJK 才剥——ZHIPU/GLM-5.3 这类路径式模型名不含 CJK，保留原样。
    """
    text = name.strip()
    segments = re.split(r"/|[\r\n]+", text)
    for index, segment in enumerate(segments[1:], start=1):
        if _CJK_PATTERN.search(segment):
            text = segments[0]
            break
    return _TRAILING_QUALIFIER_PATTERN.sub("", text).strip()


# 上下文窗口文本的取数与单位：K/M 按二进制口径（与 Kimi 官方标注 1,048,576 tokens 一致）
_CONTEXT_TOKEN_PATTERN = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(K|M|B|万|亿)?", re.IGNORECASE)
_CONTEXT_TOKEN_SCALE = {"K": 1024, "M": 1024**2, "B": 1024**3, "万": 10_000, "亿": 10_000**2}
# 显式输出上限标注：标注后的数字是输出上限而不是上下文
_OUTPUT_LABEL_PATTERN = re.compile(r"最大输出|输出上限|输出|max(?:imum)?[\s-]*output", re.IGNORECASE)
# "8K~1M"这类可取值区间读不出单一上限，宁缺勿猜
_CONTEXT_RANGE_PATTERN = re.compile(r"\d[^/／]*[-~～–—]\s*\d")


def _context_tokens(text: str) -> int | None:
    """一段文本里第一个「数字+单位」→ token 数；读不出 ≥1 的整数返回 None。"""
    match = _CONTEXT_TOKEN_PATTERN.search(text)
    if match is None:
        return None
    value = float(match.group(1).rstrip(",.、；;，").replace(",", ""))
    scale = _CONTEXT_TOKEN_SCALE.get((match.group(2) or "").upper(), 1)
    tokens = int(value * scale)
    return tokens if tokens >= 1 else None


def parse_context_limit(text: str | None) -> dict[str, int | None] | None:
    """定价页上下文列原文 → {"context": 上限, "output": 输出上限或 None}；解析不出返回 None。

    认三种形态：单值（"1M"、"1,048,576 tokens"）、斜杠对（"200K/32K"）、
    输出标注（"256K；最大输出 32K"）。时段名（"高峰时段"）、区间（"8K~1M"）、
    输入价格分档（"[0, 32k)"）这些不是上下文窗口的文本一律返回 None，不猜。
    """
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw or raw.casefold() in _EMPTY_CELLS or not re.search(r"\d", raw):
        return None
    if _CONTEXT_RANGE_PATTERN.search(raw):
        return None
    parts = [part for part in re.split(r"[/／]", raw) if part.strip()]
    if len(parts) >= 2:
        context = _context_tokens(parts[0])
        if context is None:
            return None
        return {"context": context, "output": _context_tokens(parts[1])}
    label = _OUTPUT_LABEL_PATTERN.search(raw)
    if label:
        context = _context_tokens(raw[: label.start()])
        if context is None:
            return None
        return {"context": context, "output": _context_tokens(raw[label.end() :])}
    context = _context_tokens(raw)
    if context is None:
        return None
    return {"context": context, "output": None}
