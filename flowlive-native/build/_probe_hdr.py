# -*- coding: utf-8 -*-
"""对比不同请求头组合的通过率：判断"被拒"是限流还是 WAF 指纹拦截。"""
import os, sys, time, http.client, json, random
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata as fd
PATH = ("/api/qt/stock/fflow/kline/get?lmt=0&klt=1&secid=90.BK0475"
        "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut=" + fd.UT)
UA = fd.UA
VARIANTS = {
    "A 现状（UA+Referer+Accept）": dict(fd.HDRS),
    "B 加 Cookie": dict(fd.HDRS, **{"Cookie": "qgqp_b_id=%032x" % random.getrandbits(128)}),
    "C 加 Accept-Language/Encoding": dict(fd.HDRS, **{
        "Cookie": "qgqp_b_id=%032x" % random.getrandbits(128),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate, br"}),
    "D 极简（只 UA）": {"User-Agent": UA},
}
for name, hdr in VARIANTS.items():
    ok = bad = 0
    for i in range(5):
        t = time.time()
        try:
            c = http.client.HTTPSConnection("push2.eastmoney.com", timeout=8,
                                            context=fd._ssl_ctx())
            c.request("GET", PATH, headers=hdr)
            r = c.getresponse(); b = r.read(); c.close()
            ok += 1
            print(f"  {name:30s} #{i} OK {r.status} len={len(b)} {(time.time()-t)*1000:.0f}ms")
        except Exception as e:
            bad += 1
            print(f"  {name:30s} #{i} {type(e).__name__} {(time.time()-t)*1000:.0f}ms")
        time.sleep(1.5)
    print(f"  → {name}: 通 {ok}/5\n")
