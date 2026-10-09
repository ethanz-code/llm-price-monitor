# 站点导入与配置手册

> 回答三个问题：站点怎么进来、进来后怎么配、配什么不配什么（渠道筛选口径）。字段级技术细节见 [pricing.md](pricing.md)「配置文件」；动手前先翻 [lessons.md](lessons.md) 触发词索引与 Serena 记忆 `dom_url_auth_constraint`。

## 导入站点的三条路

都在管理台 `/admin/sites` 页面：

| 入口 | 适用场景 | 流程 |
| --- | --- | --- |
| 从新站发现导入 | 有候选清单时首选 | 「导入站点」弹窗 → 从新站发现导入，勾选自动收录的候选站点一键导入。发现清单来自 `price-discover`（公开聚合源拉候选 + 探测可导入性），公开页 `/discover` 也能浏览 |
| 手工新增站点 | 已知站点与接口地址 | 「新增站点」→ 最少填 id + 价格接口地址（`network.url`）→ 保存 |
| 从文件导入 | 整份迁移 / 备份恢复 | 「导入站点」→ 从文件导入，上传站点配置 JSON，同名站点逐个确认冲突；「导出站点」反向导出（**含登录凭证**，文件妥善保管）|

「批量管理」显示勾选列，可批量删除、只导出所选。保存即写 SQLite `var/monitor.db` 立即生效；`config/default-seed.json` 只在空库首次启动时作种子导入，日常配置一律走管理面板。

## 编辑站点怎么下手

站点行的「编辑」进编辑弹窗：主弹窗管基本信息与采集地址，子弹窗按需进（认证与续签 / 网页模式 / 倍率 / 渠道状态 / 公告 / 高级 JSON），子弹窗写回草稿、主弹窗统一保存。常见场景对照：

- 接口要登录 → 「认证与续签」（auth_token / auth_inject / token_refresh；new-api 会话模板按采集地址自动带出，贴 Refresh Token 后点「测试续签」验证）
- 页面是 JS 渲染的空壳 → 「网页模式」（`network.headless`，无头浏览器渲染后再解析）
- 要盯渠道可用性、站内公告 → 「渠道状态」「公告」各自子弹窗，new-api 系默认地址自动推断
- 采集被墙/超时 → 「系统设置 → 采集出口」配备用代理

**模型清单不分站点**：站点级 `models` 字段已废弃（加载时静默丢弃），监控哪些模型由页面顶部「检测模型」统一配置（`settings.monitor_models`），所有站点共用一份。目录刷新自动补各厂商最新发布的模型、发布超 3 个月的自动移出、手动删过的不回加；通配符 `*` 全量采集已移除，只收明确清单。站点没有清单里的模型只是提示「没采到价」，不是故障。

## 渠道筛选口径（只采官方共享渠道）

监控对象是**站点自己对外售卖的共享渠道模型**——普通访客充钱就能用、按站点标价计费的那些。三类渠道区别对待：

| 渠道类型 | 采不采 | 识别特征 |
| --- | --- | --- |
| 站点自营 / 官方共享 | ✅ 采 | new-api 系常规价格接口默认就是自营渠道；市场型站点选官方共享订阅源（如 subrouter 的 `source=shared_subscription`）|
| 用户上传 key 的经销商渠道 | ❌ 不采 | subrouter 的 `source=self`；价格是上传者自定，不代表站点官方售价 |
| 挂官方名但无实际供货的空壳 | ❌ 不采 | subrouter 的 `source=official`（official-channels 页）；特征：已供货模型 0（如 0/32）、可用 Key 0、供货商家 0、最近最低价/最高允许价「不限 · 不限」|

落点在**采集地址**，不是模型清单：接口自带渠道过滤参数的，把参数固化进 `network.url`（subrouter 现配置即范例：`/api/marketplace/shared-models?…&source=shared_subscription`）；接口不支持过滤的市场站，只采官方渠道确实在售、且在检测模型清单里的模型，市场渠道独占的模型不加进清单。

## 公告与渠道状态怎么配（与价格同周期顺带采集）

公告和渠道状态跟着同一次采集顺带执行，配站点时**两样都尽量配上**。字段细节见 [pricing.md](pricing.md)，这里讲「什么时候配什么」。

**公告（notice）**：

- new-api 系零配置也能跑：未配 `notice.url` 时自动从 `network.url` 根地址推导 `/api/notice`，并自动请求 `/api/status` 把后台「公告管理」发布的多条 announcements 拼成分节 Markdown 合并进正文。
- **配法约定（2026-10-08 拍板）：new-api 系公告地址一律显式填 `/api/notice`**——解析层会自动把 `/api/status` 的 announcements 拼进正文（实测 `parse=json+status`），手写正文与公告板两条都拿全；显式配 `/api/status` 只拿公告板、会丢 `/api/notice` 手写正文，别用。零配置虽等价于显式 `/api/notice`，但按「填什么抓什么」原则统一显式写。
- 其余显式场景：① 标准路径不存在或自研路径 → 配实际公告接口（`data` 直接是公告数组、`announcements` 挂顶层这两种形态都能解析）；② 公告接口要登录 → 显式配 + 认证（`auth_inject` 的 notice 腿或 `notice.headers`）。
- 正文变化才存新版本发事件，正文为空不入库；显式配置的地址 404 是配置错误会报错，自动推导地址 404 视为站点没有公告接口、静默跳过。

**渠道状态（status）**：

