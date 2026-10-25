# -*- coding: utf-8 -*-
"""probe_lag_e2e.py —— 端到端「不跟手」量化：走完整 MainWindow + 事件循环

为什么还要再测一次
------------------
bench_hover.py 量的是各段**孤立**耗时，但用户的体感来自**队列**：
鼠标以约 8 ms 间隔产生事件，而每个事件若要求 120 ms 才能处理完，
队列必然持续增长 —— 表现为"光标越划越滞后、松手后才追上"。

本探针直接复现这个场景：
  ① 造真实 MainWindow（离屏），灌入真实规模数据，走 on_data 真渲染一次
  ② 注入 N 次连续鼠标移动，其间**不**处理事件循环（模拟"事件涌入"）
  ③ 一次性 processEvents()，统计从"注入开始"到"全部处理完"的墙钟时间
  ④ 关键指标：**平均每像素延迟** = 总耗时 / 注入次数
       · < 17 ms → 跟得上 60Hz 手感
       · > 50 ms → 明显不跟手
  ⑤ 另测「追踪误差」：全部处理完后，竖线停在的分钟号 vs 最后一次注入的分钟号
     —— 若两者不一致，说明事件被合并/丢弃，即"跟不上鼠标"

用法：
    python probe_lag_e2e.py            # 默认 200 次注入
    python probe_lag_e2e.py --n 400
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np                                      # noqa: E402

from PySide6.QtCore import QEvent, QPointF, Qt          # noqa: E402
from PySide6.QtGui import QFont, QMouseEvent            # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

import native_app as NA                                 # noqa: E402

WIN_W, WIN_H = 1480, 921
T = 240


def build_window(app):
    """真实窗口 + 真实规模数据，并触发一次真实渲染。"""
    win = NA.MainWindow()
    win.resize(WIN_W, WIN_H)
    win.show()
    app.processEvents()

    # ★ 关键：把 hover_cb 真正接通（含 score_table 与整表重建）。
    #   若只造图形而不接回调，就测不出"表格重建 → 布局变化 → 图表 resize
    #   → 背景快照失效 → 又退回 120ms 全量重绘"这条二阶路径。
    F = _make_features()
    win.data = {"F": F, "times": list(F.times), "source": "live",
                "date_tag": "PROBE", "n_sectors": F.V.shape[1]}
    win._hover_key = None
    win._hover_cache = {}

    names = _sector_names(F.V.shape[1])
    series = []
    for i in range(FAN_N):
        j = i if i < FAN_N // 2 else F.V.shape[1] - 1 - (i - FAN_N // 2)
        v = F.V[:, j]
        series.append({"name": names[j], "series": [float(x) for x in v],
                       "net": float(v[-1]),
                       "direction": "in" if i < FAN_N // 2 else "out",
                       "times": list(F.times)})
    win.ch_fan.plot(series)
    win.ch_fan.hover_cb = win.on_fan_hover
    app.processEvents()
    return win, series


def _sector_names(n):
    return [f"板块名称示例{i:03d}概念" for i in range(n)]


def _make_features():
    """真实本地面板 → 真 salience 特征（拿不到就合成同规模数据）。"""
    try:
        import flowdata as fd
        import salience as S
        ddir = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                            "FlowLiveNative", "data")
        fd.set_data_dir(ddir)
        cands = sorted([f for f in os.listdir(ddir)
                        if f.startswith("panel_") and f.endswith(".json")])
        tag = cands[-1][len("panel_"):-len(".json")]
        F = S.build_features(tag)
        print(f"  [真实面板] tag={tag} 板块={F.V.shape[1]} 分钟={F.V.shape[0]}")
        return F
    except Exception as e:                              # noqa: BLE001
        print(f"  [合成面板] 真实数据不可用（{type(e).__name__}）")
        rng = np.random.default_rng(3)
        return _SynthF(rng.normal(0, 30.0, (T, 427)))


class _SynthF:
    def __init__(self, V):
        self.V = V
        self.times = _times()
        self.codes = [f"BK{i:04d}" for i in range(V.shape[1])]
        self.meta = [{"name": f"板块{i}", "kind": "concept",
                      "net_yi": float(V[-1, i])} for i in range(V.shape[1])]


FAN_N = 20


def _times():
    out = []
    for k in range(T):
        m = 30 + k
        out.append(f"{9 + m // 60:02d}:{m % 60:02d}" if m < 120
                   else f"{13 + (m - 120) // 60:02d}:{(m - 120) % 60:02d}")
    return out


def inject(app, fc, n, y_frac=0.45, x0=60, x1=None, pump=True):
    """注入 n 次连续水平移动；返回 (墙钟耗时 ms, 处理完后的分钟号)。"""
    w = fc.width()
    h = fc.height()
    x1 = x1 if x1 is not None else w - 60
    y = int(h * y_frac)
    dt_px = (x1 - x0) / max(1, n - 1)
    frames = []
    for j in range(n):
        x = int(round(x0 + dt_px * j))
        margin = (x - x0) / max(1, (x1 - x0))      # 0..1 的进度
        u = x * (TRADING_MAX) / max(1, w)          # 像素 → 分钟（粗估）
        frames.append((x, y, margin, u))
    t0 = time.perf_counter()
    for (x, y, _m, _u) in frames:
        ev = QMouseEvent(QEvent.MouseMove, QPointF(x, y), QPointF(x, y),
                         Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        app.sendEvent(fc, ev)
        if pump:
            app.processEvents()
    dt = (time.perf_counter() - t0) * 1000.0
    return dt, frames[-1]


TRADING_MAX = 239


def measure(app, win, n):
    fc = win.ch_fan
    fc._n = T
    fc._times = _times()
    fc._hover_px = None

    # --- 场景 A：逐事件泵事件循环（模拟"系统一次只给一个事件"的常态）
    dt_a, last_a = inject(app, fc, n, pump=True)
    per_a = dt_a / n

    # --- 场景 B：先灌满队列再一次性处理（模拟"事件积压"）
    fc._hover_px = None
    app.processEvents()
    t0 = time.perf_counter()
    inject(app, fc, n, pump=False)
    t_inject = (time.perf_counter() - t0) * 1000.0
    app.processEvents()
    dt_b = (time.perf_counter() - t0) * 1000.0

    return {"per_event": per_a, "total_a": dt_a, "per_b": dt_b / n,
            "inject_only": t_inject / n, "total_b": dt_b}


def measure_clean(app, win, n):
    """干净对照：先排空后台线程的信号事件，再测悬停本身。

    ★★ 为什么需要这个对照 ★★
      MainWindow 构造时会起 QTimer（200ms 后 reload），抓取 Worker 又是 QThread，
      它们的信号投递会把 **MetaCall** 事件混进事件循环 ——
      实测每个 MetaCall 约 97 ms，二十几次就是两秒多。
      若不排空就测，会把"后台抓数据的开销"算成"悬停延迟"，
      优化方向会被完全带偏（这类噪声占过报告总数的 90%）。
      做法：注入前先反复 processEvents 直到队列真正安静，
      并用一轮**不注入鼠标**的空跑作为基线，两轮相减得到纯悬停成本。
    """
    fc = win.ch_fan
    fc._n = T
    fc._times = _times()

    def pump_until_quiet(rounds=40):
        """反复 processEvents 把后台线程的信号投递排空。

        ★ 用固定轮数而不是 hasPendingEvents()：后者在 PySide6 里没有
          （QApplication.hasPendingEvents 是 Qt4 时代的 API，已移除）。
          排空是"幂等"的 —— 队列真空了，多泵几轮只是空转，成本可忽略。
        """
        for _ in range(rounds):
            app.processEvents()
        # 若有 QTimer 触发的 reload，给它一点时间把后续事件也投完
        app.processEvents()

    def run(inject):
        fc._hover_px = None
        pump_until_quiet()
        pump_until_quiet()
        t0 = time.perf_counter()
        for j in range(n):
            if inject:
                w = fc.width()
                x = 60 + int((w - 120) * j / max(1, n - 1))
                y = int(fc.height() * 0.45)
                app.sendEvent(fc, QMouseEvent(
                    QEvent.MouseMove, QPointF(x, y), QPointF(x, y),
                    Qt.NoButton, Qt.NoButton, Qt.NoModifier))
            app.processEvents()
        return (time.perf_counter() - t0) * 1000.0 / n

    base = run(False)          # 空跑基线：只有后台噪声
    hit = run(True)            # 注入鼠标
    return {"baseline": base, "with_mouse": hit, "hover_cost": hit - base}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    a = ap.parse_args()

    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Microsoft YaHei", 9))

    print("端到端「不跟手」量化探针")
    print(f"窗口 {WIN_W}×{WIN_H} · 注入 {a.n} 次连续水平移动")
    print()

    win, series = build_window(app)
    fc = win.ch_fan
    print(f"画布尺寸      : {fc.width()} × {fc.height()} px")
    print(f"曲线          : {len(fc.fig.axes[0].get_lines())} 条 Line2D · "
          f"图例 {len(fc.fig.legends[0].get_texts()) if fc.fig.legends else 0} 条")
    print(f"悬停面板行数  : {win.t_hover.rowCount()}")
    print()

    r = measure(app, win, a.n)
    print("=" * 70)
    print("场景 A：逐事件处理（每个事件后泵一次事件循环）")
    print("=" * 70)
    print(f"  总耗时            {r['total_a']:9.1f} ms")
    print(f"  平均每事件        {r['per_event']:9.2f} ms   ← 体感延迟的核心指标")
    print(f"  60Hz 预算         {16.7:9.2f} ms   （超出即肉眼可见卡顿）")
    ratio = r["per_event"] / 16.7
    verdict = "跟手" if r["per_event"] < 16.7 else (
        "轻微顿挫" if r["per_event"] < 33 else "明显不跟手")
    print(f"  判定              {ratio:8.1f}× 预算 → {verdict}")
    print("  ★ 注意：本场景含后台抓取线程的信号噪声，见下方干净对照。")
    print()
    print("=" * 70)
    print("场景 B：先灌满队列再一次性处理（模拟事件积压）")
    print("=" * 70)
    print(f"  仅注入耗时        {r['inject_only']:9.2f} ms/事件（不含处理）")
    print(f"  注入+处理         {r['total_b']:9.1f} ms")
    print(f"  平均每事件        {r['per_b']:9.2f} ms")
    print()
    print("=" * 70)
    print("场景 C ★ 干净对照（排空后台噪声后，纯悬停成本）")
    print("=" * 70)
    c = measure_clean(app, win, a.n)
    print(f"  空跑基线（不注入鼠标）  {c['baseline']:9.2f} ms/次  ← 纯后台噪声")
    print(f"  注入鼠标（含噪声）      {c['with_mouse']:9.2f} ms/次")
    print(f"  ★ 纯悬停成本            {c['hover_cost']:9.2f} ms/事件")
    print(f"  60Hz 预算               {16.7:9.2f} ms")
    v2 = ("跟手" if c["hover_cost"] < 16.7
          else ("轻微顿挫" if c["hover_cost"] < 33 else "明显不跟手"))
    print(f"  判定                    {c['hover_cost'] / 16.7:8.2f}× 预算 → {v2}")
    print()
    print("=" * 70)
    print(f"结论：单次鼠标移动引发约 {c['hover_cost']:.1f} ms 的同步工作"
          f"（优化前为 122.9 ms）。")
    if c["hover_cost"] < 16.7:
        print("      已进入 60Hz 预算，光标应实时跟随。")
    else:
        print(f"      仍有 {c['hover_cost'] / 8:.1f}× 的堆积风险（鼠标约每 8ms 移动一次）。")


if __name__ == "__main__":
    main()
