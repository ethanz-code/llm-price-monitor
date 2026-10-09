# 项目经验速查（踩坑 → 方案索引）

动手前先翻这页，历史上踩过的坑不重踩。细节看指针：部署排障见 [deploy/ai-runbook.md](deploy/ai-runbook.md)，采集与价格口径见 [pricing.md](pricing.md)，UI 规范见 [design-guide.md](design-guide.md)，认证/反爬实测见 Serena 记忆 `dom_url_auth_constraint`。

## 触发词索引（命中先查，再动手）

| 途中见到这些词 | 先看 |
| --- | --- |
| 401 / 403 / WAF / Cookie / Cloudflare / 认证 / 登录态 | 本页 §1 + Serena 记忆 `dom_url_auth_constraint` |
| 代理 / 出口 / Clash / 境外站 / 超时 / TLS 掐断 | 本页 §1 |
| AI 输出截断 / max_tokens / 助手 token 爆炸 | 本页 §2 |
| 价格 / 基准价 / 官方目录 / 排序 / 变更事件 / 假变更 | 本页 §3 |
| 骨架屏 / 暗色 / 表格 / 窄屏 / 响应式 | 本页 §4 |
| Docker / 构建 / 回滚 / APP_TAG / 端口 | 本页 §5 |
| 网站打不开 / 域名被拦 / webblock / 302 跳备案页 | 本页 §5 |

## 1. 采集与网络（坑最多）

| 踩过的坑 | 已落地的方案 | 指针 |
| --- | --- | --- |
| httpx 直采没有浏览器环境，DOM 页必须免登录；认证要显式配 headers/token_refresh | 无头浏览器是例外分支（`network.headless.enabled`），注入 Cookie/localStorage 后渲染再解析 | Serena 记忆；`browser_fetch.py` |
| new-api 系站点 401：光有 Cookie 不够，还要 `New-Api-User` 头；Cookie 只挂公告 headers 时全站采不到 | Cookie 提升到站点级 `network.headers` 共享 | Serena 记忆 |
| Cloudflare WAF 403：多为缺 `Referer`/`Origin` 头，补齐后匿名也能过；别只报"可能需要认证" | 站点级 request_headers 配齐 Referer+Origin+Accept，看响应体里的真实 message | Serena 记忆 |
| 采集客户端被 shell 环境变量代理劫持（Clash env） | `build_client` 显式传 transport；采集出口语义统一为"直连优先、代理兜底" | 提交 843bcd9 / 088c631 |
| 境内服务器采不到国外站 | mihomo 兜底代理 sidecar（吃机场订阅），profile 默认不启、`.env` 一行启用；mihomo 必须 `allow-lan`；代理自带 healthcheck | docker-compose.md「采集兜底代理」 |
| 对端间歇掐断 TLS，整轮数据丢失 | GET/HEAD 退避重试 2 次（1s/2s） | 提交 6adf0b0 |
| 采集卡死拖垮整轮 | 每站采完立即落库 + 单站硬超时 | 提交 9f2fb35 |
| 站点模型名省略版本号对不上，漏采（DeepSeek 案例） | 模型名一侧省略版本号也能匹配，歧义有处理规则 | 提交 3c383d1；pricing.md |
| "整站没价"被记成错误，用户看不到原因 | 401 这类"没价但不算错误"的原因直接报给用户 | 提交 40124fe |
| 想采站点全部模型逐个列太累 | models 配置 `["*"]` 通配符全量采集，不可与其他模型混列 | 提交 3459300 |

## 2. AI 抽取与助手

| 踩过的坑 | 已落地的方案 | 指针 |
| --- | --- | --- |
| 输出被模型 max_tokens 掐断 | 学到的上限落库预载、过小模型自动跳过；固定 `ai.price_model` 起始档；单段预算 16000 | 提交 274e5fa / 75fd18f / e371c5b |
| prompt 太长挤爆输出 | 简介压 40 字内再进 prompt | 提交 8452ccc |
| 换模型回退逻辑三处拷贝，改一处漏两处 | 合并为一套 helper | 提交 eccba2d |
| 供应商报错天书，思考受限模型直接失败 | 错误可读化 + 自动换参重试 | 提交 3387d6a |
| 助手单次提问 token 爆炸 | 数据摘要瘦身，消耗降约 88% | 提交 d2f626e |
| AI 调用失败还扣每日次数 | 失败不扣次数 | 提交 2f6487b |

