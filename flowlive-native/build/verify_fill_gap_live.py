# -*- coding: utf-8 -*-
"""真实网络端到端：证明「全量重抓能补齐稀疏时间轴」（v1.2.4）。

在**临时目录**里跑一次 build_panel（不碰用户真实数据目录），
然后对比：稀疏的旧面板 vs 刚抓到的密集面板。

用法：
    <native_env py> verify_fill_gap_live.py
"""
import os
import sys
import json
import glob
import time
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

import flowdata as FD                                         # noqa: E402


def minute_idx(hm):
    h, m = map(int, str(hm).split(":")[:2])
    t = h * 60 + m
    if 571 <= t <= 690:
        return t - 571
    if 781 <= t <= 900:
        return 120 + (t - 781)
    if t == 570:
        return 0
    return None


def describe(times, label):
    idx = [minute_idx(x) for x in times]
    bad = [x for x, k in zip(times, idx) if k is None]
    ok = [k for k in idx if k is not None]
    if not ok:
        print(f"  {label}: 无可识别分钟")
        return
    span = ok[-1] - ok[0] + 1
    n_bad = span - len(ok)
    print(f"  {label}: {len(times)} 点 · {times[0]}~{times[-1]} · "
          f"跨度 {span} 分钟 · 缺 {n_bad} 分钟"
          + (f" · 脏刻度 {bad}" if bad else ""))


print("=" * 70)
print("真实网络端到端：全量重抓补齐稀疏时间轴")
print("=" * 70)

# ---- 旧面板（用户真实数据，只读）------------------------------------------
#   ★ 挑**最稀疏**的那份做对比 —— 那才是"曲线只画到十点多"的真身。
#     按"点数 / 跨度"的比值排序，缺口比例最高者胜出。
REAL = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                    "FlowLiveNative", "data")
old_meta = None            # (times, filename, holes)
for p in sorted(glob.glob(os.path.join(REAL, "panel_2026*.json"))):
    try:
        m = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    t = m.get("times") or []
    if len(t) < 20:
        continue
    idx = [minute_idx(x) for x in t]
    ok = [k for k in idx if k is not None]
    if not ok:
        continue
    holes = (ok[-1] - ok[0] + 1) - len(ok)
    if old_meta is None or holes > old_meta[2]:
        old_meta = (t, os.path.basename(p), holes)

print("\n[1] 修复前的面板（用户数据目录，只读）")
if old_meta:
    describe(old_meta[0], old_meta[1])
    print(f"      （这份缺口最多，正是用户看到「只画到十点多」的那一份）")
    gap = FD.spot_gap_minutes(old_meta[0])
    print(f"      → 缺口判定 = {gap} 分钟 "
          f"（阈值 {FD.SPOT_MAX_GAP_MIN}）"
          f" → {'触发全量补齐 ✔' if (gap or 0) > FD.SPOT_MAX_GAP_MIN else '走增量'}")
else:
    print("      未找到可用面板")

# ---- 全量重抓到临时目录 ---------------------------------------------------
TMPD = tempfile.mkdtemp(prefix="flowgap_")
FD.DATA = TMPD
print(f"\n[2] 全量重抓（临时目录 {TMPD}）")
t0 = time.time()
try:
    FD.LAST_DATA_DATE = None
    _r = FD.build_panel("gapcheck", workers=24,
                        on_progress=lambda m: None)
    meta, mat = _r[0], _r[1]
    dt = time.time() - t0
    t_new = meta["times"]
    print(f"      用时 {dt:.1f}s · 面板 {mat.shape[0]}×{mat.shape[1]}")
    describe(t_new, "重抓后")

    # ---- 结论 -------------------------------------------------------------
    idx = [minute_idx(x) for x in t_new]
    bad = [x for x, k in zip(t_new, idx) if k is None]
    ok = [k for k in idx if k is not None]
    holes = (ok[-1] - ok[0] + 1) - len(ok) if ok else -1
    print("\n[3] 结论")
    print(f"      ✔ 脏刻度（非交易分钟）: {len(bad)} 个"
          + (f" {bad}" if bad else " —— 无"))
    print(f"      ✔ 时间轴缺口: {holes} 分钟"
          + ("（完全连续）" if holes == 0 else ""))
    if old_meta:
        _oi = [minute_idx(x) for x in old_meta[0]]
        _ook = [k for k in _oi if k is not None]
        _oholes = (_ook[-1] - _ook[0] + 1) - len(_ook)
        print(f"      ✔ 旧面板缺口 {_oholes} 分钟 → 重抓后 {holes} 分钟"
              f"（缺口 {_oholes} → {holes}）")
    print(f"      ✔ 最新分钟: {t_new[-1]} "
          f"（第 {minute_idx(t_new[-1])} 格 = 横轴上的位置）")
finally:
    import shutil
    shutil.rmtree(TMPD, ignore_errors=True)
    print(f"\n      临时目录已清理")
