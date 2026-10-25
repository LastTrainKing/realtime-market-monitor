# -*- coding: utf-8 -*-
"""v2.0.2 桌面部署：D:\exe 被系统句柄锁定时的替代落点（用户指定）。"""
import hashlib
import os
import shutil

SRC = r"D:\flowlive_v201\build\dist_v202b\资金流向原生版"
DST = r"C:\Users\user\Desktop\资金流向原生版_v2.0.2"
DATA_SRC = r"D:\exe\data"


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def log(m):
    print(m, flush=True)


# [1] 程序本体
if os.path.isdir(DST):
    shutil.rmtree(DST, ignore_errors=True)
os.makedirs(DST, exist_ok=True)
shutil.copy2(os.path.join(SRC, "资金流向原生版.exe"),
             os.path.join(DST, "资金流向原生版.exe"))
shutil.copytree(os.path.join(SRC, "_internal"),
                os.path.join(DST, "_internal"))
log("[1] 程序本体复制完成  exe=%d B" % os.path.getsize(os.path.join(DST, "资金流向原生版.exe")))

# [2] 历史数据（便携：直接放在 exe 旁边，启动即有 0918/0921 本地数据）
dd = os.path.join(DST, "data")
os.makedirs(dd, exist_ok=True)
keep = [n for n in os.listdir(DATA_SRC)
        if n.endswith((".json", ".npy")) and "today" not in n]
for n in keep:
    shutil.copy2(os.path.join(DATA_SRC, n), os.path.join(dd, n))
log("[2] 历史数据复制完成：%d 个文件（含 0918/0921 面板 + 离线板块清单）" % len(keep))

# [3] 校验
ok = md5(os.path.join(SRC, "资金流向原生版.exe")) == md5(os.path.join(DST, "资金流向原生版.exe"))
n_int = sum(len(f) for _, _, f in os.walk(os.path.join(DST, "_internal")))
log("[3] exe 校验一致：%s；_internal：%d 文件" % ("YES" if ok else "NO", n_int))

# [4] 三行说明
with open(os.path.join(DST, "先看我.txt"), "w", encoding="utf-8") as f:
    f.write(
        "资金流向 · 原生版 v2.0.2（桌面应急部署）\n"
        "========================================\n"
        "· 双击「资金流向原生版.exe」即用，整个文件夹别拆散（exe 和 _internal 必须成对）。\n"
        "· 旁边 data\\ 里已带 9/18 与 9/21 的完整历史数据：现在（非交易时段/接口被限流）\n"
        "  打开会自动显示 9/21 的全天行情，不会再弹「数据加载失败」。\n"
        "· D:\\exe 里的旧版暂被系统文件占用没能更新；哪天锁释放了，把本文件夹\n"
        "  的 exe 和 _internal 复制回 D:\\exe 即可完成正式升级（data 不用动）。\n")
log("[4] 说明文件已写")
print("DONE", ok)
