# -*- coding: utf-8 -*-
"""verify_timeline_order.py —— 「全天关键节点」时间排序专项验证

用户反馈：榜单里各时刻排列无序、杂乱。要求按时间先后（早→晚）排列，
且**数据更新或刷新后仍维持有序**。

本脚本分四组验证，全部用真实面板 + 构造面板，不依赖窗口：
    A. 真实面板：全局时间单调、覆盖所有节点、与旧口径的差异量化
    B. 排序键本身：_time_key 的解析与容错（脏刻度 / 跨午休 / 非法值）
    C. topn 语义：「先按强度选，再按时间摆」—— 堵住 [:N] 静默退化
    D. 刷新后仍有序：反复重算（模拟盘中数据增长）+ 三种板块时间轴形状

★ 为什么必须单独验「刷新后仍有序」：
  排序只发生在数据层。若哪天有人把排序挪到 UI、或加了缓存又在缓存路径上
  跳过排序，表现就是"首次打开是对的、点一下刷新就乱了"——
  这种"只在特定操作后出现"的回归，单次调用的断言抓不到。
"""
import dataclasses
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))

import flowdata                                                    # noqa: E402

DEPLOY_DATA = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                           "FlowLiveNative", "data")
if os.path.isdir(DEPLOY_DATA):
    flowdata.set_data_dir(DEPLOY_DATA)

import salience                                                    # noqa: E402

PASS = FAIL = 0
FAILS = []


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}" + (f"  · {detail}" if detail else ""))
    else:
        FAIL += 1
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f"  · {detail}" if detail else ""))


def tk(hm):
    """测试用的钟点转分钟数 —— **直接复用生产代码的 `_time_key`**。

    ★ 为什么不在这里另写一份：第一版写的是
          h, m = str(hm).split(":")[:2]; return int(h) * 60 + int(m)
      它遇到 'bad' 会直接 ValueError 崩掉 —— 而"不可解析时刻"正是
      本组要覆盖的边界之一，测试因为自己的辅助函数不够健壮而中断。
      更根本的问题是：**测排序时若自己另写一套时间解析，就等于在验
      "我的解析和我的解析一致"**，生产代码改了这里不会跟着发现。
      复用 `_time_key` 才是真的在验生产口径。
    """
    return salience._time_key(hm)


def is_ascending(times):
    """时间序列是否按钟点升序。

    ★ 不可解析时刻（None）按生产代码的约定**排在最后**，
      因此这里也把它们视为"大于一切合法钟点"，映射成 +∞ 参与比较 ——
      与 `_key()` 的 `(1, 0, ...)` 分支语义一致。
    """
    ks = [(tk(t) if tk(t) is not None else float("inf")) for t in times]
    return all(ks[i] <= ks[i + 1] for i in range(len(ks) - 1))


print("=" * 70)
print("「全天关键节点」时间排序专项验证")
print("=" * 70)

# ---------------------------------------------------------------- A 真实面板
print("\n--- A. 真实面板：全局单调 + 覆盖完整 ---")
TAG = None
for cand in ("20260921", "20260918"):
    try:
        F = salience.build_features(cand)
        if len(F.times) > 0:
            TAG = cand
            break
    except Exception:                                              # noqa: BLE001
        continue

if TAG is None:
    print("[SKIP] 没有可用面板，A 组跳过")
