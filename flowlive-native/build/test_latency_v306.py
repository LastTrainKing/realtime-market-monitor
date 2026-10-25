# -*- coding: utf-8 -*-
"""test_latency_v306.py —— v3.0.6「延迟过高」修复的专项回归（**完全离线**）。

对应 2026-09-23 盘中实测定位到的六条根因：

  RC-1 快通道被**永久**关闭：`_KA_STATE["off"]` 只置位、全文件无复位点。
       → 启动初期撞上一次限流窗口，整个进程都退回 urllib（实测 10× 退化）。
  RC-2 冷却中的域名仍被**每个请求**重试一遍（旧 `_load_hosts` 把它排最后）。
  RC-3 冷却判定用"连续失败 3 次"，24 并发下毫秒级攒满；且冷却期内
       每再失败一次就 `until = now + 60`，**冷却永不到期**。
  RC-4 失败重试固定睡 `0.4*(i+1)`，单个失败请求最坏睡掉 1.2s。
  RC-5 `build_panel` 固定 24 并发（与 fetch_spot_batch 的 5 口径不一致），
       且不随网络状况自适应。
  RC-6 全量建表**没有全局时限**，撞上持续限流时耗时无界。

本文件覆盖 A~F 六组，全部不联网、不启动 GUI。
"""
import os
import sys
import time
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
# ★★ v3.0.6：本用例验证的是**东财通道**的行为，必须把数据源钉死在东财 ——
#   否则在默认已切到替代源的机器上，`build_panel` 会走 `_alt_build_panel`
#   （种子 + 逐分钟累积），被测对象就不是本用例要测的那条路径了。
os.environ.setdefault("FLOWLIVE_SOURCE", "eastmoney")
import flowdata as fd                                                   # noqa: E402

PASS = 0
FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


def reset_ka(proven=False, off=False, off_until=0.0, backoff=0.0):
    with fd._KA_LOCK:
        fd._KA_STATE.update(ok=0, fail=0, off=off, proven=proven,
                            off_until=off_until, backoff=backoff)


def reset_hosts():
    with fd._HOST_FAIL_LOCK:
        fd._HOST_FAIL.clear()
    with fd._HOST_LOCK:
        fd._HOST_ORDER[:] = list(fd.HOSTS)


# ======================================================================
print("=" * 72)
print("A. RC-1 快通道软关闭必须**可恢复**（旧实现只置位不复位）")
print("=" * 72)
reset_ka()
check(fd._ka_off() is False, "A1 初始状态：快通道可用")

reset_ka()
for _ in range(fd.KA_PROBE_LIMIT):
    fd._ka_note(False)
check(fd._KA_STATE["off"] is True,
      "A2 未证实可用时连续失败达阈值 → 进入软关闭")
check(abs(fd._KA_STATE["backoff"] - fd.KA_RETRY_AFTER) < 1e-6,
      "A3 首次软关闭的退避 = KA_RETRY_AFTER",
      "%.0fs" % fd._KA_STATE["backoff"])
check(fd._ka_off() is True, "A4 软关闭期内返回不可用")

with fd._KA_LOCK:
    fd._KA_STATE["off_until"] = time.time() - 1.0
check(fd._ka_off() is False, "A5 ★ 到期后放行一次重探（旧实现永远 False）")
check(fd._ka_off() is True,
      "A6 同窗口内其余线程仍走 urllib（重探天然单线程，不会一起冲限流）")

fd._ka_note(True)
check(fd._ka_off() is False and fd._ka_proven() is True,
      "A7 ★ 重探成功 → 立刻回到快通道（这是旧实现缺失的关键路径）")
check(fd._KA_STATE["backoff"] == 0.0, "A8 恢复后退避复位")

reset_ka()
for _ in range(fd.KA_PROBE_LIMIT):
    fd._ka_note(False)
with fd._KA_LOCK:
    fd._KA_STATE["off_until"] = time.time() - 1.0
