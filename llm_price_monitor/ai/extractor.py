"""AI 价格抽取：证据组装、prompt 构造、分批调用、结果缓存与价格证据校验。"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

import llm_price_monitor.ai as _ai

from llm_price_monitor import tasklog
from llm_price_monitor.config import AIConfig, SiteSpec
from llm_price_monitor.evidence import (
    candidate_payload,
    contains_price_evidence,
    is_preferred_response_url,
    join_page_sources,
    payload_hash,
    redact_text,
    redact_url,
    sanitize_evidence,
    structure_page_source,
    slim_pricing_payload,
)
from llm_price_monitor.matching import canonical_target, contains_model_alias
from llm_price_monitor.tracker import PriceRecord
from llm_price_monitor.units import has_pricing_tiers, merge_model_items, number_or_none, price_digit_forms

from .api_format import ai_content, json_content
from .client import ai_http_client
from .errors import (
    AIExtractionError,
    AIBudgetExhaustedError,
    _plain_text,
    _prompt_too_long,
    _response_detail,
    fit_text,
)

NEWAPI_ONEAPI_PRICING_GUIDANCE = """你正在分析中转站的价格接口响应（one-api/new-api 及各种自研结构）。自己判断响应的组织方式和价格字段：从原始 JSON 里找到目标模型的价格节点，按字段名和数量级判断单价口径（每 token 还是每 1M tokens，必要时换算成 CNY/1M tokens），同一模型的多份价格（分组/阶梯）逐个展开各输出一条。只使用证据里的数据，不得把倍率、余额或官方参考价冒充实际价格；拿不准口径就在 notes 写明推断依据并标 candidate，不要因此放弃。"""

# 证据超长被供应商拒绝时，按阶梯收紧单条证据文本上限逐级重试；None 表示不限制。
EVIDENCE_CHAR_LADDER: tuple[int | None, ...] = (None, 240_000, 96_000, 40_000, 16_000)
# 抽取按模型分批的下限：实际批大小随 config.max_tokens 折算（1200 tokens/条安全余量，
# 4000 预算=4/批、16000=12/批），见 extract()。批太大输出会在 max_tokens 中途截断成
# 非法 JSON，批太小则调用次数与重复系统提示翻倍。
AI_EXTRACT_BATCH_SIZE = 4
# 批间并发行数：批间相互独立（各自证据/缓存键/调用），总输出 token 不变，省的是
# 请求排队与证据重发的串行等待。并发峰值 = 站点 4 路（report.timeouts.SITE_FETCH_CONCURRENCY）
# × 批次 4 路 = 16 路对同一供应商网关；429 限流走换模型 fallback（不冷却），真出现限流
# 事故再考虑全局并发闸
AI_EXTRACT_CONCURRENCY = 4

# 单批 AI 提取的时长预算（秒）：600 按单批正常最慢耗时（约 6 分钟）取 2 倍富余标定。
# 整轮预算 = 600 × 并行波次数（批间 4 路并行，墙钟按波次算而不是按批次数线性放大），
# 再钳到 SITE_AI_BUDGET_MAX_SECONDS 封顶：站点采集有 900s 硬上限（report.timeouts），
# 预算若超过它，硬超时会先把整站连同已完成批次一起作废，「超预算保留已完成批次」
# （直采兜底承接剩余模型）就永远没机会生效。一个批次都没成才按失败上报该站。
SITE_AI_BUDGET_SECONDS = 600
SITE_AI_BUDGET_MAX_SECONDS = 840.0

def _validated_price_payload(answer: str) -> dict[str, Any]:
    """价格提取换模型链的内容校验：合法 JSON 之外还必须是带 models 数组的对象。

    只查「能解析」会放过形状坏掉的回复（实测模型会输出 {"models":":[{",…} 这类
    解析得出但结构全废的 JSON），它会在 _records 才炸掉、整轮提取直接回落直采；
    在这里拦下就能走既有的换下一个模型链路重抽一次。
    """
    result = json_content(answer)
    if not isinstance(result.get("models"), list):
        raise AIExtractionError("AI 标准化结果缺少 models 数组")
    return result


def _validated_alias_payload(answer: str) -> dict[str, Any]:
    """模型名对照换模型链的内容校验：合法 JSON 且带 mappings 数组。"""
    result = json_content(answer)
    if not isinstance(result.get("mappings"), list):
        raise AIExtractionError("AI 模型名对照结果缺少 mappings 数组")
    return result


def _alias_mappings(
    mappings: list[Any],
    expected: list[str],
    site_names: set[str],
) -> dict[str, dict[str, Any]]:
    """对照结果收紧：只留目标集合内的标准名，原始名与别名都必须真实出现在站点名单里。

    对照表只用于和站点接口名单做匹配，名单外的值是模型幻觉——留着匹配不上是废料，
    万一撞名还会把别的模型的价格挂到目标头上，必须丢弃。
    """
    details: dict[str, dict[str, Any]] = {}
    for item in mappings:
        if not isinstance(item, dict):
            continue
        model = str(item.get("model") or "").strip()
        if model not in expected or model in details:
            continue
        observed = str(item.get("observed_model") or "").strip()
        if observed and observed not in site_names:
            observed = ""
        aliases: list[str] = []
        for alias in item.get("aliases") or []:
            text = str(alias).strip()
            if text in site_names and text not in aliases:
                aliases.append(text)
        if not observed and not aliases:
            continue
        details[model] = {"observed_model": observed, "aliases": aliases}
    return details

# 缓存键里的逐请求噪声字段：值随机、与模型名/价格无关（如 cun 每次请求把随机哈希
# pricing_version 挂在随机模型上），只从缓存键里剔除，发给 AI 的证据原文不动
_CACHE_KEY_NOISE_KEYS = frozenset({"pricing_version"})


def canonical_cache_evidence(value: Any) -> Any:
    """缓存键专用的证据规范化：dict 按键名排序、数组按内容排序、整数值浮点归一、
    恰好是完整 JSON 的字符串引文递归规范化。new-api 系接口的模型数组与分组列表
    逐请求洗牌（内容相同、顺序不同），原序哈希会让缓存永不命中；数组顺序对抽取
    结果无影响（模型按名字读取、分组按名字匹配），排序只让键稳定。位置型价格
    数组出现在页面打包 JS 里，走的是不可解析的字符串引文，不会被这里重排。"""
    if isinstance(value, dict):
        return {
            key: canonical_cache_evidence(item)
            for key, item in sorted(value.items())
            if key not in _CACHE_KEY_NOISE_KEYS
        }
    if isinstance(value, list):
        items = [canonical_cache_evidence(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "{[":
            try:
                parsed = json.loads(text)
            except ValueError:
                return value
            if isinstance(parsed, (dict, list)):
                return json.dumps(canonical_cache_evidence(parsed), ensure_ascii=False, sort_keys=True)
    return value

class AIPriceExtractor:
    def __init__(self, config: AIConfig) -> None:
        self.config = config

    def _cache_key(self, spec: SiteSpec, expected_models: list[str], evidence: str) -> str:
        # version=8：位置型数组与分组倍率规则上线后旧缓存结果不可信，整体失效重抽。
        # version=12：价格合理性校验上线，旧缓存结果未过检（含 hao 站 OCR 模型提取的百万级错价），整体失效重抽。
        # version=13：提取模型固定化（price_model）+ 输出预算 16000，随机模型时代的缓存结果读法不可信，整体失效重抽。
        # version=14：键改用顺序规范化证据（canonical_cache_evidence）。new-api 系接口的模型数组与
        # 分组列表逐请求洗牌（cun 实测两次响应字节数相同、顺序不同），原序哈希让缓存永不命中，
        # 每轮采集都全额重跑全部批次；旧键整体失效一次。
        try:
            canonical = json.dumps(canonical_cache_evidence(json.loads(evidence)), ensure_ascii=False, sort_keys=True)
        except ValueError:
            canonical = evidence
        return payload_hash({"version": 14, "site": spec.id, "models": expected_models, "evidence": canonical})

    def _cached_result(self, key: str) -> dict[str, Any] | None:
        if self.config.cache is None:
            return None
        return self.config.cache.cache_get(key)

    def _save_cached_result(self, key: str, result: dict[str, Any]) -> None:
        if self.config.cache is not None:
            self.config.cache.cache_put(key, result)

    def _evidence(
        self,
        spec: SiteSpec,
        page_text: str,
        responses: list[dict[str, Any]],
        expected_models: list[str],
        page_sources: list[dict[str, str]] | None = None,
        max_chars: int | None = None,
    ) -> tuple[str, str, str, list[dict[str, Any]], list[dict[str, Any]]]:
        raw_page_sources = page_sources or [{"source": "model_list", "url": str(spec.network.get("url") or ""), "text": page_text}]
        for source in raw_page_sources:
            source["text"] = _plain_text(str(source.get("text", "")))
        filtered_page_sources: list[dict[str, Any]] = []
        for source in raw_page_sources:
            if str(source.get("source") or "model_list") != "model_list":
                continue
            structured_source = structure_page_source(source, expected_models)
            if structured_source is None:
                continue
            filtered_page_sources.append(structured_source)
        filtered_page_text = join_page_sources(filtered_page_sources)
        clean_responses: list[dict[str, Any]] = []
        preferred_candidates = [
            response for response in responses
            if str(response.get("resource_type", "")) in {"fetch", "xhr"}
            and str(response.get("source") or "model_list") == "model_list"
            and is_preferred_response_url(str(response.get("url", "")))
        ]
        if preferred_candidates:
            responses = [sorted(
                preferred_candidates,
                key=lambda item: ("price" not in str(item.get("url", "")).casefold(),),
            )[0]]
        for response in responses:
            if str(response.get("resource_type", "")) not in {"fetch", "xhr"}:
                continue
            original_payload = slim_pricing_payload(sanitize_evidence(response.get("payload")))
            candidate = candidate_payload(original_payload, expected_models) if original_payload is not None else None
            original_body = (
                json.dumps(original_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if original_payload is not None
                else redact_text(_plain_text(str(response.get("text", ""))))
            )
            response_url = str(response.get("url", ""))
            source = str(response.get("source") or "model_list")
            if source != "model_list":
                continue
            preferred_response = bool(response.get("preferred_response")) or (
                source == "model_list"
                and is_preferred_response_url(response_url)
            )
            # 找不到候选时保留完整响应，避免误丢模型；目标模型要到 AI 解析阶段才能确定。
            if not preferred_response and not contains_price_evidence(original_body):
                continue
            payload = candidate if candidate is not None else original_payload
            if response.get("payload") is None:
                payload = None
            body = (
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if payload is not None
                else redact_text(_plain_text(str(response.get("text", ""))))
            )
            if response.get("payload") is not None and payload is None:
                continue
            captured = {
                "url": redact_url(response_url),
                "status": response.get("status"),
                "resource_type": response.get("resource_type"),
                "content_type": response.get("content_type"),
                "source": source,
                "preferred_response": preferred_response,
                # 采集期目标模型未解析，统一记为 unresolved；别名匹配依据留待 AI 阶段产出。
                "target_model": "unresolved",
                "match_basis": [],
                "quote": body,
            }
            clean_responses.append(captured)
        # 价格证据默认原样交给 AI；只有供应商以"超长"拒绝时才按 EVIDENCE_CHAR_LADDER 收紧上限。
        if max_chars is not None:
            for source in filtered_page_sources:
                source["quote"] = fit_text(str(source.get("quote", "")), max_chars)
            for captured in clean_responses:
                captured["quote"] = fit_text(str(captured.get("quote", "")), max_chars)
        evidence = {
            "site_id": spec.id,
            "source_url": redact_url(str(spec.network.get("url") or "")),
            "requested_models": expected_models,
            "page_evidence": filtered_page_sources,
            "network_evidence": clean_responses,
        }
        serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        searchable = filtered_page_text + "\n" + json.dumps(clean_responses, ensure_ascii=False)
        return serialized, searchable, filtered_page_text, filtered_page_sources, clean_responses

    def _request(
        self,
        spec: SiteSpec,
        page_text: str,
        responses: list[dict[str, Any]],
        expected_models: list[str],
        page_sources: list[dict[str, str]] | None = None,
        max_chars: int | None = None,
    ) -> tuple[str, str, list[dict[str, str]], list[dict[str, Any]]]:
        evidence, _, filtered_page_text, filtered_page_sources, clean_responses = self._evidence(spec, page_text, responses, expected_models, page_sources, max_chars=max_chars)
        expected = expected_models or []
        scope_line = "只分析 expected_models 指定的目标模型，不要识别其他模型。"
        id_rule = (
            "若页面卡片或结构中明确出现 expected_models 的精确 model ID，价格归属以该 ID 为准，"
            "优先于 display_name、上游模型名或备注名，即使这些名称与 ID 不一致。"
        )
        system = (
            NEWAPI_ONEAPI_PRICING_GUIDANCE
            + "\n\n你是模型价格数据抽取器。只能使用 user 消息中的网页证据，禁止凭常识补全或猜测价格。"
            + scope_line
            + "调用方只提供标准模型名；请从证据中自动识别展示名、供应商前缀、版本写法和接口 model ID，"
            "并按输出结构填入 observed_model 与 aliases（aliases 要求见下方规则 2）。"
            + id_rule
            + "若页面卡片已明确标注精确 model ID 和站点价格，即使网络响应没有同名模型记录，"
            "也要保留该页面价格并标 candidate，不得输出 unavailable，也不得用网络中的相似模型价格替代。"
            "必须只返回 JSON，不要 Markdown，不要解释。"
        )
        output_rule = "- 只输出 expected_models 中的目标模型；model 必须填标准名，页面显示名和接口模型 ID 分别填进 observed_model 与 aliases，忽略其他模型。"
        trim_rule = "- model_list 的 network_evidence 是完整原始响应，可能同时包含多个模型、多个分组和多段上下文价格；不要因为当前 expected_models 只有一个就裁剪、过滤或只取第一条，只在最终 models 输出中保留目标模型。"
        unavailable_rule = "- 没有可靠价格时也要为每个 expected_models 输出 unavailable 记录。"
        models_line = json.dumps(expected_models, ensure_ascii=False)
        user = f"""请将以下价格页面证据标准化。

