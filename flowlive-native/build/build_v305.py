# -*- coding: utf-8 -*-
"""v3.0.5 一键构建脚本（构建 → 体积对账 → 版本标记）

为什么单独写脚本而不是手敲 pyinstaller：
  1) 构建耗时数分钟，必须用 run_in_background 跑，脚本便于后台调用；
  2) **体积对账是硬门槛**：打包环境一旦混入 pandas，exe 会从 ~70MB 涨到 ~92MB，
     应用自身零 pandas 依赖，体积差就是"环境不干净"的唯一信号。
     本脚本把 70MB 级设为期望区间，超出即告警（不阻断，但必须人工确认）。
  3) 版本标记（版本.txt）由脚本写，避免忘记 → 与"部署了旧构建"是同一类假成功陷阱。

用法：
  ../native_env/Scripts/python.exe build_v305.py
"""
import os, sys, shutil, subprocess, time, json

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "资金流向原生版_v305.spec")
WORK = os.path.join(HERE, "build_v305")
DIST = os.path.join(HERE, "dist_v305")
PYI = os.path.join(HERE, "..", "native_env", "Scripts", "pyinstaller.exe")

VERSION = "3.0.5"

# 体积期望（onedir，exe 本体 + _internal 合计）
EXE_EXPECT_MB = (7.5, 9.0)       # exe 本体：正常 ~8.3MB
INT_EXPECT_MB = (130.0, 165.0)   # _internal：正常 ~150MB
INT_ALERT_MB = 175.0             # 超过即判定"环境不干净"，多半混入了 pandas


def human(n):
    return f"{n:,} B ({n / 1048576:.1f} MB)"


def main():
    if not os.path.exists(SPEC):
        print("[FAILED] spec 不存在:", SPEC)
        return 1
    if not os.path.exists(PYI):
        print("[FAILED] pyinstaller 不存在:", PYI)
        return 1

    print(f"=== 构建 v{VERSION} ===")
    print(f"spec   : {SPEC}")
    print(f"dist   : {DIST}")
    t0 = time.time()

    # ★★ 构建前清理（重要：**不做自动删除**，只提示）
    #   原因（实测踩到两次）：本机沙箱有"批量删除保护"
    #   （SAFE_DELETE_BULK_CONFIRM_REQUIRED，阈值 50 个文件）。
    #   `dist_v305/资金流向原生版`（473 文件）和 `build_v305`（488 文件）
    #   都超阈值 → 脚本里的 rmtree 直接触发保护、构建退出码 1。
    #   pyinstaller 自带的 `--noconfirm --clean` 同样会因为删这些目录被拦。
    #   故：**由调用方在构建前手动清理**，脚本只负责检查并给出命令。
    inner_dir = os.path.join(DIST, "资金流向原生版")
    stale = []
    if os.path.isdir(inner_dir):
        stale.append(inner_dir)
    if os.path.isdir(WORK):
        stale.append(WORK)
    if stale:
        print("[提示] 以下目录存在旧内容，会导致 '改代码但产物没变' 的假构建：")
        for p in stale:
            n = sum(len(f) for _, _, f in os.walk(p))
            print(f"       {p}  ({n} 个文件)")
        print("       请先在 shell 手动清理后再运行本脚本：")
        for p in stale:
            print(f'         rm -rf "{p}"')
        print("       （不用 --clean：它删同样的目录，一样会被沙箱批量删除保护拦下）")
        return 1

    cmd = [PYI, "--noconfirm",
           "--distpath", DIST, "--workpath", WORK, SPEC]
    print("cmd    :", " ".join(cmd))
    print("-" * 60)
    # 不捕获输出：让 PyInstaller 的进度实时可见（后台运行时写入任务日志）
    rc = subprocess.call(cmd)
    dt = time.time() - t0
    print("-" * 60)
    print(f"pyinstaller 退出码 {rc}，耗时 {dt:.1f}s")

    inner = os.path.join(DIST, "资金流向原生版")
    exe = os.path.join(inner, "资金流向原生版.exe")
    internal = os.path.join(inner, "_internal")
    if rc != 0 or not os.path.exists(exe):
        print("[FAILED] 构建未产出 exe")
        return 1

    exe_sz = os.path.getsize(exe)
    n_int = sum(len(f) for _, _, f in os.walk(internal))
    int_sz = sum(os.path.getsize(os.path.join(r, f))
                 for r, _, fs in os.walk(internal) for f in fs)

    print(f"\n=== 体积对账 ===")
    print(f"exe       : {human(exe_sz)}")
    print(f"_internal : {human(int_sz)}  / {n_int:,} 个文件")
    print(f"合计      : {human(exe_sz + int_sz)}")

    alert = []
    if not (EXE_EXPECT_MB[0] * 1048576 <= exe_sz <= EXE_EXPECT_MB[1] * 1048576):
        alert.append(f"exe 体积 {exe_sz / 1048576:.1f}MB 超出期望区间 {EXE_EXPECT_MB}")
    if not (INT_EXPECT_MB[0] * 1048576 <= int_sz <= INT_EXPECT_MB[1] * 1048576):
        alert.append(f"_internal 体积 {int_sz / 1048576:.1f}MB 超出期望区间 {INT_EXPECT_MB}")
    if int_sz > INT_ALERT_MB * 1048576:
        alert.append(f"★★ _internal > {INT_ALERT_MB}MB：极可能混入了 pandas/lxml 等重库，"
                     f"请检查打包环境（应为专用 venv，只装 PySide6+matplotlib+numpy）")

    # 顺带核对：_internal 里**不应**出现 pandas
    bad = [d for d in os.listdir(internal) if d.lower().startswith("pandas")]
    if bad:
        alert.append(f"★★ _internal 中发现 pandas 相关目录: {bad}")

    if alert:
        print("\n[警告]")
        for a in alert:
            print("  -", a)
    else:
        print("\n[OK] 体积落在期望区间，且未发现重库混入")

    # 写版本标记
    with open(os.path.join(DIST, "版本.txt"), "w", encoding="utf-8") as f:
        f.write(VERSION)
    meta = {
        "version": VERSION,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "build_sec": round(dt, 1),
        "exe_bytes": exe_sz,
        "internal_bytes": int_sz,
        "internal_files": n_int,
        "spec": os.path.basename(SPEC),
        "alerts": alert,
    }
    with open(os.path.join(DIST, "构建信息.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"\n已写 版本.txt = {VERSION}")
    print(f"已写 构建信息.json")
    print(f"\n[SUCCESS] 构建完成 → {DIST}")
    print("部署命令：python deploy_to_running.py dist_v305")
    return 0


if __name__ == "__main__":
    sys.exit(main())
