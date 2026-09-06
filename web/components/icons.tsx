/** 内联 SVG 图标集：统一 1.7 描边、currentColor，替代 @ant-design/icons。 */

interface IconProps {
  size?: number;
  className?: string;
}

function Stroke({ size = 16, className, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {children}
    </svg>
  );
}

export function IconBolt(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M13 2 4.5 13.5H11L10 22l9.5-12H13L13 2Z" fill="currentColor" stroke="none" />
    </Stroke>
  );
}

export function IconSync(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M21 12a9 9 0 1 1-3.2-6.9" />
      <path d="M21 3v6h-6" />
    </Stroke>
  );
}

export function IconSearch(p: IconProps) {
  return (
    <Stroke {...p}>
      <circle cx={11} cy={11} r={7} />
      <path d="m20 20-3.5-3.5" />
    </Stroke>
  );
}

export function IconMenu(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M4 6h16M4 12h16M4 18h16" />
    </Stroke>
  );
}

export function IconMoon(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
    </Stroke>
  );
}

export function IconSun(p: IconProps) {
  return (
    <Stroke {...p}>
      <circle cx={12} cy={12} r={4} />
      <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </Stroke>
  );
}

export function IconMonitor(p: IconProps) {
  return (
    <Stroke {...p}>
      <rect x={3} y={4.5} width={18} height={12.5} rx={1.5} />
      <path d="M9 20.5h6m-3-3.5v3.5" />
    </Stroke>
  );
}

export function IconKey(p: IconProps) {
  return (
    <Stroke {...p}>
      <circle cx={8} cy={15.5} r={4} />
      <path d="m11 12.5 8.5-8.5M16 7l2.5 2.5M13.5 9.5 16 12" />
    </Stroke>
  );
}

export function IconEye(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8Z" />
      <circle cx={12} cy={12} r={3} />
    </Stroke>
  );
}

export function IconEyeOff(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
      <path d="m1 1 22 22" />
    </Stroke>
  );
}

export function IconPlus(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M12 5v14M5 12h14" />
    </Stroke>
  );
}

export function IconGithub(p: IconProps) {
  return (
    <svg width={p.size ?? 16} height={p.size ?? 16} viewBox="0 0 24 24" fill="currentColor" className={p.className} aria-hidden>
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.55 0-.27-.01-1.17-.02-2.12-3.2.7-3.87-1.36-3.87-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.03 1.76 2.69 1.25 3.35.96.1-.75.4-1.25.72-1.54-2.55-.29-5.23-1.28-5.23-5.68 0-1.26.45-2.28 1.18-3.09-.12-.29-.51-1.46.11-3.05 0 0 .96-.31 3.15 1.18a10.9 10.9 0 0 1 2.87-.39c.97 0 1.95.13 2.87.39 2.19-1.49 3.15-1.18 3.15-1.18.62 1.59.23 2.76.11 3.05.73.81 1.18 1.83 1.18 3.09 0 4.41-2.69 5.38-5.25 5.67.41.35.77 1.05.77 2.12 0 1.53-.01 2.76-.01 3.14 0 .3.2.67.8.55A11.51 11.51 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z" />
    </svg>
  );
}

export function IconDashboard(p: IconProps) {
  return (
    <Stroke {...p}>
      <rect x={3.5} y={3.5} width={7} height={7} rx={1.5} />
      <rect x={13.5} y={3.5} width={7} height={7} rx={1.5} />
      <rect x={3.5} y={13.5} width={7} height={7} rx={1.5} />
      <rect x={13.5} y={13.5} width={7} height={7} rx={1.5} />
    </Stroke>
  );
}

export function IconAppstore(p: IconProps) {
  return (
    <Stroke {...p}>
      <rect x={3.5} y={3.5} width={7.5} height={7.5} rx={1.5} />
      <path d="M14 4.5h6.5V11H14zM4.5 14H11v6.5H4.5zM14 14h6.5v6.5H14z" />
    </Stroke>
  );
}

export function IconBook(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
    </Stroke>
  );
}

export function IconSettings(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M4 7h9m4 0h3M4 12h3m4 0h9M4 17h13m3 0h0" />
      <circle cx={15} cy={7} r={1.6} />
      <circle cx={9} cy={12} r={1.6} />
      <circle cx={19} cy={17} r={1.6} />
    </Stroke>
  );
}

export function IconAim(p: IconProps) {
  return (
    <Stroke {...p}>
      <circle cx={12} cy={12} r={7.5} />
      <path d="M12 2.5v4m0 11v4M2.5 12h4m11 0h4" />
      <circle cx={12} cy={12} r={1.4} fill="currentColor" stroke="none" />
    </Stroke>
  );
}

export function IconFileSearch(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z" />
      <path d="M14 3v5h5" />
      <circle cx={11.5} cy={13.5} r={2.5} />
      <path d="m13.5 15.5 2 2" />
    </Stroke>
  );
}

export function IconNodes(p: IconProps) {
  return (
    <Stroke {...p}>
      <circle cx={5.5} cy={12} r={2.2} />
      <circle cx={18.5} cy={5.5} r={2.2} />
      <circle cx={18.5} cy={18.5} r={2.2} />
      <path d="m7.5 10.8 8.8-4.2M7.5 13.2l8.8 4.2" />
    </Stroke>
  );
}

export function IconChevronDown(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="m6 9 6 6 6-6" />
    </Stroke>
  );
}