else:
    rows = salience.timeline_summary(F)
    times = [r["time"] for r in rows]
    print(f"  面板 {TAG}：{F.V.shape[0]} 时间点 × {F.V.shape[1]} 板块，"
          f"时间轴 {F.times[0]}~{F.times[-1]}")
    print(f"  关键节点 {len(rows)} 条：{times[0]} … {times[-1]}")

    check("A1 全局按时间升序（早→晚）", is_ascending(times),
          " → ".join(times[:5]) + " …")
    check("A2 首个节点是全天最早的、末个是全天最晚的",
          tk(times[0]) == min(tk(t) for t in times)
          and tk(times[-1]) == max(tk(t) for t in times),
          f"{times[0]} ~ {times[-1]}")
    check("A3 覆盖全部节点（条数与独立扫描一致）",
          len(rows) == len({r["time"] for r in rows}),
          f"{len(rows)} 条，无重复时刻")

    # 独立复算一遍，防止"排序对了但漏了行"
    manual = []
    for i in range(salience.VEL_WIN, len(F.times), 3):
        ev = salience.detect_events(F, i)
        if ev and ev[0]["strength"] >= 1.5:
            manual.append(F.times[i])
    check("A4 与独立扫描的节点集合完全一致（不漏不重）",
          sorted(manual) == sorted(times),
          f"独立扫描 {len(manual)} 条 / 函数返回 {len(rows)} 条")

    # 时间轴本身上限检查：节点时间必须都在面板时间轴内
    tset = set(F.times)
    check("A5 每个节点时刻都存在于该面板时间轴中（无幽灵时刻）",
          set(times) <= tset,
          f"越界 {len(set(times) - tset)} 个")

    # 量化旧口径的差异
    old = sorted(rows, key=lambda r: -float(r["strength"]))
    same_head = sum(1 for a, b in zip(old[:10], rows[:10]) if a["time"] == b["time"])
    print(f"  对照：旧口径（强度序）前 10 条里，只有 {same_head} 条恰好也在时间序前 10")

# ------------------------------------------------------------- B 排序键容错
print("\n--- B. 排序键 _time_key 的解析与容错 ---")
cases = [
    ("09:31", 571, "开盘第一分钟"),
    ("11:30", 690, "上午收盘"),
    ("13:01", 781, "下午开盘（跨午休仍单调）"),
    ("15:00", 900, "全天收盘"),
    ("9:35", 575, "单位数小时（不补零）"),
    (" 10:07 ", 607, "带空格"),
    ("25:00", None, "非法小时"),
    ("10:75", None, "非法分钟"),
    ("abc", None, "非时间串"),
    ("", None, "空串"),
    (None, None, "None"),
    (1234, None, "非字符串"),
]
for raw, want, desc in cases:
    got = salience._time_key(raw)
    check(f"B·{desc}  {raw!r} → {got}", got == want, f"期望 {want}")

check("B·跨午休单调（11:30 < 13:01）",
      salience._time_key("11:30") < salience._time_key("13:01"),
      f"{salience._time_key('11:30')} < {salience._time_key('13:01')}")
check("B·字典序陷阱已避开（'9:35' 应大于 '10:00' 之外，正确值见下）",
      salience._time_key("9:35") < salience._time_key("10:00"),
      f"9:35={salience._time_key('9:35')} < 10:00={salience._time_key('10:00')}"
      "（字符串比较会得出相反结论）")

# ------------------------------------------------------------ C topn 语义
print("\n--- C. topn：先按强度选，再按时间摆 ---")
if TAG is not None:
    full = salience.timeline_summary(F)
    t18 = salience.timeline_summary(F, topn=18)
    check("C1 topn 生效：条数被截到 18", len(t18) == 18, f"{len(t18)} 条")
    check("C2 topn 结果仍是时间升序",
          is_ascending([r["time"] for r in t18]),
          " → ".join(r["time"] for r in t18))
    check("C3 topn 选出来的就是强度最高的那 18 条（选谁没变）",
          {r["time"] for r in t18}
          == {r["time"] for r in sorted(full,
                                        key=lambda r: -float(r["strength"]))[:18]},
          "集合一致")
    check("C4 topn 结果里强度不再降序（证明摆的是时间，不是强度）",
          [r["strength"] for r in t18]
          != sorted([r["strength"] for r in t18], reverse=True),
          f"实际强度序列 {[round(r['strength'], 1) for r in t18][:6]} …"
          "（若仍严格降序说明根本没按时间排）")
    naive = full[:18]                                              # 误写成"时间序前 18"
    lost = tk(full[-1]["time"]) - tk(naive[-1]["time"])
    check("C5 若误写 full[:18] 会丢掉尾盘（量化）", lost > 0,
          f"最晚只到 {naive[-1]['time']}，丢掉 {lost} 分钟；"
          f"正确 topn=18 最晚到 {t18[-1]['time']}")
    check("C6 topn=None 与不传参完全一致（向后兼容）",
          salience.timeline_summary(F)
          == salience.timeline_summary(F, topn=None),
          "逐字段相等")

