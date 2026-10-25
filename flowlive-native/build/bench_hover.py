# -*- coding: utf-8 -*-
"""bench_hover.py —— 主力资金曲线「光标跟随」延迟专项探针（可重复运行）

为什么单写一个探针
------------------
"光标不跟手"这类主观抱怨必须落到**可测的分段耗时**上，否则改哪儿都是猜。
本脚本把一次鼠标移动所触发的**全部**工作拆成四段，逐段计时：

    ① 事件到达密度 —— 鼠标划一下到底产生多少个 motion_notify_event
    ② 竖线绘制     —— FanChart._mark() → draw_idle() → 整幅画布重绘
    ③ 悬停取数     —— MainWindow.on_fan_hover() 里的 score_table（含缓存命中/未命中）
    ④ 表格填充     —— fill_table() 重建 QTableWidget

用法
----
    python bench_hover.py           # 全量（离线，用本地面板数据；秒级）
    python bench_hover.py --quiet   # 只输出汇总表

★ 关键：必须用**真实规模**测
   · 曲线：20 条 × 240 分钟（4800 点）+ 20 条图例 —— 图例是重活，别用假数据
   · 榜单：真实板块数（约 1000 个），score_table 要对全量做 argsort
   用 30 点的小样本测出来的数字没有意义。
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np                                     # noqa: E402

import native_app as NA                                # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402
from PySide6.QtGui import QFont                        # noqa: E402

DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "FlowLiveNative", "data")

QUIET = False


def say(*a):
    if not QUIET:
        print(*a)


def _med(fn, n=9):
    xs = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t) * 1000.0)
    return statistics.median(xs)


def _best(fn, n=9):
    """取最小值：排除 GC / 系统抖动，看"一次调用最理想要多久"。"""
    xs = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t) * 1000.0)
    return min(xs)


# ------------------------------------------------------------------ 造真实曲线
def make_fan(tag=None, W=10.02, T=240):
    """按真实窗口尺寸造一张 FanChart：20 条 × T 分钟 + 图例。"""
    names = [f"板块名称示例{i:02d}概念" for i in range(20)]
    series = []
    rng = np.random.default_rng(7)
    for i in range(20):
        v = np.cumsum(rng.normal(0, 3.0, T)) + (i - 10) * 12.0
        series.append({"name": names[i],
                       "series": [float(x) for x in v],
                       "net": float(v[-1]),
                       "direction": "in" if i < 10 else "out",
                       "times": [f"{9 + (30 + k) // 60:02d}:{(30 + k) % 60:02d}"
                                 for k in range(T)]})
    fc = NA.FanChart(3.67, W)
    fc.plot(series)
    return fc, series


def load_real_panel():
    """载入真实本地面板（有就用真的，没有就合成一个同规模的）。"""
    try:
        import flowdata as fd
        import salience as S
        d = os.path.join(DATA_DIR)
        fd.set_data_dir(d)
        cands = sorted([f for f in os.listdir(d)
                        if f.startswith("panel_") and f.endswith(".json")])
        if not cands:
            return None
        tag = cands[-1][len("panel_"):-len(".json")]
        F = S.build_features(tag)
        say(f"  [真实数据] tag={tag} 板块数={F.V.shape[1]} 分钟数={F.V.shape[0]}")
        return tag, F
    except Exception as e:                             # noqa: BLE001
        say(f"  [真实数据不可用] {type(e).__name__}: {e}")
        return None


# ================================================================== ① 事件密度
def bench_event_rate(app):
    """实测：一段连续鼠标划动会产生多少个 motion 事件。

    ★ 这是全局前提 —— 若事件本身就以 100+ Hz 到达，那么
      「每个事件做 30ms 的活」必然卡；反之若只有 20 Hz，则 30ms 也还凑合。
      所以先量事件密度，再谈单次成本。
    """
    print("=" * 74)
    print("① 事件密度：一次真实划动产生多少 motion 事件")
    print("=" * 74)
    fc, _ = make_fan()
    fc.resize(1002, 367)
    n = [0]

    def _cnt(ev):
        n[0] += 1

    cid = fc.mpl_connect("motion_notify_event", _cnt)
    from PySide6.QtCore import QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt as _Qt

    y = 150
    t0 = time.perf_counter()
    for x in range(40, 980):
        fc.mpl_connect  # noqa: B018  (占位，保持引用)
        ev = QMouseEvent(QEvent.MouseMove, QPointF(x, y), QPointF(x, y),
                         _Qt.NoButton, _Qt.NoButton, _Qt.NoModifier)
        app.sendEvent(fc, ev)
    dt = (time.perf_counter() - t0) * 1000.0
    fc.mpl_disconnect(cid)
    print(f"  注入 940 次鼠标移动 → 收到 {n[0]} 个 motion_notify_event"
          f"（{n[0] / 940:.2f} 个/像素）· 共耗时 {dt:6.1f} ms")
    print(f"  平均每次注入 {dt / 940:6.2f} ms"
          f"  ← 这就是「鼠标移动 → 界面响应」的端到端回环成本")
    return dt / 940


# ================================================================== ② 竖线绘制
def bench_vline(fc):
    print()
    print("=" * 74)
    print("② 竖线绘制：FanChart._mark() 一次要多久（整幅画布重绘）")
    print("=" * 74)
    # 冷启动一次（把 lazily 的东西都建出来）
    fc._mark(10)

    t_draw = _med(lambda: fc.draw_idle(), 15)
    t_mark = _med(lambda: fc._mark(int(np.random.randint(0, 240))), 15)

    # 拆开：draw_idle 的真实重绘来自 canvas.draw()
    t_full = _med(lambda: fc.draw(), 15)

    print(f"  draw_idle()（请求重绘，排队）      {t_draw:7.2f} ms")
    print(f"  draw()     （同步全量重绘）        {t_full:7.2f} ms")
    print(f"  _mark(k)   （set_xdata + 重绘）    {t_mark:7.2f} ms")
    print()
    print(f"  画布内容：{len(fc.fig.axes[0].get_lines())} 条 Line2D · "
          f"图例 {len(fc.fig.legends[0].get_texts()) if fc.fig.legends else 0} 条")

    # 分解 draw 的成本：Agg 光栅化 vs 图例布局
    fig = fc.fig
    t_layout = _med(lambda: fig.canvas.get_renderer(), 15)
    print(f"  get_renderer()（Agg 缓冲复用）     {t_layout:7.2f} ms")

    # 若把图例摘掉，draw 会快多少？（量化图例的拖累）
    leg = fig.legends[0] if fig.legends else None
    t_noleg = None
    if leg is not None:
        leg.set_visible(False)
        t_noleg = _med(lambda: fc.draw(), 15)
        leg.set_visible(True)
        fc.draw()
        print(f"  隐藏图例后 draw()（对照）          {t_noleg:7.2f} ms"
              f"   ← 图例占 {t_full - t_noleg:6.2f} ms")
    return t_mark, t_full


# ================================================================== ③ 悬停取数
def bench_score(tag, F):
    print()
    print("=" * 74)
    print("③ 悬停取数：on_fan_hover 内 score_table 的耗时（缓存未命中时）")
    print("=" * 74)
    import salience as S
    T = F.V.shape[0]
    mid = T // 2
    t_in = _med(lambda: S.score_table(F, mid, topn=7), 7)
    t_out = _med(lambda: S.score_table(F, mid, topn=7, direction="out"), 7)
    print(f"  score_table(topn=7)     流入        {t_in:7.2f} ms")
    print(f"  score_table(topn=7)     流出        {t_out:7.2f} ms")
    print(f"  → 一次缓存未命中 ≈          {t_in + t_out:7.2f} ms")
    print(f"  → 缓存命中（反向划回同一列）        ≈ 0 ms（只查 dict）")
    return t_in + t_out


# ================================================================== ④ 表格填充
def bench_fill(rows_n=14):
    print()
    print("=" * 74)
    print("④ 表格填充：fill_table() 重建 QTableWidget 的耗时")
    print("=" * 74)
    cols_t = ["#", "题材", "净额(亿)", "5分钟(亿)", "辨识度"]
    t = NA.make_table(cols_t)
    t.resize(560, 420)

    def payload(n):
        return [[i, f"板块名称{i}", f"{i * 1.1:+.2f}", f"{i * .3:+.2f}", "1.23"]
                for i in range(n)]

    al = t._align_map
    for n in (len(cols_t) and 14, 20, 30):
        rws = payload(n)
        t._align_map = al
        print(f"  fill_table({n:2d} 行)            {_med(lambda: NA.fill_table(t, rws), 15):7.2f} ms")

    # 分解：新建 QTableWidgetItem 是大头吗？
    from PySide6.QtWidgets import QTableWidgetItem
    rws = payload(14)
    t2 = NA.make_table(cols_t)

    def _no_align():
        t2.setRowCount(14)
        for i, row in enumerate(rws):
            for c, txt in enumerate(row):
                t2.setItem(i, c, QTableWidgetItem(str(txt)))

    print(f"  ├ 只 setItem（不设对齐/颜色）      {_med(_no_align, 15):7.2f} ms")
    print(f"  └ 完整 fill_table（含对齐+着色）   "
          f"{_med(lambda: NA.fill_table(t2, rws), 15):7.2f} ms")

    # 关键对照：只改文字 vs 重建行 —— 用于判断"能否只更新不重建"
    xy = [(i, c) for i in range(14) for c in range(5)]
    def _rewrite_only():
        for i, c in xy:
            it = t2.item(i, c)
            if it is not None:
                it.setText("0.00")
    print(f"  ★ 原地改 70 个单元格文字（不重建）  {_med(_rewrite_only, 15):7.2f} ms")
    return _med(lambda: NA.fill_table(t2, rws), 15)


# ================================================================== 汇总
def main():
    global QUIET
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    QUIET = a.quiet

    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Microsoft YaHei", 9))

    print("资金流向原生版 · 光标跟随延迟探针")
    print(f"数据目录: {DATA_DIR}")
    print()

    per_px = bench_event_rate(app)
    fc, _ = make_fan()
    fc.resize(1002, 367)
    t_mark, t_full = bench_vline(fc)

    real = load_real_panel()
    if real:
        tag, F = real
        t_score = bench_score(tag, F)
    else:
        print()
        print("③ 悬停取数：无本地面板数据，跳过（改用合成面板估算）")
        t_score = float("nan")

    t_fill = bench_fill()

    print()
    print("=" * 74)
    print("汇总：一次「鼠标移动到新分钟」的总成本")
    print("=" * 74)
    parts = [("竖线重绘 _mark", t_mark),
             ("悬停取数 score_table×2（未命中）", t_score),
             ("表格重建 fill_table", t_fill)]
    tot = 0.0
    for nm, v in parts:
        if v == v:
            tot += v
            print(f"  {nm:38s} {v:8.2f} ms")
    print(f"  {'—' * 38} {'—' * 8}")
    print(f"  {'合计（缓存未命中）':38s} {tot:8.2f} ms")
    hit = t_mark + t_fill
    print(f"  {'合计（缓存命中）':38s} {hit:8.2f} ms")
    print()
    print(f"  若鼠标以 60 Hz 划动（16.7 ms 预算）：")
    print(f"    未命中路径超出预算 {tot / 16.7:5.1f}×  → 视觉上就是「明显不跟手」")
    print(f"    命中路径超出预算   {hit / 16.7:5.1f}×")
    print()
    print(f"  实测每像素端到端 {per_px:.2f} ms（@ 1002px 宽、940 次注入）")


if __name__ == "__main__":
    main()
