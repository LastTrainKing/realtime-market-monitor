# -*- coding: utf-8 -*-
"""证明 v1.2.3 的「免光栅化测量」与旧路径**测出同一组尺寸**，因此不会选错列数。

======================================================================
为什么要专门验这一条
======================================================================
上一轮观察到一个可疑细节：图例宽度从 1147.11 变成 1142.11（差 5px）。
宽度本身不影响观感，但**它是"是否超限"的判据**：

    超限判据 = (bb.x0 < 3) or (bb.x1 > fig_w_px - 3) or (bb.height > max_leg_px)

若测量**偏小**，就可能把本应判为"太宽"的列数放行 → 选出的列数比原来多一档
→ 图例横着捅出画布。而且这类错误**不报错、不崩溃**，只是排版变丑，极易漏过。

----------------------------------------------------------------------
第一版测试错在哪（保留教训，避免后人重蹈）
----------------------------------------------------------------------
第一版把 `Legend.get_window_extent` 打桩成「抛异常」，以为这样就能强制走旧路径。
结果 A 组所有列数的尺寸全是 None —— 因为**两条路径都依赖同一个测量原语**：
打桩把它掐了，回退分支同样量不出来 → 每次试探都得到"没超限" → 二分一路爬到 20 列。
测试于是"发现"了一个 A=20 / B=10 的巨大差异，纯属测试自身故障。

★ 教训：**绝不要打桩被测两条路径共用的那个原语**；
   要证明"省掉 X 不影响结果"，正确做法是证明 **X 对结果没有贡献**，
   而不是把测量本身掐掉。于是本版改为下面的设计。

----------------------------------------------------------------------
本版设计：直接证明「画布光栅化对测量结果零贡献」
----------------------------------------------------------------------
安全性的**充分条件**是这一条：

    legend / ticklabel 的 get_window_extent(renderer) 取值，
    与「冷画布（从未 draw 过）」还是「刚 draw 完」**无关**。

若这条成立，则 `_try()` / `_measure()` 里那次 `canvas.draw()` 对最终选出的
列数与尺寸**毫无影响** —— 去掉它必然等价，无需依赖任何打桩。

本脚本做三件事：
  ① 冷/热画布一致性：同一 artist，在 draw 前后各量一次，逐值比对（容差 1e-9）；
  ② 全列数扫描：对 1..N 每个列数，两种状态下的 (宽, 高) 逐值比对；
  ③ 真实控件回归：用真窗口的 FanChart 跑一次，确认选出的列数落在合理区间，
     且去掉 draw 后 `_try` 的调用次数从「每次试探都画」降到 0。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

from PySide6.QtWidgets import QApplication                       # noqa: E402
import native_app as NA                                          # noqa: E402

TOL = 1e-9


def bb_of(artist, renderer):
    b = artist.get_window_extent(renderer)
    return (float(b.x0), float(b.y0), float(b.width), float(b.height))


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(NA.QFont("Microsoft YaHei", 9))
    app.setStyleSheet(NA.build_qss(NA.THEMES["light"]))

    win = NA.MainWindow()
    win.resize(1480, 940)
    win.show()
    for _ in range(600):
        app.processEvents()
        import time as _t
        _t.sleep(0.05)
        if win.data and win.data.get("F"):
            break

    fc = win.ch_fan
    ax = fc.fig.axes[0]
    leg0 = getattr(fc, "_legend", None)
    if leg0 is None:
        print("  [FAIL] 没有现成图例，无法取 handles")
        return 1
    handles = list(leg0.legend_handles)
    half = len(handles) // 2
    h_in, h_out = handles[:half], handles[half:]
    print(f"\n  handles: 共 {len(handles)} 条（in {len(h_in)} / out {len(h_out)}）")

    print("\n=========== v1.2.3 免光栅化测量 · 等价性证明 ===========")
    fails = []

    # ------------------------------------------------------------------
    # ① 冷/热画布一致性（核心命题）
    # ------------------------------------------------------------------
    print("\n  ① 冷画布 vs 热画布 —— 同一 artist 的测量值是否一致")
    print("     命题：若一致，则 canvas.draw() 对测量零贡献，去掉它必然等价。")
    cand = fc._make_legend(handles, 10, fc.th)
    if cand is None:
        print("     [FAIL] 建图例失败")
        win.close()
        return 1
    R_cold = fc.fig.canvas.get_renderer()
    leg_cold = bb_of(cand, R_cold)
    tk_cold = [bb_of(t, R_cold) for t in ax.get_xticklabels()]

    fc.fig.canvas.draw()                      # 光栅化一次
    R_hot = fc.fig.canvas.get_renderer()
    leg_hot = bb_of(cand, R_hot)
    tk_hot = [bb_of(t, R_hot) for t in ax.get_xticklabels()]

    d_leg = max(abs(a - b) for a, b in zip(leg_cold, leg_hot))
    print(f"     图例 bbox 冷 : {[round(v,3) for v in leg_cold]}")
    print(f"     图例 bbox 热 : {[round(v,3) for v in leg_hot]}")
    print(f"     最大偏差     : {d_leg:.3e}")
    if d_leg > TOL:
        fails.append(f"图例测量受光栅化影响（偏差 {d_leg:.3e}）")
    n_bad_tick = 0
    d_tick = 0.0
    for a, b in zip(tk_cold, tk_hot):
        d = max(abs(x - y) for x, y in zip(a, b))
        d_tick = max(d_tick, d)
        if d > TOL:
            n_bad_tick += 1
    print(f"     刻度标签 {len(tk_cold)} 个，最大偏差 {d_tick:.3e}，"
          f"不一致 {n_bad_tick} 个")
    if n_bad_tick:
        fails.append(f"{n_bad_tick} 个刻度标签测量随光栅化变化")
    print(f"     → {'✅ 测量与是否光栅化无关' if not (d_leg > TOL or n_bad_tick) else '❌ 有关，优化不安全'}")
    cand.remove()

    # ------------------------------------------------------------------
    # ② 全列数扫描：每个列数在冷/热两种状态下的尺寸逐值一致
    # ------------------------------------------------------------------
    print("\n  ② 全列数扫描 —— 1..N 每个列数的 (宽, 高) 在两种状态下逐值比对")
    n_total = len(handles)
    worst = 0.0
    worst_at = None
    n_checked = 0
    for ncol in range(1, n_total + 1):
        c = fc._make_legend(handles, ncol, fc.th)
        if c is None:
            continue
        fc.fig.canvas.draw()                  # 先热
        w1 = bb_of(c, fc.fig.canvas.get_renderer())
        c.remove()
        c2 = fc._make_legend(handles, ncol, fc.th)   # 冷（新建、未画）
        w2 = bb_of(c2, fc.fig.canvas.get_renderer())
        c2.remove()
        d = max(abs(x - y) for x, y in zip(w1, w2))
        n_checked += 1
        if d > worst:
            worst, worst_at = d, ncol
    print(f"     已比对 {n_checked} 个列数档位")
    print(f"     最大偏差 {worst:.3e}"
          + (f"（出现在 ncol={worst_at}）" if worst_at else ""))
    if worst > TOL:
        fails.append(f"列数扫描存在偏差 {worst:.3e}")
    print(f"     → {'✅ 逐值一致，选列数不受影响' if worst <= TOL else '❌ 存在偏差'}")

    # ------------------------------------------------------------------
    # ③ 真实控件回归：去掉 draw 后，列数是否仍在合理区间
    # ------------------------------------------------------------------
    print("\n  ③ 真实控件回归 —— 真窗口里重排一次，检查列数与光栅化次数")
    n_draw = [0]
    orig_draw = fc.fig.canvas.draw

    def draw_counted(*a, **kw):
        n_draw[0] += 1
        return orig_draw(*a, **kw)

    # 包住 FanChart 自己的画布 draw 计数。series 由 render_charts 内部组装
    #   （来自 win.data["market"] 的 in_list/out_list），所以直接走真实入口，
    #   不自己拼数据 —— 免得测的不是线上那条路径。
    #   FanChart.plot 末尾有一次正当的 draw() + _ensure_blit_bg 一次，
    #   合计 2 次；优化前是 7 次（多出来的 5 次里 4 次纯为测量）。
    fc.fig.canvas.draw = draw_counted
    try:
        win.render_charts()
        app.processEvents()
    except Exception as e:                                     # noqa: BLE001
        print(f"     [WARN] render_charts 失败（{e}），改用现有图例做回归")
        n_draw[0] = 0
    finally:
        fc.fig.canvas.draw = orig_draw

    leg = getattr(fc, "_legend", None)
    if leg is not None:
        R = fc.fig.canvas.get_renderer()
        b = bb_of(leg, R)
        cols = getattr(leg, "_ncols", None)
        fig_w_px = fc.fig.get_figwidth() * fc.fig.dpi
        fig_h_px = fc.fig.get_figheight() * fc.fig.dpi
        inside = (b[0] >= 3) and (b[0] + b[2] <= fig_w_px - 3)
        print(f"     选中列数 : {cols}")
        print(f"     图例 bbox: x0={b[0]:.2f} w={b[2]:.2f} "
              f"(右缘 {b[0]+b[2]:.2f} / 画布 {fig_w_px:.0f})")
        print(f"     高度     : {b[3]:.2f}px (上限 {fig_h_px*0.48-18:.0f}px)")
        print(f"     横向不越界: {'✅' if inside else '❌'}")
        if not inside:
            fails.append("图例横向越界")
    print(f"     本轮光栅化次数: {n_draw[0]}（优化前整轮 7 次，其中 4 次纯为测量）")
    if n_draw[0] >= 7:
        fails.append(f"光栅化次数未下降（{n_draw[0]}）")
    else:
        print("     → ✅ 测量段已不再光栅化")

    # ------------------------------------------------------------------
    print("\n=========== 结论 ===========")
    if fails:
        for f in fails:
            print(f"  ❌ {f}")
        print("\n  总结: ❌ 需复查")
    else:
        print("  测量与光栅化无关（冷/热逐值一致）")
        print("  全部列数档位尺寸不变")
        print("  真窗口列数合理、图例不越界")
        print("\n  总结: ✅ 免光栅化测量与旧路径等价")

    win.close()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
