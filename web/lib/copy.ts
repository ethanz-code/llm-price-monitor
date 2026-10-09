/**
 * 站点文案集中地：面向用户的文字都放在这里，想改措辞只改这个文件。
 * 按页面/区域分组；改完保存即可生效，不涉及任何逻辑。
 * 管理面板（/admin）内部的提示文案暂未抽离。
 */

/** 品牌与站点级描述（浏览器标签、搜索结果、分享摘要） */
export const site = {
  /** 品牌展示名即域名，全站统一用英文字体呈现 */
  name: "llmprices.cn",
  title: "llmprices.cn — 中转站价格逐条可溯源",
  description:
    "哪个中转站价格更低、服务更稳？每条数据都附来源链接，点开就能核对。",
};

/** hover 大弹窗内容：一句话说明 + 核心看点 */
export interface NavPopover {
  desc: string;
  points: string[];
}

/** 顶部导航 */
export const nav = {
  items: [
    { key: "/", label: "首页" },
    { key: "/overview", label: "中转站定价" },
    { key: "/calculator", label: "花费计算" },
    { key: "/catalog", label: "厂商定价" },
    { key: "/history", label: "事件追踪" },
    { key: "/discover", label: "新站发现" },
  ],
  adminLabel: "工作台",
  /** 桌面主导航 hover 大弹窗：一句话说明 + 核心看点（全部为真实功能）；没配置的 tab 不出弹窗 */
  popovers: {
    "/": {
      desc: "价格、渠道状态、公告的实时对照，全部逐条可溯源。",
      points: [
        "最新价格：每个模型只留最低价一行",
        "价格走势：低价曲线按小时记录",
        "站点健康：可用率与延迟持续探测",
      ],
    },
    "/overview": {
      desc: "每个模型只展示检测站点里的最低价，每条都附来源链接，点开就能核对。",
      points: [
        "站点检测档案：可用率、延迟逐轮留档",
        "厂商官方价对照，折扣一眼看清",
        "按厂商分组、发布日期排序",
      ],
    },
    "/calculator": {
      desc: "填好单价和用量，按缓存命中率估算这笔花费。",
      points: [
        "输入/输出/缓存命中三档分开算",
        "10M/100M/1B 总用量快捷档位",
        "复制分享链接，打开就是你算的这笔账",
      ],
    },
    "/catalog": {
      desc: "厂商官方定价目录，站点折扣的对照基准。",
      points: [
        "国际价来自 models.dev 开源目录",
        "国内厂商定价页逐家抓取",
        "按发布日期看最新模型",
      ],
    },
    "/history": {
      desc: "价格与公告的变化流，变化全部留档可回查。",
      points: [
        "降价/涨价/新增/公告分类徽章",
        "按站点与模型筛选定位",
        "同模型多分组折叠成一张卡",
      ],
    },
    "/discover": {
      desc: "从多个来源聚合发现新的中转站，补全检测清单。",
      points: [
        "四路来源聚合找站，去重合并",
        "探测可用性后生成可导入清单",
        "自动排除库内已有站点",
      ],
    },
    "/admin": {
      desc: "管理后台：站点、任务与数据运营入口。",
      points: [
        "站点管理：接入与采集配置",
        "采集任务：手动触发与运行状态",
        "定价源、AI 日志与数据分析",
      ],
    },
  } as Record<string, NavPopover>,
  theme: {
    light: { label: "浅色", title: "浅色模式" },
    dark: { label: "深色", title: "深色模式" },
    system: { label: "系统", title: "跟随系统" },
  },
};

/** Bento 小卡图标键：与首页 BENTO_ICONS 映射（components/icons.tsx 的导出名）一一对应，
 *  加新键必须两边同步，类型约束让漏改在编译期报错而不是渲染期崩页面 */
export type BentoIconKey = "sync" | "monitor" | "aim";

