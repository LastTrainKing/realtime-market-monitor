# -*- coding: utf-8 -*-
"""
archive.py —— 每日收盘归档（v1.1.5 新增）

移植自 HTML 版 资金流向看板.html 的归档机制（localStorage → 本地文件）：
那个版本把「每个交易日收盘后的数据」存成一条记录，滚动保留最近 30 天，
攒满后新的一天挤掉最早的一天；页面可随时回看任意已归档的日期。

与 HTML 版的对应关系
--------------------------------------------------------------------
  HTML 版                              本模块
  ----------------------------------   ------------------------------------
  localStorage["flowlive_archive_v1"]  data/archive.json（索引 + 摘要）
  ARCHIVE_LIMIT = 30                   同（30 个交易日）
  ARCHIVE_SV = 4                       同（结构版本，改动时 +1 触发重写）
  buildDayRecord(date)                 build_day_record(tag)
  maybeArchive(sess, dateOverride)     maybe_archive(tag, source, …)
  enterHistory(date) / 回看时间表       store.list_local_panels() + 下拉框

★ 一个关键差异（本版更强的地方，刻意保留）：
  HTML 版只存"摘要"（榜单/簇/龙头/精选曲线），回看时用平线 + 精选曲线
  **重建**面板，所以历史日的榜单之外的东西（事件流、阶段对比全量）看不全。
  本版把**完整分钟面板**（panel_*.npy）留在本地，回看时走高保真管线，
  所有页面与实盘完全一致。archive.json 因此只承担"索引 + 归档元信息"，
  而不是数据的唯一来源 —— 数据本体仍是面板文件。

归档时机（与 HTML 版一致）
--------------------------------------------------------------------
  · 当日数据已"定稿"才写：实盘日要求分钟点足够多（≥200，HTML 版同口径），
    避免把开盘几分钟的残缺数据当成当天归档；
  · 幂等：已归档且数据点不更新时跳过，不重复写；
  · 30 天滚动：保留最近 30 个交易日（与 store.purge_beyond_window 同窗口）。
"""
from __future__ import annotations

import json
import os
import time

ARCHIVE_LIMIT = 30          # 滚动窗口：最近 30 个交易日
ARCHIVE_SV = 1              # 记录结构版本；改字段时 +1
ARCHIVE_NAME = "archive.json"
MIN_BARS = 200              # 定稿门槛：分钟点数（HTML 版同样要求 ≥200）


def _path(data_dir):
    return os.path.join(data_dir, ARCHIVE_NAME)


def read(data_dir):
    """读取归档索引。缺失/损坏时返回空结构（不抛异常）。"""
    empty = {"v": 1, "limit": ARCHIVE_LIMIT, "sv": ARCHIVE_SV,
             "updated_at": None, "days": []}
    try:
        with open(_path(data_dir), encoding="utf-8") as f:
            obj = json.load(f)
        if not isinstance(obj, dict) or not isinstance(obj.get("days"), list):
            return empty
        obj.setdefault("limit", ARCHIVE_LIMIT)
        obj.setdefault("sv", ARCHIVE_SV)
        return obj
    except Exception:                                    # noqa: BLE001
        return empty


def _write(obj, data_dir):
    """原子写（与 store/backfill 同一纪律：先 tmp 再 os.replace）。"""
    p = _path(data_dir)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return True
    except Exception:                                    # noqa: BLE001
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:                                # noqa: BLE001
            pass
        return False


def build_day_record(tag, n_bars, n_sectors, source, data_dir):
    """生成某一天的归档记录（对应 HTML 版 buildDayRecord）。

    HTML 版把榜单/簇/事件/龙头全塞进记录里，因为它没有别的存储；
    本版有完整面板文件，所以这里只存**归档元信息**：
    日期、分钟点数、板块数、来源、归档时间 —— 足够索引与展示。
    """
    return {
        "date": tag,
        "bars": int(n_bars or 0),
        "sectors": int(n_sectors or 0),
        "source": source or "live",          # live = 实盘归档 / backfill = 合成
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sv": ARCHIVE_SV,
    }


