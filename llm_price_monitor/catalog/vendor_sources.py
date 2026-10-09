"""厂商定价源：管理台配置国内厂商定价页，抓取结果合并为官方价基准。

背景：models.dev 的国内价格不进目录（时段分档缺失、他方汇率换算，实测与
官方人民币标价偏差大），国内基准价只认这里抓到的官方定价页——无论厂商在
models.dev 是否收录、是否带国内站口径（智谱的 zhipuai/zai 甚至都指向 z.ai）。
本模块提供三件事：

- **覆盖检测**：`DOMESTIC_BRANDS` 品牌表人工整理各厂商在 models.dev 里的渠道
  与国内站口径，`detect_vendor_coverage` 据此判定每个品牌是「已覆盖 / 仅国际
  口径（推荐添加）/ 未收录（推荐添加）」；输入是目录 meta 里的 `providers`
  厂商清单（见 modelsdev._provider_inventory），零网络开销。
- **定价源存取**：管理员为厂商配置一个公开定价页 URL（无鉴权字段），抓取复用
  `page_price.fetch_page_prices`（静态解析优先、AI 兜底防幻觉），结果连同状态
  存 `vendor_sources` 文档（读改写加进程内锁）。源带 `region`：国内源（cn）
  的页价作为国内折扣基准，海外源（global）只作国际参考价。
- **合并**：`merge_sources_into_catalog` 把抓到的价格按 `model_key` 合并进官方
  价目录——国内源命中条目则基准换成页价、原国际价退居 `list_global` 参考，
  未命中则新增条目（region=cn）；海外源只给 region=cn 的命中条目补 `list_global`
  参考价，不动国内基准。目录统一 USD 口径：CNY 页价按快照汇率折算存
  `list`，`list_cny` 存页面标价的精确人民币值。抓到的上下文窗口解析成 `limit`
  数字、简介写进 `description_zh`（登记 desc_fp 跳过翻译），只在条目缺这两项
  时补——models.dev 已有的元数据不覆盖。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from llm_price_monitor.config import AIConfig

from llm_price_monitor.catalog import fx
from llm_price_monitor.catalog.brands import is_hosted_model, is_unbranded, skip_reason
from llm_price_monitor.catalog.general import is_general_llm
from llm_price_monitor.catalog.normalize import model_key, round2
from llm_price_monitor.catalog.translate import description_fingerprint_text
from llm_price_monitor.page_price import fetch_page_prices, parse_context_limit
from llm_price_monitor.units import number_or_none

VENDOR_SOURCES_DOCUMENT = "vendor_sources"

# 读改写与后台任务可能并发（管理台编辑 vs 刷新任务），与 ai_cache 同款进程内锁
_LOCK = threading.Lock()


@dataclass(frozen=True)
class DomesticBrand:
    """一个国内厂商品牌在 models.dev 里的渠道对照，覆盖检测的判定依据。

    aliases: models.dev 的 provider id（完整匹配）；cn_provider_ids: 其中属于
    国内站口径的渠道；keywords: 名称/文档 URL 兜底关键词（models.dev 未来新增
    厂商也能被识别）；suggested_url: 已核验的国内定价页（2026-09-29 逐页实测：
    静态解析可直接出价，或原始 HTML 有内容、AI 兜底可出价）；suggested_json_url:
    已核验的 JSON 价格接口（可选）。两者在推荐添加时并列预填，标注采集类型。
    解析会取错价或拿不到内容的页面一律不预填，避免脏价静默进基准。
    """

    vendor: str
    aliases: tuple[str, ...]
    cn_provider_ids: tuple[str, ...]
    keywords: tuple[str, ...]
    suggested_url: str = ""
    suggested_json_url: str = ""
    note: str = ""


# 人工整理（2026-09 核对 models.dev 快照 221 个渠道）；带价与否以快照为准，不在表里写死
DOMESTIC_BRANDS: tuple[DomesticBrand, ...] = (
    DomesticBrand(
        vendor="Zhipu AI",
        aliases=("zhipuai", "zai", "zhipuai-coding-plan", "zai-coding-plan"),
        cn_provider_ids=(),  # zhipuai/zai 的文档都指向 docs.z.ai 国际站
        keywords=("zhipu", "bigmodel", "z.ai"),
        suggested_url="https://docs.bigmodel.cn/cn/guide/start/pricing.md",
        note="GLM 系列最全的一家（GLM-5.3 到 Flash 轻量档共 32 个模型）；官方 .md 价目表直连解析，不依赖 AI，最稳",
    ),
    DomesticBrand(
        vendor="DeepSeek",
        aliases=("deepseek",),
        cn_provider_ids=("deepseek",),  # 单条目已核与官方中文价页空闲时段一致
        keywords=("deepseek",),
        suggested_url="https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
        note="V4 系列（Flash/Pro），以低价著称；官方价分峰谷时段、闲时半价，基准取高峰标准档",
    ),
    DomesticBrand(
        vendor="Moonshot AI",
        aliases=("moonshotai", "moonshotai-cn"),
        cn_provider_ids=("moonshotai-cn",),
        keywords=("moonshot",),
        suggested_url="https://platform.kimi.com/docs/pricing/chat",
        note="Kimi K3/K2.7 系列（K3 支持百万级上下文）；另有 code 编程档与 highspeed 提速加价档，共 4 个模型",
    ),
    DomesticBrand(
        vendor="Alibaba Cloud",
        aliases=(
            "alibaba", "alibaba-cn", "alibaba-token-plan", "alibaba-token-plan-cn",
            "alibaba-coding-plan", "alibaba-coding-plan-cn",
        ),
        cn_provider_ids=("alibaba-cn", "alibaba-token-plan-cn", "alibaba-coding-plan-cn"),
        keywords=("alibaba", "aliyun"),
        suggested_url="https://help.aliyun.com/zh/model-studio/model-pricing",
        note="千问 Qwen 全系 + 聚合第三方模型，开源阵容最大的一家；价格页超大，AI 分块提取约十分钟，先覆盖千问主力档",
    ),
    DomesticBrand(
        vendor="MiniMax",
        aliases=("minimax", "minimax-cn", "minimax-coding-plan", "minimax-cn-coding-plan"),
        cn_provider_ids=("minimax-cn", "minimax-cn-coding-plan"),
        keywords=("minimax",),
        suggested_url="https://platform.minimax.cn/docs/guides/pricing-paygo",
        note="M 系列文本主力，语音/视频同厂交付；价格分 512k 上下文档与 highspeed 档，基准取标准档",
    ),
    DomesticBrand(
        vendor="Volcengine Ark",
        aliases=("volcengine", "volcengine-coding-plan"),
        cn_provider_ids=("volcengine", "volcengine-coding-plan"),
        keywords=("volcengine",),
        suggested_url="https://docs.volcengine.com/docs/ark/model-pricing",
        note="豆包 doubao-seed 全系 + 平台代售 DeepSeek 等，文本/生图/视频一张价目表；29 个模型入库",
    ),
    DomesticBrand(
        vendor="Tencent",
        aliases=("tencent-tokenhub", "tencent-coding-plan", "tencent-token-plan"),
        cn_provider_ids=("tencent-tokenhub", "tencent-coding-plan", "tencent-token-plan"),
        keywords=("tencent",),
        suggested_url="https://cloud.tencent.com/document/product/1729/97731",
        note="混元家族全系（a13b/role/translation/vision 等）；计费带峰谷时段，基准取高峰档",
    ),
    DomesticBrand(
        vendor="StepFun",
        aliases=("stepfun", "stepfun-ai", "stepfun-step-plan", "stepfun-ai-step-plan"),
        cn_provider_ids=("stepfun", "stepfun-step-plan"),
        keywords=("stepfun",),
        suggested_url="https://platform.stepfun.com/docs/zh/guides/pricing/details",
        note="Step 3.x 系列、含视觉理解型号；10 个模型明码标价，静态直解析",
    ),
    DomesticBrand(
        vendor="SiliconFlow",
        aliases=("siliconflow", "siliconflow-cn"),
        cn_provider_ids=("siliconflow-cn",),
        keywords=("siliconflow",),
        suggested_url="https://www.siliconflow.cn/pricing",
        note="聚合托管平台：一份页价覆盖 GLM/DeepSeek/千问/Kimi 等各家开源模型，横向比价最方便",
    ),
    DomesticBrand(
        vendor="SenseNova",
        aliases=("sensenova",),
        cn_provider_ids=("sensenova",),
        keywords=("sensenova",),
        suggested_url="https://www.sensecore.cn/help/docs/model-as-a-service/nova/pricing",
        note="商汤日日新 V6.5 系列；按千 tokens 计价（入库已折算百万口径），语音按次计费的型号没入库",
    ),
    DomesticBrand(
        vendor="Xiaomi",
        aliases=("xiaomi-token-plan-cn",),
        cn_provider_ids=("xiaomi-token-plan-cn",),
        keywords=("xiaomi",),
        suggested_url="https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go",
        note="MiMo 系列（pro/flash/ultraspeed 三档速度），模型少而精；ASR 按小时计价未入库",
    ),
    # models.dev 未收录的常见国内厂商：关键词用于将来新增渠道时自动识别
    DomesticBrand(vendor="Baidu", aliases=(), cn_provider_ids=(), keywords=("baidu", "qianfan"),
                  suggested_url="https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya",
                  note="ERNIE 5.1/4.5 企业级系列，40 个模型（含聚合的 DeepSeek/GLM/Kimi 与 OCR/Embedding）"),
    DomesticBrand(vendor="iFlytek", aliases=(), cn_provider_ids=(), keywords=("iflytek", "xfyun", "xinghuo"),
                  suggested_url="https://xinghuo.xfyun.cn/sparkapi",
                  note="星火 X2 系列 + 星辰 MaaS 广场（GLM/Kimi/DeepSeek 同场在售）；免费小型号标 0 价"),
    DomesticBrand(vendor="01.AI", aliases=(), cn_provider_ids=(), keywords=("01.ai", "lingyi", "wanwu"),
                  suggested_url="https://platform.lingyiwanwu.com/docs",
                  note="零一走单主力路线（yi-lightning 仅 2 个模型）；定价不分输入输出、统一价"),
    DomesticBrand(vendor="Baichuan", aliases=(), cn_provider_ids=(), keywords=("baichuan",),
                  suggested_url="https://platform.baichuan-ai.com/prices",
                  note="百川 M3/Baichuan4 系列；部分模型输入输出合并计价（按并价入库），复核时留意"),
)


def validate_source(vendor: str, url: str) -> tuple[str, str]:
    """厂商名与 URL 校验；返回 (规整后的厂商名, URL)，非法抛 ValueError。"""
    vendor = (vendor or "").strip()
    url = (url or "").strip()
    if not vendor:
        raise ValueError("厂商名不能为空")
    if len(vendor) > 60:
        raise ValueError("厂商名过长（60 字以内）")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL 必须是完整的 http(s) 地址")
    return vendor, url


def validate_region(region: str) -> str:
    """区域规整：cn=国内基准 / global=国际参考；缺省 cn，非法值抛 ValueError。"""
    value = str(region or "").strip().lower()
    if value in {"", "cn"}:
        return "cn"
    if value == "global":
        return "global"
    raise ValueError("区域只支持 cn（国内基准）或 global（国际参考）")


def load_sources(store: Any) -> dict[str, dict[str, Any]]:
    """读取厂商定价源配置与最近抓取结果；返回 {厂商名: 记录}。"""
    doc = store.get_document(VENDOR_SOURCES_DOCUMENT)
    sources = doc.get("sources") if isinstance(doc, dict) else None
    return {str(k): v for k, v in sources.items() if isinstance(v, dict)} if isinstance(sources, dict) else {}


def _save_sources(store: Any, sources: dict[str, dict[str, Any]]) -> None:
    store.set_document(VENDOR_SOURCES_DOCUMENT, {"sources": sources})


def upsert_source(
    store: Any, vendor: str, url: str, enabled: bool = True, region: str = "cn", note: str | None = None
) -> dict[str, Any]:
    """新增或更新一个定价源（只动配置字段）；返回该源记录。

    note 是给覆盖检测行展示的厂商描述（如"官方中文定价页，人民币标价"），不传则保留原值。
    换 URL 或换区域时清空上次抓取结果——models 归属旧地址与旧合并口径，
    合并前必须重新抓取。
    """
    vendor, url = validate_source(vendor, url)
    normalized_region = validate_region(region)
    with _LOCK:
        sources = load_sources(store)
        record = sources.get(vendor) or {}
        url_changed = bool(record) and record.get("url") != url
        region_changed = bool(record) and validate_region(str(record.get("region") or "")) != normalized_region
        record.update({"vendor": vendor, "url": url, "enabled": bool(enabled), "region": normalized_region})
        if note is not None:
            record["note"] = str(note).strip() or None
        if url_changed or region_changed:
            for field in ("last_fetched_at", "last_status", "last_error", "last_method", "model_count", "models"):
                record[field] = None
        record.setdefault("last_fetched_at", None)
        record.setdefault("last_status", None)
        record.setdefault("last_error", None)
        record.setdefault("last_method", None)
        record.setdefault("model_count", None)
        record.setdefault("models", None)
        sources[vendor] = record
        _save_sources(store, sources)
    return dict(record)


def delete_source(store: Any, vendor: str) -> bool:
    """删除定价源；存在返回 True。调用方负责随后恢复目录基准（触发目录刷新）。"""
    with _LOCK:
        sources = load_sources(store)
        if vendor not in sources:
            return False
        del sources[vendor]
        _save_sources(store, sources)
    return True


def _record_fetch(store: Any, vendor: str, **fields: Any) -> dict[str, Any]:
    with _LOCK:
        sources = load_sources(store)
        record = sources.get(vendor)
        if record is None:
            return {}
        record.update(fields)
        sources[vendor] = record
        _save_sources(store, sources)
    return dict(record)


def refresh_source(store: Any, vendor: str, *, timeout: float, ai_config: AIConfig | None) -> dict[str, Any]:
    """抓取单个厂商定价页并更新源文档；失败记入 last_error，不抛出。"""
    source = load_sources(store).get(vendor)
    if not source:
        raise ValueError(f"厂商定价源不存在: {vendor}")
    url = str(source.get("url") or "")
    try:
        # 静态解析失败才触发渲染，正常页零开销；SPA 定价页（火山/星火/百川等）没有这步就拿不到内容
        result = fetch_page_prices(url, ai_config=ai_config, timeout=timeout, headless=True)
    except httpx.HTTPError as exc:
        _record_fetch(store, vendor, last_fetched_at=time.time(), last_status="failed", last_error=f"抓取失败：{exc}", last_method=None, model_count=0, models=None)
        return {"vendor": vendor, "status": "failed", "error": f"抓取失败：{exc}", "model_count": 0}
    models = result.get("models") or []
    # 根源过滤：特殊领域模型与日期后缀快照变体（gpt-4o-2024-05-13、qwen3.8-max-0902
    # 这类）不落库，详情展示、覆盖检测与后续合并都只见通用对话大模型
    models = [item for item in models if is_general_llm({"model": str(item.get("model") or "")})]
    # 同一告警会因分块/AI 返回重复模型而重复（如小米 asr 列两次），去重后再存
    error = "；".join(dict.fromkeys(result.get("warnings") or [])) or None
    record = _record_fetch(
        store, vendor,
        last_fetched_at=time.time(),
        last_status="ok" if models else "empty",
        last_error=error,
        last_method=str(result.get("method") or ""),
        model_count=len(models),
        models=models,
    )
    return {
        "vendor": vendor,
        "status": record.get("last_status"),
        "method": record.get("last_method"),
        "model_count": len(models),
        "error": error,
    }


def _cn_meta_fields(item: dict[str, Any]) -> dict[str, Any]:
    """抓取记录里的上下文/简介 → 目录条目元数据字段；没有就返回空 dict。

    上下文解析成 limit 数字（输出上限可能缺）；简介是国内页的中文原文，
    直接写 description_zh 并登记 desc_fp，跳过 translate 的英→中翻译队列。
    """
    fields: dict[str, Any] = {}
    limit = parse_context_limit(item.get("context"))
    if limit and limit.get("context"):
        fields["limit"] = limit
    description = str(item.get("description") or "").strip()
    if description:
        fields["description_zh"] = description
        fields["desc_fp"] = description_fingerprint_text(description)
    return fields


def _converted_price_views(
    input_price: float | None,
    output_price: float | None,
    cache_read: float | None,
    currency: str,
    rate: float,
) -> tuple[dict[str, float | None], dict[str, float | None], float | None, float | None]:
    """单渠道价目 → 目录双币种视图：(list, list_cny, cache_read, cache_read_cny)。

    目录统一 USD 口径：人民币标价按快照汇率折算成 list，页面原价进 list_cny；
    美元标价反之。
    """
    if currency == "CNY":
        usd = {k: round(v / rate, 6) if v is not None else None
               for k, v in (("input", input_price), ("output", output_price))}
        cny = {k: round2(v) for k, v in (("input", input_price), ("output", output_price))}
        usd_cache = round(cache_read / rate, 6) if cache_read is not None else None
        cny_cache = round2(cache_read)
    else:
        usd = {"input": input_price, "output": output_price}
        cny = {k: round2(v * rate) if v is not None else None
               for k, v in (("input", input_price), ("output", output_price))}
        usd_cache = cache_read
        cny_cache = round2(cache_read * rate) if cache_read is not None else None
    return usd, cny, usd_cache, cny_cache


def _price_order_violation(
    input_price: float | None, output_price: float | None, cache_read: float | None
) -> str | None:
    """价目设定自校验：缓存读 ≤ 输入 ≤ 输出是定价常识，输入或输出低于缓存读价
    说明页面列被错位提取（如把缓存读列当成输入价）。返回 skip 原因，正常返回 None。"""
    if cache_read is None:
        return None
    if input_price is not None and input_price < cache_read:
        return "（输入价低于缓存读价，判定为价目列错位）"
    if output_price is not None and output_price < cache_read:
        return "（输出价低于缓存读价，判定为价目列错位）"
    return None


def merge_sources_into_catalog(
    catalog: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    rate: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """把定价源抓取结果合并进官方价目录；返回 (新目录, 摘要)。

    国内源（region=cn）：命中条目则基准换成页面价（原基准为国际口径时退居
    list_global 参考），未命中则新增条目（vendor 用源厂商名、region=cn）。
    海外源（region=global）：只给 region=cn 的命中条目补 list_global 国际参考
    价，不动基准；条目已是国际口径或目录未收录时跳过。
    同一轮里两个国内源撞同一模型键时按厂商名先到先得；禁用或没有抓取结果的源跳过。
    命中已有条目时 vendor 同步为当前源，保证价格、来源与厂商归属同渠道。
    国内定价页大量列出托管/转售的第三方模型（千帆卖 DeepSeek、百炼代售 deepseek、
    SiliconFlow 聚合各家开源模型），按模型名判品牌，归属不是本厂商的一律不进
    官方目录——官方基准只认厂商自研价，托管渠道价留在全量渠道价目录里比价。
    """
    models = catalog.get("models")
    if not isinstance(models, dict):
        models = {}
        catalog["models"] = models
    matched = 0
    added = 0
    referenced = 0
    skipped: list[str] = []
    claimed: set[str] = set()  # 国内基准归属（cn 源先到先得）
    referenced_keys: set[str] = set()  # 国际参考价归属（global 源先到先得）
    for vendor in sorted(sources):
        source = sources[vendor]
        if not source.get("enabled", True):
            skipped.append(f"{vendor}（已停用）")
            continue
        fetched = source.get("models") or []
        if not fetched:
            continue
        source_url = str(source.get("url") or "")
        source_region = validate_region(str(source.get("region") or ""))
        for item in fetched:
            if not isinstance(item, dict):
                continue
            name = str(item.get("model") or "").strip()
            if not name:
                continue
            key = model_key(name)
            if not key:
                continue
            input_price = number_or_none(item.get("input_price"))
            output_price = number_or_none(item.get("output_price"))
            if input_price is None and output_price is None:
                continue
            cache_read = number_or_none(item.get("cache_read_price"))
            violation = _price_order_violation(input_price, output_price, cache_read)
            if violation is not None:
                skipped.append(f"{vendor}/{name}{violation}")
                continue
            currency = str(item.get("currency") or "").upper()
            # 页面没标注货币时按源区域兜底：国内定价页默认人民币、海外页默认美元
            currency = currency if currency in {"CNY", "USD"} else ("USD" if source_region == "global" else "CNY")
            usd, cny, usd_cache, cny_cache = _converted_price_views(input_price, output_price, cache_read, currency, rate)
            if source_region == "global":
                # 海外源只作国际参考：给国内基准条目补 list_global，基准与 region 不动
                entry = models.get(key)
                if key in referenced_keys:
                    skipped.append(f"{vendor}/{name}（国际参考价已由其他海外源提供，先到先得）")
                elif not isinstance(entry, dict):
                    skipped.append(f"{vendor}/{name}（目录未收录，海外源不新增条目）")
                elif entry.get("region") != "cn":
                    skipped.append(f"{vendor}/{name}（条目已是国际口径，无需参考价）")
                else:
                    entry["list_global"] = usd
                    entry["list_global_cny"] = cny
                    referenced_keys.add(key)
                    referenced += 1
                continue
            # 官方自研闸门：归属他家的托管/转售模型与识别不出归属的第三方模型都不进官方目录
            if is_unbranded(name) or is_hosted_model(name, vendor):
                skipped.append(skip_reason(name, vendor))
                continue
            # 只收通用对话大模型：特殊领域与日期后缀快照变体不进官方目录
            # （存储层已过滤，这里是旧缓存数据未重抓时的防御）
            if not is_general_llm({"model": name}):
                skipped.append(f"{vendor}/{name}（特殊领域/快照变体，不进官方目录）")
                continue
            if key in claimed:
                skipped.append(f"{vendor}/{name}（与其他定价源撞模型键，先到先得）")
                continue
            entry = models.get(key)
            if isinstance(entry, dict):
                displaced_region = entry.get("region")
                if displaced_region == "global" and isinstance(entry.get("list"), dict):
                    entry["list_global"] = entry["list"]
                    entry["list_global_cny"] = entry.get("list_cny")
                entry["region"] = "cn"
                entry["vendor"] = vendor  # 基准价已被本源覆盖，归属跟着换成当前渠道
                entry["list"] = usd
                entry["list_cny"] = cny
                entry["source_url"] = source_url
                if cache_read is not None:
                    previous_cache = entry.get("cache") if isinstance(entry.get("cache"), dict) else {}
                    previous_cache_cny = entry.get("cache_cny") if isinstance(entry.get("cache_cny"), dict) else {}
                    entry["cache"] = {"read": usd_cache, "write": previous_cache.get("write")}
                    entry["cache_cny"] = {"read": cny_cache, "write": previous_cache_cny.get("write")}
                if item.get("price_status"):
                    entry["price_status"] = item["price_status"]
                # 上下文/简介只补空不覆盖：models.dev 已有的 limit 更结构化，已有简介译文不打架
                meta = _cn_meta_fields(item)
                if isinstance(meta.get("limit"), dict):
                    entry_limit = entry.get("limit") if isinstance(entry.get("limit"), dict) else None
                    if not (isinstance(entry_limit, dict) and entry_limit.get("context")):
                        entry["limit"] = meta["limit"]
                if meta.get("description_zh") and not entry.get("description") and not entry.get("description_zh"):
                    entry["description_zh"] = meta["description_zh"]
                    entry["desc_fp"] = meta["desc_fp"]
                claimed.add(key)
                matched += 1
            else:
                models[key] = {
                    "found": True,
                    "model": name,
                    "name": name,
                    "vendor": vendor,
                    "region": "cn",
                    "currency": "USD",
                    "list": usd,
                    "list_cny": cny,
                    "cache": {"read": usd_cache, "write": None},
                    "cache_cny": {"read": cny_cache, "write": None},
                    "source_url": source_url,
                    "release_date": None,
                    **({"price_status": item["price_status"]} if item.get("price_status") else {}),
                    **_cn_meta_fields(item),
                }
                claimed.add(key)
                added += 1
    catalog["models"] = models
    return catalog, {"matched": matched, "added": added, "referenced": referenced, "skipped": skipped}


def merge_sources_into_channel_catalog(
    full: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    rate: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """把国内定价源抓取结果并入全量渠道价目录；返回 (新目录, 摘要)。

    models.dev 有意不收国内渠道价（时段分档缺失、他方汇率换算偏差大），
    国内渠道价由厂商定价源补位：`厂商:模型` 一家一条，与海外渠道同待遇，
    官方目录撞键的模型在全量目录里各渠道价格并存、可直接对比。
    同名键已存在时以官方定价页的抓取结果覆盖（比快照新鲜）。
    只收国内源（region=cn）；禁用或没有抓取结果的源跳过。
    """
    models = full.get("models")
    if not isinstance(models, dict):
        models = {}
        full["models"] = models
    added = 0
    replaced = 0
    skipped: list[str] = []
    for vendor, source in sorted(sources.items()):
        if not source.get("enabled", True):
            continue
        if validate_region(str(source.get("region") or "")) != "cn":
            continue
        source_url = str(source.get("url") or "")
        for item in source.get("models") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("model") or "").strip()
            # 特殊领域/快照变体不进全量渠道目录（存储层已过滤，这里防旧缓存）
            if not is_general_llm({"model": name}):
                continue
            key = model_key(name)
            if not key:
                continue
            input_price = number_or_none(item.get("input_price"))
            output_price = number_or_none(item.get("output_price"))
            if input_price is None and output_price is None:
                continue
            cache_read = number_or_none(item.get("cache_read_price"))
            violation = _price_order_violation(input_price, output_price, cache_read)
            if violation is not None:
                skipped.append(f"{vendor}/{name}{violation}")
                continue
            currency = str(item.get("currency") or "").upper()
            # 国内定价页没标注货币时默认人民币
            currency = currency if currency in {"CNY", "USD"} else "CNY"
            usd, cny, usd_cache, cny_cache = _converted_price_views(input_price, output_price, cache_read, currency, rate)
            entry_key = f"{model_key(vendor)}:{key}"
            if entry_key in models:
                replaced += 1
            else:
                added += 1
            models[entry_key] = {
                "found": True,
                "model": name,
                "name": name,
                "vendor": vendor,
                "region": "cn",
                "logo": None,
                "currency": "USD",
                "list": usd,
                "list_cny": cny,
                "cache": {"read": usd_cache, "write": None},
                "cache_cny": {"read": cny_cache, "write": None},
                "source_url": str(item.get("source_url") or "").strip() or source_url,
                "release_date": None,
                **({"price_status": item["price_status"]} if item.get("price_status") else {}),
                **_cn_meta_fields(item),
            }
    full["models"] = models
    return full, {"added": added, "replaced": replaced, "skipped": skipped}


def detect_vendor_coverage(
    providers: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按品牌表判定 models.dev 对国内厂商的收录情况；返回检测记录。

    providers 是目录 meta 里的厂商清单（id/name/doc/models_total/models_priced）。
    判定：品牌没有任何渠道命中 → not_listed；命中国内站渠道且带价 → has_cn；
    其余（只有国际站口径，或国内渠道无带价模型）→ missing_cn。verdict 只描述
    models.dev 收录情况：目录不收 models.dev 国内价，任何品牌都需要配置国内
    定价源才能产出国内基准。
    """
    by_id = {str(p.get("id")): p for p in providers if isinstance(p, dict)}
    records: list[dict[str, Any]] = []
    claimed_by_keyword: set[str] = set()
    for brand in DOMESTIC_BRANDS:
        hits = [pid for pid in brand.aliases if pid in by_id]
        for pid, provider in by_id.items():
            if pid in hits or pid in claimed_by_keyword:
                continue
            haystack = f"{provider.get('name') or ''} {provider.get('doc') or ''}".casefold()
            if any(keyword in haystack for keyword in brand.keywords):
                hits.append(pid)
                claimed_by_keyword.add(pid)
        cn_hits = [pid for pid in hits if pid in brand.cn_provider_ids or str(pid).endswith("-cn")]
        cn_priced = sum(int(by_id[pid].get("models_priced") or 0) for pid in cn_hits if pid in by_id)
        if not hits:
            verdict = "not_listed"
        elif cn_hits and cn_priced > 0:
            verdict = "has_cn"
        else:
            verdict = "missing_cn"
        source = (sources or {}).get(brand.vendor)
        records.append({
            "vendor": brand.vendor,
            "verdict": verdict,
            "providers": hits,
            "models_total": sum(int(by_id[pid].get("models_total") or 0) for pid in hits if pid in by_id),
            "models_priced": sum(int(by_id[pid].get("models_priced") or 0) for pid in hits if pid in by_id),
            "suggestions": [
                {"kind": kind, "url": url}
                for kind, url in (("web", brand.suggested_url), ("json", brand.suggested_json_url))
                if url
            ],
            "note": brand.note,
            "source_note": (source or {}).get("note"),
            "source_added": source is not None,
            "source_enabled": bool(source.get("enabled", True)) if source else None,
        })
    # 判定只描述 models.dev 的收录情况，不代表国内价已可用（目录不再收
    # models.dev 国内价）：无论哪种 verdict，配置国内定价源都需要且有效
    return records


