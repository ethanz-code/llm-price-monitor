"""模型名归一化与价格数值处理。"""
from __future__ import annotations

import re
from typing import Any


def model_key(model: str) -> str:
    """官方价缓存与匹配用的模型键：去掉空格/横线/下划线并 casefold。

    使 "GPT-5.6 Sol"、"gpt-5.6-sol"、"gpt_5_6_SOL" 归一到同一个键。
    """
    return re.sub(r"[\s_-]+", "", model).casefold()


def round2(value: Any) -> Any:
    return round(value, 2) if isinstance(value, (int, float)) else value
