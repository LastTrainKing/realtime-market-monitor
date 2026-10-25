# -*- coding: utf-8 -*-
"""test_universe_v306.py —— 板块宇宙"分层退化 + 无历史剔除"专项回归（**完全离线**）。

对应 2026-09-23 18:30 用户报告：「大部分数据不显示…首页板块也只有 5 个」。

【这条链是怎么断的（实测复现，不是推测）】
  1. 本机 IP 被东财 push2 封禁 → 全量抓取只取到 **122** 个板块就写盘，
     而这 122 个**几乎全是二/三级行业**（抓取顺序是"行业在前"）；
  2. 程序把替代源（腾讯）的板块**并入**该面板 → 面板变成 **956 列**，
     其中 **834 列只有"当前 1 个点"**、122 列才有完整的 240 分钟历史；
  3. `clean_universe` 的行业白名单只留 31 个一级行业 → 命中 5 个（有历史）
     + 31 个（并入的、无历史）= 36 个；
  4. `salience.build_features` 里的**市场中性化**
     `resid = dV5 - dV5.mean(axis=1)` 只要某时刻有**任意一列**是 NaN，
     该时刻均值就是 NaN → **污染全部板块**。
     实测 `F.V` 只剩 1.0% 有限值、`dV5.mean(axis=1)` 只剩 2.1% → 界面全空。

【本文件守住的不变量】
  · 有历史的列**必须**活下来（哪怕它不在行业白名单里）；
  · 无历史的列**必须**在进分析前剔除（否则 NaN 会污染全表）；
  · 健康面板**完全不受影响**（不能为了救残缺面板把正常路径改坏）。
"""
import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
sys.path.insert(0, HERE)

import universe as uni                                                  # noqa: E402
import flowdata as fd                                                   # noqa: E402
import salience                                                         # noqa: E402

PASS = 0
FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


PRIMARY = sorted(uni.PRIMARY_INDUSTRIES)


def mk_meta(codes, names, kinds):
    return {"codes": list(codes), "names": dict(names), "kinds": dict(kinds),
            "times": ["09:%02d" % (31 + i) for i in range(240)],
            "source": "live", "data_date": "20260923"}


def mk_mat(cols, hist_points, rows=240, nan_fill=np.nan):
    """cols 列、每列前 hist_points 个点有效（其余 NaN）——模拟"列在历史不在"。"""
    m = np.full((rows, cols), nan_fill, dtype=float)
    for j in range(cols):
        k = hist_points[j] if isinstance(hist_points, (list, tuple)) else hist_points
        if k > 0:
            m[:min(k, rows), j] = 1.0 + j
    return m


# ======================================================================
print("=" * 72)
print("A. 健康面板：必须完全不受影响（回归护栏）")
print("=" * 72)
# 1000 列：31 个一级行业 + 465 个细分行业 + 504 个概念，全部有 240 点历史
codes, names, kinds = [], {}, {}
for i, nm in enumerate(PRIMARY):
    c = "90.BK1%03d" % i
    codes.append(c); names[c] = nm; kinds[c] = "industry"
for i in range(465):
    c = "90.BK2%03d" % i
    codes.append(c); names[c] = "细分行业%d" % i; kinds[c] = "industry"
for i in range(504):
    c = "90.BK3%03d" % i
    codes.append(c); names[c] = "概念%d" % i; kinds[c] = "concept"
meta = mk_meta(codes, names, kinds)
hist_ok = {c: 240 for c in codes}
kept, _n, _k, dr = uni.clean_universe(meta)
# 行业层留 31 个一级行业；概念层 504 个全部保留（"概念N" 不是统计桶）
check(len(kept) == 31 + 504,
      "A1 健康面板：31 个一级行业 + 504 个概念（白名单与桶过滤都生效）",
      f"{len(kept)} 个")
check(dr.get("行业层级") == 465 and dr.get("统计桶") == 0,
      "A2 健康面板：465 个细分行业被白名单丢掉、无统计桶误伤", str(dr))
check("行业层退化_不过滤" not in dr and "行业层补回有历史" not in dr,
      "A3 健康面板：**不触发任何退化**")
kept2, _n2, _k2, dr2 = uni.clean_universe(meta, hist=hist_ok)
check(len(kept2) == len(kept) and "无历史剔除" not in dr2,
      "A4 ★ 健康面板：传了 hist 也不改变结果（每列都有历史 → 不剔除）",
      f"{len(kept2)} 个 {dr2}")
check(uni.clean_universe(meta)[0] == kept2,
      "A5 传/不传 hist 对健康面板结果一致（向后兼容）")


# ======================================================================
print()
print("=" * 72)
print("B. 被截断的面板（122 列，全是二/三级行业）")
print("=" * 72)
codes2, names2, kinds2 = [], {}, {}
for i in range(5):
    c = "90.BK4%03d" % i
    codes2.append(c); names2[c] = PRIMARY[i]; kinds2[c] = "industry"
