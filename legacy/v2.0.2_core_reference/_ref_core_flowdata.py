# -*- coding: utf-8 -*-
"""
flowdata.py —— 板块分时资金流数据层
数据源：东方财富 push2 公开接口（无需签名、无需登录）

对外接口：
    list_sectors(kind)        -> [(secid, code, name, kind)]
    fetch_sector_kline(secid) -> [(hm, main, small, mid, big, xbig)]   # 1分钟
    fetch_index_trend(secid)  -> [(hm, price, volume)]
    build_panel(date_tag)     -> 落盘 panel.npy / panel.json / index.json

★ 零第三方依赖：只用标准库（urllib / json / concurrent.futures / numpy）。
  这是刻意的设计约束 —— 本模块会被 PyInstaller 打包进单文件 exe，
  任何第三方 HTTP 库（requests 等）都可能因动态导入而被漏收，
  导致运行期 ModuleNotFoundError。标准库永远在，不存在漏收问题。
"""
from __future__ import annotations

import json, os, time, threading, urllib.error, urllib.parse, urllib.request
import http.client, ssl
from concurrent.futures import ThreadPoolExecutor

# ------------------------------------------------------------------ 数据源配置
# 主域 push2 会被限流（连接被重置），因此保留多域名做故障转移；
# 最近成功的主机会被自动提到最前，减少无效重试。
HOSTS = [
    "https://push2.eastmoney.com",
    "https://push2delay.eastmoney.com",
]
UT = "b2884a393a59ad64002292a3e90d46a5"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HDRS = {
    "User-Agent": UA,
    "Referer": "https://data.eastmoney.com/",
    "Accept": "*/*",
}

# 本机环境变量里有 HTTP(S)_PROXY，且代理不稳定（RemoteDisconnected）。
# 因此准备两个通道：直连优先，失败再走环境代理。
_OPENER_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_OPENER_ENV = urllib.request.build_opener()      # 默认行为：尊重 HTTP(S)_PROXY
_OPENERS = [_OPENER_DIRECT, _OPENER_ENV]

_HOST_ORDER = list(HOSTS)
_HOST_LOCK = threading.Lock()

# ------------------------------------------------------------------ 长连接池
# ★★ v1.2.1 关键性能修复 —— 这一处把抓取耗时降到原来的 ~1/10。
#
#   问题：原实现走 urllib，**每个请求都新建一条 TCP+TLS 连接**。
#   实测（同一台机器、20 个板块连续请求）：
#       urllib 每请求新建连接      550 ms/请求
#       http.client 复用同一连接    53 ms/请求      ← 10.3×
#   也就是说耗时绝大部分不是"传数据"，而是**重复的 TLS 握手**。
#   抓全市场 ~1000 个板块时，这个差异是「约 23 秒」和「约 2 秒」的区别。
#
#   做法：**每线程每主机**持有一条 HTTPSConnection，请求完不关闭，
#   下个请求接着用（HTTP/1.1 keep-alive）。用 threading.local 存，
#   天然线程安全，不需要锁；ThreadPoolExecutor 的每个 worker 各有一条。
#
#   ★ 为什么不用 requests/urllib3 的 Session：本模块有硬约束 ——
#     **零第三方依赖**（见文件头说明，PyInstaller 会漏收动态导入的库）。
#     http.client + ssl 都是标准库，同样的效果，不引入打包风险。
#
#   ★ 连接坏了怎么办（keep-alive 的固有风险）：
#     服务端会主动关闭空闲连接，此时复用会抛 RemoteDisconnected。
#     处理分两层：① 单次请求内部**丢弃坏连接、重建后重试一次**；
#     ② 仍失败则退到原来的 urllib 通道（含环境代理），保证兼容性。
_SSL_CTX = None
_SSL_LOCK = threading.Lock()


def _ssl_ctx():
    """复用同一个 SSL context —— 避免每次建连都重载一次 CA 证书库。"""
    global _SSL_CTX
    if _SSL_CTX is None:
        with _SSL_LOCK:
            if _SSL_CTX is None:
                _SSL_CTX = ssl.create_default_context()
    return _SSL_CTX


_TLS = threading.local()

# ------------------------------------------------------------------ 通道与主机健康度
# ★ 这里区分**两类失败**，不能混为一谈（v1.2.1 踩过并修正）：
#
#   ① 「长连接通道本身不可用」—— 例如必须走 HTTP 代理的环境，
#      keep-alive 直连永远连不上。对策：探测若干次仍失败就把整条
#      通道关掉，退回 urllib（含环境代理）。
#
#   ② 「某个域名被限流」—— 这是**主机级**问题，与通道无关。
#      实测：push2 被限流时 keep-alive 和 urllib **两个通道都**
#      返回 RemoteDisconnected，而备用的 push2delay 完全正常。
#      对策：主机级冷却（连续失败就把它排到最后），而不是关掉通道。
#
#   ★ 第一版把两者合成一个全局计数器，后果很隐蔽：主域被限流会把
#     全局计数打满 → 长连接通道被永久关闭 → **即使主域恢复也再不会用
#     快速通道**，性能静默退化且没有任何提示。故拆开。
_KA_STATE = {"ok": 0, "fail": 0, "off": False, "proven": False}
_KA_LOCK = threading.Lock()
KA_PROBE_LIMIT = 6              # 未证明可用前，连试这么多次就判通道不可用

