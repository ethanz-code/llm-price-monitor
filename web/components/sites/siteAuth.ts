/* ---------- 站点编辑：子弹窗公共部分 ---------- */

/** 续签字段的局部覆盖：onChange 里刚敲入、还没写回主状态的值用 overrides 显式传入 */
type RefreshOverride = Partial<{
  method: string;
  url: string;
  refresh_token: string;
  body: string;
  response_sample: string;
  access_token_field: string;
  refresh_token_field: string;
  headers_text: string;
  refresh_cookie_name: string;
}>;

/** 测试续签接口的成功返回：新 access_token 用于回填各处认证头，refresh_token 可能已被服务端换新 */
type RefreshTestResult = { access_token: string; refresh_token: string; refresh_token_rotated: boolean };

/** 凭证注入的三个目标：与后端 AUTH_INJECT_TARGETS 一致 */
type InjectTarget = "price" | "status" | "notice";

const INJECT_TARGETS: InjectTarget[] = ["price", "status", "notice"];

/** 注入值里可引用的凭证变量（与后端 config.ACCESS_TOKEN_VAR / REFRESH_TOKEN_VAR 一致） */
const ACCESS_VAR = "${access_token}";

/** 一条凭证注入规则：头名（Authorization / cookie / 任意）+ 值模板（可用 ${access_token}、${refresh_token}） */
type InjectRule = { header: string; value: string };

/** 认证与续签子弹窗提交回主弹窗的字段集合 */
type AuthFields = {
  mode: AuthMode;
  authToken: string;
  method: string;
  url: string;
  token: string;
  body: string;
  sample: string;
  accessTokenField: string;
  refreshTokenField: string;
  headersText: string;
  cookieName: string;
  inject: Record<InjectTarget, InjectRule>;
};

/** 认证方式：无需认证 / 固定令牌 / 登录会话自动续签 */
type AuthMode = "none" | "token" | "session";

export type { AuthFields, AuthMode, InjectRule, InjectTarget, RefreshOverride, RefreshTestResult };
export { ACCESS_VAR, INJECT_TARGETS };
