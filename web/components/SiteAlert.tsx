import { Alert } from "antd";

export function SiteAlert({ title, detail, fix }: { title: string; detail: string; fix?: string }) {
  return (
    <Alert
      className="section-gap"
      type="warning"
      showIcon
      message={title}
      description={
        <span>
          {detail}
          {fix ? <>。{fix}</> : null}
        </span>
      }
    />
  );
}
