# -*- coding: utf-8 -*-
"""验证 v3.0.4 跨分钟守卫（支撑 1~10 秒刷新档）。

核心命题：
  · 同一交易分钟内多次 tick → **只抓一次**（数据源是分钟粒度，重复抓无意义）
  · 跨分钟后第一次 tick → **立即抓**（不等下一拍，这是"更快"的关键）
  · 午休/盘后 → clamp_to_bar 夹到合法刻度，守卫仍能正确生效
  · 启动首轮 → 必定抓（_last_fetch_bar 为 None）

★ 测试方式：把 tick 逻辑抽出来做纯逻辑模拟（不依赖真实 GUI/网络），
  因为守卫的全部逻辑就是"比较当前刻度与上次刻度"。
"""
import sys
import os
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
import flowdata                                                          # noqa: E402

PASS = 0
FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


class FakeWin:
    """模拟 MainWindow 的自动刷新状态机（只保留守卫相关字段）。"""

    def __init__(self):
        self._last_fetch_bar = None
        self._review_locked = False
        self.fetch_count = 0
        self.worker = None

    def _in_window(self, now):
        if now.weekday() >= 5:
            return False
        hm = now.hour * 100 + now.minute
        return 925 <= hm <= 1505

    def tick(self, now):
        """复刻 _auto_tick 的守卫链（不含 reload 的真实副作用）。"""
        if not self._in_window(now):
            return "skip-hours"
        if self._review_locked:
            return "skip-locked"
        try:
            bar = flowdata.clamp_to_bar(now)
        except Exception:
            bar = None
        if bar is not None:
            if self._last_fetch_bar == bar:
                return "skip-same-bar"
            self._last_fetch_bar = bar
        self.fetch_count += 1
        return "fetch"


def dt(h, m, s=0, day=23):
    return datetime(2026, 9, day, h, m, s)


print("=" * 74)
print("A. 核心命题：同分钟只抓一次")
print("=" * 74)
w = FakeWin()
# 09:36:00 → 09:36:59 每 1 秒 tick 一次 = 60 次
for s in range(60):
    w.tick(dt(9, 36, s))
check(w.fetch_count == 1, "09:36 整分钟内 60 次 tick → 只抓 1 次",
      "实际 %d 次" % w.fetch_count)

print()
print("B. 跨分钟立即抓（不等下一拍）")
print("=" * 74)
r = w.tick(dt(9, 37, 0))
check(r == "fetch", "跨到 09:37:00 立即抓", "返回 %s" % r)
check(w.fetch_count == 2, "累计抓取 2 次", "实际 %d" % w.fetch_count)
# 09:37 内再 tick 不应抓
for s in range(1, 30):
    w.tick(dt(9, 37, s))
check(w.fetch_count == 2, "09:37 后续 tick 仍不抓", "实际 %d" % w.fetch_count)

print()
print("C. 1 秒档 vs 10 秒档：真实抓取次数相同")
print("=" * 74)
for interval in (1, 10):
    w2 = FakeWin()
    t = 0.0
    # 模拟 09:36:00 ~ 09:39:00（3 分钟 = 180 秒）
    while t <= 180:
        base = dt(9, 36, 0)
        now = base + __import__("datetime").timedelta(seconds=t)
        w2.tick(now)
        t += interval
    check(w2.fetch_count == 4,
          "%2d 秒档 / 3 分钟 → 抓 %d 次（4 个不同分钟）" % (interval, w2.fetch_count),
          "实际 %d 次" % w2.fetch_count)

print()
print("D. 午休与盘后：clamp_to_bar 夹到合法刻度")
print("=" * 74)
w3 = FakeWin()
r1 = w3.tick(dt(11, 35))         # 午休 → 夹到 11:30
check(w3._last_fetch_bar == "11:30", "11:35 夹到 11:30", "得到 %s" % w3._last_fetch_bar)
w3.tick(dt(11, 40))              # 仍在午休 → 还是 11:30 → 不抓
check(w3.fetch_count == 1, "午休内重复 tick 不抓", "实际 %d" % w3.fetch_count)
w3.tick(dt(14, 0))               # 下午 → 新刻度
check(w3.fetch_count == 2, "14:00 恢复抓取", "实际 %d" % w3.fetch_count)
w3.tick(dt(15, 30))              # 盘后 → 夹到 15:00
before = w3.fetch_count
w3.tick(dt(15, 45))              # 盘后重复 → 仍 15:00 → 不抓
check(w3.fetch_count == before, "盘后重复 tick 不抓", "实际 %d" % w3.fetch_count)

print()
print("E. 启动首轮必定抓（不迟滞）")
print("=" * 74)
w4 = FakeWin()
r = w4.tick(dt(10, 0, 0))
check(r == "fetch", "首次 tick 必定抓", "返回 %s" % r)

print()
print("F. 边界：盘前 / 周末 / 回看锁定")
print("=" * 74)
w5 = FakeWin()
check(w5.tick(dt(9, 10)) == "skip-hours", "09:10 盘前不抓")
# ★ 09:30 的 hm=930 落在 925~1505 窗口内 → **会抓**。
#   此时 clamp_to_bar 返回 None（09:31 前不写新行），代码走"bar 为 None →
#   不设游标、直接抓"分支：抓到的值由 refresh_panel_spot 覆盖末行。
#   这是**有意为之**：09:30 是集合竞价末尾，抓一次无害（面板不写脏刻度）。
check(w5.tick(dt(9, 30)) == "fetch", "09:30 在窗口内会抓（bar=None 分支）")
check(w5.tick(dt(9, 25)) == "fetch", "09:25 进入窗口可抓")
w6 = FakeWin()
check(w6.tick(dt(10, 0, day=26)) == "skip-hours", "周六不抓")
w7 = FakeWin()
w7._review_locked = True
check(w7.tick(dt(10, 0)) == "skip-locked", "回看锁定不抓")

print()
print("=" * 74)
print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
