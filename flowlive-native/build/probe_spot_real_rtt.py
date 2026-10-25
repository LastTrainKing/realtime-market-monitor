# -*- coding: utf-8 -*-
"""P0 实测：真实网络下 fetch_spot_batch 并行 vs 串行（v3.0.3 遗留待测）

与 test_spot_parallel.py 的区别（两者都要，不可互替）：
  - test_spot_parallel.py 用**打桩 RTT**（_get 被替换成 time.sleep）：
    验证的是"并发逻辑本身对不对 + 加速比的理论上限"，与网络无关，可离线跑。
  - 本脚本用**真实 _get**：验证的是"真实网络下到底快多少 + 会不会被限流"，
    **必须联网、且最好在交易时段跑**（非交易时段接口可能整体不可用）。

为什么要在交易时段跑：
  非交易时段东财对 ulist.np 的响应可能被 CDN 直接短路（返回空/极快/或直接拒绝），
  得到的 RTT 不代表盘中真实值，据此算出的占空比会严重偏乐观。

用法：
  ../native_env/Scripts/python.exe probe_spot_real_rtt.py
  ../native_env/Scripts/python.exe probe_spot_real_rtt.py --sectors 1000 --rounds 5
"""
import os
import sys
import time
import json
import argparse
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
import flowdata                                                          # noqa: E402

# 数据目录（真实运行时的缓存目录，与程序一致）
RUNTIME_DATA = os.path.join(HERE, "..", "runtime", "FlowLiveNative", "data")


def banner(s):
    print()
    print("=" * 72)
    print(s)
    print("=" * 72)


def preflight():
    """接口可用性预检 —— 不可用就直接终止，不做无意义的反复重试。

    ★ 反复重试会加剧限流，所以这里最多试 2 次就放弃。
    """
    banner("预检：接口可用性")
    if os.path.isdir(RUNTIME_DATA):
        flowdata.set_data_dir(RUNTIME_DATA)
        print(f"  数据目录: {RUNTIME_DATA}")
    else:
        print(f"  [警告] 数据目录不存在，跳过: {RUNTIME_DATA}")

    t0 = time.time()
    try:
        secs = flowdata.list_sectors(kind="industry", page_size=10)
    except Exception as e:                                        # noqa: BLE001
        print(f"  [FAIL] list_sectors 异常: {type(e).__name__}: {e}")
        return None
    dt = (time.time() - t0) * 1000
    if not secs:
        print(f"  [FAIL] list_sectors 返回空（{dt:.0f}ms）"
              f" —— 接口当前不可用（夜间/维护/代理问题）")
        print("  结论：本次无法实测。请改在交易时段重跑。")
        return None
    print(f"  [OK] list_sectors 返回 {len(secs)} 个板块，耗时 {dt:.0f}ms")
    # ★ 返回结构是 4 元组 (secid, code, name, kind)，不是 dict —— 别写 .get()
    print(f"       样例: {secs[:3]}")
    return secs


def gather_codes(secs, want):
    """取够 want 个 secid（不足则循环填充）。secs 元素是 (secid, code, name, kind)。"""
    codes = [t[0] for t in secs if t and t[0]]
    if not codes:
        return []
    while len(codes) < want:
        codes = codes + codes[:want - len(codes)]
    return codes[:want]


