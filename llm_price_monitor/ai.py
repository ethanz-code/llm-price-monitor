"""AI 价格抽取：把证据交给 LLM 标准化，并校验返回结果。

抽取只用 model_list 网络证据与页面证据；pricing 本身保持确定性，
AI 只负责识别模型别名、补齐别名形式和标准化字段。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from llm_price_monitor.config import AIConfig, PriceMonitorError, SiteSpec
from llm_price_monitor.evidence import (
    candidate_payload,
    contains_price_evidence,
    first_model_segment,
    first_target_payload,
    is_preferred_response_url,
    join_page_sources,
    payload_hash,
    redact_text,
    redact_url,
    sanitize_evidence,
    structure_page_source,
)
from llm_price_monitor.matching import canonical_target, contains_model_alias
from llm_price_monitor.tracker import PriceRecord, _TextParser
from llm_price_monitor.units import has_pricing_tiers, merge_model_items, number_or_none

NEWAPI_ONEAPI_PRICING_GUIDANCE = """你正在分析 one-api/new-api 风格的中转站价格接口。响应通常包含顶层 data 数组和 group_ratio 字典；data 中模型字段包括 model_ratio、completion_ratio、cache_ratio、create_cache_ratio、enable_groups，部分模型包含 billing_mode/billing_expr 或 pricing_rules。普通模式以 model_ratio=1 对应的站点基准价（默认 2 CNY/1M tokens，除非证据或配置明确说明其他币种/基准）计算：输入单价=model_ratio×基准价，输出单价=输入单价×completion_ratio，缓存读取=输入单价×cache_ratio，缓存写入=输入单价×create_cache_ratio，最后乘 group_ratio[分组名]。模型的 enable_groups 是可用分组列表，必须为每个分组分别输出，不能按显示顺序猜分组。billing_mode=tiered_expr 时忽略普通 model_ratio 公式，执行 billing_expr；表达式中的系数就是最终每 1M tokens 单价，不再乘基准价。len 是总上下文 token 数，p/c/cr 分别是输入/输出/缓存读取 token 数；若存在 pricing_rules.tiers，也要保留每个上下文阶梯。单次请求费用按各类 token 数除以 1,000,000 后乘对应单价。没有明确证据时填 null/unavailable，不要把倍率、余额或官方参考价冒充实际价格。"""


class AIExtractionError(PriceMonitorError):
    pass


# 证据超长被供应商拒绝时，按阶梯收紧单条证据文本上限逐级重试；None 表示不限制。
EVIDENCE_CHAR_LADDER: tuple[int | None, ...] = (None, 240_000, 96_000, 40_000, 16_000)

_PROMPT_TOO_LONG_MARKERS = (
    "1261",
    "prompt 超长",
    "prompt is too long",
    "context length",
    "maximum context",
    "too many tokens",
    "request too large",
)


def _plain_text(value: str) -> str:
    """HTML 页面证据转纯文本，避免整页标签撑爆 AI 上下文；JSON 等非 HTML 内容原样返回。"""
    if not value.lstrip().startswith("<"):
        return value
    parser = _TextParser()
    parser.feed(value)
    return " ".join(parser.parts)


def _fit_text(value: str, limit: int) -> str:
    """把超长证据文本收敛到 limit 字符以内；保留开头与结尾，价格证据可能在任一端。"""
    if len(value) <= limit:
        return value
    head = limit * 7 // 10
    tail = limit - head
    return value[:head] + "\n…[证据过长已截断]…\n" + value[-tail:]


def _prompt_too_long(exc: httpx.HTTPStatusError) -> bool:
    body = exc.response.text[:2000].casefold()
    return any(marker in body for marker in _PROMPT_TOO_LONG_MARKERS)


def _response_detail(exc: httpx.HTTPError) -> str:
    response = getattr(exc, "response", None)
    body = response.text[:300].strip() if response is not None else ""
    return f"；响应: {body}" if body else ""


def ai_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def ping_model(config: AIConfig, model: str, *, client: httpx.Client | None = None) -> str:
    """发送一次最小对话请求验证 AI 配置连通性，返回模型回复文本；HTTP/网络错误原样抛出。"""
    url, headers, request_body = ai_request(config, model, "", "连接测试，请只回复 ok", max_tokens=8, json_mode=False)
    own_client = client or httpx.Client(timeout=config.timeout)
    try:
        response = own_client.post(url, headers=headers, json=request_body, timeout=config.timeout)
    finally:
        if client is None:
            own_client.close()
    response.raise_for_status()
    return ai_content(config.api_format, response.json()).strip()


def chat_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AIExtractionError("AI 响应缺少 choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise AIExtractionError("AI 响应缺少 message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    raise AIExtractionError("AI 响应 content 不是文本")


def json_content(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIExtractionError(f"AI 没有返回合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AIExtractionError("AI 标准化结果必须是 JSON 对象")
    return parsed


def _responses_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/responses"):
        return base
    if base.endswith("/v1"):
        return f"{base}/responses"
    return f"{base}/v1/responses"


def _anthropic_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1/messages"):
        return base
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


def ai_request(
    config: AIConfig,
    model: str,
    system: str,
    user: str,
    *,
    max_tokens: int | None = None,
    json_mode: bool = True,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """按配置的接口结构（config.api_format）构造请求 URL、headers 与 body；只构造不发送。"""
    limit = config.max_tokens if max_tokens is None else max_tokens
    if config.api_format == "openai_responses":
        input_messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
        body = {
            "model": model,
            "input": input_messages,
            "max_output_tokens": limit,
            "temperature": 0,
        }
        if json_mode:
            body["text"] = {"format": {"type": "json_object"}}
        headers = {"content-type": "application/json"}
        if config.api_key:
            headers["authorization"] = f"Bearer {config.api_key}"
        return _responses_endpoint(config.base_url), headers, body
    if config.api_format == "anthropic":
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": limit,
            "temperature": 0,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            body["system"] = system
        return (
            _anthropic_endpoint(config.base_url),
            {"content-type": "application/json", "x-api-key": config.api_key or "", "anthropic-version": "2023-06-01"},
            body,
        )
    if config.api_format == "gemini":
        base = config.base_url.rstrip("/")
        if base.endswith(":generateContent"):
            url = base
        elif base.endswith("/v1beta"):
            url = f"{base}/models/{model}:generateContent"
        else:
            url = f"{base}/v1beta/models/{model}:generateContent"
        generation_config: dict[str, Any] = {"temperature": 0, "maxOutputTokens": limit}
        if json_mode:
            generation_config["responseMimeType"] = "application/json"
        gemini_body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": generation_config,
        }
        if system:
            gemini_body["systemInstruction"] = {"parts": [{"text": system}]}
        return url, {"content-type": "application/json", "x-goog-api-key": config.api_key or ""}, gemini_body
    if config.api_format != "chat_completions":
        raise AIExtractionError(f"未知的 AI 接口结构: {config.api_format}")
    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": limit,
        "messages": messages,
    }
    if json_mode:
        body["enable_thinking"] = config.enable_thinking
        body["response_format"] = {"type": "json_object"}
    return ai_endpoint(config.base_url), headers, body


def ai_content(api_format: str, payload: dict[str, Any]) -> str:
    """从所选接口结构的响应 JSON 中取出文本回复。"""
    if api_format == "openai_responses":
        output = payload.get("output")
        if not isinstance(output, list) or not output:
            raise AIExtractionError("AI 响应缺少 output")
        texts: list[str] = []
        for item in output:
            content = item.get("content") if isinstance(item, dict) else None
            if isinstance(content, list):
                texts.extend(str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "output_text")
        text = "".join(texts)
        if not text:
            raise AIExtractionError("AI 响应 output 中没有文本输出")
        return text
    if api_format == "anthropic":
        blocks = payload.get("content")
        if not isinstance(blocks, list) or not blocks:
            raise AIExtractionError("AI 响应缺少 content 文本块")
        text = "".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict) and block.get("type") == "text")
        if not text:
            raise AIExtractionError("AI 响应 content 中没有文本块")
        return text
    if api_format == "gemini":
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise AIExtractionError("AI 响应缺少 candidates")
        content = candidates[0].get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list) or not parts:
            raise AIExtractionError("AI 响应缺少 candidates[0].content.parts")
        return "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
    return chat_content(payload)


class AIPriceExtractor:
    def __init__(self, config: AIConfig) -> None:
        self.config = config

    def _cache_key(self, spec: SiteSpec, expected_models: list[str], evidence: str) -> str:
        return payload_hash({"version": 2, "site": spec.id, "models": expected_models, "evidence": evidence})

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
            original_payload = sanitize_evidence(response.get("payload"))
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
            # 只按目标模型做候选预筛；找不到候选时保留完整响应，避免误丢模型。
            matched_models = ["unresolved"]
            if not preferred_response and not contains_price_evidence(original_body):
                continue
            for matched_model in matched_models:
                # Keep every object matching this target model. A single pricing
                # response commonly contains several configured models; selecting
                # only the first match silently discarded later prices.
                payload = candidate if matched_model == "unresolved" else first_target_payload(original_payload, matched_model)
                if matched_model == "unresolved" and candidate is None:
                    payload = original_payload
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
                    "target_model": matched_model,
                    "match_basis": [
                        basis
                        for basis, matched in (
                            ("response_url", contains_model_alias(response_url, matched_model)),
                            ("response_body", contains_model_alias(original_body, matched_model)),
                        )
                        if matched
                    ],
                    "quote": (
                        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        if payload is not None
                        else (first_model_segment(body, matched_model, expected_models) if matched_model != "unresolved" else body)
                    ),
                }
                clean_responses.append(captured)
        # 价格证据默认原样交给 AI；只有供应商以"超长"拒绝时才按 EVIDENCE_CHAR_LADDER 收紧上限。
        if max_chars is not None:
            for source in filtered_page_sources:
                source["quote"] = _fit_text(str(source.get("quote", "")), max_chars)
            for captured in clean_responses:
                captured["quote"] = _fit_text(str(captured.get("quote", "")), max_chars)
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
        system = (
            NEWAPI_ONEAPI_PRICING_GUIDANCE
            + "\n\n你是模型价格数据抽取器。只能使用 user 消息中的网页证据，禁止凭常识补全或猜测价格。"
            "只分析 expected_models 指定的目标模型，不要识别其他模型。"
            "调用方只会提供标准模型名，不要求用户预先填写 aliases；请从原始 JSON、页面证据和接口字段中自动识别展示名、供应商前缀、版本写法和接口 model ID，并分别填入 observed_model 与 aliases。确认一个模型后，aliases 必须至少同时列出接口原始 ID、规范展示名、供应商前缀 ID。例如识别到 gpt-5.6-sol 时，aliases 必须为 [\"gpt-5.6-sol\", \"GPT-5.6 Sol\", \"openai/gpt-5.6-sol\"]。这些是同一已确认模型的格式规范化，允许基于已确认 ID 生成；不得把不同模型当作别名。"
            "每个模型必须独立建立证据闭环：模型名称、输入价格、输出价格必须出现在同一条页面或网络证据中。"
            "严禁把一个模型的价格复制、平均、换算或推断到另一个模型；严禁用其他模型的价格填补缺失字段。"
            "请识别模型列表来源中的目标模型价格字段，并用页面与网络证据交叉核对。"
            "监控目标是站点实际售价，不是官方参考价。页面卡片同时出现站点价和“官方价”时，输入/输出字段必须取未标注“官方价”的站点价；官方价只能作为参考证据，绝不能填入 input_price 或 output_price。"
            "若同一页面卡片或结构中明确出现 expected_models 的精确 model ID，该 ID 对价格归属优先于 display_name、上游模型名或备注名；即使这些展示名称不一致，也必须按精确 model ID 归属于该目标模型。"
            "若页面卡片已明确标注 expected_models 的精确 model ID 和站点价格，即使网络响应没有同名模型记录，也要保留该页面价格并标记 candidate，不得因此输出 unavailable，也不得用网络中的相似模型价格替代。"
            "必须只返回 JSON，不要 Markdown，不要解释。"
        )
        user = f"""请将以下价格页面证据标准化。

