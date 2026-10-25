# -*- coding: utf-8 -*-
"""板块数据读取全链路诊断（盘中实测用）

用户报告："部分板块无法读取数据"。本脚本把 4 个环节逐一扫描，
**列出具体哪些板块失败、失败在哪个环节、失败原因是什么** ——
因为 build_panel 里 `if rows:` 是静默跳过，日志只有"997/1000"这种
总数，根本定位不到具体板块（这是排查的第一个障碍）。

四个环节：
  A. 板块清单（list_sectors）—— 行业 496 + 概念 504 = 1000
  B. 分时资金流（fetch_sector_kline）—— 建面板的主数据源
  C. 快照批量（fetch_spot_batch）—— 增量刷新通道
  D. 清单 vs 快照 的键覆盖对比 —— 找出"清单里有但快照拿不到"的板块

用法：
  ../native_env/Scripts/python.exe diag_sector_read.py
  ../native_env/Scripts/python.exe diag_sector_read.py --workers 8
"""
import os
import sys
import time
import json
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
import flowdata                                                          # noqa: E402

RUNTIME_DATA = os.path.join(HERE, "..", "runtime", "FlowLiveNative", "data")


def banner(s):
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 个（0=全部）")
    ap.add_argument("--out", default="_sector_read_diag.json")
    args = ap.parse_args()

    if os.path.isdir(RUNTIME_DATA):
        flowdata.set_data_dir(RUNTIME_DATA)
        print(f"数据目录: {RUNTIME_DATA}")

    print(f"诊断时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # ============================================================ A. 板块清单
    banner("A. 板块清单（list_sectors）")
    sectors = []
    for kind in ("industry", "concept"):
        t0 = time.time()
        try:
            got = flowdata.list_sectors(kind)
        except Exception as e:                                    # noqa: BLE001
            print(f"  {kind}: 异常 {type(e).__name__}: {e}")
            got = []
        dt = (time.time() - t0) * 1000
        print(f"  {kind}: {len(got)} 个，耗时 {dt:.0f}ms")
        # 名录重复检查
        codes = [t[1] for t in got]
        dup = [c for c, n in Counter(codes).items() if n > 1]
        if dup:
            print(f"     ★ 清单内重复代码 {len(dup)} 个: {dup[:10]}")
        # 字段完整性
        bad = [t for t in got if not (t[0] and t[1] and t[2])]
        if bad:
            print(f"     ★ 字段缺失 {len(bad)} 条: {bad[:5]}")
        sectors += got

    print(f"  合计 {len(sectors)} 个板块")
    if args.limit:
        sectors = sectors[:args.limit]
        print(f"  （限测前 {len(sectors)} 个）")

    if not sectors:
        print("  [中止] 板块清单为空，接口不可用")
        return 2

    # ============================================================ B. 分时资金流
    banner(f"B. 分时资金流（fetch_sector_kline）· {args.workers} 并发")
    t0 = time.time()
    ok, fail = {}, {}
    n_rows = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(flowdata.fetch_sector_kline, s[0]): s for s in sectors}
        done = 0
        for fu in as_completed(futs):
            s = futs[fu]
            secid, code, name, kind = s
            done += 1
            try:
                rows = fu.result()
            except Exception as e:                                # noqa: BLE001
                fail[secid] = {"code": code, "name": name, "kind": kind,
                               "why": f"异常 {type(e).__name__}: {e}", "rows": 0}
                continue
            if rows:
                ok[secid] = (name, kind)
                n_rows[secid] = len(rows)
            else:
                fail[secid] = {"code": code, "name": name, "kind": kind,
                               "why": "空返回 / 无 klines", "rows": 0}
            if done % 200 == 0:
                print(f"    进度 {done}/{len(sectors)}  ({time.time()-t0:.0f}s)")
    dt = time.time() - t0
    print(f"  ★ 有数据 {len(ok)} / 无数据 {len(fail)} / 共 {len(sectors)}"
          f"   用时 {dt:.0f}s")
    if n_rows:
        vals = sorted(n_rows.values())
        print(f"  分钟行数: 最少 {vals[0]}  中位 {vals[len(vals)//2]}  最多 {vals[-1]}")

    # 行数异常（明显短于中位 → 该板块数据不完整）
    if n_rows:
        med = sorted(n_rows.values())[len(n_rows) // 2]
        short = {k: v for k, v in n_rows.items() if v < med * 0.8}
        if short:
            print(f"  ★ 行数明显偏短（< 中位 {med} 的 80%）的板块 {len(short)} 个：")
            for sid, n in list(sorted(short.items(), key=lambda x: x[1]))[:15]:
                nm = ok.get(sid, ("?",))[0]
                print(f"      {sid:14s} {nm:12s} {n} 行")

    # 失败清单（按原因归类）
    if fail:
        by_why = Counter(v["why"] for v in fail.values())
        print(f"\n  ★ 无数据板块按原因归类：")
        for why, n in by_why.most_common():
            print(f"      {n:4d} 个: {why}")
        by_kind = Counter(v["kind"] for v in fail.values())
        print(f"  ★ 按类型: {dict(by_kind)}")
        print(f"\n  ★ 无数据板块明细（前 30）：")
        for sid, v in list(fail.items())[:30]:
            print(f"      {sid:14s} {v['code']:8s} {v['name'][:14]:16s} "
                  f"{v['kind']:9s} {v['why']}")

    # ============================================================ C. 快照批量
    banner("C. 快照批量（fetch_spot_batch）")
    all_secids = [s[0] for s in sectors]
    t0 = time.time()
    try:
        spot = flowdata.fetch_spot_batch(all_secids, detail=True)
    except Exception as e:                                        # noqa: BLE001
        print(f"  异常 {type(e).__name__}: {e}")
        spot = {}
    dt = (time.time() - t0) * 1000
    print(f"  请求 {len(all_secids)} 个 secid，返回 {len(spot)} 个键，耗时 {dt:.0f}ms")

    # ============================================================ D. 覆盖对比
    banner("D. 清单 vs 快照 覆盖对比（清单里有但快照拿不到）")
    want = set(all_secids)
    got = set(spot.keys())
    missing = sorted(want - got)
    extra = sorted(got - want)
    print(f"  清单 {len(want)} · 快照 {len(got)}")
    print(f"  ★ 快照缺失 {len(missing)} 个")
    name_map = {s[0]: (s[2], s[3]) for s in sectors}
    for sid in missing[:30]:
        nm, kd = name_map.get(sid, ("?", "?"))
        print(f"      {sid:14s} {nm[:14]:16s} {kd}")

    # 净额为 null 的（拿到键但没值）
    null_net = [k for k, v in spot.items()
                if isinstance(v, dict) and v.get("net") is None]
    print(f"  ★ 快照里 net 为 null 的 {len(null_net)} 个")
    for sid in null_net[:15]:
        nm, kd = name_map.get(sid, ("?", "?"))
        print(f"      {sid:14s} {nm[:14]:16s} {kd}  net=None")
    if extra:
        print(f"  （快照多出清单外的 {len(extra)} 个: {extra[:5]}）")

    # ============================================================ 汇总落盘
    banner("汇总")
    report = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_sectors": len(sectors),
        "kline_ok": len(ok), "kline_fail": len(fail),
        "kline_fail_detail": fail,
        "spot_keys": len(spot),
        "spot_missing": missing,
        "spot_null_net": null_net,
        "rows_stat": {
            "min": min(n_rows.values()) if n_rows else 0,
            "median": sorted(n_rows.values())[len(n_rows) // 2] if n_rows else 0,
            "max": max(n_rows.values()) if n_rows else 0,
        },
    }
    out = os.path.join(HERE, args.out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  已写 {out}")
    print(f"  分时资金流: {len(ok)}/{len(sectors)} 成功")
    print(f"  快照:       {len(spot)}/{len(sectors)} 命中，缺失 {len(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
