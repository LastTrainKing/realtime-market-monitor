# -*- coding: utf-8 -*-
"""验证「曲线只画到十点多」的时间同步修复（v1.2.4）。

用法（离屏，不弹窗）：
    set QT_QPA_PLATFORM=offscreen
    <native_env py> verify_time_sync.py

用**真实面板数据**（panel_20260921.json，41 点 / 覆盖 09:31~11:17）证明：
  ① 修复前：x = 数组下标 → 最后一点落位读作 10:11（用户看到的现象）
  ② 修复后：x = 分钟序号 → 最后一点落位就是 11:17
  ③ 曲线右端与横轴刻度对齐（用 matplotlib 反查像素位置交叉验证）
  ④ 悬停换算：鼠标停在 11:17 那一列 → 回调拿到的是**最后一行**的数据
"""
import os
import sys
import json
import glob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CORE = os.path.join(HERE, "core")
if CORE not in sys.path:
    sys.path.insert(0, CORE)

import numpy as np                                            # noqa: E402

# ---- 定位一份"有缺口"的真实面板：优先用今天，其次用任意一份 ------------------
DATA_DIRS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "FlowLiveNative", "data"),
    os.path.join(HERE, "core", "data"),
]


def _minute_idx(hm):
    h, m = str(hm).split(":")[:2]
    t = int(h) * 60 + int(m)
    if 9 * 60 + 31 <= t <= 11 * 60 + 30:
        return t - (9 * 60 + 31)
    if 13 * 60 + 1 <= t <= 15 * 60:
        return 120 + (t - (13 * 60 + 1))
    if t == 9 * 60 + 30:
        return 0
    return None


def _hm_at_pos(k):
    """分钟序号 → 'HH:MM'（_minute_index 的逆运算，用于把落位读成人看的时刻）。"""
    if 0 <= k <= 119:
        t = 9 * 60 + 31 + k
    elif k <= 239:
        t = 13 * 60 + 1 + (k - 120)
    else:
        return "?"
    return f"{t // 60:02d}:{t % 60:02d}"


def load_gappy_panel():
    """挑一份时间轴**有缺口**的真实面板（缺口越大越能体现问题）。"""
    best = None
    for d in DATA_DIRS:
        for p in sorted(glob.glob(os.path.join(d, "panel_*.json"))):
            try:
                meta = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            t = meta.get("times") or []
            if len(t) < 5:
                continue
            idx = [_minute_idx(x) for x in t]
            if any(v is None for v in idx):
                continue
            span = idx[-1] - idx[0] + 1
            gaps = span - len(t)
            if gaps <= 0:
                continue
            if best is None or gaps > best[0]:
                best = (gaps, meta, p)
    return best


RES = []


def check(name, ok, detail=""):
    RES.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  · {detail}" if detail else ""), flush=True)


print("=" * 74)
print("时间同步修复验证（v1.2.4）")
print("=" * 74)

import native_app as NA                                       # noqa: E402

# ---------------------------------------------------------------- A. 单元级
print("\n--- A. _x_positions 单元行为 ---")

# A1 有缺口：按分钟序号落点
t_gap = ["09:31", "09:32", "10:20", "10:21", "11:17"]
xp, mapped = NA._x_positions(t_gap, 5)
check("A1 有缺口时按分钟序号定位（10:20→49, 11:17→106）",
      mapped and xp == [0, 1, 49, 50, 106], f"{xp}")

# A2 无缺口：与下标一致（保证密集面板行为不变）
t_dense = [f"{9 + (31 + k) // 60:02d}:{(31 + k) % 60:02d}" for k in range(30)]
xp2, mapped2 = NA._x_positions(t_dense, 30)
check("A2 无缺口时与下标等价（不改变既有行为）",
      mapped2 and xp2 == list(range(30)), f"n={len(xp2)}")

# A3 认不出时间标签 → 退回下标（测试用的 [""]×T 走这条）
xp3, mapped3 = NA._x_positions([""] * 7, 7)
check("A3 时间标签不可解析时退回下标（保持兼容）",
      (not mapped3) and xp3 == list(range(7)), f"mapped={mapped3}")

# A4 空 times → 退回下标
xp4, mapped4 = NA._x_positions([], 4)
check("A4 times 为空时退回下标", (not mapped4) and xp4 == [0, 1, 2, 3], f"{xp4}")

