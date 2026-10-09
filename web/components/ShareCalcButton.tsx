"use client";

import { useEffect, useRef, useState } from "react";
import { toPng } from "html-to-image";
import { useTheme } from "@/app/providers";
import { CAPTURE_TIMEOUT_MS, nextFrame, withFrameFallback, withTimeout } from "@/lib/capture";
import { Btn, Modal, toast } from "./ui";
import { ShareCalcCard } from "./ShareCalcCard";
import type { ShareTheme } from "./ShareSiteCard";
import type { CalcPrices, CalcResult } from "@/lib/calculator";

/** 花费计算分享图：把当前参数与结果栅格化成 PNG，预览后一键保存。
 *  截图链路与站点状况分享图同一套（离屏卡片 + html-to-image），交互也保持一致：
 *  生成 → 预览弹窗 → 保存；配色跟随站点当前明暗，不做手动切换。 */
export function ShareCalcButton({
  modelName,
  modelSub,
  sourceLabel,
  currency,
  prices,
  totalTokens,
  hitRate,
  result,
  missingCacheRead,
  disabled = false,
}: {
  modelName: string;
  modelSub: string;
  sourceLabel: string;
  currency: string;
  prices: CalcPrices;
  totalTokens: number;
  hitRate: number;
  result: CalcResult;
  missingCacheRead: boolean;
  /** 还没有可展示的花费结果（没填任何单价）时按钮置灰 */
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [domain, setDomain] = useState("");
  const { dark } = useTheme();
  // SSR 初值固定暗色保证水合一致，挂载后校正成站点当前主题
  const [theme, setTheme] = useState<ShareTheme>("dark");
  // 初值 0（卡片时间显示"—"），挂载后再取真实时间；真正出图前 capture() 还会再刷新一次
  const [generatedAt, setGeneratedAt] = useState(0);
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => setDomain(window.location.origin), []);
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
      toast("分享图还没排好版，稍等片刻再试");
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
        <ShareCalcCard
          modelName={modelName}
          modelSub={modelSub}
          sourceLabel={sourceLabel}
          currency={currency}
          prices={prices}
          totalTokens={totalTokens}
          hitRate={hitRate}
          result={result}
          missingCacheRead={missingCacheRead}
          domain={domain}
          generatedAt={generatedAt}
          theme={theme}
        />
      </div>

      <Btn variant="ghost" size="sm" disabled={disabled} loading={busy} onClick={generate}>
        导出分享图
      </Btn>

      <Modal open={open} onClose={() => setOpen(false)} title="分享图已生成" width={720}>
        {imgUrl && (
          <div style={{ display: "grid", gap: 14 }}>
            <img
              src={imgUrl}
              alt="花费估算分享图"
              style={{ width: "100%", borderRadius: 10, border: "1px solid var(--border)" }}
            />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <Btn onClick={() => setOpen(false)}>关闭</Btn>
              <a className="btn btn-primary" href={imgUrl} download={`花费计算-${modelName || "结果"}.png`}>
                保存图片
              </a>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
