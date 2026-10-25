# -*- coding: utf-8 -*-
"""v2.0.3/2.0.4 桌面部署脚本（v202b 版修了两处：补 import ctypes、路径改桌面布局）。

用法：python deploy_desktop_v203.py   （把 build/dist_v204 部署为 exe 文件夹内的成品）
"""
import ctypes
import hashlib
import os
import shutil

SRC = r"C:\Users\user\Desktop\workbuddy\flowlive_v201\build\dist_v204\资金流向原生版"
DST = r"C:\Users\user\Desktop\exe\资金流向原生版_v2.0.4"
DATA_SRC = r"C:\Users\user\Desktop\exe\资金流向原生版_v2.0.4\data"


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def log(m):
    print(m, flush=True)


if os.path.isdir(DST):
    K32 = ctypes.windll.kernel32
    for root, dirs, files in os.walk(DST, topdown=False):
        for n in files:
            K32.DeleteFileW(os.path.join(root, n))
        for n in dirs:
            K32.RemoveDirectoryW(os.path.join(root, n))
os.makedirs(DST, exist_ok=True)
shutil.copy2(os.path.join(SRC, "资金流向原生版.exe"),
             os.path.join(DST, "资金流向原生版.exe"))
shutil.copytree(os.path.join(SRC, "_internal"), os.path.join(DST, "_internal"))
log("[1] exe=%d B" % os.path.getsize(os.path.join(DST, "资金流向原生版.exe")))
ok = md5(os.path.join(SRC, "资金流向原生版.exe")) == md5(os.path.join(DST, "资金流向原生版.exe"))
log("[2] 校验一致：%s" % ("YES" if ok else "NO"))
print("DONE", ok)