输出结构必须是：
{{
  "models": [{{
    "model": "模型名",
    "observed_model": "页面或接口中实际出现的原始模型名",
    "aliases": ["属于该标准模型的展示名称或模型 ID，不要填标准模型名本身"],
    "input_price": null,
    "output_price": null,
    "unit": "CNY/1M tokens",
    "currency": "CNY",
    "status": "confirmed|candidate|rule_only|unavailable",
    "confidence": 0.0,
    "group": "default",
    "context_min": null,
    "context_max": null,
    "cache_read_price": null,
    "cache_create_price": null,
    "cache_create_1h_price": null,
    "pricing_rules": {{"groups": [{{"name": "default", "tiers": [{{"context_min": 0, "context_max": null, "input_price": null, "output_price": null, "cache_read_price": null, "cache_create_price": null, "cache_create_1h_price": null, "unit": "CNY/1M tokens"}}]}}]}},
    "network_evidence": [{{"source": "model_list", "url": "实际捕获的响应 URL", "resource_type": "fetch|xhr", "match_basis": ["response_url|response_body"], "quote": "响应正文中的目标模型证据"}}],
    "page_evidence": [{{"source": "model_list", "url": "页面 URL", "target_model": "目标模型", "quote": "目标模型卡片中的原始页面证据"}}],
    "notes": ""
  }}],
  "cross_validation": {{"status": "matched|mismatch|partial|none", "conflicts": ["冲突说明"]}}
}}

