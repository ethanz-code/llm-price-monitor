import { Alert } from "@/components/ui";

/** 数据读取失败的警示条。 */
export function SiteAlert({ title, detail, fix }: { title: string; detail: string; fix?: string }) {
  return (
    <Alert tone="warn" title={title} band className="section-gap">
      {detail}
      {fix ? <>。{fix}</> : null}
    </Alert>
  );
}
