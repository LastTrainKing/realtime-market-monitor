# -*- coding: utf-8 -*-
"""运行期面板普查 —— 直接问 Qt 要真实控件树，与源码普查对账。

源码普查 = 设计意图（打算建几个面板）
运行期普查 = 实际结果（Qt 里真有 几个）
两者必须相等；不等 = "写了没挂上"或"挂了两次"。

★ 判据：`make_card()` 建的卡片都有 objectName == "card"（见 L3961）。
"""
import importlib.util as ilu
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

spec = ilu.spec_from_file_location("native_app", os.path.join(HERE, "app_main_v203.py"))
NA = ilu.module_from_spec(spec)
sys.modules["native_app"] = NA
spec.loader.exec_module(NA)
assert os.path.basename(NA.__file__) == "app_main_v203.py", NA.__file__
NA.ensure_mpl()

from PySide6.QtWidgets import QApplication, QFrame

app = QApplication.instance() or QApplication(sys.argv)
# 找主窗口类（名字随版本变，按"有 tabs 属性"认）
Win = None
for nm in dir(NA):
    o = getattr(NA, nm)
    if isinstance(o, type) and issubclass(o, QApplication.__mro__[-1].__class__ or object):
        pass
for nm in ("MainWindow", "App", "MainWin", "NativeApp"):
    if hasattr(NA, nm):
        Win = getattr(NA, nm)
        break
if Win is None:
    from PySide6.QtWidgets import QMainWindow
    cands = [getattr(NA, n) for n in dir(NA)
             if isinstance(getattr(NA, n), type)
             and issubclass(getattr(NA, n), QMainWindow)]
    Win = cands[0] if cands else None
print("主窗口类：", Win)

win = Win()
top = win.tabs
print("=" * 80)
print("运行期面板普查（Qt 真实控件树）")
print("=" * 80)
print("顶层页签 %d 个：" % top.count(), [top.tabText(i) for i in range(top.count())])

groups = getattr(win, "group_tabs", {})
print("子页签容器 %d 个：%s" % (len(groups), sorted(groups.keys())))

total = 0
per = {}
for g, sub in groups.items():
    print("\n【%s】%d 个子页" % (g, sub.count()))
    for i in range(sub.count()):
        nm = sub.tabText(i)
        page = sub.widget(i)
        cards = [c for c in page.findChildren(QFrame) if c.objectName() == "card"]
        total += len(cards)
        per[(g, nm)] = len(cards)
        print("   └─ %-6s %2d 个面板" % (nm, len(cards)))

# 总览（顶层页，不在 group_tabs 里）
ov = None
for i in range(top.count()):
    if top.tabText(i) == "总览":
        ov = top.widget(i)
if ov is not None:
    cards = [c for c in ov.findChildren(QFrame) if c.objectName() == "card"]
    total += len(cards)
    per[("总览", "总览")] = len(cards)
    print("\n【总览】%d 个面板" % len(cards))

print("\n" + "=" * 80)
print("运行期面板合计：%d" % total)

# 导出给对账用
import json
out = os.path.join(HERE, "_census_runtime.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump({f"{g}|{s}": n for (g, s), n in per.items()},
              f, ensure_ascii=False, indent=1)
print("已写：", out)
