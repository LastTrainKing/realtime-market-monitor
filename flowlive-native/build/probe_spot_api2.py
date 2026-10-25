# -*- coding: utf-8 -*-
"""深挖批量快照通道：找到「与 kline 末值等价的实时字段」。

Q3 上一轮失败原因：`stock/get` 不带 `ut` 时 f62 返回 0。
本脚本要确认：
  A. clist 的 f62（排序字段）是否等于 kline 当日累计末值？
  B. ulist.np 的 f62 是否等于 kline 末值？（批量通道能否用于增量）
  C. 若 f62 不可用，有哪些等价字段？（f184 主力净占比 / f66 超大单 / f72 大单…）
  D. 全市场（行业+概念）批量抓取的完整耗时与请求数。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
import flowdata                                              # noqa: E402

UT = flowdata.UT


def rows_of(j):
    return ((j or {}).get("data") or {}).get("diff") or []


secs = flowdata.list_sectors("industry")[:8]
secs_c = flowdata.list_sectors("concept")[:8]

print("\n===== A. clist 的 f62 vs kline 末值 =====")
# 取一页 clist，按 secid 建索引
url = (f"/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f62"
       f"&fs=m:90+t:2&fields=f12,f14,f62")
j = flowdata._get(url)
cl = {d["f12"]: d.get("f62") for d in rows_of(j)}
print(f"  clist 返回 {len(cl)} 条，样本前 3 条 f62: "
      f"{[d.get('f62') for d in rows_of(j)[:3]]}")

hit = 0
for secid, code, name, kind in secs[:6]:
    if code not in cl:
        continue
    k = flowdata.fetch_sector_kline(secid)
    klast = (k[-1][1] / 1e8) if k else None
    c62 = cl[code]
    c62 = (c62 / 1e8) if isinstance(c62, (int, float)) else None
    if klast is not None and c62 is not None:
        hit += 1
        print(f"  {name:16s} kline={klast:10.4f}亿   clist.f62={c62:10.4f}亿   "
              f"Δ={klast - c62:+.4f}亿")
print(f"  → 可比对 {hit} 条")

print("\n===== B. ulist.np 批量（带 ut）的 f62 vs kline 末值 =====")
for flds in ("f12,f14,f62", "f12,f14,f62,f184,f66,f72,f78,f84"):
    ids = ",".join(s[0] for s in secs)
    url = (f"/api/qt/ulist.np/get?fltt=2&invt=2&fields={flds}"
           f"&secids={ids}&ut={UT}")
    t0 = time.perf_counter()
    j = flowdata._get(url)
    ms = (time.perf_counter() - t0) * 1000
    d = rows_of(j)
    print(f"  fields={flds[:34]:36s} → {len(d)} 行, {ms:6.1f} ms")
    if d:
        s0 = d[0]
        print(f"    样本: {s0.get('f14')}  f62={s0.get('f62')} "
              f"f184={s0.get('f184')} f66={s0.get('f66')}")

print("\n===== C. 逐字段比对（找与 kline 末值一致的字段）=====")
ids = ",".join(s[0] for s in secs)
flds = ("f12,f14,f62,f184,f66,f72,f78,f84,f69,f75,f81,f87")
url = (f"/api/qt/ulist.np/get?fltt=2&invt=2&fields={flds}"
       f"&secids={ids}&ut={UT}")
j = flowdata._get(url)
u = {d["f12"]: d for d in rows_of(j)}
for secid, code, name, kind in secs[:5]:
    k = flowdata.fetch_sector_kline(secid)
    if not k:
        print(f"  {name:16s} kline 缺失")
        continue
    klast = k[-1][1] / 1e8
    d = u.get(code)
    if not d:
        print(f"  {name:16s} ulist 无返回")
        continue
    def g(f):
        v = d.get(f)
        return f"{v/1e8:10.4f}" if isinstance(v, (int, float)) else f"{str(v):>10s}"
    print(f"  {name:14s} kline={klast:9.4f} | f62={g('f62')} f184={g('f184')} "
          f"f66={g('f66')} f72={g('f72')}")

print("\n===== D. 全市场批量抓取（行业+概念，分批 200）=====")
alls = flowdata.list_sectors("industry") + flowdata.list_sectors("concept")
print(f"  全市场 {len(alls)} 个板块")
t0 = time.perf_counter()
nreq, got = 0, 0
B = 200
for i in range(0, len(alls), B):
    chunk = alls[i:i + B]
    ids = ",".join(s[0] for s in chunk)
    url = (f"/api/qt/ulist.np/get?fltt=2&invt=2&fields=f12,f14,f62"
           f"&secids={ids}&ut={UT}")
    j = flowdata._get(url)
    nreq += 1
    got += len(rows_of(j))
tot = (time.perf_counter() - t0) * 1000
print(f"  → {nreq} 次请求, 取回 {got} 条, {tot:.1f} ms")
print(f"  （对照：全量 kline 重抓 1000 次请求 / 2000~3500 ms）")
