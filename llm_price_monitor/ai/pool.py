"""模型池随机抽取与换模型降级：冷却名单、翻参重试、截断放大与 max_tokens 上限联动。"""
from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

import llm_price_monitor.ai as _ai

from .api_format import _finish_reason, _usage_tokens, _with_token_budget, ai_content, ai_request
from .client import ai_http_client
from .errors import AIExtractionError, AIBudgetExhaustedError, _prompt_too_long, provider_error_detail
from .state import MIN_USABLE_MAX_TOKENS, learn_model_limit, log_ai_request

logger = logging.getLogger(__name__)

# 校验失败（空正文/坏 JSON/输出非对象）的模型短期冷却：随机抽池会让同一个惯犯模型反复被选中
# 反复失败，纯烧时间；冷却期内选模型直接跳过，全冷却时回退全池不拒服。截断（预算问题）与
# 传输抖动（渠道问题）不冷却——那不是模型的错。进程重启清零；助手路径另有存库版 24h 冷却。
_MODEL_COOLDOWN: dict[str, float] = {}
_MODEL_COOLDOWN_TTL = 2 * 3600
# fallback 日志里存的模型回复原文取尾部：JSON 坏在收尾（截断/少括号），开头没信息量
_RESPONSE_TAIL_CHARS = 500

def model_pool(config: AIConfig) -> list[str]:
    """去重后的候选模型池。"""
    return list(config.models)

# 这类状态码多半是单个模型的问题（不支持参数、无权限、限流、上游抖动），换池子里下一个模型重试；
# 401 是密钥问题，换模型没用，直接抛。
_MODEL_FALLBACK_STATUSES = frozenset({400, 402, 403, 404, 408, 422, 429, 500, 502, 503, 504})


# max_tokens 越界报错解析：DashScope 等会在 400 文案里给出该模型的准确上限
_MAX_TOKENS_RANGE_RE = re.compile(r"max_tokens should be \[1,\s*(\d+)\]", re.IGNORECASE)


def _thinking_restricted(response: httpx.Response) -> bool:
    """思考不可关的模型（如 glm-5.3）：收到 enable_thinking=false 报 400，且文案点名该参数。"""
    return response.status_code == 400 and "enable_thinking" in response.text



def _max_tokens_range_error(response: httpx.Response) -> int | None:
    """从 max_tokens 越界的 400 报错里解析出该模型的上限，解析不出返回 None。"""
    match = _MAX_TOKENS_RANGE_RE.search(response.text[:2000])
    return int(match.group(1)) if match else None

def _fallback_pool(config: AIConfig, preferred_model: str = "") -> list[str]:
    """换模型重试共用的起点：模型池为空直接抛配置错误；返回打乱后的副本供调用方再过滤。

    preferred_model 非空时固定排在首位（不在池里也照样排，池子只作其后备），其余随机乱序。
    """
    pool = model_pool(config)
    if not pool:
        raise AIExtractionError("配置文件 ai.models 未配置")
    rest = [name for name in pool if name != preferred_model]
    random.shuffle(rest)
    return ([preferred_model] if preferred_model else []) + rest


def _log_pool_exhausted(scene: str, model: str, last_error_text: str, prompt_excerpt: str) -> None:
    """模型池全部失败的整次失败补一条 error 行：成功率按最终结果统计时才不会漏掉这种失败。"""
    log_ai_request(
        scene=scene, model=model, status="error", duration_ms=0,
        error="模型池全部失败，最后一次错误 " + last_error_text, prompt_excerpt=prompt_excerpt,
    )


def _stream_thinking_attempts(config: AIConfig) -> list[bool | None]:
    """流式重试的翻参序列：只有 chat_completions 的请求体带 enable_thinking，其他结构翻参重试只会白发一次同样的请求。"""
    attempts: list[bool | None] = [None]
    if config.enable_thinking is False and config.api_format == "chat_completions":
        attempts.append(True)
    return attempts