# A5 严格单调递增（缺分钟/重复标签都不许出现折返）
t_dup = ["09:31", "09:31", "10:00", "09:45", "11:17"]
xp5, _ = NA._x_positions(t_dup, 5)
check("A5 重复/乱序标签下仍严格递增（不画成折返线）",
      all(xp5[i] < xp5[i + 1] for i in range(len(xp5) - 1)), f"{xp5}")

# A6 午休跨段：11:30→13:01 只差 1 格
xp6, _ = NA._x_positions(["11:29", "11:30", "13:01", "13:02"], 4)
check("A6 午休跨段连续（11:30=119 → 13:01=120）",
      xp6 == [118, 119, 120, 121], f"{xp6}")

# A7 times 比曲线短 → 尾部续格，不塌缩到同一点
xp7, _ = NA._x_positions(["09:31", "09:32"], 4)
check("A7 times 短于曲线时尾部续格", xp7 == [0, 1, 2, 3], f"{xp7}")

# ---------------------------------------------------------------- B. 真实面板
print("\n--- B. 真实面板端到端（修复前后对比）---")
picked = load_gappy_panel()
check("B0 找到一份带缺口的真实面板", picked is not None,
      f"gaps={picked[0]} file={os.path.basename(picked[2])}" if picked else "none")

