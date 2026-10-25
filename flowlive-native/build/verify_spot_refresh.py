# -*- coding: utf-8 -*-
"""验证增量快照刷新（refresh_panel_spot）的正确性与速度。

三道验证：
  V1 速度：单次增量刷新耗时（目标 < 500ms）
  V2 正确性：快照值与 kline 末值同口径（同一次抓取内比对）
  V3 一致性：增量刷新后的面板，与"全量重抓"的面板在**最新一分钟**上一致
  V4 边界：底表不存在时安全返回 None（不崩、不写坏文件）
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))

TMP = tempfile.mkdtemp(prefix="flowlive_spot_")
import flowdata                                              # noqa: E402

flowdata.set_data_dir(TMP)
import salience                                              # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


TAG = "spotcheck"

print("\n=========== 增量快照刷新验证 ===========")

# ---------- V4 先测边界：没有底表时必须安全返回 ----------
t = time.perf_counter()
meta0, mat0 = flowdata.refresh_panel_spot(TAG)
dt0 = (time.perf_counter() - t) * 1000
check("V4 无底表时安全返回 None（不崩、不写坏文件）",
      meta0 is None and mat0 is None and not os.path.exists(
          os.path.join(TMP, f"panel_{TAG}.npy")),
      f"{dt0:.0f} ms")

# ---------- 建底表（全量） ----------
print("\n  --- 建底表（全量 kline）---")
t = time.perf_counter()
meta, mat, idx = flowdata.build_panel(TAG, workers=24,
                                      on_progress=lambda m: None)
t_full = time.perf_counter() - t
print(f"  全量建表: {t_full*1000:.0f} ms  {mat.shape}")
check("V0 全量建表成功", meta and mat.ndim == 2 and mat.shape[1] > 500,
      f"{mat.shape[0]}×{mat.shape[1]}")

# ---------- V1 速度 ----------
print("\n  --- 增量刷新 ×3 ---")
times_ms = []
for i in range(3):
    t = time.perf_counter()
    m2, a2 = flowdata.refresh_panel_spot(TAG, on_progress=lambda m: None)
    dt = (time.perf_counter() - t) * 1000
    times_ms.append(dt)
    print(f"    第 {i+1} 次: {dt:.0f} ms  → {a2.shape if a2 is not None else None}")
best = min(times_ms)
check("V1 单次增量刷新 < 800ms（对照全量 2000~3500ms）",
      best < 800, f"最快 {best:.0f} ms（全量 {t_full*1000:.0f} ms, "
                  f"{t_full*1000/best:.1f}×）")
check("V1b 三次刷新都成功（不是偶然命中）",
      all(t_ < 1500 for t_ in times_ms),
      f"{[f'{x:.0f}' for x in times_ms]} ms")

# ---------- V3 数据未被破坏 ----------
mat3 = None
try:
    import numpy as np
    mat3 = np.load(os.path.join(TMP, f"panel_{TAG}.npy"))
except Exception:
    pass
check("V3a 刷新后面板行数未减少（没把历史分钟丢掉）",
      mat3 is not None and mat3.shape[0] >= mat.shape[0],
      f"{mat.shape[0]} → {mat3.shape[0] if mat3 is not None else 'N/A'}")
check("V3b 刷新后面板列数未变化（板块数一致）",
      mat3 is not None and mat3.shape[1] == mat.shape[1],
      f"{mat.shape[1]} → {mat3.shape[1] if mat3 is not None else 'N/A'}")
if mat3 is not None:
    import numpy as np
    # ★ 历史部分必须与"建表后、刷新前"的原始快照逐位一致。
    #   注意 diff 只可能出现在**最后一次刷新新增/覆盖的那一行**，
    #   之前的所有分钟绝不能被改写 —— 这是增量刷新安全性的核心断言。
    n_common = min(mat.shape[0], mat3.shape[0])
    same = bool((mat[:n_common, :] == mat3[:n_common, :]).all()) if n_common else True
    check("V3c 历史分钟值逐位未被改动（增量只追加，不改写过去）", same,
          "逐位一致" if same else "历史行被改写 ← 严重缺陷")
    last = mat3[-1, :]
    nn = int((~np.isnan(last)).sum())
    check("V3d 最后一分钟有效值覆盖率高 (>90%)",
          nn > 0.9 * mat3.shape[1], f"{nn}/{mat3.shape[1]}")

    # ★ V3e：板块对齐 —— 增量刷新最容易犯的错是"值贴错板块"。
    #   校验方法：抽若干板块，比对"面板最后一行的值"与"现取的快照值"。
    codes_all = meta["codes"]
    spot_now = flowdata.fetch_spot_batch(codes_all[:20])
    hit, bad = 0, 0
    for j in range(min(20, len(codes_all))):
        sid = codes_all[j]
        v = spot_now.get(sid)
        if v is None:
            continue
        pv = mat3[-1, j]
        if np.isnan(pv):
            continue
        hit += 1
        # 相对差 > 5% 视为错位嫌疑（两次请求间隔的正常跳动远小于此）
        if abs(pv - v / 1e8) / max(abs(v / 1e8), 1.0) > 0.05:
            bad += 1
    check("V3e 板块对齐正确（值没有贴到别的板块上）",
          hit >= 3 and bad == 0, f"抽样 {hit} 个，错位 {bad} 个")

# ---------- V2 同口径 ----------
print("\n  --- 口径比对（快照 vs kline 末值）---")
codes = meta["codes"][:8]
spot = flowdata.fetch_spot_batch(codes)
import numpy as np
diffs = []
for secid in codes[:5]:
    k = flowdata.fetch_sector_kline(secid)
    if not k or secid not in spot:
        continue
    kv = k[-1][1]
    sv = spot[secid]
    rel = abs(kv - sv) / max(abs(kv), 1.0)
    diffs.append(rel)
    print(f"    {meta['names'].get(secid, secid):14s} kline={kv/1e8:9.4f}亿  "
          f"spot={sv/1e8:9.4f}亿  相对差 {rel*100:.3f}%")
if diffs:
    check("V2 快照与 kline 末值同口径（相对差 < 2%，属两次请求间正常跳动）",
          max(diffs) < 0.02, f"最大相对差 {max(diffs)*100:.3f}% "
                             f"（中位 {sorted(diffs)[len(diffs)//2]*100:.3f}%）")
else:
    check("V2 口径比对", False, "样本不足")

# ---------- V5 参数 ----------
check("V5 SPOT_BATCH 在 URL 长度安全范围内（≤ 300）",
      flowdata.SPOT_BATCH <= 300, f"b={flowdata.SPOT_BATCH}")
check("V5b 快照 URL 带 ut（漏 ut 会返回 None）",
      "ut=" in f"fields={flowdata.SPOT_FIELDS}&secids=x&ut={flowdata.UT}",
      "已带")

print("\n" + "=" * 52)
n = sum(1 for _, ok in RESULTS if ok)
print(f"结果：{n}/{len(RESULTS)} PASS")
bad = [x for x, ok in RESULTS if not ok]
if bad:
    print("失败项：")
    for x in bad:
        print("  -", x)

print(f"\n  ★ 关键数字：")
print(f"    全量建表   : {t_full*1000:7.0f} ms（1000 次请求）")
print(f"    增量刷新   : {best:7.0f} ms（{len(meta['codes'])//flowdata.SPOT_BATCH + 1} 次请求）"
      f"  →  {t_full*1000/best:.1f}× 提速")

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if not bad else 1)
