# -*- coding: utf-8 -*-
"""localio.py —— 本地数据导入与回放（离线复盘终端专用）

【定位】
本模块是 Shell 版唯一的数据入口。它**不发起任何网络请求**，只做三件事：
  1. 把用户导入的 CSV / JSON 规范化成面板结构；
  2. 落盘到与实盘完全相同的 panel 格式，让上层算法零改动复用；
  3. 列出本地已有面板，供回放。

【面板结构】（与 flowdata.build_panel 完全一致）
    meta = {
        "times":     ["09:31", ...],        # 分钟序列，升序
        "codes":     ["90.BK1036", ...],    # 板块 secid
        "names":     {secid: 名称},
        "kinds":     {secid: "industry"|"concept"},
        "source":    "import",              # 导入数据一律标 import
        "data_date": "YYYYMMDD",
    }
    mat.shape == (len(times), len(codes))

【为什么复用同一套格式】
辨识度算法（salience）、榜单池（watchlist）、龙头分析（leader）全部按这个结构
读取。导入数据只要对齐结构，算法就原样可用——这样"离线复盘"与"实盘"跑的是
同一套计算，结论才可比。
"""

from __future__ import annotations

import csv
import json
import os
import re

import numpy as np

# CSV 表头别名：兼容常见券商导出与手工整理的表格
_COL_TIME = ("时间", "time", "分钟", "hm", "时刻")
_COL_CODE = ("板块代码", "代码", "code", "secid", "板块id")
_COL_NAME = ("板块名称", "名称", "name", "板块")
_COL_KIND = ("类型", "kind", "类别")
_COL_VAL = ("主力净额", "主力净流入", "净额", "net", "value")

SHELL_MODE_ENV = "FLOWLIVE_SHELL_MODE"     # pure | replay
_MODE_PURE = "pure"
_MODE_REPLAY = "replay"


# ---------------------------------------------------------------- 模式
def mode():
    """当前 Shell 形态：pure（纯空壳）/ replay（离线复盘终端）。

    默认 replay —— 纯空壳没有使用价值，回放才有。需要纯空壳时设环境变量
    FLOWLIVE_SHELL_MODE=pure。
    """
    v = (os.environ.get(SHELL_MODE_ENV) or _MODE_REPLAY).strip().lower()
    return _MODE_PURE if v == _MODE_PURE else _MODE_REPLAY


def import_enabled():
    """是否允许导入（纯空壳形态下关闭）。"""
    return mode() == _MODE_REPLAY


# ---------------------------------------------------------------- 工具
def _data_dir():
    import flowdata
    return getattr(flowdata, "DATA", None) or "."


def _pick(header, aliases):
    """在表头里找第一个匹配的列名（大小写/空格不敏感）。"""
    norm = {re.sub(r"\s+", "", str(h)).lower(): h for h in header}
    for a in aliases:
        k = re.sub(r"\s+", "", a).lower()
        if k in norm:
            return norm[k]
    return None


