"use client";

import { useState } from "react";
import Link from "next/link";
import { IconGithub, IconMail, IconWecom } from "./icons";
import { LogoMark } from "./LogoMark";
import { FeedbackModal } from "./FeedbackModal";
import { CONTACT_EMAIL, ContactModal } from "./ContactModal";
import { ChromeMosaic } from "./ChromeMosaic";
import { footer, site } from "@/lib/copy";

/** 全站页脚：品牌 + 一句话说明合并数据免责；联系方式图标与低调管理入口在底行。 */
export function SiteFooter() {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [contactOpen, setContactOpen] = useState(false);
  return (
    <footer className="site-footer">
      <ChromeMosaic />
      <div className="site-footer-inner">
        <div className="site-footer-brand">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <LogoMark size={20} />
            <span style={{ fontWeight: 600, color: "var(--text)" }}>{site.name}</span>
          </div>
          <p>{footer.brandLine}</p>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <a
              className="footer-icon"
              href="https://github.com/ethanz-code/llm-price-monitor"
              target="_blank"
              rel="noreferrer"
              aria-label={footer.aria.github}
            >
              <IconGithub size={18} />
            </a>
            <a className="footer-icon" href={`mailto:${CONTACT_EMAIL}`} aria-label={footer.aria.mail}>
              <IconMail size={18} />
            </a>
            <button
              type="button"
              className="footer-icon"
              aria-label={footer.aria.wecom}
              onClick={() => setContactOpen(true)}
            >
              <IconWecom size={18} />
            </button>
          </div>
        </div>
      </div>
      <div className="site-footer-meta">
        <span>© 2026 {site.name}</span>
        <span className="site-footer-meta-links">
          <Link href="/catalog">{footer.links.catalog}</Link>
          <button type="button" onClick={() => setFeedbackOpen(true)}>{footer.links.feedback}</button>
          <Link href="/admin" className="footer-admin-link">{footer.links.admin}</Link>
        </span>
      </div>
      <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
      <ContactModal open={contactOpen} onClose={() => setContactOpen(false)} />
    </footer>
  );
}
