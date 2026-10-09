/**
 * 大橘品牌插画层：吉祥物橘猫的场景图 + 爪印装饰。
 * 手绘 inline SVG（无版权风险），猫毛色走 --cat-* 主题变量，明暗主题自动适配；
 * 环境元素（曲线、面板、阴影）沿用既有语义变量。全部纯装饰 aria-hidden。
 * 猫的形象语言与 LogoMark 一致：眯眼笑、ω 嘴、额头条纹。
 */

/**
 * 探头的大橘：猫头从品牌绿底的下缘探出来，下半身被裁掉——品牌主形象（logo 与助手球同源）。
 * shape=square 用于 LogoMark（绿底圆角方块），circle 用于助手悬浮球/抽屉头像（绿圆）；
 * 探头好奇表情（睁眼），带 .daju-peek-head 类供悬停探更高。
 */
export function DajuPeek({ shape }: { shape: "square" | "circle" }) {
  return (
    <>
      <defs>
        <clipPath id={`daju-peek-clip-${shape}`}>
          {shape === "square" ? (
            <rect width="64" height="64" rx="14" />
          ) : (
            <circle cx="32" cy="32" r="32" />
          )}
        </clipPath>
      </defs>
      {/* 荧光绿底 */}
      {shape === "square" ? (
        <rect width="64" height="64" rx="14" fill="var(--accent)" />
      ) : (
        <circle cx="32" cy="32" r="32" fill="var(--accent)" />
      )}
      <g clipPath={`url(#daju-peek-clip-${shape})`}>
        {/* 猫头：圆心沉到画面外下方，只露出头顶到眼睛的一段，被底缘裁掉形成探头感 */}
        <circle cx="32" cy="50" r="24" fill="var(--cat-coat)" stroke="var(--cat-ink)" strokeWidth="2.6" />
        {/* 圆润曲线耳（后画盖住脸弧描边） */}
        <path d="M17.5 31 Q12.5 16 14.5 15 Q16.5 14.5 26.5 28.5 Z" fill="var(--cat-coat)" stroke="var(--cat-ink)" strokeWidth="2.6" strokeLinejoin="round" />
        <path d="M46.5 31 Q51.5 16 49.5 15 Q47.5 14.5 37.5 28.5 Z" fill="var(--cat-coat)" stroke="var(--cat-ink)" strokeWidth="2.6" strokeLinejoin="round" />
        <g className="daju-peek-head">
          {/* 额头条纹 */}
          <path d="M32 27.5 L32 35" stroke="var(--cat-stripe)" strokeWidth="3" strokeLinecap="round" />
          <path d="M25.8 31.5 L25.2 37" stroke="var(--cat-stripe)" strokeWidth="2.6" strokeLinecap="round" />
          <path d="M38.2 31.5 L38.8 37" stroke="var(--cat-stripe)" strokeWidth="2.6" strokeLinecap="round" />
          {/* 好奇睁眼 + ω 嘴 */}
          <circle cx="23.5" cy="44" r="3" fill="var(--cat-ink)" />
          <circle cx="40.5" cy="44" r="3" fill="var(--cat-ink)" />
          <path d="M26.5 50.5 Q29.5 53.6 32 50.5 Q34.5 53.6 37.5 50.5" fill="none" stroke="var(--cat-ink)" strokeWidth="2.6" strokeLinecap="round" />
        </g>
      </g>
    </>
  );
}

/** 爪印：填充 currentColor，调用处用 CSS 控制颜色（标题、列表符号等）。 */
export function PawPrint({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden>
      <ellipse cx="12" cy="15.6" rx="5.6" ry="4.6" />
      <ellipse cx="5.4" cy="10.2" rx="2" ry="2.7" transform="rotate(-18 5.4 10.2)" />
      <ellipse cx="9.6" cy="7.4" rx="2" ry="2.8" />
      <ellipse cx="14.4" cy="7.4" rx="2" ry="2.8" />
      <ellipse cx="18.6" cy="10.2" rx="2" ry="2.7" transform="rotate(18 18.6 10.2)" />
    </svg>
  );
}

