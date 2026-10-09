import { beforeEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "./proxy";

const request = (path: string, userAgent: string) =>
  new NextRequest(`http://localhost:3000${path}`, { headers: { "user-agent": userAgent } });

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"ok":true}')));
});

it("浏览器访问正常上报", async () => {
  proxy(request("/discover", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"));
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  const [, init] = vi.mocked(fetch).mock.calls[0];
  expect(JSON.parse(String(init?.body))).toEqual({ path: "/discover" });
});

it("wget / curl 等机器流量不上报（compose 健康检查不再刷库）", () => {
  proxy(request("/", "Wget/1.21.2"));
  proxy(request("/", "curl/8.4.0"));
  proxy(request("/overview", "Mozilla/5.0 (compatible; UptimeRobot/2.0)"));
  expect(fetch).not.toHaveBeenCalled();
});
