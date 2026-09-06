"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { apiAuthPost, apiSend, fetchMeta } from "@/lib/api";
import { Btn, Input, Switch } from "@/components/ui";
import { LogoMark } from "@/components/LogoMark";
import { IconCheck } from "@/components/icons";

/** 首次设置向导：创建管理员账号 → 配置必填密钥（可跳过）→ 完成指引。
 *  仅在数据库还没有管理员账号时可用；完成后 /setup 不可重复进入。 */

type Step = 1 | 2 | 3;

const STEPS: { id: Step; label: string }[] = [
  { id: 1, label: "创建账号" },
  { id: 2, label: "配置密钥" },
  { id: 3, label: "开始使用" },
];

export default function SetupPage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [step, setStep] = useState<Step>(1);

  useEffect(() => {
    fetchMeta().then((meta) => {
      if (meta?.needs_setup) setChecking(false);
      else router.replace(meta?.is_admin ? "/admin" : "/login");
    });
  }, [router]);

  if (checking) {
    return (
      <div className="auth-page">
        <span className="skel" style={{ width: 420, height: 300 }} />
      </div>
    );
  }

  return (
    <div className="auth-page">
      <div className="auth-card auth-card-wide">
        <LogoMark size={36} />
        <h1 className="auth-title">开始使用</h1>
        <p className="auth-sub">第一次运行需要三步：创建管理员账号、填入必需的密钥，然后添加要监控的站点。</p>
        <ol className="setup-steps">
          {STEPS.map((item) => (
            <li key={item.id} className={item.id === step ? "on" : item.id < step ? "done" : ""}>
              <span className="mono">{item.id < step ? "✓" : `0${item.id}`}</span>
              {item.label}
            </li>
          ))}
        </ol>
        {step === 1 && <StepAccount onDone={() => setStep(2)} />}
        {step === 2 && <StepKeys onDone={() => setStep(3)} onSkip={() => setStep(3)} />}
        {step === 3 && <StepDone />}
      </div>
    </div>
  );
}

/* ---------- 第 1 步：创建管理员账号 ---------- */

function StepAccount({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("两次输入的密码不一致");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await apiAuthPost("/api/setup", { username, password });
      onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  return (
    <form className="auth-form" onSubmit={submit}>
      <label className="auth-field">
        <span>用户名</span>
        <Input value={username} onChange={setUsername} autoComplete="username" />
      </label>
      <label className="auth-field">
        <span>密码（至少 6 位）</span>
        <Input value={password} onChange={setPassword} type="password" autoComplete="new-password" />
      </label>
      <label className="auth-field">
        <span>确认密码</span>
        <Input value={confirm} onChange={setConfirm} type="password" autoComplete="new-password" />
      </label>
      {error && <p className="auth-error" role="alert">{error}</p>}
      <Btn variant="primary" size="lg" type="submit" loading={loading} className="auth-submit">
        创建账号并继续
      </Btn>
      <p className="auth-hint">账号保存在本机数据库中；忘记密码可用 <code className="mono">uv run price-admin</code> 重置。</p>
    </form>
  );
}

/* ---------- 第 2 步：必需密钥（均可跳过，之后在系统设置里补填） ---------- */

function StepKeys({ onDone, onSkip }: { onDone: () => void; onSkip: () => void }) {
  const [aiEnabled, setAiEnabled] = useState(true);
  const [aiBaseUrl, setAiBaseUrl] = useState("");
  const [aiModels, setAiModels] = useState("");
  const [aiApiKey, setAiApiKey] = useState("");
  const [tavilyKey, setTavilyKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function save() {
    setLoading(true);
    setError(null);
    try {
      const ai: Record<string, unknown> = { enabled: aiEnabled };
      if (aiBaseUrl.trim()) ai.base_url = aiBaseUrl.trim();
      const models = aiModels.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
      if (models.length) ai.models = models;
      if (aiApiKey.trim()) ai.api_key = aiApiKey.trim();
      const settings: Record<string, unknown> = {};
      if (tavilyKey.trim()) settings.tavily_api_key = tavilyKey.trim();
      await apiSend("/api/settings", "PUT", { settings, ai });
      onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-form">
      <div className="setup-keys-group">
        <div className="setup-keys-head">
          <span>AI 兜底提取</span>
          <Switch checked={aiEnabled} onChange={setAiEnabled} />
        </div>
        <p className="auth-hint">开启后每次采集都会调用 AI：标准 New API 站点仅解析模型别名（价格仍本地计算），格式不明站点由 AI 提取价格；结果按证据哈希缓存，会产生 token 费用。</p>
        {aiEnabled && (
          <div className="setup-keys-fields">
            <label className="auth-field">
              <span>Base URL</span>
              <Input value={aiBaseUrl} onChange={setAiBaseUrl} placeholder="https://api.example.com/v1" />
            </label>
            <label className="auth-field">
              <span>模型列表（逗号分隔）</span>
              <Input value={aiModels} onChange={setAiModels} placeholder="model-a, model-b" />
            </label>
            <label className="auth-field">
              <span>API Key</span>
              <Input value={aiApiKey} onChange={setAiApiKey} placeholder="sk-…" type="password" />
            </label>
          </div>
        )}
      </div>
      <div className="setup-keys-group">
        <div className="setup-keys-head">
          <span>Tavily API Key</span>
        </div>
        <p className="auth-hint">刷新厂商官方价库用；留空则回退 TAVILY_API_KEY 环境变量。</p>
        <Input value={tavilyKey} onChange={setTavilyKey} placeholder="tvly-…" type="password" />
      </div>
      {error && <p className="auth-error" role="alert">{error}</p>}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <Btn variant="primary" size="lg" loading={loading} onClick={save}>
          保存并继续
        </Btn>
        <Btn size="lg" onClick={onSkip}>
          暂时跳过
        </Btn>
      </div>
    </div>
  );
}

/* ---------- 第 3 步：完成 ---------- */

const NEXT_STEPS = [
  { title: "添加监控站点", text: "在「站点管理」里新增中转站的价格接口地址与目标模型。", href: "/admin/sites", label: "去添加站点" },
  { title: "触发首次采集", text: "在「概览」或「采集任务」里点「立即采集」，跑通第一轮价格取证。", href: "/admin/tasks", label: "去采集" },
  { title: "刷新官方价库", text: "配置好 Tavily 与 AI 后，刷新厂商官方定价，折扣对比才有锚点。", href: "/admin/settings", label: "查看设置" },
];

function StepDone() {
  return (
    <div className="setup-done">
      <ul>
        {NEXT_STEPS.map((item) => (
          <li key={item.title}>
            <div>
              <div className="setup-done-title">
                <IconCheck size={13} /> {item.title}
              </div>
              <p>{item.text}</p>
            </div>
            <Link href={item.href} className="landing-more">
              {item.label} →
            </Link>
          </li>
        ))}
      </ul>
      <Btn variant="primary" size="lg" onClick={() => (window.location.href = "/admin")}>
        进入管理面板
      </Btn>
    </div>
  );
}
