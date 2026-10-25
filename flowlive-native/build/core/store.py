# -*- coding: utf-8 -*-
"""
store.py —— 面板库存与 30 交易日窗口清理（v1.1.0 新增）

存储结构（与 v1.0.5 的按日缓存一脉相承）：
  data/
    panel_YYYYMMDD.npy / .json   分钟宽表 + 元信息（meta.source = live|backfill）
    index_YYYYMMDD.json          指数分时（仅实盘日有；历史日为 []）
    calendar.json                交易日历缓存（tradecal 模块维护）
    daykline_cache.json          日级资金流原始响应缓存（backfill 模块维护）

窗口规则（用户需求）：
  始终只保留「最近 30 个交易日」的数据；当出现第 31 个交易日的数据时，
  把最早且超出窗口的那一天删掉。是否交易日以交易日历为准（非自然日）。
"""
from __future__ import annotations

import json, os

import flowdata

DATE_LEN = 8


def _is_date_tag(tag):
    return isinstance(tag, str) and len(tag) == DATE_LEN and tag.isdigit()


def panel_tag_of(fname):
    """panel_20260918.npy -> 20260918；非面板文件名返回 None。"""
    if not fname.startswith(("panel_", "index_")):
        return None
    if not fname.endswith((".npy", ".json")):
        return None
    tag = fname.split("_", 1)[1].rsplit(".", 1)[0]
    return tag if _is_date_tag(tag) else None


def list_local_panels(data_dir=None):
    """本地完整面板的日期 tag（升序）。

    面板至少需要配套的 ``panel_*.json``（时间轴/列元数据）与 ``panel_*.npy``
    （数值矩阵）；只看 npy 会把半迁移或写入失败的残缺缓存误列为可回看日期。
    """
    data_dir = data_dir or flowdata.DATA
    tags = set()
    try:
        names = set(os.listdir(data_dir))
        for f in names:
            if f.startswith("panel_") and f.endswith(".npy"):
                tag = panel_tag_of(f)
                if tag and f"panel_{tag}.json" in names:
                    tags.add(tag)
    except Exception:                            # noqa: BLE001
        pass
    return sorted(tags)


def panel_source(tag, data_dir=None):
    """读取面板来源标记：live / backfill / None（文件缺失或老格式未标记）。"""
    data_dir = data_dir or flowdata.DATA
    try:
        with open(os.path.join(data_dir, f"panel_{tag}.json"), encoding="utf-8") as f:
            meta = json.load(f)
        src = meta.get("source")
        # 老格式（v1.0.5 及以前抓的实盘缓存）没有 source 字段 —— 它们都是实盘
        return src or "live"
    except Exception:                            # noqa: BLE001
        return None


def purge_beyond_window(keep_tags, data_dir=None):
    """删除窗口外的日期面板（panel_/index_ 的 npy+json 一起删）。

    keep_tags: 允许保留的日期 tag 集合（最近 30 个交易日 ∪ 今天）。
    非日期名的文件（panel_today、panel_testnan 等）一律不动。
    返回实际删除的日期 tag 列表（升序）。
    """
    data_dir = data_dir or flowdata.DATA
    keep = set(keep_tags)
    doomed = set()
    try:
        for f in os.listdir(data_dir):
            tag = panel_tag_of(f)
            if tag and tag not in keep:
                doomed.add(tag)
    except Exception:                            # noqa: BLE001
        return []
    deleted = []
    for tag in sorted(doomed):
        ok = True
        for fname in (f"panel_{tag}.npy", f"panel_{tag}.json", f"index_{tag}.json",
                      f"index_all_{tag}.json", f"spot_leaders_{tag}.json",
                      f"opensnap_{tag}.json"): 
            fp = os.path.join(data_dir, fname)
            if not os.path.exists(fp):
                continue
            try:
                os.remove(fp)
            except Exception:                    # noqa: BLE001
                ok = False
        if ok:
            deleted.append(tag)
    return deleted
