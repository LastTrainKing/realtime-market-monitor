# -*- coding: utf-8 -*-
"""拆解 render_charts 的 1950ms：到底是哪张图、哪个环节贵。

已知：增量抓取仅 ~337ms，但整轮 UI 更新要 ~1950ms（占 84%）。
      10 秒周期下这意味着每次刷新界面会卡近 2 秒 —— 必须找到大头。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

from PySide6.QtWidgets import QApplication                       # noqa: E402
import native_app as NA                                          # noqa: E402


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(NA.QFont("Microsoft YaHei", 9))
    app.setStyleSheet(NA.build_qss(NA.THEMES["light"]))

    win = NA.MainWindow()
    win.resize(1480, 940)
    win.show()
    for _ in range(600):
        app.processEvents()
        time.sleep(0.05)
        if win.data and win.data.get("F"):
            break
    print(f"\n  数据就位: tag={(win.data or {}).get('date_tag')}")

    # 逐张图计时
    print("\n=========== 逐图重绘耗时 ===========")
    charts = [
        ("ch_index  指数", lambda: win.ch_index.plot(
            [(str(r[0]), float(r[1])) for r in
             (getattr(win.data.get("F"), "index", None) or [])
             if isinstance(r, (list, tuple)) and len(r) >= 2], [])),
        ("ch_flow   行业/概念柱", None),
        ("ch_fan    主力资金曲线", None),
        ("ch_lead   龙头", None),
    ]
    # 直接量每张 canvas 的 draw()（这是重绘的实质成本）
    for name, c in (("ch_index", win.ch_index), ("ch_flow", win.ch_flow),
                    ("ch_fan", win.ch_fan), ("ch_lead", win.ch_lead),
                    ("ch_stage", win.ch_stage), ("ch_theme", win.ch_theme)):
        if c is None:
            continue
        t = time.perf_counter()
        c.draw()
        dt = (time.perf_counter() - t) * 1000
        na = len(getattr(c, "fig", None).axes) if getattr(c, "fig", None) else 0
        try:
            nlines = sum(len(a.lines) for a in c.fig.axes)
            npatches = sum(len(a.patches) for a in c.fig.axes)
        except Exception:
            nlines = npatches = -1
        print(f"    {name:12s} draw(): {dt:8.1f} ms   axes={na} "
              f"lines={nlines} patches={npatches}")

    # 单独量 fan（20 条曲线 + 图例）
    print("\n=========== FanChart 细节 ===========")
    fc = win.ch_fan
    t = time.perf_counter()
    fc.draw()
    t_all = (time.perf_counter() - t) * 1000
    # 有图例 vs 无图例
    leg = None
    try:
        leg = fc.fig.legends[0] if fc.fig.legends else None
    except Exception:
        pass
    if leg is not None:
        leg.set_visible(False)
        t = time.perf_counter()
        fc.draw()
        t_noleg = (time.perf_counter() - t) * 1000
        leg.set_visible(True)
        print(f"    整图 draw():        {t_all:8.1f} ms")
        print(f"    隐藏图例后 draw():  {t_noleg:8.1f} ms")
        print(f"    → 图例占:           {t_all - t_noleg:8.1f} ms "
              f"({(t_all - t_noleg)/max(t_all,1e-9)*100:.0f}%)")

    # 整轮 render_charts
    print("\n=========== render_charts 整体 ===========")
    for i in range(3):
        t = time.perf_counter()
        win.render_charts()
        dt = (time.perf_counter() - t) * 1000
        print(f"    第 {i+1} 次: {dt:8.1f} ms")

    # 表格填充
    print("\n=========== 表格填充 ===========")
    d = win.data
    cands = [(n, getattr(win, n)) for n in dir(win)
             if n.startswith("t_") and isinstance(getattr(win, n, None),
                                                  NA.QTableWidget)]
    for n, t in sorted(cands):
        rows = t.rowCount()
        t0 = time.perf_counter()
        for r in range(rows):
            for c in range(t.columnCount()):
                it = t.item(r, c)
                if it is not None:
                    _ = it.text()
        dt = (time.perf_counter() - t0) * 1000
        print(f"    {n:12s} {rows:3d} 行 × {t.columnCount()} 列  "
              f"遍历读取 {dt:6.1f} ms   frozen={getattr(t,'_widths_frozen',None)}")

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
