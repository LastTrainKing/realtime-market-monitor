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

# [shell] 网络模块已全部移除：本模块不具备任何联网取数能力。
import json, os, random, time, threading
from concurrent.futures import ThreadPoolExecutor

# ------------------------------------------------------------------ 已移除：数据源配置
# 原 HOSTS / UT / UA / HDRS / opener / 主机故障转移表 均已删除。
# 这些常量只服务于联网取数，本版本无取数能力，故一并移除。
_OPENERS = []

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




















def close_connections():
    """[shell] 无连接可关。保留接口以兼容 UI 退出流程。"""
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




def _get(path_or_url, tries=3, timeout=15):
    """[shell] 数据读取能力已移除。恒返回 None，调用方按原有失败路径降级。"""
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
    """[shell] 数据读取能力已移除，只返回本地板块清单缓存。"""
    return list(_load_sectors_cache().get(kind) or [])


# ---------------------------------------------------------------- 分时资金流
# ★ 接口只返回「最近一个交易日」的数据（v1.1.0 实测）：
#   周末/节假日抓到的其实是前一交易日。这里把真实数据日期记下来，
#   让上层能把面板归到正确的交易日名下，而不是按抓取日期张冠李戴。
LAST_DATA_DATE = None


def fetch_sector_kline(secid, tries=2, timeout=8):
    """[shell] 数据读取能力已移除。恒返回空列表。"""
    return []


def fetch_index_trend(secid="1.000001"):
    """[shell] 数据读取能力已移除。恒返回空列表。"""
    return []


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
    """[shell] 数据读取能力已移除。恒返回空字典。"""
    return {}


def fetch_spot_index(secid="1.000001"):
    """[shell] 数据读取能力已移除。恒返回空字典。"""
    return {}


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
    """[shell] 数据读取能力已移除。不产生新榜单。"""
    return None


def fetch_spot_batch(secids, detail=False, workers=None, tries=3, names=None):
    """[shell] 数据读取能力已移除。恒返回空字典。"""
    return {}


def refresh_panel_spot(tag="today", workers=6, on_progress=None, allow_gap=False):
    """[shell] 数据读取能力已移除。不刷新面板，保留本地已有数据。"""
    if on_progress:
        try:
            on_progress(0, 0, "本版本不含数据读取能力")
        except Exception:            # noqa: BLE001
            pass
    return None


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
