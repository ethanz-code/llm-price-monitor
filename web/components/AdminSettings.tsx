"use client";

/** 系统设置：按「采集调度 / 数据保留 / AI 提取 / 微信通知 WxPusher / 种子导入」分组，AI 与通知可独立测试有效性。 */

import { useEffect, useState } from "react";
import { toast, Btn, Input, Sel, Modal } from "./ui";
import { IconEye, IconEyeOff } from "./icons";
import { apiSend } from "@/lib/api";
import type { SettingsData, SiteConfig } from "@/lib/types";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 四类采集任务的后台定时间隔（分钟），与后端 settings.schedule 默认值保持一致；0 = 关闭定时。 */
const DEFAULT_SCHEDULE_MINUTES: Record<string, number> = { price: 60, status: 5, notice: 30, catalog: 1440 };

const SCHEDULE_ITEMS: { key: string; label: string; hint: string }[] = [
  { key: "price", label: "价格采集", hint: "定时去各站点看价格，有变化就记下来" },
  { key: "status", label: "渠道状态", hint: "定时检查开了状态监测的站点，渠道有变化就记事件" },
  { key: "notice", label: "站点公告", hint: "定时看站点公告，内容有变化就记下来并通知" },
  { key: "catalog", label: "厂商定价", hint: "定时更新厂商原价目录（默认 24 小时一次）" },
];

/** 数据保留天数配置项：与后端 settings 的 retention_*_days 字段一一对应；价格/状态事件与公告永不清理。 */
const DEFAULT_RETENTION_DAYS = 90;

const RETENTION_ITEMS: { key: string; label: string; hint: string }[] = [
  { key: "retention_price_days", label: "价格趋势点", hint: "画价格走势图用的历史点，超期自动清掉" },
  { key: "retention_status_days", label: "渠道状态记录", hint: "渠道可用性的历史检查结果，超期自动清掉" },
  { key: "retention_visit_days", label: "访问统计明细", hint: "每天的浏览量、来访设备等明细，超期自动清掉" },
];

/** 任务记录与日志上限配置项：与后端 settings 的 max_task_* 字段一一对应，超出即淘汰最旧。 */
const DEFAULT_MAX_TASK_RUNS = 100;
const DEFAULT_MAX_LOG_LINES = 500;

/** AI 助手每 IP 每日提问上限：与后端 settings.assistant_daily_limit 对应；0 = 不限制。 */
const DEFAULT_ASSISTANT_DAILY_LIMIT = 10;

function modelsToText(models: SiteConfig["models"]): string {
  return (models ?? []).join(", ");
}

/** 密钥输入：password 型 + 显隐切换，避免旁人瞥屏或截图泄露。 */
function SecretInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <Input
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      type={show ? "text" : "password"}
      autoComplete="off"
      style={{ width: "min(360px, 100%)" }}
      suffix={
        <button
          type="button"
          className="input-suffix"
          onClick={() => setShow((current) => !current)}
          aria-label={show ? "隐藏密钥" : "显示密钥"}
          title={show ? "隐藏" : "显示"}
        >
          {show ? <IconEyeOff /> : <IconEye />}
        </button>
      }
    />
  );
}

function SettingRow({ label, hint, children }: { label: string; hint?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gap: 4 }}>
      {/* label 包裹控件：读屏软件能把字段名和输入框关联起来 */}
      <label style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <span style={{ fontSize: 13.5 }}>{label}</span>
        {children}
      </label>
      {hint && <span style={{ fontSize: 12, color: "var(--text-3)" }}>{hint}</span>}
    </div>
  );
}

type TestTarget = "ai" | "wxpusher";

type TestOutcome = { ok: boolean; text: string };

type TestResponse = { elapsed_ms: number; model?: string; reply?: string };

type SeedResponse = {
  mode: "skip_existing" | "overwrite";
  settings_written: string[];
  ai_written: string[];
  sites_written: number;
  sites_replaced: boolean;
};

