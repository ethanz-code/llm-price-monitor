import { DajuPeek } from "./DajuArt";

/** 品牌 Logo：探头的橘猫——奶油暖白圆角方块底（--logo-bg，与 favicon 同底同款），橘猫从下缘探出头。
 *  形象与助手悬浮球同源（DajuPeek circle，橙圆底）。 */
export function LogoMark({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label="llmprices.cn"
      style={{ flexShrink: 0 }}
    >
      <DajuPeek shape="square" />
    </svg>
  );
}
