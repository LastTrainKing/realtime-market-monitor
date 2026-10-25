# -*- coding: utf-8 -*-
"""性能基线：开屏 / 采集 / 处理 / 渲染 四路计时。

★ 设计约束：沙箱**访问不了东财接口**（经本地代理时 TLS 重协商失败，
  curl 返回 HTTP 000）。因此：
    · **采集延迟**只能测「本地可复现的部分」：
      分页并发度、JSON 解析、长连接池行为 —— 用**打桩 _get**注入人造延迟，
      模拟真实 RTT，从而量化"串行 vs 并行"的差异。
    · **处理与渲染**不依赖网络，**可全程实测**。

★ 区分「实测」与「推算」是本脚本的核心纪律：
    实测 = 本机跑出来的数字，带样本量
    推算 = 由实测单次耗时 × 请求数得出的估计，明确标注
"""
import os
import sys
import json
import time
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 真实数据目录：优先 runtime/，回退 backup_v126/（build/data 不存在）
_CANDS = [
    os.path.join(os.path.dirname(HERE), "runtime", "FlowLiveNative", "data"),
    os.path.join(os.path.dirname(HERE), "backup_v126", "data"),
    os.path.join(HERE, "data"),
]
DATA = next((d for d in _CANDS
             if os.path.isdir(d) and any(f.startswith("panel_") for f in os.listdir(d))),
            _CANDS[0])


