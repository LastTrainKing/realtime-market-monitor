# -*- coding: utf-8 -*-
"""v3.0.2 补采标注 UI 端到端测试（离屏，不联网）。

验证：当一个节点是**补采**（on_time=False）时，
  · 该节点对应的卡片标题出现「… · HH:MM补采」后缀；
  · 准点节点（on_time=True）的标题**没有**任何后缀；
  · 缺失节点的标题保持原样，表格显示整行"该节点未采集"提示；
  · 个股精选标题在有补采时出现「（含补采数据）」。

★ 为什么必须做 UI 层测试：数据层对了不等于屏幕对了 ——
  中间还隔着 build_payload → fill_open_pages → _set_card_title 三层。
  本项目既有教训："数据层排好序 ≠ 屏幕上有序"。
"""
import importlib.util as ilu
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

spec = ilu.spec_from_file_location("native_app", os.path.join(HERE, "app_main_v203.py"))
NA = ilu.module_from_spec(spec)
sys.modules["native_app"] = NA
spec.loader.exec_module(NA)
NA.ensure_mpl()

from PySide6.QtWidgets import QApplication, QFrame, QLabel  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

FAILS = []
N = 0


def chk(cond, name, detail=""):
    global N
    N += 1
    if cond:
        print("  PASS %s" % name)
    else:
        print("  FAIL %s   %s" % (name, detail))
        FAILS.append(name)


# ---- 构造一份「m5 准点 / m10 补采 / m15 缺失」的快照 ----------------------
TMP = tempfile.mkdtemp(prefix="snaptest_")
TAG = "20260923"


def _node(captured_at, on_time, planned):
    secs = [{"c": "BK%03d" % i, "n": "合成板块%02d" % i,
             "v": 100000 - i * 1000, "a": 1e9 - i * 1e7, "net": 5e8 - i * 1e7}
            for i in range(60)]
    stks = [{"c": "00%04d" % i, "n": "合成股%02d" % i, "v": 90000 - i * 500,
             "a": 8e8 - i * 1e6, "lb": 9.0 - i * 0.3, "net": 4e8 - i * 1e7,
             "pct": 9.5 - i * 0.5} for i in range(60)]
    return {"minutes": 5, "label": "", "captured_at": captured_at,
            "on_time": on_time, "planned": planned,
            "sectors": secs, "stocks": stks}


snap = {"nodes": {
    "m5": _node("09:36:08", True, "09:36"),        # 准点
    "m10": _node("10:05:31", False, "09:41"),      # 补采
    # m15 缺失
}}
with open(os.path.join(TMP, "opensnap_%s.json" % TAG), "w", encoding="utf-8") as f:
    json.dump(snap, f, ensure_ascii=False)

# 让 flowdata.DATA 指向临时目录（opensnap.snap_path 默认取 flowdata.DATA）
import flowdata  # noqa: E402
flowdata.DATA = TMP
import opensnap  # noqa: E402
opensnap.flowdata = flowdata

win = NA.MainWindow()
win.resize(1480, 940)
win.show()
app.processEvents()

# 伪装成"当前数据日 = 测试 tag"
win.data = {"date_tag": TAG, "times": [], "sectors": []}
win.fill_open_pages()
app.processEvents()


def title_of(panel_id):
    for c in win.findChildren(QFrame):
        if str(c.property("panelId") or "") == panel_id:
            for lb in c.findChildren(QLabel):
                if lb.objectName() == "cardTitle":
                    return lb.text()
    return None


print("=" * 70)
print("v3.0.2 补采标注 UI 端到端测试")
print("=" * 70)

t = title_of("open_check.m5_vol")
chk(t == "开盘后5分钟 · 成交量 Top5", "准点节点标题无后缀", "实际=%r" % t)

t = title_of("open_check.m10_vol")
chk(t == "开盘后10分钟 · 成交量 Top5 · 10:05补采",
    "补采节点标题带真实采集时刻", "实际=%r" % t)

t = title_of("open_check.m10_net")
chk(t == "开盘后10分钟 · 主力净流入 Top5 · 10:05补采",
    "补采节点（净流入）也带标注", "实际=%r" % t)

t = title_of("open_check.m15_vol")
chk(t == "开盘后15分钟 · 成交量 Top5",
    "缺失节点标题保持原样（不误标补采）", "实际=%r" % t)

t = title_of("stock_move.m10_lb")
chk(t == "开盘后10分钟 · 量比 Top10 · 10:05补采",
    "个股异动补采节点带标注", "实际=%r" % t)

t = title_of("stock_pick.nine_boards")
chk(t == "个股精选 · 九榜出现次数排名（含补采数据）",
    "个股精选在有补采时标注", "实际=%r" % t)

# 表格内容检查
chk(win.t_open[("m5", "vol")].rowCount() == 5, "m5 板块表 5 行", "")
chk(win.t_open[("m15", "vol")].rowCount() == 1, "m15 未采集 → 单行提示", "")
_it = win.t_open[("m15", "vol")].item(0, 0)
chk(_it is not None and "未采集" in _it.text(),
    "m15 提示文案完整可读（非『该…』截断）",
    "实际=%r" % (_it.text() if _it else None))
chk(win.t_open[("m15", "vol")].columnSpan(0, 0) == 4,
    "m15 提示整行合并（columnSpan=4）", "")

# 准点节点在前端不被打扰：确认标题里没有"补采"二字
chk("补采" not in (title_of("open_check.m5_net") or ""),
    "准点节点标题不含『补采』", "")

shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "=" * 70)
print("结果：%d PASS / %d FAIL" % (N - len(FAILS), len(FAILS)))
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  · %s" % f)
print("=" * 70)
sys.exit(1 if FAILS else 0)