if picked:
    gaps, meta, path = picked
    times = meta["times"]
    n = len(times)
    real_last = times[-1]
    # ★ 用**夹取版**：末点可能是旧版本写进去的午休脏刻度（如 11:43），
    #   语义上等于 11:30（119）—— 直接问 _minute_idx 会拿到 None。
    t_idx = [NA._minute_index_clamped(x) for x in times]
    real_pos = t_idx[-1]
    old_last = n - 1                     # 旧口径：最后一点落位 = 下标

    print(f"  面板：{os.path.basename(path)}")
    print(f"  时间轴：{n} 个点，{times[0]}~{real_last}，"
          f"真实跨度 {real_pos + 1} 分钟，缺 {gaps} 分钟")

    # 用真实的 series 造一张 FanChart（20 条，模拟实盘）
    rng = np.random.default_rng(7)
    series = []
    for i in range(20):
        v = rng.normal(0, 30) + rng.normal(0, 2, n).cumsum()
        series.append({
            "name": f"板块{i}", "series": [float(x) for x in v],
            "net": float(v[-1]),
            "direction": ("in" if i < 10 else "out"),
            "times": times,
        })
    fc = NA.FanChart(3.67, 10.02)
    fc.plot(series)
    ax = fc.fig.axes[0]

    # 取一条曲线的 xdata 末端
    lines = [ln for ln in ax.get_lines() if len(ln.get_xydata()) == n]
    check("B1 20 条曲线都画满 n 个点", len(lines) == 20, f"n_lines={len(lines)}")
    right_end = float(list(lines[0].get_xdata())[-1]) if lines else -1

    check(f"B2 曲线右端 = 最新数据分钟序号（{real_pos}，即 {real_last}）",
          abs(right_end - real_pos) < 0.01,
          f"末端 x={right_end}")

    # 旧口径下，最后一点落在下标 n-1 位置上 —— 把它**换算回时刻**做对照
    _old_hm = _hm_at_pos(old_last)
    check(f"B3 旧口径落到第 {old_last} 格、读作 {_old_hm}；"
          f"修后落在第 {real_pos} 格 = {real_last}",
          old_last < real_pos,
          f"少画 {real_pos - old_last} 格 = 落后 "
          f"{real_pos - old_last} 分钟")

    # 像素级：曲线末端与横轴刻度的相对位置
    # ★ 断言要**跟着数据走**，不能硬编码"落在 11:00~11:30 之间"——
    #   面板随时在长（首轮写死了 11:30 上界，数据长到 11:43 就假失败了）。
    ax.set_xlim(0, NA.TRADING_MINUTES - 1)
    fc.fig.canvas.draw()
    disp = ax.transData.transform
    px_data_end = float(disp((right_end, 0))[0])
    # 用"曲线末端所在的分钟序号"换算出它应该落在哪两个刻度之间
    lo_lab = max((lb for lb in NA.TRADING_TICKS
                  if (NA._minute_index_clamped(lb) or 0) <= right_end),
                 key=lambda lb: NA._minute_index_clamped(lb))
    px_lo = float(disp((NA._minute_index_clamped(lo_lab), 0))[0])
    # 右邻刻度（末端已是最后一个刻度时用轴右端）
    _nxt = [lb for lb in NA.TRADING_TICKS
            if (NA._minute_index_clamped(lb) or 0) > right_end]
    if _nxt:
        px_hi = float(disp((NA._minute_index_clamped(_nxt[0]), 0))[0])
        hi_lab = _nxt[0]
    else:
        px_hi = float(disp((NA.TRADING_MINUTES - 1, 0))[0])
        hi_lab = "轴右端"
    check(f"B4 曲线末端像素位置落在 [{lo_lab}, {hi_lab}] 区间内（视觉可验证）",
          px_lo <= px_data_end <= px_hi,
          f"{lo_lab}@{px_lo:.1f}px  ≤  末端@{px_data_end:.1f}px  "
          f"≤  {hi_lab}@{px_hi:.1f}px")

    # ------------------------------------------------------------ C. 悬停
    print("\n--- C. 悬停换算（图与表必须指向同一条数据）---")
    hits = []
    fc.hover_cb = lambda i, hm: hits.append((i, hm))
    # 模拟鼠标停在"曲线末端那一列"
    fc._on_motion(type("E", (), {"inaxes": ax, "xdata": float(right_end)})())
    # ★ 期望哪一行？—— 由 _row_at_x 的定义直接推导：末端位置上最近的行。
    #   不能想当然写 n-1：脏刻度会被夹到 119（11:30），而它前面可能还有
    #   一个合法点 11:17（106），此时 119 最近的行未必是最后一行。
    _want_row = fc._row_at_x(float(right_end))
    check("C1 鼠标停在最新一列 → 回调拿到该列对应的行",
          len(hits) == 1 and hits[0][0] == _want_row,
          f"hits={hits}, 期望行={_want_row}")
    check(f"C2 回调拿到的时长 = 该行的真实标签",
          len(hits) == 1 and hits[0][1] == times[_want_row],
          f"hm={hits[0][1] if hits else '-'} · 该行={times[_want_row]}")

    # 再验一个中间列：挑一个"不是从 0 连续"的点，验证换算不是恒等
    _mid = next((k for k in range(1, n)
                 if t_idx[k] != k), None)     # 序号 ≠ 下标 的第一个点
    if _mid is not None:
        hits.clear()
        fc._hover_px = None
        fc._on_motion(type("E", (), {
            "inaxes": ax,
            "xdata": float(t_idx[_mid])})())
        check(f"C3 鼠标停在 {times[_mid]}（序号 {t_idx[_mid]}）→ "
              f"换算回第 {_mid} 行（不是下标 {t_idx[_mid]}）",
              len(hits) == 1 and hits[0][0] == _mid,
              f"row={hits[0][0] if hits else '-'}（下标口径会错给 {t_idx[_mid]}）")

    # ------------------------------------------------------------ D. 竖线
    print("\n--- D. 准星竖线落位 ---")
    # ★ 明确把准星移到"最后一行"，再去量竖线 ——
    #   不能直接量上一步遗留的竖线：C3 把鼠标停在了 10:20，
    #   那条竖线**理应**停在 49，拿它跟 106 比是测量对象错了（首轮踩过）。
    hits.clear()
    fc._hover_px = None
    fc._on_motion(type("E", (), {
        "inaxes": ax, "xdata": float(right_end)})())
    vx = float(list(fc._vline.get_xdata())[0]) if fc._vline is not None else -1.0
    check("D1 准星竖线与鼠标所在格一致（同一套分钟序号坐标）",
          abs(vx - right_end) < 0.01, f"vline x={vx}, 鼠标格 x={right_end}")
    check("D2 竖线不落在下标口径的位置（旧实现会停在 40）",
          abs(vx - old_last) > 0.5, f"vline x={vx} ≠ {old_last}")

# ---------------------------------------------------------------- E. 缺口口径
print("\n--- E. 缺口判定（决定走增量还是全量）---")
from datetime import datetime as _dt                          # noqa: E402
import flowdata as FD                                         # noqa: E402