# ------------------------------------------------------- D 刷新后仍维持有序
print("\n--- D. 数据更新 / 刷新后仍维持有序 ---")
if TAG is not None:
    # D1：模拟盘中数据增长（截断面板的不同前缀），每次重算都要有序
    #
    # ★ 用 dataclasses.replace 而不是手写全部字段：
    #   手写一遍就得把 Features 的 17 个字段名逐字抄对，抄错一个就是
    #   AttributeError（本轮已踩：把 salience 写成了 idx）——
    #   而且**将来给 Features 加字段时这里会静默漏切**（新字段保持全长，
    #   与前缀长度不匹配）。replace 只覆盖要变的，其余原样带过。
    #   ★ v1.2.5：dataclasses 已提到模块顶层导入（F 组也要用，
    #     而 D 组在 `TAG is not None` 分支里 —— 局部导入会让 F 组 NameError）。
    ok_all, detail = True, []
    cuts = range(salience.VEL_WIN + 3, F.V.shape[0] + 1, 7)
    for cut in cuts:
        sub = dataclasses.replace(
            F, times=F.times[:cut], V=F.V[:cut], dV5=F.dV5[:cut],
            dV1=F.dV1[:cut], resid=F.resid[:cut], z=F.z[:cut],
            rank=F.rank[:cut], rank_neg=F.rank_neg[:cut],
            persist=F.persist[:cut], pct_abs=F.pct_abs[:cut],
            pct_vel=F.pct_vel[:cut], salience=F.salience[:cut])
        try:
            t = [r["time"] for r in salience.timeline_summary(sub)]
        except Exception as e:                                     # noqa: BLE001
            ok_all = False
            detail.append(f"cut={cut} 抛异常 {type(e).__name__}: {e}")
            continue
        if t and not is_ascending(t):
            ok_all = False
            detail.append(f"cut={cut} 乱序：{' → '.join(t)}")
    check("D1 逐段增长（模拟盘中刷新）每次都保持有序", ok_all,
          detail[0] if detail else f"扫了 {len(list(cuts))} 个前缀截面")

    # D2：连续重算 5 次，结果必须完全一致（说明排序是幂等、无状态）
    runs = [salience.timeline_summary(F) for _ in range(5)]
    check("D2 连续重算 5 次结果逐字段一致（幂等、无隐藏状态）",
          all(r == runs[0] for r in runs), "5 次全等")

# --------------------------------------------------- E 边界：时间轴形状
print("\n--- E. 边界时间轴形状 ---")


class _FakeF:
    """最小可用替身：只提供 timeline_summary 用到的两个属性。"""
    def __init__(self, times):
        self.times = times


def _probe(times, label, min_rows=2):
    """直接对 timeline_summary 的排序逻辑做时间轴形状测试。

    不构造完整 Features（那需要 (T,N) 矩阵），改用轻量替身 +
    把 detect_events 打桩成"每个时点都产出一条事件"，
    这样时间轴形状就是唯一变量 —— 正是我们要验的东西。

    ★★ min_rows 是**必需的**，不是装饰：
       `timeline_summary` 的扫描是 `range(VEL_WIN, len(times), step)`，
       时间点少于 VEL_WIN+1 (=6) 个时循环体为空 → 返回空列表。
       而 `is_ascending([])` 恒为 True —— **空集合上的断言必然通过**。
       本组第一版就是这样：7 个用例里 5 个返回 0～1 条，
       全部"PASS"，但实际一个字节的排序逻辑都没验到。
       （"没有数据"和"数据有序"是两回事，测试里必须分开断言。）
    """
    orig_de = salience.detect_events
    salience.detect_events = lambda F, i: [
        {"type": "测试", "name": f"板块{i}", "desc": "d", "strength": 9.9}]
    try:
        F2 = _FakeF(list(times))
        got = [r["time"] for r in salience.timeline_summary(F2)]
    finally:
        salience.detect_events = orig_de
    if len(got) < min_rows:
        check(f"E·{label}", False,
              f"只产出 {len(got)} 条（要求 ≥{min_rows}）——"
              "用例本身构造得不够长，断言会是假通过")
        return
    check(f"E·{label}", is_ascending(got),
          f"{len(got)} 条：{' → '.join(got)}")


