import { AdminTasks } from "@/components/AdminTasks";
import { CollectButton } from "@/components/CollectButton";
import { CatalogRefreshButton } from "@/components/CatalogRefreshButton";

export const metadata = { title: "管理面板 · 采集任务" };

export default function AdminTasksPage() {
  return (
    <>
      <div className="admin-toolbar" style={{ justifyContent: "flex-end" }}>
        <span style={{ display: "inline-flex", gap: 8 }}>
          <CollectButton />
          <CatalogRefreshButton />
        </span>
      </div>
      <AdminTasks />
    </>
  );
}
