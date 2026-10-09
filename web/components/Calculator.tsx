"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Btn, Input, Seg, toast } from "./ui";
import { IconCheck } from "./icons";
import { DajuAwake, DajuNap } from "./DajuArt";
import { formatCount, formatDiscount, formatPrice, looseIncludes } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import { calculator } from "@/lib/copy";
import { compareByReleaseDesc } from "@/lib/modelOrder";
import { vendorDisplay } from "@/lib/vendorNames";
import { ShareCalcButton } from "./ShareCalcButton";
import type { CatalogData, CatalogEntry, OverviewData, OverviewRecord } from "@/lib/types";
import {
  DEFAULT_TOTAL_TOKENS,
  EMPTY_PRICES,
  PRICE_KEYS,
  TOKEN_PRESET_VALUES,
  calcCost,
  encodeCalcState,
  formatAmount,
  hasAnyPrice,
  parseAmount,
  parseHitRate,
  type CalcSource,
  type CalcState,
  type PriceKey,
} from "@/lib/calculator";

/** 一个候选模型：选定后带出其单价 */
interface Option {
  value: string;
  label: string;
  sub: string;
  prices: Record<PriceKey, number | null>;
  currency: string;
  /** 目录发布日期：候选按「发布日期倒序 + 名称」排，与监控模型下拉同一套口径 */
  release?: string;
}

const BUCKET_LABEL = calculator.buckets;

/** 厂商分组默认露出的模型条数：超出部分折叠进「更多」，搜索时不限量 */
const VENDOR_PREVIEW_COUNT = 5;

function symbolOf(currency: string): string {
  if (currency === "CNY") return "¥";
  if (currency === "USD") return "$";
  return "";
}

/** 目录条目 → 候选模型：缓存命中价缺失就留空，不填估算值；缓存写入价不进计算，不展示。
 *  国内条目取定价源抓来的人民币原价（list_cny/cache_cny），海外条目取美元原价，均不做汇率换算。 */
function optionFromCatalog(key: string, entry: CatalogEntry): Option {
  const cn = entry.region === "cn";
  return {
    value: key,
    label: entry.name ?? entry.model ?? key,
    sub: vendorDisplay(entry.vendor),
    release: entry.release_date ?? "",
    prices: {
      input: (cn ? entry.list_cny?.input : entry.list?.input) ?? null,
      output: (cn ? entry.list_cny?.output : entry.list?.output) ?? null,
      cacheRead: (cn ? entry.cache_cny?.read : entry.cache?.read) ?? null,
    },
    currency: cn ? "CNY" : entry.currency || "USD",
  };
}

/** 站点价记录 → 候选模型：只取输入/输出价，缓存价站点侧不落库，留空。
 *  同一站点同名模型可能按分组各有一条价：value 带上分组保证唯一，选中态才不会亮一排。 */
function optionFromRecord(row: OverviewRecord): Option {
  const unit = row.unit || "";
  const group = row.metadata?.group ?? "";
  return {
    value: group ? `${row.model}:${group}` : row.model,
    label: row.model,
    sub: group,
    prices: {
      input: row.input_price ?? null,
      output: row.output_price ?? null,
      cacheRead: null,
    },
    currency: unit.split("/")[0]?.trim().toUpperCase() || "",
  };
}

