# -*- coding: utf-8 -*-
"""tradingtime.py —— A 股交易时段模型（v3.0.4 新增）

★ 本模块是**时间处理的唯一权威来源**。任何"现在是什么时段""该不该刷新"
  的判断都必须走这里，不得再各写一份。

===========================================================================
一、改造前的现状：时段判断散落在 4 处，口径互不一致，且全都没有集合竞价
===========================================================================

  | 位置 | 判据 | 缺陷 |
  |---|---|---|
  | `flowdata.clamp_to_bar()` | 盘前/上午/午休/下午/盘后 | ★ **无集合竞价**（09:15~09:30 一律当"盘前"拒绝写入） |
  | `app_main._auto_tick()` | `weekday>=5` + `925<=hm<=1505` | ★★ **只排周末，不排法定节假日** |
  | `app_main._snap_tick()` | `weekday>=5` + tradecal 查表 | 与上一条口径不同（一个有节假日过滤、一个没有） |
  | `opensnap.due_nodes/catchup_nodes()` | 节点窗口 | 自己解析 HH:MM，与上面三处各成一套 |

  最严重的是第二处。实测场景：**国庆 10-01 是周四**，`weekday()=3 < 5`
  且 `10:00` 落在 `925~1505` 内 → **整个假期每 3 秒打一次接口**，
  每次都拿回"上一个交易日"的同一个值，纯烧流量与 CPU，还可能触发限流。
  而同一份程序里的快照线程（`_snap_tick`）却能正确识别节假日 ——
  两个线程对"今天开不开市"给出**相反答案**，这是散落实现的必然结果。

===========================================================================
二、两级时段模型
===========================================================================

  **一级（用户可见，4 段）**：盘前 / 集合竞价 / 盘中 / 盘后
  **二级（内部处理，8 子相）**：见 PHASES

  为什么要两级：
    · 一级只有 4 段，做不了"午休不该写面板行""尾盘集合竞价单独处理"
      "开盘前静默期"这些区分；
    · 二级单独暴露又太细，用户不需要知道"09:25~09:30 叫开盘前静默"。

  A 股真实时段（本模型以此为准，全部用**分钟数**表示，区间左闭右开）：

     子相            起止              一级时段    可连续交易   数据特征
     ─────────────────────────────────────────────────────────────────
     pre_dawn        00:00–09:15       盘前        否          上一交易日收盘值
     auction_open    09:15–09:25       集合竞价    否          竞价撮合，09:25 出开盘价
     pre_open        09:25–09:30       集合竞价    否          开盘价已定，静默期
     morning         09:30–11:30       盘中        是          上午连续竞价
     noon            11:30–13:00       盘中        否          午间休市
     afternoon       13:00–14:57       盘中        是          下午连续竞价
     close_auction   14:57–15:00       盘中        是          尾盘集合竞价（收盘价来源）
     post            15:00–24:00       盘后        否          收盘值已定

  ★ 关于"尾盘集合竞价"归入**盘中**而非"集后竞价"这个一级时段：
    它发生在盘中交易时间之内（沪深 14:57–15:00），且深市该阶段可撤单、
    会产生连续成交 —— 它就是"盘中的最后一段"，不是独立时段。
    用户要的"集合竞价"指**开盘集合竞价**（09:15–09:25），语义清晰。

===========================================================================
三、交易日判定的四层证据链（这是本模块最讲究的部分）
===========================================================================

  判"今天开不开市"看似简单，实际有个真实难点：**长假期间，本地日历
  的最后一天是假期前那个交易日**，而"今天"比它晚好几天 —— 此时无法
  仅凭"今天不在日历里"断定它是节假日（因为日历可能只是**还没更新**）。

  所以采用四层证据，从强到弱：

    L1 周末        —— `weekday()>=5`。硬规则，零依赖，100% 可靠。
                       ★ 唯一一条不需要任何数据就能定死的规则。
    L2 观测自校正  —— 运行期抓到过"实际数据日期"，若 ≠ 某天，
                       则那天**确证**为非交易日。这是**最强证据**：
                       不是推断，是亲眼看到接口给了别的日期的数据。
    L3 日历命中    —— tradecal 给出的日期列表就是真实交易所日历
                       （源自 fflow/daykline，天然排除周末与节假日）。
    L4 日历区间内  —— 落在日历的 [最早, 最新] 区间内、但不在列表中
                       → **确证**为节假日（日历覆盖了那段时间，却没有那天）。
    L5 日历区间外  —— 日期晚于日历最新一天。★ 这一层是本模块的实测修正：
                       实测（2026-09-23 盘中）确认 **fflow/daykline 返回的是
                       "已完成交易日的日线"，盘中永远不含今天** ——
                       联网取回后最后一天仍是 09-22。所以"今天不在日历里"
                       在盘中是**结构性常态**，不是异常信号。
                       若日历是新鲜的（今天联网取过 / 今天写过盘），
                       判为交易日，标 `calendar_live`（可靠）。
    L6 工作日推断  —— 兜底：周一~周五视为交易日，标 `assumed`（**不确定**）。
                       ★ 长假第一天会被这层或 L5 放行一次（L5 无法区分
                       "长假第 1 天"与"普通交易日"—— 两种情况下
                       日历最后一天距今天都只差 1 天）。因此必须配合
                       L2 自校正：第一次抓取就会暴露真实数据日期。
                       代价仅"多抓一次"，而那次抓取本来就要做。

  为什么 L6 不做得更"聪明"（例如"距日历最后一天 ≥4 天就判假期"）：
    这类启发式在**跨周末**时必然出错 —— 周五取日历、周一再看就是差 3 天，
    而周一是正常交易日。用一个会出错的规则去省一次请求，不划算。
    宁可"先放行一次、抓到数据后立刻自校正"，也不引入偶发误判。

===========================================================================
四、刷新节奏：把"至少每分钟更新一次"做成硬保证（v3.0.6 起）
===========================================================================

  ★★ v3.0.6 变更（用户要求"读取频率改成分钟级，避免再次被 IP 封禁"）：
    上一版的保证是"盘中每 10 秒内至少更新一次"；本版放宽为
    **"盘中每分钟内至少更新一次"**。原因：2026-09-23 本机 IP 被东财
    push2 集群封禁，复盘结论是"单位时间请求总量"踩线；而**数据源本身
    就是分钟粒度** —— 同一交易分钟内拿回的是完全相同的累计值，
    所以 10 秒档与 60 秒档拿到的数据一模一样，只是"跨分钟后多久去取"不同。
    用最多 60 秒的等待换"绝不因为刷新本身触发限流"，是划算的。

  本模块的做法是给出**每个时段的节奏上限**（`PHASE_REFRESH_MS`），
  由上层取 `min(用户选的档位, 时段上限)`：

    · 用户选 1/2/3/5 分钟 → 实际就是所选值（不慢于 1 分钟）
    · 用户选 10/15 分钟   → **被钳到 1 分钟**（这才是"保证"二字的含义：
                            哪怕用户设了慢档，盘中也不会慢过 1 分钟）

  ★ 钳制必须**留痕**（见 `Session.clamped` / 上层日志）：
    本项目已有"设置被静默否决"的历史坑（1 秒档被 REFRESH_MIN_GAP 挡掉），
    静默钳制会重蹈覆辙 —— 用户会以为程序坏了。所以本模块把
    "原始档位 / 生效档位 / 是否被钳"三个值都暴露出去，由 UI 决定怎么提示。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

# ============================================================ 时段定义（唯一权威）

_MIN = 60


def _hm(t: int) -> str:
    """分钟数 -> 'HH:MM'。"""
    return f"{t // _MIN:02d}:{t % _MIN:02d}"


@dataclass(frozen=True)
class Phase:
    """一个交易子相。区间为 [t0, t1)，单位是"当天第几分钟"。"""
    key: str
    label: str            # 子相中文名（日志/状态栏用）
    major: str            # 所属一级时段：盘前 / 集合竞价 / 盘中 / 盘后
    t0: int               # 起始分钟（含）
    t1: int               # 结束分钟（不含）
    tradable: bool        # 是否允许连续竞价交易
    note: str             # 数据特征说明

    @property
    def span(self) -> str:
        return f"{_hm(self.t0)}–{_hm(self.t1)}"

    def contains(self, mod: int) -> bool:
        return self.t0 <= mod < self.t1


# ★ 顺序即时间顺序。相邻相首尾相接、无缝隙、无重叠（由校验函数保证）。
PHASES: tuple[Phase, ...] = (
    Phase("pre_dawn", "盘前", "盘前", 0, 9 * _MIN + 15,
          False, "上一交易日收盘值"),
    Phase("auction_open", "开盘集合竞价", "集合竞价", 9 * _MIN + 15, 9 * _MIN + 25,
          False, "竞价撮合中，09:25 产生开盘价"),
    Phase("pre_open", "开盘前静默", "集合竞价", 9 * _MIN + 25, 9 * _MIN + 30,
          False, "开盘价已定，等待连续竞价"),
    Phase("morning", "上午连续竞价", "盘中", 9 * _MIN + 30, 11 * _MIN + 30,
          True, "上午连续交易"),
    Phase("noon", "午间休市", "盘中", 11 * _MIN + 30, 13 * _MIN,
          False, "午休，数据冻结在 11:30"),
    Phase("afternoon", "下午连续竞价", "盘中", 13 * _MIN, 14 * _MIN + 57,
          True, "下午连续交易"),
    Phase("close_auction", "尾盘集合竞价", "盘中", 14 * _MIN + 57, 15 * _MIN,
          True, "收盘价来源（深市可撤单）"),
    Phase("post", "盘后", "盘后", 15 * _MIN, 24 * _MIN,
          False, "收盘值已定；15:00–15:10 为结算窗"),
)

PHASE_BY_KEY = {p.key: p for p in PHASES}

# 用户可见的一级时段（严格 4 个，顺序即时间顺序）
MAJORS: tuple[str, ...] = ("盘前", "集合竞价", "盘中", "盘后")
MAJOR_CLOSED = "休市"          # 非交易日的"一级时段"

# ★ 各时段的自动刷新间隔上限（毫秒）；None = 该时段不自动刷新。
#   取值理由：
#     · 连续竞价/集合竞价 = 10 秒 —— 用户明确要求"不超过每 10 秒一次"；
#       且数据源本身是分钟粒度，配合跨分钟守卫生效后每分钟只真实抓 1 次，
#       10 秒档的请求压力与 30 秒档**完全相同**（见 app_main._auto_tick）。
#     · 盘前 = None —— 数据不会变（拿到的是上一交易日收盘值），刷新纯属白费。
#     · 午休 = None —— 数据冻结在 11:30，同上。
#     · 盘后 = 60 秒，但只在 15:00–15:10 的结算窗内（此后 None）。
#       15:00 收盘后东财的资金流数据有一个最终结算过程，值得补两次；
#       10 分钟之后就完全定稿了，再刷没有意义。
PHASE_REFRESH_MS: dict[str, int | None] = {
    "pre_dawn": None,
    "auction_open": 60_000,
    "pre_open": 60_000,
    "morning": 60_000,
    "noon": None,
    "afternoon": 60_000,
    "close_auction": 60_000,
    "post": 60_000,
}

#: 盘中"至少每分钟更新一次"这条要求的**节奏上限**。
#  ★★ v3.0.6：从 10 秒放宽到 **60 秒**（用户要求"读取频率改成分钟级，
#     避免再次被 IP 封禁"）。背景：2026-09-23 本机 IP 被东财 push2 集群封禁，
#     复盘结论是"单位时间请求总量"踩线。
#     ★ 这里放宽**不损失数据新鲜度** —— 数据源本身就是分钟粒度，
#       同一交易分钟内拿回的是完全相同的累计值（见 _auto_tick 的跨分钟守卫）。
#       所以 10 秒档与 60 秒档拿到的数据**一模一样**，差别只是"跨分钟后多久去取"。
#     ★ 上限的语义不变：`min(用户选的档位, 本上限)` ——
#       用户选 1 分钟 → 1 分钟；选 15 分钟 → **被钳到 1 分钟**（保证不比 1 分钟更慢）。
REFRESH_CAP_MS = 60_000

#: 盘后结算窗终点（15:10）—— 此后 post 相不再自动刷新。
POST_SETTLE_END = 15 * _MIN + 10

#: 数据日期比"今天"旧多少天以内，仍认为抓取正常（容忍接口延迟与跨日）。
#  取 1：只要不是明显陈旧（如假期里的"上一个交易日"）就不触发自校正。
#  ★ 这里之所以不用 0（严格相等）：盘前启动时，接口给的就是上一交易日数据，
#    那是**正常**的，不能因此判定"今天不开市"。
STALE_TOLERANCE_DAYS = 1


# ============================================================ 交易日判定（含自校正）

_OBS_LOCK = threading.Lock()
#: 由运行期观测**确证**的非交易日（YYYYMMDD 集合）。
#  来源：抓到的真实数据日期与预期不符时，由 note_data_date() 写入。
#  ★ 这是最强证据 —— 不是推断，是接口亲口说的。
_OBSERVED_NON_TRADING: set[str] = set()
#: 观测到的"接口实际返回的数据日期"（最近一次）。
_LAST_OBSERVED: dict[str, str] = {"date": "", "at": ""}


def _tag(d) -> str:
    return d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d)


def note_data_date(date_str, now=None):
    """把"接口实际返回的数据日期"喂进来，用于**运行期自校正**。

    返回 (changed, reason)：
      · 若该日期确证了"某天不是交易日"，返回 (True, 被确证的非交易日)
      · 否则 (False, "")

    调用时机：每次抓到面板数据之后（本来就有一次请求，成本为零）。
    判定逻辑：我们只关心**今天**。如果接口给的日期明显早于今天
    （超过 STALE_TOLERANCE_DAYS 天），说明"今天"根本不在交易 ——
    否则接口至少会给出最近一日的数据。

    ★ 为什么容忍 1 天：盘前（09:00）启动时接口给的是**上一交易日**数据，
      这是完全正常的，不能据此判定今天休市。只有差 ≥2 天才算证据。
    """
    now = now or datetime.now()
    d = None
    if isinstance(date_str, str) and len(date_str) >= 8:
        s = date_str[:10].replace("-", "")
        if s[:8].isdigit():
            try:
                d = datetime.strptime(s[:8], "%Y%m%d").date()
            except ValueError:
                d = None
    if d is None:
        return False, ""

    with _OBS_LOCK:
        _LAST_OBSERVED["date"] = _tag(d)
        _LAST_OBSERVED["at"] = now.strftime("%Y-%m-%d %H:%M:%S")

    today = now.date()
    gap = (today - d).days
    if 0 <= gap <= STALE_TOLERANCE_DAYS:
        return False, ""

    # gap > 容忍度：今天不是交易日（或接口严重异常）
    tag = _tag(today)
    with _OBS_LOCK:
        if tag in _OBSERVED_NON_TRADING:
            return False, ""
        _OBSERVED_NON_TRADING.add(tag)
    return True, tag


def last_observed() -> dict:
    """最近一次观测到的数据日期（诊断用）。"""
    with _OBS_LOCK:
        return dict(_LAST_OBSERVED)


def observed_non_trading() -> set[str]:
    """已被运行期观测确证为非交易日的日期集合。"""
    with _OBS_LOCK:
        return set(_OBSERVED_NON_TRADING)


def reset_observation():
    """清空观测状态（测试与"切换到历史日"时用）。"""
    with _OBS_LOCK:
        _OBSERVED_NON_TRADING.clear()
        _LAST_OBSERVED["date"] = ""
        _LAST_OBSERVED["at"] = ""


def is_trading_day(d=None, days=None):
    """判定某日是否交易日。返回 (是否交易日, 依据)。

    依据取值（含可信度语义）：
      "weekend"   周末 —— 硬规则
      "observed"  运行期观测确证 —— 最强证据
      "calendar"  交易日历命中 —— 可靠
      "holiday"   落在日历覆盖区间内但不在列表中 —— 可靠（确证为节假日）
      "assumed"   日历未覆盖，按工作日推断 —— **不确定**（长假会被误放行，
                  依赖 note_data_date 后续自校正）
      "assumed_weekend" 无日历且是周末——（不会出现，weekend 已先返回）

    days：可选，传入 `tradecal.get_trading_days()` 的结果以避免重复取数。
    若为 None 则本函数自行调用（走 tradecal 的进程内缓存，不额外联网）。
    """
    d = d or datetime.now()
    if hasattr(d, "date") and not hasattr(d, "weekday"):
        pass
    day = d.date() if isinstance(d, datetime) else d
    tag = _tag(day)

    # ---- L1 周末（硬规则）----
    if day.weekday() >= 5:
        return False, "weekend"

    # ---- L2 运行期观测（最强证据）----
    if tag in observed_non_trading():
        return False, "observed"

    # ---- L3 交易日历 ----
    if days is None:
        days = _load_days()
    if days:
        dset = days if isinstance(days, (set, frozenset)) else set(days)
        if tag in dset:
            return True, "calendar"
        # ★ 只有落在日历覆盖区间内，才能断定"不在列表 = 节假日"。
        #   落在区间之外（今天比日历最新一天还晚）说明**日历还没覆盖到**，
        #   此时不能下结论 —— 继续走 L4。
        lo, hi = min(dset), max(dset)
        if lo <= tag <= hi:
            return False, "holiday"

    # ---- L4 工作日推断（不确定）----
    return True, "assumed"


def _load_days():
    """尽力取交易日历；任何异常都返回 None（不抛、不阻塞）。"""
    try:
        import tradecal
        days, _approx = tradecal.get_trading_days(refresh=False)
        return days or None
    except Exception:                                    # noqa: BLE001
        return None


def _calendar_fresh():
    """日历是否**新鲜**（今天联网取过 / 今天写过盘）。

    ★ 为什么要这个信号：它决定 L5 层能不能用。
      只有"日历确实是新的"时候，"今天不在日历里"才能被解读为
      "今天的日线还没生成"（正常）而不是"日历根本没更新到"（无法判断）。
      来源是 tradecal.last_trusted()，它区分 net / disk_fresh / disk_stale
      等来源并给出 trusted 布尔 —— 本函数只是读取，不重新判断。
    """
    try:
        import tradecal
        return bool((tradecal.last_trusted() or {}).get("trusted"))
    except Exception:                                    # noqa: BLE001
        return False


def ensure_calendar(refresh=True):
    """启动时调一次：确保日历尽量新鲜（L5 层的前提）。

    ★ 为什么需要显式调用、而不在 is_trading_day 里联网：
      is_trading_day 会被 UI 高频调用（每次 tick、每次状态栏刷新）。
      在里面联网会造成"每次刷新都打一次 push2his"——正是 tradecal
      文档里记录过的历史事故。
      所以：**启动时调一次这里**，之后所有判断都只读缓存。

    tradecal 自身有"磁盘优先"保护（今天写过盘就不再联网），
    所以本函数一天最多触发一次真实网络请求。
    """
    if not refresh:
        return None
    try:
        import tradecal
        days, approx = tradecal.get_trading_days(refresh=True)
        return (days, approx)
    except Exception:                                    # noqa: BLE001
        return None


def is_trading_day(d=None, days=None):
    """判定某日是否交易日。返回 (是否交易日, 依据)。

    依据取值（含可信度语义）：
      "weekend"       周末 —— 硬规则，零依赖
      "observed"      运行期观测确证 —— 最强证据
      "calendar"      交易日历命中 —— 可靠
      "holiday"       落在日历覆盖区间内但不在列表中 —— 可靠（确证节假日）
      "calendar_live" 今天晚于日历最新一天，但日历是新鲜的 —— 可靠
                      （★ 实测：fflow/daykline 只返回**已完成交易日**，
                        盘中永远不含今天，故这是盘中常态）
      "assumed"       日历不可用或陈旧，按工作日推断 —— **不确定**
                      （长假会被放行一次，依赖 note_data_date 自校正）

    days：可选，传入 `tradecal.get_trading_days()` 的结果以避免重复取数。
    """
    d = d or datetime.now()
    day = d.date() if isinstance(d, datetime) else d
    tag = _tag(day)

    # ---- L1 周末（硬规则）----
    if day.weekday() >= 5:
        return False, "weekend"

    # ---- L2 运行期观测（最强证据）----
    if tag in observed_non_trading():
        return False, "observed"

    # ---- L3/L4/L5 交易日历 ----
    if days is None:
        days = _load_days()
    if days:
        dset = days if isinstance(days, (set, frozenset)) else set(days)
        if tag in dset:
            return True, "calendar"
        lo, hi = min(dset), max(dset)
        if lo <= tag <= hi:
            # 日历覆盖了那段时间却没有这一天 → 确证为节假日/调休
            return False, "holiday"
        if hi < tag and _calendar_fresh():
            # ★★ L5：今天晚于日历最新一天，且日历新鲜 → 交易日。
            #   实测依据（2026-09-23 盘中）：联网取回后日历最后一天仍是
            #   09-22，而当天确实是交易日 —— fflow/daykline 返回的是
            #   **已完成交易日的日线**，今天的日线要收盘后才生成。
            #   所以"今天不在日历里"在盘中是结构性常态，**不是**节假日信号。
            return True, "calendar_live"

    # ---- L6 工作日推断（不确定）----
    return True, "assumed"


def day_reason_text(reason: str) -> str:
    """依据 -> 面向用户的中文说明（状态栏用）。"""
    return {
        "weekend": "周末",
        "observed": "已确认非交易日",
        "calendar": "交易日",
        "calendar_live": "交易日",
        "holiday": "法定节假日",
        "assumed": "交易日（日历未更新，待确认）",
    }.get(reason, reason or "未知")


def is_confident(reason: str) -> bool:
    """该判定是否足够可靠（不需要向用户提示"待确认"）。"""
    return reason in ("weekend", "observed", "calendar", "calendar_live", "holiday")


# ============================================================ 时段分类

@dataclass(frozen=True)
class Session:
    """某一时刻的完整时段画像。UI 只需要读这个对象，不再自己算时间。"""
    now: datetime
    is_trading_day: bool
    day_reason: str            # 见 is_trading_day 的"依据"取值
    phase: Phase | None        # 非交易日为 None
    major: str                 # 盘前 / 集合竞价 / 盘中 / 盘后 / 休市
    tradable: bool
    auto_refresh: bool         # 此刻是否应当自动刷新
    phase_cap_ms: int | None   # 该时段的刷新间隔上限（None=不刷新）
    next_boundary: datetime | None   # 下一个时段边界（用于精确定时）
    seconds_to_boundary: float | None
    label: str                 # 中文一句话描述（状态栏直接用）

    @property
    def is_intraday(self) -> bool:
        """是否处于"盘中"这个一级时段（含午休）。"""
        return self.major == "盘中"

    @property
    def is_live(self) -> bool:
        """此刻是否会持续产生新数据（决定"数据会不会变"）。"""
        return bool(self.phase and self.phase.tradable)

    def refresh_interval(self, user_ms: int | None) -> tuple[int | None, bool]:
        """结合用户档位算出**生效**刷新间隔。

        返回 (生效毫秒 | None, 是否被钳制)。
        规则：effective = min(user_ms, phase_cap_ms)；cap 为 None 时返回 (None, False)。
        ★ 用户档位比上限更快时**不算钳制**（用户是主动要更快的，
          不是被程序否决），只有"用户要慢、程序强制提速"才算 ——
          因为那才是需要向用户解释的行为改变。
        """
        cap = self.phase_cap_ms
        if cap is None:
            return None, False
        if user_ms is None:
            return cap, False
        if user_ms <= cap:
            return int(user_ms), False
        return int(cap), True


def _phase_at(now: datetime) -> Phase:
    """当前时刻落在哪个子相。整天无缝覆盖，故必有返回。"""
    mod = now.hour * _MIN + now.minute
    for p in PHASES:
        if p.contains(mod):
            return p
    # 理论上不可达（PHASES 覆盖 0..1439）；防御性返回最后一相。
    return PHASES[-1]


def _next_boundary(now: datetime) -> datetime:
    """下一个"时段边界"时刻（子相切换点，或跨日）。

    ★ 为什么需要它：定时器是固定周期的（如 10 秒），若不对齐边界，
      09:29:57 那一拍之后要等到 09:30:07 才知道"开盘了" —— 开盘后
      前 7 秒的空窗会让"开盘瞬间的数据"错过。有了边界时刻，
      UI 可以用 singleShot(seconds_to_boundary) 精确在 09:30:00 触发。
    """
    mod = now.hour * _MIN + now.minute
    for p in PHASES:
        if p.t0 > mod:
            return now.replace(hour=p.t0 // _MIN, minute=p.t0 % _MIN,
                               second=0, microsecond=0)
    # 已过最后一个边界（post 相）→ 下一个边界是明天 00:00
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt


def classify(now=None, days=None) -> Session:
    """把某一时刻classify成完整时段画像。UI 唯一入口。

    days：可选，便于批量调用时复用同一份日历（避免逐次取数）。
    """
    now = now or datetime.now()
    trading, reason = is_trading_day(now, days=days)

    if not trading:
        nxt = _next_boundary(now)
        lbl = f"休市（{day_reason_text(reason)}）"
        return Session(
            now=now, is_trading_day=False, day_reason=reason, phase=None,
            major=MAJOR_CLOSED, tradable=False, auto_refresh=False,
            phase_cap_ms=None, next_boundary=nxt,
            seconds_to_boundary=max(0.0, (nxt - now).total_seconds()),
            label=lbl,
        )

    phase = _phase_at(now)
    cap = PHASE_REFRESH_MS.get(phase.key)

    # ★ 盘后结算窗：post 相只在 15:00–15:10 内自动刷新。
    if phase.key == "post":
        mod = now.hour * _MIN + now.minute
        if mod >= POST_SETTLE_END:
            cap = None

    auto = cap is not None
    nxt = _next_boundary(now)

    # 状态栏文案：把"时段 + 数据状态"压成一句话
    extra = ""
    if not is_confident(reason):
        extra = " · 待确认"
    if phase.key == "post" and cap is None:
        extra = " · 已定稿"
    lbl = f"{phase.major} · {phase.label}（{phase.span}）{extra}"

    return Session(
        now=now, is_trading_day=True, day_reason=reason, phase=phase,
        major=phase.major, tradable=phase.tradable, auto_refresh=auto,
        phase_cap_ms=cap, next_boundary=nxt,
        seconds_to_boundary=max(0.0, (nxt - now).total_seconds()),
        label=lbl,
    )


# ============================================================ 便捷判断（给各处替换用）

def is_tradable(now=None, days=None) -> bool:
    """此刻是否可连续交易（盘中且非午休）。"""
    return classify(now, days=days).tradable


def should_auto_refresh(now=None, days=None) -> bool:
    """此刻是否应当自动刷新（已含节假日过滤）。"""
    return classify(now, days=days).auto_refresh


def in_any_session(now=None, days=None) -> bool:
    """此刻是否落在"交易日的任何一个时段"内（盘前~盘后，即整天）。

    ★ 保留这个函数的语义边界：交易日 → 恒 True（PHASES 覆盖整天）；
      非交易日 → False。它替代原先各处 `925<=hm<=1505` 那种**裸数字区间**，
      但语义更明确："今天开市吗"，而不是"现在几点"。
    """
    return classify(now, days=days).is_trading_day


def phase_key(now=None, days=None) -> str:
    s = classify(now, days=days)
    return s.phase.key if s.phase else "closed"


def describe(now=None, days=None) -> str:
    """一句话中文描述（状态栏/日志用）。"""
    return classify(now, days=days).label


# ============================================================ 自检（模块级不变量）

def validate_phases() -> list[str]:
    """校验时段表自身的完整性。返回问题列表（空 = 全部通过）。

    ★ 为什么要机器校验：时段表是"手写常量"，一旦有人改错一个数字
      （如把 11:30 写成 12:30），会造成"午休时段里居然在交易"这类
      静默错误，而且不会崩溃、只会行为怪异。用断言把它钉死。
    """
    errs = []
    if not PHASES:
        return ["PHASES 为空"]
    if PHASES[0].t0 != 0:
        errs.append(f"首相应从 00:00 开始，实际 {_hm(PHASES[0].t0)}")
    if PHASES[-1].t1 != 24 * _MIN:
        errs.append(f"末相应到 24:00 结束，实际 {_hm(PHASES[-1].t1)}")
    for a, b in zip(PHASES, PHASES[1:]):
        if a.t1 != b.t0:
            errs.append(f"{a.key} 止于 {_hm(a.t1)}，但 {b.key} 始于 {_hm(b.t0)}"
                        f" —— 存在缝隙或重叠")
    for p in PHASES:
        if p.t0 >= p.t1:
            errs.append(f"{p.key} 区间非法：{_hm(p.t0)}–{_hm(p.t1)}")
        if p.major not in MAJORS:
            errs.append(f"{p.key} 的一级时段 {p.major!r} 不在 {MAJORS} 之内")
        if p.key not in PHASE_REFRESH_MS:
            errs.append(f"{p.key} 缺少刷新策略（PHASE_REFRESH_MS）")
    # 一级时段必须恰好 4 个且都被用到
    used = {p.major for p in PHASES}
    if used != set(MAJORS):
        errs.append(f"一级时段未完整覆盖：用到 {sorted(used)}，"
                    f"应为 {sorted(MAJORS)}")
    return errs


def summary() -> str:
    """时段表的一行式概览（日志用）。"""
    parts = []
    for m in MAJORS:
        segs = [p.span for p in PHASES if p.major == m]
        parts.append(f"{m}[{' + '.join(segs)}]")
    return " · ".join(parts)
