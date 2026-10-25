# -*- coding: utf-8 -*-
"""实测「增量刷新 → 界面全部更新」的端到端耗时。

10 秒周期能否成立，取决于整条链路：
    增量抓取(~350ms) + build_features + score_table + 6 张图重绘 + 表格填充
本脚本在真实 MainWindow 上量完整一轮，找出还能优化的环节。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "core"))

from PySide6.QtWidgets import QApplication                       # noqa: E402
import native_app as NA                                          # noqa: E402


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(NA.QFont("Microsoft YaHei", 9))
    app.setStyleSheet(NA.build_qss(NA.THEMES["light"]))

    print("\n=========== 增量刷新端到端耗时 ===========")

    t0 = time.perf_counter()
    win = NA.MainWindow()
    win.resize(1480, 940)
    win.show()
    for _ in range(12):
        app.processEvents()
        time.sleep(0.05)
    t_win = (time.perf_counter() - t0) * 1000
    print(f"  建窗 + 显示: {t_win:.0f} ms")

    # 等首屏数据到位
    t0 = time.perf_counter()
    for _ in range(600):
        app.processEvents()
        time.sleep(0.05)
        if win.data and win.data.get("F"):
            break
    t_data = (time.perf_counter() - t0) * 1000
    print(f"  等首屏数据: {t_data:.0f} ms  "
          f"（tag={ (win.data or {}).get('date_tag') }）")

    if not (win.data or {}).get("F"):
        print("  [FAIL] 首屏数据未到位，无法继续")
        return 1

    F0 = win.data["F"]
    print(f"  面板: {len(F0.times)} 分钟 × {len(F0.codes)} 板块")

    # ---- 量一轮完整的「增量刷新 → UI 更新」----
    print("\n  --- 手动走一轮增量刷新（含 UI 更新）---")
    rounds = []
    for i in range(3):
        t = time.perf_counter()
        # 1) 抓取 + 特征（同步调用，等价于 Worker 的活儿）
        import flowdata
        m, a = flowdata.refresh_panel_spot("today", on_progress=lambda _m: None)
        t_fetch = (time.perf_counter() - t) * 1000
        if m is None:
            print(f"    第 {i+1} 轮：增量不可用（跳过）")
            break

        import salience
        t = time.perf_counter()
        F = salience.build_features("today")
        t_feat = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        last = len(F.times) - 1
        salience.score_table(F, last, topn=30)
        salience.score_table(F, last, topn=30, direction="out")
        t_score = (time.perf_counter() - t) * 1000

        # 2) 让 on_data 跑一遍完整 UI 更新
        d = dict(win.data)
        d["F"] = F
        d["times"] = list(F.times)
        d["last"] = last
        t = time.perf_counter()
        try:
            win.on_data(d)
        except Exception as e:                           # noqa: BLE001
            print(f"    on_data 异常: {e}")
        t_ui = (time.perf_counter() - t) * 1000
        app.processEvents()

        tot = t_fetch + t_feat + t_score + t_ui
        rounds.append((t_fetch, t_feat, t_score, t_ui, tot))
        print(f"    第 {i+1} 轮: 抓取 {t_fetch:6.0f} + 特征 {t_feat:5.0f} "
              f"+ 榜单 {t_score:5.0f} + UI {t_ui:6.0f} = **{tot:6.0f} ms**")

    if rounds:
        avg = [sum(r[i] for r in rounds) / len(rounds) for i in range(5)]
        print(f"\n  平均: 抓取 {avg[0]:.0f} + 特征 {avg[1]:.0f} "
              f"+ 榜单 {avg[2]:.0f} + UI {avg[3]:.0f} = **{avg[4]:.0f} ms**")
        print(f"  → 10 秒周期占用 {avg[4]/10000*100:.1f}%，"
              f"30 秒周期占用 {avg[4]/30000*100:.1f}%")
        if avg[4] < 10000:
            print("  ✅ 单轮 < 10 秒，10 秒档可成立")
        else:
            print(f"  ⚠️ 单轮 {avg[4]:.0f} ms ≥ 10 秒，10 秒档会被跳过累积")

    # ---- 拆分 UI 更新里最贵的一项 ----
    print("\n  --- UI 更新分项（找最大头）---")
    d = dict(win.data)
    for label, fn in (
        ("render_charts", lambda: win.render_charts()),
        ("fill_tables", lambda: win.fill_tables()),
    ):
        t = time.perf_counter()
        try:
            fn()
        except Exception as e:                           # noqa: BLE001
            print(f"    {label}: 异常 {e}")
            continue
        dt = (time.perf_counter() - t) * 1000
        print(f"    {label:16s}: {dt:7.1f} ms")

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