for i in range(117):
    c = "90.BK5%03d" % i
    codes2.append(c); names2[c] = "住宅开发%d" % i; kinds2[c] = "industry"
meta2 = mk_meta(codes2, names2, kinds2)
hist2 = {c: 240 for c in codes2}
kept, _n, _k, dr = uni.clean_universe(meta2, hist=hist2)
check(len(kept) == 122,
      "B1 ★★ 122 列全有历史 → 全部保留（否则界面塌成 5 个）", f"{len(kept)} 个")
_kept_b2, _n, _k, _d2 = uni.clean_universe(meta2)
check(len(_kept_b2) == 122 and _d2.get("退化_不过滤"),
      "B2 ★ 不传 hist 时由「总数判据」兜住 → 也是 122（两道防线互补）",
      f"{len(_kept_b2)} 个 {_d2}")


# ======================================================================
print()
print("=" * 72)
print("C. ★★ 并入替代源之后的面板（本次真实故障形态）")
print("=" * 72)
# 5 个一级行业（有 240 点历史）+ 31 个并入的一级行业（只有 1 点）
# + 117 个细分行业（有 240 点历史）+ 803 个并入概念（只有 1 点）
codes3, names3, kinds3, hist3 = [], {}, {}, {}
for i in range(5):
    c = "90.BK6%03d" % i
    codes3.append(c); names3[c] = PRIMARY[i]; kinds3[c] = "industry"; hist3[c] = 240
for i in range(5, 31):          # 剩下 26 个一级行业由替代源并入
    c = "qt.pt%d" % i
    codes3.append(c); names3[c] = PRIMARY[i]; kinds3[c] = "industry"; hist3[c] = 1
for i in range(117):
    c = "90.BK7%03d" % i
    codes3.append(c); names3[c] = "商用载货车%d" % i; kinds3[c] = "industry"
    hist3[c] = 240
for i in range(803):
    c = "qt.gn%d" % i
    codes3.append(c); names3[c] = "并入概念%d" % i; kinds3[c] = "concept"
    hist3[c] = 1
meta3 = mk_meta(codes3, names3, kinds3)
kept3, _n3, _k3, dr3 = uni.clean_universe(meta3, hist=hist3)
ind3 = [c for c in kept3 if kinds3[c] == "industry"]
con3 = [c for c in kept3 if kinds3[c] == "concept"]
check(len(codes3) == 951, "C1 构造出 951 列的面板（与实测 956 同形态）", str(len(codes3)))
check(len(ind3) == 122,
      "C2 ★★ 行业层：补回 117 个有历史的细分行业（5+117=122）", f"{len(ind3)} 个")
check(len(con3) == 0,
      "C3 ★★ 概念层：803 个「有名字没历史」的并入列全部剔除", f"{len(con3)} 个")
check(all(hist3[c] >= uni.HIST_MIN_POINTS for c in kept3),
      "C4 ★★ 保留下来的每一列都有可用历史（这是不变量）")
check(dr3.get("行业层补回有历史") == 117,
      "C5 如实记录「补回有历史」的数量", str(dr3.get("行业层补回有历史")))
# 803 个并入概念 + 26 个并入一级行业 = 829 个「有名字没历史」的列被剔除
check(dr3.get("无历史剔除") == 829,
      "C6 如实记录「无历史剔除」的数量", str(dr3.get("无历史剔除")))
check(len(kept3) == 122,
      "C7 最终宇宙 = 122（对照：修复前只有 5）", f"{len(kept3)} 个")


# ======================================================================
print()
print("=" * 72)
print("D. ★★★ 核心不变量：分析结果**不能**被 NaN 污染")
print("=" * 72)
_tmp = tempfile.mkdtemp(prefix="flowlive_univ_")
_old = fd.DATA
fd.set_data_dir(_tmp)
try:
    # 把 C 组那块面板真落盘，走完整的 build_features 链路
    mat3 = np.full((240, len(codes3)), np.nan)
    for j, c in enumerate(codes3):
        k = hist3[c]
        mat3[:k, j] = 1.0 + j
    fd.save_panel("20260923", meta3, mat3)
    F = salience.build_features("20260923")
    finV = float(np.isfinite(F.V).mean())
    d5 = np.asarray(F.dV5).mean(axis=1)
    fin5 = float(np.isfinite(d5).mean())
    check(len(F.codes) == 122,
          "D1 特征层宇宙 = 122", f"{len(F.codes)} 个")
    check(finV > 0.99,
          "D2 ★★★ F.V 几乎全为有限值（修复前实测只有 1.0%）",
          f"{finV * 100:.1f}%")
    check(fin5 > 0.99,
          "D3 ★★★ dV5.mean(axis=1) 几乎全为有限值 —— **市场中性化不再被污染**"
          "（修复前实测只有 2.1%）", f"{fin5 * 100:.1f}%")
    check(np.isfinite(F.rank).mean() > 0.99,
          "D4 排名矩阵不再是 NaN 泥潭",
          f"{np.isfinite(F.rank).mean() * 100:.1f}%")
