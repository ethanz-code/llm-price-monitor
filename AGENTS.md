# llm-price-monitor 项目约定（AI 会话自动加载）

## 必读文档（按任务选读）

- 全自动协作协议：docs/ai-collab.md（先读）
- 部署/运维：docs/deploy/ai-runbook.md（事实卡、红线、故障速查）+ docs/deploy/docker-compose.md
- 服务器现状档案（域名/证书/1Panel 站点/出口 IP，敏感不入公开仓库）：~/workspace/docs/私人资料/服务器/，部署到线上时与本项目 runbook 配套读
- 采集与价格口径：docs/pricing.md；认证/反爬实测坑：Serena 记忆 `dom_url_auth_constraint`
- UI 规范：docs/design-guide.md
- 历史踩坑速查：docs/lessons.md（动手前先翻，别重踩）

## 全自动开发协议（一次对话交付，全程自动推进、不逐条确认，不设任何 grill/追问环节）

用户开场一次性说需求，此后全程自动推进，不逐条询问：

1. 中途所有决策自己做：按 docs/ai-collab.md 默认决策清单与项目惯例选推荐项，并记录「选了什么、为什么」。
2. 主线跑通后主动加码：按更高标准补齐体验、文案、边界情况，汇报时列出加的部分。
3. 处理不了的（缺凭据/外部信息/需要用户环境）：不停等——记入「待你处理」清单（说明卡点与建议做法）先跳过，继续做其余部分，回复末尾集中列出；用户下轮输入后接着做。
4. 红线：删数据、发版上线、重启用户在跑的服务一律不擅自执行，进待确认清单。
5. 需求再模糊也不反问：列出关键假设，直接做 v1。
6. 小步交付每步自验（测试/浏览器走查），结果如实报告；完成判据是自验绿，不是"我觉得写完了"。
7. 长任务先列任务清单再动手，每阶段把「决策/进度/待处理」落盘到 .zcode/plans/，会话压缩或中断可无损续跑；大任务拆 subagent 并行。
8. 防死循环：同一问题连续两次同一修法失败就换思路，再不行记「待你处理」清单跳过；途中命中 docs/lessons.md 触发词索引的关键词先翻坑再动手。

## 环境事实

- 后端 Python 3.12+（uv 管理，llm_price_monitor/），前端 Next.js（web/，npm），数据 SQLite var/monitor.db。
- 开发模式 `uv run price-web --dev`；测试 `uv run pytest tests -q`（HTTPX mock，不请求真实站点）。
- 端口：API 8437、前端 3000；更新/回滚走 APP_TAG（见 docker-compose.md）。
- 不替用户启动/重启项目；需要服务在线时先探测端口，不在线请用户自己启动。

## 经验沉淀

踩新坑收尾时：通用坑追加进 docs/lessons.md 对应分类，跨会话硬约束写 Serena 记忆。