def _catalog_rate(catalog: dict[str, Any]) -> float | None:
    """目录快照汇率优先，缺失时实时兜底。"""
    rate = catalog.get("usd_cny_rate")
    if isinstance(rate, (int, float)) and rate > 0:
        return float(rate)
    resolved, _source = fx.resolve_rate(None)
    return resolved


def refresh_and_merge(store: Any, vendor: str, *, timeout: float, ai_config: AIConfig | None) -> dict[str, Any]:
    """单源「立即抓取」：抓页更新源文档后，就地重合并官方价目录。

    合并价格字段全量更新；上下文/简介只补空，条目已有的元数据原样保留。
    """
    summary = refresh_source(store, vendor, timeout=timeout, ai_config=ai_config)
    if summary.get("status") == "failed":
        return summary
    catalog = store.get_document("catalog")
    if not isinstance(catalog, dict):
        return {**summary, "merge": "目录不存在，等目录刷新后自动合并"}
    rate = _catalog_rate(catalog)
    if not rate:
        return {**summary, "merge": "无可用汇率，未合并"}
    merged, merge_summary = merge_sources_into_catalog(catalog, load_sources(store), rate)
    store.set_document("catalog", merged)
    return {**summary, "merge": merge_summary}


def refresh_all_sources(store: Any, *, timeout: float, ai_config: AIConfig | None) -> dict[str, Any]:
    """逐源抓取（厂商名序），供目录刷新任务在合并前调用；失败不中断。"""
    results: list[dict[str, Any]] = []
    for vendor in sorted(load_sources(store)):
        try:
            results.append(refresh_source(store, vendor, timeout=timeout, ai_config=ai_config))
        except ValueError as exc:  # 源记录缺失等异常情况：跳过并记录
            results.append({"vendor": vendor, "status": "failed", "error": str(exc), "model_count": 0})
    ok = sum(1 for item in results if item.get("status") == "ok")
    empty = sum(1 for item in results if item.get("status") == "empty")
    failed = sum(1 for item in results if item.get("status") == "failed")
    return {
        "sources": len(results),
        "ok": ok,
        "empty": empty,
        "failed": failed,
        "models": sum(int(item.get("model_count") or 0) for item in results),
        "detail": results,
    }


def remerge_catalog(store: Any) -> dict[str, Any]:
    """用当前源配置就地重合并官方价目录（删除/停用源后的快速恢复路径）。"""
    catalog = store.get_document("catalog")
    if not isinstance(catalog, dict):
        return {"matched": 0, "added": 0, "referenced": 0, "skipped": []}
    rate = _catalog_rate(catalog)
    if not rate:
        return {"matched": 0, "added": 0, "referenced": 0, "skipped": [], "error": "无可用汇率"}
    merged, summary = merge_sources_into_catalog(catalog, load_sources(store), rate)
    store.set_document("catalog", merged)
    return summary


__all__ = [
    "VENDOR_SOURCES_DOCUMENT",
    "DOMESTIC_BRANDS",
    "detect_vendor_coverage",
    "load_sources",
    "upsert_source",
    "delete_source",
    "refresh_source",
    "refresh_all_sources",
    "refresh_and_merge",
    "merge_sources_into_catalog",
    "merge_sources_into_channel_catalog",
    "remerge_catalog",
    "validate_source",
    "validate_region",
]
