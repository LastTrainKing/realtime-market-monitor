# -*- coding: utf-8 -*-
"""bench_perf.py —— 资金流向原生版 · 性能基准（可重复运行）

用途
----
把「数据抓取」与「算法」两条链路的耗时**量化**出来，便于：
  · 改动前后对比（防止性能回退）；
  · 排查"某天特别慢"到底慢在哪一段。

用法
----
    python bench_perf.py            # 全量（含联网，约 1~2 分钟）
    python bench_perf.py --alg      # 只测算法（离线，秒级）
    python bench_perf.py --net      # 只测网络（会打真实接口，注意限流）

★ 关于联网测量的一条重要提醒
  东财 push2 对**同 IP 的高频请求**会限流（返回 RemoteDisconnected）。
  连续跑多次网络基准很容易把自己打进限流，届时数字会全面变差 ——
  那不是代码退化，而是被限流了。判断方法：看输出里 "主机健康" 一段，
  若主域被标为冷却中、且 `_ka_proven()` 仍为 True，说明通道正常、是单域被限。
  受控对比请**间隔开**做，或分不同时段做。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))

import numpy as np                                    # noqa: E402
import flowdata as fd                                 # noqa: E402
import salience as S                                  # noqa: E402
import universe as U                                  # noqa: E402

DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "FlowLiveNative", "data")
TAG = "20260918"


def _med(fn, n=7):
    """多次运行取中位数（比均值抗抖动）。"""
    xs = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t) * 1000)
    return statistics.median(xs)


def _have_local():
    return os.path.exists(os.path.join(DATA_DIR, f"panel_{TAG}.json"))


# --------------------------------------------------------------------- 算法
def bench_alg():
    print("=" * 72)
    print("一、算法层（离线，中位数 / 7 次）")
    print("=" * 72)
    if not _have_local():
        print(f"  [!] 本地没有 panel_{TAG}，跳过。请先跑一次实盘或 --net。")
        return
    fd.set_data_dir(DATA_DIR)
    meta, mat, idx = fd.load_panel(TAG)
    S.build_features(TAG)                             # 预热

    print(f"  load_panel                      {_med(lambda: fd.load_panel(TAG)):8.2f} ms")
    print(f"  clean_universe                  {_med(lambda: U.clean_universe(meta)):8.2f} ms")

    # ★ 参考实现：**照抄优化前的原始写法**（Python 双层 while 求平均秩）。
    #   注意不能拿 S._pct_rank 当"旧写法"比 —— 它现在是新实现的薄封装，
    #   比出来只有 2× 左右，会把真实收益严重低估。
    #   原始实现的真实差距：整条 build_features 97.5 ms → 约 34 ms。
    def _pct_rank_ORIG(x):
        n = len(x)
        if n == 0:
            return x
        order = np.argsort(x, kind="mergesort")
        ranks = np.empty(n, dtype=float)
        ranks[order] = np.arange(n, dtype=float)
        s = np.asarray(x)[order]
        i = 0
        while i < n:
            j = i + 1
            while j < n and s[j] == s[i]:
                j += 1
            if j - i > 1:
                ranks[order[i:j]] = (i + j - 1) / 2.0
            i = j
        return ranks / max(1, n - 1)

    F = S.build_features(TAG)
    V = F.V
    t_rows = _med(lambda: [_pct_rank_ORIG(np.abs(V[i])) for i in range(len(V))], 3)
    t_blk = _med(lambda: S._pct_rank_rows(np.abs(V)), 7)
    print(f"  _pct_rank 逐行×{len(V)}（**原始实现**）  {t_rows:8.2f} ms")
    print(f"  _pct_rank_rows 整块（现写法）        {t_blk:8.2f} ms")
    print(f"    → 单矩阵提速 {t_rows / max(t_blk, 1e-9):.2f}×"
          f"（build_features 里跑 3 个这样的矩阵）")

    t_bf = _med(lambda: S.build_features(TAG), 7)
    print(f"  build_features（整条链路）        {t_bf:8.2f} ms"
          f"   ← v1.2.0 实测约 97.5 ms")

    last = len(F.times) - 1
    print(f"  cluster_themes(topn=8)          "
          f"{_med(lambda: S.cluster_themes(F, last, topn=8)):8.2f} ms")
    print(f"  session_report                  "
          f"{_med(lambda: S.session_report(F)):8.2f} ms")
    print(f"  timeline_summary                "
          f"{_med(lambda: S.timeline_summary(F)):8.2f} ms")
    print()
    print("  验收基线：build_features 应 < 60 ms（本机实测约 33 ms）。")
    print("           若显著高于此值，先确认是否被其他进程抢占 CPU。")


# --------------------------------------------------------------------- 网络
def bench_net():
    print()
    print("=" * 72)
    print("二、数据抓取（联网，会打真实接口）")
    print("=" * 72)
    fd.set_data_dir(DATA_DIR)
    secids = ["90.BK0475", "90.BK0493", "90.BK0494", "90.BK0497",
              "90.BK0498", "90.BK0500", "90.BK0501", "90.BK0503"]

    print("  [A] 连接复用 vs 每请求新建（隔离 TLS 握手成本）")
    import http.client

    def path_of(s):
        return (f"/api/qt/stock/fflow/kline/get?lmt=0&klt=1&secid={s}"
                f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={fd.UT}")

    host = fd._load_hosts()[0]
    h = fd._host_of(host)

    def run_reuse():
        c = http.client.HTTPSConnection(h, timeout=10, context=fd._ssl_ctx())
        n = 0
        for s in secids:
            try:
                c.request("GET", path_of(s), headers=fd.HDRS)
                if c.getresponse().read():
                    n += 1
            except Exception:                          # noqa: BLE001
                c.close()
                c = http.client.HTTPSConnection(h, timeout=10,
                                                context=fd._ssl_ctx())
        c.close()
        return n

    def run_new():
        n = 0
        for s in secids:
            try:
                c = http.client.HTTPSConnection(h, timeout=10, context=fd._ssl_ctx())
                c.request("GET", path_of(s), headers=fd.HDRS)
                if c.getresponse().read():
                    n += 1
                c.close()
            except Exception:                          # noqa: BLE001
                pass
        return n

    # 各跑两轮取较优（第一轮含 TLS 冷启动与 DNS，不具代表性）
    res = {}
    for label, fn in (("复用", run_reuse), ("新建", run_new),
                      ("复用", run_reuse), ("新建", run_new)):
        t0 = time.perf_counter()
        ok = fn()
        dt = time.perf_counter() - t0
        res.setdefault(label, []).append((ok, dt))
        print(f"      {label}连接: {ok}/{len(secids)} 成功 · {dt:5.2f}s"
              f" · 均值 {dt / len(secids) * 1000:5.0f} ms")
    r = min(d[1] for d in res["复用"])
    nw = min(d[1] for d in res["新建"])
    print(f"    → 复用比新建快 {nw / max(r, 1e-9):.2f}×"
          f"（干净网络下实测可达 ~10×；限流时段会被压缩）")

    print()
    print("  [B] 端到端抓一批（含清单/指数并行 + 连接复用）")
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp(prefix="bench_bp_")
    fd.set_data_dir(tmp)
    t0 = time.perf_counter()
    try:
        meta, mat, idx = fd.build_panel("bench", workers=16, limit=80,
                                        kinds=("industry", "concept"))
        dt = time.perf_counter() - t0
        print(f"      build_panel(limit=80) {dt:5.1f}s · mat={mat.shape}"
              f" · idx={len(idx)} 点 · NaN={int(np.isnan(mat).sum())}")
        print(f"      → 折算全市场 1000 板块约 {dt / 80 * 1000:.0f}s"
              f"（受单域限流影响很大，仅作量级参考）")
    except Exception as e:                             # noqa: BLE001
        print(f"      build_panel 失败：{type(e).__name__}: {e}")
        print("      （多半是被限流了 —— 等一段时间再试）")
    shutil.rmtree(tmp, ignore_errors=True)
    fd.set_data_dir(DATA_DIR)

    print()
    print("  [C] 主机健康")
    print(f"      keep-alive 通道已证可用 = {fd._ka_proven()}")
    print(f"      keep-alive 通道已停用   = {fd._ka_off()}")
    print(f"      被冷却的域名           = "
          f"{[k for k in fd.HOSTS if fd._host_cooling(k)] or '无'}")
    print(f"      当前域名顺序           = {fd._load_hosts()}")
    fd.close_connections()


def main():
    ap = argparse.ArgumentParser(description="资金流向原生版性能基准")
    ap.add_argument("--alg", action="store_true", help="只测算法（离线）")
    ap.add_argument("--net", action="store_true", help="只测网络")
    a = ap.parse_args()
    print(f"数据目录: {DATA_DIR}")
    print(f"面板 tag : {TAG}"
          + ("" if _have_local() else "  [本地缺失]"))
    if a.alg or not (a.alg or a.net):
        bench_alg()
    if a.net or not (a.alg or a.net):
        bench_net()
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
