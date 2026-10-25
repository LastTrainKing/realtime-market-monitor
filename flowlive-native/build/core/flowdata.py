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

import json, os, random, time, threading, urllib.error, urllib.parse, urllib.request
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

# 写入同一交易日面板时，必须在进程内串行化：面板由 json + npy 两个文件共同表达，
# 单文件原子替换不能阻止两个写者交错提交。锁粒度按绝对数据目录与 tag 隔离，
# 不阻塞不同交易日或不同测试目录的正常并发。
_PANEL_LOCK_GUARD = threading.Lock()
_PANEL_LOCKS = {}
_SECTORS_LOCK = threading.RLock()


def _panel_lock(tag, data_dir=None):
    key = (os.path.abspath(data_dir or DATA), str(tag))
    with _PANEL_LOCK_GUARD:
        return _PANEL_LOCKS.setdefault(key, threading.RLock())


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
_KA_STATE = {"ok": 0, "fail": 0, "off": False, "proven": False,
             "off_until": 0.0, "backoff": 0.0}
_KA_LOCK = threading.Lock()
KA_PROBE_LIMIT = 6              # 未证明可用前，连试这么多次就**暂时**关闭快通道

# ★★ v3.0.6 延迟修复（RC-1）：**快通道的关闭必须是可恢复的**。
#
#   【旧行为的致命缺陷】`_KA_STATE["off"] = True` 一旦置位，全文件**没有任何
#   复位点**（v3.0.5 及以前都是如此）。而置位条件是"通道尚未成功过时连续失败
#   6 次"—— 这恰好命中**进程启动初期**：盘中启动若撞上一次限流窗口
#   （2026-09-23 13:52 实测复现，push2 连续 RemoteDisconnected），
#   6 次失败在 ~2 秒内攒满 → 此后**整个进程生命周期**都退回 urllib
#   逐请求新建 TCP+TLS（实测 550ms/请求，而复用只需 53ms）→ **10× 静默退化**，
#   界面上只表现为"数据加载很慢"，没有任何提示。
#
#   实测证据（_probe_lat3.py）：
#     第 1 次 list_sectors：KA×3 + UR×3 全失败 → ka_off=True proven=False
#     第 2 次 list_sectors：明细里**再没有一条 KA**，全部走 UR ——
#     即接口/网络恢复后，快通道也不会回来。
#
#   【修法】把"关"从**布尔**改成**带截止时间的软关闭 + 自动重探**：
#     · 触发关闭时记 `off_until = now + KA_RETRY_AFTER`；
#     · `_ka_off()` 到期后**放行一次**重探（半开），并立刻把窗口顺延
#       KA_PROBE_TIMEOUT 秒，保证高并发下同一瞬间只有一个线程去探，
#       不会几十个线程一起冲进去把限流打得更狠；
#     · 重探成功 → 立刻回到快通道（`proven=True`、退避复位）；
#       重探失败 → 退避**翻倍**（15 → 30 → 60 → 120s 封顶）。
#   这样"接口暂时不可用"只让快通道退避一小会儿，而不是永久作废。
KA_RETRY_AFTER = 15.0           # 软关闭后多久允许第一次重探
KA_RETRY_MAX = 120.0            # 重探间隔退避上限
KA_FAIL_LIMIT_PROVEN = 30       # 已证实可用后，连续失败到此值才怀疑通道本身坏了

# ★★ v3.0.3：**探测期用短超时**，避免"接口不可用时启动程序"白等。
#
#   实测（2026-09-23 01:52，东财接口关闭期间）：
#     _keepalive_once('push2.eastmoney.com', ...) 单次耗时 **530ms** 即返回失败——
#     是**服务端快速拒绝**，不是沉默超时。6 次探测累计约 **3.2 秒**。
#   这 3.2 秒出现在"盘前/盘后启动程序"的场景，表现为开屏变慢。
#
#   为什么"探测期"可以安全用更短的超时：
#     · 通道**未证实可用**时，我们本来就在做"能不能用"的判断，
#       用 15s 等一个很可能失败的连接，性价比极低；
#     · 一旦某次成功（proven=True），后续请求立刻恢复用调用方给的完整
#       timeout —— 也就是说**正常盘中的超时语义一字未改**，
#       改动只作用于"通道还没证明好用"的那几次尝试。
#     · 短超时只会让"判通道不可用"更快，代价是极端慢网络下可能误判。
#       兜底：`_ka_off()` 只是不再优先走长连接，仍会走 urllib 通道，
#       功能不受影响（只是慢），不会取不到数据。
KA_PROBE_TIMEOUT = 3.0


def _ka_timeout(full_timeout):
    """探测期返回较短超时；通道已证实可用则用调用方给的完整超时。"""
    if _ka_proven():
        return full_timeout
    try:
        return min(float(full_timeout), KA_PROBE_TIMEOUT)
    except Exception:                                    # noqa: BLE001
        return KA_PROBE_TIMEOUT

_HOST_FAIL = {}                 # {host: {...}} —— 名字保留（诊断脚本在用）
_HOST_FAIL_LOCK = threading.Lock()
HOST_FAIL_LIMIT = 3             # ★ 保留旧名（源码守卫引用）；实际判定见下
HOST_COOLDOWN = 60.0            # ★ 保留旧名；实际冷却基数见 HOST_COOLDOWN_BASE

# ★★ v3.0.6 延迟修复（RC-2 / RC-3）：域名健康度改用**滑动窗口失败率**判定。
#
#   【旧行为的缺陷】判据是"连续失败 3 次"（HOST_FAIL_LIMIT = 3）。
#   在 24 并发下，3 次瞬时失败在**毫秒级**就攒满 —— 一次正常的网络抖动
#   就能让主域进入 60 秒冷却。更糟的是冷却期内每再失败一次就
#   `until = now + 60`，**冷却永不到期**；而冷却中的域名仍留在轮转里
#   （排最后），于是持续打空枪。
#
#   实测（2026-09-23 盘中，两组相隔 7 分钟的观测）：
#     · 13:51 → 主域 push2 **通**（280ms），备用域 push2delay **全拒**（129ms 快速拒）；
#     · 13:58 → 主域 push2 **全拒**，备用域 push2delay **通**（386ms，176 条 klines）。
#     结论：**两个域名的限流彼此独立**，各自"服务一阵、拒绝一阵"。
#     这带来两个后果：① 负载要**摊到两个域名上**，否则更快触发单域阈值；
#     ② 一个域名正处于拒绝窗口时，请求不该在它身上多耗一次白跑往返。
#
#   【修法】
#     · 判据换成「HOST_WINDOW 秒窗口内、样本数 ≥ HOST_MIN_SAMPLES、
#       且失败率 ≥ HOST_FAIL_RATIO」—— 高并发下的瞬时抖动不再成片触发；
#     · 冷却用**指数退避**（30 → 60 → 120 → 240s 封顶），成功一次立即清零；
#     · 从没成功过、且失败率 ≥ HOST_NEVER_OK_RATIO 的域名，直接**隔离**
#       HOST_DEAD_RETRY 秒，不再参与每轮重试；
#     · `_load_hosts()` 只要还有**可用**域名，就**不再尝试冷却中的域名**
#       —— 旧实现把冷却域名"排最后"，等于每个请求都还要在它身上白跑一次。
#       ★ 顺序本身仍按"最近成功优先"的**粘性**策略，**不做轮转**：
#         实测轮转反而慢 12×（把一半流量首先推给了更差的备用域名），
#         详见 `_load_hosts` 的实测记录。
HOST_WINDOW = 20.0              # 健康度统计窗口（秒）
HOST_MIN_SAMPLES = 8            # 窗口内样本不足此数不做熔断判定
HOST_FAIL_RATIO = 0.55          # 窗口失败率达到此值 → 冷却
HOST_NEVER_OK_RATIO = 0.8       # 从未成功过 + 失败率达此值 → 直接隔离
HOST_COOLDOWN_BASE = 30.0       # 首次冷却秒数
HOST_COOLDOWN_MAX = 240.0       # 冷却退避上限
HOST_DEAD_RETRY = 120.0         # 隔离后多久再放一次探测


def _ka_off():
    """快通道当前是否不可用。

    ★ v3.0.6：不再是"永久布尔"。软关闭到期后**放行一次重探**，并把窗口
      顺延 KA_PROBE_TIMEOUT 秒 —— 于是"重探"天然是**单个线程**的，
      高并发下不会几十个线程同时冲进限流窗口。
    """
    with _KA_LOCK:
        st = _KA_STATE
        if not st["off"]:
            return False
        now = time.time()
        if now < st["off_until"]:
            return True
        # 到期 → 半开：放这一个线程去探，其余线程在窗口内继续走 urllib
        st["off_until"] = now + KA_PROBE_TIMEOUT
        return False


def _ka_proven():
    """长连接通道是否**已经成功过**（成功过就不再回退 urllib，省时间）。"""
    with _KA_LOCK:
        return _KA_STATE["proven"]


def _ka_note(ok):
    """记录一次长连接结果，并按退避策略管理快通道的软关闭 / 恢复。

    ★ v3.0.6 关键改动：`off` 可被**重探成功**复位。旧实现只置位不复位，
      导致"启动初期撞上限流"= 整个进程永久慢 10 倍。
    """
    with _KA_LOCK:
        st = _KA_STATE
        if ok:
            was_off = st["off"]
            st["ok"] += 1
            st["proven"] = True
            st["off"] = False
            st["off_until"] = 0.0
            st["fail"] = 0
            st["backoff"] = 0.0
            if was_off:
                print("  [net] keep-alive 通道恢复（重探成功），已回到快速通道",
                      flush=True)
            return

        st["fail"] += 1
        limit = KA_PROBE_LIMIT if not st["proven"] else KA_FAIL_LIMIT_PROVEN
        if st["fail"] < limit:
            return
        # ★ 计数清零：让**退避**（而不是累积计数）决定下一次重探时机。
        #   否则"连续失败 30 次"这种口径在限流期间会一直满足，
        #   退避就永远走不到下一档。
        st["fail"] = 0
        if not st["off"]:
            st["off"] = True
            st["backoff"] = KA_RETRY_AFTER
            print(f"  [net] keep-alive 通道暂不可用，退回 urllib（含代理）；"
                  f"{KA_RETRY_AFTER:.0f}s 后自动重探", flush=True)
        else:
            st["backoff"] = min((st["backoff"] or KA_RETRY_AFTER) * 2.0,
                                KA_RETRY_MAX)
        st["off_until"] = time.time() + st["backoff"]


def _host_state(h):
    with _HOST_FAIL_LOCK:
        return _HOST_FAIL.setdefault(
            h, {"n": 0, "until": 0.0, "samples": [], "ok": 0, "fail": 0,
                "round": 0, "dead_until": 0.0})


def _host_cooling(h):
    """该域名当前是否**不可用**（冷却中 / 已隔离）。

    ★ 语义从"连续失败"换成"滑动窗口失败率"，见上方说明。
    """
    st = _host_state(h)
    now = time.time()
    with _HOST_FAIL_LOCK:
        return now < st["until"] or now < st["dead_until"]


def _host_dead(h):
    """是否处于"隔离"状态（本进程内从未成功过，暂时不参与常规轮转）。"""
    st = _host_state(h)
    with _HOST_FAIL_LOCK:
        return time.time() < st["dead_until"]


