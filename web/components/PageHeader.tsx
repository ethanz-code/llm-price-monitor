import type { ReactNode } from "react";

/** 统一页头：标题 + 副标题，底部细分隔线。当前页由顶部导航标识，不另加装饰性小标签。 */
export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: 24 }}>
        <div>
          <h1 className="page-title">{title}</h1>
          {subtitle && <p className="page-subtitle">{subtitle}</p>}
        </div>
        {actions && <div style={{ paddingBottom: 4, flexShrink: 0 }}>{actions}</div>}
      </div>
    </div>
  );
}
