# -*- coding: utf-8 -*-
"""精确定位：render_charts 的 1900ms 里，"绘图"与"光栅化"各占多少。

上一轮线索：
  · 6 张图单独 draw() 合计 485ms
  · render_charts 整体 1900ms
  · cProfile 显示 render_charts 自身约 0.5s（text._get_layout 0.535s 最大）
      → 说明有约 1.4s 花在 cProfile **看不见**的地方：
        Qt 事件循环里的延迟重绘（draw_idle → paintEvent）。

关键怀疑：每次 plot() 里调用 draw()/draw_idle()，
导致同一张图在一轮刷新中被**重复光栅化多次**。
数一数 paintEvent 被调用几次即可证实。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

from PySide6.QtWidgets import QApplication                       # noqa: E402
import matplotlib.backends.backend_qtagg as bqa                  # noqa: E402
import native_app as NA                                          # noqa: E402

# ---- 计数器：统计真实光栅化次数 ----
CNT = {"draw": 0, "paint": 0, "t_draw": 0.0}


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(NA.QFont("Microsoft YaHei", 9))
    app.setStyleSheet(NA.build_qss(NA.THEMES["light"]))

    # 打桩：统计 FigureCanvasAgg.draw 的真实调用
    orig_draw = bqa.FigureCanvasQTAgg.draw
    orig_paint = bqa.FigureCanvasQTAgg.paintEvent

    def patched_draw(self, *a, **kw):
        t = time.perf_counter()
        CNT["draw"] += 1
        r = orig_draw(self, *a, **kw)
        CNT["t_draw"] += (time.perf_counter() - t) * 1000
        return r

    def patched_paint(self, *a, **kw):
        CNT["paint"] += 1
        return orig_paint(self, *a, **kw)

    bqa.FigureCanvasQTAgg.draw = patched_draw
    bqa.FigureCanvasQTAgg.paintEvent = patched_paint

    win = NA.MainWindow()
    win.resize(1480, 940)
    win.show()
    for _ in range(600):
        app.processEvents()
        time.sleep(0.05)
        if win.data and win.data.get("F"):
            break

    print("\n=========== 光栅化次数统计 ===========")
    for i in range(3):
        CNT["draw"] = CNT["paint"] = 0
        CNT["t_draw"] = 0.0
        t = time.perf_counter()
        win.render_charts()
        t_plot = (time.perf_counter() - t) * 1000
        app.processEvents()
        t_all = (time.perf_counter() - t) * 1000
        print(f"  第 {i+1} 轮:")
        print(f"    render_charts() 调用阶段 : {t_plot:8.1f} ms")
        print(f"    + processEvents()       : {t_all:8.1f} ms")
        print(f"    canvas.draw() 被调用    : {CNT['draw']:3d} 次，"
              f"累计 {CNT['t_draw']:8.1f} ms")
        print(f"    paintEvent 被调用       : {CNT['paint']:3d} 次")

    # 对照：一次完整的 UI 更新（on_data 全流程）会重绘几次
    print("\n=========== 对照：完整 on_data 一轮 ===========")
    import flowdata
    m, a = flowdata.refresh_panel_spot("today", on_progress=lambda _m: None)
    if m is not None:
        import salience
        F = salience.build_features("today")
        d = dict(win.data)
        d["F"] = F
        d["times"] = list(F.times)
        d["last"] = len(F.times) - 1
        CNT["draw"] = CNT["paint"] = 0
        CNT["t_draw"] = 0.0
        t = time.perf_counter()
        win.on_data(d)
        t_od = (time.perf_counter() - t) * 1000
        app.processEvents()
        t_all = (time.perf_counter() - t) * 1000
        print(f"    on_data()            : {t_od:8.1f} ms")
        print(f"    + processEvents()    : {t_all:8.1f} ms")
        print(f"    canvas.draw() 调用   : {CNT['draw']:3d} 次，"
              f"累计 {CNT['t_draw']:8.1f} ms")
        print(f"    paintEvent 调用      : {CNT['paint']:3d} 次")
        print(f"\n    → 若 draw 次数远超 6，说明存在**重复光栅化**，"
              f"是 10 秒档最值得砍的一刀")

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
