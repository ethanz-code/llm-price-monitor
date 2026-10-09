"use client";

import { IconGithub } from "./icons";
import { LogoMark } from "./LogoMark";

/** 全站页脚：品牌介绍、数据说明与 GitHub 入口，紧凑单区布局。 */
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
            面向 API 中转站的价格取证与监控：周期性请求各站点价格接口，与厂商官方价相除得到折扣，并把每次采集沉淀为历史曲线与变化事件。所有价格均来自直接请求的
            HTTP JSON 响应，逐条可溯源。
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
        <div className="site-footer-note">
          <div className="site-footer-title">数据说明</div>
          页面展示的价格均为特定时间的取证快照，可能与站点当前实时价格不同，也不构成对任何站点的使用推荐；折扣对比以厂商官方价为锚点，汇率快照与来源链接随每条记录一同展示。
        </div>
      </div>
      <div className="site-footer-meta">© 2026 LLM 价格监控</div>
    </footer>
  );
}
