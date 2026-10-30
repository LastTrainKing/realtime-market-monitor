# -*- coding: utf-8 -*-
"""
tradecal.py —— 交易日历（v1.1.0 新增）

来源优先级：
  1. 东财 push2his fflow/daykline（参考板块 BK0475 银行，lmt=40）
     —— 返回的日期列表本身就是真实交易所日历，自动排除周末/节假日；
  2. 本地缓存 calendar.json（被限流/断网时用最近一次成功结果）；
  3. 工作日推断（仅排除周末，不含节假日，标记 approx=True —— 最后兜底）。

注意：push2his 对 IP 限流敏感（连续请求会被 RemoteDisconnected），
因此本模块每次启动最多发 1 次请求，失败立即回落缓存，绝不重试轰炸。

★ v1.2.1 修复：上面那句"每次启动最多 1 次请求"原先**只是口号**——
  `get_trading_days(refresh=True)` 每次调用都无条件联网，而它被
  native_app 的每次 reload() 调用（含 5 分钟自动刷新、每次手动刷新），
  于是一天下来会往限流敏感的 push2his 打几十次请求。
  现在加了**进程内内存缓存**（_MEMO）：同一自然日 + 未超 TTL 直接命中，
  不再联网；失败结果也记住，避免每轮重试轰炸。见 get_trading_days。

★★ v1.2.3：**磁盘优先**（启动提速最大的一刀，实测省 894ms / 占启动 40.7%）。
  v1.2.1 的内存缓存随进程消失，对"重新点开 exe"毫无帮助 ——
  每次启动仍然先联网再落盘。而交易日历一天最多变一次。
  现在改为「今天写过的盘缓存 → 内存 → 联网」，命中即 0 网络。
  见 get_trading_days 的 docstring。
"""
from __future__ import annotations

import json, os, threading, time
from datetime import datetime, timedelta

import flowdata

REF_SECID = "90.BK0475"                    # 银行板块：历史悠久、每个交易日必有数据
# [shell] 原 push2his 主机常量已移除：本版本不联网取数。
# ★ v1.2.1：进程内日历缓存。
#   取值策略：**同一自然日**内 + 距上次成功不超过 CAL_TTL 秒 → 直接用内存。
#   为什么还要"同一自然日"这个条件：跨日之后交易日历可能新增一天
#   （今天开市了），必须允许重取，否则窗口 purge 会少算一天。
#   CAL_TTL 取 6 小时：一个交易时段内不会变，又能兜住长时间挂机。
CAL_TTL = 6 * 3600
_MEMO = {"days": None, "approx": False, "day": "", "at": 0.0, "trusted": False}
_MEMO_LOCK = threading.Lock()

# ★★ v2.0.2：最近一次日历的**来源**与**可信度**。
#   用途只有一个：native_app 的 30 交易日窗口清理（purge）要知道
#   "这份日历能不能拿来决定删哪些文件"。
#   实测事故（2026-09-22）：日历缓存是 09-21 **盘中**写的，里面最后一格
#   只到 0918；次日凌晨启动时联网失败、回退到这份过期缓存，
#   而 approx 仍是 False（旧代码只把"工作日推断"当作不可信信号），
#   于是 purge 把真实的 09-21 全天数据当"窗口外"删掉了 —— 数据永久丢失。
#   所以：**只要日历不是"今天写的"或"联网现取的"，就一律不许做窗口清理。**
_LAST = {"trusted": False, "src": ""}


def last_trusted():
    """最近一次 get_trading_days 的日历是否可信到可做窗口清理。

    返回 {"trusted": bool, "src": str}。trusted=False 的三种情形：
      · 网络取数失败（回退到过期盘缓存）
      · 回退到工作日推断（approx）
      · 任何"今天没写过盘、也没联上网"的路径
    """
    with _MEMO_LOCK:
        return dict(_LAST)


def _set_last(trusted, src):
    with _MEMO_LOCK:
        _LAST["trusted"] = bool(trusted)
        _LAST["src"] = src


def _memo_get():
    with _MEMO_LOCK:
        return dict(_MEMO)


def _memo_put(days, approx, day, at, trusted=False):
    with _MEMO_LOCK:
        _MEMO["days"] = list(days)
        _MEMO["approx"] = bool(approx)
        _MEMO["day"] = day
        _MEMO["at"] = at
        _MEMO["trusted"] = bool(trusted)


def _cal_file():
    # ★ 必须惰性取路径：flowdata.DATA 会在运行期被重定向到用户目录
    #   （onefile 打包后 core/ 在只读 _MEIPASS），模块级常量会把路径
    #   固化在 import 那一刻的值 —— 与 salience.DATA 是同一个坑。
    return os.path.join(flowdata.DATA, "calendar.json")


def _fetch_days_from_daykline(lmt=40):
    """[shell] 数据读取能力已移除。交易日历只读本地缓存。"""
    return []


