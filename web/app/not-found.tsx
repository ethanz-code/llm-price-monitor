import Link from "next/link";
import { Btn } from "@/components/ui";
import { Illustration404 } from "@/components/Illustration404";

export default function NotFound() {
  return (
    <div className="page" style={{ minHeight: "50vh", display: "grid", placeItems: "center" }}>
      <div style={{ textAlign: "center", padding: "48px 0" }}>
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 8 }}>
          <Illustration404 />
        </div>
        <div className="mono" style={{ fontSize: 44, fontWeight: 550, letterSpacing: "-0.04em" }}>
          404
        </div>
        <p style={{ color: "var(--text-2)", margin: "12px 0 24px" }}>
          这条价格曲线跑到没有页面的地方去了，回首页接着逛吧。
        </p>
        <Link href="/">
          <Btn variant="primary">返回首页</Btn>
        </Link>
      </div>
    </div>
  );
}
