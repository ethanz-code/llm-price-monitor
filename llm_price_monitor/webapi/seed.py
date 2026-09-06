"""手动重新导入种子：把种子文件内容按「补齐缺失 / 覆盖写入」两种模式合并进数据库。

与首次启动的 _seed_store 不同：不导入 var/ 存量文件、不打 seeded 标记，
只处理种子文件里显式声明的内容；种子未声明的配置（包括缺省的 ai 段）一律不动。
"""
from __future__ import annotations

from typing import Any

from llm_price_monitor.config import config_from_raw, schedule_from_raw
from llm_price_monitor.store import Store

MODES = ("skip_existing", "overwrite")


def apply_seed(store: Store, raw: dict[str, Any], mode: str) -> dict[str, Any]:
    """按模式把种子内容合并进库，返回写入摘要；先整体校验后落库，非法种子抛 ValueError。"""
    raw = raw if isinstance(raw, dict) else {}
    seed_settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    seed_ai = raw.get("ai") if isinstance(raw.get("ai"), dict) else {}

    settings = dict(store.get_document("settings") or {})
    ai = dict(store.get_document("ai") or {})
    if mode == "overwrite":
        settings_written = list(seed_settings)
        ai_written = list(seed_ai)
        settings.update(seed_settings)
        ai.update(seed_ai)
    else:
        settings_written = [key for key in seed_settings if key not in settings]
        ai_written = [key for key in seed_ai if key not in ai]
        for key in settings_written:
            settings[key] = seed_settings[key]
        for key in ai_written:
            ai[key] = seed_ai[key]

    existing_sites = [item for item in store.list_site_configs() if isinstance(item, dict)]
    seed_sites = [item for item in raw.get("sites", []) if isinstance(item, dict)] if isinstance(raw.get("sites"), list) else []
    sites_replaced = False
    if mode == "overwrite" and seed_sites:
        # 覆盖保护：默认种子 sites 为空数组时不清空站点，只有种子列出了站点才整表替换
        final_sites, sites_replaced = seed_sites, True
    elif mode == "skip_existing":
        known_ids = {str(item.get("id")) for item in existing_sites}
        final_sites = [*existing_sites, *[item for item in seed_sites if str(item.get("id")) not in known_ids]]
    else:
        final_sites = existing_sites

    if "schedule" in settings:
        schedule_from_raw(settings["schedule"])  # schedule 不在 MonitorSettings 字段里，需单独校验
    config_from_raw(
        {"settings": settings, "ai": ai, "sites": final_sites},
        resolve_env=False,
        cache=store,
    )  # 合并结果整体校验，坏种子在写入前报错

    store.set_document("settings", settings)
    store.set_document("ai", ai)
    if sites_replaced:
        store.replace_sites(final_sites)
    else:
        for item in final_sites[len(existing_sites):]:
            store.upsert_site(str(item.get("id")), item)

    return {
        "mode": mode,
        "settings_written": settings_written,
        "ai_written": ai_written,
        "sites_written": len(final_sites) if sites_replaced else len(final_sites) - len(existing_sites),
        "sites_replaced": sites_replaced,
    }
