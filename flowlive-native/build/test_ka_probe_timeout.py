# -*- coding: utf-8 -*-
"""验证 v3.0.3「探测期短超时」改动（core/flowdata._ka_timeout）。

设计意图（改动的唯一目的）：
  通道**未证实可用**时，用短超时快速判定"能不能用"，
  避免"接口不可用时启动程序"白等 6 × 完整超时。
  一旦某次成功（proven），**立刻恢复**调用方给的完整超时 ——
  也就是说正常盘中的超时语义必须**一字不变**。

本测试不需要联网：只验证 `_ka_timeout()` 的纯逻辑与状态耦合。
"""
import os
import sys
import time

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


def reset_state(proven=False, off=False):
    """复位通道状态，保证各用例互不影响。"""
    with flowdata._KA_LOCK:
        flowdata._KA_STATE["ok"] = 0
        flowdata._KA_STATE["fail"] = 0
        flowdata._KA_STATE["off"] = off
        flowdata._KA_STATE["proven"] = proven


print("=" * 70)
print("A. 未证实可用（proven=False）→ 探测期用短超时")
print("=" * 70)
reset_state(proven=False)
t = flowdata._ka_timeout(15.0)
check(t == flowdata.KA_PROBE_TIMEOUT, "15s 被压到探测超时",
      "%.1fs -> %.1fs" % (15.0, t))
t = flowdata._ka_timeout(8.0)
check(t == flowdata.KA_PROBE_TIMEOUT, "8s 被压到探测超时",
      "%.1fs -> %.1fs" % (8.0, t))
# 原本就比探测超时短的，不应被"抬高"
t = flowdata._ka_timeout(2.0)
check(t == 2.0, "2s（短于探测超时）保持 2s，不被抬高", "实际 %.1f" % t)

print()
print("=" * 70)
print("B. 已证实可用（proven=True）→ 恢复完整超时（关键回归点）")
print("=" * 70)
reset_state(proven=True)
t = flowdata._ka_timeout(15.0)
check(t == 15.0, "proven 后 15s 原样返回（盘中语义不变）", "实际 %.1f" % t)
t = flowdata._ka_timeout(8.0)
check(t == 8.0, "proven 后 8s 原样返回", "实际 %.1f" % t)
t = flowdata._ka_timeout(30.0)
check(t == 30.0, "proven 后 30s 原样返回", "实际 %.1f" % t)

print()
print("=" * 70)
print("C. 状态耦合：_ka_note(True) 应立刻把 proven 置真")
print("=" * 70)
reset_state(proven=False)
before = flowdata._ka_timeout(15.0)
flowdata._ka_note(True)                       # 模拟一次成功
after = flowdata._ka_timeout(15.0)
check(before == flowdata.KA_PROBE_TIMEOUT,
      "成功前是短超时", "%.1fs" % before)
check(after == 15.0, "一次成功后立刻恢复完整超时", "%.1fs" % after)

print()
print("=" * 70)
print("D. 异常入参不崩（健壮性）")
print("=" * 70)
reset_state(proven=False)
for bad in (None, "abc", object()):
    try:
        v = flowdata._ka_timeout(bad)
        check(isinstance(v, float) and v > 0,
              "异常入参 %r 返回可用超时" % type(bad).__name__, "%.1fs" % v)
    except Exception as e:                                # noqa: BLE001
        check(False, "异常入参 %r 不应抛异常" % type(bad).__name__, repr(e))

print()
print("=" * 70)
print("E. 端到端：6 次探测的总代价上限")
print("=" * 70)
reset_state(proven=False)
host = "127.0.0.1:9"                          # discard 端口，必然連不上
t0 = time.time()
for _ in range(flowdata.KA_PROBE_LIMIT):
    flowdata._keepalive_once(host, "/x", flowdata._ka_timeout(15.0))
dt = time.time() - t0
# ★ 口径说明（第一版断言写错，此处修正）：
#   `_keepalive_once` 内部对"连接被关闭"有透明重试（for attempt in (0,1)），
#   所以**单次调用最坏 = 2 × timeout**，不是 1 × timeout。
#   因此 6 次探测的最坏上限 = 6 × 2 × KA_PROBE_TIMEOUT = 36s（旧行为是 180s）。
#   本断言检查的是"确实比旧行为短一个数量级"，而不是一个绝对小值。
worst_old = flowdata.KA_PROBE_LIMIT * 2 * 15.0
worst_new = flowdata.KA_PROBE_LIMIT * 2 * flowdata.KA_PROBE_TIMEOUT
check(dt < worst_new, "6 次探测耗时在上限内",
      "实测 %.2fs（上限 %.0fs）" % (dt, worst_new))
check(worst_new <= worst_old / 4.0,
      "新上限不高于旧上限的 1/4（缩短一个数量级）",
      "旧 %.0fs -> 新 %.0fs" % (worst_old, worst_new))
print("     参考：旧行为最坏 %.0fs，新行为最坏 %.0fs" % (worst_old, worst_new))

print()
print("=" * 70)
print("F. 真实东财地址：探测失败的耗时（不联网也可跑，接口死时更快）")
print("=" * 70)
reset_state(proven=False)
t0 = time.time()
body = flowdata._keepalive_once(
    "push2.eastmoney.com",
    "/api/qt/ulist.np/get?fltt=2&invt=2&fields=f12&secids=90.BK1036&ut=" + flowdata.UT,
    flowdata._ka_timeout(15.0))
dt = time.time() - t0
print("     单次探测 %.2fs（ok=%s）" % (dt, body is not None))
check(dt <= flowdata.KA_PROBE_TIMEOUT * 2 + 1.5,
      "单次探测不超过 2×探测超时（含重试）",
      "%.2fs" % dt)
reset_state()

print()
print("=" * 70)
print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 70)
sys.exit(1 if FAIL else 0)
