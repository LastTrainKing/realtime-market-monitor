# -*- coding: utf-8 -*-
"""v3.0.1 面板注册表守恒校验 —— 「孙模块一个不少」的机器可验证形式。

校验三件事：
  P1 PANELS 表自身自洽（id 唯一、所属母模块与子模块在 TAB_GROUPS 里存在）
  P2 运行期实际挂载的卡片 panelId 集合 == PANELS 的 id 集合（sorted 相等）
  P3 每个子页的实挂面板集合 == 该子页在 PANELS 里登记的集合

用法（在 build/ 下）：
  <native_env>/Scripts/python.exe verify_v301_panels.py
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

PASS, FAIL = [], []


def chk(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name,
                         ("  ← " + detail) if (detail and not cond) else ""))


print("=" * 78)
print("v3.0.1 面板（孙模块）注册表守恒校验")
print("=" * 78)

# ---------------------------------------------------------------- P1 表自洽
print("\n[P1] PANELS 表自洽")
ids = [p[0] for p in NA.PANELS]
chk("P1.1 PANELS 条目数 == 40", len(NA.PANELS) == 40, str(len(NA.PANELS)))
chk("P1.2 panel id 无重复", len(ids) == len(set(ids)),
    "dup=%s" % sorted({x for x in ids if ids.count(x) > 1}))
chk("P1.3 PANEL_IDS 与 PANELS 一致", NA.PANEL_IDS == ids)
_groups = {g for g, _s in NA.TAB_GROUPS}
_subs = {s for _g, subs in NA.TAB_GROUPS for s in subs}
_bad_g = [p[0] for p in NA.PANELS if p[2] not in _groups]
_bad_s = [p[0] for p in NA.PANELS if p[3] is not None and p[3] not in _subs]
chk("P1.4 所有面板的母模块都存在于 TAB_GROUPS", not _bad_g, str(_bad_g))
chk("P1.5 所有面板的子模块都存在于 TAB_GROUPS", not _bad_s, str(_bad_s))
# 子模块 → 面板数应与其"应有面板数"一致
_expect_sub = {
    "总览": 1, "市场全景": 4, "开盘检测": 6, "个股异动": 9, "个股精选": 1,
    "异动雷达": 2, "辨识度榜": 2, "行业概况": 2, "实时榜单": 5,
    "题材聚簇": 4, "实时事件": 1, "主角更替": 1, "阶段对比": 1, "关键节点": 1,
}
for _k, _n in _expect_sub.items():
    _got = len(NA.PANELS_BY_SUB.get(_k, []))
    chk("P1.6 %s 登记 %d 个面板" % (_k, _n), _got == _n, "实得 %d" % _got)

# ---------------------------------------------------------------- P2 运行期 == PANELS
print("\n[P2] 运行期实际挂载 == PANELS 登记")
Win = NA.MainWindow
win = Win()

# ★ 为顶层页分类（实测控件树结构）：
#   · 「总览」= 直接是 QWidget，其下就是卡片 → 数它
#   · 其余 5 个 = **母模块外壳 QWidget**，内部还嵌着一个 QTabWidget
#     （group_tabs[母模块]），真正的子页在那个 QTabWidget 里。
#     对外壳页调 findChildren 会把**它内部全部子页**的卡片一起数进来
#     （实测 大盘=10 而不是 4+6 分开），于是总数从 40 涨到 79 ——
#     属于"校验逻辑自己算重了"，必须与"真有面板重复挂载"区分开。
#   判据：母模块外壳页 = 内部含 group_tabs 中某个 QTabWidget 的那个页。
shells = set()
for _g, _sub in win.group_tabs.items():
    shells.add(id(_sub))
found = []
for i in range(win.tabs.count()):
    pg = win.tabs.widget(i)
    if pg is None:
        continue
    # 只数「不含任何 group_tabs 子页签」的顶层页（即总览）
    if any(id(s) in shells for s in pg.findChildren(type(next(iter(win.group_tabs.values()))))):
        continue
    for c in pg.findChildren(QFrame):
        pid = c.property("panelId")
        if pid:
            found.append(str(pid))
for g, sub in win.group_tabs.items():
    for i in range(sub.count()):
        pg = sub.widget(i)
        if pg is None:
            continue
        for c in pg.findChildren(QFrame):
            pid = c.property("panelId")
            if pid:
                found.append(str(pid))

chk("P2.1 运行期面板数 == 40", len(found) == 40, "实得 %d" % len(found))
chk("P2.2 无重复挂载", len(found) == len(set(found)),
    "dup=%s" % sorted({x for x in found if found.count(x) > 1}))
chk("P2.3 集合完全相等（一个不少、一个不多）",
    sorted(found) == sorted(NA.PANEL_IDS),
    "missing=%s extra=%s" % (
        [x for x in NA.PANEL_IDS if x not in found],
        [x for x in found if x not in NA.PANEL_IDS]))

# ---------------------------------------------------------------- P3 逐子页
print("\n[P3] 逐子页面板集合核对")
for g, sub in win.group_tabs.items():
    for i in range(sub.count()):
        nm = sub.tabText(i)
        pg = sub.widget(i)
        got = sorted(str(c.property("panelId")) for c in pg.findChildren(QFrame)
                     if c.property("panelId"))
        want = sorted(NA.PANELS_BY_SUB.get(nm, []))
        chk("P3 %s / %s" % (g, nm), got == want,
            "got=%s want=%s" % (got, want))
# 总览
for i in range(win.tabs.count()):
    if win.tabs.tabText(i) == "总览":
        pg = win.tabs.widget(i)
        got = sorted(str(c.property("panelId")) for c in pg.findChildren(QFrame)
                     if c.property("panelId"))
        want = sorted(NA.PANELS_BY_SUB.get("总览", []))
        chk("P3 总览 / 总览", got == want, "got=%s want=%s" % (got, want))

print("\n" + "=" * 78)
print("结果：%d PASS / %d FAIL" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  ·", f)
print("=" * 78)
sys.exit(1 if FAIL else 0)
