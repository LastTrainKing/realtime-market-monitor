# -*- coding: utf-8 -*-
"""
probe_api2.py —— 第二轮实测：push2his 历史通道

第一轮结论：push2 上 fflow/kline 只给最近交易日（lmt 无效）、trends2 ndays=45 空。
第二轮验证历史数据专用域名 push2his.eastmoney.com：
  H1. push2his + fflow/kline klt=1  → 历史【分钟】资金流？
  H2. push2his + fflow/daykline/get → 历史【日级】资金流？（已知常用接口）
  H3. push2his + trends2 ndays=45    → 多日指数分时（分钟日历）？
  H4. push2his + kline/get klt=101   → 日 K 线（备选交易日历，最稳）？
"""
import os, sys, json, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata

SECID_TEST = "90.BK0475"
UT = flowdata.UT
HIS = "https://push2his.eastmoney.com"


def h1_minute_history():
    print("=" * 64)
    print("H1: push2his fflow/kline klt=1 —— 历史分钟资金流？")
    print("=" * 64)
    for lmt in (0, 480, 5000):
        url = (f"{HIS}/api/qt/stock/fflow/kline/get?lmt={lmt}&klt=1&secid={SECID_TEST}"
               f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT}")
        j = flowdata._get(url)
        if not j or not j.get("data") or not j["data"].get("klines"):
            print(f"  lmt={lmt}: 无数据")
            continue
        kl = j["data"]["klines"]
        dates = sorted({line.split(",")[0][:10] for line in kl})
        print(f"  lmt={lmt}: {len(kl)} 条，{len(dates)} 天，{dates[0]} ~ {dates[-1]}")
        if len(dates) > 1:
            print(f"    日期: {dates[:8]}{' ...' if len(dates) > 8 else ''}")


def h2_daykline():
    print()
    print("=" * 64)
    print("H2: push2his fflow/daykline/get —— 历史日级资金流？")
    print("=" * 64)
    url = (f"{HIS}/api/qt/stock/fflow/daykline/get?lmt=40&klt=101&secid={SECID_TEST}"
           f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT}")
    j = flowdata._get(url)
    if not j or not j.get("data") or not j["data"].get("klines"):
        print("  无数据！")
        return
    kl = j["data"]["klines"]
    dates = [line.split(",")[0][:10] for line in kl]
    print(f"  {len(kl)} 条，{dates[0]} ~ {dates[-1]}")
    print(f"  首条: {kl[0]}")
    print(f"  末条: {kl[-1]}")


def h3_trends_multi():
    print()
    print("=" * 64)
    print("H3: push2his trends2 ndays=45 —— 多日指数分钟（分钟级交易日历）？")
    print("=" * 64)
    url = (f"{HIS}/api/qt/stock/trends2/get?secid=1.000001&fields1=f1,f2,f3,f7"
           f"&fields2=f51,f53,f56&iscr=0&ndays=45&ut={UT}")
    j = flowdata._get(url)
    if not j or not j.get("data") or not j["data"].get("trends"):
        print("  无数据！")
        return
    tr = j["data"]["trends"]
    dates = sorted({line.split(",")[0][:10] for line in tr})
    print(f"  {len(tr)} 条，{len(dates)} 个交易日，{dates[0]} ~ {dates[-1]}")
    bad = [d for d in dates if datetime.date.fromisoformat(d).weekday() >= 5]
    print(f"  含周末: {bad if bad else '无'}")
    print(f"  全部: {dates}")


def h4_daily_kline_calendar():
    print()
    print("=" * 64)
    print("H4: push2his kline/get klt=101 —— 日 K（备选交易日历）？")
    print("=" * 64)
    url = (f"{HIS}/api/qt/stock/kline/get?secid=1.000001&klt=101&fqt=0&lmt=45"
           f"&fields1=f1,f2,f3,f7&fields2=f51,f52&ut={UT}")
    j = flowdata._get(url)
    if not j or not j.get("data") or not j["data"].get("klines"):
        print("  无数据！")
        return
    kl = j["data"]["klines"]
    dates = [line.split(",")[0] for line in kl]
    print(f"  {len(kl)} 条，{dates[0]} ~ {dates[-1]}")
    bad = [d for d in dates if datetime.date.fromisoformat(d).weekday() >= 5]
    print(f"  含周末: {bad if bad else '无'}")
    print(f"  全部: {dates}")


if __name__ == "__main__":
    print(f"开始: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    h1_minute_history()
    h2_daykline()
    h3_trends_multi()
    h4_daily_kline_calendar()
    print()
    print("完毕。")
