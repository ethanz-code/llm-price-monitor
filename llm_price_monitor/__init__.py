"""Proxy-SmartAven 价格监控子包。

多站点价格监控、单站点采集、官方原价搜索与折扣率计算的统一实现。

模块结构：
- `tracker`：单站点确定性计价核心（PriceRecord、newapi_price_record、fetch_price）
- `units`：价格数值与单位归一化（monitor/tracker/usage_cost 共用）
- `matching`：目标模型别名匹配
- `evidence`：网络/页面证据捕获、脱敏与按目标模型筛选
- `useragent`：浏览器 UA 构造与轮换
- `config`：强类型配置与校验（load_config）
- `ai`：AI 价格抽取（AIPriceExtractor）
- `adapters`：价格采集适配器（NetworkAdapter、ADAPTERS）
- `report`：运行编排、事件分类、持久化与汇总（run_once、summary_row）
- `monitor`：CLI 入口与向后兼容 re-export
- `official`：官方价搜索与折扣率计算（tavily/extraction/search/fx/discount）
- `official_cli`：官方价两个 CLI 入口（fetch-official-prices / price-discount）

命令行入口已注册到 pyproject `[project.scripts]`：
price-monitor / price-tracker / fetch-official-prices / price-discount。
`scripts/` 下的同名脚本为兼容 shim，行为一致。
"""