def _mark_host(h, ok):
    """记录域名级成败，按滑动窗口失败率决定冷却 / 隔离 / 恢复。

    ★ 关键点（都是 v3.0.6 明确修的）：
      ① 判定用**窗口失败率**而非连续计数 —— 并发高时不再被瞬时抖动误伤；
      ② 冷却期内**不再延长**冷却（旧实现会 `until = now + 60` 无限续期）；
      ③ 成功一次**立即**清空冷却、退避档位与隔离状态。
    """
    st = _host_state(h)
    now = time.time()
    with _HOST_FAIL_LOCK:
        smp = st["samples"]
        smp.append((now, bool(ok)))
        cut = now - HOST_WINDOW
        i = 0
        while i < len(smp) and smp[i][0] < cut:
            i += 1
        if i:
            del smp[:i]

        if ok:
            st["n"] = 0
            st["ok"] += 1
            st["until"] = 0.0
            st["round"] = 0
            st["dead_until"] = 0.0
            return

        st["n"] += 1
        st["fail"] += 1
        if now < st["until"] or now < st["dead_until"]:
            return                       # ② 已在冷却/隔离期，不重复判定、不续期

        n = len(smp)
        if n < HOST_MIN_SAMPLES:
            return
        fails = sum(1 for _t, o in smp if not o)
        ratio = fails / float(n)
        if st["round"] == 0 and ratio < HOST_FAIL_RATIO:
            return

        if st["ok"] == 0 and ratio >= HOST_NEVER_OK_RATIO:
            st["dead_until"] = now + HOST_DEAD_RETRY
            st["round"] += 1
            print(f"  [net] 域名 {h.split('//')[-1]} 从未成功且失败率 "
                  f"{ratio:.0%}，隔离 {HOST_DEAD_RETRY:.0f}s", flush=True)
            return

        st["round"] += 1
        st["until"] = now + min(
            HOST_COOLDOWN_BASE * (2 ** (st["round"] - 1)), HOST_COOLDOWN_MAX)


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


# ---------------------------------------------------------------- 面板原子落盘
# ★★ v3.0.5：面板由 **两个文件** 共同表达（json 的时间标签 + npy 的数值矩阵），
#   二者**必须同版本**才有一致语义。旧写法是「先 np.save 再 json.dump」两次裸写，
#   读者（UI 刷新线程）可能正好夹在中间 → 拿到「新矩阵 + 旧标签」的跨版本组合。
#   实测组合示例：panel_20260922.npy(240行) + panel_20260923.json(50个times)
#   —— 列口径一致、**完全不报错**，但曲线会被拉到 15:00 变成假横线。
#
#   修法分两步（缺一不可，两者都留着）：
#     ① 写侧：**先写 json、再写 npy**，且都走「临时文件 + os.replace」原子替换。
#        os.replace 在同分区上是原子的（POSIX rename / Win32 MoveFileEx），
#        读者要么看到旧文件、要么看到新文件，**不会看到半截内容**。
#        ★ 顺序为什么是「先 json 后 npy」：读者先读 json(times)、后读 npy(矩阵)。
#          按此顺序写，读者最坏拿到「新 times + 旧矩阵」—— 矩阵更短，
#          渲染层按 `min(len(times), len(s))` 取用只会**少画几个尾点**（无害）；
#          反之（旧 times + 新矩阵）才会多出无标签的点 → 正是那条假横线。
#          让**可能的不一致方向偏向"少画"而非"多画"**，是所有渲染代码的安全侧。
#     ② 读侧：`load_panel` 的对齐守卫（见其 docstring）—— 兜住人手改动、
#        旧版本遗留文件、以及跨进程写入。
def _atomic_write_bytes(path, data):
    """临时文件 + os.replace 原子替换（同目录，保证同分区）。"""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    # 同一进程也可能有多个刷新线程；仅用 PID 会让它们争用同一个临时文件。
    tmp = f"{path}.tmp{os.getpid()}.{threading.get_ident()}.{time.time_ns()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())          # 落盘再改名，避免断电留下空文件
        except Exception:                 # noqa: BLE001
            pass
    os.replace(tmp, path)


def _atomic_write_json(path, obj):
    _atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False)
                        .encode("utf-8"))


def _atomic_save_npy(path, arr):
    """numpy 矩阵原子落盘（先写 .tmp 再 replace，避免读者读到半截 npy）。"""
    import io
    import numpy as np
    buf = io.BytesIO()
    np.save(buf, arr)
    _atomic_write_bytes(path, buf.getvalue())


def save_panel(tag, meta, mat, data_dir=None):
    """保存一个面板快照（先 json 后 npy）。

    每个 ``(data_dir, tag)`` 在同一进程内由同一把锁保护，避免两个刷新任务
    交错成「A 的 json + B 的 npy」。跨进程仍由读侧对齐守卫兜底；部署版
    正常运行时单实例锁已保证只有一个写进程。
    """
    root = data_dir or DATA
    with _panel_lock(tag, root):
        _atomic_write_json(os.path.join(root, f"panel_{tag}.json"), meta)
        _atomic_save_npy(os.path.join(root, f"panel_{tag}.npy"), mat)


def _load_hosts():
    """按优先级返回**本轮值得尝试**的域名。

    ★★ v3.0.6 语义变更：冷却中的域名从「排最后」改成「先不用」。
      旧实现是"冷却中的**排到最后**"—— 也就是**每个请求仍会把它试一遍**。
      新实现：只要还有**可用**域名，就**只返回可用的**；全部不可用时
      退化**只探一个**（半开探测，见下方实测说明），
      保证不会出现"一个都不试、直接失败"。
      已隔离的域名（`_host_dead`）在"还有别的选择"时一律不返回。

    ★★★ 实测记录（务必保留，避免后人重犯）：
      本版**曾经**在可用域名之间做"轮转"（想让两个域名的独立限流阈值
      都更晚触发），**实测证明这是错的，已回退**：
        · 同一时段、同一 limit=150 的 A/B（2026-09-23 14:05）——
          粘性优先（本实现）：**3.5s**，150/150，172 次尝试、20 次失败；
          轮转版本        ：**42.8s**，148/150，295 次尝试、140 次失败，
                            105 次冷却命中、21.2s 纯重试等待。
        · 原因：两个域名的**质量并不对等**。主域 push2 在被限流之外基本都能
          服务；备用域 push2delay 则经常拒绝。轮转把约一半流量**首先**推给
          更差的那个，于是每个请求平均要失败 1 次才落到好域名上 ——
          额外往返 + 重试停顿，比"省下来的那点限流风险"贵得多。
      → 结论：**按最近成功优先的粘性策略**才是对的（`_promote_host` 维护
        `_HOST_ORDER`，本函数只负责把不可用的摘出去）。
        粘性 + 冷却剔除的组合，在"主域被限流"时仍能整体切到备用域，
        在"主域正常"时则完全不碰备用域 —— 两种情形都是最优。
    """
    with _HOST_LOCK:
        hosts = list(_HOST_ORDER)
    if not hosts:
        return hosts
    alive, cooling, dead = [], [], []
    for h in hosts:
        if _host_dead(h):
            dead.append(h)
        elif _host_cooling(h):
            cooling.append(h)
        else:
            alive.append(h)
    if alive:
        return alive
    # ★★ v3.0.6 追加：**没有任何域名可用时，只探一个**，不把全部不可用域名都打一遍。
    #
    #   实测（2026-09-23 16:55，东财全站对本机 IP 阻断期间）：
    #     `build_panel(limit=100, deadline=60)` 首轮耗时 **64s**，其中
    #     **urllib 尝试 224 次** —— 因为两个域名都已隔离，而本函数仍返回两个，
    #     于是每个板块的每次尝试都要在它们身上各跑一遍（各自 ~130ms 拒绝
    #     + urllib 新建 TLS ~300ms），全是白跑。
    #
    #   "隔离/冷却"的语义本来就该是**半开探测**：放**一个**请求去试，
    #   成功了就恢复（`_mark_host(ok=True)` 会清掉隔离并 `_promote_host`），
    #   失败了继续按退避等待 —— 而不是把每个域名都当成可用的打。
    #   这样"接口全死"场景的无效尝试数直接减半。
    if cooling:
        return cooling[:1]
    return (dead or hosts)[:1]


def _promote_host(h):
    """把最近成功的域名提到最前，降低后续请求的失败率。"""
    with _HOST_LOCK:
        if h in _HOST_ORDER:
            _HOST_ORDER.remove(h)
            _HOST_ORDER.insert(0, h)


# ---------------------------------------------------------------- 兼容通道健康度
# 与快通道（keep-alive）同一套"软关闭 + 指数退避重探"纪律，见 `_open_once` docstring。
#
# ★ 两条通道**分档**（重要，第一版一刀切踩到了）：
#   实测发现"直连 242ms 快速拒绝 / 兼容通道 2048ms"之后，我第一版给两条通道
#   用了同一个阈值（连续失败 3 次就软关）。结果**连直连也被关掉了** ——
#   而直连失败只要 242ms，关掉它没有收益，反而在接口短暂抖动时
#   （3 次失败在 10 并发下是毫秒级攒满的）让主通道白白停摆 15 秒。
#   所以按通道分档：
#     · 第 0 条（**直连**）：主通道，失败便宜 → 阈值高（20）、退避短（5s）。
#       它只在"防火墙静默丢包"这类**持续**故障下才会被关。
#     · 第 1 条及之后（**兼容 / 系统代理**）：后备通道，失败昂贵 → 阈值低（3）、
#       退避 15→120s。这正是那 2 秒的来源，值得早关、关久一点。
OPENER_RULES = ((20, 5.0), (3, 15.0))   # 按 _OPENERS 顺序：(失败阈值, 首次退避)
OPENER_RETRY_MAX = 120.0                # 退避上限
_OPENER_STATE = [{"fail": 0, "off_until": 0.0, "backoff": 0.0, "ok": 0}
                 for _ in _OPENERS]
_OPENER_LOCK = threading.Lock()


def _opener_rule(i):
    return OPENER_RULES[i] if i < len(OPENER_RULES) else OPENER_RULES[-1]


def _opener_usable(i):
    """第 i 条兼容通道当前是否可用；软关闭到期时**放行一次**重探。"""
    with _OPENER_LOCK:
        st = _OPENER_STATE[i]
        if not st["off_until"]:
            return True
        now = time.time()
        if now < st["off_until"]:
            return False
        # 到期 → 半开：放一个请求去探；同时**先把退避翻倍并顺延**，
        # 这样"再失败"时不会几秒后又来一次（失败路径不会重复续期）。
        st["backoff"] = min(max(st["backoff"], _opener_rule(i)[1]) * 2.0,
                            OPENER_RETRY_MAX)
        st["off_until"] = now + st["backoff"]
        return True


def _opener_note(i, ok):
    """记录第 i 条兼容通道的成败。"""
    with _OPENER_LOCK:
        st = _OPENER_STATE[i]
        now = time.time()
        if ok:
            st["fail"] = 0
            st["off_until"] = 0.0
            st["backoff"] = 0.0
            st["ok"] += 1
            return
        st["fail"] += 1
        if st["off_until"] and now < st["off_until"]:
            return                      # 冷却/半开期内不重复判定、不续期
        limit, base = _opener_rule(i)
        if st["fail"] < limit:
            return
        st["fail"] = 0
        st["backoff"] = base if not st["backoff"] else min(
            st["backoff"] * 2.0, OPENER_RETRY_MAX)
        st["off_until"] = now + st["backoff"]


def _opener_reset():
    """复位兼容通道健康度（测试用）。"""
    with _OPENER_LOCK:
        for st in _OPENER_STATE:
            st.update(fail=0, off_until=0.0, backoff=0.0, ok=0)


