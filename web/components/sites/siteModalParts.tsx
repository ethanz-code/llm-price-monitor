"use client";

import { useState } from "react";
import { Btn, Input, Modal, Seg, toast } from "../ui";
import { IconChevronRight, IconClose, IconLock, IconPlus } from "../icons";
import { advancedJsonError, dictToRows, rowsToText, type KvRow } from "./siteShared";
import { SettingRow } from "../ui";
import { ACCESS_VAR, type InjectRule } from "./siteAuth";
import type { SiteConfig } from "@/lib/types";

/** 主弹窗中部的入口卡片：点击打开对应子弹窗；configured 时标注"已配置" */function EntryCard({
  title,
  desc,
  configured,
  onClick,
}: {
  title: string;
  desc: string;
  configured: boolean;
  onClick: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick();
        }
      }}
      style={{
        display: "grid",
        gap: 3,
        padding: "10px 12px",
        border: "1px solid var(--border)",
        borderRadius: 8,
        background: "var(--panel)",
        cursor: "pointer",
        transition: "border-color 150ms ease, background 150ms ease",
      }}
      onMouseEnter={(event) => {
        event.currentTarget.style.borderColor = "var(--border-strong)";
        event.currentTarget.style.background = "var(--panel-2)";
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.borderColor = "var(--border)";
        event.currentTarget.style.background = "var(--panel)";
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 13.5, fontWeight: 550 }}>{title}</span>
        {configured && <span style={{ fontSize: 11, color: "var(--tone-green-text)" }}>已配置</span>}
      </span>
      <span style={{ fontSize: 12, color: "var(--text-3)", wordBreak: "break-all" }} title={desc}>
        {desc}
      </span>
    </div>
  );
}

/** 子弹窗底部按钮：取消丢弃本次修改，确定才写回主弹窗草稿 */
function SubModalFooter({
  onCancel,
  onConfirm,
  confirmDisabled,
  confirmTitle,
  confirmLabel,
}: {
  onCancel: () => void;
  onConfirm: () => void;
  confirmDisabled?: boolean;
  confirmTitle?: string;
  /** 覆盖主按钮文案：认证子弹窗拦截未插入时换成「插入并确定」 */
  confirmLabel?: string;
}) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
      <Btn onClick={onCancel}>取消</Btn>
      <Btn variant="primary" disabled={confirmDisabled} title={confirmTitle} onClick={onConfirm}>
        {confirmLabel ?? "确定"}
      </Btn>
    </div>
  );
}

/* ---------- 子弹窗：请求头编辑区（各地址专用请求头共用） ---------- */

/** 只读注入行的悬停提示：值由凭证注入统一管理，这里只展示 */
const INJECTED_TITLE = "由「认证与续签 → 凭证注入」统一管理，采集时自动带上；要改去那里";

/** 单个地址的专用请求头编辑区：行编辑 + 覆盖提醒。warningKeys 为该地址 headers 里写死的认证头提示；
 * injected 为该地址的凭证注入规则，有值时在最上方只读展示（采集时它优先于下面手写的同名头）。 */
