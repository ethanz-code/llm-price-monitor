/** 首页顶部通栏绿色柔光：内联 SVG，颜色走主题变量，明暗主题自动适配；纯装饰不参与交互。 */
export function HeroBackdrop() {
  return (
    <div className="hero-backdrop" aria-hidden>
      <svg width="100%" height="100%" viewBox="0 0 1440 620" preserveAspectRatio="xMidYMid slice">
        <defs>
          <radialGradient id="hb-glow-green">
            <stop offset="0" style={{ stopColor: "var(--glow-green)" }} />
            <stop offset="0.55" style={{ stopColor: "var(--glow-green)", stopOpacity: 0.5 }} />
            <stop offset="1" style={{ stopColor: "var(--glow-green)", stopOpacity: 0 }} />
          </radialGradient>
        </defs>
        {/* 左上主光斑压在 hero 标题区，中部弱光斑过渡；马赛克只保留在页脚 */}
        <ellipse cx="290" cy="10" rx="700" ry="350" fill="url(#hb-glow-green)" />
        <ellipse cx="700" cy="120" rx="480" ry="200" fill="url(#hb-glow-green)" opacity="0.3" />
      </svg>
    </div>
  );
}
