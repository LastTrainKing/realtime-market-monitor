# -*- coding: utf-8 -*-
"""端到端出图：用**真实面板**渲染修复前/后的对比图（v1.2.4）。

不启动 GUI，直接离屏渲染两张 PNG：
    shots_tsync/before.png  —— 旧口径（x = 数组下标）
    shots_tsync/after.png   —— 新口径（x = 分钟序号）

对照点：曲线右端落在横轴上哪个时刻。
"""
import os
import sys
import json
import glob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

import numpy as np                                            # noqa: E402
import native_app as NA                                       # noqa: E402

OUT = os.path.join(HERE, "shots_tsync")
os.makedirs(OUT, exist_ok=True)

# ---- 取真实面板（优先今天那份稀疏的）--------------------------------------
CAND = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 "FlowLiveNative", "data", "panel_20260921.json"),
]
CAND += sorted(glob.glob(os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "FlowLiveNative", "data",
    "panel_*.json")))
CAND += sorted(glob.glob(os.path.join(HERE, "core", "data", "panel_*.json")))

meta = path = None
for p in CAND:
    if not os.path.exists(p):
        continue
    try:
        m = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    t = m.get("times") or []
    if len(t) >= 20:
        meta, path = m, p
        if len(t) < 60:      # 稀疏的那份（缺口大 → 对比明显）
            break

if not meta:
    print("找不到可用面板"); sys.exit(1)

times = meta["times"]
n = len(times)
print(f"面板：{os.path.basename(path)}")
print(f"时间轴：{n} 个点，{times[0]} ~ {times[-1]}")

# ---- 造 20 条曲线（用真实的板块名，数据用合成走势）-----------------------
rng = np.random.default_rng(11)
names = [x for x in (meta.get("names") or {}).values()][:24] or \
        [f"板块{i}" for i in range(24)]
series = []
for i in range(20):
    up = i < 10
    drift = rng.normal(0.6, 0.25)
    v = rng.normal(0, 12) + np.cumsum(rng.normal(drift, 2.2, n))
    if not up:
        v = -np.abs(v) - 3.0
    else:
        v = np.abs(v) + 3.0
    series.append({
        "name": names[i % len(names)],
        "series": [float(x) for x in v],
        "net": float(v[-1]),
        "direction": ("in" if up else "out"),
        "times": times,
    })

# ---- after：新口径 -------------------------------------------------------
fc = NA.FanChart(3.67, 10.02)
fc.plot(series)
ax = fc.fig.axes[0]
fc.fig.canvas.draw()
lines = [ln for ln in ax.get_lines() if len(ln.get_xydata()) == n]
after_end = float(list(lines[0].get_xdata())[-1])

# 把"数据末端"标一根醒目的线，便于肉眼核对
ax.axvline(after_end, color="#ff2d55", linewidth=1.6, alpha=.9, zorder=5)
ax.text(after_end - 1.5, ax.get_ylim()[1], f" 数据末端 {times[-1]} ",
        fontsize=8, color="#ff2d55", va="top", ha="right",
        bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#ff2d55",
                  alpha=.85))
fc.draw()
p_after = os.path.join(OUT, "after_by_minute.png")
fc.fig.savefig(p_after, dpi=110, bbox_inches="tight", facecolor="white")
print(f"[after ] 曲线末端 x={after_end}  →  {p_after}")

# ---- before：旧口径（手工按下标画，还原用户看到的画面）------------------
fc2 = NA.FanChart(3.67, 10.02)
fc2.plot(series)
ax2 = fc2.fig.axes[0]
for ln in list(ax2.get_lines()):
    yd = ln.get_ydata()
    if len(yd) == n:
        ln.set_xdata(list(range(n)))          # ← 旧写法：x = 数组下标
old_end = float(n - 1)
ax2.axvline(old_end, color="#8e8e93", linewidth=1.6, alpha=.9, zorder=5)
ax2.text(old_end - 1.5, ax2.get_ylim()[1], f" 旧口径画到这里 ",
         fontsize=8, color="#8e8e93", va="top", ha="right",
         bbox=dict(boxstyle="round,pad=0.22", fc="white", ec="#8e8e93",
                   alpha=.85))
fc2.draw()
p_before = os.path.join(OUT, "before_by_index.png")
fc2.fig.savefig(p_before, dpi=110, bbox_inches="tight", facecolor="white")
print(f"[before] 曲线末端 x={old_end}  →  {p_before}")

# ---- 数值对照 ------------------------------------------------------------
def hm_at(k):
    if 0 <= k <= 119:
        t = 9 * 60 + 31 + k
    elif k <= 239:
        t = 13 * 60 + 1 + (k - 120)
    else:
        return "?"
    return f"{t // 60:02d}:{t % 60:02d}"

print("\n" + "=" * 62)
_li = next((NA._minute_index(x) for x in reversed(times)
             if NA._minute_index(x) is not None), None)
print(f"  面板真实最后一分钟 : {times[-1]}" + (f"（第 {_li} 格）" if _li is not None else ""))
print(f"  修复后曲线末端     : 第 {after_end:.0f} 格 = {hm_at(int(after_end))}  ✔")
print(f"  修复前曲线末端     : 第 {old_end:.0f} 格 = {hm_at(int(old_end))}  ✘")
_lx = next((NA._minute_index(x) for x in reversed(times)
            if NA._minute_index(x) is not None), None)
if _lx is not None:
    print(f"  旧口径少画         : {_lx - old_end:.0f} 格"
          f"（横轴上差 {_lx - old_end:.0f} 分钟）")
print("=" * 62)
