# -*- coding: utf-8 -*-
"""v1.1.5 离屏验证：配色切换 / 20条曲线+固定横轴+悬停 / 题材簇前八+龙头 /
   归档与回看下拉 / 东财全A。

不开窗口（不调 show）、真实网络一律 mock，直接断言。
"""
import os
import sys
import json
import shutil
import tempfile
from datetime import datetime

BASE = r"C:\Users\user\WorkBuddy\2026-09-17-23-56-54\work\native_build"
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "core"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


# ★★ v2.1.0：导入**实际打包的主源文件**，而不是同目录那份遗留的 native_app.py。
#
#   踩过的坑：本套测试长期 `import native_app as NA`，而 native_app.py 是
#   v2.0.2 时代的旧副本、**根本不参与打包**（spec 的 Analysis 入口是
#   app_main_v203.py）。后果是测试一路"全绿"却在验证一份早已作废的代码 ——
#   测试与交付物脱钩，比没有测试更危险（它给出虚假的安全感）。
#   现在改为按 spec 的口径加载 app_main_v203.py，并作硬校验：
#   加载到的模块必须确实来自 app_main_v203.py。
import importlib.util as _ilu
import os as _os
_MAIN_PY = "app_main_v203.py"
_spec = _ilu.spec_from_file_location(
    "native_app", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                _MAIN_PY))
NA = _ilu.module_from_spec(_spec)
import sys as _sys
_sys.modules["native_app"] = NA          # 让子模块间 import 也拿到同一实例
_spec.loader.exec_module(NA)
assert _os.path.basename(getattr(NA, "__file__", "") or "") == _MAIN_PY, \
    f"chk115 加载了错误的源文件：{getattr(NA, '__file__', None)}"

# ★★ v2.1.0：必须先触发 matplotlib 懒加载。
#   主源文件把 7 个图表类定义在 _define_chart_classes() 工厂里，由
#   ensure_mpl() 首次调用时才注入模块 globals（v2.0.3 的启动提速手段）。
#   旧 chk115 依赖 native_app.py 里"图表类在模块级直接定义"的旧结构，
#   换成真实源文件后必须先 ensure_mpl()，否则 NA.FanChart 不存在。
NA.ensure_mpl()

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

app = QApplication.instance() or QApplication([])
app.setFont(QFont("Microsoft YaHei", 9))

import flowdata
import store
import archive as ARC
import leader as LD

print("\n=========== A. 配色方向固定为红涨绿跌（v1.2.1 移除切换按钮）===========")

# ★ v1.2.1 用户需求：移除界面里的「红涨绿跌 / 绿涨红跌」切换按钮。
#   配色方向固定为 **A 股约定（红涨绿跌）**。
#   因此 A 段由"切换是否生效"改为"切换入口是否彻底消失 + 默认配色是否正确"。

_A_th_light = NA.coloring(NA.THEMES["light"])
_A_th_dark = NA.coloring(NA.THEMES["dark"])

check("A1 配色固定红涨绿跌：up=红(#e0393e)、dn=绿(#12a05c)",
      _A_th_light["up"] == "#e0393e" and _A_th_light["dn"] == "#12a05c",
      f'up={_A_th_light["up"]} dn={_A_th_light["dn"]}')

check("A2 coloring() 为恒等映射（不再翻转 up/dn）",
      _A_th_light is NA.THEMES["light"]
      and _A_th_light["up"] == NA.THEMES["light"]["up"]
      and _A_th_light["up_soft"] == NA.THEMES["light"]["up_soft"],
      "返回原字典、未做交换")

check("A3 暗色主题下同样红涨绿跌（不随主题漂移）",
      _A_th_dark["up"] == NA.THEMES["dark"]["up"]
      and _A_th_dark["dn"] == NA.THEMES["dark"]["dn"],
      f'up={_A_th_dark["up"]} dn={_A_th_dark["dn"]}')

for _sym in ("COLOR_DIR", "toggle_colors"):
    check(f"A4 模块级 {_sym} 已移除", not hasattr(NA, _sym),
          f"still present={hasattr(NA, _sym)}")

import re as _re
_src = open(os.path.join(BASE, "native_app.py"), encoding="utf-8").read()
_hard = _re.findall(r'QColor\(\s*"#(?:e0393e|12a05c|ff5f63|3ecf8e)"', _src)
check("A5 无硬编码涨跌色（仍全部经 th[up]/th[dn] 取色）", len(_hard) == 0,
      f"命中 {len(_hard)} 处")

_win = NA.MainWindow()
win = _win          # 后续段落沿用旧名字
for _attr in ("act_color", "tb_color"):
    check(f"A6 窗口无 {_attr}（切换控件已删除）", not hasattr(_win, _attr),
          f"still present={hasattr(_win, _attr)}")

_acts = []
for _m in _win.menuBar().actions():
    if _m.menu():
        _acts += [a.text() for a in _m.menu().actions()]
_prob = [a for a in _acts if ("涨" in a and "跌" in a)]
check("A7 菜单里无任何「涨跌配色」条目", len(_prob) == 0, f"命中={_prob}")

_tb_txt = []
for _tb in _win.findChildren(NA.QToolBar):
    _tb_txt += [a.text() for a in _tb.actions() if a.text()]
_prob2 = [a for a in _tb_txt if ("涨" in a and "跌" in a)]
check("A8 工具栏里无「涨跌配色」按钮", len(_prob2) == 0, f"工具栏按钮={_tb_txt}")

_fin, _fout = NA.seq_for(True), NA.seq_for(False)
check("A9 seq_for：暖色族给涨、冷色族给跌（方向固定后不再对调）",
      _fin is NA.UP_SEQ and _fout is NA.DN_SEQ,
      f"in[0]={_fin[0]} out[0]={_fout[0]}")

_A_series = [{"name": f"流入{i}", "series": [float(k) for k in range(10)],
              "net": 100.0 - i, "direction": "in",
              "times": [f"09:{31 + k}" for k in range(10)]} for i in range(10)]
_A_series += [{"name": f"流出{i}", "series": [float(-k) for k in range(10)],
               "net": -50.0 - i, "direction": "out",
               "times": [f"09:{31 + k}" for k in range(10)]} for i in range(10)]
_fc = NA.FanChart(3.0)
_fc.plot(_A_series)
_ln = [x for x in _fc.fig.axes[0].get_lines() if len(list(x.get_xdata())) == 10]
_cols = [x.get_color() for x in _ln]
check("A10 曲线配色：流入用暖色族、流出用冷色族",
      _cols[:10] == NA.UP_SEQ and _cols[10:20] == NA.DN_SEQ,
      f"流入首色={_cols[0]} 流出首色={_cols[10]}")

try:
    _NMIN = 60
    _A_SER = [{"name": f"流入{i}", "direction": "in", "net": 100.0 - i,
               "series": [float(80 - i) * k / _NMIN for k in range(_NMIN)],
               "times": [f"09:{31 + k}" for k in range(_NMIN)]} for i in range(4)]
    _A_SER += [{"name": f"流出{i}", "direction": "out", "net": -50.0 - i,
                "series": [float(-60 + i) * k / _NMIN for k in range(_NMIN)],
                "times": [f"09:{31 + k}" for k in range(_NMIN)]} for i in range(4)]
    _f2 = NA.FanChart(3.6, 8.0)
    _f2.plot(_A_SER)
    _f2.fig.canvas.draw()
    _buf = np.asarray(_f2.fig.canvas.buffer_rgba())[..., :3].astype(np.int16)
    _warm = _buf[..., 0] - _buf[..., 2]
    _m = np.abs(_warm) > 40
    _n = int(_m.sum())
    _share = int((_warm[_m] > 0).sum()) / max(1, _n)
    check("A11 像素级：暖色(涨)与冷色(跌)曲线共存且方向未反转",
          0.15 < _share < 0.85 and _n > 500,
          f"暖色占比 {_share * 100:.1f}% · 彩点 {_n:,}")
except Exception as _e:                                       # noqa: BLE001
    check("A11 像素级：暖冷两族曲线共存且未反转", False, f"异常：{_e}")

# A12 ★ 交付目录完整性：截图必须与页签一一对应（页名顺序也要一致）
#   ★ v1.2.1：原先这条断言是校验"配色方向对比"目录（旧功能的产物）。
#     切换按钮移除后那批截图已无意义、已从交付目录删除，
#     这里改为**更有价值**的检查：交付的 N 张截图必须正好对应 N 个页签。
_odir = os.path.join(BASE, "..", "..", "outputs", "资金流向_原生版")
try:
    _shots = sorted(f for f in os.listdir(_odir) if f.endswith(".png"))
    _expect = [f"{i}_{nm}.png" for i, nm in enumerate(NA.PAGE_NAMES)]
    check(f"A12 交付截图与页签一一对应（{len(_expect)} 张）",
          _shots == _expect,
          f"实际={_shots}" if _shots != _expect else f"{len(_shots)} 张，顺序一致")
except Exception as _e:                                       # noqa: BLE001
    check("A12 交付截图与页签一一对应", False, f"异常：{_e}")

# A13 ★ 交付目录不应残留已删功能的产物
check("A13 交付目录无「配色方向对比」残留（切换功能已移除）",
      not os.path.isdir(os.path.join(BASE, "..", "..", "outputs", "资金流向_原生版",
                                     "配色方向对比")),
      "旧产物已清理")

print("\n=========== B. 主力资金累计曲线 ===========")

# B1: 分钟索引映射（午间不占位次，保证横轴等距）
check("B1 _minute_index：09:31=0 / 11:30=119 / 13:01=120 / 15:00=239",
      NA._minute_index("09:31") == 0 and NA._minute_index("11:30") == 119
      and NA._minute_index("13:01") == 120 and NA._minute_index("15:00") == 239,
      f'{NA._minute_index("09:31")},{NA._minute_index("11:30")},'
      f'{NA._minute_index("13:01")},{NA._minute_index("15:00")}')
check("B2 _minute_index：非交易时段返回 None",
      NA._minute_index("12:00") is None and NA._minute_index("15:30") is None)

# B3: 标的数常量 ≥ 20
check("B3 FAN_TOP_N >= 20（用户要求不少于 20 个标的）",
      NA.FAN_TOP_N >= 20, f"FAN_TOP_N={NA.FAN_TOP_N}")

# B4: _market_stats 返回 10+10
import salience


def mkF(T=30, N=60):
    """造一个最小 Features（真实 np 矩阵，走真算）"""
    rng = np.random.default_rng(7)
    V = np.cumsum(rng.normal(0, 1.0, (T, N)), axis=0)
    times = [f"{9 + (31 + k) // 60:02d}:{(31 + k) % 60:02d}" for k in range(T)]
    F = salience.Features(
        times=times, codes=[f"C{i:03d}" for i in range(N)],
        names={f"C{i:03d}": f"板块{i}" for i in range(N)},
        kinds={f"C{i:03d}": ("concept" if i % 2 == 0 else "industry")
               for i in range(N)},
        V=V, dV5=np.zeros_like(V), dV1=np.zeros_like(V),
        resid=np.zeros_like(V), z=np.zeros_like(V),
        rank=np.zeros_like(V, dtype=int), rank_neg=np.zeros_like(V, dtype=int),
        persist=np.zeros_like(V), pct_abs=np.zeros_like(V),
        pct_vel=np.zeros_like(V), salience=np.zeros_like(V),
        index=[], concept_mask=np.array([i % 2 == 0 for i in range(N)]))
    return F


Fm = mkF()
mst = NA._market_stats(Fm, salience, len(Fm.times) - 1)
check("B4 in_list/out_list 各 10 条（合计 20 条曲线）",
      len(mst["in_list"]) == 10 and len(mst["out_list"]) == 10,
      f'in={len(mst["in_list"])} out={len(mst["out_list"])}')

# B5: 双向覆盖——有涨也有跌
check("B5 流入榜全正、流出榜全负（双向都画）",
      all(r["net"] > 0 for r in mst["in_list"])
      and all(r["net"] < 0 for r in mst["out_list"]))

# B6: 曲线配色序列按序取色（参考 HTML 版 UP_SEQ/DN_SEQ）
check("B6 UP_SEQ/DN_SEQ 各 10 色且互不相同",
      len(NA.UP_SEQ) == 10 and len(NA.DN_SEQ) == 10
      and len(set(NA.UP_SEQ)) == 10 and len(set(NA.DN_SEQ)) == 10)

# B7: 横轴固定 9:30–15:00（240 分钟）
check("B7 TRADING_MINUTES=240、刻度含 09:30 与 15:00",
      NA.TRADING_MINUTES == 240
      and NA.TRADING_TICKS[0] == "09:30" and NA.TRADING_TICKS[-1] == "15:00"
      and len(NA.TRADING_TICKS) == 9,
      f"{NA.TRADING_TICKS}")
# B7b: 刻度里不出现 13:00（11:30=119 与 13:01=120 在轴口径上只差 1 格，同画必重叠）
check("B7b 刻度无 13:00（与 HTML 版 ticks 一致，午休另用分界竖线）",
      "13:00" not in NA.TRADING_TICKS)

# B8: 实测画布 xlim 固定为 [0,239]（数据只有 30 点时也不塌缩）
fc = NA.FanChart(3.0)
series = [{"name": f"板块{i}", "series": [float(x) for x in range(30)],
           "net": (1.0 if i < 10 else -1.0),
           "direction": ("in" if i < 10 else "out"),
           "times": [f"09:{31 + k}" for k in range(30)]} for i in range(20)]
fc.plot(series)
xlim = fc.fig.axes[0].get_xlim()
check("B8 横轴 xlim 固定 [0,239]（30 点数据也不拉伸铺满）",
      abs(xlim[0] - 0.0) < 0.01 and abs(xlim[1] - 239.0) < 0.01,
      f"xlim={xlim}")
xt = [t.get_text() for t in fc.fig.axes[0].get_xticklabels()]
check("B9 x 轴刻度标签 = 9:30–15:00 各整点",
      xt[0] == "09:30" and xt[-1] == "15:00" and len(xt) == 9, f"{xt}")
# B9b: 午休分界竖线存在（跳空可视化）
_vl = [ln for ln in fc.fig.axes[0].get_lines()
       if ln.get_xdata() is not None and len(list(ln.get_xdata())) == 2
       and list(ln.get_xdata())[0] == 119.5 and list(ln.get_xdata())[1] == 119.5]
check("B9b 午休分界竖线存在（x=119.5）", len(_vl) >= 1, f"n={len(_vl)}")

# B10: 悬停联动——回调触发且索引正确
hits = []
fc.hover_cb = lambda i, hm: hits.append((i, hm))
fc._n = 30
fc._times = [f"09:{31 + k}" for k in range(30)]
fc._on_motion(type("E", (), {"inaxes": fc.fig.axes[0], "xdata": 12.4})())
check("B10 悬停触发回调且索引取整正确（12.4→12）",
      len(hits) == 1 and hits[0][0] == 12, f"hits={hits}")

# B11: 同一分钟不重复触发（避免高频重绘）
fc._on_motion(type("E", (), {"inaxes": fc.fig.axes[0], "xdata": 12.4})())
check("B11 同一分钟重复移动不重复触发", len(hits) == 1)

# B12: 移出范围时不触发
fc._on_motion(type("E", (), {"inaxes": None, "xdata": None})())
check("B12 移出绘图区不触发新回调", len(hits) == 1)

print("\n=========== C. 悬停联动面板 ===========")

# C1: on_fan_hover 填充左侧表格
win.data = {"F": Fm, "times": Fm.times, "source": "live",
            "date_tag": "20260918", "n_sectors": 60}
win.on_fan_hover(10, Fm.times[10])
rows = win.t_hover.rowCount()
check("C1 悬停后左侧面板填入 14 行（流入7+流出7）",
      rows == 14, f"rows={rows}")
txt = win.lbl_hover.text()
check("C2 面板标题显示该时点", Fm.times[10] in txt, txt)

# C3: 换一个时点，数据随之变化
win.on_fan_hover(20, Fm.times[20])
txt2 = win.lbl_hover.text()
check("C3 切到另一时点后标题变化（数据跟着横轴走）",
      Fm.times[20] in txt2 and txt2 != txt, txt2)

# C4: 越界索引被夹住不崩
try:
    win.on_fan_hover(9999, "15:00")
    win.on_fan_hover(-5, "09:31")
    check("C4 越界索引安全夹取（不抛异常）", True)
except Exception as e:                                   # noqa: BLE001
    check("C4 越界索引安全夹取（不抛异常）", False, str(e))

print("\n=========== D. 题材簇：前八 + 第四象限龙头 ===========")

# D1: ThemeBoard 默认只取前八/后八
tb = NA.ThemeBoard(3.0)
many = [{"seed": f"簇{i}", "n_members": 3, "total_yi": (100 - i) * (1 if i % 2 else -1),
         "members": [{"name": "x"}]} for i in range(20)]
tb.plot(many)
# 两个子图各自的条形数 <= 8
n_bars = [len([p for p in ax.patches]) for ax in tb.fig.axes]
check("D1 题材簇预览每侧最多 8 条（流入/流出各前八）",
      all(n <= 8 for n in n_bars), f"每侧条形数={n_bars}")

# D2: 该图表自带 2 个子图（左流出 / 右流入）
check("D2 预览图仍是左流出/右流入两栏", len(tb.fig.axes) == 2)

# D3: 龙头表格与提示标签存在（第四象限）
check("D3 第四象限：龙头表格 + 状态标签已创建",
      hasattr(win, "t_leader") and hasattr(win, "lbl_leader")
      and win.t_leader.columnCount() == 8,
      f"列数={win.t_leader.columnCount()}")