_HOST_FAIL = {}                 # {host: {"n": 连续失败数, "until": 冷却截止}}
_HOST_FAIL_LOCK = threading.Lock()
HOST_FAIL_LIMIT = 3             # 连续失败达到这个数就进入冷却
HOST_COOLDOWN = 60.0            # 冷却秒数（限流通常持续分钟级）


def _ka_off():
    with _KA_LOCK:
        return _KA_STATE["off"]


def _ka_proven():
    """长连接通道是否**已经成功过**（成功过就不再回退 urllib，省时间）。"""
    with _KA_LOCK:
        return _KA_STATE["proven"]


def _ka_note(ok):
    """记录一次长连接结果。仅在"从未成功过"时才可能判通道不可用。"""
    global _KA_STATE
    with _KA_LOCK:
        if ok:
            _KA_STATE["ok"] += 1
            _KA_STATE["proven"] = True
        else:
            _KA_STATE["fail"] += 1
            if (not _KA_STATE["proven"]
                    and _KA_STATE["fail"] >= KA_PROBE_LIMIT
                    and not _KA_STATE["off"]):
                _KA_STATE["off"] = True
                print("  [net] keep-alive 通道探测失败，回退 urllib（含代理）",
                      flush=True)


def _host_state(h):
    with _HOST_FAIL_LOCK:
        return _HOST_FAIL.setdefault(h, {"n": 0, "until": 0.0})


def _host_cooling(h):
    """该域名是否处于冷却中（近期连续失败）。"""
    st = _host_state(h)
    return st["n"] >= HOST_FAIL_LIMIT and time.time() < st["until"]


def _mark_host(h, ok):
    """记录域名级成败：成功清零，失败累计并在达阈值后进入冷却。"""
    st = _host_state(h)
    with _HOST_FAIL_LOCK:
        if ok:
            st["n"] = 0
            st["until"] = 0.0
        else:
            st["n"] += 1
            if st["n"] >= HOST_FAIL_LIMIT:
                st["until"] = time.time() + HOST_COOLDOWN


def _conn_pool():
    pool = getattr(_TLS, "pool", None)
    if pool is None:
        pool = _TLS.pool = {}
    return pool


def _close_conn(host):
    """丢弃本线程到 host 的连接（连接已损坏 / 需要重建时调用）。"""
    c = _conn_pool().pop(host, None)
    if c is not None:
        try:
            c.close()
        except Exception:                            # noqa: BLE001
            pass


def close_connections():
    """关闭**本线程**持有的全部长连接。

    build_panel 收尾时调用一次，避免把连接挂到进程退出。
    （线程池里的 worker 线程结束时会自动回收，这里主要照顾调用线程自己。）
    """
    pool = _conn_pool()
    for h in list(pool):
        c = pool.pop(h, None)
        if c is not None:
            try:
                c.close()
            except Exception:                        # noqa: BLE001
                pass


def _host_of(url):
    return url.split("://", 1)[1] if "://" in url else url


def _keepalive_once(host, path, timeout):
    """用长连接发一次 GET，返回响应体文本；失败返回 None。

    内部对"连接被服务端关闭"做了透明重试：第一次若复用失败，
    丢弃该连接、新建一条再试一次 —— 这样 keep-alive 的固有
    RemoteDisconnected 不会冒泡成一次可见的请求失败。
    ★ 单次调用最多重建 1 次，因此最坏耗时 = 2 × timeout，
      不会因为反复重试而把整体抓取拖垮。
    """
    h = _host_of(host)
    pool = _conn_pool()
    for attempt in (0, 1):
        conn = pool.get(h)
        if conn is None:
            conn = http.client.HTTPSConnection(h, timeout=timeout,
                                               context=_ssl_ctx())
            pool[h] = conn
        try:
            conn.request("GET", path, headers=HDRS)
            r = conn.getresponse()
            body = r.read()
            if r.status != 200 or not body:
                _close_conn(h)                   # 非 200 保守丢弃连接
                return None
            return body.decode("utf-8", "ignore")
        except Exception:                        # noqa: BLE001
            _close_conn(h)                       # 坏连接 → 丢弃，下轮重建
            continue
    return None


ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
# ★ 测试/嵌入式场景会把 DATA 指到别处（如临时目录）。
#   core 下所有以 `flowdata.DATA` 为默认值的模块（store / archive / watchlist …）
#   都在**导入时**读了它，谁先导入谁就把它钉死了。因此这里留一个统一的
#   改写入入口，导入顺序就不再是隐式约束（见 set_data_dir 的说明）。
_DATA_LOCK = threading.Lock()


def set_data_dir(path):
    """统一切换数据目录（测试注入 / 运行期重定向用）。

    ★ 为什么不能直接 `flowdata.DATA = p`：
      那只是改了本模块的属性，**已经导入过的** store/archive/watchlist 里
      默认参数早就在 def 那一刻求值完毕（`data_dir or flowdata.DATA` 是
      函数体里求值的，这点没问题；但 `DATA=flowdata.DATA` 这种模块级常量
      就钉死了）。统一走这里，语义清晰，也方便后续加入其他侧效（如建目录）。
    """
    global DATA
    with _DATA_LOCK:
        DATA = path
    return DATA