def _open_once(url, timeout):
    """依次尝试「直连 -> 环境/系统代理」，任一成功即返回响应体文本。

    ★★ v3.0.6 实测修复（这是"接口不可用时特别慢"的**真正元凶**）：
      实测（2026-09-23 17:00，东财对本机 IP 阻断期间）`_open_once` **单次 2313ms**：
        · 直连 opener        → RemoteDisconnected，**242ms**（服务端快速拒绝）
        · "环境代理" opener  → URLError，**2048ms**
      而且 `env | grep -i proxy` 显示**没有任何代理环境变量**。那 2 秒来自
      `urllib.request.build_opener()` 会去读 **Windows 系统代理**（注册表
      `Internet Settings`）——本机系统里配了一个不可用的代理，于是兼容通道
      每次都要在它身上等满。
      【代价有多大】"接口全死"场景实测 `build_panel(limit=100)` 首轮 64s 里，
      urllib 尝试 147 次 × 2.3s ≈ **338 线程·秒**，是那 60s 墙钟的主要来源。
      也就是说：**接口不可用时，程序有近一半时间是在等一个用不上的代理。**

    【修法】给兼容通道加上与快通道**同一套纪律**：连续失败 N 次 → 软关闭 +
    指数退避重探（15→30→60→120s 封顶），到期只放**一个**请求去探。
      · 代理专网环境不受影响：那种环境下直连恒失败、代理恒成功，
        成功会立刻把计数与退避清零，永远不会被关掉；
      · 本机这种"配了个死代理"的情况：3 次失败后 15s 内不再碰它，
        单次 `_open_once` 从 2313ms 降到 **242ms（约 9.6×）**。
    """
    last_err = None
    for i, opener in enumerate(_OPENERS):
        if not _opener_usable(i):
            continue
        try:
            req = urllib.request.Request(url, headers=HDRS)
            with opener.open(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "ignore")
            if body:
                _opener_note(i, True)
                return body, None
            _opener_note(i, False)
        except Exception as e:                       # noqa: BLE001
            last_err = e
            _opener_note(i, False)
            continue
    return None, last_err


# ---------------------------------------------------------------- 并发闸门（v3.0.6）
# ★★ v3.0.6（RC-5）：为什么需要它 ——
#   旧实现 `build_panel(workers=24)` 是**固定 24 并发**打 1000 次请求。
#   24 并发对 push2 属于"突发高并发"，正是它自己注释里说的"对突发并发敏感"
#   的情形；而 `fetch_spot_batch` 那边却只有 5~6 —— 两个口径不一致。
#   固定并发还有个更根本的问题：网络好时它太保守、网络差时又太激进，
#   而**我们事先并不知道**当下属于哪一种。
#
#   改成 AIMD（乘性减 / 加性增），与 TCP 拥塞控制同一套思路：
#     · 失败率 > NET_FAIL_HIGH → 并发**减半**（立刻降压，避免限流雪崩）；
#     · 失败率 < NET_FAIL_LOW  → 并发 **+1**（慢慢试探上限）；
#     · 中间区间保持不动（避免来回抖动）。
#   它会在不触发限流的前提下，自己爬到当前网络能承受的并发。
#
#   ★ 线程池用 NET_CONCURRENCY_MAX 个 worker，真正的"在途请求数"由本闸门
#     控制 —— 于是**降档立即生效**（在途的跑完就不再放新的进来），
#     不需要重建线程池，也不用改调用方签名。
NET_CONCURRENCY_DEFAULT = 10    # 全量建表的默认并发（替代旧的固定 24）
NET_CONCURRENCY_MIN = 3
NET_CONCURRENCY_MAX = 24
NET_ADJUST_SAMPLES = 24         # 每累计这么多次请求重新评估一次并发档位
NET_FAIL_HIGH = 0.15            # 失败率高于此 → 并发减半
NET_FAIL_LOW = 0.02             # 失败率低于此 → 并发 +1（缓慢加）


class _AdaptiveGate:
    """可**运行期调整上限**的并发闸门 + AIMD 自适应。

    · `hard_max` 是调用方给的硬上限（例如 `build_panel(workers=N)` 的 N）；
    · `limit` 是 AIMD 在当前硬上限内自己爬到的值，真正约束在途请求数。
    """

    def __init__(self, limit, hard_max=NET_CONCURRENCY_MAX):
        self._cond = threading.Condition()
        self._hard_max = max(1, int(hard_max))
        self._limit = max(1, min(self._hard_max, int(limit)))
        self._active = 0
        self._peak = self._limit
        self._ok = 0
        self._fail = 0
        self._down = 0
        self._up = 0

    # ---- 闸门本体 ----
    def acquire(self):
        with self._cond:
            while self._active >= self._limit:
                self._cond.wait()
            self._active += 1
            if self._active > self._peak:
                self._peak = self._active

    def release(self):
        with self._cond:
            self._active -= 1
            self._cond.notify()

    @property
    def limit(self):
        with self._cond:
            return self._limit

    @property
    def hard_max(self):
        with self._cond:
            return self._hard_max

    def set_hard_max(self, n):
        """设定硬上限（调用方显式要求的上限），并把当前档位压到上限内。"""
        with self._cond:
            self._hard_max = max(1, int(n))
            if self._limit > self._hard_max:
                self._limit = self._hard_max
            self._ok = self._fail = 0
            self._cond.notify_all()

    def set_limit(self, n):
        with self._cond:
            self._limit = max(1, min(self._hard_max, int(n)))
            self._cond.notify_all()

    def stats(self):
        with self._cond:
            return {"limit": self._limit, "hard_max": self._hard_max,
                    "active": self._active, "peak": self._peak,
                    "down": self._down, "up": self._up,
                    "ok": self._ok, "fail": self._fail}

    def reset(self, hard_max=None):
        with self._cond:
            if hard_max is not None:
                self._hard_max = max(1, int(hard_max))
            self._limit = max(1, min(self._hard_max, NET_CONCURRENCY_DEFAULT))
            self._ok = self._fail = self._down = self._up = 0
            self._peak = self._limit
            self._cond.notify_all()

    # ---- AIMD ----
    def note(self, ok):
        """记录一次请求结果，累计够样本就调档。"""
        with self._cond:
            if ok:
                self._ok += 1
            else:
                self._fail += 1
            total = self._ok + self._fail
            if total < NET_ADJUST_SAMPLES:
                return
            ratio = self._fail / float(total)
            self._ok = self._fail = 0
            floor = max(1, min(NET_CONCURRENCY_MIN, self._hard_max))
            if ratio > NET_FAIL_HIGH:
                new = max(floor, self._limit // 2)
                self._down += 1
            elif ratio < NET_FAIL_LOW:
                new = min(self._hard_max, self._limit + 1)
                self._up += 1
            else:
                return
            if new != self._limit:
                print(f"  [net] 并发自适应 {self._limit} → {new}"
                      f"（近 {total} 次失败率 {ratio:.0%}）", flush=True)
                self._limit = new
                self._cond.notify_all()


_NET_GATE = _AdaptiveGate(NET_CONCURRENCY_DEFAULT)

# ---------------------------------------------------------------- 全局熔断（v3.0.6）
# ★★ 为什么需要（这是 A/B 实测里**最大的一笔剩余开销**）：
#   2026-09-23 14:10 的交替 A/B 中，新版单轮 70.0s 里有 **30.8s（44%）**
#   是纯重试等待（`_retry_pause` 的累加），对应约 154 次"整轮失败"的 `_get`。
#   成因：接口处于重度限流时，每个板块都要
#     「域名A 失败 → 域名B 失败 → 睡 0.2s → 域名A 失败 → 域名B 失败 → 放弃」
#   才结束；1000 个板块就是上千次这种空转，而成功率并不会因此变好。
#
# ★ 设计上刻意**只掐重试、不掐首次尝试**：
#   熔断打开时，每个板块仍然发**一次**请求（保留"万一是瞬时限流、这一下正好
#   放行"的机会），只是不再做第 2、3 次重试、也不再睡重试间隔。
#   这样既砍掉了空转的长尾，又不会因为熔断本身把取数率压到 0
#   —— "熔断反而让面板彻底没数据"是这类机制最容易踩的坑，这里明确规避。
BREAKER_WINDOW = 10.0           # 失败率统计窗口（秒）
BREAKER_MIN_SAMPLES = 12        # 窗口内样本不足此数不判定
BREAKER_FAIL_RATIO = 0.85       # 窗口失败率达到此值 → 打开
BREAKER_COOLDOWN = 4.0          # 打开后持续多久（期间只发首次尝试）
# ★★ v3.0.6 追加：**升级式熔断**。
#   实测（2026-09-23 17:05，东财全站对本机 IP 阻断期间）：
#     只做"轻熔断"时，`build_panel(limit=100, deadline=60)` 首轮 6s 就失败完了，
#     但**串行补抓又跑了 34s**（100 个板块 × 约 340ms），总计 47s ——
#     明知接口全死，还在一个板块一个板块地敲，全是白费。
#   更糟的是：**对已经封禁我们的服务持续猛打，只会让封禁更久**。
#   所以加一档"硬熔断"：连续 BREAKER_HARD_TRIPS 次打开熔断、
#   且期间**没有任何一次成功**（成功即清零）→ 升级为硬熔断，
#   冷却期放大 BREAKER_HARD_FACTOR 倍，期间**连首次尝试都不发**。
#   到期后仍会放一个请求去探（不会永久静默），探通了立刻回到正常。
BREAKER_HARD_TRIPS = 5          # 连续打开这么多轮且零成功 → 升级
BREAKER_HARD_FACTOR = 3.0       # 硬熔断冷却倍数

_BREAKER = {"samples": [], "until": 0.0, "trips": 0, "hard": False}
_BREAKER_LOCK = threading.Lock()


def _breaker_open():
    """熔断是否打开（打开时只发首次尝试，不做重试）。"""
    with _BREAKER_LOCK:
        return time.time() < _BREAKER["until"]


def _breaker_hard():
    """是否处于**硬熔断**（连首次尝试也不发，等冷却到期再探）。"""
    with _BREAKER_LOCK:
        return bool(_BREAKER["hard"]) and time.time() < _BREAKER["until"]


def _breaker_note(ok):
    """记录一次请求结果；窗口内失败率过高则打开熔断，连续多轮则升级为硬熔断。"""
    now = time.time()
    with _BREAKER_LOCK:
        st = _BREAKER
        smp = st["samples"]
        smp.append((now, bool(ok)))
        cut = now - BREAKER_WINDOW
        i = 0
        while i < len(smp) and smp[i][0] < cut:
            i += 1
        if i:
            del smp[:i]
        if ok:
            # ★ 任何一次成功都**立刻**把升级档位清零（恢复正常）
            st["trips"] = 0
            st["hard"] = False
            return
        n = len(smp)
        if n < BREAKER_MIN_SAMPLES:
            return
        fails = sum(1 for _t, o in smp if not o)
        ratio = fails / float(n)
        if ratio < BREAKER_FAIL_RATIO or now < st["until"]:
            return
        st["trips"] += 1
        cd = BREAKER_COOLDOWN
        if st["trips"] >= BREAKER_HARD_TRIPS and not st["hard"]:
            st["hard"] = True
            cd *= BREAKER_HARD_FACTOR
            print(f"  [net] 全局熔断升级为**硬熔断**：连续 {st['trips']} 轮零成功，"
                  f"{cd:.0f}s 内不再发任何请求（含首次尝试）", flush=True)
        elif st["hard"]:
            cd *= BREAKER_HARD_FACTOR
        st["until"] = now + cd
        if not st["hard"]:
            print(f"  [net] 全局熔断：近 {n} 次失败率 {ratio:.0%}，"
                  f"{cd:.0f}s 内只发首次尝试（不再重试）", flush=True)
        smp.clear()


def _breaker_reset():
    """复位熔断状态（测试用）。"""
    with _BREAKER_LOCK:
        _BREAKER["samples"] = []
        _BREAKER["until"] = 0.0
        _BREAKER["trips"] = 0
        _BREAKER["hard"] = False

# ★ 全量建表的全局时限（秒）。0 表示不限制（仅测试用）。
#   实测教训：旧实现无上限，撞上持续限流时 1000 个板块会一个个重试到
#   天荒地老（2026-09-23 实测 2m42s 才到 800/1000，且还在继续）。
BUILD_DEADLINE = 180.0
# ★ 串行补抓的预算（秒）。补抓是"低并发挽回"手段，本身也必须有时限，
#   否则它会把首轮砍掉的长尾原样长回来。
BACKFILL_DEADLINE = 45.0
# ★ 面板"不降级"比例：同一交易日下，本次取到的板块数低于本地已有的这个比例
#   时**不覆盖**本地面板。见 build_panel 里的说明（实测事故：1000 列被 122 列覆盖，
#   导致特征层塌到只剩 5 个板块）。
PANEL_KEEP_RATIO = 0.6
# ★ 替代源下"已有面板板块数不足替代源可提供量的这个比例 → 并入补齐"。
#   实测依据：被截断的老面板 122 列**全是行业、没有概念**，而辨识度榜/题材簇/
#   主线全部建立在概念层上 → 不并入的话界面依然是空的（final_in/out/themes 全 0）。
ALT_MERGE_RATIO = 0.6


def _retry_pause():
    """失败重试之间的**短抖动**停顿。

    ★ v3.0.6（RC-4）：旧实现是 `time.sleep(0.4 * (i + 1))` —— 固定且偏长。
      单个失败板块最坏要在这里睡掉 1.2s，而 build_panel 连续遇到几十个
      失败板块时，累积起来就是几十秒的**纯等待**。
      改成 0.12~0.30s 抖动，作用有三：
        ① 仍然"不贴着限流窗口猛打"（保留原意图）；
        ② 显著缩短单个失败请求的占位时间，把 worker 尽快还给队列；
        ③ 抖动而非固定 —— 避免所有线程**同频**重试。固定节奏会让
           8 个 worker 在同一个瞬间一起再打一轮，反而更容易再次触发限流。
    """
    time.sleep(0.12 + random.random() * 0.18)


def _get(path_or_url, tries=3, timeout=15):
    """传路径时会自动在多域名间做故障转移；失败返回 None。

    ★ 取数路径（v1.2.1）：**优先长连接复用**（快 10 倍，见长连接池注释），
      长连接通道整体不可用时自动退回 urllib（保留环境代理兼容性）。
    ★★ v2.0.9：**绝对 URL 也走长连接池** —— 此前 fetch_spot_batch /
      opensnap._fetch_list 等用绝对 URL 的调用永远走 urllib 逐请求建连
      （每次 TCP+TLS 握手 ~100-300ms），白白放弃连接复用。现在把绝对
      URL 拆成 host+path 后同样优先长连接，失败仍回退 urllib（保代理）。
    ★★ v3.0.6：外面包一层薄壳，把**每次请求的成败**喂给并发闸门做 AIMD。
      包壳而不是在每个 return 点插一句，是为了保证"**一次调用恰好一个样本**"
      —— 否则多域名重试会一次调用报出好几个样本，把失败率口径搞乱。
      ★ 硬熔断时直接返回，**不计入样本** —— 否则"熔断造成的失败"会自己
        把熔断续期下去（自我强化），这是这类机制最常见的死锁。
    """
    if _breaker_hard():
        return None
    j = _get_inner(path_or_url, tries=tries, timeout=timeout)
    _breaker_note(j is not None)
    _NET_GATE.note(j is not None)
    return j


def _get_inner(path_or_url, tries=3, timeout=15):
    """`_get` 的真实实现（不含并发闸门记账，便于单测直接调用）。"""
    if path_or_url.startswith("http"):
        # 绝对 URL：拆出 host 与 path，优先长连接；失败回退 urllib（含代理）
        _scheme, _rest = path_or_url.split("://", 1)
        host, path = _rest.split("/", 1)
        path = "/" + path
        for i in range(tries):
            if i and _breaker_open():
                break                # ★ v3.0.6：熔断打开 → 不再重试（省掉空转）
            body = None
            if not _ka_off():
                body = _keepalive_once(host, path, _ka_timeout(timeout))
                _ka_note(body is not None)
                if body is not None:
                    _mark_host(host, True)       # ★ v3.0.6：绝对 URL 也计入域名健康度
                    try:
                        return json.loads(body)
                    except Exception:                # noqa: BLE001
                        pass
            body, _err = _open_once(path_or_url, timeout)
            if body is not None:
                _mark_host(host, True)
                try:
                    return json.loads(body)
                except Exception:                    # noqa: BLE001
                    pass
            _mark_host(host, False)
            _retry_pause()
        return None

    for i in range(tries):
        if i and _breaker_open():
            break                    # ★ v3.0.6：熔断打开 → 不再重试（省掉空转）
        for h in _load_hosts():
            body = None
            # ---- 主通道：长连接复用（快，见上方长连接池说明）
            if not _ka_off():
                body = _keepalive_once(h, path_or_url, _ka_timeout(timeout))
                _ka_note(body is not None)
            # ---- 兼容通道：仅在通道未证实可用时才回退 urllib
            #   ★ 为什么用 `not _ka_proven()` 而不是"每次失败都回退"：
            #     一旦长连接成功过，失败就基本是**主机被限流**（实测两个
            #     通道会一起失败），此时再走 urllib 只是白等 2×timeout。
            #     直接换下一个域名更快 —— 这正是 _load_hosts 在做的。
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
        _retry_pause()
    return None


# ---------------------------------------------------------------- 板块清单
_SECTORS_MEMO = {}


def _sectors_cache_file():
    # 惰性取路径：DATA 会在运行期被重定向到用户目录（同 tradecal._cal_file）
    return os.path.join(DATA, "sectors.json")


def _load_sectors_cache():
    """读本地板块清单缓存 → {kind: [(secid, code, name, kind), ...]}。"""
    with _SECTORS_LOCK:
        if _SECTORS_MEMO:
            return _SECTORS_MEMO
        try:
            with open(_sectors_cache_file(), encoding="utf-8") as f:
                j = json.load(f)
            for k, v in (j.get("kinds") or {}).items():
                _SECTORS_MEMO[k] = [tuple(x) for x in v]
        except Exception:                    # noqa: BLE001
            pass
        return _SECTORS_MEMO


def _save_sectors_cache(kind, rows):
    """把某一类清单落盘，合并并发获取的其他类别且避免读者读到半截 JSON。"""
    try:
        with _SECTORS_LOCK:
            cache = _load_sectors_cache()
            cache[kind] = [list(x) for x in rows]
            payload = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "kinds": dict(cache),
            }
            _atomic_write_json(_sectors_cache_file(), payload)
    except Exception:                        # noqa: BLE001
        pass