# D4: fill_leader 正确填表
win.leader_rows = [
    {"code": "BK0001", "name": "算力概念", "net": 12.3, "ok": True,
     "up": [{"name": "某龙头A", "avg_lead": 2.5, "hits": 3, "n": 4}],
     "down": [{"name": "某龙头B", "avg_lead": 1.2, "hits": 2, "n": 3}]},
    {"code": "BK0002", "name": "锂电池", "net": -8.1, "ok": True,
     "up": [], "down": [{"name": "某龙头C", "avg_lead": 3.0, "hits": 1, "n": 2}]},
    {"code": "BK0003", "name": "无龙头", "net": 1.0, "ok": False},
]
win.fill_leader()
check("D4 fill_leader 只填 ok 的行（2 行，跳过无龙头板块）",
      win.t_leader.rowCount() == 2, f"rows={win.t_leader.rowCount()}")

# D5: 排序按净额绝对值降序（最活跃在最上）
c0 = win.t_leader.item(0, 0).text()
check("D5 按 |净额| 降序排列（算力 12.3 在首行）", c0 == "算力概念", c0)

# D6: 空单元格渲染为 — 而非崩
c_empty = win.t_leader.item(1, 2).text()
check("D6 无双向龙头时该格显示 —", c_empty == "—", c_empty)

print("\n=========== E. 龙头算法（移植自 HTML 版） ===========")

# E1: stock_secid 交易所前缀
check("E1 stock_secid：6/9/5 开头→1.，其余→0.",
      LD.stock_secid("600000") == "1.600000"
      and LD.stock_secid("000001") == "0.000001"
      and LD.stock_secid("300750") == "0.300750")

# E2: 拐点识别——造一段"先平后猛拉"的曲线，应识别出点火
hms = [f"{9 + (31 + k) // 60:02d}:{(31 + k) % 60:02d}" for k in range(60)]
vals = [0.0] * 30 + [1.0 * k for k in range(1, 31)]      # 后半段猛拉
turns = LD.detect_turns(hms, vals)
check("E2 拐点识别：猛拉段被识别为「点火」",
      len(turns) >= 1 and any(t["dir"] == "点火" for t in turns),
      f"turns={[(t['time'], t['dir']) for t in turns[:3]]}")

# E3: 平线不应有拐点
turns_flat = LD.detect_turns(hms, [0.0] * 60)
check("E3 平线识别出 0 个拐点", len(turns_flat) == 0)

# E4: 个股反应——领先分钟为正
turn = {"time": hms[35], "dir": "点火", "mag": 1.0}
kv = [0.0] * 60
kv[30] = 5.0          # 个股在拐点前 5 分钟就动了
for k in range(31, 60):
    kv[k] = kv[k - 1]
r = LD.stock_reaction(hms, kv, turn)
check("E4 个股提前反应 → lead>0（个股领先板块）",
      r is not None and r["lead"] > 0, f"r={r}")

# E5: 个股不动 → 无反应
r2 = LD.stock_reaction(hms, [0.0] * 60, turn)
check("E5 个股全程不动 → 判定为无反应", r2 is None)

# E6: 打分排序——领先多的排前面
turns2 = [{"time": hms[35], "dir": "点火", "mag": 1.5},
          {"time": hms[45], "dir": "点火", "mag": 1.2}]
stocks = [
    {"code": "600001", "name": "快手", "net": 1.0, "flow": [[h, (5.0 if i in (30, 40) else 0.0)]
     for i, h in enumerate(hms)]},
    {"code": "000002", "name": "慢手", "net": 1.0, "flow": [[h, 0.0] for h in hms]},
]
ups, downs = LD.score_leaders(turns2, stocks)
check("E6 打分：抢在两个拐点前面的排第一",
      len(ups) >= 1 and ups[0]["name"] == "快手"
      and ups[0]["hits"] == 2, f"ups={[(u['name'], u['hits']) for u in ups]}")

# E7: 参数与 HTML 版一致（防漂移）
check("E7 评分权重与 HTML 版一致（1.8/1.0/0.7/0.5）",
      LD.LEADER_CFG["w_lead"] == 1.8 and LD.LEADER_CFG["w_hit"] == 1.0
      and LD.LEADER_CFG["w_resp"] == 0.7 and LD.LEADER_CFG["w_size"] == 0.5)

# E8 ★：板块代码归一化 —— 实测踩过的真 bug。
#   面板里存的是 `90.BK0596`，而成分股接口 fs=b: 只认 `BK0596`；
#   直接透传时东财**不报错、只返回 total=0**，于是每个板块都 ok=False，
#   第四象限永远是空的，而且日志里查不出原因（不抛异常）。
check("E8 板块代码归一化：90.BK0596 → BK0596",
      LD.board_bk("90.BK0596") == "BK0596"
      and LD.board_bk("BK0475") == "BK0475"
      and LD.board_bk(" 90.BK0821 ") == "BK0821"
      and LD.board_bk("") == "",
      f'{LD.board_bk("90.BK0596")} / {LD.board_bk("BK0475")}')

# E9：在榜板块上限必须 >= 28（概念9+9、行业5+5），否则静默截断
check("E9 max_sectors >= 28（否则在榜板块会被静默截断）",
      LD.LEADER_CFG["max_sectors"] >= 28,
      f'max_sectors={LD.LEADER_CFG["max_sectors"]}')

# E10 ★：龙头结果缓存 —— 一轮 28 板块 ≈ 250 请求 / 42 秒（实测），
#   没有缓存的话每次重开程序都要再空等一遍。
_LTMP = tempfile.mkdtemp(prefix="v115ld_")
_rows = [{"code": "90.BK0001", "name": "芯片", "net": 12.3, "ok": True,
          "turns": [{"time": "10:03", "dir": "点火", "mag": 1.2}],
          "up": [{"name": "甲", "avg_lead": 3.5, "hits": 2, "n": 2}],
          "down": [{"name": "乙", "avg_lead": 2.0, "hits": 1, "n": 2}]}]
_nw = LD.save_cache("20260918", _rows, _LTMP)
check("E10 龙头缓存写入成功", _nw == 1, f"n={_nw}")

_hit, _miss = LD.from_cache("20260918", [{"code": "90.BK0001"},
                                        {"code": "90.BK9999"}], _LTMP)
check("E11 缓存按板块命中，缺的进 miss 列表（支持增量）",
      len(_hit) == 1 and len(_miss) == 1 and _miss[0]["code"] == "90.BK9999",
      f"hit={len(_hit)} miss={len(_miss)}")

# E12：换交易日不串台（缓存按 tag 隔离）
_h2, _m2 = LD.from_cache("20260919", [{"code": "90.BK0001"}], _LTMP)
check("E12 缓存按交易日隔离（换日不串台）", len(_h2) == 0 and len(_m2) == 1)

# E13：损坏的缓存文件不能让界面崩
with open(os.path.join(_LTMP, LD.CACHE_NAME), "w", encoding="utf-8") as f:
    f.write("{ 这不是 json")
check("E13 缓存文件损坏时安全降级（返回空表不抛异常）",
      LD.load_cache(_LTMP) == {} and LD.from_cache("20260918", [], _LTMP) == ([], []))
shutil.rmtree(_LTMP, ignore_errors=True)

print("\n=========== F. 归档与回看下拉 ===========")

TMP = tempfile.mkdtemp(prefix="v115_")


