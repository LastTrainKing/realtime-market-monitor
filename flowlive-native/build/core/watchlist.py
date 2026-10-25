# -*- coding: utf-8 -*-
"""
watchlist.py —— 板块概念「实时榜单」累积池（v1.1.7 新增）

用户需求原文：
    「开盘时按辨识度（资金强度、涨幅、活跃度等）筛选约 15 个板块/概念进入榜单；
      盘中一旦检测到盘口异动（明显拉升或跳水）的板块/概念，立即追加进榜单，
      总数可超过 15 个；
      已入选条目全天保留，遵循「只增不减」原则，任何情况不得移除或替换。」
    「一开始的不要删，可以做多但不要少。」

所以本模块只做一件看起来很简单、但很容易做错的事：**维护一个只增不减的池子**。

—— 三条设计纪律（每条都对应一种"看着对、实际会违反用户要求"的写法）——

【纪律 1】不许用「当前榜单」覆盖累积池。
    最自然的写法是 `self.rows = score_table(...)` —— 每 5 分钟刷新一次。
    但它隐含了「新榜单是权威」的假设：上一轮在榜、这一轮掉出辨识度前 15 的
    板块会被顺手抹掉，正好踩中用户说的"不要删"。累积池必须是**并集**语义。

【纪律 2】不许让「入选理由」被后来的刷新冲掉。
    一个板块开盘因辨识度入选、10:20 又因"加速流入"触发异动 —— 此时若把
    reason 改写成"盘口异动"，用户就再也看不到它当初是靠辨识度进来的。
    因此 reason 采用**首因保留 + 追加**：第一条理由定基调，后续只往后面
    append（用 `＋` 连接），既保住"为什么一开始在这里"，也不丢新信号。

【纪律 3】跨日必须整体重置，靠 date_tag 而不是靠"进程重启"。
    池子按交易日隔离：换日（回看昨天 / 隔夜启动）时整池作废重建。
    绝不能沿用 —— 否则今天开盘会带着昨天的榜单，那已经不是"当日辨识度"了。
    实现上以 `date_tag` 为准，与 `_hover_cache` 同一套作废口径。

—— 存储 ——
    data/watchlist_YYYYMMDD.json
    {"date_tag": "...", "updated": "HH:MM", "rounds": 3,
     "rows": [{"code","name","kind","reason","first_time","first_min",
               "last_time","net_yi","salience","strength"} ...]}

    落盘而不是只放内存的原因：盘中程序崩溃 / 用户手动重启后，榜单要能接着长，
    而不是从 0 重新攒一遍（那半小时里异动过的板块就永久丢了）。
    ★ 写入必须是**原子**的（tmp + os.replace）：崩在写一半时会留下半截 JSON，
      下次读取解析失败 → 整池清零，比不落盘还糟。
"""
from __future__ import annotations

import json
import os

import flowdata

# 开盘入榜的目标条数（用户要求「约 15 个」）。
#   含义是"每个方向各 15 条"：只选 15 条的话，20 条里若恰好都是流入，
#   流出侧一个都看不见 —— 而"谁在被资金抛弃"正是要盯的另一半。
DEFAULT_TOP_N = 15
# 入榜门槛：辨识度低于此值不进（避免开盘第一分钟全员 0 分时把池子灌满噪声）。
#   0.12 是经验值 —— 全市场分位口径下，这意味着该板块至少在某一个维度上
#   进了前 1/5，属于"勉强能看出点意思"，不是纯噪声。
SALIENCE_FLOOR = 0.12
# 盘口异动的强度门槛（detect_events 的 strength）。
#   0.8 对应"净额 0.5~1 亿级别的小动作"以下，再低就是每分钟都在响的噪声。
EVENT_FLOOR = 0.8
# 容量保险丝。正常一天 4 小时 ≈ 48 轮刷新，每轮最多追加几条，
#   理论上百来条封顶；给 200 是防"数据源抽风导致每轮都判异动"把文件撑爆。
MAX_ROWS = 200