function HeadersEditor({
  rows,
  onChange,
  warningKeys,
  injected,
  hint = "只发给这个地址；认证头在「认证与续签 → 凭证注入」里配，这里写的同名头会被它盖掉",
  keyPlaceholder = "名字，如 X-API-Key",
  valuePlaceholder = "值",
}: {
  rows: KvRow[];
  onChange: (next: KvRow[]) => void;
  warningKeys: string[];
  injected?: InjectRule | null;
  /** 传 null 表示这条规则已在同一弹窗里说过一次，不再重复 */
  hint?: string | null;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const authKeys = warningKeys.filter((key) => !/cookie/i.test(key));
  const cookieKeys = warningKeys.filter((key) => /cookie/i.test(key));
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <span style={{ fontSize: 13.5 }}>请求头</span>
      {hint && <span style={{ fontSize: 12, color: "var(--text-3)" }}>{hint}</span>}
      {authKeys.length > 0 && (
        <span style={{ fontSize: 12, color: "var(--tone-yellow-text)" }}>
          {authKeys.join("、")} 里写死了认证头；认证统一在「认证与续签 → 凭证注入」里配，
          这里写的同名头请求时会被它盖掉（保存时也会移除）
        </span>
      )}
      {cookieKeys.length > 0 && (
        <span style={{ fontSize: 12, color: "var(--tone-yellow-text)" }}>
          {cookieKeys.join("、")} 里写死了 Cookie；凭证注入没往这处塞 Cookie 时照常发送，塞了则以注入的为准
        </span>
      )}
      {injected && injected.header.trim() && injected.value.trim() && (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Input value={injected.header} disabled title={INJECTED_TITLE} ariaLabel="凭证注入的头名（只读）" style={{ flex: 1, minWidth: 120 }} />
          <Input value={injected.value} disabled title={INJECTED_TITLE} ariaLabel="凭证注入的值（只读）" style={{ flex: 2, minWidth: 160 }} />
          <span title={INJECTED_TITLE} style={{ width: 28, display: "inline-flex", justifyContent: "center", color: "var(--text-3)" }} aria-hidden>
            <IconLock size={14} />
          </span>
        </div>
      )}
      <KeyValueRows rows={rows} onChange={onChange} keyPlaceholder={keyPlaceholder} valuePlaceholder={valuePlaceholder} ariaPrefix="请求头" />
    </div>
  );
}

/* ---------- 子弹窗：渠道状态 ---------- */

