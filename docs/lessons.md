# 项目经验速查（踩坑 → 方案索引）

动手前先翻这页，历史上踩过的坑不重踩。细节看指针：部署排障见 [deploy/ai-runbook.md](deploy/ai-runbook.md)，采集与价格口径见 [pricing.md](pricing.md)，UI 规范见 [design-guide.md](design-guide.md)，认证/反爬实测见 Serena 记忆 `dom_url_auth_constraint`。

## 触发词索引（命中先查，再动手）

| 途中见到这些词 | 先看 |
| --- | --- |
| 401 / 403 / WAF / Cookie / Cloudflare / 认证 / 登录态 | 本页 §1 + Serena 记忆 `dom_url_auth_constraint` |
| 代理 / 出口 / Clash / 境外站 / 超时 / TLS 掐断 | 本页 §1 |
| AI 输出截断 / max_tokens / 助手 token 爆炸 | 本页 §2 |
| 价格 / 基准价 / 官方目录 / 排序 / 变更事件 / 假变更 / ¥0 / 假免费 / 最低价 / 旧价不更新 | 本页 §3 |
| 骨架屏 / 暗色 / 表格 / 窄屏 / 响应式 | 本页 §4 |
| panic / char boundary / code-frame / dev 半死 / 改文件不生效 | 本页 §4 |
| Docker / 构建 / 回滚 / APP_TAG / 端口 | 本页 §5 |
| 网站打不开 / 域名被拦 / webblock / 302 跳备案页 | 本页 §5 |

## 1. 采集与网络（坑最多）

