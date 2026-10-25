# -*- coding: utf-8 -*-
"""最终复现：times=50（今日盘中） + series=240（全量面板）→ 尾部横线到 15:00。"""
import json, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

D = r"C:\Users\user\Desktop\exe\最新版\data"
# 今日盘中：times 只有 50 个
meta50 = json.load(open(os.path.join(D, "panel_20260923.json"), encoding="utf-8"))
times50 = meta50["times"]
# 全量面板：240 行（昨日收盘态，列名一致）
mat240 = np.load(os.path.join(D, "panel_20260922.npy"))
print("times 长度 =", len(times50), " series 长度 =", mat240.shape[0])


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
        return 119
    return 239


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
    out, prev = [], None
    for v in raw:
        if v is None:
            v = (prev + 1) if prev is not None else 0
        elif prev is not None and v <= prev:
            v = prev + 1
        out.append(int(v))
        prev = v
    return out, True


n = mat240.shape[0]
xpos, mapped = _x_positions(times50, n)
print("n =", n, "len(xpos) =", len(xpos))
print("xpos[45:55] =", xpos[45:55], " ← 第 50 个之后是凭空续出来的")
print("xpos[-3:] =", xpos[-3:], " ← 到 239 = 15:00")

fig = plt.figure(figsize=(9.5, 4.4), dpi=110)
ax = fig.add_subplot(111)
for j in (0, 1, 2, 3, 4):
    s = [float(x) for x in mat240[:, j]]
    ax.plot(xpos[:len(s)], s, lw=1.5, alpha=.9)
ax.axhline(0, color="#888", lw=.9, ls="--", alpha=.6)
ax.set_xlim(0, 239)
ax.set_xticks([0, 30, 60, 90, 120, 150, 180, 210, 239])
ax.set_xticklabels(["09:30", "10:00", "10:30", "11:00", "11:30", "13:30",
                    "14:00", "14:30", "15:00"], fontsize=8)
ax.grid(True, axis="x", alpha=.5)
ax.axvline(49, color="red", lw=1.0, ls=":", alpha=.8)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_repro_mismatch.png")
fig.savefig(out)
print("saved:", out)
