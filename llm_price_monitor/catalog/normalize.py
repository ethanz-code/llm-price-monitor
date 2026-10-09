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


# 模型 id 是 ASCII；中文（含全角符号）出现在抓取来的名字里只可能是页面标注
# （"正式版"、"上下文缓存享有折扣"）或整行页面文案（"参见模型列表"）被连了进来
_PAGE_NOTE_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]+")
# 一行并排多个模型名（"MiMo-v2.6-Pro、MiMo-v2.5-Pro"）：价列只有一份，
# 拆开必有一边拿到错价，整行丢弃比猜着拆安全
_MERGED_NAME_RE = re.compile(r"[、,，；;]")


def sanitize_model_name(model: str) -> tuple[str, str | None]:
    """清洗定价页抓来的模型名；返回 (清洗后的名字, 跳过原因)。

    - 剥掉混进名字里的中文/全角标注尾巴，还原成模型 id（"deepseek-v4-pro正式版"
      → "deepseek-v4-pro"、"kimi-k3上下文缓存享有折扣" → "kimi-k3"）；
    - 剥完为空说明整行是页面文案而非模型（"参见模型列表"、"腾讯元器"），丢弃；
    - 并排多模型共价行丢弃，返回原因。
    """
    name = str(model or "").strip()
    if _MERGED_NAME_RE.search(name):
        return "", "并排多模型共价行"
    cleaned = _PAGE_NOTE_RE.sub("", name).strip(" -_.")
    if not cleaned:
        return "", "整行页面文案，非模型"
    return cleaned, None
