# -*- coding: utf-8 -*-
"""用**修复后的真实面板**核对：曲线末端落在横轴上的哪个时刻。

回答用户的原始疑问："曲线到底画到几点了"。
"""
import os
import sys
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

import numpy as np                                            # noqa: E402
import native_app as NA                                       # noqa: E402

P = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 "FlowLiveNative", "data", "panel_20260921.json")
meta = json.load(open(P, encoding="utf-8"))
times = meta["times"]
n = len(times)


def hm_at(k):
    if 0 <= k <= 119:
        t = 9 * 60 + 31 + k
    elif k <= 239:
        t = 13 * 60 + 1 + (k - 120)
    else:
        return "?"
    return f"{t // 60:02d}:{t % 60:02d}"


rng = np.random.default_rng(5)
names = [x for x in (meta.get("names") or {}).values()][:20]
series = []
for i in range(20):
    up = i < 10
    v = np.abs(np.cumsum(rng.normal(0.4, 2.0, n))) + 2
    if not up:
        v = -v
    series.append({"name": names[i % len(names)] if names else f"板块{i}",
                   "series": [float(x) for x in v], "net": float(v[-1]),
                   "direction": ("in" if up else "out"), "times": times})

fc = NA.FanChart(3.67, 10.02)
fc.plot(series)
ax = fc.fig.axes[0]
lines = [ln for ln in ax.get_lines() if len(ln.get_xydata()) == n]
end = float(list(lines[0].get_xdata())[-1])

# 缺分钟的检查：面板是否真密集
idx = [NA._minute_index(t) for t in times]
dense = (idx == list(range(n)))

print("=" * 66)
print("修复后：曲线末端落位核对（真实面板）")
print("=" * 66)
print(f"  面板文件      : panel_20260921.json")
print(f"  时间轴点数    : {n}")
print(f"  首/末分钟     : {times[0]} ~ {times[-1]}")
print(f"  时间轴是否连续: {'是（无缺口）' if dense else '否'}")
print(f"  最新分钟序号  : {NA._minute_index(times[-1])}")
print(f"  曲线末端 x    : {end:.0f}")
print(f"  末端读数      : {hm_at(int(end))}")
print(f"  与最新数据一致: {'✔ 一致' if int(end) == NA._minute_index(times[-1]) else '✘ 不一致'}")
print()
print(f"  对照：旧口径（x=下标）末端会是第 {n - 1} 格 = {hm_at(n - 1)}")
print(f"        落后 {(NA._minute_index(times[-1]) - (n - 1))} 分钟"
      if dense else "")
print()

# 横轴刻度落位
print("  横轴刻度落位（分钟序号）:")
for lb in NA.TRADING_TICKS:
    print(f"    {lb} → {NA._minute_index_clamped(lb)}")
print("=" * 66)
