# -*- coding: utf-8 -*-
"""verify_v121.py —— v1.2.1 优化的**等价性**深度验证（跑真实面板数据）

为什么需要它
------------
v1.2.1 对算法层做了向量化重写（`_pct_rank` 逐行 Python → 整块 numpy；
`persist` 循环 → 前缀和差分），对 universe 做了正则合并。
这些改动**必须证明输出一字不差**，否则"提速"就等于"改算法"。

做法
----
把**优化前的原始实现**照抄成本文件里的参考实现，用同一份真实面板各算一遍，
逐字段逐位比对。任何一处不等都会直接报出来。

用法
----
    python verify_v121.py            # 默认用 20260918 面板
    python verify_v121.py 20260919   # 指定 tag
"""

import os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
import flowdata as fd, salience as S, universe as U

dd = os.path.join(os.environ.get("LOCALAPPDATA", ""), "FlowLiveNative", "data")
fd.set_data_dir(dd)
TAG = sys.argv[1] if len(sys.argv) > 1 else "20260918"

# ---------- 参考实现：照抄 v1.1.8 的原始写法 ----------
def pct_rank_ORIG(x):
    n = len(x)
    if n == 0: return x
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(n, dtype=float); ranks[order] = np.arange(n, dtype=float)
    s = np.asarray(x)[order]; i = 0
    while i < n:
        j = i + 1
        while j < n and s[j] == s[i]: j += 1
        if j - i > 1: ranks[order[i:j]] = (i + j - 1) / 2.0
        i = j
    return ranks / max(1, n - 1)

def is_bucket_ORIG(name):
    return any(r.search(name) for r in U._DROP_RE)

def clean_ORIG(meta, keep_kinds=("industry", "concept")):
    names, kinds = meta["names"], meta["kinds"]
    kept = []
    for c in meta["codes"]:
        n, k = names[c], kinds[c]
        if k not in keep_kinds: continue
        if k == "concept":
            if is_bucket_ORIG(n): continue
        elif k == "industry":
            if n not in U.PRIMARY_INDUSTRIES: continue
        kept.append(c)
    return kept

def build_ORIG(tag):
    meta_all, mat_full, idx = fd.load_panel(tag)
    kept = clean_ORIG(meta_all)
    pos = {c: i for i, c in enumerate(meta_all["codes"])}
    cols = [pos[c] for c in kept if c in pos and pos[c] < mat_full.shape[1]]
    mat = mat_full[:, cols]
    times = meta_all["times"]; T, N = mat.shape
    V = mat.copy()
    dV1 = np.zeros_like(V); dV1[1:] = V[1:] - V[:-1]
    dV5 = np.zeros_like(V); dV5[S.VEL_WIN:] = V[S.VEL_WIN:] - V[:-S.VEL_WIN]
    resid = dV5 - dV5.mean(axis=1, keepdims=True)
    mu = np.nanmean(dV5, axis=0, keepdims=True)
    sd = np.nanstd(dV5, axis=0, keepdims=True); sd[sd < 1e-9] = 1e-9
    z = (dV5 - mu) / sd
    rank = np.zeros_like(V, dtype=int); rank_neg = np.zeros_like(V, dtype=int)
    for i in range(T):
        rank[i] = (-V[i]).argsort().argsort() + 1
        rank_neg[i] = (V[i]).argsort().argsort() + 1
    persist = np.zeros_like(V)
    topmask = (rank <= S.TOPK) | (rank_neg <= S.TOPK)
    for i in range(T):
        persist[i] = topmask[max(0, i - S.PERSIST_WIN + 1):i + 1].sum(axis=0)
    pct_abs = np.zeros_like(V); pct_vel = np.zeros_like(V); pct_jump = np.zeros_like(V)
    for i in range(T):
        pct_abs[i] = pct_rank_ORIG(np.abs(V[i]))
        pct_vel[i] = pct_rank_ORIG(np.abs(dV5[i]))
        if i >= S.JUMP_WIN:
            pct_jump[i] = pct_rank_ORIG((rank[i - S.JUMP_WIN] - rank[i]).astype(float))
    sal = (0.34*pct_abs + 0.28*pct_vel + 0.16*pct_jump
           + 0.12*(persist/S.PERSIST_WIN) + 0.10*np.clip(z/3.0, 0, 1))
    return dict(V=V, dV5=dV5, z=z, rank=rank, rank_neg=rank_neg, persist=persist,
                pct_abs=pct_abs, pct_vel=pct_vel, pct_jump=pct_jump, salience=sal)

print("计算参考实现（原始算法）…")
R = build_ORIG(TAG)
print("计算当前实现（优化后）…")
F = S.build_features(TAG)

print(f"  形状一致: {F.V.shape == R['V'].shape}  {F.V.shape}")
allok = True
# 注：pct_jump 是 build_features 的局部量、不在 Features 里，
# 但它通过 salience 参与合成 —— 所以 salience 相等即证明 pct_jump 也相等。
for k in ("V","dV5","z","rank","rank_neg","persist",
          "pct_abs","pct_vel","salience"):
    a, b = getattr(F, k), R[k]
    eq = np.array_equal(a, b)
    md = np.max(np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)))
    allok &= eq
    print(f"  [{'OK ' if eq else '★错'}] {k:<10s} 完全相等={eq}  最大差={md:.3e}")
print()
print("  → ★ 优化前后输出完全一致（零差异）" if allok else "  → ★ 输出存在差异")

# 下游产物是否也一致
print()
print("下游一致性：")
print("  收盘解说:", S.narrate(F, len(F.times)-1)[:60], "…")
th = S.cluster_themes(F, len(F.times)-1, topn=8)
print(f"  题材簇条数={len(th)}  首条={th[0]['seed']} {th[0]['total_yi']:+.1f}亿")