def maybe_archive(tag, n_bars, n_sectors, source="live", data_dir=None,
                  force=False):
    """按 HTML 版 maybeArchive 的规则归档当前这一天。

    返回 {action: "add"|"update"|"skip", evicted: [...], total: n} 或 None。
    """
    if not tag or len(str(tag)) != 8 or not str(tag).isdigit():
        return None
    if not force and (n_bars or 0) < MIN_BARS:
        # 数据未定稿（盘中/残缺）—— 不归档，避免把开盘几分钟写死成当天记录
        return {"action": "skip", "reason": "bars_too_few", "total": None,
                "evicted": []}

    obj = read(data_dir)
    days = obj.get("days") or []
    # ★★ v1.2.1 修复：**先过滤成"有 date 的 dict"再往下走**。
    #   旧代码只在"查重"那一处做了 isinstance 判断，紧接着的
    #   `days.sort(key=lambda x: str(x.get("date","")))` 与
    #   `days.pop(0).get("date")` 都**假定元素是 dict** ——
    #   archive.json 里只要有一条脏数据（手工编辑过 / 旧结构残留 / 写入中断），
    #   就会抛 AttributeError；而调用方是 `except Exception: log_line(...)`，
    #   于是**归档此后每天都静默失败**，界面上完全看不出来
    #   （只表现为"回看下拉不再长新日期"）。
    #   统一过滤口径，与 archived_days / prune_to 里的 isinstance 判断保持一致。
    days = [x for x in days if isinstance(x, dict) and x.get("date")]
    exist = None
    for x in days:
        if x.get("date") == tag:
            exist = x
            break

    # 幂等：已归档且数据不更新 → 跳过（HTML 版同判据 exist.bars >= axis.length）
    if exist and not force and int(exist.get("bars", 0)) >= int(n_bars or 0):
        return {"action": "skip", "reason": "already_fresh",
                "total": len(days), "evicted": []}

    rec = build_day_record(tag, n_bars, n_sectors, source, data_dir)
    days = [x for x in days if x.get("date") != tag]
    days.append(rec)
    days.sort(key=lambda x: str(x.get("date", "")))

    # ★ 30 交易日滚动：攒满后新的一天挤掉最早的一天（HTML 版同行）
    evicted = []
    while len(days) > ARCHIVE_LIMIT:
        evicted.append(days.pop(0).get("date"))

    out = {"v": 1, "limit": ARCHIVE_LIMIT, "sv": ARCHIVE_SV,
           "updated_at": rec["saved_at"], "days": days}
    ok = _write(out, data_dir)
    return {"action": "update" if exist else "add",
            "evicted": evicted, "total": len(days), "ok": ok}


def archived_days(data_dir):
    """已归档的日期列表（升序）。"""
    return sorted(str(x.get("date")) for x in (read(data_dir).get("days") or [])
                  if isinstance(x, dict) and x.get("date"))


def prune_to(keep_tags, data_dir):
    """把归档索引裁到窗口内（与 store.purge_beyond_window 保持同步）。

    面板文件被 30 日窗口清掉后，归档索引里不该再留着它的记录 ——
    否则回看下拉会出现"有记录但没数据"的条目。
    """
    keep = set(keep_tags)
    obj = read(data_dir)
    days = obj.get("days") or []
    kept = [x for x in days
            if isinstance(x, dict) and str(x.get("date")) in keep]
    dropped = [str(x.get("date")) for x in days
               if isinstance(x, dict) and str(x.get("date")) not in keep]
    if dropped:
        obj["days"] = kept
        obj["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write(obj, data_dir)
    return dropped


def summary(data_dir):
    """给「数据状态」页用的一句话摘要。"""
    days = archived_days(data_dir)
    if not days:
        return ("尚未归档任何交易日。每个交易日收盘后会自动归档一条；"
                f"攒满 {ARCHIVE_LIMIT} 条后，新的一天会挤掉最早的一天。")
    n = len(days)
    left = ARCHIVE_LIMIT - n
    tail = (f"还可再攒 {left} 天" if left > 0
            else "已满，新的一天将挤掉最早的一天")
    return (f"已归档 {n} / {ARCHIVE_LIMIT} 个交易日"
            f"（{days[0]} ~ {days[-1]}，{tail}）")
