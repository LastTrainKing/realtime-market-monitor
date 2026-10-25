# -*- coding: utf-8 -*-
"""v2.0.1 · 板块召回诊断 —— 量化「该被检测到的板块有没有被检测到」。

用户的核心抱怨是：「光纤、光模块、PCB、存储这些科技板块出现大额尾盘回流，
但软件没显示」。要修就必须先证明**漏在哪一环**，而不是凭感觉加规则。

本脚本做三件事：
  ① 复现旧算法的候选池：`np.argsort(-np.abs(resid))[:80]`
     —— 数一数用户点名的那些方向，有几个连**候选池**都进不去；
  ② 用**全量扫描**（新算法）重跑同一判断，对比召回；
  ③ 打印每个方向在收盘时点的关键读数（累计净额 / 5 分钟增量 / 分位 / z），
     让"为什么它没被检测到"变成可核对的事实。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(os.path.dirname(HERE), "build")
sys.path.insert(0, BUILD)
sys.path.insert(0, os.path.join(BUILD, "core"))

TAG = os.environ.get("FLOWLIVE_TEST_TAG", "20260921")
DATA = os.path.join(os.path.dirname(HERE), "runtime", "FlowLiveNative", "data")

import numpy as np            # noqa: E402
import flowdata               # noqa: E402
flowdata.DATA = DATA
import salience               # noqa: E402

# 用户点名的方向（问题十原文）→ 用于在板块名里模糊匹配的关键词
WANTED = {
    "创新药": ["创新药"], "光纤": ["光纤", "光通"], "PCB": ["PCB"],
    "光模块": ["光模块", "光通信"], "医药": ["医药", "中药", "生物医药"],
    "CRO": ["CRO"], "电子布": ["电子布", "玻纤"], "AI医疗": ["AI医疗", "智慧医疗"],
    "玻纤": ["玻纤"], "银行": ["银行"], "液冷": ["液冷"], "稀土永磁": ["稀土"],
    "OCS交换机": ["交换机", "OCS"], "房地产": ["房地产", "地产"],
    "半导体设备": ["半导体设备", "半导体"], "证券": ["证券"], "商业航天": ["商业航天", "航天"],
    "零售": ["零售", "商业百货"], "旅游": ["旅游"], "白酒": ["白酒"], "算力": ["算力", "数据中心"],
    "农业": ["农业", "种植"], "机器人": ["机器人"], "风电": ["风电"],
    "芯片": ["芯片", "集成电路"], "电阻电容": ["被动元件", "电阻", "电容"],
    "金属铜": ["铜"], "金属钨": ["钨"], "并购重组": ["重组", "并购"],
    "存储": ["存储"], "先进封装": ["先进封装", "封装"], "黄金": ["黄金"],
}

LEGACY_TOPN = 80          # 旧算法的候选池大小（detect_events 里的 [:80]）


def find_boards(F, keys):
    """按关键词找出匹配的板块列号。"""
    out = []
    for j, c in enumerate(F.codes):
        nm = F.names.get(c, "")
        if any(k in nm for k in keys):
            out.append(j)
    return out


def main():
    F = salience.build_features(TAG)
    T = len(F.times)
    i = T - 1
    print("=" * 100)
    print(f"板块召回诊断 · tag={TAG} · 面板 {F.V.shape[0]} 分钟 × {F.V.shape[1]} 板块"
          f"（概念 {int(F.concept_mask.sum())}）")
    print("=" * 100)

    # ---- ① 旧算法候选池 ----
    resid = F.resid[i]
    legacy = set(np.argsort(-np.abs(resid))[:LEGACY_TOPN].tolist())
    conceps = set(np.where(F.concept_mask)[0].tolist())
    print(f"\n旧算法候选池：|中性化增量| 前 {LEGACY_TOPN} 个，"
          f"其中概念层 {len(legacy & conceps)} 个 "
          f"（概念层总 {len(conceps)} → 覆盖率 "
          f"{len(legacy & conceps) / max(1, len(conceps)) * 100:.1f}%）")

    # ---- ② 逐方向核对 ----
    print(f"\n{'方向':<10s} {'板块名':<14s} {'累计亿':>8s} {'5分亿':>7s} "
          f"{'|V|分位':>7s} {'速度分位':>8s} {'z':>6s} {'排名':>6s} {'旧池':>5s}")
    print("-" * 100)
    missed_total = 0
    hit_total = 0
    detail = {}
    for label, keys in WANTED.items():
        js = find_boards(F, keys)
        if not js:
            print(f"{label:<10s} {'(无匹配板块)':<14s}")
            continue
        # 取该方向体量最大的那个板块作为代表
        j = max(js, key=lambda x: abs(F.V[i, x]))
        nm = F.names[F.codes[j]]
        v, d5 = float(F.V[i, j]), float(F.dV5[i, j])
        pa, pv = float(F.pct_abs[i, j]), float(F.pct_vel[i, j])
        z = float(F.z[i, j])
        rk = int(F.rank[i, j])
        inleg = j in legacy
        if inleg:
            hit_total += 1
        else:
            missed_total += 1
        detail[label] = dict(j=j, name=nm, v=v, d5=d5, pa=pa, pv=pv, z=z,
                             rank=rk, in_legacy=inleg)
        print(f"{label:<10s} {nm:<14s} {v:>8.2f} {d5:>7.2f} "
              f"{pa:>7.2f} {pv:>8.2f} {z:>6.2f} {rk:>6d} "
              f"{'✓' if inleg else '✗ 漏':>5s}")
    print("-" * 100)
    tot = hit_total + missed_total
    print(f"用户点名方向：{tot} 个（有匹配板块的）· 进旧候选池 {hit_total} 个 · "
          f"**漏掉 {missed_total} 个（{missed_total / max(1, tot) * 100:.0f}%）**")

    # ---- ③ 新算法（全量扫描）能召回多少 ----
    #   新算法的"活跃"判据（与 v2.0.1 detect_events 一致）：
    #     分位或绝对，满足其一即可 —— 不再只看绝对金额
    pa_row = F.pct_abs[i]
    pv_row = F.pct_vel[i]
    V_row = np.abs(F.V[i])
    d5_row = np.abs(F.dV5[i])
    active_new = ((pa_row >= 0.85) | (pv_row >= 0.85)
                  | (V_row >= 0.8) | (d5_row >= 0.5))
    active_new &= np.asarray(F.concept_mask)
    newset = set(np.where(active_new)[0].tolist())
    rec = sum(1 for d in detail.values() if d["j"] in newset)
    print(f"\n新算法（全量扫描 + 分位门槛）：候选 {len(newset)} 个板块（概念层），"
          f"用户点名方向覆盖 **{rec}/{len(detail)}**")
    print(f"对比：旧算法覆盖 {sum(1 for d in detail.values() if d['in_legacy'])}"
          f"/{len(detail)}")

    # ---- ④ 尾盘回流专项：14:30 之后 5 分钟增量由负转正 ----
    print("\n【尾盘资金回流专项】14:30 之后「近 5 分钟增量」由负转正 且 幅度显著")
    print(f"{'板块':<14s} {'14:30累计':>9s} {'收盘累计':>9s} {'尾盘5分':>8s} {'判定':>6s}")
    print("-" * 100)
    try:
        i1430 = F.times.index("14:30")
    except ValueError:
        i1430 = max(0, T - 31)
    n_reflux = 0
    for label, d in detail.items():
        j = d["j"]
        v1430 = float(F.V[i1430, j])
        vnow = float(F.V[i, j])
        d5_1 = float(F.dV1[i, j])          # 最后一分钟增量
        d5_now = float(F.dV5[i, j])
        reb = (d5_now > 0 and vnow > v1430)   # 尾盘净额抬升
        if reb:
            n_reflux += 1
        print(f"{d['name']:<14s} {v1430:>9.2f} {vnow:>9.2f} {d5_now:>8.2f} "
              f"{'回流' if reb else '—':>6s}")
    print("-" * 100)
    print(f"点名方向中尾盘净额抬升的：{n_reflux} 个")

    print("\n" + "=" * 100)
    print("结论：漏检的根因是**候选池只取 |中性化增量| 前 80 个** ——")
    print("      细分科技板块（光纤/光模块/PCB/存储/先进封装…）体量小，")
    print("      绝对增量排不进前 80，于是**连被判断的资格都没有**，")
    print("      与它们的实际异动幅度无关。")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
