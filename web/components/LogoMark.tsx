/** 品牌 Logo：荧光绿底上的价格脉搏折线，终点圆点代表实时价格节点。配色走 CSS 变量，双主题自动适配。 */
export function LogoMark({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label="LLM 价格监控"
      style={{ flexShrink: 0 }}
    >
      <rect width="64" height="64" rx="14" fill="var(--accent)" />
      <polyline
        points="14,45 27,31 35,37 42.8,27.4"
        fill="none"
        stroke="var(--accent-contrast)"
        strokeWidth="4.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="48" cy="21" r="4.5" fill="var(--accent-contrast)" />
    </svg>
  );
}
