import { Alert } from "@/components/ui";
import { DajuFace } from "@/components/DajuArt";

/** 数据读取失败的警示条：左侧换成猫脸——出问题时它也盯到了。 */
export function SiteAlert({ title, detail, fix }: { title: string; detail: string; fix?: string }) {
  return (
    <Alert
      tone="warn"
      title={title}
      band
      className="section-gap"
      icon={<DajuFace size={14} className="alert-icon" />}
    >
      {detail}
      {fix ? <>。{fix}</> : null}
    </Alert>
  );
}