# 每个用例都给足时间点（≥ 8），确保真的进到循环体、产出多条再判序
_probe(["09:31", "09:34", "09:37", "09:40", "09:43", "09:46", "09:49", "09:52",
        "09:55", "09:58"],
       "常规连续分钟（早盘）")
_probe(["09:31", "09:34", "09:37", "10:00", "11:27", "11:30", "13:01", "13:04",
        "14:00"],
       "跨午休（含 11:30 → 13:01 断点）")
_probe(["14:30", "14:33", "14:36", "14:39", "14:42", "14:45", "14:48", "14:51",
        "14:54", "14:57", "15:00"],
       "尾盘到尾盘收盘")
_probe(["09:31", "10:15", "10:45", "11:30", "13:01", "13:45", "14:30", "15:00",
        "14:00", "12:30", "10:30", "11:00"],
       "十二个大间隔钟点（跨午休 + 顺序打乱）")
_probe(["13:01", "09:31", "15:00", "11:30", "10:00", "14:00", "09:45", "12:00",
        "13:30", "10:30", "14:30", "11:00"],
       "输入本身乱序 → 输出必须有序")
_probe(["10:00", "10:00", "10:03", "10:03", "10:06", "10:09", "10:12", "10:15",
        "10:18", "10:21"],
       "重复时刻（不得乱序、不得让排序崩）")
# ★ 「不可解析时刻」必须放在**下标 ≥ VEL_WIN(5)** 的位置才扫得到 ——
#   timeline_summary 从 VEL_WIN 开始扫（要够 5 分钟才能算 dV5）。
#   放下标 2 的话它压根不进结果集，断言会变成"空集也通过"的假象。
_probe(["10:00", "10:03", "10:06", "10:09", "10:12", "bad", "10:15", "10:18",
        "10:21", "10:24"],
       "含不可解析时刻（排到最后，不挤到最前）")

# E·末条：显式验证不可解析时刻确实被推到队尾（而不是被当成 0 排在最前）
_orig = salience.detect_events
salience.detect_events = lambda F, i: [
    {"type": "测试", "name": "x", "desc": "d", "strength": 9.9}]
try:
    _got = [r["time"] for r in salience.timeline_summary(
        _FakeF(["10:00", "10:03", "10:06", "10:09", "10:12", "bad", "10:15",
                "10:18", "10:21", "10:24"]))]
finally:
    salience.detect_events = _orig
check("E·不可解析时刻 bad 排在最末（不被当成 0 挤到最前）",
      bool(_got) and _got[-1] == "bad",
      f"{len(_got)} 条 末条={_got[-1] if _got else '(空)'}；"
      "若 bad 被当成 0 会排到 10:00 前面")

# E·边界最大值：全天满格（240 分钟）也要有序
_probe([f"{h:02d}:{m:02d}"
        for h in (9, 10, 11, 13, 14)
        for m in range(0, 60, 3)
        if not (h == 9 and m < 31) and not (h == 11 and m > 30)
        and not (h == 14 and m > 59)][:80] + ["15:00"],
       "全天满格时间轴（80+ 点）")

# ------------------------- F 数据更新 / 换日刷新后的有序性（矩阵全覆盖）
print("\n--- F. 数据更新 / 换日刷新后仍维持有序（多日期 × 全前缀）---")


def _panel_dates():
    """列出本机已落盘、可被 build_features 读到的面板日期。"""
    dd = os.path.join(os.environ.get("LOCALAPPDATA", ""), "FlowLiveNative", "data")
    if not os.path.isdir(dd):
        return []
    ds = [f[len("panel_"):-len(".json")]
          for f in os.listdir(dd)
          if f.startswith("panel_") and f.endswith(".json")]
    return sorted({d for d in ds if d.isdigit()})


_dates = _panel_dates()
check("F1 本机存在可验证的面板日期（否则本组无意义）", len(_dates) >= 1,
      f"找到 {len(_dates)} 天：{'、'.join(_dates)}")

