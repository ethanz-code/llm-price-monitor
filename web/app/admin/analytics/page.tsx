import { AdminAnalytics } from "@/components/AdminAnalytics";

export const metadata = { title: "访问统计 · 管理面板" };

/** 访问统计：页面埋点数据的 KPI、趋势与明细；数据由 AdminAnalytics 浏览器侧拉取。 */
export default function AdminAnalyticsPage() {
  return (
    <>
      <AdminAnalytics />
    </>
  );
}
