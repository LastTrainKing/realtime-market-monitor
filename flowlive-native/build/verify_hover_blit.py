# -*- coding: utf-8 -*-
"""verify_hover_blit.py —— 光标跟随加速的**正确性**验证（离屏，可重复）

性能提升是公开的数字，但 blit 局部重绘有个众所周知的失败模式：
**残影（ghosting）** —— 旧竖线的像素没被擦干净，屏幕上留下一条条竖线。
本脚本就针对这一点做**逐像素**验证，而不是"看起来没问题"。

六组检查
--------
1. 等价性：blit 画出的画面  vs  全量 draw 画出的画面  → 必须**逐像素一致**
2. 无残影：连续移动 40 个位置后，画面必须与"只有最后一条竖线"的全量重绘一致
3. 离开干净：_on_leave 后画面必须回到"完全没有竖线"的全量重绘状态
4. 快照失效：改尺寸 / 改主题后，快照必须重建（否则贴错背景 → 花屏）
5. 退回路径：不支持 blit 时安全退回，且**竖线不能消失**
6. 性能护栏：单次悬停 < 5 ms、提速比 ≥ 20×（防止未来改动悄悄退化）

★ 测试自身的纪律：**每项检查用一张全新的画布**。
  共用一个 FanChart 会让前一项遗留的竖线/参考线污染后一项的基准图，
  产生"差 117 像素"这类假失败 —— 本脚本第一版就踩了这个坑。
  统一用 `fresh()` 取新画布，避免状态串味。

用法：
    python verify_hover_blit.py
"""
from __future__ import annotations

import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np                                      # noqa: E402

import native_app as NA                                 # noqa: E402
from PySide6.QtGui import QFont                         # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

import bench_hover as BH                                # noqa: E402

RESULTS = []
VLINE_Z = 3          # 与 native_app.FanChart._mark 保持一致


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def fresh(w=1002, h=367):
    """一张全新的、已绘制的 FanChart（避免跨检查的状态污染）。"""
    fc, series = BH.make_fan()
    fc.resize(w, h)
    fc.plot(series)
    return fc, series


def actual(fc):
    """取当前真实像素缓冲。"""
    return np.asarray(fc.buffer_rgba()).copy()


def reference(fc, k, ax=None):
    """参考图：用**全量 draw** 画出一条位于 k 的竖线，返回像素。

    k=None 表示不画竖线（干净基准）。

    ★ 必须先摘掉画布上已有的竖线（含 fc._vline）再画基准线。
      否则会得到**两条重叠的竖线** —— 半透明线叠两次颜色更深，
      逐像素比对会差出一整条色带（实测 744 px，约 3 列 × 247 行），
      看起来像"残影没擦干净"，其实是本函数自己造的假失败。
    """
    ax = ax if ax is not None else fc.fig.axes[0]
    old = getattr(fc, "_vline", None)
    if old is not None:
        try:
            old.remove()
        except Exception:                            # noqa: BLE001
            pass
        fc._vline = None
    fc._invalidate_blit()
    fc.draw()
    if k is not None:
        ax.axvline(k, color=fc.th["accent"], linewidth=1.0, alpha=.75,
                   zorder=VLINE_Z)
        fc.draw()
    return actual(fc)


def px_equal(a, b):
    if a.shape != b.shape:
        return False, -1
    d = (a != b).any(axis=2)
    return (not d.any()), int(d.sum())


