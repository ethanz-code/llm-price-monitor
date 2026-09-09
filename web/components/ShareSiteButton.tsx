"use client";

import { useEffect, useRef, useState } from "react";
import { toPng } from "html-to-image";
import { Btn, Modal, Seg, toast } from "./ui";
import { ShareSiteCard, type ShareTheme } from "./ShareSiteCard";
import type { AvailabilityPoint, ChannelDotRow, LatencyPoint } from "@/lib/channelStatus";

/** 中转站实时状况分享图：点击生成 PNG（含站点状态、监控站域名与介绍），预览后一键保存。 */
export function ShareSiteButton({
  siteName,
  homepage,
  availability,
  channels,
  latency,
  latencyNames,
}: {
  siteName: string;
  homepage: string;
  availability: AvailabilityPoint[];
  channels: ChannelDotRow[];
  latency: LatencyPoint[];
  latencyNames: string[];
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [domain, setDomain] = useState("");
  // 分享图配色：默认暗色（历史行为），生成后在弹窗里可切亮/暗并即时重出图
  const [theme, setTheme] = useState<ShareTheme>("dark");
  const hostRef = useRef<HTMLDivElement>(null);

  // 监控站域名在客户端取：分享图要的是"现在打开的这个网站"的地址
  useEffect(() => setDomain(window.location.origin), []);
  const [generatedAt, setGeneratedAt] = useState(() => Math.floor(Date.now() / 1000));

  /** 把离屏卡片栅格化成 PNG；theme 切换后先等 React 提交再截，避免截到旧配色。 */
  async function capture() {
    const host = hostRef.current;
    const card = host?.firstElementChild as HTMLElement | null;
    if (!card) return;
    setBusy(true);
    try {
      setGeneratedAt(Math.floor(Date.now() / 1000));
      await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
      const url = await toPng(card, { pixelRatio: 2, cacheBust: true });
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

  async function switchTheme(next: string) {
    if (next === theme) return;
    setTheme(next as ShareTheme);
    await capture();
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
          latency={latency}
          latencyNames={latencyNames}
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
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 13, color: "var(--text-2)" }}>配色</span>
              <Seg
                value={theme}
                onChange={switchTheme}
                options={[
                  { value: "dark", label: "暗色" },
                  { value: "light", label: "亮色" },
                ]}
              />
            </div>
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