/** 尾部 CTA：大橘趴在价格曲线的高原上打盹，曲线在它身下继续走向最新一个点。 */
export function DajuChartNap({ width = 300 }: { width?: number }) {
  return (
    <svg
      width={width}
      height={width * (132 / 300)}
      viewBox="0 0 300 132"
      fill="none"
      aria-hidden
      style={{ maxWidth: "100%" }}
    >
      {/* 价格曲线：爬升 → 高原（大橘趴着） → 回落 → 最新一个点 */}
      <path
        d="M8 112 C48 112 66 100 96 98 C116 96.5 124 78 148 78 L196 78 C222 78 232 102 258 110 C272 114 284 115 292 115"
        style={{ stroke: "var(--accent-text)" }}
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
      <circle className="daju-halo" cx="292" cy="115" r="7" style={{ fill: "var(--accent)" }} opacity="0.25" />
      <circle className="daju-dot" cx="292" cy="115" r="3.5" style={{ fill: "var(--accent)" }} />

      {/* 趴着的猫：面包身 + 垂在高原边上的尾巴 */}
      <path
        className="daju-tail"
        d="M196 60 Q209 63 208 77 Q207 86 199 89"
        style={{ stroke: "var(--cat-coat)" }}
        strokeWidth="7"
        strokeLinecap="round"
        fill="none"
      />
      <polygon points="140,46 136,26 154,40" style={{ fill: "var(--cat-coat)" }} />
      <polygon points="160,40 168,25 172,44" style={{ fill: "var(--cat-coat)" }} />
      <rect x="132" y="42" width="64" height="36" rx="18" style={{ fill: "var(--cat-coat)" }} />
      <path d="M158 44 q3 7 0 13" style={{ stroke: "var(--cat-stripe)" }} strokeWidth="4.5" strokeLinecap="round" fill="none" />
      <path d="M168 42.5 q3 7.5 0 14" style={{ stroke: "var(--cat-stripe)" }} strokeWidth="4.5" strokeLinecap="round" fill="none" />
      <path d="M178 44 q3 7 0 13" style={{ stroke: "var(--cat-stripe)" }} strokeWidth="4.5" strokeLinecap="round" fill="none" />
      <ellipse cx="141" cy="75" rx="8" ry="4.5" style={{ fill: "var(--cat-cream)" }} />
      {/* 脸：眯眼笑 + ω 嘴 + 胡须 */}
      <path d="M143 58 q3.2 3.2 6.4 0" style={{ stroke: "var(--cat-ink)" }} strokeWidth="2" strokeLinecap="round" fill="none" />
      <path d="M155 58 q3.2 3.2 6.4 0" style={{ stroke: "var(--cat-ink)" }} strokeWidth="2" strokeLinecap="round" fill="none" />
      <polygon points="150.6,63.6 152.2,65.7 153.8,63.6" style={{ fill: "var(--cat-ink)" }} />
      <path d="M148.9 67.4 q1.7 2 3.3 0 M152.2 67.4 q1.7 2 3.3 0" style={{ stroke: "var(--cat-ink)" }} strokeWidth="1.6" strokeLinecap="round" fill="none" />
      <path d="M136 60 l-7 -1.5 M136 63.5 l-7 1.5 M162 60 l7 -1.5 M162 63.5 l7 1.5" style={{ stroke: "var(--cat-ink)" }} strokeWidth="1.4" strokeLinecap="round" opacity="0.8" fill="none" />
      {/* 打盹的 z */}
      <path className="daju-z1" d="M206 22 h9 l-9 9 h9" style={{ stroke: "var(--text-3)" }} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      <path className="daju-z2" d="M222 10 h6.5 l-6.5 6.5 h6.5" style={{ stroke: "var(--text-3)" }} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" opacity="0.75" fill="none" />
    </svg>
  );
}

