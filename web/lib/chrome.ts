/** 镀铬马赛克方块生成：椭圆核心区 + 越靠边出现概率越低，形成溶解式消散；
 *  固定种子线性同余随机保证 SSR 与客户端重渲染得到同一批方块，避免 hydration 抖动。 */

export type ChromeCell = {
  x: number;
  y: number;
  size: number;
  fill: string;
  opacity: number;
  /** 呼吸动效的相位与时长（秒），由 CSS keyframes chr-shimmer 消费 */
  delay: number;
  dur: number;
  /** 归一化到椭圆的距离（0 中心 ~ 1 边缘），用作蔓延波纹的传播延迟 */
  dist: number;
};

export function buildChromeCells({
  cx,
  cy,
  rx,
  ry,
  cell,
  seed,
  gradientPrefix,
}: {
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  cell: number;
  seed: number;
  gradientPrefix: string;
}): ChromeCell[] {
  let state = seed >>> 0;
  const rnd = () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
  const cells: ChromeCell[] = [];
  for (let y = cy - ry; y < cy + ry; y += cell) {
    for (let x = cx - rx; x < cx + rx; x += cell) {
      const dx = (x + cell / 2 - cx) / rx;
      const dy = (y + cell / 2 - cy) / ry;
      const d = Math.hypot(dx, dy);
      if (d >= 1 || rnd() > (1 - d) * 1.35) continue;
      const r = rnd();
      const fill = r < 0.14 ? `url(#${gradientPrefix}-g)` : `url(#${gradientPrefix}-${Math.floor(rnd() * 4)})`;
      cells.push({
        x,
        y,
        size: cell - 2,
        fill,
        opacity: 0.55 + rnd() * 0.45,
        delay: rnd() * 7,
        dur: 3.5 + rnd() * 4,
        dist: d,
      });
    }
  }
  return cells;
}

/** 镀铬质感：同一组银色明暗带在不同方向上重复，偶尔一块混入品牌绿反光；颜色走主题变量。 */
export const CHROME_STOPS: Array<[number, string]> = [
  [0, "var(--chrome-hi)"],
  [0.3, "var(--chrome-lo)"],
  [0.55, "var(--chrome-hi)"],
  [0.8, "var(--chrome-md)"],
  [1, "var(--chrome-lo)"],
];
export const CHROME_DIRS: Array<[number, number]> = [
  [1, 1],
  [0, 1],
  [1, 0.2],
  [0.3, 1],
];
