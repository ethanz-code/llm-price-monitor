"""流式对话：SSE 增量解析、多轮工具消息事件流与换模型/翻参回退。"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from typing import Any

import httpx

from .api_format import _stream_delta, ai_request
from .client import ai_http_client
from .errors import AIExtractionError
from .pool import (
    _fallback_pool,
    _log_pool_exhausted,
    _stream_status_failure,
    _stream_thinking_attempts,
    _stream_transport_failure,
)
from .state import log_ai_request

def ai_stream_fallback(config: AIConfig, system: str, user: str, *, max_tokens: int | None = None, scene: str = "AI 请求", timeout: float | None = None) -> Iterator[str]:
    """流式对话的换模型版本：首个分片产出前模型报错则换下一个，已开始输出后出错原样抛出。

    思考不可关的模型拒收 enable_thinking=false 时，先翻成 true 同模型重试一次，再考虑换模型。
    传输错误（超时/SSL 等）同样处理：还没出字就换下一个并留痕，已出字再断记一条失败后抛出。
    timeout 覆盖单次请求超时，不传用 config.timeout；四种接口结构都支持。
    """
    order = _fallback_pool(config)
    last_exc: httpx.HTTPError | None = None
    last_error_text = ""
    for model in order:
        for enable_thinking in _stream_thinking_attempts(config):
            started = time.monotonic()
            received: list[str] = []
            try:
                for chunk in ai_stream(config, model, system, user, max_tokens=max_tokens, enable_thinking=enable_thinking, timeout=timeout):
                    received.append(chunk)
                    yield chunk
                log_ai_request(
                    scene=scene, model=model, status="ok", duration_ms=int((time.monotonic() - started) * 1000),
                    prompt_excerpt=user, response_excerpt="".join(received),
                )
                return
            except httpx.HTTPStatusError as exc:
                action, error_text = _stream_status_failure(
                    exc, model=model, scene=scene, duration_ms=int((time.monotonic() - started) * 1000),
                    thinking_flip_possible=config.enable_thinking is False, enable_thinking=enable_thinking,
                    prompt_excerpt=user,
                )
                if action == "retry":
                    continue
                last_exc = exc
                last_error_text = error_text
                break
            except httpx.TransportError as exc:
                last_error_text = _stream_transport_failure(
                    exc, model=model, scene=scene, duration_ms=int((time.monotonic() - started) * 1000),
                    mid_stream=bool(received), prompt_excerpt=user,
                )
                last_exc = exc
                break
    assert last_exc is not None
    # 把状态码和供应商报错要点一起报出去，这才是用户该看到的真实原因
    _log_pool_exhausted(scene, model, last_error_text, user)
    raise AIExtractionError(f"模型池全部失败，最后一次错误 {last_error_text}") from last_exc

class _StreamErrorPayload(Exception):
    """HTTP 200 但流里带 error 数据帧：部分供应商（如 MiniMax）参数报错不走状态码。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _stream_chat_events(
    config: AIConfig,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    enable_thinking: bool | None,
    timeout: float | None = None,
) -> Iterator[dict[str, Any]]:
    """单模型流式对话：四种接口结构都产出 {"type":"delta","text"} 增量与最后的 finish 事件。

    finish 事件带 finish_reason、按分片拼装完整的 tool_calls 与 usage；HTTP/网络错误原样抛出。
    timeout 覆盖单次请求超时；流式没有总时长限制（read 超时只管字节间隔），这里按墙钟死线兜底，
    连续吐字的思考型模型拖过死线同样掐断换人。
    """
    url, headers, body = ai_request(config, model, "", "", messages=messages, tools=tools, json_mode=False)
    if enable_thinking is not None and "enable_thinking" in body:
        body["enable_thinking"] = enable_thinking
    api_format = config.api_format
    if api_format in {"chat_completions", "openai_responses", "anthropic"}:
        body["stream"] = True
    elif api_format == "gemini":
        url = url.replace(":generateContent", ":streamGenerateContent?alt=sse")
    request_timeout = timeout or config.timeout
    tool_acc: dict[Any, dict[str, Any]] = {}
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    started = time.monotonic()
    with ai_http_client(config, request_timeout) as client:
        with client.stream("POST", url, headers=headers, json=body) as response:
            if response.is_error:
                # 供应商报错时先读出响应体：流式响应默认不读正文，后续取 .text 会直接抛 ResponseNotRead
                response.read()
            response.raise_for_status()
            for line in response.iter_lines():
                if time.monotonic() - started > request_timeout:
                    raise httpx.ReadTimeout(f"模型流式输出超过 {int(request_timeout)} 秒仍未完成", request=response.request)
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if "error" in payload:
                    # 200 状态码下的报错帧（含 anthropic 流内 error 事件）：抛给上层走翻参/换模型分支
                    err = payload["error"]
                    message = err.get("message") if isinstance(err, dict) else str(err)
                    raise _StreamErrorPayload(str(message or "上游返回错误帧"))
                if api_format == "chat_completions":
                    if isinstance(payload.get("usage"), dict):
                        usage = payload["usage"]
                    choices = payload.get("choices")
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        yield {"type": "delta", "text": text}
                    for tc in delta.get("tool_calls") or []:
                        if not isinstance(tc, dict):
                            continue
                        index = tc.get("index") if isinstance(tc.get("index"), int) else len(tool_acc)
                        slot = tool_acc.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = str(tc["id"])
                        function = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                        if function.get("name"):
                            slot["name"] = str(function["name"])
                        if isinstance(function.get("arguments"), str):
                            slot["arguments"] += function["arguments"]
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
                elif api_format == "anthropic":
                    kind = payload.get("type")
                    if kind == "message_start":
                        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
                        message_usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                        if message_usage.get("input_tokens") is not None:
                            usage = {"prompt_tokens": message_usage.get("input_tokens")}
                    elif kind == "content_block_start":
                        block = payload.get("content_block") if isinstance(payload.get("content_block"), dict) else {}
                        if block.get("type") == "tool_use":
                            slot = tool_acc.setdefault(payload.get("index") if isinstance(payload.get("index"), int) else len(tool_acc), {"id": "", "name": "", "arguments": ""})
                            if block.get("id"):
                                slot["id"] = str(block["id"])
                            if block.get("name"):
                                slot["name"] = str(block["name"])
                    elif kind == "content_block_delta":
                        delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
                        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str) and delta["text"]:
                            yield {"type": "delta", "text": delta["text"]}
                        elif delta.get("type") == "input_json_delta" and isinstance(delta.get("partial_json"), str):
                            slot = tool_acc.setdefault(payload.get("index") if isinstance(payload.get("index"), int) else len(tool_acc), {"id": "", "name": "", "arguments": ""})
                            slot["arguments"] += delta["partial_json"]
                    elif kind == "message_delta":
                        delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
                        if delta.get("stop_reason"):
                            finish_reason = "tool_calls" if delta["stop_reason"] == "tool_use" else str(delta["stop_reason"])
                        delta_usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                        if delta_usage.get("output_tokens") is not None:
                            usage = usage or {}
                            usage["completion_tokens"] = delta_usage.get("output_tokens")
                elif api_format == "openai_responses":
                    kind = payload.get("type")
                    if kind == "response.output_text.delta" and isinstance(payload.get("delta"), str) and payload["delta"]:
                        yield {"type": "delta", "text": payload["delta"]}
                    elif kind == "response.output_item.added":
                        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
                        if item.get("type") == "function_call":
                            item_id = str(item.get("id") or f"fc_{len(tool_acc)}")
                            slot = tool_acc.setdefault(item_id, {"id": "", "name": "", "arguments": ""})
                            slot["id"] = str(item.get("call_id") or item_id)
                            if item.get("name"):
                                slot["name"] = str(item["name"])
                            if isinstance(item.get("arguments"), str):
                                slot["arguments"] += item["arguments"]
                    elif kind == "response.function_call_arguments.delta":
                        item_id = str(payload.get("item_id") or "")
                        slot = tool_acc.setdefault(item_id, {"id": str(payload.get("call_id") or ""), "name": "", "arguments": ""})
                        if not slot["id"]:
                            slot["id"] = str(payload.get("call_id") or "")
                        if isinstance(payload.get("delta"), str):
                            slot["arguments"] += payload["delta"]
                    elif kind == "response.completed":
                        completed = payload.get("response") if isinstance(payload.get("response"), dict) else {}
                        completed_usage = completed.get("usage") if isinstance(completed.get("usage"), dict) else {}
                        if completed_usage:
                            usage = {"prompt_tokens": completed_usage.get("input_tokens"), "completion_tokens": completed_usage.get("output_tokens"), "total_tokens": completed_usage.get("total_tokens")}
                        finish_reason = "tool_calls" if tool_acc else "stop"
                elif api_format == "gemini":
                    candidates = payload.get("candidates")
                    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
                        content = candidates[0].get("content")
                        parts = content.get("parts") if isinstance(content, dict) and isinstance(content.get("parts"), list) else []
                        for part in parts:
                            if not isinstance(part, dict):
                                continue
                            if isinstance(part.get("text"), str) and part["text"]:
                                yield {"type": "delta", "text": part["text"]}
                            call = part.get("functionCall") if isinstance(part.get("functionCall"), dict) else None
                            if call:
                                index = len(tool_acc)
                                tool_acc[index] = {
                                    "id": str(call.get("id") or f"tool_{index}"),
                                    "name": str(call.get("name") or ""),
                                    "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                                }
                    if isinstance(payload.get("usageMetadata"), dict):
                        meta = payload["usageMetadata"]
                        usage = {"prompt_tokens": meta.get("promptTokenCount"), "completion_tokens": meta.get("candidatesTokenCount"), "total_tokens": meta.get("totalTokenCount")}
                        if finish_reason is None and tool_acc:
                            finish_reason = "tool_calls"
    tool_calls = [
        {
            "id": tool_acc[index]["id"] or f"call_{index}",
            "type": "function",
            "name": tool_acc[index]["name"],
            "arguments": tool_acc[index]["arguments"] or "{}",
        }
        for index in tool_acc
    ]
    yield {"type": "finish", "finish_reason": finish_reason, "tool_calls": tool_calls, "usage": usage}

# 模型没走 tool_calls 协议、把调用过程当正文"演"出来的特征（如“调用工具：get_prices(...)”）：
# 这类输出对用户是假动作噪声，按失败处理换下一个模型
_PSEUDO_TOOL_CALL_RE = re.compile(r"(调用工具|函数调用|tool_call)\s*[:：]?\s*[`\"']?\w+\s*\(", re.IGNORECASE)


def ai_stream_messages_fallback(
    config: AIConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    *,
    scene: str = "AI 请求",
    on_model_failure: Any | None = None,
    timeout: float | None = None,
) -> Iterator[dict[str, Any]]:
    """带工具配置的多轮消息流式对话：产出 delta 事件，最后产出 {"type":"finish", …}。

    四种接口结构都支持（工具定义按结构自动转换）；换模型/翻参重试/传输错误的语义与
    ai_stream_fallback 一致：首个增量产出前失败换下一个模型，已产出后再失败记失败并抛出。
    每次尝试写一条 AI 请求日志。on_model_failure(model, error) 在每个模型被放弃
    （fallback/transport/伪工具调用/空响应/流中断）时回调，供调用方记冷却名单；
    timeout 覆盖单次请求超时（如助手场景的收紧死线）。
    """
    order = _fallback_pool(config)

    def notify_failure(model: str, error_text: str) -> None:
        if on_model_failure is not None:
            on_model_failure(model, error_text)

    prompt_excerpt = str(messages[-1].get("content") or "")[:300] if messages else ""
    last_exc: Exception | None = None
    last_error_text = ""
    for model in order:
        for enable_thinking in _stream_thinking_attempts(config):
            started = time.monotonic()
            received = False
            text_parts: list[str] = []
            # 带工具的请求先缓冲正文再定性：工具轮的解说/伪调用不能漏给用户，漏出去就没法干净换模型
            deferred: list[dict[str, Any]] = []
            finish: dict[str, Any] = {"type": "finish", "finish_reason": None, "tool_calls": [], "usage": None}
            try:
                for event in _stream_chat_events(config, model, messages, tools, enable_thinking, timeout):
                    if event["type"] == "delta":
                        received = True
                        text_parts.append(event["text"])
                        if tools is None:
                            yield event
                        else:
                            deferred.append(event)
                    else:
                        finish = event
                usage = finish.get("usage") if isinstance(finish.get("usage"), dict) else {}
                tool_names = "、".join(tc["name"] for tc in finish["tool_calls"])
                answer_text = "".join(text_parts)
                if not answer_text.strip() and not tool_names:
                    # 既无正文也无工具调用：这个模型的空响应不可用，换下一个模型，不让整题失败
                    log_ai_request(scene=scene, model=model, status="fallback", duration_ms=int((time.monotonic() - started) * 1000), error="模型返回了空内容", prompt_excerpt=prompt_excerpt)
                    notify_failure(model, "模型返回了空内容")
                    last_exc = AIExtractionError("模型返回了空内容")
                    last_error_text = "模型返回了空内容"
                    break
                if tools is not None and not tool_names and _PSEUDO_TOOL_CALL_RE.search(answer_text):
                    # 有工具可用却不走 tool_calls 协议，把调用过程当正文演出来：对用户是假动作，换下一个模型
                    log_ai_request(scene=scene, model=model, status="fallback", duration_ms=int((time.monotonic() - started) * 1000), error="模型把工具调用当正文输出，未走 tool_calls 协议", prompt_excerpt=prompt_excerpt)
                    notify_failure(model, "模型把工具调用当正文输出，未走 tool_calls 协议")
                    last_exc = AIExtractionError("模型把工具调用当正文输出")
                    last_error_text = "模型把工具调用当正文输出"
                    break
                if not tool_names:
                    # 只有真正给用户的正文才放行：带工具轮的解说正文只留在会话上下文里
                    for event in deferred:
                        yield event
                log_ai_request(
                    scene=scene, model=model, status="ok", duration_ms=int((time.monotonic() - started) * 1000),
                    prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    prompt_excerpt=prompt_excerpt,
                    response_excerpt=answer_text or ("调用工具 " + tool_names),
                )
                yield finish
                return
            except httpx.HTTPStatusError as exc:
                action, error_text = _stream_status_failure(
                    exc, model=model, scene=scene, duration_ms=int((time.monotonic() - started) * 1000),
                    thinking_flip_possible=config.enable_thinking is False, enable_thinking=enable_thinking,
                    prompt_excerpt=prompt_excerpt, on_model_failure=notify_failure,
                )
                if action == "retry":
                    continue
                last_exc = exc
                last_error_text = error_text
                break
            except _StreamErrorPayload as exc:
                # 200 + 错误帧：按错误文案走与状态码相同的分支（思考受限翻参，其余换模型）
                duration_ms = int((time.monotonic() - started) * 1000)
                error_text = exc.message
                if received and tools is None:
                    # 无工具的流已经把正文放给用户了，半截回答没法换模型重来
                    log_ai_request(scene=scene, model=model, status="error", duration_ms=duration_ms, error="流式输出中断｜" + error_text, prompt_excerpt=prompt_excerpt)
                    notify_failure(model, "流式输出中断｜" + error_text)
                    raise AIExtractionError(f"流式输出中断：{error_text}") from exc
                if "enable_thinking" in error_text and config.enable_thinking is False and enable_thinking is None:
                    log_ai_request(scene=scene, model=model, status="param_retry", duration_ms=duration_ms, error="模型要求开启思考，已自动开启并用同一模型重试｜" + error_text, prompt_excerpt=prompt_excerpt)
                    continue
                log_ai_request(scene=scene, model=model, status="fallback", duration_ms=duration_ms, error=error_text, prompt_excerpt=prompt_excerpt)
                notify_failure(model, error_text)
                last_exc = exc
                last_error_text = error_text
                break
            except httpx.TransportError as exc:
                # 无工具的流正文已放给用户，半截回答没法换模型重来；带工具的还攥在手里，按连接抖动换下一个模型
                last_error_text = _stream_transport_failure(
                    exc, model=model, scene=scene, duration_ms=int((time.monotonic() - started) * 1000),
                    mid_stream=bool(received and tools is None), prompt_excerpt=prompt_excerpt,
                    on_model_failure=notify_failure,
                )
                last_exc = exc
                break
    assert last_exc is not None
    _log_pool_exhausted(scene, model if order else "", last_error_text, prompt_excerpt)
    raise AIExtractionError(f"模型池全部失败，最后一次错误 {last_error_text}") from last_exc

def ai_stream(config: AIConfig, model: str, system: str, user: str, *, max_tokens: int | None = None, enable_thinking: bool | None = None, timeout: float | None = None) -> Iterator[str]:
    """流式对话：逐段产出模型输出文本；四种接口结构都走各自的 stream 模式，HTTP 错误原样抛出。

    enable_thinking 供换模型重试链路覆盖配置值（翻参重试）；None 表示按配置，且只对带该参数的接口结构生效。
    timeout 覆盖单次请求超时，不传用 config.timeout。
    """
    url, headers, body = ai_request(config, model, system, user, max_tokens=max_tokens, json_mode=False)
    if enable_thinking is not None and "enable_thinking" in body:
        body["enable_thinking"] = enable_thinking
    api_format = config.api_format
    if api_format in {"chat_completions", "openai_responses", "anthropic"}:
        body["stream"] = True
    elif api_format == "gemini":
        url = url.replace(":generateContent", ":streamGenerateContent?alt=sse")
    with ai_http_client(config, timeout) as client:
        with client.stream("POST", url, headers=headers, json=body) as response:
            if response.is_error:
                # 供应商报错时先读出响应体：流式响应默认不读正文，后续取 .text 会直接抛 ResponseNotRead
                response.read()
            response.raise_for_status()
            for line in response.iter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                chunk = _stream_delta(api_format, payload)
                if chunk:
                    yield chunk
