"""AI 抽取错误类型与供应商错误响应解析。"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from llm_price_monitor.config import PriceMonitorError
from llm_price_monitor.tracker import TextParser

class AIExtractionError(PriceMonitorError):
    pass


class AIBudgetExhaustedError(AIExtractionError):
    """单站 AI 提取的时间预算耗尽：调用方可保留已完成批次的部分成果，不再发起新请求。"""

_PROMPT_TOO_LONG_MARKERS = (
    "1261",  # 裸数字错误码：个别供应商 prompt 超限时正文只返回错误码不带文案
    "prompt 超长",
    "prompt is too long",
    "context length",
    "maximum context",
    "too many tokens",
    "request too large",
    "range of input length",  # 阿里系（DashScope）：Range of input length should be [1, N]
)


def _plain_text(value: str) -> str:
    """HTML 页面证据转纯文本，避免整页标签撑爆 AI 上下文；JSON 等非 HTML 内容原样返回。"""
    if not value.lstrip().startswith("<"):
        return value
    parser = TextParser()
    parser.feed(value)
    return " ".join(parser.parts)


def fit_text(value: str, limit: int) -> str:
    """把超长证据文本收敛到 limit 字符以内；保留开头与结尾，价格证据可能在任一端。"""
    if len(value) <= limit:
        return value
    head = limit * 7 // 10
    tail = limit - head
    return value[:head] + "\n…[证据过长已截断]…\n" + value[-tail:]


def _prompt_too_long(exc: httpx.HTTPStatusError) -> bool:
    body = exc.response.text[:2000].casefold()
    return any(marker in body for marker in _PROMPT_TOO_LONG_MARKERS)

# 递归找错误消息的字段优先级：常见消息字段 → error/errors/detail 子结构
_ERROR_MESSAGE_KEYS = ("message", "msg", "detail", "error_description", "description")
_ERROR_NEST_KEYS = ("error", "errors", "detail")


def _error_message(value: Any, depth: int = 4) -> str:
    """在任意结构的错误 JSON 里递归找第一个可读消息，找不到返回空串。"""
    if depth < 0:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in _ERROR_MESSAGE_KEYS:
            field = value.get(key)
            if isinstance(field, str) and field.strip():
                return field.strip()
        for key in _ERROR_NEST_KEYS:
            if key in value:
                found = _error_message(value[key], depth - 1)
                if found:
                    return found
    if isinstance(value, list):
        for item in value:
            found = _error_message(item, depth - 1)
            if found:
                return found
    return ""


def provider_error_detail(response: httpx.Response) -> str:
    """把任意供应商的错误响应整理成可读一句话，不假定错误 JSON 的具体结构。

    递归找消息字段（OpenAI/DashScope、Anthropic、Gemini 的 {"error":{"message":...}}、
    Google 的 errors 数组、new-api/FastAPI 的 {"detail":...}、error 直接是字符串的写法都覆盖），
    HTML 错误页剥掉标签，纯文本原样保留；都取不到时退回响应原文。
    """
    body = response.text.strip()
    message = ""
    code = ""
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if payload is not None:
        message = _error_message(payload)
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            error = payload["error"]
            raw_code = error.get("type") or error.get("status") or error.get("code")
            code = str(raw_code) if raw_code is not None and not isinstance(raw_code, (int, float)) else ""
    if not message:
        message = _plain_text(body)[:300] if body.startswith("<") else body[:300]
    suffix = f"（{code}）" if code and code not in message else ""
    return f"HTTP {response.status_code}：{message[:300]}{suffix}"


def _response_detail(exc: httpx.HTTPError) -> str:
    response = getattr(exc, "response", None)
    return f"；{provider_error_detail(response)}" if response is not None else ""