def main():
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Microsoft YaHei", 9))

    print("=" * 72)
    print("光标跟随（blit 局部重绘）正确性验证")
    print("=" * 72)

    # ---------------------------------------------------------------- 1
    print()
    print("[1] 等价性：blit 结果 vs 全量 draw 结果（逐像素）")
    fc, series = fresh()
    check("1a 后端声明支持 blit", bool(fc.supports_blit),
          f"supports_blit={fc.supports_blit}")
    ref = reference(fc, 120)
    fc2, _ = fresh()
    fc2._mark(120)
    got = actual(fc2)
    same, nd = px_equal(ref, got)
    check("1b blit 与全量重绘**逐像素一致**（漏画/错位检查）", same,
          f"差异像素={nd}" if not same else "完全一致")

    # ---------------------------------------------------------------- 2
    print()
    print("[2] 无残影：连续移动 40 个位置后，只应剩最后一条竖线")
    fc3, _ = fresh()
    for k in range(20, 60):
        fc3._mark(k)
    last_k = 59
    blit_after = actual(fc3)
    ref2 = reference(fc3, last_k)
    same2, nd2 = px_equal(ref2, blit_after)
    check("2a 连续移动 40 次后**无残影**（逐像素比对）", same2,
          f"差异像素={nd2}" if not same2 else "无残留")

    # 像素法独立复核：统计竖线色带所在的列数（40 条残影会是很多列）
    acc = np.array(hex_rgb(fc3.th["accent"]) + (255,), dtype=np.uint8)
    d = (np.abs(blit_after.astype(int) - acc.astype(int)).sum(axis=2) < 110)
    n_cols = int((d.sum(axis=0) > 30).sum())
    check("2b 竖线色带列数 ≈ 1（不是 40 条残影）", 1 <= n_cols <= 3,
          f"命中列数={n_cols}")

    # ---------------------------------------------------------------- 3
    print()
    print("[3] 鼠标离开绘图区后，画面必须完全干净")
    fc4, _ = fresh()
    fc4._mark(59)
    fc4._hover_px = 59
    fc4._on_leave()
    after_leave = actual(fc4)
    clean = reference(fc4, None)
    same3, nd3 = px_equal(clean, after_leave)
    check("3a _on_leave 后画面 == 无竖线的干净画面", same3,
          f"差异像素={nd3}" if not same3 else "完全干净")
    check("3b _on_leave 后 _vline 已释放", fc4._vline is None)

    # ---------------------------------------------------------------- 4
    print()
    print("[4] 快照失效：尺寸/主题变化后必须重建（防串味/花屏）")
    fc5, series5 = fresh()
    fc5._mark(100)
    k_before = fc5._blit_bbox
    fc5.resize(900, 340)
    fc5._mark(100)
    k_after = fc5._blit_bbox
    check("4a 改尺寸后几何指纹改变 → 快照重建", k_before != k_after,
          f"{k_before} → {k_after}")

    # 尺寸变化后 blit 仍须与全量一致（贴错背景就会花屏）
    fc6, _ = fresh(900, 340)
    fc6._mark(100)
    got6 = actual(fc6)
    ref6 = reference(fc6, 100)
    ok6, nd6 = px_equal(ref6, got6)
    check("4b 改尺寸后 blit 仍与全量重绘逐像素一致", ok6,
          f"差异像素={nd6}" if not ok6 else "一致")

    # 主题切换必须作废快照
    fc7, _ = fresh()
    fc7._mark(100)
    check("4c 悬停中快照已建立", fc7._blit_bg is not None)
    fc7.apply_theme(NA.coloring(NA.THEMES["dark"]))
    check("4d apply_theme 后快照被作废（须重建，防串色）",
          fc7._blit_bg is None and fc7._blit_bbox is None,
          f"_blit_bg={'None' if fc7._blit_bg is None else '非空'}")
    # 换主题后立刻悬停 → 画面必须与全量口径一致
    fc7._mark(100)
    got7 = actual(fc7)
    ref7 = reference(fc7, 100)
    ok7, nd7 = px_equal(ref7, got7)
    check("4e 换主题后 blit 仍与全量重绘逐像素一致", ok7,
          f"差异像素={nd7}" if not ok7 else "一致")

    # ---------------------------------------------------------------- 5
    print()
    print("[5] 不支持 blit 时安全退回，且竖线**不能消失**")
    fc8, _ = fresh()
    fc8._hover_px = None
    _orig = NA.FanChart.supports_blit
    try:
        NA.FanChart.supports_blit = False          # 伪装成不支持
        fc8._vline = None
        fc8._invalidate_blit()
        fc8._mark(150)
        fc8.draw()                                 # 落实 draw_idle
        vl = fc8._vline
        check("5a 退回路径仍创建了竖线对象", vl is not None)
        check("5b 退回路径竖线**可见**（未被 animated 吞掉）",
              vl is not None and vl.get_visible(),
              f"visible={vl.get_visible() if vl else None}")
        check("5c 退回路径确实走了全量重绘（快照未被建立）",
              fc8._blit_bg is None)
    finally:
        NA.FanChart.supports_blit = _orig

    # 竖线像素确实出现在画布上
    fc9, _ = fresh()
    base = actual(fc9)
    fc9._mark(150)
    fc9.draw()
    after9 = actual(fc9)
    ok9, nd9 = px_equal(base, after9)
    check("5d 竖线像素确实画在画布上（与无竖线画面不同）", not ok9,
          f"差异像素={nd9}")

    # ---------------------------------------------------------------- 6
    print()
    print("[6] 性能回归护栏")
    fcA, _ = fresh()
    fcA._mark(10)

    def med(fn, n=15):
        xs = []
        for _ in range(n):
            t = time.perf_counter()
            fn()
            xs.append((time.perf_counter() - t) * 1000.0)
        return statistics.median(xs)

    t_mark = med(lambda: fcA._mark(int(np.random.randint(0, 240))))
    t_full = med(fcA.draw, 7)
    check("6a 单次悬停 _mark < 5 ms（优化前实测约 120 ms）", t_mark < 5.0,
          f"{t_mark:.2f} ms")
    ratio = t_full / max(t_mark, 1e-9)
    check("6b 提速比 ≥ 20×", ratio >= 20,
          f"{t_full:.1f} ms / {t_mark:.2f} ms = {ratio:.0f}×")

    print()
    print("=" * 72)
    n = sum(1 for _, ok in RESULTS if ok)
    print(f"结果：{n}/{len(RESULTS)} PASS")
    fails = [nm for nm, ok in RESULTS if not ok]
    if fails:
        print("失败项：")
        for nm in fails:
            print("  -", nm)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
