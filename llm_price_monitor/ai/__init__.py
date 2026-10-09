"""AI 抽取/请求层（原 ai.py 拆分为包）。

公有接口与旧单文件模块完全一致：`from llm_price_monitor.ai import X` 与
`llm_price_monitor.ai.X`（含测试对 ai_log_hook / _MODEL_COOLDOWN 等属性的
monkeypatch）继续可用。json / random / time / httpx / re 等模块对象也保留在
包命名空间上，测试按旧方式 `ai_mod.random.shuffle` 打桩不受影响。
"""
from __future__ import annotations

# 旧 ai.py 的模块级导入，测试会经 ai_mod.random / ai_mod.time / ai_mod.httpx 打桩，
# 必须作为包属性保留（各自是共享模块对象，打桩全局生效）。
import json
import random
import re
import time
from typing import Any

import httpx

# 旧 ai.py 曾从 config 导入 AIConfig（notice.py 等经 llm_price_monitor.ai 引用它），保持暴露
from llm_price_monitor.config import AIConfig

from .errors import (
    AIBudgetExhaustedError,
    AIExtractionError,
    _error_message,
    _plain_text,
    _prompt_too_long,
    _response_detail,
    fit_text,
    provider_error_detail,
)
from .state import (
    MIN_USABLE_MAX_TOKENS,
    AiLogHook,
    _MODEL_MAX_TOKENS_LIMIT,
    ai_log_hook,
    learn_model_limit,
    load_model_limits,
    log_ai_request,
    model_limits_loader,
    model_limits_saver,
)
from .client import ai_http_client
from .api_format import (
    _anthropic_endpoint,
    _anthropic_messages,
    _arguments_object,
    _finish_reason,
    _gemini_contents,
    _responses_endpoint,
    _responses_messages,
    _stream_delta,
    _tool_specs_for,
    _usage_tokens,
    _with_token_budget,
    ai_content,
    ai_endpoint,
    ai_request,
    chat_content,
    json_content,
)
from .pool import (
    _MODEL_COOLDOWN,
    _fallback_pool,
    _max_tokens_range_error,
    model_pool,
    request_with_model_fallback,
)
from .streaming import ai_stream, ai_stream_fallback, ai_stream_messages_fallback
from .extractor import (
    AI_EXTRACT_BATCH_SIZE,
    EVIDENCE_CHAR_LADDER,
    NEWAPI_ONEAPI_PRICING_GUIDANCE,
    SITE_AI_BUDGET_SECONDS,
    AIPriceExtractor,
    canonical_cache_evidence,
)
from .tasks import extract_notice_content, infer_token_fields, ping_model

__all__ = [
    "AIExtractionError",
    "AIBudgetExhaustedError",
    "AIPriceExtractor",
    "AI_EXTRACT_BATCH_SIZE",
    "AiLogHook",
    "EVIDENCE_CHAR_LADDER",
    "MIN_USABLE_MAX_TOKENS",
    "NEWAPI_ONEAPI_PRICING_GUIDANCE",
    "SITE_AI_BUDGET_SECONDS",
    "ai_content",
    "ai_endpoint",
    "ai_http_client",
    "ai_log_hook",
    "ai_request",
    "ai_stream",
    "ai_stream_fallback",
    "ai_stream_messages_fallback",
    "canonical_cache_evidence",
    "chat_content",
    "extract_notice_content",
    "fit_text",
    "infer_token_fields",
    "learn_model_limit",
    "load_model_limits",
    "log_ai_request",
    "model_limits_loader",
    "model_limits_saver",
    "model_pool",
    "ping_model",
    "provider_error_detail",
    "request_with_model_fallback",
]
