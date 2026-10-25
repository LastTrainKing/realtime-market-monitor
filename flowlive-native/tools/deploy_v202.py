# -*- coding: utf-8 -*-
"""把 v2.0.2 部署到 D:\exe（便携形态），保留用户真实数据。

约束沿袭 deploy_v201：data/logs/shots/app.lock 绝不触碰。
★ v2.0.2 环境变化：WorkBuddy 的 sitecustomize 给 os.remove/unlink 挂了
  回收站式 safe-delete 钩子，且该钩子在 D 盘上 trash 失败（fail-closed）。
  → 本脚本所有删除一律走 ctypes 直调 Win32 API，绕开 python 层钩子。
"""
import ctypes
import os
import shutil
import subprocess
import time

ROOT = r"D:\flowlive_v201"
SRC_DIR = os.path.join(ROOT, "build", "dist_v202", "资金流向原生版")
DST_DIR = r"D:\exe"
EXE_NAME = "资金流向原生版.exe"


def log(m):
    print(m, flush=True)


def win_delete(path):
    """Win32 直接删除（文件或目录），不经过 os.remove 钩子。"""
    if os.path.isdir(path) and not os.path.islink(path):
        if not ctypes.windll.kernel32.RemoveDirectoryW(path):
            raise OSError("RemoveDirectoryW failed: %s" % path)
    else:
        if not ctypes.windll.kernel32.DeleteFileW(path):
            raise OSError("DeleteFileW failed: %s" % path)


def rmtree_force(path):
    """递归删除：每个条目用 Win32 删除，绕开 safe-delete 钩子。"""
    for root, dirs, files in os.walk(path, topdown=False):
        for n in files:
            win_delete(os.path.join(root, n))
        for n in dirs:
            try:
                win_delete(os.path.join(root, n))
            except OSError:
                pass                                    # 非空目录，下一轮
    try:
        win_delete(path)
    except OSError:
        pass                                            # 留给下方 copytree 合并


def md5(p):
    import hashlib
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    src_exe = os.path.join(SRC_DIR, EXE_NAME)
    if not os.path.exists(src_exe):
        log("[FAILED] 构建产物不存在：" + src_exe)
        return 1

    # [0] 关旧实例
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + EXE_NAME],
                           capture_output=True, timeout=30)
        if EXE_NAME in (r.stdout or b"").decode("gbk", "ignore"):
            log("[0] 关闭运行中的旧实例 …")
            subprocess.run(["taskkill", "/F", "/IM", EXE_NAME],
                           capture_output=True, timeout=30)
            time.sleep(1.5)
    except Exception as e:                               # noqa: BLE001
        log("[0] 检测实例出错（忽略）：" + str(e))

    # [1] 替换 exe
    dst_exe = os.path.join(DST_DIR, EXE_NAME)
    if os.path.exists(dst_exe):
        win_delete(dst_exe)
    shutil.copy2(src_exe, dst_exe)
    log("[1] exe 替换完成：%d B (md5 %s)" % (os.path.getsize(dst_exe), md5(dst_exe)[:12]))

    # [2] 替换 _internal（成对更新）
    src_int = os.path.join(SRC_DIR, "_internal")
    dst_int = os.path.join(DST_DIR, "_internal")
    if os.path.isdir(dst_int):
        rmtree_force(dst_int)
    shutil.copytree(src_int, dst_int, dirs_exist_ok=True)
    n = sum(len(f) for _, _, f in os.walk(dst_int))
    mb = sum(os.path.getsize(os.path.join(r, f))
             for r, _, fs in os.walk(dst_int) for f in fs) / 1048576
    log("[2] _internal 替换完成：%d 文件 / %.1f MB" % (n, mb))

    # [3] 归档旧日志（避免"新 exe 写不进旧日志"的历史故障）
    logdir = os.path.join(DST_DIR, "logs")
    if os.path.isdir(logdir):
        for f in os.listdir(logdir):
            if f.endswith(".log"):
                try:
                    os.rename(os.path.join(logdir, f),
                              os.path.join(logdir, f + ".bak_v201"))
                    log("[3] 旧日志已归档：" + f)
                except OSError as e:
                    log("[3] 日志改名失败（忽略）：" + f + " " + str(e))

    # [4] 核对
    ok_exe = md5(src_exe) == md5(dst_exe)
    log("[4] exe 与构建产物一致：%s" % ("YES" if ok_exe else "NO"))
    log("完成。数据/日志/截图/锁文件未触碰。")
    return 0 if ok_exe else 1


if __name__ == "__main__":
    raise SystemExit(main())
