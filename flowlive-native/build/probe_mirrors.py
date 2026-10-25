# -*- coding: utf-8 -*-
"""probe_mirrors.py —— push2his 编号镜像可用性诊断"""
import os, sys, json, time, urllib.request, urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata

UT = flowdata.UT
PATH = (f"/api/qt/stock/fflow/daykline/get?lmt=40&klt=101&secid=90.BK0475"
        f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT}")

HOSTS = (
    ["https://push2his.eastmoney.com"] +
    [f"https://{n}.push2his.eastmoney.com" for n in (1, 7, 16, 28, 33, 63, 90)]
)

for h in HOSTS:
    url = h + PATH
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=flowdata.HDRS)
        with flowdata._OPENER_DIRECT.open(req, timeout=10) as r:
            body = r.read().decode("utf-8", "ignore")
        j = json.loads(body)
        kl = (j.get("data") or {}).get("klines") or []
        if kl:
            dates = [l.split(",")[0][:10] for l in kl]
            print(f"  OK   {h}  {len(kl)} 条 {dates[0]}~{dates[-1]}  ({time.time()-t0:.1f}s)")
        else:
            print(f"  EMPTY {h}  body={body[:80]}  ({time.time()-t0:.1f}s)")
    except Exception as e:
        print(f"  FAIL {h}  {type(e).__name__}: {str(e)[:60]}  ({time.time()-t0:.1f}s)")
    time.sleep(1.5)