for _ in range(fd.KA_PROBE_LIMIT):
    fd._ka_note(False)
check(abs(fd._KA_STATE["backoff"] - fd.KA_RETRY_AFTER * 2) < 1e-6,
      "A9 重探再失败 → 退避翻倍",
      "%.0fs" % fd._KA_STATE["backoff"])

reset_ka(proven=True)
for _ in range(fd.KA_PROBE_LIMIT):
    fd._ka_note(False)
check(fd._KA_STATE["off"] is False,
      "A10 已证实可用后，少量失败不关通道（主机限流≠通道坏）")

reset_ka()
check(fd._ka_timeout(15.0) == fd.KA_PROBE_TIMEOUT,
      "A11 未证实可用时仍用探测短超时（v3.0.3 语义未回归）")


# ======================================================================
print()
print("=" * 72)
print("B. RC-2/RC-3 域名熔断：滑动窗口失败率 + 不续期 + 可恢复 + 隔离")
print("=" * 72)
h0, h1 = fd.HOSTS[0], fd.HOSTS[1]

reset_hosts()
for _ in range(fd.HOST_MIN_SAMPLES - 5):
    fd._mark_host(h0, False)
check(not fd._host_cooling(h0),
      "B1 样本不足 %d 个 → 不冷却（旧实现 3 次就冷却）" % fd.HOST_MIN_SAMPLES)

reset_hosts()
for _ in range(6):
    fd._mark_host(h0, True)
for _ in range(2):
    fd._mark_host(h0, False)
check(not fd._host_cooling(h0), "B2 窗口失败率 25% → 不冷却")

reset_hosts()
for _ in range(3):
    fd._mark_host(h0, True)
for _ in range(5):
    fd._mark_host(h0, False)
check(fd._host_cooling(h0), "B3 窗口失败率 62.5% → 冷却")
check(not fd._host_dead(h0), "B4 曾成功过的域名走冷却，不走隔离")

st = fd._host_state(h0)
with fd._HOST_FAIL_LOCK:
    before = st["until"]
for _ in range(10):
    fd._mark_host(h0, False)
with fd._HOST_FAIL_LOCK:
    after = st["until"]
check(abs(after - before) < 1e-6,
      "B5 ★ 冷却期内继续失败**不延长**冷却（旧实现会 until=now+60 永不到期）")

fd._mark_host(h0, True)
check(not fd._host_cooling(h0) and not fd._host_dead(h0),
      "B6 ★ 成功一次立即清除冷却与隔离")
with fd._HOST_FAIL_LOCK:
    check(st["round"] == 0, "B7 成功后退避档位归零")

reset_hosts()
for _ in range(fd.HOST_MIN_SAMPLES):
    fd._mark_host(h1, False)
check(fd._host_dead(h1),
      "B8 本进程内从未成功 + 失败率 100% → 直接隔离（不再每轮重试）")

reset_hosts()
for _ in range(fd.HOST_MIN_SAMPLES):
    fd._mark_host(h1, False)
hosts = fd._load_hosts()
check(h1 not in hosts, "B9 ★ 隔离中的域名在**还有可用域名**时不被返回")
check(h0 in hosts, "B10 可用域名仍被返回")

for _ in range(fd.HOST_MIN_SAMPLES):
    fd._mark_host(h0, False)
hosts = fd._load_hosts()
check(len(hosts) >= 1,
      "B11 ★ 全部不可用时仍返回候选（不会「一个都不试、直接失败」）",
      "返回 %d 个" % len(hosts))
check(len(hosts) == 1,
      "B12 ★ 全部不可用时**只探一个**（半开语义；实测可让无效尝试数减半）",
      "返回 %d 个" % len(hosts))

# 复位，避免影响后续用例
reset_hosts()


