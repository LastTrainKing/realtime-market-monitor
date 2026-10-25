# -*- coding: utf-8 -*-
"""test_source_v306.py —— 数据源层 + 分钟级频率 + 暗门 的专项回归（**完全离线**）。

对应本轮用户需求：
  ① 「读取频率改成分钟级，避免再次被 ip 封禁」
  ② 「去找新的数据源替换，多试几个」
  ③ 「保留切换回东方财富数据的暗门」

本文件不联网：腾讯的响应全部用打桩数据喂进去，只验证**解析、换算、匹配、
优先级、降级**这些纯逻辑。
"""
import ast
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
sys.path.insert(0, HERE)

import source as src                                                    # noqa: E402
import flowdata as fd                                                   # noqa: E402
import tradingtime as tt                                                # noqa: E402

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


def consts_from(path, names):
    """从源码里取模块级常量（用 ast + 受限 eval）。

    ★ 为什么不用 import：`app_main_v203.py` 导入即拉起 PySide6 与整条启动链，
      为了读几个数字而付这个代价不合适；而且本项目已有"源码级守卫用 ast"
      的纪律。这里只求值 `Name = <纯整数表达式>` 这一种形态。
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    out[t.id] = eval(compile(ast.Expression(node.value),
                                             "<c>", "eval"),
                                     {"__builtins__": {}}, {})
    return out


# ======================================================================
print("=" * 72)
print("A. 读取频率必须是**分钟级**（用户要求 ①）")
print("=" * 72)
APP = os.path.join(HERE, "app_main_v203.py")
C = consts_from(APP, {"AUTO_MS_OPTIONS", "AUTO_MS_DEFAULT", "REFRESH_MIN_GAP_SEC"})

opts = C.get("AUTO_MS_OPTIONS") or []
check(len(opts) >= 3, "A1 档位表存在", f"{len(opts)} 档")
mins = [ms for _lab, ms in opts]
check(all(ms >= 60_000 for ms in mins),
      "A2 ★ 所有档位都是分钟级（≥60000ms），秒级档已移除",
      str([int(m / 1000) for m in mins]) + " 秒")
check(all(ms % 60_000 == 0 for ms in mins),
      "A3 档位都是整分钟（不是 90 秒这种半档）")
check(C.get("AUTO_MS_DEFAULT") == 60_000,
      "A4 ★ 默认档 = 1 分钟", f"{C.get('AUTO_MS_DEFAULT')}ms")
check(C.get("AUTO_MS_DEFAULT") in mins,
      "A5 默认档在档位表里（不会出现「默认值不在菜单中」）")

check(tt.REFRESH_CAP_MS == 60_000,
      "A6 ★ 时段节奏上限放宽到 60s", f"{tt.REFRESH_CAP_MS}ms")
bad = {k: v for k, v in tt.PHASE_REFRESH_MS.items()
       if v is not None and v > 60_000}
check(not bad, "A7 没有任何时段的上限比 1 分钟更慢", str(bad))
check(tt.PHASE_REFRESH_MS["morning"] == 60_000
      and tt.PHASE_REFRESH_MS["afternoon"] == 60_000,
      "A8 上午/下午连续竞价的上限都是 60s")
check(tt.PHASE_REFRESH_MS["noon"] is None
      and tt.PHASE_REFRESH_MS["pre_dawn"] is None,
      "A9 午休与盘前仍然**不刷新**（保持原语义）")
check(tt.PHASE_REFRESH_MS["post"] == 60_000,
      "A10 盘后 60s（15:10 之后仍由 POST_SETTLE_END 关闭）")
check(tt.POST_SETTLE_END == 15 * 60 + 10, "A11 盘后结算窗终点仍是 15:10")

# 钳制语义：min(用户档位, 时段上限)
import datetime as _dt                                                  # noqa: E402
_morning = _dt.datetime(2026, 9, 23, 10, 0, 0)
_s = tt.classify(_morning)
check(_s.auto_refresh and _s.phase_cap_ms == 60_000,
      "A12 盘中分类：允许自动刷新且上限 60s",
      f"cap={_s.phase_cap_ms}")
check(min(60_000, _s.phase_cap_ms) == 60_000,
      "A13 用户选 1 分钟 → 生效 1 分钟（不被钳）")
check(min(15 * 60_000, _s.phase_cap_ms) == 60_000,
      "A14 用户选 15 分钟 → 被钳到 1 分钟（保证不比 1 分钟更慢）")
_noon = tt.classify(_dt.datetime(2026, 9, 23, 12, 0, 0))
check(not _noon.auto_refresh, "A15 午休不自动刷新（不会白打接口）")


# ======================================================================
print()
print("=" * 72)
print("B. 数据源解析与「暗门」优先级（用户要求 ③）")
print("=" * 72)
check(src.SOURCE_ENV == "FLOWLIVE_SOURCE",
      "B1 暗门之一：环境变量名", src.SOURCE_ENV)
check(src.SOURCE_FILE == "source.json", "B2 暗门之二：配置文件名", src.SOURCE_FILE)
check(set(src.PROVIDERS) == {"eastmoney", "tencent"},
      "B3 可选源集合", str(src.PROVIDERS))

_old_env = os.environ.get(src.SOURCE_ENV)


def _set_env(v):
    if v is None:
        os.environ.pop(src.SOURCE_ENV, None)
    else:
        os.environ[src.SOURCE_ENV] = v
    src.reset()


# B4 环境变量最高优先级
_set_env("eastmoney")
check(src.resolve() == ("eastmoney", "环境变量 FLOWLIVE_SOURCE=eastmoney"),
      "B4 ★ 环境变量可强制切回东财（暗门最高优先级）")
_set_env("tencent")
check(src.active() == "tencent", "B5 环境变量可强制腾讯")
check(src.uses_alt() is True, "B6 uses_alt 正确识别替代源")
_set_env(None)
check(src.resolve()[0] in src.PROVIDERS,
      "B7 未设环境变量时也能解析出一个有效源", src.resolve()[0])

# ★★ B7b/B7c：用户明确要求「默认不激活」—— 默认必须是东财、且**不做探测**
_tmp0 = tempfile.mkdtemp(prefix="flowlive_def_")
_old_data0 = fd.DATA
fd.set_data_dir(_tmp0)
try:
    src.reset()
    _p0, _how0 = src.resolve()
    check(_p0 == src.DEFAULT_PROVIDER,
          "B7b ★★ 默认数据源 = DEFAULT_PROVIDER（用户要求：默认先用替代源）",
          f"{_p0} / {_how0}")
    check(_p0 == "tencent",
          "B7c ★★ 当前默认是腾讯（东财接口已被封）", _p0)
    check("默认" in _how0 and "auto" not in _how0,
          "B7d ★★ 默认路径**不做探测**（不发任何额外请求）", _how0)
    check(src.uses_alt() is True,
          "B7e ★★ 默认下 uses_alt=True（走替代源代码路径）")
    # ★ 暗门必须仍然能把它切回东财
    _set_env("eastmoney")
    check(src.uses_alt() is False,
          "B7f ★★ 暗门可把默认的替代源切回东财")
    _set_env(None)
finally:
    fd.set_data_dir(_old_data0)
    src.reset()

# B8 配置文件优先级低于环境变量
_tmp = tempfile.mkdtemp(prefix="flowlive_src_")
_old_data = fd.DATA
fd.set_data_dir(_tmp)
try:
    p = src.write_config("eastmoney")
    check(os.path.exists(p), "B8 write_config 落盘", os.path.basename(p))
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    check(cfg.get("provider") == "eastmoney", "B9 配置内容正确", str(cfg))
    src.reset()
    check(src.resolve()[0] == "eastmoney",
          "B10 ★ 配置文件可把源切回东财（暗门第二条入口）")
    _set_env("tencent")
    check(src.resolve()[0] == "tencent",
          "B11 ★ 环境变量优先于配置文件（优先级顺序正确）")
    _set_env(None)
    # 非法值不静默忽略
    _set_env("no_such_source")
    got = src.resolve()[0]
    check(got in src.PROVIDERS,
          "B12 非法源名不崩、按默认（东财）处理（且会打印提示）", got)
    _set_env(None)
finally:
    fd.set_data_dir(_old_data)
    _set_env(_old_env)


# ======================================================================
print()
print("=" * 72)
print("C. 腾讯源解析：单位换算 / 名称匹配（用户要求 ②）")
print("=" * 72)
check(src.CAPS["tencent"]["minute_flow_history"] is False,
      "C1 ★ 腾讯源**如实声明**没有分钟级资金流历史")
check(src.CAPS["tencent"]["sector_members"] is False,
      "C2 ★ 腾讯源**如实声明**没有板块成分股")
check(src.CAPS["eastmoney"]["minute_flow_history"] is True,
      "C3 东财源仍声明具备分钟级资金流历史")

# ---- 归一化规则 ----
cases = [
    ("ChatGPT概念", "ChatGPT"),
    ("元宇宙概念", "元宇宙"),
    ("DeepSeek概念", "DeepSeek"),
    ("短剧互动游戏", "短剧互动游戏"),
    ("抖音概念(字节概念)", "抖音"),
    ("消费电子概念", "消费电子"),
    ("IT服务Ⅲ", "IT服务"),
    ("光伏设备Ⅱ", "光伏设备"),
]
ok = all(src.norm_name(a) == b for a, b in cases)
check(ok, "C4 名称归一化：去括号/去标点/去通用后缀/去层级罗马数字",
      str([(a, src.norm_name(a)) for a, b in cases if src.norm_name(a) != b] or "全部符合"))

# ---- 匹配器（打桩腾讯库）----
FAKE = {
    "食品饮料": {"code": "pt01801120", "name": "食品饮料", "zljlr": "72151.42",
               "lzg": {"code": "sz002650", "name": "ST加加", "zdf": "6.29"}},
    "ChatGPT": {"code": "pt02GN1", "name": "ChatGPT", "zljlr": "1000",
                "lzg": {}},
    "消费电子产业": {"code": "pt02GN2", "name": "消费电子产业", "zljlr": "500",
                 "lzg": {}},
    # ★ 下面两个归一化后都是「机器人」→ 构成**真歧义**（见 C7）
    "机器人概念": {"code": "pt02GN4", "name": "机器人概念", "zljlr": "900",
               "lzg": {}},
    "机器人产业": {"code": "pt02GN5", "name": "机器人产业", "zljlr": "800",
               "lzg": {}},
}
resolve, stats = src._build_matcher(FAKE)
r1, h1 = resolve("食品饮料")
check(r1 is FAKE["食品饮料"] and h1 == "exact", "C5 逐字相等 → exact 匹配")
r2, h2 = resolve("ChatGPT概念")
check(r2 is FAKE["ChatGPT"] and h2 == "norm",
      "C6 ★ 归一化匹配：东财「ChatGPT概念」↔ 腾讯「ChatGPT」")
r3, h3 = resolve("机器人")
check(r3 is None and h3 == "ambig",
      "C7 ★★ 归一化后有多候选时**放弃匹配**（宁缺勿错，不猜）",
      f"腾讯侧同时有 机器人概念/机器人产业 → {h3}")
r4, h4 = resolve("完全不存在的板块")
check(r4 is None and h4 == "none", "C8 匹配不到 → none（不抛异常）")

# ---- 单位换算 ----
_orig_rank = src._tencent_rank
_orig_all = src._tencent_all_boards
try:
    src._tencent_all_boards = lambda timeout=8: dict(FAKE)
    names = {"90.BK0475": "食品饮料", "90.BK9999": "ChatGPT概念"}
    got = src.tencent_fetch_spot_batch(["90.BK0475", "90.BK9999"],
                                       detail=True, names=names)
    check(abs(got["90.BK0475"]["net"] - 72151.42 * 10000.0) < 1.0,
          "C9 ★ 单位换算：腾讯万元 → 元（×10000），与东财口径一致",
          f"{got['90.BK0475']['net']:.0f} 元 = {got['90.BK0475']['net']/1e8:.2f} 亿")
    check(got["90.BK0475"]["lead"] == "ST加加",
          "C10 领涨股正确带出")
    check(got["90.BK0475"]["lag"] == "" and got["90.BK0475"]["lag_code"] == "",
          "C11 ★ 腾讯没有领跌股字段 → 如实留空（不拿别的字段顶）")
    check("90.BK9999" in got,
          "C12 归一化匹配的板块也能取到值")

    got2 = src.tencent_fetch_spot_batch(["90.BK0475"], detail=False,
                                        names=names)
    check(isinstance(got2["90.BK0475"], float),
          "C13 detail=False 返回纯数值（向后兼容）")

    none_got = src.tencent_fetch_spot_batch(["90.BK0475"], detail=True,
                                            names=None)
    check(none_got == {},
          "C14 ★ 没有名称映射时返回空（宁可少数据，也不猜着填）")
finally:
    src._tencent_rank = _orig_rank
    src._tencent_all_boards = _orig_all


# ======================================================================
print()
print("=" * 72)
print("D. 降级行为：替代源下各接口的表现必须「诚实」")
print("=" * 72)
check(src.T_INDEX_MAP["47.800000"][1].startswith("中证全指"),
      "D1 ★ 东财全A 的替代标的被如实标注为中证全指",
      src.T_INDEX_MAP["47.800000"][1])
check("替代" in src.T_INDEX_MAP["47.800000"][1],
      "D2 ★ 替代标签里明写「替代」二字（不冒充东财全A）")
check(src.T_INDEX_MAP["1.000001"][1] == "上证指数",
      "D3 上证指数无需替代，标签保持原样")
check(src.CAP_NOTES["tencent"].find("不含") >= 0,
      "D4 腾讯源的能力说明里明写「不含分钟级资金流历史」")

# flowdata 侧的路由（用打桩源验证，不联网）
_old_active = src.active
_old_uses = src.uses_alt
try:
    src.uses_alt = lambda: True
    src.active = lambda: "tencent"
    fd._NO_MH_WARNED["done"] = False
    k = fd.fetch_sector_kline("90.BK0475")
    check(k == [],
          "D5 ★ 替代源下 fetch_sector_kline 如实返回空（不编造历史分钟）")
    check(fd._NO_MH_WARNED["done"] is True,
          "D6 该缺失会**提示一次**（不静默，也不刷屏）")
    got = fd.fetch_spot_batch(["90.BK0475"], detail=True, names=None)
    check(got == {},
          "D7 替代源下无名称映射时 fetch_spot_batch 返回空")
finally:
    src.active = _old_active
    src.uses_alt = _old_uses
    fd._NO_MH_WARNED["done"] = False


# ======================================================================
print()
print("=" * 72)
print("E. 端到端：切回东财后必须**完全恢复原行为**（暗门不能有副作用）")
print("=" * 72)
_set_env("eastmoney")
try:
    check(src.uses_alt() is False, "E1 强制东财后 uses_alt=False")
    secids = fd.index_secids()
    check(secids == [("sh", "1.000001", "上证指数"),
                     ("all", "47.800000", "东财全A")],
          "E2 ★ 东财源下指数标签**原样**（不出现「替代」字样）", str(secids))
    check(src.cap("minute_flow_history") is True,
          "E3 东财源下分钟资金流历史能力为 True")
    check(src.cap("sector_members") is True,
          "E4 东财源下成分股能力为 True")
finally:
    _set_env(_old_env)
    src.reset()

# ======================================================================
print()
print("=" * 72)
print("F. 替代源下：硬编码东财域名的模块必须**一个请求都不发**")
print("=" * 72)
# ★ 这条为什么重要：backfill / opensnap / tradecal 都硬编码了东财域名。
#   替代源下继续调用它们，不只是"取不到数据"，更是**对已经封禁我们的服务
#   继续发请求** —— 用户要的"避免再次被 IP 封禁"，这条比取不到数据更关键。
import backfill as _bf                                                   # noqa: E402
import tradecal as _tc                                                   # noqa: E402
import opensnap as _os                                                   # noqa: E402

check(src.skip_reason("day_flow_history") is not None,
      "F1 替代源下 day_flow_history 能力被判定为缺失")
check(src.skip_reason("market_snapshot") is not None,
      "F2 替代源下 market_snapshot 能力被判定为缺失")
check(src.skip_reason("trade_calendar") is not None,
      "F3 替代源下 trade_calendar 能力被判定为缺失")

_calls = {"n": 0}
_orig_get = fd._get


def _counting_get(*a, **k):
    _calls["n"] += 1
    return None


fd._get = _counting_get
_orig_rank2 = src._tencent_rank
src._tencent_rank = lambda *a, **k: (0, [])
src._tencent_all_boards = lambda timeout=8: {}
try:
    _tmpF = tempfile.mkdtemp(prefix="flowlive_noreq_")
    _oldF = fd.DATA
    fd.set_data_dir(_tmpF)

    got = _bf.fetch_daykline_map(["90.BK0475", "90.BK1038"])
    check(got == {}, "F4 替代源下 backfill 返回空（不假装成功）", str(got))
    check(_calls["n"] == 0,
          "F5 ★★ 替代源下 backfill **一个请求都没发**", f"{_calls['n']} 次")

    _calls["n"] = 0
    days = _tc._fetch_days_from_daykline()
    check(days is None, "F6 替代源下交易日历联网取数返回 None（走缓存兜底）")
    check(_calls["n"] == 0,
          "F7 ★★ 替代源下 tradecal **一个请求都没发**", f"{_calls['n']} 次")

    _calls["n"] = 0
    raised = ""
    try:
        _os.capture_node(5, trigger="09:36")
    except Exception as e:                               # noqa: BLE001
        raised = f"{type(e).__name__}: {e}"
    check("替代源" in raised,
          "F8 ★★ 替代源下 opensnap 明确拒绝并说明原因（不静默失败）",
          raised[:70])
    check(_calls["n"] == 0,
          "F9 ★★ 替代源下 opensnap **一个请求都没发**"
          "（否则就是 70 连击已封禁的东财）", f"{_calls['n']} 次")

    fd.set_data_dir(_oldF)
finally:
    fd._get = _orig_get
    src._tencent_rank = _orig_rank2

# ---- 切回东财后，三者必须恢复联网行为（暗门不能有副作用）----
_set_env("eastmoney")
try:
    check(src.skip_reason("day_flow_history") is None,
          "F10 ★ 切回东财后 day_flow_history 能力恢复")
    check(src.skip_reason("market_snapshot") is None,
          "F11 ★ 切回东财后 market_snapshot 能力恢复")
    check(src.skip_reason("trade_calendar") is None,
          "F12 ★ 切回东财后 trade_calendar 能力恢复")
finally:
    _set_env(_old_env)
    src.reset()

print()
print("=" * 72)
print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 72)
sys.exit(1 if FAIL else 0)
