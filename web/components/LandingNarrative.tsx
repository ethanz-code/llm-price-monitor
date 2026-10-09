import Link from "next/link";
import { home } from "@/lib/copy";
import {
  IconCalculator,
  IconFileSearch,
  IconMegaphone,
  IconPercent,
  IconPulse,
  IconTrend,
} from "./icons";

/**
 * 首页叙事三区（对照 OpenHands 产品首页骨架：能力总览 → 流水线 → 使用场景），
 * 视觉规格按 openhands.dev 实测提炼：能力区 = 图标芯片横滑卡列（automations 卡），
 * 流水线 = 整块次级底舞台 + 虚线节点路径（signal→shipped stage），
 * 使用场景 = mono 编号 + 箭头的可点卡（use cases 卡）。数据来自 copy.ts，不依赖接口。
 */

/** copy.ts 里 icon 键 → 图标组件的映射，键名即 icons.tsx 导出名 */
const CAP_ICONS = {
  trend: IconTrend,
  pulse: IconPulse,
  "file-search": IconFileSearch,
  percent: IconPercent,
  calculator: IconCalculator,
  megaphone: IconMegaphone,
} as const;

/** 能力总览：横向滚动卡列，每张 = 图标芯片 + 标题 + 一句话 + 底部分类 */
export function CapabilityGrid() {
  return (
    <section className="landing-section landing-statement" id="sec-caps">
      <div className="landing-section-head">
        <div className="landing-section-title">
          <h2>{home.sections.capabilities}</h2>
        </div>
      </div>
      <p className="landing-section-sub">{home.sectionSubs.capabilities}</p>
      <ul className="cap-row">
        {home.capabilities.map((cap) => {
          const Icon = CAP_ICONS[cap.icon as keyof typeof CAP_ICONS];
          return (
            <li key={cap.title} className="cap-card">
              <span className="cap-ico" aria-hidden>
                <Icon size={22} />
              </span>
              <span className="cap-title">{cap.title}</span>
              <p className="cap-desc">{cap.desc}</p>
              <span className="cap-tag">{cap.tag}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** 采集流水线：六步装进一整块舞台，桌面档由虚线串成节点路径，与 docs/pricing.md 口径一致 */
export function PipelineSteps() {
  return (
    <section className="landing-section landing-statement" id="sec-pipeline">
      <div className="pipe-stage">
        <div className="landing-section-head">
          <div className="landing-section-title">
            <h2>{home.sections.pipeline}</h2>
          </div>
        </div>
        <p className="landing-section-sub">{home.sectionSubs.pipeline}</p>
        <ol className="pipe-band">
          {home.pipeline.map((step, index) => (
            <li key={step.name} className="pipe-step">
              <span className="pipe-no mono num" aria-hidden>
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="pipe-name">{step.name}</span>
              <p className="pipe-desc">{step.desc}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/** 使用场景：mono 编号 + 箭头的可点卡，整卡直达对应功能页 */
export function UseCaseCards() {
  return (
    <section className="landing-section landing-statement" id="sec-uses">
      <div className="landing-section-head">
        <div className="landing-section-title">
          <h2>{home.sections.useCases}</h2>
        </div>
      </div>
      <p className="landing-section-sub">{home.sectionSubs.useCases}</p>
      <div className="use-grid">
        {home.useCases.map((use, index) => (
          <Link key={use.title} href={use.href} className="use-card">
            <span className="use-top">
              <span className="use-no mono num" aria-hidden>
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="use-go" aria-hidden>
                →
              </span>
            </span>
            <span className="use-title">{use.title}</span>
            <p className="use-desc">{use.desc}</p>
          </Link>
        ))}
      </div>
    </section>
  );
}
