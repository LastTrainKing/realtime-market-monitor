# -*- coding: utf-8 -*-
"""诊断 v2 ：「exe 写不了 D:\\exe\\logs 的日志，Python 却能写」到底差在哪。

v1 已经确认的事实：
  · exe 运行正常（截图/表格全产出），便携判定也对（portable_root=D:\\exe）；
  · 但 exe 的 `[diag] log write test=FAILED PermissionError: [Errno 13]`
    —— 目标正是 `D:\\exe\\logs\\app_20260921.log`；
  · 同一个文件，用托管 Python 以 'a' 模式打开**成功**。

本脚本查三件事，逐条排除：
  ① 文件属性（只读 / 隐藏 / 系统 / 压缩 / 稀疏 / 离线）与 ACL；
  ② 文件是不是被别的进程独占（试探性重命名/删除）；
  ③ 删掉后让 exe 重新创建，是否就正常了 —— 这一步直接决定修法。
"""
import os
import stat
import subprocess
import shutil
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

LOG = r"D:\exe\logs\app_20260921.log"
PROBE = r"D:\exe\logs\_probe_new.log"
KEEP = r"D:\flowlive_v201\backup_v126\logs\app_20260921.log"

ATTR_FLAGS = [
    ("只读 READONLY", stat.FILE_ATTRIBUTE_READONLY),
    ("隐藏 HIDDEN", stat.FILE_ATTRIBUTE_HIDDEN),
    ("系统 SYSTEM", stat.FILE_ATTRIBUTE_SYSTEM),
    ("存档 ARCHIVE", stat.FILE_ATTRIBUTE_ARCHIVE),
    ("压缩 COMPRESSED", 0x800),
    ("离线 OFFLINE", stat.FILE_ATTRIBUTE_OFFLINE),
    ("稀疏 SPARSE", 0x200),
    ("重解析点 REPARSE_POINT", stat.FILE_ATTRIBUTE_REPARSE_POINT),
]


def step1_attrs():
    print("[1] 文件属性")
    if not os.path.exists(LOG):
        print("    目标不存在")
        return
    st = os.stat(LOG)
    a = getattr(st, "st_file_attributes", 0)
    print(f"    路径: {LOG}")
    print(f"    大小: {st.st_size} B   模式: {oct(st.st_mode)}")
    print(f"    原始属性位: {a:#x}")
    for name, bit in ATTR_FLAGS:
        if a & bit:
            print(f"      ★ 置位: {name}")
    if not (a & stat.FILE_ATTRIBUTE_READONLY) and not (a & 0x800):
        print("      未发现异常属性（非只读、非压缩）")
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(LOG)
        print(f"    GetFileAttributesW: {attrs:#x}  (0xFFFFFFFF=不存在)")
    except Exception as e:                               # noqa: BLE001
        print(f"    GetFileAttributesW 失败: {e}")


def step2_inuse():
    print("\n[2] 是否被占用 / 能否删除")
    if not os.path.exists(LOG):
        print("    目标不存在，跳过")
        return
    try:
        os.rename(LOG, LOG + ".try")
        print("    重命名: 成功")
        os.rename(LOG + ".try", LOG)
        print("    还原: 成功")
    except Exception as e:                               # noqa: BLE001
        print(f"    重命名失败: {type(e).__name__}: {e}")
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("")
        print("    以 'a' 打开: 成功（Python 侧）")
    except Exception as e:                               # noqa: BLE001
        print(f"    以 'a' 打开失败: {type(e).__name__}: {e}")


def step3_recreate():
    """把日志备份走、删掉，再跑一次 exe，看它能不能新建并写入。"""
    print("\n[3] 删除后让 exe 重建（决定修法的关键一步）")
    os.makedirs(os.path.dirname(KEEP), exist_ok=True)
    if os.path.exists(LOG):
        try:
            shutil.copy2(LOG, KEEP)
            print(f"    已备份旧日志 → {KEEP}")
        except Exception as e:                           # noqa: BLE001
            print(f"    备份失败（继续）: {e}")
        try:
            os.remove(LOG)
            print("    已删除旧日志")
        except Exception as e:                           # noqa: BLE001
            print(f"    ★ 删除失败: {type(e).__name__}: {e}")
            return
    if os.path.exists(PROBE):
        try:
            os.remove(PROBE)
        except OSError:
            pass

    exe = r"D:\exe\资金流向原生版.exe"
    env = dict(os.environ)
    env["FLOWLIVE_SHOTS"] = r"D:\flowlive_v201\shots\dx2"
    print("    运行 exe --shot dx2 …")
    try:
        r = subprocess.run([exe, "--shot", "dx2"], cwd=r"D:\exe", env=env,
                           capture_output=True, timeout=300)
        out = (r.stdout or b"").decode("utf-8", "replace")
        for ln in out.splitlines():
            if "[diag]" in ln:
                print("      " + ln)
    except Exception as e:                               # noqa: BLE001
        print(f"    运行失败: {e}")
    time.sleep(0.5)
    print("\n    运行后 D:\\exe\\logs:")
    for f in os.listdir(r"D:\exe\logs"):
        p = os.path.join(r"D:\exe\logs", f)
        print(f"      {f}  {os.path.getsize(p)} B")
    if os.path.exists(LOG) and os.path.getsize(LOG) > 0:
        print("    ★ 结论：删除后 exe 能正常写入 → 根因是「旧文件本身不可写」")
        print("      修法：部署时把 logs/ 下的旧日志一并清理（或改名）。")
    else:
        print("    ★ 结论：删除后仍写不进去 → 不是文件状态问题，"
              "需继续查（进程权限 / 杀软 / 盘符策略）。")


def main():
    print("=" * 74)
    print("诊断 v2 · exe 日志写入失败的真因")
    print("=" * 74)
    step1_attrs()
    step2_inuse()
    step3_recreate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
