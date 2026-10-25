# -*- coding: utf-8 -*-
"""make_shortcut_latest.py —— 创建「资金流向」桌面快捷方式（固定入口）。

目标指向 exe\\最新版\\（固定路径），升级部署时只需更新该文件夹内容，
快捷方式永不失效。运行一次即可；重复运行会覆盖重建（幂等）。
"""
import os
import win32com.client

D = r"C:\Users\user\Desktop"
LNK = os.path.join(D, "资金流向.lnk")
TGT = os.path.join(D, r"exe\最新版\资金流向原生版.exe")
WD = os.path.join(D, r"exe\最新版")
ICO = os.path.join(D, r"exe\最新版\_internal\app.ico,0")

ws = win32com.client.Dispatch("WScript.Shell")
lnk = ws.CreateShortcut(LNK)
lnk.TargetPath = TGT
lnk.WorkingDirectory = WD
lnk.IconLocation = ICO
lnk.Description = "资金流向 · 原生版（固定入口，始终指向最新版）"
lnk.Save()

chk = ws.CreateShortcut(LNK)
print("saved:", os.path.exists(LNK))
print("target:", chk.TargetPath)
print("workdir:", chk.WorkingDirectory)
print("icon:", chk.IconLocation)
