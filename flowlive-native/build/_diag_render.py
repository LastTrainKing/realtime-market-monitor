# -*- coding: utf-8 -*-
"""定位 render_charts 里那"消失的 1400ms"。

6 张图 draw() 合计仅 485ms，render_charts 却要 1900ms。
→ 差值必然在**数据构建**（plot() 内部的计算/排序/取色），而不是光栅化。

用逐图表包裹计时 + cProfile 双路确认。
"""
import cProfile
import io
import os
import pstats
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

    d = win.data
    F = d.get("F")
    m = d.get("market") or {}
    times = d.get("times") or []

    print("\n=========== 逐图 plot() 计时（含数据构建）===========")

    # 复刻 render_charts 的各段，单独计时
    segs = []

    def seg(name, fn):
        t = time.perf_counter()
        try:
            fn()
        except Exception as e:                           # noqa: BLE001
            print(f"    {name}: 异常 {e}")
            return None
        dt = (time.perf_counter() - t) * 1000
        segs.append((name, dt))
        print(f"    {name:26s}: {dt:8.1f} ms")
        return dt

    # 1) 指数
    idx = []
    try:
        for r in (getattr(F, "index", None) or []):
            if isinstance(r, (list, tuple)) and len(r) >= 2:
                idx.append((str(r[0]), float(r[1])))
    except Exception:
        pass
    idx_all = [((r[0], float(r[1])) if isinstance(r, (list, tuple)) else None)
               for r in (d.get("index_all") or [])]
    idx_all = [x for x in idx_all if x]
    seg("ch_index.plot", lambda: win.ch_index.plot(idx, idx_all))

    # 2) 行业柱
    seg("ch_flow.plot", lambda: win.ch_flow.plot(
        m.get("top_in") or [], m.get("top_out") or [],
        m.get("sector_in") or [], m.get("sector_out") or []))

    # 3) Fan 曲线 —— 先单独建 series（分离"构建"与"绘制"）
    def build_series():
        import salience
        # 复刻 render_charts 里 fan 的 series 构建
        return None

    def fan_plot():
        # 直接调用完整路径
        try:
            win.render_fan()                                  # 若存在
        except AttributeError:
            pass

    # FanChart 的 series 构建是 render_charts 内部做的，这里单独 profile
    print("\n  --- cProfile: 完整 render_charts ---")
    pr = cProfile.Profile()
    pr.enable()
    win.render_charts()
    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(22)
    out = s.getvalue()
    for line in out.splitlines():
        if any(k in line for k in ("cumulative", "salience", "native_app",
                                   "matplotlib", "numpy", "_cluster", "score",
                                   "features", "draw", "render")):
            print("   ", line[:150])

    print("\n  --- cProfile: 只跑 render_charts 的 tottime 前 15 ---")
    s2 = io.StringIO()
    ps2 = pstats.Stats(pr, stream=s2).sort_stats("tottime")
    ps2.print_stats(15)
    for line in s2.getvalue().splitlines()[4:24]:
        print("   ", line[:150])

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