# ======================================================================
print()
print("=" * 72)
print("C. RC-2 域名顺序：按最近成功**粘性优先**（轮转实测更慢，已回退）")
print("=" * 72)
reset_hosts()
check(fd._load_hosts() == list(fd.HOSTS),
      "C1 初始顺序与 HOSTS 一致", str([h.split('//')[-1] for h in fd._load_hosts()]))

fd._promote_host(h1)
check(fd._load_hosts()[0] == h1,
      "C2 ★ 最近成功的域名被提到最前（粘性：健康时只打好的那个）")
fd._promote_host(h0)
check(fd._load_hosts()[0] == h0, "C3 再次成功后顺序继续跟随最近成功者")

reset_hosts()
for _ in range(fd.HOST_MIN_SAMPLES):
    fd._mark_host(h1, False)
one = [fd._load_hosts() for _ in range(3)]
check(all(len(x) == 1 and x[0] == h0 for x in one),
      "C4 ★ 只剩一个可用域名时，不返回被隔离的那个（旧实现每次都会试它）")
check(not hasattr(fd, "_HOST_RR"),
      "C5 轮转游标已移除（实测轮转把一半流量推给更差的域名，反而慢 12×）")
reset_hosts()


# ======================================================================
print()
print("=" * 72)
print("D. RC-5 并发闸门：硬上限约束 + AIMD 自适应 + 真能限制在途数")
print("=" * 72)
g = fd._AdaptiveGate(8, hard_max=16)
check(g.limit == 8, "D1 初始档位 = 传入值")
for _ in range(fd.NET_ADJUST_SAMPLES):
    g.note(False)
check(g.limit == 4, "D2 ★ 失败率 100% → 并发**减半**", "8 → %d" % g.limit)
for _ in range(fd.NET_ADJUST_SAMPLES):
    g.note(True)
check(g.limit == 5, "D3 ★ 失败率 0% → 并发 +1（加性增，缓慢试探）",
      "4 → %d" % g.limit)
before = g.limit
# ★ 中间区间的失败数由常量推导，避免把档位阈值写死在测试里
_n = fd.NET_ADJUST_SAMPLES
_mid = max(1, int(_n * (fd.NET_FAIL_LOW + fd.NET_FAIL_HIGH) / 2.0))
for _ in range(_n - _mid):
    g.note(True)
for _ in range(_mid):
    g.note(False)
check(g.limit == before, "D4 失败率落在中间区间 → 档位不动（避免抖动）",
      "%.1f%% 时保持 %d" % (_mid / float(_n) * 100, g.limit))

g2 = fd._AdaptiveGate(2, hard_max=3)
for _ in range(fd.NET_ADJUST_SAMPLES * 3):
    g2.note(True)
check(g2.limit == 3, "D5 ★ AIMD 不会超过调用方给的硬上限", "limit=%d" % g2.limit)
g2.set_hard_max(1)
check(g2.limit == 1, "D6 硬上限下调时当前档位立刻被压到上限内")
for _ in range(fd.NET_ADJUST_SAMPLES):
    g2.note(False)
check(g2.limit == 1, "D7 下限为 1，不会出现 0 并发（死锁）")

g3 = fd._AdaptiveGate(4, hard_max=4)
peak = [0]
active = [0]
lk = threading.Lock()


def _worker():
    for _ in range(6):
        g3.acquire()
        try:
            with lk:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.01)
            with lk:
                active[0] -= 1
        finally:
            g3.release()


