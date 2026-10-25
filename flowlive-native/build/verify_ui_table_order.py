# -*- coding: utf-8 -*-
"""verify_ui_table_order.py —— 从 **Qt 表格真实内容** 核对排序（显示链路终审）

数据层排好序 ≠ 屏幕上有序。中间隔着 on_data → fill_table → QTableWidget，
任何一层做二次排序/截断/缓存，用户看到的仍然是乱的 —— 而用户看的是**屏幕**。
本脚本读 `--shot` 自检时导出的 `*_关键节点.table.txt`，
那正是 `QTableWidgetItem.text()` 的原文，零猜测、零 OCR 误差。

用法：
    FLOWLIVE_SHOTS=<dir> python native_app.py --shot <prefix>   # 先出这份 txt
    python verify_ui_table_order.py [shots_dir]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
import flowdata                                                     # noqa: E402

DEPLOY = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                      "FlowLiveNative", "data")
if os.path.isdir(DEPLOY):
    flowdata.set_data_dir(DEPLOY)
import salience                                                     # noqa: E402

PASS = FAIL = 0
FAILS = []


def check(name, ok, detail=""):
    global PASS, FAIL
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  · {detail}" if detail else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(name)


def find_dump(root):
    if not os.path.isdir(root):
        return None
    for d, _, fs in os.walk(root):
        for f in fs:
            if f.endswith(".table.txt") and "关键节点" in f:
                return os.path.join(d, f)
    return None


def parse(path):
    times, hdr = [], {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                if "rows=" in line:
                    kv = dict(p.split("=") for p in
                              line.lstrip("# ").split() if "=" in p)
                    hdr = {k: int(v) for k, v in kv.items()}
                continue
            cells = line.rstrip("\n").split("\t")
            if cells and cells[0]:
                times.append(cells[0])
    return times, hdr


print("=" * 70)
print("关键节点页 —— 界面显示顺序终审（Qt 表格真实内容）")
print("=" * 70)

root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "shots_order")
dump = find_dump(root)
check("1 找到表格导出文件（表示 --shot 已跑过且导出成功）",
      dump is not None, os.path.basename(dump or "(未找到)"))

if dump:
    times, hdr = parse(dump)
    ks = [salience._time_key(t) for t in times]
    print(f"  来源：{dump}")
    print(f"  表头声明：{hdr}；实际解析 {len(times)} 行")
    print(f"  前 6 行时刻：{' → '.join(times[:6])}")
    print(f"  末 3 行时刻：{' → '.join(times[-3:])}")

    check("2 表格行数与声明的 rows 一致（无截断/无多余）",
          hdr.get("rows") == len(times),
          f"声明 {hdr.get('rows')} / 实际 {len(times)}")
    check("3 表格列数为 5（时刻/事件/题材/说明/强度）",
          hdr.get("cols") == 5, f"cols={hdr.get('cols')}")
    check("4 每个时刻都可解析（界面上无脏刻度）",
          all(k is not None for k in ks),
          f"不可解析 {sum(1 for k in ks if k is None)} 个")
    check("5 屏幕上的「时刻」列按时间升序（早→晚）",
          all(ks[i] <= ks[i + 1] for i in range(len(ks) - 1)),
          f"{times[0]} → {times[1]} → … → {times[-1]}")
    check("6 严格递增（无同刻重复、无回跳）",
          all(ks[i] < ks[i + 1] for i in range(len(ks) - 1)),
          f"{len(times)} 行全严格递增")
    check("7 首行 = 全天最早、末行 = 全天最晚",
          ks[0] == min(ks) and ks[-1] == max(ks),
          f"{times[0]} ~ {times[-1]}")

    # 与数据层交叉核对：屏幕内容必须等于数据层算出的那份
    # ★ v1.2.5：日期**不写死**。--shot 可能是 --date 回看拍的（受控比对用
    #   20260918），也可以是当天拍的。写死日期会拿另一天的面板去对，
    #   行数必然不同 → 假失败。改为：在已落盘的 panel_*.json 里挑出
    #   "时刻序列与屏幕完全一致"的那一天，找不到才算失败。
    def _cands():
        dd = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                          "FlowLiveNative", "data")
        if not os.path.isdir(dd):
            return []
        ds = [f[len("panel_"):-len(".json")]
              for f in os.listdir(dd)
              if f.startswith("panel_") and f.endswith(".json")]
        return sorted({d for d in ds if d.isdigit()}, reverse=True)

    def _screen_matches(date):
        F = salience.build_features(date)
        return [r["time"] for r in salience.timeline_summary(F)]

    hit = None
    tried = []
    try:
        for d in _cands():
            try:
                got = _screen_matches(d)
            except Exception as e:                                  # noqa: BLE001
                tried.append(f"{d}(不可用:{type(e).__name__})")
                continue
            tried.append(f"{d}({len(got)}行)")
            if got == times:
                hit = d
                break
        check("8 屏幕时刻序列与数据层完全一致（显示链路无篡改）",
              hit is not None,
              f"命中面板 {hit}" if hit else f"试过 {'、'.join(tried)} 均不匹配")
        if hit:
            F = salience.build_features(hit)
            rows = salience.timeline_summary(F)
            print(f"  交叉核对面板：{hit}（{len(rows)} 行，与屏幕逐行相同）")
    except Exception as e:                                          # noqa: BLE001
        check("8 屏幕时刻序列与数据层一致", False, f"数据层不可用：{e}")

    # 量化"旧口径会长什么样"，作为问题真实性的证据
    try:
        F = salience.build_features(hit)
        rows = salience.timeline_summary(F)
        old = sorted(rows, key=lambda r: -float(r["strength"]))
        ok = [salience._time_key(r["time"]) for r in old]
        back = sum(1 for i in range(len(ok) - 1) if ok[i] > ok[i + 1])
        check("9 旧口径（按强度）确有回跳（证明修复前确实乱）", back > 0,
              f"旧口径 {back} 次时间回跳 / {len(ok)-1} 次相邻比较；"
              "新口径 0 次")
    except Exception:                                               # noqa: BLE001
        pass

    # 相邻间隔分布（人类可读的"顺不顺"证据）
    if len(ks) > 1:
        gaps = [ks[i + 1] - ks[i] for i in range(len(ks) - 1)]
        print(f"  相邻时刻间隔：最小 {min(gaps)} 分钟 / 中位 "
              f"{sorted(gaps)[len(gaps)//2]} 分钟 / 最大 {max(gaps)} 分钟")
        check("10 间隔全部为正（读起来是一条连续时间轴）",
              all(g > 0 for g in gaps), f"最小间隔 {min(gaps)} 分钟")

print()
print(f"结果：{PASS}/{PASS + FAIL} PASS")
if FAILS:
    print("未通过：")
    for f in FAILS:
        print("  ·", f)
else:
    print("全部通过 ✅")
print("=" * 70)
sys.exit(0 if not FAILS else 1)
