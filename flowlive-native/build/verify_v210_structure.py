# -*- coding: utf-8 -*-
"""v2.1.0 两级页签重构 —— 专项结构验证（离线、不联网）。

为什么单独写这一个：chk115.py 是 v1.1.5 时代的旧套件，里面大量断言针对
**已删除的功能**（t_hover 悬停面板、9 张交付截图等），直接跑会中途崩。
本次重构需要的是"结构守恒 + 跳转正确 + 图表归属正确"这三件事的证据，
所以单独写一份针对性强、不依赖旧套件的验证。

本脚本验证：
  S1  顶层页签 == TAB_GROUPS 母模块序列（6 个）
  S2  全部 14 个子模块完好挂载（含总览）· 无丢失、无重复
  S3  每个母模块内部的子页顺序 == TAB_TREE 声明
  S4  PAGE_INDEX / PAGE_NAMES 未被破坏（旧接口兼容）
  S5  GROUP_POS / PAGE_GROUP / GROUP_INDEX 与真实控件自洽
  S6  goto_module() 按子模块名能跳对（逐个子模块实测）
  S7  goto_page_name() 按 (母模块, 子页下标) 能跳对
  S8  CHART_PAGE 的值全部是**真实存在的母模块名**（防图表归属写错）
  S9  六张图表控件全部被创建（materialize 后）
  S10 视图菜单按两级生成，Ctrl+1..6 快捷键齐全
运行：  native_env/Scripts/python.exe verify_v210_structure.py
"""
import importlib.util as ilu
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = "app_main_v203.py"

spec = ilu.spec_from_file_location("native_app", os.path.join(HERE, MAIN))
NA = ilu.module_from_spec(spec)
sys.modules["native_app"] = NA
spec.loader.exec_module(NA)

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))

# 关键：不进入网络路径，纯 UI 结构验证
sys.argv = [MAIN, "--shot"]
NA.ensure_mpl()
win = NA.MainWindow()

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


print("=" * 72)
print("v2.1.0 两级页签重构 · 专项结构验证")
print("=" * 72)

# ---------------- S1 顶层页签 ----------------
top = [win.tabs.tabText(i) for i in range(win.tabs.count())]
expect_top = [g for g, _s in NA.TAB_GROUPS]
check("S1 顶层页签 == TAB_GROUPS 母模块序列",
      top == expect_top, f"top={top}")
check("S1b 顶层共 6 个（总览 + 5 个打包母模块）",
      len(top) == 6, f"n={len(top)}")

# ---------------- S2 子模块守恒 ----------------
mounted = []
for g, subs in NA.TAB_GROUPS:
    st = win.group_tabs.get(g)
    if st is None:
        mounted.append(g)
    else:
        mounted.extend(st.tabText(i) for i in range(st.count()))
check("S2 全部子模块完好挂载（含总览）",
      sorted(mounted) == sorted(NA.PAGE_NAMES),
      f"n={len(mounted)} missing={sorted(set(NA.PAGE_NAMES) - set(mounted))}")
check("S2b 无重复挂载",
      len(mounted) == len(set(mounted)),
      f"dup={[x for x in set(mounted) if mounted.count(x) > 1]}")
check("S2c 13 个非总览子模块 + 总览 = 14",
      len(mounted) == 14, f"n={len(mounted)}")

# ---------------- S3 每个母模块内子页顺序 ----------------
order_ok, order_msg = True, []
for g, subs in NA.TAB_GROUPS:
    st = win.group_tabs.get(g)
    if st is None:
        continue
    now = [st.tabText(i) for i in range(st.count())]
    if now != list(subs):
        order_ok = False
        order_msg.append(f"{g}: {now} != {list(subs)}")
check("S3 每个母模块内子页顺序 == TAB_TREE 声明",
      order_ok, "; ".join(order_msg) or "全部一致")

# ---------------- S4 旧接口兼容 ----------------
check("S4 PAGE_NAMES / PAGE_INDEX 未破坏（Ctrl+N 与旧跳页代码仍可用）",
      len(NA.PAGE_NAMES) == 14
      and all(NA.PAGE_INDEX[nm] == i for i, nm in enumerate(NA.PAGE_NAMES)),
      f"n={len(NA.PAGE_NAMES)}")