- **没有自动推导，必须显式配 `status.url`**。new-api 系常见候选：`/api/model_status/groups`（渠道分组状态，多要登录态 + `New-Api-User` 头）、公开性能汇总接口（如 `/api/perf-metrics/summary?hours=24`）；自研站找 channel-monitors / channel-status 类端点。
- 解析三层：结构化 JSON 直接采用 → HTML/文本提取内嵌 JSON → AI 兜底；数据原样存（不强制归一 schema），路径级结构 diff 生成状态变化事件（滚动时间线字段自动跳过防噪音）。选地址时优先找直接返回 JSON 的接口。
- `status.groups` 可只保留指定分组；401/403 按需认证处理，走 `auth_inject` 的 status 腿。

**认证怎么选**（价格/状态/公告三处共用一套逻辑）：

1. 匿名能通就不配认证——先匿名 curl 试，这是最省心的形态。
2. 固定 token：站点级 `auth_token` + `auth_inject` 写清三处注入规则。
3. 短效会话：`token_refresh` 自动续签（new-api 会话系配 `refresh_cookie_name: new_api_refresh`，自研站配对应刷新端点）。
4. 登录态 Cookie 与特殊头（`New-Api-User`、`Referer`）放**站点级 `request_headers`**（站点对象顶层字段）让三处共享——注意不是 `network.headers`，后者只有公告腿显式读取，状态/价格腿不认（2026-10-07 A/B 实测）；只有个别腿需要时才写在那个腿的 `headers` 里。

## AI 浏览器代办 SOP（替用户看站 + 配置）

工具优先级与全局规范一致：黑盒走查用 Browser Use 插件（`browser-use:web-gui-tester` 方法论 + `browser-use:control-browser` 操作，支持 localhost）；需要找接口地址、看网络请求/响应体时用 Chrome DevTools MCP；纯截图核对用 Computer Use。主 Agent 亲自执行，不委托 sub-agent。

流程：

1. **看站类型**：打开站点，判断是 new-api/one-api 系（标配 `/api/pricing`、`/api/status`、`/api/notice`）还是自研接口（如 subrouter 的 marketplace API）。**动手前先学站点自己的文档**（2026-10-08 约定）：站内文档页、页脚开源署名对应的 GitHub 仓库、API 文档都看一眼再下判断——sub2api 系读它的仓库文档，new-api 系对照官方源码，不靠猜。**别用一个接口定生死**（2026-10-08 用户要求）：`/api/pricing` 404 不等于站死——把导航、模型广场、渠道、订阅、公告各页面都翻一遍再归类，页面 UI 同款模板（如 sub2api 系的 EN/暗色切换壳）直接对照同族站找接口。
2. **甄别渠道**：按上表口径过一遍模型页/渠道页，确认官方共享渠道的过滤参数或专用接口；空壳渠道（供货统计全 0、价格「不限」）直接排除。
3. **确认价格接口**：优先公开 pricing 接口；拿不准时看网络面板（DevTools MCP）找返回模型+价格的请求。返回 HTML 的页面地址必须免登录——httpx 直采没有浏览器环境（详见 `dom_url_auth_constraint`）。顺带确认公告与渠道状态接口（new-api 系候选：`/api/notice`、`/api/status`、`/api/model_status/groups`；渠道分组接口不少部署没有，公开性能汇总 `/api/perf-metrics/summary?hours=24` 是常见替代，两者都匿名可读才配）。sub2api 系端点家族：`/api/v1/model-plaza`（模型广场，匿名性逐站不同）、`/api/v1/channels/available`（可用渠道）、`/api/v1/groups/rates`（分组倍率，多为要登录）、`/api/v1/announcements`；注意「实收 = 官方美元价 × 分组倍率」的 CNY 计费站，倍率表拿不到就别采——采到的只是没乘倍率的官方价（2xapi 教训）。自研美元计价站（「基准价 × 套餐倍率」那种）先想清楚抽取路径：非 new-api 格式走 AI 兜底，抽取不稳就先别开——错价比没价糟（118.ink 教训，lessons.md §2）。
4. **试探认证**：先匿名请求接口；401/403 再配认证。new-api 系注意 `New-Api-User` 头与 Referer 坑（lessons.md §1）。**撞到登录墙（定价/接口要登录才给数据）**：不硬试——每个站单独开一个注册页标签页交给用户注册登录（2026-10-08 与用户跑通的流程），登录后直接从浏览器会话读 localStorage/Cookie 取凭证，按现役范式配（共享登录态走站点级 `request_headers`，短效凭证配 `token_refresh` 自动续签，浏览器存储登录态走 headless 注入）；凭证运行时贴入，不落仓库。
5. **录入配置**：管理台新增/编辑站点，价格、公告、渠道状态三件套配齐（能采就采），填认证；凭证运行时贴入，不落仓库。
6. **自验**：站点行「测试采集」干跑（默认不调 AI、零费用），或 `POST /api/collect` 带 `site_id`；核对采到的模型与价格同站内页面一致、渠道来源符合口径。
7. **收尾沉淀**：新坑进 lessons.md 对应分类，跨会话硬约束写 Serena 记忆。

## 红线

- 凭证（token/Cookie/密码）不写进任何仓库文件；配置里用 `${ENV_VAR}` 引用或运行时贴入；导出文件含凭证，交付时提醒用户保管。
- 目标站点只读浏览：不改对方站点任何数据、不点消耗额度的按钮、不代充值。
- 不替用户启动服务；需要管理台在线时先探测端口（API 8437 / 前端 3000），不在线请用户启动。
