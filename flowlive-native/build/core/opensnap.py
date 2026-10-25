# -*- coding: utf-8 -*-
"""opensnap.py —— 开盘检测快照（v2.0.4 新增，任务一/二的数据层）

在开盘后 5 / 10 / 15 分钟三个节点（09:36 / 09:41 / 09:46 触发，
取该时刻已收盘的最近一分钟），对全市场做一次**全量快照**：

  · 板块（行业 m:90+t:2 + 概念 m:90+t:3）
      f5  成交量（手，开盘以来累计）
      f6  成交额（元，开盘以来累计）
      f62 主力净流入（元，开盘以来累计）
  · 个股（沪深京全 A）
      f5 成交量 / f6 成交额 / f10 量比 / f62 主力净流入 / f3 涨跌幅

★ 为什么必须"定点快照"而不能事后补算：
    clist 接口返回的是**当时**的全市场状态（累计值）。
    板块主力净流入虽可用分钟面板事后累加，但成交量面板里没有；
    个股的历史分钟数据更不可能逐只回取。
    所以：程序在节点运行 → 有数据；没在节点运行 → 诚实标注缺失，
    绝不用收盘数据冒充 09:36 的数据。

落盘 `opensnap_{tag}.json`（与 panel_/watchlist_ 同目录、同一套命名习惯），
UI 两页（开盘检测 / 个股异动）都从 build_payload() 取数。
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import flowdata

# ---------------------------------------------------------------- 节点定义
# (分钟数, 触发时刻 HH:MM, 展示标签, 存储键)
SNAP_NODES = (
    (5,  "09:36", "开盘后5分钟",  "m5"),
    (10, "09:41", "开盘后10分钟", "m10"),
    (15, "09:46", "开盘后15分钟", "m15"),
)
NODE_KEYS = tuple(k for _, _, _, k in SNAP_NODES)
NODE_LABEL = {k: lab for _, _, lab, k in SNAP_NODES}

_FS_SECTOR_INDU = "m:90+t:2"
_FS_SECTOR_CONC = "m:90+t:3"
# 沪深主板 + 创业板 + 科创板 + 北交所（东财全 A 通用 fs，与行情列表一致）
_FS_STOCKS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

_PAGE = 100                    # clist 单页硬上限（实测 pz>100 也只回 100）
#: 页间轻微节流（秒）。★ 见 _fetch_list 的说明：东财限流按"单位时间请求总量"
#: 触发，瞬时齐发 70 个请求必然踩线。摊开一点点就能大幅提升成功率。
_PAGE_GAP = 0.03
_LOCK = threading.Lock()


def snap_path(tag, data_dir=None):
    d = data_dir or flowdata.DATA
    return os.path.join(d, "opensnap_%s.json" % tag)


def load_snap(tag, data_dir=None):
    """读取当天快照；无文件返回 {"nodes": {}}。损坏按无数据处理。"""
    try:
        with open(snap_path(tag, data_dir), encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict) and isinstance(j.get("nodes"), dict):
            return j
    except Exception:                                    # noqa: BLE001
        pass
    return {"nodes": {}}


def save_snap(tag, nodes, data_dir=None):
    """nodes: {node_key: node_dict}，整体覆盖写（先写临时文件再替换，防半截）。"""
    d = data_dir or flowdata.DATA
    os.makedirs(d, exist_ok=True)
    p = snap_path(tag, d)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"nodes": nodes}, f, ensure_ascii=False)
    os.replace(tmp, p)


# ---------------------------------------------------------------- 抓取
def _fetch_list(fs, fields, workers=3, page_tries=6):
    """分页抓完一个 clist 列表（pz=100 硬上限，并发翻页）。

    ★★ v3.0.4 两处改进：**首页重试 + 失败页补抓**、**并发降到 3**。

      【实测事实】（2026-09-23 盘中）
        · `capture_node` 采回的 node 里 `sectors`/`stocks` 曾经是**空列表**，
          而 `build_payload` 只判"节点是否存在"→ 界面拿到"存在但为空"的节点。
        · 换到 13 秒后再采一次，**又**是空的，且日志出现
          `[net] keep-alive 通道探测失败`。
        · 而单独、低频地重抓同一批板块，**100% 成功**。

      【根因】东财的限流是按**单位时间的请求总量**触发的，不是按单个请求。
        一次 `capture_node` 的峰值并发高达 3 个列表 × 6 并发翻页 = **18**，
        ~70 个请求几乎同时发出 —— 必然踩线。
        而失败后的行为是：首页失败 → total=0 → **直接返回空列表**，
        调用方完全无法区分"这个列表本来就没数据"和"请求被限流了"。

      【修法】
        ① 并发 6 → **3**（峰值 3 列表 × 3 = 9），页间不再瞬时齐发；
        ② 首页最多重试 `page_tries` 次（退避 0.3/0.6/0.9s）——
           首页决定总页数，它失败等于整个列表报废，值得多试；
        ③ 并发抓完后，对**失败的页**串行补抓（最多 3 次，带退避）。
        ④ 页内轻微节流（`_PAGE_GAP`），把请求摊开而不是砸下去。
    """
    url0 = ("https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=%d"
            "&po=1&np=1&fltt=2&invt=2&fid=f62&fs=%s&fields=%s&ut=%s"
            % (_PAGE, fs, fields, flowdata.UT))

    def _page(pn):
        time.sleep(_PAGE_GAP)                        # ④ 轻度节流，摊开请求
        j = flowdata._get(url0.format(pn=pn), tries=2, timeout=10)
        d = (j or {}).get("data") or {}
        return d.get("total") or 0, d.get("diff") or []

    # ---- 首页：决定"总共多少页"，失败必须重试 ----
    #   ★ 实测板块列表（m:90+t:2 / m:90+t:3）比个股列表更容易被限流，
    #     所以重试次数从 4 提到 6、退避 0.5~3.0s（累计约 10s）。
    #     板块列表只有 10 页，值得为首页多等一会儿 ——
    #     它失败就意味着"板块榜"整块没有数据。
    total, first = 0, []
    for k in range(page_tries):
        total, first = _page(1)
        if first:
            break
        time.sleep(0.5 * (k + 1))
    if not first:
        return []                                    # 首页彻底失败：如实返回空

    rows = list(first)
    if total <= _PAGE:
        return rows
    pages = min((total + _PAGE - 1) // _PAGE, 80)    # 80 页兜底，防异常 total

    # ---- 后续页：并发抓，记录失败的页 ----
    failed_pages = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for pn, diff in ex.map(lambda p: (p, _page(p)[1]),
                               range(2, pages + 1)):
            if diff:
                rows.extend(diff)
            else:
                failed_pages.append(pn)

    # ---- 失败页补抓（串行 + 退避：并发正是触发限流的原因）----
    for pn in failed_pages:
        for k in range(3):
            _t, diff = _page(pn)
            if diff:
                rows.extend(diff)
                break
            time.sleep(0.3 * (k + 1))

    return rows


def _num(x):
    return float(x) if isinstance(x, (int, float)) else 0.0


def capture_node(minutes, trigger=None):
    """抓一个节点的全市场快照。返回 node dict（见模块头注释的字段说明）。

    ~70 页请求、6 并发，实测 2~4 秒；失败抛异常由调用方记日志。

    ★★ v3.0.2 新增 `trigger` 参数与 `on_time` 标记：
      · trigger 传入该节点的**计划触发时刻**（如 "09:36"），用于判定本次采集
        是"准点"还是"错过后的补采"；
      · 落盘时写入 `planned`（计划时刻）与 `on_time`（是否准点）两个字段。
      界面据此决定是否在标题上标注真实采集时刻 ——
      **准点采的节点保持原样，只有补采的才标注**，避免正常情况被标语污染。
    """
    # ★★ v3.0.6：替代源下**直接拒绝**，不发任何请求。
    #   本模块硬编码了 `push2.eastmoney.com/api/qt/clist/get`，一次 capture
    #   要约 70 个请求。在替代源下继续打，等于对**已经封禁我们的服务**
    #   再来一轮 70 连击 —— 这正是"被封得更久"的典型成因。
    #   抛异常是刻意的：调用方（_snap_tick / _on_snap_done）会把它记进日志，
    #   用户能明确看到"节点没采到，原因是替代源没有这个能力"。
    try:
        import source as _src
        _why = _src.skip_reason("market_snapshot")
        if _why:
            raise RuntimeError(_why)
    except RuntimeError:
        raise
    except Exception:                                    # noqa: BLE001
        pass

    f_sectors = "f12,f14,f5,f6,f62"
    f_stocks = "f12,f14,f2,f3,f5,f6,f10,f62"
    # ★★ v3.0.4：并发从 3 降到 **2**。
    #   峰值并发 = 这里(2) × _fetch_list 内部(3) = **6**，原先 3×6=**18**。
    #   实测 18 并发时，70 个请求瞬时齐发会直接触发东财限流
    #   （表现：sectors/stocks 采回空列表，日志 `keep-alive 通道探测失败`）。
    #   采集慢一点（秒级）远好于采回空数据 —— 节点窗口有 60 秒，够用。
    with ThreadPoolExecutor(max_workers=2) as ex:
        fu = ex.submit(_fetch_list, _FS_SECTOR_INDU, f_sectors)
        fc = ex.submit(_fetch_list, _FS_SECTOR_CONC, f_sectors)
        fs = ex.submit(_fetch_list, _FS_STOCKS, f_stocks)
        ind, con, stk = fu.result(), fc.result(), fs.result()

    sectors = []
    for d in ind + con:
        if not d.get("f12"):
            continue
        sectors.append({"c": str(d["f12"]), "n": str(d.get("f14") or ""),
                        "v": _num(d.get("f5")), "a": _num(d.get("f6")),
                        "net": _num(d.get("f62"))})
    stocks = []
    for d in stk:
        if not d.get("f12"):
            continue
        stocks.append({"c": str(d["f12"]), "n": str(d.get("f14") or ""),
                       "v": _num(d.get("f5")), "a": _num(d.get("f6")),
                       "lb": _num(d.get("f10")), "net": _num(d.get("f62")),
                       "pct": _num(d.get("f3"))})
    cap = datetime.now().strftime("%H:%M:%S")

    # ★★ v3.0.4：**完整性校验** —— 明显残缺就抛异常，绝不落盘。
    #
    #   实测（2026-09-23 盘中，限流窗口内）：三次采集里有一次返回
    #   `sectors=0 / stocks=5919` —— 个股列表成功、板块列表失败。
    #   这种"部分残缺"比"全空"更危险：
    #     · 全空至少能被 build_payload 的 `_nsec==0 and _nstk==0` 检查抓住；
    #     · 部分残缺（如板块只有 100 条而应 1000 条）**完全看不出来**，
    #       会静静落盘，界面上的"板块榜"就成了一个错误的小样本排行 ——
    #       用户看到的数字是错的，却没有任何提示。
    #
    #   为什么"抛异常"而不是"落盘 + 标注"：
    #     调用方（_snap_tick → _on_snap_fail）在失败时**不置位** _catchup_done，
    #     于是下一轮 tick 会自动重试 —— 这正是我们想要的（限流是瞬时的）。
    #     而落盘一个残缺节点，会同时**挡住重试**（因为节点已存在，
    #     due/catchup 都会跳过）并把错误数据当最终结果呈现。
    #
    #   阈值取法（保守，宁可重试也不接受残缺）：
    #     · 板块：全市场行业+概念 ≈ 1000，取 600 为底线（允许限流少量丢失）；
    #     · 个股：沪深京全 A ≈ 5900，取 3000 为底线。
    #   注意这不影响**正常采集**（实测 sectors=1000 / stocks=5919，远高于底线）。
    MIN_SECTORS, MIN_STOCKS = 600, 3000
    if len(sectors) < MIN_SECTORS or len(stocks) < MIN_STOCKS:
        raise RuntimeError(
            f"采集数据不完整（疑为数据源限流）："
            f"sectors={len(sectors)}（需≥{MIN_SECTORS}）· "
            f"stocks={len(stocks)}（需≥{MIN_STOCKS}）。"
            f"本次不落盘，由上层稍后重试。")

    return {"minutes": int(minutes),
            "label": NODE_LABEL.get({5: "m5", 10: "m10", 15: "m15"}[minutes], ""),
            "captured_at": cap,
            # ★ v3.0.2：计划时刻 + 是否准点（供界面标注；旧快照没这两个字段，
            #   读取端按"缺失即未知"降级，见 build_payload）。
            "planned": trigger or "",
            "on_time": bool(trigger) and _is_on_time(cap, trigger),
            "sectors": sectors, "stocks": stocks}


# ---------------------------------------------------------------- 触发判定
GRACE_SEC = 60                  # 触发时刻的**准点**宽限窗：这一步采到的是真节点数据
# ★ 为什么准点窗口只有 60 秒：clist 返回的是**当下**的累计值。
#   10:00 抓出来的数贴上「开盘后5分钟」的标签就是造假。
#   所以「准点采集」必须卡在节点时刻附近几十秒内，才配得上那个标签。

# ★★ v3.0.2（用户需求）：新增**盘中补采**通道（A 打底 + B 兜底 的 B）。
#   背景（用户原话）：「东方财富接口在收盘后仍可获取当日静态数据，因此即便已过
#   开盘后十五分钟，理论上也应能获取此前的静态数据……这一看就不合理」。
#
#   【用户的判断哪里对、哪里需要修正】
#     · 对的部分：clist 提供的是**当日累计值**，收盘后（甚至第二天开盘前）
#       这些累计值**仍然留在接口里**，所以"能拿到数"是对的。
#     · 需要修正的部分：拿到的是**当天最终的累计值**（收盘值），
#       **不是 09:36 那一刻的值**。接口不提供"09:36 的 clist 快照"。
#       所以补采**不能让数据变回节点时刻的真实值** —— 这是物理限制，
#       不是代码 bug。
#
#   【因此 v3.0.2 的做法】把"补采"做成**如实标注的补采**，而不是伪装成节点：
#     · 准点采到的节点：captured_at 落在 trigger±GRACE_SEC → `on_time=True`；
#     · 盘中补采的节点：captured_at 晚于该窗口，但仍在当日 15:00 收盘前
#       → `on_time=False`，**界面标题明确标注真实采集时刻**；
#     · 收盘后（>15:00）补采：仍允许，但数据是收盘值，
#       标注为「收盘值」——是否显示由调用方决定（见 CATCHUP_DEFAULT_ON）。
#   这样既消掉了"页面一片未采集"的不合理观感，又绝不让累计值冒充节点值。
CATCHUP_UNTIL = "15:00"         # 盘中补采的截止时刻（此后采到的是收盘值）
CATCHUP_DEFAULT_ON = True       # 是否启用补采（用户已确认 A+B 方案）
# 补采窗口下限：09:31。开盘首分钟(09:30)的累计值还在形成中，不宜作节点替代。
CATCHUP_FROM = "09:31"


def _hm_to_dt(now, hm):
    h, m = hm.split(":")
    return now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def _is_on_time(captured_at, trigger):
    """判断某节点的实际采集时刻是否落在其准点窗口内（trigger ~ +GRACE_SEC）。

    captured_at 形如 "HH:MM:SS"（capture_node 写入）；缺失/格式异常时
    保守返回 False（宁可标注为"补采"，也不谎称准点）。
    """
    if not captured_at or not trigger:
        return False
    try:
        ca = datetime.strptime(captured_at, "%H:%M:%S")
        tg = datetime.strptime(trigger + ":00", "%H:%M:%S")
    except Exception:                                # noqa: BLE001
        return False
    d = (ca - tg).total_seconds()
    return 0 <= d <= GRACE_SEC


def due_nodes(tag, now=None, data_dir=None):
    """返回现在**应当准点触发**但还没采到的节点 [(minutes, key, trigger)]。

    应当触发 = 今天已到触发时刻、且仍在准点宽限窗内
    （trigger ~ trigger+GRACE_SEC）、且当天快照里还没有这个节点。

    ★ 只负责"准点采集"。错过准点窗口的补采走 catchup_nodes（v3.0.2）。
    """
    now = now or datetime.now()
    snap = load_snap(tag, data_dir)
    out = []
    for minutes, trigger, _lab, key in SNAP_NODES:
        if key in snap["nodes"]:
            continue
        t0 = _hm_to_dt(now, trigger)
        if t0 <= now <= t0 + timedelta(seconds=GRACE_SEC):
            out.append((minutes, key, trigger))
    return out


def catchup_nodes(tag, now=None, data_dir=None, today=None):
    """返回**已过准点窗口、当天内可以补采**的缺失节点 [(minutes, key, trigger)]。

    ★★ v3.0.2 新增（A 打底 + B 兜底 的 B）。

    判定条件（全部满足才补采）：
      ① 该节点当天还没有数据；
      ② 现在已过该节点的准点窗口（否则交给 due_nodes 准点采，不重复）；
      ③ 现在不早于 CATCHUP_FROM（09:31）—— 开盘首分钟数据未成形；
      ④ 若 today=True（tag 就是今天），允许补采到 CATCHUP_UNTIL（15:00）；
         若 now 已过 15:00，仍允许补采，但数据是**收盘值**
         （调用方据 is_on_time=False 在界面标注）。

    ★ 为什么允许收盘后补采：用户明确要求"收盘后也应能拿到静态数据"。
      技术上成立（累计值仍在），只是它的语义是"当日最终累计"，
      因此**必须继续标为补采**，绝不能升级成"节点值"。
    ★ 补采不做"是否交易日"判断 —— 那是调用方的职责（_snap_tick 已在做），
      本函数只回答"数据层面补不补得回来"。
    """
    if not CATCHUP_DEFAULT_ON:
        return []
    now = now or datetime.now()
    snap = load_snap(tag, data_dir)
    lo = _hm_to_dt(now, CATCHUP_FROM)
    out = []
    for minutes, trigger, _lab, key in SNAP_NODES:
        if key in snap["nodes"]:
            continue                                  # 已有数据，不覆盖
        t0 = _hm_to_dt(now, trigger)
        if now <= t0 + timedelta(seconds=GRACE_SEC):
            continue                                  # 还在准点窗口 → 归 due_nodes
        if now < lo:
            continue                                  # 太早（09:31 之前）
        out.append((minutes, key, trigger))
    return out


# ---------------------------------------------------------------- 聚合
def _sector_rows(node, key, topn):
    rows = sorted(node.get("sectors") or [], key=lambda r: -r.get(key, 0.0))[:topn]
    return [{"rank": i + 1, "code": r["c"], "name": r["n"],
             "vol_wan": r.get("v", 0.0) / 1e4,          # 成交量（万手）
             "amt_yi": r.get("a", 0.0) / 1e8,           # 成交额（亿）
             "net_yi": r.get("net", 0.0) / 1e8}         # 主力净流入（亿）
            for i, r in enumerate(rows)]


def _stock_rows(node, key, topn):
    rows = sorted(node.get("stocks") or [], key=lambda r: -r.get(key, 0.0))[:topn]
    return [{"rank": i + 1, "code": r["c"], "name": r["n"],
             "lb": r.get("lb", 0.0),
             "vol_wan": r.get("v", 0.0) / 1e4,
             "net_yi": r.get("net", 0.0) / 1e8,
             "pct": r.get("pct", 0.0)}
            for i, r in enumerate(rows)]


def build_payload(tag, data_dir=None, topn_stock=10):
    """把当天快照聚合成 UI 直接可用的 payload（「开盘检测」+「个股异动」两页共用）。

    返回：
      nodes: {key: {"label","captured_at","on_time","late","sector_vol_top5",
                    "sector_net_top5","stock_lb_top10","stock_vol_top10",
                    "stock_net_top10"}}
             缺失节点 → {"missing": True, "label": ...}
      select: 个股精选榜 [{rank,code,name,count,lists,net_yi,all9}]

    ★★ v3.0.2：新增 `on_time` / `late` / `cap_hm` 三个字段，供界面如实标注。
      · on_time=True → 准点采集，界面**不**加任何标注（正常态保持干净）；
      · on_time=False → 补采（或旧版本快照无此字段但采集时刻明显晚于计划），
        界面在标题后标注真实采集时刻，如「开盘后5分钟（09:52 补采）」。
      · 旧快照（v3.0.1 及以前落盘的）没有 planned/on_time 字段 ——
        此时按 captured_at 与该节点计划时刻重新推断，保证历史快照也能正确标注。
    """
    snap = load_snap(tag, data_dir)
    nodes = {}
    counter = {}                       # code -> {"count":n, "lists":[...], "stock":row}
    for minutes, _trigger, _lab, key in SNAP_NODES:
        node = snap["nodes"].get(key)
        if not node:
            nodes[key] = {"missing": True, "label": NODE_LABEL[key]}
            continue
        # ★★ v3.0.4：「存在但为空」的节点必须也标为缺失。
        #
        #   实测踩到：限流窗口内 capture_node 成功返回了 node 结构
        #   （含 minutes/label/captured_at），但 `sectors` 与 `stocks`
        #   都是**空列表**。原代码只判 `if not node`，于是这种节点
        #   被当成"已采集"（missing=False），界面渲染出一片**空白** ——
        #   比"未采集"提示更让人困惑：既没有数据，也没有任何说明。
        #
        #   现在把"内容为空"与"节点不存在"统一按缺失处理，
        #   并给出区分性的 reason，便于用户与排查者理解。
        _nsec = len(node.get("sectors") or [])
        _nstk = len(node.get("stocks") or [])
        if _nsec == 0 and _nstk == 0:
            nodes[key] = {"missing": True, "label": NODE_LABEL[key],
                          "reason": "empty",
                          "captured_at": node.get("captured_at", ""),
                          "hint": "采集执行了但返回空数据（多为数据源限流），"
                                  "可点刷新重试"}
            continue
        sv = _sector_rows(node, "v", 5)
        sn = _sector_rows(node, "net", 5)
        sl = _stock_rows(node, "lb", topn_stock)
        svv = _stock_rows(node, "v", topn_stock)
        snn = _stock_rows(node, "net", topn_stock)
        cap = node.get("captured_at", "")
        # ★ 兼容旧快照：没有 on_time 字段时，用 captured_at 与计划时刻现算。
        if "on_time" in node:
            on_time = bool(node.get("on_time"))
        else:
            on_time = _is_on_time(cap, _trigger)
        nodes[key] = {"missing": False, "label": NODE_LABEL[key],
                      "captured_at": cap,
                      "cap_hm": cap[:5] if len(cap) >= 5 else "",
                      "on_time": on_time,
                      "late": not on_time,
                      "planned": node.get("planned") or _trigger,
                      "sector_vol_top5": sv, "sector_net_top5": sn,
                      "stock_lb_top10": sl, "stock_vol_top10": svv,
                      "stock_net_top10": snn}
        # ---- 九个榜单的出现次数统计（任务二的「个股精选」）----
        # ★ v3.0.2：标签里带补采标注 —— 个股精选是跨节点的汇总榜，
        #   若不标注，用户无法知道某一票的上榜依据是节点值还是收盘值。
        _suffix = "" if on_time else "（%s补采）" % (cap[:5] or "补采")
        for li, lst in enumerate((sl, svv, snn)):
            metric = ("量比", "成交量", "净流入")[li]
            for r in lst:
                ent = counter.setdefault(
                    r["code"], {"code": r["code"], "name": r["name"],
                                "count": 0, "lists": [], "net_yi": r["net_yi"]})
                ent["count"] += 1
                ent["lists"].append("%s%s%s" % (NODE_LABEL[key], metric, _suffix))
                ent["net_yi"] = r["net_yi"]
    sel = sorted(counter.values(),
                 key=lambda e: (-e["count"], -e["net_yi"]))
    select = [{"rank": i + 1, "code": e["code"], "name": e["name"],
               "count": e["count"], "lists": e["lists"],
               "net_yi": e["net_yi"], "all9": e["count"] >= 9}
              for i, e in enumerate(sel)]
    return {"nodes": nodes, "select": select}