规则：
{output_rule}
- aliases：确认模型后必须至少列出接口原始 ID、规范展示名、供应商前缀 ID 三种形式（如识别到 gpt-5.6-sol，aliases 为 ["gpt-5.6-sol", "GPT-5.6 Sol", "openai/gpt-5.6-sol"]）；只允许基于已确认 ID 做格式规范化，不得把其他模型当别名。
- 监控目标是站点实际售价，不是官方参考价：页面同时出现站点价与“官方价”时必须填站点价；official_pricing、official_price 等官方价字段只能作参考证据，绝不能填入 input_price 或 output_price。
- input_price、output_price 不明确时填 null，不得把倍率或余额当成价格。页面多个价格的显示顺序不等于归属；只有页面明确标注来源时才映射，否则填 null 并在 notes 说明无法映射。
- 位置型价格数组：证据里出现"[位置型价格数组解读]"和"[分组实付价结论]"结论行时，它们是调用方对压缩数组的确定性判读，直接采信：每个"[分组实付价结论]"各输出一条记录，group 填结论中的分组名，input_price/output_price/cache_read_price 填结论里的数值，status=candidate，notes 一句话说明取自结论行；不得再以"字段顺序不明"标 rule_only 或填 null。
- 没有上述结论行时才自行判读：压缩 JS/JSON 数组（如 ["模型ID",14,84,1.4,2,12,.2]）没有字段名时，按结构判读而不是臆造：同一数组出现两组"输入/输出/缓存读取"三元组时，较大一组是官方参考价（页面通常声明"官方参考价 = 上游美元价 × 7"之类的公式，数值也正好是 7 的倍数），另一组较小的就是站内实付基础价。input_price/output_price 必须填站内实付基础价 × 所在分组倍率的结果，官方价只能写进 notes 作参考。基础价 × 分组倍率是本任务的标准换算，必须执行，不算推断；只有白名单或倍率确实缺失时才允许 rule_only。识别出两组三元组即视为"页面明确标注来源"，本条优先于"不明确时填 null""字段名不清晰标 rule_only"等其他规则。严禁把无法解释的数字编造成"缓存创建""1小时缓存"等字段名，解释不了的数字留在 quote/notes 里。
- 分组倍率：页面存在多个分组（如 lite/plus/full）且每组标注倍率（如 .15/.2/.3）、目标模型出现在该分组的模型白名单里时，input_price/output_price = 站内实付价 × 该分组倍率；每个命中分组各展开一条记录（pricing_rules.groups 同步展开），group 填分组名，不得全部塞进 default。倍率或白名单缺失、无法确定目标模型所属分组时，价格填 null 并标 rule_only。
- 同一 page_evidence 中的“页面共享价格字段说明”是页面模型卡片共用的表头或字段定义；只有它明确给出字段顺序时，才可将同一卡片的数值映射为输入、输出或缓存价格，不得把其他模型的数值当表头或目标模型价格。
- currency/unit 必须依据证据中的币种标识（如 ¥/元/人民币/$/USD、priceMicroUsd 等字段名）填写；证据完全没说明币种时按人民币回退，填 CNY 与 "CNY/1M tokens" 并在 notes 说明；不得凭字段是纯数字就猜 USD。
- 价格按分组或上下文长度变化时，必须保留所有分组和 tiers（每个 tier 记 context_min/context_max、输入/输出和缓存价格），group 缺省为 default；上下文阶梯边界必须按证据原文填写（如 context_max=278000 与下一档 context_min=278001），不要猜测或改写。统一定价的站点也要输出一个 default 分组和一个无上限 tier，不要因为没有梯度就输出 unavailable。
{trim_rule}
- 响应使用公式、倍率或字段名不清晰时，保留原始 pricing_rules 并在 notes 说明未能换算成确定单价，不要丢弃原始规则；status=rule_only 用于只有 quota 倍率、公式或计费规则的站点。
- 每个模型必须独立建立证据闭环：模型名称、输入价格、输出价格必须出现在同一条页面或网络证据中；严禁把一个模型的价格复制、平均、换算或推断到另一个模型，严禁用其他模型的价格填补缺失字段。
- notes 每条不超过 120 字，只写关键字段依据（如"两组三元组，大组为官方价，小组合计基础价×倍率"）；推导过程不要在多条记录里重复，完整原文引用放 quote。
- status=confirmed 只有在网络响应证据和页面可见证据都存在且一致时才允许；cross_validation.conflicts 只能描述同一个模型的证据冲突，不同模型之间不要生成冲突。
- network_evidence 和 page_evidence 只能引用下方证据中真实出现的 source、URL 和 quote；quote 只保留能证明当前模型及价格的短原文片段，每条最多 500 个字符，不得回显完整网络响应或整页文本。
{unavailable_rule}