def timeit_real(codes, workers, rounds):
    """真实网络下跑 rounds 轮，返回 (中位 ms, 各轮 ms, 返回键数, 平均 RTT 估计)。"""
    ts, keys = [], []
    for i in range(rounds):
        t0 = time.time()
        try:
            out = flowdata.fetch_spot_batch(codes, detail=True, workers=workers)
        except Exception as e:                                    # noqa: BLE001
            print(f"      round {i + 1}: 异常 {type(e).__name__}: {e}")
            ts.append(float("nan"))
            keys.append(0)
            continue
        dt = (time.time() - t0) * 1000
        ts.append(dt)
        keys.append(len(out))
        print(f"      round {i + 1}: {dt:8.1f} ms   返回 {len(out):4d} 键")
    good = [t for t in ts if t == t]                              # 去 NaN
    med = statistics.median(good) if good else float("nan")
    return med, ts, (statistics.median(keys) if keys else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sectors", type=int, default=1000,
                    help="参与测试的 secid 个数（默认 1000，即全市场）")
    ap.add_argument("--rounds", type=int, default=5, help="每档轮数（默认 5）")
    ap.add_argument("--workers", type=str, default="1,3,6",
                    help="要对比的并发度，逗号分隔（默认 1,3,6）")
    args = ap.parse_args()

    print(f"v3.0.3 采集并行 —— 真实网络实测")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    now = time.localtime()
    in_session = (now.tm_hour * 60 + now.tm_min) in range(9 * 60 + 30, 15 * 60 + 1)
    print(f"交易时段: {'是' if in_session else '否 —— 结果仅供参考，建议交易时段重跑'}")

    secs = preflight()
    if not secs:
        return 2

    # 先解析并发档位（下面打印分片数时要用来判断"档位是否有意义"）
    workers_list = [int(x) for x in args.workers.split(",") if x.strip()]

    codes = gather_codes(secs, args.sectors)
    # ★ 关键：gather_codes 可能会把不足的 secid 循环填充（重复），
    #   这会让"分片数"与预期不符（实测 496 个真实板块填到 1000 个 →
    #   只有 3 片有效分片，workers=3/6 走的是同一条调度路径）。
    #   这里把真实分片数打印出来，避免据此得出错误结论。
    n_uniq = len(set(codes))
    n_chunks = (len(codes) + flowdata.SPOT_BATCH - 1) // flowdata.SPOT_BATCH
    n_chunks_uniq = (n_uniq + flowdata.SPOT_BATCH - 1) // flowdata.SPOT_BATCH
    print(f"  将测试 {len(codes)} 个 secid（去重后 {n_uniq} 个）")
    print(f"  SPOT_BATCH = {flowdata.SPOT_BATCH} → 分片数 {n_chunks}（去重口径 {n_chunks_uniq}）")
    if n_uniq < len(codes):
        print(f"  ★ 注意：有 {len(codes) - n_uniq} 个是重复填充，"
              f"真实分片以去重口径 {n_chunks_uniq} 为准")
    if n_chunks_uniq < max(workers_list):
        print(f"  ★★ 警告：去重后分片数 {n_chunks_uniq} < 最大并发 {max(workers_list)}，"
              f"高并发档位与低档位走的是同一条调度路径，无法体现并发差异！"
              f"请调大 --sectors 或调小 --workers")

    results = {}
    for w in workers_list:
        banner(f"workers={w}  （{args.rounds} 轮）")
        med, ts, nk = timeit_real(codes, w, args.rounds)
        results[w] = {"median_ms": med, "rounds_ms": ts, "keys": nk}
        print(f"    → 中位 {med:.1f} ms，返回 {nk} 键")

    # 汇总
    banner("汇总（中位耗时）")
    base = results.get(workers_list[0], {}).get("median_ms")
    print(f"  {'workers':>8} {'中位 ms':>12} {'加速比':>10} {'返回键数':>10}")
    for w in workers_list:
        r = results[w]
        sp = (base / r["median_ms"]) if (base and r["median_ms"] == r["median_ms"]
                                         and r["median_ms"] > 0) else float("nan")
        print(f"  {w:>8} {r['median_ms']:>12.1f} {sp:>9.2f}x {r['keys']:>10}")

    # ---------------------------------------------------------------
    # ★★ 数据有效性门禁（v3.0.3 教训）
    #   夜间/代理不稳时，_get 会返回 None，fetch_spot_batch 于是返回 **0 键**。
    #   此时耗时反映的是"超时等待"，不是"真实抓取"，
    #   **不能**用它算加速比，否则会得出"并行有效 2.8×"这种假结论
    #   （2026-09-23 01:47 实测踩到：接口死后 3 档耗时 7027/4742/2513ms，
    #    看似漂亮，其实全是超时，返回键数 0）。
    #   判据：任一档位返回键数 < 去重分片应有键数的 50% → 整轮判为无效。
    # ---------------------------------------------------------------
    exp_keys = n_uniq
    valid = all(results[w]["keys"] >= max(1, exp_keys) * 0.5 for w in workers_list)
    print()
    if not valid:
        print(f"  ★★ 本次实测【无效】—— 返回键数不足（期望 ≈{exp_keys}）：")
        for w in workers_list:
            print(f"       workers={w}: 返回 {results[w]['keys']} 键")
        print(f"     说明接口当前不可用（_get 返回 None），耗时只是超时等待，")
        print(f"     **不可用于计算加速比**。请改在交易时段重跑本脚本。")
        verdict = "接口不可用 · 实测无效"
    else:
        best_w = max(workers_list)
        best = results[best_w]["median_ms"]
        if best == best:
            verdict = ("并行有效" if base and best < base * 0.7 else
                       "并行收益不明显（单分片或 RTT 本就低）")
            print(f"  结论: worker=1 {base:.1f}ms → worker={best_w} {best:.1f}ms  →  {verdict}")
            if results[best_w]["keys"] < results[workers_list[0]]["keys"]:
                print(f"  ★★ 警告: 并行键数少于串行 "
                      f"({results[best_w]['keys']} vs {results[workers_list[0]]['keys']})"
                      f" —— 疑有限流/丢包，需下调 workers")
        else:
            verdict = "全部失败"
            print("  结论: 全部失败，无法判定")

    out = os.path.join(HERE, "_real_rtt_v303.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "in_session": in_session, "n_secids": len(codes),
                   "n_uniq": n_uniq, "n_chunks": n_chunks_uniq,
                   "valid": valid, "verdict": verdict,
                   "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n已写 {out}  (valid={valid})")
    return 0 if valid else 3


if __name__ == "__main__":
    sys.exit(main())
