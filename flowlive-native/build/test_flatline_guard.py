# -*- coding: utf-8 -*-
"""回归测试：曲线**不得**因时间标签比数据短而延伸出"假横线"。

背景（用户报告 2026-09-23）：
  「为什么有一段数据，没采集呢，这一段直接是横线了」
  截图里资金曲线画到约 10:20 就变成一条平直横线一直拖到 15:00。

根因（两层，都要钉死）：
  ① 面板由 panel_{tag}.json（times）与 panel_{tag}.npy（矩阵）两个文件表达，
     读写都非原子 → 并发刷新时可读到「新矩阵 240 行 + 旧标签 50 个」。
  ② `_x_positions(times, n)` 在 n > len(times) 时把尾部空位按「上一个 +1」
     **续出假序号**，一路到 239(=15:00)；`_fill_gaps` 端点外推再把它变成直线。

测试项：
  A. `_x_positions` 契约：xpos 长度 = min(len(times), n)，且末值不越界
  B. 极端错配 times=50 / n=240 → 末位必须是 49（10:20），绝不能到 239
  C. FanChart 真实渲染：series 240 点 + times 50 个 → 每条线 x 数据 ≤ 49
  D. 一致性时行为不变（times=n）→ 末位 = n-1
  E. load_panel 对齐守卫：mat 行数 > times 时截断到 times
  F. 全 unknown 时间标签 → 退回下标（旧行为不破坏）
"""
import json
import os
import sys
import tempfile

import numpy as np

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
    print("载入模块:", type(e).__name__, e)

try:
    appmod.ensure_mpl()
except Exception as e:
    print("ensure_mpl:", e)

from PySide6.QtWidgets import QApplication                              # noqa: E402
app = QApplication.instance() or QApplication(sys.argv)

_x_positions = getattr(appmod, "_x_positions")
_fill_gaps = getattr(appmod, "_fill_gaps")
TRADING_MINUTES = getattr(appmod, "TRADING_MINUTES")
FanChart = getattr(appmod, "FanChart", None)

