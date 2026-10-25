# -*- coding: utf-8 -*-
"""v2.0.2 四列版 → 桌面新文件夹（旧文件夹被锁清不掉，换名避开）。"""
import hashlib
import os
import shutil

SRC = r"D:\flowlive_v201\build\dist_v202c\资金流向原生版"
DST = r"C:\Users\user\Desktop\资金流向原生版_v2.0.2b"
DATA_SRC = r"D:\exe\data"


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def log(m):
    print(m, flush=True)


if os.path.isdir(DST):
    # 新文件夹若已存在（同目录内删除是允许的——逐文件 Win32）
    K32 = ctypes.windll.kernel32
    for root, dirs, files in os.walk(DST, topdown=False):
        for n in files:
            K32.DeleteFileW(os.path.join(root, n))
        for n in dirs:
            K32.RemoveDirectoryW(os.path.join(root, n))
os.makedirs(DST, exist_ok=True)

shutil.copy2(os.path.join(SRC, "资金流向原生版.exe"),
             os.path.join(DST, "资金流向原生版.exe"))
shutil.copytree(os.path.join(SRC, "_internal"),
                os.path.join(DST, "_internal"))
log("[1] exe=%d B" % os.path.getsize(os.path.join(DST, "资金流向原生版.exe")))

dd = os.path.join(DST, "data")
os.makedirs(dd, exist_ok=True)
keep = [n for n in os.listdir(DATA_SRC)
        if n.endswith((".json", ".npy")) and "today" not in n]
for n in keep:
    shutil.copy2(os.path.join(DATA_SRC, n), os.path.join(dd, n))
log("[2] data：%d 文件" % len(keep))

ok = md5(os.path.join(SRC, "资金流向原生版.exe")) == md5(os.path.join(DST, "资金流向原生版.exe"))
n_int = sum(len(f) for _, _, f in os.walk(os.path.join(DST, "_internal")))
log("[3] 校验一致：%s；_internal %d 文件" % ("YES" if ok else "NO", n_int))

with open(os.path.join(DST, "先看我.txt"), "w", encoding="utf-8") as f:
    f.write(
        "资金流向 · 原生版 v2.0.2（桌面部署 · 四列版）\n"
        "==============================================\n"
        "· 双击「资金流向原生版.exe」即用，整个文件夹别拆散。\n"
        "· 实时榜单页下半部分：四个当下榜已改为四列横向并排。\n"
        "· data\\ 已带 9/18、9/21 历史数据与离线板块清单：断网/限流时自动显示 9/21 行情。\n"
        "· 旁边的旧文件夹「资金流向原生版_v2.0.2」（无 b）是上一版，系统暂时锁住\n"
        "  删不掉，可重启电脑后手动删除，或无视它。\n"
        "· D:\\exe 的正式升级：锁释放后把本文件夹 exe 和 _internal 复制回 D:\\exe。\n")
log("[4] 说明已写")
print("DONE", ok)
