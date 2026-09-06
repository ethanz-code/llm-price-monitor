"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { apiAuthPost, fetchMeta } from "@/lib/api";
import { Btn, Input } from "@/components/ui";
import { LogoMark } from "@/components/LogoMark";

/** 管理员登录：已有会话直接进面板；未创建账号时转首次设置。 */
export default function LoginPage() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchMeta().then((meta) => {
      if (meta?.needs_setup) router.replace("/setup");
      else if (meta?.is_admin) router.replace("/admin");
      else setChecking(false);
    });
  }, [router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await apiAuthPost("/api/auth/login", { username, password });
      router.push("/admin");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      {checking ? (
        <span className="skel" style={{ width: 320, height: 220 }} />
      ) : (
        <div className="auth-card">
          <LogoMark size={36} />
          <h1 className="auth-title">登录</h1>
          <p className="auth-sub">管理员登录后可管理站点、触发采集与修改系统设置。</p>
          <form className="auth-form" onSubmit={submit}>
            <label className="auth-field">
              <span>用户名</span>
              <Input value={username} onChange={setUsername} autoComplete="username" />
            </label>
            <label className="auth-field">
              <span>密码</span>
              <Input value={password} onChange={setPassword} type="password" autoComplete="current-password" />
            </label>
            {error && <p className="auth-error" role="alert">{error}</p>}
            <Btn variant="primary" size="lg" type="submit" loading={loading} className="auth-submit">
              登录
            </Btn>
          </form>
          <p className="auth-hint">
            忘记密码？在服务器上运行 <code className="mono">uv run price-admin</code> 重置。
          </p>
          <Link href="/" className="auth-back">← 返回首页</Link>
        </div>
      )}
    </div>
  );
}
