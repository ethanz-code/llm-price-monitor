"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { toPng } from "html-to-image";
import { useTheme } from "@/app/providers";
import { Btn, Modal, toast } from "./ui";
import { ShareSiteCard, type ShareTheme } from "./ShareSiteCard";
import { buildChannelModel, type AvailabilityPoint, type ChannelDotRow } from "@/lib/channelStatus";

/** 后台标签页、窗口失焦或锁屏时浏览器的帧时钟会停摆，rAF 拿不到回调；
 *  裸等一帧会让整个生成流程永久挂起，这里最多等 50ms 就往下走。 */
function nextFrame(): Promise<void> {
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
async function withFrameFallback<T>(run: () => Promise<T>): Promise<T> {
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
const CAPTURE_TIMEOUT_MS = 15_000;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
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

/** 中转站实时状况分享图：点击生成 PNG（含站点状态、监控站域名与介绍），预览后一键保存。 */
export function ShareSiteButton({
  siteName,
  homepage,
  availability,
  channels,
}: {
  siteName: string;
  homepage: string;
  availability: AvailabilityPoint[];
  channels: ChannelDotRow[];
}) {
  // 延迟趋势与页面 StatusCharts 同一条构建路径（统一时间轴 + 阶梯保持），
  // 分享图固定 1000px 宽，不做窄屏抽稀
  const latencyModel = useMemo(
    () =>
      buildChannelModel(
        channels,
        // 0 / 负值是站点自报的无效延迟，当缺数处理
        (dot) => (dot.latency != null && dot.latency > 0 ? dot.latency : null),
        false,
        (a, b) => (a.value >= b.value ? a : b),
      ),
    [channels],
  );
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [domain, setDomain] = useState("");
  const { dark } = useTheme();
  // 分享图配色完全跟随站点当前明暗（看到的页面什么样，图就什么样），不提供手动切换
  const [theme, setTheme] = useState<ShareTheme>("dark");
  // 初值 0（卡片时间显示"—"），挂载后再取真实时间：服务端和客户端各算一次 Date.now()
  // 会让"数据截至"的刻度文本对不上；真正出图前 capture() 还会再刷新一次
  const [generatedAt, setGeneratedAt] = useState(0);
  const hostRef = useRef<HTMLDivElement>(null);

  // 监控站域名在客户端取：分享图要的是"现在打开的这个网站"的地址
  useEffect(() => setDomain(window.location.origin), []);
  // SSR 初值固定暗色保证水合一致，挂载后校正成站点当前主题
  useEffect(() => setTheme(dark ? "dark" : "light"), [dark]);
  useEffect(() => setGeneratedAt(Math.floor(Date.now() / 1000)), []);

  /** 把离屏卡片栅格化成 PNG；theme 切换后先等 React 提交再截，避免截到旧配色。 */
  async function capture() {
    const host = hostRef.current;
    const card = host?.firstElementChild as HTMLElement | null;
    if (!card) return;
    // 尺寸量不到时 html-to-image 会拿 0 去算画布、静默产出一张空图，先拦下来
    const width = card.offsetWidth;
    const height = card.offsetHeight;
    if (width < 2 || height < 2) {
      toast("分享卡片还没排好版，稍等片刻再试");
      return;
    }
    setBusy(true);
    try {
      setGeneratedAt(Math.floor(Date.now() / 1000));
      await nextFrame();
      // 显式给尺寸，不再让库自己量；再加超时兜底，避免任何环节卡住后按钮一直转圈
      const url = await withTimeout(
        withFrameFallback(() =>
          toPng(card, { width, height, pixelRatio: 2, cacheBust: true }),
        ),
        CAPTURE_TIMEOUT_MS,
      );
      setImgUrl(url);
    } catch {
      toast("分享图生成失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    await capture();
    setOpen(true);
  }

  return (
    <>
      {/* 离屏挂载卡片本体：生成时才有布局开销，平时不可见、不响应交互 */}
      <div
        aria-hidden
        ref={hostRef}
        style={{ position: "fixed", left: -20000, top: 0, pointerEvents: "none", zIndex: -1 }}
      >
        <ShareSiteCard
          siteName={siteName}
          homepage={homepage}
          domain={domain}
          availability={availability}
          channels={channels}
          latency={latencyModel}
          generatedAt={generatedAt}
          theme={theme}
        />
      </div>

      <Btn variant="primary" loading={busy} onClick={generate}>
        生成分享图
      </Btn>

      <Modal open={open} onClose={() => setOpen(false)} title="分享图已生成" width={720}>
        {imgUrl && (
          <div style={{ display: "grid", gap: 14 }}>
            <img
              src={imgUrl}
              alt={`${siteName} 实时状况分享图`}
              style={{ width: "100%", borderRadius: 10, border: "1px solid var(--border)" }}
            />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <Btn onClick={() => setOpen(false)}>关闭</Btn>
              <a className="btn btn-primary" href={imgUrl} download={`站点检测-${siteName}.png`}>
                保存图片
              </a>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
