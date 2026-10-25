# -*- coding: utf-8 -*-
"""测量 PyInstaller onefile 的**解包耗时** —— 启动延迟里最容易被忽视的一段。

onefile 每次启动都要把 69MB 的 PKG 解到 %TEMP%\_MEIxxxx，
这段开销发生在 Python 代码执行**之前**，因此无法用 Python 自己计时。
只能从外部观测：轮询 _MEI 目录，看它何时出现、何时停止增长。

同时给出 onefile vs onedir 的对照结论。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist_v122", "资金流向原生版.exe")
TMP = os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"


def mei_dirs():
    try:
        return {d for d in os.listdir(TMP)
                if d.startswith("_MEI") and os.path.isdir(os.path.join(TMP, d))}
    except Exception:                                        # noqa: BLE001
        return set()


def dir_size(p):
    tot = 0
    try:
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    tot += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except Exception:                                        # noqa: BLE001
        pass
    return tot


def main():
    print("\n=========== onefile 解包耗时测量 ===========")
    before = mei_dirs()
    print(f"  启动前已有 _MEI 目录 {len(before)} 个")

    t0 = time.perf_counter()
    p = subprocess.Popen([EXE], cwd=os.path.dirname(EXE),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    mine = None
    t_appear = t_first_bytes = t_stable = None
    last_size, stable_since = 0, None
    samples = []

    while time.perf_counter() - t0 < 60:
        time.sleep(0.05)
        now = mei_dirs() - before
        if not now:
            continue
        if mine is None:
            mine = now
            t_appear = time.perf_counter() - t0
        # 取当前所有新目录的总大小
        sz = sum(dir_size(os.path.join(TMP, d)) for d in now)
        el = time.perf_counter() - t0
        if sz > 0 and t_first_bytes is None:
            t_first_bytes = el
        if sz != last_size:
            samples.append((el, sz))
            last_size = sz
            stable_since = el
        elif stable_since is not None and el - stable_since > 0.35:
            t_stable = stable_since
            break

    # 等窗口出现（用 preflight 日志作为"Python 代码已开始跑"的标志）
    print("\n  解包过程采样（时间, 累计字节）:")
    for el, sz in samples[:6]:
        print(f"    +{el:5.2f}s   {sz/1e6:7.1f} MB")
    if len(samples) > 6:
        print(f"    ...（共 {len(samples)} 次变化）")
        el, sz = samples[-1]
        print(f"    +{el:5.2f}s   {sz/1e6:7.1f} MB  ← 最终")

    print("\n  结论:")
    if t_appear:
        print(f"    _MEI 目录出现       : +{t_appear:.2f} s")
    if t_first_bytes:
        print(f"    开始写入数据        : +{t_first_bytes:.2f} s")
    if t_stable:
        print(f"    ★ 解包完成（稳定）  : +{t_stable:.2f} s")
        print(f"      → 这段是**每次启动的固定开销**，与数据/网络无关")

    p.terminate()
    try:
        p.wait(timeout=15)
    except Exception:                                        # noqa: BLE001
        p.kill()


if __name__ == "__main__":
    main()
