# -*- coding: utf-8 -*-
"""test_livegap_v306.py —— v3.0.6「快速先行版 + 长缺口如实断开」专项回归。

背景（用户反馈「我等了两分钟，到现在没有加载出来」）：
  实测日志显示窗口 2.9s 就出来了、旧面板 109ms 就上屏，但**全量补抓跑了
  190 秒**（14:34:42 → 14:37:54）；这期间界面上只有上午 11:30 之前的旧数据。
  而"现在这一刻的资金流"其实只要 5~6 次请求 / 约 0.3 秒就能拿到。

改动三处：
  1. `flowdata._label_of_idx`        分钟序号 → 'HH:MM'（`_minute_idx` 的逆）
  2. `flowdata.refresh_panel_spot(allow_gap=True)`
     跨大缺口时**先补当前分钟**，中间缺失分钟显式补成 NaN 占位行
  3. `app_main._fill_gaps(max_interp=6)`
     连续缺失 > 6 分钟**不再插值**，保留 NaN 让曲线如实断开

★ 最关键的一条纪律：**绝不编造走势**。第 2 步若只是"把当前分钟接在旧数据
  后面"，渲染层（x 轴是真实分钟序号）会画出一条横跨几十格的直线 ——
  那是伪造出来的走势，正是本项目反复修过的"假横线"缺陷。
  所以本文件里"长缺口必须留 NaN"是硬断言。

完全离线，不联网。
"""
import os
import sys
import json
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
sys.path.insert(0, HERE)
import flowdata as fd                                                   # noqa: E402
import app_main_v203 as A                                               # noqa: E402

PASS = 0
FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


# ======================================================================
print("=" * 74)
print("A. _label_of_idx 与 _minute_idx 必须严格互逆（240 个交易分钟全覆盖）")
print("=" * 74)
bad = [(i, fd._label_of_idx(i), fd._minute_idx(fd._label_of_idx(i)))
       for i in range(240) if fd._minute_idx(fd._label_of_idx(i)) != i]
check(not bad, "A1 240/240 互逆", "" if not bad else str(bad[:5]))
check(fd._label_of_idx(0) == "09:31", "A2 0 号位 = 09:31", fd._label_of_idx(0))
check(fd._label_of_idx(119) == "11:30", "A3 119 号位 = 11:30（上午收盘）",
      fd._label_of_idx(119))
check(fd._label_of_idx(120) == "13:01",
      "A4 120 号位 = 13:01（**跨过午休**，不能按 +1 分钟算）",
      fd._label_of_idx(120))
check(fd._label_of_idx(239) == "15:00", "A5 239 号位 = 15:00", fd._label_of_idx(239))
check(fd._label_of_idx(-1) is None and fd._label_of_idx(240) is None,
      "A6 越界返回 None")
check(all(fd._minute_idx(fd._label_of_idx(i)) is not None for i in range(240)),
      "A7 生成的标签全部是**合法交易分钟**（不含 11:31~12:59）")


# ======================================================================
print()
print("=" * 74)
print("B. refresh_panel_spot(allow_gap=True)：先补当前，缺口显式留空")
print("=" * 74)
tmp = tempfile.mkdtemp(prefix="livegap_")
old_data = fd.DATA
fd.set_data_dir(tmp)

CODES = ["90.BK%04d" % i for i in range(5)]


def _panel(tag, ntimes=120):
    """造一个只有上午的面板：mat[i, j] = i*10 + j（单位：亿）"""
    times = [fd._label_of_idx(i) for i in range(ntimes)]
    mat = np.array([[float(i * 10 + j) for j in range(len(CODES))]
                    for i in range(ntimes)])
    fd.save_panel(tag, {"times": times, "codes": list(CODES),
                        "names": {c: c for c in CODES},
                        "kinds": {c: "industry" for c in CODES},
                        "source": "live", "data_date": "t"}, mat)
    return times, mat


_orig_clamp = fd.clamp_to_bar
_orig_spot = fd.fetch_spot_batch
_orig_idx = fd.fetch_index_trend


def stub_spot(secids, detail=False, workers=None, tries=3, names=None):
    """快照里**故意漏掉最后一个板块**，用来验证前向填充基准。

    ★ v3.0.6：签名补上 `tries` / `names` —— `flowdata.fetch_spot_batch`
      新增了 `names`（替代数据源按板块名给数据时要用），`refresh_panel_spot`
      会带上它；桩必须跟着接受，否则 TypeError。
    """
    out = {}
    for s in secids:
        if s == CODES[-1]:
            continue
        out[s] = ({"net": 7.7e9} if detail else 7.7e9)
    return out


