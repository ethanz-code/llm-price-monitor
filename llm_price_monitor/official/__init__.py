"""官方价搜索与折扣率计算的共享实现。

CLI 入口为 pyproject 注册的 `fetch-official-prices` / `price-discount`（实现在
`llm_price_monitor/official_cli.py`），全部业务逻辑按职责拆在本包：

- `jsonio`：JSON 文件读写缓存
- `normalize`：模型名归一化与数值取整
- `fx`：USD→CNY 汇率（实时获取 + 缓存兜底）
- `tavily`：Tavily key 解析与搜索（HTTP API + tvly CLI 兜底）
- `extraction`：AI 提取官方价与结果条目规范化（含促销价优先、币种换算）
- `search`：按厂商/按模型的搜索编排（多查询、官方域名收敛、重试）
- `discount`：站点价格 vs 官方价的折扣计算
"""
