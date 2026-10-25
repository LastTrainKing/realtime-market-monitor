# -*- coding: utf-8 -*-
"""v3.0.6 审计修复回归：缓存一致性、跨日状态与图表增量布局。"""
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def check(cond, name, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s %s" % (name, detail))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, detail))


# ★★ v3.0.6：本用例验证的是**东财通道**的历史补全行为，必须把数据源钉死在
#   东财 —— 否则在默认已切到替代源的机器上，`backfill.fetch_daykline_map`
#   会因为"替代源不具备日级资金流历史"而直接返回空（这是刻意的守卫），
#   被测对象就不是本用例要测的那条路径了。
os.environ.setdefault("FLOWLIVE_SOURCE", "eastmoney")
import flowdata
import store
import backfill
import tradingtime
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)
spec = importlib.util.spec_from_file_location("audit_app", os.path.join(HERE, "app_main_v203.py"))
appmod = importlib.util.module_from_spec(spec)
sys.modules["audit_app"] = appmod
spec.loader.exec_module(appmod)
appmod.ensure_mpl()

print("=" * 74)
print("A. 面板缓存完整性与唯一临时写入")
print("=" * 74)
with tempfile.TemporaryDirectory() as root:
    old_data = flowdata.DATA
    try:
        flowdata.set_data_dir(root)
        meta = {"times": ["09:31"], "codes": ["x"],
                "names": {"x": "X"}, "kinds": {"x": "concept"}}
        flowdata.save_panel("20260923", meta, np.array([[1.0]]))
        check(store.list_local_panels(root) == ["20260923"], "完整 json+npy 面板可回看")
        os.remove(os.path.join(root, "panel_20260923.json"))
        check(store.list_local_panels(root) == [], "缺失 json 的半套面板不列入回看")
        flowdata.save_panel("20260923", meta, np.array([[2.0]]))
        _meta, mat, _idx = flowdata.load_panel("20260923")
        check(mat.shape == (1, 1) and float(mat[0, 0]) == 2.0,
              "同 tag 重写后读取到完整新快照")
    finally:
        flowdata.set_data_dir(old_data)

print("\n" + "=" * 74)
print("B. 日级部分缓存必须补抓缺口")
print("=" * 74)
with tempfile.TemporaryDirectory() as root:
    today = datetime.now().strftime("%Y-%m-%d")
    cache_path = os.path.join(root, "daykline_cache.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"fetch_date": today, "rows": {"a": [["20260923", 1.0]]}}, f)
    calls = []
    original_fetch = backfill._fetch_one_daykline
    try:
        backfill._fetch_one_daykline = lambda secid: (calls.append(secid) or [("20260923", 2.0)])
        got = backfill.fetch_daykline_map(["a", "b"], data_dir=root)
        check(calls == ["b"], "缓存命中 a 时仅请求缺失 b", repr(calls))
        check(set(got) == {"a", "b"}, "缓存与补抓结果合并")
    finally:
        backfill._fetch_one_daykline = original_fetch

print("\n" + "=" * 74)
print("C. 图表增量不会重复压缩标签带")
print("=" * 74)
FanChart = appmod.FanChart
chart = FanChart()


def rows(n):
    times = ["09:%02d" % (31 + i) for i in range(n)]
    return [
        {"name": "流入", "direction": "in", "series": list(np.arange(n, dtype=float)), "times": times},
        {"name": "流出", "direction": "out", "series": list(-np.arange(n, dtype=float)), "times": times},
    ]

chart.plot(rows(5))
w0 = chart.fig.axes[0].get_position().width
ok1 = chart.try_update_series(rows(6))
w1 = chart.fig.axes[0].get_position().width
ok2 = chart.try_update_series(rows(7))
w2 = chart.fig.axes[0].get_position().width
check(ok1 and ok2, "连续增量路径可用")
check(abs(w0 - w1) < 1e-9 and abs(w1 - w2) < 1e-9,
      "标签带宽度不随刷新逐轮压缩", "%.4f / %.4f / %.4f" % (w0, w1, w2))

print("\n" + "=" * 74)
print("D. 跨日状态与交易日观测")
print("=" * 74)
fake = type("Fake", (), {})()
fake.live_tag = "20260923"
fake._last_fetch_bar = "15:00"
fake._catchup_done = True
fake._auto_backfill_done = True
fake.leader_key = ("20260923", 240)
fake._leader_tag = "20260923"
fake.leader_rows = [{"ok": True}]
old_log = appmod.log_line
try:
    appmod.log_line = lambda _msg: None
    changed = appmod.MainWindow._rollover_live_day(fake, datetime(2026, 9, 24, 9, 31))
    check(changed and fake.live_tag == "20260924" and fake._last_fetch_bar is None and
          not fake._catchup_done and fake.leader_rows == [], "跨日重置当日运行态")
finally:
    appmod.log_line = old_log

tradingtime.reset_observation()
try:
    changed, tag = tradingtime.note_data_date("20260922", now=datetime(2026, 9, 24, 10, 0))
    check(changed and tag == "20260924", "陈旧数据日期触发休市观测")
finally:
    tradingtime.reset_observation()

print("\n" + "=" * 74)
print("结果：PASS %d / FAIL %d" % (PASS, FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