输出结构必须是：
{{
  "models": [{{
    "model": "模型名",
    "observed_model": "页面或接口中实际出现的原始模型名",
    "aliases": ["属于该标准模型的展示名称或模型 ID，不要填标准模型名本身"],
    "input_price": 0,
    "output_price": 0,
    "unit": "USD/1M tokens",
    "currency": "USD",
    "status": "confirmed|candidate|rule_only|unavailable",
    "confidence": 0.0,
    "group": "default",
    "context_min": null,
    "context_max": null,
    "cache_read_price": null,
    "cache_create_price": null,
    "cache_create_1h_price": null,
    "pricing_rules": {{"groups": [{{"name": "default", "tiers": [{{"context_min": 0, "context_max": null, "input_price": 0, "output_price": 0, "cache_read_price": null, "cache_create_price": null, "cache_create_1h_price": null, "unit": "USD/1M tokens"}}]}}]}},
    "network_evidence": [{{"source": "model_list", "url": "实际捕获的响应 URL", "resource_type": "fetch|xhr", "match_basis": ["response_url|response_body"], "quote": "响应正文中的目标模型证据"}}],
    "page_evidence": [{{"source": "model_list", "url": "页面 URL", "target_model": "目标模型", "quote": "目标模型卡片中的原始页面证据"}}],
    "notes": ""
  }}],
  "cross_validation": {{"status": "matched|mismatch|partial|none", "conflicts": ["冲突说明"]}}
}}