try:
    fd.clamp_to_bar = lambda now=None: "14:34"        # 当前分钟 = 14:34（序号 213）
    fd.fetch_spot_batch = stub_spot
    fd.fetch_index_trend = lambda *a, **k: []

    # ---- B1 跨大缺口 ----
    _panel("g1")
    m, a = fd.refresh_panel_spot("g1", allow_gap=True)
    check(m is not None and a is not None, "B1 跨大缺口时**不再返回 None**（旧行为是放弃）")
    check(a.shape[0] == len(m["times"]),
          "B2 行数与时间轴一致（渲染层对齐的硬前提）",
          f"{a.shape[0]} vs {len(m['times'])}")
    check(m["times"][-1] == "14:34",
          "B3 末行就是**当前这一分钟**", m["times"][-1])
    check(m["times"][119] == "11:30" and m["times"][120] == "13:01",
          "B4 缺口起点紧接 11:30、下一格直接跳到 13:01（跨过午休）",
          f"{m['times'][118]} {m['times'][119]} {m['times'][120]}")
    n_fill = 213 - 119 - 1
    check(a.shape[0] == 120 + n_fill + 1,
          "B5 占位行数 = 缺失分钟数", f"{a.shape[0]} = 120+{n_fill}+1")
    check(bool(np.isnan(a[120:213]).all()),
          "B6 ★★ 中间缺失分钟**全部是 NaN**（绝不填值 → 绝不编造走势）",
          f"{a[120:213].shape[0]} 行")
    check(bool(np.isfinite(a[119]).all()), "B7 缺口前一行（11:30）仍有真实值")
    check(abs(a[-1][0] - 77.0) < 1e-6,
          "B8 末行 = 快照给的当前值", f"{a[-1][0]:.1f} 亿")
    check(abs(a[-1][-1] - a[119][-1]) < 1e-9,
          "B9 ★ 快照里缺的板块沿用**上一分钟**的值（前向填充基准取对了）",
          f"{a[-1][-1]:.0f} == {a[119][-1]:.0f}（11:30 那一行）")
    check(bool(np.isfinite(a[-1][-1])),
          "B10 ★ 前向填充不能填成 NaN（基准若取到占位行就会踩这个坑）")
    check(all(fd._minute_idx(m["times"][i]) < fd._minute_idx(m["times"][i + 1])
              for i in range(len(m["times"]) - 1)),
          "B11 时间轴**严格递增**（倒挂会让 x 落位与曲线错乱）")
    check(m.get("partial_history") is True and m.get("gap_from") == "11:30"
          and m.get("gap_to") == "14:34",
          "B12 meta 留下 partial_history / gap_from / gap_to 标记（可追溯）",
          f"{m.get('gap_from')}→{m.get('gap_to')}")

    # ---- B2 重新读盘，确认已落盘且能读回 ----
    m2, a2, _i2 = fd.load_panel("g1")
    check(a2.shape[0] == a.shape[0] and m2["times"][-1] == "14:34",
          "B13 占位行确实落盘了（load_panel 读回一致）",
          f"{a2.shape}")

    # ---- B3 紧邻的下一分钟 → 不插占位行，走普通追加 ----
    fd.clamp_to_bar = lambda now=None: "14:35"       # 序号 214 = 末行 213 + 1
    m3, a3 = fd.refresh_panel_spot("g1", allow_gap=True)
    check(a3.shape[0] == a.shape[0] + 1,
          "B14 紧邻的下一分钟**不插占位行**，直接追加一行",
          f"{a3.shape[0]} = {a.shape[0]}+1")
    check(a3.shape[0] == len(m3["times"]) and m3["times"][-1] == "14:35",
          "B15 追加后仍对齐、末行为 14:35")

    # ---- B3b 隔 1 分钟（14:37，序号 216；末行是 14:35=214）→ 插恰好 1 个占位行 ----
    fd.clamp_to_bar = lambda now=None: "14:37"
    m3b, a3b = fd.refresh_panel_spot("g1", allow_gap=True)
    check(a3b.shape[0] == a3.shape[0] + 2 and a3b.shape[0] == len(m3b["times"]),
          "B15b 隔 1 分钟 → 插 1 个占位行 + 追加 1 行",
          f"{a3b.shape[0]} = {a3.shape[0]}+2")
    check(m3b["times"][-2] == "14:36" and bool(np.isnan(a3b[-2]).all()),
          "B15c 占位行是 14:36 且整行 NaN", m3b["times"][-2])

    # ---- B4 allow_gap=False 必须保持旧语义 ----
    _panel("g2")
    m4, a4 = fd.refresh_panel_spot("g2", allow_gap=False)
    check(m4 is None and a4 is None,
          "B16 ★ allow_gap=False 时**保持旧语义**（返回 None，交回全量重抓）")
    m5, a5, _i5 = fd.load_panel("g2")
    check(a5.shape[0] == 120,
          "B17 放弃时**绝不改动已有面板**（仍是 120 行）", f"{a5.shape}")

    # ---- B5 快照全空 → 不算刷新成功 ----
    _panel("g3")
    fd.fetch_spot_batch = lambda secids, detail=False, workers=None, tries=3, names=None: {}
    m6, a6 = fd.refresh_panel_spot("g3", allow_gap=True)
    check(m6 is None and a6 is None, "B18 快照一个都没取到 → 不写盘（返回 None）")
