import Link from "next/link";
import { Button } from "antd";

export default function NotFound() {
  return (
    <div className="page" style={{ minHeight: "50vh", display: "grid", placeItems: "center" }}>
      <div style={{ textAlign: "center", padding: "48px 0" }}>
        <div className="mono" style={{ fontSize: 56, fontWeight: 550, letterSpacing: "-0.04em" }}>
          404
        </div>
        <p style={{ color: "var(--text-2)", margin: "12px 0 24px" }}>
          这个页面不存在，或者已经被移走了。
        </p>
        <Link href="/">
          <Button type="primary">返回首页</Button>
        </Link>
      </div>
    </div>
  );
}
