"""测试全局隔离：调度线程与 Store 相对路径曾把测试数据泄漏进真实仓库库，这里双保险堵死。

- PRICE_MONITOR_DB 指向每个测试自己的临时文件：create_app 的 Store 不再按 cwd 解析，
  即使调度线程活到测试结束（cwd 已恢复），也只会碰已被清理的临时路径。
- teardown 调 stop_scheduler()：让测试期间启动的调度线程当场退出，不留活口。
"""
import pytest

import llm_price_monitor.ai as ai_mod
from llm_price_monitor import visitor_geo
from llm_price_monitor.webapi import scheduler, tasks


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    # 路径与 workspace fixture 的约定一致（tmp_path/var/…），测试里直接打开 sqlite 断言的用例才能看到同一张库
    monkeypatch.setenv("PRICE_MONITOR_DB", str(tmp_path / "var" / "monitor.db"))
    # 抽取的模型冷却名单是 ai 模块级状态，不清场会跨测试污染（测试反复用同一批假模型名）
    monkeypatch.setattr(ai_mod, "_MODEL_COOLDOWN", {})
    # 访问统计会顺带解析访客 IP 归属地（外网请求），测试一律离线替代
    monkeypatch.setattr(
        visitor_geo,
        "resolve_regions",
        lambda ips: {
            ip: {"country": "中国", "region": "广东省", "province": "广东", "city": "深圳", "lat": 22.5, "lon": 114.0}
            for ip in ips
        },
    )
    yield
    scheduler.stop_scheduler()
    # 任务注册表进程级共享（reset 注释本就要求"测试间需要显式清场"）：上一个测试残留的
    # 运行中任务（如在途的目录刷新线程）会让下一个测试提交同类任务撞互斥被 409 拒绝
    tasks.reset()
