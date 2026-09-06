import { PageHeader } from "@/components/PageHeader";
import { AdminTasks } from "@/components/AdminTasks";

export const metadata = { title: "管理面板 · 采集任务" };

export default function AdminTasksPage() {
  return (
    <>
      <PageHeader
        eyebrow="ADMIN · TASKS"
        title="采集任务"
        subtitle="全站价格采集与官方价库刷新的后台任务记录。"
      />
      <AdminTasks />
    </>
  );
}