| 踩过的坑 | 已落地的方案 | 指针 |
| --- | --- | --- |
| headless 渲染出登录墙（DaiTuAI 缺 `auth_user` 跳登录页），据此断定"价格采不到/吃老本"——实际价格全在前端打包 JS（不需要登录），采集靠引用链爬 JS 照常拿到，登录态对该站结果零影响；AI 抽取有随机性，把条数波动归因给修复纯属错判 | 纯壳 SPA 站先确认数据源在渲染页面还是打包 JS 里再谈登录态影响；归因差异要用受控对比，别拿单次 AI 输出当证据；补登录态无害可留（防站点改成接口动态下发价） | 提交见 git log |
| AI 对证据里没有的模型照样编价格（notes 自认"证据未出现具体数值，仅通过 JS 结构推断"仍给出具体数字），旧代码发现模型名不在证据只降状态不清价，幻觉价以 candidate 身份混进快照、再被合理性校验每轮作废刷异常卡片 | `ai.py _records` 里模型名不在证据 → 价格与 pricing_rules 一并清空（幻觉价无从落地）；异常卡片再出现"价格异常作废"先查该站页面是否真有此模型 | 提交见 git log |
| AI 张冠李戴：给页面上不存在的模型安上别的模型的真价（DaiTuAI 把 gpt-5.4-mini 的 ¥0.11/¥0.68 安给 step-3.5-flash，还过了 confirmed 校验）；模型名匹配也会被残留文案误判（已下线 Kimi 分组的 i18n 描述仍写着 kimi-k3，名字在证据但价格不存在） | 名字在证据 + 价格数字在证据双闸（数字以系统侧证据原文为准，AI 事后补写的引用不算自证）；数字形态补 JS 省前导零写法（.7）避免误杀；本轮没采到的价由快照沿用机制兜底，页面价格不闪没 | 提交见 git log |
| 传输错误正则漏了 DNS 解析失败（`[Errno 8] nodename nor servname` 不含 connection/timeout 字样），本机网络瞬断的 warn 刷进异常卡片 | `_TRANSPORT_ERROR_RE` 补 nodename/getaddrinfo/name or service not known | 提交见 git log |
| httpx 直采没有浏览器环境，DOM 页必须免登录；认证要显式配 headers/token_refresh | 无头浏览器是例外分支（`network.headless.enabled`），注入 Cookie/localStorage 后渲染再解析 | Serena 记忆；`browser_fetch.py` |
| new-api 系站点 401：光有 Cookie 不够，还要 `New-Api-User` 头；Cookie 只挂公告 headers 时全站采不到 | Cookie 提升到站点级 `network.headers` 共享 | Serena 记忆 |
| Cloudflare WAF 403：多为缺 `Referer`/`Origin` 头，补齐后匿名也能过；别只报"可能需要认证" | 站点级 request_headers 配齐 Referer+Origin+Accept，看响应体里的真实 message | Serena 记忆 |
| 采集客户端被 shell 环境变量代理劫持（Clash env） | `build_client` 显式传 transport；采集出口语义统一为"直连优先、代理兜底" | 提交 843bcd9 / 088c631 |
| 境内服务器采不到国外站 | mihomo 兜底代理 sidecar（吃机场订阅），profile 默认不启、`.env` 一行启用；mihomo 必须 `allow-lan`；代理自带 healthcheck | docker-compose.md「采集兜底代理」 |
| 对端间歇掐断 TLS，整轮数据丢失 | GET/HEAD 退避重试 2 次（1s/2s） | 提交 6adf0b0 |
| 采集卡死拖垮整轮 | 每站采完立即落库 + 单站硬超时 | 提交 9f2fb35 |
| 硬超时只盖了价格腿：new-api 站测试采集必跑 AI 模型名解析（每 4 模型一批 × 每批 14~31s，cun 实测单站 328s），成功路径零日志像卡死；渠道状态/公告腿连硬超时都没有，挂死任务永不结束 | AI 抽取逐批留痕；状态/公告单站请求套 `_run_site_fetch` 同款硬上限；交互弹窗的轮询上限必须 ≥ 后端最坏路径时长（5 分钟上限对着 5.5 分钟的任务，成功被误报成超时） | 提交见 git log |
| AI 抽取缓存永不命中：new-api 系接口每次请求把模型数组/分组列表重新洗牌、还往随机模型上挂随机哈希字段（cun 两次响应字节数相同、顺序不同），证据原样进哈希导致键每轮全变，每轮采集都全额重跑全部批次 | 缓存键先做顺序规范化（`canonical_cache_evidence`：dict/数组按内容排序、整数值浮点归一、剔除 `pricing_version` 噪声键），只影响键不改发给 AI 的证据；排查这类问题先对同一接口连打两次逐字段 diff | 提交见 git log |
| 站点模型名省略版本号对不上，漏采（DeepSeek 案例） | 模型名一侧省略版本号也能匹配，歧义有处理规则 | 提交 3c383d1；pricing.md |
| "整站没价"被记成错误，用户看不到原因 | 401 这类"没价但不算错误"的原因直接报给用户 | 提交 40124fe |
| 想采站点全部模型逐个列太累 | models 配置 `["*"]` 通配符全量采集，不可与其他模型混列 | 提交 3459300 |
| 首页归属地查询逐 IP 打 ip-api 免费接口（45 次/分），失败站点每轮重查把自己打进限流，9 站只剩 1 颗星 | 改 `/batch` 批量接口一轮一请求（≤100 IP/请求，响应按请求顺序对位）；传输层失败记 2 分钟短退避，单 IP 明确失败仍 30 分钟负缓存 | 提交 f0de495 |

## 2. AI 抽取与助手