_t_lunch = ["09:31", "11:30", "13:01"]
g = FD.spot_gap_minutes(_t_lunch, now=_dt(2026, 9, 21, 12, 0))
check("E1 午休期间不算落后（11:30 面板 @12:00 → 0 分钟）", g == 0, f"gap={g}")

g2 = FD.spot_gap_minutes(["11:30"], now=_dt(2026, 9, 21, 15, 30))
check("E2 盘后按 15:00 计（11:30 面板 @15:30 → 120 分钟）", g2 == 120,
      f"gap={g2}")

g3 = FD.spot_gap_minutes(["10:47"], now=_dt(2026, 9, 21, 11, 17))
check("E3 复现用户场景：10:47 面板 @11:17 → 30 分钟（> 阈值 6 → 全量补）",
      g3 == 30 and g3 > FD.SPOT_MAX_GAP_MIN, f"gap={g3}")

g4 = FD.spot_gap_minutes(["11:15"], now=_dt(2026, 9, 21, 11, 17))
check("E4 正常高频刷新（差 2 分钟）不触发全量",
      g4 == 2 and g4 <= FD.SPOT_MAX_GAP_MIN, f"gap={g4}")

# ★ 5 分钟档位：自动刷新每 5 分钟一跳，正常运行时缺口恰好 5 ——
#   阈值必须 ≥ 5，否则该档位每次刷新都会退化成全量（1000 次请求）。
g5 = FD.spot_gap_minutes(["10:55"], now=_dt(2026, 9, 21, 11, 0))
check("E5 5 分钟档位（差 5 分钟）仍走增量，不误判成断线",
      g5 == 5 and g5 <= FD.SPOT_MAX_GAP_MIN, f"gap={g5}")
g5b = FD.spot_gap_minutes(["10:56"], now=_dt(2026, 9, 21, 11, 2))
check("E5b 阈值边界（差 6 分钟）仍走增量（阈值取闭区间）",
      g5b == 6 and g5b <= FD.SPOT_MAX_GAP_MIN, f"gap={g5b}")
g5c = FD.spot_gap_minutes(["10:55"], now=_dt(2026, 9, 21, 11, 2))
check("E5c 超过阈值（差 7 分钟）→ 交回全量补齐",
      g5c == 7 and g5c > FD.SPOT_MAX_GAP_MIN, f"gap={g5c}")

g6 = FD.spot_gap_minutes(["11:20"], now=_dt(2026, 9, 21, 9, 0))
check("E6 盘前不判落后（返回 None）", g6 is None, f"gap={g6}")

g7 = FD.spot_gap_minutes([], now=_dt(2026, 9, 21, 11, 17))
check("E7 空时间轴返回 None", g7 is None, f"gap={g7}")

# ---------------------------------------------------------------- F. 数据侧
print("\n--- F. 增量刷新缺口守卫已接线 ---")
_src_fd = open(os.path.join(CORE, "flowdata.py"), encoding="utf-8").read()
_src_na = open(os.path.join(HERE, "native_app.py"), encoding="utf-8").read()
check("F1 refresh_panel_spot 检查缺口并交回全量",
      "gap = spot_gap_minutes(times)" in _src_fd
      and "return None, None" in _src_fd.split("gap = spot_gap_minutes(times)")[1][:400],
      "断线太久 → 走 build_panel 补齐空档")
check("F2 Worker 启动时也按缺口决定通道",
      "gap = self._panel_gap_min(data_dir)" in _src_na
      and "full refetch to fill the hole" in _src_na, "")
check("F3 曲线按分钟序号定位（不再 range(len(s)) 当 x）",
      "xs = xpos[:len(s)]" in _src_na, "")
check("F4 悬停换算回数据行号（不再直接 round(xdata)）",
      "def _row_at_x" in _src_na and "k = self._row_at_x(ev.xdata)" in _src_na, "")
check("F5 竖线按分钟序号落位",
      "xk = self._xpos[k] if (self._xpos and 0 <= k < len(self._xpos)) else k"
      in _src_na, "")

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 74)
npass = sum(1 for _, ok, _ in RES if ok)
print(f"结果：{npass}/{len(RES)} PASS")
fails = [(nm, dt) for nm, ok, dt in RES if not ok]
if fails:
    print("失败项：")
    for nm, dt in fails:
        print(f"  × {nm}  {dt}")
    sys.exit(1)
print("全部通过 ✅")