# ---------------- S5 映射表自洽 ----------------
ok5, msg5 = True, []
for nm in NA.PAGE_NAMES:
    if nm == "总览":
        continue
    pos = NA.GROUP_POS.get(nm)
    if pos is None:
        ok5 = False; msg5.append(f"{nm} 缺 GROUP_POS"); continue
    if top[pos[0]] != NA.PAGE_GROUP[nm]:
        ok5 = False; msg5.append(f"{nm}: {top[pos[0]]}≠{NA.PAGE_GROUP[nm]}")
check("S5 GROUP_POS / PAGE_GROUP / GROUP_INDEX 与真实控件自洽",
      ok5, "; ".join(msg5) or "全部自洽")

# ---------------- S6/S7 跳转实测 ----------------
jump_ok, jump_msg = True, []
for nm in NA.PAGE_NAMES:
    win.goto_module(nm)
    gi = win.tabs.currentIndex()
    gname = win.tabs.tabText(gi)
    if nm == "总览":
        if gname != "总览":
            jump_ok = False; jump_msg.append(f"{nm}→{gname}")
        continue
    st = win.group_tabs.get(gname)
    sname = st.tabText(st.currentIndex()) if st else "?"
    if sname != nm:
        jump_ok = False; jump_msg.append(f"{nm}⇒{gname}/{sname}")
check("S6 goto_module：逐个按**子模块名**跳转均落到正确子页",
      jump_ok, "; ".join(jump_msg) or "14/14 全部正确")

jump2_ok, jump2_msg = True, []
for g, subs in NA.TAB_GROUPS:
    for si, nm in enumerate(subs):
        win.goto_page_name(g, si)
        gname = win.tabs.tabText(win.tabs.currentIndex())
        st = win.group_tabs.get(gname)
        sname = st.tabText(st.currentIndex()) if st else "?"
        if gname != g or sname != nm:
            jump2_ok = False; jump2_msg.append(f"({g},{si})⇒{gname}/{sname}≠{nm}")
check("S7 goto_page_name：逐个 (母模块, 子页下标) 跳转均正确",
      jump2_ok, "; ".join(jump2_msg) or "13/13 全部正确")

# ---------------- S8 CHART_PAGE 指向真实母模块 ----------------
group_names = set(expect_top)
bad = [(n, p) for n, p in NA.CHART_PAGE if p not in group_names]
check("S8 CHART_PAGE 的每个归属都是真实存在的母模块名",
      not bad, f"非法={bad}" if bad else "6/6 合法")
check("S8b 六张图各有归属且不重复",
      len(NA.CHART_PAGE) == 6
      and len({n for n, _ in NA.CHART_PAGE}) == 6,
      f"{[n for n, _ in NA.CHART_PAGE]}")

# ---------------- S9 图表控件创建 ----------------
win._materialize_charts()
charts = {"ch_fan": win.ch_fan, "ch_index": win.ch_index,
          "ch_flow": win.ch_flow, "ch_theme": win.ch_theme,
          "ch_lead": win.ch_lead, "ch_stage": win.ch_stage}
missing = [k for k, v in charts.items() if v is None]
check("S9 六张图表画布在 materialize 后全部创建",
      not missing, f"missing={missing}" if missing else "6/6 已创建")

# ---------------- S10 菜单快捷键 ----------------
menu = win.menuBar()
view = None
for a in menu.actions():
    if a.text().startswith("视图"):
        view = a.menu()
        break
ctrls = []
if view is not None:
    for a in view.actions():
        for sub in ([a] + (a.menu().actions() if a.menu() else [])):
            s = sub.shortcut().toString() if sub.shortcut() else ""
            if s.startswith("Ctrl+"):
                ctrls.append(s)
check("S10 视图菜单含 Ctrl+1..6 六个母模块快捷键",
      set(f"Ctrl+{i}" for i in range(1, 7)).issubset(set(ctrls)),
      f"{sorted(ctrls)}")

win.close()
try:
    win.worker = None
except Exception:
    pass

# ---------------- 汇总 ----------------
npass = sum(1 for _n, ok in RESULTS if ok)
nfail = len(RESULTS) - npass
print("-" * 72)
print(f"结果：{npass} 通过 / {nfail} 失败 / 共 {len(RESULTS)}")
if nfail:
    print("失败项：")
    for n, ok in RESULTS:
        if not ok:
            print(f"  · {n}")
sys.exit(1 if nfail else 0)
