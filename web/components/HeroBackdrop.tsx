/** 首页顶部通栏斜向光束：内联 SVG，颜色走主题变量，明暗主题自动适配；纯装饰不参与交互。
 *  一宽一窄两束光从右上向左下斜洒：宽束做环境柔光，窄束做亮芯，模糊滤镜收掉硬边。 */
export function HeroBackdrop() {
  return (
    <div className="hero-backdrop" aria-hidden>
      <svg width="100%" height="100%" viewBox="0 0 1440 620" preserveAspectRatio="xMidYMid slice">
        <defs>
          <linearGradient id="hb-beam" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" style={{ stopColor: "var(--glow-green)" }} stopOpacity="0.9" />
            <stop offset="0.6" style={{ stopColor: "var(--glow-green)" }} stopOpacity="0.3" />
            <stop offset="1" style={{ stopColor: "var(--glow-green)" }} stopOpacity="0" />
          </linearGradient>
          <filter id="hb-soft" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="70" />
          </filter>
        </defs>
        {/* 绕右上角旋转，让垂直光束倾斜约 24°，光源感在右上、洒向标题与球体 */}
        <g transform="rotate(24 1440 0)">
          <rect className="hb-beam" x="620" y="-360" width="620" height="1500" fill="url(#hb-beam)" filter="url(#hb-soft)" />
          <rect className="hb-beam hb-beam-late" x="760" y="-360" width="200" height="1500" fill="url(#hb-beam)" filter="url(#hb-soft)" opacity="0.7" />
        </g>
      </svg>
    </div>
  );
}