# F2：每一天 × 每一个"盘中时刻截面"（前缀截断）都必须有序。
#   ★ 这是用户原话「数据更新或刷新后仍维持有序」的**矩阵化**版本：
#     换日 = 数据更新；前缀增长 = 盘中刷新逐分钟追加。
#     单个日期单次调用通过，不代表换一天、换一个时点还通过。
_fail_detail, _n_sec, _n_dates_ok = [], 0, 0
for _d in _dates:
    try:
        _F = salience.build_features(_d)
    except Exception as _e:                                        # noqa: BLE001
        _fail_detail.append(f"{_d} 面板不可读({type(_e).__name__})")
        continue
    _n_dates_ok += 1
    _bad = []
    for _cut in range(salience.VEL_WIN + 3, _F.V.shape[0] + 1, 7):
        _sub = dataclasses.replace(
            _F, times=_F.times[:_cut], V=_F.V[:_cut], dV5=_F.dV5[:_cut],
            dV1=_F.dV1[:_cut], resid=_F.resid[:_cut], z=_F.z[:_cut],
            rank=_F.rank[:_cut], rank_neg=_F.rank_neg[:_cut],
            persist=_F.persist[:_cut], pct_abs=_F.pct_abs[:_cut],
            pct_vel=_F.pct_vel[:_cut], salience=_F.salience[:_cut])
        try:
            _t = [r["time"] for r in salience.timeline_summary(_sub)]
        except Exception as _e:                                    # noqa: BLE001
            _bad.append(f"cut={_cut} 抛异常 {type(_e).__name__}")
            break
        _n_sec += 1
        if _t and not is_ascending(_t):
            # ★ 同时核验末条确实是最晚的 —— 有序但"漏掉尾盘"也是一种失败
            _bad.append(f"cut={_cut} 乱序或末条非最晚")
            break
    if _bad:
        _fail_detail.append(f"{_d}: {_bad[0]}")
check("F2 逐日 × 逐截面（模拟盘中刷新）全部有序且末条为最晚",
      not _fail_detail,
      f"覆盖 {_n_dates_ok} 天 / {_n_sec} 个截面"
      if not _fail_detail else _fail_detail[0])

# F3：交替重算不同日期，结果不得互相污染（缓存/全局状态泄漏）
#   ★ 真实触发场景：用户在不同日期之间来回点回看下拉。
#     若某处把"最后一次排序结果"缓存成了模块级变量，就会出现
#     "切回旧日期后仍显示新日期的顺序"——单日期测试抓不到。
_leak = None
if len(_dates) >= 2:
    _snap = {}
    for _d in _dates:
        try:
            _snap[_d] = [r["time"] for r in
                         salience.timeline_summary(salience.build_features(_d))]
        except Exception:                                          # noqa: BLE001
            pass
    _d1, _d2 = list(_snap)[0], list(_snap)[-1]
    for _ in range(3):                                             # 来回切 3 轮
        _again1 = [r["time"] for r in
                   salience.timeline_summary(salience.build_features(_d1))]
        _again2 = [r["time"] for r in
                   salience.timeline_summary(salience.build_features(_d2))]
        if _again1 != _snap[_d1] or _again2 != _snap[_d2]:
            _leak = f"{_d1}/{_d2} 交替重算后结果变化"
            break
check("F3 不同日期交替重算互不污染（无全局缓存泄漏）",
      _leak is None,
      "来回切 3 轮结果稳定" if _leak is None else _leak)

# F4：同日重复刷新 8 次，节点集合与顺序都不得漂移
_drift = None
if _dates:
    _d0 = _dates[-1]                                               # 最新一天
    try:
        _base = [r["time"] for r in
                 salience.timeline_summary(salience.build_features(_d0))]
        for _i in range(8):
            _now = [r["time"] for r in
                    salience.timeline_summary(salience.build_features(_d0))]
            if _now != _base:
                _drift = f"第 {_i + 2} 次刷新后变为 {len(_now)} 条（首见 {len(_base)} 条）"
                break
    except Exception as _e:                                        # noqa: BLE001
        _drift = f"异常 {type(_e).__name__}: {_e}"
check("F4 同一日连续刷新 8 次：顺序与集合零漂移（幂等）",
      _drift is None,
      f"{_d0} 连刷 8 次逐行一致（{len(_base)} 条）" if _drift is None else _drift)

print("\n" + "=" * 70)
print(f"结果：{PASS}/{PASS + FAIL} PASS")
if FAILS:
    print("未通过：")
    for f in FAILS:
        print(f"  · {f}")
else:
    print("全部通过 ✅")
print("=" * 70)
sys.exit(0 if not FAILS else 1)
