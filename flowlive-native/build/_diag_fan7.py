# -*- coding: utf-8 -*-
"""FanChart.draw() 被调用 7 次的精确调用栈（一次刷新内）。

已知：FanChart 覆写的 draw() 在单轮刷新里跑 7 次 / 1053ms，占全部光栅化 73%。
必须看清 7 次分别由谁触发，才能只砍掉冗余、不动必需的。
"""
import collections
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

from PySide6.QtWidgets import QApplication                       # noqa: E402
import matplotlib.backends.backend_qtagg as bqa                  # noqa: E402
import native_app as NA                                          # noqa: E402

STATS = collections.defaultdict(int)


def stack_key():
    """取本项目内的调用链（最多 5 帧），用于聚类。"""
    st = traceback.extract_stack()
    parts = []
    for fr in reversed(st[:-2]):
        fn = fr.filename.replace("\\", "/")
        if fn.endswith("native_app.py"):
            parts.append(f"{fr.lineno}:{fr.name}")
        if len(parts) >= 4:
            break
    return " ← ".join(parts) or "?"


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
        import time as _t
        _t.sleep(0.05)
        if win.data and win.data.get("F"):
            break

    orig = NA.FanChart.draw

    def patched(self, *a, **kw):
        STATS[stack_key()] += 1
        return orig(self, *a, **kw)

    NA.FanChart.draw = patched

    print("\n=========== FanChart.draw 调用来源（单轮 render_charts）===========")
    STATS.clear()
    win.render_charts()
    app.processEvents()
    for k, n in sorted(STATS.items(), key=lambda x: -x[1]):
        print(f"    {n} 次  ← {k}")
    print(f"    合计 {sum(STATS.values())} 次")

    print("\n=========== 单轮 on_data ===========")
    import flowdata
    import salience
    m, a = flowdata.refresh_panel_spot("today", on_progress=lambda _m: None)
    if m is not None:
        F = salience.build_features("today")
        d = dict(win.data)
        d["F"] = F
        d["times"] = list(F.times)
        d["last"] = len(F.times) - 1
        STATS.clear()
        win.on_data(d)
        app.processEvents()
        for k, n in sorted(STATS.items(), key=lambda x: -x[1]):
            print(f"    {n} 次  ← {k}")
        print(f"    合计 {sum(STATS.values())} 次")

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