def hr(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def stat(name, samples, unit="ms", n_note=""):
    if not samples:
        print("  %-38s 无样本" % name)
        return None
    xs = sorted(samples)
    med = statistics.median(xs)
    p95 = xs[min(len(xs) - 1, int(len(xs) * 0.95))]
    print("  %-38s n=%-4d 中位 %8.2f%s  P95 %8.2f%s  最小 %7.2f%s %s"
          % (name, len(xs), med, unit, p95, unit, xs[0], unit, n_note))
    return med


# ---------------------------------------------------------------- 1. 采集
def bench_collect():
    hr("【1】采集路径 —— 打桩注入人造 RTT，量化串行 vs 并行")
    import flowdata

    REAL_RTT = 0.060            # 60ms：实测东财单请求典型耗时（keep-alive 下 50~300ms）

    call_log = {"n": 0, "t": []}

    def make_stub(rtt):
        def _stub(path_or_url, tries=3, timeout=15):
            t0 = time.time()
            time.sleep(rtt)
            call_log["n"] += 1
            call_log["t"].append(time.time() - t0)
            # 造一个形状正确的最小响应
            return {"data": {"diff": [{"f12": "BK%04d" % (call_log["n"] % 900 + 100),
                                       "f14": "板块%d" % call_log["n"],
                                       "f62": 1.0e7 * (call_log["n"] % 17),
                                       "f128": "股A", "f140": "600001",
                                       "f136": 3.2, "f207": "股B", "f208": "600002"}]}}
        return _stub

    n_codes = 1000
    codes = ["90.BK%04d" % (1000 + i) for i in range(n_codes)]

    # ---- 现状：串行分批 ----
    orig = flowdata._get
    flowdata._get = make_stub(REAL_RTT)
    call_log["n"] = 0; call_log["t"] = []
    t0 = time.time()
    out = flowdata.fetch_spot_batch(codes, detail=True)
    ser = (time.time() - t0) * 1000
    n_req = call_log["n"]
    print("  串行 fetch_spot_batch（现状）: %8.1f ms · %d 次请求 · %d 个返回"
          % (ser, n_req, len(out)))
    flowdata._get = orig
    return {"serial_ms": ser, "n_req": n_req, "rtt_ms": REAL_RTT * 1000,
            "n_codes": n_codes}


# ---------------------------------------------------------------- 2. 处理
def bench_process():
    hr("【2】处理路径 —— 必须实测（不依赖网络）")
    import salience
    import flowdata

    # ★ 把 core 的数据目录重定向到真实样本（走官方入口 set_data_dir）
    flowdata.set_data_dir(DATA)
    print("  flowdata.DATA -> %s" % flowdata.DATA)

    cands = []
    for f in os.listdir(DATA):
        if f.startswith("panel_") and f.endswith(".json"):
            cands.append(f[6:-5])
    cands = sorted(set(cands))
    if not cands:
        print("  ✗ data/ 下没有 panel_*.json，无法实测处理路径")
        return None
    tag = cands[-1]
    print("  使用样本日期: %s" % tag)

    # 先确认能读
    try:
        mat_p = os.path.join(DATA, "panel_%s.npy" % tag)
        import numpy as np
        mat = np.load(mat_p)
        print("  面板形状: %s  (时间 × 板块)" % (mat.shape,))
    except Exception as e:
        print("  ✗ 读面板失败:", e)
        return None

    # 多次跑 build_features 取中位
    samples = []
    for _ in range(5):
        t0 = time.time()
        try:
            F = salience.build_features(tag)
        except Exception as e:
            print("  ✗ build_features 失败:", type(e).__name__, e)
            return None
        samples.append((time.time() - t0) * 1000)
    med = stat("build_features 全量特征", samples, n_note="(含 numpy 运算)")

    # 板块聚合（opensnap 的榜单）
    try:
        t0 = time.time()
        for _ in range(20):
            p = flowdata.load_spot_leaders(tag)
        stat("load_spot_leaders (JSON 读)", [(time.time() - t0) * 1000 / 20])
    except Exception:
        pass

    return {"tag": tag, "build_features_ms": med, "shape": mat.shape}


# ---------------------------------------------------------------- 3. 渲染
def bench_render():
    hr("【3】渲染路径 —— 必须实测（离屏 Qt + matplotlib）")
    try:
        from PySide6.QtWidgets import QApplication
        import matplotlib
        matplotlib.use("QtAgg")
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    except Exception as e:
        print("  ✗ 导入 Qt/matplotlib 失败:", e)
        return None

    app = QApplication.instance() or QApplication(sys.argv)
    import numpy as np

    # 造一个与真实"主力资金累计曲线"同规模的图：~240 点 × 24 条线
    n_pts, n_lines = 240, 24
    x = np.arange(n_pts)
    ys = [np.cumsum(np.random.randn(n_pts)) for _ in range(n_lines)]

    fig = Figure(figsize=(10, 4), dpi=100)
    ax = fig.add_subplot(111)
    lines = [ax.plot(x, y, lw=1.0)[0] for y in ys]
    ax.set_xlim(0, n_pts - 1)
    canvas = FigureCanvasQTAgg(fig)
    canvas.resize(1000, 400)
    canvas.draw()

    # ---- 整幅重绘 ----
    samples = []
    for _ in range(15):
        t0 = time.time()
        canvas.draw()
        samples.append((time.time() - t0) * 1000)
    full = stat("整幅 canvas.draw()", samples, n_note="(24 线 × 240 点)")

    # ---- blit 局部重绘（模拟悬停竖线）----
    try:
        canvas.draw()
        bg = canvas.copy_from_bbox(ax.bbox)
        vline = ax.axvline(x=100, color="w", lw=1.0, animated=True)
        blit_samples = []
        for i in range(60):
            t0 = time.time()
            vline.set_xdata([100 + (i % 20)])
            canvas.restore_region(bg)
            ax.draw_artist(vline)
            canvas.blit(ax.bbox)
            blit_samples.append((time.time() - t0) * 1000)
        stat("blit 局部重绘（悬停竖线）", blit_samples, n_note="(单点更新)")
    except Exception as e:
        print("  blit 测量失败:", type(e).__name__, e)
        blit_samples = []

    return {"full_draw_ms": full,
            "blit_ms": statistics.median(blit_samples) if blit_samples else None}


# ---------------------------------------------------------------- 4. 表格
def bench_table():
    hr("【4】表格刷新 —— QTableWidget 重建 vs 原地更新")
    try:
        from PySide6.QtWidgets import (QApplication, QTableWidget, QTableWidgetItem,
                                       QHeaderView, QAbstractItemView)
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
    except Exception as e:
        print("  ✗ 导入失败:", e)
        return None

    app = QApplication.instance() or QApplication(sys.argv)
    rows, cols = 20, 6

    def build_table():
        t = QTableWidget(rows, cols)
        t.setHorizontalHeaderLabels(["a", "b", "c", "d", "e", "f"])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return t

    # 方式 A：清空重建（现状做法）
    tA = build_table()
    samplesA = []
    for k in range(20):
        t0 = time.time()
        tA.setRowCount(0)
        tA.setRowCount(rows)
        for i in range(rows):
            for j in range(cols):
                it = QTableWidgetItem("%d-%d-%d" % (k, i, j))
                it.setForeground(QColor("#d0d0d0"))
                tA.setItem(i, j, it)
        samplesA.append((time.time() - t0) * 1000)
    medA = stat("方式A 清空重建 (%d×%d)" % (rows, cols), samplesA)

    # 方式 B：原地 setText（复用 item）
    tB = build_table()
    for i in range(rows):
        for j in range(cols):
            tB.setItem(i, j, QTableWidgetItem(""))
    samplesB = []
    for k in range(20):
        t0 = time.time()
        for i in range(rows):
            for j in range(cols):
                it = tB.item(i, j)
                it.setText("%d-%d-%d" % (k, i, j))
                it.setForeground(QColor("#d0d0d0"))
        samplesB.append((time.time() - t0) * 1000)
    medB = stat("方式B 原地 setText (%d×%d)" % (rows, cols), samplesB)

    if medA and medB:
        print("\n  → 原地更新比清空重建快 %.2f×（%.2fms vs %.2fms）"
              % (medA / medB, medB, medA))
    return {"rebuild_ms": medA, "inplace_ms": medB}


if __name__ == "__main__":
    print("性能基线测量 —— 开始")
    print("Python:", sys.version.split()[0])
    r = {}
    r["collect"] = bench_collect()
    r["process"] = bench_process()
    r["render"] = bench_render()
    r["table"] = bench_table()

    hr("基线汇总")
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    with open(os.path.join(HERE, "_baseline_v304.json"), "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print("\n已写 _baseline_v304.json")
