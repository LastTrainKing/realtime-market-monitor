# -*- coding: utf-8 -*-
"""fix_ps1_bom.py -- 给 .ps1 补 UTF-8 BOM。

为什么需要：Windows PowerShell 5.1 读取**无 BOM** 的 .ps1 时按系统 ANSI
（简中环境 = GBK）解码。含中文的脚本会因此乱码，且中文全角引号会错配，
报出「字符串缺少终止符」这类与真实原因无关的解析错误。
加 BOM 后 PS 5.1 才按 UTF-8 解析。

用法：python fix_ps1_bom.py <file1.ps1> [file2.ps1 ...]
"""
import io
import os
import sys

BOM = b"\xef\xbb\xbf"

for p in sys.argv[1:]:
    raw = open(p, "rb").read()
    if raw.startswith(BOM):
        print("已有 BOM，跳过：%s" % p)
        continue
    s = raw.decode("utf-8")
    with open(p, "wb") as f:
        f.write(BOM + s.encode("utf-8"))
    print("已补 BOM：%s  (%d → %d 字节)" % (p, len(raw), os.path.getsize(p)))
