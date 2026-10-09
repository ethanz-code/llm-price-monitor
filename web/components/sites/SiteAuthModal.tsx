"use client";

import { useState } from "react";
import { Btn, Input, Modal, Seg, Sel } from "../ui";
import { IconChevronRight } from "../icons";
import { dictToRows, originOf, rowsToText, type KvRow } from "./siteShared";
import { SettingRow } from "../ui";
import { HeadersEditor, SubModalFooter } from "./siteModalParts";
import { ACCESS_VAR, INJECT_TARGETS, toVariableAuthRule, type AuthFields, type AuthMode, type InjectRule, type InjectTarget, type RefreshOverride, type RefreshTestResult } from "./siteAuth";

/* ---------- 子弹窗：认证与续签 ---------- */

/** 用户常把浏览器里整段 Cookie 原样贴进来：去掉 cookie: 头前缀和 new_api_refresh= 名字前缀，只留纯凭据 */
function cleanRefreshToken(text: string, cookieName: string): string {
  let value = text.trim();
  const headerPrefix = value.match(/^cookie\s*:\s*/i);
  if (headerPrefix) value = value.slice(headerPrefix[0].length).trim();
  const name = (cookieName.trim() || "new_api_refresh").toLowerCase();
  if (value.includes(";")) {
    // 整条 Cookie 串（session=…; new_api_refresh=…）：挑出轮换 Cookie 那一段
    for (const pair of value.split(";")) {
      const [key, ...rest] = pair.trim().split("=");
      if (key.trim().toLowerCase() === name && rest.length > 0) return rest.join("=");
    }
    return value;
  }
  const eq = value.indexOf("=");
  if (eq > 0 && value.slice(0, eq).trim().toLowerCase() === name) return value.slice(eq + 1).trim();
  return value;
}

const INJECT_LABELS: Record<InjectTarget, string> = {
  price: "价格采集",
  status: "渠道状态",
  notice: "站点公告",
};

const REFRESH_VAR = "${refresh_token}";

const AUTH_LABELS: Record<AuthMode, string> = { none: "无需认证", token: "固定令牌", session: "登录会话自动续签" };

/** 统一认证头单框 → 注入规则：按第一个冒号拆成 头名/值；拆不出完整一对返回 null（贴的不是整行请求头） */
function parseAuthLine(text: string): InjectRule | null {
  const trimmed = text.trim();
  const colon = trimmed.indexOf(":");
  if (colon <= 0) return null;
  const header = trimmed.slice(0, colon).trim();
  const value = trimmed.slice(colon + 1).trim();
  return header && value ? { header, value } : null;
}

/** 三处注入规则是否完全一致：一致时单框回显整行，不一致留空并默认展开微调区 */
function injectUniform(inject: Record<InjectTarget, InjectRule>): boolean {
  return INJECT_TARGETS.every(
    (target) => inject[target].header === inject.price.header && inject[target].value === inject.price.value,
  );
}

/** 一条规则整批复制到三处目标（「插入到三处」的载体，会覆盖微调区之前的单独改动） */
function allTargetsRule(rule: InjectRule): Record<InjectTarget, InjectRule> {
  return Object.fromEntries(INJECT_TARGETS.map((target) => [target, { ...rule }])) as Record<InjectTarget, InjectRule>;
}

