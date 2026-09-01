"""Assay 试金：多中转站模型价格取证、监控与官方价审计。

模块结构：
- `tracker`：价格解析与采集库函数（PriceRecord、newapi_price_record、extract_price）
- `units`：价格数值与单位归一化
- `matching`：目标模型别名匹配
- `evidence`：网络/页面证据捕获、脱敏与按目标模型筛选
- `useragent`：浏览器 UA 构造与轮换
- `config`：强类型配置与校验（load_config）
- `ai`：AI 价格抽取（AIPriceExtractor）
- `adapters`：价格采集适配器（NetworkAdapter、ADAPTERS）
- `report`：运行编排、事件分类、持久化与汇总（run_once、summary_row）
- `official`：官方价搜索与折扣率计算（tavily/extraction/search/fx/discount/fetch）
- `webapi`：FastAPI 服务（数据端点、采集任务、管理接口），唯一入口 `price-web`
"""
