# -*- coding: utf-8 -*-
"""逐字节比对两组 --shot 产物（源码版 vs 打包 exe 版）。

用法：python _cmp_shots.py <dirA> <prefixA> <dirB> <prefixB>
产物名形如 {prefix}_{k}_{tab}.png / .txt —— 按 (k, tab, ext) 配对，与前缀无关。
"""
import hashlib
import os
import re
import sys


def sha(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def collect(d, prefix):
    out = {}
    for n in os.listdir(d):
        if not n.startswith(prefix + "_"):
            continue
        m = re.match(rf"{re.escape(prefix)}_(\d+)_(.+?)\.(png|txt)$", n)
        if m:
            out[(int(m.group(1)), m.group(2), m.group(3))] = os.path.join(d, n)
    return out


def main():
    da, pa, db, pb = sys.argv[1:5]
    A, B = collect(da, pa), collect(db, pb)
    print(f"A={pa} {len(A)} 个    B={pb} {len(B)} 个")
    keys = sorted(set(A) | set(B))
    same, diff, miss = [], [], []
    for k in keys:
        if k not in A or k not in B:
            miss.append(k)
            continue
        ha, hb = sha(A[k]), sha(B[k])
        (same if ha == hb else diff).append((k, ha, hb))
    print(f"逐字节一致 : {len(same)}/{len(keys)}")
    for k, ha, hb in diff:
        print(f"  ✗ {k[1]}.{k[2]}  {ha[:12]} != {hb[:12]}")
    for k in miss:
        side = "A only" if k in A else "B only"
        print(f"  missing ({side}): {k[1]}.{k[2]}")
    print("结论：", "PASS —— 全部逐字节一致" if not diff and not miss else "FAIL")
    return 0 if not diff and not miss else 1


if __name__ == "__main__":
    sys.exit(main())
