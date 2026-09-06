import { PageHeader } from "@/components/PageHeader";
import { AdminSites } from "@/components/AdminSites";

export const metadata = { title: "管理面板 · 站点管理" };

export default function AdminSitesPage() {
  return (
    <>
      <PageHeader
        eyebrow="ADMIN · SITES"
        title="站点管理"
        subtitle="维护被监控的中转站：价格接口地址、目标模型与启停。"
      />
      <AdminSites />
    </>
  );
}
