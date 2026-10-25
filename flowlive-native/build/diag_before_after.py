# -*- coding: utf-8 -*-
"""生成「修复前 vs 修复后」对照图（真实数据）。"""
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

D = r"C:\Users\user\Desktop\exe\最新版\data"
meta = json.load(open(os.path.join(D, "panel_20260923.json"), encoding="utf-8"))
mat240 = np.load(os.path.join(D, "panel_20260922.npy"))
times50 = meta["times"]
codes = meta["codes"]
names = meta["names"]


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


def _mic(hm):
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


def fill_gaps(series):
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
    return [float(x) for x in arr]


def xpos_old(times, n):
    """修复前：times 短则续假序号。"""
    if n <= 0:
        return []
    if not times:
        return list(range(n))
    raw = [_mic(t) for t in times[:n]]
    if len(raw) < n:
        raw = raw + [None] * (n - len(raw))
    if not any(v is not None for v in raw):
        return list(range(n))
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
    return out


def xpos_new(times, n):
    """修复后：以 times 为准，不续假序号。"""
    if n <= 0:
        return []
    if not times:
        return list(range(n))
    nt = min(len(times), n)
    raw = [_mic(t) for t in times[:nt]]
    if not any(v is not None for v in raw):
        return list(range(nt))
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
    return out


series = []
for j in range(10):
    series.append({"name": (names.get(codes[j]) or codes[j]),
                   "s": [float(x) for x in mat240[:, j]]})

fig, axes = plt.subplots(2, 1, figsize=(10, 7), dpi=110)
for ax, fn, title in ((axes[0], xpos_old, "修复前（v3.0.4 及更早）"),
                      (axes[1], xpos_new, "修复后（v3.0.5）")):
    for d in series:
        s = fill_gaps(d["s"])
        xp = fn(times50, len(s))
        k = min(len(s), len(xp))
        ax.plot(xp[:k], s[:k], lw=1.4, alpha=.9)
    ax.axhline(0, color="#888", lw=.9, ls="--", alpha=.6)
    ax.set_xlim(0, 239)
    ax.set_xticks([0, 30, 60, 90, 120, 150, 180, 210, 239])
    ax.set_xticklabels(["09:30", "10:00", "10:30", "11:00", "11:30",
                        "13:30", "14:00", "14:30", "15:00"], fontsize=8)
    ax.grid(True, axis="x", alpha=.5)
    ax.axvline(49, color="red", lw=1.1, ls=":", alpha=.85)
    ax.set_title(title, fontsize=10)
    ax.text(52, ax.get_ylim()[0], " 真实数据只到 10:20（红线）",
            fontsize=7.5, color="red", va="bottom")

fig.tight_layout()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "_v305_before_after.png")
fig.savefig(out)
print("saved:", out)

# 数据结论
print("\n修复前 xpos[-3:] =", xpos_old(times50, 240)[-3:])
print("修复后 xpos[-3:] =", xpos_new(times50, 240)[-3:],
      "（长度 %d）" % len(xpos_new(times50, 240)))
