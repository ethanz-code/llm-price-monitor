"""四种接口结构（chat_completions / openai_responses / anthropic / gemini）的端点、消息、工具与内容适配。"""
from __future__ import annotations

import json
import re
from typing import Any

import llm_price_monitor.ai as _ai

from .errors import AIExtractionError

def _usage_tokens(api_format: str, payload: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    """从各家响应里取 token 用量，取不到就返回空。"""
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    if api_format == "anthropic":
        prompt = usage.get("input_tokens")
        completion = usage.get("output_tokens")
    elif api_format == "gemini":
        prompt = usage.get("promptTokenCount")
        completion = usage.get("candidatesTokenCount")
    else:
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    to_int = lambda v: int(v) if isinstance(v, (int, float)) else None
    return to_int(prompt), to_int(completion), to_int(total)

def _finish_reason(api_format: str, payload: dict[str, Any]) -> str | None:
    """非流式响应的停止原因（length=预算截断等），截断判定与日志定位用；取不到返回 None。"""
    if api_format == "chat_completions":
        choices = payload.get("choices")
        reason = choices[0].get("finish_reason") if isinstance(choices, list) and choices else None
    elif api_format == "openai_responses":
        reason = payload.get("status")
    elif api_format == "anthropic":
        reason = payload.get("stop_reason")
    elif api_format == "gemini":
        candidates = payload.get("candidates")
        reason = candidates[0].get("finishReason") if isinstance(candidates, list) and candidates else None
    else:
        reason = None
    return str(reason) if reason else None


def _with_token_budget(api_format: str, body: dict[str, Any], limit: int) -> dict[str, Any]:
    """按接口结构放大单次回复的 token 预算（截断重试用），其余字段原样保留。"""
    if api_format in ("chat_completions", "anthropic"):
        return {**body, "max_tokens": limit}
    if api_format == "openai_responses":
        return {**body, "max_output_tokens": limit}
    if api_format == "gemini":
        return {**body, "generationConfig": {**body.get("generationConfig", {}), "maxOutputTokens": limit}}
    return body

def _body_token_budget(api_format: str, body: dict[str, Any], fallback: int) -> int:
    """请求体里实际发送的输出 token 预算：截断判定要与它比，而不是与 config.max_tokens 比
    （预算可能已被学到的模型上限钳小，钳小后顶格输出依旧算截断）。"""
    value = body.get("max_tokens") or body.get("max_output_tokens") or (body.get("generationConfig") or {}).get("maxOutputTokens")
    return value if isinstance(value, int) and value > 0 else fallback

def ai_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"

def _stream_delta(api_format: str, payload: dict[str, Any]) -> str:
    """从各家流式响应的单个 SSE data 帧里提取增量文本；无增量的帧返回空串。"""
    if api_format == "chat_completions":
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            delta = choices[0].get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                return delta["content"]
        return ""
    if api_format == "openai_responses":
        if payload.get("type") == "response.output_text.delta" and isinstance(payload.get("delta"), str):
            return payload["delta"]
        return ""
    if api_format == "anthropic":
        if payload.get("type") == "content_block_delta" and isinstance(payload.get("delta"), dict):
            text = payload["delta"].get("text")
            return text if isinstance(text, str) else ""
        return ""
    if api_format == "gemini":
        candidates = payload.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            content = candidates[0].get("content")
            if isinstance(content, dict) and isinstance(content.get("parts"), list):
                return "".join(part.get("text", "") for part in content["parts"] if isinstance(part, dict))
        return ""
    raise AIExtractionError(f"未知的 AI 接口结构: {api_format}")

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
    if not text:
        raise AIExtractionError("AI 回复正文为空（思考型模型把预算花在思考上时常见）")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    parsed: Any = text
    for _ in range(2):
        # 兼容两类供应商形态：返回 JSON 数组（缺 models 包装）、把 JSON 双重编码成字符串
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError as exc:
                raise AIExtractionError(f"AI 没有返回合法 JSON: {exc}") from exc
        if isinstance(parsed, dict):
            return parsed
    if isinstance(parsed, list):
        return {"models": parsed}
    raise AIExtractionError("AI 标准化结果必须是 JSON 对象")

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


def _arguments_object(arguments: str) -> dict[str, Any]:
    """模型给出的工具参数 JSON 串 → 对象；坏 JSON 容错成空参，让工具层返回可自纠的错误。"""
    try:
        parsed = json.loads(arguments) if str(arguments or "").strip() else {}
    except json.JSONDecodeError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _tool_specs_for(api_format: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI 风格的工具定义（assistant TOOL_SPECS）转成各接口结构的原生形状。"""
    if api_format == "chat_completions":
        return tools
    specs = [spec["function"] for spec in tools if spec.get("type") == "function" and isinstance(spec.get("function"), dict)]
    if api_format == "openai_responses":
        return [
            {"type": "function", "name": spec.get("name", ""), "description": spec.get("description", ""), "parameters": spec.get("parameters") or {"type": "object", "properties": {}}}
            for spec in specs
        ]
    if api_format == "anthropic":
        return [
            {"name": spec.get("name", ""), "description": spec.get("description", ""), "input_schema": spec.get("parameters") or {"type": "object", "properties": {}}}
            for spec in specs
        ]
    if api_format == "gemini":
        return [
            {
                "functionDeclarations": [
                    {"name": spec.get("name", ""), "description": spec.get("description", ""), "parameters": spec.get("parameters") or {"type": "object", "properties": {}}}
                    for spec in specs
                ]
            }
        ]
    raise AIExtractionError(f"未知的 AI 接口结构: {api_format}")


def _anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """内部多轮消息 → Anthropic 的 (system, messages)；tool_result 属 user 轮，同角色相邻则合并。"""
    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []

    def append(role: str, blocks: list[dict[str, Any]]) -> None:
        blocks = [block for block in blocks if block and not (block.get("type") == "text" and not block.get("text"))]
        if not blocks:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": blocks})

    for message in messages:
        role = message.get("role")
        if role == "system":
            system_parts.append(str(message.get("content") or ""))
        elif role == "user":
            append("user", [{"type": "text", "text": str(message.get("content") or "")}])
        elif role == "assistant":
            blocks = [{"type": "text", "text": str(message["content"])}] if message.get("content") else []
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                blocks.append({
                    "type": "tool_use",
                    "id": str(call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "input": _arguments_object(str(function.get("arguments") or "")),
                })
            append("assistant", blocks)
        elif role == "tool":
            append("user", [{"type": "tool_result", "tool_use_id": str(message.get("tool_call_id") or ""), "content": str(message.get("content") or "")}])
    return "\n\n".join(part for part in system_parts if part), turns


def _responses_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """内部多轮消息 → Responses API 的 (instructions, input)；工具调用与结果用 function_call 项表达。"""
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            instructions.append(str(message.get("content") or ""))
        elif role == "user":
            items.append({"role": "user", "content": str(message.get("content") or "")})
        elif role == "assistant":
            if message.get("content"):
                items.append({"role": "assistant", "content": str(message["content"])})
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                items.append({
                    "type": "function_call",
                    "call_id": str(call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "arguments": str(function.get("arguments") or "{}"),
                })
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": str(message.get("tool_call_id") or ""), "output": str(message.get("content") or "")})
    return "\n\n".join(part for part in instructions if part), items


def _gemini_contents(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """内部多轮消息 → Gemini 的 (systemInstruction, contents)；functionResponse 必须带工具名。"""
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}

    def append(role: str, parts: list[dict[str, Any]]) -> None:
        parts = [part for part in parts if part and not ("text" in part and not part.get("text"))]
        if not parts:
            return
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})

    for message in messages:
        role = message.get("role")
        if role == "system":
            system_parts.append(str(message.get("content") or ""))
        elif role == "user":
            append("user", [{"text": str(message.get("content") or "")}])
        elif role == "assistant":
            parts = [{"text": str(message["content"])}] if message.get("content") else []
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                call_id = str(call.get("id") or "")
                name = str(function.get("name") or "")
                call_names[call_id] = name
                function_call: dict[str, Any] = {"name": name, "args": _arguments_object(str(function.get("arguments") or ""))}
                if call_id:
                    function_call["id"] = call_id
                parts.append({"functionCall": function_call})
            append("model", parts)
        elif role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            function_response: dict[str, Any] = {"name": call_names.get(call_id, ""), "response": {"result": str(message.get("content") or "")}}
            if call_id:
                function_response["id"] = call_id
            append("user", [{"functionResponse": function_response}])
    return "\n\n".join(part for part in system_parts if part), contents

def ai_request(
    config: AIConfig,
    model: str,
    system: str,
    user: str,
    *,
    max_tokens: int | None = None,
    json_mode: bool = True,
    messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """按配置的接口结构（config.api_format）构造请求 URL、headers 与 body；只构造不发送。

    messages 传入完整对话（工具循环的多轮消息）时替代 system/user 组装；tools 为 OpenAI
    function calling 风格的工具定义，四种接口结构都会转成各自的工具协议。
    """
    limit = config.max_tokens if max_tokens is None else max_tokens
    learned_limit = _ai._MODEL_MAX_TOKENS_LIMIT.get(model)
    if learned_limit is not None and limit > learned_limit:
        # 该模型的上限已从报错里学到（见 _MODEL_MAX_TOKENS_LIMIT）：直接按上限构造，省一次 400
        limit = learned_limit
    if config.api_format == "openai_responses":
        if messages is not None:
            instructions, items = _responses_messages(messages)
            body: dict[str, Any] = {"model": model, "input": items, "max_output_tokens": limit, "temperature": 0}
            if instructions:
                body["instructions"] = instructions
        else:
            input_messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
            body = {"model": model, "input": input_messages, "max_output_tokens": limit, "temperature": 0}
        if json_mode and not tools:
            body["text"] = {"format": {"type": "json_object"}}
        if tools:
            body["tools"] = _tool_specs_for("openai_responses", tools)
            body["tool_choice"] = "auto"
        headers = {"content-type": "application/json"}
        if config.api_key:
            headers["authorization"] = f"Bearer {config.api_key}"
        return _responses_endpoint(config.base_url), headers, body
    if config.api_format == "anthropic":
        if messages is not None:
            system_text, turns = _anthropic_messages(messages)
        else:
            system_text, turns = system, [{"role": "user", "content": user}]
        body: dict[str, Any] = {"model": model, "max_tokens": limit, "temperature": 0, "messages": turns}
        if system_text:
            body["system"] = system_text
        if tools:
            body["tools"] = _tool_specs_for("anthropic", tools)
            body["tool_choice"] = {"type": "auto"}
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
        if json_mode and not tools:
            generation_config["responseMimeType"] = "application/json"
        if messages is not None:
            system_instruction, contents = _gemini_contents(messages)
        else:
            system_instruction, contents = system, [{"role": "user", "parts": [{"text": user}]}]
        gemini_body: dict[str, Any] = {"contents": contents, "generationConfig": generation_config}
        if system_instruction:
            gemini_body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if tools:
            gemini_body["tools"] = _tool_specs_for("gemini", tools)
            gemini_body["toolConfig"] = {"functionCallingConfig": {"mode": "auto"}}
        return url, {"content-type": "application/json", "x-goog-api-key": config.api_key or ""}, gemini_body
    if config.api_format != "chat_completions":
        raise AIExtractionError(f"未知的 AI 接口结构: {config.api_format}")
    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"
    if messages is None:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": limit,
        "messages": messages,
        # DashScope 的 qwen3 系列默认开思考，思考会耗尽 max_tokens 导致正文为空，必须显式声明
        "enable_thinking": config.enable_thinking,
    }
    if body["enable_thinking"] is False and model in _ai._MODEL_THINKING_REQUIRED:
        # 该模型拒收 enable_thinking=false（从 400 报错里学过）：直接按 true 构造，省一次白发请求
        body["enable_thinking"] = True
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
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