/** 趋势/事件空态：大橘玩毛线球，毛线一路拉成一条价格曲线，末端是最新的绿点。 */
export function DajuYarn({ width = 190 }: { width?: number }) {
  return (
    <svg
      width={width}
      height={width * (150 / 190)}
      viewBox="0 0 190 150"
      fill="none"
      aria-hidden
      style={{ maxWidth: "100%" }}
    >
      <ellipse cx="134" cy="132" rx="44" ry="6" style={{ fill: "var(--border)" }} opacity="0.6" />
      {/* 猫在毛线球后面：先画身体和头，球压在身前 */}
      <ellipse cx="152" cy="80" rx="19" ry="26" style={{ fill: "var(--cat-coat)" }} />
      <path d="M170 96 Q184 100 180 114" style={{ stroke: "var(--cat-coat)" }} strokeWidth="6" strokeLinecap="round" fill="none" />
      <polygon points="139,36 136,18 151,30" style={{ fill: "var(--cat-coat)" }} />
      <polygon points="154,30 168,19 165,37" style={{ fill: "var(--cat-coat)" }} />
      <circle cx="152" cy="44" r="17" style={{ fill: "var(--cat-coat)" }} />
      <path d="M147 29 q2 5 0 9 M154 28.5 q2 5 0 9.5" style={{ stroke: "var(--cat-stripe)" }} strokeWidth="3.5" strokeLinecap="round" fill="none" />
      <path d="M144 42 q2.6 2.6 5.2 0 M156 42 q2.6 2.6 5.2 0" style={{ stroke: "var(--cat-ink)" }} strokeWidth="2" strokeLinecap="round" fill="none" />
      <polygon points="150.6,46.6 152,48.2 153.4,46.6" style={{ fill: "var(--cat-ink)" }} />
      <path d="M149 50 q1.5 1.7 3 0 M152 50 q1.5 1.7 3 0" style={{ stroke: "var(--cat-ink)" }} strokeWidth="1.4" strokeLinecap="round" fill="none" />
      <path d="M136 44 l-7 -1.5 M136 47.5 l7 1.5 M168 44 l7 -1.5 M168 47.5 l7 1.5" style={{ stroke: "var(--cat-ink)" }} strokeWidth="1.3" strokeLinecap="round" opacity="0.8" fill="none" />
      {/* 毛线球：品牌绿，爪子搭在球上；球体缓慢自转（.daju-ball） */}
      <g className="daju-ball">
        <circle cx="130" cy="102" r="27" style={{ fill: "var(--accent)" }} />
        <path d="M104 96 q26 -12 52 0 M103 106 q27 14 54 0 M122 77 q-12 25 2 50" style={{ stroke: "var(--accent-contrast)" }} strokeWidth="2" strokeLinecap="round" opacity="0.45" fill="none" />
      </g>
      <ellipse cx="136" cy="82" rx="8" ry="5.5" transform="rotate(-24 136 82)" style={{ fill: "var(--cat-coat)" }} />
      {/* 拉出去的毛线 = 价格曲线，末端绿点是最新一次记录 */}
      <path
        d="M104 98 C88 92 96 74 78 76 C62 78 68 96 52 92 C40 89 44 74 36 72"
        style={{ stroke: "var(--accent-text)" }}
        strokeWidth="2.4"
        strokeLinecap="round"
        fill="none"
      />
      <circle className="daju-dot" cx="36" cy="72" r="3" style={{ fill: "var(--accent)" }} />
    </svg>
  );
}

/** 通用空态：大橘蜷在垫子上睡觉，等数据自己长出来。 */
export function DajuNap({ width = 160 }: { width?: number }) {
  return (
    <svg
      width={width}
      height={width * (116 / 170)}
      viewBox="0 0 170 116"
      fill="none"
      aria-hidden
      style={{ maxWidth: "100%" }}
    >
      <ellipse cx="85" cy="92" rx="56" ry="12" style={{ fill: "var(--panel-2)", stroke: "var(--border-strong)" }} strokeWidth="1.5" />
      {/* 蜷成一团的猫 + 环在身前的尾巴 */}
      <circle cx="84" cy="64" r="31" style={{ fill: "var(--cat-coat)" }} />
      <path d="M56 84 Q84 99 112 82" style={{ stroke: "var(--cat-coat)" }} strokeWidth="8" strokeLinecap="round" fill="none" />
      <path d="M92 38 q3 8 0 15 M102 42 q3 8 0 14 M111 50 q2.6 7 0 13" style={{ stroke: "var(--cat-stripe)" }} strokeWidth="4.5" strokeLinecap="round" fill="none" />
      {/* 埋着的头：耳朵尖 + 一只眯眼 + 奶色下巴 */}
      <polygon points="46,60 40,44 56,52" style={{ fill: "var(--cat-coat)" }} />
      <polygon points="60,51 64,37 74,50" style={{ fill: "var(--cat-coat)" }} />
      <circle cx="60" cy="70" r="17" style={{ fill: "var(--cat-coat)" }} />
      <path d="M52 68 q2.6 2.6 5.2 0" style={{ stroke: "var(--cat-ink)" }} strokeWidth="2" strokeLinecap="round" fill="none" />
      <ellipse cx="55" cy="77" rx="6.5" ry="5" style={{ fill: "var(--cat-cream)" }} />
      <ellipse cx="68" cy="84" rx="7" ry="4" style={{ fill: "var(--cat-cream)" }} />
      {/* 睡着的 z */}
      <path className="daju-z1" d="M112 30 h8 l-8 8 h8" style={{ stroke: "var(--text-3)" }} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      <path className="daju-z2" d="M128 18 h5.5 l-5.5 5.5 h5.5" style={{ stroke: "var(--text-3)" }} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" opacity="0.75" fill="none" />
    </svg>
  );
}

