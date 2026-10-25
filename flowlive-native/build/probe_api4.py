# -*- coding: utf-8 -*-
"""
probe_api4.py —— 第四轮：换 ut 令牌重试 + daykline 上限
  S1. push2his kline/get klt=101 + 经典 ut → 指数日 K（日历 + 日级 OHLC）
  S2. push2his kline/get klt=1   + 经典 ut → 指数历史【分钟】K线？
  S3. push2    trends2 ndays=5   + 经典 ut → 多日指数分时？
  S4. daykline lmt 上限二分：60 / 120 / 200
  S5. daykline 用于指数 secid=1.000001？（预期失败，仅确认）
"""
import os, sys, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata

UT_FLOW = flowdata.UT                                # b2884a...（fflow 专用）
UT_WEB = "fa5fd1943c7b386f172d6893dbfba10b"          # 经典 web ut
HIS = "https://push2his.eastmoney.com"
SECID_TEST = "90.BK0475"


def s1_index_daily_kline():
    print("=" * 64)
    print("S1: push2his kline/get klt=101 + 经典 ut —— 指数日 K")
    print("=" * 64)
    url = (f"{HIS}/api/qt/stock/kline/get?secid=1.000001&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=1&beg=0&end=20500101&lmt=45&ut={UT_WEB}")
    j = flowdata._get(url)
    if not j or not j.get("data") or not j["data"].get("klines"):
        print("  无数据！")
        return
    kl = j["data"]["klines"]
    dates = [line.split(",")[0] for line in kl]
    print(f"  {len(kl)} 条，{dates[0]} ~ {dates[-1]}")
    bad = [d for d in dates if datetime.date.fromisoformat(d).weekday() >= 5]
    print(f"  含周末: {bad if bad else '无'}")
    print(f"  末条样例: {kl[-1]}")


def s2_index_minute_kline():
    print()
    print("=" * 64)
    print("S2: push2his kline/get klt=1 + 经典 ut —— 指数历史分钟 K 线？")
    print("=" * 64)
    for lmt in (241, 482, 1446):
        url = (f"{HIS}/api/qt/stock/kline/get?secid=1.000001&fields1=f1,f2,f3,f4,f5,f6"
               f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&klt=1&fqt=1&beg=0&end=20500101"
               f"&lmt={lmt}&ut={UT_WEB}")
        j = flowdata._get(url)
        if not j or not j.get("data") or not j["data"].get("klines"):
            print(f"  lmt={lmt}: 无数据")
            continue
        kl = j["data"]["klines"]
        dates = sorted({line.split(",")[0][:10] for line in kl})
        print(f"  lmt={lmt}: {len(kl)} 条，{len(dates)} 天，{dates[0]} ~ {dates[-1]}")


def s3_trends_classic_ut():
    print()
    print("=" * 64)
    print("S3: push2 trends2 ndays=5 + 经典 ut")
    print("=" * 64)
    url = (f"/api/qt/stock/trends2/get?secid=1.000001&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=5&ut={UT_WEB}")
    j = flowdata._get(url)
    if not j or not j.get("data") or not j["data"].get("trends"):
        print("  无数据！")
        return
    tr = j["data"]["trends"]
    dates = sorted({line.split(",")[0][:10] for line in tr})
    print(f"  {len(tr)} 条，{len(dates)} 天，{dates[0]} ~ {dates[-1]}")


def s4_daykline_cap():
    print()
    print("=" * 64)
    print("S4: daykline lmt 上限")
    print("=" * 64)
    for lmt in (60, 120, 200):
        url = (f"{HIS}/api/qt/stock/fflow/daykline/get?lmt={lmt}&klt=101&secid={SECID_TEST}"
               f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT_FLOW}")
        j = flowdata._get(url)
        if not j or not j.get("data") or not j["data"].get("klines"):
            print(f"  lmt={lmt}: 无数据")
            continue
        kl = j["data"]["klines"]
        dates = [line.split(",")[0][:10] for line in kl]
        print(f"  lmt={lmt}: {len(kl)} 条，{dates[0]} ~ {dates[-1]}")


def s5_daykline_index():
    print()
    print("=" * 64)
    print("S5: daykline 用于指数（预期失败）")
    print("=" * 64)
    url = (f"{HIS}/api/qt/stock/fflow/daykline/get?lmt=5&klt=101&secid=1.000001"
           f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT_FLOW}")
    j = flowdata._get(url)
    ok = bool(j and j.get("data") and j["data"].get("klines"))
    print(f"  指数日级资金流: {'有数据（意外）' if ok else '无数据（符合预期）'}")


if __name__ == "__main__":
    print(f"开始: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    s1_index_daily_kline()
    s2_index_minute_kline()
    s3_trends_classic_ut()
    s4_daykline_cap()
    s5_daykline_index()
    print()
    print("完毕。")