def _stream_status_failure(
    exc: httpx.HTTPStatusError, *, model: str, scene: str, duration_ms: int,
    thinking_flip_possible: bool, enable_thinking: bool | None,
    prompt_excerpt: str, on_model_failure: Callable[[str, str], None] | None = None,
) -> tuple[str, str]:
    """流式尝试的 HTTP 状态错误共同定性（两个流式回退共用），返回 (action, error_text)。

    action："retry" 思考受限且本次还没翻参，同模型重试；"next" 回退状态码，换下一个模型；
    该上抛的（401 配置级问题 / 不在 _MODEL_FALLBACK_STATUSES 的失败）在这里直接抛 AIExtractionError。
    """
    error_text = provider_error_detail(exc.response)
    if thinking_flip_possible and enable_thinking is None and _thinking_restricted(exc.response):
        log_ai_request(scene=scene, model=model, status="param_retry", duration_ms=duration_ms, error="模型要求开启思考，已自动开启并用同一模型重试｜" + error_text, prompt_excerpt=prompt_excerpt)
        return "retry", error_text
    status = "fallback" if exc.response.status_code in _MODEL_FALLBACK_STATUSES else "error"
    log_ai_request(scene=scene, model=model, status=status, duration_ms=duration_ms, error=error_text, prompt_excerpt=prompt_excerpt)
    if exc.response.status_code == 401 or status == "error":
        raise AIExtractionError(error_text) from exc
    if on_model_failure is not None:
        on_model_failure(model, error_text)
    return "next", error_text


def _stream_transport_failure(
    exc: httpx.TransportError, *, model: str, scene: str, duration_ms: int,
    mid_stream: bool, prompt_excerpt: str, on_model_failure: Callable[[str, str], None] | None = None,
) -> str:
    """流式尝试的传输层失败共同处理（两个流式回退共用），返回错误明细供调用方留痕。

    正文已放给用户（mid_stream）只能记失败抛出，半截回答没法换模型重来；还没出字按渠道抖动
    处理——记 transport（连接抖动不算报错失败）后换下一个模型。
    """
    detail = f"{type(exc).__name__}: {exc}"
    if mid_stream:
        log_ai_request(scene=scene, model=model, status="error", duration_ms=duration_ms, error="流式输出中断｜" + detail, prompt_excerpt=prompt_excerpt)
        if on_model_failure is not None:
            on_model_failure(model, "流式输出中断｜" + detail)
        raise AIExtractionError(f"流式输出中断：{detail}") from exc
    log_ai_request(scene=scene, model=model, status="transport", duration_ms=duration_ms, error="连接失败，未收到响应｜" + detail, prompt_excerpt=prompt_excerpt)
    if on_model_failure is not None:
        on_model_failure(model, "连接失败，未收到响应｜" + detail)
    return detail