def _load_hosts():
    """按优先级返回域名，**冷却中的排到最后**（但保留在列表里）。

    ★ 为什么不直接剔除：全都被限流时仍要能试一下 —— 剔除会变成
      "一个都不试、直接失败"，比慢一点更糟。冷却只是降低优先级。
    """
    with _HOST_LOCK:
        hosts = list(_HOST_ORDER)
    cooling = [h for h in hosts if _host_cooling(h)]
    if not cooling:
        return hosts
    return [h for h in hosts if h not in cooling] + cooling


def _promote_host(h):
    """把最近成功的域名提到最前，降低后续请求的失败率。"""
    with _HOST_LOCK:
        if h in _HOST_ORDER:
            _HOST_ORDER.remove(h)
            _HOST_ORDER.insert(0, h)


def _open_once(url, timeout):
    """依次尝试「直连 -> 环境代理」，任一成功即返回响应体文本。"""
    last_err = None
    for opener in _OPENERS:
        try:
            req = urllib.request.Request(url, headers=HDRS)
            with opener.open(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "ignore")
            if body:
                return body, None
        except Exception as e:                       # noqa: BLE001
            last_err = e
            continue
    return None, last_err


def _get(path_or_url, tries=3, timeout=15):
    """传路径时会自动在多域名间做故障转移；失败返回 None。

    ★ 取数路径（v1.2.1）：**优先长连接复用**（快 10 倍，见长连接池注释），
      长连接通道整体不可用时自动退回 urllib（保留环境代理兼容性）。
    """
    if path_or_url.startswith("http"):
        # 绝对 URL：保持原样，只做重试，不做域名替换
        for i in range(tries):
            body, err = _open_once(path_or_url, timeout)
            if body is not None:
                try:
                    return json.loads(body)
                except Exception:                    # noqa: BLE001
                    pass
            time.sleep(0.4 * (i + 1))
        return None

    for i in range(tries):
        for h in _load_hosts():
            body = None
            # ---- 主通道：长连接复用（快，见上方长连接池说明）
            if not _ka_off():
                body = _keepalive_once(h, path_or_url, timeout)
                _ka_note(body is not None)
            # ---- 兼容通道：仅在通道未证实可用时才回退 urllib
            #   ★ 为什么用 `not _ka_proven()` 而不是"每次失败都回退"：
            #     一旦长连接成功过，失败就基本是**主机被限流**（实测两个
            #     通道会一起失败），此时再走 urllib 只是白等 2×timeout。
            #     直接换下一个域名更快 —— 这正是 _load_hosts 的冷却排序在做的。
            if body is None and (_ka_off() or not _ka_proven()):
                body, _err = _open_once(h + path_or_url, timeout)
            _mark_host(h, body is not None)
            if body is None:
                continue
            try:
                j = json.loads(body)
            except Exception:                        # noqa: BLE001
                continue
            _promote_host(h)
            return j
        time.sleep(0.4 * (i + 1))
    return None


# ---------------------------------------------------------------- 板块清单
def list_sectors(kind="industry", page_size=100):
    """kind: industry(行业) / concept(概念)"""
    fs = "m:90+t:2" if kind == "industry" else "m:90+t:3"
    out, pn = [], 1
    while True:
        url = (f"/api/qt/clist/get?pn={pn}&pz={page_size}&po=1&np=1&fltt=2&invt=2"
               f"&fid=f62&fs={fs}&fields=f12,f14,f62")
        j = _get(url)
        if not j or not j.get("data") or not j["data"].get("diff"):
            break
        diff = j["data"]["diff"]
        for d in diff:
            out.append((f"90.{d['f12']}", d["f12"], d["f14"], kind))
        if len(diff) < page_size:
            break
        pn += 1
        if pn > 12:
            break
    return out


# ---------------------------------------------------------------- 分时资金流
# ★ 接口只返回「最近一个交易日」的数据（v1.1.0 实测）：
#   周末/节假日抓到的其实是前一交易日。这里把真实数据日期记下来，
#   让上层能把面板归到正确的交易日名下，而不是按抓取日期张冠李戴。
LAST_DATA_DATE = None


def fetch_sector_kline(secid, tries=2, timeout=8):
    """返回 [(HH:MM, 主力净流入, 小单, 中单, 大单, 超大单)]，单位：元

    ★ v1.2.1 调参：默认从 tries=3/timeout=15 收到 **tries=2/timeout=8**。
      这是**批量抓取**（~1000 个板块）的默认值，一个板块拿不到就跳过
      （build_panel 里 `if rows:` 本来就允许缺），没必要为它重试 3 次
      每次等 15 秒 —— 单个卡住的板块会占着 worker 线程，直接拖长整体耗时。
      长连接复用后单请求约 50~300ms，8 秒超时已经非常宽松。
    """
    global LAST_DATA_DATE
    url = (f"/api/qt/stock/fflow/kline/get?lmt=0&klt=1&secid={secid}"
           f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT}")
    j = _get(url, tries=tries, timeout=timeout)
    if not j or not j.get("data") or not j["data"].get("klines"):
        return []
    rows = []
    for line in j["data"]["klines"]:
        p = line.split(",")
        if len(p) < 6:
            continue
        if LAST_DATA_DATE is None and len(p[0]) >= 10:
            LAST_DATA_DATE = p[0][:10].replace("-", "")
        hm = p[0][-5:]
        try:
            rows.append((hm, float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5])))
        except ValueError:
            continue
    return rows


