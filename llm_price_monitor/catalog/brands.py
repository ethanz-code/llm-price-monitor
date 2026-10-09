"""模型名 → 官方厂商归属：官方价目录只收各厂商自研模型的判定依据。

国内定价页（厂商定价源）与 models.dev 平台渠道都大量列出托管/转售的第三方
模型（百度千帆卖 DeepSeek、阿里百炼代售 deepseek 系列、models.dev 的 alibaba
渠道下也有 kimi/deepseek 条目）。合并进官方价目录前按模型名判品牌：归属厂商
与渠道厂商不一致的条目是托管渠道价，不进官方目录（全量渠道价目录照常保留，
比价场景不受影响）。

关键词在 model_key 归一化（去空格/横线/下划线、casefold）后的模型名上做子串
匹配；带 "^" 前缀的关键词改为整名前缀匹配（OpenAI 的 o1/o3/o4/o5 系列——
裸子串会误伤 veo-3.1、video-1.5 这类名字）。顺序有语义——跨厂商关键词同现时
先命中者赢（如 deepseek-r1-distill-qwen 同时含 deepseek 与 qwen，DeepSeek 必须
排在 Alibaba Cloud 前；gpt-5.3-codex-spark 同时含 gpt 与 spark，OpenAI 必须排在
iFlytek 前）。没命中任何关键词返回 None，由调用方按渠道性质决定默认口径：
models.dev 白名单是官方 lab 本店（默认视为自研），国内定价页是渠道店（默认
不进官方目录）。
"""
from __future__ import annotations

from .normalize import model_key

# 厂商名与 vendor_sources.DOMESTIC_BRANDS / modelsdev.DEFAULT_PROVIDERS 的显示名一致
MODEL_BRAND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DeepSeek", ("deepseek",)),
    ("Zhipu AI", ("glm", "chatglm", "codegeex", "cogview", "cogvideo", "zhipu")),
    ("Moonshot AI", ("kimi", "moonshot")),
    ("Alibaba Cloud", ("qwen", "qwq", "qvq", "tongyi", "wan")),
    ("Baidu", ("ernie", "paddle", "ppstructure", "musesteamer")),
    ("Volcengine Ark", ("doubao",)),
    ("MiniMax", ("minimax", "abab")),
    ("Tencent", ("hunyuan", "tencent")),
    ("StepFun", ("stepfun", "step")),
    ("SenseNova", ("sensenova", "sense", "nova")),
    ("Xiaomi", ("mimo",)),
    ("01.AI", ("yi",)),
    ("Baichuan", ("baichuan",)),
    ("SiliconFlow", ("siliconflow",)),
    # 国际 lab 的关键词区分度高，排在 iFlytek（"spark"/"xing" 这类宽泛词）前；
    # o 系列只能前缀匹配，裸子串会误伤 veo-3.1 / video-1.5
    ("OpenAI", ("gpt", "chatgpt", "davinci", "whisper", "dalle", "sora", "^o1", "^o3", "^o4", "^o5")),
    ("Anthropic", ("claude",)),
    ("Google", ("gemini", "gemma", "veo", "imagen")),
    ("xAI", ("grok",)),
    ("iFlytek", ("spark", "xinghuo", "xingchen", "xing")),
)


def home_vendor(model: str) -> str | None:
    """模型名（原始大小写/横线均可）→ 官方厂商显示名；识别不出返回 None。

    "^" 前缀的关键词按整名前缀匹配，其余按子串匹配。
    """
    key = model_key(str(model))
    if not key:
        return None
    for vendor, keywords in MODEL_BRAND_KEYWORDS:
        for keyword in keywords:
            if keyword.startswith("^"):
                if key.startswith(keyword[1:]):
                    return vendor
            elif keyword in key:
                return vendor
    return None


def vendor_key(vendor: str) -> str:
    """厂商名归一化键（与模型键同规则）：管理台配置的 "ZhipuAI" 与品牌表 "Zhipu AI" 视为同一家。"""
    return model_key(str(vendor))


def is_hosted_model(model: str, vendor: str) -> bool:
    """模型是否托管/转售条目：能识别出官方厂商且与渠道厂商不是同一家（厂商名归一化比较）。"""
    home = home_vendor(model)
    return home is not None and vendor_key(home) != vendor_key(vendor)


def is_unbranded(model: str) -> bool:
    """模型名是否识别不出品牌归属（第三方开源模型、平台服务项等）。"""
    return home_vendor(model) is None


def skip_reason(model: str, vendor: str) -> str:
    """合并摘要用的跳过说明；与 is_hosted_model / is_unbranded 同口径。"""
    home = home_vendor(model)
    if home is None:
        return f"{vendor}/{model}（识别不出官方归属，不进官方目录）"
    return f"{vendor}/{model}（托管/转售模型，官方归属 {home}，不进官方目录）"


__all__ = ["MODEL_BRAND_KEYWORDS", "home_vendor", "vendor_key", "is_hosted_model", "is_unbranded", "skip_reason"]
