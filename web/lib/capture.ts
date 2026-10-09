/** 分享图截图链路的共用工具：站点状况分享卡与花费计算分享卡都走这一套。
 *  核心是用 html-to-image 把离屏 DOM 栅格化成 PNG；这里的封装只处理帧时钟、
 *  超时与尺寸校验，UI 状态（按钮转圈、预览弹窗、toast 文案）留在各自的按钮组件里。 */

/** 后台标签页、窗口失焦或锁屏时浏览器的帧时钟会停摆，rAF 拿不到回调；
 *  裸等一帧会让整个生成流程永久挂起，这里最多等 50ms 就往下走。 */
export function nextFrame(): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    requestAnimationFrame(finish);
    window.setTimeout(finish, 50);
  });
}

/** html-to-image 内部（createImage）也要等一帧才 resolve，页面在后台时同样挂死；
 *  生成期间把 rAF 接到定时器上，结束后立刻还原，不影响页面上其它动画。 */
export async function withFrameFallback<T>(run: () => Promise<T>): Promise<T> {
  const raf = window.requestAnimationFrame;
  const caf = window.cancelAnimationFrame;
  window.requestAnimationFrame = ((callback: FrameRequestCallback) =>
    window.setTimeout(() => callback(performance.now()), 16)) as typeof window.requestAnimationFrame;
  window.cancelAnimationFrame = ((handle: number) =>
    window.clearTimeout(handle)) as typeof window.cancelAnimationFrame;
  try {
    return await run();
  } finally {
    window.requestAnimationFrame = raf;
    window.cancelAnimationFrame = caf;
  }
}

/** 生成耗时上限：正常几百毫秒内出图，超过说明链路卡住了。
 *  宁可明确报错让用户重试，也不能让按钮一直转圈、既无结果也无提示。 */
export const CAPTURE_TIMEOUT_MS = 15_000;

export function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("分享图生成超时")), ms);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        window.clearTimeout(timer);
        reject(error);
      },
    );
  });
}
