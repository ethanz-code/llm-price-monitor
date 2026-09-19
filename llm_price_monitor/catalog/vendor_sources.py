"""厂商定价源：管理台配置国内厂商定价页，抓取结果合并为官方价基准。

背景：models.dev 对部分国内厂商只有国际站口径（如智谱的 zhipuai/zai 都指向
z.ai），甚至完全未收录（百度千帆、讯飞星火等）。本模块提供三件事：

- **覆盖检测**：`DOMESTIC_BRANDS` 品牌表人工整理各厂商在 models.dev 里的渠道
  与国内站口径，`detect_vendor_coverage` 据此判定每个品牌是「已覆盖 / 仅国际
  口径（推荐添加）/ 未收录（推荐添加）」；输入是目录 meta 里的 `providers`
  厂商清单（见 modelsdev._provider_inventory），零网络开销。
- **定价源存取**：管理员为厂商配置一个公开定价页 URL（无鉴权字段），抓取复用
  `page_price.fetch_page_prices`（静态解析优先、AI 兜底防幻觉），结果连同状态
  存 `vendor_sources` 文档（读改写加进程内锁）。
- **合并**：`merge_sources_into_catalog` 把抓到的价格按 `model_key` 合并进官方
  价目录——命中条目则基准换成国内页价、原国际价退居 `list_global` 参考；未
  命中则新增条目（region=cn）。目录统一 USD 口径：CNY 页价按快照汇率折算存
  `list`，`list_cny` 存页面标价的精确人民币值。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from llm_price_monitor.catalog import fx
from llm_price_monitor.catalog.normalize import model_key, round2
from llm_price_monitor.page_price import fetch_page_prices
from llm_price_monitor.units import number_or_none

VENDOR_SOURCES_DOCUMENT = "vendor_sources"

# 读改写与后台任务可能并发（管理台编辑 vs 刷新任务），与 ai_cache 同款进程内锁
_LOCK = threading.Lock()


@dataclass(frozen=True)
class DomesticBrand:
    """一个国内厂商品牌在 models.dev 里的渠道对照，覆盖检测的判定依据。

    aliases: models.dev 的 provider id（完整匹配）；cn_provider_ids: 其中属于
    国内站口径的渠道；keywords: 名称/文档 URL 兜底关键词（models.dev 未来新增
    厂商也能被识别）；suggested_url: 已核验可解析的国内定价页，推荐添加时预填。
    """

    vendor: str
    aliases: tuple[str, ...]
    cn_provider_ids: tuple[str, ...]
    keywords: tuple[str, ...]
    suggested_url: str = ""
    note: str = ""


# 人工整理（2026-09 核对 models.dev 快照 221 个渠道）；带价与否以快照为准，不在表里写死
DOMESTIC_BRANDS: tuple[DomesticBrand, ...] = (
    DomesticBrand(
        vendor="Zhipu AI",
        aliases=("zhipuai", "zai", "zhipuai-coding-plan", "zai-coding-plan"),
        cn_provider_ids=(),  # zhipuai/zai 的文档都指向 docs.z.ai 国际站
        keywords=("zhipu", "bigmodel", "z.ai"),
        suggested_url="https://docs.bigmodel.cn/cn/guide/start/pricing.md",
        note="models.dev 只有 z.ai 国际站口径；国内站 bigmodel.cn 的价需从定价页抓取",
    ),
    DomesticBrand(
        vendor="DeepSeek",
        aliases=("deepseek",),
        cn_provider_ids=("deepseek",),  # 单条目已核与官方中文价页空闲时段一致
        keywords=("deepseek",),
        note="models.dev 条目即国内口径（空闲时段价），仅缺高峰/空闲分档",
    ),
    DomesticBrand(
        vendor="Moonshot AI",
        aliases=("moonshotai", "moonshotai-cn"),
        cn_provider_ids=("moonshotai-cn",),
        keywords=("moonshot",),
        note="国内外两渠道同价",
    ),
    DomesticBrand(
        vendor="Alibaba Cloud",
        aliases=(
            "alibaba", "alibaba-cn", "alibaba-token-plan", "alibaba-token-plan-cn",
            "alibaba-coding-plan", "alibaba-coding-plan-cn",
        ),
        cn_provider_ids=("alibaba-cn", "alibaba-token-plan-cn", "alibaba-coding-plan-cn"),
        keywords=("alibaba", "aliyun"),
        note="国内外两渠道价差大（48 个共有模型 44 个不同，国内/国际中位 0.48）",
    ),
    DomesticBrand(
        vendor="MiniMax",
        aliases=("minimax", "minimax-cn", "minimax-coding-plan", "minimax-cn-coding-plan"),
        cn_provider_ids=("minimax-cn", "minimax-cn-coding-plan"),
        keywords=("minimax",),
        note="国内外两渠道同价",
    ),
    DomesticBrand(
        vendor="Volcengine Ark",
        aliases=("volcengine", "volcengine-coding-plan"),
        cn_provider_ids=("volcengine", "volcengine-coding-plan"),
        keywords=("volcengine",),
        note="火山方舟本身即国内平台",
    ),
    DomesticBrand(
        vendor="Tencent",
        aliases=("tencent-tokenhub", "tencent-coding-plan", "tencent-token-plan"),
        cn_provider_ids=("tencent-tokenhub", "tencent-coding-plan", "tencent-token-plan"),
        keywords=("tencent",),
        note="腾讯云渠道本身即国内平台",
    ),
    DomesticBrand(
        vendor="StepFun",
        aliases=("stepfun", "stepfun-ai", "stepfun-step-plan", "stepfun-ai-step-plan"),
        cn_provider_ids=("stepfun", "stepfun-step-plan"),
        keywords=("stepfun",),
        note="stepfun 为国内站（stepfun.com），stepfun-ai 为国际站（stepfun.ai）",
    ),
    DomesticBrand(
        vendor="SiliconFlow",
        aliases=("siliconflow", "siliconflow-cn"),
        cn_provider_ids=("siliconflow-cn",),
        keywords=("siliconflow",),
        note="硅基流动国内外两渠道，少数模型有价差",
    ),
    DomesticBrand(
        vendor="SenseNova",
        aliases=("sensenova",),
        cn_provider_ids=("sensenova",),
        keywords=("sensenova",),
        note="商汤日日新本身即国内平台",
    ),
    DomesticBrand(
        vendor="ModelScope",
        aliases=("modelscope",),
        cn_provider_ids=("modelscope",),
        keywords=("modelscope",),
        note="魔搭社区本身即国内平台",
    ),
    DomesticBrand(
        vendor="iFlow",
        aliases=("iflowcn",),
        cn_provider_ids=("iflowcn",),
        keywords=("iflow",),
        note="心流本身即国内平台",
    ),
    DomesticBrand(
        vendor="Xiaomi",
        aliases=("xiaomi-token-plan-cn",),
        cn_provider_ids=("xiaomi-token-plan-cn",),
        keywords=("xiaomi",),
        note="小米 Token Plan 渠道",
    ),
    # models.dev 未收录的常见国内厂商：关键词用于将来新增渠道时自动识别
    DomesticBrand(vendor="Baidu", aliases=(), cn_provider_ids=(), keywords=("baidu", "qianfan"),
                  note="models.dev 未收录百度千帆"),
    DomesticBrand(vendor="iFlytek", aliases=(), cn_provider_ids=(), keywords=("iflytek", "xfyun", "xinghuo"),
                  note="models.dev 未收录讯飞星火"),
    DomesticBrand(vendor="01.AI", aliases=(), cn_provider_ids=(), keywords=("01.ai", "lingyi", "wanwu"),
                  note="models.dev 未收录零一万物"),
    DomesticBrand(vendor="Baichuan", aliases=(), cn_provider_ids=(), keywords=("baichuan",),
                  note="models.dev 未收录百川"),
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


def load_sources(store: Any) -> dict[str, dict[str, Any]]:
    """读取厂商定价源配置与最近抓取结果；返回 {厂商名: 记录}。"""
    doc = store.get_document(VENDOR_SOURCES_DOCUMENT)
    sources = doc.get("sources") if isinstance(doc, dict) else None
    return {str(k): v for k, v in sources.items() if isinstance(v, dict)} if isinstance(sources, dict) else {}


def _save_sources(store: Any, sources: dict[str, dict[str, Any]]) -> None:
    store.set_document(VENDOR_SOURCES_DOCUMENT, {"sources": sources})


def upsert_source(store: Any, vendor: str, url: str, enabled: bool = True) -> dict[str, Any]:
    """新增或更新一个定价源（只动配置字段）；返回该源记录。

    换 URL 时清空上次抓取结果——models 归属旧地址，合并前必须重新抓取。
    """
    vendor, url = validate_source(vendor, url)
    with _LOCK:
        sources = load_sources(store)
        record = sources.get(vendor) or {}
        url_changed = bool(record) and record.get("url") != url
        record.update({"vendor": vendor, "url": url, "enabled": bool(enabled)})
        if url_changed:
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
        result = fetch_page_prices(url, ai_config=ai_config, timeout=timeout)
    except httpx.HTTPError as exc:
        _record_fetch(store, vendor, last_fetched_at=time.time(), last_status="failed", last_error=f"抓取失败：{exc}", last_method=None, model_count=0, models=None)
        return {"vendor": vendor, "status": "failed", "error": f"抓取失败：{exc}", "model_count": 0}
    models = result.get("models") or []
    error = "；".join(result.get("warnings") or []) or None
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


def merge_sources_into_catalog(
    catalog: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    rate: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """把定价源抓取结果合并进官方价目录；返回 (新目录, 摘要)。

    按 `model_key` 匹配：命中条目则基准换成页面价（原基准为国际口径时退居
    list_global 参考），未命中则新增条目（vendor 用源厂商名、region=cn）。
    同一轮里两个源撞同一模型键时按厂商名先到先得；禁用或没有抓取结果的源跳过。
    """
    models = catalog.get("models")
    if not isinstance(models, dict):
        models = {}
        catalog["models"] = models
    matched = 0
    added = 0
    skipped: list[str] = []
    claimed: set[str] = set()
    for vendor in sorted(sources):
        source = sources[vendor]
        if not source.get("enabled", True):
            skipped.append(f"{vendor}（已停用）")
            continue
        fetched = source.get("models") or []
        if not fetched:
            continue
        source_url = str(source.get("url") or "")
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
            currency = str(item.get("currency") or "").upper()
            currency = currency if currency in {"CNY", "USD"} else "CNY"
            if currency == "CNY":
                usd = {k: round(v / rate, 6) if v is not None else None for k, v in
                       (("input", input_price), ("output", output_price))}
                cny = {k: round2(v) for k, v in (("input", input_price), ("output", output_price))}
                usd_cache = round(cache_read / rate, 6) if cache_read is not None else None
                cny_cache = round2(cache_read)
            else:
                usd = {"input": input_price, "output": output_price}
                cny = {k: round2(v * rate) if v is not None else None for k, v in
                       (("input", input_price), ("output", output_price))}
                usd_cache = cache_read
                cny_cache = round2(cache_read * rate) if cache_read is not None else None
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
                }
                claimed.add(key)
                added += 1
    catalog["models"] = models
    return catalog, {"matched": matched, "added": added, "skipped": skipped}


def detect_vendor_coverage(
    providers: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按品牌表判定 models.dev 对国内厂商国内价的覆盖情况；返回检测记录。

    providers 是目录 meta 里的厂商清单（id/name/doc/models_total/models_priced）。
    判定：品牌没有任何渠道命中 → not_listed；命中国内站渠道且带价 → has_cn；
    其余（只有国际站口径，或国内渠道无带价模型）→ missing_cn（推荐添加）。
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
            "suggested_url": brand.suggested_url,
            "note": brand.note,
            "source_added": source is not None,
            "source_enabled": bool(source.get("enabled", True)) if source else None,
        })
    # 推荐添加的排前面（未收录 > 仅国际口径 > 已覆盖），其余保持品牌表顺序
    priority = {"not_listed": 0, "missing_cn": 1, "has_cn": 2}
    records.sort(key=lambda record: priority.get(str(record["verdict"]), 3))
    return records


def _catalog_rate(catalog: dict[str, Any]) -> float | None:
    """目录快照汇率优先，缺失时实时兜底。"""
    rate = catalog.get("usd_cny_rate")
    if isinstance(rate, (int, float)) and rate > 0:
        return float(rate)
    resolved, _source = fx.resolve_rate(None)
    return resolved


def refresh_and_merge(store: Any, vendor: str, *, timeout: float, ai_config: AIConfig | None) -> dict[str, Any]:
    """单源「立即抓取」：抓页更新源文档后，就地重合并官方价目录并补 AI 档位。

    合并只动价格字段，已合并过条目的 tier_fp 原样保留，因此把目录自身当
    previous 传给 attach_ai_tiers 即可：老条目沿用档位，合并新增的条目进
    本轮判定队列（没有简介，翻译步骤无事可做，不用跑）。
    """
    from llm_price_monitor.catalog.classify import attach_ai_tiers

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
    if merge_summary["matched"] + merge_summary["added"] and ai_config is not None:
        attach_ai_tiers(merged, catalog, ai_config)
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
        return {"matched": 0, "added": 0, "skipped": []}
    rate = _catalog_rate(catalog)
    if not rate:
        return {"matched": 0, "added": 0, "skipped": [], "error": "无可用汇率"}
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
    "remerge_catalog",
    "validate_source",
]