| 踩过的坑 | 已落地的方案 | 指针 |
| --- | --- | --- |
| 输出被模型 max_tokens 掐断 | 学到的上限落库预载、过小模型自动跳过；固定 `ai.price_model` 起始档；单段预算 16000 | 提交 274e5fa / 75fd18f / e371c5b |
| prompt 太长挤爆输出 | 简介压 40 字内再进 prompt | 提交 8452ccc |
| 换模型回退逻辑三处拷贝，改一处漏两处 | 合并为一套 helper | 提交 eccba2d |
| 供应商报错天书，思考受限模型直接失败 | 错误可读化 + 自动换参重试 | 提交 3387d6a |
| 助手单次提问 token 爆炸 | 数据摘要瘦身，消耗降约 88% | 提交 d2f626e |
| 提示词模板示例值会被 AI 照抄：输出结构示例写 `"input_price": 0`，AI 抽不到价就把 0 抄进结果，而 "0" 字符几乎总在证据文本里，数字在证闸门拦不住；0/0 占位行以 candidate 落库后被前端最低价挑选选中，首页把无数据模型渲染成 ¥0 假免费价（DaiTuAI grok-4.7 等 10 条实测） | 模板示例值一律写 null 不写 0；AI 自报 unavailable 时在状态推导链之前强制清空价格/缓存价/pricing_rules；写 prompt 示例时先问"这个值被照抄了会怎样" | extractor.py unavailable 清价分支 |
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
| 厂商页「输入（命中缓存）/未命中/输出」三列表被 AI 抽错位：缓存命中价当成输入价（小米 mimo 官方价错 100 倍，站点折扣全被算成 40–60 倍，用户点名）；证据校验又被"0.036 包含 0.03"子串误匹配放行 | prompt 明确列对号规则（命中缓存进 cache_read、输入必取未命中列）+ 拆行规则；合并目录时加与既有基准的 0.01–20 倍偏差闸门（candidate 基准不受保护，留纠错通道） | ai_fallback.py `_AI_SYSTEM_PROMPT`；vendor_sources.py `_source_deviation_violation` |
| 站点价合理性校验一轮就作废：官方价目录自身带错时真数据被误标"无数据"（AIHub365 案，用户点名） | 改两轮确认：首轮异常只挂 `metadata.sanity_suspect` 标记、价格照常展示，下轮复现才作废；作废不沿用可疑旧价 | pricing.py `_apply_price_sanity`；scans.py 作废分支 |
| 0 被当成有效价贯穿全链路：`_has_price` 只判 `is not None`，0/0 双零占位行入库绕过"无价行不进快照"设计；前端 `hasUsablePrice`/最低价挑选同样不防 0，¥0 假免费价上首页。**判"有没有价"必须带 price_status 语义：0/0 只有 confirmed（真免费档）算数**；且旧价不能无限沿用——连续 3 轮无数据（响应缺席或解析不出价）就从快照摘除旧价发 group_removed（用户点名要实时，需认证/采集失败的整轮除外，那是我方问题） | pricing.py `_has_price`；scans.py `GROUP_REMOVED_MISSES = 3`；web/lib/priceRows.ts `hasUsablePrice`；存量 0/0 行由落库层无价行清扫自动摘除，无需手工清库 |

## 4. 前端

