"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { fetchMeta } from "@/lib/api";
import {
  IconAppstore,
  IconBolt,
  IconBook,
  IconDashboard,
  IconHistory,
  IconSettings,
} from "@/components/icons";

const RAIL = [
  { href: "/admin", label: "概览", icon: <IconDashboard size={15} />, exact: true },
  { href: "/admin/sites", label: "站点管理", icon: <IconAppstore size={15} /> },
  { href: "/admin/tasks", label: "采集任务", icon: <IconBolt size={15} /> },
  { href: "/admin/events", label: "事件审计", icon: <IconHistory size={15} /> },
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
    fetchMeta().then((meta) => {
      if (!alive) return;
      if (!meta || meta.needs_setup) router.replace("/setup");
      else if (!meta.is_admin) router.replace("/login");
      else setReady(true);
    });
    return () => {
      alive = false;
    };
  }, [router]);

  if (!ready) return null;

  return (
    <div className="page">
      <div className="admin-shell">
        <aside className="admin-rail" aria-label="管理面板导航">
          <div className="admin-rail-title">Admin</div>
          {RAIL.map((item) => {
            const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
            return (
              <Link key={item.href} href={item.href} className={active ? "active" : ""}>
                {item.icon}
                {item.label}
              </Link>
            );
          })}
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
  );
}
