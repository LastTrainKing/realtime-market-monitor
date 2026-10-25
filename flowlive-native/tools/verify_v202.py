"""v2.0.2 修复验证（独立数据目录版）：离线跑，不启动窗口、不联网。"""
import os
import sys
import json
import shutil

BUILD = r"D:\flowlive_v201\build"
CORE = os.path.join(BUILD, "core")
DATA = r"D:\flowlive_v201\tmp\test_data"
sys.path.insert(0, CORE)
sys.path.insert(0, BUILD)

fail = []


def chk(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("   " + detail if detail else ""))
    if not cond:
        fail.append(label)


print("=== 0. 准备独立测试数据目录 ===")
os.makedirs(DATA, exist_ok=True)
for n in ["panel_20260921.json", "panel_20260921.npy", "panel_20260918.json",
          "panel_20260918.npy", "calendar.json"]:
    srcf = os.path.join(r"D:\exe\data", n)
    if os.path.exists(srcf):
        shutil.copyfile(srcf, os.path.join(DATA, n))
print("  prepared:", sorted(os.listdir(DATA)))

print()
print("=== 1. 生成板块清单离线缓存 sectors.json（从本地面板反推） ===")
j = json.load(open(os.path.join(DATA, "panel_20260921.json"), encoding="utf-8"))
kinds = {}
for secid in j["codes"]:
    kinds.setdefault(j["kinds"].get(secid, "concept"), []).append(
        [secid, secid.split(".", 1)[1], j["names"].get(secid),
         j["kinds"].get(secid, "concept")])
with open(os.path.join(DATA, "sectors.json"), "w", encoding="utf-8") as f:
    json.dump({"updated": "test", "kinds": kinds}, f, ensure_ascii=False)
print("  kinds:", {k: len(v) for k, v in kinds.items()})
chk("sectors.json 已生成", os.path.exists(os.path.join(DATA, "sectors.json")))

print()
print("=== 2. 断网时 list_sectors 回退本地缓存 ===")
import flowdata                                        # noqa: E402
flowdata.DATA = DATA
_orig_get = flowdata._get
flowdata._get = lambda *a, **k: None                   # 模拟完全没网
got_i = flowdata.list_sectors("industry")
got_c = flowdata.list_sectors("concept")
flowdata._get = _orig_get
chk("industry 离线可用", len(got_i) > 0, "n=%d" % len(got_i))
chk("concept 离线可用", len(got_c) > 0, "n=%d" % len(got_c))
chk("返回元组结构正确", bool(got_i) and len(got_i[0]) == 4, str(got_i[0]) if got_i else "-")

print()
print("=== 3. _latest_local_panel 探测本地最近交易日 ===")
import app_main_v202 as native_app                     # noqa: E402
W = native_app.Worker
latest = W._latest_local_panel(DATA)
chk("探测到 20260921", latest == "20260921", "got=%s" % latest)

print()
print("=== 4. _plan_fetch 本地回退判定 ===")


class Fake:
    pass


f = Fake()
f.tag = "20260922"
f.force = False
f._cal_days = ["20260918", "20260921"]
f.local_only = False
f._latest_local_panel = W._latest_local_panel
f._plan_fetch = lambda dd, pp: W._plan_fetch(f, dd, pp)
missing = os.path.join(DATA, "panel_20260922.npy")

f._in_trading_hours = lambda: False
plan1, why1 = f._plan_fetch(DATA, missing)
chk("非盘中 + 今天无数据 → local", plan1 == "local", "%s / %s" % (plan1, why1))

f._in_trading_hours = lambda: True
plan2, why2 = f._plan_fetch(DATA, missing)
chk("盘中 → full（去抓今天的）", plan2 == "full", "%s / %s" % (plan2, why2))

f._in_trading_hours = lambda: False
have = os.path.join(DATA, "panel_20260921.npy")
plan3, why3 = f._plan_fetch(DATA, have)
chk("已有当天面板 → cache（原逻辑不变）", plan3 == "cache", "%s / %s" % (plan3, why3))

print()
print("=== 5. 交易日历可信度 + 非交易时段零联网 ===")
import tradecal                                        # noqa: E402
days, approx = tradecal.get_trading_days(refresh=False)
info = tradecal.last_trusted()
print("  days[-3:] =", days[-3:], " approx =", approx, " info =", info)
chk("日历返回且带可信度", len(days) > 0 and "trusted" in info, str(info))

print()
print("=== 6. 关键代码特征（源码级检查） ===")
ep = open(os.path.join(BUILD, "app_main_v202.py"), encoding="utf-8").read()
chk("purge 有 untrusted 跳过分支", "calendar untrusted" in ep)
chk("调用了 last_trusted()", "tradecal.last_trusted()" in ep)
chk("非盘中不联网取日历", "refresh=self._in_trading_hours()" in ep)
chk("local 分支会切 self.tag", "fb = self._latest_local_panel(data_dir)" in ep)
chk("四模块竖排(QVBoxLayout)", "grid_w = QVBoxLayout(); grid_w.setSpacing(GAP_CARD)" in ep)
t2 = open(os.path.join(BUILD, "core2", "tradecal.py"), encoding="utf-8").read()
chk("core2 日历超时 3 秒", "timeout=3" in t2)
fd = open(os.path.join(CORE, "flowdata.py"), encoding="utf-8").read()
chk("板块清单缓存兜底", "_save_sectors_cache" in fd)

print()
print("=" * 56)
print("结果：%s" % ("全部通过" if not fail else "失败项 %s" % fail))
