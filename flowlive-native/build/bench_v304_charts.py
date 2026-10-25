# -*- coding: utf-8 -*-
"""量化各图表的真实重绘成本，判断"哪些值得做增量"。

★ 纪律：只优化**实测占比显著**的项。FanChart 已做增量（92ms→1.18ms），
  其余图表若本身只有几毫秒，就不该为它们引入复杂度与风险。
"""
import os
import sys
import time
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402
app = QApplication.instance() or QApplication(sys.argv)

import importlib.util                                                    # noqa: E402
spec = importlib.util.spec_from_file_location(
    "appmod", os.path.join(HERE, "app_main_v203.py"))
appmod = importlib.util.module_from_spec(spec)
sys.modules["appmod"] = appmod
try:
    spec.loader.exec_module(appmod)
except SystemExit:
    pass
except Exception as e:
    print("载入异常:", type(e).__name__, e)
try:
    appmod.ensure_mpl()
except Exception:
    pass

import numpy as np                                                       # noqa: E402


def bench(name, fn, rounds=10):
    try:
        fn()                      # 预热
        ts = []
        for _ in range(rounds):
            t0 = time.time()
            fn()
            ts.append((time.time() - t0) * 1000)
        med = statistics.median(ts)
        print("  %-34s %8.2f ms" % (name, med))
        return med
    except Exception as e:
        print("  %-34s 失败 %s: %s" % (name, type(e).__name__, e))
        return None


print("=" * 72)
print("各图表单次完整重绘成本（离屏）")
print("=" * 72)

N = 240
times = ["%02d:%02d" % (9 + (31 + i) // 60, (31 + i) % 60) for i in range(N)]

results = {}

# FanChart
FanChart = getattr(appmod, "FanChart", None)
if FanChart:
    fc = FanChart()
    rng = np.random.RandomState(0)
    series = [{"name": "板块%02d" % i,
               "series": list(np.cumsum(rng.randn(N))),
               "net": 1.0,
               "direction": "in" if i < 10 else "out",
               "times": times} for i in range(20)]
    results["FanChart"] = bench("FanChart 整幅 plot (20线×240点)",
                                lambda: fc.plot(series))

# IndexChart
IndexChart = getattr(appmod, "IndexChart", None)
if IndexChart:
    ic = IndexChart()
    idx = [(times[i], 3100 + i * 0.5) for i in range(N)]
    idx2 = [(times[i], 4100 + i * 0.6) for i in range(N)]
    results["IndexChart"] = bench("IndexChart 整幅 plot (2线×240点)",
                                  lambda: ic.plot(idx, idx2))

# FlowBarChart
FlowBarChart = getattr(appmod, "FlowBarChart", None)
if FlowBarChart:
    fb = FlowBarChart()
    din = [{"name": "板块%d" % i, "net_yi": 10 - i} for i in range(10)]
    dout = [{"name": "板块%d" % (i + 20), "net_yi": -(10 - i)} for i in range(10)]
    results["FlowBarChart"] = bench("FlowBarChart 整幅 plot (20柱)",
                                    lambda: fb.plot(din, dout, topn=10))

# ThemeBoard
ThemeBoard = getattr(appmod, "ThemeBoard", None)
if ThemeBoard:
    tb = ThemeBoard()
    themes = [{"name": "题材%d" % i, "net_yi": 5 - i * 0.2, "n": 3}
              for i in range(8)]
    results["ThemeBoard"] = bench("ThemeBoard 整幅 plot (8项)",
                                  lambda: tb.plot(themes))

# LeaderGrid / StageGrid
for cls_name, arg in (("LeaderGrid", []), ("StageGrid", [])):
    cls = getattr(appmod, cls_name, None)
    if cls:
        try:
            g = cls()
            results[cls_name] = bench("%s 整幅 plot (空)" % cls_name,
                                      lambda g=g: g.plot(arg))
        except Exception as e:
            print("  %s: %s" % (cls_name, e))

print()
print("=" * 72)
print("诊断")
print("=" * 72)
if results:
    tot = sum(v for v in results.values() if v)
    print("  六图全量合计: %.2f ms" % tot)
    print()
    print("  占比排序：")
    for k, v in sorted(results.items(), key=lambda x: -(x[1] or 0)):
        if v:
            print("    %-16s %7.2f ms   %5.1f%%" % (k, v, v / tot * 100))
    print()
    print("  ★ 判据：占比 >30%% 值得做增量；<5%% 做了也是浪费复杂度。")
    fan = results.get("FanChart")
    if fan and tot:
        print("  ★ FanChart 占 %.1f%% —— %s"
              % (fan / tot * 100,
                 "已是主要成本，做增量收益最大（已完成）" if fan / tot > 0.3
                 else "占比不高，但已顺手完成且无副作用"))