def fetch_index_trend(secid="1.000001"):
    """指数分时（1分钟）：[(HH:MM, 点位, 成交量)]"""
    url = (f"/api/qt/stock/trends2/get?secid={secid}&fields1=f1,f2,f3,f7"
           f"&fields2=f51,f53,f56&iscr=0&ndays=1&ut={UT}")
    j = _get(url)
    if not j or not j.get("data") or not j["data"].get("trends"):
        return []
    out = []
    for line in j["data"]["trends"]:
        p = line.split(",")
        if len(p) < 3:
            continue
        out.append((p[0][-5:], float(p[1]), float(p[2])))
    return out


# ★ 总览顶栏展示的指数（v1.1.5 用户需求：上证指数旁增加东方财富全A）。
#   全A 覆盖沪深全市场，比只看上交所更能代表"全市场"。
#   注：东财全A 的 secid 是 47.800000；「A股平均股价」是 47.800005。
INDEX_SECIDS = [
    ("sh", "1.000001", "上证指数"),
    ("all", "47.800000", "东财全A"),
]


def fetch_all_index_trends():
    """抓取全部展示指数：{"sh": [(hm,px,vol)…], "all": [...], "meta": {...}}

    单个指数失败不影响其他（返回空列表，界面按"缺"处理），
    与 HTML 版 HEADER_INDEXES 的行为一致。
    """
    out = {}
    for key, secid, label in INDEX_SECIDS:
        try:
            out[key] = fetch_index_trend(secid)
        except Exception:                                # noqa: BLE001
            out[key] = []
    out["meta"] = {k: {"secid": s, "label": lb} for k, s, lb in INDEX_SECIDS}
    return out


def fetch_spot_index(secid="1.000001"):
    url = f"/api/qt/stock/get?secid={secid}&fields=f43,f44,f45,f46,f60,f170,f169&ut={UT}&fltt=2"
    j = _get(url)
    return (j or {}).get("data") or {}


# ---------------------------------------------------------------- 批量快照
# ★★ v1.2.3 新增：高频刷新通道。
#
#   为什么需要它：原先的刷新只有一条路 —— `build_panel` 全量重抓，
#   即 **1000 个板块各发一次 kline 请求**（实测 2.0~3.5 秒）。
#   这条路的成本与"要几个数据点"完全无关：每次刷新都会把当天
#   **全部分时**重新传一遍（10:00 时是 31 个点，14:00 时是 240 个点），
#   而我们真正需要的只是**最后一分钟**那个值。
#
#   批量快照通道实测（probe_spot_api2.py）：
#     · `ulist.np/get` 一次可查 ~200~500 个 secid；
#     · 全市场 1000 个板块 = **5 次请求 / 282 ms**（对照 1000 次 / 2~3.5 秒）；
#     · 字段 f62（主力净流入，单位元）与 kline 当日累计末值**口径一致**
#       （实测 Δ 在 0.01~0.25 亿之间，属两次请求之间的正常跳动）。
#
#   因此刷新策略改为：
#     首次（或跨交易日）→ 全量 kline 建底表；
#     之后每次刷新     → 批量快照取"当前累计值"，只追加/覆盖**最后一列**。
#
#   ★ 口径诚实声明：f62 是"当日累计主力净流入"，与 kline 的 f52 同义。
#     它不是"这一分钟的增量" —— 曲线画的就是累计值，所以直接用即可。
#     若某板块在快照里缺失，保留其旧值（宁可稍旧，也不要凭空断点）。

SPOT_BATCH = 200            # 单次 ulist.np 请求的 secid 个数（实测 300~500 也行，
                            # 取 200 留足 URL 长度余量，且失败影响面小）
SPOT_FIELDS = "f12,f14,f62"

# ★★ v1.2.4：增量刷新能接的最大缺口（分钟）。
#   为什么需要它：增量通道只会往面板尾部**追加一行**（当前这一分钟）。
#   若中间隔着一段没运行的时间（关掉程序、休眠、断网），那些分钟不会被补回 ——
#   实测 2026-09-21 的会话：10:47 关掉 → 11:17 打开，面板尾部直接是
#   「10:47 → 11:17」，中间 29 分钟永久缺失；时间轴越缺越稀，曲线看着
#   像是"追不上最新数据"。
#   超过这个缺口就不再硬接，改为返回 (None, None) 触发**全量重抓** ——
#   kline 接口每次都返回当天**完整分时**（实测 119 分钟无缺口），
#   一次全量就能把洞补齐。代价是 1000 次请求（2~3.5 秒），但只在
#   "断了很久"时付一次，正常的 10/30 秒高频刷新仍走 5 次请求的快通道。
#   ★ 阈值取 6：最慢的自动刷新档位是 5 分钟，跨档时缺口恰好 5 ——
#     不能把正常档位误判成"断线"，否则 5 分钟档每次刷新都会退化成全量。
SPOT_MAX_GAP_MIN = 6


