"""独立 AI 任务：连通性体检（ping）、公告正文提取、token 字段分析。"""
from __future__ import annotations

import json
from typing import Any

import httpx

import llm_price_monitor.ai as _ai

from .api_format import ai_content, ai_request, json_content
from .client import ai_http_client
from .errors import AIExtractionError
from .pool import _thinking_restricted

def ping_model(config: AIConfig, model: str, *, client: httpx.Client | None = None, timeout: float | None = None) -> str:
    """发送一次最小对话请求验证 AI 配置连通性，返回模型回复文本；HTTP/网络错误原样抛出。

    思考不可关的模型拒收 enable_thinking=false：翻成 true 重试一次，避免把可用配置误判为不通。
    timeout 缺省用配置值；模型池批量体检传短超时，个别慢模型按失败计，不拖住整轮。
    """
    request_timeout = config.timeout if timeout is None else timeout
    url, headers, base_body = ai_request(config, model, "", "连接测试，请只回复 ok", max_tokens=8, json_mode=False)
    own_client = client or ai_http_client(config, request_timeout)
    try:
        response = own_client.post(url, headers=headers, json=base_body, timeout=request_timeout)
        if _thinking_restricted(response) and base_body.get("enable_thinking") is False:
            response = own_client.post(url, headers=headers, json={**base_body, "enable_thinking": True}, timeout=request_timeout)
        response.raise_for_status()
    finally:
        if client is None:
            own_client.close()
    return ai_content(config.api_format, response.json()).strip()

def infer_token_fields(config: AIConfig, sample: str) -> dict[str, str]:
    """把续签接口的响应案例交给 AI，找 access_token / refresh_token 的字段路径。

    返回形如 {"access_token_field": "data.access_token"}，找不到的键不出现；AI 未配置或
    调用失败抛异常，由调用方决定是否降级。
    """
    sample = sample.strip()
    if not config.base_url or not config.models:
        raise AIExtractionError("AI 未配置，无法分析响应案例")
    if len(sample) > config.max_input_chars:
        sample = sample[: config.max_input_chars]
    system = (
        "你是接口响应结构分析器。用户会贴一段 token 续签接口的响应 JSON 案例。"
        "找出新的访问令牌（access_token / token / id_token 之类）和刷新令牌（refresh_token 之类）各自所在的字段路径，"
        "路径用点号逐层写，如 data.access_token；找不到的令牌路径填 null。"
        "只返回 JSON 对象：{\"access_token_field\": string|null, \"refresh_token_field\": string|null}，不要解释。"
    )
    _, response = _ai.request_with_model_fallback(config, system, sample, scene="token 分析")
    parsed = json_content(ai_content(config.api_format, response.json()))
    result: dict[str, str] = {}
    for key in ("access_token_field", "refresh_token_field"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip() and value.strip().lower() != "null":
            result[key] = value.strip()
    return result

def extract_notice_content(config: AIConfig, raw_text: str, *, client: httpx.Client | None = None) -> str | None:
    """让 AI 从公告接口的原始响应中逐字原样提取公告正文，站点没有公告时返回空串。

    原始响应可能是任意结构（new-api 包装、announcements 数组、HTML 片段等），
    固定解析规则认不出的形态交给 AI 判断什么是真正要拿的数据；AI 未启用、
    未配置或请求失败时返回 None，由调用方回落到固定解析结果。
    """
    if not config.usable:
        return None
    text = raw_text.strip()
    if not text:
        return None
    system = (
        "你是站点公告数据提取器。用户消息是某个 API 站点公告接口的原始响应（可能是 JSON、HTML 或纯文本）。"
        "从中找出站方发布的公告，并逐字原样提取正文：保持原文的用词、标点、空格和换行，"
        "禁止改写、润色、翻译、增删任何字符，禁止调整排版；响应是 HTML 时只去掉标签本身，标签内的文字原样保留。"
        "多条公告按时间从新到旧排列，每条开头保留它的标题和日期（同样逐字照抄原文）。"
        "只能使用原始响应中出现的内容，禁止凭常识补全。站点没有发布任何公告时，content 返回空字符串。"
        '必须只返回 JSON：{"content": "公告正文"}，不要解释。'
    )
    user = f"公告接口原始响应：\n{text[:config.max_input_chars]}"
    own = client is None
    client = client or ai_http_client(config)
    try:
        if not config.api_key:
            return None
        _, response = _ai.request_with_model_fallback(config, system, user, client=client, scene="公告提取")
        payload = json_content(ai_content(config.api_format, response.json()))
        content = payload.get("content")
        return content.strip() if isinstance(content, str) and content.strip() else ""
    except (httpx.HTTPError, ValueError, AIExtractionError):
        return None
    finally:
        if own:
            client.close()