function StatusSubModal({
  initialUrl,
  initialHeadersText,
  warningKeys,
  injected,
  onCommit,
  onClose,
}: {
  initialUrl: string;
  initialHeadersText: string;
  warningKeys: string[];
  /** 渠道状态这一路的凭证注入规则，有值时在请求头区只读展示 */
  injected?: InjectRule | null;
  onCommit: (url: string, headersText: string) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState(initialUrl);
  const [headerRows, setHeaderRows] = useState<KvRow[]>(() => dictToRows(initialHeadersText));
  return (
    <Modal
      open
      onClose={onClose}
      title="渠道状态"
      width={560}
      footer={
        <SubModalFooter
          onCancel={onClose}
          onConfirm={() => onCommit(url, rowsToText(headerRows))}
        />
      }
    >
      <div style={{ display: "grid", gap: 14 }}>
        <SettingRow label="渠道状态 URL" hint="填站点的渠道状态接口地址，每次采集会顺带检查各渠道是否正常，有变化会记成事件">
          <Input
            value={url}
            onChange={setUrl}
            placeholder="https://example.com/api/status"
            style={{ width: "min(380px, 100%)" }}
          />
        </SettingRow>
        <HeadersEditor rows={headerRows} onChange={setHeaderRows} warningKeys={warningKeys} injected={injected} />
      </div>
    </Modal>
  );
}

/* ---------- 子弹窗：站点公告 ---------- */

function NoticeSubModal({
  initialUrl,
  initialHeadersText,
  warningKeys,
  injected,
  onCommit,
  onClose,
}: {
  initialUrl: string;
  initialHeadersText: string;
  warningKeys: string[];
  /** 站点公告这一路的凭证注入规则，有值时在请求头区只读展示 */
  injected?: InjectRule | null;
  onCommit: (url: string, headersText: string) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState(initialUrl);
  const [headerRows, setHeaderRows] = useState<KvRow[]>(() => dictToRows(initialHeadersText));
  return (
    <Modal
      open
      onClose={onClose}
      title="站点公告"
      width={560}
      footer={<SubModalFooter onCancel={onClose} onConfirm={() => onCommit(url, rowsToText(headerRows))} />}
    >
      <div style={{ display: "grid", gap: 14 }}>
        <SettingRow
          label="站点公告 URL"
          hint="留空会自动抓站点的 /api/notice（new-api/one-api 都是这个地址）；公告内容有变化时会记成事件。公告请求会自动带上站点认证，不用重复填"
        >
          <Input
            value={url}
            onChange={setUrl}
            placeholder="https://example.com/api/notice"
            style={{ width: "min(380px, 100%)" }}
          />
        </SettingRow>
        <HeadersEditor rows={headerRows} onChange={setHeaderRows} warningKeys={warningKeys} injected={injected} />
      </div>
    </Modal>
  );
}

/** 价格采集子弹窗提交回主弹窗的字段集合 */
type PriceFields = {
  url: string;
  headersText: string;
  headless: HeadlessForm;
  ratioUrl: string;
};

/** 网页模式·Headless子弹窗的表单数据（localStorage 用行数组方便增删） */
type HeadlessForm = {
  enabled: boolean;
  cookies: { name: string; value: string }[];
  localStorage: { key: string; value: string }[];
  waitSeconds: string;
};

/* ---------- 子弹窗：网页模式·Headless ---------- */

/** 键值对编辑区：多行两列输入 + 删除/添加按钮；Cookie 和 localStorage 共用 */
function KeyValueRows({
  rows,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
  ariaPrefix,
  quickFill,
}: {
  rows: { key: string; value: string }[];
  onChange: (next: { key: string; value: string }[]) => void;
  keyPlaceholder: string;
  valuePlaceholder: string;
  ariaPrefix: string;
  /** 传入时在列表上方给一个快捷按钮，把这段文本（如 ${access_token}）填进一行，免手打 */
  quickFill?: string;
}) {
  // 底部永远留一行空行当"添加"入口（只在显示层补，不进数据）；保存时空行自动忽略
  const last = rows[rows.length - 1];
  const display = !last || last.key.trim() || last.value.trim() ? [...rows, { key: "", value: "" }] : rows;

  function insertQuickFill() {
    if (!quickFill) return;
    const tail = rows[rows.length - 1];
    onChange(
      tail && !tail.key.trim() && !tail.value.trim()
        ? [...rows.slice(0, -1), { key: tail.key, value: quickFill }]
        : [...rows, { key: "", value: quickFill }]
    );
  }

  return (
    <div style={{ display: "grid", gap: 6 }}>
      {quickFill && (
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <Btn variant="text" size="sm" onClick={insertQuickFill} title={`值里带 ${ACCESS_VAR} 就引用站点令牌，续签换新后自动跟着变`}>
            <IconPlus size={13} /> 插入 {ACCESS_VAR}
          </Btn>
        </div>
      )}
      {display.map((row, index) => {
        const ghost = index === display.length - 1 && !row.key.trim() && !row.value.trim();
        return (
          <div key={index} style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Input
              value={row.key}
              ariaLabel={`${ariaPrefix} 名字 ${index + 1}`}
              onChange={(next) => onChange(display.map((item, i) => (i === index ? { ...item, key: next } : item)))}
              placeholder={keyPlaceholder}
              style={{ flex: 1, minWidth: 120 }}
            />
            <Input
              value={row.value}
              ariaLabel={`${ariaPrefix} 值 ${index + 1}`}
              onChange={(next) => onChange(display.map((item, i) => (i === index ? { ...item, value: next } : item)))}
              placeholder={valuePlaceholder}
              style={{ flex: 2, minWidth: 160 }}
            />
            {ghost ? (
              <span style={{ width: 28 }} aria-hidden />
            ) : (
              <Btn
                variant="text"
                size="sm"
                ariaLabel={`删除第 ${index + 1} 条`}
                title="删除"
                onClick={() => onChange(display.filter((_, i) => i !== index))}
              >
                <IconClose size={13} />
              </Btn>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Headless 配置区（受控）：价格采集子弹窗内使用；只在网页模式下显示 */
function HeadlessSection({
  value,
  onChange,
}: {
  value: HeadlessForm;
  onChange: (next: HeadlessForm) => void;
}) {
  const [cookiesOpen, setCookiesOpen] = useState(false);
  const [storageOpen, setStorageOpen] = useState(false);
  // Cookie 用 key/value 行编辑，写回时再映射回 name/value
  const cookieRows = value.cookies.map((item) => ({ key: item.name, value: item.value }));
  const cookieCount = value.cookies.filter((item) => item.name.trim() || item.value.trim()).length;
  const storageCount = value.localStorage.filter((item) => item.key.trim() || item.value.trim()).length;
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={{ display: "grid", gap: 6 }}>
        <button
          type="button"
          className="disclosure-row"
          aria-expanded={cookiesOpen}
          onClick={() => setCookiesOpen(!cookiesOpen)}
        >
          <span className="caret" aria-hidden>
            <IconChevronRight size={13} />
          </span>
          <span style={{ fontWeight: 500, whiteSpace: "nowrap" }}>Cookie</span>
          <span
            style={{
              flex: 1,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              color: "var(--text-3)",
            }}
          >
            {cookieCount > 0 ? `已配 ${cookieCount} 条` : "未配置"}
          </span>
        </button>
        {cookiesOpen && (
          <>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              {"登录站点后按 F12 → 应用（Application）→ Cookies，把需要的键值抄进来；只填名字和值，域名、路径会按采集地址自动带上。值里带 ${access_token} 就是「认证与续签」里管的站点令牌，续签换新后自动跟着变"}
            </span>
            <KeyValueRows
              rows={cookieRows}
              onChange={(rows) =>
                onChange({ ...value, cookies: rows.map((row) => ({ name: row.key, value: row.value })) })
              }
              keyPlaceholder="名字，如 session"
              valuePlaceholder="值，如 abc123"
              ariaPrefix="Cookie"
              quickFill={ACCESS_VAR}
            />
          </>
        )}
      </div>
      <div style={{ display: "grid", gap: 6 }}>
        <button
          type="button"
          className="disclosure-row"
          aria-expanded={storageOpen}
          onClick={() => setStorageOpen(!storageOpen)}
        >
          <span className="caret" aria-hidden>
            <IconChevronRight size={13} />
          </span>
          <span style={{ fontWeight: 500, whiteSpace: "nowrap" }}>localStorage</span>
          <span
            style={{
              flex: 1,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              color: "var(--text-3)",
            }}
          >
            {storageCount > 0 ? `已配 ${storageCount} 条` : "未配置"}
          </span>
        </button>
        {storageOpen && (
          <>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              {"浏览器不会把 localStorage 发给服务器，只在页面内部被脚本读取；页面的 JS 靠它取登录信息时才需要填。值填 ${access_token} 就是「认证与续签」里管的站点令牌，续签换新后自动跟着变"}
            </span>
            <KeyValueRows
              rows={value.localStorage}
              onChange={(rows) => onChange({ ...value, localStorage: rows })}
              keyPlaceholder="键，如 token"
              valuePlaceholder="值"
              ariaPrefix="localStorage"
              quickFill={ACCESS_VAR}
            />
          </>
        )}
      </div>
      <SettingRow label="等待时间（秒）" hint="页面加载后等多久再读价格，页面慢就调大">
        <Input
          value={value.waitSeconds}
          type="number"
          ariaLabel="等待时间（秒）"
          onChange={(next) => onChange({ ...value, waitSeconds: next })}
          style={{ width: 100 }}
        />
      </SettingRow>
    </div>
  );
}

/* ---------- 子弹窗：价格采集（采集方式 + 价格接口 + Headless + 倍率接口） ---------- */

/** 采集方式：接口直采（纯接口请求，抓到空壳即失败） / 网页模式（直接用 Headless 浏览器开页面）。只作用于价格采集 */
type CollectMode = "api" | "browser";

const COLLECT_LABELS: Record<CollectMode, string> = { api: "接口直采", browser: "网页模式（Headless）" };

function PriceSubModal({
  initial,
  warningKeys,
  injected,
  onCommit,
  onClose,
}: {
  initial: PriceFields;
  /** 价格接口 headers 里写死认证头的覆盖提醒 */
  warningKeys: string[];
  /** 价格采集这一路的凭证注入规则，有值时在请求头区只读展示 */
  injected?: InjectRule | null;
  onCommit: (next: PriceFields) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState(initial.url);
  const [headerRows, setHeaderRows] = useState<KvRow[]>(() => dictToRows(initial.headersText));
  const [headless, setHeadless] = useState(initial.headless);
  const [ratioUrl, setRatioUrl] = useState(initial.ratioUrl);

  // 采集方式只影响价格这一路（渠道状态、公告始终走接口），所以收在本弹窗里
  function setCollectMode(next: CollectMode) {
    const enabled = next === "browser";
    if (enabled === headless.enabled) return;
    const waitSeconds = enabled && !headless.waitSeconds.trim() ? "3" : headless.waitSeconds;
    setHeadless({ ...headless, enabled, waitSeconds });
  }

  function commit() {
    // 等待时间收敛到 0~60；填空或非法回落默认 3
    const parsed = Number(headless.waitSeconds.trim());
    const waitSeconds = Number.isFinite(parsed) ? Math.min(60, Math.max(0, Math.round(parsed))) : 3;
    onCommit({
      url,
      headersText: rowsToText(headerRows),
      headless: {
        ...headless,
        cookies: headless.cookies.filter((item) => item.name.trim()),
        localStorage: headless.localStorage.filter((item) => item.key.trim()),
        waitSeconds: String(waitSeconds),
      },
      ratioUrl,
    });
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="价格采集"
      width={640}
      footer={<SubModalFooter onCancel={onClose} onConfirm={commit} />}
    >
      <div style={{ display: "grid", gap: 14 }}>
        {/* 采集方式只作用于价格采集，模式定了才知道下面该展示接口请求头还是浏览器登录态 */}
        <SettingRow
          label="采集方式"
          hint={
            headless.enabled
              ? "页面要在浏览器里跑 JS 才能显示价格（直接抓是空壳，常见于单页应用）时选它：每次都用真浏览器打开并渲染，Cookie、localStorage 登录态一并注入"
              : "先当接口请求（Cookie 等登录态照常随请求头带上）；抓到空壳页（常见于单页应用）就采不到价格，换成「网页模式（Headless）」就好，一般站点选这个就够"
          }
        >
          <Seg
            value={headless.enabled ? "browser" : "api"}
            onChange={(value) => setCollectMode(value === "browser" ? "browser" : "api")}
            options={(Object.keys(COLLECT_LABELS) as CollectMode[]).map((value) => ({
              value,
              label: COLLECT_LABELS[value],
            }))}
          />
        </SettingRow>
        {/* 上块：价格接口 */}
        <div style={{ display: "grid", gap: 14 }}>
          <SettingRow
            label="价格接口 URL"
            hint="填价格接口地址或网页地址都行；要带参数就直接拼在地址后面，如 ?page=1&lang=zh"
          >
            <Input
              value={url}
              onChange={setUrl}
              placeholder="https://example.com/api/pricing"
              style={{ width: "min(380px, 100%)" }}
            />
          </SettingRow>
          {/* 请求头只在接口直采下编辑；网页模式不发送请求头，配置照旧保存，切回接口直采恢复显示 */}
          {!headless.enabled && (
            <HeadersEditor rows={headerRows} onChange={setHeaderRows} warningKeys={warningKeys} injected={injected} />
          )}
          {/* 浏览器登录态（Cookie/localStorage/等待时间）只在网页模式下编辑；接口直采纯接口请求 */}
          {headless.enabled && <HeadlessSection value={headless} onChange={setHeadless} />}
        </div>

        {/* 下块：倍率接口（一般公开接口，Headless 不参与；要专用请求头时走高级 JSON） */}
        <div style={{ display: "grid", gap: 14, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
          <SettingRow
            label="倍率接口 URL"
            hint="填站点的倍率查询地址，采集时按倍率把厂商基准价换算成实售价；留空就直接用基准价。接口要专用请求头时，在「高级 JSON」里给 ratio_url 配 headers"
          >
            <Input
              value={ratioUrl}
              onChange={setRatioUrl}
              placeholder="https://example.com/api/public/model-pricing"
              style={{ width: "min(380px, 100%)" }}
            />
          </SettingRow>
        </div>
      </div>
    </Modal>
  );
}

/* ---------- 子弹窗：高级 JSON ---------- */

function JsonSubModal({
  initial,
  onCommit,
  onClose,
}: {
  initial: string;
  onCommit: (text: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState(initial);
  const [guideOpen, setGuideOpen] = useState(false);
  const error = advancedJsonError(text);
  return (
    <Modal
      open
      onClose={onClose}
      title="高级配置 JSON"
      width={780}
      footer={
        <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
          <Btn
            variant="text"
            size="sm"
            onClick={() => {
              try {
                setText(JSON.stringify(JSON.parse(text) as SiteConfig, null, 2));
              } catch {
                toast("JSON 格式不对，先改对再格式化");
              }
            }}
          >
            格式化
          </Btn>
          <SubModalFooter onCancel={onClose} onConfirm={() => onCommit(text)} confirmDisabled={error !== null} confirmTitle={error ? "高级配置 JSON 格式不对，改好才能确定" : undefined} />
        </div>
      }
    >
      <div style={{ display: "grid", gap: 8 }}>
        <button
          type="button"
          className="disclosure-row"
          aria-expanded={guideOpen}
          onClick={() => setGuideOpen(!guideOpen)}
        >
          <span className="caret" aria-hidden>
            <IconChevronRight size={13} />
          </span>
          <span style={{ fontWeight: 500, whiteSpace: "nowrap" }}>这份 JSON 管什么</span>
          <span
            style={{
              flex: 1,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              color: "var(--text-3)",
            }}
          >
            表单的每个输入框都对应这里的一段；表单没有输入框的字段只能在这里改
          </span>
        </button>
        {guideOpen && (
          <div style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-3)", lineHeight: 1.7 }}>
            <span>主弹窗里每个输入框改的就是这份 JSON 对应的字段，两边改动自动同步；保存时表单不会动只在这里维护的字段。</span>
            <span>没有表单输入框、只能在这里改的字段：</span>
            <span>
              · <span className="mono" style={{ fontWeight: 550, color: "var(--text-2)" }}>request_headers</span>
              {" — 站点级共享请求头（如 Cookie、New-Api-User、Referer），价格、渠道状态、公告三处请求都会带上"}
            </span>
            <span>
              · <span className="mono" style={{ fontWeight: 550, color: "var(--text-2)" }}>cookie / cookies</span>
              {" — 站点级 Cookie（整串或键值两种写法）；新配置建议直接写进 request_headers"}
            </span>
            <span>
              · <span className="mono" style={{ fontWeight: 550, color: "var(--text-2)" }}>networks</span>
              {" — 附加采集地址清单，每一项和主采集地址写法相同，用于多地址轮换"}
            </span>
            <span>其余字段（采集地址、分组白名单、认证与续签、Headless 等）主弹窗都有输入框，回那边改更直观。</span>
          </div>
        )}
        {error ? (
          <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>JSON 格式错误：{error}</span>
        ) : (
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>JSON 合法；确定后会和主弹窗表单自动保持一致。</span>
        )}
        <textarea
          className="input mono textarea"
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={22}
          spellCheck={false}
          style={{ fontSize: 12, minHeight: 0 }}
        />
      </div>
    </Modal>
  );
}

export type { HeadlessForm, PriceFields };
export { EntryCard, HeadersEditor, JsonSubModal, NoticeSubModal, PriceSubModal, StatusSubModal, SubModalFooter };