def _minute_idx(hm):
    """'HH:MM' → 当日分钟序号（0..239），与 native_app._minute_index 同口径。

    认不出来（含午休 11:31~12:59 这些**非交易分钟**）时返回 None。
    """
    try:
        h, m = str(hm).split(":")[:2]
        t = int(h) * 60 + int(m)
    except Exception:                                    # noqa: BLE001
        return None
    if 9 * 60 + 31 <= t <= 11 * 60 + 30:
        return t - (9 * 60 + 31)
    if 13 * 60 + 1 <= t <= 15 * 60:
        return 120 + (t - (13 * 60 + 1))
    if t == 9 * 60 + 30:
        return 0
    return None


def clamp_to_bar(now=None):
    """把"现在"夹到**最接近的交易分钟**，返回 'HH:MM'；盘前返回 None。

    ★★ v1.2.4：这是"时间轴被污染"的根治点。
      增量刷新原先用 `datetime.now().strftime("%H:%M")` **直接当标签追加行**，
      而自动刷新在午休/盘后也会照跑 —— 于是 11:31~11:42 这些
      **根本不存在的交易分钟**被写进了面板时间轴。
      实测 2026-09-21 的面板末尾长成了「…09:31 10:47 11:17 11:43」，
      危害有三：
        ① `_minute_index("11:43")` 返回 None → 该点的横坐标无处安放，
           曲线末端会被挤到上一个能认出的刻度上（或退回下标口径）；
        ② 缺口计算 `spot_gap_minutes` 拿到认不出的末点 → 判定失效；
        ③ 归档 / 回看 / 悬停读数全都带着一个假的时刻。
      修法：写面板前先把时间**夹到合法刻度** ——
        午休（11:31~12:59）→ 11:30；盘后（15:00 后）→ 15:00；
        盘中正常时刻原样返回；盘前（09:31 前）→ None（不该写行）。
      ★ 为什么"夹"而不是"直接拒绝写入"：午休期间用户手动点刷新是常见操作，
        直接拒绝会让面板停在 11:30 之前、连 11:30 那一分钟都补不上；
        夹到 11:30 则"午休刷新 = 把上午最后一分钟补全"，语义正确且不退步。
    """
    from datetime import datetime as _dt
    now = now or _dt.now()
    t = now.hour * 60 + now.minute
    if t < 9 * 60 + 31:
        return None                                   # 盘前：不写新行
    if t <= 11 * 60 + 30:
        return f"{t // 60:02d}:{t % 60:02d}"
    if t < 13 * 60 + 1:
        return "11:30"                                # 午休 → 上午收盘
    if t <= 15 * 60:
        return f"{t // 60:02d}:{t % 60:02d}"
    return "15:00"                                    # 盘后 → 收盘


def spot_gap_minutes(times, now=None):
    """面板最后一分钟距现在隔了几分钟（休市时段夹到边界）。取不到返回 None。

    ★ 为什么会返回 None：盘前（09:31 前）本就没有数据，"落后"无从谈起；
      时间标签认不出来时也不该乱猜 —— 返回 None 让调用方按"不判断"处理，
      而不是拿一个编出来的数字去触发全量重抓。
    ★ v1.2.4：末尾若是午休/盘后这类"非交易分钟"（历史面板被旧版本写入过），
      先回溯到**最后一个合法分钟**再算，避免整段判定直接失效。
    """
    from datetime import datetime as _dt
    if not times:
        return None
    last = None
    for x in reversed(times):                 # 回溯找最后一个可识别的交易分钟
        last = _minute_idx(x)
        if last is not None:
            break
    if last is None:
        return None
    now = now or _dt.now()
    t = now.hour * 60 + now.minute
    if t < 9 * 60 + 31:
        return None                       # 盘前：还没开盘，不算落后
    if t <= 11 * 60 + 30:
        cur = t - (9 * 60 + 31)
    elif t < 13 * 60 + 1:
        cur = 11 * 60 + 30 - (9 * 60 + 31)     # 午休夹到 11:30
    elif t <= 15 * 60:
        cur = 120 + (t - (13 * 60 + 1))
    else:
        cur = 240 - 1                     # 盘后夹到 15:00
    return max(0, cur - last)


def fetch_spot_batch(secids):
    """批量取"当日累计主力净流入"快照。返回 {secid: 净额(元)}。

    ★ 必须带 `ut=` —— 漏掉会返回 f62=None（实测：不带 ut 的 stock/get
      返回 0；ulist.np 不带 ut 直接给 None）。这是本函数唯一的坑。
    """
    out = {}
    if not secids:
        return out
    for i in range(0, len(secids), SPOT_BATCH):
        chunk = secids[i:i + SPOT_BATCH]
        ids = ",".join(chunk)
        url = (f"/api/qt/ulist.np/get?fltt=2&invt=2&fields={SPOT_FIELDS}"
               f"&secids={ids}&ut={UT}")
        j = _get(url, tries=2, timeout=8)
        diff = ((j or {}).get("data") or {}).get("diff") or []
        for d in diff:
            code = d.get("f12")
            v = d.get("f62")
            if code and isinstance(v, (int, float)):
                out[f"90.{code}"] = float(v)
    return out


