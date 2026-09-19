"use client";

/** 管理台「厂商定价源」：models.dev 国内价覆盖检测 + 厂商定价页抓取管理。
 *
 * models.dev 对部分国内厂商只有国际站口径甚至未收录；这里先给出覆盖检测记录
 * （推荐添加），管理员为厂商配置一个公开定价页 URL 后即可抓取该页全部模型
 * 价格，结果合并进官方价目录并作为国内折扣基准。
 */

import { useCallback, useEffect, useState } from "react";
import { apiSend } from "@/lib/api";
import { formatPrice, formatTime } from "@/lib/format";
import type {
  TaskDetail,
  VendorPricingSource,
  VendorSourceDetection,
  VendorSourceModel,
} from "@/lib/types";
import { DataTable, type DColumn } from "./DataTable";
import { IconNodes, IconPlus } from "./icons";
import { PageHeader } from "./PageHeader";
import { RiskLink } from "./RiskLink";
import { SiteAlert } from "./SiteAlert";
import { ToneTag, type Tone } from "./ToneTag";
import { Btn, Empty, Input, Modal, Switch, toast } from "./ui";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

const VERDICT_META: Record<VendorSourceDetection["verdict"], { label: string; tone: Tone; hint: string }> = {
  not_listed: { label: "未收录", tone: "red", hint: "models.dev 完全没有该厂商，推荐添加国内定价页" },
  missing_cn: { label: "仅国际口径", tone: "yellow", hint: "models.dev 收录了该厂商，但只有国际站价格，推荐添加国内定价页" },
  has_cn: { label: "已覆盖", tone: "green", hint: "models.dev 已有该厂商的国内站价格，无需额外配置" },
};

const STATUS_META: Record<string, { label: string; tone: Tone }> = {
  ok: { label: "正常", tone: "green" },
  empty: { label: "未取到价格", tone: "yellow" },
  failed: { label: "失败", tone: "red" },
};

function sourceSkeleton(): VendorPricingSource {
  return { vendor: "", url: "", enabled: true };
}

function statusOf(source: VendorPricingSource): { label: string; tone: Tone; detail: string } {
  const meta = STATUS_META[source.last_status ?? ""] ?? { label: "未抓取", tone: "gray" as Tone };
  const at = source.last_fetched_at ? formatTime(source.last_fetched_at) : "尚未抓取";
  const count = source.model_count ?? 0;
  return { label: meta.label, tone: meta.tone, detail: `${at} · ${count} 个模型` };
}

