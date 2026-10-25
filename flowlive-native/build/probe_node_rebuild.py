# -*- coding: utf-8 -*-
"""探针：验证「三个节点能否从历史分钟数据重建」的几个未知项。

★ 为什么需要这个脚本：
  本次分析的沙箱**无法访问东财接口**（经本地代理时 TLS 重协商失败，
  curl 返回 HTTP 000）。因此下列问题**未能实测**，只能推断：
     Q1 板块是否支持 trends2（决定「板块成交量」能否重建）
     Q2 个股 fflow/kline 的历史保留天数
     Q3 分钟线的日期字段跨日完整性
  请在**能直连东财的机器**上跑本脚本，用实测结果替换推断。

用法：
    python probe_node_rebuild.py
"""
import sys
import os
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata                                                       # noqa: E402


def hr(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def q0_basic():
    hr("Q0 连通性：clist 板块列表（若为空，后面全部无意义）")
    j = flowdata._get("/api/qt/clist/get?pn=1&pz=3&po=1&np=1&fltt=2&invt=2"
                      "&fid=f62&fs=m:90+t:2&fields=f12,f14,f5,f6,f62&ut=" + flowdata.UT,
                      tries=2, timeout=10)
    d = ((j or {}).get("data") or {}).get("diff") or []
    if not d:
        print("  ✗ 取不到板块列表 —— 网络不可达或接口变更，**请勿继续下结论**")
        return None
    for r in d:
        print("  ", r)
    return str(d[0].get("f12"))


def q1_sector_trends(secid_hint):
    hr("Q1 板块是否支持 trends2（决定「板块成交量」能否重建）")
    # 板块 secid 形如 90.BK0475
    cands = []
    if secid_hint:
        cands.append("90." + secid_hint)
    cands += ["90.BK0475", "90.BK0428"]
    for sid in cands:
        j = flowdata._get("/api/qt/stock/trends2/get?secid=%s&fields1=f1,f2,f3,f7"
                          "&fields2=f51,f53,f56&iscr=0&ndays=1&ut=%s" % (sid, flowdata.UT),
                          tries=1, timeout=8)
        tr = ((j or {}).get("data") or {}).get("trends") or []
        print("  %-12s trends 条数 = %d" % (sid, len(tr)))
        if tr:
            print("    第 1 条 :", tr[0])
            print("    第 2 条 :", tr[1] if len(tr) > 1 else None)
            print("    最后一条:", tr[-1])
            print("    → 若第 1 条时间在 09:3x 且为单分钟量，则**板块成交量可重建**")
            return True
    print("  ✗ 三个候选都没有 trends 数据 → 板块成交量**暂不可重建**（需另找来源）")
    return False


def q2_stock_history(secid_hint):
    hr("Q2 个股 fflow/kline 历史保留天数（决定盘后能否重建）")
    sid = "1.600519"          # 贵州茅台，长期有数据
    url = ("/api/qt/stock/fflow/kline/get?lmt=0&klt=1&secid=%s"
           "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut=%s"
           % (sid, flowdata.UT))
    j = flowdata._get(url, tries=2, timeout=10)
    kl = ((j or {}).get("data") or {}).get("klines") or []
    print("  %s klines 条数 = %d" % (sid, len(kl)))
    if not kl:
        print("  ✗ 无数据，无法判断保留天数")
        return
    print("    第 1 条 :", kl[0])
    print("    最后一条:", kl[-1])
    days = Counter(x.split(",")[0][:10] for x in kl if len(x.split(",")[0]) >= 10)
    print("    覆盖交易日数 =", len(days))
    print("    每日条数（前 5 日）:", list(days.items())[:5])
    print("    → 常见形态：每日 240 条（全天）；覆盖天数即接口保留窗口")
    # 判断累计 vs 增量
    first_vals = [float(x.split(",")[1]) for x in kl[:5] if len(x.split(",")) > 1]
    print("    前 5 分钟主力净流入 =", first_vals)
    print("    → 若首分钟≈0 且之后递增，则为**当日累计值**（可用来还原节点口径）")


def q3_date_integrity():
    hr("Q3 分钟线日期字段跨日完整性")
    sec = flowdata.list_sectors("industry")
    if not sec:
        print("  ✗ 板块列表为空，跳过")
        return
    rows = flowdata.fetch_sector_kline(sec[0][0])
    print("  板块 %s 分钟线行数 = %d" % (sec[0], len(rows)))
    if rows:
        print("  前 3 行:", rows[:3])
        print("  后 3 行:", rows[-3:])
        print("  ★ 注意：fetch_sector_kline 返回的 hm 是 p[0][-5:]，**已剥掉日期**。")
        print("    若需跨日，必须改用原始 klines 的完整时间戳。")


if __name__ == "__main__":
    print("探针开始 —— 请在有网环境运行，结果将替换 docs/节点可重建性判定.md 中的推断")
    hint = q0_basic()
    q1_sector_trends(hint)
    q2_stock_history(hint)
    q3_date_integrity()
    hr("汇总：把上面 Q1/Q2 的实测结果回填到 docs/节点可重建性判定.md 第四节")
