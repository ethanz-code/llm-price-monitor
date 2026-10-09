import { describe, expect, it } from "vitest";

import { ACCESS_VAR, toVariableAuthRule } from "./siteAuth";

/** 贴入整行认证头时的令牌段换变量：写死的令牌续签后没人同步，必须换成 ${access_token} 引用 */
describe("toVariableAuthRule", () => {
  it("Authorization + Bearer 两段式换成引用变量的值", () => {
    expect(toVariableAuthRule({ header: "Authorization", value: "Bearer eyJhbGci.x.y" })).toEqual({
      header: "Authorization",
      value: `Bearer ${ACCESS_VAR}`,
    });
  });

  it("头名与 scheme 大小写随意都认，scheme 原样保留", () => {
    expect(toVariableAuthRule({ header: "authorization", value: "bearer abc123" })).toEqual({
      header: "authorization",
      value: `bearer ${ACCESS_VAR}`,
    });
  });

  it("已是变量形式时幂等，不产生第二个变量", () => {
    const rule = { header: "Authorization", value: `Bearer ${ACCESS_VAR}` };
    expect(toVariableAuthRule(rule)).toEqual(rule);
  });

  it("非 Authorization 头是静态凭据，原样保留", () => {
    const rule = { header: "x-api-key", value: "sk-abc123" };
    expect(toVariableAuthRule(rule)).toEqual(rule);
    expect(toVariableAuthRule({ header: "cookie", value: "session=abc" })).toEqual({ header: "cookie", value: "session=abc" });
  });

  it("非 Bearer scheme 或值不止两段时不改写", () => {
    expect(toVariableAuthRule({ header: "Authorization", value: "Basic dXNlcjpwYXNz" })).toEqual({
      header: "Authorization",
      value: "Basic dXNlcjpwYXNz",
    });
    expect(toVariableAuthRule({ header: "Authorization", value: "Bearer abc def" })).toEqual({
      header: "Authorization",
      value: "Bearer abc def",
    });
    expect(toVariableAuthRule({ header: "Authorization", value: "Bearer" })).toEqual({
      header: "Authorization",
      value: "Bearer",
    });
  });
});
