# UI 设计指南

本文档从当前实现（`web/app/globals.css` + `web/components/`）提炼，是后续所有页面与组件的视觉基准。
整体风格参照阿里云百炼控制台：**白底发丝描边卡片、克制的灰阶层级、彩色只做点缀、数据优先于装饰**。
改样式先改 token，不要在组件里写死颜色和字号。

## 1. 设计原则

1. **控制台式信息密度**：数据优先，装饰最少；层级靠字号和字重，不靠装饰件。
2. **品牌色只做点缀**：荧光绿 `#c8ff00` 只出现在品牌块、主按钮填充和选中强调；正文、表格、标题永远走灰阶。
3. **无衬线紧凑排版**：标题 600 字重小号，不追求"展示型大字"。
4. **深色优先**：SSR 默认暗色；所有颜色通过 `[data-theme]` 的 CSS 变量切换，组件里不写死色值。
5. **动效克制**：只做 0.15–0.25s 的 transform/opacity 过渡，不做装饰性循环动画。

## 2. 色彩 token（`:root` 亮色 / `[data-theme="dark"]` 暗色）

| Token | 亮色 | 暗色 | 用途 |
| --- | --- | --- | --- |
| `--bg` | `#fafafa` | `#0a0c0e` | 页面底色 |
| `--panel` | `#ffffff` | `#121418` | 卡片底 |
| `--panel-2` | `#f4f6f8` | `#171a1f` | 次级底（提示条、表头、嵌套区块） |
| `--border` | `rgba(13,22,31,.08)` | `rgba(255,255,255,.08)` | 发丝线（卡片描边、分隔线） |
| `--border-strong` | `rgba(13,22,31,.16)` | `rgba(255,255,255,.16)` | 强分隔、hover 描边 |
| `--text` | `#212a33` | `#e6e8ec` | 主文字 |
| `--text-2` | `#5d6a77` | `#9aa0a8` | 次级文字（说明、副标题） |
| `--text-3` | `#6b7480` | `#858c94` | 弱化文字（标签、时间戳） |
| `--accent` | `#c8ff00` | `#c8ff00` | 品牌绿：主按钮填充、Logo、选中强调 |
| `--accent-text` | `#3f5600` | `#c8ff00` | 品牌色可读文本（链接 hover、强调词） |
| `--accent-contrast` | `#101418` | `#0b0d0f` | 品牌色底上的文字 |
| `--header-bg` | `rgba(255,255,255,.8)` | `rgba(10,12,14,.72)` | 吸顶导航毛玻璃底 |
| `--card-border` | `1px solid var(--border)` | 同亮色 | 卡片描边 |
| `--shadow-panel` | 两层 rgba ≤.04 | rgba .25/.15 | 卡片极轻投影 |
| `--tooltip-bg` | `#171a1f` | `#171a1f` | 深色气泡（两主题刻意同值） |

语义状态色（徽标、折扣条、渠道点、图表共用）：`--tone-green/blue/yellow/red/gray` 各有 `-bg` + `-text`；
图表三档 `--chart-ok`（亮 `#86c200` / 暗 `#c8ff00`）、`--chart-warn`、`--chart-down` 与 `components/chartTheme.tsx` 保持同值。
需要新颜色时先加 token，不写裸色值；浅色底上的彩色文字对比度必须 ≥ 4.5:1。

## 3. 字体与字号

- `--font-sans`：Geist Sans（next/font 注入）+ 苹方/雅黑兜底，正文与标题。
- `--font-mono`：Geist Mono，**所有数字**（价格、百分比、时间戳、限额）用 `.mono.num`，配 `tabular-nums`。
- 衬线已全面退役（`--font-serif` 已删除），不要再引入。

层级标尺（无衬线，层级靠字号+字重）：

| 场景 | 字号/字重 |
| --- | --- |
| 首页 Hero 标题 | clamp(30px, 3.6vw, 44px) / 600 |
| 页头标题 `.page-title` | clamp(22px, 2.2vw, 28px) / 600 |
| 区块标题 `.section-title` | 15px / 600（落地页区块头 20px / 600） |
| 弹窗标题 | 16px / 600 |
| 正文 / 表格单元格 | 13–14px / 400–550 |
| 辅助说明、提示条正文 | 12.5px |
| 表头 | 12px / 500 / `--text-2`（不加字距、不全大写） |
| KPI 大数字 | mono 28px / 600 |

行高：正文 1.6–1.7，说明性文字 1.75。