| 踩过的坑 | 已落地的方案 | 指针 |
| --- | --- | --- |
| Next 16.3.3 dev 错误浮层渲染 code-frame 时 Rust panic（`next-code-frame highlight.rs: not a char boundary`，切进中文字符中间），线程 abort 后 dev server 半死：HTTP 还答旧内容、改任何文件都不生效，且对终端宽度敏感 | 已知上游 bug（[vercel/next.js#92641](https://github.com/vercel/next.js/issues/92641)，Open）。panic 只是"报错的展示方式"崩了：`npm run build` 绿就说明代码本身没错，触发错误多为 HMR 增量状态损坏；重启 dev 即愈，重启时再遇同款 panic 可忽略一次。另：文章 md 文件不在模块依赖图，读取必须走每请求执行的 `getArticles()`，模块级求值会 dev 改 md 不生效 | issue 链接；web/lib/articles.ts 头注释 |
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
| 页面带自动轮播顶部横幅时坐标点击持续打偏（横幅展开/收起让全页元素 y 坐标漂移 ±70px，点 A 变点 B 还可能触发离开确认弹窗），且 Playwright 定位点击照卡 actionability | 最稳解是 playwright.evaluate 里按按钮文本精确匹配后直接调 DOM click()（React 组件也能触发）；受控组件赋值用原生 value setter + dispatch input/change 事件 | 腾讯云备案表单代填实测 |
| 同一事件流多个页面各画各的：首页不折叠、追踪页折叠，站点名一边美化名一边原始 site_id，用户并排一看以为数据错乱 | 多页面共用数据源的展示口径（折叠/命名/排序）抽到 web/lib 单处共享，改口径只能改一处 | 提交 1e7ec68 |
| 常驻动画页走查深水区：IAB 截图通道跑一阵后整体卡死（surface preparation timed out / capture failed for guest），force click 也被拖超时；fullPage 整页截图对 Reveal 懒显页全空白（下方 opacity:0 不触发） | 读 DOM 的 evaluate 始终可用；截图降级 Chrome DevTools MCP（独立实例互不拖累）；懒显页逐段滚动触发后再截视口图；受控下拉（如主题菜单）点不开时按「环境准备」预置 localStorage 再 reload | 首页叙事三区走查实测 |
| 用 python 按行号替换大 CSS 块，旧行号在多次编辑后失效，一刀把 globals.css 砍掉 3300 行（hero 平板列/导航汉堡/动画全套全没），390px 出现横向溢出才暴露 | 大文件删改必须用「锚定内容」定位（str.index 断言锚点存在），替换后立刻 `wc -l` 对账 + grep 被删类名确认零残留；走查见溢出先用 `git stash` 对照基线定位是否新引入 | 首页站点区 Statuspage 化实测 |
| 参考站只抓文字结构不截页面，做出来的「同构」设计全是小灰字（被用户打回两次） | 参考站必须真开浏览器逐屏截图，量标题字号、卡片 padding、图标做法、分段节奏再动手；监控站点列表直接抄行业事实标准 Atlassian Statuspage（状态横幅 + 一行一组件 + 90 天可用率条），不要自创瓷贴 | 首页重设计返工实录 |
| grid 子项超宽（全出血大图）把隐式轨道撑大，`justify-self: center` 的居中/两侧出血全失效（居中发生在被撑大的轨道里）；子项的 `margin-inline: auto` 又会压过 justify-self 让它贴左 | 轨道显式 `grid-template-columns: minmax(0, 1fr)` 锁到容器宽，子项清零 auto margin，居中溢出交给 grid | 首页星空 hero 改版实测 |
| 组件修饰类用裸常用词（如 `empty`）静默继承全局同名工具类——globals.css 里有全局 `.empty { padding: 36px 0 }`，flex 子项被撑高 72px 溢出卡片，视觉上"多出几根悬挂灰条" | 修饰类避开裸常用词（改 `is-empty`）；或像 `.uptime-slot` 那样显式声明 `padding: 0` 压制。新增类名前先 grep globals.css 是否已有同名/同名单类 | 首页站点墙 uptime 色条实测 |

## 5. 部署与运维

| 踩过的坑 | 已落地的方案 | 指针 |
| --- | --- | --- |
| Dockerfile RUN 找不到 `.venv` 命令（exit 127） | `ENV PATH` 必须放在 `playwright install` 之前 | 提交 a61bffd |
| 大陆服务器构建失败 | 专项排障章节 | ai-runbook.md §11 |
| 更新/回滚不知道当前是哪版 | APP_TAG 镜像标签即线上版本号，改 `.env` 即切版本；数据在 var/ 不动 | docker-compose.md 日常运维 |
| 端口冲突 | API 端口固定 8437（前端 3000） | 提交 0e98ea3 |
| 备份漏密钥 | SQLite 在线备份；密钥在数据库里随 var/ 一起走 | docker-compose.md |
| 境内新域名上线 1~2 天后突然全站打不开：80 被 302 到 dnspod webblock 页、443 TLS 握手后被 RST，同服务器上已备案域名照常通 | 腾讯云对未备案域名的境内入口拦截，与部署/容器/证书无关（用同 IP 另一域名对照即可定位）；解法只有办备案，或经用户确认后临时换已备案子域名过渡 | 私有部署单 deploy-llmprices.cn.md |

## 6. 工程习惯（踩出来的规矩）

- 静默兜底是坑：吞异常、回落默认值会掩盖真问题 → 删静默兜底、吞异常点补日志（提交 843bcd9 / f2346a1）。
- 默认路径不藏重活：默认动作（刷新/保存/打开页面）要秒级返回，逐个请求几千候选这类重活只放显式入口（CLI/单独按钮）；进度条救不了语义错误的默认行为，用户点破「我只是要清单」后先改语义再谈体验。
- 提交信息中文讲清"改了什么、为什么"，不写 `fix`/`update` 空话。
- 测试用 HTTPX mock transport / 进程内替身，不请求真实站点；改口径前先跑全量。
- 测试里经 create_app 放行任何 >0 的调度间隔（含按键回填补出来的新键）会被调度线程首轮 `_run_due` 真提交并执行——真实外网请求（discovery 实测打了 zuiquanapi 4339 条，还泄漏进后续测试的断言）；这类场景直测纯函数（如 `_ensure_schedule_defaults`），别拉起整个应用。
- 文案说人话、给下一步动作，不出现"接口/字段/渲染"这类实现词。
- 并行会话同仓干活时，共享大文件（如 globals.css）的未提交改动会被对方的主题提交卷走：主题无关的布局改动改完即小步提交，别攒。

## 维护方式

踩新坑收尾时追加到对应分类：一行坑 + 一行方案 + 指针（文档/提交号/记忆名）。跨会话必须记住的硬约束升级写进 Serena 记忆。