finally:
    fd.set_data_dir(_old)


# ======================================================================
print()
print("=" * 72)
print("E. 阈值本身也要守住（避免有人手滑把门槛改没）")
print("=" * 72)
check(uni.HIST_MIN_POINTS >= 5,
      "E1 HIST_MIN_POINTS 至少 5（1 个点的列绝不算「有历史」）",
      str(uni.HIST_MIN_POINTS))
check(uni.UNIVERSE_MIN_TOTAL >= 20, "E2 面板规模门槛存在",
      str(uni.UNIVERSE_MIN_TOTAL))
check(uni.INDUSTRY_MIN_KEEP >= 10, "E3 行业层门槛存在",
      str(uni.INDUSTRY_MIN_KEEP))
# 边界：恰好 HIST_MIN_POINTS 点应当**保留**（>= 语义）
codes4 = ["90.BK8%03d" % i for i in range(40)]
names4 = {c: PRIMARY[i % 31] for i, c in enumerate(codes4)}
kinds4 = {c: "industry" for c in codes4}
hist4 = {c: uni.HIST_MIN_POINTS for c in codes4}
kept4, _n4, _k4, _d4 = uni.clean_universe(mk_meta(codes4, names4, kinds4),
                                          hist=hist4)
# 40 个代码的名字都取自 PRIMARY（会重复），白名单按**名字**判 → 40 个全保留；
# 关键断言是"没有任何一列因为 hist==HIST_MIN_POINTS 被剔掉"
check(len(kept4) == 40 and "无历史剔除" not in _d4,
      "E4 恰好等于门槛的列被保留（>= 而不是 >）",
      f"{len(kept4)} 个 {_d4}")

# ======================================================================
print()
print("=" * 72)
print("F. ★★ GBK 安全：一条日志文案**绝不能**把数据加载搞崩")
print("=" * 72)
# 实测事故（2026-09-23 18:38，部署版 exe）：frozen 版 stdout 编码是 GBK，
# 而本模块的 print 里有 `⚠`(U+26A0) —— 不在 GBK 里 → UnicodeEncodeError
# 直接冒到 Worker 错误处理，界面 `LOAD FAILED / 数据加载过程中出现异常`。
# ★ 它只在 frozen + 中文 Windows 上复现，开发机（UTF-8）完全看不到，
#   所以必须用**子进程 + GBK stdout** 把它变成可复现的守卫。
import subprocess                                                       # noqa: E402

_child = r"""
import sys, io
_real = sys.stdout                      # 哨兵要写到**原始** stdout（管道）里
sys.path.insert(0, r"%s")
sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
import universe as u
codes = ["90.BK%%03d" %% i for i in range(120)]
names = {c: ("房地产" if i < 5 else "住宅开发%%d" %% i)
         for i, c in enumerate(codes)}
kinds = {c: "industry" for c in codes}
meta = {"codes": codes, "names": names, "kinds": kinds}
kept, _n, _k, _d = u.clean_universe(meta)
# 再走一次"有历史"的分支（会打印另一条提示）
u.clean_universe(meta, hist={c: 240 for c in codes})
print("CHILD_OK %%d" %% len(kept), file=_real)
""" % os.path.join(HERE, "core")

_r = subprocess.run([sys.executable, "-c", _child],
                    capture_output=True, text=True, errors="replace")
check(_r.returncode == 0,
      "F1 ★★ GBK stdout 下 clean_universe 不抛异常",
      f"exit={_r.returncode} {(_r.stderr or '')[-120:]}")
check("CHILD_OK" in (_r.stdout or ""),
      "F2 子进程正常跑完并给出结果",
      (_r.stdout or "").strip().splitlines()[-1] if _r.stdout else "无输出")

# 直接验证"全局兜底"本身有效：任何非 GBK 字符都不该再崩
_g = r"""
import sys, io
_real = sys.stdout
sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
print("⚠ ★ ✅ ⚡")
print("GBK_SAFE", file=_real)
"""
_r2 = subprocess.run([sys.executable, "-c", _g], capture_output=True,
                     text=True, errors="replace")
check(_r2.returncode == 0 and "GBK_SAFE" in (_r2.stdout or ""),
      "F3 ★★ 全局 reconfigure(errors=replace) 能兜住 ⚠★✅⚡ 等所有危险符号",
      f"exit={_r2.returncode}")

print()
print("=" * 72)
print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 72)
sys.exit(1 if FAIL else 0)
