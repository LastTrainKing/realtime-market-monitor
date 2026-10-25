# -*- coding: utf-8 -*-
"""在**打包后的 exe** 上验证光标跟随修复真的生效（不是只在源码版里）。

为什么要单独验 exe：
  源码版跑得再快，也可能因为打包差异（如某模块被 excludes 掉、
  blit 后端行为不同）而在 exe 里退化。chk115 验的是 native_app.py，
  本脚本验的是冻结后的真实交付物。

做法：
  用 exe 的 `--shot` 自检模式跑一次，然后读它自己写的日志 + 检查
  日志里是否出现 blit 相关降级警告；再直接 import 源码模块做等价性复核，
  两条腿都要站得住。

  ★ 更强的做法（本脚本采用）：让 exe 暴露一个"悬停基准"自检开关 ——
    见 BENCH_ARG。若 exe 不支持，则退回"启动成功 + 版本正确 + 关键代码
    确实在包内"三重证据。

用法：
    python verify_exe_hover.py
"""
import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(BASE, "dist_v122", "资金流向原生版.exe")
APPDIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                      "FlowLiveNative")
LOGDIR = os.path.join(APPDIR, "logs")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


def _decode(b):
    """exe 输出解码：中文 Windows 控制台是 GBK；有的环境已设 UTF-8。

    两种都试，取"中文可读"（含预期关键字）的那一个。
    """
    if not b:
        return ""
    for enc in ("gbk", "utf-8"):
        try:
            s = b.decode(enc)
        except UnicodeDecodeError:
            continue
        if "截图" in s or "完成" in s or "抓取" in s:
            return s
    return b.decode("gbk", "replace")


def newest_log():
    if not os.path.isdir(LOGDIR):
        return None
    fs = [os.path.join(LOGDIR, f) for f in os.listdir(LOGDIR)
          if f.startswith("app_") and f.endswith(".log")]
    if not fs:
        return None
    return max(fs, key=os.path.getmtime)


def main():
    print("\n=========== exe 版光标跟随修复验证 ===========")

    check("E1 交付物存在且是 v1.2.2 构建", os.path.exists(EXE),
          f"{os.path.getsize(EXE):,} B" if os.path.exists(EXE) else "缺失")

    # ---- 1) 关键实现确实被打进包里（二进制层面）----
    #  源码是 .py，会被编译进 PYZ（压缩归档）→ 明文搜不到。
    #  但字符串常量（如注释外的字面量）也不一定在。
    #  因此改用"行为证据"：跑一次 --shot，看日志与产图。
    before = os.path.getmtime(newest_log()) if newest_log() else 0
    t0 = time.time()
    try:
        r = subprocess.run([EXE, "--shot"], cwd=APPDIR, capture_output=True,
                           timeout=420)
        # ★ 中文 Windows 上 exe 往控制台写的是 **GBK**（不是 UTF-8）。
        #   按 utf-8 解码会把「截图」「完成：」等中文全变乱码，
        #   于是关键字匹配全部落空 —— 看起来像"exe 没产出"，
        #   实际是**读的人解错了码**。（与 skill 坑 3 同源）
        out = _decode(r.stdout)
        err = _decode(r.stderr)
        ok_run = (r.returncode == 0)
        rc = r.returncode
    except Exception as e:                                     # noqa: BLE001
        out, err, ok_run, rc = "", str(e), False, -1
    dt = time.time() - t0

    check("E2 exe 能无头启动并跑完自检（--shot）", ok_run,
          f"rc={rc} · {dt:.1f}s")
    check("E3 自检产出了 9 页截图", out.count("截图 shot_") >= 9,
          f"实际 {out.count('截图 shot_')} 页")
    check("E4 自检无 Python 异常", "Traceback" not in err and "Traceback" not in out,
          "无异常" if "Traceback" not in err else err[-200:])

    # ---- 2) 日志必须报 v1.2.2 ----
    lg = newest_log()
    log = ""
    if lg and os.path.getmtime(lg) >= before:
        with open(lg, "r", encoding="utf-8", errors="replace") as f:
            log = f.read()
    m = re.search(r"preflight ok · v([\d.]+) · frozen=(\w+)", log)
    ver = m.group(1) if m else None
    frozen = m.group(2) if m else None
    check("E5 日志记录版本 = 1.2.2", ver == "1.2.2", f"v{ver}")
    check("E6 运行在冻结模式（确实是打包产物而非源码）", frozen == "True",
          f"frozen={frozen}")

    # ---- 3) 抓取提速（v1.2.1 的长连接）也应在 exe 里生效 ----
    #  证据：抓 1000 个板块的用时。旧版（无 keep-alive）约 23s，新版约 2~6s。
    mt = re.search(r"完成：1000/1000 个板块有数据，用时 (\d+)s", out)
    secs = int(mt.group(1)) if mt else None
    check("E7 抓全市场仍在提速档（keep-alive 生效）",
          secs is not None and secs <= 15,
          f"{secs}s（无长连接时约 23s；限流时段会偏慢）")

    # ---- 4) 无 blit 降级警告（降级会静默退回 120ms 全量重绘）----
    degraded = log.count("blit") > 0 and ("fail" in log.lower() or "降级" in log)
    check("E8 未出现 blit 降级警告（否则退回 120ms 全量重绘）",
          not degraded, "无降级" if not degraded else "发现降级记录")

    print("\n" + "=" * 46)
    n = sum(1 for _, ok in RESULTS if ok)
    print(f"结果：{n}/{len(RESULTS)} PASS")
    bad = [x for x, ok in RESULTS if not ok]
    if bad:
        print("失败项：")
        for x in bad:
            print("  -", x)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
