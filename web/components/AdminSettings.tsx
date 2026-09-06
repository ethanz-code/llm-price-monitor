"use client";

/** 系统设置：AI 兜底提取、Tavily、Webhook。 */

import { useEffect, useState } from "react";
import { toast, Btn, Input, Switch } from "./ui";
import { apiSend } from "@/lib/api";
import type { SettingsData, SiteConfig } from "@/lib/types";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function modelsToText(models: SiteConfig["models"]): string {
  return (models ?? []).map((item) => (typeof item === "string" ? item : item.name)).join(", ");
}

function SettingRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13.5 }}>{label}</span>
        {children}
      </div>
      {hint && <span style={{ fontSize: 12, color: "var(--text-3)" }}>{hint}</span>}
    </div>
  );
}

export function AdminSettings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [tavilyKey, setTavilyKey] = useState("");
  const [webhook, setWebhook] = useState("");
  const [aiEnabled, setAiEnabled] = useState(true);
  const [aiBaseUrl, setAiBaseUrl] = useState("");
  const [aiModels, setAiModels] = useState("");
  const [aiApiKey, setAiApiKey] = useState("");
  const [aiTimeout, setAiTimeout] = useState("60");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiSend<SettingsData>("/api/settings", "GET")
      .then((loaded) => {
        setData(loaded);
        setTavilyKey(typeof loaded.settings.tavily_api_key === "string" ? loaded.settings.tavily_api_key : "");
        setWebhook(typeof loaded.settings.webhook === "string" ? loaded.settings.webhook : "");
        const ai = loaded.ai;
        setAiEnabled(ai.enabled !== false);
        setAiBaseUrl(typeof ai.base_url === "string" ? ai.base_url : "");
        setAiModels(modelsToText((ai.models as SiteConfig["models"]) ?? []));
        setAiApiKey(typeof ai.api_key === "string" ? ai.api_key : "");
        setAiTimeout(String(typeof ai.timeout === "number" ? ai.timeout : 60));
      })
      .catch(() => setData({ settings: {}, ai: {} }));
  }, []);

  async function save() {
    if (!data) return;
    const settings = { ...data.settings, tavily_api_key: tavilyKey.trim() || null, webhook: webhook.trim() || null };
    const ai: Record<string, unknown> = {
      ...data.ai,
      enabled: aiEnabled,
      base_url: aiBaseUrl.trim(),
      models: aiModels.split(/[,\n]/).map((item) => item.trim()).filter(Boolean),
      api_key: aiApiKey.trim() || null,
    };
    const timeout = Number.parseFloat(aiTimeout);
    if (Number.isFinite(timeout)) ai.timeout = timeout;
    setSaving(true);
    try {
      const saved = await apiSend<SettingsData>("/api/settings", "PUT", { settings, ai });
      setData(saved);
      toast("设置已保存，立即生效");
    } catch (error) {
      toast(`保存失败: ${errorText(error)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel" style={{ padding: "24px 28px", display: "grid", gap: 18, maxWidth: 620 }}>
      {!data ? (
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>加载中…</span>
      ) : (
        <>
          <SettingRow label="AI 兜底提取" hint="格式不明站点交给 AI 从证据中提取价格（会产生 token 费用）">
            <Switch checked={aiEnabled} onChange={setAiEnabled} />
          </SettingRow>
          <SettingRow label="AI Base URL">
            <Input value={aiBaseUrl} onChange={setAiBaseUrl} placeholder="https://api.example.com/v1" style={{ width: 360 }} />
          </SettingRow>
          <SettingRow label="AI 模型列表" hint="逗号分隔；每次抽取随机选用一个">
            <Input value={aiModels} onChange={setAiModels} placeholder="model-a, model-b" style={{ width: 360 }} />
          </SettingRow>
          <SettingRow label="AI API Key" hint="保存在本机数据库中，不回传第三方">
            <Input value={aiApiKey} onChange={setAiApiKey} placeholder="sk-…" style={{ width: 360 }} />
          </SettingRow>
          <SettingRow label="AI 超时（秒）">
            <Input value={aiTimeout} onChange={setAiTimeout} style={{ width: 120 }} />
          </SettingRow>
          <SettingRow label="Tavily API Key" hint="官方价库刷新用；留空则回退 TAVILY_API_KEY 环境变量">
            <Input value={tavilyKey} onChange={setTavilyKey} placeholder="tvly-…" style={{ width: 360 }} />
          </SettingRow>
          <SettingRow label="事件通知 Webhook" hint="采集出现新增/变化时推送；留空则不推送">
            <Input value={webhook} onChange={setWebhook} placeholder="https://example.com/hook" style={{ width: 360 }} />
          </SettingRow>
          <div style={{ display: "flex", gap: 10 }}>
            <Btn variant="primary" loading={saving} onClick={save}>
              保存设置
            </Btn>
          </div>
        </>
      )}
    </div>
  );
}
