"""渠道状态时间线的识别与增量裁剪：独立于存储与采集链路的纯函数模块（只依赖标准库）。

站点状态响应里的时间线（timeline/history 等）是站点原样返回的滚动窗口：新点进、旧点出。
原样入库会让每条快照都拖着整份历史（实测九成以上体积是重复）；这里提供按检测时间戳
裁掉已存条目的 strip_status_delta，读取端把各快照时间线去重合并即可还原完整序列。
键名规则与前端 web/lib/channelStatus.ts 的 TIMELINE_KEYS / TIME_KEYS / NAME_KEYS 保持一致。

部分站点的时间线时间戳是滑动合成的（每次请求整体平移、间隔恒定，相邻快照零重叠），
按时间戳去重会全部落空；这类时间线去掉时间戳字段后内容逐条相同，因此先用内容指纹判定：
内容与上一条一致就整条裁掉——读取端该记录只剩渠道当前状态点，序列信息不受损。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

TIMELINE_KEYS = {"timeline", "history", "checks", "events", "logs", "log", "uptime"}
TIMELINE_TIME_KEYS = ("checked_at", "checkedAt", "checked", "timestamp", "detected_at", "time", "at", "ts")
_TIMELINE_TIME_KEY_SET = {key.lower() for key in TIMELINE_TIME_KEYS}
TIMELINE_NAME_KEYS = ("name", "channel", "model", "id", "title", "key")


def _timeline_time(item: Any) -> float | None:
    """时间线条目的检测时间戳（秒，毫秒数/ISO 字符串也接受）；识别不了的条目返回 None。"""
    if not isinstance(item, dict):
        return None
    for key in TIMELINE_TIME_KEYS:
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
    return None


def _identity_of(item: Any) -> str | None:
    """列表条目的身份键（渠道名之类），用于跨快照对齐；没有就 None。"""
    if isinstance(item, dict):
        for key in TIMELINE_NAME_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def is_timeline_path(path: Any) -> bool:
    """diff 路径是否落在时间线数组里（如 $.data.groups[3].timeline[12].state）。"""
    if not isinstance(path, str):
        return False
    return any(segment.split("[", 1)[0].lower() in TIMELINE_KEYS for segment in path.split("."))


def _content_view(item: Any) -> str:
    """时间线条目的内容指纹：剔除时间戳类字段后按序规范化——滑动合成时间戳不影响内容判定。"""
    if isinstance(item, dict):
        filtered = {key: value for key, value in item.items() if str(key).lower() not in _TIMELINE_TIME_KEY_SET}
        return json.dumps(filtered, ensure_ascii=False, sort_keys=True)
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def prune_status_groups(data: Any, groups: list[str]) -> tuple[Any, bool]:
    """按分组名严格裁剪状态数据：带名字的渠道条目只保留名字对得上的（忽略大小写）。
    返回 (裁剪后的数据, 是否有任何条目被保留)；一个都没匹配上时数据会被裁空，
    采集链路据此回退保留原数据，历史清理链路则照裁不误。"""
    targets = {group.strip().casefold() for group in groups if group.strip()}
    if not targets:
        return data, True
    matched = False

    def prune(node: Any) -> Any:
        nonlocal matched
        if isinstance(node, list):
            pruned = []
            for item in node:
                if isinstance(item, dict):
                    names = {str(item[key]).strip().casefold() for key in TIMELINE_NAME_KEYS if key in item}
                    if names:
                        if names & targets:
                            matched = True
                            pruned.append(item)
                        continue
                kept = prune(item)
                if kept is not None:
                    pruned.append(kept)
            return pruned
        if isinstance(node, dict):
            return {key: prune(value) for key, value in node.items()}
        return node

    return prune(data), matched


def _status_channel_names_at(data: Any, path: Any) -> set[str] | None:
    """diff 路径在参照数据里指向的渠道名集合：沿途最近一个带名字键的字典条目。
    解析不了（路径对不上、参照缺失）返回 None，调用方保守保留该变化。"""
    if not isinstance(path, str) or not path.startswith("$"):
        return None
    node = data
    names: set[str] | None = None
    for token in path[1:].split("."):
        if not token:
            continue
        key, _, index_part = token.partition("[")
        if isinstance(node, dict):
            if key not in node:
                return names
            if any(name_key in node for name_key in TIMELINE_NAME_KEYS):
                names = {str(node[k]).strip().casefold() for k in TIMELINE_NAME_KEYS if k in node}
            node = node[key]
        else:
            return names
        while index_part:
            index, _, index_part = index_part.partition("[")
            index = index.rstrip("]")
            if not isinstance(node, list):
                return names
            try:
                node = node[int(index)]
            except (ValueError, IndexError):
                return names
            if isinstance(node, dict) and any(name_key in node for name_key in TIMELINE_NAME_KEYS):
                names = {str(node[k]).strip().casefold() for k in TIMELINE_NAME_KEYS if k in node}
    return names


def change_matches_groups(change: Any, reference_data: Any, groups: list[str]) -> bool:
    """状态事件的单条变化是否属于选中分组：用变化路径在未裁剪参照数据里归因到渠道名判定。
    归因不了（参照缺失、路径解析失败）保守保留。"""
    targets = {group.strip().casefold() for group in groups if group.strip()}
    if not targets:
        return True
    names = _status_channel_names_at(reference_data, change.get("path") if isinstance(change, dict) else None)
    return names is None or bool(names & targets)


def strip_status_delta(previous: Any, current: Any, *, timeline_key: str | None = None) -> Any:
    """把 current 里与 previous 重复的时间线条目裁掉（按检测时间戳判定），返回裁剪后的结构。

    结构并行走：普通列表按条目名字对齐（站点可能改分组顺序），名字缺失按位置兜底。
    没有任何可裁内容时原对象返回（identity 相等），调用方据此跳过重写。"""
    if isinstance(current, dict):
        if not isinstance(previous, dict):
            return current
        rebuilt: dict[str, Any] = {}
        changed = False
        for key, value in current.items():
            child_key = str(key).lower() if isinstance(value, list) else None
            new_value = strip_status_delta(previous.get(key), value, timeline_key=child_key)
            rebuilt[key] = new_value
            if new_value is not value:
                changed = True
        return rebuilt if changed else current
    if isinstance(current, list):
        if timeline_key is not None and timeline_key in TIMELINE_KEYS:
            if not isinstance(previous, list):
                return current
            # 时间戳真实的滚动窗口：按时间戳去重，只留上一条没有的检测点
            seen = {moment for item in previous if (moment := _timeline_time(item)) is not None}
            kept = [item for item in current if (moment := _timeline_time(item)) is None or moment not in seen]
            cur_moments = {moment for item in current if (moment := _timeline_time(item)) is not None}
            if cur_moments & seen:
                return kept if len(kept) != len(current) else current
            if not cur_moments:
                # 双方都无可解析时间戳：仅在内容完全一致时整条裁掉
                if [_content_view(item) for item in current] == [_content_view(item) for item in previous]:
                    return []
                return current
            # 时间戳与上一条完全无交集 → 滑动合成时间戳的窗口（如每次请求整体平移的假时间轴）：
            # 逐条去重必然落空，历史时序由每次采集的渠道当前状态点（captured_at）承载，整条裁掉
            return []
        if isinstance(previous, list):
            by_ident: dict[str, Any] = {}
            for item in previous:
                ident = _identity_of(item)
                if ident is not None:
                    by_ident[ident] = item
            rebuilt = []
            changed = False
            for index, item in enumerate(current):
                ident = _identity_of(item)
                prev_item = by_ident.get(ident) if ident is not None else (previous[index] if index < len(previous) else None)
                new_item = strip_status_delta(prev_item, item)
                rebuilt.append(new_item)
                if new_item is not item:
                    changed = True
            return rebuilt if changed else current
        return current
    return current
