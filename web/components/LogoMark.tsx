import { DajuPeek } from "./DajuArt";

/** 品牌 Logo：探头的大橘——荧光绿底圆角方块，橘猫从下缘探出头。
 *  形象与助手悬浮球同源（DajuPeek circle），表情为好奇睁眼。 */
export function LogoMark({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label="大橘"
      style={{ flexShrink: 0 }}
    >
      <DajuPeek shape="square" />
    </svg>
  );
}
