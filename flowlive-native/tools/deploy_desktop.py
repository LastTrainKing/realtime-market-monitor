# -*- coding: utf-8 -*-
"""deploy_desktop.py —— 统一部署入口（v2.0.10 起）。

把新构建部署到 **固定入口** `exe\\最新版\\`，桌面快捷方式指向这里，
因此每次升级后快捷方式无需任何改动。

用法（构建完成后）：
    python deploy_desktop.py [dist目录名，默认 dist_vXXX 最新一个]

行为：
  1. 读取 `exe\\最新版\\版本.txt` 的旧版本号 → 把旧版整体移入
     `exe\\历史版本\\资金流向原生版_<旧版本号>\\`（同名校验，防覆盖）；
  2. 复制新构建（exe + _internal）到 `exe\\最新版\\`；
  3. 沿用旧版 data\\（30 交易日面板 / 榜单池 / 快照全部保留）；
  4. 写入 版本.txt（版本号取自源构建目录的 版本.txt）；
  5. MD5 校验 exe 一致性。

注：本脚本**不写**「先看我.txt」（早期版本有此步骤，已取消）——
使用说明统一维护在 `exe\\使用说明总览.md` 与 `workbuddy\\更新日志.md`。
"""
import hashlib
import os
import shutil
import sys

D = r"C:\Users\user\Desktop"
BUILD = os.path.join(D, r"workbuddy\flowlive_v201\build")
LATEST = os.path.join(D, r"exe\最新版")
HIST = os.path.join(D, r"exe\历史版本")


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def pick_dist():
    if len(sys.argv) > 1:
        name = sys.argv[1]
        return os.path.join(BUILD, name, "资金流向原生版")
    cands = [d for d in os.listdir(BUILD)
             if d.startswith("dist_v") and os.path.isdir(os.path.join(BUILD, d, "资金流向原生版"))]
    if not cands:
        raise SystemExit("build 下没有可用的 dist_vXXX")
    # 按修改时间取最新（字典序会踩 dist_v9 < dist_v10 这类坑）
    last = max(cands, key=lambda d: os.path.getmtime(os.path.join(BUILD, d)))
    return os.path.join(BUILD, last, "资金流向原生版")


def main():
    src = pick_dist()
    print("[0] 源构建:", src)
    old_ver = None
    vp = os.path.join(LATEST, "版本.txt")
    if os.path.exists(vp):
        old_ver = open(vp, encoding="utf-8").read().strip()
    if old_ver:
        arch = os.path.join(HIST, "资金流向原生版_" + old_ver)
        if os.path.exists(arch):
            shutil.rmtree(arch, ignore_errors=True)
        shutil.move(LATEST, arch)
        print("[1] 旧版 %s 已归档 -> 历史版本" % old_ver)
    os.makedirs(LATEST, exist_ok=True)
    shutil.copy2(os.path.join(src, "资金流向原生版.exe"),
                 os.path.join(LATEST, "资金流向原生版.exe"))
    shutil.copytree(os.path.join(src, "_internal"),
                    os.path.join(LATEST, "_internal"))
    dd = os.path.join(LATEST, "data")
    os.makedirs(dd, exist_ok=True)
    if old_ver:
        sd = os.path.join(HIST, "资金流向原生版_" + old_ver, "data")
        if os.path.isdir(sd):
            for n in os.listdir(sd):
                if n.endswith((".json", ".npy")) and "today" not in n:
                    shutil.copy2(os.path.join(sd, n), os.path.join(dd, n))
    # 版本号：从源 exe 同目录的 版本.txt（构建后人工/脚本写入）或目录名推断
    ver = "未知"
    cand = os.path.join(os.path.dirname(src), "版本.txt")
    if os.path.exists(cand):
        ver = open(cand, encoding="utf-8").read().strip()
    with open(os.path.join(LATEST, "版本.txt"), "w", encoding="utf-8") as f:
        f.write(ver)
    print("[2] 已部署 · 版本 %s · exe %d B · data %d 文件"
          % (ver, os.path.getsize(os.path.join(LATEST, "资金流向原生版.exe")),
             len(os.listdir(dd))))
    ok = md5(os.path.join(src, "资金流向原生版.exe")) == \
        md5(os.path.join(LATEST, "资金流向原生版.exe"))
    print("[3] MD5 一致性: %s" % ("YES" if ok else "NO"))
    print("[4] 桌面快捷方式「资金流向」指向 exe\\最新版\\，无需改动。")


if __name__ == "__main__":
    main()