ts = [threading.Thread(target=_worker) for _ in range(16)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check(peak[0] <= 4, "D8 ★ 16 线程抢 4 个名额：在途峰值不超过档位",
      "峰值 %d" % peak[0])
check(peak[0] == 4, "D9 名额确实被用满（不是形同虚设）", "峰值 %d" % peak[0])

g4 = fd._AdaptiveGate(6, hard_max=6)
orig_inner = fd._get_inner
fd._get_inner = lambda *a, **k: None
try:
    fd._get("/x")
    fd._get("/y")
    s = g4.stats()
finally:
    fd._get_inner = orig_inner
s = fd._NET_GATE.stats()
check(s["fail"] >= 1, "D10 `_get` 的成败被计入闸门（一次调用恰好一个样本）")


# ======================================================================
print()
print("=" * 72)
print("E. RC-6 全量建表必须有**全局时限**（旧实现耗时无界）")
print("=" * 72)
tmp = tempfile.mkdtemp(prefix="flowlive_lat_")
old_data = fd.DATA
fd.set_data_dir(tmp)

_orig_list = fd.list_sectors
_orig_kline = fd.fetch_sector_kline
_orig_idx = fd.fetch_index_trend


def fake_list(kind="industry", page_size=100):
    # ★ 只给 industry 返回清单：避免两个 kind 返回同一批 secid 造成
    #   sectors 列表翻倍（那会让 E2 的"已得列数"断言失去意义）。
    if kind != "industry":
        return []
    return [("90.BK%04d" % i, "BK%04d" % i, "板块%d" % i, kind)
            for i in range(20)]


def fake_kline(secid, tries=2, timeout=8):
    # 每个板块都要花 0.2s：4 并发下 20 个板块需要约 1.0s，
    # 而 deadline=0.5s → 必然在中途截断（这是本用例要验的场景）。
    time.sleep(0.2)
    return [("09:31", 1.0e8, 0.0, 0.0, 0.0, 0.0),
            ("09:32", 2.0e8, 0.0, 0.0, 0.0, 0.0)]


msgs = []
fd._NET_GATE.reset(hard_max=4)
try:
    fd.list_sectors = fake_list
    fd.fetch_sector_kline = fake_kline
    fd.fetch_index_trend = lambda *a, **k: []
    t0 = time.time()
    meta, mat, idx = fd.build_panel("lattest", workers=4, deadline=0.5,
                                    on_progress=msgs.append)
    dt = time.time() - t0
finally:
    fd.list_sectors = _orig_list
    fd.fetch_sector_kline = _orig_kline
    fd.fetch_index_trend = _orig_idx
    fd.set_data_dir(old_data)

codes = meta.get("codes") or []
check(dt < 2.5, "E1 ★ deadline=0.5 时总耗时被限制住（旧实现会跑满全部 20 个）",
      "实测 %.2fs" % dt)
check(4 <= len(codes) < 20,
      "E2 ★ 超时后**用已得数据建表**，不是整体失败", "拿到 %d/20 列" % len(codes))
truncated = [m for m in msgs if "全局时限" in m]
check(bool(truncated), "E3 如实汇报「因超时截断」（不静默）",
      truncated[0][:46] if truncated else "无")
check(mat.ndim == 2 and mat.shape[1] == len(codes),
      "E4 宽表形状与 codes 一致", str(mat.shape))

# deadline=0（不限）时应把 20 个全部抓完
fd._NET_GATE.reset(hard_max=8)
try:
    fd.list_sectors = fake_list
    fd.fetch_sector_kline = lambda s, tries=2, timeout=8: [
        ("09:31", 1.0e8, 0.0, 0.0, 0.0, 0.0)]
    fd.fetch_index_trend = lambda *a, **k: []
    meta2, mat2, _ = fd.build_panel("lattest2", workers=8, deadline=0,
                                    on_progress=None)
finally:
    fd.list_sectors = _orig_list
    fd.fetch_sector_kline = _orig_kline
    fd.fetch_index_trend = _orig_idx
check(len(meta2.get("codes") or []) == 20,
      "E5 deadline=0 表示不限时，20 个板块全部抓完",
      "%d/20" % len(meta2.get("codes") or []))

# 补抓预算也必须存在
check(hasattr(fd, "BACKFILL_DEADLINE") and fd.BACKFILL_DEADLINE > 0,
      "E6 串行补抓有自己的时限预算（否则长尾会长回来）",
      "%.0fs" % fd.BACKFILL_DEADLINE)


# ======================================================================
print()
print("=" * 72)
print("I. 面板「不降级」守卫 —— 绝不用更差的数据覆盖更好的数据")
print("=" * 72)
tmp2 = tempfile.mkdtemp(prefix="flowlive_keep_")
old_data2 = fd.DATA
fd.set_data_dir(tmp2)


def _mk_panel(tag, n_sec, date, minutes=3):
    codes = ["90.BK%04d" % i for i in range(n_sec)]
    times = ["09:%02d" % (31 + i) for i in range(minutes)]
    meta = {"times": times, "codes": codes,
            "names": {c: "板块%d" % i for i, c in enumerate(codes)},
            "kinds": {c: "industry" for c in codes},
            "source": "live", "data_date": date}
    import numpy as _np
    mat = _np.ones((minutes, n_sec))
    fd.save_panel(tag, meta, mat)


_orig_list2 = fd.list_sectors
_orig_kline2 = fd.fetch_sector_kline
_orig_idx2 = fd.fetch_index_trend


def _list_n(n):
    def _f(kind="industry", page_size=100):
        if kind != "industry":
            return []
        return [("90.BK%04d" % i, "BK%04d" % i, "板块%d" % i, kind)
                for i in range(n)]
    return _f


def _kline_ok(secid, tries=2, timeout=8):
    return [("09:31", 1.0e8, 0.0, 0.0, 0.0, 0.0)]


try:
    fd.fetch_sector_kline = _kline_ok
    fd.fetch_index_trend = lambda *a, **k: []

    # 先落一个 100 板块的"好面板"
    _mk_panel("keeptest", 100, "20260923")
    fd.list_sectors = _list_n(100)
    m1, a1, _ = fd.build_panel("keeptest", workers=4, deadline=0)
    check(len(m1["codes"]) == 100, "I1 基线：100 个板块的面板已落盘",
          "%d 列" % len(m1["codes"]))

    # 再抓一次，但只取到 30 个（模拟限流截断）→ 必须**不覆盖**
    msgs2 = []
    fd.list_sectors = _list_n(30)
    m2, a2, _ = fd.build_panel("keeptest", workers=4, deadline=0,
                               on_progress=msgs2.append)
    check(len(m2["codes"]) == 100,
          "I2 ★ 截断结果（30 列）不覆盖已有的 100 列面板",
          "读回 %d 列" % len(m2["codes"]))
    check(any("保留本地面板" in x for x in msgs2),
          "I3 ★ 如实汇报「保留本地面板」（不静默）",
          next((x.strip()[:40] for x in msgs2 if "保留本地面板" in x), "无"))
    on_disk = fd.load_panel("keeptest")
    check(on_disk[0] is not None and len(on_disk[0]["codes"]) == 100,
          "I4 磁盘上的面板仍是 100 列（确实没被写坏）")

    # 更好的结果应当**正常覆盖**
    fd.list_sectors = _list_n(120)
    m3, a3, _ = fd.build_panel("keeptest", workers=4, deadline=0)
    check(len(m3["codes"]) == 120,
          "I5 ★ 更全的结果（120 列）正常覆盖 100 列（守卫不会挡住正常更新）",
          "%d 列" % len(m3["codes"]))

    # 跨交易日不做比较：新的一天必须落盘
    _mk_panel("keeptest2", 200, "20260922")
    fd.list_sectors = _list_n(10)
    m4, a4, _ = fd.build_panel("keeptest2", workers=4, deadline=0)
    check(len(m4["codes"]) == 10,
          "I6 ★ 跨交易日（data_date 不同）不做比较，新一天必须落盘",
          "%d 列" % len(m4["codes"]))
finally:
    fd.list_sectors = _orig_list2
    fd.fetch_sector_kline = _orig_kline2
    fd.fetch_index_trend = _orig_idx2
    fd.set_data_dir(old_data2)


# ======================================================================
print()
print("=" * 72)
print("F. RC-4 失败重试的停顿改为**短抖动**（旧实现固定 0.4/0.8/1.2s）")
print("=" * 72)
t0 = time.time()
fd._retry_pause()
dt = time.time() - t0
check(0.10 <= dt <= 0.60, "F1 单次重试停顿在 0.12~0.30s 量级", "%.3fs" % dt)
samples = []
for _ in range(6):
    t0 = time.time()
    fd._retry_pause()
    samples.append(time.time() - t0)
check(max(samples) - min(samples) > 1e-4,
      "F2 停顿带抖动（避免所有线程同频重试）",
      "min=%.3f max=%.3f" % (min(samples), max(samples)))
check(all(s < 0.6 for s in samples),
      "F3 ★ 单次停顿显著短于旧的 1.2s 上限")

print()
print("=" * 72)
print("G. 全局熔断：重度限流时掐掉重试空转（但**保留首次尝试**）")
print("=" * 72)
fd._breaker_reset()
check(not fd._breaker_open(), "G1 初始未熔断")

for _ in range(fd.BREAKER_MIN_SAMPLES - 1):
    fd._breaker_note(False)
check(not fd._breaker_open(),
      "G2 样本不足 %d 个 → 不熔断" % fd.BREAKER_MIN_SAMPLES)

fd._breaker_reset()
for _ in range(fd.BREAKER_MIN_SAMPLES):
    fd._breaker_note(False)
check(fd._breaker_open(),
      "G3 ★ 窗口内失败率 100% → 熔断打开", "cooldown=%.0fs" % fd.BREAKER_COOLDOWN)

fd._breaker_reset()
for _ in range(fd.BREAKER_MIN_SAMPLES - 2):
    fd._breaker_note(True)
for _ in range(2):
    fd._breaker_note(False)
check(not fd._breaker_open(),
      "G4 失败率 14% → 不熔断（只对「全网络不可用」这种量级反应）")

# ---- G5 熔断只掐重试，不掐首次尝试 ----
reset_hosts()
reset_ka(proven=True)                       # proven=True → 失败时不再回退 urllib
for _ in range(fd.HOST_MIN_SAMPLES):
    fd._mark_host(h1, False)                # 备用域隔离，_load_hosts 只剩主域
calls = [0]
_orig_ka2 = fd._keepalive_once


def _dead_ka(*a, **k):
    calls[0] += 1
    return None
fd._keepalive_once = _dead_ka
try:
    fd._breaker_reset()
    fd._get_inner("/x", tries=3, timeout=1)
    closed_calls = calls[0]

    calls[0] = 0
    fd._breaker_reset()
    with fd._BREAKER_LOCK:
        fd._BREAKER["until"] = time.time() + 30.0
    fd._get_inner("/x", tries=3, timeout=1)
    open_calls = calls[0]
finally:
    fd._keepalive_once = _orig_ka2
    fd._breaker_reset()
    reset_hosts()

check(closed_calls == 3,
      "G5 未熔断时 tries=3 会真的试 3 轮", "实际 %d 次" % closed_calls)
check(open_calls == 1,
      "G6 ★ 熔断打开时只发**首次尝试**（不是一次都不发）",
      "实际 %d 次（旧行为会是 3 次 + 2 段重试等待）" % open_calls)
check(open_calls >= 1,
      "G7 ★ 轻熔断不会把取数率压到 0（这是该机制最容易踩的坑）")

# ---- G9 升级式硬熔断 ----
fd._breaker_reset()
for _ in range(fd.BREAKER_HARD_TRIPS):
    with fd._BREAKER_LOCK:
        fd._BREAKER["until"] = 0.0            # 手动结束上一轮冷却，模拟"又失败一轮"
    for _ in range(fd.BREAKER_MIN_SAMPLES):
        fd._breaker_note(False)
check(fd._breaker_hard(),
      "G9 ★ 连续 %d 轮零成功 → 升级为硬熔断" % fd.BREAKER_HARD_TRIPS)

calls[0] = 0
_orig_ka3 = fd._keepalive_once
fd._keepalive_once = _dead_ka
try:
    fd._get("/x")                             # 硬熔断下应**一次请求都不发**
    hard_calls = calls[0]
finally:
    fd._keepalive_once = _orig_ka3
check(hard_calls == 0,
      "G10 ★ 硬熔断期间连首次尝试都不发（对已封禁我们的服务不再猛打）",
      "实际 %d 次" % hard_calls)

fd._breaker_note(True)
check(not fd._breaker_hard() and fd._BREAKER["trips"] == 0,
      "G11 ★ 任何一次成功立刻解除硬熔断并清零轮次（不会永久静默）")

fd._breaker_reset()
check(not fd._breaker_open(), "G8 可复位（测试与运行期都用得上）")

print()
print("=" * 72)
print("H. 兼容通道（urllib）健康度分档 —— 实测 2313ms → 242ms 的那处修复")
print("=" * 72)
check(len(fd.OPENER_RULES) >= 2 and fd.OPENER_RULES[0][0] > fd.OPENER_RULES[1][0],
      "H1 ★ 直连通道阈值(20)高于兼容通道阈值(3)：失败便宜的主通道不被轻易关",
      f"{fd.OPENER_RULES}")


class _FakeOpener:
    def __init__(self, ok):
        self.ok = ok
        self.n = 0

    def open(self, req, timeout=None):
        self.n += 1
        if not self.ok:
            raise OSError("fake fail")

        class _R:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s):
                return b'{"ok":1}'
        return _R()


