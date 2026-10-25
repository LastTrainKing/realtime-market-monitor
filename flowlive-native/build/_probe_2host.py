# -*- coding: utf-8 -*-
"""交替探测两个域名当前的健康度（各 8 次，间隔 2s）"""
import os, sys, time, http.client, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata as fd
PATH = ("/api/qt/ulist.np/get?fltt=2&invt=2&fields=f12,f62&secids="
        + ",".join("90.BK%04d" % i for i in range(200)) + "&ut=" + fd.UT)
stat = {}
for r in range(8):
    for h in ("push2.eastmoney.com", "push2delay.eastmoney.com"):
        t = time.time()
        try:
            c = http.client.HTTPSConnection(h, timeout=8, context=fd._ssl_ctx())
            c.request("GET", PATH, headers=fd.HDRS)
            resp = c.getresponse(); b = resp.read(); c.close()
            j = json.loads(b); n = len(((j.get("data") or {}).get("diff")) or [])
            k = stat.setdefault(h, [0, 0]); k[0] += 1
            print(f"  {h:34s} OK diff={n} {(time.time()-t)*1000:.0f}ms")
        except Exception as e:
            k = stat.setdefault(h, [0, 0]); k[1] += 1
            print(f"  {h:34s} {type(e).__name__} {(time.time()-t)*1000:.0f}ms")
        time.sleep(2)
print("\n汇总：")
for h, (ok, bad) in stat.items():
    print(f"  {h:34s} 通 {ok}/8  拒 {bad}/8")
