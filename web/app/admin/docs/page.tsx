import { PageHeader } from "@/components/PageHeader";
import { AdminDocs } from "@/components/AdminDocs";

export const metadata = { title: "管理面板 · 使用文档" };

export default function AdminDocsPage() {
  return (
    <>
      <PageHeader
        eyebrow="ADMIN · DOCS"
        title="使用文档"
        subtitle="项目 README 实时渲染，改完仓库文档刷新即可看到最新版。"
      />
      <AdminDocs />
    </>
  );
}