规则：
- 只输出 expected_models 中的目标模型，忽略其他模型。
- model 必须填写 expected_models 中的标准名，不能填写页面显示名或接口模型 ID。
- observed_model 填写证据中实际出现的原始模型名（例如 \"Claude Opus 5\"）。
- aliases 表示该标准模型对应的展示名称和模型 ID。确认模型后必须提供接口原始 ID、规范展示名、供应商前缀 ID 三种形式；例如 gpt-5.6-sol 输出 [\"gpt-5.6-sol\", \"GPT-5.6 Sol\", \"openai/gpt-5.6-sol\"]。可以基于已确认 ID 进行大小写/前缀规范化，但不得把其他模型名放进来。
- model_list 是唯一来源；页面文字和网络响应都存在时必须比较并说明是否一致。
- input_price、output_price 不明确时填 null，不得把倍率或余额当成价格。
- 站点页面同时显示“输入 ¥X/1M 官方价 $Y/1M”或“输出 ¥X/1M 官方价 $Y/1M”时，必须填 X，不得填 Y；currency/unit 按站点价（例如 CNY）填写。
- network_evidence 中的 official_pricing、official_price 等字段属于官方参考价，不能覆盖页面显示的站点实际售价。
- 页面中多个价格的显示顺序不等于价格归属；只有页面明确标注来源时才填写对应价格，否则填写 null 并在 notes 说明无法映射。
- 同一 page_evidence 中的“页面共享价格字段说明”是该页面模型卡片共用的表头或字段定义；只有它明确给出字段顺序时，才可将同一卡片的数值映射为输入、输出或缓存价格。不得把其他模型的数值当作表头或目标模型价格。
- 价格按分组或上下文长度变化时，必须保留所有分组和 tiers，不得只返回第一条；每个 tier 记录 context_min/context_max、输入/输出和缓存价格。group 缺省为 default。
- model_list 的 network_evidence 是完整原始响应，可能同时包含多个模型、多个分组和多段上下文价格；不要因为当前 expected_models 只有一个就裁剪、过滤、截断或只取第一条。只在最终 models 输出中保留 expected_models 指定的模型。
- 上下文阶梯必须按证据中的明确边界填写，例如 `context_max=278000` 与下一档 `context_min=278001`；不要猜测边界，不要把“超过某长度”改写成固定数字。
- 如果响应使用公式、倍率或字段名不清晰，也要保留原始 pricing_rules，并在 notes 说明未能换算成确定单价；不要丢弃原始规则。
- 统一定价的网站也必须输出一个 default 分组和一个无上限 tier；若输入/输出明确且页面与网络证据一致，可确认，不要因为没有梯度就输出 unavailable。
- 只有页面文字无法把数字映射到输入/输出，或仅有官方参考价时，才填 null/candidate；不得把官方价当站点价。
- 每个模型单独校验，不能用跨模型的相同倍率、汇率、顺序或相邻卡片推导价格。
- cross_validation.conflicts 只能描述同一个模型的证据冲突；不同模型之间没有可比关系时不要生成冲突。
- status=confirmed 只有在网络响应证据和页面可见证据都存在且一致时才允许。
- status=rule_only 用于只有 quota 倍率、公式或计费规则的站点。
- network_evidence 只能引用下面 network_evidence 中真实出现的 source、URL 和 quote。
- network_evidence 和 page_evidence 的 quote 只能保留能证明当前模型及价格的短原文片段，每条最多 500 个字符；不得回显完整网络响应或整页文本。
- 没有可靠价格时也要为每个 expected_models 输出 unavailable 记录。

expected_models：
{json.dumps(expected_models, ensure_ascii=False)}

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
            raise AIExtractionError("配置文件 ai.base_url 或 ai.model/ai.models 未配置")
        expected = list(dict.fromkeys(expected_models or [target.name for target in spec.models]))
        if not expected:
            raise AIExtractionError("browser 价格监控必须配置目标模型 models")
        own = client is None
        client = client or httpx.Client(timeout=self.config.timeout)
        try:
            raw_result: dict[str, Any] | None = None
            evidence_key = ""
            base_evidence_key = ""
            searchable = ""
            network_evidence: list[dict[str, Any]] = []
            previous_body = ""
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
                    url, headers, request_body = ai_request(self.config, ai_model, system, user)
                    response = client.post(url, headers=headers, json=request_body, timeout=self.config.timeout)
                    response.raise_for_status()
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
        finally:
            if own:
                client.close()

    def _records(
        self,
        spec: SiteSpec,
        result: dict[str, Any],
        searchable: str,
        result_hash: str,
        allowed_urls: set[str],
        response_bodies: dict[str, str] | None = None,
        expected_models: list[str] | None = None,
        ai_model: str | None = None,
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
            if not network_evidence:
                legacy_api_evidence = item.get("api_evidence", []) if isinstance(item.get("api_evidence"), list) else []
                legacy_response_evidence = item.get("response_evidence", []) if isinstance(item.get("response_evidence"), list) else []
                network_evidence = [*legacy_api_evidence, *legacy_response_evidence]
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
            input_forms = {str(input_price), f"{input_price:g}"} if input_price is not None else set()
            output_forms = {str(output_price), f"{output_price:g}"} if output_price is not None else set()
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
            if not model_in_evidence:
                status = "unavailable"
                item["notes"] = f"模型名未在网页或 JSON 证据中出现；{item.get('notes', '')}".strip()
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
            if ("¥" in evidence_text or "人民币" in evidence_text) and currency == "USD":
                currency = "CNY"
                unit = unit.replace("USD", "CNY")
                metadata_note = "AI 将人民币符号误识别为 USD，已按页面证据纠正为 CNY。"
                item["notes"] = f"{metadata_note}{item.get('notes', '')}"
            if currency == "CNY" and "USD" in unit:
                unit = unit.replace("USD", "CNY")
            metadata = {
                "adapter": "browser_ai",
                "ai_model": ai_model if ai_model is not None else self.config.model,
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
                    "ai_model": ai_model if ai_model is not None else self.config.model,
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


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class AISchemaNormalizer:
    """AI 字段归一化兜底：把未知形态的价格证据归一化为标准 schema。

    只做字段映射（字段名/嵌套结构可能因站点而异），不推算、不换算任何数值；
    归一化结果仍由适配器确定性计价，产出记录标记 candidate。
    """

    def __init__(self, config: AIConfig):
        self.config = config

    def available(self) -> bool:
        return bool(self.config.enabled and self.config.base_url and self.config.pick_model())

    def _chat_json(self, system: str, user: str, client: httpx.Client | None) -> dict[str, Any]:
        ai_model = self.config.pick_model()
        if not self.config.base_url or not ai_model:
            raise AIExtractionError("配置文件 ai.base_url 或 ai.model/ai.models 未配置")
        if not self.config.api_key:
            raise AIExtractionError("配置文件 ai.api_key 未配置")
        url, headers, request_body = ai_request(self.config, ai_model, system, user)
        own = client is None
        client = client or httpx.Client(timeout=self.config.timeout)
        try:
            try:
                response = client.post(url, headers=headers, json=request_body, timeout=self.config.timeout)
                response.raise_for_status()
                return json_content(ai_content(self.config.api_format, response.json()))
            except (httpx.HTTPError, ValueError) as exc:
                raise AIExtractionError(f"AI schema 归一化请求失败: {exc}{_response_detail(exc)}") from exc
        finally:
            if own:
                client.close()