## 4. 布局与间距

- 内容容器 `.page`：`padding: 76px 32px 96px`（顶部留白大于吸顶导航高度），`overflow-x: clip`。
- 页头 `PageHeader`：标题 + 副标题，底部发丝线（`padding-bottom: 28px; margin-bottom: 32px`）。**不加 eyebrow/面包屑装饰**，当前页由顶部导航标识。
- 区块间距 `.section-gap`：32px；提示条自带 `margin-bottom: 20px`，不与内容贴死。
- 卡片内边距 18–28px（统计卡 20px 22px、站点卡 20px、入门清单 28px 32px）。
- 层级 z-index 标尺：悬浮球 90 < 吸顶导航 100 < 弹出层 200 < 弹窗 1000 < 全局顶层 1100。
- 断点：1100 / 900 / 720 / 640 / 480 / 420。移动端基线 390px，**页面不得出现横向滚动**。

## 5. 形状：圆角 / 描边 / 投影

- 圆角：卡片 12px；提示条 8px；按钮、输入框、seg 项 4–6px；胶囊/圆点 9999px。
- 卡片 = `--card-border` 发丝描边 + `--shadow-panel` 极轻投影；**不用大投影、不用无边框毛玻璃当卡片**（导航胶囊除外）。
- 描边与分隔线统一用 `--border`；hover 强化才用 `--border-strong`。

## 6. 组件规范（`web/components/ui.tsx`）

- **Panel 卡片**：`.panel` 基座；结构分隔用内部发丝线，不叠多层投影。
- **Alert 提示条**：`info` 用 `--panel-2` 灰底、`warn` 用 `--tone-yellow-bg`；前置 `IconAlertCircle`（14px）；**无彩色左竖线、无白底描边卡**。整宽告警用 `band` 变体（tone 底色通栏铺开）。
- **数据表格 `.dtable`**：表头 12px 灰字、底部分隔线用 inset 阴影；单元格 `padding: 15px 12px`，首尾列 16px；行 hover 变底色；横向滚动只发生在 `.dtable-scroll` 内。
- **按钮 `.btn`**：高 34px，圆角 4px；`primary` = accent 填充 + `--accent-contrast` 文字；`ghost` / `text` 走描边或纯文字。
- **Seg 分段控件**：小胶囊组，激活项亮底 + `--shadow-pop`。
- **Tip 名词提示**：深色气泡 `--tooltip-bg`，portal 到 body 防裁切。
- **Modal / Toaster / Skel**：统一从 `ui.tsx` 引用，弹窗圆角 12px。
- 数字一律 `.mono.num`；价格展示用 `PriceCell`，不要手拼字符串。

## 7. 动效

- 时长 token：`--dur-fast 0.15s` / `--dur 0.2s` / `--dur-slow 0.25s`，缓动统一 `--ease: cubic-bezier(0.16, 1, 0.3, 1)`。
- 只动 `transform` / `opacity`；hover 给轻反馈（变底色、描边强化、上浮 2px 以内）。
- `prefers-reduced-motion: reduce` 下所有动画全量降级。

## 8. "去 AI 味"禁止清单

以下元素是 AI 生成落地页的典型签名，**一律不用**；删掉的实现不要找回：

- 衬线展示大字、等宽全大写英文眉题（OVERVIEW 式）、装饰性字距。
- 渐变/发光光晕、斜向光束背景、金属镀铬质感、发光球体渐变、呼吸/波纹循环动画、打字机与闪烁光标。
- 彩色左竖线告警条、大面积彩色底色块、彩虹点缀。
- 仿真终端窗口（假 shell、红黄绿圆点、假文件名）。

彩色只允许出现在：品牌按钮/Logo、状态语义色（正常/警告/故障/折扣档位）、链接与选中强调。

## 9. 新页面上手清单

- [ ] `PageHeader` + `.page` 容器起步，不传 eyebrow。
- [ ] 卡片用 `.panel`，提示用 `Alert`，表格用 `.dtable`，数字加 `.mono.num`。
- [ ] 颜色、字号、圆角、动效全部引用 token；需要新值先在 `:root` 加 token。
- [ ] grid / flex 子项记得 `min-width: 0`，长模型名、长 URL 要能截断。
- [ ] 390px 宽度过一遍：无横向滚动、表格在 `.dtable-scroll` 内滚、卡片不贴边。
- [ ] 亮色、暗色两套主题各看一眼，确认对比度和彩色点缀没有超范围。
