# -*- coding: utf-8 -*-
"""onefile vs onedir：启动**解包+import 段**的真实 A/B（v1.2.3）。

为什么要专门量这一段：
  "点进 exe 要 5 秒"里，**最大的一块是 PyInstaller 解包**，它发生在
  Python 代码执行之前，任何代码级优化都碰不到。只有换个打包形态才能省掉。
  所以这一段必须单独量，否则会误以为"优化代码没用"或"优化代码很有用"。

做法（避开所有会干扰计时的东西）：
  · 只量「进程启动 → 日志出现 preflight ok」——
    preflight 在 MainWindow 构造**之前**执行，恰好卡在
    "解包 + import 全部完成、业务还没开始"的位置，是这段的干净边界；
  · 每次启动前**清掉日志尾标记**，只认新追加的行（避免读到上一次的）；
  · 不依赖 loaded ok（盘中同一分钟不会重复写，会误判成超时）；
  · 每档跑 N 次取中位数，并报 min/max —— 冷/热文件缓存差异很大，
    只跑一次容易得出错误结论。

用法：
    python bench_unpack_ab.py            # 两个形态各 5 次
    python bench_unpack_ab.py 3          # 各 3 次
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APPDIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                      "FlowLiveNative")
LOGDIR = os.path.join(APPDIR, "logs")

CANDIDATES = [
    ("onefile 单文件（v1.2.2 形态）", os.path.join(
        HERE, "dist_v122", "资金流向原生版.exe")),
    ("onefile 单文件（v1.2.3）", os.path.join(
        HERE, "dist_v123_onefile", "资金流向原生版.exe")),
    ("onedir  文件夹（v1.2.3）", os.path.join(
        HERE, "dist_v123_onedir", "资金流向原生版", "资金流向原生版.exe")),
]


def log_path():
    return os.path.join(LOGDIR, "app_" + time.strftime("%Y%m%d") + ".log")


def read_lines():
    try:
        with open(log_path(), "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception:                                        # noqa: BLE001
        return []


def measure_once(exe):
    """启动一次，返回 (到 preflight ok 的秒数, 到 loaded ok 的秒数|None)。"""
    before = len(read_lines())
    t0 = time.time()
    p = subprocess.Popen([exe], cwd=os.path.dirname(exe),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t_pre = None
    try:
        while time.time() - t0 < 90:
            time.sleep(0.10)
            lines = read_lines()
            if len(lines) <= before:
                continue
            for ln in lines[before:]:
                if "preflight ok" in ln and t_pre is None:
                    t_pre = time.time() - t0
            if t_pre is not None:
                break
    finally:
        p.terminate()
        try:
            p.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            p.kill()
    return t_pre, None


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print("\n=========== onefile vs onedir · 启动解包+import 段 A/B ===========")
    print(f"  每档 {n} 次，取中位数")
    print("  （这段发生在 Python 代码之前，是「点进 exe 卡一下」的主因）\n")
    rows = []
    for label, exe in CANDIDATES:
        if not os.path.exists(exe):
            print(f"  {label}: 不存在，跳过 ({exe})")
            continue
        xs = []
        for i in range(n):
            t_pre, _ = measure_once(exe)
            if t_pre is None:
                print(f"    [{label}] 第 {i+1} 次未捕获 preflight，跳过")
                continue
            xs.append(t_pre)
            print(f"    [{label}] 第 {i+1} 次: {t_pre:.2f} s")
        if xs:
            xs_sorted = sorted(xs)
            med = xs_sorted[len(xs_sorted) // 2]
            rows.append((label, med, min(xs), max(xs), len(xs)))

    print("\n  ---------------- 汇总 ----------------")
    print(f"  {'形态':<30} {'中位':>8} {'最快':>8} {'最慢':>8}")
    base = rows[0][1] if rows else None
    for label, med, lo, hi, k in rows:
        delta = ""
        if base and label != rows[0][0]:
            delta = f"   ({med - base:+.2f}s)"
        print(f"  {label:<30} {med:>7.2f}s {lo:>7.2f}s {hi:>7.2f}s{delta}")
    print()


if __name__ == "__main__":
    main()