## 3. 价格与目录口径

| 踩过的坑 | 已落地的方案 | 指针 |
| --- | --- | --- |
| models.dev 的国内价格不可靠 | 国内基准只认厂商定价源，models.dev 国内价不进系统 | 提交 da6c392；pricing.md |
| 官方目录混入托管/转售条目 | 品牌归属闸门：只收厂商自研模型 | 提交 e05b430 / 55c4d79 |
| 各页排序各搞一套 | 全站统一目录口径：发布日期倒序 + 名称 | 提交 ea29ee7 / 62830d8 |
| 快照 key 拼法、fingerprint 白名单等隐式契约散落多处 | 收口成唯一常量/函数（`latest_key`、`FINGERPRINT_METADATA_KEYS`） | 提交 a55a65d |
| 高峰/空闲时段价丢档 | AI 兜底改多档抽取，时段价都保留并带说明 | 提交 0716b3f |
| 国内定价页条目没有发布日期和 modalities，监控清单自动追加没法按日期/形态过滤 | 双线追加：白名单厂商按 release_date 最新一批，国内厂商按定价页插入序前 N 滚动；准入统一走 catalog/general.py `is_general_llm`——特殊领域名字黑名单 + 日期后缀快照变体（gpt-4o-2024-05-13、deepseek-v4-pro-0813、qwen3.8-max-0902）在官方目录构建、厂商定价源存储、两个合并与自动追加四处共用；YYMM 版本号（step-3.5-flash-2603）不是日期不拦 | catalog/general.py |
| 厂商定价页解析出的缓存读价比输出价还高（如 MiniMax M2.x 的 0.042/0.021/0.018） | 判"价目列错位"整条跳过不进目录——该模型自动追加也随之缺失，修复要回到页面解析层 | vendor_sources.py `_price_order_violation` |
| AI 抽取轮次间表示法漂移：tier 里 `cache_create_price: null` 下一轮直接省略键，指纹当成两种价格，有效价一分没变也刷"变更"事件（DaiTuAI 一轮四连假事件，用户点名） | fingerprint 归一"空"的两种写法：dict 里 None 值键剔除与键缺失等价；有值↔缺失仍算真变更。凡跨轮比较的结构都要做同类归一 | report.py `fingerprint`；存量清理 `uv run price-admin prune-noop-events`（默认预览，--apply 才删） |

## 4. 前端