# ★★ v2.0.1：异动雷达每侧每轮最多追加多少条。
#   为什么必须限流（而不是把雷达结果全灌进来）：
#     雷达是**高召回**口径（native_app 里每侧展示 24 条），每 30 秒一轮。
#     若每轮把两个方向的 48 条全追加，几轮下来池子就被
#     "雷达捞到的边角板块"填满 —— 用户要的"主角 + 突发的"反而被淹没，
#     而且 MAX_ROWS 保险丝会开始淘汰真正的主角（淘汰顺序按强度，
#     雷达条目的强度乘以 10 后仍在中等偏下）。
#   取 6：既保留"盘中及时纳入新异动"的能力（一天 48 轮 × 12 条
#   远超实际需要的量），又不牺牲池子的信噪比。
RADAR_ADD_PER_SIDE = 6


def path_of(tag, data_dir=None):
    d = data_dir or flowdata.DATA
    return os.path.join(d, f"watchlist_{tag}.json")


def load(tag, data_dir=None):
    """读取该交易日的累积池；不存在 / 损坏 → 返回 None（调用方按空池处理）。"""
    try:
        with open(path_of(tag, data_dir), encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:                                    # noqa: BLE001
        return None
    if not isinstance(obj, dict):
        return None
    rows = obj.get("rows")
    if not isinstance(rows, list):
        return None
    # 同一天的数据：date_tag 不符说明文件被串了，宁可不认
    if obj.get("date_tag") and obj.get("date_tag") != tag:
        return None
    obj["rows"] = [r for r in rows if isinstance(r, dict) and r.get("code")]
    return obj


def save(tag, rows, data_dir=None, updated="", rounds=0):
    """原子落盘。失败只返回 False，绝不抛 —— 存不下不该让整个刷新流程崩。"""
    d = data_dir or flowdata.DATA
    fp = path_of(tag, d)
    tmp = fp + ".tmp"
    payload = {
        "date_tag": tag,
        "updated": updated,
        "rounds": int(rounds),
        "n": len(rows),
        "rows": rows,
    }
    try:
        os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, fp)                              # ★ 原子替换
        return True
    except Exception:                                    # noqa: BLE001
        try:
            os.remove(tmp)
        except Exception:                                # noqa: BLE001
            pass
        return False


def purge_beyond_window(keep_tags, data_dir=None):
    """删除回看窗口外的 watchlist_*.json（与面板同口径，防无限累积）。

    只删文件名里的日期 tag **不在** keep_tags 里的文件；
    非日期名（watchlist_today 之类）一律不动 —— 与 store.purge_beyond_window
    同一条纪律，避免误删测试产物。
    """
    d = data_dir or flowdata.DATA
    keep = set(keep_tags)
    gone = []
    try:
        files = os.listdir(d)
    except Exception:                                    # noqa: BLE001
        return []
    for f in files:
        if not (f.startswith("watchlist_") and f.endswith(".json")):
            continue
        tag = f[len("watchlist_"):-len(".json")]
        if len(tag) != 8 or not tag.isdigit():
            continue
        if tag in keep:
            continue
        try:
            os.remove(os.path.join(d, f))
            gone.append(tag)
        except Exception:                                # noqa: BLE001
            pass
    return sorted(gone)


