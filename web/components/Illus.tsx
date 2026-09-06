/** 极简单色几何插画：连续线条勾勒 + 单块柔和色块偏移，
 *  用于 hero 侧栏与空状态，避免"光秃秃"又不破坏克制的编辑感。 */

interface IllusProps {
  width?: number;
  className?: string;
}

/** 价格脉搏：上升折线穿过一个偏移的荧光绿方块，终点圆点呼应 LogoMark。 */
export function IllusPulse({ width = 180, className }: IllusProps) {
  return (
    <svg
      width={width}
      height={width * 0.62}
      viewBox="0 0 180 112"
      fill="none"
      className={className}
      aria-hidden
    >
      <rect x={112} y={10} width={40} height={40} rx={6} fill="var(--accent)" opacity={0.85} />
      <path
        d="M8 96 C 34 92, 40 74, 56 74 S 78 88, 92 74 104 40 122 40"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
      />
      <circle cx={126} cy={40} r={3.5} fill="currentColor" />
      <path d="M8 104h60" stroke="currentColor" strokeWidth={1} opacity={0.35} strokeLinecap="round" />
      <path d="M8 104h28" stroke="currentColor" strokeWidth={1} opacity={0.7} strokeLinecap="round" />
    </svg>
  );
}

/** 三层叠放的取证明细：文档线条 + 偏移色块，呼应"逐条取证"。 */
export function IllusLedger({ width = 150, className }: IllusProps) {
  return (
    <svg
      width={width}
      height={width * 0.78}
      viewBox="0 0 150 117"
      fill="none"
      className={className}
      aria-hidden
    >
      <rect x={88} y={8} width={34} height={34} rx={6} fill="var(--tone-blue-bg)" stroke="var(--tone-blue-text)" strokeOpacity={0.4} />
      <rect x={22} y={30} width={96} height={12} rx={6} stroke="currentColor" strokeWidth={1.6} opacity={0.55} />
      <rect x={30} y={54} width={88} height={12} rx={6} stroke="currentColor" strokeWidth={1.6} opacity={0.75} />
      <rect x={22} y={78} width={96} height={12} rx={6} stroke="currentColor" strokeWidth={1.6} />
      <circle cx={44} cy={60} r={2.6} fill="currentColor" />
      <circle cx={44} cy={84} r={2.6} fill="currentColor" />
    </svg>
  );
}
