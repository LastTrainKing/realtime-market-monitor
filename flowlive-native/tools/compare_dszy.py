# -*- coding: utf-8 -*-
"""对比 D:\\ 与桌面副本 DSZY 的差异（只读，不写任何文件）。

用途：用户把 D 盘整份复制到了桌面，需要知道"这一份到底是不是完整的、
以及有没有已经过时/多余的东西"。
输出三类：① 只在 D 盘有的 ② 只在副本有的 ③ 两边都有但内容不同（按大小粗判）。
"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

A = r"D:\."                      # 源：D 盘
B = r"C:\Users\user\Desktop\DSZY"  # 副本

SKIP_DIRS = {"System Volume Information", "$RECYCLE.BIN", "tmp"}


def walk(root, base_prefix):
    """收集 {相对路径: 大小}，跳过系统目录与缓存。"""
    out = {}
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and d != "__pycache__"]
        for f in files:
            p = os.path.join(r, f)
            try:
                out[os.path.relpath(p, base_prefix)] = os.path.getsize(p)
            except OSError:
                pass
    return out


def main():
    print("=" * 72)
    print("D 盘  ⇄  桌面副本 DSZY  差异对比（只读）")
    print("=" * 72)
    a = walk(A, A)
    b = walk(B, B)
    print(f"\n文件数：D 盘 {len(a):,}   DSZY {len(b):,}")

    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    both = set(a) & set(b)
    differ = sorted(k for k in both if a[k] != b[k])

    def _show(title, keys, sizes=None, limit=25):
        print(f"\n【{title}】{len(keys)} 个")
        for k in keys[:limit]:
            sz = f"  {sizes[k]:,} B" if sizes else ""
            print(f"    {k}{sz}")
        if len(keys) > limit:
            print(f"    …（其余 {len(keys) - limit} 个略）")

    _show("只在 D 盘有（副本缺）", only_a, a)
    _show("只在副本有（D 盘已删/新增）", only_b, b)
    _show("两边都有但大小不同", differ, a)

    # 副本里那些"构建中间产物"占了多少
    #   ★ 只统计**真正的中间产物**：PyInstaller 的 _wk_*（工作目录）与
    #     被取代的 dist_v201_onedir / dist_v201b。**不把 native_env 算进来** ——
    #     它是构建用的虚拟环境（PySide6+matplotlib+PyInstaller），
    #     以后要重新打包还得用它，属于"要保留的东西"而不是垃圾。
    #     第一版脚本把它误算成可清理项，属于分类错误，已修正。
    JUNK_PAT = ("\\_wk_", "/_wk_", "dist_v201_onedir", "dist_v201b",
                "__pycache__")
    junk = [k for k in b if any(p in k for p in JUNK_PAT)]
    junk_sz = sum(b[k] for k in junk)
    print(f"\n【副本里的构建中间产物】{len(junk)} 个文件 / "
          f"{junk_sz / 1048576:.0f} MB")
    print("    （_wk_* 与旧 dist_* 为 PyInstaller 中间产物，可安全清理；"
          "__pycache__ 是字节码缓存）")
    print("    ★ native_env/ 是**构建环境**（PySide6 + matplotlib + "
          "PyInstaller），重新打包要用它，不建议删。")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