finally:
    fd.clamp_to_bar = _orig_clamp
    fd.fetch_spot_batch = _orig_spot
    fd.fetch_index_trend = _orig_idx
    fd.set_data_dir(old_data)


# ======================================================================
print()
print("=" * 74)
print("C. _fill_gaps：短缺口插值，**长缺口如实断开**（不编造走势）")
print("=" * 74)
fg = A._fill_gaps

r = fg([1.0, 2.0, float("nan"), float("nan"), 5.0])
check(len(r) == 5, "C1 返回值长度与入参一致（长度变了曲线会横向错位）", str(len(r)))
check(r == [1.0, 2.0, 3.0, 4.0, 5.0],
      "C2 短缺口（2 分钟）照旧线性插值 —— 语义是「这一分钟没有新成交」", str(r))

r = fg([1.0] + [float("nan")] * 6 + [100.0])
check(len(r) == 8 and all(v == v for v in r),
      "C3 缺口**恰好 6** 分钟 → 仍然插值（阈值是 >6 才断开）", str(r[:3]))

r = fg([1.0] + [float("nan")] * 7 + [100.0])
check(len(r) == 9 and not any(np.isfinite(v) for v in r[1:8]),
      "C4 ★★ 缺口 7 分钟（>6）→ **保留 NaN，曲线如实断开**",
      f"中间 7 个是否全非有限: {not any(np.isfinite(v) for v in r[1:8])}")
check(r[0] == 1.0 and r[-1] == 100.0,
      "C5 断开不影响两侧真实值", f"{r[0]} … {r[-1]}")

big = [1.0] + [float("nan")] * 93 + [77.0]
r = fg(big)
check(len(r) == 95 and not any(np.isfinite(v) for v in r[1:94]),
      "C6 ★★ 实测同款：11:30 之后 93 个缺失分钟 → 全部保持 NaN"
      "（若插值就是伪造 93 分钟走势）")

r = fg([float("nan")] * 10 + [5.0, 6.0])
check(not any(np.isfinite(v) for v in r[:10]) and r[10] == 5.0,
      "C7 开头长缺口（板块晚露面）→ 保留 NaN，曲线晚点开始，不硬造起点")

r = fg([float("nan")] * 3 + [5.0, 6.0])
check(r[0] == 5.0 and r[1] == 5.0 and r[2] == 5.0,
      "C8 开头**短**缺口 → 用后一个已知值回填（不写 0，避免造出下滑假走势）",
      str(r))

r = fg([1.0, 2.0, 3.0])
check(r == [1.0, 2.0, 3.0], "C9 无缺口 → 原样返回")
r = fg([])
check(r == [], "C10 空输入 → 空输出")
r = fg([float("nan")] * 5)
check(r == [0.0] * 5, "C11 整条全空 → 贴零轴的平线（旧兜底语义不变）", str(r))
r = fg([1.0, None, "x", 4.0])
check(len(r) == 4 and all(np.isfinite(v) for v in r),
      "C12 非数值（None / 字符串）也能兜住", str(r))
check(all(isinstance(v, float) for v in fg([1, 2, 3])),
      "C13 返回值统一是 float（避免下游类型分叉）")

# ======================================================================
print()
print("=" * 74)
print("D. 两处口径必须一致：_fill_gaps 的阈值 == flowdata.SPOT_MAX_GAP_MIN")
print("=" * 74)
import inspect                                                          # noqa: E402
sig = inspect.signature(A._fill_gaps)
check(sig.parameters["max_interp"].default == fd.SPOT_MAX_GAP_MIN,
      "D1 ★ 两个阈值共用一个数字（以后各改各的就会语义分叉）",
      f"{sig.parameters['max_interp'].default} == {fd.SPOT_MAX_GAP_MIN}")

_sig2 = inspect.signature(fd.fetch_spot_batch)
check(_sig2.parameters["tries"].default >= 3,
      "D2 ★ 增量快照默认重试 ≥3 次（它是快速先行版的全部数据来源，"
      "失败就等于回去干等几分钟全量）",
      f"tries={_sig2.parameters['tries'].default}")
