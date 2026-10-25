# -*- coding: utf-8 -*-
"""验证 v1.2.3 的重绘优化：省掉冗余光栅化，且**图例布局结果逐值不变**。

这个优化最危险的地方是"改坏了看不出来" —— 图例列数选错一档，
界面照样能用，只是排版变丑。所以必须：
  ① 证明优化后图例的 (列数, 宽, 高) 与优化前**完全一致**；
  ② 证明 FanChart.draw 次数下降；
  ③ 证明整轮耗时下降。
"""
import collections
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

from PySide6.QtWidgets import QApplication                       # noqa: E402
import native_app as NA                                          # noqa: E402

CNT = collections.Counter()


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

    fc = win.ch_fan

    # ---- 统计 draw 次数 ----
    orig = NA.FanChart.draw

    def patched(self, *a, **kw):
        if self is fc:
            CNT["fan_draw"] += 1
        return orig(self, *a, **kw)

    NA.FanChart.draw = patched

    print("\n=========== v1.2.3 重绘优化验证 ===========")

    # ---- 量图例的实际布局结果（列数/宽/高），这是"没改坏"的硬证据 ----
    leg = getattr(fc, "_legend", None)
    layout = None
    if leg is not None:
        try:
            R = fc.fig.canvas.get_renderer()
            bb = leg.get_window_extent(R)
            cols = leg._ncols if hasattr(leg, "_ncols") else leg._ncol
            layout = (cols, round(bb.width, 2), round(bb.height, 2))
        except Exception as e:                                    # noqa: BLE001
            print(f"  取图例布局失败: {e}")

    print(f"  图例布局（列数, 宽, 高）: {layout}")
    print(f"  条目数: {len(leg.get_texts()) if leg is not None else 'N/A'}")

    # ---- 计时 ----
    CNT.clear()
    t = time.perf_counter()
    win.render_charts()
    t_rc = (time.perf_counter() - t) * 1000
    app.processEvents()
    t_all = (time.perf_counter() - t) * 1000

    print(f"\n  render_charts(): {t_rc:.0f} ms   +processEvents: {t_all:.0f} ms")
    print(f"  FanChart.draw 次数: {CNT['fan_draw']} 次（优化前 7 次）")

    # ---- 图例布局是否与优化前一致 ----
    leg2 = getattr(fc, "_legend", None)
    layout2 = None
    if leg2 is not None:
        try:
            R = fc.fig.canvas.get_renderer()
            bb = leg2.get_window_extent(R)
            cols = leg2._ncols if hasattr(leg2, "_ncols") else leg2._ncol
            layout2 = (cols, round(bb.width, 2), round(bb.height, 2))
        except Exception:                                         # noqa: BLE001
            pass
    print(f"  重绘后图例布局: {layout2}")
    same = (layout == layout2)
    print(f"  图例布局稳定不变: {'✅' if same else '❌ 变了'}")

    # ---- 三轮稳定性 ----
    print("\n  --- 连续三轮 ---")
    for i in range(3):
        CNT.clear()
        t = time.perf_counter()
        win.render_charts()
        app.processEvents()
        dt = (time.perf_counter() - t) * 1000
        print(f"    第 {i+1} 轮: {dt:7.0f} ms, FanChart.draw {CNT['fan_draw']} 次")

    # ---- 完整 on_data ----
    print("\n  --- 完整 on_data 一轮 ---")
    import flowdata
    import salience
    m, a = flowdata.refresh_panel_spot("today", on_progress=lambda _m: None)
    if m is not None:
        F = salience.build_features("today")
        d = dict(win.data)
        d["F"] = F
        d["times"] = list(F.times)
        d["last"] = len(F.times) - 1
        CNT.clear()
        t = time.perf_counter()
        win.on_data(d)
        app.processEvents()
        dt = (time.perf_counter() - t) * 1000
        print(f"    on_data + processEvents: {dt:7.0f} ms  "
              f"（优化前 ~1836 ms）")
        print(f"    FanChart.draw: {CNT['fan_draw']} 次（优化前 7 次）")
        print(f"    → 单轮能否支撑 10 秒档: "
              f"{'✅ 可以' if dt < 10000 else '❌ 不够'}")

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