/** 醒着的大橘：睁眼坐直、尾巴翘起——算出结果时它也精神了（计算器结果头）。 */
export function DajuAwake({ width = 22 }: { width?: number }) {
  return (
    <svg
      width={width}
      height={width * (34 / 30)}
      viewBox="0 0 30 34"
      fill="none"
      aria-hidden
      style={{ flexShrink: 0 }}
    >
      <path className="daju-tail-mini" d="M25 30 q7 -3 4 -11" style={{ stroke: "var(--cat-coat)" }} strokeWidth="4" strokeLinecap="round" fill="none" />
      <path d="M8 30 C7 19 12 13 17.5 13 C23 13 26.5 19 26 30 Z" style={{ fill: "var(--cat-coat)" }} />
      <polygon points="6.5,8 5.5,1 12,5.5" style={{ fill: "var(--cat-coat)" }} />
      <polygon points="15,5.5 21.5,1 20.5,8.5" style={{ fill: "var(--cat-coat)" }} />
      <circle cx="13.5" cy="11.5" r="8" style={{ fill: "var(--cat-coat)" }} />
      <path d="M10.5 4.5 q1.4 3.2 0 6 M15 4.2 q1.4 3.2 0 6.4" style={{ stroke: "var(--cat-stripe)" }} strokeWidth="2.2" strokeLinecap="round" fill="none" />
      <circle cx="10.8" cy="10.8" r="1.5" style={{ fill: "var(--cat-ink)" }} />
      <circle cx="16" cy="10.8" r="1.5" style={{ fill: "var(--cat-ink)" }} />
      <polygon points="12.7,13.6 13.7,14.7 14.7,13.6" style={{ fill: "var(--cat-ink)" }} />
      <path d="M12.5 16 q1.2 1.4 2.5 0" style={{ stroke: "var(--cat-ink)" }} strokeWidth="1.3" strokeLinecap="round" fill="none" />
    </svg>
  );
}

/** 迷你坐姿大橘（页脚「蹲守」一句旁）：侧脸朝左盯着数据。 */
export function DajuSit({ width = 17 }: { width?: number }) {
  return (
    <svg
      width={width}
      height={width * (40 / 34)}
      viewBox="0 0 34 40"
      fill="none"
      aria-hidden
      style={{ flexShrink: 0 }}
    >
      <path className="daju-tail-mini" d="M27 36 q6 -1 5 -7" style={{ stroke: "var(--cat-coat)" }} strokeWidth="4.5" strokeLinecap="round" fill="none" />
      <path d="M11 36 C9.5 23 15 16.5 21.5 16.5 C28 16.5 32.5 23 31.5 36 Z" style={{ fill: "var(--cat-coat)" }} />
      <polygon points="9.5,9 8,0.5 16,6" style={{ fill: "var(--cat-coat)" }} />
      <polygon points="19,6 26,0.5 24.5,9.5" style={{ fill: "var(--cat-coat)" }} />
      <circle cx="17" cy="14" r="9.5" style={{ fill: "var(--cat-coat)" }} />
      <path d="M27.5 21 q2.5 5 0.5 10" style={{ stroke: "var(--cat-stripe)" }} strokeWidth="3" strokeLinecap="round" fill="none" />
      <path d="M14 6.5 q1.5 3.5 0 6.5 M19 6 q1.5 3.5 0 7" style={{ stroke: "var(--cat-stripe)" }} strokeWidth="2.6" strokeLinecap="round" fill="none" />
      <circle cx="13.5" cy="13" r="1.4" style={{ fill: "var(--cat-ink)" }} />
      <circle cx="20" cy="13" r="1.4" style={{ fill: "var(--cat-ink)" }} />
      <polygon points="15.8,16 17,17.2 18.2,16" style={{ fill: "var(--cat-ink)" }} />
    </svg>
  );
}

/** 警示条里的小猫脸：出问题时大橘也盯到了，眯眼表情与 LogoMark 同语言。 */
export function DajuFace({ size = 14, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <polygon points="5.5,8.5 4,2 11,6" style={{ fill: "var(--cat-coat)" }} />
      <polygon points="18.5,8.5 20,2 13,6" style={{ fill: "var(--cat-coat)" }} />
      <circle cx="12" cy="13.5" r="8" style={{ fill: "var(--cat-coat)" }} />
      <path d="M7.6 12.6 q1.7 1.7 3.4 0 M13 12.6 q1.7 1.7 3.4 0" style={{ stroke: "var(--cat-ink)" }} strokeWidth="1.7" strokeLinecap="round" fill="none" />
      <polygon points="11.2,15.4 12,16.3 12.8,15.4" style={{ fill: "var(--cat-ink)" }} />
    </svg>
  );
}
