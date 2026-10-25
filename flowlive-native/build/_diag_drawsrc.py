# -*- coding: utf-8 -*-
"""定位 12 次 canvas.draw() 的**具体调用来源**（按调用栈归因）。

已知：一轮刷新里 6 张图被光栅化 12 次，累计 ~1455ms。
      需要知道每一组是哪条路径触发的，才能精准砍掉重复的那一半。
"""
import collections
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

from PySide6.QtWidgets import QApplication                       # noqa: E402
import matplotlib.backends.backend_qtagg as bqa                  # noqa: E402
import native_app as NA                                          # noqa: E402

STATS = collections.defaultdict(lambda: [0, 0.0])


def caller_key():
    """取最近一个属于本项目的调用帧（跳过 matplotlib/PySide6 内部）。"""
    st = traceback.extract_stack()
    for fr in reversed(st[:-2]):
        fn = fr.filename.replace("\\", "/")
        if fn.endswith("native_app.py"):
            return f"native_app.py:{fr.lineno} {fr.name}()"
        if fn.endswith("verify_hover_blit.py"):
            return f"verify:{fr.lineno} {fr.name}()"
    # 都不是 → 记录最近的 Qt/matplotlib 帧
    for fr in reversed(st[:-2]):
        fn = fr.filename.replace("\\", "/")
        base = fn.split("/")[-1]
        if base in ("backend_qt.py", "backend_qtagg.py", "backend_agg.py",
                    "figure.py"):
            return f"{base}:{fr.lineno} {fr.name}()"
    return "?"


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(NA.QFont("Microsoft YaHei", 9))
    app.setStyleSheet(NA.build_qss(NA.THEMES["light"]))

    orig = bqa.FigureCanvasQTAgg.draw

    def patched(self, *a, **kw):
        t = time.perf_counter()
        r = orig(self, *a, **kw)
        dt = (time.perf_counter() - t) * 1000
        rec = STATS[caller_key()]
        rec[0] += 1
        rec[1] += dt
        return r

    bqa.FigureCanvasQTAgg.draw = patched

    win = NA.MainWindow()
    win.resize(1480, 940)
    win.show()
    for _ in range(600):
        app.processEvents()
        time.sleep(0.05)
        if win.data and win.data.get("F"):
            break

    print("\n=========== 一轮 render_charts 的 draw 归因 ===========")
    STATS.clear()
    t = time.perf_counter()
    win.render_charts()
    t_rc = (time.perf_counter() - t) * 1000
    app.processEvents()
    t_all = (time.perf_counter() - t) * 1000
    print(f"  render_charts(): {t_rc:.0f} ms   +processEvents: {t_all:.0f} ms")
    tot = sum(v[1] for v in STATS.values())
    for k, (n, ms) in sorted(STATS.items(), key=lambda x: -x[1][1]):
        print(f"    {n:3d} 次 {ms:8.1f} ms   ← {k}")
    print(f"    合计 {sum(v[0] for v in STATS.values())} 次, {tot:.1f} ms")

    print("\n=========== 一轮完整 on_data 的 draw 归因 ===========")
    import flowdata
    m, a = flowdata.refresh_panel_spot("today", on_progress=lambda _m: None)
    if m is not None:
        import salience
        F = salience.build_features("today")
        d = dict(win.data)
        d["F"] = F
        d["times"] = list(F.times)
        d["last"] = len(F.times) - 1
        STATS.clear()
        t = time.perf_counter()
        win.on_data(d)
        app.processEvents()
        t_all = (time.perf_counter() - t) * 1000
        print(f"  on_data + processEvents: {t_all:.0f} ms")
        tot = sum(v[1] for v in STATS.values())
        for k, (n, ms) in sorted(STATS.items(), key=lambda x: -x[1][1]):
            print(f"    {n:3d} 次 {ms:8.1f} ms   ← {k}")
        print(f"    合计 {sum(v[0] for v in STATS.values())} 次, {tot:.1f} ms")

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