function AuthSubModal({
  initial,
  priceUrl,
  runTest,
  onCommit,
  onClose,
}: {
  /** 打开时的草稿初值；认证方式不传，由字段反推 */
  initial: Omit<AuthFields, "mode">;
  /** 价格采集地址：登录会话模板的续签域名从它推导 */
  priceUrl: string;
  /** 真实调用续签接口；由主弹窗基于当前草稿组装完整配置发请求 */
  runTest: (overrides: RefreshOverride) => Promise<{ ok: boolean; text: string; result?: RefreshTestResult }>;
  onCommit: (fields: AuthFields, rotation?: RefreshTestResult) => void;
  /** 关闭时带回未落草稿的换新凭证（轮换型凭据取消也不能丢）；主弹窗只接凭证、其他改动照旧丢弃 */
  onClose: (rotation?: RefreshTestResult) => void;
}) {
  // 认证方式由当前配置反推：配了续签是登录会话，配了站点令牌是固定令牌
  const [mode, setModeState] = useState<AuthMode>(() =>
    initial.url.trim() || initial.cookieName.trim() ? "session" : initial.authToken.trim() ? "token" : "none",
  );
  const [authToken, setAuthToken] = useState(initial.authToken);
  const [method, setMethod] = useState(initial.method);
  const [url, setUrl] = useState(initial.url);
  const [token, setToken] = useState(initial.token);
  const [body, setBody] = useState(initial.body);
  const [sample, setSample] = useState(initial.sample);
  const [headersRows, setHeadersRows] = useState<KvRow[]>(dictToRows(initial.headersText));
  const [cookieName, setCookieName] = useState(initial.cookieName);
  // AI 分析出的字段路径没有表单入口，展示出来让用户知道分析到了什么；模板预填时本地补默认值，确定才回写
  const [accessTokenField, setAccessTokenField] = useState(initial.accessTokenField);
  const [refreshTokenField, setRefreshTokenField] = useState(initial.refreshTokenField);
  // 凭证注入规则：把上面的 token 塞进价格/渠道状态/公告三处请求，头名与值都可改
  const [inject, setInject] = useState<Record<InjectTarget, InjectRule>>(initial.inject);
  // 统一认证头单框：三处初始一致时回显整行，不一致留空并展开微调区；只作插入工具，不随微调区回写
  const [injectBox, setInjectBox] = useState(() => {
    const first = initial.inject.price;
    return injectUniform(initial.inject) && first.header.trim() && first.value.trim()
      ? `${first.header}: ${first.value}`
      : "";
  });
  const [fineTuneOpen, setFineTuneOpen] = useState(() => !injectUniform(initial.inject));
  const [insertedAck, setInsertedAck] = useState(false);
  // 确定被拦截后置位：注入区红字提醒，底部主按钮变成「插入并确定」
  const [insertNotice, setInsertNotice] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  // 测试成功后暂存的新 token：点确定才随表单一并回写主弹窗草稿，取消则全部丢弃
  const [rotation, setRotation] = useState<RefreshTestResult | null>(null);
  // 标准 new-api 模板（GET + Cookie 凭据、无请求体）配好时高级选项整体收起：贴凭据 → 测试 就完了；
  // 模板可能在弹窗里切换方式时才带出，所以动态判定，用户手动展开/收起后以手动为准
  const [advancedOverride, setAdvancedOverride] = useState<boolean | null>(null);
  // 凭据获取步骤默认折叠，主界面只留一句"凭据是什么"
  const [showHowTo, setShowHowTo] = useState(false);
  const standardTemplate =
    url.trim() !== "" &&
    body.trim() === "" &&
    cookieName.trim().toLowerCase() === "new_api_refresh" &&
    headersRows.some((row) => row.key.trim().toLowerCase() === "cookie" && row.value.includes("new_api_refresh="));
  const showAdvanced = advancedOverride ?? !standardTemplate;
  const headersText = rowsToText(headersRows);

  // 切到登录会话且续签还空白时，带出 new-api 型模板（域名从价格采集地址推导）；已填过的一律不动
  function setMode(next: string) {
    const nextMode: AuthMode = next === "session" ? "session" : next === "token" ? "token" : "none";
    setModeState(nextMode);
    setTestResult(null);
    if (nextMode !== "session") return;
    if (url.trim() || cookieName.trim()) return;
    const origin = originOf(priceUrl);
    if (origin) setUrl(`${origin}/api/user/auth/refresh`);
    if (headersRows.every((row) => !row.key.trim() && !row.value.trim())) {
      setHeadersRows([{ key: "cookie", value: "new_api_refresh=${refresh_token}" }]);
    }
    if (!cookieName.trim()) setCookieName("new_api_refresh");
  }

  // 续签即时校验：地址格式、请求体占位符、响应案例 JSON，填错当场提示不用等保存
  const urlError = url.trim() && !/^https?:\/\/\S+\.\S+/.test(url.trim()) ? "地址要以 http(s):// 开头且带域名" : "";
  const bodyError =
    body.trim() && !body.includes("${refresh_token}")
      ? "请求体里要写 ${refresh_token}，续签时才能自动代入凭证"
      : "";
  const sampleError = (() => {
    const text = sample.trim();
    if (!text) return "";
    try {
      JSON.parse(text);
      return "";
    } catch {
      return "案例不是合法 JSON，AI 分析不了；贴一段接口实际返回的 JSON";
    }
  })();

  // 凭证注入即时校验：头名和值成对填；引用 ${refresh_token} 需要「登录会话」模式才有值
  const injectError = (() => {
    const half = INJECT_TARGETS.find((target) => Boolean(inject[target].header.trim()) !== Boolean(inject[target].value.trim()));
    return half ? `${INJECT_LABELS[half]}的头名和值要成对填；那处不想注入就两个都清空` : "";
  })();
  const injectWarning =
    mode === "token" && INJECT_TARGETS.some((target) => inject[target].value.includes(REFRESH_VAR))
      ? `「固定令牌」没有 Refresh Token，值里引用 ${REFRESH_VAR} 的规则采集时会报错；要用它就把认证方式换成「登录会话自动续签」`
      : "";
  // 单框解析与插入判定：解析结果与三处当前值一致才算已插入；头名比较不区分大小写，避免无谓的重复提醒。
  // 解析出两段式 Bearer 认证头时令牌段自动换成 ${access_token}：写死的令牌续签后没人换，注入值必须引用凭证变量
  const rawParsedInject = parseAuthLine(injectBox);
  const parsedInject = rawParsedInject ? toVariableAuthRule(rawParsedInject) : null;
  const parsedToVariable = parsedInject !== null && rawParsedInject !== null && parsedInject.value !== rawParsedInject.value;
  const injectParseError =
    injectBox.trim() && !parsedInject ? `按 头名: 值 的格式贴，冒号前是头名，如 Authorization: Bearer ${ACCESS_VAR}` : "";
  const boxApplied =
    parsedInject !== null &&
    INJECT_TARGETS.every(
      (target) =>
        inject[target].header.trim().toLowerCase() === parsedInject.header.toLowerCase() &&
        inject[target].value.trim() === parsedInject.value,
    );
  const pendingInsert = injectBox.trim() !== "" && !boxApplied;
  const threeUniform = injectUniform(inject);

  const overrides = (): RefreshOverride => ({
    method,
    url,
    refresh_token: cleanRefreshToken(token, cookieName),
    body,
    response_sample: sample,
    headers_text: headersText,
    refresh_cookie_name: cookieName,
  });
  const fields = (): AuthFields => ({
    mode,
    authToken,
    method,
    url,
    token: cleanRefreshToken(token, cookieName),
    body,
    sample,
    accessTokenField,
    refreshTokenField,
    headersText,
    cookieName,
    inject,
  });

  function onMethodChange(next: string) {
    setMethod(next);
    setTestResult(null);
  }

  function onUrlChange(next: string) {
    setUrl(next);
    setTestResult(null);
  }

  // 真实调用一次续签接口：验证地址、凭证、响应结构是否都能对上
  async function test() {
    if (!url.trim()) {
      setTestResult({ ok: false, text: "先点开「续签接口」填上地址再测试" });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const outcome = await runTest(overrides());
      setTestResult({ ok: outcome.ok, text: outcome.text });
      if (outcome.ok && outcome.result) {
        // 新 token 先回填到本弹窗输入框，点确定才落草稿：Refresh Token 供下次续签，
        // Access Token 就是采集请求头里带的那份，测试换新的直接填上让用户看得见
        setToken(outcome.result.refresh_token);
        setAuthToken(outcome.result.access_token);
        setRotation(outcome.result);
      }
    } finally {
      setTesting(false);
    }
  }

  // 把单框解析出的整行认证头整批覆盖到三处；微调区里之前的单独改动随之统一
  function applyInsert() {
    if (!parsedInject) return;
    setInject(allTargetsRule(parsedInject));
    setInsertedAck(true);
    setInsertNotice(false);
  }

  function commit(injectOverride?: Record<InjectTarget, InjectRule>) {
    // 登录会话必须有续签地址，否则配置落不下去；不打算配就切回其他方式
    if (mode === "session" && !url.trim()) {
      setTestResult({ ok: false, text: "先点开「续签接口」填上地址；不配认证就把方式切回「无需认证」" });
      return;
    }
    if (injectError && !injectOverride) {
      setTestResult({ ok: false, text: injectError });
      return;
    }
    // 单框里有内容但还没插入：拦下来提醒，底部主按钮变成「插入并确定」；不要了就清空输入框
    if (pendingInsert && !injectOverride) {
      setInsertNotice(true);
      return;
    }
    onCommit({ ...fields(), inject: injectOverride ?? inject }, rotation ?? undefined);
  }

  // 取消也把换新凭证带回主弹窗：测试这一下就可能把旧 Refresh Token 作废，丢掉只能回浏览器重新抓。
  // 测试后又手改过任一 token 输入框的，以手改为准，全部丢弃
  function discard() {
    const untouched =
      rotation !== null &&
      token.trim() === rotation.refresh_token &&
      authToken.trim() === rotation.access_token;
    onClose(untouched && rotation ? rotation : undefined);
  }

  return (
    <Modal
      open
      onClose={discard}
      title="认证与续签"
      width={640}
      footer={
        <SubModalFooter
          onCancel={discard}
          onConfirm={() => commit(insertNotice && parsedInject ? allTargetsRule(parsedInject) : undefined)}
          confirmLabel={insertNotice && parsedInject ? "插入并确定" : undefined}
        />
      }
    >
      <div style={{ display: "grid", gap: 14 }}>
        {/* 三种方式互斥，选中才显示对应输入区；被切走方式的配置在点确定时清掉 */}
        <SettingRow
          label="认证方式"
          hint={
            mode === "session"
              ? "站点登录 token 短效时，配一个续签接口：价格、渠道状态、公告被拒时自动换新 token 重试"
              : mode === "token"
                ? "填站点级的 API Key，价格、渠道状态、公告采集自动带上认证头"
                : "公开接口不用认证；之后要认证了随时回来切"
          }
        >
          <Seg
            value={mode}
            onChange={setMode}
            options={(Object.keys(AUTH_LABELS) as AuthMode[]).map((value) => ({ value, label: AUTH_LABELS[value] }))}
          />
        </SettingRow>
        {mode === "none" && (
          <span style={{ fontSize: 12, color: "var(--text-3)" }}>
            采集请求不带任何认证；如果某个接口的请求头里手写过凭证，仍按原样发送，不受这里影响
          </span>
        )}
        {mode === "token" && (
          <div style={{ display: "grid", gap: 8 }}>
            <span style={{ fontSize: 13.5 }}>站点令牌</span>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              会自动加到价格、渠道状态、公告所有采集请求的认证头（默认 Authorization: Bearer …）；
              保存后各接口请求头里手写的 Authorization 会自动移除，认证只走这一处
            </span>
            <Input value={authToken} onChange={setAuthToken} placeholder="sk-… 或令牌原文" style={{ width: "100%" }} />
          </div>
        )}
        {mode === "session" && (
          <div style={{ display: "grid", gap: 14 }}>
            <div style={{ display: "grid", gap: 6 }}>
              <div style={{ display: "flex", alignItems: "center" }}>
                <span style={{ fontSize: 13.5, flex: 1 }}>Refresh Token</span>
                <Btn variant="text" size="sm" onClick={() => setShowHowTo(!showHowTo)}>
                  {showHowTo ? "收起" : "怎么获取？"}
                </Btn>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <Input
                  value={token}
                  onChange={(value) => {
                    setToken(value);
                    setTestResult(null);
                  }}
                  placeholder="粘贴 Cookie 里的 new_api_refresh 值，即续签请求里的 ${refresh_token}"
                  style={{ flex: 1, minWidth: 0 }}
                />
                <Btn loading={testing} onClick={test}>
                  测试续签
                </Btn>
              </div>
              {testResult && (
                <span style={{ fontSize: 12.5, color: testResult.ok ? "var(--tone-green-text)" : "var(--tone-red-text)" }}>
                  {testResult.text}
                </span>
              )}
              {showHowTo && (
                <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                  登录站点网页版 → 按 F12 打开开发者工具 → 应用（Application）→ Cookies → 找到 new_api_refresh，复制它的值贴到上面，
                  整段带着 cookie: 前缀直接贴也认得。贴完回浏览器重新登录一次站点，两边各用各的会话；凭据最长 30 天，到期重抓一次更新
                </span>
              )}
            </div>
            <div style={{ display: "grid", gap: 6 }}>
              <span style={{ fontSize: 13.5 }}>Access Token</span>
              <Input
                value={authToken}
                onChange={(value) => {
                  setAuthToken(value);
                  setTestResult(null);
                }}
                placeholder="选填：F12 → 网络 → 任一接口请求头里 Authorization 的 Bearer 后面那段"
                style={{ width: "100%" }}
              />
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                采集价格、渠道状态、公告时带的登录凭证就是它（Authorization: Bearer …）；填了立刻能用，留空也行——
                第一次采集被拒时会自动续签补上，之后每次续签也会自动换成新的
              </span>
            </div>
            <button type="button" className="disclosure-row" aria-expanded={showAdvanced} onClick={() => setAdvancedOverride(!showAdvanced)}>
              <span className="caret" aria-hidden>
                <IconChevronRight size={13} />
              </span>
              <span style={{ fontWeight: 500, whiteSpace: "nowrap" }}>续签接口</span>
              {url.trim() ? (
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-3)" }}>
                  {method} {url.trim()}
                </span>
              ) : (
                <span style={{ flex: 1, minWidth: 0, color: "var(--tone-red-text)" }}>未配置，点这行填上</span>
              )}
              {standardTemplate && (
                <span style={{ fontSize: 11, lineHeight: 1.6, padding: "0 7px", borderRadius: 999, background: "var(--tone-blue-bg)", color: "var(--tone-blue-text)", whiteSpace: "nowrap" }}>
                  new-api 模板
                </span>
              )}
            </button>
            {showAdvanced && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 10, background: "color-mix(in srgb, var(--panel-2) 45%, var(--panel))", padding: "10px 12px", display: "grid", gap: 14 }}>
                <div style={{ display: "grid", gap: 4 }}>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <Sel
                      value={method}
                      onChange={onMethodChange}
                      options={["GET", "POST", "PUT", "PATCH"].map((item) => ({ value: item, label: item }))}
                      style={{ width: 92 }}
                    />
                    <Input
                      value={url}
                      onChange={onUrlChange}
                      placeholder="续签接口地址，如 https://example.com/api/v1/auth/refresh"
                      style={{ flex: 1, minWidth: 220 }}
                    />
                  </div>
                  {urlError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{urlError}</span>}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ flex: 1, height: 1, background: "var(--border-strong)" }} />
                  <span style={{ fontSize: 12, color: "var(--text-3)", whiteSpace: "nowrap" }}>请求细节（一般不用改）</span>
                  <span style={{ flex: 1, height: 1, background: "var(--border-strong)" }} />
                </div>
                <div style={{ display: "grid", gap: 6 }}>
                  <span style={{ fontSize: 13.5 }}>请求体</span>
                  <Input
                    value={body}
                    onChange={(value) => {
                      setBody(value);
                      setTestResult(null);
                    }}
                    placeholder={'选填：接口要 JSON 参数才填，如 {"refresh_token": "${refresh_token}"}；凭据走 Cookie 的留空'}
                    style={{ width: "100%" }}
                  />
                  {bodyError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{bodyError}</span>}
                </div>
                <HeadersEditor
                  rows={headersRows}
                  onChange={(next) => {
                    setHeadersRows(next);
                    setTestResult(null);
                  }}
                  warningKeys={[]}
                  hint={null}
                  keyPlaceholder="名字，如 cookie"
                  valuePlaceholder="值，如 new_api_refresh=${refresh_token}，续签自动代入最新值"
                />
                <div style={{ display: "grid", gap: 6 }}>
                  <span style={{ fontSize: 13.5 }}>轮换 Cookie 名</span>
                  <Input
                    value={cookieName}
                    onChange={(value) => {
                      setCookieName(value);
                      setTestResult(null);
                    }}
                    placeholder="如 new_api_refresh；填了能自动接住换新后的凭据，不用回浏览器重抓"
                    style={{ width: "100%" }}
                  />
                </div>
                <div style={{ display: "grid", gap: 6 }}>
                  <span style={{ fontSize: 13.5 }}>响应案例</span>
                  <textarea
                    className="input mono textarea"
                    value={sample}
                    onChange={(event) => setSample(event.target.value)}
                    rows={3}
                    spellCheck={false}
                    placeholder="选填：贴一段续签接口实际返回的 JSON，保存时自动分析新 token 在哪；不贴按常见结构找"
                    style={{ fontSize: 12 }}
                  />
                  {sampleError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{sampleError}</span>}
                </div>
              </div>
            )}
          </div>
        )}
        {/* 凭证注入：三处一致是常态，只留一个整行输入框 + 插入按钮；个别站点要按目标区分时展开微调区 */}
        {mode !== "none" && (
          <div style={{ display: "grid", gap: 8 }}>
            <span style={{ fontSize: 13.5 }}>凭证注入</span>
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>
              决定上面的 token 怎么带进采集请求。推荐直接填 Authorization: Bearer {ACCESS_VAR}
              （{ACCESS_VAR} 代表上面的凭证，续签换新自动跟上），或把 F12 里的整行认证头贴进来点插入——
              价格、渠道状态、站点公告三处一起带上，写死的令牌段会自动换成 {ACCESS_VAR}；
              值里也可以引用 {REFRESH_VAR}，仅「登录会话自动续签」有值
            </span>
            <div style={{ display: "flex", gap: 8 }}>
              <Input
                value={injectBox}
                ariaLabel="统一认证头"
                onChange={(value) => {
                  setInjectBox(value);
                  setInsertedAck(false);
                  setInsertNotice(false);
                }}
                placeholder={`推荐 Authorization: Bearer ${ACCESS_VAR}，或贴 F12 里的整行认证头`}
                style={{ flex: 1, minWidth: 0 }}
              />
              <Btn disabled={!parsedInject} onClick={applyInsert}>
                插入到三处
              </Btn>
            </div>
            {injectParseError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{injectParseError}</span>}
            {insertedAck && (
              <span style={{ fontSize: 12, color: "var(--tone-green-text)" }}>
                {parsedToVariable
                  ? `已插入到三处，令牌段换成了 ${ACCESS_VAR}，续签换新自动跟上；点确定生效`
                  : "已插入到三处，点确定生效"}
              </span>
            )}
            {!threeUniform && (
              <span style={{ fontSize: 12, color: "var(--tone-yellow-text)" }}>
                三处当前不一样：想让某处不带认证就在「按目标微调」里清空那一行；要统一就贴整行认证头点插入
              </span>
            )}
            <button
              type="button"
              className="disclosure-row"
              aria-expanded={fineTuneOpen}
              onClick={() => setFineTuneOpen(!fineTuneOpen)}
            >
              <span className="caret" aria-hidden>
                <IconChevronRight size={13} />
              </span>
              <span style={{ fontWeight: 500, whiteSpace: "nowrap" }}>按目标微调</span>
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  color: "var(--text-3)",
                }}
              >
                {threeUniform
                  ? inject.price.header.trim()
                    ? `三处一致：${inject.price.header}`
                    : "三处都不注入"
                  : "三处不一致"}
              </span>
            </button>
            {fineTuneOpen && (
              <div style={{ display: "grid", gap: 8 }}>
                {INJECT_TARGETS.map((target) => (
                  <div key={target} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <span style={{ width: 64, fontSize: 12.5, color: "var(--text-2)", flexShrink: 0 }}>
                      {INJECT_LABELS[target]}
                    </span>
                    <Input
                      value={inject[target].header}
                      ariaLabel={`${INJECT_LABELS[target]}注入的头名`}
                      onChange={(value) => setInject({ ...inject, [target]: { ...inject[target], header: value } })}
                      placeholder="头名，如 Authorization 或 cookie"
                      style={{ flex: 1, minWidth: 0 }}
                    />
                    <Input
                      value={inject[target].value}
                      ariaLabel={`${INJECT_LABELS[target]}注入的值`}
                      onChange={(value) => setInject({ ...inject, [target]: { ...inject[target], value: value } })}
                      placeholder={`值，如 Bearer ${ACCESS_VAR}`}
                      style={{ flex: 1.6, minWidth: 0 }}
                    />
                  </div>
                ))}
                <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                  哪处不想注入就把那一行清空；这里改的值不会被单框回写，再点一次插入才会整批覆盖
                </span>
              </div>
            )}
            {insertNotice && (
              <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>
                {parsedInject
                  ? "上面贴的认证头还没插入到三处，点右下角「插入并确定」一并完成；不想要就清空输入框"
                  : "输入框里的内容不是 头名: 值 的格式，插不进去；改好再试，或者清空它"}
              </span>
            )}
            {injectError && <span style={{ fontSize: 12, color: "var(--tone-red-text)" }}>{injectError}</span>}
            {injectWarning && <span style={{ fontSize: 12, color: "var(--tone-yellow-text)" }}>{injectWarning}</span>}
          </div>
        )}
      </div>
    </Modal>
  );
}

export { AuthSubModal };
