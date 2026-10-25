# -*- coding: utf-8 -*-
"""
probe_api.py —— v1.1.0 前置实测（任务 #31）

探测三个决定补全方案设计的问题：
  Q1. fflow/kline/get?klt=1 能否取到【历史日】的分钟级资金流？
      - lmt=0    （现网行为，预期只有当日）
      - lmt=5000 （若按"最近 N 条"返回，5000 条 ≈ 20 个交易日，可跨日）
  Q2. trends2/get?ndays=N 能否给出交易日历（用于排除周末/节假日）？
  Q3. fflow/kline/get?klt=101（日级资金流）能否取历史日？（降级方案候选）
"""
import os, sys, json, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata

SECID_TEST = "90.BK0475"   # 银行板块（长期存在，数据稳定）
UT = flowdata.UT


def q1_fflow_minute_history():
    print("=" * 64)
    print("Q1: fflow/kline klt=1 分钟资金流能否跨日？")
    print("=" * 64)
    for lmt in (0, 5000):
        url = (f"/api/qt/stock/fflow/kline/get?lmt={lmt}&klt=1&secid={SECID_TEST}"
               f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT}")
        j = flowdata._get(url)
        if not j or not j.get("data") or not j["data"].get("klines"):
            print(f"  lmt={lmt}: 无数据返回")
            continue
        kl = j["data"]["klines"]
        dates = sorted({line.split(",")[0][:10] for line in kl})
        print(f"  lmt={lmt}: 共 {len(kl)} 条，覆盖日期 {len(dates)} 天")
        print(f"    日期范围: {dates[0]} ~ {dates[-1]}")
        if len(dates) > 1:
            print(f"    全部日期: {dates}")
        else:
            # 只有一天：看首末时间戳
            print(f"    首条: {kl[0].split(',')[0]}   末条: {kl[-1].split(',')[0]}")


def q2_trading_calendar():
    print()
    print("=" * 64)
    print("Q2: trends2/get ndays=N 作交易日历")
    print("=" * 64)
    url = (f"/api/qt/stock/trends2/get?secid=1.000001&fields1=f1,f2,f3,f7"
           f"&fields2=f51,f53,f56&iscr=0&ndays=45&ut={UT}")
    j = flowdata._get(url)
    if not j or not j.get("data") or not j["data"].get("trends"):
        print("  无数据返回！")
        return
    tr = j["data"]["trends"]
    dates = sorted({line.split(",")[0][:10] for line in tr})
    print(f"  共 {len(tr)} 条分时，覆盖 {len(dates)} 个交易日")
    print(f"  范围: {dates[0]} ~ {dates[-1]}")
    print(f"  全部交易日: {dates}")
    # 校验：是否全是周一~周五
    bad = [d for d in dates if datetime.date.fromisoformat(d).weekday() >= 5]
    print(f"  含周末的日期: {bad if bad else '无（已排除周末）'}")


def q3_fflow_daily_history():
    print()
    print("=" * 64)
    print("Q3: fflow/kline klt=101 日级资金流历史（降级候选）")
    print("=" * 64)
    for lmt in (40, 120):
        url = (f"/api/qt/stock/fflow/kline/get?lmt={lmt}&klt=101&secid={SECID_TEST}"
               f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={UT}")
        j = flowdata._get(url)
        if not j or not j.get("data") or not j["data"].get("klines"):
            print(f"  lmt={lmt}: 无数据返回")
            continue
        kl = j["data"]["klines"]
        dates = [line.split(",")[0][:10] for line in kl]
        print(f"  lmt={lmt}: 共 {len(kl)} 条，{dates[0]} ~ {dates[-1]}")
        print(f"    样例首条: {kl[0]}")
        print(f"    样例末条: {kl[-1]}")


def q4_clist_history_check():
    """附加探测：clist/get 的板块清单是否带交易日标记（板块存活性）。"""
    print()
    print("=" * 64)
    print("Q4: 板块总数确认（历史面板需要全量板块 × 历史日）")
    print("=" * 64)
    ind = flowdata.list_sectors("industry")
    con = flowdata.list_sectors("concept")
    print(f"  行业板块: {len(ind)}，概念板块: {len(con)}，合计 {len(ind)+len(con)}")


if __name__ == "__main__":
    t0 = datetime.datetime.now()
    print(f"开始实测: {t0:%Y-%m-%d %H:%M:%S} (周{'一二三四五六日'[t0.weekday()]})")
    q1_fflow_minute_history()
    q2_trading_calendar()
    q3_fflow_daily_history()
    q4_clist_history_check()
    print()
    print("实测完毕。")
