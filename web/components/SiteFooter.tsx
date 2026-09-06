import Link from "next/link";
import { LogoMark } from "./LogoMark";

/** 全站页脚：品牌、数据入口、关于与免责声明。 */
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
            中转站价格取证、历史监控与官方价折扣审计。价格事实仅来自直接请求的 HTTP JSON
            响应，不做任何猜测。
          </p>
        </div>
        <div className="site-footer-col">
          <div className="site-footer-title">监控数据</div>
          <Link href="/overview">价格总览</Link>
          <Link href="/history">历史与事件</Link>
          <Link href="/official">官方价库</Link>
          <Link href="/discount">折扣对比</Link>
        </div>
        <div className="site-footer-col">
          <div className="site-footer-title">关于</div>
          <a href="https://github.com/ethanz-code/llm-price-monitor" target="_blank" rel="noreferrer">
            GitHub 仓库
          </a>
          <span>免责声明：所有价格来自公开接口的取证快照，仅供研究参考，不构成对任何站点的使用推荐。</span>
        </div>
      </div>
      <div className="site-footer-meta">
        <span>© 2026 LLM 价格监控</span>
        <span className="mono">data over vibes</span>
      </div>
    </footer>
  );
}
