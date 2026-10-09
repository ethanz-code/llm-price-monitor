"""三类独立扫描：价格（_scan_prices）、渠道状态（_scan_statuses）、站点公告（_scan_notices）与分组下线检测。"""
from __future__ import annotations

import time
from dataclasses import asdict, replace
from typing import Any

import httpx

from llm_price_monitor import tasklog
from llm_price_monitor.adapters import ADAPTERS
from llm_price_monitor.config import AuthRequiredError, MonitorConfig, PriceMonitorError, SiteSpec
from llm_price_monitor.status import diff_status
from llm_price_monitor.store import Store, latest_key, latest_site_prefix, split_latest_key
from llm_price_monitor.token_refresh import needs_refresh, refresh_and_recollect, refresh_and_retry_once
from llm_price_monitor.tracker import PriceRecord

# fetch_site_status / fetch_site_notice 运行时经包命名空间回查：
# tests 用 monkeypatch.setattr(report_module, "fetch_site_status", ...) 打桩，
# 子模块必须从包属性取值打桩才生效（与 store/_MAX_ROW_LIMIT 同一套路）。
from llm_price_monitor import report as _report_pkg

from .events import PriceEvent, SectionScan, classify, fingerprint, record_dict, site_status_from_records
from .health import (
    _MERGE_LOCK,
    STATUS_TRANSPORT_TOLERANCE,
    _bump_transport_streak,
    _reset_transport_streak,
    is_transport_error,
)
from .persist import _persist_scan_results
from .pricing import _apply_price_sanity, _backfill_rule_price, _carry_last_price, _filter_price_groups, _has_price, _sanity_context
from .timeouts import SITE_FETCH_CONCURRENCY, _run_site_fetch, start_network_phase


def _build_network_phase(
    spec: SiteSpec,
    adapter: Any,
    whitelist: list[str],
    client: httpx.Client,
    config: MonitorConfig,
    user_agent: str,
    store: Store | None,
) -> Any:
    """组装单站网络阶段（多地址合并 + 续签重采）。闭包只依赖入参、不引用外层循环变量，
    供并行线程安全延迟执行。"""
    def collect_all(site_spec: SiteSpec) -> tuple[list[PriceRecord], list[str]]:
        site_records: list[PriceRecord] = []
        site_by_key: dict[tuple[str | None, str | None], PriceRecord] = {}
        site_errors: list[str] = []
        for index, endpoint in enumerate([site_spec.network, *site_spec.networks]):
            endpoint_spec = replace(site_spec, network=endpoint, networks=())
            try:
                endpoint_records = _filter_price_groups(
                    adapter.collect(endpoint_spec, client, config.settings.timeout, user_agent, config.ai),
                    whitelist,
                )
            except (PriceMonitorError, httpx.HTTPError, ValueError) as exc:
                site_errors.append(f"{'主地址' if index == 0 else f'附加地址{index}'}: {exc}")
                continue
            # 同一模型+分组多地址命中时主地址优先；但主地址的"需认证/无数据"占位
            # 不得挡掉附加地址的真价格（401 只说明该地址要登录，不代表分组没数据）
            for record in endpoint_records:
                key = (record.model, (record.metadata or {}).get("group"))
                existing = site_by_key.get(key)
                if existing is not None and (existing.price_status != "unavailable" or record.price_status == "unavailable"):
                    continue
                site_by_key[key] = record
                if existing is None:
                    site_records.append(record)
                else:
                    # 用下标原位替换：PriceRecord 值相等，按值 index 可能命中另一个相等对象
                    for idx, item in enumerate(site_records):
                        if item is existing:
                            site_records[idx] = record
                            break
        return site_records, site_errors

    def network_phase() -> tuple[list[PriceRecord], list[str]]:
        phase_records, phase_errors = collect_all(spec)
        # 有任一"需认证"且配了续签接口：续签 token、回写数据库并用新 token 重采一次
        if spec.token_refresh and needs_refresh(phase_records):
            tasklog.emit(f"[{spec.id}] 采集返回需认证，尝试续签 token…")
            # 重采结果整体替换本轮记录与地址错误；续签失败或重采无数据时拿到的是续签前的
            # "需认证"占位记录，保住该状态下面才会跳过分组下线判定（401 是我方凭证问题，
            # 不代表分组真的下线）。错误列表不再被静默清空，重采阶段的地址失败照常上报。
            phase_records, phase_errors = refresh_and_recollect(
                spec,
                phase_records,
                collect=collect_all,
                client=client,
                timeout=config.settings.timeout,
                user_agent=user_agent,
                store=store,
            )
            if not phase_errors:
                tasklog.emit(f"[{spec.id}] token 已续签并重新采集：{len(phase_records)} 条价格")
        return phase_records, phase_errors

    return network_phase


