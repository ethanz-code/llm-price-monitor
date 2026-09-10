"""WxPusher 推送：只覆盖「appToken + UID 发文本消息」这一个最小场景。

API 文档 https://wxpusher.zjiecode.com/docs/：POST /api/send/message，
请求体 appToken/content/summary(≤100 字)/contentType(1=文本)/uids，业务码 1000 表示成功，限流约 2 QPS。
"""
from __future__ import annotations

import httpx

SEND_URL = "https://wxpusher.zjiecode.com/api/send/message"


def send_wxpusher(*, app_token: str, content: str, summary: str, uid: str | None, timeout: float = 10.0) -> None:
    payload: dict[str, object] = {
        "appToken": app_token,
        "content": content,
        "summary": summary[:100],
        "contentType": 1,
    }
    if uid:
        payload["uids"] = [uid]
    response = httpx.post(SEND_URL, json=payload, timeout=timeout)
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 1000:
        raise RuntimeError(f"WxPusher 返回 code={result.get('code')}: {result.get('msg')}")


_PRICE_KIND_LABELS = {"new": "新增", "changed": "价格变化", "recovered": "价格恢复", "group_removed": "分组下线"}
_MAX_DIGEST_LINES = 30


def _fmt_record(record: object) -> str:
    if not isinstance(record, dict):
        return "无数据"
    unit = str(record.get("unit") or "").strip()
    suffix = f"（{unit}）" if unit else ""
    return f"输入 {record.get('input_price')} / 输出 {record.get('output_price')}{suffix}"


def _price_line(event: dict) -> str:
    label = _PRICE_KIND_LABELS.get(str(event.get("kind")), str(event.get("kind")))
    previous, current = event.get("previous"), event.get("current")
    group = ((previous or current or {}).get("metadata") or {}).get("group") or "default"
    head = f"{event.get('site_id')} · {event.get('model')}（{group}）{label}"
    if current is not None and str(event.get("kind")) != "group_removed":
        tail = _fmt_record(current)
        if previous is not None:
            tail += f"，原 {_fmt_record(previous)}"
        return f"{head}：{tail}"
    return f"{head}，最后 {_fmt_record(previous)}"


def send_change_digest(
    *,
    app_token: str,
    uid: str | None,
    price_events: list[dict],
    status_events: list[dict],
    notice_events: list[dict],
    timeout: float = 10.0,
) -> None:
    """把一轮采集的价格/渠道状态/公告变化汇总成一条消息推送；无变化不发送。"""
    lines: list[str] = [_price_line(event) for event in price_events]
    lines += [
        f"{event.get('site_id')}：渠道状态{'首次记录' if event.get('kind') == 'status_init' else '有变化'}（{len(event.get('changes') or [])} 处）"
        for event in status_events
    ]
    lines += [
        f"{event.get('site_id')}：公告{'发布' if event.get('kind') == 'notice_init' else '更新'}：{str(event.get('content') or '')[:80]}"
        for event in notice_events
    ]
    if not lines:
        return
    omitted = len(lines) - _MAX_DIGEST_LINES
    if omitted > 0:
        lines = lines[:_MAX_DIGEST_LINES] + [f"……其余 {omitted} 条变化略，请到监控面板查看"]
    content = "监测到站点变化：\n" + "\n".join(lines)
    send_wxpusher(app_token=app_token, content=content, summary=f"监测到 {len(lines)} 条站点变化", uid=uid, timeout=timeout)
