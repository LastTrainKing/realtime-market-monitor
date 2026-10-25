# -*- coding: utf-8 -*-
"""v2.0.1 性能剖析 —— 定位首屏加载与数据处理的真实热点。

为什么要先量再改：
  本项目历史上出现过"凭直觉优化"的教训（以为热点在绘图，实测在分位排名）。
  这里把 Worker.run 的处理链逐段计时，优化只针对真正的大头，
  改完再量一次对比（同机同数据，可直接比较）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(os.path.dirname(HERE), "build")
sys.path.insert(0, BUILD)
sys.path.insert(0, os.path.join(BUILD, "core"))

TAG = os.environ.get("FLOWLIVE_TEST_TAG", "20260921")
DATA = os.path.join(os.path.dirname(HERE), "runtime", "FlowLiveNative", "data")

import flowdata  # noqa: E402
flowdata.DATA = DATA
import salience  # noqa: E402
import native_app as NA  # noqa: E402


def timed(label, fn, *a, **kw):
    t0 = time.perf_counter()
    r = fn(*a, **kw)
    dt = (time.perf_counter() - t0) * 1000
    n = len(r) if hasattr(r, "__len__") else ""
    print(f"  {label:<44s} {dt:8.1f} ms   {n}")
    return r, dt


def main():
    print("=" * 74)
    print(f"性能剖析 · tag={TAG} · 数据目录={DATA}")
    print("=" * 74)

    tot = 0.0

    # ---- 1) 读面板 + 建特征 ----
    print("\n[1] 数据加载与特征构建")
    t0 = time.perf_counter()
    F = salience.build_features(TAG)
    dt = (time.perf_counter() - t0) * 1000
    tot += dt
    print(f"  {'salience.build_features':<44s} {dt:8.1f} ms   "
          f"{F.V.shape[0]}×{F.V.shape[1]}")
    last = len(F.times) - 1

    # ---- 2) 榜单类 ----
    print("\n[2] 榜单")
    for lab, fn in (("score_table(top30,in)", lambda: salience.score_table(F, last, 30)),
                    ("score_table(top30,out)", lambda: salience.score_table(F, last, 30, direction="out")),
                    ("velocity_table(d5,in)", lambda: salience.velocity_table(F, last, 15, kind="concept")),
                    ("industry_overview(31)", lambda: salience.industry_overview(F, last, 31))):
        _, d = timed(lab, fn)
        tot += d

    # ---- 3) 事件流（历史热点）----
    print("\n[3] 事件与时间轴")
    ev, d = timed("_event_stream(全扫)", NA._event_stream, F, salience)
    tot += d
    tl, d = timed("salience.timeline_summary(全扫)", salience.timeline_summary, F)
    tot += d

    # ---- 4) 聚类 / 主角 / 阶段 ----
    print("\n[4] 聚类与时间片")
    _, d = timed("cluster_themes(topn=8)", salience.cluster_themes, F, last, topn=8)
    tot += d
    _, d = timed("_leader_timeline(16 片)", NA._leader_timeline, F, salience)
    tot += d
    _, d = timed("_stage_snapshot(6 时点)", NA._stage_snapshot, F, salience)
    tot += d

    # ---- 5) 全市场汇总 ----
    print("\n[5] 全市场汇总（曲线选线）")
    _, d = timed("_market_stats", NA._market_stats, F, salience, last)
    tot += d

    # ---- 6) 榜单池 ----
    print("\n[6] 实时榜单池")
    import watchlist as _wl
    t0 = time.perf_counter()
    pool = _wl.Pool(TAG, DATA)
    pool.view()
    dt = (time.perf_counter() - t0) * 1000
    tot += dt
    print(f"  {'watchlist.Pool.view':<44s} {dt:8.1f} ms   {len(pool.rows)} 行")

    print("\n" + "=" * 74)
    print(f"合计（不含网络与绘图）：{tot:.0f} ms")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