def _load_cache():
    try:
        with open(_cal_file(), encoding="utf-8") as f:
            j = json.load(f)
        days = [d for d in j.get("days", []) if isinstance(d, str) and len(d) == 8]
        return sorted(set(days)), j
    except Exception:                        # noqa: BLE001
        return [], {}


def _disk_fresh(meta, today):
    """盘缓存是不是**今天**更新的。

    ★ v1.2.3：交易日历一天最多变一次（开市时可能新增今天这一格），
      所以「今天取过」就等于「今天是准的」，没必要再问一次接口。
      这是启动提速最大的一刀（实测省 894ms / 占启动 40.7%）。
    """
    upd = str((meta or {}).get("updated") or "")
    return upd[:10].replace("-", "") == today


def _save_cache(days, source):
    try:
        os.makedirs(flowdata.DATA, exist_ok=True)
        payload = {
            "days": days,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
        }
        with open(_cal_file(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:                        # noqa: BLE001
        pass


def _weekday_fallback(n=45):
    """兜底：纯工作日推断（不含节假日，仅在网络与缓存双失时用）。"""
    days, d = [], datetime.now().date()
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return sorted(days)


def get_trading_days(refresh=True):
    """返回 (days, approx)：YYYYMMDD 升序列表；approx=True 表示工作日推断兜底。

    ★ v1.2.1：加**进程内内存缓存**（见模块头）。
      命中条件：同一自然日 且 距上次成功 < CAL_TTL → 直接返回，不联网。
      失败（返回缓存或工作日推断）同样会被记住，避免下一轮立刻重试。

    ★★ v1.2.3：再补**磁盘优先**（启动提速最大的一刀）。
      原先顺序是「先联网，失败才落盘」→ 每次启动都要等一次
      push2his 往返，实测 **894ms，占启动总耗时 40.7%**。
      而交易日历一天最多变一次（开市时可能新增今天），
      内存缓存又随进程消失、对"重新点开 exe"毫无帮助 ——
      于是每开一次都要重新付这 894ms。

      现在顺序改为「**今天写过的盘缓存 → 内存 → 联网**」：
        · 盘缓存今天是新的 → 直接用，**0 网络**，启动省下整段；
        · 盘缓存过期/不存在 → 才联网（失败仍落回盘缓存 → 工作日推断）。
      ★★ v2.0.2 更正：上面「少了"今天"这一格也不会误删」的说法**是错的**，
        已由 2026-09-22 的数据误删事故证伪 ——
        盘缓存在**上一个交易日盘中**写入时，缺的不是"今天"，而是
        **那一个交易日自己**；到了第二天它就成了"上一个交易日"，
        正好落进 30 日窗口清理的删除目标里，把真实数据删掉。
        所以可信度现在是**显式上报**（last_trusted()），由 purge 侧判定，
        不再依赖 approx 这一个布尔量去承担两件事。
    """
    now = time.time()
    today = datetime.now().strftime("%Y%m%d")
    m = _memo_get()
    if m["days"] and m["day"] == today and (now - m["at"]) < CAL_TTL:
        _set_last(m.get("trusted", False), "memo")
        return list(m["days"]), m["approx"]

    # ★ v1.2.3：先看盘。今天更新过就直接用 —— 启动路径上省掉一次网络往返。
    cached, meta = _load_cache()
    if cached and _disk_fresh(meta, today):
        _memo_put(cached, False, today, now, trusted=True)
        _set_last(True, "disk_fresh")
        return cached, False

    if refresh:
        days = _fetch_days_from_daykline()
        if days:
            _save_cache(days, f"daykline:{REF_SECID}")
            _memo_put(days, False, today, now, trusted=True)
            _set_last(True, "net")
            return days, False
    if cached:
        # ★★ v2.0.2：这里是 09-22 数据误删事故的直接入口 ——
        #   联网失败后回退到**过期**的盘缓存，而这份缓存是在上一个交易日
        #   盘中写下的，缺的正是"上一个交易日"这一格。旧代码 approx=False
        #   让上层以为它可信，purge 于是删掉了那一格对应的真实数据。
        #   现在显式标记 trusted=False，上层据此跳过窗口清理。
        _memo_put(cached, False, today, now, trusted=False)
        _set_last(False, "disk_stale")
        return cached, False
    fb = _weekday_fallback()
    _memo_put(fb, True, today, now, trusted=False)
    _set_last(False, "weekday_fallback")
    return fb, True


def recent_days(n=30, refresh=True):
    """最近 n 个交易日（升序）。返回 (days, approx)。"""
    days, approx = get_trading_days(refresh=refresh)
    return days[-n:], approx


def is_trading_day(tag, days=None):
    if days is None:
        days, _ = get_trading_days(refresh=False)
    return tag in set(days)


def pretty(tag):
    """YYYYMMDD -> YYYY-MM-DD（无法解析时原样返回）。"""
    if isinstance(tag, str) and len(tag) == 8 and tag.isdigit():
        return f"{tag[:4]}-{tag[4:6]}-{tag[6:]}"
    return str(tag)
