# -*- coding: utf-8 -*-
"""验证：修复后"节点未采集"问题是否解决（端到端，真实采集）

完整复刻 app_main._snap_tick 的判定顺序，但可以直接跑（不需 UI）：

  ① 交易日判定（旧代码在这里被误杀）
  ② due_nodes    → 准点采集
  ③ catchup_nodes→ 补采（v3.0.2 写了却一直够不到的路径）
  ④ 真实执行 capture_node 并落盘
  ⑤ 回读校验

用法：
  ../native_env/Scripts/python.exe verify_snap_fixed.py
  ../native_env/Scripts/python.exe verify_snap_fixed.py --dry   # 只判定不采集
"""
import os
import sys
import time
import json
import argparse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
import flowdata                                                          # noqa: E402

RUNTIME_DATA = os.path.join(HERE, "..", "runtime", "FlowLiveNative", "data")


def banner(s):
    print()
    print("=" * 76)
    print(s)
    print("=" * 76)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只判定，不真实采集")
    args = ap.parse_args()

    flowdata.set_data_dir(RUNTIME_DATA)
    import tradingtime
    import opensnap

    now = datetime.now()
    tag = now.strftime("%Y%m%d")
    print(f"验证时间: {now.strftime('%Y-%m-%d %H:%M:%S')}   交易日 tag={tag}")
    print(f"数据目录: {RUNTIME_DATA}")

    # ======================================================== ① 交易日判定
    banner("① 交易日判定（旧代码在这里被误杀）")
    import tradecal
    days, _ = tradecal.get_trading_days(refresh=False)
    old_would_return = bool(days and tag not in days)
    print(f"  日历最后 5 天 : {days[-5:] if days else '(空)'}")
    print(f"  旧判据 tag not in days  -> {old_would_return}"
          f"   {'★ 会 return（跳过采集）' if old_would_return else '继续'}")

    ok, why = tradingtime.is_trading_day(now)
    print(f"  新判据 is_trading_day   -> {ok}  依据={why} ({tradingtime.day_reason_text(why)})")
    if not ok:
        print("\n  [结果] 判定为非交易日 → 不采集（这是正确行为）")
        return 0
    print("  ★ 判定为交易日 → 继续走采集流程")

    sess = tradingtime.classify(now)
    print(f"  时段: {sess.label}")

    # ======================================================== ②③ 节点判定
    banner("②③ 节点判定（准点 / 补采）")
    snap_before = opensnap.load_snap(tag)
    have = set((snap_before.get("nodes") or {}).keys())
    print(f"  当天已有节点: {sorted(have) if have else '(无)'}")

    due = opensnap.due_nodes(tag, now)
    ck = opensnap.catchup_nodes(tag, now)
    print(f"  准点窗口内待采 (due_nodes)     : {due}")
    print(f"  已过窗口可补采 (catchup_nodes)  : {ck}")

    target = None
    late = False
    if due:
        target = due[0]
        print(f"  → 走【准点采集】{target}")
    elif ck:
        target = ck[0]
        late = True
        print(f"  → 走【补采】{target}（这正是一直够不到的那条路径）")
    else:
        print("  → 无待采节点（可能已全部采齐，或还没到 09:31）")
        if have:
            print("  [结果] 已有数据，无需采集")
            return 0
        return 0

    if args.dry:
        print("\n  [--dry] 只判定，不采集。以上流程已跑通。")
        return 0

    # ======================================================== ④ 真实采集
    minutes, key, trigger = target
    banner(f"④ 真实采集节点 {key}（计划 {trigger}，{'补采' if late else '准点'}）")
    t0 = time.time()
    try:
        node = opensnap.capture_node(minutes, trigger=trigger)
    except Exception as e:                                    # noqa: BLE001
        print(f"  ★★ 采集失败: {type(e).__name__}: {e}")
        return 1
    dt = time.time() - t0
    print(f"  采集完成，耗时 {dt:.1f}s")
    print(f"  captured_at = {node.get('captured_at')}")
    print(f"  planned     = {node.get('planned')}")
    print(f"  on_time     = {node.get('on_time')}  （{'准点' if node.get('on_time') else '补采'}）")
    n_sec = len(node.get("sectors") or [])
    n_stk = len(node.get("stocks") or [])
    print(f"    sectors 原始数据 {n_sec} 条   （★ 0 = 采到空数据，即用户报的问题）")
    print(f"    stocks  原始数据 {n_stk} 条")
    if n_sec == 0 and n_stk == 0:
        print("  ★★ 仍然采到空数据 —— 修复未生效或仍在限流窗口，需继续排查")
        return 1

    # ======================================================== ⑤ 落盘校验
    banner("⑤ 落盘与回读校验")
    nodes = dict(snap_before.get("nodes") or {})
    nodes[key] = node
    opensnap.save_snap(tag, nodes)
    back = opensnap.load_snap(tag)
    got = back.get("nodes") or {}
    print(f"  快照文件: {opensnap.snap_path(tag)}")
    print(f"  已存节点: {sorted(got.keys())}")
    print(f"  {key} 已落盘: {key in got}")

    # payload（UI 实际消费的结构）
    pay = opensnap.build_payload(tag)
    nd = (pay.get("nodes") or {}).get(key) or {}
    print(f"  payload[{key}]: missing={nd.get('missing')} "
          f"late={nd.get('late')} cap_hm={nd.get('cap_hm')}")
    if nd.get("missing"):
        print("  ★★ payload 仍标为缺失 —— 需继续排查")
        return 1
    print("\n  [SUCCESS] 节点采集链路已打通，UI 不再会显示「该节点未采集」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