def refresh_panel_spot(tag="today", workers=6, on_progress=None):
    """**增量刷新**：只把最新一分钟的值接到已有面板上（v1.2.3）。

    与 build_panel 的区别：
      build_panel        1000 次请求 / 2000~3500ms —— 拉全部分时（建底表用）
      refresh_panel_spot    5 次请求 /   ~300ms —— 只取当前累计值（高频刷新用）

    返回 (meta, mat)；任何一步失败都返回 (None, None)，由调用方决定
    是否退回全量抓取 —— **绝不半途改坏已有面板**。
    """
    import numpy as np

    def say(msg):
        if on_progress:
            try:
                on_progress(msg)
            except Exception:                            # noqa: BLE001
                pass

    p_npy = os.path.join(DATA, f"panel_{tag}.npy")
    p_json = os.path.join(DATA, f"panel_{tag}.json")
    if not (os.path.exists(p_npy) and os.path.exists(p_json)):
        return None, None                            # 没有底表 → 交给全量抓

    try:
        with open(p_json, encoding="utf-8") as f:
            meta = json.load(f)
        mat = np.load(p_npy)
    except Exception:                                # noqa: BLE001
        return None, None

    codes = meta.get("codes") or []
    times = meta.get("times") or []
    if not codes or mat.ndim != 2:
        return None, None

    # ★★ v1.2.4：缺口守卫 —— 尾部断得太多就别硬接，交回全量重抓。
    #   增量通道只会追加"当前这一分钟"，中间没运行的分钟补不回来。
    #   实测：10:47 关 → 11:17 开，面板尾部成了「10:47 → 11:17」，
    #   29 分钟的空洞永久留在时间轴上（曲线越看越稀、像追不上数据）。
    #   返回 (None, None) 后调用方（Worker.run）会自动降级为 build_panel，
    #   而 kline 每次都给当天完整分时 → 一次全量就把洞补齐。
    gap = spot_gap_minutes(times)
    if gap is not None and gap > SPOT_MAX_GAP_MIN:
        say(f"  面板落后 {gap} 分钟（> {SPOT_MAX_GAP_MIN}）→ 改走全量补齐空档")
        return None, None

    t0 = time.time()
    spot = fetch_spot_batch(codes)
    if not spot:
        return None, None                            # 一个都没取到 → 不算刷新成功

    say(f"  增量快照：{len(spot)}/{len(codes)} 个板块有值"
        f"（{time.time()-t0:.1f}s）")

    # ---- 当前分钟刻度：夹到**合法交易分钟**（v1.2.4 修复）----
    #   ★ 旧写法 `datetime.now().strftime("%H:%M")` 直接当标签，而自动刷新
    #     在午休/盘后也照跑 → 11:31~11:42 这类**不存在的交易分钟**被写进
    #     时间轴（实测面板末尾长成「…10:47 11:17 11:43」）。
    #     危害：_minute_index("11:43")=None → 横坐标无处安放、
    #     缺口判定失效、悬停读数带着假时刻。
    #   ★ 夹到合法刻度后："午休刷新" = 补全上午最后一分钟（11:30），
    #     "盘后刷新" = 补全 15:00，语义正确且不退步（详见 clamp_to_bar）。
    hm = clamp_to_bar()
    if hm is None:
        # 盘前（09:31 之前）：本就没有交易分钟可写。不追加行，
        # 但仍把已取到的快照值**覆盖到最后一行**（当作最新累计值），
        # 避免整次刷新白跑。
        hm = times[-1] if times else None
        if hm is None:
            return None, None

    # 把快照值写成新的一列/更新最后一列
    col = np.full(len(codes), np.nan)
    for j, secid in enumerate(codes):
        v = spot.get(secid)
        if v is not None:
            col[j] = v / 1e8

    # ★ 形状约定（务必记牢，见下方注释）：面板是 (时间 × 板块)
    #     某一分钟所有板块的值  = mat[t, :]   （一行，长度=板块数）
    #     某板块全部时间的值    = mat[:, j]   （一列，长度=分钟数）
    #   col 的长度是**板块数**，因此它只能与 mat 的"行"运算。
    if times and times[-1] == hm:
        # 同一分钟 → 覆盖最后一行（同一分钟内的多次刷新取最新值）
        row = mat[-1, :]
        mat[-1, :] = np.where(np.isnan(col), row, col)
    else:
        # 新的一分钟 → 追加一行。
        #   ★ 快照里缺的板块沿用**上一个时间点**的值（前向填充）。
        #   ★★ 形状陷阱（本函数连续踩了两次，都要记）：
        #      "上一分钟所有板块"是 `mat[-1, :]`（最后一行，长度=板块数），
        #      **不是** `mat[:, -1]`（最后一列，长度=分钟数）。
        #      写错通常直接抛 ValueError（999 vs 48），但**当分钟数恰好等于
        #      板块数时会静默通过**，然后把 A 板块的值贴到 B 板块上 ——
        #      那是最坏的情况：不报错、数据全错。
        #   ★★ v1.2.4 追加防倒挂：**时间轴必须单调递增**。
        #      夹到合法刻度后可能出现"新刻度 ≤ 末尾刻度"（例如午休期间
        #      末尾已是 11:30、此刻又夹出 11:30，或历史面板末尾是脏值
        #      11:43 而夹出来是 11:30）—— 此时**覆盖最后一行**，
        #      绝不追加成倒挂的时间轴（倒挂会让 x 落位与曲线错乱）。
        _newi = _minute_idx(hm)
        _lasti = _minute_idx(times[-1]) if times else None
        _back = (_newi is not None and _lasti is not None and _newi <= _lasti)
        if _back:
            row = mat[-1, :]
            mat[-1, :] = np.where(np.isnan(col), row, col)
            say(f"  刻度 {hm} 不新于末行 {times[-1]} → 覆盖末行（保持单调）")
        else:
            prev = mat[-1, :] if mat.shape[0] else np.zeros(len(codes))
            newcol = np.where(np.isnan(col), prev, col)
            mat = (np.vstack([mat, newcol]) if mat.shape[0]
                   else newcol[None, :])
            times = list(times) + [hm]

    try:
        np.save(p_npy, mat)
        meta["times"] = times
        meta["source"] = "live"
        with open(p_json, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception as e:                           # noqa: BLE001
        log_note = f"spot refresh save failed: {e}"
        print("  " + log_note, flush=True)
        return None, None

    # 指数同步走批量/轻量通道（复用 fetch_all_index_trends 的两条分时）
    try:
        idx = fetch_index_trend()
    except Exception:                                # noqa: BLE001
        idx = []
    if idx:
        try:
            with open(os.path.join(DATA, f"index_{tag}.json"), "w",
                      encoding="utf-8") as f:
                json.dump(idx, f, ensure_ascii=False)
        except Exception:                            # noqa: BLE001
            pass
    try:
        iall = fetch_index_trend("47.800000")
    except Exception:                                # noqa: BLE001
        iall = []
    if iall:
        try:
            with open(os.path.join(DATA, f"index_all_{tag}.json"), "w",
                      encoding="utf-8") as f:
                json.dump(iall, f, ensure_ascii=False)
        except Exception:                            # noqa: BLE001
            pass

    return meta, mat


# ---------------------------------------------------------------- 面板构建
def build_panel(tag="today", workers=16, limit=None, kinds=("industry", "concept"),
                on_progress=None):
    """抓取全市场板块分时资金流，落盘为宽表。

    on_progress: 可选回调 fn(msg:str)，用于向 UI 汇报进度。
    """
    # ★ 每次构建前复位「真实数据日期」（v1.1.6 修复）：
    #   LAST_DATA_DATE 是模块级全局，且 fetch_sector_kline 里只在
    #   `is None` 时写入 —— 不复位的话，**同一个进程内第二次 build_panel
    #   会沿用上一次的日期**。真实场景：用户盘中点「重新抓取」时若已跨越
    #   交易日，或在周末抓过一次后再抓，meta["data_date"] 就会是旧值，
    #   进而把面板归档到**错误的交易日名下**（污染 30 日窗口与回看列表）。
    global LAST_DATA_DATE
    LAST_DATA_DATE = None

    def say(msg):
        print(msg, flush=True)
        if on_progress:
            try:
                on_progress(msg)
            except Exception:                        # noqa: BLE001
                pass

    os.makedirs(DATA, exist_ok=True)
    # ★ v1.2.1 并行：两类板块清单（行业 / 概念）互不依赖，原先串行拉取。
    #   各自还是分页串行的（同一主机的分页顺序无并发收益），但两类之间
    #   并行能把"清单阶段"的墙钟时间大致减半。
    sectors = []
    if len(kinds) <= 1:
        for k in kinds:
            s = list_sectors(k)
            say(f"  {k}: {len(s)} 个板块")
            sectors += s
    else:
        with ThreadPoolExecutor(max_workers=len(kinds)) as lex:
            got = list(lex.map(list_sectors, kinds))
        for k, s in zip(kinds, got):
            say(f"  {k}: {len(s)} 个板块")
            sectors += s
    if limit:
        sectors = sectors[:limit]

    if not sectors:
        raise RuntimeError("板块清单为空：网络不可达或数据源接口变更。")

    t0 = time.time()
    results = {}

    def job(item):
        secid, code, name, kind = item
        return secid, name, kind, fetch_sector_kline(secid)

    # ★ v1.2.1 并行：指数分时与板块抓取**同时**开跑。
    #   原先指数是等 1000 个板块全部抓完才串行抓 2 条（上证 + 全A），
    #   凭空多出 2 次往返。它俩与板块数据毫无依赖关系，放一个后台线程
    #   并行拿，收尾时取结果即可（抓不到就按"缺"降级，不阻塞面板）。
    idx_box = {}

    def _fetch_indexes():
        try:
            idx_box["sh"] = fetch_index_trend()
        except Exception:                            # noqa: BLE001
            idx_box["sh"] = []
        try:
            idx_box["all"] = fetch_index_trend("47.800000")
        except Exception:                            # noqa: BLE001
            idx_box["all"] = []

    idx_thread = threading.Thread(target=_fetch_indexes, daemon=True)
    idx_thread.start()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (secid, name, kind, rows) in enumerate(ex.map(job, sectors), 1):
            if rows:
                results[secid] = (name, kind, rows)
            if i % 200 == 0:
                say(f"  已抓取 {i}/{len(sectors)}  （{time.time()-t0:.0f}s）")

    say(f"  完成：{len(results)}/{len(sectors)} 个板块有数据，用时 {time.time()-t0:.0f}s")

    if not results:
        raise RuntimeError("未取到任何板块数据：网络不可达或数据源接口变更。")

    # 汇总所有出现过的分钟刻度
    all_times = set()
    for secid, (name, kind, rows) in results.items():
        for hm, main, *_ in rows:
            all_times.add(hm)
    times = sorted(all_times)
    tindex = {t: i for i, t in enumerate(times)}

    # 宽表（time × sector）
    import numpy as np
    codes = list(results.keys())
    mat = np.full((len(times), len(codes)), np.nan)
    for j, secid in enumerate(codes):
        name, kind, rows = results[secid]
        for hm, main, *_ in rows:
            mat[tindex[hm], j] = main / 1e8
    # 前向填充（某些板块个别分钟缺失）
    for j in range(mat.shape[1]):
        col = mat[:, j]
        last = np.nan
        for i in range(len(col)):
            if np.isnan(col[i]):
                col[i] = last
            else:
                last = col[i]
        # ★ 开头仍是 NaN 的（该板块晚于全市场首个分钟才返回数据）必须回填 0
        #   （v1.0.5 修复）。留着 NaN 会一路传进 build_features 的排名计算：
        #   np.argsort 把 NaN 排在最后，_pct_rank 会给这类板块打**满分位**，
        #   等于「没数据」被当成「全场最强」，直接污染辨识度排序。
        #   语义上：开盘时尚无成交 = 累计净额从 0 起步。
        if np.isnan(col).any():
            np.copyto(col, 0.0, where=np.isnan(col))
        mat[:, j] = col

    np.save(os.path.join(DATA, f"panel_{tag}.npy"), mat)
    meta = {
        "times": times,
        "codes": codes,
        "names": {s: results[s][0] for s in codes},
        "kinds": {s: results[s][1] for s in codes},
        # ★ 来源标记（v1.1.0）：实盘抓取 = live；历史补全 = backfill（见 backfill.py）。
        #   回看界面据此区分「实盘」与「合成」标注，两类数据绝不混用。
        "source": "live",
        # ★ 数据的真实交易日（v1.1.0）：周末抓取时 tag 是周六，
        #   但内容属于周五 —— 用接口时间戳里的日期，不靠猜。
        "data_date": LAST_DATA_DATE or tag,
    }
    with open(os.path.join(DATA, f"panel_{tag}.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    # ★ v1.2.1：指数结果取自启动时就并行跑的那个线程（见上方 idx_thread）。
    #   这里 join 一下即可 —— 它通常早已跑完，等于零等待。
    idx_thread.join(timeout=20)
    idx = idx_box.get("sh") or []
    with open(os.path.join(DATA, f"index_{tag}.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)

    # ★ 东财全A（v1.1.5）：另存一份，与上证并列展示。
    #   抓不到就写空表，不阻塞面板构建（界面按"缺"降级）。
    idx_all = idx_box.get("all") or []
    with open(os.path.join(DATA, f"index_all_{tag}.json"), "w",
              encoding="utf-8") as f:
        json.dump(idx_all, f, ensure_ascii=False)

    # ★ v1.2.1：收尾关闭本线程的长连接（worker 线程随线程池回收，这里管调用线程）
    close_connections()

    say(f"  面板：{mat.shape[0]} 个时间点 × {mat.shape[1]} 个板块")
    return meta, mat, idx


def load_panel(tag="today"):
    import numpy as np
    # ★ with 显式关闭（v1.0.5）：原来裸 open() 交给 GC，
    #   CPython 引用计数下通常立即关，但这是实现细节而非保证。
    with open(os.path.join(DATA, f"panel_{tag}.json"), encoding="utf-8") as f:
        meta = json.load(f)
    mat = np.load(os.path.join(DATA, f"panel_{tag}.npy"))
    with open(os.path.join(DATA, f"index_{tag}.json"), encoding="utf-8") as f:
        idx = json.load(f)
    return meta, mat, idx


def load_index_all(tag):
    """东财全A 分时（v1.1.5）。文件不存在（v1.1.5 之前抓的旧面板、
    或历史合成日）时返回 []，界面按"缺"降级而不是报错。"""
    try:
        with open(os.path.join(DATA, f"index_all_{tag}.json"),
                  encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                    # noqa: BLE001
        return []


if __name__ == "__main__":
    import sys
    tag = sys.argv[1] if len(sys.argv) > 1 else "today"
    print("构建板块分时资金流面板 ...")
    meta, mat, idx = build_panel(tag)
    print("时间点示例:", meta["times"][:3], "...", meta["times"][-3:])
    print("指数分时点数:", len(idx))
