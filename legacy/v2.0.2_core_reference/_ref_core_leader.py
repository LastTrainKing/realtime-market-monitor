# -*- coding: utf-8 -*-
"""
leader.py —— 板块「龙头领先反应」分析（v1.1.5 新增）

移植自 HTML 版 资金流向看板.html 的 LEADER_CFG / detectTurns / stockReaction /
scoreLeaders 一整套算法（那一版在浏览器里已经跑通并被使用）。

判定主线
--------------------------------------------------------------------
龙头不是「净额最大」，而是**在板块拐点上先动手的那只**：
  ① 从板块的分钟累计资金流里识别拐点（点火 / 跳水）
  ② 对每只候选成分股，找它在拐点窗口内「同向首次显著反应」的分钟
  ③ 领先分钟 = 板块拐点分钟 − 个股反应分钟（>0 = 个股领先）
  ④ 按「领先幅度 × 拐点强度」为主，辅以先手率/响应幅度/自身净额给龙头打分
  ⑤ 点火事件上得分最高 = 领涨龙头；跳水事件上得分最高 = 领跌龙头

数据源（东财 push2 公开接口）
--------------------------------------------------------------------
  板块成分股  /api/qt/clist/get?fs=b:BKxxxx&fields=f12,f14,f3,f62,f184,f6
  个股分钟资金 /api/qt/stock/fflow/kline/get?klt=1&secid=0.000001

★ 与 flowdata 一样只用标准库（urllib），保证 PyInstaller 打包不漏收。
★ 请求量大（每个板块 8 只候选 × 最多 N 个板块），因此：
   · 线程池并发，但每个请求前 sleep 主动限速（沿用 backfill 的纪律）
   · 单板块失败不影响整体；结果带 ok 标记，界面按「已分析 N 个板块」展示
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import flowdata

# ------------------------------------------------------------------ 参数
LEADER_CFG = {
    "enabled": True,
    # 最多分析多少个在榜板块（控制请求量）。与 HTML 版一致 36 ——
    # ★ 别调小：在榜板块的构造口径是「概念前 9 + 概念后 9 + 行业前 5 + 行业后 5 = 28」，
    #   上限若低于 28 会**静默截断**掉一半板块（曾误设 18，等于只分析前缀），
    #   第四象限就只剩零星几行，看起来像功能没生效。留 36 给未来扩榜的余量。
    "max_sectors": 36,
    "cand_per_sector": 8,   # 每个板块取多少只候选（按主力净额 + 跌幅取并集）
    "turn_k": 3.0,          # 拐点阈值 = k × MAD(板块平滑增量)
    "turn_min_yi": 0.8,     # 拐点阈值下限（亿/分）
    "merge_gap": 4,         # 相邻同类拐点合并间隔（分钟）
    "lead_win": 10,         # 向前搜索领先反应的窗口（分钟）
    "react_k": 3.0,         # 个股反应阈值 = k × MAD(个股增量)
    "react_min_yi": 0.03,   # 个股反应阈值下限（亿/分）
    "decay_win": 6,         # 领先幅度计分上限（分钟）
    "w_lead": 1.8, "w_hit": 1.0, "w_resp": 0.7, "w_size": 0.5,
    "smooth": 3,            # 板块增量平滑窗口（HTML 版固定 3）
}


# ------------------------------------------------------------------ 数学助手
def l_median(a):
    if not a:
        return 0.0
    s = sorted(a)
    m = len(s) >> 1
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def l_mad(a):
    if len(a) < 3:
        return 0.0
    m = l_median(a)
    return l_median([abs(x - m) for x in a])


def stock_secid(code):
    """6/9/5 开头 = 上交所(1.)，其余 = 深交所(0.)。与 HTML 版一致。"""
    code = str(code)
    return ("1." if code[:1] in ("6", "9", "5") else "0.") + code


# ------------------------------------------------------------------ 抓取
_rate_lock = {"last": 0.0}


def _throttle(min_gap=0.06):
    """主动限速：push2 对突发并发敏感，宁可慢一点也别被限流。"""
    now = time.time()
    gap = now - _rate_lock["last"]
    if gap < min_gap:
        time.sleep(min_gap - gap)
    _rate_lock["last"] = time.time()


def board_bk(code):
    """板块代码归一化 → 成分股接口要的纯 `BK####` 形态。

    ★ 这是实际踩到的坑（2026-09-20 E2E 发现）：
      面板里存的板块代码是 **`90.BK0596`**（市场前缀+代码，与 push2 一致），
      而 `fs=b:` 参数只认 **`BK0596`**。直接透传时东财对 `b:90.BK0596`
      不报错、只是返回 total=0 —— 于是每个板块都"没有成分股"，
      analyze_boards 全部走 ok=False 分支，**龙头区块永远是空的**，
      而且因为不抛异常，日志里也看不出问题。
      （HTML 版没这个坑：它的 code 取自 clist 的 f12，本来就是 BK 形态。）
    """
    c = str(code or "").strip()
    i = c.rfind("BK")
    return c[i:] if i >= 0 else c


def fetch_board_members(bk, n=24):
    """板块成分股：按主力净额降序取前 n 只。

    bk 可传 `90.BK0596` 或 `BK0596`（内部统一归一化）。
    返回 [{code, name, pct, net(亿), share, amount}]；失败返回 []。
    """
    _throttle()
    bk = board_bk(bk)
    url = (f"/api/qt/clist/get?pn=1&pz={n}&po=1&np=1&fltt=2&invt=2&fid=f62"
           f"&fs=b:{bk}&fields=f12,f14,f3,f62,f184,f6")
    j = flowdata._get(url)
    d = ((j or {}).get("data") or {}).get("diff") or []
    out = []
    for x in d:
        try:
            out.append({
                "code": str(x.get("f12") or ""),
                "name": str(x.get("f14") or ""),
                "pct": float(x.get("f3") or 0),
                "net": float(x.get("f62") or 0) / 1e8,
                "share": float(x.get("f184") or 0),
                "amount": float(x.get("f6") or 0),
            })
        except Exception:                                # noqa: BLE001
            continue
    return [x for x in out if x["code"]]


def fetch_stock_flow(code):
    """个股分钟主力净流入：[['HH:MM', 亿], …]；失败返回 []。"""
    _throttle()
    url = (f"/api/qt/stock/fflow/kline/get?lmt=0&klt=1&secid={stock_secid(code)}"
           f"&fields1=f1,f2,f3,f7&fields2=f51,f52&ut={flowdata.UT}")
    j = flowdata._get(url)
    kl = ((j or {}).get("data") or {}).get("klines") or []
    out = []
    for s in kl:
        p = str(s).split(",")
        if len(p) >= 2:
            try:
                out.append([p[0][-5:], float(p[1]) / 1e8])
            except ValueError:
                continue
    return out


# ------------------------------------------------------------------ 拐点识别
def detect_turns(hms, vals):
    """从板块分钟累计资金流里找拐点（点火 / 跳水），返回 [{i,dir,mag,time}]。"""
    cfg = LEADER_CFG
    n = len(vals)
    if n < 10:
        return []
    dv = [0.0]
    for i in range(1, n):
        dv.append(vals[i] - vals[i - 1])
    # 平滑（近 smooth 分钟均值）
    sm = []
    w = cfg["smooth"]
    for i in range(n):
        lo = max(0, i - (w - 1))
        seg = dv[lo:i + 1]
        sm.append(sum(seg) / len(seg))
    thr = max(cfg["turn_min_yi"], cfg["turn_k"] * l_mad(sm))
    raw = []
    for i in range(6, n):
        lo = min(sm[i - 5:i]) if i >= 6 else 0.0
        hi = max(sm[i - 5:i]) if i >= 6 else 0.0
        if sm[i] >= thr and lo <= thr * 0.2:
            raw.append({"i": i, "dir": "点火", "mag": sm[i], "end": i})
        elif sm[i] <= -thr and hi >= -thr * 0.2:
            raw.append({"i": i, "dir": "跳水", "mag": sm[i], "end": i})
    # 合并同类且间隔 <= merge_gap 的拐点
    out = []
    for t in raw:
        last = out[-1] if out else None
        if last and last["dir"] == t["dir"] and t["i"] - last["end"] <= cfg["merge_gap"]:
            last["end"] = t["i"]
            if abs(t["mag"]) > abs(last["mag"]):
                last["mag"] = t["mag"]
        else:
            out.append(dict(t))
    return [{"i": t["i"], "dir": t["dir"], "mag": t["mag"],
             "time": hms[t["i"]] if t["i"] < len(hms) else ""} for t in out]


# ------------------------------------------------------------------ 个股反应
def stock_reaction(k_hms, k_vals, turn):
    """个股在某个拐点上的反应：返回 {lead, react_at, mag} 或 None。

    lead > 0 = 个股领先（先动手）。
    """
    cfg = LEADER_CFG
    try:
        idx = k_hms.index(turn["time"])
    except ValueError:
        return None
    if idx < 0:
        return None
    kd = [0.0]
    for i in range(1, len(k_vals)):
        kd.append(k_vals[i] - k_vals[i - 1])
    kthr = max(cfg["react_min_yi"], cfg["react_k"] * l_mad(kd))
    lo = max(1, idx - cfg["lead_win"])
    hi = min(len(kd) - 1, idx + 2)
    for t in range(lo, hi + 1):
        d = kd[t]
        if turn["dir"] == "点火" and d >= kthr:
            return {"lead": idx - t, "react_at": k_hms[t], "mag": d}
        if turn["dir"] == "跳水" and d <= -kthr:
            return {"lead": idx - t, "react_at": k_hms[t], "mag": d}
    return None


# ------------------------------------------------------------------ 打分
def score_leaders(turns, stocks):
    """对每只候选股在两个方向上打分，返回 (up_list, down_list)。

    stocks: [{name, flow:[[hm, 亿],…], net}]
    """
    cfg = LEADER_CFG
    res = {"点火": [], "跳水": []}
    for direction in ("点火", "跳水"):
        evs = [t for t in turns if t["dir"] == direction]
        if not evs:
            continue
        cands = []
        for s in stocks:
            flow = s.get("flow") or []
            if len(flow) < 10:
                continue
            kh = [x[0] for x in flow]
            kv = [x[1] for x in flow]
            lead_sum = hits = resp_sum = miss = 0
            detail = []
            for ev in evs:
                r = stock_reaction(kh, kv, ev)
                if not r:
                    miss += 1
                    continue
                lead = max(0, min(r["lead"], cfg["decay_win"]))
                w = min(1.5, abs(ev["mag"]) / cfg["turn_min_yi"] / 3.0)
                lead_sum += lead * w
                resp_sum += abs(r["mag"]) * w
                hits += 1
                detail.append({"t": ev["time"], "dir": ev["dir"],
                               "lead": r["lead"], "at": r["react_at"]})
            n_ev = len(evs)
            hit_rate = hits / n_ev if n_ev else 0.0
            score = (cfg["w_lead"] * lead_sum
                     + cfg["w_hit"] * hit_rate * 2
                     + cfg["w_resp"] * resp_sum * 4
                     + cfg["w_size"] * abs(s.get("net", 0)))
            if hits:
                cands.append({
                    "code": s.get("code", ""), "name": s.get("name", ""),
                    "score": round(score, 3), "hits": hits, "n": n_ev,
                    "avg_lead": round(lead_sum / max(1, hits)
                                      / max(0.01, cfg["w_lead"]), 2),
                    "detail": detail[:6], "net": s.get("net", 0),
                })
        cands.sort(key=lambda x: -x["score"])
        res[direction] = cands[:3]
    return res["点火"], res["跳水"]


# ------------------------------------------------------------------ 对外入口
def analyze_boards(panel_times, panel_codes, panel_names, V, board_rows,
                   max_sectors=None, on_progress=None):
    """对在榜板块做一轮龙头分析。

    panel_* : 本地面板的分钟轴与板块清单
    V       : 累计净额矩阵（分钟 × 板块，单位亿）
    board_rows: [{'code','name','net_yi',…}] —— 在榜板块（流入+流出）
    返回 [{code, name, net, turns, up, down, ok}, …]
    """
    cfg = LEADER_CFG
    if not cfg["enabled"] or not board_rows:
        return []
    max_n = max_sectors or cfg["max_sectors"]
    # 每个板块的板块级分钟曲线（用于拐点识别）
    idx_of = {c: j for j, c in enumerate(panel_codes)}
    tasks = []
    for r in board_rows[:max_n]:
        code = str(r.get("code") or "")
        j = idx_of.get(code)
        if j is None:
            continue
        vals = [float(x) for x in V[:, j]]
        turns = detect_turns(list(panel_times), vals)
        if not turns:
            continue
        tasks.append({"code": code, "name": r.get("name", ""),
                      "net": float(r.get("net_yi", 0) or 0), "turns": turns})

    total = len(tasks)
    if not total:
        return []
    results = []
    done = {"n": 0}

    def _one(t):
        """单个板块：抓成分股 → 抓个股分钟流 → 打分。"""
        try:
            members = fetch_board_members(t["code"], cfg["cand_per_sector"] * 3)
        except Exception:                                # noqa: BLE001
            members = []
        if not members:
            return {**t, "ok": False, "up": [], "down": []}
        # 候选 = 净额前 N ∪ 跌幅前 N（领跌龙头往往不在净额榜前列）
        cand = members[:cfg["cand_per_sector"]]
        weak = sorted(members, key=lambda x: x.get("pct", 0))[:cfg["cand_per_sector"]]
        seen, picks = set(), []
        for x in cand + weak:
            if x["code"] not in seen:
                seen.add(x["code"])
                picks.append(x)
        picks = picks[:cfg["cand_per_sector"] * 2]

        def _flow(x):
            try:
                return {**x, "flow": fetch_stock_flow(x["code"])}
            except Exception:                            # noqa: BLE001
                return {**x, "flow": []}

        with ThreadPoolExecutor(max_workers=4) as ex:
            stocks = list(ex.map(_flow, picks))
        up, down = score_leaders(t["turns"], stocks)
        done["n"] += 1
        if on_progress:
            try:
                on_progress(f"龙头分析 {done['n']}/{total}：{t['name']}")
            except Exception:                            # noqa: BLE001
                pass
        return {**t, "ok": bool(up or down), "up": up, "down": down}

    # 板块之间串行（每个板块内部已并发），把总并发控制在可控范围
    for t in tasks:
        results.append(_one(t))
        _throttle(0.1)
    return results


# ------------------------------------------------------------------ 结果缓存
CACHE_NAME = "leader.json"


def _cache_path(data_dir):
    return os.path.join(data_dir or flowdata.DATA, CACHE_NAME)


def load_cache(data_dir=None):
    """读取龙头缓存：{date_tag: {code: record}}；缺失/损坏返回 {}。"""
    try:
        with open(_cache_path(data_dir), encoding="utf-8") as f:
            j = json.load(f)
        return j if isinstance(j, dict) else {}
    except Exception:                                    # noqa: BLE001
        return {}


def save_cache(tag, rows, data_dir=None):
    """把一轮结果并入缓存。

    ★ 为什么需要它（v1.1.5）：
      一轮 28 个板块 ≈ 250 个请求、实测 42 秒。盘中每 5 分钟重跑一次还算合理，
      但**程序重启后又从零开始**——用户切个页面、重开一次程序，
      第四象限就要重新空等 42 秒。
      按「交易日 + 板块代码」缓存后，同一交易日内的重开/重算都能秒出，
      只有新出现的板块才需要联网（增量）。
      `--shot` 自检也因此能拍到一个**有内容**的第四象限（读缓存、不联网），
      否则交付截图里那一格永远是"等待分析…"。
    """
    if not tag or not rows:
        return 0
    p = _cache_path(data_dir)
    try:
        allc = load_cache(data_dir)
        day = allc.get(tag) or {}
        n = 0
        for r in rows:
            c = r.get("code")
            if not c:
                continue
            rec = dict(r)
            # flow/detail 里的中间量不入盘（体积大且用不到），只留展示所需
            rec.pop("stocks", None)
            day[c] = rec
            n += 1
        allc[tag] = day
        # 只保留最近 5 个交易日，避免文件无限膨胀
        for k in sorted(allc.keys())[:-5]:
            allc.pop(k, None)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(allc, f, ensure_ascii=False)
        os.replace(tmp, p)
        return n
    except Exception:                                    # noqa: BLE001
        return 0


def from_cache(tag, boards, data_dir=None):
    """按板块清单从缓存取结果；缺任何一个板块都返回 (rows, missing)。

    返回 (已命中的结果列表, 尚缺的板块列表) —— 调用方可只对缺失部分联网。
    """
    day = load_cache(data_dir).get(tag) or {}
    hit, miss = [], []
    for b in boards or []:
        c = b.get("code")
        rec = day.get(c)
        if rec:
            hit.append(rec)
        else:
            miss.append(b)
    return hit, miss