export function AdminPricingSources() {
  const [sources, setSources] = useState<VendorPricingSource[] | null>(null);
  const [detection, setDetection] = useState<VendorSourceDetection[] | null>(null);
  const [detectionError, setDetectionError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ source: VendorPricingSource; isNew: boolean } | null>(null);
  const [detailVendor, setDetailVendor] = useState<string | null>(null);
  const [removing, setRemoving] = useState<VendorPricingSource | null>(null);
  const [refreshing, setRefreshing] = useState<string | null>(null);

  const reload = useCallback(() => {
    apiSend<{ sources: VendorPricingSource[] }>("/api/vendor-sources", "GET")
      .then((data) => setSources(data.sources))
      .catch(() => setSources([]));
    apiSend<{ records: VendorSourceDetection[] }>("/api/vendor-sources/detection", "GET")
      .then((data) => {
        setDetection(data.records);
        setDetectionError(null);
      })
      .catch((error) => {
        setDetection(null);
        setDetectionError(errorText(error));
      });
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  async function refreshOne(vendor: string) {
    if (refreshing) return;
    setRefreshing(vendor);
    try {
      const { task_id } = await apiSend<{ task_id: string }>(
        `/api/vendor-sources/${encodeURIComponent(vendor)}/refresh`,
        "POST",
      );
      for (let attempt = 0; attempt < 200; attempt++) {
        const detail: TaskDetail = await apiSend<TaskDetail>(`/api/tasks/${task_id}`, "GET");
        if (detail.status !== "running") {
          if (detail.status === "done") {
            const count = Number(detail.result?.model_count ?? 0);
            toast(count > 0 ? `已抓取 ${vendor} 的 ${count} 个模型价格，目录已更新` : `抓取完成，但该页面没有取到价格`);
          } else {
            toast(`抓取失败：${detail.error ?? "未知错误"}`);
          }
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    } catch (error) {
      toast(`抓取失败: ${errorText(error)}`);
    } finally {
      setRefreshing(null);
      reload();
    }
  }

  async function remove(vendor: string) {
    try {
      await apiSend(`/api/vendor-sources/${encodeURIComponent(vendor)}`, "DELETE");
      toast(`已删除 ${vendor}，正在后台恢复 models.dev 基准`);
      setRemoving(null);
      reload();
    } catch (error) {
      toast(`删除失败: ${errorText(error)}`);
    }
  }

  const sourceColumns: DColumn<VendorPricingSource>[] = [
    {
      title: "厂商",
      dataIndex: "vendor",
      width: 150,
      render: (v: string, row) => (
        <button
          type="button"
          title="查看抓取到的模型价格"
          onClick={() => setDetailVendor(row.vendor)}
          style={{
            background: "none",
            border: "none",
            padding: 0,
            cursor: "pointer",
            color: "inherit",
            font: "inherit",
            fontWeight: 550,
          }}
        >
          {v}
        </button>
      ),
    },
    {
      title: "定价页",
      dataIndex: "url",
      render: (v: string) => (
        <span
          style={{ display: "inline-block", maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", verticalAlign: "bottom" }}
        >
          <RiskLink href={v}>{v.replace(/^https?:\/\//, "")}</RiskLink>
        </span>
      ),
    },
    {
      title: "最近抓取",
      key: "status",
      width: 190,
      render: (_, row) => {
        const status = statusOf(row);
        return (
          <span style={{ display: "inline-grid", gap: 2 }}>
            <ToneTag tone={status.tone}>{status.label}</ToneTag>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              {status.detail}
              {row.last_error ? ` · ${row.last_error}` : ""}
            </span>
          </span>
        );
      },
    },
    {
      title: "方式",
      dataIndex: "last_method",
      width: 110,
      mobileHide: true,
      render: (v: string | null) =>
        v ? <span className="mono" style={{ fontSize: 12 }}>{v}</span> : <span style={{ color: "var(--text-3)" }}>—</span>,
    },
    {
      title: "启用",
      dataIndex: "enabled",
      width: 70,
      render: (v: boolean, row) => (
        <Switch
          checked={v}
          title={v ? "参与抓取与合并" : "已停用：不抓取、不合并"}
          onChange={(checked) => {
            apiSend(`/api/vendor-sources/${encodeURIComponent(row.vendor)}`, "PUT", {
              vendor: row.vendor,
              url: row.url,
              enabled: checked,
            })
              .then(() => {
                toast(checked ? "已启用，点「立即抓取」获取价格" : "已停用，正在后台恢复 models.dev 基准");
                reload();
              })
              .catch((error) => toast(`保存失败: ${errorText(error)}`));
          }}
        />
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 210,
      render: (_, row) => (
        <span style={{ display: "inline-flex", gap: 8 }}>
          <Btn size="sm" variant="primary" loading={refreshing === row.vendor} onClick={() => refreshOne(row.vendor)}>
            立即抓取
          </Btn>
          <Btn size="sm" onClick={() => setEditing({ source: row, isNew: false })}>
            编辑
          </Btn>
          <Btn size="sm" variant="text" onClick={() => setRemoving(row)}>
            删除
          </Btn>
        </span>
      ),
    },
  ];

  return (
    <div className="section-gap rise-in" style={{ display: "grid", gap: 16 }}>
      <PageHeader
        title="厂商定价源"
        subtitle="models.dev 缺国内厂商官方价时，在这里配置该厂商的国内定价页；抓到的全部模型价格会作为国内基准合并进官方价目录，每 24 小时随目录自动刷新。"
        actions={
          <Btn variant="primary" onClick={() => setEditing({ source: sourceSkeleton(), isNew: true })}>
            <IconPlus size={14} /> 新增定价源
          </Btn>
        }
      />

      <div className="panel" style={{ padding: "14px 18px", display: "grid", gap: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 550 }}>models.dev 国内价覆盖检测</div>
        {detectionError ? (
          <SiteAlert title="覆盖检测暂时不可用" detail={detectionError} fix="在「采集任务」页刷新一次厂商定价后再来。" />
        ) : detection === null ? (
          <div style={{ color: "var(--text-2)", fontSize: 13 }}>检测中…</div>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {detection.map((record) => {
              const meta = VERDICT_META[record.verdict];
              const added = record.source_added;
              return (
                <div
                  key={record.vendor}
                  style={{
                    display: "flex",
                    gap: 12,
                    alignItems: "center",
                    flexWrap: "wrap",
                    padding: "8px 10px",
                    borderRadius: 8,
                    background: "color-mix(in srgb, var(--panel-2) 55%, transparent)",
                  }}
                >
                  <span style={{ fontWeight: 550, minWidth: 110 }}>{record.vendor}</span>
                  <ToneTag tone={meta.tone}>{meta.label}</ToneTag>
                  <span style={{ fontSize: 12.5, color: "var(--text-2)", flex: "1 1 260px" }}>
                    {record.note || meta.hint}
                    {record.providers.length > 0 && (
                      <span style={{ color: "var(--text-3)" }}>
                        {" "}· models.dev 渠道：{record.providers.join("、")}（带价 {record.models_priced}/{record.models_total}）
                      </span>
                    )}
                  </span>
                  {added ? (
                    <ToneTag tone="blue">已添加定价源</ToneTag>
                  ) : record.verdict === "has_cn" ? (
                    <span style={{ color: "var(--text-3)", fontSize: 12.5 }}>无需配置</span>
                  ) : (
                    <Btn
                      size="sm"
                      onClick={() =>
                        setEditing({
                          source: {
                            ...sourceSkeleton(),
                            vendor: record.vendor,
                            url: record.suggested_url ?? "",
                          },
                          isNew: true,
                        })
                      }
                    >
                      添加定价页
                    </Btn>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="panel" style={{ overflow: "hidden" }}>
        {sources === null ? (
          <div style={{ padding: "16px 20px", color: "var(--text-2)", fontSize: 13 }}>加载中…</div>
        ) : (
          <DataTable<VendorPricingSource>
            rowKey="vendor"
            columns={sourceColumns}
            rows={sources}
            scrollX={900}
            empty={
              <Empty
                icon={<IconNodes size={18} />}
                title="还没有厂商定价源"
                description="从上面的覆盖检测里推荐添加，或直接配置一个厂商的国内定价页地址。"
                action={
                  <Btn size="sm" onClick={() => setEditing({ source: sourceSkeleton(), isNew: true })}>
                    新增定价源
                  </Btn>
                }
              />
            }
          />
        )}
      </div>

      {editing && (
        <SourceModal
          initial={editing.source}
          isNew={editing.isNew}
          onClose={() => setEditing(null)}
          onSaved={reload}
        />
      )}
      {detailVendor && <SourceDetailModal vendor={detailVendor} onClose={() => setDetailVendor(null)} />}
      {removing && (
        <Modal
          open
          onClose={() => setRemoving(null)}
          title={`删除定价源：${removing.vendor}`}
          footer={
            <Btn variant="primary" onClick={() => remove(removing.vendor)}>
              确认删除
            </Btn>
          }
        >
          <div style={{ display: "grid", gap: 8, fontSize: 13.5, color: "var(--text-2)" }}>
            <span>删除后会自动在后台重新同步目录，该厂商的国内基准价将恢复为 models.dev 的口径。</span>
            <span className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>{removing.url}</span>
          </div>
        </Modal>
      )}
    </div>
  );
}

function SourceModal({
  initial,
  isNew,
  onClose,
  onSaved,
}: {
  initial: VendorPricingSource;
  isNew: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [vendor, setVendor] = useState(initial.vendor);
  const [url, setUrl] = useState(initial.url);
  const [enabled, setEnabled] = useState(initial.enabled);
  const [saving, setSaving] = useState(false);
  const valid = vendor.trim().length > 0 && /^https?:\/\//.test(url.trim());

  async function save() {
    if (!valid || saving) return;
    setSaving(true);
    try {
      const payload = { vendor: vendor.trim(), url: url.trim(), enabled };
      if (isNew) {
        await apiSend("/api/vendor-sources", "POST", payload);
        toast("定价源已添加，点「立即抓取」获取价格");
      } else {
        await apiSend(`/api/vendor-sources/${encodeURIComponent(initial.vendor)}`, "PUT", payload);
        toast(initial.url !== payload.url ? "已保存；地址变了，记得重新「立即抓取」" : "定价源已保存");
      }
      onSaved();
      onClose();
    } catch (error) {
      toast(`保存失败: ${errorText(error)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={isNew ? "新增厂商定价源" : `编辑定价源：${initial.vendor}`}
      footer={
        <Btn variant="primary" loading={saving} disabled={!valid} onClick={save}>
          保存
        </Btn>
      }
    >
      <div style={{ display: "grid", gap: 12 }}>
        <Input
          value={vendor}
          onChange={setVendor}
          placeholder="厂商名，例如：Zhipu AI"
          ariaLabel="厂商名"
          disabled={!isNew}
        />
        <Input
          value={url}
          onChange={setUrl}
          placeholder="官方定价页地址，以 https:// 开头（无需登录）"
          ariaLabel="定价页地址"
        />
        <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13.5 }}>
          <Switch checked={enabled} onChange={setEnabled} />
          启用（参与自动抓取与合并）
        </label>
        {isNew && (
          <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>
            保存后点列表里的「立即抓取」即可解析该页全部模型价格；解析不动时才动用 AI，结果会标「待复核」。
          </span>
        )}
      </div>
    </Modal>
  );
}

function SourceDetailModal({ vendor, onClose }: { vendor: string; onClose: () => void }) {
  const [source, setSource] = useState<VendorPricingSource | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    apiSend<VendorPricingSource>(`/api/vendor-sources/${encodeURIComponent(vendor)}`, "GET")
      .then((data) => {
        if (alive) setSource(data);
      })
      .catch((e) => {
        if (alive) setError(errorText(e));
      });
    return () => {
      alive = false;
    };
  }, [vendor]);

  const columns: DColumn<VendorSourceModel>[] = [
    {
      title: "模型",
      dataIndex: "model",
      width: 200,
      render: (v: string, row) => (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="mono" title={v}>{v}</span>
          {row.price_status === "candidate" && <ToneTag tone="yellow">待复核</ToneTag>}
        </span>
      ),
    },
    {
      title: "输入",
      dataIndex: "input_price",
      align: "right",
      width: 110,
      sorter: (a, b) => (a.input_price ?? 0) - (b.input_price ?? 0),
      render: (v: number | null, row) => <PriceCell value={v} currency={row.currency} />,
    },
    {
      title: "输出",
      dataIndex: "output_price",
      align: "right",
      width: 110,
      sorter: (a, b) => (a.output_price ?? 0) - (b.output_price ?? 0),
      render: (v: number | null, row) => <PriceCell value={v} currency={row.currency} />,
    },
    {
      title: "缓存读",
      dataIndex: "cache_read_price",
      align: "right",
      width: 110,
      mobileHide: true,
      render: (v: number | null, row) => <PriceCell value={v} currency={row.currency} />,
    },
    {
      title: "分档",
      key: "tiers",
      width: 250,
      mobileHide: true,
      render: (_, row) => {
        const tiers = row.tiers ?? [];
        const labeled = tiers.some((tier) => tier.name || tier.context);
        if (tiers.length <= 1 && !labeled) return <span>单档</span>;
        const symbol = (row.currency ?? "").toUpperCase() === "USD" ? "$" : "¥";
        return (
          <span style={{ display: "inline-grid", gap: 2, fontSize: 12 }}>
            {tiers.map((tier, index) => (
              <span key={index} className="mono" title={tier.name || tier.context || undefined}>
                {tier.name || tier.context || "基准"}：{symbol}
                {formatPrice(tier.input_price)} / {symbol}
                {formatPrice(tier.output_price)}
              </span>
            ))}
          </span>
        );
      },
    },
    {
      title: "原文",
      dataIndex: "quote",
      mobileHide: true,
      render: (v: string) =>
        v ? (
          <span
            title={v}
            style={{ display: "inline-block", maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-3)", fontSize: 12 }}
          >
            {v}
          </span>
        ) : (
          <span style={{ color: "var(--text-3)" }}>—</span>
        ),
    },
  ];

  const status = source ? statusOf(source) : null;

  return (
    <Modal open onClose={onClose} title={`定价页抓取结果：${vendor}`} width={860}>
      {error ? (
        <SiteAlert title="读取失败" detail={error} fix="刷新页面重试。" />
      ) : source === null ? (
        <div style={{ padding: "24px 0", color: "var(--text-2)", fontSize: 13 }}>加载中…</div>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", fontSize: 12.5, color: "var(--text-2)" }}>
            {status && <ToneTag tone={status.tone}>{status.label}</ToneTag>}
            <span>{status?.detail}</span>
            {source.last_method && <span className="mono">解析方式 {source.last_method}</span>}
            <RiskLink href={source.url}>打开定价页</RiskLink>
          </div>
          {(source.models ?? []).length === 0 ? (
            <Empty
              icon={<IconNodes size={18} />}
              title="没有取到模型价格"
              description={source.last_error ?? "该页面可能没有结构化价目，或抓取时页面尚未渲染。"}
            />
          ) : (
            <DataTable<VendorSourceModel>
              rowKey="model_key"
              columns={columns}
              rows={source.models ?? []}
              scrollX={760}
              paginated
            />
          )}
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>
            以上价格按定价页标价币种展示；合并进官方目录时统一折算为美元口径，人民币价保留页面标价精度。
          </span>
        </div>
      )}
    </Modal>
  );
}

function PriceCell({ value, currency }: { value: number | null | undefined; currency?: string | null }) {
  if (value === null || value === undefined) return <span style={{ color: "var(--text-3)" }}>—</span>;
  const symbol = (currency ?? "").toUpperCase() === "USD" ? "$" : "¥";
  return <span className="mono num">{symbol}{formatPrice(value)}</span>;
}
