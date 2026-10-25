# -*- coding: utf-8 -*-
"""快速刷新通道探测：验证「快照式增量抓取」能否替代「全量 kline 重抓」。

背景：全量重抓 = 1000 个板块各发 1 次 kline 请求（约 2~3.5 秒）。
      若要做到 10 秒一刷，这条路太贵 —— 而且是**重复拉当天全部分时**，
      每次都在传同样的历史分钟。

要回答四个问题：
  Q1 clist 单页上限是多少？（决定全市场要几次请求）
  Q2 真实全市场「快照式」抓取要几次请求、多少毫秒？
  Q3 快照值（f62）与 kline 当日累计值是否一致？
     —— 不一致就不能用它更新曲线的最后一个点。
  Q4 ulist.np 批量接口能一次查多少 secid？（备选通道）
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
import flowdata                                              # noqa: E402


def timed(fn, *a, **kw):
    t0 = time.perf_counter()
    r = fn(*a, **kw)
    return r, (time.perf_counter() - t0) * 1000


def rows_of(j):
    return ((j or {}).get("data") or {}).get("diff") or []


print("\n===== Q1  clist 单页上限（pz）=====")
for pz in (100, 200, 500, 1000, 5000):
    url = (f"/api/qt/clist/get?pn=1&pz={pz}&po=1&np=1&fltt=2&invt=2&fid=f62"
           f"&fs=m:90+t:2&fields=f12,f14,f62")
    j, ms = timed(flowdata._get, url)
    d = rows_of(j)
    print(f"  pz={pz:5d} → {len(d):4d} 行, {ms:7.1f} ms")

print("\n===== Q2  真实全市场「快照式」抓取成本 =====")
_snap_total = {}
for kind, fs in (("industry", "m:90+t:2"), ("concept", "m:90+t:3")):
    t0 = time.perf_counter()
    got, pn, nreq, page = [], 1, 0, 500
    while pn <= 12:
        url = (f"/api/qt/clist/get?pn={pn}&pz={page}&po=1&np=1&fltt=2&invt=2"
               f"&fid=f62&fs={fs}&fields=f12,f14,f62")
        j = flowdata._get(url)
        nreq += 1
        d = rows_of(j)
        if not d:
            break
        got += d
        if len(d) < page:
            break
        pn += 1
    ms = (time.perf_counter() - t0) * 1000
    _snap_total[kind] = (len(got), nreq, ms)
    print(f"  {kind:9s}: {len(got):4d} 个板块, {nreq} 次请求, {ms:7.1f} ms")

_tt = sum(v[2] for v in _snap_total.values())
_tr = sum(v[1] for v in _snap_total.values())
print(f"  ---\n  合计: {_tr} 次请求, {_tt:.1f} ms  "
      f"（全量 kline 重抓为 1000 次请求 / 2000~3500 ms）")

print("\n===== Q3  快照 f62 与 kline 当日累计是否一致 =====")
secs = flowdata.list_sectors("industry")[:6]
for secid, code, name, kind in secs:
    k = flowdata.fetch_sector_kline(secid)
    klast = (k[-1][1] / 1e8) if k else None
    url = f"/api/qt/stock/get?secid={secid}&fltt=2&invt=2&fields=f12,f14,f62"
    j, _ = timed(flowdata._get, url)
    raw = ((j or {}).get("data") or {}).get("f62")
    spot = (raw / 1e8) if isinstance(raw, (int, float)) else None
    if klast is None or spot is None:
        print(f"  {name:14s} 数据缺失  kline={klast} spot={spot}")
    else:
        print(f"  {name:14s} kline末值={klast:11.4f}亿  "
              f"快照={spot:11.4f}亿  Δ={klast - spot:+.4f}亿")

print("\n===== Q4  ulist.np 批量（备选通道）=====")
allsec = flowdata.list_sectors("industry")
for n in (100, 300, len(allsec)):
    n = min(n, len(allsec))
    ids = ",".join(s[0] for s in allsec[:n])
    url = f"/api/qt/ulist.np/get?fltt=2&invt=2&fields=f12,f14,f62&secids={ids}"
    j, ms = timed(flowdata._get, url)
    d = rows_of(j)
    print(f"  请求 {n:4d} 个 → 返回 {len(d):4d} 个, {ms:7.1f} ms")