export function Calculator({
  catalog,
  overview,
  initial,
}: {
  catalog: CatalogData | null;
  overview: OverviewData | null;
  initial: CalcState;
}) {
  const [source, setSource] = useState<CalcSource>(initial.source);
  const [site, setSite] = useState(initial.site);
  const [model, setModel] = useState(initial.model);
  const [currency, setCurrency] = useState(initial.currency);
  const [prices, setPrices] = useState<Record<PriceKey, string>>(textsOf(initial.prices));
  const [totalTokens, setTotalTokens] = useState(initial.totalTokens ?? DEFAULT_TOTAL_TOKENS);
  const [hitRate, setHitRate] = useState(textOfNumber(initial.hitRate));
  const [keyword, setKeyword] = useState("");
  // 厂商分组的展开状态：默认全部折叠，只露每家前几条；深链带着模型进来时自动展开其厂商
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    const vendor = catalog?.models[initial.model]?.vendor;
    return new Set(vendor ? [vendor] : []);
  });

  /** 站点价模式下的站点清单：只保留有可用价的站点。 */
  const siteOptions = useMemo(() => {
    const ids = new Set<string>();
    for (const row of overview?.records ?? []) {
      if (row.input_price != null || row.output_price != null) ids.add(row.site_id);
    }
    return [...ids].sort().map((id) => ({ value: id, label: getSiteInfo(id).name || id }));
  }, [overview]);

  /** 候选模型：厂商模式走目录，站点模式走该站的价格记录（同模型同分组各占一项）。 */
  const options = useMemo<Option[]>(() => {
    if (source === "official") {
      // 目录口径排序（发布倒序+名称）：厂商分节的先后也由它带出——新模型多的厂商靠前
      return Object.entries(catalog?.models ?? {})
        .map(([key, entry]) => optionFromCatalog(key, entry))
        .sort((a, b) => compareByReleaseDesc({ name: a.label, release: a.release }, { name: b.label, release: b.release }));
    }
    if (!site) return [];
    const seen = new Set<string>();
    const list: Option[] = [];
    for (const row of overview?.records ?? []) {
      if (row.site_id !== site || (row.input_price == null && row.output_price == null)) continue;
      const option = optionFromRecord(row);
      if (seen.has(option.value)) continue;
      seen.add(option.value);
      list.push(option);
    }
    return list.sort((a, b) => a.label.localeCompare(b.label));
  }, [source, site, catalog, overview]);

  /** 厂商官方价 + 未搜索时按厂商分节；搜索与站点模式返回 null 走平铺。 */
  const groups = useMemo(() => {
    if (source !== "official" || keyword) return null;
    const byVendor = new Map<string, Option[]>();
    for (const option of options) {
      const list = byVendor.get(option.sub) ?? [];
      list.push(option);
      byVendor.set(option.sub, list);
    }
    return [...byVendor].map(([vendor, items]) => ({ vendor, items }));
  }, [source, keyword, options]);

  const visible = useMemo(() => {
    const hit = options.filter((option) => looseIncludes(`${option.label} ${option.sub} ${option.value}`, keyword));
    return hit.slice(0, 60);
  }, [options, keyword]);

  const parsedPrices = useMemo(() => {
    const result = { ...EMPTY_PRICES };
    for (const key of PRICE_KEYS) result[key] = parseAmount(prices[key]);
    return result;
  }, [prices]);

  // 命中率留空回落 98%
  const hitNum = parseHitRate(hitRate);
  const result = useMemo(
    () => calcCost(parsedPrices, { total: totalTokens, hitRate: hitNum }),
    [parsedPrices, totalTokens, hitNum],
  );
  // 命中价缺失时命中的 token 不计费，要在结果里挑明，免得总价被悄悄低估
  const missingCacheRead =
    parsedPrices.cacheRead == null && hitNum > 0 && totalTokens > 0;
  // 至少填了一档单价才有得算：明细表三档常显，空态与导出按钮都以它为准
  const anyPriced = hasAnyPrice(parsedPrices);

  const symbol = symbolOf(currency);

  // 分享图要的展示信息：选中模型的显示名与厂商/分组，价格来源标签
  const selected = useMemo(
    () => options.find((option) => option.value === model) ?? null,
    [options, model],
  );
  const sourceLabel =
    source === "official"
      ? calculator.source.official
      : `${calculator.source.site} · ${getSiteInfo(site).name || site}`;

  // 状态同步进网址：刷新不丢、可直接分享；用 replaceState 避免每次输入都写历史
  useEffect(() => {
    const params = encodeCalcState({
      source,
      model,
      site,
      currency,
      prices: parsedPrices,
      totalTokens,
      hitRate: hitNum,
    });
    const query = params.toString();
    window.history.replaceState(null, "", query ? `/calculator?${query}` : "/calculator");
  }, [source, model, site, currency, parsedPrices, totalTokens, hitNum]);

  function switchSource(next: string) {
    setSource(next as CalcSource);
    setModel("");
    setKeyword("");
    setExpanded(new Set());
    setPrices(textsOf(EMPTY_PRICES));
  }

  // 进页面没带模型深链时，默认代选厂商官方价的首条并展开其厂商组：单价带出、结果立算，
  // 不用再点两下才能看到第一笔账；每次挂载只代选一次，之后切来源留空由用户自己挑
  const autoPicked = useRef(false);
  useEffect(() => {
    if (autoPicked.current || model || source !== "official" || options.length === 0) return;
    autoPicked.current = true;
    const first = options[0];
    setModel(first.value);
    setPrices(textsOf(first.prices));
    setCurrency(first.currency);
    setExpanded((prev) => (prev.has(first.sub) ? prev : new Set(prev).add(first.sub)));
  }, [model, source, options]);

  function toggleVendor(vendor: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(vendor)) next.delete(vendor);
      else next.add(vendor);
      return next;
    });
  }

  function pickSite(next: string) {
    setSite(next);
    setModel("");
    setKeyword("");
    setPrices(textsOf(EMPTY_PRICES));
  }

  /** 选中模型：带出单价与币种，缺的价留空（不编估算值）；用量保留，方便换模型直接比总价。 */
  function pickModel(option: Option) {
    setModel(option.value);
    setPrices(textsOf(option.prices));
    setCurrency(option.currency);
  }

  function setPrice(key: PriceKey, text: string) {
    setPrices((prev) => ({ ...prev, [key]: text }));
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast(calculator.copied);
    } catch {
      toast(calculator.copied);
    }
  }

  /** 单个候选按钮：分组与平铺两种布局共用 */
  const renderOption = (option: Option) => {
    const active = option.value === model;
    return (
      <button
        key={`${option.value}:${option.sub}`}
        type="button"
        role="option"
        aria-selected={active}
        className={`calc-option${active ? " on" : ""}`}
        onClick={() => pickModel(option)}
      >
        <span className="calc-option-main">
          <span className="calc-option-name">{option.label}</span>
          {option.sub && <span className="calc-option-sub">{option.sub}</span>}
        </span>
        <span className="calc-option-price">
          {symbolOf(option.currency)}
          {formatPrice(option.prices.input)} / {symbolOf(option.currency)}
          {formatPrice(option.prices.output)}
          {active && <IconCheck size={13} />}
        </span>
      </button>
    );
  };

  return (
    <div className="calculator">
      {/* 选模型：来源切换 + 搜索 + 候选列表 */}
      <div className="panel calc-block">
        <div className="calc-head">
          <Seg
            value={source}
            onChange={switchSource}
            options={[
              { value: "official", label: calculator.source.official },
              { value: "site", label: calculator.source.site },
            ]}
          />
          {source === "site" && (
            <span className="sel-wrap calc-site" style={{ marginLeft: "auto" }}>
              <select className="sel" aria-label="选择站点" value={site} onChange={(event) => pickSite(event.target.value)}>
                <option value="">{calculator.sitePlaceholder}</option>
                {siteOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </span>
          )}
          <Input
            value={keyword}
            onChange={setKeyword}
            placeholder={calculator.modelPlaceholder}
            style={{ maxWidth: 280 }}
          />
        </div>
        {source === "site" && !site ? (
          <p className="calc-empty">{calculator.sitePlaceholder}</p>
        ) : groups ? (
          <div className="calc-options" role="listbox">
            {groups.map((group) => {
              const open = expanded.has(group.vendor);
              const shown = open ? group.items : group.items.slice(0, VENDOR_PREVIEW_COUNT);
              const folded = group.items.length - shown.length;
              return (
                <section key={group.vendor}>
                  <button
                    type="button"
                    className="calc-vendor"
                    onClick={() => toggleVendor(group.vendor)}
                    aria-expanded={open}
                  >
                    <span>{vendorDisplay(group.vendor)}</span>
                    <span className="calc-vendor-count">· {group.items.length}</span>
                    {folded > 0 && (
                      <span className="calc-vendor-more">{open ? calculator.lessModels : calculator.moreModels(folded)}</span>
                    )}
                  </button>
                  {shown.map(renderOption)}
                </section>
              );
            })}
          </div>
        ) : visible.length === 0 ? (
          <p className="calc-empty">没找到匹配的模型，换个关键词试试。</p>
        ) : (
          <div className="calc-options" role="listbox">{visible.map(renderOption)}</div>
        )}
      </div>

      {/* 单价与用量 */}
      <div className="panel calc-block">
        <div className="calc-head">
          <span className="calc-label">{calculator.priceTitle}</span>
        </div>
        <div className="calc-grid">
          {PRICE_KEYS.map((key) => (
            <label key={key} className="calc-field">
              <span className="calc-field-label">{BUCKET_LABEL[key]}</span>
              <Input
                value={prices[key]}
                onChange={(text) => setPrice(key, text)}
                prefix={symbol ? <span className="calc-affix">{symbol}</span> : undefined}
                placeholder="—"
              />
            </label>
          ))}
        </div>
        <div className="calc-head" style={{ margin: "18px 0 0" }}>
          <span className="calc-label">{calculator.usageTotal}</span>
        </div>
        <div
          className="calc-grid"
          style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}
        >
          <label className="calc-field">
            <span className="calc-field-label">{calculator.usageSelect}</span>
            <span className="sel-wrap">
              <select
                className="sel"
                aria-label="选择用量档位"
                value={String(totalTokens)}
                onChange={(event) => setTotalTokens(Number(event.target.value))}
              >
                {TOKEN_PRESET_VALUES.map((value, index) => (
                  <option key={value} value={String(value)}>
                    {calculator.tokenPresets[index]}
                  </option>
                ))}
              </select>
            </span>
          </label>
          <label className="calc-field">
            <span className="calc-field-label">{calculator.hitRate}</span>
            <Input
              value={hitRate}
              onChange={setHitRate}
              suffix={<span className="calc-affix is-right">%</span>}
              placeholder="98"
            />
          </label>
        </div>
        <div className="calc-actions">
          <span className="calc-tip">{calculator.usageHint}</span>
        </div>
      </div>

      {/* 结果 */}
      <div className="panel calc-block calc-result">
        <div className="calc-head">
          <span className="calc-label" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            {anyPriced && <DajuAwake width={20} />}
            {calculator.resultTitle}
          </span>
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8 }}>
            <ShareCalcButton
              modelName={selected?.label ?? ""}
              modelSub={selected?.sub ?? ""}
              sourceLabel={sourceLabel}
              currency={currency}
              prices={parsedPrices}
              totalTokens={totalTokens}
              hitRate={hitNum}
              result={result}
              missingCacheRead={missingCacheRead}
              disabled={!anyPriced}
            />
            <Btn variant="ghost" size="sm" onClick={copyLink} disabled={!anyPriced}>
              {calculator.copyLink}
            </Btn>
          </span>
        </div>
        {!anyPriced ? (
          <div className="calc-empty">
            <DajuNap width={110} />
            {calculator.emptyResult}
          </div>
        ) : (
          <>
            <div className="calc-total">
              <span className="calc-total-label">{calculator.totalLabel}</span>
              <span className="calc-total-value">
                {symbol}
                {formatAmount(result.total)}
              </span>
            </div>
            {missingCacheRead && <p className="calc-tip">{calculator.missingCache}</p>}
            <div className="dtable-wrap">
              <div className="dtable-scroll">
                <table className="dtable">
                  <thead>
                    <tr>
                      <th>{calculator.detail.bucket}</th>
                      <th style={{ textAlign: "right" }}>{calculator.detail.unitPrice}</th>
                      <th style={{ textAlign: "right" }}>{calculator.detail.tokens}</th>
                      <th style={{ textAlign: "right" }}>{calculator.detail.subtotal}</th>
                      <th style={{ textAlign: "right" }}>{calculator.detail.share}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.lines.map((line) => {
                      // 没填价的档：单价/小计/占比留空并标注「未计入」，用量照实展示，
                      // 让三档用量加总与总用量对得上，占比失真的疑问在表内就有答案
                      const unbilled = line.subtotal === null;
                      return (
                        <tr key={line.key} style={unbilled ? { opacity: 0.55 } : undefined}>
                          <td>
                            {BUCKET_LABEL[line.key]}
                            {unbilled && <span className="calc-unbilled">未计入</span>}
                          </td>
                          <td className="num" style={{ textAlign: "right" }}>
                            {line.unitPrice !== null ? `${symbol}${formatPrice(line.unitPrice)}` : "—"}
                          </td>
                          <td className="num" style={{ textAlign: "right" }}>
                            {formatCount(line.tokens)}
                          </td>
                          <td className="num" style={{ textAlign: "right" }}>
                            {line.subtotal !== null ? `${symbol}${formatAmount(line.subtotal)}` : "—"}
                          </td>
                          <td className="num" style={{ textAlign: "right" }}>
                            {line.share !== null ? formatDiscount(line.share) : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** 数字 → 输入框文本；null 留空，让用户看到"这一档没填"。 */
function textsOf(source: Record<PriceKey, number | null>): Record<PriceKey, string> {
  const result = { ...EMPTY_PRICES } as unknown as Record<PriceKey, string>;
  for (const key of PRICE_KEYS) {
    const value = source[key];
    result[key] = value === null || value === undefined ? "" : String(value);
  }
  return result;
}

/** 单个数字 → 输入框文本；null/undefined 留空。 */
function textOfNumber(value: number | null | undefined): string {
  return value == null ? "" : String(value);
}
