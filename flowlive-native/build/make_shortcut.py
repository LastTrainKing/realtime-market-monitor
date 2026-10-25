# -*- coding: utf-8 -*-
"""在桌面创建「资金流向原生版」快捷方式（.lnk）

用 WScript.Shell COM 创建，指向 dist 里的 exe，
并设置工作目录与图标。若 pywin32 可用则直接用 COM，
否则回退到临时 VBScript（Windows 自带 cscript 可执行）。
"""
import os, sys, subprocess, tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(BASE, "dist", "资金流向原生版.exe")
ICON = os.path.join(BASE, "app.ico")
DESK = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Desktop")
LNK = os.path.join(DESK, "资金流向原生版.lnk")


def make_via_pywin32():
    import win32com.client
    sh = win32com.client.Dispatch("WScript.Shell")
    sc = sh.CreateShortCut(LNK)
    sc.TargetPath = EXE
    sc.WorkingDirectory = os.path.dirname(EXE)
    sc.IconLocation = ICON + ",0"
    sc.Description = "资金流向 · 纯原生桌面版"
    sc.Save()
    return True


def make_via_vbs():
    vbs = f'''
Set sh = CreateObject("WScript.Shell")
Set sc = sh.CreateShortCut("{LNK}")
sc.TargetPath = "{EXE}"
sc.WorkingDirectory = "{os.path.dirname(EXE)}"
sc.IconLocation = "{ICON},0"
sc.Description = "资金流向 · 纯原生桌面版"
sc.Save
'''
    p = os.path.join(tempfile.gettempdir(), "_mklnk.vbs")
    with open(p, "w", encoding="gbk", errors="replace") as f:
        f.write(vbs)
    r = subprocess.run(["cscript", "//nologo", p], capture_output=True, timeout=60)
    return os.path.exists(LNK)


def main():
    if not os.path.exists(EXE):
        print("[FAILED] exe 不存在:", EXE)
        return 1
    ok = False
    try:
        ok = make_via_pywin32()
        print("方式: pywin32 COM")
    except Exception:
        ok = make_via_vbs()
        print("方式: VBScript")
    if ok and os.path.exists(LNK):
        print("[OK] 快捷方式已创建:", LNK, os.path.getsize(LNK), "B")
        return 0
    print("[FAILED] 创建失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
