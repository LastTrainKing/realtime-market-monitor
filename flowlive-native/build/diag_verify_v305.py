# -*- coding: utf-8 -*-
"""用**真实面板数据**跑 FanChart，验收「曲线不再画到 15:00」。

同时验证最危险的错配组合（times=50 + 矩阵 240 行）在真实渲染路径下被拦住。
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import importlib.util                                                   # noqa: E402

spec = importlib.util.spec_from_file_location(
    "appmod", os.path.join(HERE, "app_main_v203.py"))
appmod = importlib.util.module_from_spec(spec)
sys.modules["appmod"] = appmod
try:
    spec.loader.exec_module(appmod)
except SystemExit:
    pass
except Exception as e:
    print("载入:", type(e).__name__, e)
appmod.ensure_mpl()

from PySide6.QtWidgets import QApplication                              # noqa: E402
app = QApplication.instance() or QApplication(sys.argv)

import matplotlib                                                       # noqa: E402
matplotlib.use("Agg")
from matplotlib.lines import Line2D                                     # noqa: E402

FanChart = appmod.FanChart
D = r"C:\Users\user\Desktop\exe\最新版\data"
meta = json.load(open(os.path.join(D, "panel_20260923.json"), encoding="utf-8"))
mat50 = np.load(os.path.join(D, "panel_20260923.npy"))
mat240 = np.load(os.path.join(D, "panel_20260922.npy"))
times50 = meta["times"]
codes = meta["codes"]
names = meta["names"]

print("真实数据：today mat=%s times=%d | 昨日 mat=%s"
      % (mat50.shape, len(times50), mat240.shape))

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


def build_series(matx, T_used):
    """取前 12 列做 6 进 6 出，长度 = matx 行数（可故意超长）。"""
    out = []
    for j in range(12):
        col = [float(x) for x in matx[:, j][:T_used]] if T_used else \
              [float(x) for x in matx[:, j]]
        out.append({"name": (names.get(codes[j]) or codes[j]),
                    "series": col,
                    "net": col[-1] if col else 0.0,
                    "direction": "in" if j < 6 else "out",
                    "times": times50})
    return out


def curve_extent(ch):
    """返回所有曲线里最大的 x 与点数集合。"""
    ax = ch.fig.axes[0]
    mx = -1
    lens = set()
    for ln in ax.get_lines():
        if not isinstance(ln, Line2D):
            continue
        xd = list(ln.get_xdata())
        if len(xd) > 5:
            mx = max(mx, max(xd))
            lens.add(len(xd))
    return mx, lens


print()
print("=" * 72)
print("[1] 真实今日数据（times=50, 矩阵 50 行）→ 曲线止于 10:20")
print("=" * 72)
ch = FanChart()
ch.plot(build_series(mat50, None))
mx, lens = curve_extent(ch)
check(mx <= 49, "所有曲线 max(x) ≤ 49 即 10:20", "max=%s" % mx)
check(lens == {50}, "每条曲线 50 个点", "点数集=%s" % lens)
check(len(ch._xpos) == 50, "_xpos 长度 50", "得到 %d" % len(ch._xpos))
if ch._inc_ends:
    check(max(e["x"] for e in ch._inc_ends) <= 49,
          "右端标签锚点 x ≤ 49",
          "max=%s" % max(e["x"] for e in ch._inc_ends))

print()
print("=" * 72)
print("[2] ★ 危险错配：times=50 + 矩阵 240 行（旧版会画到 15:00）")
print("=" * 72)
ch2 = FanChart()
data2 = build_series(mat240, 240)          # 每个 series 都是 240 点
lens2 = {len(d["series"]) for d in data2}
check(lens2 == {240}, "构造成功：series 均为 240 点", "%s" % lens2)
ch2.plot(data2)
mx2, lens2r = curve_extent(ch2)
check(mx2 <= 49, "★ 曲线 max(x) ≤ 49（不再延伸到 239=15:00）", "max=%s" % mx2)
check(lens2r == {50}, "★ 每条曲线被截到 50 个点", "点数集=%s" % lens2r)
check(len(ch2._xpos) == 50, "_xpos 长度 = 50（以 times 为准）",
      "得到 %d" % len(ch2._xpos))

print()
print("=" * 72)
print("[3] 增量更新路径（try_update_series）同样受保护")
print("=" * 72)
# 先正常画 50 点建立增量现场，再用 240 点触发增量
ch3 = FanChart()
ch3.plot(build_series(mat50, None))
ok = ch3.try_update_series(build_series(mat240, 240))
mx3, lens3 = curve_extent(ch3)
check(mx3 <= 49, "增量后 max(x) ≤ 49", "max=%s" % mx3)
check(lens3 == {50}, "增量后仍 50 点一条", "点数集=%s" % lens3)

print()
print("=" * 72)
print("[4] 时间标签一致时不受影响（回归）")
print("=" * 72)
ch4 = FanChart()
t240 = ["%02d:%02d" % (9 + (31 + i) // 60, (31 + i) % 60) for i in range(240)]
d4 = []
for j in range(12):
    col = [float(x) for x in mat240[:, j]]
    d4.append({"name": "N%d" % j, "series": col, "net": col[-1],
               "direction": "in" if j < 6 else "out", "times": t240})
ch4.plot(d4)
mx4, lens4 = curve_extent(ch4)
check(mx4 <= 239, "240 点满轴时 max(x) ≤ 239", "max=%s" % mx4)
check(lens4 == {240}, "每条曲线 240 点（未被误截）", "点数集=%s" % lens4)

print()
print("=" * 72)
print("结果：PASS %d / FAIL %d" % (PASS, FAIL))
print("=" * 72)
sys.exit(1 if FAIL else 0)