TIMES50 = ["%02d:%02d" % (9 + (31 + i) // 60, (31 + i) % 60) for i in range(50)]
# 09:31..10:20 —— 与用户现场一致

print("=" * 72)
print("[A] _x_positions 契约：长度 = min(len(times), n)")
print("=" * 72)
xp, mapped = _x_positions(TIMES50, 240)
check(len(xp) == 50, "times=50 / n=240 → len(xpos)=50", "得到 %d" % len(xp))
check(mapped is True, "仍按分钟序号定位（mapped=True）")
check(all(xp[i] < xp[i + 1] for i in range(len(xp) - 1)),
      "严格单调递增")
check(max(xp) <= TRADING_MINUTES - 1,
      "max(xpos) ≤ 239", "得到 %d" % max(xp))

print()
print("=" * 72)
print("[B] ★ 核心：极端错配不得出现 15:00 的假横线")
print("=" * 72)
check(xp[-1] == 49, "末位 = 49（10:20），不是 239（15:00）", "得到 %d" % xp[-1])
check(xp[-1] != TRADING_MINUTES - 1,
      "末位 ≠ 239（旧实现在此续到横轴最右端）")
check(len(xp) == len(TIMES50), "未凭空多出坐标")

print()
print("=" * 72)
print("[C] FanChart 真实渲染：240 点数据 + 50 个标签")
print("=" * 72)
if FanChart is None:
    check(False, "取到 FanChart 类")
else:
    import matplotlib
    matplotlib.use("Agg")
    rng = np.random.RandomState(7)
    data = []
    for i, nm in enumerate(["甲板块", "乙板块", "丙板块", "丁板块", "戊板块", "己板块"]):
        direction = "in" if i < 3 else "out"
        # series 故意给 240 点（比 times 长 190）
        sv = list(np.cumsum(rng.randn(240) * 0.2))
        data.append({"name": nm, "series": sv, "net": sv[-1],
                     "direction": direction, "times": TIMES50})
    ch = FanChart()
    ch.plot(data)
    ax = ch.fig.axes[0]
    from matplotlib.lines import Line2D
    bad = []
    for ln in ax.get_lines():
        if not isinstance(ln, Line2D):
            continue
        # 只检查曲线本身（跳过 0 基准线与引线：单点/单点式）
        xd = list(ln.get_xdata())
        if len(xd) > 5:
            if len(xd) != 50:
                bad.append(("len=%d" % len(xd), ln.get_label()))
            elif max(xd) > 49:
                bad.append(("max_x=%.0f" % max(xd), ln.get_label()))
    check(not bad, "所有曲线只画 50 个点且 x ≤ 49（10:20）",
          ("异常: %s" % bad[:3]) if bad else "")
    check(bool(ch._inc_ends), "曲线终点信息非空")
    if ch._inc_ends:
        mx = max(e["x"] for e in ch._inc_ends)
        check(mx <= 49, "所有终点 x ≤ 49", "max=%s" % mx)
    check(ch._xpos and len(ch._xpos) == 50,
          "_xpos 现场长度 = 50", "得到 %s" % (len(ch._xpos) if ch._xpos else 0))

print()
print("=" * 72)
print("[D] 一致时行为不变（times 与 n 等长）")
print("=" * 72)
xp2, m2 = _x_positions(TIMES50, 50)
check(len(xp2) == 50 and xp2[-1] == 49 and m2 is True,
      "times=50 / n=50 → 50 个坐标、末位 49")

print()
print("=" * 72)
print("[E] load_panel 对齐守卫（mat 行数 > times → 截断）")
print("=" * 72)
import flowdata                                                        # noqa: E402

_tmp = tempfile.mkdtemp()
_old = flowdata.DATA
try:
    flowdata.set_data_dir(_tmp)
    _t = TIMES50
    _m = rng.rand(240, 4).cumsum(axis=0)
    with open(os.path.join(_tmp, "panel_t.json"), "w", encoding="utf-8") as f:
        json.dump({"times": _t, "codes": ["A", "B", "C", "D"],
                   "names": {}, "kinds": {}}, f, ensure_ascii=False)
    np.save(os.path.join(_tmp, "panel_t.npy"), _m)
    with open(os.path.join(_tmp, "index_t.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    _meta, _mat, _ = flowdata.load_panel("t")
    check(_mat.shape[0] == len(_meta["times"]),
          "load_panel 返回的 mat 行数 = times 数",
          "mat=%d times=%d" % (_mat.shape[0], len(_meta["times"])))
    check(_mat.shape[0] == 50, "截断到 50 行", "得到 %d" % _mat.shape[0])

    # 一致时不得改动
    with open(os.path.join(_tmp, "panel_u.json"), "w", encoding="utf-8") as f:
        json.dump({"times": _t, "codes": ["A", "B", "C", "D"],
                   "names": {}, "kinds": {}}, f, ensure_ascii=False)
    np.save(os.path.join(_tmp, "panel_u.npy"), _m[:50])
    with open(os.path.join(_tmp, "index_u.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    _m2, _mat2, _ = flowdata.load_panel("u")
    check(_mat2.shape[0] == 50, "一致时保持 50 行不变", "得到 %d" % _mat2.shape[0])
finally:
    flowdata.set_data_dir(_old)

print()
print("=" * 72)
print("[F] 不可解析的时间标签 → 退回下标（旧行为）")
print("=" * 72)
xp3, m3 = _x_positions([""] * 7, 7)
check(xp3 == list(range(7)) and m3 is False, 'times=[""]×7 → range(7), mapped=False')
xp4, m4 = _x_positions([], 5)
check(xp4 == list(range(5)) and m4 is False, "times=[] → range(5), mapped=False")
xp5, m5 = _x_positions(TIMES50, 0)
check(xp5 == [] and m5 is False, "n=0 → ([], False)")

print()
print("=" * 72)
print("结果：PASS %d / FAIL %d" % (PASS, FAIL))
print("=" * 72)
sys.exit(1 if FAIL else 0)
