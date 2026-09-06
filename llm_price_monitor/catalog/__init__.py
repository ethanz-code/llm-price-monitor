"""官方价目录（catalog）：models.dev 数据同步、折扣计算与共享工具。

官方价数据源为开源模型目录 models.dev（https://models.dev），价格统一 USD / 1M tokens；
刷新入口在 webapi 的目录刷新任务，本包只负责数据获取与纯计算：

- `jsonio`：JSON 文件读写缓存
- `normalize`：模型名归一化与数值取整
- `fx`：USD→CNY 汇率（实时获取 + 缓存兜底）
- `modelsdev`：models.dev api.json 同步与官方价目录映射
- `discount`：站点价格 vs 官方价的折扣计算
"""
