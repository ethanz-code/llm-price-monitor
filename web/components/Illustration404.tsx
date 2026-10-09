/** 404 插画：一块监测屏上的价格曲线掉出了画面，放大镜凑过来找——呼应产品「盯价格」的场景；
 *  颜色全走主题变量，明暗主题自动适配；纯装饰不参与交互。 */
export function Illustration404() {
  return (
    <svg width="220" height="170" viewBox="0 0 220 170" fill="none" aria-hidden>
      <defs>
        <linearGradient id="il-chr-0" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: "var(--chrome-hi)" }} />
          <stop offset="1" style={{ stopColor: "var(--chrome-lo)" }} />
        </linearGradient>
        <linearGradient id="il-chr-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: "var(--chrome-hi)" }} />
          <stop offset="0.5" style={{ stopColor: "var(--chrome-glint)" }} />
          <stop offset="1" style={{ stopColor: "var(--chrome-lo)" }} />
        </linearGradient>
      </defs>

      {/* 监测屏 */}
      <rect x="26" y="24" width="168" height="112" rx="12" style={{ fill: "var(--panel-2)", stroke: "var(--border-strong)" }} />
      <rect x="26" y="24" width="168" height="26" rx="12" style={{ fill: "var(--tone-gray-bg)" }} />
      <circle cx="42" cy="37" r="3.5" style={{ fill: "var(--tone-green-text)" }} />
      <circle cx="54" cy="37" r="3.5" style={{ fill: "var(--tone-yellow-text)" }} />
      <circle cx="66" cy="37" r="3.5" style={{ fill: "var(--tone-red-text)" }} />

      {/* 价格曲线：先平稳、后掉出屏幕下缘 */}
      <path
        d="M40 78 L64 72 L86 80 L108 66 L126 74 L142 96 L154 128 L160 150"
        style={{ stroke: "var(--accent)" }}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="108" cy="66" r="4" style={{ fill: "var(--accent)" }} />

      {/* 掉落处的省略号：曲线消失在屏幕外 */}
      <circle cx="176" cy="118" r="3" style={{ fill: "var(--text-3)" }} />
      <circle cx="188" cy="126" r="3" style={{ fill: "var(--text-3)" }} />
      <circle cx="199" cy="134" r="3" style={{ fill: "var(--text-3)" }} />

      {/* 放大镜：凑到掉落点寻找 */}
      <g>
        <circle cx="128" cy="104" r="20" style={{ fill: "var(--bg)", stroke: "var(--text-2)" }} strokeWidth="3" />
        <line x1="143" y1="119" x2="158" y2="134" style={{ stroke: "var(--text-2)" }} strokeWidth="4" strokeLinecap="round" />
        <path d="M116 102 L124 98 L132 104" style={{ stroke: "var(--text-3)" }} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </g>

      {/* 右上角溶解中的镀铬小方块：呼应站点背景的金属马赛克 */}
      <rect x="186" y="8" width="14" height="14" fill="url(#il-chr-g)" />
      <rect x="170" y="14" width="10" height="10" fill="url(#il-chr-0)" opacity="0.7" />
      <rect x="158" y="6" width="7" height="7" fill="url(#il-chr-0)" opacity="0.45" />
    </svg>
  );
}
