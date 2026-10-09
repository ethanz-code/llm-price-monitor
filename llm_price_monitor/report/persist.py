"""采集结果落库：历史/事件/快照的复查-写入原子写入与并发轮次去重。"""
from __future__ import annotations

from typing import Any

from llm_price_monitor import tasklog
from llm_price_monitor.store import Store, latest_key

from .events import fingerprint
from .health import _PERSIST_LOCK
from .pricing import _has_price


def _persist_latest(
    store: Store,
    latest: dict[str, dict[str, Any]],
    *,
    removed_keys: set[str] | None = None,
    touched_keys: set[str] | None = None,
) -> None:
    """写回最新快照；replace_latest 只做 upsert，分组下线摘除的 key 需要显式删行。
    顺带清理历史遗留的无价占位行——现行采集不再产出占位记录，
    快照里残留的无价行都是旧版本（或旧库）留下的，保留只会在定价页造成同模型重复。
    touched_keys 限定本轮真正写过的 key：本轮开始时读到的其他站点行可能已被并行采集更新，
    拿旧值比对"内容变了"会把这些行回写覆盖，也可能把并发轮次刚摘除的分组复活。"""
    stale_keys = [key for key, row in latest.items() if isinstance(row, dict) and not _has_price(row)]
    if stale_keys:
        for key in stale_keys:
            del latest[key]
        store.remove_latest(stale_keys)
    if removed_keys:
        store.remove_latest(sorted(removed_keys))
    # 增量写入：与库中现有快照比对，内容没变的行不重写
    existing = store.latest_all()
    changed = {
        key: row
        for key, row in latest.items()
        if (touched_keys is None or key in touched_keys) and existing.get(key) != row
    }
    store.replace_latest(changed)


# 含 ":" 的模型/分组名解码时会并段（group 恒取最后一段），每个名字只提醒一次
_COLON_KEY_WARNED: set[str] = set()


def _event_snapshot_key(event: dict[str, Any]) -> str:
    """事件对应的快照 key（store.latest_key，site:model:group）；分组下线事件从 previous 取分组。"""
    record = event.get("current") or event.get("previous") or {}
    group = (record.get("metadata") or {}).get("group") or "default"
    model = event["model"]
    if (":" in model or ":" in group) and model not in _COLON_KEY_WARNED:
        _COLON_KEY_WARNED.add(model)
        tasklog.emit(f"[{event['site_id']}] 模型或分组名含冒号，快照 key 解析会把多余段并进模型名：{model} / {group}", "warn")
    return latest_key(event["site_id"], model, group)


def _drop_persisted_events(
    events: list[dict[str, Any]], fresh_latest: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """丢掉已被并发轮次落库的价格事件：测试采集与全量采集并行时（tasks.COLLECT_KINDS 特意放行），
    两路可能对同一份旧快照各自检出同一变化——快照里该 key 的指纹已经等于事件 current，
    或分组下线时 key 已被摘除，都说明另一路记录过同一变化，再写只会在事件流里出重复卡片。"""
    kept: list[dict[str, Any]] = []
    for event in events:
        key = _event_snapshot_key(event)
        if event.get("current") is not None:
            concurrent = fresh_latest.get(key)
            if concurrent is not None and fingerprint(concurrent) == fingerprint(event["current"]):
                continue
        elif key not in fresh_latest:
            continue
        kept.append(event)
    return kept


def _drop_persisted_notice_events(events: list[dict[str, Any]], store: Store) -> list[dict[str, Any]]:
    """公告事件同口径去重：库里该站点最新公告正文已等于事件正文，说明并发轮次已记录。"""
    kept: list[dict[str, Any]] = []
    for event in events:
        notice = store.latest_notice(event["site_id"])
        if isinstance(notice, dict) and notice.get("content") == event.get("content"):
            continue
        kept.append(event)
    return kept


def _persist_scan_results(
    store: Store,
    *,
    latest: dict[str, dict[str, Any]],
    history_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    removed_keys: set[str] | None,
    touched_keys: set[str],
    notice_records: list[dict[str, Any]] | None = None,
    notice_events: list[dict[str, Any]] | None = None,
) -> None:
    """价格/公告采集结果统一落库。
    复查与写入在同一把锁内完成：后落库的一路必然看到先落库一路的快照与事件，
    同一变化只入库一次；锁外再做的只有 collect_status / 健康档案合并（自有锁）。
    入参 events 只被去重后的结果写入库里，报告仍带本轮原始观测（弹窗如实回显）。"""
    with _PERSIST_LOCK:
        fresh_latest = store.latest_all()
        # 公告去重必须在本轮 notice_records 入库前做：比对对象是"库里已有的最新公告"，
        # 先写后比会拿本轮刚落的记录跟自己比对，把真实事件误判成并发重复
        kept_notice_events = (
            _drop_persisted_notice_events(notice_events, store) if notice_events else notice_events
        )
        store.append_history(history_rows)
        store.append_events(_drop_persisted_events(events, fresh_latest))
        if notice_records:
            store.append_notice_records(notice_records)
        if kept_notice_events:
            store.append_notice_events(kept_notice_events)
        _persist_latest(store, latest, removed_keys=removed_keys, touched_keys=touched_keys)
