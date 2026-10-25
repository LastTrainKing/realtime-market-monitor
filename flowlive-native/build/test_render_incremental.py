# -*- coding: utf-8 -*-
"""验证 v3.0.4 渲染增量更新（FanChart.try_update_series）。

三件事：
  A. 正确性 —— 增量更新后的曲线数据必须与"整幅重建"逐点一致
  B. 回退   —— 指纹不符（曲线增删/改名）时必须返回 False 并保持可画
  C. 提速   —— 增量 vs 整幅重建的耗时对比
"""
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


from PySide6.QtWidgets import QApplication                             # noqa: E402
app = QApplication.instance() or QApplication(sys.argv)

import importlib.util                                                   # noqa: E402

# app_main_v203 是脚本而非包，直接按模块载入
spec = importlib.util.spec_from_file_location(
    "appmod", os.path.join(HERE, "app_main_v203.py"))
appmod = importlib.util.module_from_spec(spec)
sys.modules["appmod"] = appmod
try:
    spec.loader.exec_module(appmod)
except SystemExit:
    pass
except Exception as e:
    print("载入模块时异常（可能是 GUI 相关）:", type(e).__name__, e)

FanChart = None
# MplCanvas/FanChart 定义在 ensure_mpl() 之后的 if 块里 —— 需要先触发
try:
    appmod.ensure_mpl()
except Exception as e:
    print("ensure_mpl:", e)
FanChart = getattr(appmod, "FanChart", None)
if FanChart is None:
    print("✗ 取不到 FanChart 类，无法测试")
    sys.exit(2)

import numpy as np                                                      # noqa: E402

TIMES = ["09:%02d" % (31 + i) for i in range(10)]                       # 10 个分钟
NAMES = [("板块%02d" % i) for i in range(6)]


def mk_series(n_pts, seed=0):
    rng = np.random.RandomState(seed)
    out = []
    for i, nm in enumerate(NAMES):
        direction = "in" if i < 3 else "out"
        out.append({"name": nm,
                    "series": list(np.cumsum(rng.randn(n_pts)).round(3)),
                    "net": 1.0, "direction": direction,
                    "times": TIMES[:n_pts]})
    return out


print("=" * 72)
print("A. 正确性：增量更新 vs 整幅重建")
print("=" * 72)

# 用两个独立实例对比，避免互相污染
c1 = FanChart()
c2 = FanChart()
d0 = mk_series(6, seed=1)
c1.plot(d0)          # 基准
c2.plot(d0)          # 准备走增量

d1 = mk_series(8, seed=1)   # ★ 同一 seed → 前 6 个点相同，只是多了 2 个点
ok = c2.try_update_series(d1)
check(ok, "try_update_series 返回 True")

# 取两者所有线的 ydata 比对
def ydata_of(c):
    ax = c.fig.axes[0]
    return [list(ln.get_ydata()) for ln in ax.get_lines()
            if ln.get_linestyle() == "-" and len(ln.get_ydata()) > 0]

c1.plot(d1)          # 基准整幅重建
y1, y2 = ydata_of(c1), ydata_of(c2)
check(len(y1) == len(y2), "曲线条数一致", "%d vs %d" % (len(y1), len(y2)))
same = all(len(a) == len(b) and all(abs(x - y) < 1e-9 for x, y in zip(a, b))
           for a, b in zip(y1, y2))
check(same, "逐点数据一致")

print()
print("=" * 72)
print("B. 回退：指纹不符必须拒绝增量")
print("=" * 72)

c3 = FanChart()
c3.plot(mk_series(6, seed=2))
# B1 曲线数量变了
d_more = mk_series(6, seed=2) + [{"name": "新板块", "series": [1, 2, 3],
                                  "net": 1, "direction": "in", "times": TIMES[:3]}]
check(c3.try_update_series(d_more) is False, "曲线数变化 → 返回 False")
# B2 名称变了
d_ren = mk_series(6, seed=2)
d_ren[0]["name"] = "改名了"
check(c3.try_update_series(d_ren) is False, "名称变化 → 返回 False")
# B3 方向变了（in/out 对调 → 名称序列错位）
d_flip = mk_series(6, seed=2)
for r in d_flip:
    r["direction"] = "out" if r["direction"] == "in" else "in"
check(c3.try_update_series(d_flip) is False, "方向全对调 → 返回 False")
# B4 未 plot 过就增量
c4 = FanChart()
check(c4.try_update_series(mk_series(6)) is False, "未 plot 过 → 返回 False")
# B5 空数据
c5 = FanChart()
c5.plot(mk_series(6, seed=3))
check(c5.try_update_series([]) is False, "空数据 → 返回 False")
# B6 单点数据（n<=1）
c6 = FanChart()
c6.plot(mk_series(6, seed=4))
d_one = mk_series(1, seed=4)
check(c6.try_update_series(d_one) is False, "单点(n<=1) → 返回 False")

print()
print("=" * 72)
print("C. 提速：增量 vs 整幅重建")
print("=" * 72)

cA = FanChart()
base = mk_series(200, seed=7)
cA.plot(base)

# 整幅重建：每轮 +1 个点
ts_full = []
for k in range(10):
    d = mk_series(201 + k, seed=7)
    t0 = time.time()
    cA.plot(d)
    ts_full.append((time.time() - t0) * 1000)
med_full = statistics.median(ts_full)

# 增量：同样每轮 +1 个点
cB = FanChart()
cB.plot(mk_series(200, seed=7))
ts_inc = []
for k in range(10):
    d = mk_series(201 + k, seed=7)
    t0 = time.time()
    r = cB.try_update_series(d)
    ts_inc.append((time.time() - t0) * 1000)
    if not r:
        print("  ⚠ 第 %d 轮增量失败，回退了" % k)
med_inc = statistics.median(ts_inc)

print("  整幅重建 plot()            %8.2f ms" % med_full)
print("  增量 try_update_series()   %8.2f ms   %.1fx" % (med_inc, med_full / med_inc))
check(med_inc < med_full * 0.5, "增量至少 2× 快",
      "%.2fms -> %.2fms (%.1fx)" % (med_full, med_inc, med_full / med_inc))
check(med_inc < 5.0, "增量绝对耗时 < 5ms", "%.2fms" % med_inc)

print()
print("=" * 72)
print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 72)
sys.exit(1 if FAIL else 0)