export function IconClose(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M6 6l12 12M18 6 6 18" />
    </Stroke>
  );
}

export function IconCheck(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M20 6 9 17l-5-5" />
    </Stroke>
  );
}

export function IconAlertCircle(p: IconProps) {
  return (
    <Stroke {...p}>
      <circle cx={12} cy={12} r={9} />
      <path d="M12 7.2v5.4" />
      <circle cx={12} cy={16.3} r={1} fill="currentColor" stroke="none" />
    </Stroke>
  );
}

export function IconMail(p: IconProps) {
  return (
    <Stroke {...p}>
      <rect x={3} y={5} width={18} height={14} rx={2} />
      <path d="m3.5 7.5 8.5 6 8.5-6" />
    </Stroke>
  );
}

/** 提建议：对话气泡（lucide `message-square`，ISC）。 */
export function IconFeedback(p: IconProps) {
  return (
    <Stroke {...p}>
      <path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10Z" />
    </Stroke>
  );
}

/** 企业微信官方标志（Tencent TDesign `logo-wecom`，MIT），单色 currentColor 填充。 */
export function IconWecom(p: IconProps) {
  return (
    <svg width={p.size ?? 16} height={p.size ?? 16} viewBox="0 0 24 24" fill="currentColor" className={p.className} aria-hidden>
      <path d="m17.326 8.158l-.003-.007a6.6 6.6 0 0 0-1.178-1.674c-1.266-1.307-3.067-2.19-5.102-2.417a9.3 9.3 0 0 0-2.124 0h-.001c-2.061.228-3.882 1.107-5.14 2.405a6.7 6.7 0 0 0-1.194 1.682A5.7 5.7 0 0 0 2 10.657c0 1.106.332 2.218.988 3.201l.006.01c.391.594 1.092 1.39 1.637 1.83l.983.793l-.208.875l.527-.267l.708-.358l.761.225c.467.137.955.227 1.517.29h.005q.515.06 1.026.059c.355 0 .724-.02 1.095-.06a9 9 0 0 0 1.346-.258c.095.7.43 1.337.932 1.81c-.658.208-1.352.358-2.061.436c-.442.048-.883.072-1.312.072q-.627 0-1.253-.072a10.7 10.7 0 0 1-1.861-.36l-2.84 1.438s-.29.131-.44.131c-.418 0-.702-.285-.702-.704c0-.252.067-.598.128-.84l.394-1.653c-.728-.586-1.563-1.544-2.052-2.287A7.76 7.76 0 0 1 0 10.658a7.7 7.7 0 0 1 .787-3.39a8.7 8.7 0 0 1 1.551-2.19c1.61-1.665 3.878-2.73 6.359-3.006a11.3 11.3 0 0 1 2.565 0c2.47.275 4.712 1.353 6.323 3.017a8.6 8.6 0 0 1 1.539 2.192c.466.945.769 1.937.769 2.978a3.06 3.06 0 0 0-2-.005c-.001-.644-.189-1.329-.564-2.09zm4.125 6.977l-.024-.024l-.024-.018l-.024-.018l-.096-.095a4.24 4.24 0 0 1-1.169-2.192q0-.038-.006-.075l-.006-.056l-.035-.144a1.3 1.3 0 0 0-.358-.61a1.386 1.386 0 0 0-1.957 0a1.4 1.4 0 0 0 0 1.963c.191.191.418.311.668.371c.024.012.06.012.084.012q.019 0 .041.006q.023.005.042.006a4.24 4.24 0 0 1 2.231 1.186c.048.048.096.095.131.143a.323.323 0 0 0 .466 0a.35.35 0 0 0 .036-.455m-1.05 4.37l-.025.025c-.119.096-.31.096-.453-.036a.326.326 0 0 1 0-.467c.047-.036.094-.083.141-.13l.002-.002a4.27 4.27 0 0 0 1.187-2.28q.005-.024.006-.043c0-.024 0-.06.012-.084a1.386 1.386 0 0 1 2.326-.67a1.4 1.4 0 0 1 0 1.964c-.167.18-.382.299-.608.359l-.143.036l-.057.005q-.035.006-.075.007a4.2 4.2 0 0 0-2.183 1.173l-.095.096q-.009.01-.018.024t-.018.024m-4.392-1.053l.024.024l.024.018q.015.009.024.018l.096.096a4.25 4.25 0 0 1 1.169 2.19q0 .04.006.076q.005.03.006.057l.035.143c.06.228.18.443.358.611c.537.539 1.42.539 1.957 0a1.4 1.4 0 0 0 0-1.964a1.4 1.4 0 0 0-.668-.371c-.024-.012-.06-.012-.084-.012q-.018 0-.041-.006l-.042-.006a4.25 4.25 0 0 1-2.231-1.185a1.4 1.4 0 0 1-.131-.144a.323.323 0 0 0-.466 0a.325.325 0 0 0-.036.455m1.039-4.358l.024-.024a.32.32 0 0 1 .453.035a.326.326 0 0 1 0 .467c-.047.036-.094.083-.141.13l-.002.002a4.27 4.27 0 0 0-1.187 2.281l-.006.042c0 .024 0 .06-.012.084a1.386 1.386 0 0 1-2.326.67a1.4 1.4 0 0 1 0-1.963c.166-.18.381-.3.608-.36l.143-.035q.026 0 .056-.006q.037-.005.075-.006a4.2 4.2 0 0 0 2.183-1.174l.096-.095l.018-.025z" />
    </svg>
  );
}
