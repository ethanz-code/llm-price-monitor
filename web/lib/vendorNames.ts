/** 厂商显示名映射：目录数据里的厂商名保持英文原样（数据键、接口、测试都不动），
 *  界面展示统一换成用户熟悉的品牌名，未收录的厂商回落原始名。
 *  键为目录厂商名的小写形式；新厂商进目录时在这里补一行。 */
const VENDOR_DISPLAY: Record<string, string> = {
  "alibaba cloud": "通义千问 Qwen",
  "01.ai": "零一万物",
  baichuan: "百川智能",
  baidu: "百度 文心",
  iflytek: "讯飞星火",
  "moonshot ai": "月之暗面 Kimi",
  sensenova: "商汤日日新",
  siliconflow: "硅基流动",
  stepfun: "阶跃星辰",
  tencent: "腾讯混元",
  "volcengine ark": "火山方舟 豆包",
  xiaomi: "小米 MiMo",
  "zhipu ai": "智谱 GLM",
};

export function vendorDisplay(vendor: string): string {
  return VENDOR_DISPLAY[vendor.trim().toLowerCase()] ?? vendor;
}