# ------------------------------------------------------------------ 池子本体
class Pool:
    """一个交易日的累积榜池。只增不减，接口全部返回"当前全量"。"""

    def __init__(self, tag, data_dir=None):
        self.tag = tag
        self.data_dir = data_dir
        self.rows = []                 # 有序：入选顺序（先来在前）
        self._by_code = {}             # code -> row（同一对象的引用）
        self.rounds = 0
        self.updated = ""
        self.new_codes = []            # 本轮新增的 code（供 UI 高亮"新进"）
        obj = load(tag, self.data_dir)
        if obj:
            self.rows = obj["rows"]
            self.rounds = int(obj.get("rounds") or 0)
            self.updated = obj.get("updated") or ""
            self._reindex()

    def _reindex(self):
        self._by_code = {}
        for r in self.rows:
            self._by_code[r["code"]] = r

    # -------------------------------------------------- 内部：单条追加
    def _add(self, code, name, kind, reason, hm, strengthen_row=None,
             is_seed=False):
        """把一条板块记入池子。

        返回 (是否新增, 该条 row)。
        ★ 已存在时**不覆盖** reason / first_time —— 见模块头「纪律 2」。
          只更新 last_time 与数值快照（数值是"当前状态"，本来就该动）。

        is_seed=True 表示这是**开盘按辨识度选出的首批条目**。
        ★ v1.2.1：加这个标记是为了修一个真实缺陷 —— 容量保险丝（见 update）
          原先按 `strength` 排序淘汰，而**开盘入榜的条目根本没有 strength**
          （seed 传进来的 score_table 行只有 net_yi/salience/…，
            没有 strength 键 → row["strength"] 恒为 0），
          于是它们排序永远垫底、**最先被删掉**，恰好与用户要的
          「一开始的 15 个 + 后面突发的，只增不减」相反。
          现在首批条目带 is_seed，保险丝把它们排在最后淘汰。
        """
        row = self._by_code.get(code)
        if row is None:
            row = {
                "code": code, "name": name, "kind": kind,
                "reason": reason,
                "first_time": hm, "first_min": None,
                "last_time": hm,
                "net_yi": 0.0, "salience": 0.0, "strength": 0.0,
                "is_new": True,          # 本轮新增（UI 标"新进"，下轮清）
                "is_seed": bool(is_seed),
            }
            self.rows.append(row)
            self._by_code[code] = row
            self.new_codes.append(code)
            ok = True
        else:
            if is_seed:
                row["is_seed"] = True          # 已被盘中追加过的，补上标记
            # 理由追加（去重：同一个理由标签不重复堆）
            if reason and reason not in str(row.get("reason", "")):
                row["reason"] = f'{row.get("reason", "")}＋{reason}'
            row["last_time"] = hm
            ok = False
        if strengthen_row:
            for k in ("net_yi", "salience", "strength"):
                if k in strengthen_row:
                    try:
                        row[k] = float(strengthen_row[k])
                    except Exception:                    # noqa: BLE001
                        pass
            if strengthen_row.get("name"):
                row["name"] = strengthen_row["name"]
        return ok, row

    # -------------------------------------------------- 对外：开盘入榜
    def seed(self, F, i, top_n=DEFAULT_TOP_N, salience_mod=None, kind="concept"):
        """开盘（或首次刷新）按辨识度双向各取 top_n 入榜。

        ★ 为什么排序键是 **salience 而不是净额**：
          用户点名要的是「按辨识度（资金强度、涨幅、活跃度等）」——
          净额只反映"钱有多少"这一个维度，开盘头几分钟大权重行业天然领先，
          榜单会退化成"体量榜"。salience 是五维加权（体量/速度/跃迁/
          持续/异动 z），正是为"此刻谁是主角"设计的。
        ★ 但门槛不能只卡 salience：开盘第一分钟全场净额都是 0，
          _pct_rank 在并列值上给的都是平均秩 → 一大批板块拿到同一个中等分，
          此时 salience 区分度极低（全挤在一起）。所以**叠加一条绝对量门槛**：
          净额绝对值或 5 分钟增量必须至少有一个不像噪声，才允许进场。
        """
        sal = salience_mod
        if sal is None:
            import salience as sal
        hm = F.times[i] if 0 <= i < len(F.times) else ""
        added = 0
        for direction in ("in", "out"):
            rows = sal.score_table(F, i, topn=max(top_n * 3, 45),
                                   kind=kind, direction=direction)
            picked = 0
            for r in rows:
                if picked >= top_n:
                    break
                code = r.get("code")
                if not code:
                    continue
                s = float(r.get("salience", 0) or 0)
                net = float(r.get("net_yi", 0) or 0)
                d5 = float(r.get("d5_yi", 0) or 0)
                # 双门槛：辨识度分位 或 绝对量显著，二者取一
                if s < SALIENCE_FLOOR and abs(net) < 0.5 and abs(d5) < 0.3:
                    continue
                tag_cn = "辨识度·" + ("流入" if direction == "in" else "流出")
                ok, _ = self._add(code, r.get("name", ""), r.get("kind", ""),
                                  tag_cn, hm, r, is_seed=True)
                if ok:
                    added += 1
                picked += 1
        return added

    # -------------------------------------------------- 对外：盘中追加异动
    def grow(self, F, i, events=None, salience_mod=None, kind="concept"):
        """检测盘口异动，把**未在池中**的板块追加进来（已在池中的只更新数值）。

        异动来源两路，取并集 —— 任一路命中就追加：
          ① `salience.detect_events(F, i)`：语义化事件（新晋 / 加速流入 /
             加速流出 / 资金回流 / 资金撤离 / 逆向吸筹 / 借涨出货 / 排名跃迁），
             strength 有明确量纲，作为主判据；
          ② **z 值突异兜底**：`|z| ≥ z_gate`（近 5 分钟增量相对自身波动率）。
             单靠 ① 会漏掉一类情形：板块全天横盘、某一分钟突然一根量，
             此时 v/d 的绝对值很小（`abs(v)<0.8 and abs(d)<0.5` 直接被
             detect_events 滤掉），但 z 值会飙到 3~5 —— 这正是"盘口异动"
             最典型的样子（无明显趋势的突然放量）。
        """
        sal = salience_mod
        if sal is None:
            import salience as sal
        hm = F.times[i] if 0 <= i < len(F.times) else ""
        if events is None:
            try:
                events = sal.detect_events(F, i, concepts_only=(kind == "concept"))
            except Exception:                            # noqa: BLE001
                events = []
        name_of = {F.names.get(c, c): c for c in F.codes}
        added = 0
        for e in events or []:
            try:
                st = float(e.get("strength", 0) or 0)
            except Exception:                            # noqa: BLE001
                st = 0.0
            if st < EVENT_FLOOR:
                continue
            nm = e.get("name") or ""
            code = name_of.get(nm)
            if not code:
                continue                                     # 名字对不上就跳过
            j = None
            try:
                j = F.codes.index(code)
            except ValueError:
                j = None
            snap = None
            if j is not None:
                snap = {"name": nm,
                        "net_yi": round(float(F.V[i, j]), 2),
                        "salience": round(float(F.salience[i, j]), 3),
                        "strength": round(st, 2)}
            ok, _ = self._add(code, nm, F.kinds.get(code, ""),
                              "盘口异动·" + str(e.get("type", "")), hm, snap)
            if ok:
                added += 1

        # ② z 值突异兜底
        try:
            import numpy as _np
            zrow = _np.asarray(F.z[i], dtype=float)
            pool = _np.arange(len(F.codes))
            if kind == "concept":
                pool = pool[_np.asarray(F.concept_mask, dtype=bool)]
            if pool.size:
                sub = _np.nan_to_num(zrow[pool])
                # ★★ v2.0.1：先按门槛**全量筛**，再按 |z| 取前 40。
                #   原实现是 `np.argsort(-|z|)[:25]` —— 只看前 25 名，
                #   一个板块只要 z 排在第 26 名就永远不会被兜底路径捞到，
                #   哪怕它的 |z| 远高于 2.5 的门槛。这与 detect_events
                #   原先"只看前 80"是同一类漏检，只是量级小些。
                #   先筛后截，门槛才是真正生效的那个条件。
                hitp = _np.where(_np.abs(sub) >= 2.5)[0]
                if hitp.size:
                    hitp = hitp[_np.argsort(-_np.abs(sub[hitp]))][:40]
                for k in hitp:
                    j = int(pool[k])
                    zz = float(zrow[j])
                    code = F.codes[j]
                    if code in self._by_code:
                        continue
                    d5 = float(F.dV5[i, j])
                    if abs(d5) < 0.3:                    # 增量太小，z 高也是噪声
                        continue
                    snap = {"name": F.names.get(code, code),
                            "net_yi": round(float(F.V[i, j]), 2),
                            "salience": round(float(F.salience[i, j]), 3),
                            "strength": round(abs(zz), 2)}
                    ok, _ = self._add(code, snap["name"], F.kinds.get(code, ""),
                                      f"盘口异动·z={zz:+.1f}", hm, snap)
                    if ok:
                        added += 1
        except Exception:                                # noqa: BLE001
            pass

        # ③ 异动雷达（★★ v2.0.1 新增，问题十的落地）
        #
        #   为什么在①（语义事件）②（z 兜底）之外还要加这一路：
        #     ①② 都是从"单点突异"出发的 —— 要么命中一条事件规则，
        #     要么某一分钟 z 特别大。它们抓不到这样一类板块：
        #     **单看哪一项都不极端，合起来却明显异常**。
        #     典型例子正是用户点名的"科技板块尾盘回流"：
        #       · 尾盘每分钟的增量平缓 → 单分钟 z 不高 → ②不命中；
        #       · 累计体量不大 → 事件规则里的绝对金额门槛够不着 → ①不命中；
        #     可它全天最值得关注的就是"钱在持续回到这条线上"。
        #     雷达是四因子合成口径（z + 速度 + 排名跃迁 + 尾盘回流），
        #     恰好把这种"复合形态"捞得起来 —— 这也是"能筛出类似清单
        #     但不套用名单"的实现方式：没有任何板块名，全靠当日实时分位。
        try:
            n_radar = 0
            for _side in ("in", "out"):
                for r in (sal.anomaly_radar(F, i, topn=RADAR_ADD_PER_SIDE,
                                             direction=_side) or []):
                    code = r.get("code")
                    if not code or code in self._by_code:
                        continue
                    snap = {"name": r.get("name", ""),
                            "net_yi": round(float(r.get("net_yi", 0) or 0), 2),
                            "salience": round(float(r.get("salience", 0) or 0), 3),
                            # 雷达分是 0~1 的分位合成分，乘 10 让它与
                            # EVENT_FLOOR(0.8) 这类事件强度落在同一量级 ——
                            # 否则容量保险丝排序时会一律垫底、优先被淘汰。
                            "strength": round(float(r.get("anomaly", 0) or 0) * 10, 2)}
                    ok, _ = self._add(code, snap["name"], r.get("kind", ""),
                                      "异动雷达·" + str(r.get("why", "")), hm, snap)
                    if ok:
                        added += 1
                        n_radar += 1
            if n_radar:
                pass                                     # 计入 added，供调用方统计
        except Exception:                                # noqa: BLE001
            pass
        return added

    # -------------------------------------------------- 对外：一轮刷新
    def update(self, F, i, salience_mod=None, top_n=DEFAULT_TOP_N, kind="concept"):
        """一轮完整刷新：空池 → seed（开盘入榜）；非空 → grow（异动追加）。

        返回 (新增条数, 全量条数)。
        ★ 刻意做成"空池才 seed"：seed 是**排序取前 N**，每轮都跑会把
          "这一轮辨识度前 15"的概念反复注入 —— 语义没错但没必要，
          反而让 reason 里堆满重复标签。开盘入榜只做一次，之后靠异动生长，
          这才是用户描述的「一开始的 15 个 + 后面突发的」。
        """
        sal = salience_mod
        if sal is None:
            import salience as sal
        self.new_codes = []
        if not self.rows:
            n = self.seed(F, i, top_n=top_n, salience_mod=sal, kind=kind)
        else:
            n = self.grow(F, i, salience_mod=sal, kind=kind)
        # 容量保险丝：超出上限时按"最后更新时间 + 强度"淘汰最没存在感的。
        #   ★ 只在极端情况下触发（数据源抽风每轮判异动），正常一天到不了。
        #   ★★ v1.2.1 修复：排序键**第一位是 is_seed** ——
        #     开盘按辨识度选出的首批条目必须活到最后，因为
        #     ① 用户的需求原话是「一开始的 15 个 + 后面突发的，只增不减」；
        #     ② 首批条目身上没有 strength（score_table 不返回该键），
        #        原先按 strength 排 → 它们恒为 0 → **最先被删**，正好反了。
        #     所以顺序是：非 seed 先淘汰（按 strength 升序、再按时间），
        #     最后才轮到 seed 条目。
        if len(self.rows) > MAX_ROWS:
            self.rows.sort(key=lambda r: (
                0 if r.get("is_seed") else 1,          # seed 条目最后被淘汰
                -(float(r.get("strength", 0) or 0)),
                str(r.get("last_time", ""))))
            self.rows = self.rows[:MAX_ROWS]
            self._reindex()
        self.rounds += 1
        self.updated = F.times[i] if 0 <= i < len(F.times) else ""
        return n, len(self.rows)

    # -------------------------------------------------- 对外：交给 UI 的视图
    def view(self, i=None):
        """返回可直接喂给表格的列表。按方向分组、组内按净额绝对值降序。

        ★ 为什么不按"入选时间"排：用户要的是"清晰看到异动的板块和概念"，
          整齐的分组排序比一条时间流水更容易扫读；
          而"谁是新进的"由 `is_new` 标记 + reason 里的 `盘口异动·` 前缀表达，
          信息不丢。
        """
        ins = [r for r in self.rows if float(r.get("net_yi", 0) or 0) >= 0]
        outs = [r for r in self.rows if float(r.get("net_yi", 0) or 0) < 0]
        ins.sort(key=lambda r: -abs(float(r.get("net_yi", 0) or 0)))
        outs.sort(key=lambda r: -abs(float(r.get("net_yi", 0) or 0)))
        ordered = ins + outs
        out = []
        for k, r in enumerate(ordered, 1):
            out.append({
                "rank": k,
                "code": r.get("code", ""),
                "name": r.get("name", ""),
                "kind": r.get("kind", ""),
                "net_yi": float(r.get("net_yi", 0) or 0),
                "salience": float(r.get("salience", 0) or 0),
                "reason": r.get("reason", ""),
                "first_time": r.get("first_time", ""),
                "last_time": r.get("last_time", ""),
                "is_new": bool(r.get("is_new")),
            })
        return out

    def stats(self):
        """(总条数, 流入数, 流出数, 异动追加数)"""
        n_in = sum(1 for r in self.rows if float(r.get("net_yi", 0) or 0) >= 0)
        n_ev = sum(1 for r in self.rows if "盘口异动" in str(r.get("reason", "")))
        return len(self.rows), n_in, len(self.rows) - n_in, n_ev

    # -------------------------------------------------- 对外：收尾
    def flush(self):
        """清掉"本轮新增"标记并落盘。下一轮开始时这些条就不再高亮。"""
        for r in self.rows:
            r["is_new"] = False
        return save(self.tag, self.rows, self.data_dir,
                    updated=self.updated, rounds=self.rounds)

    def clear_new_flags(self):
        for r in self.rows:
            r["is_new"] = False


if __name__ == "__main__":
    print("watchlist.py —— 累积池模块，无独立入口。")
    print("  DEFAULT_TOP_N =", DEFAULT_TOP_N)
    print("  SALIENCE_FLOOR =", SALIENCE_FLOOR)
    print("  EVENT_FLOOR =", EVENT_FLOOR)
