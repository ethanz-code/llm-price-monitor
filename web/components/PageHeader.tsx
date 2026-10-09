import type { ReactNode } from "react";

/** 统一页头：eyebrow 路径、大标题、副标题，底部细分隔线 + 单色光晕。 */
export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div className="page-eyebrow">{eyebrow}</div>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: 24 }}>
        <div>
          <h1 className="page-title">{title}</h1>
          <p className="page-subtitle">{subtitle}</p>
        </div>
        {actions && <div style={{ paddingBottom: 4, flexShrink: 0 }}>{actions}</div>}
      </div>
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: string;
  /** 标签前色点的语义色（对齐实时亮点的指标分色） */
  tone?: "green" | "yellow" | "blue" | "red" | "gray";
}) {
  return (
    <div className="stat-card">
      <div className="stat-label">
        {tone && <span aria-hidden className={`stat-dot dot-${tone}`} />}
        {label}
      </div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}
