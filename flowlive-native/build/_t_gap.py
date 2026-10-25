# -*- coding: utf-8 -*-
"""验证 allow_gap：跨缺口追加当前分钟 → 中间显式 NaN 占位行"""
import os, sys, json, tempfile, time
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import flowdata as fd

tmp = tempfile.mkdtemp(prefix="gap_")
fd.set_data_dir(tmp)
codes = ["90.BK%04d" % i for i in range(5)]
times = [fd._label_of_idx(i) for i in range(120)]          # 09:31..11:30
mat = np.tile(np.arange(120, dtype=float)[:, None], (1, 5)) * 1e8
fd.save_panel("t", {"times": times, "codes": codes,
                    "names": {c: c for c in codes},
                    "kinds": {c: "industry" for c in codes},
                    "source": "live", "data_date": "t"}, mat)

# 桩：当前时刻夹到 14:34，快照给 5 个板块各 77 亿
fd.clamp_to_bar = lambda now=None: "14:34"
fd.fetch_spot_batch = lambda secids, detail=False, workers=None: {
    s: ({"net": 7.7e9} if detail else 7.7e9) for s in secids}
fd.fetch_index_trend = lambda *a, **k: []

m, a = fd.refresh_panel_spot("t", allow_gap=True)
print("返回:", None if m is None else f"mat={a.shape} times={len(m['times'])}")
print("范围:", m["times"][118], m["times"][119], "|", m["times"][120], "...",
      m["times"][180], m["times"][181])
print("partial_history =", m.get("partial_history"),
      " gap =", m.get("gap_from"), "→", m.get("gap_to"))
print("末行值:", a[-1][:3], "（应为 7.7）")
gap_rows = a[120:181]
print("占位行全 NaN:", bool(np.isnan(gap_rows).all()), f"（{gap_rows.shape[0]} 行）")
print("占位行前一行(11:30)有值:", bool(np.isfinite(a[119]).all()))
print("时间轴严格递增:",
      all(fd._minute_idx(m["times"][i]) < fd._minute_idx(m["times"][i+1])
          for i in range(len(m["times"])-1)))
print("行数==times:", a.shape[0] == len(m["times"]))

# allow_gap=False 时必须保持旧语义（返回 None, None）
fd.save_panel("t2", {"times": times, "codes": codes,
                     "names": {c: c for c in codes},
                     "kinds": {c: "industry" for c in codes},
                     "source": "live", "data_date": "t"}, mat)
m2, a2 = fd.refresh_panel_spot("t2", allow_gap=False)
print("allow_gap=False →", (m2, a2), "（应为 (None, None)，保持旧语义）")
