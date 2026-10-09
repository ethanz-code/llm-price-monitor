"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { apiAuthPost, apiSend, fetchAuthState } from "@/lib/api";
import { Btn, Input } from "@/components/ui";
import { LogoMark } from "@/components/LogoMark";
import { IconCheck } from "@/components/icons";

/** 首次设置向导：创建管理员账号 → 配置必填密钥（可跳过）→ 监控模型（可跳过）→ 完成指引。
 *  仅在数据库还没有管理员账号时可用；完成后 /setup 不可重复进入。 */

type Step = 1 | 2 | 3 | 4;

const STEPS: { id: Step; label: string }[] = [
  { id: 1, label: "创建账号" },
  { id: 2, label: "配置密钥" },
  { id: 3, label: "检测模型" },
  { id: 4, label: "开始使用" },
];

export default function SetupPage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [step, setStep] = useState<Step>(1);

  useEffect(() => {
    fetchAuthState().then((state) => {
      if (state?.needs_setup) setChecking(false);
      else router.replace(state?.is_admin ? "/admin" : "/login");
    });
  }, [router]);

  if (checking) {
    return (
      <div className="auth-page">
        <span className="skel" style={{ width: "min(420px, 100%)", height: 300 }} />
      </div>
    );
  }

  return (
    <div className="auth-page">
      <div className="auth-card auth-card-wide">
        <LogoMark size={36} />
        <h1 className="auth-title">开始使用</h1>
        <p className="auth-sub">首次运行需完成四步：创建管理员账号、配置必需的密钥、选择要检测的模型、添加要检测的站点。</p>
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
        {step === 3 && <StepModels onDone={() => setStep(4)} onSkip={() => setStep(4)} />}
        {step === 4 && <StepDone />}
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
  const [aiBaseUrl, setAiBaseUrl] = useState("");
  const [aiModels, setAiModels] = useState("");
  const [aiApiKey, setAiApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // 默认种子里预置了推荐 AI（阿里云百炼）：带出 Base URL 与模型列表，管理员只需填 Key
    apiSend<{ ai: { base_url?: unknown; models?: unknown } }>("/api/settings", "GET")
      .then((loaded) => {
        if (typeof loaded.ai?.base_url === "string" && loaded.ai.base_url) setAiBaseUrl(loaded.ai.base_url);
        const models = Array.isArray(loaded.ai?.models) ? loaded.ai.models : [];
        setAiModels(models.filter((item): item is string => typeof item === "string").join(", "));
      })
      .catch(() => {}); // 读取失败就保持空表单，手动填写
  }, []);

  async function save() {
    setLoading(true);
    setError(null);
    try {
      const ai: Record<string, unknown> = { enabled: true };
      if (aiBaseUrl.trim()) ai.base_url = aiBaseUrl.trim();
      const models = aiModels.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
      if (models.length) ai.models = models;
      if (aiApiKey.trim()) ai.api_key = aiApiKey.trim();
      const settings: Record<string, unknown> = {};
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
          <span>AI 提取</span>
        </div>
        <p className="auth-hint">
          标准协议站点在本地直接解析定价，AI 用于识别模型别名与非标准定价格式；结果会缓存以减少重复调用，仍可能产生 token 费用。
          推荐使用阿里云百炼免费模型：Base URL 与模型列表已预置，仅需创建一个 API Key 填入下方。新用户开通百炼即赠新人免费额度
          （90 天，北京地域），无需实名认证：
          <a href="https://help.aliyun.com/zh/model-studio/new-free-quota" target="_blank" rel="noreferrer">免费额度说明</a>
          ·
          <a href="https://help.aliyun.com/zh/model-studio/get-api-key" target="_blank" rel="noreferrer">获取 API Key</a>
        </p>
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

/* ---------- 第 3 步：监控模型（可跳过，留空则按官方目录自动补各厂商最新模型） ---------- */

function StepModels({ onDone, onSkip }: { onDone: () => void; onSkip: () => void }) {
  const [models, setModels] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    apiSend<{ settings?: { monitor_models?: unknown } }>("/api/settings", "GET")
      .then((loaded) => {
        const existing = Array.isArray(loaded.settings?.monitor_models) ? loaded.settings.monitor_models : [];
        setModels(existing.filter((item): item is string => typeof item === "string").join(", "));
      })
      .catch(() => {}); // 读取失败就保持空表单，跳过也能起步
  }, []);

  async function save() {
    setLoading(true);
    setError(null);
    try {
      const list = models.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
      await apiSend("/api/settings", "PUT", { settings: { monitor_models: list }, ai: {} });
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
          <span>选择要检测的模型</span>
        </div>
        <p className="auth-hint">
          输入模型名称（逗号分隔），所有站点统一按此清单采集价格；后续可在「站点管理」顶部调整。
          不想现在选也行：目录每天刷新时会自动把各厂商最新发布的模型加入清单。
        </p>
        <div className="setup-keys-fields">
          <label className="auth-field">
            <span>检测模型（逗号分隔，留空则自动添加各厂商最新模型）</span>
            <Input value={models} onChange={setModels} placeholder="gpt-5.6, claude-sonnet-5, glm-5.3…" />
          </label>
        </div>
      </div>
      {error && <p className="auth-error" role="alert">{error}</p>}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <Btn variant="primary" size="lg" loading={loading} onClick={save}>
          保存并继续
        </Btn>
        <Btn size="lg" onClick={onSkip}>
          先跳过
        </Btn>
      </div>
    </div>
  );
}

/* ---------- 第 4 步：完成 ---------- */

const NEXT_STEPS = [
  { title: "添加检测站点", text: "在「站点管理」里新增中转站的价格接口地址与登录凭证。", href: "/admin/sites", label: "去添加站点" },
  { title: "等待自动采集", text: "系统会按「系统设置」里的频率自动采集，第一次价格数据很快就有。", href: "/admin/tasks", label: "看采集任务" },
];

function StepDone() {
  const [autoFill, setAutoFill] = useState<"running" | "manual" | null>(null);

  useEffect(() => {
    let cancelled = false;
    // 监控清单留空：补一次目录同步，各厂商最新发布的模型自动进清单；同步失败则提示手动选择
    apiSend<{ settings?: { monitor_models?: unknown } }>("/api/settings", "GET")
      .then((loaded) => {
        if (cancelled) return;
        const models = Array.isArray(loaded.settings?.monitor_models) ? loaded.settings.monitor_models : [];
        if (models.length) return;
        setAutoFill("running");
        apiSend("/api/catalog/refresh", "POST").catch(() => {
          if (!cancelled) setAutoFill("manual");
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="setup-done">
      {autoFill === "running" && (
        <p className="auth-hint">正在同步官方目录，各厂商最新发布的模型将自动添加至检测清单。</p>
      )}
      {autoFill === "manual" && (
        <p className="auth-hint">还没选要检测的模型：可在「站点管理」顶部添加。</p>
      )}
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