def list_sectors(kind="industry", page_size=100):
    """kind: industry(行业) / concept(概念)

    ★★ v2.0.2：抓不到时回退本地缓存 sectors.json。
      旧行为是"抓不到就返回空列表"，上层随即 raise「板块清单为空」，
      整个面板建不起来 —— 而板块清单实际上是**静态数据**，
      本地存过一份就足以把面板跑起来。

    ★★ v3.0.6：接入多源层。当前源不是东财时（见 core/source.py），
      走替代源的实现；替代源也失败才落回东财。
    """
    # ---- 多源路由（v3.0.6）----
    try:
        import source as _src
        if _src.uses_alt():
            got = _src.tencent_list_sectors(kind, page_size)
            if got:
                _save_sectors_cache(kind, got)
                print(f"  [source] {_src.active()} 源：{kind} {len(got)} 个板块",
                      flush=True)
                return got
            print(f"  [source] {_src.active()} 源未取到 {kind} 清单，"
                  f"回落东财通道", flush=True)
    except Exception as e:                               # noqa: BLE001
        print(f"  [source] 替代源 list_sectors 异常："
              f"{type(e).__name__}: {e}", flush=True)

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
    if out:
        _save_sectors_cache(kind, out)
        return out
    cached = _load_sectors_cache().get(kind) or []
    if cached:
        return list(cached)
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
    # ---- 多源路由（v3.0.6）----
    # ★ 替代源（腾讯）**没有**分钟级资金流历史 —— 这是能力差异，不是故障。
    #   如实返回空列表，由 build_panel 走降级路径（靠本地面板 + 逐分钟累积），
    #   绝不用"点位"等别的东西冒充资金流。
    try:
        import source as _src
        if _src.uses_alt():
            _warn_no_minute_history(_src.active())
            return []
    except Exception:                                    # noqa: BLE001
        pass
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
    # ---- 多源路由（v3.0.6）----
    try:
        import source as _src
        if _src.uses_alt():
            return _src.tencent_fetch_index_trend(secid)
    except Exception as e:                               # noqa: BLE001
        print(f"  [source] 替代源 fetch_index_trend 异常："
              f"{type(e).__name__}: {e}", flush=True)
        return []
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

_NO_MH_WARNED = {"done": False}


def _warn_no_minute_history(src_name):
    """替代源缺"分钟资金流历史"时**只提示一次**（避免 1000 次刷屏）。

    ★ 为什么必须提示而不是静默返回空：用户会看到"板块取不到数据"，
      如果不说明是"换了源、该源没这个能力"，就会被当成程序坏了。
    """
    if _NO_MH_WARNED["done"]:
        return
    _NO_MH_WARNED["done"] = True
    print(f"  [source] {src_name} 源不提供「分钟级资金流历史」—— "
          f"本次全量建表将取不到历史分钟，改由本地面板 + 逐分钟累积提供；"
          f"该能力只有东财源有（可用 {SOURCE_HINT} 切回）。", flush=True)


#: 提示里给的"切回东财"暗门写法（与 core/source.py 保持一致）
SOURCE_HINT = "环境变量 FLOWLIVE_SOURCE=eastmoney 或 data/source.json"


def index_secids():
    """当前数据源下应展示的指数列表 `[(key, secid, label)]`。

    ★ 标签必须与**真实标的**一致：替代源下没有"东财全A"，
      用别的指数顶替就必须把标签改掉（见 core/source.T_INDEX_MAP），
      继续显示"东财全A"属于编造。
    """
    try:
        import source as _src
        return _src.index_secids()
    except Exception:                                    # noqa: BLE001
        return list(INDEX_SECIDS)


def fetch_all_index_trends():
    """抓取全部展示指数：{"sh": [(hm,px,vol)…], "all": [...], "meta": {...}}

    单个指数失败不影响其他（返回空列表，界面按"缺"处理），
    与 HTML 版 HEADER_INDEXES 的行为一致。
    """
    out = {}
    # ★ v3.0.6：指数清单改为**按当前数据源**给出（替代源下"东财全A"要用
    #   别的标的顶替，标签同步改，见 index_secids / source.T_INDEX_MAP）。
    _secids = index_secids()
    for key, secid, label in _secids:
        try:
            out[key] = fetch_index_trend(secid)
        except Exception:                                # noqa: BLE001
            out[key] = []
    out["meta"] = {k: {"secid": s, "label": lb} for k, s, lb in _secids}
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

# ★★ v2.0.1：SPOT_FIELDS 扩展 —— 同一次请求顺带取回「领涨 / 领跌股」。
#
#   为什么这么做是**零成本**的：
#     ulist.np 是"按 secid 批量取字段"的接口，字段数量不改变请求次数 ——
#     实测同一次 200 板块的请求，从 3 个字段加到 9 个字段，
#     耗时没有可测差异（都在 300~500ms 量级）。所以这是**纯收益**。
#
#   字段含义（实测确认，三个板块的返回值见 v2.0.1 验证记录）：
#     f12  板块代码        f14  板块名称        f62  当日累计主力净额
#     f128 领涨股名称      f140 领涨股代码      f136 领涨股涨跌幅(%)
#     f207 领跌股名称      f208 领跌股代码      f209 领跌股涨跌幅(%)
#   例：{"f12":"BK0896","f14":"白酒","f62":42502752.0,
#        "f128":"绿地控股","f140":"600606","f136":10.34,
#        "f207":"贵州茅台","f208":"600519"}
#
#   为什么要它（这是"龙头选手"列能做到全覆盖的关键）：
#     原先"板块龙头"只有一条来源 —— leader.py：抓每个板块的成分股分钟资金流，
#     再算"谁在板块拐点上先动手"。它很精确，但代价是**网络密集**：
#     实测 22 个板块 ≈ 250 次请求 / 42 秒，因此只能覆盖在榜的少数板块，
#     而且首次要等十几秒。
#     快照字段则覆盖**全部 427 个板块**、零额外请求、每 30 秒刷新一次。
#   两者并存、各司其职：
#     · 快照 f128  → 「当下领涨股」：全体板块、高频、随刷新即时更新（主来源）；
#     · leader.py → 「拐点上先动手的那只」：少数板块、低频、语义更精细（覆盖项）。
SPOT_FIELDS = "f12,f14,f62,f128,f140,f136,f207,f208,f209"

