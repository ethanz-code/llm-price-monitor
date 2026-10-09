"use client";

import { useState } from "react";
import { Btn, Check, Modal, Tip } from "../ui";
import { formatTime } from "@/lib/format";
import type { SiteCollectHealth, SiteCollectIssue } from "@/lib/types";

/** 删除站点确认弹窗：行内单删与勾选批删共用；勾选清理时后端同时删除该站点的历史与事件数据。 */
function DeleteSitesModal({
  ids,
  onClose,
  onDeleted,
}: {
  ids: string[];
  onClose: () => void;
  onDeleted: (purge: boolean) => void;
}) {
  const [purge, setPurge] = useState(false);
  const batch = ids.length > 1;
  return (
    <Modal
      open
      onClose={onClose}
      title={batch ? `删除所选站点（${ids.length}）` : "删除站点"}
      width={460}
      footer={
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <Btn onClick={onClose}>取消</Btn>
          <Btn variant="primary" onClick={() => onDeleted(purge)}>
            确认删除
          </Btn>
        </div>
      }
    >
      <div style={{ display: "grid", gap: 12 }}>
        <p style={{ color: "var(--text-2)", margin: 0, fontSize: 13.5, lineHeight: 1.7 }}>
          将删除{batch ? `所选 ${ids.length} 个站点` : <>站点 <span className="mono">{ids[0]}</span></>} 的采集配置；
          历史价格与事件数据默认保留在数据库中。
        </p>
        <Check checked={purge} onChange={setPurge}>
          同时清理{batch ? "这些站点" : "该站点"}的历史价格、事件与状态数据（不可恢复）
        </Check>
      </div>
    </Modal>
  );
}

/* ---------- 行内采集异常提示 ---------- */

const HEALTH_SECTIONS: { key: "price" | "status" | "notice"; label: string }[] = [
  { key: "price", label: "价格采集" },
  { key: "status", label: "渠道状态" },
  { key: "notice", label: "站点公告" },
];

/** 站点行内的采集异常小图标：红=采集失败，黄=需认证/没抓到数据；悬停看三类明细，下一轮采集正常自动消失。 */
function SiteHealthTip({ health }: { health: SiteCollectHealth | undefined }) {
  const issues = HEALTH_SECTIONS.map((section) => ({ ...section, issue: health?.[section.key] })).filter(
    (entry): entry is { key: "price" | "status" | "notice"; label: string; issue: SiteCollectIssue } =>
      Boolean(entry.issue),
  );
  if (issues.length === 0) return null;
  const tone = issues.some((entry) => entry.issue.level === "error") ? ("red" as const) : ("yellow" as const);
  return (
    <Tip
      tone={tone}
      ariaLabel="采集异常提示"
      content={
        <span style={{ display: "grid", gap: 8 }}>
          {issues.map(({ key, label, issue }) => (
            <span key={key} style={{ display: "grid", gap: 1 }}>
              <span style={{ display: "inline-flex", alignItems: "baseline", gap: 8 }}>
                <span style={{ fontWeight: 550, color: issue.level === "error" ? "#fca5a5" : "#fcd34d" }}>
                  {label}·{issue.level === "error" ? "错误" : "注意"}
                </span>
                <span style={{ opacity: 0.6, fontSize: 11 }}>{formatTime(issue.time)}</span>
              </span>
              <span style={{ wordBreak: "break-all" }}>{issue.message}</span>
            </span>
          ))}
        </span>
      }
    />
  );
}

/** 站点管理页顶部的配置速查：按场景一句话"怎么配"，再加几条最容易踩的注意事项 */
function SitesGuide() {
  const scenarios: { when: string; how: string }[] = [
    { when: "接口公开，直接能访问", how: "采集方式用「接口直采」，地址填接口即可，不用配认证" },
    {
      when: "接口要令牌",
      how: "认证方式选「固定令牌」，令牌填进「认证与续签」；下面「凭证注入」决定它怎么带进价格、渠道状态、公告三处请求（默认 Authorization: Bearer）",
    },
    {
      when: "令牌很快过期（如 new-api 登录会话）",
      how: "认证方式选「登录会话自动续签」，把浏览器里 new_api_refresh Cookie 的值贴进 Refresh Token 点「测试续签」；Access Token 留空也行，采集被拒时会自动续签补上。贴完回浏览器重新登录一次（两边各用各的会话），每个登录最长保持 30 天",
    },
    {
      when: "直接抓页面是空壳（价格靠 JS 算，如单页应用）",
      how: "采集方式选「网页模式（Headless）」，每次用真浏览器打开渲染，Cookie、localStorage 从 F12 → 应用 → Cookies 里抄；接口直采不再自动换浏览器重试",
    },
    { when: "渠道状态、站点公告", how: "各自卡片里填地址就行，认证自动沿用站点凭证；被拒时会自动续签一次再试" },
  ];
  const notes = [
    "要监控哪些模型在页面顶部「监控模型」里统一配，所有站点共用；价格抽取按 8 个模型一批调用 AI，清单越长单轮采集越慢",
    "目录每天刷新时会自动把各厂商最新发布的模型补进监控清单；从清单删掉的模型不会被加回，选项按发布日期新→旧排",
    "认证在「认证与续签」里配一处：凭证注入把 token 送到价格、渠道状态、公告三处，接口请求头里手写的认证头会被它盖掉并自动移除",
    "要让某处带 Cookie（如 new_api_refresh），在凭证注入那行把头名填成 cookie、值填成 new_api_refresh=${refresh_token}，续签换新会自动跟着变",
    "localStorage 不会发给服务器，只在页面脚本内部读取；页面的 JS 靠它取登录信息时才需要填。值填 ${access_token} 就是「认证与续签」里管的站点令牌，续签换新后自动跟着变",
    "配完点行内的「测试」马上验证；站点行的红/黄小标就是最近一次采集的异常提示",
  ];
  return (
    <div style={{ display: "grid", gap: 12, paddingTop: 10, borderTop: "1px solid var(--border)" }}>
      <div style={{ display: "grid", gap: 6 }}>
        {scenarios.map((item) => (
          <div
            key={item.when}
            style={{ display: "grid", gridTemplateColumns: "minmax(140px, 260px) 1fr", gap: 10, fontSize: 12.5 }}
          >
            <span style={{ color: "var(--text-2)", fontWeight: 550 }}>{item.when}</span>
            <span style={{ color: "var(--text-3)" }}>{item.how}</span>
          </div>
        ))}
      </div>
      <div style={{ display: "grid", gap: 4 }}>
        {notes.map((note) => (
          <span key={note} style={{ fontSize: 12, color: "var(--text-3)" }}>
            · {note}
          </span>
        ))}
      </div>
    </div>
  );
}

export { DeleteSitesModal, SiteHealthTip, SitesGuide };
