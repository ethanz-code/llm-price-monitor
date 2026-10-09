/**
 * 站点文案集中地：面向用户的文字都放在这里，想改措辞只改这个文件。
 * 按页面/区域分组；改完保存即可生效，不涉及任何逻辑。
 * 管理面板（/admin）内部的提示文案暂未抽离。
 */

/** 品牌与站点级描述（浏览器标签、搜索结果、分享摘要） */
export const site = {
  name: "LLM 价格监控",
  title: "LLM 价格监控 — 中转站价格逐条可溯源",
  description:
    "哪个中转站价格更低、服务更稳？每条数据都附来源链接，点开就能核对。",
};

/** 顶部导航 */
export const nav = {
  items: [
    { key: "/", label: "首页" },
    { key: "/overview", label: "中转站定价" },
    { key: "/calculator", label: "花费计算" },
    { key: "/catalog", label: "厂商定价" },
    { key: "/history", label: "事件追踪" },
  ],
  adminLabel: "工作台",
  theme: {
    light: { label: "浅色", title: "浅色模式" },
    dark: { label: "深色", title: "深色模式" },
    system: { label: "系统", title: "跟随系统" },
  },
};

/** 首页 */
export const home = {
  /** Hero 大标题打字机逐行打出 */
  typeLines: ["中转站", "集成式检测平台"],
  heroSub:
    "自己用的中转站，是不是时不时就不能用？想找个靠谱的，先来对照各家价格和渠道状态。平台不偏向任何中转站，使用需谨慎，Token 少充值。",
  heroButtons: {
    primary: "进入中转站定价 →",
    secondary: "算一笔花费 →",
  },
  intro:
    "这是一个自动化的中转站监测面板：定时抓取各站点的模型价格、公告和渠道可用性，每条数据都附来源链接。不推荐、不评分，只做对照。",
  sections: {
    latestPrice: "最新价格",
    trend: "价格走势",
    events: "最新事件",
    sites: "监控中的站点",
    dataSource: "数据从哪来",
    faq: "常见问题",
  },
  sectionSubs: {
    latestPrice: "站点标多少记多少，每条价格都附来源链接，点开就能核对",
    sites: "每个站点的渠道检测与公告都自动存档，点站点名进检测档案",
  },
  viewAll: "查看全部 →",
  viewAllEvents: "全部事件 →",
  submitSite: "提交监控站点",
  submitSiteDesc:
    "填写站点地址即可申请加入监控清单，我们会逐个核验后接入。",
  dataPoints: [
    "定时抓取各站点公开的模型价目页，标价原样记录",
    "渠道可用性由后台自动探测，正常与异常都有存档",
    "站点公告变化实时留档，方便回溯“之前说过什么”",
    "每条数据都能点回来源页面，自行核对",
  ],
  empty: {
    events: "还没有事件记录。",
    sites: "还没有站点数据。",
    siteNotice: "暂无公告",
    siteNoCheckRecord: "暂无渠道检测记录",
    siteCheckNotEnabled: "渠道检测未接入",
    siteDisabled: "已停用",
  },
  /** 站点卡片：近 N 次渠道检测 · 最新正常 X% */
  siteCard: {
    checkSuffix: "次渠道检测 · 最新正常",
    latencySuffix: "延迟",
  },
  faq: [
    {
      q: "价格数据准确吗？",
      a: "每条价格都附来源链接，点开就能看到站点当前标价；我们只记录，不修改。",
    },
    {
      q: "多久更新一次？",
      a: "价格和公告由内置调度定时抓取，渠道状态几分钟探测一轮；历史记录全部留档可查。",
    },
    {
      q: "会推荐用哪个中转站吗？",
      a: "不会。这里只提供价格、公告和可用性的对照数据，选哪家由你自己判断。",
    },
    {
      q: "标价就是我要付的钱吗？",
      a: "不一定。价格表里的单价是站点公开标价，实际账单还受缓存命中率和各家计费口径影响，可能和按标价算出来的数字有不小出入。建议先小额充值试跑，确认账单符合预期再加量。",
    },
    {
      q: "想把我的站点加进监控怎么办？",
      a: "点首页「提交监控站点」，填站点地址即可申请，我们会逐个核验后接入。",
    },
  ],
  ctaTitle: "选中转站，先看数据。",
};

/** 花费计算页 */
export const calculator = {
  source: {
    official: "厂商官方价",
    site: "中转站价",
  },
  siteLabel: "选站点",
  sitePlaceholder: "先选一个中转站",
  modelPlaceholder: "搜索或选一个模型",
  buckets: {
    input: "输入",
    output: "输出",
    cacheRead: "缓存命中",
    cacheWrite: "缓存存储",
  },
  priceTitle: "单价 · 每百万 token",
  usageTotal: "用量",
  /** 总用量下拉框的字段标签 */
  usageSelect: "总用量",
  /** 总用量快捷档位标签，与 lib/calculator.ts 的 TOKEN_PRESET_VALUES 按序对应 */
  tokenPresets: ["10M（一千万）", "100M（一亿）", "1B（十亿）"],
  hitRate: "缓存命中率",
  usageHint: "输入按 99.2% · 输出按 0.8% 拆分：命中部分按缓存命中价，未命中按输入价。",
  resultTitle: "花费结果",
  totalLabel: "合计",
  emptyResult: "填好单价和用量，这里就会显示花费。",
  missingCache: "没查到缓存命中单价，命中的 token 没计入总价；知道单价的话在上面补一格。",
  detail: {
    bucket: "计费项",
    unitPrice: "单价",
    tokens: "用量",
    subtotal: "小计",
    share: "占比",
  },
  copyLink: "复制分享链接",
  copied: "链接已复制，发给别人看到的就是同一套算法。",
  loadFailed: {
    title: "暂时读不到价格数据",
    fix: "计算器要先有厂商定价或最新价格快照：稍后刷新重试；管理员可在管理后台「采集任务」页点击「刷新厂商定价」。",
  },
};

/** 页面加载失败的提示条（SiteAlert） */
export const alerts = {
  loadData: {
    title: "暂时读不到监控数据",
    fix: "请稍后刷新重试；若持续出现，欢迎通过页脚「提建议」告诉我们。",
  },
  catalog: {
    title: "无法读取厂商定价",
    fix: "请稍后刷新重试；若持续出现，欢迎通过页脚「提建议」告诉我们。",
  },
};

/** 页脚 */
export const footer = {
  brandLine:
    "盯着各家 API 中转站的价格、折扣、渠道状态和公告，数据抓取自各站点公开页面，仅供研究参考，不构成对任何站点的使用推荐。",
  links: {
    catalog: "厂商定价",
    feedback: "提建议",
    admin: "管理",
  },
  aria: {
    github: "GitHub 仓库",
    mail: "邮件联系",
    wecom: "企业微信联系",
  },
};

/** 分享图 */
export const share = {
  /** 分享图品牌头上的简介 */
  brandDesc:
    "哪个中转站价格更低、服务更稳？每条数据都附来源链接，点开就能核对。",
};