def _scan_prices(
    config: MonitorConfig,
    client: httpx.Client,
    user_agent: str,
    store: Store | None,
    latest: dict[str, dict[str, Any]],
    persist: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """价格采集主体：逐站点走适配器，与上次快照比对生成事件。

    返回 (records, events, errors, site_status)；持久化在本函数内逐站完成——
    history_rows 只含本次真正取到价的记录（沿用上次价的占位行与无数据的跳过行都不写历史）、
    removed_keys 是分组下线要从快照摘除的 key、touched_keys 是本轮真正写过快照的 key
    （防止与并行采集互相回写覆盖），三者只进落库链路，不再外传给调用方。
    """
    records: list[dict[str, Any]] = []
    history_rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    site_status: dict[str, dict[str, Any]] = {}
    removed_keys: set[str] = set()
    touched_keys: set[str] = set()
    enabled_specs = [spec for spec in config.sites if spec.enabled]
    enabled_count = len(enabled_specs)
    scan_started = time.time()
    tasklog.emit(f"开始价格采集：{enabled_count} 个站点（并发 {SITE_FETCH_CONCURRENCY}）")
    # 网络阶段并行开工、按提交顺序处理落库：一个站卡满硬上限只占一个并发槽，
    # 不再让排在后面的站陪等（串行时最坏 ⌊整轮⌋ = 站数 × 硬上限）
    handles: dict[str, Any] = {}
    for done_count, spec in enumerate(enabled_specs, 1):
        # 每站开始即落一行：整轮拖长时能直接看出卡在哪个站、卡在站点内哪一步之后
        tasklog.emit(f"[{spec.id}] 开始采集（第 {done_count}/{enabled_count} 站）")
        adapter = ADAPTERS.get(spec.adapter)
        if adapter is None:
            errors.append({"site_id": spec.id, "error": f"未知适配器: {spec.adapter}"})
            site_status[spec.id] = {"status": "error", "error": f"未知适配器: {spec.adapter}", "checked_at": time.time()}
            tasklog.emit(f"[{spec.id}] 价格采集失败：未知适配器 {spec.adapter}", "error")
            continue
        # 多地址站点：主地址 + networks 附加地址依次采集；同一模型重复命中时主地址优先
        # 分组白名单：status.groups 配置了就只保留选中分组的记录（含续签重采路径）
        whitelist = [str(item) for item in spec.status["groups"]] if isinstance(spec.status.get("groups"), list) else []
        handles[spec.id] = start_network_phase(
            _build_network_phase(spec, adapter, whitelist, client, config, user_agent, store), spec.id
        )
    for done_count, spec in enumerate(enabled_specs, 1):
        handle = handles.get(spec.id)
        if handle is None:
            continue  # 未知适配器的站在开工轮已记过错误
        site_started = time.time()
        collected, collect_errors = handle.result()
        if collect_errors and not collected:
            message = "；".join(collect_errors)
            errors.append({"site_id": spec.id, "error": message})
            site_status[spec.id] = {"status": "error", "error": message, "checked_at": time.time()}
            tasklog.emit(f"[{spec.id}] 价格采集失败：{message}", "error")
            continue
        for message in collect_errors:
            errors.append({"site_id": spec.id, "error": message})
            tasklog.emit(f"[{spec.id}] 部分地址采集失败：{message}", "error")
        site_status[spec.id] = {"checked_at": time.time(), **site_status_from_records(collected)}
        site_changed = 0
        site_keys: set[str] = set()
        # 本轮真正取到价的 key：分组/模型缺失计数只认真价轮——响应还在但没解析出价的轮次
        # 也是"没有数据"，同样向摘除阈值累计，过期旧价不得无限期挂在定价页
        site_priced_keys: set[str] = set()
        # 站点切片标记：本站新增的历史行与事件从这两个下标起，供站内立即落库
        history_mark = len(history_rows)
        events_mark = len(events)
        # 本轮开始前快照里已有的模型：这些模型冒出新分组记 group_added，全新模型仍记 new
        known_models = {
            split_latest_key(key, spec.id)[0]
            for key in latest
            if key.startswith(latest_site_prefix(spec.id))
        }
        # 快照里没有任何本站数据 = 建档轮：逐模型"新增"事件只会刷屏，静默入库
        site_is_new = not known_models
        # sanity 判据每站现读：一轮采集可能被卡死站点或机器休眠拖上数小时，
        # 目录中途修正后要用新判据，启动时的坏目录不得贯穿整轮
        sanity_models, sanity_rate = _sanity_context(store)
        for record in collected:
            # 分组归一：metadata 缺失时兜底 default，保证事件键跨扫描稳定
            group = (record.metadata or {}).get("group") or "default"
            key = latest_key(spec.id, record.model, group)
            site_keys.add(key)
            previous = latest.get(key)
            # sanity 两轮确认要读上一轮的待复核标记，须在查出 previous 之后判
            current = _apply_price_sanity(
                _backfill_rule_price(record_dict(spec.id, record)), sanity_models, sanity_rate, previous
            )
            if not _has_price(current):
                if previous is None or not _has_price(previous):
                    # 本次没拿到数据、上次也没有可用价：不新增占位记录，避免快照与历史重复膨胀
                    continue
                if (previous.get("metadata") or {}).get("sanity_suspect"):
                    # 上次价挂着待复核标记（异常首轮），本次作废说明异常复现：
                    # 可疑旧价不沿用（落库层会清掉无价行，等于该行下架待下一轮好数据）
                    pass
                else:
                    # 上次有价、本次没拿到：沿用上次价格并标"需认证"，但不写历史（价格没有新观测）
                    current = _carry_last_price(current, previous)
                latest[key] = current
                touched_keys.add(key)
                records.append(current)
                kind = classify(previous, current)
                events.append(asdict(PriceEvent(spec.id, record.model, kind, previous, current, time.time())))
                site_changed += kind != "unchanged"
                continue

            current["fingerprint"] = fingerprint(current)
            current["captured_at"] = record.captured_at
            site_priced_keys.add(key)
            records.append(current)
            # 指纹去重：价格口径与上一轮一致就不写历史行，趋势表只保留真实变化点。
            # previous 的指纹现场重算而非读存量哈希——历史行落库时的口径可能比当前代码旧，
            # 直接比存量哈希会让口径升级后的第一轮全量多写一遍趋势行
            if previous is None or fingerprint(previous) != current["fingerprint"]:
                history_rows.append(current)
            if previous is None and record.model in known_models:
                kind = "group_added"  # 老模型的新分组：与分组下线对称，区别于全新模型的"新增"
            else:
                kind = classify(previous, current)
            if not site_is_new:
                events.append(asdict(PriceEvent(spec.id, record.model, kind, previous, current, time.time())))
                site_changed += kind != "unchanged"
            latest[key] = current
            touched_keys.add(key)
        # 出现"需认证"占位行（401/403）时是我方凭证问题，不代表站点真的没这份数据，
        # 跳过缺失计数，避免 token 过期把整站价格刷成下线事件；
        # 有地址采集失败时本轮记录同样不完整（没采到 ≠ 站点没数据），一并跳过；
        # 测试采集（persist=False）同样不计数——不完整的测试轮次不得污染正式摘除阈值
        site_removed: set[str] = set()
        if store is not None and persist and not needs_refresh(collected) and not collect_errors:
            site_removed = _detect_removed_groups(store, latest, spec.id, site_priced_keys, events)
            removed_keys |= site_removed
        # 每站采完立即落库：单站卡死被放弃、或整轮中途被重启时，已完成的站点不随内存丢失。
        # 落库范围用站点切片限定（touched ∩ site_keys 恰为本站写过的快照 key），
        # latest 仍传完整内存快照，由 touched 限定只比对本站 key。
        if store is not None and persist:
            _persist_scan_results(
                store,
                latest=latest,
                history_rows=history_rows[history_mark:],
                events=[
                    event
                    for event in events[events_mark:]
                    if event["kind"] not in ("unchanged", "status_changed")
                ],
                removed_keys=site_removed or None,
                touched_keys=touched_keys & site_keys,
            )
        if site_is_new:
            tasklog.emit(f"[{spec.id}] 站点建档：{len(collected)} 条价格入库（首轮不产生变化事件），{time.time() - site_started:.1f}s")
        else:
            tasklog.emit(f"[{spec.id}] 价格采集成功：{len(collected)} 条价格，{site_changed} 处变化，{time.time() - site_started:.1f}s")
    tasklog.emit(f"价格采集完成：{len(records)} 条记录，{len(errors)} 个错误，{time.time() - scan_started:.1f}s")
    return records, events, errors, site_status


# 连续缺失这么多次才摘除：站点换分组清单、临时调整常态发生，单轮缺失就删会把改版刷成下线事件；
# 用户口径是数据要跟站点实时对齐——连续 3 轮拿不到数据（约半天内）就把旧价从快照删掉，不长期挂过期价
GROUP_REMOVED_MISSES = 3


def _detect_removed_groups(
    store: Store, latest: dict[str, dict[str, Any]], site_id: str, priced_keys: set[str], events: list[dict[str, Any]]
) -> set[str]:
    """本轮采集成功的站点里，快照中存在但本轮没取到真实价格的分组视为一次缺失：
    连续 GROUP_REMOVED_MISSES 次缺失记 group_removed 事件并从快照摘除；中途恢复则清零。
    缺失既包括模型/分组从站点响应里消失，也包括响应还在但没解析出价的轮次
    （占位行、沿用旧价轮）——两种都算"没有数据"，向同一阈值累计。
    缺失计数持久化到 group_miss 文档，跨轮次累计；返回本轮摘除的快照 key。
    group_miss 是并行采集（全量 + 单站测试）共享的读-改-写文档，全程持 _MERGE_LOCK，
    否则并发轮次会互相丢计数或对同一次下线重复发事件。"""
    with _MERGE_LOCK:
        watch = dict(store.get_document("group_miss") or {})
        removed: set[str] = set()
        changed = False
        site_prefix = f"{site_id}:"
        for key in [key for key in latest if key.startswith(site_prefix)]:
            if key in priced_keys:
                if watch.pop(key, None) is not None:
                    changed = True
                continue
            count = int(watch.get(key) or 0) + 1
            if count >= GROUP_REMOVED_MISSES:
                previous = latest.pop(key)
                removed.add(key)
                watch.pop(key, None)
                model = split_latest_key(key, site_id)[0]
                events.append({
                    "site_id": site_id,
                    "model": model,
                    "kind": "group_removed",
                    "previous": previous,
                    "current": None,
                    "detected_at": time.time(),
                })
                tasklog.emit(f"[{site_id}] 连续 {GROUP_REMOVED_MISSES} 轮无数据，移除旧价：{model}")
            else:
                watch[key] = count
            changed = True
        for key in [key for key in watch if key not in latest]:
            watch.pop(key, None)  # 快照里已经没有的 key 不再计数（站点被删/已被摘除）
            changed = True
        if changed:
            store.set_document("group_miss", watch)
        return removed


def _scan_statuses(
    config: MonitorConfig, client: httpx.Client, user_agent: str, store: Store | None
) -> SectionScan:
    """渠道状态采集：只处理配置了 status.url 的站点；与上次记录 diff，变化写入 status 事件。"""
    scan = SectionScan()
    scan_started = time.time()
    tasklog.emit("开始渠道状态采集")

    def _record_status_failure(site_id: str, exc_text: str) -> None:
        """状态采集失败落账：传输抖动（SSL/超时/连接重置）连续不足容忍轮数只留 warn 日志、
        不刷错误卡片；达到容忍轮数或非传输类失败照旧报错。成功与非传输失败会清零抖动计数。"""
        if is_transport_error(exc_text):
            streak = _bump_transport_streak(store, site_id)
            if streak < STATUS_TRANSPORT_TOLERANCE:
                tasklog.emit(
                    f"[{site_id}] 渠道状态采集失败：{exc_text}（网络抖动连续第 {streak}/{STATUS_TRANSPORT_TOLERANCE} 轮，暂不报警）",
                    "warn",
                )
                return
            tasklog.emit(f"[{site_id}] 渠道状态采集失败：{exc_text}（网络抖动已连续 {streak} 轮）", "error")
        else:
            _reset_transport_streak(store, site_id)
            tasklog.emit(f"[{site_id}] 渠道状态采集失败：{exc_text}", "error")
        scan.errors.append({"site_id": site_id, "error": f"渠道状态采集失败: {exc_text}"})

    for spec in config.sites:
        if not spec.enabled or not spec.status.get("url"):
            continue
        # 逐站留痕：整轮拖长时能直接看出卡在哪个站的哪条腿
        tasklog.emit(f"[{spec.id}] 开始渠道状态采集")
        try:
            status_record = _run_site_fetch(
                lambda: _report_pkg.fetch_site_status(spec, client, config.settings.timeout, user_agent, config.ai), spec.id
            )
        except AuthRequiredError as exc:
            # 站点配了续签就换一次新 token 重试；没配续签的按普通失败处理
            if not spec.token_refresh:
                _record_status_failure(spec.id, str(exc))
                continue
            tasklog.emit(f"[{spec.id}] 渠道状态返回需认证，尝试续签 token…")
            status_record, retry_error = refresh_and_retry_once(
                spec,
                client=client,
                timeout=config.settings.timeout,
                user_agent=user_agent,
                store=store,
                attempt=lambda fresh: _run_site_fetch(
                    lambda: _report_pkg.fetch_site_status(fresh, client, config.settings.timeout, user_agent, config.ai), spec.id
                ),
            )
            if retry_error is not None:
                _record_status_failure(spec.id, str(retry_error))
                continue
            tasklog.emit(f"[{spec.id}] token 已续签并重新采集渠道状态")
        except (PriceMonitorError, httpx.HTTPError, ValueError) as exc:
            _record_status_failure(spec.id, str(exc))
            continue
        scan.records.append(status_record)
        _reset_transport_streak(store, spec.id)
        # diff 基准用未裁剪的参照快照：库里的快照只存时间线增量，直接拿会误报大量删除
        previous_record = store.status_reference(spec.id) if store is not None else None
        previous_data = previous_record.get("data") if isinstance(previous_record, dict) else None
        if previous_data is None:
            changes: list[dict[str, Any]] = [{"op": "init", "path": "$"}]
            status_kind = "status_init"
        else:
            changes = diff_status(previous_data, status_record["data"])
            status_kind = "status_changed"
        if len(changes) > 80:
            changes = changes[:80] + [{"op": "truncated", "path": "$", "count": len(changes) - 80}]
        if changes:
            scan.events.append({
                "site_id": spec.id,
                "kind": status_kind,
                "detected_at": status_record["captured_at"],
                "changes": changes,
            })
            tasklog.emit(f"[{spec.id}] 渠道状态有变化")
        else:
            tasklog.emit(f"[{spec.id}] 渠道状态无变化")
    tasklog.emit(f"渠道状态采集完成：{len(scan.records)} 个站点，{len(scan.errors)} 个错误，{time.time() - scan_started:.1f}s")
    return scan


def _scan_notices(
    config: MonitorConfig, client: httpx.Client, user_agent: str, store: Store | None
) -> SectionScan:
    """站点公告采集：未配置 notice.url 的站点自动抓根地址 /api/notice，404 视为没有公告接口静默跳过。

    正文与上一版本比较：空正文不入库；内容不变不重复存版本、不发事件。
    """
    scan = SectionScan()
    scan_started = time.time()
    tasklog.emit("开始站点公告采集")
    for spec in config.sites:
        if not spec.enabled:
            continue
        tasklog.emit(f"[{spec.id}] 开始站点公告采集")
        try:
            notice_record = _run_site_fetch(
                lambda: _report_pkg.fetch_site_notice(spec, client, config.settings.timeout, user_agent, ai=config.ai), spec.id
            )
        except AuthRequiredError as exc:
            # 同渠道状态：配了续签就换新 token 重试一次，否则照常记错误
            if not spec.token_refresh:
                scan.errors.append({"site_id": spec.id, "error": f"站点公告采集失败: {exc}"})
                scan.site_results.append({"site_id": spec.id, "outcome": "error", "content": ""})
                tasklog.emit(f"[{spec.id}] 站点公告采集失败：{exc}", "error")
                continue
            tasklog.emit(f"[{spec.id}] 公告地址返回需认证，尝试续签 token…")
            notice_record, retry_error = refresh_and_retry_once(
                spec,
                client=client,
                timeout=config.settings.timeout,
                user_agent=user_agent,
                store=store,
                attempt=lambda fresh: _run_site_fetch(
                    lambda: _report_pkg.fetch_site_notice(
                        fresh, client, config.settings.timeout, user_agent, ai=config.ai
                    ), spec.id
                ),
            )
            if retry_error is not None:
                scan.errors.append({"site_id": spec.id, "error": f"站点公告采集失败: {retry_error}"})
                scan.site_results.append({"site_id": spec.id, "outcome": "error", "content": ""})
                tasklog.emit(f"[{spec.id}] 站点公告采集失败：{retry_error}", "error")
                continue
            tasklog.emit(f"[{spec.id}] token 已续签并重新采集公告")
        except (PriceMonitorError, httpx.HTTPError, ValueError) as exc:
            scan.errors.append({"site_id": spec.id, "error": f"站点公告采集失败: {exc}"})
            scan.site_results.append({"site_id": spec.id, "outcome": "error", "content": ""})
            tasklog.emit(f"[{spec.id}] 站点公告采集失败：{exc}", "error")
            continue
        if notice_record is None:
            scan.site_results.append({"site_id": spec.id, "outcome": "none", "content": ""})
            continue
        if not notice_record["content"]:
            # 站点没发布公告是常态：带上库里最近一条公告，测试弹窗回显给用户看
            latest = store.latest_notice(spec.id) if store is not None else None
            scan.site_results.append({
                "site_id": spec.id,
                "outcome": "empty",
                "content": "",
                "latest": str(latest.get("content") or "") if isinstance(latest, dict) else "",
            })
            continue
        previous_notice = store.latest_notice(spec.id) if store is not None else None
        previous_content = previous_notice.get("content") if isinstance(previous_notice, dict) else None
        if previous_content is None:
            notice_kind: str | None = "notice_init"
        elif previous_content != notice_record["content"]:
            notice_kind = "notice_changed"
        else:
            notice_kind = None
        scan.site_results.append({"site_id": spec.id, "outcome": notice_kind or "unchanged", "content": notice_record["content"]})
        if notice_kind:
            notice_record["kind"] = notice_kind
            scan.records.append(notice_record)
            scan.events.append({
                "site_id": spec.id,
                "kind": notice_kind,
                "detected_at": notice_record["captured_at"],
                "content": notice_record["content"],
            })
            # 公告无变化与未配置公告接口的站点不逐站记日志，避免刷屏
            tasklog.emit(f"[{spec.id}] 公告{'新增' if notice_kind == 'notice_init' else '更新'}")
    tasklog.emit(f"站点公告采集完成：{len(scan.records)} 条公告，{len(scan.errors)} 个错误，{time.time() - scan_started:.1f}s")
    return scan