# 快照龙头落盘文件名（按交易日存，与 panel_/watchlist_ 同一套命名习惯）
SPOT_LEADER_FILE = "spot_leaders_{tag}.json"

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


def _label_of_idx(i):
    """分钟序号（0..239）→ 'HH:MM'。是 `_minute_idx` 的逆。

    ★ 为什么需要它（v3.0.6）：把"面板落后了一大段"补成**显式占位行**时，
      要给这些行写上正确的交易分钟标签。用 `datetime` 累加会踩午休
      （11:30 之后是 13:01），所以必须复用 `_minute_idx` 的同一套口径。
      口径（与 `_minute_idx` 严格互逆）：
        0        → 09:31（面板惯例；09:30 无分时数据）
        1..119   → 09:32 .. 11:30
        120..239 → 13:01 .. 15:00
    """
    try:
        i = int(i)
    except Exception:                                    # noqa: BLE001
        return None
    if i < 0 or i > 239:
        return None
    if i == 0:
        return "09:31"
    t = (9 * 60 + 31 + i) if i <= 119 else (13 * 60 + 1 + (i - 120))
    return f"{t // 60:02d}:{t % 60:02d}"


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


def panel_internal_gap_min(times):
    """面板时间轴**内部**的最大跳跃（分钟）。缺失的分钟数 = 相邻序号差 - 1。

    ★★ v3.0.6：为什么必须看"内部"而不只看"尾部"——
      快速先行版（refresh_panel_spot(allow_gap=True)）会把**当前这一分钟**
      补到面板尾部，中间缺失的分钟写成 NaN 占位行（图上如实断开）。
      这时**尾部缺口是 0**（末行就是当前分钟），只按 `spot_gap_minutes`
      判定就会得出"面板很新"的结论 → **永远不再触发全量补齐** →
      那段占位行永久留在面板里，曲线一直是断的。
      所以缺口判定必须取「尾部缺口」与「内部最大跳跃」的**较大者**。
    """
    idx = [_minute_idx(t) for t in (times or [])]
    idx = [i for i in idx if i is not None]
    if len(idx) < 2:
        return 0
    return max(0, max(b - a - 1 for a, b in zip(idx, idx[1:])))


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


def spot_leaders_path(tag):
    """当日快照龙头（领涨/领跌股）落盘路径。"""
    return os.path.join(DATA, SPOT_LEADER_FILE.format(tag=tag))


