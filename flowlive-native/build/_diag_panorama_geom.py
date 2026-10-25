# -*- coding: utf-8 -*-
"""市场全景页「田字格」几何诊断（离屏，不亮屏）。

打印 4 个模块的**真实几何**（x,y,w,h）、每张卡的 minimumSizeHint/sizeHint，
以及 QGridLayout 的列/行 stretch、spacing、页面 contentsMargins。

用法：
  QT_QPA_PLATFORM=offscreen python _diag_panorama_geom.py [页名] [宽] [高]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QGridLayout, QLabel     # noqa: E402
import app_main_v203 as NA                                          # noqa: E402

PAGE = sys.argv[1] if len(sys.argv) > 1 else "市场全景"
W = int(sys.argv[2]) if len(sys.argv) > 2 else 1480
H = int(sys.argv[3]) if len(sys.argv) > 3 else 940


def main():
    app = QApplication(sys.argv[:1])
    win = NA.MainWindow()
    win.resize(W, H)
    win.show()
    win.resize(W, H)                 # 与 --shot 一致：show() 后抢回尺寸
    for _ in range(30):
        app.processEvents()
    win._materialize_charts()
    for _ in range(10):
        app.processEvents()

    tabs = win.tabs
    names = [tabs.tabText(i) for i in range(tabs.count())]
    idx = names.index(PAGE) if PAGE in names else None
    if idx is None:
        print("no such tab:", PAGE, names)
        return 1
    tabs.setCurrentIndex(idx)
    for _ in range(10):
        app.processEvents()

    page = tabs.widget(idx)
    lay = page.layout()
    g = None
    for i in range(lay.count()):
        it = lay.itemAt(i)
        if isinstance(it, QGridLayout):
            g = it
    if g is None:
        g = page.findChild(QGridLayout)
    pg = page.geometry()
    m = lay.contentsMargins()
    print(f"窗口 {win.width()}x{win.height()}   page={pg.width()}x{pg.height()}  "
          f"outer margins={m.left()},{m.top()},{m.right()},{m.bottom()} "
          f"spacing={lay.spacing()}")
    gm = g.contentsMargins()
    print(f"grid margins={gm.left()},{gm.top()},{gm.right()},{gm.bottom()} "
          f"hspacing={g.horizontalSpacing()} vspacing={g.verticalSpacing()} "
          f"colStretch=[{g.columnStretch(0)},{g.columnStretch(1)}] "
          f"rowStretch=[{g.rowStretch(0)},{g.rowStretch(1)}]")

    cells = {}
    print("-- cells --")
    for i in range(g.count()):
        it = g.itemAt(i)
        rr, cc, rs, cs = g.getItemPosition(i)
        wdg = it.widget()
        if wdg is None:
            continue
        geo, mn, sh = wdg.geometry(), wdg.minimumSizeHint(), wdg.sizeHint()
        cells[(rr, cc)] = geo
        title = ""
        for lb in wdg.findChildren(QLabel):
            if lb.objectName() == "cardTitle":
                title = lb.text()
                break
        print(f"  [{rr},{cc}] geo=({geo.x():4d},{geo.y():4d}) "
              f"{geo.width():4d}x{geo.height():4d}  "
              f"min={mn.width()}x{mn.height()} hint={sh.width()}x{sh.height()}  {title}")

    if len(cells) == 4:
        tl, tr, bl, br = cells[(0, 0)], cells[(0, 1)], cells[(1, 0)], cells[(1, 1)]
        print("-- 对称性 --")
        print(f"  左列宽={tl.width():4d}  右列宽={tr.width():4d}  差={abs(tl.width()-tr.width())}")
        print(f"  上行高={tl.height():4d}  下行高={bl.height():4d}  差={abs(tl.height()-bl.height())}")
        print(f"  竖直缝={tr.x()-tl.x()-tl.width():2d}px  水平缝={bl.y()-tl.y()-tl.height():2d}px")
        print(f"  左列占比={tl.width()/(tl.width()+tr.width()):.4f}")

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
