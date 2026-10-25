# -*- coding: utf-8 -*-
r"""exe 冷启动到首屏可用的**真实**计时（不经自检模式，直接量到「数据可用」）。

为什么要单独量：
  用户说「点进 exe 要 5 秒」。这 5 秒里包含：
    ① PyInstaller onefile 解包（把 69MB 解到 %TEMP%\_MEIxxxx）
    ② Python 解释器 + 各模块 import
    ③ 交易日历联网
    ④ 1000 板块抓取（或读缓存）
    ⑤ 特征计算
  只有 ④⑤ 是"算法"层，①② 是打包固有的。必须分开量，
  否则会花大力气优化 2%，却对 80% 视而不见。

做法：读 exe 自己写的日志时间戳（它每次启动都记 preflight），
      用进程墙钟与日志行的时间差反推各阶段。
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ v1.2.3：支持用环境变量指定被测 exe，便于 onefile / onedir A/B 对比。
#   默认仍指 onefile 单文件版（用户日常双击的那个形态）。
EXE = os.environ.get("FLOW_EXE") or os.path.join(
    HERE, "dist_v123_onefile", "资金流向原生版.exe")
APPDIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                      "FlowLiveNative")
LOGDIR = os.path.join(APPDIR, "logs")


def log_path():
    return os.path.join(LOGDIR, "app_" + time.strftime("%Y%m%d") + ".log")


def read_tail(path, n=60):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except Exception:                                        # noqa: BLE001
        return []


def parse_ts(line):
    m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
    if not m:
        return None
    return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    lp = log_path()
    print("\n=========== exe 冷启动真实计时 ===========")
    print(f"  目标: {EXE}")
    print(f"  日志: {lp}\n")

    for i in range(runs):
        before = len(read_tail(lp, 500))
        t0 = time.time()
        # 直接启动（不传 --shot）→ 正常窗口，跑一会儿后关掉
        p = subprocess.Popen([EXE], cwd=APPDIR,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        # 等日志里出现 "loaded ok"（= 首屏数据可用）
        ready = None
        while time.time() - t0 < 120:
            time.sleep(0.15)
            lines = read_tail(lp, 500)
            if len(lines) <= before:
                continue
            for ln in lines[before:]:
                if "loaded ok" in ln:
                    ts = parse_ts(ln)
                    ready = (ts - t0) if ts else (time.time() - t0)
                    break
            if ready is not None:
                break
        wall = time.time() - t0
        p.terminate()
        try:
            p.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            p.kill()

        # 拆解日志里的阶段
        lines = read_tail(lp, 500)[before:]
        stages = []
        for ln in lines:
            ts = parse_ts(ln)
            if ts is None:
                continue
            body = ln.split("] ", 1)[-1].strip()
            stages.append((ts - t0, body[:70]))

        # ★ v1.2.3：单独报「到 preflight ok」的时间 ——
        #   这一行之前的耗时全部是**解包 + import**，发生在任何业务代码之前，
        #   是 onefile/onedir 差异的唯一体现，也是代码优化碰不到的那一段。
        t_pre = None
        for ln in lines:
            if "preflight ok" in ln:
                ts = parse_ts(ln)
                if ts:
                    t_pre = ts - t0
                    break

        print(f"  --- 第 {i+1} 次 ---")
        if t_pre is None:
            print("    未能捕获 preflight ok")
        else:
            print(f"    ★ 到 preflight ok（= 解包+import，代码碰不到）: "
                  f"{t_pre:.2f} s")
        if ready is None:
            print(f"    未在 120s 内捕获 loaded ok（墙钟 {wall:.2f}s）")
        else:
            print(f"    ★ 到「数据可用」: {ready:.2f} s")
            if t_pre is not None:
                print(f"    → 业务段（日历+数据+特征）: {ready - t_pre:.2f} s")
        for off, body in stages[:8]:
            print(f"       +{off:6.2f}s  {body}")
        print()


if __name__ == "__main__":
    main()
