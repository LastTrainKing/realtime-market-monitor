# -*- coding: utf-8 -*-
"""v3.0.2 快照补采逻辑单元测试（纯逻辑，不联网）。

覆盖用户需求 A+B 的 B 部分（盘中/盘后补采）：
  · due_nodes   —— 准点窗口（trigger ~ +60s）内才触发
  · catchup_nodes —— 错过准点后、当天内的补采判定
  · _is_on_time —— 准点/补采的判定（决定界面是否标注）
  · 兼容旧快照（v3.0.1 及以前没有 on_time 字段）

★ 为什么必须有这个测试：补采一旦判错，就会出现"累计值冒充节点值"
  —— 这正是原注释里反复强调"绝不能做"的事。所以判定边界必须逐条钉死。
"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))

import opensnap  # noqa: E402

FAILS = []
N = 0


def chk(cond, name, detail=""):
    global N
    N += 1
    if cond:
        print("  PASS %s" % name)
    else:
        print("  FAIL %s   %s" % (name, detail))
        FAILS.append(name)


def dt(hm, ss=0):
    """'09:36' 或 '09:36:59' → datetime；秒缺省用 ss。"""
    parts = str(hm).split(":")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else ss
    return datetime(2026, 9, 23, h, m, s)


print("=" * 70)
print("v3.0.2 快照补采逻辑单元测试")
print("=" * 70)

# ---------------------------------------------------------------- _is_on_time
print("\n[1] _is_on_time（准点判定：trigger ~ trigger+60s）")
chk(opensnap._is_on_time("09:36:00", "09:36") is True, "恰在 trigger → 准点")
chk(opensnap._is_on_time("09:36:59", "09:36") is True, "trigger+59s → 准点")
chk(opensnap._is_on_time("09:37:00", "09:36") is True, "trigger+60s → 准点(边界含)")
chk(opensnap._is_on_time("09:37:01", "09:36") is False, "trigger+61s → 补采")
chk(opensnap._is_on_time("09:35:59", "09:36") is False, "早于 trigger → 补采")
chk(opensnap._is_on_time("14:59:00", "09:36") is False, "收盘前晚采 → 补采")
chk(opensnap._is_on_time("", "09:36") is False, "空 captured_at → 保守判补采")
chk(opensnap._is_on_time("bad", "09:36") is False, "格式异常 → 保守判补采")
chk(opensnap._is_on_time("09:36:00", "") is False, "无 trigger → 保守判补采")

# ---------------------------------------------------------------- due_nodes
print("\n[2] due_nodes（准点触发；空快照前提下）")
_DD = os.path.join(HERE, "_snap_test_dir")
os.makedirs(_DD, exist_ok=True)
for _f in os.listdir(_DD):
    os.remove(os.path.join(_DD, _f))
TAG = "20260923"

d = opensnap.due_nodes(TAG, now=dt("09:35:59"), data_dir=_DD)
chk(d == [], "09:35:59 未到 → 不触发", str(d))

d = opensnap.due_nodes(TAG, now=dt("09:36:00"), data_dir=_DD)
chk(len(d) == 1 and d[0][1] == "m5", "09:36:00 → 触发 m5", str(d))

d = opensnap.due_nodes(TAG, now=dt("09:37:00"), data_dir=_DD)
chk(len(d) == 1 and d[0][1] == "m5", "09:37:00 → 仍触发 m5(+60s边界)", str(d))

d = opensnap.due_nodes(TAG, now=dt("09:37:01"), data_dir=_DD)
chk(d == [], "09:37:01 过窗 → 准点不触发", str(d))

d = opensnap.due_nodes(TAG, now=dt("14:00:00"), data_dir=_DD)
chk(d == [], "14:00 过窗 → 准点不触发", str(d))

# ---------------------------------------------------------------- catchup_nodes
print("\n[3] catchup_nodes（补采；要求已过准点窗、且不早于 09:31）")
d = opensnap.catchup_nodes(TAG, now=dt("09:30:30"), data_dir=_DD)
chk(d == [], "09:30:30 早于 09:31 → 不补采", str(d))

d = opensnap.catchup_nodes(TAG, now=dt("09:36:30"), data_dir=_DD)
chk(d == [], "仍在 m5 准点窗内 → 不补采(交给 due_nodes)", str(d))

d = opensnap.catchup_nodes(TAG, now=dt("09:38:00"), data_dir=_DD)
chk([x[1] for x in d] == ["m5"], "09:38 → 只补 m5（m10/m15 未到）", str(d))

d = opensnap.catchup_nodes(TAG, now=dt("09:45:00"), data_dir=_DD)
chk([x[1] for x in d] == ["m5", "m10"], "09:45 → 补 m5+m10", str(d))

d = opensnap.catchup_nodes(TAG, now=dt("10:30:00"), data_dir=_DD)
chk([x[1] for x in d] == ["m5", "m10", "m15"], "10:30 → 补全部三个", str(d))

d = opensnap.catchup_nodes(TAG, now=dt("15:30:00"), data_dir=_DD)
chk([x[1] for x in d] == ["m5", "m10", "m15"],
    "15:30 收盘后 → 仍可补（用户明确要求；数据为收盘值，标注为补采）", str(d))

# ---------------------------------------------------------------- 已有数据不覆盖
print("\n[4] 已有数据的节点不被补采覆盖")
import json  # noqa: E402
with open(os.path.join(_DD, "opensnap_%s.json" % TAG), "w", encoding="utf-8") as f:
    json.dump({"nodes": {"m5": {"captured_at": "09:36:10", "on_time": True,
                                "planned": "09:36", "sectors": [{"c":"BK0001","n":"板块","v":1.0,"a":1.0,"net":1.0}], "stocks": [{"c":"000001","n":"个股","v":1.0,"a":1.0,"lb":1.0,"net":1.0,"pct":1.0}]}}},
              f, ensure_ascii=False)
d = opensnap.due_nodes(TAG, now=dt("09:36:30"), data_dir=_DD)
chk(d == [], "m5 已有数据 → 准点不重复采", str(d))
d = opensnap.catchup_nodes(TAG, now=dt("10:30:00"), data_dir=_DD)
chk([x[1] for x in d] == ["m10", "m15"], "m5 已有数据 → 补采跳过 m5", str(d))

# ---------------------------------------------------------------- build_payload
print("\n[5] build_payload 的 on_time / late / cap_hm（含旧快照兼容）")
p = opensnap.build_payload(TAG, data_dir=_DD)
n5 = p["nodes"]["m5"]
chk(n5["on_time"] is True and n5["late"] is False,
    "新快照带 on_time=True → 准点", str(n5.get("on_time")))
chk(p["nodes"]["m10"]["missing"] is True, "m10 无数据 → missing", "")

# 旧快照（无 on_time 字段）→ 按 captured_at 现算
with open(os.path.join(_DD, "opensnap_%s.json" % TAG), "w", encoding="utf-8") as f:
    json.dump({"nodes": {
        "m5": {"captured_at": "09:36:10", "sectors": [{"c":"BK0001","n":"板块","v":1.0,"a":1.0,"net":1.0}], "stocks": [{"c":"000001","n":"个股","v":1.0,"a":1.0,"lb":1.0,"net":1.0,"pct":1.0}]},   # 准点
        "m10": {"captured_at": "10:05:00", "sectors": [{"c":"BK0001","n":"板块","v":1.0,"a":1.0,"net":1.0}], "stocks": [{"c":"000001","n":"个股","v":1.0,"a":1.0,"lb":1.0,"net":1.0,"pct":1.0}]},  # 补采
    }}, f, ensure_ascii=False)
p = opensnap.build_payload(TAG, data_dir=_DD)
chk(p["nodes"]["m5"]["on_time"] is True,
    "旧快照 09:36:10 → 推断为准点", str(p["nodes"]["m5"]))
chk(p["nodes"]["m10"]["on_time"] is False and p["nodes"]["m10"]["late"] is True,
    "旧快照 10:05:00 → 推断为补采", str(p["nodes"]["m10"]))
chk(p["nodes"]["m10"]["cap_hm"] == "10:05",
    "cap_hm 供标题标注使用", str(p["nodes"]["m10"].get("cap_hm")))

# ---------------------------------------------------------------- 空数据保护（v3.0.4）
print("\n[6] 空节点保护（v3.0.4：限流导致采回空数据时必须如实标缺失）")
# 背景：实测限流窗口内 capture_node 会返回"结构完整但 sectors/stocks 为空"
# 的节点。若按原逻辑（只判 node 是否存在）会被当成"已采集"，
# 界面渲染出一片空白 —— 比"未采集"提示更让人困惑。
# 契约：内容为空 ⇒ missing=True 且 reason="empty"。
with open(os.path.join(_DD, "opensnap_%s.json" % TAG), "w", encoding="utf-8") as f:
    json.dump({"nodes": {
        "m5": {"captured_at": "09:36:10", "on_time": True, "planned": "09:36",
               "sectors": [], "stocks": []},                       # 全空
        "m10": {"captured_at": "09:41:10", "on_time": True, "planned": "09:41",
                "sectors": [{"c": "BK0001", "n": "板块", "v": 1.0, "a": 1.0,
                             "net": 1.0}],
                "stocks": []},                                     # 只有板块
    }}, f, ensure_ascii=False)
p = opensnap.build_payload(TAG, data_dir=_DD)
_e5, _e10 = p["nodes"]["m5"], p["nodes"]["m10"]
chk(_e5["missing"] is True and _e5.get("reason") == "empty",
    "全空节点 → missing/empty（不渲染空白）", str(_e5.get("reason")))
chk(_e5.get("hint"),
    "空节点带 hint 说明（多为数据源限流，可重试）", str(_e5.get("hint"))[:24])
chk(_e5.get("captured_at") == "09:36:10",
    "空节点仍保留 captured_at（便于排查何时采的）", str(_e5.get("captured_at")))
chk(_e10["missing"] is False and _e10.get("on_time") is True,
    "只有板块/无个股 → 仍按有效节点处理（非全空）", str(_e10.get("missing")))

# ---------------------------------------------------------------- 补采状态机（v3.0.4）
print("\n[7] 补采状态机（v3.0.4：多节点残留 —— 用户现场症状的回归测试）")
#
# 背景（用户原话）：「开盘检测、个股异动等板块**仍**提示该节点未采集」。
# 现场日志证明不是"全空"，而是**部分节点长期残留**：m5 采到了，m10/m15 补不到。
#
# 根因是 _snap_tick / _on_snap_done 的状态机写错了：
#   · catchup_nodes 能正确返回全部待补节点 [(m5),(m10),(m15)]；
#   · 但 _snap_tick 每轮只取 ck[0] 补一个；
#   · 且 _on_snap_done 在第一次补采成功后就置位 _catchup_done=True
#     → 后两个节点**永远补不到**。
#
# 这里不依赖 Qt，用纯逻辑复刻状态机，逐轮推进，断言"最终三个节点都能补到"。
# ★ 这正是原测试缺的一环：过去只测了单节点判定，没测**跨轮次的状态机收敛**。


class _MiniScheduler:
    """复刻 _snap_tick 的补采状态机（去掉 Qt / 网络，只保留判定与置位时机）。

    与 app_main 的对应关系：
      due 分支  ←→ opensnap.due_nodes
      ck  分支  ←→ opensnap.catchup_nodes（取 ck[0]）
      _catchup_done 置位 ←→ v3.0.4 新逻辑（只有全齐才置位）
    """

    def __init__(self, tag, data_dir):
        self.tag = tag
        self.dd = data_dir
        self.catchup_done = False
        self.captured = []            # 记录补采顺序

    def tick(self, now):
        """跑一轮 tick。返回本轮动作描述（'due:x' / 'catchup:x' / 'idle'）。"""
        due = opensnap.due_nodes(self.tag, now=now, data_dir=self.dd)
        if due:
            _m, k, _t = due[0]
            self._capture(k)
            return "due:%s" % k
        if not self.catchup_done:
            ck = opensnap.catchup_nodes(self.tag, now=now, data_dir=self.dd)
            if ck:
                _m, k, _t = ck[0]
                self._capture(k)
                return "catchup:%s" % k
            # v3.0.4：只有"数据层面确实全齐"才停止补采
            have = opensnap.load_snap(self.tag, data_dir=self.dd).get("nodes") or {}
            if all(k in have for _m, _t, _l, k in opensnap.SNAP_NODES):
                self.catchup_done = True
        return "idle"

    def _capture(self, key):
        """模拟一次成功采集（写入带最小可用数据的节点）。"""
        snap = opensnap.load_snap(self.tag, data_dir=self.dd)
        snap["nodes"][key] = {
            "minutes": {"m5": 5, "m10": 10, "m15": 15}[key],
            "captured_at": "10:30:00", "on_time": False, "planned": "",
            "sectors": [{"c": "BK0001", "n": "板块", "v": 1.0, "a": 1.0, "net": 1.0}],
            "stocks": [{"c": "000001", "n": "个股", "v": 1.0, "a": 1.0, "lb": 1.0,
                        "net": 1.0, "pct": 1.0}],
        }
        opensnap.save_snap(self.tag, snap["nodes"], data_dir=self.dd)
        self.captured.append(key)


_MD = os.path.join(HERE, "_snap_fsm_dir")
os.makedirs(_MD, exist_ok=True)
for _f in os.listdir(_MD):
    os.remove(os.path.join(_MD, _f))

# 场景：程序 10:30 才启动，当天三个节点全未采（最坏情况）
sch = _MiniScheduler(TAG, _MD)
seq = []
for _i in range(6):                       # 模拟 6 轮 tick（每轮 15s → 90s 窗口）
    seq.append(sch.tick(dt("10:30") if _i == 0 else dt("10:31")))
    if sch.catchup_done:
        break

chk(sch.captured == ["m5", "m10", "m15"],
    "三节点全缺时逐轮补完 m5→m10→m15", "实际顺序=%s" % sch.captured)
chk(len(sch.captured) == 3,
    "补采总数 = 3（修复前只会补 1 个就停）", "实际=%d 轮序=%s" % (len(sch.captured), seq))
chk(sch.catchup_done is True,
    "三节点齐后状态机才收敛（catchup_done=True）", str(sch.catchup_done))

_p = opensnap.build_payload(TAG, data_dir=_MD)
_missing = [k for k, nd in _p["nodes"].items() if nd.get("missing")]
chk(_missing == [],
    "★ core 断言：build_payload 三节点全部 missing=False（界面无「该节点未采集」）",
    "仍缺失=%s" % _missing)

# 反向场景：只补到 1 个时，绝不允许提前收敛（旧 bug 的核心）
for _f in os.listdir(_MD):
    os.remove(os.path.join(_MD, _f))
sch2 = _MiniScheduler(TAG, _MD)
sch2._capture("m5")                        # 手动只补一个
sch2.tick(dt("10:31"))
chk(sch2.catchup_done is False,
    "只补到 1/3 时状态机**不得**收敛（旧 bug 正是在这里提前置位）",
    "catchup_done=%s captured=%s" % (sch2.catchup_done, sch2.captured))

for _f in os.listdir(_MD):
    os.remove(os.path.join(_MD, _f))
os.rmdir(_MD)

print("\n" + "=" * 70)
print("结果：%d PASS / %d FAIL" % (N - len(FAILS), len(FAILS)))
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  · %s" % f)
print("=" * 70)
sys.exit(1 if FAILS else 0)
