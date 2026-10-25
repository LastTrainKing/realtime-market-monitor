# -*- coding: utf-8 -*-
"""
probe_api5.py —— 最终轮：daykline 上限 + 多板块一致性 + 指数历史最后尝试
  T1. daykline lmt ∈ {42,45,48,50,55} 精确上限
  T2. 5 个不同板块 daykline 抽查：是否都有 40 天、日期是否一致（=日历可信）
  T3. push2his trends2 + 经典 ut（指数历史分钟最后尝试）
  T4. 板块日级资金流字段完整性（f52~f56 五路资金）
"""
import os, sys, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata

UT_FLOW = flowdata.UT
UT_WEB = "fa5fd1943c7b386f172d6893dbfba10b"
HIS = "https://push2his.eastmoney.com"


def daykline(secid, lmt):
    url = (f"{HIS}/api/qt/stock/fflow/daykline/get?lmt={lmt}&klt=101&secid={secid}"
           f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT_FLOW}")
    j = flowdata._get(url)
    if not j or not j.get("data") or not j["data"].get("klines"):
        return None
    return j["data"]["klines"]


def t1_cap():
    print("=" * 64)
    print("T1: daykline lmt 精确上限")
    print("=" * 64)
    for lmt in (42, 45, 48, 50, 55):
        kl = daykline("90.BK0475", lmt)
        if kl is None:
            print(f"  lmt={lmt}: 无数据")
        else:
            print(f"  lmt={lmt}: {len(kl)} 条")


def t2_multi_sector():
    print()
    print("=" * 64)
    print("T2: 多板块 daykline 一致性与日历可信度")
    print("=" * 64)
    secs = ["90.BK0475", "90.BK0737", "90.BK1046", "90.BK0800", "90.BK1180"]
    ref_dates = None
    for s in secs:
        kl = daykline(s, 40)
        if kl is None:
            print(f"  {s}: 无数据！")
            continue
        dates = [l.split(",")[0][:10] for l in kl]
        wknd = [d for d in dates if datetime.date.fromisoformat(d).weekday() >= 5]
        print(f"  {s}: {len(kl)} 天，{dates[0]} ~ {dates[-1]}，周末: {wknd if wknd else '无'}")
        if ref_dates is None:
            ref_dates = dates
        else:
            print(f"    与基准日期完全一致: {dates == ref_dates}")
    if ref_dates:
        print(f"  基准日历({len(ref_dates)} 天): {ref_dates}")


def t3_trends_his_classic():
    print()
    print("=" * 64)
    print("T3: push2his trends2 + 经典 ut（指数历史分钟最后尝试）")
    print("=" * 64)
    url = (f"{HIS}/api/qt/stock/trends2/get?secid=1.000001&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=5&ut={UT_WEB}")
    j = flowdata._get(url)
    if not j or not j.get("data") or not j["data"].get("trends"):
        print("  无数据 —— 指数历史分钟确认不可得")
        return
    tr = j["data"]["trends"]
    dates = sorted({l.split(",")[0][:10] for l in tr})
    print(f"  意外成功: {len(tr)} 条，{len(dates)} 天")


def t4_field_check():
    print()
    print("=" * 64)
    print("T4: 日级五路资金字段完整性（与分钟数据同构）")
    print("=" * 64)
    kl = daykline("90.BK0475", 3)
    if kl:
        print("  字段序: 日期, 主力净流入, 小单净流入, 中单净流入, 大单净流入, 超大单净流入")
        for l in kl:
            print(f"  {l}")


if __name__ == "__main__":
    print(f"开始: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    t1_cap()
    t2_multi_sector()
    t3_trends_his_classic()
    t4_field_check()
    print()
    print("完毕。")
