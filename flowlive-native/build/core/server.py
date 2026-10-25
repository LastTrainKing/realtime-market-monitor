# -*- coding: utf-8 -*-
"""
资金流向实时看板 —— 本地后端（纯 Python 标准库，零第三方依赖）

启动：  python server.py          （会自动打开浏览器）
        python server.py --port 9000 --no-browser

职责：
  1. 判定「当前应该展示哪个交易日」和「是否处于盘中实时」
  2. 抓取全市场板块的 1 分钟分时主力资金流，维护成一张随时间增长的 面板[时刻 × 板块]
  3. 计算：主力资金曲线、板块辨识度、事件、主线题材簇
  4. 通过本地 HTTP 接口把结果给前端（前端只负责画，不碰数据源）

只监听 127.0.0.1，不对外开放；无登录、无合规相关功能。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

# ============================================================================
# 一、数据源配置
# ============================================================================
# 东方财富 push2 公开接口：无需签名、无需登录。
# 主域 push2 会被限流（连接被重置），因此按顺序做故障转移。
HOSTS = [
    "https://push2.eastmoney.com",
    "https://push2delay.eastmoney.com",
]
UT = "b2884a393a59ad64002292a3e90d46a5"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 不走系统代理（本机 HTTP_PROXY 环境变量指向一个不稳定的本地代理）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_HOST_ORDER = list(HOSTS)          # 最近成功的主机自动提到最前
_HOST_LOCK = threading.Lock()

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
STATIC_DIR = os.path.join(ROOT, "static")
os.makedirs(DATA_DIR, exist_ok=True)


def _http_json(path: str, timeout: int = 12, tries: int = 3):
    """在多域名之间做故障转移地取 JSON。"""
    with _HOST_LOCK:
        hosts = list(_HOST_ORDER)
    last_err = None
    for attempt in range(tries):
        for h in hosts:
            url = h + path
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": UA, "Referer": "https://data.eastmoney.com/",
                    "Accept": "*/*"})
                with _OPENER.open(req, timeout=timeout) as r:
                    body = r.read().decode("utf-8", "ignore")
                if not body:
                    continue
                if h != hosts[0]:
                    with _HOST_LOCK:
                        _HOST_ORDER.remove(h)
                        _HOST_ORDER.insert(0, h)
                return json.loads(body)
            except Exception as e:                                   # noqa: BLE001
                last_err = e
        time.sleep(0.3 * (attempt + 1))
    if last_err:
        pass
    return None


# ============================================================================
# 二、板块宇宙清洗（领域常识：剔掉统计桶，否则它们会霸榜）
# ============================================================================
DROP_PATTERNS = [
    # 宽基指数与互联互通
    r"融资融券", r"沪股通", r"深股通", r"MSCI", r"富时罗素", r"标准普尔",
    r"^AH股$", r"^GDR$", r"^AB股$", r"^B股$", r"HS300", r"^上证",
    r"^深证", r"^深成", r"^中证", r"创业板综", r"^央视", r"^科创\d", r"^北证",
    # 财报事件
    r"^20\d\d(一季报|中报|三季报|年报)",
    # 情绪 / 技术形态
    r"昨日", r"连板", r"涨停", r"炸板", r"触板", r"首板", r"高换手", r"高振幅",
    r"新高", r"破净", r"破发", r"超跌", r"反转股", r"趋势股", r"最近多板", r"摘帽",
    # 市值 / 风格桶
    r"微盘", r"小盘", r"大盘", r"中盘", r"价值股", r"成长股", r"红利",
    r"权重股", r"低价股", r"百元股", r"微利股", r"高市净率", r"低市净率",
    r"风格$", r"茅指数", r"宁组合", r"行业龙头", r"专精特新", r"东方财富热股",
    r"超级品牌", r"稀缺资源", r"^中字头$", r"^ST股$", r"次新股",
    # 持股 / 资金属性
    r"机构重仓", r"基金重仓", r"QFII", r"社保重仓", r"证金持股", r"养老金",
    r"北向", r"举牌", r"股权集中", r"股权分散", r"股权激励", r"股权转让",
    r"密集调研", r"券商金股", r"转债标的", r"IPO受益", r"独角兽", r"^创投$",
    r"参股(银行|券商|保险|期货|新三板)",
]
_DROP_RE = None

# 一级行业白名单：东财 t:2 把一/二/三级行业全返回了（496 个），
# 「汽车」=「汽车零部件」+「乘用车」+… 的父集，混用会重复计资金。
PRIMARY_INDUSTRIES = {
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车", "家用电器",
    "食品饮料", "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输",
    "房地产", "商贸零售", "社会服务", "综合", "建筑材料", "建筑装饰",
    "电力设备", "国防军工", "计算机", "传媒", "通信", "银行", "非银金融",
    "煤炭", "石油石化", "环保", "美容护理", "机械设备",
}


def is_bucket(name: str) -> bool:
    global _DROP_RE
    if _DROP_RE is None:
        import re as _re
        _DROP_RE = [_re.compile(p) for p in DROP_PATTERNS]
    return any(r.search(name) for r in _DROP_RE)


def keep_sector(name: str, kind: str) -> bool:
    if kind == "concept":
        return not is_bucket(name)
    if kind == "industry":
        return name in PRIMARY_INDUSTRIES
    return False


# ============================================================================
# 三、抓取层：板块清单 / 分时资金流 / 指数分时 / 全市场快照
# ============================================================================
def fetch_sector_list() -> list[dict]:
    """返回 [{code, name, kind, secid}]；code 形如 BK1211。"""
    out = []
    for kind, fs in (("industry", "m:90+t:2"), ("concept", "m:90+t:3")):
        pn = 1
        while pn <= 12:
            path = (f"/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2"
                    f"&fid=f62&fs={fs}&fields=f12,f14,f62")
            j = _http_json(path)
            diff = (((j or {}).get("data") or {}).get("diff")) or []
            if not diff:
                break
            for d in diff:
                code = d.get("f12")
                name = d.get("f14")
                if code and name and keep_sector(name, kind):
                    out.append({"code": code, "name": name, "kind": kind,
                                "secid": f"90.{code}"})
            if len(diff) < 100:
                break
            pn += 1
    return out


def fetch_kline(secid: str) -> list[tuple[str, float]]:
    """板块 1 分钟分时主力资金流。
    返回 [(HH:MM, 主力净流入亿元)]，是**从开盘起的累计值**（不是每分钟增量）。
    接口字段 fields2 = f51时间,f52主力净流入,f53小单,f54中单,f55大单,f56超大单（单位：元）
    """
    path = (f"/api/qt/stock/fflow/kline/get?lmt=0&klt=1&secid={secid}"
            f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT}")
    j = _http_json(path)
    kl = ((j or {}).get("data") or {}).get("klines") or []
    out = []
    for line in kl:
        p = line.split(",")
        if len(p) >= 2:
            try:
                out.append((p[0][-5:], round(float(p[1]) / 1e8, 4)))
            except ValueError:
                continue
    return out


def fetch_index_trend(secid: str = "1.000001"):
    """指数 1 分钟分时。返回 [(date_str, HH:MM, 点位, 成交量)]"""
    path = (f"/api/qt/stock/trends2/get?secid={secid}&fields1=f1,f2,f3,f7"
            f"&fields2=f51,f53,f56&iscr=0&ndays=1&ut={UT}")
    j = _http_json(path)
    tr = ((j or {}).get("data") or {}).get("trends") or []
    out = []
    for line in tr:
        p = line.split(",")
        if len(p) >= 3:
            try:
                out.append((p[0][:10], p[0][-5:], float(p[1]), float(p[2])))
            except ValueError:
                continue
    return out


def fetch_snapshot_all():
    """全市场当前累计主力净流入（一次请求拿 1000 个板块）。
    字段 f62 = 今日主力净流入额（元），与 kline 的 f52 同口径。
    """
    snap = {}
    for kind, fs in (("industry", "m:90+t:2"), ("concept", "m:90+t:3")):
        for pn in (1, 2, 3, 4, 5, 6):
            path = (f"/api/qt/clist/get?pn={pn}&pz=200&po=1&np=1&fltt=2&invt=2"
                    f"&fid=f62&fs={fs}&fields=f12,f14,f62,f124")
            j = _http_json(path)
            diff = (((j or {}).get("data") or {}).get("diff")) or []
            if not diff:
                break
            for d in diff:
                code, name = d.get("f12"), d.get("f14")
                v = d.get("f62")
                if code and isinstance(v, (int, float)):
                    snap[code] = (name, round(v / 1e8, 4), kind)
            if len(diff) < 200:
                break
    return snap


def fetch_index_quote():
    path = ("/api/qt/stock/get?secid=1.000001&fields=f43,f44,f45,f46,f60,f170,f169"
            f"&ut={UT}&fltt=2")
    j = _http_json(path)
    d = (j or {}).get("data") or {}
    return {"last": d.get("f43"), "high": d.get("f44"), "low": d.get("f45"),
            "open": d.get("f46"), "prev": d.get("f60"),
            "chg": d.get("f169"), "pct": d.get("f170")}


# ============================================================================
# 四、交易时段判定  ← 用户最关心的「自动切换日期」
# ============================================================================
PHASES = [
    ("pre_open", "盘前", (0, 0), (9, 14)),
    ("call_auction", "集合竞价", (9, 15), (9, 25)),
    ("pre_open2", "开盘前", (9, 25), (9, 30)),
    ("morning", "早盘", (9, 30), (11, 30)),
    ("lunch", "午间休市", (11, 30), (13, 0)),
    ("afternoon", "午盘", (13, 0), (15, 0)),
    ("closed", "已收盘", (15, 0), (23, 59)),
]


def phase_of(now: datetime) -> tuple[str, str]:
    hm = now.hour * 60 + now.minute
    for key, label, a, b in PHASES:
        if a[0] * 60 + a[1] <= hm < b[0] * 60 + b[1]:
            return key, label
    return "closed", "已收盘"


def is_weekend(d: datetime) -> bool:
    return d.weekday() >= 5


def resolve_session(now: datetime | None = None) -> dict:
    """
    决定「展示哪个交易日」「是否实时」。

    核心思路：**让数据源自己告诉我们当前交易日**。
    指数分时接口永远返回最近一个有数据的交易日：
      · 9/18 凌晨 01:00 打开 → 返回 2026-09-17  → 展示 9-17（已收盘的完整数据）
      · 9/18 早上 09:31   打开 → 返回 2026-09-18  → 展示 9-18 并进入实时轮询
      · 周末打开             → 返回上周五        → 展示上周五
    这样不需要维护节假日日历，交易所不开市自然就没有当天数据。
    """
    now = now or datetime.now()
    key, label = phase_of(now)
    trend = fetch_index_trend()
    api_date, last_hm = None, None
    if trend:
        api_date = trend[0][0]
        last_hm = trend[-1][1]

    today = now.strftime("%Y-%m-%d")
    live = bool(api_date) and api_date == today and key in (
        "call_auction", "pre_open2", "morning", "afternoon")

    if not api_date:                                  # 网络异常兜底
        d = now
        if key in ("pre_open", "call_auction", "pre_open2") or is_weekend(now):
            d = now - timedelta(days=1)
            while is_weekend(d):
                d = d - timedelta(days=1)
        api_date = d.strftime("%Y-%m-%d")

    session = {
        "display_date": api_date,
        "is_today": api_date == today,
        "phase": key,
        "phase_label": label,
        "live": live,
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "last_bar": last_hm,
        # 前端轮询节奏：实时 3 秒，非实时 15 秒
        "poll_ms": 3000 if live else 15000,
        "note": "",
    }
    if live:
        session["note"] = f"盘中实时，正在接收 {api_date} 的分时数据（每 5 秒刷新快照）"
    elif api_date == today and key == "lunch":
        session["note"] = f"午间休市，展示 {api_date} 上午数据；13:00 恢复实时刷新"
    elif api_date == today:
        session["note"] = f"今日 {api_date} 已收盘，展示全天完整数据"
    elif key in ("pre_open", "call_auction", "pre_open2"):
        session["note"] = f"未开盘，当前展示最近交易日 {api_date}；09:30 开盘后自动切换到今日"
    else:
        session["note"] = f"非交易日，当前展示最近交易日 {api_date}"
    return session


# ============================================================================
# 五、面板与指标计算
# ============================================================================
class Panel:
    """
    维护「时刻 × 板块」的面板，并计算辨识度等指标。

    axis        : ['09:31','09:32',...] 时间轴（随盘中推进增长）
    values[code]: 与 axis 等长的数值列表（亿元），缺失用前值填充
    数据来源：
      · kline 接口（1 分钟）→ 首次全量拉取 + 每 5 分钟回补
      · 快照接口（5 秒）    → 刷新「当前分钟」的值，让曲线末端持续跳动
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        self.axis: list[str] = []
        self.idx: dict[str, int] = {}
        self.values: dict[str, list[float]] = {}
        self.names: dict[str, str] = {}
        self.kinds: dict[str, str] = {}
        self.series_ok: set[str] = set()
        self.index: list[tuple[str, float, float]] = []   # (HH:MM, 点位, 量)
        self.quote: dict = {}
        self.updated_at: str = ""
        self.version: int = 0

    # ---- 时间轴 ----
    def _ensure_axis(self, hms: list[str]):
        """把新出现的分钟加进时间轴；已有板块用前值填充（保证矩阵等长）。"""
        with self.lock:
            new = [hm for hm in hms if hm not in self.idx]
            if not new:
                return
            known = set(self.axis)
            merged = sorted(known | set(new))
            order = {hm: i for i, hm in enumerate(merged)}
            for code, arr in self.values.items():
                if len(arr) == len(self.axis):
                    grown = [arr[order[hm]] if hm in known else None for hm in merged]
                    last = arr[-1] if arr else 0.0
                    for k, hm in enumerate(merged):
                        if grown[k] is None:
                            grown[k] = last
                        else:
                            last = grown[k]
                    self.values[code] = grown
                else:
                    self.values[code] = [arr[-1] if arr else 0.0] * len(merged)
            self.axis = merged
            self.idx = order

    def apply_kline(self, data: dict[str, list[tuple[str, float]]], meta: dict):
        """data: {code: [(hm, val)]}；meta: {code: (name, kind)}"""
        with self.lock:
            hms = sorted({hm for rows in data.values() for hm, _ in rows})
            if hms:
                self._ensure_axis(hms)
            for code, rows in data.items():
                if code not in self.values:
                    self.values[code] = [0.0] * len(self.axis)
                name, kind = meta.get(code, (code, "concept"))
                self.names[code] = name
                self.kinds[code] = kind
                arr = self.values[code]
                for hm, v in rows:
                    i = self.idx.get(hm)
                    if i is not None:
                        arr[i] = v
                self.series_ok.add(code)
            self.touch()

    def apply_snapshot(self, snap: dict[str, tuple[str, float, str]]):
        """snap: {code: (name, val, kind)} —— 刷新当前分钟的值"""
        with self.lock:
            if not self.axis:
                return
            for code, (name, val, kind) in snap.items():
                if not keep_sector(name, kind):
                    continue
                if code not in self.values:
                    self.values[code] = [0.0] * len(self.axis)
                self.names[code] = name
                self.kinds[code] = kind
                self.values[code][-1] = val
            self.touch()

    def set_index(self, trend_rows, quote):
        with self.lock:
            self.index = [(hm, px, vol) for _, hm, px, vol in trend_rows]
            self.quote = quote or {}

    def touch(self):
        self.updated_at = datetime.now().strftime("%H:%M:%S")
        self.version += 1

    # ---- 指标 ----
    def _pct_rank(self, arr):
        n = len(arr)
        if n <= 1:
            return [0.0] * n
        order = sorted(range(n), key=lambda k: arr[k])
        out = [0.0] * n
        for pos, k in enumerate(order):
            out[k] = pos / (n - 1)
        return out

    def analyze(self, topk=20, vel_win=5, jump_win=10, persist_win=20):
        """
        在当前时间点上计算全市场辨识度。
        salience = 体量34% + 速度28% + 排名跃迁16% + 持续性12% + 异动z10%
        全部用**横截面分位**而不是绝对金额，避免体量大的行业桶永远霸榜。
        """
        with self.lock:
            T = len(self.axis)
            if T < 2:
                return None
            codes = [c for c, arr in self.values.items() if c in self.series_ok]
            if not codes:
                return None
            i = T - 1
            j0 = max(0, i - vel_win)
            j1 = max(0, i - jump_win)

            cur = [self.values[c][i] for c in codes]
            d5 = [self.values[c][i] - self.values[c][j0] for c in codes]
            abs_cur = [abs(v) for v in cur]
            abs_d5 = [abs(v) for v in d5]

            p_abs = self._pct_rank(abs_cur)
            p_vel = self._pct_rank(abs_d5)

            rank_now = self._rank_of(cur)            # 1 = 净流入最大
            rank_prev = self._rank_of([self.values[c][j1] for c in codes])
            jump = [rank_prev[k] - rank_now[k] for k in range(len(codes))]
            p_jump = self._pct_rank(jump)

            # 持续性：近 persist_win 分钟内进入过 TopK 的次数
            persist = self._persist(codes, i, persist_win, topk)

            # 异动 z：近 5 分钟增量相对自身当日波动率
            zs = self._zscore(codes, d5)

            rows = []
            for k, c in enumerate(codes):
                sal = (0.34 * p_abs[k] + 0.28 * p_vel[k] + 0.16 * p_jump[k]
                       + 0.12 * persist[k] / float(persist_win)
                       + 0.10 * min(max(zs[k] / 3.0, 0.0), 1.0))
                rows.append({
                    "code": c, "name": self.names.get(c, c),
                    "kind": self.kinds.get(c, "concept"),
                    "net": round(cur[k], 2),
                    "d5": round(d5[k], 2),
                    "rank": rank_now[k],
                    "jump10": int(rank_prev[k] - rank_now[k]),
                    "z": round(zs[k], 2),
                    "persist": persist[k],
                    "salience": round(sal, 3),
                })
            return {"rows": rows, "i": i, "axis": list(self.axis)}

    @staticmethod
    def _rank_of(vals):
        order = sorted(range(len(vals)), key=lambda k: -vals[k])
        out = [0] * len(vals)
        for pos, k in enumerate(order):
            out[k] = pos + 1
        return out

    def _persist(self, codes, i, win, topk):
        out = [0] * len(codes)
        lo = max(0, i - win + 1)
        for t in range(lo, i + 1):
            vals = [self.values[c][t] for c in codes]
            order = sorted(range(len(codes)), key=lambda k: -vals[k])
            for pos, k in enumerate(order):
                if pos < topk or pos >= len(codes) - topk:
                    out[k] += 1
        return out

    def _zscore(self, codes, d5):
        n = len(codes)
        mu = sum(d5) / n
        var = sum((x - mu) ** 2 for x in d5) / max(1, n - 1)
        sd = var ** 0.5 or 1e-9
        return [(x - mu) / sd for x in d5]

    # ---- 事件检测 ----
    def detect_events(self, ana, limit=12):
        """
        挑出「值得解说」的事件。
        原则：事件应当是**变化**（新晋/加速/跃迁/反转），而不是静态状态；
        「持续」类只在体量与速度都显著时才报，并且最多 2 条，避免刷屏。
        """
        events = []
        big_move = sorted((r for r in ana["rows"] if r["kind"] == "concept"),
                          key=lambda r: -abs(r["d5"]))
        d5_cut = abs(big_move[4]["d5"]) if len(big_move) > 4 else 0.0

        for r in ana["rows"]:
            if r["kind"] != "concept":
                continue
            name, net, d5, sal, z = r["name"], r["net"], r["d5"], r["salience"], r["z"]
            if abs(net) < 0.8 and abs(d5) < 0.5:
                continue

            # 1) 新晋：排名进入前列且 10 分钟内大幅上升
            if r["rank"] <= 10 and r["jump10"] >= 8:
                events.append(("新晋", name,
                               f"资金排名升至第 {r['rank']} 位，净额 {net:+.2f} 亿",
                               abs(net) * (1 + sal) * 1.15))
            # 2) 加速：近 5 分钟增量在横截面里最靠前且 z 值显著
            if abs(z) >= 1.8 and abs(d5) >= max(1.0, d5_cut * 0.8):
                events.append(("加速流入" if d5 > 0 else "加速流出", name,
                               f"近 5 分钟 {d5:+.2f} 亿（当日 {net:+.2f} 亿），z={z:.1f}",
                               abs(d5) * 1.5))
            # 3) 排名跃迁
            if r["jump10"] >= 20 and abs(net) >= 0.8:
                events.append(("排名跃迁", name,
                               f"10 分钟内排名提升 {r['jump10']} 位，净额 {net:+.2f} 亿",
                               r["jump10"] * 0.3))
            # 4) 持续：必须体量+速度双显著
            if r["persist"] >= 18 and abs(net) >= 5 and abs(d5) >= d5_cut * 0.5:
                events.append(("持续强势" if net > 0 else "持续失血", name,
                               f"近 20 分钟持续在榜，累计 {net:+.2f} 亿，近 5 分钟 {d5:+.2f} 亿",
                               abs(net) * 0.75))

        best = {}
        for t, nm, d, s in events:
            if nm not in best or s > best[nm][3]:
                best[nm] = (t, nm, d, s)
        seq = sorted(best.values(), key=lambda x: -x[3])
        # 「持续」类最多保留 2 条
        out, n_persist = [], 0
        for t, nm, d, s in seq:
            if t.startswith("持续"):
                if n_persist >= 2:
                    continue
                n_persist += 1
            out.append({"type": t, "name": nm, "desc": d, "strength": round(s, 2)})
            if len(out) >= limit:
                break
        return out

    # ---- 主线聚类（市场中性化 + 龙头种子贪心） ----
    def cluster_themes(self, ana, corr_th=0.5, min_members=2, max_cand=120,
                       max_size=20, lookback=100, topn=6):
        """
        全市场板块的分时资金是同涨同跌的（共同因子）。
        先做市场中性化（扣掉每个时刻的横截面均值），再做相关聚类，
        否则 400 个板块会聚成一个大簇。
        用「以龙头为种子的贪心聚类」而不是单链聚类，避免传递串簇。
        """
        with self.lock:
            i = ana["i"]
            codes = [r["code"] for r in ana["rows"]]
            by = {r["code"]: r for r in ana["rows"]}
            cand = sorted(codes, key=lambda c: -abs(by[c]["net"]))[:max_cand]
            lo = max(0, i - lookback)
            if i - lo < 8:
                return []
            # 构造中性化增量矩阵
            vectors = {}
            for c in cand:
                arr = self.values[c]
                inc = []
                for t in range(lo + 1, i + 1):
                    inc.append(arr[t] - arr[t - 1])
                vectors[c] = inc
            L = i - lo
            means = [sum(vectors[c][k] for c in cand) / len(cand) for k in range(L)]
            for c in cand:
                vectors[c] = [vectors[c][k] - means[k] for k in range(L)]
            norms = {}
            for c in cand:
                v = vectors[c]
                mu = sum(v) / L
                var = sum((x - mu) ** 2 for x in v) / max(1, L - 1)
                norms[c] = var ** 0.5 or 1e-9

            def corr(a, b):
                va, vb = vectors[a], vectors[b]
                ma = sum(va) / L
                mb = sum(vb) / L
                s = sum((va[k] - ma) * (vb[k] - mb) for k in range(L))
                return s / ((norms[a] * norms[b]) * max(1, L - 1))

            used, clusters = set(), []
            for seed in cand:
                if seed in used:
                    continue
                members = [seed]
                for other in cand:
                    if other == seed or other in used:
                        continue
                    if corr(seed, other) >= corr_th:
                        members.append(other)
                        if len(members) >= max_size:
                            break
                used.update(members)
                if len(members) < min_members:
                    continue
                total = sum(by[m]["net"] for m in members)
                lead = max(members, key=lambda m: abs(by[m]["net"]))
                # 簇名取「与簇内其他成员平均相关性最高」的题材——比单纯取体量最大者
                # 更能代表这簇在讲什么
                if len(members) > 1:
                    cen = {}
                    for a in members:
                        cs = [corr(a, b) for b in members if b != a]
                        cen[a] = sum(cs) / len(cs)
                    core = max(cen, key=lambda m: cen[m])
                else:
                    core = seed
                show = sorted(members, key=lambda m: -abs(by[m]["net"]))[:8]
                clusters.append({
                    "lead": by[lead]["name"], "core": by[core]["name"],
                    "n_members": len(members), "total": round(total, 2),
                    "direction": "流入" if total > 0 else "流出",
                    "members": [{"name": by[m]["name"], "net": by[m]["net"]} for m in show],
                })
            clusters.sort(key=lambda x: -abs(x["total"]))
            return clusters[:topn]

    # ---- 主角更替时间轴 ----
    def leader_timeline(self, step=10, ranks=3):
        """每隔 step 分钟取一次辨识度 Top3。"""
        with self.lock:
            T = len(self.axis)
            codes = [c for c, a in self.values.items() if c in self.series_ok]
            if T < 12 or not codes:
                return []
            out = []
            for i in range(11, T, step):
                j0 = max(0, i - 5)
                cur = [self.values[c][i] for c in codes]
                d5 = [abs(self.values[c][i] - self.values[c][j0]) for c in codes]
                p_abs = self._pct_rank([abs(v) for v in cur])
                p_vel = self._pct_rank(d5)
                scored = sorted(range(len(codes)),
                                key=lambda k: -(0.6 * p_abs[k] + 0.4 * p_vel[k]))[:ranks]
                slot = []
                for k in scored:
                    c = codes[k]
                    slot.append({"name": self.names.get(c, c),
                                 "net": round(cur[k], 2)})
                out.append({"time": self.axis[i], "top": slot})
            return out

    def fan_series(self, topn=20):
        """取当前净额最靠前的流入/流出题材的完整曲线，供前端画发散图。"""
        with self.lock:
            codes = [c for c, a in self.values.items() if c in self.series_ok]
            if not codes:
                return {"axis": [], "items": []}
            i = len(self.axis) - 1
            ranked = sorted(codes, key=lambda c: -self.values[c][i])
            k = max(1, topn // 2)
            picks = ranked[:k] + ranked[-k:]
            items = []
            for c in picks:
                items.append({
                    "code": c, "name": self.names.get(c, c),
                    "kind": self.kinds.get(c, "concept"),
                    "net": round(self.values[c][i], 2),
                    "dir": "in" if self.values[c][i] >= 0 else "out",
                    "values": [round(v, 2) for v in self.values[c]],
                })
            return {"axis": list(self.axis), "items": items}

    def timeline_series(self, topn=18):
        """辨识度时间轴用的题材级曲线（取当日峰值最大的若干个）。"""
        with self.lock:
            codes = [c for c, a in self.values.items() if c in self.series_ok]
            if not codes:
                return {"axis": [], "items": []}
            peaks = {c: max(abs(v) for v in self.values[c]) for c in codes}
            picks = sorted(codes, key=lambda c: -peaks[c])[:topn]
            return {
                "axis": list(self.axis),
                "items": [{"name": self.names.get(c, c), "peak": round(peaks[c], 2),
                           "values": [round(v, 2) for v in self.values[c]]}
                          for c in picks],
            }


# ============================================================================
# 六、后台刷新线程
# ============================================================================
class Service:
    def __init__(self):
        self.panel = Panel()
        self.lock = threading.RLock()
        self.session = resolve_session()
        self.status = "启动中"
        self.sector_meta: dict[str, tuple[str, str]] = {}
        self.last_full_fetch = 0.0
        self.last_kline_touch = 0.0
        self.full_fetch_in_progress = False
        self.exit_flag = False

    # ---- 全量拉取 ----
    def cache_path(self):
        return os.path.join(DATA_DIR, f"panel_{self.session['display_date']}.json")

    def save_cache(self):
        """把当日面板落盘，重启时可直接加载，避免等待。"""
        try:
            p = self.panel
            with p.lock:
                payload = {"date": self.session["display_date"],
                           "axis": list(p.axis),
                           "names": dict(p.names), "kinds": dict(p.kinds),
                           "values": {c: [round(v, 4) for v in a]
                                      for c, a in p.values.items() if c in p.series_ok},
                           "index": [[hm, px] for hm, px, _ in p.index],
                           "quote": p.quote}
            tmp = self.cache_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self.cache_path())
        except Exception:                                            # noqa: BLE001
            pass

    def load_cache(self) -> bool:
        """若磁盘缓存与当前交易日一致，直接加载（秒开）。"""
        try:
            p = self.cache_path()
            if not os.path.exists(p):
                return False
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("date") != self.session["display_date"] or not d.get("axis"):
                return False
            self.panel.reset()
            self.panel.axis = list(d["axis"])
            self.panel.idx = {hm: i for i, hm in enumerate(self.panel.axis)}
            self.panel.names = dict(d["names"])
            self.panel.kinds = dict(d["kinds"])
            self.panel.values = {c: list(v) for c, v in d["values"].items()}
            self.panel.series_ok = set(self.panel.values.keys())
            self.panel.index = [(hm, px, 0) for hm, px in d.get("index", [])]
            self.panel.quote = d.get("quote", {})
            with self.lock:
                self.sector_meta = {c: (d["names"][c], d["kinds"][c]) for c in d["values"]}
            self.panel.touch()
            return True
        except Exception:                                            # noqa: BLE001
            return False

    def full_fetch(self):
        if self.full_fetch_in_progress:
            return
        self.full_fetch_in_progress = True
        try:
            self.status = "拉取板块清单…"
            sectors = fetch_sector_list()
            if not sectors:
                self.status = "板块清单获取失败，稍后重试"
                return
            meta = {s["code"]: (s["name"], s["kind"]) for s in sectors}
            with self.lock:
                self.sector_meta = meta
            self.status = f"拉取 {len(sectors)} 个板块的分时资金流…"
            data = {}
            with ThreadPoolExecutor(max_workers=16) as ex:
                for s, rows in zip(sectors, ex.map(lambda x: fetch_kline(x["secid"]), sectors)):
                    if rows:
                        data[s["code"]] = rows
            # 拉取严重失败时不要清掉已有数据（宁可展示旧快照）
            if len(data) < max(30, len(sectors) // 5):
                self.status = f"本次仅取到 {len(data)} 个板块，保留原有数据"
                return
            self.panel.reset()
            self.panel.apply_kline(data, meta)
            self.panel.set_index(fetch_index_trend(), fetch_index_quote())
            self.last_full_fetch = time.time()
            self.last_kline_touch = time.time()
            self.status = "就绪"
            self.save_cache()
        finally:
            self.full_fetch_in_progress = False

    # ---- 增量：回补分时（只补重点板块） ----
    def refresh_kline_top(self, n=160):
        with self.lock:
            meta = dict(self.sector_meta)
        codes = list(meta.keys())
        if not codes:
            return
        # 按当前净额挑重点，保证画面上的板块曲线是最新的
        with self.panel.lock:
            if self.panel.axis:
                i = len(self.panel.axis) - 1
                codes = sorted(codes, key=lambda c: -abs(
                    self.panel.values.get(c, [0])[i] if self.panel.values.get(c) else 0))[:n]
        data = {}
        with ThreadPoolExecutor(max_workers=12) as ex:
            for c, rows in zip(codes, ex.map(lambda x: fetch_kline("90." + x), codes)):
                if rows:
                    data[c] = rows
        if data:
            self.panel.apply_kline(data, meta)
            self.last_kline_touch = time.time()

    def refresh_snapshot(self):
        snap = fetch_snapshot_all()
        if snap:
            self.panel.apply_snapshot(snap)

    def refresh_index(self):
        self.panel.set_index(fetch_index_trend(), fetch_index_quote())

    # ---- 主循环 ----
    def loop(self):
        # 首次：优先读当日缓存（秒开），否则全量拉取
        try:
            if self.load_cache():
                self.status = "已从本地缓存加载当日数据"
                try:
                    self.refresh_index()
                except Exception:                                    # noqa: BLE001
                    pass
            else:
                self.full_fetch()
        except Exception as e:                                       # noqa: BLE001
            self.status = f"首次拉取异常：{type(e).__name__}"

        while not self.exit_flag:
            try:
                sess = resolve_session()
                with self.lock:
                    changed_date = sess["display_date"] != self.session["display_date"]
                    self.session = sess
                if changed_date:
                    # 关键：日期切换（例如 09:30 由昨日切到今日）→ 重新全量拉取
                    self.status = f"交易日切换为 {sess['display_date']}，重新拉取…"
                    self.full_fetch()
                    self.refresh_index()
                    continue

                if sess["live"]:
                    self.status = "盘中实时刷新中"
                    self.refresh_snapshot()                        # 每 5 秒
                    if time.time() - self.last_kline_touch > 300:   # 每 5 分钟回补
                        self.refresh_kline_top()
                        self.save_cache()
                    if time.time() - self.last_full_fetch > 1800:   # 每 30 分钟全量校准
                        self.full_fetch()
                    self.refresh_index()
                    time.sleep(5)
                else:
                    self.status = "非盘中：已加载完整数据"
                    time.sleep(20)
            except Exception as e:                                   # noqa: BLE001
                self.status = f"刷新异常：{type(e).__name__}，将重试"
                time.sleep(5)

    # ---- 组装前端状态 ----
    def build_state(self):
        ana = self.panel.analyze()
        sess = self.session
        if not ana:
            return {"meta": sess, "status": self.status, "ready": False}
        rows = ana["rows"]
        concepts = [r for r in rows if r["kind"] == "concept"]
        inlist = sorted(concepts, key=lambda r: -r["net"])[:15]
        outlist = sorted(concepts, key=lambda r: r["net"])[:15]
        industries = [r for r in rows if r["kind"] == "industry"]
        ind_in = sorted(industries, key=lambda r: -r["net"])[:6]
        ind_out = sorted(industries, key=lambda r: r["net"])[:6]
        return {
            "meta": sess,
            "status": self.status,
            "ready": True,
            "updated_at": self.panel.updated_at,
            "version": self.panel.version,
            "bars": len(self.panel.axis),
            "axis": self.panel.axis,
            "fan": self.panel.fan_series(20),
            "timeline": self.panel.timeline_series(18),
            "leaders": self.panel.leader_timeline(10, 3),
            "events": self.panel.detect_events(ana, 12),
            "themes": self.panel.cluster_themes(ana),
            "board": {"in": inlist, "out": outlist,
                      "ind_in": ind_in, "ind_out": ind_out},
            "index": {"series": [[hm, px] for hm, px, _ in self.panel.index],
                      "quote": self.panel.quote},
            "stats": {"total": len(rows),
                      "concept": len(concepts),
                      "industry": len(industries)},
        }


SERVICE = Service()


# ============================================================================
# 七、HTTP 服务
# ============================================================================
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer       # noqa: E402


class Handler(BaseHTTPRequestHandler):
    server_version = "FlowLive/1.0"

    def log_message(self, fmt, *args):                       # 静默访问日志
        if os.environ.get("FLOWLIVE_VERBOSE"):
            sys.stderr.write("[http] " + (fmt % args) + "\n")

    def _send(self, code, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def do_GET(self):                                        # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if route in ("/", "/index.html"):
            p = os.path.join(STATIC_DIR, "index.html")
            if not os.path.exists(p):
                self._send(500, b"index.html missing", "text/plain; charset=utf-8")
                return
            with open(p, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
            return
        if route == "/api/state":
            body = json.dumps(SERVICE.build_state(), ensure_ascii=False,
                              default=str).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if route == "/api/refresh":
            threading.Thread(target=SERVICE.refresh_snapshot, daemon=True).start()
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
            return
        if route == "/api/session":
            self._send(200, json.dumps(SERVICE.session, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
            return
        if route == "/api/health":
            self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
            return
        self._send(404, b'{"error":"not found"}', "application/json; charset=utf-8")


def free_port(preferred=8848):
    for p in [preferred] + list(range(preferred + 1, preferred + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return preferred


def main():
    ap = argparse.ArgumentParser(description="资金流向实时看板（本地）")
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    threading.Thread(target=SERVICE.loop, daemon=True).start()

    port = free_port(args.port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 66)
    print("  资金流向实时看板 已启动")
    print(f"  地址：{url}")
    print("  关闭本窗口即可停止服务（或按 Ctrl+C）")
    print("=" * 66)
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        SERVICE.exit_flag = True
        httpd.server_close()


if __name__ == "__main__":
    main()
