"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

/** 延迟趋势图图例的隐藏状态：图表实例内不再自持，页面级共享——
 *  点「生成分享图」时分享卡拿到同一份状态，页面图例里被点掉的渠道，
 *  分享图的延迟趋势图里也不再出现（线、图例都排除）。 */
interface ChartLegendState {
  /** 被隐藏的系列名集合（图例里点掉的渠道名） */
  hidden: ReadonlySet<string>;
  toggle: (name: string) => void;
}

const ChartLegendContext = createContext<ChartLegendState>({
  hidden: new Set(),
  toggle: () => {},
});

export function ChartLegendProvider({ children }: { children: ReactNode }) {
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const toggle = useCallback((name: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);
  const value = useMemo(() => ({ hidden, toggle }), [hidden, toggle]);
  return <ChartLegendContext.Provider value={value}>{children}</ChartLegendContext.Provider>;
}

export function useChartLegend(): ChartLegendState {
  return useContext(ChartLegendContext);
}
