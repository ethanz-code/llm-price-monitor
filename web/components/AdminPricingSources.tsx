"use client";

/** 管理台「厂商定价源」：国内基准价的唯一配置入口。
 *
 * models.dev 的国内价格一律不作基准（时段价混装、他方汇率换算，与官方人民币
 * 标价偏差大）：所有国内厂商都需要在这里配置官方定价页，由程序定时抓取标价
 * 合并进目录；海外定价页只作国际参考价。
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
import { RiskLink } from "./RiskLink";
import { SiteAlert } from "./SiteAlert";
import { ToneTag, type Tone } from "./ToneTag";
import { Btn, Empty, Input, Modal, Pick, Switch, toast } from "./ui";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 覆盖检测统一口径：models.dev 的国内价一律不作基准，国内厂商都需要配置定价源，
 * verdict 只剩记录意义，展示不再区分。 */
const VERDICT_PENDING: { label: string; tone: Tone; hint: string } = {
  label: "待添加",
  tone: "yellow",
  hint: "models.dev 的国内价格不作为折扣基准；添加国内定价页后由程序定时抓取官方标价",
};
const VERDICT_META: Record<VendorSourceDetection["verdict"], typeof VERDICT_PENDING> = {
  not_listed: VERDICT_PENDING,
  missing_cn: VERDICT_PENDING,
  has_cn: VERDICT_PENDING,
};

const STATUS_META: Record<string, { label: string; tone: Tone }> = {
  ok: { label: "正常", tone: "green" },
  empty: { label: "未取到价格", tone: "yellow" },
  failed: { label: "失败", tone: "red" },
};

function sourceSkeleton(): VendorPricingSource {
  return { vendor: "", url: "", enabled: true, region: "cn" };
}

function regionTag(region: "cn" | "global" | undefined) {
  return region === "global" ? <ToneTag tone="gray">海外参考</ToneTag> : <ToneTag tone="blue">国内基准</ToneTag>;
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
  const [quickAdding, setQuickAdding] = useState<string | null>(null);

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

  async function quickAdd(record: VendorSourceDetection, suggestion: { kind: "web" | "json"; url: string }) {
    if (quickAdding) return;
    setQuickAdding(record.vendor);
    try {
      // 推荐项人工核验过：直接按预填配置创建国内源，随即抓取并回填状态
      await apiSend("/api/vendor-sources", "POST", {
        vendor: record.vendor,
        url: suggestion.url,
        enabled: true,
        region: "cn",
      });
      toast(`已添加 ${record.vendor} 的${suggestion.kind === "json" ? "接口" : "网页"}定价源，开始抓取…`);
      await refreshOne(record.vendor);
    } catch (error) {
      toast(`添加失败: ${errorText(error)}`);
    } finally {
      setQuickAdding(null);
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
      width: 280,
      ellipsis: true,
      render: (v: string) => <RiskLink href={v} title={v}>{v.replace(/^https?:\/\//, "")}</RiskLink>,
    },
    {
      title: "区域",
      dataIndex: "region",
      width: 92,
      render: (v: "cn" | "global" | undefined) => regionTag(v),
    },
    {
      title: "最近抓取",
      key: "status",
      width: 190,
      render: (_, row) => {
        const status = statusOf(row);
        const detail = `${status.detail}${row.last_error ? ` · ${row.last_error}` : ""}`;
        return (
          <span style={{ display: "inline-grid", gap: 2, maxWidth: 190 }}>
            <ToneTag tone={status.tone}>{status.label}</ToneTag>
            <span
              title={detail}
              style={{ fontSize: 12, color: "var(--text-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {detail}
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
    <div className="rise-in" style={{ display: "grid", gap: 10 }}>
      {/* 页面说明条：与站点管理页顶部速查条同款，紧凑不加标题 */}
      <div className="panel" style={{ padding: "10px 16px" }}>
        <span style={{ fontSize: 13, color: "var(--text-2)" }}>
          models.dev 的国内价格不作为基准：国内厂商都需要在下方添加官方定价页，由程序定时抓取标价作为国内折扣基准；海外定价页只作国际参考，每
          24 小时随目录自动刷新。
        </span>
      </div>

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
                <div key={record.vendor} className="detect-row">
                  <span className="detect-vendor">{record.vendor}</span>
                  <ToneTag tone={meta.tone}>{meta.label}</ToneTag>
                  <span className="detect-note">
                    {record.note || meta.hint}
                    {record.providers.length > 0 && (
                      <span style={{ color: "var(--text-3)" }}>
                        {" "}· models.dev 渠道：{record.providers.join("、")}（带价 {record.models_priced}/{record.models_total}）
                      </span>
                    )}
                  </span>
                  {added ? (
                    <ToneTag tone="blue">已添加定价源</ToneTag>
                  ) : (record.suggestions ?? []).length === 0 ? (
                    <Btn
                      size="sm"
                      onClick={() => setEditing({ source: { ...sourceSkeleton(), vendor: record.vendor }, isNew: true })}
                    >
                      手动添加
                    </Btn>
                  ) : (
                    <span style={{ display: "inline-flex", gap: 8 }}>
                      {(record.suggestions ?? []).map((suggestion) => (
                        <Btn
                          key={suggestion.kind}
                          size="sm"
                          variant="primary"
                          loading={quickAdding === record.vendor}
                          onClick={() => quickAdd(record, suggestion)}
                        >
                          {suggestion.kind === "json" ? "一键添加接口" : "一键添加网页源"}
                        </Btn>
                      ))}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="panel" style={{ overflow: "hidden" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
            padding: "12px 18px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <span style={{ fontSize: 14, fontWeight: 600 }}>已配置的定价源</span>
          <Btn variant="primary" onClick={() => setEditing({ source: sourceSkeleton(), isNew: true })}>
            <IconPlus size={14} /> 新增定价源
          </Btn>
        </div>
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
                description="从上面的覆盖检测里一键添加，或直接配置一个厂商的公开定价页地址。"
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
            <span>删除后会自动在后台重新同步目录，该源合并的国内基准价会回落为 models.dev 国际站口径。</span>
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
  const [region, setRegion] = useState<"cn" | "global">(initial.region ?? "cn");
  const [saving, setSaving] = useState(false);
  const valid = vendor.trim().length > 0 && /^https?:\/\//.test(url.trim());

  async function save() {
    if (!valid || saving) return;
    setSaving(true);
    try {
      const payload = { vendor: vendor.trim(), url: url.trim(), enabled, region };
      const basisChanged = initial.url !== payload.url || (initial.region ?? "cn") !== region;
      if (isNew) {
        await apiSend("/api/vendor-sources", "POST", payload);
        toast("定价源已添加，点「立即抓取」获取价格");
      } else {
        await apiSend(`/api/vendor-sources/${encodeURIComponent(initial.vendor)}`, "PUT", payload);
        toast(basisChanged ? "已保存；地址或区域变了，记得重新「立即抓取」" : "定价源已保存");
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
          placeholder="官方定价页或价格接口地址，以 https:// 开头（无需登录）"
          ariaLabel="定价页地址"
        />
        <div style={{ display: "grid", gap: 6 }}>
          <span style={{ fontSize: 13.5 }}>区域</span>
          <Pick
            value={region}
            onChange={(value) => setRegion(value === "global" ? "global" : "cn")}
            options={[
              { value: "cn", label: "国内 · 折扣基准" },
              { value: "global", label: "海外 · 国际参考" },
            ]}
            style={{ width: 240 }}
          />
          <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>
            国内源的价格作为国内折扣基准；海外源只作国际参考价，不影响国内基准。
          </span>
        </div>
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
