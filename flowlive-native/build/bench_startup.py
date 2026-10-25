# -*- coding: utf-8 -*-
"""启动延迟构成探头：把「点进 exe 到首屏可用」拆成逐阶段耗时。

为什么必须分段量：
  "启动要 5 秒"是个笼统感受，但 5 秒可能来自完全不同的地方 ——
  进程冷启（PyInstaller 解包）、导入 matplotlib、交易日历联网、
  1000 板块抓取、特征计算。**优化手段完全不同**，先量再改。

用法：
    python bench_startup.py            # 冷启动（不带 force，走缓存）
    python bench_startup.py --force    # 强制抓取（模拟盘中刷新）
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

MARKS = []


def mark(label, t0):
    dt = (time.perf_counter() - t0) * 1000
    MARKS.append((label, dt))
    print(f"  {dt:8.1f} ms   {label}")
    return time.perf_counter()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制重抓（盘中刷新路径）")
    ap.add_argument("--workers", type=int, default=24)
    args = ap.parse_args()

    print("\n=========== 启动延迟构成 ===========")

    t = time.perf_counter()
    import numpy as np
    t = mark("import numpy", t)

    import flowdata
    t = mark("import flowdata", t)

    # ★ 关键：模拟真实启动时数据目录的重定向
    import native_app
    data_dir = native_app.ensure_user_data_dir()
    t = mark("import native_app + 目录重定向", t)

    import tradecal
    t = mark("import tradecal", t)

    days, approx = tradecal.get_trading_days(refresh=True)
    t = mark(f"交易日历（{len(days)} 天, approx={approx}）", t)

    # 二次调用（模拟 reload 再走一次）—— 内存缓存应命中
    t2 = time.perf_counter()
    days2, _ = tradecal.get_trading_days(refresh=True)
    dt2 = (time.perf_counter() - t2) * 1000
    MARKS.append(("交易日历二次调用（应走内存缓存）", dt2))
    print(f"  {dt2:8.1f} ms   交易日历二次调用（应走内存缓存）")

    tag = "today"
    p = os.path.join(flowdata.DATA, f"panel_{tag}.npy")
    has_cache = os.path.exists(p)
    print(f"  {'-'*46}")
    print(f"  本地缓存 panel_{tag}.npy: {'有' if has_cache else '无'}")

    if args.force or not has_cache:
        t = time.perf_counter()
        flowdata.build_panel(tag, workers=args.workers,
                             on_progress=lambda m: None)
        t = mark(f"★ 抓取全市场（workers={args.workers}）", t)
    else:
        t2 = time.perf_counter()
        np.load(p, mmap_mode="r")
        dt2 = (time.perf_counter() - t2) * 1000
        MARKS.append(("读取本地面板 (.npy)", dt2))
        print(f"  {dt2:8.1f} ms   读取本地面板 (.npy)")
        print("  （未传 --force，跳过抓取）")

    import salience
    t = time.perf_counter()
    F = salience.build_features(tag)
    t = mark("★ build_features（特征+辨识度）", t)

    last = len(F.times) - 1
    t = time.perf_counter()
    salience.score_table(F, last, topn=30)
    salience.score_table(F, last, topn=30, direction="out")
    t = mark("★ score_table × 2（榜单）", t)

    print(f"  {'-'*46}")
    total = sum(v for _, v in MARKS)
    print(f"  合计（不含 Qt 启动与建窗）: {total/1000:.2f} s")
    print("\n  分项排序（谁最贵）:")
    for lab, ms in sorted(MARKS, key=lambda x: -x[1])[:8]:
        print(f"    {ms:9.1f} ms  {ms/total*100:5.1f}%   {lab}")


if __name__ == "__main__":
    main()