def request_with_model_fallback(
    config: AIConfig,
    system: str,
    user: str,
    *,
    max_tokens: int | None = None,
    json_mode: bool = True,
    client: httpx.Client | None = None,
    scene: str = "AI 请求",
    timeout: float | None = None,
    validate: Callable[[str], None] | None = None,
    deadline: float | None = None,
    preferred_model: str = "",
) -> tuple[str, httpx.Response]:
    """从模型池随机抽一个开始请求，模型自身报错（见 _MODEL_FALLBACK_STATUSES）自动换下一个。

    preferred_model 非空时固定用它开头（价格提取用：同一站点每轮同一模型读数，避免轮间漂移），
    池内其余模型作后备。最近校验失败进冷却名单的模型先跳过（全冷却时回退全池，不拒服）。思考不可关的模型拒收
    enable_thinking=false 时，先翻成 true 同模型重试一次，再考虑换模型。连接失败（超时/连接
    重置/SSL EOF 等传输错误）与模型报错同样换下一个并留痕。
    validate 传入时在 HTTP 成功后对回复正文做内容校验（如 JSON 可解析），校验失败按模型问题
    换下一个——思考型模型耗尽 max_tokens 输出空正文就靠这条兜住；正文非空且 token 用量顶格
    说明是预算截断（思考 token 与正文共享 max_tokens），同模型放大预算重试一次再考虑换，
    不冷却；空正文与没顶格的坏输出才是模型的产出质量问题，进冷却名单。冷却与截断判定的
    依据都随
    fallback 日志落库（token 用量 + finish 原因），日志里能直接看到模型实际回了什么。
    401 属于配置级问题原样抛出；prompt 超出单模型上下文按模型级故障换下一个——池内模型上下文差异大，一个装不下不代表全部装不下；max_tokens 超出模型上限时解析上限同模型降额重试并缓存，之后的请求经 ai_request 直接按上限构造。池子耗尽时抛最后一个错误。
    返回 (实际使用的模型, 响应)。每次尝试都写入 AI 请求日志。timeout 可覆盖配置的单次请求超时
    （如助手场景收紧死线），不传用 config.timeout。deadline 为换模型重试的总时长上界
    （time.monotonic() 时刻），超出即抛 AIExtractionError，价格提取用它与单站预算挂钩。
    """
    pool = _fallback_pool(config, preferred_model)
    now = time.time()
    # 最近校验失败进冷却名单的模型先跳过（全冷却时回退全池，不拒服）
    order = [name for name in pool if _ai._MODEL_COOLDOWN.get(name, 0) <= now] or pool
    request_timeout = timeout or config.timeout
    request_limit = config.max_tokens if max_tokens is None else max_tokens
    # 已学上限连本次最低输出预算（MIN_USABLE_MAX_TOKENS 与 request_limit 取小）都装不下的
    # 模型按"规格过小"剔除——发起必然截断成坏 JSON 白烧一次调用；全被剔时回退原序不拒服
    floor = min(MIN_USABLE_MAX_TOKENS, request_limit)
    too_small = [name for name in order if _ai._MODEL_MAX_TOKENS_LIMIT.get(name, request_limit) < floor]
    if too_small:
        order = [name for name in order if name not in too_small] or order
        logger.info("已学 max_tokens 上限低于 %d 的模型本轮跳过：%s", floor, "、".join(too_small))
    passed_client = client is not None
    client = client or ai_http_client(config, request_timeout)
    last_exc: Exception | None = None
    last_error_text = ""
    try:
        for model in order:
            if deadline is not None and time.monotonic() > deadline:
                # 换模型重试无总上限，链路整体抖动时会把整轮采集拖死；到预算即中止，
                # 由调用方按各自语义处理（价格提取回落确定性定价或记该站失败）
                raise AIBudgetExhaustedError("AI 请求超出单站时间预算，中止换模型重试")
            url, headers, base_body = ai_request(config, model, system, user, max_tokens=max_tokens, json_mode=json_mode)
            attempts = [base_body]
            if base_body.get("enable_thinking") is False:
                attempts.append({**base_body, "enable_thinking": True})
            budget_tried = False
            token_clamped = False
            while attempts:
                body = attempts.pop(0)
                started = time.monotonic()
                try:
                    # client 统一由 ai_http_client / 调用方注入：测试可以 patch httpx.Client 或直接传 client
                    response = client.post(url, headers=headers, json=body, timeout=request_timeout)
                    response.raise_for_status()
                    duration_ms = int((time.monotonic() - started) * 1000)
                    payload: dict[str, Any] = {}
                    parse_error: str | None = None
                    try:
                        payload = response.json()
                        prompt_tokens, completion_tokens, total_tokens = _usage_tokens(config.api_format, payload)
                        answer = ai_content(config.api_format, payload)
                    except Exception as cause:
                        prompt_tokens = completion_tokens = total_tokens = None
                        answer = None
                        parse_error = str(cause)
                    if validate is not None:
                        try:
                            validate(answer or "")
                        except Exception as exc:
                            # 内容校验失败（空正文/非法 JSON 等）按模型问题换下一个，不整轮失败；
                            # 用量顶格且正文非空是预算截断（思考 token 与正文共享 max_tokens），同模型
                            # 放大预算重试一次；用量没顶格才是产出质量问题，顺手记短期冷却。
                            duration_ms = int((time.monotonic() - started) * 1000)
                            finish = _finish_reason(config.api_format, payload)
                            detail = str(exc) + (f"｜finish={finish}" if finish else "")
                            if parse_error:
                                # 响应根本不是 JSON（网关返回 HTML/空体等）与模型输出空正文是两类问题，
                                # 原始解析异常必须进日志，否则换模型救不了也查不出方向
                                detail = f"响应解析失败（{parse_error}）｜{detail}"
                            truncated = completion_tokens is not None and completion_tokens >= request_limit
                            nonempty = bool(answer and answer.strip())
                            raw_tail = (answer or "")[-_RESPONSE_TAIL_CHARS:] or None
                            if nonempty and truncated and not budget_tried:
                                budget_tried = True
                                log_ai_request(
                                    scene=scene, model=model, status="param_retry", duration_ms=duration_ms,
                                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=total_tokens,
                                    error=f"回复在 {request_limit} tokens 预算内被截断，已放大到 {request_limit * 4} 同模型重试｜" + detail,
                                    prompt_excerpt=user, response_excerpt=raw_tail,
                                )
                                attempts.insert(0, _with_token_budget(config.api_format, body, request_limit * 4))
                                continue
                            if not (nonempty and truncated):
                                # 空正文（思考烧光预算没产出）与没顶格的坏输出是模型自己的质量问题，短期不再选它；
                                # 非空截断是预算问题，放大后大多能成，不冷却
                                _ai._MODEL_COOLDOWN[model] = time.time() + _MODEL_COOLDOWN_TTL
                            log_ai_request(
                                scene=scene, model=model, status="fallback", duration_ms=duration_ms,
                                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=total_tokens,
                                error="回复内容校验未通过，换下一个模型｜" + detail, prompt_excerpt=user,
                                response_excerpt=raw_tail,
                            )
                            last_exc = exc
                            last_error_text = str(exc)
                            attempts.clear()
                            continue
                    log_ai_request(
                        scene=scene, model=model, status="ok", duration_ms=duration_ms,
                        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=total_tokens,
                        prompt_excerpt=user, response_excerpt=answer,
                    )
                    return model, response
                except httpx.HTTPStatusError as exc:
                    duration_ms = int((time.monotonic() - started) * 1000)
                    error_text = provider_error_detail(exc.response)
                    if _thinking_restricted(exc.response) and body.get("enable_thinking") is False:
                        log_ai_request(scene=scene, model=model, status="param_retry", duration_ms=duration_ms, error="模型要求开启思考，已自动开启并用同一模型重试｜" + error_text, prompt_excerpt=user)
                        continue
                    if exc.response.status_code == 401:
                        log_ai_request(scene=scene, model=model, status="error", duration_ms=duration_ms, error=error_text, prompt_excerpt=user)
                        raise
                    clamped = _max_tokens_range_error(exc.response)
                    current_budget = body.get("max_tokens") or body.get("max_output_tokens") or (body.get("generationConfig") or {}).get("maxOutputTokens") or request_limit
                    if clamped is not None and current_budget > clamped and not token_clamped:
                        # max_tokens 超出模型上限（含截断放大后撞上限）：解析上限同模型降额重试一次并
                        # 持久化缓存；池里其他路径经 ai_request 直接按上限构造。token_clamped 防同类错死循环
                        learn_model_limit(model, clamped)
                        token_clamped = True
                        log_ai_request(scene=scene, model=model, status="param_retry", duration_ms=duration_ms, error=f"模型 max_tokens 上限 {clamped}，已降额同模型重试｜" + error_text, prompt_excerpt=user)
                        attempts.insert(0, _with_token_budget(config.api_format, body, clamped))
                        continue
                    if exc.response.status_code not in _MODEL_FALLBACK_STATUSES:
                        log_ai_request(scene=scene, model=model, status="error", duration_ms=duration_ms, error=error_text, prompt_excerpt=user)
                        raise
                    # prompt 超出该模型上下文（如 7b 蒸馏模型只有 32k）按模型级故障换下一个，不冷却：
                    # 超长是请求属性不是模型质量问题；其余 400 类照旧换模型
                    detail = ("prompt 超出该模型上下文上限，换下一个模型｜" + error_text) if _prompt_too_long(exc) else error_text
                    log_ai_request(scene=scene, model=model, status="fallback", duration_ms=duration_ms, error=detail, prompt_excerpt=user)
                    last_exc = exc
                    last_error_text = error_text
                    attempts.clear()
                    continue
                except httpx.TransportError as exc:
                    # 传输层失败（超时/连接重置/SSL EOF 等，没拿到 HTTP 响应）：以前直接往上抛、一条日志
                    # 都不留；按渠道故障处理——留痕后换下一个模型（同一 base_url，多半是整台机器在抖）。
                    # 记 transport 而非 fallback：连接抖动不算报错失败，成功率也不把它算进去
                    duration_ms = int((time.monotonic() - started) * 1000)
                    last_error_text = f"{type(exc).__name__}: {exc}"
                    log_ai_request(scene=scene, model=model, status="transport", duration_ms=duration_ms, error="连接失败，未收到响应｜" + last_error_text, prompt_excerpt=user)
                    last_exc = exc
                    attempts.clear()
                    continue
        assert last_exc is not None
        _log_pool_exhausted(scene, model, last_error_text, user)
        raise last_exc
    finally:
        if not passed_client:
            client.close()
