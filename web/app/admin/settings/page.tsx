import { PageHeader } from "@/components/PageHeader";
import { AdminSettings } from "@/components/AdminSettings";

export const metadata = { title: "管理面板 · 系统设置" };

export default function AdminSettingsPage() {
  return (
    <>
      <PageHeader
        eyebrow="ADMIN · SETTINGS"
        title="系统设置"
        subtitle="AI 兜底提取与 Tavily 官方价检索，保存后立即生效。"
      />
      <AdminSettings />
    </>
  );
}
