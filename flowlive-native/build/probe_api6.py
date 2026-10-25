# -*- coding: utf-8 -*-
"""probe_api6.py —— 诊断 push2his 是否被限流：打印原始响应"""
import os, sys, time, urllib.request, urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata

UT = flowdata.UT
url = (f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
       f"?lmt=40&klt=101&secid=90.BK0475"
       f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT}")

for attempt in range(3):
    print(f"--- 尝试 {attempt+1} ---")
    try:
        req = urllib.request.Request(url, headers=flowdata.HDRS)
        with flowdata._OPENER_DIRECT.open(req, timeout=15) as r:
            body = r.read().decode("utf-8", "ignore")
            print(f"  HTTP {r.status}, 长度 {len(body)}")
            print(f"  前 300 字符: {body[:300]}")
    except urllib.error.HTTPError as e:
        print(f"  HTTPError {e.code}: {e.read()[:200]}")
    except Exception as e:
        print(f"  {type(e).__name__}: {e}")
    if attempt < 2:
        time.sleep(20)
