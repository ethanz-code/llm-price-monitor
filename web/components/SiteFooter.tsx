"use client";

import Link from "next/link";
import { IconGithub } from "./icons";
import { LogoMark } from "./LogoMark";

/** 全站页脚：品牌 + 一句话说明合并数据免责；管理入口只以低调文字链接出现在底行。 */
export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <div className="site-footer-brand">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <LogoMark size={20} />
            <span style={{ fontWeight: 600, color: "var(--text)" }}>LLM 价格监控</span>
          </div>
          <p>
            多站点价格快照、厂商官方价锚定与变化事件追踪。页面数据均为特定时间的取证快照，仅供研究参考，不构成对任何站点的使用推荐。
          </p>
          <a
            className="footer-github"
            href="https://github.com/ethanz-code/llm-price-monitor"
            target="_blank"
            rel="noreferrer"
            aria-label="GitHub 仓库"
          >
            <IconGithub size={18} />
          </a>
        </div>
      </div>
      <div className="site-footer-meta">
        <span>© 2026 LLM 价格监控</span>
        <span className="site-footer-meta-links">
          <Link href="/discount">折扣口径</Link>
          <Link href="/official">官方价来源</Link>
          <Link href="/admin" className="footer-admin-link">管理</Link>
        </span>
      </div>
    </footer>
  );
}
