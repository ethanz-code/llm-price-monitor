import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

/** 前端纯函数单测（lib/ 金额与折叠口径）：别名与 tsconfig 的 @/* 一致；
 *  不引 jsdom/RTL——先把测试网铺在最容易错钱的纯函数上，组件测试后续按需再加。 */
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
});