def load_spot_leaders(tag):
    """读当日快照龙头映射 `{secid: {...}}`；没有或损坏时返回 `{}`。

    返回结构（每项）：
        {"net": 净额(元), "lead": 领涨股名, "lead_code": 代码, "lead_pct": 涨幅,
         "lag": 领跌股名, "lag_code": 代码}
    界面侧只读不改，因此这里不必加锁；文件是**原子写**（见 save_spot_leaders），
    不会读到半截 JSON。
    """
    try:
        with open(spot_leaders_path(tag), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                                    # noqa: BLE001
        return {}


def save_spot_leaders(tag, spot):
    """原子写快照龙头（先写 .tmp 再 os.replace，避免进程被杀时留半截文件）。"""
    if not spot:
        return 0
    p = spot_leaders_path(tag)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(spot, f, ensure_ascii=False)
        os.replace(tmp, p)
        return len(spot)
    except Exception:                                    # noqa: BLE001
        try:
            os.remove(tmp)
        except Exception:                                # noqa: BLE001
            pass
        return 0


def refresh_spot_leaders(tag="today"):
    """只取「龙头快照」（领涨 / 领跌股），**不动面板**。

    用途：全量建表之后补一次。此时面板已经是最新的一整份分时，
    但 `spot_leaders_*.json` 还不存在 —— 界面的「龙头选手」列会是空的
    （要等到下一次增量刷新才有内容）。首次使用时那一列空着最显眼，
    所以建表后顺手补一发。

    成本：与增量刷新同量级（5~6 次请求 / 约 0.35 秒）——
    因为用的是同一个批量快照通道，只是不写面板。返回写入的板块数。
    """
    p_json = os.path.join(DATA, f"panel_{tag}.json")
    try:
        with open(p_json, encoding="utf-8") as f:
            codes = json.load(f).get("codes") or []
    except Exception:                                    # noqa: BLE001
        return 0
    if not codes:
        return 0
    spot = fetch_spot_batch(codes, detail=True)
    return save_spot_leaders(tag, spot)


def fetch_spot_batch(secids, detail=False, workers=None, tries=3, names=None):
    """批量取"当日累计主力净流入"快照。

    detail=False → `{secid: 净额(元)}`（历史行为，向后兼容）
    detail=True  → `{secid: {net, lead, lead_code, lead_pct, lag, lag_code}}`

    names: 可选 `{secid: 板块名}`。★ v3.0.6 新增 —— 替代源（腾讯）按**名称**
           提供数据，没有名称就无法把值贴回面板的列；缺失时替代源返回空结果
           并如实说明，**不猜着填**。

    ★★ v2.0.1 新增 detail 参数：把「领涨股 / 领跌股」一并带回来。
      为什么不另开一次请求：同一次 ulist.np 请求里多取几个字段**不增加
      请求次数**（字段数不影响耗时），另开一次反而会让盘中刷新多花一个 RTT。
      而「龙头选手」列需要的信息恰好就在这几个字段里（见 SPOT_FIELDS 注释）。

    ★ 必须带 `ut=` —— 漏掉会返回 f62=None（实测：不带 ut 的 stock/get
      返回 0；ulist.np 不带 ut 直接给 None）。这是本函数唯一的坑。

    ★★ v3.0.4（本次重构）：**分片请求改为并行**。
      改动前：`for i in range(0, len(secids), SPOT_BATCH)` 是**串行**的 ——
      1000 个板块 / 200 一批 = 5 次请求**逐个排队**，总耗时 = 5 × RTT。
      打桩实测（RTT=60ms）：**302ms**，其中 300ms 全是在等网络，CPU 零占用。
      改动后：分片用 ThreadPoolExecutor 并发发出，总耗时 ≈ 1 × RTT + 抖动。
      同一打桩条件下理论下限 **60ms（5.0× 提速）**。

      ★ 为什么这里安全（两条都已核实）：
        ① `_TLS = threading.local()` —— 连接池**每线程一条** HTTPSConnection，
           不存在多线程抢同一条连接的竞争；
        ② 结果按 secid 写入**各自指定的 dict 项**，无共享可变状态。
           （不是"后写覆盖先写"，因为每个 secid 只出现在一个分片里。）

        ★★ v3.0.6 口径说明（回应"build_panel 16/24 并发与这里 5 并发不一致"）：
          两者**保持不同是刻意的**，但 build_panel 那边已从"固定 24"改成
          `_NET_GATE` 的 AIMD 自适应（起点 10，区间 [3, 调用方给的上限]）。
          为什么这里不共用同一个闸门：
            · 本函数是**短突发**（真实盘中恰好 5 个分片，一次发完就结束，
              总在途 ≤ 5），闸门对它没有调节空间；
            · build_panel 是**长流水**（1000 次请求持续几分钟），
              它才是会把域名推到限流阈值的那一方，也才是需要 AIMD 的一方。
          换句话说：这里的 5~6 是"分片数的自然上限"，那边的 10 是"可自适应的
          起点"—— 两个数字含义不同，不是口径打架。

        ★★ v3.0.6（tries 默认 2 → 3）：**这一步失败的代价极高，值得多试。**
          它是"快速先行版"的全部数据来源（见 refresh_panel_spot 的 allow_gap）：
          3~5 个分片只要有**一个**没回来，整次快照就不算成功，用户就得回去
          干等几分钟的全量补抓。而每次尝试在"被拒"时只要 ~150ms
          （服务端快速拒绝，不是超时），多试一次的成本可以忽略。
          实测依据（2026-09-23 15:0x 的请求头对比探针）：被拒是服务端**速率
          限制**，且通过率呈"连拒几次、再放行几次"的桶式节奏 —— 单次成功
          约 40%，所以"多试几次"对"整批都成功"的概率提升很明显
          （单分片 1 次 40% → 3 次约 78%；3 个分片全成功 6% → 约 48%）。
          ★ 同一次探针还确认了两件**不要做**的事，写在这里免得后人再试：
            · 加 `Accept-Encoding` → 通过率直接掉到 0/5；
            · 去掉 Referer/Accept（只留 UA）→ 同样 0/5。
            也就是说**现有请求头组合是对的**，别再"优化"它。
    """
    if not secids:
        return {}

    # ---- 多源路由（v3.0.6）----
    try:
        import source as _src
        if _src.uses_alt():
            got = _src.tencent_fetch_spot_batch(secids, detail=detail,
                                                names=names)
            print(f"  [source] {_src.active()} 源：{len(got)}/{len(secids)} "
                  f"个板块取到当前累计主力净额", flush=True)
            return got
    except Exception as e:                               # noqa: BLE001
        print(f"  [source] 替代源 fetch_spot_batch 异常："
              f"{type(e).__name__}: {e}", flush=True)
        return {}

    def _num(x):
        return float(x) if isinstance(x, (int, float)) else None

    chunks = [secids[i:i + SPOT_BATCH]
              for i in range(0, len(secids), SPOT_BATCH)]
    # 并发度 = 分片数与上限的较小者（分片少时不需要那么多线程）
    w = int(workers) if workers else min(6, len(chunks))
    w = max(1, min(w, len(chunks)))

    def _one(chunk):
        """取一个分片，返回 [(secid, parsed_value), ...]。不碰共享状态。"""
        ids = ",".join(chunk)
        url = (f"/api/qt/ulist.np/get?fltt=2&invt=2&fields={SPOT_FIELDS}"
               f"&secids={ids}&ut={UT}")
        j = _get(url, tries=tries, timeout=8)
        diff = ((j or {}).get("data") or {}).get("diff") or []
        got = []
        for d in diff:
            code = d.get("f12")
            if not code:
                continue
            v = d.get("f62")
            secid = f"90.{code}"
            if not detail:
                if isinstance(v, (int, float)):
                    got.append((secid, float(v)))
                continue
            got.append((secid, {
                "net": _num(v),
                "lead": str(d.get("f128") or ""),
                "lead_code": str(d.get("f140") or ""),
                "lead_pct": _num(d.get("f136")),
                "lag": str(d.get("f207") or ""),
                "lag_code": str(d.get("f208") or ""),
            }))
        return got

    out = {}
    if len(chunks) == 1:
        # ★ 只有**一个分片**时才走单线程直取。
        #   ⚠ 这里必须判 `len(chunks) == 1`，**不能**判 `w == 1`：
        #     w=1 的语义是"用 1 个线程把所有分片串行发完"（等价于旧行为），
        #     不是"只发第一个分片"。写错会**静默丢掉后面所有分片的数据**
        #     （1000 个板块只回 200 个），而且不报错 —— 属于最危险的缺陷类型。
        for sid, val in _one(chunks[0]):
            out[sid] = val
        return out
    with ThreadPoolExecutor(max_workers=w) as ex:
        for got in ex.map(_one, chunks):
            for sid, val in got:
                out[sid] = val
    return out


def refresh_panel_spot(tag="today", workers=6, on_progress=None, allow_gap=False):
    """**增量刷新**：只把最新一分钟的值接到已有面板上（v1.2.3）。

    与 build_panel 的区别：
      build_panel        1000 次请求 / 2000~3500ms —— 拉全部分时（建底表用）
      refresh_panel_spot    5 次请求 /   ~300ms —— 只取当前累计值（高频刷新用）

    返回 (meta, mat)；任何一步失败都返回 (None, None)，由调用方决定
    是否退回全量抓取 —— **绝不半途改坏已有面板**。

    allow_gap（v3.0.6 新增）：
      False（默认，保持旧语义）→ 落后超过 SPOT_MAX_GAP_MIN 分钟就返回
        (None, None)，交回全量重抓。
      True → 即使落后很多也**先把当前这一分钟补上**，中间缺失的分钟
        写成 NaN 占位行（渲染层会断开曲线）。用途是"全量补抓开始前的
        快速先行版"：限流环境下全量要跑几分钟，而当前这一刻的真实资金流
        只要 5~6 次请求 / 约 0.3 秒就能拿到（见下方缺口守卫处的说明）。
        meta 里会留下 partial_history / gap_from / gap_to 三个标记。
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
    #
    # ★★ v3.0.6（allow_gap）：缺口大时**不再直接放弃**，而是"先给当前、
    #   历史留空"。起因是用户反馈「等了两分钟还没加载出来」——
    #   实测日志：全量补抓在限流下跑了 **190 秒**（14:34:42→14:37:54），
    #   这期间界面上只有上午 11:30 之前的旧数据，体感就是"没加载出来"。
    #   而现在这一刻的真实资金流，其实**只要 5~6 次请求 / 约 0.3 秒**
    #   就能拿到（ulist.np 一次给 200 个板块的当前累计净额）。
    #
    #   为什么不能像以前那样"把当前分钟直接接在 11:30 后面"：
    #     渲染层的 x 轴是**真实分钟序号**（见 _minute_positions/_x_positions），
    #     11:30 落在 x=119、14:34 落在 x=182 —— 直接相接会让 matplotlib
    #     画出一条横跨 63 格的直线，等于**编造了 94 分钟的走势**。
    #     这正是本项目反复修过的"假横线"缺陷，不能再犯。
    #   所以 allow_gap=True 时：**把中间缺失的分钟显式补成 NaN 占位行**，
    #   由渲染层的长缺口守卫在那里**断开曲线**，如实呈现"这段没有数据"。
    gap = spot_gap_minutes(times)
    if gap is not None and gap > SPOT_MAX_GAP_MIN:
        if not allow_gap:
            say(f"  面板落后 {gap} 分钟（> {SPOT_MAX_GAP_MIN}）→ 改走全量补齐空档")
            return None, None
        say(f"  面板落后 {gap} 分钟：allow_gap=True → 先补当前分钟，"
            f"中间缺失分钟显式留空（图上断开，不编造走势）")

    t0 = time.time()
    # ★★ v2.0.1：detail=True —— 同一次请求顺带取回「领涨 / 领跌股」。
    #   为什么要在这里（而不是另开一次请求）：字段数不影响请求次数与耗时，
    #   另开请求会让每一次盘中刷新多一个 RTT，与"降低盘中读取延迟"相反。
    #   净额抽出来喂面板，富信息另存一份供界面「龙头选手」列使用。
    #   ★ v3.0.6：带上 names —— 替代源（腾讯）按板块**名称**给数据，
    #     面板的 codes 是东财 secid，没有名称就贴不回去。
    spot = fetch_spot_batch(codes, detail=True, names=meta.get("names") or {})
    if not spot:
        return None, None                            # 一个都没取到 → 不算刷新成功

    net = {}
    for k, v in spot.items():
        n = (v or {}).get("net")
        if n is not None:
            net[k] = n
    if not net:
        return None, None

    say(f"  增量快照：{len(net)}/{len(codes)} 个板块有值"
        f"（{time.time()-t0:.1f}s）")
    # 龙头快照落盘（失败不影响本次刷新 —— 它是附加信息，不是必需品）
    try:
        n_ld = save_spot_leaders(tag, spot)
        if n_ld:
            say(f"  龙头快照：{n_ld} 个板块的领涨股已更新")
    except Exception:                                    # noqa: BLE001
        pass

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
        v = net.get(secid)                    # ★ v2.0.1：用抽出来的净额字典
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
        # ★ v3.0.6：前向填充的基准值必须取**插入占位行之前**的最后一行。
        #   顺序写反的后果很隐蔽：占位行整行是 NaN，于是"快照里缺的板块"
        #   会被填成 NaN 而不是上一分钟的已知值 —— 图上表现为那几条曲线
        #   在当前分钟凭空断开。不报错、只是数据错，属于最难发现的一类。
        _prev = (mat[-1, :].copy() if mat.shape[0]
                 else np.zeros(len(codes)))
        if _back:
            row = mat[-1, :]
            mat[-1, :] = np.where(np.isnan(col), row, col)
            say(f"  刻度 {hm} 不新于末行 {times[-1]} → 覆盖末行（保持单调）")
        else:
            # ★ v3.0.6：缺口大的时候，把中间缺失的分钟**显式补成 NaN 占位行**。
            #   为什么必须补：渲染层 x 轴是真实分钟序号，不补的话
            #   11:30(x=119) 会与 14:34(x=182) 被一条直线连起来 —— 那是
            #   编造出来的 94 分钟走势。补成 NaN 行后，渲染层的长缺口守卫
            #   会让曲线在那里断开，如实呈现"这段没有数据"。
            if (allow_gap and _newi is not None and _lasti is not None
                    and _newi > _lasti + 1):
                n_fill = _newi - _lasti - 1
                mat = np.vstack([mat, np.full((n_fill, len(codes)), np.nan)])
                times = list(times) + [_label_of_idx(i)
                                       for i in range(_lasti + 1, _newi)]
                meta["partial_history"] = True
                meta["gap_from"] = times[_lasti]
                meta["gap_to"] = hm
                say(f"  已插入 {n_fill} 个缺失分钟占位（{times[_lasti]}→{hm}，"
                    f"NaN，图上断开；全量补齐后会填实）")
            newcol = np.where(np.isnan(col), _prev, col)
            mat = (np.vstack([mat, newcol]) if mat.shape[0]
                   else newcol[None, :])
            times = list(times) + [hm]

    try:
        # ★★ v3.0.5：改为**原子成对写**（先 json 后 npy）——
        #   旧写法先 np.save(npy) 再 json.dump(json)，读者夹在中间会拿到
        #   「新矩阵 + 旧 times」→ 曲线被续到 15:00 画成假横线。
        #   详见 save_panel / _atomic_write_bytes 的说明。
        meta["times"] = times
        meta["source"] = "live"
        save_panel(tag, meta, mat)
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
            _atomic_write_json(os.path.join(DATA, f"index_{tag}.json"), idx)
        except Exception:                            # noqa: BLE001
            pass
    try:
        iall = fetch_index_trend("47.800000")
    except Exception:                                # noqa: BLE001
        iall = []
    if iall:
        try:
            _atomic_write_json(os.path.join(DATA, f"index_all_{tag}.json"), iall)
        except Exception:                            # noqa: BLE001
            pass

    return meta, mat


# ---------------------------------------------------------------- 失败板块留痕
def failed_sectors_path(data_dir=None):
    return os.path.join(data_dir or DATA, "failed_sectors.jsonl")


def log_failed_sectors(items, data_dir=None, cap=200):
    """把"**最终**仍取不到数据"的板块追加记录到 failed_sectors.jsonl。

    ★ 为什么值得单独记（v3.0.4 新增）：
      原代码的失败是**静默**的 —— 只在末尾报一个"919/996"的总数，
      既不知道是哪些板块，也无从判断"是不是同一批反复失败"。
      用户报"部分板块读不到"时，排查只能靠猜。
      记下来之后，下次再报就能直接对照：
        · 若**总是同一批** → 那些板块自身有问题（如新上市、无资金流数据）；
        · 若**每次不同**   → 是限流（本项目的实测结论就是这种）。
      两者处置方式完全不同，而区分它们的成本只有几行代码。

    格式：每行一个 JSON（JSONL，追加写，便于持续累积与 grep）。
    """
    try:
        p = failed_sectors_path(data_dir)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        rows = items[:cap]
        with open(p, "a", encoding="utf-8") as f:
            for secid, name in rows:
                f.write(json.dumps({"at": stamp, "secid": secid, "name": name},
                                   ensure_ascii=False) + "\n")
        return p
    except Exception:                                # noqa: BLE001
        return None


# ---------------------------------------------------------------- 面板构建
def _alt_merge_panel(tag, meta, mat, sectors, on_progress=None):
    """把替代源的板块**并入**已有面板：老列保留（含历史），新列补上（仅当前分钟）。

    ★★ 为什么必须"并入"而不是"重建"或"只追加一行"（实测驱动，两个方向都踩过）：
      · **重建**会丢掉老面板里的 **240 分钟真实历史**（替代源没有分钟历史）；
      · **只追加一行**则补不进新板块 —— 而实测发现这是更致命的一半：
        被截断的老面板里 **122 个全是行业、一个概念都没有**，而
        「辨识度榜 / 题材簇 / 主线 / 领涨领跌」**全部建立在概念层上**，
        实测 `session_report` 的 `final_in` / `final_out` / `themes`
        **全是 0 条** —— 界面上依然是空的。
      所以正确做法是"两个都要"：老列保历史，新列补概念。

    【数据诚实性】新列只有"当前这一分钟"的值，其余分钟**显式留 NaN**
      （不是 0、不是前向填充）—— 渲染层会如实断开，不会画出一条假曲线。
      meta 里标 `merged_from="alt-source"`，事后能查到这个面板被并过。
    """
    import numpy as np

    def say(msg):
        print(msg, flush=True)
        if on_progress:
            try:
                on_progress(msg)
            except Exception:                            # noqa: BLE001
                pass

    old_codes = list(meta.get("codes") or [])
    old_times = list(meta.get("times") or [])
    hm = clamp_to_bar()
    if not old_times or hm is None or mat.ndim != 2:
        return None
    if mat.shape[1] != len(old_codes):
        say(f"  ⚠ 面板列数({mat.shape[1]}) 与 codes({len(old_codes)}) 不一致，"
            f"放弃并入（保持原面板不动）")
        return None

    names = {s[0]: s[2] for s in sectors}
    kinds_of = {s[0]: s[3] for s in sectors}
    all_codes = [s[0] for s in sectors]
    say(f"  并入替代源板块：候选 {len(all_codes)} 个，取当前累计主力净额 …")
    spot = fetch_spot_batch(all_codes, detail=True, names=names)
    if not spot:
        say("  ⚠ 替代源未取到任何当前值 → 放弃并入（保持原面板不动）")
        return None

    # 当前值落到哪一行：已在时间轴上就覆盖那一行，否则追加一行
    if hm in old_times:
        row = old_times.index(hm)
        times = old_times
        base = mat.copy()
    else:
        row = len(old_times)
        times = old_times + [hm]
        base = np.vstack([mat, np.full((1, mat.shape[1]), np.nan)])

    pos = {c: i for i, c in enumerate(old_codes)}
    add_cols, add_codes, add_names, add_kinds = [], [], [], []
    updated = 0
    for c in all_codes:
        v = (spot.get(c) or {}).get("net")
        if v is None:
            continue
        i = pos.get(c)
        if i is not None:
            base[row, i] = float(v) / 1e8
            updated += 1
            continue
        col = np.full((len(times),), np.nan)     # ★ 其余分钟显式 NaN（不编造）
        col[row] = float(v) / 1e8
        add_cols.append(col)
        add_codes.append(c)
        add_names.append(names.get(c, c))
        add_kinds.append(kinds_of.get(c, "concept"))

    if add_cols:
        base = np.hstack([base, np.column_stack(add_cols)])

    m2 = dict(meta)
    m2["times"] = times
    m2["codes"] = old_codes + add_codes
    m2["names"] = dict(meta.get("names") or {},
                       **{c: n for c, n in zip(add_codes, add_names)})
    m2["kinds"] = dict(meta.get("kinds") or {},
                       **{c: k for c, k in zip(add_codes, add_kinds)})
    m2["source"] = "live"
    m2["merged_from"] = "alt-source"     # 如实标记：这个面板被并过
    save_panel(tag, m2, base)
    say(f"  并入完成：新增 {len(add_codes)} 个板块（仅当前分钟 {hm}，其余留空）、"
        f"更新 {updated} 个已有板块的 {hm} 行 → 合计 {len(m2['codes'])} 个板块")
    return m2, base


def _alt_build_panel(tag, kinds, on_progress=None):
    """**替代源**下的"全量建表" —— 用当前快照**种一行**，之后逐分钟累积。

    【为什么必须有这个函数（否则替代源等于没用）】
      替代源（腾讯）**没有分钟级资金流历史**（见 `source.CAPS`）。如果
      `build_panel` 照原样去逐板块取分钟序列，只会拿到 0 个板块 → 抛
      「未取到任何板块数据」→ 上层退回"上一个交易日的本地面板"。
      而 `refresh_panel_spot` 的前提是**当天已有面板**才会追加 —— 于是
      当天永远没有面板、累积永远开始不了。**替代源就彻底用不起来了。**

    【做法】分两条路径，产出的都是**真实数据**（当前累计主力净额）：
      ① 当天已有面板 → 直接走增量通道，它会把"当前这一分钟"接上去；
      ② 当天还没有面板 → 用当前快照**种一行**（times = [当前交易分钟]），
         之后每分钟由 `refresh_panel_spot` 往上接。
      meta 里标 `partial_history=True` + `gap_from/gap_to`，
      界面据此**如实呈现"这段没有数据"**，而不是画一条假曲线。

    【与"不降级守卫"的关系】本函数只在**替代源**下被调用；走路径 ① 时
      面板由 `refresh_panel_spot` 维护（只追加、不减少列），不会覆盖好数据。
    """
    import numpy as np

    def say(msg):
        print(msg, flush=True)
        if on_progress:
            try:
                on_progress(msg)
            except Exception:                            # noqa: BLE001
                pass

    say("  替代源不提供分钟级资金流历史 → 用当前快照种一行，"
        "之后每分钟累积（曲线从启动这一刻起生长）")

    # ---- ① 已有当天的面板：交给增量通道 ----
    #   ★ 注意 `refresh_panel_spot` 返回的是 **2 元组 (meta, mat)**，
    #     而 `build_panel` 的契约是 **3 元组 (meta, mat, idx)** ——
    #     必须在这里补上 idx，否则调用方 unpack 会 ValueError。
    got = refresh_panel_spot(tag, allow_gap=True, on_progress=on_progress)
    if got and got[0] is not None:
        _m, _a = got
        say(f"  已有当天面板 → 走增量通道：{len(_m.get('codes') or [])} 个板块、"
            f"{len(_m.get('times') or [])} 个分钟点")
        # ★★ 若已有面板的板块数**远少于**替代源能提供的，就把替代源的板块并进来。
        #   为什么必须做：实测（2026-09-23）被截断的老面板 122 列**全是行业、
        #   一个概念都没有**，而辨识度榜/题材簇/主线全建立在概念层上 ——
        #   不并入的话 `final_in/final_out/themes` 依然是 0 条，界面照样是空的。
        try:
            _sectors = []
            for _k in kinds:
                _sectors += list_sectors(_k)
            _n_old = len(_m.get("codes") or [])
            if _sectors and _n_old < len(_sectors) * ALT_MERGE_RATIO:
                say(f"  已有面板 {_n_old} 个板块，替代源可提供 {len(_sectors)} 个"
                    f"（不足 {ALT_MERGE_RATIO:.0%}）→ 并入补齐")
                _merged = _alt_merge_panel(tag, _m, _a, _sectors, on_progress)
                if _merged:
                    _m, _a = _merged
        except Exception as e:                           # noqa: BLE001
            say(f"  ⚠ 并入替代源板块失败（保留原面板不动）："
                f"{type(e).__name__}: {e}")
        _ix = []
        try:
            _ix = fetch_index_trend()
        except Exception:                                # noqa: BLE001
            _ix = []
        return _m, _a, _ix

    # ---- ② 没有面板：用当前快照种一行 ----
    sectors = []
    for k in kinds:
        sectors += list_sectors(k)
    if not sectors:
        raise RuntimeError("板块清单为空：替代源也没取到（网络不可达或接口变更）")
    names = {s[0]: s[2] for s in sectors}
    kinds_of = {s[0]: s[3] for s in sectors}
    all_codes = [s[0] for s in sectors]
    say(f"  板块清单 {len(all_codes)} 个，取当前累计主力净额 …")
    spot = fetch_spot_batch(all_codes, detail=True, names=names)
    if not spot:
        raise RuntimeError(
            "替代源未取到任何板块的当前资金流（可能被限流或接口变更）")
    hm = clamp_to_bar()
    if hm is None:
        raise RuntimeError("盘前无法用快照种面板（没有合法的交易分钟）")

    codes = [c for c in all_codes if c in spot]
    mat = np.array([[float((spot[c] or {}).get("net") or 0.0) / 1e8
                     for c in codes]], dtype=float)
    meta = {
        "times": [hm],
        "codes": codes,
        "names": {c: names.get(c, c) for c in codes},
        "kinds": {c: kinds_of.get(c, "concept") for c in codes},
        "source": "live",
        "data_date": LAST_DATA_DATE or tag,
        # ★ 如实标记：这是"只有当前分钟"的面板，当天历史分钟是缺的。
        #   界面与 _plan_fetch 都据此判定"需要补齐"，不会被误当成完整面板。
        "partial_history": True,
        "gap_from": "09:31",
        "gap_to": hm,
        "seed": "alt-source",          # 便于排查：这一行是"种"出来的
    }
    save_panel(tag, meta, mat)
    try:
        save_spot_leaders(tag, spot)
    except Exception:                                    # noqa: BLE001
        pass
    say(f"  已种下面板：{len(codes)} 个板块 × 1 个分钟点（{hm}）"
        f"｜未匹配 {len(all_codes) - len(codes)} 个（替代源名称对不上，如实跳过）")

    # 指数与完整版同口径（替代源提供分钟分时，标签已按源改名）
    idx = []
    try:
        idx = fetch_index_trend()
    except Exception:                                    # noqa: BLE001
        idx = []
    try:
        _atomic_write_json(os.path.join(DATA, f"index_{tag}.json"), idx)
    except Exception:                                    # noqa: BLE001
        pass
    try:
        iall = fetch_index_trend("47.800000")
    except Exception:                                    # noqa: BLE001
        iall = []
    try:
        _atomic_write_json(os.path.join(DATA, f"index_all_{tag}.json"), iall)
    except Exception:                                    # noqa: BLE001
        pass
    close_connections()
    return meta, mat, idx


def build_panel(tag="today", workers=None, limit=None, kinds=("industry", "concept"),
                on_progress=None, deadline=None):
    """抓取全市场板块分时资金流，落盘为宽表。

    on_progress: 可选回调 fn(msg:str)，用于向 UI 汇报进度。
    workers    : 并发**硬上限**；None 表示用 `NET_CONCURRENCY_DEFAULT`。
                 ★ v3.0.6：不再是"固定并发"，真实在途请求数由 `_NET_GATE`
                 按 AIMD 自适应，本参数只做天花板（见并发闸门一节）。
    deadline   : 全局时限（秒）；None 表示用 `BUILD_DEADLINE`，0 表示不限。

    ★★ v3.0.6 延迟修复（RC-5 / RC-6）：两处结构性改动 ——
      ① **并发不再固定**：旧实现由调用方传 `workers=24`，是固定 24 并发打
         1000 次请求，正是 push2 最敏感的突发形态；现在由 AIMD 在
         [3, workers] 区间内自己找当前网络能承受的档位。
      ② **加了全局时限**：旧实现没有上限，撞上持续限流时 1000 个板块会
         一个个重试到天荒地老（实测 2m42s 才到 800/1000 且还在继续）。
         到点即**停发新请求**，用已得数据把面板建起来，并如实汇报截断。
    """
    # ★ 每次构建前复位「真实数据日期」（v1.1.6 修复）：
    #   LAST_DATA_DATE 是模块级全局，且 fetch_sector_kline 里只在
    #   `is None` 时写入 —— 不复位的话，**同一个进程内第二次 build_panel
    #   会沿用上一次的日期**。真实场景：用户盘中点「重新抓取」时若已跨越
    #   交易日，或在周末抓过一次后再抓，meta["data_date"] 就会是旧值，
    #   进而把面板归档到**错误的交易日名下**（污染 30 日窗口与回看列表）。
    global LAST_DATA_DATE
    LAST_DATA_DATE = None

    # ★★ v3.0.6：替代源走"种子 + 逐分钟累积"（原因见 _alt_build_panel 的说明）。
    #   放在这里（而不是在各处零散判断）是为了让**所有调用方**——包括
    #   Worker 的全量补抓、bench 脚本——都自动获得同一套降级行为。
    #
    #   ★ 注意 try 只包住"判断用不用替代源"，**不包 _alt_build_panel 的调用**：
    #     第一版把调用也包进去了，结果是 `_alt_build_panel` 抛出的
    #     "替代源未取到任何板块" 被自己的 except 吞掉 → 悄悄回落到东财通道
    #     → 在"东财已被封"的机器上白打一轮注定失败的请求，而日志里只留一句
    #     "替代源建表异常"。**失败必须如实上抛**，否则上层无法走正确的降级。
    _use_alt = False
    try:
        import source as _src
        _use_alt = _src.uses_alt()
    except Exception as e:                               # noqa: BLE001
        print(f"  [source] 数据源层判断失败，按东财通道继续："
              f"{type(e).__name__}: {e}", flush=True)
    if _use_alt:
        return _alt_build_panel(tag, kinds, on_progress=on_progress)

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
        raise RuntimeError(
            "板块清单为空：网络不可达或数据源接口变更，"
            "且本地没有清单缓存（sectors.json）。首次使用需要一次联网。")

    t0 = time.time()
    results = {}

    # ★ v3.0.6（RC-5）：把调用方给的并发作为**硬上限**装进自适应闸门。
    cap = int(workers) if workers else NET_CONCURRENCY_MAX
    cap = max(1, min(cap, NET_CONCURRENCY_MAX))
    _NET_GATE.set_hard_max(cap)

    # ★ v3.0.6（RC-6）：全局时限。到点后 job() 直接返回空，
    #   于是 ex.map 会很快被抽干，不会把调用方卡住。
    dl = float(BUILD_DEADLINE if deadline is None else deadline)
    stop = threading.Event()

    def job(item):
        secid, code, name, kind = item
        _NET_GATE.acquire()
        try:
            if stop.is_set() or (dl > 0 and time.time() - t0 > dl):
                stop.set()
                return secid, name, kind, None
            return secid, name, kind, fetch_sector_kline(secid)
        finally:
            _NET_GATE.release()

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

    with ThreadPoolExecutor(max_workers=cap) as ex:
        for i, (secid, name, kind, rows) in enumerate(ex.map(job, sectors), 1):
            if rows:
                results[secid] = (name, kind, rows)
            if i % 200 == 0:
                say(f"  已抓取 {i}/{len(sectors)}  "
                    f"（{time.time()-t0:.0f}s，当前并发 {_NET_GATE.limit}）")

    say(f"  完成：{len(results)}/{len(sectors)} 个板块有数据，"
        f"用时 {time.time()-t0:.0f}s（并发档位 {_NET_GATE.limit}）")
    if stop.is_set():
        say(f"  ⚠ 已达全局时限 {dl:g}s，已停止发新请求："
            f"{len(sectors)-len(results)} 个板块本轮未取到，面板按现有数据建立")

    # ★★ v3.0.4：失败板块**补抓**（用户报「部分板块无法读取数据」的根治）
    #
    #   【实测事实】（2026-09-23 盘中，1000 个板块 8 并发）：
    #     · 首轮成功 919/996，失败 76 个（**7.6%**），失败原因全是"空返回"；
    #     · 但**失败是瞬时限流，不是永久不可达** ——
    #       把同一批"失败"板块隔几分钟单独重抓，**100% 成功**（各返回 15 行）。
    #     · 连续 15 次同类请求也复现了这一点：前 2 次失败（1362/1383ms），
    #       之后 13 次全成功（240~1032ms）。可见失败集中在一阵一阵的窗口里。
    #
    #   【原代码的问题】抓取循环是 `if rows: results[secid] = …` ——
    #     失败就**静默丢弃**，只在末尾报一个"919/996"的总数，既不记录
    #     是哪些板块失败，也不做任何补救。于是那 7.6% 的板块在面板里
    #     就是**永久缺列**（宽表里整列为 NaN，前向填充后是 0），
    #     用户看到的就是"部分板块没有数据"。
    #
    #   【修法】首轮结束后，对失败板块**串行补抓一轮**：
    #     · 串行 + 50ms 间隔 —— 首轮是 8~16 并发把限流触发了，
    #       补抓再并发只会火上浇油；低频单线程反而更容易成功；
    #     · tries=3 / timeout=10 —— 比首轮更耐心的重试；
    #     · 只补"失败的"，全成功时**零额外开销**（直接跳过）。
    #   实测补抓能挽回大部分失败板块，代价只有几秒到二十几秒。
    #
    #   ★★ v3.0.6 追加两个上限（补抓本身也会变成新的长尾）：
    #     · 首轮已因**全局时限**提前收工时，**不再补抓** —— 否则刚砍掉的
    #       长尾会立刻以"串行补抓"的形式长回来；
    #     · 补抓自己的预算 `BACKFILL_DEADLINE`，到点即停并如实汇报剩余数。
    missing = [s for s in sectors if s[0] not in results]
    if missing and stop.is_set():
        say(f"  首轮因全局时限提前收工，跳过串行补抓（{len(missing)} 个待补）")
        missing = []
    if missing:
        say(f"  {len(missing)} 个板块首轮未取到，开始串行补抓 …")
        t1 = time.time()
        saved = 0
        still = []
        # ★ v3.0.6：补抓预算与**全局时限共享**，不是各自独立。
        #   实测教训（2026-09-23 14:22 的 1000 板块全量跑）：首轮 137s + 补抓 47s
        #   = 184.5s，**超过了 BUILD_DEADLINE=180s** —— 因为补抓当时用的是
        #   自己的 45s 预算。既然是"全局时限"，就必须是**总预算**：
        #   这里取 min(补抓预算, 全局剩余)。剩余为 0 时直接不补抓。
        bf_dl = float(BACKFILL_DEADLINE)
        if dl > 0:
            bf_dl = max(0.0, min(bf_dl, dl - (time.time() - t0)))
        if bf_dl <= 0:
            say(f"  ⚠ 全局时限已用尽，跳过串行补抓（{len(missing)} 个待补）")
            still = [(s[0], s[2]) for s in missing]
        else:
            for k, (secid, _code, name, kind) in enumerate(missing):
                if time.time() - t1 > bf_dl:
                    still.extend((s[0], s[2]) for s in missing[k:])
                    say(f"  ⚠ 补抓已达时限 {bf_dl:.0f}s，剩余 "
                        f"{len(missing) - k} 个未补")
                    break
                # ★ v3.0.6：接口已进入**硬熔断**时，补抓不可能成功 ——
                #   立即收工并如实汇报，不要一个板块一个板块地空转
                #   （实测这会让"接口全死"场景的总耗时从 ~47s 涨到无谓的几十秒）。
                if _breaker_hard():
                    still.extend((s[0], s[2]) for s in missing[k:])
                    say(f"  ⚠ 接口持续不可用（硬熔断中），跳过剩余 "
                        f"{len(missing) - k} 个补抓")
                    break
                rows = fetch_sector_kline(secid, tries=3, timeout=10)
                if rows:
                    results[secid] = (name, kind, rows)
                    saved += 1
                else:
                    still.append((secid, name))
                time.sleep(0.05)                 # 轻微间隔，避免再次触发限流
            say(f"  补抓完成：挽回 {saved}/{len(missing)} 个，"
                f"用时 {time.time()-t1:.0f}s → 合计 {len(results)}/{len(sectors)}")
        if still:
            # ★ 如实记录仍失败的板块（便于排查）；不抛异常 ——
            #   缺几个板块不该让整个面板建不起来。
            say(f"  ⚠ 仍有 {len(still)} 个板块无数据（已记录）: "
                f"{[s for s, _ in still[:8]]}{' …' if len(still) > 8 else ''}")
            log_failed_sectors(still)

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
    # ★★ v3.0.6 面板"不降级"守卫 —— 绝不用更差的数据覆盖更好的数据。
    #
    #   【为什么必须有】2026-09-23 的实测事故：当天本机 IP 被东财封禁，
    #   全量抓取只取到 122 个板块就写盘，而本地同类面板原本列数更多
    #   （此前多日都是 1000 列）。后果不只是"少几列"：
    #     · 抓取顺序是「行业在前、概念在后」，被截断的 122 个**几乎全是
    #       二/三级行业**（住宅开发、商用载货车、面板…）；
    #     · 而 `universe.clean_universe` 的行业层只保留 31 个**一级**行业 ——
    #       于是特征层 1000 列 → 122 列 → **只剩 5 个板块**。
    #       实测 `salience.build_features('20260923')` 的 `F.codes == 5`，
    #       界面塌成"就绪 · 5 个板块"，辨识度/主角更替/阶段对比全部失效。
    #   【结论】"面板可以缺列"这个宽容策略是对的，但**覆盖**必须谨慎：
    #     缺列可以接受，把好数据换成差数据不可接受（而且不可逆）。
    #   【规则】同一交易日（data_date 相同）时，若本次取到的板块数不到本地
    #     已有的 PANEL_KEEP_RATIO，就**保留本地面板**并如实汇报。
    #     跨交易日（data_date 不同）不做比较 —— 那是新的一天，必须落盘。
    old_n, old_date = 0, None
    try:
        with open(os.path.join(DATA, f"panel_{tag}.json"), encoding="utf-8") as _f:
            _om = json.load(_f)
        old_n = len(_om.get("codes") or [])
        old_date = _om.get("data_date")
    except Exception:                                    # noqa: BLE001
        pass
    if (old_n and old_date and old_date == meta.get("data_date")
            and len(codes) < old_n * PANEL_KEEP_RATIO):
        say(f"  ⚠ 本次只取到 {len(codes)} 个板块，而本地已有 {old_n} 个"
            f"（同一交易日 {old_date}）—— 按「不用更差的数据覆盖更好的数据」"
            f"保留本地面板，本次结果未落盘。")
        _kept = load_panel(tag)
        if _kept and _kept[0] is not None:
            return _kept
        say("  ⚠ 本地面板读取失败，退回写入本次结果。")

    # ★★ v3.0.5：原子成对写（先 json 后 npy）。旧写法是「先 np.save(npy) 再
    #   json.dump(json)」两次裸写 —— 读者夹在中间会拿到「新矩阵 + 旧 times」，
    #   曲线尾部被续出假序号一路画到 15:00（用户报告的"假横线"）。
    #   详见 save_panel / _atomic_write_bytes 的说明。
    save_panel(tag, meta, mat)
    # 交易时段模块需要看到接口实际返回的数据日期，才能把节假日/调休的
    # "日历未覆盖"状态从推断升级为运行期观测结论。
    try:
        import tradingtime
        changed, observed = tradingtime.note_data_date(meta.get("data_date"))
        if changed:
            print(f"  [tradingtime] observed non-trading day: {observed}", flush=True)
    except Exception:                                    # noqa: BLE001
        pass

    # ★ v1.2.1：指数结果取自启动时就并行跑的那个线程（见上方 idx_thread）。
    #   这里 join 一下即可 —— 它通常早已跑完，等于零等待。
    idx_thread.join(timeout=20)
    idx = idx_box.get("sh") or []
    # 指数文件同样必须原子替换：指数缺失可降级，但半截 JSON 不能拖垮整套面板读取。
    _atomic_write_json(os.path.join(DATA, f"index_{tag}.json"), idx)

    # ★ 东财全A（v1.1.5）：另存一份，与上证并列展示。
    #   抓不到就写空表，不阻塞面板构建（界面按"缺"降级）。
    idx_all = idx_box.get("all") or []
    _atomic_write_json(os.path.join(DATA, f"index_all_{tag}.json"), idx_all)

    # ★ v1.2.1：收尾关闭本线程的长连接（worker 线程随线程池回收，这里管调用线程）
    close_connections()

    say(f"  面板：{mat.shape[0]} 个时间点 × {mat.shape[1]} 个板块")
    return meta, mat, idx


def load_panel(tag="today"):
    """读回面板 (meta, mat, idx)。

    ★★ v3.0.5 关键加固：**行数必须与 times 对齐**（用户报告「曲线尾部一条横线」）
    ────────────────────────────────────────────────────────────────
    【为什么会不一致】面板由**两个文件**共同表达时间轴：
        panel_{tag}.json  →  meta["times"]（时间标签，如 ["09:31",…,"10:20"]）
        panel_{tag}.npy   →  mat（形状 时间×板块，行序与 times 一一对应）
      而 build_panel / refresh_panel_spot 落盘时是**两次独立写**（先 .npy 后 .json），
      这里读回来又是**两次独立读**（先 .json 后 .npy）。UI 刷新线程（QThread）与
      另一轮刷新**并发**执行时，就可能读到「昨天的 .npy（240 行）+ 今天的 .json
      （50 个 times）」这种**跨版本组合** —— 实测本机 panel_20260922.npy 恰好
      240 行、panel_20260923.npy 盘中只有 50 行，两者列名一致，混起来完全不报错。

    【后果链（逐环可复现）】
      T = mat.shape[0] = 240（矩阵新），times = 50（标签旧）
      → `_market_stats` 的 `series` 取整列 = 240 个值
      → `FanChart.plot` 里 `n = max(len(series)) = 240`
      → `_x_positions(times=50, n=240)` 把第 50 个之后的空位**按「上一个 +1」续号**，
        一路续到 **239 = 15:00**
      → `_fill_gaps` 又对缺失段做端点外推（重复末值）→ 尾部成为**一条平直横线**。
      看起来就像「有一段数据没采集，直接画了条横线」。

    【修法】宁可少画，不可错画：**以 times 为准把矩阵截断到同一行数**。
      时间标签是横轴的唯一依据 —— 没有对应时刻的数据点，画出来一定是错的
      （落到别的时刻上），比不画更糟。截断只丢"标签没覆盖到的多余行"，
      正常情况下两文件本就一致（实测一致时 size 相同，本分支不进）。
      同时把不一致记进日志，便于判断是否真的发生过并发读写。
    """
    import numpy as np
    # ★ with 显式关闭（v1.0.5）：原来裸 open() 交给 GC，
    #   CPython 引用计数下通常立即关，但这是实现细节而非保证。
    with open(os.path.join(DATA, f"panel_{tag}.json"), encoding="utf-8") as f:
        meta = json.load(f)
    mat = np.load(os.path.join(DATA, f"panel_{tag}.npy"))
    # ★★ v3.0.5 对齐守卫（见 docstring）
    try:
        _times = meta.get("times") or []
        if mat.ndim == 2 and mat.shape[0] != len(_times):
            _k = min(mat.shape[0], len(_times))
            _excess = mat.shape[0] - len(_times)
            print(f"  [panel guard] {tag}: mat {mat.shape[0]} 行 ≠ times "
                  f"{len(_times)} 个 → 按 times 截断到 {_k} 行"
                  f"（多余 {_excess} 行无时间标签，丢弃以免曲线落到错误时刻）",
                  flush=True)
            mat = mat[:_k, :]
            meta = dict(meta)
            meta["times"] = list(_times[:_k])
    except Exception as _e:                              # noqa: BLE001
        print(f"  [panel guard] {tag}: 对齐检查跳过（{_e}）", flush=True)
    try:
        with open(os.path.join(DATA, f"index_{tag}.json"), encoding="utf-8") as f:
            idx = json.load(f)
    except Exception as e:                              # noqa: BLE001
        # 指数是可选增强信息；缓存缺失/损坏时不应让面板和榜单整体不可用。
        print(f"  [panel guard] {tag}: index unavailable ({type(e).__name__})", flush=True)
        idx = []
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
