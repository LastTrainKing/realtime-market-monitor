"""端到端集成测试：非交易时段 + 今天无数据 + 断网 → 应回退本地 20260921 上屏。"""
import os
import sys
import shutil

os.environ["QT_QPA_PLATFORM"] = "offscreen"
RT = r"D:\flowlive_v201\tmp\test_runtime"
os.environ["LOCALAPPDATA"] = RT

BUILD = r"D:\flowlive_v201\build"
sys.path.insert(0, os.path.join(BUILD, "core"))
sys.path.insert(0, BUILD)

DATA = os.path.join(RT, "FlowLiveNative", "data")
os.makedirs(DATA, exist_ok=True)
for n in ["index_20260921.json", "panel_20260921.json", "panel_20260921.npy", "panel_20260918.json",
          "panel_20260918.npy", "watchlist_20260921.json", "calendar.json",
          "sectors.json"]:
    srcf = os.path.join(r"D:\exe\data", n)
    if os.path.exists(srcf):
        shutil.copyfile(srcf, os.path.join(DATA, n))
# 断网桩：flowdata 一切请求返回 None
import flowdata                                        # noqa: E402
flowdata._get = lambda *a, **k: None

from PySide6.QtWidgets import QApplication             # noqa: E402
app = QApplication([])

import app_main_v202 as N                              # noqa: E402

w = N.Worker(tag="20260922", force=False, local_only=False)
w._in_trading_hours = lambda: False                    # 模拟凌晨/收盘后

out = {}
w.done.connect(lambda d: out.update(ok=True, d=d))
w.failed.connect(lambda m: out.update(ok=False, msg=m))
w.run()                                                # 同步执行

d = out.get("d") or {}
print("ok        :", out.get("ok"))
if not out.get("ok"):
    print("failed msg:", out.get("msg", "")[:500])
    sys.exit(1)
print("date_tag  :", d.get("date_tag"))
print("view_mode :", d.get("view_mode"))
print("n_sectors :", d.get("n_sectors"))
print("n_minutes :", d.get("n_minutes"))
print("source    :", d.get("source"))
print("board_in  :", len(d.get("board_in") or []), "rows")
print("radar_in  :", len(d.get("radar_in") or []), "rows")
print("watch     :", len(d.get("watch") or []), "rows")

ok = (d.get("date_tag") == "20260921"
      and d.get("view_mode") == "history"
      and (d.get("n_sectors") or 0) > 0
      and (d.get("n_minutes") or 0) > 0)
print()
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
