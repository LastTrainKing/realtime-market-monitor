# -*- coding: utf-8 -*-
"""导出 v1.2.6 入口脚本的 code object 结构，与 v1.2.5 源码比对。

产出：
  v126_funcs.txt     函数/类清单（名 + 起始行）
  v126_strings.txt   所有字符串常量（带所属函数前缀）
然后与本仓库 v1.2.5 源码做同名函数行号比对，定位 v1.2.6 改动了哪些位置。
"""
import os
import sys
import marshal
import types

OUT = r"D:\flowlive_v201\build\_v126_extract"
BIN = os.path.join(OUT, "native_app.bin")


def walk(co, path, funcs, strings, depth=0):
    for c in co.co_consts:
        if isinstance(c, str):
            strings.append((path, c))
    funcs.append((co.co_name, co.co_firstlineno, path, depth))
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            walk(c, path + "/" + c.co_name, funcs, strings, depth + 1)


def main():
    data = open(BIN, "rb").read()
    co = marshal.loads(data)
    funcs, strings = [], []
    walk(co, "<module>", funcs, strings)

    with open(os.path.join(OUT, "v126_funcs.txt"), "w", encoding="utf-8") as f:
        for n, ln, p, d in funcs:
            f.write(f"{ln}\t{'  ' * d}{n}\t{p}\n")
    with open(os.path.join(OUT, "v126_strings.txt"), "w", encoding="utf-8") as f:
        for p, s in strings:
            f.write(f"{p}\t{s}\n")

    print("[1] 函数/代码对象:", len(funcs), " 字符串:", len(strings))

    # 顶层函数与类（depth 0/1）
    print("\n--- v1.2.6 顶层函数与类（行号）---")
    for n, ln, p, d in funcs:
        if d <= 1:
            print(f"  {ln:>5}  {'  '*(d)} {n}")

    print("\n--- 版本 / 便携 / 路径 相关字符串 ---")
    keys = ("1.2.6", "1.2.5", "_MEIPASS", "LOCALAPPDATA", "FlowLiveNative",
            "preflight", "portable", "便携", "data", "logs", "shots",
            "app.lock", "BASE")
    shown = 0
    for p, s in strings:
        if any(k in s for k in keys) and len(s) < 260:
            print(f"  [{p}]\n      {s!r}")
            shown += 1
            if shown > 90:
                print("  ... (更多见 v126_strings.txt)")
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
