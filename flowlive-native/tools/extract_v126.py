# -*- coding: utf-8 -*-
"""从现网 exe 提取 native_app 的字节码，导出字符串常量与函数名。

目的：归档包是 v1.2.5，而 D:\\exe 是 v1.2.6（新增便携模式）。
core/ 已确认两边逐字节相同 → 改动只在 native_app.py。
这里把 v1.2.6 的 native_app 字节码取出来，扫出所有常量字符串，
用来精确还原便携模式那段路径逻辑，避免凭猜测重写。
"""
import os
import sys
import marshal
import dis
import types

EXE = r"D:\exe\资金流向原生版.exe"
OUT = r"D:\flowlive_v201\build\_v126_extract"
os.makedirs(OUT, exist_ok=True)


def main():
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    print("[1] 打开 CArchive:", EXE)
    arch = CArchiveReader(EXE)
    names = list(arch.toc)
    print("    条目数:", len(names))
    pyz_name = next((n for n in names if n.lower().endswith(".pyz")), None)
    print("    PYZ:", pyz_name)
    if not pyz_name:
        return 1

    data = arch.extract(pyz_name)
    pyz_path = os.path.join(OUT, "PYZ.pyz")
    with open(pyz_path, "wb") as f:
        f.write(data)
    print("    已写出:", pyz_path, len(data), "B")

    pyz = ZlibArchiveReader(pyz_path)
    mods = sorted(pyz.toc)
    print("[2] PYZ 内模块数:", len(mods))
    want = [m for m in mods if "native_app" in m]
    print("    含 native_app 的:", want)

    code = None
    for m in want:
        code = pyz.extract(m)
        if code is not None:
            print("    提取成功:", m, type(code))
            break
    if code is None:
        print("    [FAILED] 未找到 native_app 模块")
        return 1

    # 递归收集常量字符串与函数名
    strings = []
    funcs = []
    seen = set()

    def walk(co, prefix=""):
        if id(co) in seen:
            return
        seen.add(id(co))
        for c in co.co_consts:
            if isinstance(c, str):
                strings.append((prefix, c))
            elif isinstance(c, types.CodeType):
                walk(c, prefix + co.co_name + ".")
        funcs.append((prefix + co.co_name, co.co_firstlineno))

    walk(code)
    print("[3] 函数数:", len(funcs), " 字符串常量数:", len(strings))

    with open(os.path.join(OUT, "funcs.txt"), "w", encoding="utf-8") as f:
        for n, ln in funcs:
            f.write(f"{ln}\t{n}\n")
    with open(os.path.join(OUT, "strings.txt"), "w", encoding="utf-8") as f:
        for p, s in strings:
            f.write(f"{p}\t{s}\n")

    # 反汇编顶层，落一份便于人工核对
    with open(os.path.join(OUT, "disasm.txt"), "w", encoding="utf-8") as f:
        dis.dis(code, file=f, depth=3)

    print("[4] 已写出 funcs.txt / strings.txt / disasm.txt 到", OUT)

    # 直接把与便携/路径相关的字符串打出来
    keys = ("data", "logs", "shots", "lock", "portable", "_MEIPASS", "LOCALAPPDATA",
            "preflight", "BASE", "FlowLiveNative", "便携")
    print("\n--- 路径/便携相关字符串 ---")
    for p, s in strings:
        if any(k in s for k in keys) and len(s) < 200:
            print(f"  [{p}] {s!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