function seedResultText(result: SeedResponse): string {
  const parts: string[] = [];
  if (result.settings_written.length) parts.push(`设置 ${result.settings_written.length} 项`);
  if (result.ai_written.length) parts.push(`AI 配置 ${result.ai_written.length} 项`);
  if (result.sites_replaced) parts.push(`站点已替换为 ${result.sites_written} 个`);
  else if (result.sites_written) parts.push(`新增站点 ${result.sites_written} 个`);
  return parts.length ? `种子导入完成：${parts.join("，")}` : "种子导入完成：内容都已是最新，没有要补的";
}

function resultText(target: TestTarget, data: TestResponse): string {
  const ms = `${data.elapsed_ms}ms`;
  if (target === "ai") {
    const reply = data.reply ? ` · 回复「${data.reply.slice(0, 20)}」` : "";
    return `✓ 连通 · ${data.model} · ${ms}${reply}`;
  }
  return `✓ 测试消息已发送 · ${ms}`;
}

function SettingsSection({
  title,
  description,
  action,
  result,
  children,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  result?: TestOutcome;
  children?: React.ReactNode;
}) {
  return (
    <section className="settings-section" style={{ display: "grid", gap: 14, padding: "16px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "grid", gap: 3 }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{title}</span>
          {description && <span style={{ fontSize: 12, color: "var(--text-3)" }}>{description}</span>}
        </div>
        {action}
      </div>
      {children}
      {result && (
        <span style={{ fontSize: 12.5, color: result.ok ? "var(--tone-green-text)" : "var(--tone-red-text)" }}>
          {result.text}
        </span>
      )}
    </section>
  );
}

export function AdminSettings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [wxToken, setWxToken] = useState("");
  const [wxUid, setWxUid] = useState("");
  const [aiBaseUrl, setAiBaseUrl] = useState("");
  const [aiFormat, setAiFormat] = useState("chat_completions");
  const [aiModels, setAiModels] = useState("");
  const [aiApiKey, setAiApiKey] = useState("");
  const [aiTimeout, setAiTimeout] = useState("60");
  const [schedule, setSchedule] = useState<Record<string, string>>({});
  const [retention, setRetention] = useState<Record<string, string>>({});
  const [maxTaskRuns, setMaxTaskRuns] = useState("");
  const [maxLogLines, setMaxLogLines] = useState("");
  const [assistantDailyLimit, setAssistantDailyLimit] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<TestTarget | null>(null);
  const [results, setResults] = useState<Partial<Record<TestTarget, TestOutcome>>>({});
  const [seeding, setSeeding] = useState<"skip_existing" | "overwrite" | null>(null);
  const [seedWarnOpen, setSeedWarnOpen] = useState(false);

  async function load() {
    try {
      const loaded = await apiSend<SettingsData>("/api/settings", "GET");
        setData(loaded);
        setWxToken(typeof loaded.settings.wxpusher_app_token === "string" ? loaded.settings.wxpusher_app_token : "");
        setWxUid(typeof loaded.settings.wxpusher_uid === "string" ? loaded.settings.wxpusher_uid : "");
        const ai = loaded.ai;
        setAiBaseUrl(typeof ai.base_url === "string" ? ai.base_url : "");
        setAiFormat(typeof ai.api_format === "string" && ai.api_format ? ai.api_format : "chat_completions");
        setAiModels(modelsToText((ai.models as SiteConfig["models"]) ?? []));
        setAiApiKey(typeof ai.api_key === "string" ? ai.api_key : "");
        setAiTimeout(String(typeof ai.timeout === "number" ? ai.timeout : 60));
        const rawSchedule = (loaded.settings.schedule ?? {}) as Record<string, unknown>;
        const nextSchedule: Record<string, string> = {};
        for (const item of SCHEDULE_ITEMS) {
          const value = rawSchedule[item.key];
          nextSchedule[item.key] = typeof value === "number" && Number.isFinite(value) ? String(value) : "";
        }
        setSchedule(nextSchedule);
        const nextRetention: Record<string, string> = {};
        for (const item of RETENTION_ITEMS) {
          const value = (loaded.settings as Record<string, unknown>)[item.key];
          nextRetention[item.key] = typeof value === "number" && Number.isFinite(value) ? String(value) : "";
        }
        setRetention(nextRetention);
        setMaxTaskRuns(
          typeof loaded.settings.max_task_runs === "number" && Number.isFinite(loaded.settings.max_task_runs)
            ? String(loaded.settings.max_task_runs)
            : "",
        );
        setMaxLogLines(
          typeof loaded.settings.max_task_log_lines === "number" && Number.isFinite(loaded.settings.max_task_log_lines)
            ? String(loaded.settings.max_task_log_lines)
            : "",
        );
        setAssistantDailyLimit(
          typeof loaded.settings.assistant_daily_limit === "number" && Number.isFinite(loaded.settings.assistant_daily_limit)
            ? String(loaded.settings.assistant_daily_limit)
            : "",
        );
    } catch {
      setData({ settings: {}, ai: {} });
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function save() {
    if (!data) return;
    // 间隔留空 = 回默认值，0 = 关闭该项定时；非法输入直接拦截，不发请求
    const scheduleOut: Record<string, number> = {};
    for (const item of SCHEDULE_ITEMS) {
      const text = (schedule[item.key] ?? "").trim();
      const value = text ? Number.parseFloat(text) : DEFAULT_SCHEDULE_MINUTES[item.key];
      if (!Number.isFinite(value) || value < 0) {
        toast(`${item.label}间隔需是不小于 0 的数字（分钟，0 为关闭）`);
        return;
      }
      scheduleOut[item.key] = value;
    }
    // 保留天数留空 = 回默认值 90 天；非法输入直接拦截，不发请求
    const retentionOut: Record<string, number> = {};
    for (const item of RETENTION_ITEMS) {
      const text = (retention[item.key] ?? "").trim();
      const value = text ? Number.parseFloat(text) : DEFAULT_RETENTION_DAYS;
      if (!Number.isFinite(value) || value < 1 || !Number.isInteger(value)) {
        toast(`${item.label}保留天数需是不小于 1 的整数`);
        return;
      }
      retentionOut[item.key] = value;
    }
    // 任务/日志上限留空 = 回默认值；非法输入直接拦截，不发请求
    const runsText = maxTaskRuns.trim();
    const maxRuns = runsText ? Number.parseFloat(runsText) : DEFAULT_MAX_TASK_RUNS;
    if (!Number.isFinite(maxRuns) || maxRuns < 1 || !Number.isInteger(maxRuns)) {
      toast("任务记录保留条数需是不小于 1 的整数");
      return;
    }
    const logLinesText = maxLogLines.trim();
    const maxLogLinesOut = logLinesText ? Number.parseFloat(logLinesText) : DEFAULT_MAX_LOG_LINES;
    if (!Number.isFinite(maxLogLinesOut) || maxLogLinesOut < 1 || !Number.isInteger(maxLogLinesOut)) {
      toast("单任务日志行数上限需是不小于 1 的整数");
      return;
    }
    // 助手限次留空 = 回默认 10 次；0 = 不限制；非法输入直接拦截，不发请求
    const assistantLimitText = assistantDailyLimit.trim();
    const assistantLimitOut = assistantLimitText ? Number.parseFloat(assistantLimitText) : DEFAULT_ASSISTANT_DAILY_LIMIT;
    if (!Number.isFinite(assistantLimitOut) || assistantLimitOut < 0 || !Number.isInteger(assistantLimitOut)) {
      toast("AI 助手每日提问上限需是不小于 0 的整数（0 为不限制）");
      return;
    }
    const settings = {
      ...data.settings,
      wxpusher_app_token: wxToken.trim() || null,
      wxpusher_uid: wxUid.trim() || null,
      schedule: scheduleOut,
      ...retentionOut,
      max_task_runs: maxRuns,
      max_task_log_lines: maxLogLinesOut,
      assistant_daily_limit: assistantLimitOut,
    };
    // Base URL 留空 = 暂不启用 AI；填了就必须是完整 http(s) 地址，否则要到调用时才报错
    const base = aiBaseUrl.trim();
    if (base && !/^https?:\/\//i.test(base)) {
      toast("AI Base URL 需以 http:// 或 https:// 开头");
      return;
    }
    // 超时非法直接拦截，不发请求；留空 = 回默认 60 秒
    const timeoutText = aiTimeout.trim();
    const timeout = timeoutText ? Number.parseFloat(timeoutText) : 60;
    if (!Number.isFinite(timeout) || timeout <= 0) {
      toast("AI 超时需是大于 0 的数字（秒）");
      return;
    }
    const ai: Record<string, unknown> = {
      ...data.ai,
      enabled: true,
      base_url: base,
      api_format: aiFormat,
      models: aiModels.split(/[,\n]/).map((item) => item.trim()).filter(Boolean),
      api_key: aiApiKey.trim() || null,
      timeout,
    };
    setSaving(true);
    try {
      const saved = await apiSend<SettingsData>("/api/settings", "PUT", { settings, ai });
      setData(saved);
      setResults({});
      toast("设置已保存，立即生效");
    } catch (error) {
      toast(`保存失败: ${errorText(error)}`);
    } finally {
      setSaving(false);
    }
  }

  async function runTest(target: TestTarget) {
    setTesting(target);
    try {
      const result = await apiSend<TestResponse>("/api/settings/test", "POST", {
        target,
        settings: {
          wxpusher_app_token: wxToken.trim() || null,
          wxpusher_uid: wxUid.trim() || null,
        },
        ai: {
          base_url: aiBaseUrl.trim(),
          api_format: aiFormat,
          models: aiModels.split(/[,\n]/).map((item) => item.trim()).filter(Boolean),
          api_key: aiApiKey.trim() || null,
          timeout: Number.isFinite(Number.parseFloat(aiTimeout)) ? Number.parseFloat(aiTimeout) : 60,
        },
      });
      setResults((prev) => ({ ...prev, [target]: { ok: true, text: resultText(target, result) } }));
    } catch (error) {
      setResults((prev) => ({ ...prev, [target]: { ok: false, text: `✗ ${errorText(error)}` } }));
    } finally {
      setTesting(null);
    }
  }

  async function runSeed(mode: "skip_existing" | "overwrite") {
    if (mode === "overwrite") {
      setSeedWarnOpen(true); // 覆盖有风险：先弹确认弹窗再执行
      return;
    }
    await doSeed(mode);
  }

  async function doSeed(mode: "skip_existing" | "overwrite") {
    setSeeding(mode);
    try {
      const result = await apiSend<SeedResponse>("/api/seed", "POST", { mode });
      toast(seedResultText(result));
      // 种子导入会改后端配置，重新拉一遍回填表单，避免面板还显示旧值
      await load();
    } catch (error) {
      toast(`种子导入失败: ${errorText(error)}`);
    } finally {
      setSeeding(null);
    }
  }

  async function confirmOverwriteSeed() {
    await doSeed("overwrite");
    setSeedWarnOpen(false);
  }

  return (
    <div className="panel settings-panel">
      {!data ? (
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>加载中…</span>
      ) : (
        <div style={{ display: "grid" }}>
          <SettingsSection
            title="采集调度"
            description="各项采集多久自动跑一次（分钟），保存后立即生效；填 0 表示关闭定时，只手动采集"
          >
            {SCHEDULE_ITEMS.map((item) => (
              <SettingRow key={item.key} label={`${item.label}间隔（分钟）`} hint={item.hint}>
                <Input
                  value={schedule[item.key] ?? ""}
                  onChange={(value) => setSchedule((prev) => ({ ...prev, [item.key]: value }))}
                  placeholder={`默认 ${DEFAULT_SCHEDULE_MINUTES[item.key]}`}
                  style={{ width: 120, maxWidth: "100%" }}
                />
              </SettingRow>
            ))}
          </SettingsSection>
          <SettingsSection
            title="数据保留"
            description="这些记录超过保留天数会自动清掉；价格的变动事件、渠道状态事件和站点公告不受影响，会一直保留。留空表示默认 90 天"
          >
            {RETENTION_ITEMS.map((item) => (
              <SettingRow key={item.key} label={`${item.label}保留（天）`} hint={item.hint}>
                <Input
                  value={retention[item.key] ?? ""}
                  onChange={(value) => setRetention((prev) => ({ ...prev, [item.key]: value }))}
                  placeholder={`默认 ${DEFAULT_RETENTION_DAYS}`}
                  style={{ width: 120, maxWidth: "100%" }}
                />
              </SettingRow>
            ))}
          </SettingsSection>
          <SettingsSection
            title="任务与日志"
            description="采集任务的记录条数和每次任务的日志行数上限，超出会淘汰最早的记录；留空表示默认值"
          >
            <SettingRow label="任务记录保留条数" hint="采集任务列表最多保留多少次运行记录，超过后最早的被清掉">
              <Input
                value={maxTaskRuns}
                onChange={setMaxTaskRuns}
                placeholder={`默认 ${DEFAULT_MAX_TASK_RUNS}`}
                style={{ width: 120, maxWidth: "100%" }}
              />
            </SettingRow>
            <SettingRow label="单任务日志行数上限" hint="每次任务最多保留多少行日志，超了以后滚动保留最新的">
              <Input
                value={maxLogLines}
                onChange={setMaxLogLines}
                placeholder={`默认 ${DEFAULT_MAX_LOG_LINES}`}
                style={{ width: 120, maxWidth: "100%" }}
              />
            </SettingRow>
          </SettingsSection>
          <SettingsSection
            title="AI 提取"
            description="AI 负责认出模型别名、读本地算不了的站点价格。填好 Base URL 和模型列表就算启用；会产生少量 token 费用"
            result={results.ai}
          >
            <SettingRow label="服务商格式" hint="按你的 AI 服务商选：选 Anthropic 就填 https://api.anthropic.com，选 Gemini 就填 https://generativelanguage.googleapis.com">
              <Sel
                value={aiFormat}
                onChange={setAiFormat}
                options={[
                  { value: "chat_completions", label: "OpenAI 兼容（/chat/completions）" },
                  { value: "openai_responses", label: "OpenAI Responses（/v1/responses）" },
                  { value: "anthropic", label: "Anthropic Messages（/v1/messages）" },
                  { value: "gemini", label: "Gemini（:generateContent）" },
                ]}
              />
            </SettingRow>
            <SettingRow label="AI Base URL">
              <Input value={aiBaseUrl} onChange={setAiBaseUrl} placeholder="https://api.example.com/v1" style={{ width: "min(360px, 100%)" }} />
            </SettingRow>
            <SettingRow label="AI 模型列表" hint="逗号分隔，每次随机用一个">
              <Input value={aiModels} onChange={setAiModels} placeholder="model-a, model-b" style={{ width: "min(360px, 100%)" }} />
            </SettingRow>
            <SettingRow
              label="AI API Key"
              hint={
                <>
                  只存在本机，不会发给第三方。推荐先用阿里云百炼：Base URL 和模型列表已预置，开通即送新人免费额度（
                  <a href="https://help.aliyun.com/zh/model-studio/new-free-quota" target="_blank" rel="noreferrer">额度说明</a>
                  ·
                  <a href="https://help.aliyun.com/zh/model-studio/get-api-key" target="_blank" rel="noreferrer">获取 API Key</a>
                  ）
                </>
              }
            >
              <SecretInput value={aiApiKey} onChange={setAiApiKey} placeholder="sk-…" />
            </SettingRow>
            <SettingRow label="AI 超时（秒）">
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <Input value={aiTimeout} onChange={setAiTimeout} style={{ width: 120, maxWidth: "100%" }} />
                <Btn size="sm" loading={testing === "ai"} onClick={() => runTest("ai")}>
                  测试连通
                </Btn>
              </div>
            </SettingRow>
            <SettingRow label="AI 助手每 IP 每日提问上限" hint="每个访客每天最多向 AI 助手提多少个问题，超了当天就不再回答；填 0 表示不限制">
              <Input
                value={assistantDailyLimit}
                onChange={setAssistantDailyLimit}
                placeholder={`默认 ${DEFAULT_ASSISTANT_DAILY_LIMIT}`}
                style={{ width: 120, maxWidth: "100%" }}
              />
            </SettingRow>
          </SettingsSection>
          <SettingsSection
            title="通知推送（WxPusher）"
            description="站点价格/状态/公告有变化、收到新建议、或有访客提交站点时通知你；Token 留空就只保存不推送"
            action={
              <Btn size="sm" loading={testing === "wxpusher"} onClick={() => runTest("wxpusher")}>
                发测试消息
              </Btn>
            }
            result={results.wxpusher}
          >
            <SettingRow label="WxPusher App Token" hint="在 wxpusher.zjiecode.com 管理后台创建应用后获取">
              <SecretInput value={wxToken} onChange={setWxToken} placeholder="AT_…" />
            </SettingRow>
            <SettingRow label="WxPusher UID" hint="关注自己创建的应用后，在管理后台「用户管理」或客户端里能看到自己的 UID；留空就发给全部关注者">
              <Input value={wxUid} onChange={setWxUid} placeholder="UID_…" style={{ width: "min(360px, 100%)" }} />
            </SettingRow>
          </SettingsSection>
          <SettingsSection
            title="种子导入"
            description="按 config/default-seed.json 把默认配置重新导入一遍；没提到的配置不动"
          >
            <SettingRow label="手动重新导入">
              <div style={{ display: "flex", gap: 10 }}>
                <Btn size="sm" loading={seeding === "skip_existing"} onClick={() => runSeed("skip_existing")}>
                  补齐缺失
                </Btn>
                <Btn size="sm" loading={seeding === "overwrite"} onClick={() => runSeed("overwrite")}>
                  覆盖写入
                </Btn>
              </div>
            </SettingRow>
          </SettingsSection>
          <div className="settings-save-bar">
            <span style={{ fontSize: 12.5, color: "var(--text-3)" }}>修改保存后立即生效</span>
            <Btn variant="primary" loading={saving} onClick={save}>
              保存设置
            </Btn>
          </div>
          <Modal
            open={seedWarnOpen}
            onClose={() => setSeedWarnOpen(false)}
            title="确认覆盖写入？"
            footer={
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
                <Btn onClick={() => setSeedWarnOpen(false)}>取消</Btn>
                <Btn variant="primary" loading={seeding === "overwrite"} onClick={confirmOverwriteSeed}>
                  确认覆盖写入
                </Btn>
              </div>
            }
          >
            <div style={{ display: "grid", gap: 10, fontSize: 13.5, lineHeight: 1.7 }}>
              <span>
                会用 <code>config/default-seed.json</code> 里的值覆盖同名设置
                （调度间隔、超时、AI 配置等回到默认），缺的会补上。
              </span>
              <span style={{ color: "var(--text-3)", fontSize: 12.5 }}>
                没提到的配置不动；种子列了站点才会整体替换站点，默认种子不会清空你的站点。
              </span>
            </div>
          </Modal>
        </div>
      )}
    </div>
  );
}
