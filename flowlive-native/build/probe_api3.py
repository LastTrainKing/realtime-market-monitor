# -*- coding: utf-8 -*-
"""
probe_api3.py —— 第三轮：补齐参数重试日历接口
  R1. push2his kline/get 带 beg/end + 完整 fields1 → 日 K 日历
  R2. push2 trends2 ndays=2/5（现网 ndays=1 可用，找断裂点）
  R3. daykline 长度上限：lmt=300 能取多少天（决定日历+历史日级数据纵深）
  R4. daykline 是否支持 f62 等更多字段（净流入 vs 净占比）
"""
import os, sys, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata

UT = flowdata.UT
HIS = "https://push2his.eastmoney.com"
SECID_TEST = "90.BK0475"


def r1_kline_calendar():
    print("=" * 64)
    print("R1: push2his kline/get + beg/end —— 日 K 日历")
    print("=" * 64)
    url = (f"{HIS}/api/qt/stock/kline/get?secid=1.000001&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=0&beg=0&end=20500101&lmt=45&ut={UT}")
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


def r2_trends_ndays():
    print()
    print("=" * 64)
    print("R2: push2 trends2 ndays 断裂点")
    print("=" * 64)
    for nd in (1, 2, 5, 10):
        url = (f"/api/qt/stock/trends2/get?secid=1.000001&fields1=f1,f2,f3,f7"
               f"&fields2=f51,f53,f56&iscr=0&ndays={nd}&ut={UT}")
        j = flowdata._get(url)
        if not j or not j.get("data") or not j["data"].get("trends"):
            print(f"  ndays={nd}: 无数据")
            continue
        tr = j["data"]["trends"]
        dates = sorted({line.split(",")[0][:10] for line in tr})
        print(f"  ndays={nd}: {len(tr)} 条，{len(dates)} 天，{dates[0]} ~ {dates[-1]}")


def r3_daykline_depth():
    print()
    print("=" * 64)
    print("R3: daykline lmt=300 纵深")
    print("=" * 64)
    url = (f"{HIS}/api/qt/stock/fflow/daykline/get?lmt=300&klt=101&secid={SECID_TEST}"
           f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT}")
    j = flowdata._get(url)
    if not j or not j.get("data") or not j["data"].get("klines"):
        print("  无数据！")
        return
    kl = j["data"]["klines"]
    dates = [line.split(",")[0][:10] for line in kl]
    print(f"  {len(kl)} 条，{dates[0]} ~ {dates[-1]}")


def r4_daykline_rich_fields():
    print()
    print("=" * 64)
    print("R4: daykline 完整 fields2（f51~f63）")
    print("=" * 64)
    url = (f"{HIS}/api/qt/stock/fflow/daykline/get?lmt=3&klt=101&secid={SECID_TEST}"
           f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63&ut={UT}")
    j = flowdata._get(url)
    if not j or not j.get("data") or not j["data"].get("klines"):
        print("  无数据！")
        return
    for line in j["data"]["klines"]:
        print(f"  {line}")


if __name__ == "__main__":
    print(f"开始: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    r1_kline_calendar()
    r2_trends_ndays()
    r3_daykline_depth()
    r4_daykline_rich_fields()
    print()
    print("完毕。")