expected_models：
{models_line}

网页证据：
{evidence}"""
        return system, user, filtered_page_sources, clean_responses

    def extract(
        self,
        spec: SiteSpec,
        page_text: str,
        responses: list[dict[str, Any]],
        *,
        client: httpx.Client | None = None,
        expected_models: list[str] | None = None,
        page_sources: list[dict[str, str]] | None = None,
    ) -> list[PriceRecord]:
        if not self.config.enabled:
            raise AIExtractionError("价格监控 AI 已禁用")
        ai_model = self.config.pick_model()
        if not self.config.base_url or not ai_model:
            raise AIExtractionError("配置文件 ai.base_url 或 ai.models 未配置")
        expected = list(dict.fromkeys(expected_models or [target.name for target in spec.models]))
        if not expected:
            raise AIExtractionError(f"站点 {spec.id} 未配置目标模型 models")
        own = client is None
        client = client or ai_http_client(self.config)
        # 批大小随输出预算走：一条完整记录约 300~700 tokens（分组多更肥），按 1200/条折减给
        # 思考型模型留余量——max_tokens=4000 → 4 条/批（旧默认），16000 → 12 条/批；
        # 批越大，每批重复发送的系统提示与规则（数 K tokens）和调用开销省得越多
        batch_size = max(AI_EXTRACT_BATCH_SIZE, min(12, int(self.config.max_tokens or 16000) // 1200))
        # 整轮预算按并行波次折算再封顶：批间 AI_EXTRACT_CONCURRENCY 路并行，墙钟预算
        # 是「批次数 × 600s」串行口径的 1/并发数；封顶保证硬超时（900s）不抢先作废整站
        batch_count = max(1, -(-len(expected) // batch_size))
        waves = max(1, -(-batch_count // AI_EXTRACT_CONCURRENCY))
        budget_seconds = min(_ai.SITE_AI_BUDGET_SECONDS * waves, _ai.SITE_AI_BUDGET_MAX_SECONDS)
        deadline = time.monotonic() + budget_seconds
        try:
            records: list[PriceRecord] = []
            # 分批抽取：单批输出控制在 max_tokens 预算内；任一批失败整轮失败，不落半份数据。
            # 批间相互独立（各自证据、各自缓存键、各自调用），按 AI_EXTRACT_CONCURRENCY 路并行
            # 发起、按批次顺序收结果落记录；deadline 随批次进线程，慢模型+换模型重试超预算的
            # 批次自己报错。worker 线程不共享主线程的 tasklog 出口，逐线程重绑转发过程日志
            batches = [expected[start : start + batch_size] for start in range(0, len(expected), batch_size)]
            sink = tasklog.current_sink()
            workers = max(1, min(AI_EXTRACT_CONCURRENCY, len(batches)))

            def run_batch(index: int) -> list[PriceRecord]:
                if sink is not None:
                    tasklog.bind(sink)
                # 预算在批次开工入口检查：串行拦住后续批次，并行拦住还没开工的槽位
                if time.monotonic() > deadline:
                    raise AIBudgetExhaustedError(
                        f"AI 提取超出单站 {budget_seconds} 秒预算（共 {batch_count} 批），剩余批次不再发起"
                    )
                batch = batches[index]
                # 逐批留痕：单批十几秒到两分钟，成功路径无日志时测试弹窗只能干等，
                # 任务列表也看不出整轮是卡死还是在正常推进
                tasklog.emit(
                    f"[{spec.id}] AI 解析模型名：第 {index + 1}/{batch_count} 批（{len(batch)} 个模型）"
                )
                return self._extract_batch(spec, page_text, responses, batch, page_sources, client, deadline)

            budget_error: AIBudgetExhaustedError | None = None
            try:
                if workers <= 1:
                    for index in range(len(batches)):
                        records.extend(run_batch(index))
                else:
                    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"ai-extract-{spec.id}") as executor:
                        futures = [executor.submit(run_batch, index) for index in range(len(batches))]
                        for future in futures:
                            try:
                                records.extend(future.result())
                            except AIBudgetExhaustedError as exc:
                                # 预算耗尽的批次产出为零，warn 一次继续收其余批次；
                                # 所有批次都颗粒无收时按整轮预算失败报错
                                budget_error = budget_error or exc
            except AIBudgetExhaustedError:
                if not records:
                    raise
                budget_error = budget_error or AIBudgetExhaustedError("预算耗尽")
            if budget_error is not None and not records:
                raise budget_error
            if budget_error is not None:
                tasklog.emit(
                    f"[{spec.id}] AI 提取超出单站 {budget_seconds} 秒预算，保留已完成 {len(records)} 条，剩余批次不再发起",
                    "warn",
                )
            return records
        finally:
            if own:
                client.close()

    def extract_aliases(
        self,
        spec: SiteSpec,
        responses: list[dict[str, Any]],
        *,
        client: httpx.Client | None = None,
        expected_models: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """new-api 直采路径专用的轻量模型名对照：单次调用，只回名字映射不回价格。

        重量级 extract() 的输出按统一结构带价格分档和证据引用，一条 300~700 tokens，
        必须按 4 模型分批才塞得进输出预算；而直采路径价格本来就不从 AI 来，AI 只提供
        「标准名 → 站点接口模型名」的对照，输出是纯映射表，全部目标模型一次调用完成。
        返回 {标准名: {"observed_model": str, "aliases": [str, ...]}}，原始名与别名都
        已过滤到站点接口名单内；接口里没有模型名单时返回空表（直采同样解析不出，
        不值得烧一次 AI），解析或校验失败抛 AIExtractionError，调用方按既定设计回落
        无别名的直采定价。
        """
        if not self.config.enabled:
            raise AIExtractionError("价格监控 AI 已禁用")
        if not self.config.base_url or not self.config.pick_model():
            raise AIExtractionError("配置文件 ai.base_url 或 ai.models 未配置")
        expected = [name for name in dict.fromkeys(expected_models or [target.name for target in spec.models]) if name]
        if not expected:
            raise AIExtractionError(f"站点 {spec.id} 未配置目标模型 models")
        site_names = sorted({
            str(item.get("model_name")).strip()
            for response in responses
            if isinstance(response.get("payload"), dict)
            for item in response["payload"].get("data", [])
            if isinstance(item, dict) and str(item.get("model_name", "")).strip()
        })
        if not site_names:
            return {}
        # 缓存键独立于价格抽取：对照只取决于两份名单，名单没变（顺序无关）就一直命中
        evidence = json.dumps({"models": expected, "site_names": site_names}, ensure_ascii=False)
        key = payload_hash({"version": 1, "kind": "aliases", "site": spec.id, "evidence": evidence})
        cached = self._cached_result(key)
        if isinstance(cached, dict) and isinstance(cached.get("mappings"), list):
            return _alias_mappings(cached["mappings"], expected, set(site_names))
        own = client is None
        client = client or ai_http_client(self.config)
        system = (
            "你是模型名对照助手。给你两份名单：目标模型的标准名列表，和一个模型中转站接口返回的模型 ID 列表。"
            "把每个标准名对应到站点名单里属于同一模型的模型 ID：只允许同一家模型的命名变体"
            "（供应商前缀、大小写、分隔符、版本号写法差异），禁止把标准名对应到另一个不同模型。"
            "站点名单里找不到对应项的标准名直接省略，不要编造。必须只返回 JSON，不要解释。"
        )
        user = (
            f"目标标准名：{json.dumps(expected, ensure_ascii=False)}\n"
            f"站点接口模型名：{json.dumps(site_names, ensure_ascii=False)}\n"
            '输出结构：{"mappings": [{"model": "标准名", "observed_model": "站上模型 ID 或空串", "aliases": ["属于该标准名的站点模型 ID"]}]}'
        )
        try:
            _, response = _ai.request_with_model_fallback(
                self.config, system, user,
                max_tokens=4000,
                client=client,
                scene="模型名对照",
                validate=_validated_alias_payload,
                deadline=time.monotonic() + 300,
            )
        finally:
            if own:
                client.close()
        mappings = _validated_alias_payload(ai_content(self.config.api_format, response.json()))["mappings"]
        self._save_cached_result(key, {"mappings": mappings})
        return _alias_mappings(mappings, expected, set(site_names))

    def _extract_batch(
        self,
        spec: SiteSpec,
        page_text: str,
        responses: list[dict[str, Any]],
        expected: list[str],
        page_sources: list[dict[str, str]] | None,
        client: httpx.Client,
        deadline: float | None = None,
    ) -> list[PriceRecord]:
        raw_result: dict[str, Any] | None = None
        evidence_key = ""
        base_evidence_key = ""
        searchable = ""
        network_evidence: list[dict[str, Any]] = []
        previous_body = ""
        ai_model = self.config.pick_price_model()
        for max_chars in EVIDENCE_CHAR_LADDER:
            system, user, page_evidence, network_evidence = self._request(
                spec, page_text, responses, expected, page_sources, max_chars=max_chars,
            )
            request_key = json.dumps({"system": system, "user": user}, ensure_ascii=False, sort_keys=True)
            if request_key == previous_body:
                # 证据本身没超过当前上限，请求与上一次完全相同，再发也必然同样被拒。
                continue
            previous_body = request_key
            evidence_key = self._cache_key(spec, expected, json.dumps({"page": page_evidence, "network": network_evidence}, ensure_ascii=False, sort_keys=True))
            if max_chars == EVIDENCE_CHAR_LADDER[0]:
                base_evidence_key = evidence_key
            cached = self._cached_result(evidence_key)
            if cached is not None:
                raw_result = cached
                searchable = join_page_sources(page_evidence) + "\n" + json.dumps(network_evidence, ensure_ascii=False)
                break
            if not self.config.api_key:
                raise AIExtractionError("配置文件 ai.api_key 未配置")
            searchable = join_page_sources(page_evidence) + "\n" + json.dumps(network_evidence, ensure_ascii=False)
            try:
                ai_model, response = _ai.request_with_model_fallback(
                    self.config, system, user, client=client, scene="价格抽取",
                    validate=_validated_price_payload, deadline=deadline, preferred_model=ai_model,
                )
                raw_result = json_content(ai_content(self.config.api_format, response.json()))
                break
            except httpx.HTTPStatusError as exc:
                if _prompt_too_long(exc) and max_chars != EVIDENCE_CHAR_LADDER[-1]:
                    continue
                raise AIExtractionError(f"AI 价格识别请求失败: {exc}{_response_detail(exc)}") from exc
            except httpx.TimeoutException as exc:
                # 只在大证据超时时降级重试——小证据超时多半是供应商抖动，多等无益。
                if len(request_key) > 100_000 and max_chars != EVIDENCE_CHAR_LADDER[-1]:
                    continue
                raise AIExtractionError(f"AI 价格识别请求失败: {exc}") from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise AIExtractionError(f"AI 价格识别请求失败: {exc}{_response_detail(exc)}") from exc
        if raw_result is None:
            raise AIExtractionError("AI 请求未完成")
        allowed_urls = {redact_url(str(item.get("url", ""))) for item in network_evidence}
        response_bodies = {
            redact_url(str(item.get("url", ""))): str(item.get("quote", ""))
            for item in network_evidence
        }
        records = self._records(spec, raw_result, searchable, payload_hash(raw_result), allowed_urls, response_bodies, expected, ai_model=ai_model)
        self._save_cached_result(evidence_key, raw_result)
        if base_evidence_key and evidence_key != base_evidence_key:
            # 结果同时挂在未截断证据的 key 下：页面内容不变时，下次无需再白等一次超时。
            self._save_cached_result(base_evidence_key, raw_result)
        return records

    def _records(
        self,
        spec: SiteSpec,
        result: dict[str, Any],
        searchable: str,
        result_hash: str,
        allowed_urls: set[str],
        response_bodies: dict[str, str] | None = None,
        expected_models: list[str] | None = None,
        ai_model: str = "",
    ) -> list[PriceRecord]:
        raw_models = result.get("models")
        if not isinstance(raw_models, list):
            raise AIExtractionError("AI 标准化结果缺少 models 数组")
        models = merge_model_items([item for item in raw_models if isinstance(item, dict)])
        validation = result.get("cross_validation") if isinstance(result.get("cross_validation"), dict) else {}
        validation_status = str(validation.get("status", "none"))
        records: list[PriceRecord] = []
        expected = expected_models or []
        returned_models: set[str] = set()
        for item in models:
            if not isinstance(item, dict) or not isinstance(item.get("model"), str):
                continue
            raw_model = item["model"].strip()
            model = canonical_target(raw_model, expected) if expected else raw_model
            if expected and model is None:
                continue
            returned_models.add(model)
            network_evidence = item.get("network_evidence") if isinstance(item.get("network_evidence"), list) else []
            page_evidence = item.get("page_evidence") if isinstance(item.get("page_evidence"), list) else []
            observed_model = str(item.get("observed_model") or "").strip()
            aliases = [
                str(alias).strip()
                for alias in (item.get("aliases") if isinstance(item.get("aliases"), list) else [])
                if isinstance(alias, str) and alias.strip()
            ]
            evidence_names = [model, observed_model, *aliases]
            model_in_evidence = any(contains_model_alias(searchable, name) for name in evidence_names if name)
            network_urls_valid = all(isinstance(entry, dict) and redact_url(str(entry.get("url", ""))) in allowed_urls for entry in network_evidence)
            required_sources = {"model_list"}
            network_sources = {
                str(entry.get("source") or "model_list")
                for entry in network_evidence
                if isinstance(entry, dict)
            }
            network_sources_valid = required_sources.issubset(network_sources)
            status = str(item.get("status", "unavailable"))
            input_price = number_or_none(item.get("input_price"))
            output_price = number_or_none(item.get("output_price"))
            input_forms = price_digit_forms(input_price) if input_price is not None else set()
            output_forms = price_digit_forms(output_price) if output_price is not None else set()
            network_price_evidence = False
            for entry in network_evidence:
                if not isinstance(entry, dict):
                    continue
                url = redact_url(str(entry.get("url", "")))
                payload_text = (response_bodies or {}).get(url, "").casefold()
                if (
                    any(contains_model_alias(payload_text, name) for name in evidence_names if name)
                    and input_price is not None
                    and output_price is not None
                    and any(value.casefold() in payload_text for value in input_forms)
                    and any(value.casefold() in payload_text for value in output_forms)
                ):
                    network_price_evidence = True
                    break
            page_price_evidence = False
            for entry in page_evidence:
                quote = (
                    str(entry.get("quote", ""))
                    if isinstance(entry, dict)
                    else str(entry)
                ).casefold()
                if (
                    any(contains_model_alias(quote, name) for name in evidence_names if name)
                    and input_price is not None
                    and output_price is not None
                    and any(value.casefold() in quote for value in input_forms)
                    and any(value.casefold() in quote for value in output_forms)
                ):
                    page_price_evidence = True
                    break
            if input_price is not None or output_price is not None:
                # 价格数字必须在证据文本中真实出现过：名字校验会被残留文案绕过（DaiTuAI 已下线的
                # Kimi 分组描述里仍写着 kimi-k3），AI 还会张冠李戴（实测把 gpt-5.4-mini 的
                # ¥0.11/¥0.68 安给页面上不存在的 step-3.5-flash）。证据里没有的数字一律作废；
                # searchable 是系统侧证据原文（AI 事后补写的引用不算），编 quote 无法自证
                searchable_cf = searchable.casefold()
                missing = [
                    label
                    for label, forms in (("输入", input_forms), ("输出", output_forms))
                    if forms and not any(form.casefold() in searchable_cf for form in forms)
                ]
                if missing:
                    status = "unavailable"
                    input_price = None
                    output_price = None
                    item.pop("pricing_rules", None)
                    item["notes"] = f"AI 给出的{'、'.join(missing)}价数字未在证据文本中出现，已作废；{item.get('notes', '')}".strip()
            if not model_in_evidence:
                status = "unavailable"
                # 模型名都不在证据里，价格必然是模型按常识编造的（实测 DaiTuAI 页面无 MiniMax
                # 时 AI 自报"证据未出现具体数值"仍给出 0.0014/0.0056），只降状态不清价会让
                # 幻觉价以 candidate 身份混进快照、再被合理性校验每轮作废刷异常卡片
                if input_price is not None or output_price is not None:
                    input_price = None
                    output_price = None
                    item.pop("pricing_rules", None)
                    item["notes"] = f"模型名未在网页或 JSON 证据中出现，AI 给出的价格已作废；{item.get('notes', '')}".strip()
            elif status == "confirmed" and (
                validation_status != "matched"
                or not network_evidence
                or not page_evidence
                or not network_urls_valid
                or not network_sources_valid
                or not network_price_evidence
                or not page_price_evidence
            ):
                status = "candidate"
                item["notes"] = f"网络证据未同时包含模型列表中的模型和输入/输出价格；{item.get('notes', '')}".strip()
            if status == "unavailable" and (input_price is not None or output_price is not None):
                # AI 自报"没有可靠价格"却照提示词模板把数值抄成 0（"0"字符几乎总在证据里，
                # 数字在证闸门拦不住）：unavailable 是终态，占位行不得携带数值价格，
                # 否则 0/0 会以 candidate 落库，首页最低价挑选把无数据模型渲染成 ¥0 假免费价
                input_price = None
                output_price = None
                item.pop("pricing_rules", None)
                for cache_field in ("cache_read_price", "cache_create_price", "cache_create_1h_price"):
                    item[cache_field] = None
            if network_evidence and not network_urls_valid:
                item["notes"] = f"AI 给出的网络响应 URL 不在已请求接口列表中；{item.get('notes', '')}".strip()
            has_pricing_rules = has_pricing_tiers(item.get("pricing_rules"))
            if status == "rule_only" and not has_pricing_rules:
                # AI 状态给了 rule_only 但没回填任何真实 tiers，视为拿不到价格。
                status = "unavailable"
                item["notes"] = f"AI 未返回可用的计费规则或价格；{item.get('notes', '')}".strip()
            if status == "rule_only" or (has_pricing_rules and input_price is None and output_price is None):
                price_status: str = "rule_only"
                pricing_kind = "tiered_expr"
            elif status == "confirmed" and input_price is not None and output_price is not None:
                price_status = "confirmed"
                pricing_kind = "explicit_price"
            elif input_price is not None or output_price is not None:
                price_status = "candidate"
                pricing_kind = "explicit_price"
            else:
                price_status = "unavailable"
                pricing_kind = "unavailable"
            evidence_text = json.dumps({"page": page_evidence, "network": network_evidence}, ensure_ascii=False)
            currency = item.get("currency")
            unit = str(item.get("unit") or "来源未说明单位")
            evidence_casefold = evidence_text.casefold()
            has_usd_marker = any(marker in evidence_casefold for marker in ("$", "usd", "美元", "dollar"))
            if (input_price is not None or output_price is not None) and (
                currency not in ("CNY", "USD") or (currency == "USD" and not has_usd_marker)
            ):
                # 纯数字接口没标币种、或 AI 声称 USD 但证据里找不到任何美元标识时，
                # 按人民币回退，不得猜 USD。
                currency = "CNY"
                unit = "CNY/1M tokens"
                item["notes"] = f"证据未标明美元，按人民币回退；{item.get('notes', '')}".strip()
            if ("¥" in evidence_text or "人民币" in evidence_text) and currency == "USD":
                currency = "CNY"
                unit = unit.replace("USD", "CNY")
                metadata_note = "AI 将人民币符号误识别为 USD，已按页面证据纠正为 CNY。"
                item["notes"] = f"{metadata_note}{item.get('notes', '')}"
            if currency == "CNY" and "USD" in unit:
                unit = unit.replace("USD", "CNY")
            # browser_ai 只表示价格由 AI 从页面文本提取；"browser" 是历史命名，与采集是否走了浏览器无关
            metadata = {
                "adapter": "browser_ai",
                "ai_model": ai_model,
                "ai_result_sha256": result_hash,
                "observed_model": observed_model or None,
                "aliases": aliases,
                "group": item.get("group"),
                "context_min": number_or_none(item.get("context_min")),
                "context_max": number_or_none(item.get("context_max")),
                "cache_read_price": number_or_none(item.get("cache_read_price")),
                "cache_create_price": number_or_none(item.get("cache_create_price")),
                "cache_create_1h_price": number_or_none(item.get("cache_create_1h_price")),
                "pricing_rules": item.get("pricing_rules") or item.get("groups") or item.get("tiers"),
                "pricing_kind": pricing_kind,
                "currency": currency,
                "confidence": number_or_none(item.get("confidence")),
                "network_evidence": network_evidence,
                "page_evidence": page_evidence,
                "cross_validation": {"status": validation_status, "conflicts": validation.get("conflicts", [])},
                "notes": item.get("notes", ""),
            }
            raw_groups = metadata.get("pricing_rules").get("groups") if isinstance(metadata.get("pricing_rules"), dict) else None
            split_groups = [group for group in raw_groups if isinstance(group, dict)] if isinstance(raw_groups, list) else []
            needs_split = len(split_groups) > 1 or (
                len(split_groups) == 1 and str(split_groups[0].get("name") or "default").casefold() not in ("", "default")
            )
            if not needs_split:
                records.append(PriceRecord(model, input_price, output_price, unit, str(spec.network.get("url") or ""), time.time(), metadata, price_status))
                continue
            # AI 路径把多个分组的单价塞在同一条记录里时，拆成与 NewAPI 路径一致的 per-group 记录。
            # 各分组的具体价留在 metadata.pricing_rules，由 report.summary_row 按分组平铺。
            item_group = str(item.get("group") or "default").casefold()
            for group in split_groups:
                group_name = str(group.get("name") or "default")
                is_item_group = group_name.casefold() == item_group or (not item.get("group") and group_name.casefold() == "default")
                group_metadata = dict(metadata)
                group_metadata["group"] = group_name
                group_metadata["pricing_rules"] = {"groups": [group]}
                records.append(PriceRecord(
                    model,
                    input_price if is_item_group else None,
                    output_price if is_item_group else None,
                    unit,
                    str(spec.network.get("url") or ""),
                    time.time(),
                    group_metadata,
                    price_status,
                ))
        returned_models = {record.model.casefold() for record in records}
        for model in expected:
            if model.casefold() in returned_models:
                continue
            records.append(PriceRecord(
                model,
                None,
                None,
                "来源未说明单位",
                str(spec.network.get("url") or ""),
                time.time(),
                {
                    "adapter": "browser_ai",
                    "ai_model": ai_model,
                    "ai_result_sha256": result_hash,
                    "pricing_kind": "unavailable",
                    "currency": None,
                    "confidence": None,
                    "network_evidence": [],
                    "page_evidence": [],
                    "cross_validation": {"status": validation_status, "conflicts": validation.get("conflicts", [])},
                    "notes": "AI 未返回该模型的标准化记录",
                },
                "unavailable",
            ))
        return records


