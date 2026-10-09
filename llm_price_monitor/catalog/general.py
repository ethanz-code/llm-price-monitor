"""通用对话大模型判定：官方目录、厂商定价源与监控清单自动维护共用的准入过滤器。

本系统只关注通用大语言模型的官方价格。两类条目不收：

- 特殊领域模型：生图/音乐/TTS/视频生成、ASR/OCR/文档解析、向量/重排、角色扮演/
  翻译等专用模型。models.dev 对其中不少的 modalities 标注与对话模型相同，
  国内定价页则完全没有标注，所以形态判断之外必须辅以名字黑名单；
- 日期后缀的快照变体：厂商给同一模型按发布快照起的版本名（gpt-4o-2024-05-13、
  deepseek-v4-pro-0813、doubao-seed-1-6-250615、qwen3.8-max-0902）。监控基准价
  只认模型主线名，快照变体一律不进目录。
"""
from __future__ import annotations

import re
from typing import Any

# 名字黑名单——子串匹配：向量/重排/识别/语音/文档解析类，以及知名生成产品线
# （海螺视频）与国内定价页的中文名条目（图生视频等）
_NAME_BLOCK_SUBSTR = (
    "embed", "rerank", "ocr", "audio", "hailuo", "character", "structure",
    "seedance", "图生视频", "文生图", "视频生成", "语音合成", "数字人",
)
# 名字黑名单——整段匹配（按 -_. 空格切段）：视觉（vl/vision）、语音（asr/tts）、
# 实时（realtime）、翻译/角色扮演等专用模型与全模态生成系列（h3）
_NAME_BLOCK_TOKENS = frozenset({
    "role", "translation", "vision", "vl", "asr", "tts", "speech",
    "video", "image", "realtime", "voice", "live", "h3",
    # 媒体生成产品线的形态标记（国内定价页普遍不标 modalities，只能靠名字拦）：
    # 文生图 t2i、图生图 i2i、文生视频 t2v、图文生视频 it2v、图生视频 i2v、
    # 参考图生视频 r2v、首尾帧生视频 kf2v、通用视频生成 text2video，
    # 以及定向能力档（万相 vace、意图识别 intent、会话分析 analysis）
    "t2i", "i2i", "t2v", "it2v", "i2v", "r2v", "kf2v", "vace",
    "text2video", "intent", "analysis",
})

# 万相系生图/视频模型（wan2.7-t2v、wanx2.1-t2i-plus、wanx-v1）：整族都是媒体
# 生成，名字前缀一刀切，不依赖形态标记凑齐
_WAN_MEDIA_PREFIX = re.compile(r"^wan(?:x)?(?:\d|-)")

# 日期后缀快照变体（对 casefold 后的名字做行尾匹配）：
# - 2025-11-17 全日期 / 20240620 全数字全日期
# - 250615 YYMMDD（25-29 年）；0902/0813/0731 MMDD
# YYMM 四位版本号（step-3.5-flash-2603）不是日期，不匹配
_DATE_SUFFIX = re.compile(
    r"(?:20\d{2}-\d{2}-\d{2}|20\d{6}|2[5-9](?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
    r"|(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]))$"
)


def is_snapshot_variant(model: str) -> bool:
    """模型名是否日期后缀的快照变体（如 gpt-4o-2024-05-13、deepseek-v4-pro-0813）。"""
    return bool(_DATE_SUFFIX.search(str(model).casefold()))


def is_general_llm(entry: dict[str, Any]) -> bool:
    """目录条目是否通用对话大模型；官方目录构建、厂商定价源与监控清单自动维护共用。

    - 名字命中黑名单（子串/切段）或日期后缀快照变体：跳过；
    - 输出非纯文本（含 image/audio/video）：生图、音乐、TTS、视频生成，跳过；
    - 输入不含 text（纯音频/纯图输入）：ASR 语音识别、OCR，跳过；
    - 未标 modalities 的条目（国内定价页普遍如此）按名字过一遍后照常参与。
    """
    name = str(entry.get("model") or "").casefold()
    if any(tag in name for tag in _NAME_BLOCK_SUBSTR):
        return False
    tokens = {token for token in re.split(r"[-_. ]+", name) if token}
    if tokens & _NAME_BLOCK_TOKENS:
        return False
    if _WAN_MEDIA_PREFIX.match(name):
        return False
    if _DATE_SUFFIX.search(name):
        return False
    modalities = entry.get("modalities")
    if not isinstance(modalities, dict):
        return True
    if set(modalities.get("output") or []) != {"text"}:
        return False
    return "text" in (modalities.get("input") or [])