/** 首页 */
export const home = {
  /** Hero 大标题静态两行，第二行开头两个字走品牌色（见 components/HeroType.tsx） */
  typeLines: ["中转站", "价格逐条可溯源"],
  heroSub:
    "自己用的中转站，是不是时不时就不能用？想找个靠谱的，先来对照各家价格和渠道状态。我们不偏向任何中转站，只把数据摆给你看；使用需谨慎，Token 少充值。",
  heroButtons: {
    primary: "进入中转站定价 →",
    secondary: "算一笔花费 →",
  },
  sections: {
    latestPrice: "最新价格",
    trend: "价格走势",
    events: "最新事件",
    sites: "检测中的站点",
    rankings: "模型榜单速览",
    faq: "常见问题",
  },
  sectionSubs: {
    latestPrice:
      "每个模型只展示检测站点里的最低价，每条都附来源链接，点开就能核对",
    sites: "每个站点的渠道检测与公告我们都自动存档，点站点名就能进检测档案",
    rankings:
      "第三方评测机构 Artificial Analysis 的智能指数前五名，判断模型能力档位时拿它做参考",
  },
  /** Bento 产品介绍：1 大（主价值 + 真实价格行预览，可点）+ 3 小（图标芯片 + 能力一句话）；
   *  icon 是 components/icons.tsx 的导出名键 */
  introBento: {
    main: {
      title: "每条价格，逐条可溯源",
      desc: "每条价格都附来源链接，点开就是站点当时的价目页，随时自己核对；我们只记录，不修改。",
      cta: "去看最新价格 →",
    },
    items: [
      {
        icon: "sync",
        title: "调价第一时间留档",
        desc: "哪家涨价了、哪家降价了，一变就记成事件，新价旧价、变化时间都在记录里。",
      },
      {
        icon: "monitor",
        title: "渠道自动探测",
        desc: "几分钟一轮自动探测各家渠道，能不能用、延迟多少，正常异常都有记录。",
      },
      {
        icon: "aim",
        title: "折扣一眼看清",
        desc: "厂商官方定价摆在旁边，中转站标价折到几折，不用自己按计算器。",
      },
    ] satisfies { icon: BentoIconKey; title: string; desc: string }[],
  },
  viewAll: "查看全部 →",
  viewAllEvents: "全部事件 →",
  submitSite: "提交站点，加入检测",
  submitSiteDesc: "把站点地址填给我们，逐个核验通过后就开始检测。",
  empty: {
    events: "还没有事件记录。",
    sites: "还没有站点数据。",
    siteNoCheckRecord: "暂无渠道检测记录",
    siteDisabled: "已停用",
  },
  sitesOverview: {
    more: (n: number) => `还有 ${n} 个站点 →`,
  },

  faq: [
    {
      q: "价格数据准确吗？",
      a: "每条价格都附来源链接，点开就能看到站点当前标价；我们只记录，不修改。",
    },
    {
      q: "多久更新一次？",
      a: "价格和公告我们定时去抓，渠道状态几分钟探测一轮；历史记录全部留档，随时可查。",
    },
    {
      q: "会推荐用哪个中转站吗？",
      a: "不会。我们只提供价格、公告和可用性的对照数据，选哪家由你自己判断。",
    },
    {
      q: "标价就是我要付的钱吗？",
      a: "不一定。价格表里的单价是站点公开标价，实际账单还受缓存命中率和各家计费口径影响，可能和按标价算出来的数字有不小出入。建议先小额充值试跑，确认账单符合预期再加量。",
    },
    {
      q: "想让我的站点也被检测怎么办？",
      a: "点首页「提交站点，加入检测」，把站点地址填给我们，核验通过后就接入。",
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
  /** 厂商分组节头的「更多」按钮，括号里是被折叠的模型数 */
  moreModels: (count: number) => `更多 ${count} 个`,
  lessModels: "收起",
  buckets: {
    input: "输入",
    output: "输出",
    cacheRead: "缓存命中",
  },
  priceTitle: "单价 · 每百万 token",
  usageTotal: "用量",
  /** 总用量下拉框的字段标签 */
  usageSelect: "总用量",
  /** 总用量快捷档位标签，与 lib/calculator.ts 的 TOKEN_PRESET_VALUES 按序对应 */
  tokenPresets: ["10M（一千万）", "100M（一亿）", "1B（十亿）"],
  hitRate: "缓存命中率",
  usageHint:
    "输入按 99.2% · 输出按 0.8% 拆分：命中部分按缓存命中价，未命中按输入价。",
  resultTitle: "花费结果",
  totalLabel: "合计",
  emptyResult: "填好单价和用量，这里就会显示花费。",
  missingCache:
    "没查到缓存命中单价，命中的 token 没计入总价；知道单价的话在上面补一格。",
  detail: {
    bucket: "计费项",
    unitPrice: "单价",
    tokens: "用量",
    subtotal: "小计",
    /** 占的是费用（各档小计占总花费），不是 token 用量，列名直接挑明 */
    share: "费用占比",
  },
  copyLink: "复制分享链接",
  copied: "链接已复制，发给别人，打开看到的就是你算的这笔账。",
  loadFailed: {
    title: "暂时读不到价格数据",
    fix: "计算器还没拿到厂商定价或最新价格：请稍后刷新重试；管理员可在后台「采集任务」页点「刷新厂商定价」。",
  },
};

/** 页面加载失败的提示条（SiteAlert） */
export const alerts = {
  loadData: {
    title: "暂时读不到数据",
    fix: "请稍后刷新重试；若持续出现，欢迎通过页脚「提建议」告诉我们。",
  },
  catalog: {
    title: "无法读取厂商定价",
    fix: "请稍后刷新重试；若持续出现，欢迎通过页脚「提建议」告诉我们。",
  },
  rankings: {
    title: "暂时读不到模型榜单",
    fix: "请稍后刷新重试；若持续出现，欢迎通过页脚「提建议」告诉我们。",
  },
  discovery: {
    title: "新站清单还没生成",
    fix: "管理员在服务器上执行 price-discover harvest + probe 后，这里会列出发现的中转站。",
  },
};

/** 页脚 */
export const footer = {
  brandLine:
    "我们检测各家 API 中转站的价格、渠道状态和公告，数据都抓自各站点公开页面，仅供使用参考，不构成任何使用推荐。\n最后祝大家 Vibe Coding 之路畅通无阻，永远用到低价不降智模型，天天 Happy.",
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