check(inspect.signature(fd.refresh_panel_spot).parameters["allow_gap"].default is False,
      "D3 allow_gap 默认必须是 False（不改变其它调用方的旧语义）")

# ======================================================================
print()
print("=" * 74)
print("E. ★ 缺口判定必须同时看「尾部」与「内部」—— 否则占位行永久留在面板里")
print("=" * 74)
# 背景：快速先行版把"当前这一分钟"补到尾部后，**尾部缺口变成 0**。
# 若判定只看尾部，就会认为"面板很新"→ 永远不再触发全量补齐 →
# 那段 NaN 占位行永久留在面板里、曲线一直是断的。
contig = [fd._label_of_idx(i) for i in range(120)]                     # 09:31..11:30
check(fd.panel_internal_gap_min(contig) == 0,
      "E1 连续时间轴 → 内部缺口 0", str(fd.panel_internal_gap_min(contig)))

# 真正"标签缺失"的情形（旧版本写入 / 数据源缺口）：0..29 之后直接跳到 60..89
label_hole = contig[:30] + [fd._label_of_idx(i) for i in range(60, 90)]
check(fd.panel_internal_gap_min(label_hole) == 30,
      "E2 ★ 标签真缺 30 分钟 → 内部缺口 30", str(fd.panel_internal_gap_min(label_hole)))
check(fd.panel_internal_gap_min([]) == 0 and fd.panel_internal_gap_min(None) == 0,
      "E3 空 / None → 0（不炸）")
check(fd.panel_internal_gap_min(["09:31"]) == 0, "E4 只有一个点 → 0")
check(fd.panel_internal_gap_min(["垃圾", "09:31", "09:32"]) == 0,
      "E5 认不出的标签被跳过，不参与判定")

# ★★ 关键：快速先行版的空洞**不能**靠"内部跳跃"发现 ——
#   它补的占位行用的是真实交易分钟标签，时间轴在序号上是连续的，
#   空洞体现在**整行数值是 NaN**。必须靠 partial_history 标记识别。
live_times = ([fd._label_of_idx(i) for i in range(120)]
              + [fd._label_of_idx(i) for i in range(120, 214)])
check(fd.panel_internal_gap_min(live_times) == 0,
      "E6 ★ 先行版时间轴在序号上**是连续的**（所以内部跳跃查不出这个洞）",
      str(fd.panel_internal_gap_min(live_times)))

tmp2 = tempfile.mkdtemp(prefix="gapchk_")
fd.set_data_dir(tmp2)


class _FakeSelf:
    tag = "chk"


import json as _json                                                     # noqa: E402


def _write_meta(tag, meta):
    p = os.path.join(tmp2, f"panel_{tag}.json")
    with open(p, "w", encoding="utf-8") as f:
        _json.dump(meta, f)


try:
    _write_meta("chk", {"times": contig, "codes": [], "source": "live"})
    g_contig = A.Worker._panel_gap_min(_FakeSelf(), tmp2)

    _write_meta("chk", {"times": live_times, "codes": [], "source": "live",
                        "partial_history": True,
                        "gap_from": "11:30", "gap_to": "14:34"})
    g_live = A.Worker._panel_gap_min(_FakeSelf(), tmp2)
    check(g_live >= 93,
          "E7 ★★ Worker._panel_gap_min 能识别先行版留下的 93 分钟空洞",
          f"有洞={g_live}")
    check(g_live > fd.SPOT_MAX_GAP_MIN,
          "E8 ★ 该值超过 SPOT_MAX_GAP_MIN → 下一轮**会**走全量补齐"
          "（否则占位行永久留在面板里、曲线一直断）",
          f"{g_live} > {fd.SPOT_MAX_GAP_MIN}")

    _write_meta("chk", {"times": label_hole, "codes": [], "source": "live"})
    g_label = A.Worker._panel_gap_min(_FakeSelf(), tmp2)
    check(g_label >= 30,
          "E9 标签真缺时也能识别（两种成因都覆盖）", f"标签缺={g_label}")

    # 全量重抓成功后 build_panel 写的是**全新 meta**（无该标记）→ 判定复位
    _write_meta("chk", {"times": contig, "codes": [], "source": "live",
                        "partial_history": False})
    check(A.Worker._panel_gap_min(_FakeSelf(), tmp2) == g_contig,
          "E10 标记为 False 时判定回到基线（重抓成功后能复位）",
          f"{A.Worker._panel_gap_min(_FakeSelf(), tmp2)} vs {g_contig}")
finally:
    fd.set_data_dir(old_data)

print()
print("=" * 74)
print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
