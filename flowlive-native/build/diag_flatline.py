# -*- coding: utf-8 -*-
"""复现「曲线尾部横线」——用真实面板数据走 FanChart 的 x 轴逻辑。"""
import json, os, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

D = r"C:\Users\user\Desktop\exe\最新版\data"
meta = json.load(open(os.path.join(D, "panel_20260923.json"), encoding="utf-8"))
mat = np.load(os.path.join(D, "panel_20260923.npy"))
times = meta["times"]
codes = meta["codes"]; names = meta["names"]; kinds = meta["kinds"]
T, N = mat.shape
print("T,N =", T, N, "| times", times[0], "~", times[-1])


# ---- 直接内联 _x_positions / _fill_gaps（与 app_main_v203.py 逐字一致）----
def _minute_index(hm):
    try:
        h, m = str(hm).split(":")[:2]
        t = int(h) * 60 + int(m)
    except Exception:
        return None
    if 9 * 60 + 31 <= t <= 11 * 60 + 30:
        return t - (9 * 60 + 31)
    if 13 * 60 + 1 <= t <= 15 * 60:
        return 120 + (t - (13 * 60 + 1))
    if t == 9 * 60 + 30:
        return 0
    return None


def _minute_index_clamped(hm):
    v = _minute_index(hm)
    if v is not None:
        return v
    try:
        h, m = str(hm).split(":")[:2]
        t = int(h) * 60 + int(m)
    except Exception:
        return None
    if t < 9 * 60 + 31:
        return 0
    if t < 13 * 60 + 1:
        return 11 * 60 + 30 - (9 * 60 + 31)
    return 239


TRADING_MINUTES = 240


def _x_positions(times, n):
    if n <= 0:
        return [], False
    if not times:
        return list(range(n)), False
    raw = [_minute_index_clamped(t) for t in times[:n]]
    if len(raw) < n:
        raw = raw + [None] * (n - len(raw))
    if not any(v is not None for v in raw):
        return list(range(n)), False
    k0 = next(i for i, v in enumerate(raw) if v is not None)
    base = raw[k0]
    for i in range(k0 - 1, -1, -1):
        base = max(0, base - 1)
        raw[i] = base
    out = []
    prev = None
    for v in raw:
        if v is None:
            v = (prev + 1) if prev is not None else 0
        elif prev is not None and v <= prev:
            v = prev + 1
        out.append(int(v))
        prev = v
    return out, True


def _fill_gaps(series):
    arr = np.asarray(series, dtype=float)
    if arr.size == 0:
        return []
    bad = ~np.isfinite(arr)
    if not bad.any():
        return [float(x) for x in arr]
    idx = np.arange(arr.size, dtype=float)
    good = np.isfinite(arr)
    if good.any():
        arr = np.interp(idx, idx[good], arr[good])
    else:
        arr = np.zeros(arr.size, dtype=float)
    return [float(x) for x in arr]


# ---- 模拟 _market_stats + _fan_pick 选线（概念层）----
FAN_TOP_N = 20


def _fnum(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d


def _fan_pick(ranked, total, dynamic=None):
    k = max(2, int(total) // 2)
    dyn = max(1, int(dynamic)) if dynamic is not None else max(2, k * 3 // 10)
    dyn = min(dyn, k - 1)
    base = list(ranked[:k - dyn])
    taken = {r.get("code") for r in base}
    pool = [r for r in ranked if r.get("code") not in taken]
    pool.sort(key=lambda r: -abs(_fnum(r.get("d5"), 0.0)))
    return base + pool[:dyn]


concept_mask = np.array([k == "concept" for k in kinds], dtype=bool)
i = T - 1
vals = mat[i]
idxs = np.where(concept_mask)[0]
if len(idxs) == 0:
    idxs = np.arange(N)

# dV5
dV5 = np.zeros(N)
if T > 5:
    dV5 = mat[i] - mat[i - 5]

rows = []
for j in idxs:
    rows.append({"code": codes[j], "name": names.get(codes[j], codes[j]),
                 "net": float(vals[j]), "d5": float(dV5[j]),
                 "series": [float(x) for x in mat[:, j]]})

ins = sorted([r for r in rows if r["net"] > 0], key=lambda r: -r["net"])
outs = sorted([r for r in rows if r["net"] < 0], key=lambda r: r["net"])
in_list = _fan_pick(ins, FAN_TOP_N)
out_list = _fan_pick(outs, FAN_TOP_N)

series = []
half = FAN_TOP_N // 2
for direction, rl in (("in", in_list), ("out", out_list)):
    for r in rl[:half]:
        series.append({"name": r["name"], "series": r["series"],
                       "net": r["net"], "direction": direction, "times": times})

print("series count =", len(series))
lens = {len(d["series"]) for d in series}
print("series lengths set =", lens)

n = max((len(d["series"]) for d in series), default=0)
xpos, mapped = _x_positions(times, n)
print("n =", n, "| mapped =", mapped)
print("len(xpos) =", len(xpos))
print("xpos[-5:] =", xpos[-5:])
print("xpos[:5] =", xpos[:5])

# ---- 画图 ----
fig = plt.figure(figsize=(9, 4.2), dpi=110)
ax = fig.add_subplot(111)
for d in series:
    s = _fill_gaps(d["series"])
    xs = xpos[:len(s)] if len(xpos) >= len(s) else range(len(s))
    ax.plot(list(xs), s, linewidth=1.4, alpha=.9, label=d["name"])
ax.axhline(0, color="#888", lw=.9, ls="--", alpha=.6)
ax.set_xlim(0, TRADING_MINUTES - 1)
ax.set_xticks([0, 30, 60, 90, 120, 150, 180, 210, 239])
ax.set_xticklabels(["09:30", "10:00", "10:30", "11:00", "11:30", "13:30",
                    "14:00", "14:30", "15:00"], fontsize=8)
ax.grid(True, axis="x", alpha=.5)
ax.legend(fontsize=6, ncol=2)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_repro_real_fan.png")
fig.savefig(out)
print("saved:", out)

# ---- 逐条报告的终点 x ----
for d in series:
    s = _fill_gaps(d["series"])
    xs = xpos[:len(s)] if len(xpos) >= len(s) else list(range(len(s)))
    print(f"  {d['direction']:>3} {d['name'][:14]:<16} len={len(s):>4} "
          f"x_end={list(xs)[-1]:>4} y_end={s[-1]:>10.3f}")
