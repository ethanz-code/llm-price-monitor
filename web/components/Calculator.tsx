"use client";

import { useEffect, useMemo, useState } from "react";
import { Btn, Input, Seg, toast } from "./ui";
import { IconCheck } from "./icons";
import { formatPrice, looseIncludes, formatDiscount } from "@/lib/format";
import { getSiteInfo } from "@/lib/sites";
import { calculator } from "@/lib/copy";
import type { CatalogData, CatalogEntry, OverviewData, OverviewRecord } from "@/lib/types";
import {
  DEFAULT_TOTAL_TOKENS,
  EMPTY_PRICES,
  PRICE_KEYS,
  TOKEN_PRESET_VALUES,
  calcCost,
  encodeCalcState,
  formatAmount,
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
}

const BUCKET_LABEL = calculator.buckets;

function symbolOf(currency: string): string {
  if (currency === "CNY") return "¥";
  if (currency === "USD") return "$";
  return "";
}

/** 目录条目 → 候选模型：缓存价缺失就留空，不填估算值。 */
function optionFromCatalog(key: string, entry: CatalogEntry): Option {
  return {
    value: key,
    label: entry.name ?? entry.model ?? key,
    sub: entry.vendor,
    prices: {
      input: entry.list?.input ?? null,
      output: entry.list?.output ?? null,
      cacheRead: entry.cache?.read ?? null,
      cacheWrite: entry.cache?.write ?? null,
    },
    currency: entry.currency || "USD",
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
      cacheWrite: null,
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

  const rate = catalog?.usd_cny_rate ?? overview?.catalog?.usd_cny_rate ?? null;

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
      return Object.entries(catalog?.models ?? {})
        .map(([key, entry]) => optionFromCatalog(key, entry))
        .sort((a, b) => a.sub.localeCompare(b.sub) || a.label.localeCompare(b.label));
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

  const symbol = symbolOf(currency);
  /** 折人民币口径与站内一致：USD 乘快照汇率，CNY 原样，未知币种不折算。 */
  const cnyTotal = useMemo(() => {
    if (currency === "CNY") return result.total;
    if (currency === "USD" && rate) return result.total * rate;
    return null;
  }, [currency, rate, result.total]);

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
    setPrices(textsOf(EMPTY_PRICES));
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
        ) : visible.length === 0 ? (
          <p className="calc-empty">没找到匹配的模型，换个关键词试试。</p>
        ) : (
          <div className="calc-options" role="listbox">
            {visible.map((option) => {
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
            })}
          </div>
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
          <span className="calc-label">{calculator.resultTitle}</span>
          <Btn variant="ghost" size="sm" onClick={copyLink}>
            {calculator.copyLink}
          </Btn>
        </div>
        {result.lines.length === 0 ? (
          <p className="calc-empty">{calculator.emptyResult}</p>
        ) : (
          <>
            <div className="calc-total">
              <span className="calc-total-label">{calculator.totalLabel}</span>
              <span className="calc-total-value">
                {symbol}
                {formatAmount(result.total)}
              </span>
              {cnyTotal !== null && currency !== "CNY" && (
                <span className="calc-total-cny">≈ ¥{formatAmount(cnyTotal)}</span>
              )}
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
                    {result.lines.map((line) => (
                      <tr key={line.key}>
                        <td>{BUCKET_LABEL[line.key]}</td>
                        <td className="num" style={{ textAlign: "right" }}>
                          {symbol}
                          {formatPrice(line.unitPrice)}
                        </td>
                        <td className="num" style={{ textAlign: "right" }}>
                          {line.tokens.toLocaleString("en-US")}
                        </td>
                        <td className="num" style={{ textAlign: "right" }}>
                          {symbol}
                          {formatAmount(line.subtotal)}
                        </td>
                        <td className="num" style={{ textAlign: "right" }}>
                          {formatDiscount(line.share)}
                        </td>
                      </tr>
                    ))}
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