_orig_openers = fd._OPENERS[:]
o0, o1 = _FakeOpener(False), _FakeOpener(False)
fd._OPENERS = [o0, o1]
try:
    fd._opener_reset()
    for _ in range(3):
        fd._open_once("https://example.invalid/x", 1)
    check(o1.n == 3, "H2 兼容通道前 3 次仍会被尝试（不误伤代理专网环境）",
          f"调用 {o1.n} 次")
    check(not fd._OPENER_STATE[0]["off_until"],
          "H3 ★ 直连通道失败 3 次**不**被关闭（主通道不该被瞬时抖动关掉）")
    fd._open_once("https://example.invalid/x", 1)
    check(o1.n == 3,
          "H4 ★ 兼容通道被软关后不再被调用（单次耗时 2313ms → 242ms 的来源）",
          f"仍为 {o1.n} 次")
    check(o0.n == 4, "H5 直连通道仍每次都试", f"调用 {o0.n} 次")

    fd._opener_note(1, True)
    check(fd._OPENER_STATE[1]["off_until"] == 0.0
          and fd._OPENER_STATE[1]["backoff"] == 0.0,
          "H6 ★ 兼容通道一旦成功立刻清除软关闭与退避")

    fd._opener_reset()
    for _ in range(fd.OPENER_RULES[1][0]):
        fd._opener_note(1, False)
    st = fd._OPENER_STATE[1]
    b1 = st["backoff"]
    with fd._OPENER_LOCK:
        st["off_until"] = time.time() - 1.0
    check(fd._opener_usable(1) is True, "H7 软关闭到期后放行一次重探（半开）")
    check(fd._OPENER_STATE[1]["backoff"] > b1,
          "H8 半开时先翻倍退避，避免几秒后又来一次",
          f"{b1:.0f}s → {fd._OPENER_STATE[1]['backoff']:.0f}s")
    check(fd._opener_usable(1) is False, "H9 半开窗口内其余请求不再走该通道")

    fd._opener_reset()
    o0.ok = True
    o1.n = 0
    body, err = fd._open_once("https://example.invalid/x", 1)
    check(body is not None and o1.n == 0,
          "H10 ★ 直连成功时**根本不会**去碰兼容通道（代理专网环境下也一样安全）")
finally:
    fd._OPENERS = _orig_openers
    fd._opener_reset()

print()
print("=" * 72)
print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 72)
sys.exit(1 if FAIL else 0)
