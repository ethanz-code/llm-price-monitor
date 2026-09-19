"""按域拆分的 APIRouter：每个模块暴露 build_router(store)，由 app.create_app 统一挂载。

拆分原则是纯移动：端点行为、路径、鉴权语义与单体 app.py 时代完全一致；
跨域复用的小工具（配置加载、登录态、目录读取、汇率取值）统一放 webapi/deps.py，
路由模块之间不互相导入。
"""

from . import (  # noqa: F401
    ai_logs,
    analytics,
    assistant,
    auth,
    catalog,
    collect,
    data,
    geo,
    settings,
    sites,
    status,
    submissions,
    vendor_sources,
)
