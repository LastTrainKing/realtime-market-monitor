# -*- coding: utf-8 -*-
"""v3.0.2 补采状态截图 —— 给用户看"补采长什么样"。

构造「m5 准点 / m10 补采 / m15 缺失」三种状态同屏，截「大盘·开盘检测」
与「个股·个股异动」两页，用于交付说明与视觉验收。

★ 截图用离屏平台（无中文字体问题在此场景不影响判断 —— 我们看的是标题后缀
  与表格布局，不是字形）。真实字形验收仍走 --shot 正常平台路径。
"""
import importlib.util as ilu
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

spec = ilu.spec_from_file_location("native_app", os.path.join(HERE, "app_main_v203.py"))
NA = ilu.module_from_spec(spec)
sys.modules["native_app"] = NA
spec.loader.exec_module(NA)
NA.ensure_mpl()

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

OUT = os.path.join(HERE, "_catchup_shots")
os.makedirs(OUT, exist_ok=True)
TMP = os.path.join(OUT, "_data")
os.makedirs(TMP, exist_ok=True)
TAG = "20260923"

import random  # noqa: E402
rnd = random.Random(20260923)

NAMES = ["半导体设备", "汽车零部件", "光模块", "固态电池", "低空经济",
         "人形机器人", "算力租赁", "创新药", "消费电子", "军工电子"]
SNAMES = ["中际旭创", "寒武纪", "北方华创", "拓普集团", "亿纬锂能",
          "万丰奥威", "三花智控", "恒瑞医药", "立讯精密", "中航光电"]


def _node(captured_at, on_time, planned):
    secs = [{"c": "BK%03d" % i, "n": NAMES[i], "v": 980000 - i * 52000,
             "a": 9.2e9 - i * 5.1e8, "net": 3.1e9 - i * 2.6e8}
            for i in range(len(NAMES))]
    stks = [{"c": "00%04d" % i, "n": SNAMES[i], "v": 88000 - i * 4200,
             "a": 7.8e8 - i * 4.2e7, "lb": 9.4 - i * 0.55,
             "net": 2.4e8 - i * 1.8e7, "pct": 9.8 - i * 0.7}
            for i in range(len(SNAMES))]
    return {"minutes": 5, "label": "", "captured_at": captured_at,
            "on_time": on_time, "planned": planned,
            "sectors": secs, "stocks": stks}


snap = {"nodes": {
    "m5": _node("09:36:08", True, "09:36"),      # 准点
    "m10": _node("10:05:31", False, "09:41"),    # 补采
    # m15 缺失
}}
with open(os.path.join(TMP, "opensnap_%s.json" % TAG), "w", encoding="utf-8") as f:
    json.dump(snap, f, ensure_ascii=False)

import flowdata  # noqa: E402
flowdata.DATA = TMP
import opensnap  # noqa: E402
opensnap.flowdata = flowdata

win = NA.MainWindow()
win.resize(1480, 940)
win.show()
app.processEvents()
win.data = {"date_tag": TAG, "times": [], "sectors": []}
win.fill_open_pages()
app.processEvents()

# 定位到「大盘 · 开盘检测」
for i in range(win.tabs.count()):
    if win.tabs.tabText(i) == "大盘":
        win.tabs.setCurrentIndex(i)
        break
app.processEvents()
win.group_tabs["大盘"].setCurrentIndex(1)          # 开盘检测
app.processEvents()
p1 = os.path.join(OUT, "A_开盘检测_准点+补采+缺失.png")
win.grab().save(p1)
print("saved", os.path.basename(p1), os.path.getsize(p1), "B")

# 定位到「个股 · 个股异动」
for i in range(win.tabs.count()):
    if win.tabs.tabText(i) == "个股":
        win.tabs.setCurrentIndex(i)
        break
app.processEvents()
win.group_tabs["个股"].setCurrentIndex(0)          # 个股异动
app.processEvents()
p2 = os.path.join(OUT, "B_个股异动_补采标注.png")
win.grab().save(p2)
print("saved", os.path.basename(p2), os.path.getsize(p2), "B")

# 打印标题核对
from PySide6.QtWidgets import QFrame, QLabel  # noqa: E402


def title_of(pid):
    for c in win.findChildren(QFrame):
        if str(c.property("panelId") or "") == pid:
            for lb in c.findChildren(QLabel):
                if lb.objectName() == "cardTitle":
                    return lb.text()
    return None


print("\n卡片标题实测：")
for pid in ("open_check.m5_vol", "open_check.m10_vol", "open_check.m15_vol",
            "stock_move.m5_lb", "stock_move.m10_lb", "stock_move.m15_lb"):
    print("  %-24s %s" % (pid, title_of(pid)))