def _norm_time(s):
    """把 0931 / 9:31 / 09:31:00 统一成 09:31。"""
    s = str(s).strip()
    m = re.match(r"^(\d{1,2}):?(\d{2})(?::\d{2})?$", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return "%02d:%02d" % (h, mi)


def _norm_code(s):
    """把 BK1036 / 90.BK1036 统一成 90.BK1036。"""
    s = str(s).strip()
    if not s:
        return None
    if "." in s:
        return s
    if s.upper().startswith("BK"):
        return "90." + s.upper()
    return s


# ---------------------------------------------------------------- 导入
def import_csv(path, tag, data_date=None, kind="concept"):
    """从 CSV 导入一份面板。

    必需列：时间 / 代码 / 数值。名称、类型可选。
    返回 (meta, mat)，并已落盘到 panel_{tag}。
    """
    if not import_enabled():
        raise RuntimeError("纯空壳形态不提供导入功能（FLOWLIVE_SHELL_MODE=pure）")

    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        raise ValueError("CSV 至少需要表头 + 一行数据")

    header = rows[0]
    c_time = _pick(header, _COL_TIME)
    c_code = _pick(header, _COL_CODE)
    c_val = _pick(header, _COL_VAL)
    c_name = _pick(header, _COL_NAME)
    c_kind = _pick(header, _COL_KIND)
    if not (c_time and c_code and c_val):
        raise ValueError(
            "缺少必需列。需要「时间」「代码」「数值」三列；当前表头：%s" % header)

    idx = {h: i for i, h in enumerate(header)}
    series = {}
    names = {}
    kinds = {}
    need = max(idx[c_time], idx[c_code], idx[c_val])
    for r in rows[1:]:
        if len(r) <= need:
            continue
        t = _norm_time(r[idx[c_time]])
        code = _norm_code(r[idx[c_code]])
        if not (t and code):
            continue
        try:
            v = float(str(r[idx[c_val]]).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        series.setdefault(code, {})[t] = v
        if c_name and r[idx[c_name]].strip():
            names[code] = r[idx[c_name]].strip()
        if c_kind and r[idx[c_kind]].strip():
            kinds[code] = r[idx[c_kind]].strip()

    if not series:
        raise ValueError("没有解析出任何有效数据行")

    times = sorted({t for d in series.values() for t in d})
    codes = sorted(series)
    mat = np.zeros((len(times), len(codes)), dtype=float)
    ti = {t: i for i, t in enumerate(times)}
    for j, code in enumerate(codes):
        for t, v in series[code].items():
            mat[ti[t], j] = v

    meta = {
        "times": times,
        "codes": codes,
        "names": {c: names.get(c, c) for c in codes},
        "kinds": {c: kinds.get(c, kind) for c in codes},
        "source": "import",
        "data_date": data_date or _guess_date(path),
    }
    _save(tag, meta, mat)
    return meta, mat


def import_json(path, tag):
    """从 JSON 导入面板。接受两种形态：

    ① {"meta": {...}, "values": [[...], ...]}   显式结构
    ② 平铺：times / codes / values 同层
    """
    if not import_enabled():
        raise RuntimeError("纯空壳形态不提供导入功能（FLOWLIVE_SHELL_MODE=pure）")

    with open(path, encoding="utf-8") as f:
        j = json.load(f)
    meta = j.get("meta") or {k: j.get(k) for k in
                             ("times", "codes", "names", "kinds", "data_date")}
    values = j.get("values") or j.get("mat")
    if not meta.get("times") or not meta.get("codes") or values is None:
        raise ValueError("JSON 缺少 times / codes / values")

    mat = np.asarray(values, dtype=float)
    if mat.shape != (len(meta["times"]), len(meta["codes"])):
        raise ValueError(
            "矩阵形状不匹配：期望 (%d, %d)，实际 %s"
            % (len(meta["times"]), len(meta["codes"]), mat.shape))

    meta = dict(meta)
    meta.setdefault("source", "import")
    meta.setdefault("data_date", _guess_date(path))
    meta["names"] = meta.get("names") or {c: c for c in meta["codes"]}
    meta["kinds"] = meta.get("kinds") or {c: "concept" for c in meta["codes"]}
    _save(tag, meta, mat)
    return meta, mat


def _guess_date(path):
    """从文件名里猜交易日，猜不到就用今天。"""
    m = re.search(r"(20\d{6})", os.path.basename(path))
    if m:
        return m.group(1)
    import datetime
    return datetime.date.today().strftime("%Y%m%d")


def _save(tag, meta, mat):
    import flowdata
    flowdata.save_panel(tag, meta, mat)


# ---------------------------------------------------------------- 回放
def list_panels():
    """列出本地已有面板 `[(tag, data_date, 行数, 列数, source)]`，按日期倒序。"""
    d = _data_dir()
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for fn in names:
        if not fn.startswith("panel_") or not fn.endswith(".json"):
            continue
        tag = fn[len("panel_"):-len(".json")]
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:                # noqa: BLE001
            continue
        out.append((
            tag,
            meta.get("data_date") or meta.get("date") or "",
            len(meta.get("times") or []),
            len(meta.get("codes") or []),
            meta.get("source") or "",
        ))
    out.sort(key=lambda x: (x[1] or "", x[0]), reverse=True)
    return out


def load(tag):
    """按 tag 读回面板。"""
    import flowdata
    return flowdata.load_panel(tag)


def export_csv(tag, path):
    """把本地面板导出成 CSV（便于外部工具处理）。"""
    meta, mat, _ = load(tag)
    times, codes = meta.get("times") or [], meta.get("codes") or []
    names = meta.get("names") or {}
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["时间", "板块代码", "板块名称", "主力净额"])
        for i, t in enumerate(times):
            for j, c in enumerate(codes):
                w.writerow([t, c, names.get(c, c), mat[i, j]])
    return path
