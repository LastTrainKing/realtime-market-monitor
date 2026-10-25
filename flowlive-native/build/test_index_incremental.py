# -*- coding: utf-8 -*-
"""验证 v3.0.4 IndexChart 增量更新。"""
import os
import sys
import time
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


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

IndexChart = appmod.IndexChart
N = 260
# 造够长的分钟标签（260 个，覆盖全天 240 分钟还多）
T = ["%02d:%02d" % (9 + (31 + i) // 60, (31 + i) % 60) for i in range(N)]


def mk(n):
    a = [(T[i], 3100 + i * 1.0) for i in range(n)]
    b = [(T[i], 4100 + i * 0.8) for i in range(n)]
    return a, b


def chg_of(c, line):
    """取某条线的涨跌幅序列（已转成 %）。"""
    return list(line.get_ydata())


print("=" * 72)
print("A. 正确性：增量后数据 == 整幅重建后的数据")
print("=" * 72)
c1, c2 = IndexChart(), IndexChart()
d0 = mk(20)
c1.plot(*d0); c2.plot(*d0)

d1 = mk(26)                      # 多 6 个点
ok = c2.try_update_series(*d1)
check(ok, "try_update_series 返回 True")

c1.plot(*d1)                     # 基准整幅重建
# 取两条主线的 ydata（排除零轴 axhline，它是常量线）
def lines_of(c):
    ax = c.fig.axes[0]
    out = []
    for ln in ax.get_lines():
        y = list(ln.get_ydata())
        if len(y) > 1:
            out.append(y)
    return out

l1, l2 = lines_of(c1), lines_of(c2)
check(len(l1) == len(l2), "线数一致", "%d vs %d" % (len(l1), len(l2)))
same = all(len(a) == len(b) and all(abs(x - y) < 1e-9 for x, y in zip(a, b))
           for a, b in zip(l1, l2))
check(same, "逐点数据一致")
check(len(l2[0]) == 26, "增量后点数正确", "%d" % len(l2[0]))

print()
print("=" * 72)
print("B. 回退守卫")
print("=" * 72)
c3 = IndexChart()
c3.plot(*mk(20))
check(c3.try_update_series([], None) is False, "单侧为空 → False")
check(c3.try_update_series(mk(20)[0], None) is False, "只有上证 → False")
c4 = IndexChart()
check(c4.try_update_series(*mk(20)) is False, "未 plot 过 → False")
# 只画上证的情形不允许增量
c5 = IndexChart()
c5.plot(mk(20)[0], None)
check(c5.try_update_series(*mk(20)) is False, "上次只有上证 → False")

print()
print("=" * 72)
print("C. 提速")
print("=" * 72)
cA = IndexChart()
cA.plot(*mk(200))
tf = []
for k in range(8):
    d = mk(201 + k)
    t0 = time.time(); cA.plot(*d); tf.append((time.time() - t0) * 1000)

cB = IndexChart()
cB.plot(*mk(200))
ti = []
for k in range(8):
    d = mk(201 + k)
    t0 = time.time()
    if not cB.try_update_series(*d):
        print("  ⚠ 增量失败")
    ti.append((time.time() - t0) * 1000)

mf, mi = statistics.median(tf), statistics.median(ti)
print("  整幅重建 plot()           %8.2f ms" % mf)
print("  增量 try_update_series()  %8.2f ms   %.1fx" % (mi, mf / mi))
check(mi < mf * 0.5, "增量至少 2× 快", "%.2f -> %.2f (%.1fx)" % (mf, mi, mf / mi))

print()
print("=" * 72)
print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 72)
sys.exit(1 if FAIL else 0)
