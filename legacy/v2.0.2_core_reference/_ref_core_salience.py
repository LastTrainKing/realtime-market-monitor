# -*- coding: utf-8 -*-
"""
salience.py —— 盘中「板块辨识度」评分 + 主线聚合 + 事件检测 + 解读生成

这是要复刻的核心能力：不是画图，而是**在每个时间点上判断"现在市场的主角是谁"**。

辨识度（salience）= 一个板块此刻"能不能被一眼认出来是主角"，由五个可观测维度合成：
    1. 体量  |V|        —— 累计主力净额的横截面分位          （钱有多少）
    2. 速度  |ΔV5|      —— 近 5 分钟增量的横截面分位        （钱来得快不快）
    3. 跃迁  jump       —— 相对 10 分钟前的排名提升          （是不是新面孔）
    4. 持续  persist    —— 过去 20 分钟内进入 TopK 的分钟数  （是不是一直强）
    5. 突异  z          —— 近 5 分钟增量相对自身波动率的 z    （今天是否反常）

两个关键处理（不做就得出错误结论）：
· **市场中性化**：全市场板块的分时资金是同涨同跌的（共同因子）。
  不做中性化直接聚类，会把 400 个板块聚成一个大簇。必须先扣掉横截面均值。
· **分层**：行业层（31 个一级行业）看大盘格局；概念层（~400 个）看主线题材。
  事件与主线识别以概念层为主，行业层只作背景，避免大桶霸榜。

输出：
    score_table(t)      -> 该时刻辨识度排序
    detect_events(t)    -> 值得解说的事件（新晋/加速/反转/背离/排名跃迁）
    cluster_themes(t)   -> 主线题材簇及其合计资金（市场中性化后聚类）
    narrate(t)          -> 一句话解读
    session_report(t)   -> 盘中/盘后报告
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ★ 只导入函数，**不要** `from flowdata import DATA`（v1.1.6 修复）：
#   DATA 是模块级字符串，`from ... import DATA` 拿到的是导入那一刻的**值拷贝**，
#   之后 flowdata.DATA 被重定向（exe 下从只读 _MEIPASS 换到用户目录）时，
#   salience.DATA 不会跟着变，两者静默脱钩。本项目里 salience.DATA 从未被使用，
#   属死引用；但留着就是一颗雷 —— 谁哪天拿它拼路径就会踩到。
#   需要路径时统一走 flowdata.DATA（惰性读取，永远是最新值）。
#   （同一个坑 tradecal.py 已规避并留了注释，这里补齐。）
from flowdata import load_panel
from universe import clean_universe

TOPK = 20
PERSIST_WIN = 20
VEL_WIN = 5
JUMP_WIN = 10


@dataclass
class Features:
    times: list
    codes: list
    names: dict
    kinds: dict
    V: np.ndarray          # (T,N) 累计主力净额(亿)
    dV5: np.ndarray        # (T,N) 近5分钟增量
    dV1: np.ndarray        # (T,N) 单分钟增量
    resid: np.ndarray      # (T,N) 市场中性化后的增量
    z: np.ndarray          # (T,N) 增量 z 值
    rank: np.ndarray       # (T,N) 净流入排名
    rank_neg: np.ndarray   # (T,N) 净流出排名
    persist: np.ndarray    # (T,N) TopK 持续分钟数
    pct_abs: np.ndarray
    pct_vel: np.ndarray
    salience: np.ndarray   # (T,N) 辨识度 0~1
    index: list            # 指数分时 [(hm, price, vol)]
    concept_mask: np.ndarray


def _pct_rank(x):
    """分位排名（0~1）。并列值取**平均秩**，不人为拉开差距。

    ★ v1.1.6 修复：原实现 `argsort(argsort(x)) / (n-1)` 对并列值会给
      出**不同**的分位 —— 实测 [5,5,5] → [0.0, 0.5, 1.0]。
      这些板块明明一模一样，辨识度却被拆成三档；在"多个板块净额同为 0"
      （开盘初期、或某分钟全体无变化）时，会给 salience 注入纯噪声，
      进而影响榜单排序与"主角更替"的取前三。
      改为平均秩：并列者拿到同一个分位。

    ★ v1.2.1：单行版本保留为**薄封装**（向后兼容），
      批量计算走 `_pct_rank_rows`（向量化，快一个量级）。
    """
    return _pct_rank_rows(np.asarray(x, dtype=float).reshape(1, -1))[0]


def _pct_rank_rows(mat):
    """对 (T, N) 矩阵**逐行**做平均秩分位（0~1）—— 全向量化。

    语义与单行版**完全一致**：稳定排序 → 并列组取平均秩 → 除以 (N-1)。
    等价性可按"并列组在排序后的位置区间 [starts, ends]"直接对上：
        原式  (i + j - 1) / 2      ← 组跨排序位 i..j-1
        本式  (starts + ends) / 2  ← 完全同一个值

    ★ 为什么要向量化（v1.2.1 性能）：
      原实现对每一行跑一个 Python 双层 while 循环 ——
      240 行 × 3 个指标 = 720 次调用，实测占 build_features 总耗时的
      **约 66%（74ms / 100ms）**，是算法层唯一的真热点。
      改成整块 numpy 后，这块降到个位数毫秒。
      注意这不是"用近似换速度"：输出与原实现逐位一致（见 N 段断言）。
    """
    a = np.asarray(mat, dtype=float)
    if a.ndim == 1:
        a = a.reshape(1, -1)
    if a.size == 0:
        return np.zeros_like(a)
    T, N = a.shape
    if N == 0:
        return np.zeros_like(a)

    order = np.argsort(a, axis=1, kind="mergesort")     # 稳定排序（同原实现）
    rows = np.arange(T)[:, None]
    s = a[rows, order]                                  # 排序后的值（新数组）

    # 并列组的**起止排序位**（组首/组尾都是**闭区间**下标）：
    #   组首 = 首列 或 与前一列不同；组尾 = 末列 或 与后一列不同。
    #   再用 maximum/minimum.accumulate 把"组首/组尾"广播到组内每一列。
    #
    #   ★ 踩过的坑：`is_end[j]=True` 表示"j 是组内**最后一个**下标"，
    #     所以它就是闭区间右端，**不能再减 1** ——
    #     第一版照抄原实现的 `(i+j-1)/2`（那里 j 是开区间右端）多减了一次 1，
    #     导致每档分位整体偏低 1/(N-1)（单列时甚至变成 -0.5）。
    #     等价性由 _verify_vec.py 的逐位比对把关。
    is_start = np.ones((T, N), dtype=bool)
    is_start[:, 1:] = s[:, 1:] != s[:, :-1]
    is_end = np.ones((T, N), dtype=bool)
    is_end[:, :-1] = s[:, :-1] != s[:, 1:]

    idx = np.broadcast_to(np.arange(N), (T, N))
    starts = np.maximum.accumulate(np.where(is_start, idx, -1), axis=1)
    ends = np.minimum.accumulate(
        np.where(is_end, idx, N)[:, ::-1], axis=1)[:, ::-1]

    avg_rank = (starts + ends) / 2.0                    # 闭区间 → (首+尾)/2
    out = np.empty((T, N), dtype=float)
    out[rows, order] = avg_rank                         # 映回原列序
    return out / max(1, N - 1)


def build_features(tag="today") -> Features:
    meta_all, mat_full, idx = load_panel(tag)
    kept, names, kinds, _ = clean_universe(meta_all)
    # ★ 用 dict 建映射并**逐项校验**（v1.1.6 加固）：
    #   原写法 `[pos[c] for c in kept]` 依赖「kept 必是 codes 子集」这一隐含前提。
    #   它在 clean_universe 当前实现下成立，但一旦上游改成"补充/规范化代码"
    #   （例如把 BK0596 补成 90.BK0596），这里就会抛 KeyError；
    #   而 mat_full 若因去重导致列数变少，列索引还会越界成 IndexError。
    #   显式过滤 + 对齐行数，把"静默踩坑"变成"明确跳过"，并记录丢弃数。
    pos = {c: i for i, c in enumerate(meta_all["codes"])}
    cols, kept_f = [], []
    for c in kept:
        i = pos.get(c)
        if i is None or i >= mat_full.shape[1]:
            continue                       # 代码不在面板里 / 索引越界 → 跳过
        cols.append(i)
        kept_f.append(c)
    kept = kept_f
    if not cols:                           # 全被过滤掉：给一个形状正确、全 0 的空特征
        T0 = mat_full.shape[0] if mat_full.ndim == 2 else 0
        z0 = np.zeros((T0, 0))
        return Features(meta_all.get("times", []), [], {}, {},
                        z0, z0, z0, z0, z0,
                        np.zeros((T0, 0), dtype=int),
                        np.zeros((T0, 0), dtype=int),
                        z0, z0, z0, z0, idx, np.zeros(0, dtype=bool))
    mat = mat_full[:, cols]
    times, codes = meta_all["times"], kept
    T, N = mat.shape
    V = mat.copy()

    dV1 = np.zeros_like(V)
    dV1[1:] = V[1:] - V[:-1]
    dV5 = np.zeros_like(V)
    dV5[VEL_WIN:] = V[VEL_WIN:] - V[:-VEL_WIN]

    resid = dV5 - dV5.mean(axis=1, keepdims=True)          # 市场中性化

    mu = np.nanmean(dV5, axis=0, keepdims=True)
    sd = np.nanstd(dV5, axis=0, keepdims=True)
    sd[sd < 1e-9] = 1e-9
    z = (dV5 - mu) / sd

    rank = np.zeros_like(V, dtype=int)
    rank_neg = np.zeros_like(V, dtype=int)
    for i in range(T):
        rank[i] = (-V[i]).argsort().argsort() + 1
        rank_neg[i] = (V[i]).argsort().argsort() + 1

    persist = np.zeros_like(V)
    topmask = (rank <= TOPK) | (rank_neg <= TOPK)
    # ★ v1.2.1 向量化：滑动窗口求和 → 前缀和差分。
    #   persist[i] = Σ topmask[max(0, i-W+1) .. i]
    #              = cs[i] - cs[i-W]   （i >= W；否则就是 cs[i]）
    #   topmask 是 bool，cumsum 得到整数和 → 结果与原循环**逐位一致**，
    #   不存在浮点误差或语义漂移。
    cs = np.cumsum(topmask, axis=0)
    persist = cs.astype(float)
    if T > PERSIST_WIN:
        persist[PERSIST_WIN:] = (cs[PERSIST_WIN:] - cs[:-PERSIST_WIN])

    # ★ v1.2.1：三个分位矩阵一次算完（原先是 240 次逐行 Python 调用，
    #   占 build_features 约 66% 的耗时 —— 见 _pct_rank_rows 的说明）。
    pct_abs = _pct_rank_rows(np.abs(V))
    pct_vel = _pct_rank_rows(np.abs(dV5))
    pct_jump = np.zeros_like(V)
    if T > JUMP_WIN:
        # 原式：pct_jump[i] = _pct_rank(rank[i-JUMP_WIN] - rank[i])，i >= JUMP_WIN
        # 对齐成整块：第 i 行用 (rank[i-JUMP_WIN], rank[i]) 这一对
        pct_jump[JUMP_WIN:] = _pct_rank_rows(
            (rank[:-JUMP_WIN] - rank[JUMP_WIN:]).astype(float))

    salience = (0.34 * pct_abs + 0.28 * pct_vel + 0.16 * pct_jump
                + 0.12 * (persist / PERSIST_WIN) + 0.10 * np.clip(z / 3.0, 0, 1))

    cmask = np.array([kinds[c] == "concept" for c in codes])
    return Features(times, codes, names, kinds, V, dV5, dV1, resid, z, rank, rank_neg,
                    persist, pct_abs, pct_vel, salience, idx, cmask)


# ---------------------------------------------------------------- 榜单
def score_table(F: Features, i: int, topn=15, kind=None, direction="in"):
    vals = F.V[i]
    order = np.argsort(vals) if direction == "out" else np.argsort(-vals)
    rows = []
    for j in order:
        if kind == "concept" and not F.concept_mask[j]:
            continue
        if kind == "industry" and F.concept_mask[j]:
            continue
        r = int(F.rank[i, j])
        rows.append({
            "code": F.codes[j], "name": F.names[F.codes[j]], "kind": F.kinds[F.codes[j]],
            "net_yi": round(float(vals[j]), 2),
            "d5_yi": round(float(F.dV5[i, j]), 2),
            "rank": r,
            "jump10": int(F.rank[max(0, i - JUMP_WIN), j] - r),
            "z": round(float(F.z[i, j]), 2),
            "persist": int(F.persist[i, j]),
            "salience": round(float(F.salience[i, j]), 3),
        })
        if len(rows) >= topn:
            break
    return rows


def industry_overview(F: Features, i: int, topn=8):
    return {"in": score_table(F, i, topn, kind="industry", direction="in"),
            "out": score_table(F, i, topn, kind="industry", direction="out")}


# ---------------------------------------------------------------- 事件
def detect_events(F: Features, i: int, concepts_only=True, z_gate=1.8):
    """以概念层为主；门槛用横截面分位而非绝对金额，避免大行业桶霸榜。"""
    ev = []
    V, dV5, rank, resid = F.V[i], F.dV5[i], F.rank[i], F.resid[i]
    prev_rank = F.rank[max(0, i - JUMP_WIN)]
    prevV = F.V[max(0, i - JUMP_WIN)]
    pv = F.pct_vel[i]

    idx_move = None
    if F.index:
        p = min(len(F.index) - 1, i)
        try:
            idx_move = F.index[p][1] - F.index[max(0, p - VEL_WIN)][1]
        except Exception:
            idx_move = None

    for j in np.argsort(-np.abs(resid))[:80]:
        if concepts_only and not F.concept_mask[j]:
            continue
        name = F.names[F.codes[j]]
        v, d, sal = V[j], dV5[j], F.salience[i, j]
        if abs(v) < 0.8 and abs(d) < 0.5:
            continue

        if rank[j] <= 10 and prev_rank[j] > 12:
            ev.append(("新晋", name, f"资金排名 {prev_rank[j]} → 第 {rank[j]} 位，净额 {v:+.2f} 亿",
                       abs(v) * (1 + sal) * 0.9))
        if pv[j] >= 0.97 and abs(F.z[i, j]) >= z_gate:
            ev.append(("加速流入" if d > 0 else "加速流出", name,
                       f"近 5 分钟 {d:+.2f} 亿（当日 {v:+.2f} 亿），z={F.z[i, j]:.1f}",
                       abs(d) * 1.3))
        if v > 0.5 > prevV[j] and d > 0:
            ev.append(("资金回流", name, f"由 {prevV[j]:+.2f} 亿翻多至 {v:+.2f} 亿", abs(v) * 1.5))
        if v < -0.5 < prevV[j] and d < 0:
            ev.append(("资金撤离", name, f"由 {prevV[j]:+.2f} 亿转为 {v:+.2f} 亿", abs(v) * 1.4))
        if idx_move is not None and F.pct_abs[i, j] >= 0.90:
            if v > 1 and idx_move < -3:
                ev.append(("逆向吸筹", name, f"指数 {idx_move:+.1f} 点下跌中净流入 {v:+.2f} 亿",
                           abs(v) * 1.2))
            if v < -1 and idx_move > 3:
                ev.append(("借涨出货", name, f"指数 {idx_move:+.1f} 点上涨中净流出 {v:+.2f} 亿",
                           abs(v) * 1.2))
        jp = int(prev_rank[j] - rank[j])
        if jp >= 20 and abs(v) > 0.8:
            ev.append(("排名跃迁", name, f"10 分钟内排名 {prev_rank[j]} → {rank[j]}", jp * 0.25))

    best = {}
    for t, n, d, s in ev:
        if n not in best or s > best[n][3]:
            best[n] = (t, n, d, s)
    out = sorted(best.values(), key=lambda x: -x[3])
    return [{"type": t, "name": n, "desc": d, "strength": round(s, 2)} for t, n, d, s in out]


# ---------------------------------------------------------------- 主线聚类
def _greedy_cluster(mat, order, V_i, corr_th, min_members, max_size, names, codes):
    """在一个候选池（已按体量排序）上跑一次龙头种子贪心聚类。

    抽出来单独用，是因为**流入池与流出池必须各跑一次**（见 cluster_themes 说明）。
    """
    sub = mat[:, order]
    sd = sub.std(axis=0, keepdims=True)
    sd[sd < 1e-9] = 1e-9
    X = (sub - sub.mean(axis=0, keepdims=True)) / sd
    C = np.nan_to_num((X.T @ X) / X.shape[0])
    n = len(order)

    used, clusters = set(), []
    for seed in range(n):
        if seed in used:
            continue
        row = C[seed]
        members = [k for k in range(n) if k not in used and row[k] >= corr_th]
        if len(members) > max_size:
            members = sorted(members, key=lambda k: -row[k])[:max_size]
        used.update(members)
        if len(members) < min_members:
            continue
        js = [int(order[k]) for k in members]
        total = float(V_i[js].sum())
        lead = max(js, key=lambda j: abs(V_i[j]))          # 体量最大者 = 这簇的龙头
        show = sorted(js, key=lambda j: -abs(V_i[j]))[:8]
        corrs = [row[k] for k in members if k != seed]
        # 簇「核心」= 与簇内其他成员平均相关性最高的题材 —— 比单纯取体量最大者更能代表这簇在讲什么
        if len(js) > 1:
            Csub = C[np.ix_(members, members)]
            centrality = (Csub.sum(axis=1) - 1) / max(1, len(members) - 1)
            core_j = int(order[members[int(np.argmax(centrality))]])
        else:
            core_j = int(order[seed])
        # 龙头集中度 = 体量最大的成员占簇合计的比重。
        #   高（>50%）= 这条线其实是「一个龙头带几个跟风」，看龙头就够了；
        #   低（<20%）= 整条线一起动，是真正的板块级行情。
        #   ★ 注意：不要用「同向度」（簇内同号成员占比）—— 候选池既然已按方向拆开，
        #     同向度恒为 100%，是个无信息量的列（v1.0.4 第一版就踩了这个坑）。
        lead_share = abs(V_i[lead]) / abs(total) if abs(total) > 1e-9 else 0.0
        clusters.append({
            "seed": names[codes[int(order[seed])]],
            "core": names[codes[core_j]],
            "seed_corr_avg": round(float(np.mean(corrs)), 2) if corrs else 1.0,
            "lead": names[codes[lead]], "lead_code": codes[lead],
            "n_members": len(js), "total_yi": round(total, 2),
            "direction": "流入" if total > 0 else "流出",
            "lead_share": round(lead_share, 2),
            "members": [{"name": names[codes[j]], "net": round(float(V_i[j]), 2)}
                        for j in show],
        })
    return clusters


def cluster_themes(F: Features, i: int, corr_th=0.50, min_members=2, topn=6,
                   concepts_only=True, max_cand=160, max_size=20,
                   out_ratio=0.34):
    """
    主线聚合（以龙头为种子的贪心聚类），**双向**：流入主线 + 流出主线。

    ★ topn 的语义（v1.1.8 起）：**每侧**的条数上限，不是两侧合计。
      topn=8 → 最多流入 8 条 + 流出 8 条。改语义的原因见文末"按方向分配"。
    ★ out_ratio：**保留但已不用于截断**（历史参数，向后兼容）。
      它原来的作用是"流出应占的配额比例"，但那会把可用的流出簇砍掉
      （topn=12 时只放 4 个流出），已改为两侧各自取满。

    为什么不用 union-find 单链聚类：单链会通过传递关系把 400 个板块串成一个大簇
    （实测出现过 123 个成员、簇内平均相关只有 0.03 的假簇）。
    改成：按当下体量从大到小取种子 → 吸纳所有与种子相关性 ≥ 阈值的板块 → 从候选中移除 → 下一个种子。
    这与选手的实际思路一致："哪些题材是跟着这个龙头一起动的"。

    ★ 双向（v1.0.4 新增），此处踩过两层坑：

      【坑 A】候选池原先按 `argsort(-abs(V))[:160]` 取。流入体量普遍远大于流出
        （实测最大流入 +1743 亿 vs 最大流出 −60 亿），160 个候选里流出板块只挤进
        14 个 → 最终 12 簇里 11 簇是流入，「谁在被资金抛弃」完全看不到。

      【坑 B】只在同一个池里把流入/流出**交错**排列是不够的。
        时序相关性（谁跟谁一起动）与资金方向（净额正负）是两件独立的事：
        实测 PCB 是全天最大流出（−60.19 亿），却因为与「国产芯片」簇的走势相关
        达 0.5 以上，被流入侧种子先一步吞并 —— 结果最大的一条流出主线消失了。
        教训：**方向必须先在候选池层面分开，再各自聚类**，不能靠排序技巧解决。

      所以这里把流入池、流出池**各跑一遍贪心**，再**两侧各自取满 topn 条**合并。
      （v1.1.8 之前是"按配额合并"，配额会把可用的流出簇砍掉，已弃用；
        out_ratio 参数仅为向后兼容保留，不再参与截断。）
    """
    mat = F.resid[max(0, i - 100):i + 1]
    if mat.shape[0] < 10:
        return []
    pool = np.arange(len(F.codes))
    if concepts_only:
        pool = pool[F.concept_mask]
    V_i = F.V[i]
    n_side = max(8, max_cand // 2)
    pos = np.array(sorted([int(j) for j in pool if V_i[j] > 0],
                          key=lambda j: -V_i[j])[:n_side], dtype=int)
    neg = np.array(sorted([int(j) for j in pool if V_i[j] < 0],
                          key=lambda j: V_i[j])[:n_side], dtype=int)
    kw = dict(corr_th=corr_th, min_members=min_members, max_size=max_size,
              names=F.names, codes=F.codes)
    c_in = _greedy_cluster(mat, pos, V_i, **kw) if len(pos) else []
    c_out = _greedy_cluster(mat, neg, V_i, **kw) if len(neg) else []
    if not c_in and not c_out:
        return []

    # ★ v1.1.8 按方向分配 —— 两侧**各自取满**，不再按比例截断
    #
    #   旧写法 `outs = c_out[:n_out]` 有个隐蔽毛病：n_out 是"流出应占几个位"
    #   的**配额**，却当成了**硬上限**。topn=12 时 n_out=round(12*0.34)=4 ——
    #   于是即使负向候选池里有 8~10 个簇可用、上游 91 个板块净额为负，
    #   也只放 4 个进来。用户看到"流出只显示 4 条"会以为当天没几个板块在
    #   流出，实际是被配额砍掉了（实测 2026-09-18：负向概念板块 91 个，
    #   聚出负簇 8 个，界面只显示 4 个）。
    #
    #   现在：`per_side` = **每侧各自**取多少条，两侧互不挤占。
    #     topn 的语义随之明确为"**每侧**的条数上限"（不再是总数）。
    #   为什么改成每侧独立：展示端要的是"红 N / 绿 N 对称"，
    #   按总数配比例永远凑不出对称（一侧体量大就必然挤掉另一侧）。
    per_side = max(1, int(topn))
    ins = c_in[:per_side]
    outs = c_out[:per_side]
    picked = ins + outs
    # 去重（同一簇可能同时出现在两侧候选里，极少见但要兜住）
    seen, uniq = set(), []
    for c in picked:
        k = c.get("seed") or c.get("core")
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    uniq.sort(key=lambda x: -abs(x["total_yi"]))
    return uniq


# ---------------------------------------------------------------- 解读
def narrate(F: Features, i: int):
    events = detect_events(F, i)
    top_in = score_table(F, i, 5, kind="concept", direction="in")
    top_out = score_table(F, i, 5, kind="concept", direction="out")
    themes = cluster_themes(F, i, topn=4)
    parts = []
    t_in = next((t for t in themes if t["total_yi"] > 0), None)
    t_out = next((t for t in themes if t["total_yi"] <= 0), None)
    if t_in:
        parts.append(f"主线【{t_in['lead']}】系 {t_in['n_members']} 个题材"
                     f"合计 {t_in['total_yi']:+.1f} 亿")
    if t_out:
        parts.append(f"流出主线【{t_out['lead']}】系 {t_out['n_members']} 个题材"
                     f"合计 {t_out['total_yi']:+.1f} 亿")
    if top_in:
        parts.append(f"最强 {top_in[0]['name']} {top_in[0]['net_yi']:+.2f} 亿")
    if top_out:
        parts.append(f"最弱 {top_out[0]['name']} {top_out[0]['net_yi']:+.2f} 亿")
    if events:
        e = events[0]
        parts.append(f"{e['type']}·{e['name']}（{e['desc']}）")
    return f"{F.times[i]}｜" + "；".join(parts)


def _time_key(hm):
    """把 'HH:MM' 转成可直接比大小的整数（分钟数）。解析不出返回 None。

    ★ 为什么不用横轴那套「分钟序号」口径（09:31=0 … 15:00=239，午休不占格）：
      那套口径是为**画图定位**设计的 —— 横轴上 11:30 与 13:01 必须相邻，
      否则午休两小时会在图上留出一大段空白。
      但**排序**要的是"真实钟点先后"，`HH*60+MM` 天然满足：
      09:31=571 < 11:30=690 < 13:01=781 < 15:00=900，跨午休依然单调。
      两套口径用途不同，混用会人为制造"谁在前"的歧义。
    """
    if not hm or not isinstance(hm, str):
        return None
    try:
        h, m = hm.strip().split(":")[:2]
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def timeline_summary(F: Features, step=3, min_strength=1.5, topn=None):
    """全天关键节点 —— **按时间先后（早→晚）排列**。

    ★★ v1.2.5：排序口径由「强度倒序」改为「时间升序」。
    【原来为什么是强度序】这个函数最初是给「收盘解说」的文字稿用的，
      只取前若干条念出来，谁最劲爆谁靠前，读起来顺。
    【为什么必须改】它后来被复用成**表格**的数据源（原生版「关键节点」页、
      HTML 报告「全天关键节点榜」）。表格里按强度排的后果是时间轴完全跳跃：

          09:42 → 09:45 → 10:15 → 09:51 → 09:48 → 11:00 → 11:03 → 10:57 …

      用户看到的就是「排列无序、杂乱」—— 同一时刻看不出先后，
      也没法顺着读出一条"全天是怎么演变的"叙事线。
      一个叫「全天 / 时间轴」的表，首要信息就是**时序**。

    【topn：把"选谁"和"怎么摆"分开，两件事都要做对】
      不传 topn → 返回全量（原生版表格要的就是全部节点，一行不落）。
      传 topn   → **先按 strength 选出最劲爆的 N 条，再按时间摆好**。
      这个参数是为了堵住一种很容易犯的错：调用方写「时间序结果的 [:24]」，
      以为是在"取前 24 条要展示的"，实际拿到的是**最早**的 24 条 ——
      尾盘整段消失，而且不报错、不缺行数，只是内容悄悄变了。
      语义显式写在参数里，就不必让每个调用方各自拼一遍排序。

    【排序键怎么定】三层，缺一层的后果都写在下面：
      ① `_time_key(time)`   解析钟点为分钟数 → 主键，真正决定早晚
      ② `idx`               面板行序 → 次级键。两个不同时点落在同一分钟时
                            （跨日面板拼接等脏数据）用它兜底，保持稳定
      ③ `-strength`         强度降序 → 三级键。本函数每个时点只保留
                            strength 最大的那一条（`e = ev[0]`），
                            正常不会有同刻多行；留着是为了将来放开
                            "每时点取多条"时仍是**全序**，
                            不让同刻顺序取决于 sort 的稳定性
      解析不出时间的行（理论不该有）排到最后，而不是被当成 0 挤到最前面。
    """
    rows = []
    for i in range(VEL_WIN, len(F.times), step):
        ev = detect_events(F, i)
        if not ev:
            continue
        e = ev[0]
        if e["strength"] < min_strength:
            continue
        rows.append({"time": F.times[i], "idx": i, **e})

    if topn is not None and topn >= 0:                  # ① 先选：按强度取前 N
        rows.sort(key=lambda r: -float(r.get("strength", 0) or 0))
        rows = rows[:topn]

    def _key(r):                                        # ② 再摆：按时间升序
        tk = _time_key(r.get("time"))
        return (0, tk, r["idx"], -float(r.get("strength", 0) or 0)) \
            if tk is not None else \
            (1, 0, r["idx"], -float(r.get("strength", 0) or 0))

    rows.sort(key=_key)
    return rows


def session_report(F: Features, as_of=None):
    T = len(F.times)
    i = T - 1 if as_of is None else (F.times.index(as_of) if as_of in F.times else T - 1)

    def ti(hm):
        return F.times.index(hm) if hm in F.times else None

    out = {"as_of": F.times[i], "segments": [], "final_in": [], "final_out": [],
           "themes": [], "industry": industry_overview(F, i)}

    def seg(a, b, label):
        if a is None or b is None or b - a < 3:
            return
        pool = []
        for k in range(a, b):
            for e in detect_events(F, k)[:2]:
                pool.append((k, e))
        pool.sort(key=lambda x: -x[1]["strength"])
        seen, picks = set(), []
        for k, e in pool:
            if e["name"] in seen:
                continue
            seen.add(e["name"])
            picks.append({"time": F.times[k], **e})
            if len(picks) >= 5:
                break
        out["segments"].append({"label": label, "picks": picks})

    seg(0, ti("11:30"), "早盘 09:30–11:30")
    seg(ti("13:00"), ti("14:30"), "午后 13:00–14:30")
    seg(ti("14:30"), i, "尾盘 14:30–15:00")

    out["final_in"] = score_table(F, i, 12, kind="concept", direction="in")
    out["final_out"] = score_table(F, i, 12, kind="concept", direction="out")
    out["themes"] = cluster_themes(F, i, topn=6)
    return out


if __name__ == "__main__":
    import sys
    tag = sys.argv[1] if len(sys.argv) > 1 else "today"
    F = build_features(tag)
    print(f"面板 {F.V.shape[0]} 时间点 × {F.V.shape[1]} 板块"
          f"（概念 {int(F.concept_mask.sum())} / 一级行业 {int((~F.concept_mask).sum())}）")
    last = len(F.times) - 1
    print("\n--- 收盘解说 ---")
    print(narrate(F, last))
    print("\n--- 全天关键节点 Top 18（按时间先后）---")
    for r in timeline_summary(F, topn=18):
        print(f"  {r['time']} [{r['type']}] {r['name']} —— {r['desc']}")
    print("\n--- 收盘主线簇 ---")
    for t in cluster_themes(F, last):
        print(f"  [{t['direction']}] 种子={t['seed']:<10s} 合计 {t['total_yi']:+8.2f} 亿 "
              f"｜{t['n_members']:>3d} 个题材 ｜seed_corr={t['seed_corr_avg']:.2f}  "
              + " / ".join(f"{m['name']}{m['net']:+.1f}" for m in t["members"][:5]))
    print("\n--- 一级行业格局 ---")
    io = industry_overview(F, last)
    print("  流入:", " / ".join(f"{r['name']}{r['net_yi']:+.1f}" for r in io["in"][:6]))
    print("  流出:", " / ".join(f"{r['name']}{r['net_yi']:+.1f}" for r in io["out"][:6]))
