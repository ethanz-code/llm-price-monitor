"use client";

import { useState } from "react";
import Link from "next/link";
import { IconGithub, IconMail, IconWecom } from "./icons";
import { LogoMark } from "./LogoMark";
import { FeedbackModal } from "./FeedbackModal";
import { CONTACT_EMAIL, ContactModal } from "./ContactModal";

/** 全站页脚：品牌 + 一句话说明合并数据免责；联系方式图标与低调管理入口在底行。 */
export function SiteFooter() {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [contactOpen, setContactOpen] = useState(false);
  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <div className="site-footer-brand">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <LogoMark size={20} />
            <span style={{ fontWeight: 600, color: "var(--text)" }}>LLM 价格监控</span>
          </div>
          <p>
            盯着各家 API 中转站的价格、折扣、渠道状态和公告，数据抓取自各站点公开页面，仅供研究参考，不构成对任何站点的使用推荐。
          </p>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <a
              className="footer-icon"
              href="https://github.com/ethanz-code/llm-price-monitor"
              target="_blank"
              rel="noreferrer"
              aria-label="GitHub 仓库"
            >
              <IconGithub size={18} />
            </a>
            <a className="footer-icon" href={`mailto:${CONTACT_EMAIL}`} aria-label="邮件联系">
              <IconMail size={18} />
            </a>
            <button
              type="button"
              className="footer-icon"
              aria-label="企业微信联系"
              onClick={() => setContactOpen(true)}
            >
              <IconWecom size={18} />
            </button>
          </div>
        </div>
      </div>
      <div className="site-footer-meta">
        <span>© 2026 LLM 价格监控</span>
        <span className="site-footer-meta-links">
          <Link href="/discount">折扣对比</Link>
          <Link href="/catalog">厂商定价</Link>
          <button type="button" onClick={() => setFeedbackOpen(true)}>提建议</button>
          <Link href="/admin" className="footer-admin-link">管理</Link>
        </span>
      </div>
      <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
      <ContactModal open={contactOpen} onClose={() => setContactOpen(false)} />
    </footer>
  );
}
