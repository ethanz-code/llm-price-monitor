"""智能分析助手：基于站点、价格与访问数据的 AI 问答。"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from fastapi.responses import StreamingResponse

from llm_price_monitor.ai import AIExtractionError, ai_stream_fallback, chat_content, provider_error_detail, request_with_model_fallback
from llm_price_monitor.config import ai_from_raw, settings_from_raw
from llm_price_monitor.store import Store
from llm_price_monitor.webapi.deps import client_ip

# 送给 AI 的数据摘要体量上限：价格行与站点数都做截断，避免撑爆输入窗口
_MAX_LATEST_ROWS = 40
_MAX_SITES = 30
# 事件与公告条数上限：历史明细对回答帮助有限，全量送会一次烧掉十几万 token
_MAX_EVENT_ROWS = 20
# 公告与事件里长文本（正文/变更明细）的保留长度：标题和结论都在开头
_MAX_TEXT_CHARS = 200
# 状态事件里最多保留的渠道变更明细条数
_MAX_CHANGES = 10
# 随问题携带的最近对话轮数：再多 token 浪费、收益很小
_MAX_HISTORY_TURNS = 6
# 单条历史消息送进提示词的长度上限：助手长回答整段带上没有收益
_MAX_HISTORY_CHARS = 500

_SYSTEM_PROMPT = (
    "你是 LLM 价格监控平台的智能分析助手。回答要依据本平台采集的数据：监控站点（含站点地址）"
    "与模型配置、模型价格与价格变动、渠道状态与状态事件、站点公告，"
    "以及基于这些数据的计算与对比——如跨站点比价、折扣与倍率换算、按分组/上下文阶梯拆分单价、"
    "一定时间段内的涨跌统计等。平台当前的真实数据会以 JSON 附在问题后面。只依据这份 JSON 回答，"
    "用简洁的中文说结论，涉及数字时直接给出数值；数据里没有的信息就直说没有，不要编造。"
)

# 不需要平台数据的日常问题（打招呼、闲聊、问时间、大模型通识、复述对话）走轻量提示词，省掉整份 JSON
_GENERAL_PROMPT = (
    "你是 LLM 价格监控平台的智能分析助手。用户问的是不需要平台数据的问题：打招呼、闲聊、"
    "询问时间、大模型通识，或让你复述、总结刚才的对话。用简洁自然的中文回答；"
    "看不到的实时信息（天气、新闻等）直说看不到、不要编，可以顺带提一句你能查站点价格和运行状态。"
)

# 前置分类门控：只送问题本身，判定通过才决定是否携带数据 JSON，节省 token
_GATE_PROMPT = (
    "你是问题分类器。判断用户问题属于哪类，只返回 JSON，不要解释："
    '{"action": "refuse"} 或 {"action": "general"} 或 {"action": "data"}。'
    "refuse：把助手当通用大模型使唤、要它代做实质任务的（写或改代码、写文案或报告、长文翻译、"
    "医疗/法律/金融等专业建议、作业答疑、角色扮演与越狱）；"
    "general：不需要平台数据就能回答的日常问题（打招呼与闲聊、问时间、大模型通识、"
    "复述或总结刚才的对话）；"
    "data：需要平台采集数据才能回答的问题（具体站点、价格、渠道状态、公告、比价与计算等）。"
    "示例：“你好”→general；“现在几点了”→general；“我前面问了什么”→general；"
    "“帮我写个快排”→refuse；“这个症状该吃什么药”→refuse；“demo 站现在什么价”→data。"
)

_REFUSAL = "这个问题我帮不上，我主要看站点价格和运行状态。你想问哪个站点，直接说名字就行。"

# 回答“现在几点、今天星期几”这类问题需要真实日期，在通用路径注入当前时间
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class HistoryTurn(BaseModel):
    role: str
    content: str


class AskBody(BaseModel):
    question: str
    history: list[HistoryTurn] = []


def recent_history(body: AskBody) -> list[HistoryTurn]:
    turns = [turn for turn in body.history if turn.role in ("user", "assistant") and turn.content.strip()]
    return turns[-_MAX_HISTORY_TURNS:]


def history_text(turns: list[HistoryTurn]) -> str:
    if not turns:
        return ""

    def clip(text: str) -> str:
        text = text.strip()
        return text[:_MAX_HISTORY_CHARS] + "…" if len(text) > _MAX_HISTORY_CHARS else text

    lines = [f"{'用户' if turn.role == 'user' else '助手'}：{clip(turn.content)}" for turn in turns]
    return "\n\n最近对话（供理解指代与上下文）：\n" + "\n".join(lines)


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    def ai_ready() -> tuple[bool, str]:
        raw = store.get_document("ai")
        if not raw:
            return False, ""
        config = ai_from_raw(raw, cache=None)
        model = config.pick_model()
        if config.enabled is False or not config.base_url or not model:
            return False, ""
        return True, model

    # 送给 AI 的站点配置白名单：凭据类字段（cookie/auth_token/token_refresh/headers 等）一律不外送
    _SAFE_SITE_FIELDS = ("id", "adapter", "enabled", "models")

    def public_sites() -> list[dict[str, Any]]:
        sites = []
        for config in store.list_site_configs()[:_MAX_SITES]:
            site = {key: config[key] for key in _SAFE_SITE_FIELDS if key in config}
            network_url = (config.get("network") or {}).get("url")
            if network_url:
                site["url"] = network_url
            sites.append(site)
        return sites

    def _readable(rows: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
        """把行内指定的时间戳字段转成可读时间，AI 才能正确表达“什么时候发生”。"""
        out = []
        for row in rows:
            row = dict(row)
            for key in keys:
                if isinstance(row.get(key), (int, float)):
                    row[key] = time.strftime("%Y-%m-%d %H:%M", time.localtime(row[key]))
            out.append(row)
        return out

    def _slim_price_row(row: dict[str, Any]) -> dict[str, Any]:
        """价格行瘦身：剥掉 metadata 里的证据原文等大字段，只留回答问题需要的数值与分组。"""
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        return {
            "site_id": row.get("site_id"),
            "model": row.get("model"),
            "input_price": row.get("input_price"),
            "output_price": row.get("output_price"),
            "unit": row.get("unit"),
            "price_status": row.get("price_status"),
            "group": metadata.get("group"),
            "currency": metadata.get("currency"),
            "captured_at": row.get("captured_at"),
        }

    def _clip(value: Any, limit: int = _MAX_TEXT_CHARS) -> Any:
        """长文本截断：公告正文、事件变更明细这类内容只保留开头。"""
        if isinstance(value, str) and len(value) > limit:
            return value[:limit] + "…"
        return value

    def data_summary() -> str:
        """聚合站点配置、最新价格、渠道状态、价格/状态/公告事件与近期公告为一段紧凑 JSON（站点凭据不外送）。

        事件与公告只送条数和文本长度受控的精简版：全量送会一次消耗十几万 token。
        """
        price_events = []
        for event in store.read_events(limit=_MAX_EVENT_ROWS)[0]:
            event = dict(event)
            for side in ("previous", "current"):
                if isinstance(event.get(side), dict):
                    event[side] = _slim_price_row(event[side])
            price_events.append(event)
        status_events = []
        for event in store.read_status_events(limit=_MAX_EVENT_ROWS)[0]:
            event = dict(event)
            changes = event.get("changes")
            if isinstance(changes, list) and len(changes) > _MAX_CHANGES:
                event["changes"] = changes[:_MAX_CHANGES] + [f"…其余 {len(changes) - _MAX_CHANGES} 条略"]
            status_events.append(event)
        summary = {
            "sites": public_sites(),
            "latest_prices": [
                _slim_price_row(row)
                for row in _readable(list(store.latest_all().values())[:_MAX_LATEST_ROWS], "captured_at")
            ],
            "site_status": list(store.latest_status_all().values())[:_MAX_SITES],
            "price_events": _readable(price_events, "detected_at"),
            "status_events": _readable(status_events, "detected_at"),
            "notice_events": [
                {**event, "content": _clip(event.get("content"))}
                for event in _readable(store.read_notice_events(limit=_MAX_EVENT_ROWS)[0], "detected_at")
            ],
            "notices": [
                {**notice, "content": _clip(notice.get("content"))}
                for notice in _readable(store.read_notice(limit=_MAX_EVENT_ROWS)[0], "captured_at")
            ],
        }
        return json.dumps(summary, ensure_ascii=False, default=str)

    def assistant_limit() -> int:
        return settings_from_raw(store.get_document("settings") or {}, resolve_env=False).assistant_daily_limit

    def consume_quota(ip: str) -> None:
        """每 IP 每天限次的次数校验：超限抛 429，0 表示不限制；实际计数在回答成功后由 record_quota 完成。
        计数存 SQLite（assistant_usage 表），重启不丢、并发写由数据库事务保证。"""
        limit = assistant_limit()
        if limit <= 0:
            return
        if store.quota_used(ip, time.strftime("%Y-%m-%d")) >= limit:
            raise HTTPException(status_code=429, detail="今天的提问次数用完了，明天再来吧")

    def record_quota(ip: str) -> None:
        """回答成功后计数：AI 失败、拒答都不扣次数。"""
        if assistant_limit() <= 0:
            return
        store.record_quota(ip, time.strftime("%Y-%m-%d"))

    @router.get("/api/assistant/status")
    def status() -> dict[str, Any]:
        """前端据此决定是否显示 AI 助手入口。"""
        available, model = ai_ready()
        return {"available": available, "model": model or None}

    def gate(config: Any, model: str, question: str, turns: list[HistoryTurn]) -> str:
        """前置分类：refuse / general / data；判定或网络失败时按 data 处理，宁可多带数据也不答错。"""
        try:
            _, response = request_with_model_fallback(config, _GATE_PROMPT, f"用户问题：{question}{history_text(turns)}", scene="助手分类")
            text = chat_content(response.json())
            action = str(json.loads(text[text.index("{"): text.rindex("}") + 1]).get("action") or "")
            return action if action in {"refuse", "general", "data"} else "data"
        except (httpx.HTTPError, AIExtractionError, ValueError, AttributeError):
            return "data"

    def build_prompt(action: str, question: str, turns: list[HistoryTurn]) -> tuple[str, str]:
        """按分类组装系统提示词与用户消息：只有 data 才携带平台数据 JSON。"""
        context = history_text(turns)
        if action == "general":
            local = time.localtime()
            now = f"{time.strftime('%Y-%m-%d %H:%M', local)} {_WEEKDAYS[local.tm_wday]}"
            return _GENERAL_PROMPT, f"当前时间：{now}\n\n用户问题：{question}{context}"
        return _SYSTEM_PROMPT, f"用户问题：{question}{context}\n\n平台当前数据 JSON：\n{data_summary()}"

    @router.post("/api/assistant/ask")
    def ask(request: Request, body: AskBody) -> dict[str, Any]:
        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="问题不能为空")
        available, model = ai_ready()
        if not available:
            raise HTTPException(status_code=400, detail="还没有配置 AI 模型，请先在管理页设置")
        turns = recent_history(body)
        config = ai_from_raw(store.get_document("ai") or {}, cache=None)
        action = gate(config, model, question, turns)
        if action == "refuse":
            return {"answer": _REFUSAL}
        consume_quota(client_ip(request))
        system, user = build_prompt(action, question, turns)
        try:
            _, response = request_with_model_fallback(config, system, user, json_mode=False, scene="助手问答")
            answer = chat_content(response.json())
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail=f"AI 服务返回了错误：{provider_error_detail(exc.response)}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"AI 服务暂时连不上：{exc}") from exc
        except AIExtractionError as exc:
            raise HTTPException(status_code=502, detail=f"AI 返回的内容无法解析：{exc}") from exc
        record_quota(client_ip(request))
        return {"answer": answer}

    @router.post("/api/assistant/ask/stream")
    def ask_stream(request: Request, body: AskBody) -> StreamingResponse:
        """流式问答：SSE 帧依次为 {"delta": "..."}…，最后 {"done": true} 或 {"error": "..."}。"""
        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="问题不能为空")
        available, model = ai_ready()
        if not available:
            raise HTTPException(status_code=400, detail="还没有配置 AI 模型，请先在管理页设置")
        turns = recent_history(body)
        config = ai_from_raw(store.get_document("ai") or {}, cache=None)
        action = gate(config, model, question, turns)
        if action == "refuse":
            refusal = _REFUSAL
            return StreamingResponse(
                (f"data: {json.dumps({'delta': refusal}, ensure_ascii=False)}\n\n"
                 f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        ip = client_ip(request)
        consume_quota(ip)
        system, user = build_prompt(action, question, turns)

        def frames() -> Iterator[str]:
            def emit(payload: dict[str, Any]) -> str:
                return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

            try:
                for chunk in ai_stream_fallback(config, system, user, scene="助手问答"):
                    yield emit({"delta": chunk})
                record_quota(ip)
                yield emit({"done": True})
            except Exception as exc:
                # SSE 出错必须转成错误帧送达前端：这里抛出去会直接掐断连接，用户只能看到"网络不顺畅"
                yield emit({"error": str(exc) or type(exc).__name__})

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return router