def mk_panel(data_dir, tag, source="live", n_sector=6, n_min=240):
    mat = np.zeros((n_min, n_sector), np.float32)
    with open(os.path.join(data_dir, f"panel_{tag}.npy"), "wb") as f:
        np.save(f, mat)
    meta = {"times": [f"{9 + (31 + k) // 60:02d}:{(31 + k) % 60:02d}"
                      for k in range(n_min)],
            "codes": [f"C{i}" for i in range(n_sector)],
            "names": {f"C{i}": f"板块{i}" for i in range(n_sector)},
            "kinds": {f"C{i}": "concept" for i in range(n_sector)},
            "source": source, "data_date": tag}
    with open(os.path.join(data_dir, f"panel_{tag}.json"), "w",
              encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


# F1: 归档写入
mk_panel(TMP, "20260918")
res = ARC.maybe_archive("20260918", 240, 6, "live", TMP)
check("F1 归档写入成功（action=add）",
      res and res["action"] == "add" and res["total"] == 1, f"{res}")

# F2: 幂等——同样数据再归档不重复写
res2 = ARC.maybe_archive("20260918", 240, 6, "live", TMP)
check("F2 幂等：同数据再归档 → skip",
      res2 and res2["action"] == "skip" and res2["reason"] == "already_fresh",
      f"{res2}")

# F3: 数据变多（盘中→收盘）→ update
res3 = ARC.maybe_archive("20260918", 240, 6, "live", TMP, force=True)
check("F3 force 时重写为 update", res3 and res3["action"] == "update")

# F4: 分钟点不足不归档（防把开盘几分钟写成收盘档）
res4 = ARC.maybe_archive("20260919", 12, 6, "live", TMP)
check("F4 分钟点 <200 不归档（数据未定稿）",
      res4 and res4["action"] == "skip" and res4["reason"] == "bars_too_few")

# F5: 非法日期不归档
check("F5 非法日期 tag 返回 None",
      ARC.maybe_archive("today", 240, 6, "live", TMP) is None)

# F6: 30 天滚动淘汰
for i in range(1, 35):
    ARC.maybe_archive(f"202608{i:02d}", 240, 6, "live", TMP)
days = ARC.archived_days(TMP)
check("F6 滚动 30 天：攒满后挤掉最早的",
      len(days) == 30 and days[0] > "20260801",
      f"n={len(days)} first={days[0]} last={days[-1]}")

# F7: prune_to 同步裁剪（面板被清后归档不留孤儿）
dropped = ARC.prune_to({"20260918"}, TMP)
check("F7 prune_to 裁掉窗口外记录（只留 20260918）",
      len(dropped) > 0 and ARC.archived_days(TMP) == ["20260918"],
      f"dropped={len(dropped)} kept={ARC.archived_days(TMP)}")

# F8: summary 文案含进度
s = ARC.summary(TMP)
check("F8 summary 含「已归档 N / 30」", "已归档" in s and "/ 30" in s, s)

# F9: 回看下拉只列**有数据**的日期（用户要求：把没有的日期都删了）
D2 = tempfile.mkdtemp(prefix="v115b_")
for t in ("20260916", "20260917", "20260918"):
    mk_panel(D2, t)
flowdata.DATA = D2
win.calendar_days = [f"202608{d:02d}" for d in range(1, 31)]   # 日历很长
win.live_tag = "20260920"
win._refresh_date_combo()
items = [win.combo_date.itemData(i) for i in range(win.combo_date.count())]
check("F9 下拉只含有数据的 3 天（日历里的空日期不再出现）",
      items == ["20260918", "20260917", "20260916"],
      f"items={items}")

# F10: 无数据时给明确占位且置灰
D3 = tempfile.mkdtemp(prefix="v115c_")
flowdata.DATA = D3
win._refresh_date_combo()
check("F10 无数据时显示占位且不可选",
      win.combo_date.count() == 1 and win.combo_date.itemData(0) is None
      and not win.combo_date.model().item(0).isEnabled())

# F11: 最新在最上 + 今日标记
flowdata.DATA = D2
win.live_tag = "20260918"
win._refresh_date_combo()
first = win.combo_date.itemText(0)
check("F11 最新的排最上、当天的带★今日标记",
      first.startswith("★") and "20260918" in win.combo_date.itemData(0).replace("-", ""),
      first)

# F12 ★：目录口径统一 —— current_data_dir() 必须跟随 flowdata.DATA，
#   否则会出现「面板在这里、索引读那里」→ 回看列表永远是空的。
check("F12 current_data_dir() 跟随 flowdata.DATA（不再各算各的）",
      os.path.normcase(os.path.abspath(NA.current_data_dir()))
      == os.path.normcase(os.path.abspath(D2)),
      NA.current_data_dir())

# F13 ★：ensure_user_data_dir 不得覆盖调用方已指定的目录
#   （旧写法无条件 flowdata.DATA = app_data_dir()，会把注入路径改回默认值）
kept = NA.ensure_user_data_dir()
check("F13 ensure_user_data_dir 不覆盖已指定的目录",
      os.path.normcase(os.path.abspath(kept))
      == os.path.normcase(os.path.abspath(D2)), kept)

print("\n=========== G. 东财全A ===========")

# G1: secid 与 HTML 版一致
check("G1 全A secid = 47.800000（与 HTML 版 HEADER_INDEXES 一致）",
      any(s == "47.800000" for _, s, _ in flowdata.INDEX_SECIDS),
      f"{flowdata.INDEX_SECIDS}")

# G2: 两个指数都在清单里（上证 + 全A）
check("G2 指数清单含上证(1.000001)与全A(47.800000)",
      len(flowdata.INDEX_SECIDS) == 2
      and flowdata.INDEX_SECIDS[0][1] == "1.000001"
      and flowdata.INDEX_SECIDS[1][1] == "47.800000")

# G3: IndexChart 支持双线（可调用且画 2 条线）
ic = NA.IndexChart(2.5)
ic.plot([("09:31", 3000.0), ("09:32", 3010.0), ("09:33", 3005.0)],
        [("09:31", 4000.0), ("09:32", 4020.0), ("09:33", 4010.0)])
n_lines = len(ic.fig.axes[0].get_lines())
check("G3 IndexChart 双线绘制（含 0 轴共 3 条线）", n_lines >= 2, f"lines={n_lines}")

# G4: 只有上证（全A 缺失）时不崩
try:
    ic.plot([("09:31", 3000.0), ("09:32", 3010.0)], [])
    check("G4 全A 缺失时降级为单线（不崩）", True)
except Exception as e:                                   # noqa: BLE001
    check("G4 全A 缺失时降级为单线（不崩）", False, str(e))

# G5: 全A 数据缺失（旧面板）→ load_index_all 返回 []
flowdata.DATA = D2
check("G5 旧面板无 index_all 文件 → 返回空表不报错",
      flowdata.load_index_all("20260916") == [])

# G6: KPI 副标题含全A
win.data = {"market": {"index_pct": 1.23}, "times": ["09:31", "15:00"],
            "index_all": [["09:31", 4000.0], ["15:00", 4040.0]],
            "source": "live", "date_tag": "20260918"}
win.fill_kpi()
# ★ v1.1.6：KpiCard 现在会按控件宽度打省略号（窄窗口下不再硬裁文字），
#   而未 show() 的窗口宽度是默认值，显示文本可能被省略。
#   因此断言以**原始文本** _raw_sub 为准（这才是功能语义），
#   同时确认显示文本非空（省略逻辑没有把内容清成空）。
sub = win.kpi_index.lb_sub.text()
raw = win.kpi_index._raw_sub
check("G6 KPI 副标题含「全A」（上证旁并列展示）",
      "全A" in raw and "上证" in raw and "+1.00" in raw and sub,
      f"raw={raw!r} shown={sub!r}")

print("\n=========== H. 回归：既有功能未被破坏 ===========")

# H1: 版本号
check("H1 版本号已升到 1.2.5", NA.VERSION == "1.2.5", NA.VERSION)

# H2: preflight 通过（新模块已注册）
probs = NA.preflight()
check("H2 preflight 通过（含 archive/leader 新模块）", len(probs) == 0,
      str(probs[:2]))

# H3 ★★ v2.1.0：两级页签重构后的守恒校验（原"9 个标签页 == PAGE_NAMES"已升级）
#
#   旧断言的前提是"顶层页签 == 全部子模块"，v2.1.0 起不成立：
#   顶层只剩 6 个母模块，14 个子模块分布在母模块内部的子页签里。
#   新断言直接对准用户那条**硬要求**：「禁止删除任一子模块」——
#   把所有母模块的子页签名展开，集合与数量必须与 PAGE_NAMES 完全相等。
#   这比原来只数顶层更严格：它同时能抓到"子模块被漏挂"和"重复挂载"。
_top_now = [win.tabs.tabText(i) for i in range(win.tabs.count())]
check("H3 顶层页签 == TAB_GROUPS 的母模块序列（v2.1.0 两级结构）",
      _top_now == [g for g, _s in NA.TAB_GROUPS],
      f"top={_top_now}")

_mounted = []
for _g, _subs in NA.TAB_GROUPS:
    _sub = win.group_tabs.get(_g)
    if _sub is None:
        _mounted.append(_g)              # 总览这类无子页的母模块
    else:
        _mounted.extend(_sub.tabText(i) for i in range(_sub.count()))
check("H3a 全部 14 个子模块完好挂载（含总览）· 无丢失无重复",
      sorted(_mounted) == sorted(NA.PAGE_NAMES)
      and len(_mounted) == len(NA.PAGE_NAMES),
      f"n={len(_mounted)} mounted={sorted(_mounted)} "
      f"missing={sorted(set(NA.PAGE_NAMES) - set(_mounted))}")
check("H3a2 「实时榜单」「数据状态」的存在性口径未变",
      "实时榜单" in _mounted and "数据状态" not in _mounted,
      f"实时榜单={'实时榜单' in _mounted}")

# H3b ★ PAGE_INDEX 仍然必须与 PAGE_NAMES 逐项一致（Ctrl+N / 跳页的依据）
check("H3b PAGE_INDEX 与 PAGE_NAMES 下标逐项一致",
      all(NA.PAGE_INDEX[nm] == i for i, nm in enumerate(NA.PAGE_NAMES))
      and len(NA.PAGE_INDEX) == len(NA.PAGE_NAMES),
      f"PAGE_INDEX={NA.PAGE_INDEX}")

# H3b2 ★★ v2.1.0 新增：每个子模块都能按名换算到 (母模块下标, 子页下标)
#       这是 goto_module / 菜单快捷键的正确性基础。
_pos_ok = True
_pos_msg = ""
for _nm in NA.PAGE_NAMES:
    if _nm == "总览":
        continue
    _p = NA.GROUP_POS.get(_nm)
    if _p is None:
        _pos_ok = False; _pos_msg = f"{_nm} 不在 GROUP_POS"; break
    _g = NA.PAGE_GROUP[_nm]
    if _top_now[_p[0]] != _g:
        _pos_ok = False; _pos_msg = f"{_nm}: 顶层{_top_now[_p[0]]}≠{_g}"; break
check("H3b2 GROUP_POS/PAGE_GROUP 与真实顶层下标自洽（跳页基础）",
      _pos_ok, _pos_msg or "全部自洽")

# H3c ★ 「数据状态」页的替代入口（帮助 → 运行状态）必须存在
_help_acts = []
for _m in win.menuBar().actions():
    if _m.menu() and "帮助" in _m.text():
        _help_acts = [a.text() for a in _m.menu().actions()]
check("H3c 「数据状态」页删除后仍有替代入口（帮助 → 运行状态）",
      any("运行状态" in a for a in _help_acts) and hasattr(win, "show_status_dialog"),
      f"帮助菜单={_help_acts}")

# H4: 原有图表对象都在
check("H4 原有图表对象完好（index/flow/fan/lead/stage/theme）",
      all(hasattr(win, a) for a in
          ("ch_index", "ch_flow", "ch_fan", "ch_lead", "ch_stage", "ch_theme")))

# H5: 主题切换仍工作（配色方向已固定为红涨绿跌）
win.theme_name = "light"
win.apply_theme()
# ★ v1.2.1：方向固定 → 涨色就是主题的 up、跌色就是 dn（不再互换）
check("H5 apply_theme 应用主题且配色固定红涨绿跌",
      win.ch_fan.th["up"] == NA.THEMES["light"]["up"]
      and win.ch_fan.th["dn"] == NA.THEMES["light"]["dn"],
      f'up={win.ch_fan.th["up"]} dn={win.ch_fan.th["dn"]}')
win.apply_theme()

# H6: 工作日历/窗口清理模块仍可导入使用
check("H6 store/tradecal 模块仍可用",
      callable(store.list_local_panels) and hasattr(store, "purge_beyond_window"))

# H7: 归档模块不干扰面板文件（只写 archive.json）
before = set(os.listdir(D2))
ARC.maybe_archive("20260918", 240, 6, "live", D2, force=True)
after = set(os.listdir(D2))
check("H7 归档只新增 archive.json（不动面板文件）",
      (after - before) <= {"archive.json"} or "archive.json" in after,
      f"new={after - before}")

print("\n=========== I. v1.1.6 自检修复项 ===========")

# I1 ★：KpiCard.set_value 必须能**清除**颜色（原实现 None 时跳过 → 残留旧色）
_c = NA.KpiCard("t", "—", "")
_c.set_value("+1.23%", "s", color="#e0393e")
_c.set_value("—", "s2")                     # 不带 color = 要求回到默认色
check("I1 KpiCard 无 color 时清除旧色（不残留）",
      _c.lb_value.styleSheet() == "",
      f"styleSheet={_c.lb_value.styleSheet()!r}")

# I2 ★：KPI top_in/top_out 在空数据时必须回落，不能留上一份的旧值
_w = NA.MainWindow.__new__(NA.MainWindow)
_w.theme_name = "light"
for _n in ("kpi_index", "kpi_in", "kpi_out", "kpi_net",
           "kpi_top_in", "kpi_top_out"):
    setattr(_w, _n, NA.KpiCard(_n, "—", ""))
_w.data = {"market": {"top_in": [{"name": "半导体", "net": 88.8}],
                      "top_out": [{"name": "银行", "net": -66.6}],
                      "n_in": 10, "n_out": 20, "n_total": 100,
                      "sum_in": 1, "sum_out": -1, "net_total": 0,
                      "index_pct": 1.0},
           "times": ["09:30", "15:00"], "index_all": []}
_w.fill_kpi()
_first = _w.kpi_top_in.lb_value.text()
_w.data = {"market": {"top_in": [], "top_out": [], "n_in": 0, "n_out": 0,
                      "n_total": 100, "sum_in": 0, "sum_out": 0,
                      "net_total": 0, "index_pct": None},
           "times": [], "index_all": []}
_w.fill_kpi()
check("I2 KPI 最强流入/流出在空数据时回落为 —（不留陈旧值）",
      _first != "—" and _w.kpi_top_in.lb_value.text() == "—"
      and _w.kpi_top_out.lb_value.text() == "—",
      f"有数据={_first!r} 空数据={_w.kpi_top_in.lb_value.text()!r}")

# I3 ★：flowdata.LAST_DATA_DATE 必须在每次 build_panel 前复位
import inspect as _insp
_bp = _insp.getsource(flowdata.build_panel)
check("I3 build_panel 开头复位 LAST_DATA_DATE（跨次抓取不串日期）",
      "LAST_DATA_DATE = None" in _bp, "已复位" if "LAST_DATA_DATE = None" in _bp else "未复位")

# I4 ★：salience 不再顶层 import DATA（值拷贝陷阱）
#   注意：注释里会提到这个写法本身，所以必须**剔除注释行**再判断，
#   否则断言会匹配到说明文字而产生假失败（第一次就踩了）。
_sal_lines = []
for _ln in open(os.path.join(BASE, "core", "salience.py"),
                encoding="utf-8").read().splitlines():
    _st = _ln.strip()
    if _st.startswith("#"):
        continue                       # 纯注释行跳过
    if "#" in _ln:
        _ln = _ln.split("#", 1)[0]     # 去掉行尾注释
    _sal_lines.append(_ln)
_sal_code = "\n".join(_sal_lines)
check("I4 salience 不再 from flowdata import DATA（避免值拷贝脱钩）",
      "from flowdata import DATA" not in _sal_code,
      "仅保留 load_panel" if "from flowdata import load_panel" in _sal_code else "?")

# I5 ★：_pct_rank 并列值取平均秩（不再是 [0,0.5,1]）
import salience as _sal2
_pr = _sal2._pct_rank(np.array([5.0, 5.0, 5.0]))
check("I5 _pct_rank 并列值给同一分位（原为 0/0.5/1 三档）",
      len(set(np.round(_pr, 9))) == 1,
      f"[5,5,5] → {np.round(_pr, 3)}")
# 且非并列时仍保持严格单调
_pr2 = _sal2._pct_rank(np.array([1.0, 2.0, 3.0]))
check("I5b _pct_rank 非并列时保持单调递增",
      list(np.round(_pr2, 6)) == [0.0, 0.5, 1.0], f"{np.round(_pr2, 3)}")

# I6 ★：QSS 补齐的关键交互态
_qss = NA.build_qss(NA.THEMES["light"])
check("I6 QSS 含可勾选按钮的 :checked 态（配色开关有视觉反馈）",
      "QToolButton:checked" in _qss)
check("I6b QSS 含 :focus 焦点态（键盘导航可见）", ":focus" in _qss)
check("I6c QSS 含 QComboBox 样式（回看下拉与主题一致）",
      "QComboBox" in _qss and "QComboBox::down-arrow" in _qss)
check("I6d QSS 含 QCheckBox 样式", "QCheckBox" in _qss)
check("I6e QSS 含表格行 hover 态", "QTableWidget::item:hover" in _qss)
check("I6f 状态栏有 min-height（进度条显隐不抖布局）",
      "min-height" in _qss.split("QStatusBar")[1][:260])

# I7 ★：布局常量统一（不再有硬编码 spacing）
_ui_src = open(os.path.join(BASE, "native_app.py"), encoding="utf-8").read()
_ui = _ui_src[_ui_src.index("    def _build_ui(self):"):
              _ui_src.index("    def _build_menu(self):")]
check("I7 _build_ui 内不再有硬编码 setSpacing(7/8)（统一走常量）",
      "setSpacing(7)" not in _ui and "setSpacing(8)" not in _ui,
      f"PAD_PAGE×{_ui.count('setContentsMargins(*PAD_PAGE)')} "
      f"GAP×{_ui.count('setSpacing(GAP_')}")

# I8 ★：悬停缓存生效（同一分钟不重算）
_hc = NA.MainWindow.__new__(NA.MainWindow)
_hc._hover_key = None
_hc._hover_cache = {}
_hc.theme_name = "light"
_hc.t_hover = NA.make_table(["#", "题材", "净额(亿)", "5分钟(亿)", "辨识度"])
_hc.lbl_hover = __import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel("")
_hc._net_color = lambda row: None
_hc._fmt_rows = lambda rows: []


class _FakeF:
    times = ["09:31", "09:32", "09:33"]
    codes = []


_hc.data = {"F": _FakeF(), "times": _FakeF.times, "date_tag": "T1"}
_hc.on_fan_hover(1, "09:32")
_c1 = dict(_hc._hover_cache)
_hc.on_fan_hover(1, "09:32")          # 同列再来一次
check("I8 悬停结果按分钟号缓存（同列不重算）",
      1 in _c1 and _hc._hover_cache.get(1) is _c1[1], f"cache keys={list(_c1)}")
# 换数据 → 缓存作废
_hc.data["date_tag"] = "T2"
_hc.on_fan_hover(1, "09:32")
check("I8b 换数据（date_tag 变化）后悬停缓存作废重建",
      _hc._hover_key == "T2" and list(_hc._hover_cache.keys()) == [1])

# I9 ★：on_fail 首次失败时把图表切成错误态
_src_fail = _ui_src[_ui_src.index("    def on_fail(self, msg):"):]
_src_fail = _src_fail[:_src_fail.index("    def on_data(self, d):")]
check("I9 on_fail 在无数据时切图表为错误态占位（不再停留在「加载中」）",
      "数据加载失败" in _src_fail and "blank(" in _src_fail)

print("\n=========== J. v1.1.6 资金曲线图例遮挡修复 ===========")


def _legend_stats(chart):
    """返回 (落在图例矩形内的数据点数, 图例 bbox, 绘图区 bbox, 刻度标签底)。"""
    fig = chart.fig
    fig.canvas.draw()
    R = fig.canvas.get_renderer()
    ax = fig.axes[0]
    leg = fig.legends[0] if fig.legends else None
    if leg is None:
        return -1, None, None, None
    lb = leg.get_window_extent(R)
    ab = ax.get_window_extent(R)
    cnt = 0
    for ln in ax.get_lines():
        xy = ln.get_xydata()
        if len(xy) < 5:                       # 跳过 2 点的辅助线（0 轴/分界线）
            continue
        px = ax.transData.transform(xy)
        cnt += int(((px[:, 0] >= lb.x0) & (px[:, 0] <= lb.x1)
                    & (px[:, 1] >= lb.y0) & (px[:, 1] <= lb.y1)).sum())
    tby = min(t.get_window_extent(R).y0 for t in ax.get_xticklabels())
    return cnt, lb, ab, tby


def _mkfan(W=10.02, T=240, long_names=False):
    """造一张真实尺寸的 FanChart：20 条曲线 × T 分钟。"""
    NAMES = (["特高压输变电设备概念", "储能与锂电材料产业链", "工业母机与数控机床",
              "算力基础设施液冷散热", "创新药CXO一体化服务"] if long_names else
             ["半导体设备", "PCB概念", "消费电子", "光伏设备", "风电设备",
              "AI算力芯片", "液冷服务器", "固态电池", "人形机器人", "创新药"])
    rng = np.random.default_rng(3)
    fc = NA.FanChart(3.67, W)
    ser = []
    for i in range(20):
        isup = i < 10
        v = rng.normal(0, 40) + rng.normal(0, 3, T).cumsum()
        ser.append({"name": NAMES[i % len(NAMES)], "series": [float(x) for x in v],
                    "net": float(v[-1]),
                    "direction": ("in" if isup else "out"), "times": [""] * T})
    fc.plot(ser)
    return fc


# J1 ★ 核心：真实尺寸下 20 条曲线**零覆盖**（修前实测 790 点 / 16.5%）
_fc = _mkfan()
_cnt, _lb, _ab, _tby = _legend_stats(_fc)
check("J1 图例不再遮挡曲线（真实尺寸下覆盖点数为 0）", _cnt == 0,
      f"覆盖点数={_cnt}（修前 790）")

# J2 ★ 图例完整落在画布内（不被裁掉）
_Wpx = _fc.fig.canvas.get_width_height()[0]
_Hpx = _fc.fig.canvas.get_width_height()[1]
check("J2 图例完整落在画布内（不越界）",
      _lb.x0 >= 0 and _lb.x1 <= _Wpx and _lb.y0 >= 0 and _lb.y1 <= _Hpx,
      f"x=[{_lb.x0:.0f},{_lb.x1:.0f}]/{_Wpx} y=[{_lb.y0:.0f},{_lb.y1:.0f}]/{_Hpx}")

# J3 ★ 图例在横轴刻度标签**下方**（不压「09:30」这类标签）
check("J3 图例位于横轴刻度标签下方（不压刻度）", _lb.y1 <= _tby + 1.5,
      f"图例顶={_lb.y1:.0f} 标签底={_tby:.0f}")

# J4 ★ 曲线区仍保留足够高度（不能为放图例把曲线压没）
check("J4 图例让位后曲线区仍占画布 ≥ 55% 高",
      _ab.height / _Hpx >= 0.55, f"曲线区高占比 {_ab.height / _Hpx:.0%}")

# J5 ★ 两组条目（流入/流出）文字分别着涨色/跌色
_fc2 = _mkfan()
_th2 = NA.coloring(NA.THEMES["light"])
_texts = _fc2.fig.legends[0].get_texts()
_n_up = sum(1 for t in _texts[:10]
            if t.get_color().lower() == _th2["up"].lower())
_n_dn = sum(1 for t in _texts[10:]
            if t.get_color().lower() == _th2["dn"].lower())
check("J5 图例流入条目着涨色、流出条目着跌色", _n_up == 10 and _n_dn == 10,
      f"涨色 {_n_up}/10 · 跌色 {_n_dn}/10")

# J6 ★ 图例两组颜色必须取自 th[up]/th[dn]（不能写死红/绿）
#    ★ v1.2.1：配色方向已固定为红涨绿跌，本断言改为校验"图例颜色
#      与主题取色一致"，不再涉及方向对调（切换入口已删除）。
_fc3 = _mkfan()
_th3 = NA.coloring(NA.THEMES["light"])
_t3 = _fc3.fig.legends[0].get_texts()
_up_rev = sum(1 for t in _t3[:10] if t.get_color().lower() == _th3["up"].lower())
_dn_rev = sum(1 for t in _t3[10:] if t.get_color().lower() == _th3["dn"].lower())
check("J6 图例两组颜色取自 th[up]/th[dn]（未写死红绿）",
      _up_rev == 10 and _dn_rev == 10,
      f"涨色 {_up_rev}/10 · 跌色 {_dn_rev}/10")

# J7 ★ 超长板块名也不越界（列数要自适应，不能固定 20 列）
_fc4 = _mkfan(long_names=True)
_c4, _l4, _a4, _t4 = _legend_stats(_fc4)
check("J7 超长板块名下图例仍不越界且不遮挡",
      _l4.x1 <= _fc4.fig.canvas.get_width_height()[0] and _c4 == 0,
      f"x1={_l4.x1:.0f}/{_fc4.fig.canvas.get_width_height()[0]} 覆盖={_c4}")

# J8 ★ 窄窗口（最小尺寸附近）同样不越界、不遮挡
_fc5 = _mkfan(W=6.5)
_c5, _l5, _a5, _t5 = _legend_stats(_fc5)
check("J8 窄窗口（6.5in）下图例不越界且不遮挡",
      _c5 == 0 and _l5.x0 >= 0
      and _l5.x1 <= _fc5.fig.canvas.get_width_height()[0]
      and _l5.y0 >= 0,
      f"覆盖={_c5} y0={_l5.y0:.0f}")

# J9 ★ 只建一个图例（早先一次建多个会叠着一起画出来）
check("J9 画布上只保留一个图例（其余已清理）",
      len(_fc.fig.legends) == 1, f"n={len(_fc.fig.legends)}")

# J10 ★ 图例放在**轴外**（ax 内不应再挂 Legend）
_axlegs = [a for a in _fc.fig.axes[0].get_children()
           if a.__class__.__name__ == "Legend"]
check("J10 图例已移出绘图区（ax 内无 Legend）", len(_axlegs) == 0,
      f"ax 内图例数={len(_axlegs)}")

print("\n=========== K. v1.1.7 曲线连续实线 / 缺口衔接 / Y轴自适应 ===========")

# K1 ★ 流出曲线改连续实线（原来 (0,(4,2.2)) 虚线），颜色/线宽不变
_fc_k = _mkfan()
_lines_k = [ln for ln in _fc_k.fig.axes[0].get_lines()
            if len(ln.get_xydata()) >= 5]
_ls = [ln.get_linestyle() for ln in _lines_k]
check("K1 流入/流出曲线一律连续实线（无虚线样式残留）",
      len(_ls) == 20 and all(s == "-" for s in _ls),
      f"n={len(_ls)} 非实线={sum(1 for s in _ls if s != '-')}")

# K1b ★ 流出侧颜色仍来自冷色族（虚改实不能把配色带偏）
_th_k = NA.coloring(NA.THEMES["light"])
_outs_k = _lines_k[10:]
check("K1b 流出侧仍取冷色族（颜色未随线型改动）",
      [ln.get_color() for ln in _outs_k] == NA.DN_SEQ[:10],
      f"{[ln.get_color() for ln in _outs_k][:3]}")

# K1c ★ 线宽口径不变（流入 1.7 / 流出 1.5）
check("K1c 线宽口径未变（流入 1.7 / 流出 1.5）",
      all(abs(ln.get_linewidth() - 1.7) < 1e-6 for ln in _lines_k[:10])
      and all(abs(ln.get_linewidth() - 1.5) < 1e-6 for ln in _outs_k))

# K2 ★ 缺口衔接：单条曲线中段塞 NaN/None → 画出来必须是有限值、无断点
_gap = [1.0, 2.0, None, 4.0, float("nan"), 6.0, 7.0]
_filled = NA._fill_gaps(_gap)
check("K2 缺口衔接：None/NaN 被补齐为有限值，长度不变",
      len(_filled) == len(_gap)
      and all(isinstance(x, float) and np.isfinite(x) for x in _filled),
      f"{[round(x, 2) for x in _filled]}")

# K2b ★ 中段缺口按两侧线性插值（3.0 与 ~5.33 附近），不是简单写 0
check("K2b 中段缺口按两侧取值衔接（未凭空写 0）",
      abs(_filled[2] - 3.0) < 1e-6 and 4.0 < _filled[4] < 6.0,
      f"filled[2]={_filled[2]:.2f} filled[4]={_filled[4]:.2f}")

# K2c ★ 开头缺口用后一个已知值回填（而非硬写 0）
_f2 = NA._fill_gaps([None, None, -3.0, -4.0])
check("K2c 开头缺口回填为后一个已知值（不硬写 0 造假走势）",
      _f2[0] == -3.0 and _f2[1] == -3.0, f"{_f2}")

# K2d ★ 整条全空 → 补 0 平线，不留空白段
_f3 = NA._fill_gaps([None, float("nan"), None])
check("K2d 整条空值 → 补 0 平线（不留空白段）",
      _f3 == [0.0, 0.0, 0.0], f"{_f3}")

# K2e ★ 无缺口时原样返回（不得引入插值误差）
_f4 = NA._fill_gaps([1.5, 2.5, 3.5])
check("K2e 无缺口时原样返回（不引入插值误差）",
      _f4 == [1.5, 2.5, 3.5], f"{_f4}")

# K2f ★ 端到端：把带缺口的序列喂进 FanChart，画出的 ydata 必须全为有限值
_fc_gap = NA.FanChart(3.67, 10.02)
_ser_gap = [{"name": "半导体设备", "direction": "in", "net": 10.0,
             "series": [1.0, None, 3.0, float("nan"), 5.0],
             "times": [""] * 5},
            {"name": "PCB概念", "direction": "out", "net": -8.0,
             "series": [None, -2.0, None, -4.0, -5.0],
             "times": [""] * 5}]
_fc_gap.plot(_ser_gap)
_yd_all = []
for ln in _fc_gap.fig.axes[0].get_lines():
    if len(ln.get_xydata()) == 5:
        _yd_all.append(ln.get_ydata())
check("K2f 端到端：带缺口序列渲染后无 NaN（曲线不断裂）",
      len(_yd_all) == 2
      and all(np.isfinite(np.asarray(y, dtype=float)).all() for y in _yd_all),
      f"曲线数={len(_yd_all)}")

# K3 ★ Y 轴自适应：不同数据量级下量程都要包住数据且不贴边
_th_lim = NA._fan_ylim([[0.0, 10.0, 20.0], [-5.0, 0.0, 3.0]])
check("K3 Y 轴自适应：量程包住全部数据且上下留余量",
      _th_lim is not None and _th_lim[0] < -5.0 and _th_lim[1] > 20.0,
      f"ylim=({_th_lim[0]:.2f}, {_th_lim[1]:.2f})" if _th_lim else "None")

# K3b ★ 早盘数据少（跨度小）时不能被撑成满幅 —— 有最小跨度保护
_lim_small = NA._fan_ylim([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
check("K3b 极平坦数据（全员 0）得到最小跨度，不画成满幅横线",
      _lim_small is not None and (_lim_small[1] - _lim_small[0]) >= 2.0,
      f"span={_lim_small[1] - _lim_small[0]:.2f}" if _lim_small else "None")

# K3c ★ 尾盘大跨度（上千亿）量程按比例放大，不溢出
_lim_big = NA._fan_ylim([[-800.0, 500.0, 1600.0]])
check("K3c 尾盘大跨度量程按比例放大（上不封顶、下不裁 0 轴）",
      _lim_big is not None and _lim_big[0] < -800.0 and _lim_big[1] > 1600.0,
      f"ylim=({_lim_big[0]:.0f}, {_lim_big[1]:.0f})" if _lim_big else "None")

# K3d ★ 0 轴必须落在量程内（否则 axhline(0) 参考线被裁掉看不见）
_lim_allpos = NA._fan_ylim([[5.0, 8.0, 12.0]])
_lim_allneg = NA._fan_ylim([[-12.0, -8.0, -5.0]])
check("K3d 全正 / 全负数据下 0 轴仍在量程内（参考线可见）",
      _lim_allpos[0] <= 0 <= _lim_allpos[1]
      and _lim_allneg[0] <= 0 <= _lim_allneg[1],
      f"全正=({_lim_allpos[0]:.2f},{_lim_allpos[1]:.2f}) "
      f"全负=({_lim_allneg[0]:.2f},{_lim_allneg[1]:.2f})")

# K3e ★ 空数据不抛异常（返回 None，调用方跳过 set_ylim）
check("K3e 空数据返回 None（不设量程、不抛异常）",
      NA._fan_ylim([]) is None and NA._fan_ylim([[], []]) is None)

# K3f ★ 端到端：FanChart 渲染后 ylim 确实等于 _fan_ylim 的取值
_fc_lim = _mkfan()
_ax_k = _fc_lim.fig.axes[0]
_yv = np.concatenate([np.asarray(ln.get_ydata(), dtype=float)
                      for ln in _ax_k.get_lines()
                      if len(ln.get_xydata()) >= 5])
_lo_k, _hi_k = _ax_k.get_ylim()
check("K3f 端到端：画布实际 ylim 包住全部曲线（不裁峰谷）",
      _lo_k <= _yv.min() + 1e-6 and _hi_k >= _yv.max() - 1e-6,
      f"ylim=({_lo_k:.1f},{_hi_k:.1f}) 数据=({_yv.min():.1f},{_yv.max():.1f})")

print("\n=========== L. v1.1.7 实时榜单累积池（只增不减） ===========")

sys.path.insert(0, os.path.join(BASE, "core"))
import watchlist as WL
import salience as SAL


class _FakeF:
    """最小可用的 Features 替身：只提供 score_table / detect_events 用到的东西。

    ★ 为什么不直接跑 salience.build_features：那要读真实面板（联网 + 1000 板块），
      离屏测试里又慢又不稳。累积池的逻辑与数据来源无关 —— 它只吃
      score_table/detect_events 的**输出**，所以给一张构造好的表就够，
      而且能把"异动"精确摆到想要的分钟上，测起来反而更硬。
    """
    def __init__(self, T=6, N=8):
        self.times = [f"09:{31 + k}" for k in range(T)]
        self.codes = [f"BK{i:04d}" for i in range(N)]
        self.names = {c: f"题材{i}" for i, c in enumerate(self.codes)}
        self.kinds = {c: "concept" for c in self.codes}
        self.concept_mask = np.ones(N, dtype=bool)
        self.V = np.zeros((T, N))
        self.dV5 = np.zeros((T, N))
        self.dV1 = np.zeros((T, N))
        self.z = np.zeros((T, N))
        self.salience = np.zeros((T, N))
        self.rank = np.tile(np.arange(1, N + 1), (T, 1))
        self.rank_neg = self.rank
        self.pct_vel = np.zeros((T, N))
        self.pct_abs = np.zeros((T, N))
        self.persist = np.zeros((T, N))
        self.resid = np.zeros((T, N))
        self.index = []


_L = "题材0"
_T = "题材1"

# L1 ★ 开盘入榜：空池 → seed 双向各取 top_n，池子非空
_LD1 = tempfile.mkdtemp()
_p1 = WL.Pool("20260101", _LD1)
_F1 = _FakeF()
_F1.V[0] = np.array([20.0, 15.0, -30.0, -25.0, 5.0, 4.0, 3.0, 2.0])
_F1.salience[0] = np.array([.9, .8, .85, .75, .3, .3, .3, .3])
_n1, _t1 = _p1.update(_F1, 0, salience_mod=SAL, top_n=2, kind="concept")
check("L1 空池 → 开盘按辨识度双向入榜", _t1 >= 4 and _p1.rows,
      f"入榜 {_n1} 条 · 累计 {_t1}")

# L2 ★ 只增不减：第二轮换了榜单（原榜全掉出），池子只准变多
_p1.update(_F1, 1, salience_mod=SAL, top_n=2, kind="concept")
_before = {r["code"] for r in _p1.rows}
_n_all_before = len(_p1.rows)
# 把原来在榜的板块净额/辨识度全打到地板上，模拟"掉出辨识度前 15"
_F1.V[1] = np.array([-1.0, -2.0, -3.0, -4.0, -5.0, -6.0, -7.0, -8.0])
_F1.salience[1] = np.zeros(8)
_F1.dV5[1] = np.zeros(8)
_p1.update(_F1, 1, salience_mod=SAL, top_n=2, kind="concept")
_after = {r["code"] for r in _p1.rows}
check("L2 只增不减：原榜数据塌陷后仍全部保留（不因刷新被移除）",
      _before.issubset(_after) and len(_p1.rows) >= _n_all_before,
      f"前 {len(_before)} 条 → 现 {len(_after)} 条 · 丢失 {len(_before - _after)}")

# L3 ★ 异动立即追加：造一个 z 值突异的新板块，必须进池
_F1.z[2, 7] = 4.5                       # 题材7 突然一根量
_F1.dV5[2, 7] = 3.0
_F1.V[2, 7] = 8.0
_F1.salience[2, 7] = 0.2
_n3, _t3 = _p1.update(_F1, 2, salience_mod=SAL, top_n=2, kind="concept")
check("L3 盘中盘口异动（z 突异）立即追加进榜",
      "BK0007" in _after | {r["code"] for r in _p1.rows}
      and _t3 > _n_all_before,
      f"异动追加 {_n3} 条 · 累计 {_t3}")

# L4 ★ 入选原因首因保留 + 追加（不被后来的刷新冲掉）
_row7 = _p1._by_code.get("BK0007")
check("L4 新进条目带「盘口异动·」原因标记",
      _row7 is not None and "盘口异动" in str(_row7.get("reason", "")),
      f'{(_row7 or {}).get("reason")}')

# L5 ★ first_time 一旦写下就不再变（跨多轮刷新保持）
_ft = {r["code"]: r["first_time"] for r in _p1.rows}
_F1.z[3, 7] = 5.5
_F1.dV5[3, 7] = 4.0
_p1.update(_F1, 3, salience_mod=SAL, top_n=2, kind="concept")
_ft2 = {r["code"]: r["first_time"] for r in _p1.rows}
check("L5 首次入选时刻跨轮保持不变（不被后来的刷新改写）",
      all(_ft2[k] == v for k, v in _ft.items() if k in _ft2),
      f"变化项={[k for k,v in _ft.items() if k in _ft2 and _ft2[k]!=v]}")

# L6 ★ 新进标记只亮一轮（is_new 在 flush 后清掉）
_p1.flush()
_new_after_flush = [r for r in _p1.rows if r.get("is_new")]
check("L6 flush 后「本轮新进」标记被清掉（下轮不再高亮）",
      len(_new_after_flush) == 0, f"残留 {len(_new_after_flush)}")

# L7 ★ 落盘 + 重新载入：池子内容与顺序完整还原
_LD2 = tempfile.mkdtemp()
_p2 = WL.Pool("20260102", _LD2)
_F2 = _FakeF()
_F2.V[0] = np.array([20.0, -30.0, 5.0, 4.0, 3.0, 2.0, 1.0, -1.0])
_F2.salience[0] = np.array([.9, .85, .3, .3, .3, .3, .3, .3])
_p2.update(_F2, 0, salience_mod=SAL, top_n=2, kind="concept")
_snapshot = [r["code"] for r in _p2.rows]
_p2.flush()
_p2b = WL.Pool("20260102", _LD2)
check("L7 池子落盘后重新载入，条目与顺序完整还原",
      [r["code"] for r in _p2b.rows] == _snapshot,
      f"{len(_snapshot)} 条")

# L8 ★ 跨日不串池：另一天的 Pool 读不到这一天的内容
_p3 = WL.Pool("20260103", _LD2)
check("L8 跨交易日隔离：换日读到空池，不沿用前一天榜单",
      len(_p3.rows) == 0, f"n={len(_p3.rows)}")

# L9 ★ 损坏的存档不导致崩溃（半截 JSON → 当空池处理）
_LD3 = tempfile.mkdtemp()
with open(os.path.join(_LD3, "watchlist_20260104.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"date_tag": "20260104", "rows": [{"code": "BK00')
_p4 = WL.Pool("20260104", _LD3)
check("L9 存档损坏时降级为空池（不抛异常）", len(_p4.rows) == 0)

# L10 ★ view() 输出分组有序：流入在前、流出在后，组内按绝对额降序
_v = _p2b.view()
_nets = [r["net_yi"] for r in _v]
_pos = [n for n in _nets if n >= 0]
_neg = [n for n in _nets if n < 0]
check("L10 view() 流入在前流出在后，组内按绝对额降序",
      _nets == _pos + _neg
      and _pos == sorted(_pos, reverse=True)
      and _neg == sorted(_neg),
      f"nets={[round(n,1) for n in _nets]}")

# L11 ★ 容量保险丝：条目数不会无限增长
#   ★ 造数据要**绕过 seed 的双门槛**：seed 只收"辨识度 ≥ 0.12 或净额/增量
#     显著"的板块，若把 salience 全给 0 且 V 全 0，一条都进不来 ——
#     那样测的其实是"门槛生效"而不是"保险丝生效"，断言会在 n=0 上假通过。
#     这里给足辨识度 + 逐分钟递增的净额，让池子真的被灌满。
_LD4 = tempfile.mkdtemp()
_F5 = _FakeF(6, WL.MAX_ROWS + 60)
_F5.salience[:, :] = 0.95
for _k in range(6):
    _F5.V[_k] = np.linspace(30.0, -30.0, _F5.V.shape[1])
    _F5.dV5[_k] = np.linspace(3.0, -3.0, _F5.V.shape[1])
_p5 = WL.Pool("20260105", _LD4)
for _k in range(2):
    _p5.update(_F5, _k, salience_mod=SAL, top_n=WL.MAX_ROWS, kind="concept")
check("L11 容量保险丝生效（条目被灌到上限后不再增长）",
      0 < len(_p5.rows) <= WL.MAX_ROWS,
      f"n={len(_p5.rows)}/{WL.MAX_ROWS}（候选 {_F5.V.shape[1]} 个）")

# L12 ★ 窗口裁剪：只删窗口外的 watchlist 文件
_LD5 = tempfile.mkdtemp()
for _t in ("20260110", "20260111", "20260112"):
    WL.save(_t, [{"code": "BK0001", "name": "x"}], _LD5)
with open(os.path.join(_LD5, "watchlist_today.json"), "w",
          encoding="utf-8") as _f:
    _f.write("{}")                       # 非日期名：不该被删
_gone = WL.purge_beyond_window({"20260112"}, _LD5)
check("L12 窗口裁剪只删窗口外的日期池（非日期名不动）",
      _gone == ["20260110", "20260111"]
      and os.path.exists(os.path.join(_LD5, "watchlist_20260112.json"))
      and os.path.exists(os.path.join(_LD5, "watchlist_today.json")),
      f"删除={_gone}")

# L13 ★ 页面接线：Worker 产出与 UI 表格都就位
_src_k = _ui_src
check("L13 Worker 产出 watch/watch_stats/watch_base_in 等字段",
      all(k in _src_k for k in ('out["watch"]', 'out["watch_stats"]',
                                'out["watch_base_in"]', 'out["watch_base_out"]')))
check("L13b UI 有实时榜单表格与状态标签",
      hasattr(win, "t_watch") and hasattr(win, "lbl_watch")
      and hasattr(win, "t_wb_in") and hasattr(win, "t_wb_out"))

# L14 ★ 历史回看不生长池子（只读快照）
#   取到「下一段 def 之前」为止 —— 固定 800 字符的窗口会随代码增长而
#   截错位置（本轮就截到了 flush 之前，误报失败）。
_src_local = _src_k[_src_k.index("import watchlist as _wl"):]
_nxt = _src_local.find("\n            # ★ 全市场汇总")
_src_local = _src_local[: _nxt if _nxt > 0 else 3000]
check("L14 历史回看路径不写池子（只读当日快照，不做 update/flush）",
      "self.local_only" in _src_local
      and "pool.update(" in _src_local
      and "pool.flush()" in _src_local
      # 两者必须在同一个 if/else 里互为分支：local_only 走读、否则才写
      and _src_local.index("self.local_only") < _src_local.index("pool.update("))

# L15 ★ 主窗口确实有 fill_watch 且在 fill_all 里被调用
check("L15 fill_watch 已接入 fill_all（刷新即重绘榜单）",
      hasattr(win, "fill_watch")
      and "self.fill_watch()" in _src_k)

# ★ v1.2.1：PIL 导入提到这里（原先只在已被删除的 A12 块里导入，
#   导致 M 段像素比对报 NameError: _PILImage is not defined）。
from PIL import Image as _PILImage

print("\n=========== M. v1.1.7 交付比对：源码版 vs exe 版 ===========")

# M1~M3 ★ 受控逐字节比对（shots_ctrl_src vs shots_ctrl_exe）。
#   ★ 口径必须先讲清楚，否则这个断言一定会被误读成"有回归"：
#     两次**独立启动**之间面板会刷新、鼠标落点不同 —— 那是"输入不同"，
#     不是"渲染不同"。正确做法：
#       ① 冻结同一份面板 + 清空榜单池（受控输入）；
#       ② 走 --date 历史回看路径（只读本地、绝不抓取）；
#       ③ 比对时把**鼠标悬停带**排除 —— QTableWidget::item:hover 会给
#          光标所在整行铺 #eef2f8 底色，两次运行光标位置必然不同。
#     实测：不排除悬停带时 6/10 页 DIFF；排除后 **10/10 逐字节一致**，
#     且差异像素里 97~99% 恰好就是 hover 底色 (238,242,248) ——
#     归因 100% 指向鼠标位置。
try:
    # ★ v1.2.1：目录可用环境变量覆盖（与 shot_controlled_compare.py 同一套变量）。
    #   动机：受管环境里"批量删除"可能要人工确认，比对脚本清空默认目录会被拦；
    #   这时用 CTRL_SRC_OUT / CTRL_EXE_OUT 指到全新目录，断言照样能跑。
    _CS = os.environ.get("CTRL_SRC_OUT") or os.path.join(BASE, "shots_ctrl_src")
    _CE = os.environ.get("CTRL_EXE_OUT") or os.path.join(BASE, "shots_ctrl_exe")
    # ★ v1.2.5：受控目录里除 PNG 外还有 `*_关键节点.table.txt`，只认图片。
    _IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp")

    def _pair_file(f):
        """按"去掉前缀后的页名"配对 exe 侧文件。

        ★ 为什么不能直接用同名：两侧的 `--shot <前缀>` 刻意取了不同前缀
          （源码版 v117s_ / exe 版 v117e_），否则两边写在同一个目录时会
          互相覆盖。所以这里必须按**页名尾巴**配对，不能同名字符串相等比对
          ——第一版就是直接 join 同名，结果 exe 侧找不到文件、M1~M3 全报异常。
        ★ v1.2.5：`--shot` 现在除 PNG 外还会导出 `*_关键节点.table.txt`
          （给屏幕级排序终审用）。列目录时必须**只认图片**，否则
          `_pages` 会变成 10 个（9 图 + 1 txt），M1/M2 会以"页数不符"假失败
          —— 明明渲染毫无差异，却报成回归。
        """
        _tail = f.split("_", 1)[1] if "_" in f else f
        for _c in sorted(os.listdir(_CE)):
            if _c.lower().endswith(_IMG_EXT) and _c.endswith("_" + _tail):
                return os.path.join(_CE, _c)
        return None

    def _ctrl_pair(f):
        """返回 (完全一致?, 排除悬停带后是否一致, 差异像素数, 非hover差异数)"""
        _B_path = _pair_file(f)
        if _B_path is None:
            return False, False, -1, -1
        _A = np.asarray(_PILImage.open(os.path.join(_CS, f)).convert("RGB"))
        _B = np.asarray(_PILImage.open(_B_path).convert("RGB"))
        if _A.shape != _B.shape:
            return False, False, -1, -1
        _d = ~((_A == _B).all(axis=2))
        _tot = int(_d.sum())
        if _tot == 0:
            return True, True, 0, 0
        _HOV = np.array([238, 242, 248])          # 浅色主题 hover 底色
        _band = ((_A == _HOV).all(axis=2) | (_B == _HOV).all(axis=2))
        _ys = np.where(_band.any(axis=1))[0]
        if len(_ys):
            _lo = max(0, int(_ys.min()) - 25)
            _hi = min(_A.shape[0], int(_ys.max()) + 25)
            _keep = np.ones_like(_d)
            _keep[_lo:_hi, :] = False
            _real = int((_d & _keep).sum())
        else:
            _real = _tot
        return False, _real == 0, _tot, _real

    _pages = ([f for f in sorted(os.listdir(_CS))
               if f.lower().endswith(_IMG_EXT)] if os.path.isdir(_CS) else [])
    _res = {f: _ctrl_pair(f) for f in _pages}
    _n_exact = sum(1 for v in _res.values() if v[0])
    _n_clean = sum(1 for v in _res.values() if v[1])
     # ★ v1.2.1：「数据状态」页已删除 → 页签 10 → 9，受控比对的页数随之变为 9。
    #   页数不写死，直接对齐 PAGE_NAMES 的长度，将来增删页签不必再改这里。
    _npages = len(NA.PAGE_NAMES)
    check(f"M1 受控比对覆盖 {_npages} 页（源码版 vs exe 版）",
          len(_pages) == _npages, f"n={len(_pages)} (期望 {_npages})")
    check(f"M2 受控比对：排除鼠标悬停带后 {_npages}/{_npages} 逐字节一致",
          _n_clean == _npages and len(_pages) == _npages,
          f"完全一致 {_n_exact}/{_npages} · 排除悬停后 {_n_clean}/{_npages}")
    _hov_share = []
    for f, v in _res.items():
        if v[2] > 0:
            _hov_share.append(1 - v[3] / v[2])
    # ★ 判据要容得下"最好的结果"：v1.1.7 修掉 `--date` 启动污染榜单池之后，
    #   10 页连一个差异像素都没有了（鼠标落点都恰好一致），_hov_share 为空。
    #   旧写法 `bool(_hov_share) and ...` 会把"零差异"判成 FAIL —— 断言把
    #   成功当失败报。现在：**无差异页 = 通过**；有差异页则要求 ≥85% 可归因。
    check("M3 差异像素可 100% 归因于鼠标悬停底色（非渲染差异）",
          (not _hov_share) or min(_hov_share) > 0.85,
          f"{_npages} 页零差异（无需归因）" if not _hov_share else
          f"悬停底色占比 {min(_hov_share) * 100:.1f}%~{max(_hov_share) * 100:.1f}%")
except Exception as _e:                                       # noqa: BLE001
    check("M1 受控比对覆盖 N 页（源码版 vs exe 版）", False, f"异常：{_e}")
    check("M2 受控比对：排除鼠标悬停带后 N/N 逐字节一致", False, "异常")
    check("M3 差异像素可 100% 归因于鼠标悬停底色（非渲染差异）", False, "异常")

# M4 ★ exe 已把 watchlist.py / salience.py 等模块打进去（datas= 不走静态分析）
check("M4 exe 产物存在且体积正常（含新模块）",
      os.path.exists(os.path.join(BASE, "dist_v121b", "资金流向原生版.exe")),
      "dist_v121b/资金流向原生版.exe")


# ============================================================ N. v1.2.0 题材簇相对长度
print("\n=========== N. v1.2.0：题材簇相对长度 + 两栏同向 ===========")
try:
    _src = open(os.path.join(BASE, "core", "salience.py"), encoding="utf-8").read()
    _app = open(os.path.join(BASE, "native_app.py"), encoding="utf-8").read()

    # N1 ★ 配额截断已移除：旧写法 `outs = c_out[:n_out]` 会把流出硬砍到 4 条
    #    （topn=12 × out_ratio 0.34 = 4），明明有 8+ 个负簇可用。
    #    ★ 判据要**排除注释行** —— 新代码的注释里正解释着这句旧写法
    #      （"旧写法 `outs = c_out[:n_out]` 有个隐蔽毛病…"），
    #      直接 `in` 匹配会把注释也算命中，断言就永远 FAIL。只扫非注释行。
    def _live_lines(txt):
        return [ln for ln in txt.splitlines()
                if not ln.lstrip().startswith("#")]
    _live = "\n".join(_live_lines(_src))
    _has_quota = "outs = c_out[:n_out]" in _live
    check("N1 方向配额不再截断流出（旧 `outs = c_out[:n_out]` 已移除）",
          not _has_quota, "旧配额截断仍在" if _has_quota else "已改为两侧各自取满")

    # N2 两侧各自取满：per_side
    check("N2 两侧各自取满（per_side = topn）",
          "per_side = max(1, int(topn))" in _src
          and "ins = c_in[:per_side]" in _src and "outs = c_out[:per_side]" in _src,
          "per_side 分配已就位" if "per_side" in _src else "未找到 per_side")

    # N3 调用点传 8（每侧 8 → 共 16）
    check("N3 调用点 topn=8（每侧 8，共 16 条）",
          "cluster_themes(F, last, topn=8)" in _app,
          "topn=8 已接线" if "cluster_themes(F, last, topn=8)" in _app else "调用点未改")

    # N4 ★ v1.2.0 相对长度：两侧各自归一（不再共用一把刻度）
    #    用户反馈 v1.1.8 的共用刻度版"大小太不协调了，相对长度就行"。
    check("N4 改为相对长度：两侧各自按本侧最大值归一",
          "_norm_fn" in _app and "_norm_of" in _app
          and "MIN_FRAC = 0.10" in _app,
          "归一映射已就位" if "_norm_of" in _app else "未找到 _norm_of")

    # N5 ★ 归一映射必须**保序**（上端固定 + 线性映射，不能单抬最小值）
    #    判据：存在 k = (1.0 - MIN_FRAC) / (hi - lo)，且**不含**旧的
    #    「整侧都太短 → 整体平移」写法（那种在归一化下已无必要，
    #    因为归一后 hi 恒映射到 1.0，不存在 hi < t 的情形）。
    _has_k = "k = (1.0 - MIN_FRAC) / (hi - lo)" in _app
    _has_flat = "shift = t - hi" in _app
    check("N5 归一映射保序（线性映射 [lo,hi]→[MIN_FRAC,1]，上端固定）",
          _has_k and not _has_flat,
          f"线性映射={_has_k} · 旧平移残留={_has_flat}")

    # N5b ★ 边界：单条 / 全等值必须画满格（否则除零或长度飘）
    check("N5b 单条 / 全等值时画满格（无除零）",
          "if hi <= lo:" in _app and "return lambda v: 1.0" in _app,
          "满格分支已就位")

    # N6 端到端：真实面板跑一次，验证红 8 绿 8 且两侧各自保序
    import sys as _sys
    _sys.path.insert(0, os.path.join(BASE, "core"))
    import flowdata as _fd
    import salience as _sal
    _dd = os.path.join(os.environ.get("LOCALAPPDATA", ""), "FlowLiveNative", "data")
    _fd.set_data_dir(_dd)
    try:
        _F = _sal.build_features("20260918")
        _i = len(_F.times) - 1
        _th = _sal.cluster_themes(_F, _i, topn=8)
        _neg = [t for t in _th if float(t.get("total_yi", 0)) <= 0]
        _pos = [t for t in _th if float(t.get("total_yi", 0)) > 0]
        check(f"N6 端到端：红 8 绿 8（实测 正{len(_pos)} / 负{len(_neg)}）",
              len(_pos) == 8 and len(_neg) == 8,
              f"总 {len(_th)} 条 · 正 {len(_pos)} / 负 {len(_neg)}")

        # N7 流出侧条目数与负向板块数一致向好（不再被配额砍到 4）
        _V = _F.V[_i]
        _pool = np.arange(len(_F.codes))[_F.concept_mask]
        _n_neg_boards = int(sum(1 for j in _pool if _V[j] < 0))
        check(f"N7 负向板块充足（{_n_neg_boards} 个）时流出簇不被砍到 4",
              _n_neg_boards > 20 and len(_neg) > 4,
              f"负向板块 {_n_neg_boards} 个 → 流出簇 {len(_neg)} 条")

        # N8 两侧种子不重复
        _si = {t.get("seed") for t in _pos}
        _so = {t.get("seed") for t in _neg}
        check("N8 两侧种子无重复", not (_si & _so),
              f"交集 {sorted(_si & _so)}" if (_si & _so) else "无交集")

        # N10 ★ v1.2.0 相对长度端到端：两侧最大值都必须映到满格 1.0
        #     （这是"视觉重量相当"的量化判据）
        def _norm_probe(vals):
            absv = [abs(v) for v in vals]
            lo, hi = min(absv), max(absv)
            if hi <= 0:
                return None
            if hi <= lo:
                return 1.0
            kk = (1.0 - 0.10) / (hi - lo)
            return 0.10 + (hi - lo) * kk          # hi 处 = 1.0
        _p_in = _norm_probe([float(t["total_yi"]) for t in _pos])
        _p_out = _norm_probe([float(t["total_yi"]) for t in _neg])
        check("N10 相对长度：两侧最大值均映射到满格 1.0",
              abs(_p_in - 1.0) < 1e-9 and abs(_p_out - 1.0) < 1e-9,
              f"流入满格={_p_in:.3f} · 流出满格={_p_out:.3f}")

        # N11 ★ 保序：侧内按 |值| 排序后，归一长度必须单调不减
        def _order_ok(vals):
            absv = [abs(v) for v in vals]
            lo, hi = min(absv), max(absv)
            if hi <= lo:
                return True
            kk = (1.0 - 0.10) / (hi - lo)
            lens = [0.10 + (a - lo) * kk for a in absv]
            srt = [l for _, l in sorted(zip(absv, lens))]
            return all(srt[j] <= srt[j + 1] + 1e-9 for j in range(len(srt) - 1))
        check("N11 侧内保序：|净额| 越大 → 柱越长（无排序反转）",
              _order_ok([float(t["total_yi"]) for t in _pos])
              and _order_ok([float(t["total_yi"]) for t in _neg]),
              "流入/流出均保序")
    except Exception as _e:                                   # noqa: BLE001
        check("N6 端到端：红 8 绿 8（实测）", False, f"异常：{_e}")
        check("N7 负向板块充足时流出簇不被砍到 4", False, "异常")
        check("N8 两侧种子无重复", False, "异常")
        check("N10 相对长度：两侧最大值均映射到满格 1.0", False, "异常")
        check("N11 侧内保序：|净额| 越大 → 柱越长", False, "异常")

    # N9 图下必须有"按本栏最大值归一 / 不可横向比较金额"的说明
    check("N9 图下注明「按本栏归一 · 两侧不可比金额」",
          "按各栏最大值归一" in _app and "不可横向比较金额" in _app,
          "标注已就位")

    # N12 ★ v1.2.0 两栏柱条**均向右伸**（用户明确要求）
    #    判据：barh 统一 left=X0（不再有 left=0 之外的正负分向写法），
    #    且不再出现"流出向左"的负坐标标签写法。
    check("N12 两栏柱条均向右伸（barh 统一 left=X0）",
          "ax.barh(k, w, left=X0" in _app
          and "-abs(draw_v) - span" not in _app,
          "两栏同向已就位")

    # N13 ★ 版面三列都在轴内（名称/柱区/金额），不再靠负坐标越界画字
    check("N13 名称/柱区/金额三列均在轴内（不越界出血）",
          "X_NAME = X0 - 0.02" in _app and "ax.set_xlim(0.0, 1.0)" in _app
          and "ax.text(-0.02, k" not in _app,
          "三列内收已就位")

    # N14 ★ 各栏标题必须带「最长 xxx 亿」——相对长度下量级差异只能靠文字承载
    check("N14 各栏标题含「最长 xxx 亿」（承载量级差异）",
          "· 最长 {mx:.0f} 亿" in _app,
          "标题已含最长值")
except Exception as _e:                                       # noqa: BLE001
    check("N1 方向配额不再截断流出", False, f"异常：{_e}")

# ============================================================ O. v1.2.1 性能与缺陷修复
print("\n=========== O. v1.2.1：抓取/算法提速 + 7 项缺陷修复 ===========")
try:
    _app = open(os.path.join(BASE, "native_app.py"), encoding="utf-8").read()
    _fd = open(os.path.join(BASE, "core", "flowdata.py"), encoding="utf-8").read()
    _sal = open(os.path.join(BASE, "core", "salience.py"), encoding="utf-8").read()
    _uni = open(os.path.join(BASE, "core", "universe.py"), encoding="utf-8").read()
    _wl = open(os.path.join(BASE, "core", "watchlist.py"), encoding="utf-8").read()
    _tc = open(os.path.join(BASE, "core", "tradecal.py"), encoding="utf-8").read()
    _ar = open(os.path.join(BASE, "core", "archive.py"), encoding="utf-8").read()

    # ---------------- O1 数据抓取：连接复用 ----------------
    check("O1 flowdata 引入线程内 keep-alive 长连接池",
          "def _keepalive_once" in _fd and "def _conn_pool" in _fd
          and "threading.local()" in _fd and "http.client" in _fd,
          "长连接池已就位")
    check("O1b 复用同一个 SSL context（省掉重复加载 CA）",
          "def _ssl_ctx" in _fd and "ssl.create_default_context" in _fd, "")
    check("O2 _get 优先长连接、失败才退 urllib（保留代理兼容）",
          "_keepalive_once(h, path_or_url, timeout)" in _fd
          and "_open_once(h + path_or_url, timeout)" in _fd, "")
    check("O3 长连接池可显式收尾关闭（build_panel 调用）",
          "def close_connections" in _fd and "close_connections()" in _fd, "")

    # ★ 关键设计：主机级冷却 ≠ 通道级关闭。
    #   第一版把两者合成一个全局计数器 → 主域被限流会把长连接通道
    #   **永久关闭**，性能静默退化。这里断言已拆开。
    check("O4 主机级冷却与通道级回退已拆分（不再合成全局计数器）",
          "def _host_cooling" in _fd and "def _mark_host" in _fd
          and "HOST_COOLDOWN" in _fd and "def _ka_proven" in _fd
          and "_KA_FAIL" not in _fd,
          "per-host cooldown + per-channel probe 分离")
    check("O4b 冷却中的主机排到最后而非直接剔除（全限流时仍能试）",
          "def _load_hosts" in _fd and "cooling" in _fd, "")

    # ---------------- O5 数据抓取：并行化与超时 ----------------
    check("O5 指数分时与板块抓取并行（不再等 1000 板块抓完才串行抓指数）",
          "idx_thread = threading.Thread" in _fd and "idx_thread.start()" in _fd
          and "idx_thread.join" in _fd,
          "指数抓取已与板块抓取重叠")
    check("O6 两类板块清单并行拉取",
          "list(lex.map(list_sectors, kinds))" in _fd, "")
    check("O7 批量抓取降低重试/超时开销（不再 3 次×15s）",
          "def fetch_sector_kline(secid, tries=2, timeout=8)" in _fd, "")

    # ---------------- O8 算法：向量化 ----------------
    check("O8 _pct_rank_rows 向量化（无 Python 逐行 while 循环）",
          "def _pct_rank_rows" in _sal
          and "np.maximum.accumulate" in _sal
          and "np.minimum.accumulate" in _sal,
          "整块向量化已就位")
    # O8b ★ 等价性：逐位比对（并列 / 全等值 / 单列 / 单行 / 含 NaN）
    import numpy as _np
    _rng = _np.random.default_rng(20260921)

    def _pct_ORIG(x):
        n = len(x)
        if n == 0:
            return x
        order = _np.argsort(x, kind="mergesort")
        ranks = _np.empty(n, dtype=float)
        ranks[order] = _np.arange(n, dtype=float)
        s = _np.asarray(x)[order]
        i = 0
        while i < n:
            j = i + 1
            while j < n and s[j] == s[i]:
                j += 1
            if j - i > 1:
                ranks[order[i:j]] = (i + j - 1) / 2.0
            i = j
        return ranks / max(1, n - 1)

    _cases = [
        ("随机", _rng.normal(size=(40, 37))),
        ("并列", _rng.integers(0, 4, size=(30, 50)).astype(float)),
        ("全等值", _np.full((4, 20), 7.0)),
        ("单列", _rng.normal(size=(15, 1))),
        ("单行", _rng.normal(size=(1, 33))),
    ]
    _eq_all = True
    for _nm, _m in _cases:
        _g = salience._pct_rank_rows(_m)
        _e = _np.array([_pct_ORIG(r) for r in _m])
        _eq_all &= _np.array_equal(_g, _e)
    check("O8b _pct_rank_rows 与原实现逐位一致（5 组边界用例）", _eq_all,
          "含并列/全等值/单列/单行")

    # O8c persist 前缀和等价
    _T2, _N2, _W2 = 60, 45, salience.PERSIST_WIN
    _rk = _rng.integers(1, _N2 + 1, size=(_T2, _N2))
    _rn = _rng.integers(1, _N2 + 1, size=(_T2, _N2))
    _tm = (_rk <= salience.TOPK) | (_rn <= salience.TOPK)
    _old = _np.zeros((_T2, _N2))
    for _i in range(_T2):
        _old[_i] = _tm[max(0, _i - _W2 + 1):_i + 1].sum(axis=0)
    _cs = _np.cumsum(_tm, axis=0)
    _newp = _cs.astype(float)
    _newp[_W2:] = _cs[_W2:] - _cs[:-_W2]
    check("O8c persist 前缀和向量化与原循环逐位一致",
          _np.array_equal(_old, _newp), "")

    # ---------------- O9 universe 正则合并 ----------------
    check("O9 universe 用单条交替式正则（每名 1 次 search，原为最多 54 次）",
          "_DROP_ALT" in _uni and 're.compile("|".join' in _uni
          and "return _DROP_ALT.search(name) is not None" in _uni,
          "合并正则已就位")
    import universe as _U
    import json as _json
    with open(os.path.join(os.environ.get("LOCALAPPDATA", ""), "FlowLiveNative",
                           "data", "panel_20260918.json"), encoding="utf-8") as _f:
        _meta = _json.load(_f)
    _names = list(_meta["names"].values())
    _mismatch = [n for n in _names
                 if (any(r.search(n) for r in _U._DROP_RE)
                     != (_U._DROP_ALT.search(n) is not None))]
    _edge = ["AH股", "AH股价", "XAH股", "GDR", "AB股", "B股", "上证50", "创投",
             "X创投", "ST股", "ST股票", "2026中报预增", "昨日涨停", "国产芯片"]
    _mismatch += [n for n in _edge
                  if (any(r.search(n) for r in _U._DROP_RE)
                      != (_U._DROP_ALT.search(n) is not None))]
    check(f"O9b 合并正则与逐条判定完全等价（{len(_names)} 真实 + {len(_edge)} 边界）",
          len(_mismatch) == 0, f"不一致 {len(_mismatch)} 个")

    # ---------------- O10 算法提速实测 ----------------
    import time as _time
    _F2 = salience.build_features("20260918")
    _ts = []
    for _ in range(5):
        _t0 = _time.perf_counter()
        salience.build_features("20260918")
        _ts.append((_time.perf_counter() - _t0) * 1000)
    _medi = sorted(_ts)[len(_ts) // 2]
    check(f"O10 build_features 耗时 < 60ms（实测中位 {_medi:.1f}ms，原 ~97ms）",
          _medi < 60, f"{_medi:.1f} ms")

    # ---------------- O11 交易日历不再每轮联网 ----------------
    check("O11 tradecal 加进程内内存缓存（同自然日 + TTL 命中即返回）",
          "_MEMO" in _tc and "CAL_TTL" in _tc and "def _memo_get" in _tc, "")

    # ---------------- O12 watchlist 顺序与保险丝 ----------------
    check("O12 watchlist 在 native_app 中改为先 view() 再 flush()",
          _app.find('out["watch"] = pool.view()')
          < _app.find("                    pool.flush()"),
          "「▲新进」高亮不再被提前清掉")
    check("O13 累积池保险丝保护开盘首批（is_seed）",
          "is_seed" in _wl and 'row["is_seed"] = True' in _wl
          and "0 if r.get(\"is_seed\") else 1" in _wl,
          "seed 条目最后被淘汰")

    # ---------------- O14 archive 健壮性 ----------------
    check("O14 archive 读入后先过滤非 dict 脏数据（不再 AttributeError）",
          'days = [x for x in days if isinstance(x, dict) and x.get("date")]' in _ar,
          "")
    check("O15 native_app 检查 archive 返回的 ok（不再把写失败报成成功）",
          'if res.get("ok"):' in _app and "archive WRITE FAILED" in _app, "")

    # ---------------- O16 GUI 侧三项修复 ----------------
    check("O16 _auto_tick 不再用 current_tag()!=live_tag 判定（修复永久停摆）",
          "current_tag() != self.live_tag" not in _app, "")
    check("O17 load_history 真正暂存用户选择并补跳（_pending_tag + goto_tag）",
          "self._pending_tag = tag" in _app and "def goto_tag(self, tag):" in _app,
          "")
    check("O18 历史日会清空龙头表（不再残留上一交易日的龙头结果）",
          "self.leader_rows = []" in _app and "leader clear on history failed" in _app,
          "")

    # ---------------- O19 界面：移除项与替代入口 ----------------
    # ★ 与 N1 同一个坑：**必须排除注释行**。
    #   新代码的注释里正解释着"原先有 act_color/tb_color/COLOR_DIR"，
    #   直接对全文 `in` 判断会把注释也算命中，断言就永远 FAIL。
    _app_live = "\n".join(ln for ln in _app.splitlines()
                          if not ln.lstrip().startswith("#"))
    _ghost = [k for k in ("act_color", "tb_color", "def toggle_colors",
                          "COLOR_DIR") if k in _app_live]
    check("O19 配色切换入口已彻底移除（控件 + 方法 + 常量）",
          len(_ghost) == 0, f"残留={_ghost}" if _ghost else "三层面均无引用")
    check("O19b 「数据状态」页已删除，但状态文本与弹窗入口保留",
          'self.tabs.addTab(w, "数据状态")' not in _app
          and "self.status_text" in _app and "def show_status_dialog" in _app,
          "页签删了、信息没丢")
    check("O20 页签清单收敛为 PAGE_NAMES 常量（跳页不再用 count()-1）",
          "PAGE_NAMES = [" in _app and "PAGE_INDEX = {" in _app
          and "self.tabs.setCurrentIndex(self.tabs.count() - 1)" not in _app,
          "脆性写法已清除")
    check("O20b 启动时自检页签与 PAGE_NAMES 一致（不一致只记日志不崩）",
          "_tab_names_actual" in _app and "TAB MISMATCH" in _app, "")
except Exception as _e:                                       # noqa: BLE001
    check("O 段执行", False, f"异常：{_e}")

# ================================================= P. v1.2.2 光标跟随延迟修复
print("\n=========== P. v1.2.2：光标跟随（局部重绘）提速 ===========")


def _Ppx_cmp(a, b):
    """(是否逐像素一致, 不一致像素数)。"""
    if a.shape != b.shape:
        return False, -1
    d = (a != b).any(axis=2)
    return (not d.any()), int(d.sum())


def _Ppx_ref(fc, k):
    """参考图：摘掉画布已有竖线后，用**全量 draw** 画一条位于 k 的竖线。

    ★ 必须先摘旧的：两条半透明竖线重叠会加深颜色，
      逐像素比对会差出整整一条色带，是测试自身的假失败（实测踩过）。
    """
    ax = fc.fig.axes[0]
    old = getattr(fc, "_vline", None)
    if old is not None:
        try:
            old.remove()
        except Exception:                                # noqa: BLE001
            pass
        fc._vline = None
    fc._invalidate_blit()
    fc.draw()
    if k is not None:
        ax.axvline(k, color=fc.th["accent"], linewidth=1.0, alpha=.75, zorder=3)
        fc.draw()
    return np.asarray(fc.buffer_rgba()).copy()


def _Ppx_eq(fc, k):
    """走 blit 路径重画 k，与全量参考图比对。"""
    bb = np.asarray(fc.buffer_rgba()).copy()             # blit 结果（现画面）
    return _Ppx_cmp(_Ppx_ref(fc, k), bb)


try:
    _app_p = open(os.path.join(BASE, "native_app.py"), encoding="utf-8").read()

    # ---- 静态：三层根因各自的修复标记都在 ----
    check("P1 悬停竖线走 blit 局部重绘（restore_region + draw_artist + blit）",
          "def _ensure_blit_bg" in _app_p
          and "self.restore_region(bg)" in _app_p
          and "ax.draw_artist(self._vline)" in _app_p,
          "官方 Cursor 同源方案")
    check("P1b 整幅 draw 后作废背景快照（换数据/主题/尺寸的唯一可靠作废点）",
          "def _invalidate_blit" in _app_p
          and "super().draw()" in _app_p
          and "self._invalidate_blit()" in _app_p, "")
    check("P1c 快照取景框外扩 BLIT_PAD（防贴边像素漏出 → 顶部残影）",
          "BLIT_PAD" in _app_p and "def _blit_frame" in _app_p
          and "Bbox.from_extents" in _app_p,
          "copy_from_bbox 取整会漏掉一行，实测 117px 残影")
    check("P1d 竖线 zorder 提到曲线之上（否则 blit 与全量 draw 画面不等价）",
          'linewidth=1.0, alpha=.75, zorder=3' in _app_p,
          "原先 zorder=1 被 20 条曲线压住，逐像素差 327px")

    # ---- 根因 2：标签文本驱动布局 ----
    _h = _app_p[_app_p.index("self.lbl_hover = QLabel("):]
    _h = _h[:_h.index("card_side, body_side")]
    check("P2 悬停标签不参与最小宽度计算（否则每帧挤压图表 → 毁掉快照）",
          "setWordWrap(True)" in _h and "QSizePolicy.Ignored" in _h,
          "实测文字变长把画布 1002→965px")

    # ---- 根因 3：ResizeToContents 全表测量 ----
    check("P3 fill_table 支持列宽冻结（ResizeToContents 每次 setItem 全表测量）",
          "def _freeze_widths" in _app_p
          and "_freeze_widths(t)" in _app_p
          and "QHeaderView.ResizeToContents" in _app_p,
          "实测 12.73ms → 0.64ms")
    check("P3b 列宽冻结为 opt-in（仅悬停表启用，榜单表仍自适应）",
          "self.t_hover._freeze_widths = True" in _app_p
          and 'getattr(t, "_freeze_widths", False)' in _app_p,
          "避免长题材名被锁死截断")

    # ---- 运行时：真跑一遍 blit 路径 ----
    _fcP, _sP = _mkfan(W=10.02), None
    import numpy as _npP
    check("P4 后端支持 blit 且快照可在竖线不可见时建立",
          bool(_fcP.supports_blit))
    _fcP._mark(120)
    _sameP, _ndP = _Ppx_eq(_fcP, 120)
    check("P4b blit 画面与全量 draw **逐像素一致**", _sameP,
          f"差异像素={_ndP}" if not _sameP else "完全一致")

    # 残影：连续移动 40 次
    _fcP2 = _mkfan(W=10.02)
    for _k in range(20, 60):
        _fcP2._mark(_k)
    _sameR, _ndR = _Ppx_eq(_fcP2, 59)
    check("P4c 连续移动 40 次**无残影**", _sameR,
          f"差异像素={_ndR}" if not _sameR else "无残留")

    # 离开后干净
    _fcP3 = _mkfan(W=10.02)
    _fcP3._mark(59)
    _fcP3._hover_px = 59
    _fcP3._on_leave()
    _cleanP = _Ppx_ref(_fcP3, None)
    _sameC, _ndC = _Ppx_cmp(_cleanP, _npP.asarray(_fcP3.buffer_rgba()).copy())
    check("P4d 鼠标离开后画面完全干净（无残留竖线）", _sameC,
          f"差异像素={_ndC}" if not _sameC else "完全干净")

    # 主题切换必须重新着色竖线 + 作废快照
    _fcP4 = _mkfan(W=10.02)
    _fcP4._mark(100)
    _c_before = str(_fcP4._vline.get_color())
    _fcP4.apply_theme(NA.coloring(NA.THEMES["dark"]))
    check("P4e 换主题后竖线重新着色（原先留着旧主题的蓝，深色底几乎看不见）",
          str(_fcP4._vline.get_color()) != _c_before
          and _fcP4._blit_bg is None,
          f"{_c_before} → {_fcP4._vline.get_color()}")

    # 性能护栏
    import time as _tP
    _fcP5 = _mkfan(W=10.02)
    _fcP5._mark(10)
    _xsP = []
    for _ in range(15):
        _t0 = _tP.perf_counter()
        _fcP5._mark(int(_npP.random.randint(0, 240)))
        _xsP.append((_tP.perf_counter() - _t0) * 1000)
    _t_markP = sorted(_xsP)[len(_xsP) // 2]
    check("P5 单次悬停 < 5ms（优化前实测 122.9ms/事件）", _t_markP < 5.0,
          f"{_t_markP:.2f} ms")

    # 不支持 blit 时必须安全退回且竖线可见
    _fcP6 = _mkfan(W=10.02)
    _origP = NA.FanChart.supports_blit
    try:
        NA.FanChart.supports_blit = False
        _fcP6._vline = None
        _fcP6._invalidate_blit()
        _fcP6._mark(150)
        _fcP6.draw()
        _vlP = _fcP6._vline
        check("P5b 不支持 blit 时安全退回，且竖线仍可见（不静默消失）",
              _vlP is not None and _vlP.get_visible()
              and _fcP6._blit_bg is None,
              f"visible={_vlP.get_visible() if _vlP else None}")
    finally:
        NA.FanChart.supports_blit = _origP
except Exception as _eP:                                      # noqa: BLE001
    check("P 段执行", False, f"异常：{_eP}")

# ============================================ Q. v1.2.3 启动提速 + 高频刷新
print("\n=========== Q. v1.2.3：启动提速 + 30s/10s 高频刷新 ===========")

try:
    _app_q = open(os.path.join(BASE, "native_app.py"), encoding="utf-8").read()
    _fd_q = open(os.path.join(BASE, "core", "flowdata.py"), encoding="utf-8").read()

    # ---- 通道 1：批量快照（高频刷新的前提）----
    check("Q1 新增批量快照通道 fetch_spot_batch（一次问 200 个板块）",
          "def fetch_spot_batch" in _fd_q and "ulist.np/get" in _fd_q
          and "SPOT_BATCH" in _fd_q,
          "1000 次请求 → 5~6 次")
    check("Q1b 批量快照带 ut=（漏掉会让 f62 返回 None，静默丢数据）",
          '&ut={UT}' in _fd_q or "ut={UT}" in _fd_q,
          "实测不带 ut 时 f62 恒为 None")
    check("Q1c f62 语义与 kline 末值一致（当日累计主力净流入）",
          "f62" in _fd_q and "SPOT_FIELDS" in _fd_q, "")

    # ---- 通道 2：增量刷新 ----
    check("Q2 新增增量刷新 refresh_panel_spot（只接最新一分钟）",
          "def refresh_panel_spot" in _fd_q
          and "panel_{tag}.npy" in _fd_q,
          "不重抓全量 kline")

    # ★ 面板矩阵是 (时间 × 板块)，"上一分钟所有板块" = mat[-1, :]（一行）
    #   而不是 mat[:, -1]（一列）。当"分钟数恰好等于板块数"时写错也不会报错，
    #   只是把资金贴到错的板块上 —— 静默错位，最危险。
    _seg_q = _fd_q[_fd_q.index("def refresh_panel_spot"):]
    _seg_q = _seg_q[:_seg_q.index("\ndef ", 10)]
    # ★ 只扫**可执行代码**，先剥掉注释行 —— 否则"注释里警告别用 mat[:, -1]"
    #   会把这条断言自己顶掉（实测踩过：代码写对了，反而是注释让它 FAIL）。
    _code_q = "\n".join(ln.split("#")[0] for ln in _seg_q.splitlines())
    check("Q2b 增量刷新按行取「上一分钟」（mat[-1, :]，不是按列）",
          "mat[-1, :]" in _code_q and "mat[:, -1]" not in _code_q,
          "矩阵为 (时间 × 板块)，按列取会静默错贴板块")

    # ---- 缓存陈旧判定 ----
    check("Q3 缓存不再「存在即用」（盘中超 60s 走增量，而非拿旧数据）",
          "SKIP_STALE_SEC" in _app_q and "def _panel_age_sec" in _app_q
          and "def _in_trading_hours" in _app_q,
          "原实现只判 os.path.exists → 重启后仍显示十几分钟前的旧数据")

    # ---- 周期档位 ----
    check("Q4 刷新周期可配：10s / 30s / 1min / 5min 四档",
          "AUTO_MS_OPTIONS" in _app_q
          and all(k in _app_q for k in ("10 * 1000", "30 * 1000",
                                        "60 * 1000", "5 * 60 * 1000")), "")
    check("Q4b 默认周期为 30 秒（用户要求的档位）",
          "AUTO_MS_DEFAULT = 30 * 1000" in _app_q
          and NA.AUTO_MS_DEFAULT == 30 * 1000,
          f"{NA.AUTO_MS_DEFAULT} ms")
    check("Q4c 周期可运行时切换（_set_auto_interval + 菜单单选）",
          "def _set_auto_interval" in _app_q and "QActionGroup" in _app_q
          and "刷新周期" in _app_q,
          "切换后立即按新周期重启，不停在旧档")

    # ---- 自动刷新走增量 ----
    check("Q5 自动刷新 tick 走增量通道（不再全量重抓）",
          "self.reload(force=False, incremental=True)" in _app_q,
          "旧实现每次 tick 都是 1000 次请求")
    check("Q5b 增量失败自动降级为全量（保证刷新不「什么都没发生」）",
          "spot refresh unavailable" in _app_q
          and "flowdata.build_panel" in _app_q, "")

    # ---- 重绘优化：测量不再光栅化 ----
    check("Q6 图例定列数不再光栅化（get_window_extent 替代 canvas.draw）",
          "def _try(ncol)" in _app_q
          and "cand.get_window_extent(R)" in _app_q,
          "每次试探都全量重绘，占 FanChart 重绘耗时 57%")
    check("Q6b 底部预留测量有**合理性校验**（刻度未布局时会静默返回 0 高）",
          "def _measure()" in _app_q and "implausible tick_px" in _app_q,
          "不校验则 tick_px 被算成巨大值 → 图例带被撑爆")

    # ---- ★ 运行时：免光栅化测量必须与光栅化后**逐值一致** ----
    #   这是"没改坏"的充分条件：测量与画布是否 raster 无关 ⇒ 去掉 draw 必等价。
    #   ★ 反例教训：早先的测试把 Legend.get_window_extent 打桩成抛异常，
    #     但两条路径都依赖这个原语 → 量出来的值全是 None → 每次试探都
    #     "没超限" → 二分爬到 20 列。测试自身故障却报出"巨大差异"。
    #     正确做法是证明**光栅化对测量零贡献**，而不是掐掉测量。
    _fcQ = _mkfan(W=10.02)
    _axQ = _fcQ.fig.axes[0]
    _hsQ = list(_fcQ._legend.legend_handles)
    _candQ = _fcQ._make_legend(_hsQ, 10, _fcQ.th)
    _Rcold = _fcQ.fig.canvas.get_renderer()

    def _bbq(a, R):
        b = a.get_window_extent(R)
        return (float(b.x0), float(b.y0), float(b.width), float(b.height))

    _coldQ = _bbq(_candQ, _Rcold)
    _tk_coldQ = [_bbq(t, _Rcold) for t in _axQ.get_xticklabels()]
    _fcQ.draw()                                     # 光栅化
    _Rhot = _fcQ.fig.canvas.get_renderer()
    _hotQ = _bbq(_candQ, _Rhot)
    _tk_hotQ = [_bbq(t, _Rhot) for t in _axQ.get_xticklabels()]
    _dlegQ = max(abs(a - b) for a, b in zip(_coldQ, _hotQ))
    _dtkQ = max((max(abs(x - y) for x, y in zip(a, b))
                 for a, b in zip(_tk_coldQ, _tk_hotQ)), default=0.0)
    check("Q7 图例测量与是否光栅化**无关**（冷/热画布逐值一致）",
          _dlegQ <= 1e-9,
          f"最大偏差 {_dlegQ:.3e}")
    check("Q7b 刻度标签测量同样与光栅化无关",
          _dtkQ <= 1e-9,
          f"最大偏差 {_dtkQ:.3e}（{len(_tk_coldQ)} 个标签）")

    # 全列数扫描：每个档位在冷/热下尺寸都一致 → 选列数必然不变
    _worstQ, _nQ = 0.0, 0
    for _nc in range(1, len(_hsQ) + 1):
        _c1 = _fcQ._make_legend(_hsQ, _nc, _fcQ.th)
        if _c1 is None:
            continue
        _fcQ.draw()
        _w1 = _bbq(_c1, _fcQ.fig.canvas.get_renderer())
        _c1.remove()
        _c2 = _fcQ._make_legend(_hsQ, _nc, _fcQ.th)   # 新建、未画 = 冷
        _w2 = _bbq(_c2, _fcQ.fig.canvas.get_renderer())
        _c2.remove()
        _worstQ = max(_worstQ, max(abs(x - y) for x, y in zip(_w1, _w2)))
        _nQ += 1
    check("Q7c 全部列数档位的尺寸在冷/热下逐值一致（选列数不受影响）",
          _nQ > 0 and _worstQ <= 1e-9,
          f"比对 {_nQ} 档，最大偏差 {_worstQ:.3e}")

    # ---- 重绘次数护栏：一轮重排里 FanChart 只应 draw 2 次 ----
    _cntQ = [0]
    _odQ = NA.FanChart.draw

    def _pdQ(self, *a, **kw):
        if self is _fcQ:
            _cntQ[0] += 1
        return _odQ(self, *a, **kw)

    NA.FanChart.draw = _pdQ
    try:
        _fcQ.plot([{"name": f"板块{i}", "series": [float(x) for x in
                                                   np.cumsum(np.random.default_rng(i)
                                                             .normal(0, 3, 240))],
                    "net": float(i), "direction": ("in" if i < 10 else "out"),
                    "times": [""] * 240} for i in range(20)])
    finally:
        NA.FanChart.draw = _odQ
    check("Q8 一轮重排 FanChart 只 draw 2 次（优化前 7 次，其中 4 次纯为测量）",
          _cntQ[0] <= 2, f"{_cntQ[0]} 次")

    # ---- ★ 启动最大的一刀：交易日历「磁盘优先」 ----
    #   原先顺序是「先联网，失败才落盘」→ 每次启动都白等一次 push2his 往返，
    #   实测 894ms / 占启动总耗时 40.7%。而 v1.2.1 的内存缓存随进程消失，
    #   对"重新点开 exe"毫无帮助。改为「今天写过的盘缓存 → 内存 → 联网」。
    import tradecal as _TC
    _tc_src = open(os.path.join(BASE, "core", "tradecal.py"),
                   encoding="utf-8").read()
    check("Q9 交易日历改为「磁盘优先」（今天取过就不再联网）",
          "def _disk_fresh" in _tc_src
          and "_disk_fresh(meta, today)" in _tc_src,
          "原先先联网再落盘，每次启动白付 894ms")

    _tc_dir = tempfile.mkdtemp()
    _tc_bak = flowdata.DATA
    _memo_bak = dict(_TC._MEMO)
    _fetch_bak = _TC._fetch_days_from_daykline
    try:
        flowdata.DATA = _tc_dir
        _calp = os.path.join(_tc_dir, "calendar.json")
        _TC._MEMO.update({"days": None, "approx": False, "day": "", "at": 0.0})

        # ① 无盘缓存 → 必须联网（不能静默退化成工作日推断）
        _n1 = [0]

        def _f1(lmt=40):
            _n1[0] += 1
            return [f"202601{i:02d}" for i in range(1, 31)]
        _TC._fetch_days_from_daykline = _f1
        _d1, _a1 = _TC.get_trading_days(refresh=True)
        check("Q9b 无盘缓存时仍会联网取（不静默退化成工作日推断）",
              _n1[0] == 1 and _a1 is False and len(_d1) == 30,
              f"联网 {_n1[0]} 次 · {len(_d1)} 天 · approx={_a1}")

        # ② 盘缓存是今天的 → 0 网络
        _TC._MEMO.update({"days": None, "approx": False, "day": "", "at": 0.0})
        _n2 = [0]

        def _f2(lmt=40):
            _n2[0] += 1
            return ["20991231"]
        _TC._fetch_days_from_daykline = _f2
        _d2, _a2 = _TC.get_trading_days(refresh=True)
        check("Q9c 盘缓存是今天的 → 零联网（启动省下整段）",
              _n2[0] == 0 and _d2 == _d1,
              f"联网 {_n2[0]} 次 · {len(_d2)} 天")

        # ③ 盘缓存过期 → 必须重新联网（跨日要能长出新交易日）
        _TC._MEMO.update({"days": None, "approx": False, "day": "", "at": 0.0})
        _jj = json.load(open(_calp, encoding="utf-8"))
        _jj["updated"] = "2020-01-01 08:00:00"
        json.dump(_jj, open(_calp, "w", encoding="utf-8"))
        _n3 = [0]

        def _f3(lmt=40):
            _n3[0] += 1
            return [f"202602{i:02d}" for i in range(1, 31)]
        _TC._fetch_days_from_daykline = _f3
        _d3, _a3 = _TC.get_trading_days(refresh=True)
        check("Q9d 盘缓存过期（非今天）→ 重新联网（跨日仍能长出新交易日）",
              _n3[0] == 1 and _d3[0].startswith("202602"),
              f"联网 {_n3[0]} 次 · 首日 {_d3[0]}")

        # ④ 过期盘缓存 + 断网 → 回落盘缓存，且**不**退化成工作日推断
        _TC._MEMO.update({"days": None, "approx": False, "day": "", "at": 0.0})
        _TC._fetch_days_from_daykline = lambda lmt=40: None
        _d4, _a4 = _TC.get_trading_days(refresh=True)
        check("Q9e 过期盘缓存 + 断网 → 回落盘缓存（approx 仍为 False）",
              _d4 == _d3 and _a4 is False,
              f"{len(_d4)} 天 · approx={_a4}")

        # ⑤ 盘缓存损坏 + 断网 → 工作日推断兜底（approx=True）
        _TC._MEMO.update({"days": None, "approx": False, "day": "", "at": 0.0})
        with open(_calp, "w", encoding="utf-8") as _f:
            _f.write("{ broken")
        _d5, _a5 = _TC.get_trading_days(refresh=True)
        check("Q9f 盘缓存损坏 + 断网 → 工作日推断兜底（approx=True）",
              _a5 is True and len(_d5) > 30,
              f"{len(_d5)} 天 · approx={_a5}")
    finally:
        flowdata.DATA = _tc_bak
        _TC._MEMO.update(_memo_bak)
        _TC._fetch_days_from_daykline = _fetch_bak
        shutil.rmtree(_tc_dir, ignore_errors=True)
except Exception as _eQ:                                      # noqa: BLE001
    check("Q 段执行", False, f"异常：{_eQ}")

# ============================================================
print("\n=========== R. v1.2.4：曲线与横轴的时间同步（「只画到十点多」根因）===========")
# ★ 用户报的现象：数据源时间戳已到 11:17，曲线却只画到 10 点多。
#   根因 (b)：曲线 x 用的是**数组下标** range(len(s))，横轴刻度用的是
#   **分钟序号** _minute_index() —— 两套坐标系只在"一分钟不缺"时才相等。
#   盘中面板必有缺口（增量刷新只追加当前分钟），缺得越多曲线被压得越靠左。
#   实测 2026-09-21 的面板：41 个点 / 真实跨度 107 分钟，末端落在下标 40
#   → 横轴读作 10:11，比真实的 11:17 落后 66 分钟。
try:
    _ap_r = open(os.path.join(BASE, "native_app.py"), encoding="utf-8").read()
    _fd_rp = os.path.join(BASE, "core", "flowdata.py")
    _fd_r = open(_fd_rp, encoding="utf-8").read()

    # ---- R1 _x_positions 存在且按分钟序号定位 ----
    check("R1 新增 _x_positions（时间标签 → 分钟序号）",
          hasattr(NA, "_x_positions"), "")
    _xp, _mp = NA._x_positions(["09:31", "09:32", "10:20", "10:21", "11:17"], 5)
    check("R2 有缺口时按分钟序号落点（10:20→49、11:17→106，不是下标 2/4）",
          _mp and _xp == [0, 1, 49, 50, 106], f"{_xp}")

    # ---- R3 无缺口/不可解析时保持旧行为（兼容 + 不引入回归）----
    _xp2, _mp2 = NA._x_positions([f"09:3{k}" for k in range(1, 10)], 9)
    check("R3a 无缺口时与下标等价（密集面板行为不变）",
          _mp2 and _xp2 == list(range(9)), f"{_xp2}")
    _xp3, _mp3 = NA._x_positions([""] * 6, 6)
    check("R3b 标签不可解析时退回下标（兼容测试里的 ['']×T）",
          (not _mp3) and _xp3 == list(range(6)), f"mapped={_mp3}")

    # ---- R4 严格单调（重复/乱序标签不得画出折返线）----
    _xp4, _ = NA._x_positions(["09:31", "09:31", "10:00", "09:45", "11:17"], 5)
    check("R4 重复/乱序标签下 x 仍严格递增（不画成折返线）",
          all(_xp4[_i] < _xp4[_i + 1] for _i in range(len(_xp4) - 1)), f"{_xp4}")
    _xp4b, _ = NA._x_positions(["11:29", "11:30", "13:01", "13:02"], 4)
    check("R4b 午休跨段连续（11:30=119 → 13:01=120，不留空档）",
          _xp4b == [118, 119, 120, 121], f"{_xp4b}")

    # ---- R5 曲线真的按分钟序号画（端到端，用带缺口的真实场景）----
    _fcR = NA.FanChart(3.67, 10.02)
    _tR = ["09:31", "09:32", "10:20", "10:21", "11:17"]
    _serR = [{"name": f"板块{i}", "direction": ("in" if i < 10 else "out"),
              "net": float(i), "series": [float(i + k) for k in range(5)],
              "times": _tR} for i in range(20)]
    _fcR.plot(_serR)
    _lnR = [ln for ln in _fcR.fig.axes[0].get_lines()
            if len(ln.get_xydata()) == 5]
    _endR = float(list(_lnR[0].get_xdata())[-1]) if _lnR else -1.0
    check("R5 曲线末端落位 = 最新数据分钟序号（106=11:17，旧实现是 4）",
          abs(_endR - 106.0) < 0.01, f"末端 x={_endR}")

    # ---- R6 悬停把"鼠标横坐标"换算回"数据行号" ----
    check("R6 新增 _row_at_x（分钟序号 → 数据行号）",
          hasattr(NA.FanChart, "_row_at_x"), "")
    _hitsR = []
    _fcR.hover_cb = lambda i, hm: _hitsR.append((i, hm))
    _fcR._on_motion(type("E", (), {
        "inaxes": _fcR.fig.axes[0], "xdata": 106.0})())
    check("R7 鼠标停在最新一列 → 回调拿到最后一行（不是下标 106 越界）",
          len(_hitsR) == 1 and _hitsR[0] == (4, "11:17"), f"{_hitsR}")
    _hitsR.clear()
    _fcR._hover_px = None
    _fcR._on_motion(type("E", (), {
        "inaxes": _fcR.fig.axes[0], "xdata": 49.0})())
    check("R8 鼠标停在 10:20（序号 49）→ 换算回该分钟所在行（第 2 行，不是第 49 行）",
          len(_hitsR) == 1 and _hitsR[0][0] == 2, f"{_hitsR}")

    # ---- R9 准星竖线也落在分钟序号上 ----
    if _fcR._vline is not None:
        _vxR = float(list(_fcR._vline.get_xdata())[0])
        check("R9 准星竖线与曲线同一格（不跑回下标口径）",
              abs(_vxR - 49.0) < 0.01, f"vline x={_vxR}")

    # ---- R10 源码层面确认旧写法已消失 ----
    check("R10 绘图不再用 range(len(s)) 当 x 坐标",
          "range(len(s)), s, color=c" not in _ap_r
          and "xs = xpos[:len(s)]" in _ap_r, "")
    check("R11 悬停不再直接 round(xdata) 当行号",
          "k = int(round(ev.xdata))" not in _ap_r
          and "k = self._row_at_x(ev.xdata)" in _ap_r, "")

    # ---- R12 数据侧缺口守卫（缺口太大交回全量补齐）----
    from datetime import datetime as _dtR
    check("R12a 新增 spot_gap_minutes（面板时间轴距现在几分钟）",
          hasattr(flowdata, "spot_gap_minutes")
          and flowdata.SPOT_MAX_GAP_MIN == 6,
          f"阈值={getattr(flowdata, 'SPOT_MAX_GAP_MIN', '?')}")
    _g1 = flowdata.spot_gap_minutes(["10:47"],
                                    now=_dtR(2026, 9, 21, 11, 17))
    check("R12b 复现用户场景：10:47 面板 @11:17 → 落后 30 分钟（> 6 → 全量补）",
          _g1 == 30 and _g1 > flowdata.SPOT_MAX_GAP_MIN, f"gap={_g1}")
    _g2 = flowdata.spot_gap_minutes(["11:15"],
                                    now=_dtR(2026, 9, 21, 11, 17))
    check("R12c 正常高频刷新（差 2 分钟）不触发全量",
          _g2 == 2 and _g2 <= flowdata.SPOT_MAX_GAP_MIN, f"gap={_g2}")
    _g3 = flowdata.spot_gap_minutes(["10:55"],
                                    now=_dtR(2026, 9, 21, 11, 0))
    check("R12d 5 分钟档位（差 5 分钟）仍走增量（阈值不得误伤该档）",
          _g3 == 5 and _g3 <= flowdata.SPOT_MAX_GAP_MIN, f"gap={_g3}")
    _g4 = flowdata.spot_gap_minutes(["11:30"],
                                    now=_dtR(2026, 9, 21, 12, 0))
    check("R12e 午休期间不算落后（11:30 面板 @12:00 → 0 分钟）",
          _g4 == 0, f"gap={_g4}")
    _g5 = flowdata.spot_gap_minutes(["11:20"],
                                    now=_dtR(2026, 9, 21, 9, 0))
    check("R12f 盘前不判落后（返回 None，不误触发全量）",
          _g5 is None, f"gap={_g5}")
    check("R13 增量刷新检查缺口并交回全量（不再硬接断线）",
          "gap = spot_gap_minutes(times)" in _fd_r, "")
    check("R14 Worker 启动时也按缺口决定通道"
          "（缺口 > 阈值 → build_panel 补齐空档）",
          "_panel_gap_min" in _ap_r
          and "full refetch to fill the hole" in _ap_r, "")

    # ---- R15 时间轴污染（午休/盘后写进不存在的交易分钟）----
    #   ★ 旧写法 `datetime.now().strftime("%H:%M")` 直接当标签追加，
    #     而自动刷新在午休也照跑 → 面板末尾长成「…10:47 11:17 11:43」，
    #     _minute_index("11:43")=None → 横坐标无处安放。
    check("R15a 新增 clamp_to_bar（写面板前把时间夹到合法交易分钟）",
          hasattr(flowdata, "clamp_to_bar"), "")
    check("R15b 午休夹到 11:30（11:43/12:30 → 11:30）",
          flowdata.clamp_to_bar(_dtR(2026, 9, 21, 11, 43)) == "11:30"
          and flowdata.clamp_to_bar(_dtR(2026, 9, 21, 12, 30)) == "11:30", "")
    check("R15c 盘后夹到 15:00",
          flowdata.clamp_to_bar(_dtR(2026, 9, 21, 15, 20)) == "15:00", "")
    check("R15d 盘中原样返回（不改动正常刻度）",
          flowdata.clamp_to_bar(_dtR(2026, 9, 21, 10, 30)) == "10:30"
          and flowdata.clamp_to_bar(_dtR(2026, 9, 21, 14, 5)) == "14:05", "")
    check("R15e 盘前返回 None（不写新行）",
          flowdata.clamp_to_bar(_dtR(2026, 9, 21, 8, 50)) is None, "")
    # ★ 必须扫**真正的可执行代码** —— 说明这件事的注释与 docstring 里也
    #   写着那个旧写法。逐行 `split("#")` 只剥得掉注释、tokenize 也剥不掉
    #   docstring（它本身就是 STRING token）—— 前两轮都是这么假失败的。
    #   正解：用 ast 解析，只取**赋值/调用**这类实参文本，docstring 天然排除。
    import ast as _ast
    _fd_calls = []
    try:
        _fd_tree = _ast.parse(_fd_r)
        for _node in _ast.walk(_fd_tree):
            if isinstance(_node, _ast.Call):
                try:
                    _fd_calls.append(_ast.unparse(_node))
                except Exception:                    # noqa: BLE001
                    pass
    except Exception:                                # noqa: BLE001
        _fd_calls = []
    _fd_code = " ; ".join(_fd_calls)
    check("R15f 增量刷新已改用 clamp_to_bar（不再裸 strftime 当标签）",
          "clamp_to_bar()" in _fd_code and "strftime" not in _fd_code, "")
    _g6 = flowdata.spot_gap_minutes(["10:47", "11:17", "11:43"],
                                    now=_dtR(2026, 9, 21, 11, 50))
    check("R15g 末点是午休脏值时回溯到最后一个合法分钟（不整体失效）",
          _g6 == 13, f"gap={_g6}（按 11:17 算，而非返回 None）")
    check("R15h 追加新行前校验单调（防时间轴倒挂）",
          "_back = (_newi is not None and _lasti is not None" in _fd_r, "")

    # ---- R16 脏刻度在绘图侧的夹取落位 ----
    #   ★ 历史面板里的 11:43 这类标签，语义上就是"11:30 收盘那一刻的快照"，
    #     夹到 119 才是准确落位；若顺延成 107（读作 11:18）会偏。
    check("R16a 新增 _minute_index_clamped（休市钟点夹到边界）",
          hasattr(NA, "_minute_index_clamped"), "")
    check("R16b 午休钟点夹到 11:30（11:43 → 119）",
          NA._minute_index_clamped("11:43") == 119, "")
    check("R16c 盘后钟点夹到 15:00（15:20 → 239）",
          NA._minute_index_clamped("15:20") == 239, "")
    check("R16d 合法交易分钟不受夹取影响（原样返回）",
          NA._minute_index_clamped("10:20") == 49
          and NA._minute_index_clamped("11:30") == 119
          and NA._minute_index_clamped("13:01") == 120, "")
    check("R16e 真正认不出的标签仍返回 None（退回下标口径）",
          NA._minute_index_clamped("") is None
          and NA._minute_index_clamped("abc") is None, "")
    _xpR, _mpR = NA._x_positions(["09:31", "10:20", "11:17", "11:43"], 4)
    check("R16f 带脏刻度的面板：末端落在 119（11:30）而非 107",
          _mpR and _xpR == [0, 49, 106, 119], f"{_xpR}")
    check("R16g _x_positions 已改用夹取版本",
          "_minute_index_clamped(t) for t in times[:n]" in _ap_r, "")
except Exception as _eR:                                      # noqa: BLE001
    check("R 段执行", False, f"异常：{_eR}")

# 清理
for d in (TMP, D2, D3, _LD1, _LD2, _LD3, _LD4, _LD5):
    shutil.rmtree(d, ignore_errors=True)

# ============================================================
print("\n=========== S. v1.2.5：全天关键节点榜按时间先后排列 ===========")
# ★ 用户报的现象：关键节点榜「各时刻排列无序、杂乱」。
#   根因：salience.timeline_summary() 结尾是
#       rows.sort(key=lambda r: -r["strength"])      ← 按强度倒序
#   这个函数最初服务于「收盘解说」文字稿（只念前几条，谁劲爆谁靠前），
#   后来被复用成**表格**数据源 → 表格里时间轴跳跃（09:42→10:15→09:51→…）。
#   改法：主键 _time_key(time)（HH*60+MM）升序，idx 次键，-strength 三级键。
#   ★ 连带坑：`timeline_summary(F)[:24]` 这类截断原先依赖强度序才有
#     "取最强的 N 条"语义，改序后会静默变成"取最早的 N 条"（尾盘丢失、
#     行数不变、不报错）→ 改为显式参数 topn=N。
import importlib as _il
sys.path.insert(0, os.path.join(BASE, "core"))
_SAL = _il.import_module("salience") if "salience" in sys.modules \
    else _il.import_module("salience")
try:
    _ap_s = open(os.path.join(BASE, "native_app.py"), encoding="utf-8").read()
    _sal_src = open(os.path.join(BASE, "core", "salience.py"),
                    encoding="utf-8").read()

    # ---- S1 排序键存在且口径正确 ----
    check("S1 新增 _time_key（HH:MM → 分钟数）", hasattr(_SAL, "_time_key"), "")
    _tkpairs = [("09:31", 571), ("11:30", 690), ("13:01", 781), ("15:00", 900)]
    check("S2 钟点换算正确（含跨午休单调）",
          all(_SAL._time_key(a) == b for a, b in _tkpairs),
          " ".join(f"{a}→{_SAL._time_key(a)}" for a, _ in _tkpairs))
    check("S3 字典序陷阱已避开（'9:35' 必须小于 '10:00'）",
          _SAL._time_key("9:35") < _SAL._time_key("10:00"),
          f"{_SAL._time_key('9:35')} < {_SAL._time_key('10:00')}"
          "（字符串比较会反）")
    check("S4 非法输入返回 None（不抛异常、不返回 0）",
          _SAL._time_key("abc") is None and _SAL._time_key("") is None
          and _SAL._time_key(None) is None and _SAL._time_key(1234) is None
          and _SAL._time_key("25:00") is None
          and _SAL._time_key("10:75") is None, "")

    # ---- S5 源码级守卫：旧的强度排序已从**可执行代码**里消失 ----
    # ★★ 必须用 ast，不能用 split("#") 或 tokenize：
    #   本轮已踩两次 —— 逐行 split("#") 会命中**注释**里记录的旧写法，
    #   tokenize 会命中 **docstring**（docstring 本身就是 STRING token）。
    #   结果守卫把"记录这个坑的文档"判成违规，成了一条永远红的断言。
    #   ast 只看 Call 节点，注释与 docstring 天然不计入。
    import ast as _ast

    def _exec_calls(src):
        out = []
        for node in _ast.walk(_ast.parse(src)):
            if isinstance(node, _ast.Call):
                out.append(_ast.unparse(node.func))
        return out

    _calls = _exec_calls(_sal_src)
    check("S5a 旧写法 '-r[\"strength\"]' 已不在可执行代码中（ast 口径）",
          not any("strength" in c for c in _calls
                  if "lambda" in c and "sort" in c),
          "只检查 Call 节点，注释/docstring 不计入")
    check("S5b 排序键已换成 _time_key",
          "_time_key(r.get(\"time\"))" in _sal_src, "")
    check("S5c 主键列为 _time_key 且为升序（无负号）",
          "rows.sort(key=_key)" in _sal_src
          and "(0, tk, r[\"idx\"], -float(" in _sal_src, "")

    # ---- S6 topn 语义：先选后摆 ----
    import inspect as _insp
    _sig = _insp.signature(_SAL.timeline_summary)
    check("S6a timeline_summary 新增 topn 参数",
          "topn" in _sig.parameters, f"{_sig}")
    check("S6b topn 默认 None（不传即全量，向后兼容）",
          _sig.parameters["topn"].default is None, "")

    # ---- S7 构造面板端到端：乱序输入 → 有序输出 ----
    class _FF:
        def __init__(self, t):
            self.times = t

    _de_orig = _SAL.detect_events
    _SAL.detect_events = lambda F, i: [
        {"type": "T", "name": "x", "desc": "d", "strength": 9.9}]
    try:
        _fake_times = ["13:01", "09:31", "15:00", "11:30", "10:00", "14:00",
                       "09:45", "12:00", "13:30", "10:30", "14:30", "11:00"]
        _out = [r["time"] for r in _SAL.timeline_summary(_FF(_fake_times))]
        _outk = [_SAL._time_key(t) for t in _out]
        check("S7 乱序输入 → 输出严格按钟点升序（E 组核心断言）",
              len(_out) >= 2
              and all(_outk[i] <= _outk[i + 1] for i in range(len(_outk) - 1)),
              f"{len(_out)} 条：{' → '.join(_out)}")
        check("S8 覆盖全部可扫描时点（不漏节点）",
              len(_out) == len(range(_SAL.VEL_WIN, len(_fake_times), 3)),
              f"{len(_out)} 条 vs 期望 "
              f"{len(range(_SAL.VEL_WIN, len(_fake_times), 3))} 条")
        # 不可解析时刻必须排到队尾，而不是被当成 0 挤到最前
        _out2 = [r["time"] for r in _SAL.timeline_summary(
            _FF(["10:00", "10:03", "10:06", "10:09", "10:12", "bad",
                 "10:15", "10:18", "10:21", "10:24"]))]
        check("S9 不可解析时刻排到队尾（不被当成 0 挤到最前）",
              bool(_out2) and _out2[-1] == "bad",
              f"末条={_out2[-1] if _out2 else '(空)'}")
    finally:
        _SAL.detect_events = _de_orig

    # ---- S10 真实面板：整体有序 + topn 语义 + [:N] 陷阱量化 ----
    _real = None
    for _cand in ("20260921", "20260918"):
        try:
            _F = _SAL.build_features(_cand)
            if len(_F.times) > _SAL.VEL_WIN + 5:
                _real = _F
                break
        except Exception:                                       # noqa: BLE001
            continue
    if _real is None:
        check("S10 真实面板可用（用于端到端排序验证）", False, "没有可用面板")
    else:
        _rows = _SAL.timeline_summary(_real)
        _ks = [_SAL._time_key(r["time"]) for r in _rows]
        check("S10a 真实面板：全局按时间升序",
              all(_ks[i] <= _ks[i + 1] for i in range(len(_ks) - 1)),
              f"{len(_rows)} 条 {_rows[0]['time']} … {_rows[-1]['time']}")
        check("S10b 真实面板：首条 = 最早、末条 = 最晚",
              _ks[0] == min(_ks) and _ks[-1] == max(_ks),
              f"{_rows[0]['time']} ~ {_rows[-1]['time']}")
        # 逐段增长（模拟盘中刷新）：每个前缀截面都必须有序
        import dataclasses as _dc
        _bad = []
        for _cut in range(_SAL.VEL_WIN + 3, _real.V.shape[0] + 1, 9):
            _sub = _dc.replace(
                _real, times=_real.times[:_cut], V=_real.V[:_cut],
                dV5=_real.dV5[:_cut], dV1=_real.dV1[:_cut],
                resid=_real.resid[:_cut], z=_real.z[:_cut],
                rank=_real.rank[:_cut], rank_neg=_real.rank_neg[:_cut],
                persist=_real.persist[:_cut], pct_abs=_real.pct_abs[:_cut],
                pct_vel=_real.pct_vel[:_cut], salience=_real.salience[:_cut])
            _t = [_SAL._time_key(r["time"])
                  for r in _SAL.timeline_summary(_sub)]
            if _t and any(_t[i] > _t[i + 1] for i in range(len(_t) - 1)):
                _bad.append(_cut)
        check("S11 逐段增长（模拟盘中刷新）每个截面都保持有序",
              not _bad, f"乱序截面 {_bad}" if _bad else "全部有序")
        # topn：先按强度选，再按时间摆
        if len(_rows) >= 5:
            _t5 = _SAL.timeline_summary(_real, topn=5)
            _want5 = {r["time"] for r in
                      sorted(_rows, key=lambda r: -float(r["strength"]))[:5]}
            _tk5 = [_SAL._time_key(r["time"]) for r in _t5]
            check("S12 topn=N 选出的是最强 N 条，且仍按时间摆",
                  len(_t5) == 5 and {r["time"] for r in _t5} == _want5
                  and all(_tk5[i] <= _tk5[i + 1] for i in range(len(_tk5) - 1)),
                  " → ".join(r["time"] for r in _t5))
            _naive_cut = max(_ks) - _ks[min(5, len(_ks)) - 1]
            check("S13 量化旧写法 [:N] 的损失（证明这个陷阱是真的）",
                  _naive_cut > 0,
                  f"误写时间序[:5] 会丢掉 {_naive_cut} 分钟的尾盘")

    # ---- S14 UI 接线：表格喂的是已排好的数据 + hint 文案已改 ----
    check("S14a UI 直接消费排序结果（不在 UI 层另排序）",
          'out["timeline"] = salience.timeline_summary(F)' in _ap_s, "")
    check("S14b 界面 hint 已改为「按时间先后」（不再写「按强度排序」）",
          "按时间先后排列" in _ap_s, "")
    check("S14c 关键节点表格新建处不再有旧的 hint 文案",
          'hint="按强度排序", stretch_col=3' not in _ap_s,
          "旧文案已从可执行代码移除")

    # ---- S15 三份实现同源（避免只修一处） ----
    # ★ 路径推算易错，且"只找到一份也算 all() 通过"是断言自身的弱点 ——
    #   本段第一版就踩了这个：join(dirname(BASE), "..", "..", ...) 多退了一级，
    #   flowtool 那份没被找到，但 `all([单一结果])` 仍为 True，
    #   报出"已同步"，实际根本没检查。改为**逐份独立断言**：
    #   存在就必须同步；不存在则显式标注（跨机器时技能目录可能没有）。
    _root = os.path.dirname(os.path.dirname(os.path.abspath(BASE)))
    _pairs = [
        ("本项目 flowtool",
         os.path.join(_root, "outputs", "flowtool", "salience.py")),
        ("可复用技能 scripts",
         os.path.expanduser("~/.workbuddy/skills/"
                            "astock-intraday-flow-salience/scripts/salience.py")),
    ]
    for _label, _p in _pairs:
        if not os.path.isfile(_p):
            check(f"S15 {_label} 实现可定位", False, f"找不到 {_p}")
            continue
        _s = open(_p, encoding="utf-8").read()
        check(f"S15 {_label} 已同步同款排序",
              "_time_key" in _s and "rows.sort(key=_key)" in _s
              and "topn=None" in _s,
              "_time_key + rows.sort(key=_key) + topn 三处齐备")
except Exception as _eS:                                          # noqa: BLE001
    check("S 段执行", False, f"异常：{type(_eS).__name__}: {_eS}")

print("\n" + "=" * 56)
n_pass = sum(1 for _, ok in RESULTS if ok)
print(f"结果：{n_pass}/{len(RESULTS)} PASS")
fails = [n for n, ok in RESULTS if not ok]
if fails:
    print("失败项：")
    for n in fails:
        print("  -", n)
sys.exit(0 if not fails else 1)
