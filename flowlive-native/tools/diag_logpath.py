# -*- coding: utf-8 -*-
"""诊断：D 盘上「追加到已存在文件」是否被拒 —— 定位日志静默失败的真因。

背景：v2.0.1 的 exe 部署到 D:\\exe 后，运行一切正常（截图/表格全部产出），
但 D:\\exe\\logs\\app_20260921.log 迟迟不更新，C 盘也没有记录。
而同一个 exe 在一个"全新目录"（dist 构建目录，logs 尚不存在）里跑，
`[diag] log write test=OK` —— 日志写得好好的。

假设：D 盘文件系统上，**对已存在文件的写入（覆盖/追加）被拒**，
而"新建"与"删除"正常 —— 于是日志文件一旦存在，后续追加全部静默失败
（log_line 内部 try/except 吞掉了异常）。
本脚本逐条验证这个假设。
"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

CASES = [
    (r"D:\exe\logs\app_20260921.log", "D:\\exe 已有日志（append）"),
    (r"D:\exe\logs\_probe_new.log", "D:\\exe 全新日志（新建+append）"),
    (r"D:\flowlive_v201\build\dist_v201b\资金流向原生版\logs\app_20260921.log",
     "dist 目录日志（append 二次）"),
    (r"D:\flowlive_v201\build\dist_v201b\资金流向原生版\logs\_probe2.log",
     "dist 目录全新日志"),
]


def main():
    print("=" * 74)
    print("D 盘文件写入行为诊断")
    print("=" * 74)
    for p, label in CASES:
        existed = os.path.exists(p)
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write("")
            print(f"  [OK]   {label:<34s} 原本存在={existed}")
        except Exception as e:                           # noqa: BLE001
            print(f"  [FAIL] {label:<34s} 原本存在={existed}  "
                  f"{type(e).__name__}: {e}")
        if not existed and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    print("\n--- 覆盖（'w' 模式）行为 ---")
    for p, label in ((r"D:\exe\logs\app_20260921.log", "D:\\exe 已有日志（'w' 覆盖）"),):
        try:
            with open(p, "a", encoding="utf-8"):
                pass
            print(f"  [OK]   {label}")
        except Exception as e:                           # noqa: BLE001
            print(f"  [FAIL] {label}  {type(e).__name__}: {e}")

    print("\n--- 目录清单 ---")
    for d in (r"D:\exe\logs",
              r"D:\flowlive_v201\build\dist_v201b\资金流向原生版\logs"):
        try:
            items = [(f, os.path.getsize(os.path.join(d, f)))
                     for f in os.listdir(d)]
            print(f"  {d}\n      {items}")
        except Exception as e:                           # noqa: BLE001
            print(f"  {d}  读取失败: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
