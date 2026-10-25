# -*- coding: utf-8 -*-
"""_bench_sched.py —— **离线**测量调度层开销（与网络无关，完全确定性）。

为什么需要这个基准：
  网络实测受限流主导、方差极大（同一版本单轮可从 1.3s 到 59.8s），
  因此**无法**用它回答"并发闸门起点 10 相比旧的固定 24 慢多少"这类问题。
  但这一层的开销其实是**纯确定性**的：给定"每个请求固定耗时 L、共 N 个请求、
  并发上限 c"，墙钟时间就是 Σ 各档位下的 N_k/c_k × L。
  把网络换成固定延时的桩，就能精确测出调度策略本身的开销。

用法：
  python _bench_sched.py <并发起点> <硬上限> <请求数> <单请求毫秒>
  例：python _bench_sched.py 10 24 1000 250
"""
import os
import sys
import time
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
import flowdata as fd                                                   # noqa: E402

start = int(sys.argv[1]) if len(sys.argv) > 1 else 10
hardmax = int(sys.argv[2]) if len(sys.argv) > 2 else 24
nsec = int(sys.argv[3]) if len(sys.argv) > 3 else 1000
lat_ms = float(sys.argv[4]) if len(sys.argv) > 4 else 250.0

fd.NET_CONCURRENCY_DEFAULT = start
fd.NET_CONCURRENCY_MAX = hardmax
fd._NET_GATE.reset(hard_max=hardmax)
fd._NET_GATE.set_limit(start)
fd._breaker_reset()

tmp = tempfile.mkdtemp(prefix="flowlive_sched_")
old_data = fd.DATA
fd.set_data_dir(tmp)

_ol, _ok, _oi = fd.list_sectors, fd.fetch_sector_kline, fd.fetch_index_trend

_limit_seen = []


def fake_list(kind="industry", page_size=100):
    if kind != "industry":
        return []
    return [("90.BK%04d" % i, "BK%04d" % i, "板块%d" % i, kind)
            for i in range(nsec)]


def fake_kline(secid, tries=2, timeout=8):
    # ★ 必须在这里补记一次闸门样本：真实链路里 `_get` 会调用
    #   `_NET_GATE.note(ok)`，而打桩把 `_get` 整个绕过了。
    #   不补的话闸门永远收不到样本、档位一动不动 ——
    #   基准会得出"起点 10 效率 41%、加性增 0 次"这种**假结论**
    #   （第一版就踩了这个坑，已修正）。
    time.sleep(lat_ms / 1000.0)
    fd._NET_GATE.note(True)
    return [("09:31", 1.0e8, 0.0, 0.0, 0.0, 0.0)]


try:
    fd.list_sectors = fake_list
    fd.fetch_sector_kline = fake_kline
    fd.fetch_index_trend = lambda *a, **k: []
    t0 = time.time()
    meta, mat, idx = fd.build_panel("sched", workers=hardmax, deadline=0)
    dt = time.time() - t0
finally:
    fd.list_sectors = _ol
    fd.fetch_sector_kline = _ok
    fd.fetch_index_trend = _oi
    fd.set_data_dir(old_data)

codes = len(meta.get("codes") or [])
ideal = nsec / float(hardmax) * (lat_ms / 1000.0)     # 全程跑在硬上限的理论下限
eff = ideal / dt if dt > 0 else 0.0
st = fd._NET_GATE.stats()
print(f"起={start:2d} 上限={hardmax:2d} 请求={nsec:4d} 单请求={lat_ms:.0f}ms  "
      f"墙钟={dt:6.2f}s  理论下限={ideal:5.2f}s  效率={eff:5.1%}  "
      f"取到={codes}/{nsec}  峰值并发={st['peak']}  加性增={st['up']} 次")
