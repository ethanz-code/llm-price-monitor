/** 页顶数据摘要条：一行真实数字（站点数、汇率、更新时间等），mono 数字 + 灰标签，细线收底。
 * 页面身份由顶部导航标识，这里只放数据事实，不重复页名。 */
export function PageDigest({ items }: { items: { label: string; value: string }[] }) {
  if (items.length === 0) return null;
  return (
    <div className="page-digest">
      {items.map((item) => (
        <span key={item.label} className="page-digest-item">
          <span className="page-digest-label">{item.label}</span>
          <span className="page-digest-value">{item.value}</span>
        </span>
      ))}
    </div>
  );
}
