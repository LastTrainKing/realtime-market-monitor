# -*- coding: utf-8 -*-
"""市场全景页版面几何的**像素级剖面**测量（不靠肉眼）。

原理：卡片 `QFrame#card` 有 1px 边框 #dfe3ea，页面底色是 #ffffff。
      沿某一行/列扫描整幅图像，把「颜色发生跳变」的 x/y 打印出来，
      外框（边框线）的位置就一目了然 —— 从而量出四个格子的宽高。

用法：python _pano_px.py <png> [<png> ...]
"""
import sys
from collections import Counter

from PIL import Image

BORDER = (223, 227, 234)     # #dfe3ea
CARD = (247, 248, 251)       # #f7f8fb
PAGE = (255, 255, 255)


def near(c, t, tol=6):
    return all(abs(c[i] - t[i]) <= tol for i in range(3))


def runs_of(pred, seq):
    out, cur = [], None
    for k in seq:
        if pred(k):
            cur = (k, k) if cur is None else (cur[0], k)
        else:
            if cur is not None:
                out.append(cur)
                cur = None
    if cur is not None:
        out.append(cur)
    return out


def profile(path):
    im = Image.open(path).convert("RGB")
    W, H = im.size
    px = im.load()
    print(f"\n=== {os.path.basename(path) if False else path}\n    {W}x{H} ===")

    # 找卡片边框的竖向位置：统计每列/每行上 #dfe3ea 的像素数
    col_border = [(x, sum(1 for y in range(H) if near(px[x, y], BORDER))) for x in range(W)]
    row_border = [(y, sum(1 for x in range(W) if near(px[x, y], BORDER))) for y in range(H)]

    def top_n(pairs, n, minv):
        return [k for k, v in sorted(pairs, key=lambda p: -p[1])[:n * 6] if v >= minv]

    vx = sorted(k for k, v in col_border if v > H * 0.55)   # 近乎贯穿整高的竖边框
    hy = sorted(k for k, v in row_border if v > W * 0.55)   # 近乎贯穿整宽的横边框
    print(f"贯穿式竖边框 x: {vx}")
    print(f"贯穿式横边框 y: {hy}")

    # 取一行（穿过卡片中部）打印颜色跳变点
    y = (hy[0] + 60) if hy else 300
    changes, prev = [], None
    for x in range(W):
        c = px[x, y]
        if prev is None or (not near(c, prev, 2)):
            changes.append((x, c))
        prev = c
    key = [(x, c) for x, c in changes
           if near(c, BORDER) or near(c, PAGE) or near(c, CARD)]
    print(f"\ny={y} 剖面颜色跳变（仅边框/页面/卡片色）:")
    print("   " + ", ".join(f"{x}:{'B' if near(c,BORDER) else ('P' if near(c,PAGE) else 'C')}"
                            for x, c in key))

    # 竖直方向剖面：取左卡片中部一列
    x = 200
    changes, prev = [], None
    for yy in range(H):
        c = px[x, yy]
        if prev is None or (not near(c, prev, 2)):
            changes.append((yy, c))
        prev = c
    key2 = [(yy, c) for yy, c in changes
            if near(c, BORDER) or near(c, PAGE) or near(c, CARD)]
    print(f"\nx={x} 剖面颜色跳变:")
    print("   " + ", ".join(f"{yy}:{'B' if near(c,BORDER) else ('P' if near(c,PAGE) else 'C')}"
                            for yy, c in key2))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        profile(p)
