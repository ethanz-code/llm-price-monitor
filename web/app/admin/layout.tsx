"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { fetchAuthState } from "@/lib/api";
import { LoadingRows, Skel } from "@/components/ui";
import {
  IconAppstore,
  IconBolt,
  IconBook,
  IconDashboard,
  IconNodes,
  IconSettings,
  IconAim,
} from "@/components/icons";
import { EgressStatus } from "@/components/EgressStatus";

const RAIL = [
  { href: "/admin", label: "概览", icon: <IconDashboard size={15} />, exact: true },
  { href: "/admin/sites", label: "站点管理", icon: <IconAppstore size={15} /> },
  { href: "/admin/pricing-sources", label: "厂商定价源", icon: <IconNodes size={15} /> },
  { href: "/admin/tasks", label: "采集任务", icon: <IconBolt size={15} /> },
  { href: "/admin/ai-logs", label: "AI 日志", icon: <IconAim size={15} /> },
  { href: "/admin/docs", label: "使用文档", icon: <IconBook size={15} /> },
  { href: "/admin/settings", label: "系统设置", icon: <IconSettings size={15} /> },
];

/** 管理面板骨架：登录守卫 + 左侧菜单子路由导航。
 *  未登录跳 /login，未创建账号跳 /setup；守卫通过后才渲染子页。 */
export default function AdminLayout({ children }: React.PropsWithChildren) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchAuthState().then((state) => {
      if (!alive) return;
      if (!state || state.needs_setup) router.replace("/setup");
      else if (!state.is_admin) router.replace("/login");
      else setReady(true);
    });
    return () => {
      alive = false;
    };
  }, [router]);

  // 守卫未过时先画外壳（菜单 + 内容区骨架），避免硬加载 /admin 时整页白屏
  if (!ready) {
    return (
      <div className="page">
        <div className="admin-shell">
          <aside className="admin-rail" aria-hidden>
            {RAIL.map((item, index) => (
              <span key={item.href} style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 10px" }}>
                <Skel w={15} h={15} style={{ borderRadius: 4, flexShrink: 0 }} delay={index * 60} />
                <Skel w={52} h={12} delay={index * 60 + 20} />
              </span>
            ))}
          </aside>
          <div className="admin-content" aria-hidden>
            <LoadingRows rows={7} style={{ padding: "4px 0" }} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      {/* 通栏光晕挂在 .page 之外：视口宽于内容区（如折叠浏览器侧栏）时最右侧不留空缺 */}
      <div className="page">
        <div className="admin-shell">
        <aside className="admin-rail" aria-label="管理面板导航">

          {RAIL.map((item) => {
            const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
            return (
              <Link key={item.href} href={item.href} className={active ? "active" : ""}>
                {item.icon}
                {item.label}
              </Link>
            );
          })}
          <EgressStatus />
          <button
            type="button"
            className="admin-rail-logout"
            onClick={async () => {
              await fetch("/api/auth/logout", { method: "POST" });
              router.replace("/");
            }}
          >
            退出登录
          </button>
        </aside>
          <div className="admin-content">{children}</div>
        </div>
      </div>
    </>
  );
}