| 踩过的坑 | 已落地的方案 | 指针 |
| --- | --- | --- |
| 进页先闪别的页骨架或假空态（"还没有记录"） | 每页 loading.tsx 对齐真实布局；admin 布局登录守卫先画外壳骨架 | 提交 54c1412 / 4e349ec / c6e84f0 |
| 行高/列宽全站一刀切，窄屏挤爆 | 行高分档（默认/dense/紧凑），窄屏减列保关键列 | 提交 b1e7354 / 21038a1 / debee38 |
| AdminSites 3038 行改不动 | 拆 sites/ 七个子模块，零逻辑改动 | 提交 58f3f47 |
| 暗色下元素不可见、装饰违和 | 暗色骨架高亮 8%→14%；删光晕/拟物装饰；logo 内联 SVG 走 currentColor | 提交 c6e84f0 / 05a3ec9 / ff8fd27 |
| 流式字号（clamp）各处视觉漂移 | 断点收敛四档，流式字号改固定档位 | 提交 70f5f0d |
| 亮色下文字看不清（3D 地球站点标签） | 加深文字+压实底色，亮暗两套对比都逐一实测 | 提交 5a1172b |
| 配置表单十几项平铺，用户不会填 | 按「采集方式×认证方式」场景组织+选场景自动预填；预填有顺序依赖，顺序错会漏填（修过一次顺序漏洞） | 提交 e0d96c6 / c28ea66 / b52bc7a |
| 长列表按字母序平铺，关键项被挤出首屏 | 按厂商分组限量（每家前 N）+节头整行展开，搜索时放开走平铺；排序全站统一目录口径 | 提交 2234045 |
| 输入控件边框风格显旧、焦点乱 | 全站去边框改填充底 | 提交 dae36c4 |
| antd 全量引入体积大、风格难收敛 | 移除 antd 换自研 UI kit（ui.tsx），版式对齐 AA 可达性 | 提交 ac68525 |
| 界面文案缺中文映射回退英文原文；下拉与已选标签排序不一致 | 映射表补齐任务类型中文名；显示与保存同口径（同序归一） | 提交 034269b / 62830d8 |
| 新页面样式随意发挥 | 服从 design-guide.md（色彩 token、组件规范、去 AI 味清单） | design-guide.md |
| 首页这类常驻动画页上 Browser Use 的 Playwright 定位点击卡 actionability 超时、导航/刷新后立即截图超时 | 元素确认可命中后改用坐标点击（cua.click）；截图前先等 2–3s 页面稳定，一次只拍一张 | 首页 2D 地球替换走查实测 |
| 同一事件流多个页面各画各的：首页不折叠、追踪页折叠，站点名一边美化名一边原始 site_id，用户并排一看以为数据错乱 | 多页面共用数据源的展示口径（折叠/命名/排序）抽到 web/lib 单处共享，改口径只能改一处 | 提交 1e7ec68 |
| 常驻动画页走查深水区：IAB 截图通道跑一阵后整体卡死（surface preparation timed out / capture failed for guest），force click 也被拖超时；fullPage 整页截图对 Reveal 懒显页全空白（下方 opacity:0 不触发） | 读 DOM 的 evaluate 始终可用；截图降级 Chrome DevTools MCP（独立实例互不拖累）；懒显页逐段滚动触发后再截视口图；受控下拉（如主题菜单）点不开时按「环境准备」预置 localStorage 再 reload | 首页叙事三区走查实测 |

## 5. 部署与运维

| 踩过的坑 | 已落地的方案 | 指针 |
| --- | --- | --- |
| Dockerfile RUN 找不到 `.venv` 命令（exit 127） | `ENV PATH` 必须放在 `playwright install` 之前 | 提交 a61bffd |
| 大陆服务器构建失败 | 专项排障章节 | ai-runbook.md §11 |
| 更新/回滚不知道当前是哪版 | APP_TAG 镜像标签即线上版本号，改 `.env` 即切版本；数据在 var/ 不动 | docker-compose.md 日常运维 |
| 端口冲突 | API 端口固定 8437（前端 3000） | 提交 0e98ea3 |
| 备份漏密钥 | SQLite 在线备份；密钥在数据库里随 var/ 一起走 | docker-compose.md |
| 境内新域名上线 1~2 天后突然全站打不开：80 被 302 到 dnspod webblock 页、443 TLS 握手后被 RST，同服务器上已备案域名照常通 | 腾讯云对未备案域名的境内入口拦截，与部署/容器/证书无关（用同 IP 另一域名对照即可定位）；解法只有办备案，或经用户确认后临时换已备案子域名过渡 | 私有部署单 deploy-llm-price-monitor.md |

## 6. 工程习惯（踩出来的规矩）

- 静默兜底是坑：吞异常、回落默认值会掩盖真问题 → 删静默兜底、吞异常点补日志（提交 843bcd9 / f2346a1）。
- 提交信息中文讲清"改了什么、为什么"，不写 `fix`/`update` 空话。
- 测试用 HTTPX mock transport / 进程内替身，不请求真实站点；改口径前先跑全量。
- 文案说人话、给下一步动作，不出现"接口/字段/渲染"这类实现词。

## 维护方式

踩新坑收尾时追加到对应分类：一行坑 + 一行方案 + 指针（文档/提交号/记忆名）。跨会话必须记住的硬约束升级写进 Serena 记忆。
