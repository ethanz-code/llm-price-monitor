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
    "哪个中转站价格更优惠，服务状态更好？这里都能看哦，快来了解一下吧～",
};

/** 顶部导航 */
export const nav = {
  items: [
    { key: "/", label: "首页" },
    { key: "/overview", label: "中转站定价" },
    { key: "/history", label: "历史与事件" },
    { key: "/catalog", label: "厂商定价" },
    { key: "/discount", label: "折扣对比" },
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
    secondary: "查看折扣对比",
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
    "站点提报功能即将上线，届时填写站点地址即可申请加入监控清单，我们会逐个核验后接入。",
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
      q: "想把我的站点加进监控怎么办？",
      a: "站点提报功能即将上线，届时填写站点地址即可申请，我们会逐个核验后接入。",
    },
  ],
  ctaTitle: "选中转站，先看数据。",
};

/** 各内容页顶部的副标题 */
export const subtitles = {
  overview:
    "各中转站最新的模型单价，折扣为站点价相对厂商原价的比值——越低越便宜。",
  discount:
    "站点价折算 CNY 后与厂商原价相除：比值 19% 即“1.9 折”，越低越便宜。",
  catalog:
    "数据来自开源模型目录 models.dev，价格统一为美元并按快照汇率换算成人民币，是折扣对比的基准；每条价格都能点开来源核对。切到「全量渠道」可看 OpenRouter 等全部渠道的价格。",
  history:
    "价格变化、分组下线与站点公告都会留档在这里；趋势图每次采集记一个点，保留近 90 天。",
  siteStatus:
    "这个中转站最近 7 天的检测档案：可用渠道占比趋势、各渠道当前状态与站点公告；绿色为正常、灰色为异常或未知。",
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
  discount: {
    title: "无法计算折扣",
    fix: "折扣对比需要先有厂商定价和最新价格快照：管理员可在管理后台「采集任务」页点击「刷新厂商定价」。",
  },
};

/** 页脚 */
export const footer = {
  brandLine:
    "盯着各家 API 中转站的价格、折扣、渠道状态和公告，数据抓取自各站点公开页面，仅供研究参考，不构成对任何站点的使用推荐。",
  links: {
    discount: "折扣对比",
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
    "哪个中转站价格更优惠，服务状态更好？这里都能看哦，快来了解一下吧～",
};
