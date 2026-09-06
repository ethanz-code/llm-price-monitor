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

export function IconHistory(p: IconProps) {
  return (
    <Stroke {...p}>
      <circle cx={12} cy={12} r={8.5} />
      <path d="M12 7.5V12l3 2" />
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
