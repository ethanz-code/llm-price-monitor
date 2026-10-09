import { useEffect, useState } from "react";

/** 是否处于窄屏（默认 ≤900px，与表格 .col-hide-m 藏列断点一致）。SSR 首帧按桌面渲染，挂载后同步真实视口。 */
export function useNarrow(maxWidth = 900): boolean {
  const query = `(max-width: ${maxWidth}px)`;
  const [narrow, setNarrow] = useState(false);

  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setNarrow(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);

  return narrow;
}
