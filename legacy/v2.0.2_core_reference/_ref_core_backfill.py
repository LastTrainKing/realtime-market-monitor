# -*- coding: utf-8 -*-
"""
backfill.py —— 历史数据补全（v1.1.0 新增）

★ 接口现实（2026-09-19 五轮实测结论，见 probe_api*.py）：
  · 东财 fflow/kline?klt=1 只返回**最近一个交易日**的分钟资金流，lmt 无效；
    push2his 的分钟接口（fflow klt=1 / kline / trends2 ndays>1）全部不可用。
  · 唯一可取的历史数据是 push2his 的 fflow/daykline/get（日级五路资金流，
    lmt=40 实测可用，约覆盖最近 40 个交易日）。

★ 因此历史面板采用「日级真实总额 + 分钟形态合成」：
  · 每个板块每天的**收盘主力净额是真实数据**（daykline 官方口径）；
  · 分钟曲线是把当日总额按 U 型日内分布摊开（确定性随机种子，
    同一板块同一天重算结果逐字节一致，可进 MD5 回归）；
  · 面板 meta.source = "backfill"，界面据此标注「合成」，与实盘严格区分。

★ 幂等：已有面板的交易日一律跳过 —— 实盘数据永远不会被合成数据覆盖。

★ 限流纪律：push2his 对 IP 敏感（连发会被 RemoteDisconnected），
  本模块 6 线程 + 指数退避 + 连续失败熔断；原始响应按自然日缓存，
  同一天内重复补全不重复发请求。
"""
from __future__ import annotations

import json, os, time, threading, zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

import flowdata
import store

HIS = "https://push2his.eastmoney.com"
LMT = 40                                   # daykline 实测可用上限（覆盖 30 日窗口有余）
WORKERS = 6
BACKOFF = (1.2, 3.0, 6.0)                  # 逐次重试等待秒数
BREAKER_FAILS = 25                         # 连续失败熔断阈值
BREAKER_SLEEP = 45                         # 熔断冷却秒数
PROFILE = "ushape-v1"

# 标准分钟轴：09:31~11:30（120）+ 13:01~15:00（120）= 240 点
# （实盘面板的轴来自接口并集，与之一致；仅当本地无任何实盘面板时用它兜底）
STANDARD_MINUTES = (
    [f"09:{m:02d}" for m in range(31, 60)] +
    [f"10:{m:02d}" for m in range(0, 60)] +
    [f"11:{m:02d}" for m in range(0, 31)] +
    [f"13:{m:02d}" for m in range(1, 60)] +
    [f"14:{m:02d}" for m in range(0, 60)] +
    ["15:00"]
)


def _cache_file(data_dir):
    return os.path.join(data_dir, "daykline_cache.json")


def _atomic_json(path, obj):
    """先写临时文件再 os.replace：杜绝读者看到写了一半的 JSON。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 日级数据抓取
def _fetch_one_daykline(secid):
    """单板块日级资金流（带退避重试）。返回 [(yyyymmdd, 主力净额元), ...]。"""
    url = (f"{HIS}/api/qt/stock/fflow/daykline/get?lmt={LMT}&klt=101&secid={secid}"
           f"&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56&ut={flowdata.UT}")
    for k in range(len(BACKOFF) + 1):
        j = flowdata._get(url, tries=1, timeout=15)
        if j and j.get("data") and j["data"].get("klines"):
            rows = []
            for line in j["data"]["klines"]:
                p = line.split(",")
                if len(p) < 2:
                    continue
                d = p[0][:10].replace("-", "")
                try:
                    rows.append((d, float(p[1])))
                except (ValueError, IndexError):
                    continue
            return rows
        if k < len(BACKOFF):
            time.sleep(BACKOFF[k])
    return None


class _Breaker:
    """连续失败计数器：打满就全员冷却，避免被数据源拉黑名单。"""

    def __init__(self):
        self.n = 0
        self.lock = threading.Lock()

    def ok(self):
        with self.lock:
            self.n = 0

    def fail(self):
        with self.lock:
            self.n += 1
            return self.n >= BREAKER_FAILS
        return False


def fetch_daykline_map(secids, data_dir=None, on_progress=None):
    """批量抓日级资金流：{secid: {yyyymmdd: 主力净额元}}。

    同一天（自然日）内重复调用直接读缓存，不再发请求。
    """
    data_dir = data_dir or flowdata.DATA
    today = datetime.now().strftime("%Y-%m-%d")

    def say(msg):
        if on_progress:
            try:
                on_progress(msg)
            except Exception:                # noqa: BLE001
                pass

    # ---- 当日缓存优先
    try:
        with open(_cache_file(data_dir), encoding="utf-8") as f:
            cache = json.load(f)
        if cache.get("fetch_date") == today and cache.get("rows"):
            say(f"日级资金流：读当日缓存（{len(cache['rows'])} 个板块）")
            return {s: {d: v for d, v in rows} for s, rows in cache["rows"].items()}
    except Exception:                        # noqa: BLE001
        pass

    say(f"日级资金流：抓取 {len(secids)} 个板块（限流保护：{WORKERS} 线程）…")
    t0 = time.time()
    brk = _Breaker()
    out, failed = {}, 0

    def job(secid):
        nonlocal failed
        time.sleep(0.08)                      # 主动限速：宁可慢，不触发封禁
        rows = _fetch_one_daykline(secid)
        if rows is None:
            failed += 1
            if brk.fail():
                say("  连续失败过多，冷却 45 秒防限流…")
                time.sleep(BREAKER_SLEEP)
            return secid, None
        brk.ok()
        return secid, rows

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, (secid, rows) in enumerate(ex.map(job, secids), 1):
            if rows:
                out[secid] = {d: v for d, v in rows}
            if i % 100 == 0:
                say(f"  日级数据 {i}/{len(secids)}（{time.time()-t0:.0f}s）")

    say(f"  日级数据完成：{len(out)}/{len(secids)} 有数据，失败 {failed}，"
        f"用时 {time.time()-t0:.0f}s")

    # ---- 写当日缓存（失败板块不记，下次还能补）
    try:
        os.makedirs(data_dir, exist_ok=True)
        payload = {"fetch_date": today,
                   "rows": {s: sorted(r.items()) for s, r in out.items()}}
        _atomic_json(_cache_file(data_dir), payload)
    except Exception:                        # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------- 分钟曲线合成
def synth_curve(total_yi, n, seed):
    """把当日真实总额摊到 n 分钟：U 型日内分布 + 确定性噪声。

    同 (total, n, seed) 输出逐字节一致（MD5 回归的前提）。
    曲线终点严格等于 total_yi（真实锚点）。
    """
    if not total_yi:
        return np.zeros(n, dtype=float)
    rng = np.random.default_rng(seed)
    i = np.arange(n, dtype=float)
    # U 型：开盘重、收盘次之、午间最淡（A 股资金流典型形态）
    w = 1.0 + 1.1 * np.exp(-i / 18.0) + 0.7 * np.exp(-(n - 1 - i) / 28.0)
    noise = np.clip(1.0 + 0.35 * rng.standard_normal(n), 0.05, None)
    inc = w * noise
    cum = np.cumsum(inc)
    return cum / cum[-1] * float(total_yi)


def _seed_of(secid, tag):
    return zlib.crc32(f"{secid}|{tag}".encode("utf-8")) & 0x7FFFFFFF


# ---------------------------------------------------------------- 板块全集与分钟轴
def canonical_universe(data_dir=None, on_progress=None):
    """板块全集：优先沿用本地最新面板的名单（列对齐、离线可用）；
    本地什么都没有时才联网取清单。返回 (codes, names, kinds)。"""
    data_dir = data_dir or flowdata.DATA
    local = store.list_local_panels(data_dir)
    if local:
        try:
            with open(os.path.join(data_dir, f"panel_{local[-1]}.json"),
                      encoding="utf-8") as f:
                meta = json.load(f)
            return meta["codes"], meta["names"], meta["kinds"]
        except Exception:                    # noqa: BLE001
            pass
    # 兜底：联网取（与 build_panel 同口径：行业在前、概念在后）
    codes, names, kinds = [], {}, {}
    for kind in ("industry", "concept"):
        for secid, code, name, k in flowdata.list_sectors(kind):
            codes.append(secid)
            names[secid] = name
            kinds[secid] = k
    return codes, names, kinds


def canonical_minutes(data_dir=None):
    """分钟轴：沿用本地最新面板；没有就用标准 240 点轴。"""
    data_dir = data_dir or flowdata.DATA
    local = store.list_local_panels(data_dir)
    if local:
        try:
            with open(os.path.join(data_dir, f"panel_{local[-1]}.json"),
                      encoding="utf-8") as f:
                times = json.load(f).get("times")
            if times:
                return list(times)
        except Exception:                    # noqa: BLE001
            pass
    return list(STANDARD_MINUTES)


# ---------------------------------------------------------------- 编排
def run_backfill(days, data_dir=None, on_progress=None):
    """把 days（YYYYMMDD 升序）中缺失的交易日面板补齐。

    返回 {"written": [...], "skipped": [...], "n_sectors": int, "failed": int}
    """
    data_dir = data_dir or flowdata.DATA

    def say(msg):
        print(msg, flush=True)
        if on_progress:
            try:
                on_progress(msg)
            except Exception:                # noqa: BLE001
                pass

    have = set(store.list_local_panels(data_dir))
    todo = [d for d in days if d not in have]
    skipped = [d for d in days if d in have]
    if not todo:
        say(f"历史补全：{len(days)} 个交易日全部已存在，无需补全。")
        return {"written": [], "skipped": skipped, "n_sectors": 0, "failed": 0}

    say(f"历史补全：需补 {len(todo)} 天（{todo[0]} ~ {todo[-1]}），"
        f"已有 {len(skipped)} 天跳过。")

    codes, names, kinds = canonical_universe(data_dir, on_progress)
    if not codes:
        raise RuntimeError("板块全集为空：无法补全（网络不可达且无本地面板）。")
    minutes = canonical_minutes(data_dir)
    n_min = len(minutes)

    dk = fetch_daykline_map(codes, data_dir=data_dir, on_progress=on_progress)
    if not dk:
        raise RuntimeError("日级资金流抓取失败（可能被限流）：请稍后再试。")
    # ★ 质量门：日级数据缺失过多的面板是「残疾数据」——缺板板块整天为 0，
    #   看起来就像全天无资金活动，比没有数据更误导。宁可不写，等下次补。
    #   阈值取「比例 >20% 且绝对数 >20」：既抓得住限流式大面积缺失，
    #   又容忍新上市/停牌板块的零星缺数（小全集下 1~2 个属正常）。
    n_missing = len(codes) - len(dk)
    missing_ratio = n_missing / max(1, len(codes))
    if missing_ratio > 0.20 and n_missing > 20:
        raise RuntimeError(
            f"日级数据覆盖不足（{len(dk)}/{len(codes)}，缺失 {missing_ratio:.0%}）："
            f"多半是被数据源限流。请等待 30 分钟后再从「工具」菜单重试，"
            f"已抓到的部分已缓存，重试只会补缺口。")

    written = []
    for tag in todo:
        mat = np.zeros((n_min, len(codes)), dtype=float)
        for j, secid in enumerate(codes):
            total_yuan = (dk.get(secid) or {}).get(tag)
            if not total_yuan:
                continue                     # 该板块当日无数据 → 全天 0（与实盘 NaN→0 同义）
            mat[:, j] = synth_curve(total_yuan / 1e8, n_min, _seed_of(secid, tag))
        # ★ 原子写入：先写临时文件再 os.replace —— 补全进行中用户可能
        #   正好切到这一天，读到写了一半的 npy 会直接报错。
        #   注意 np.save 会给不以 .npy 结尾的文件名**自动补后缀**，
        #   所以临时文件用文件对象写入，绕开这个隐式行为。
        npy_tmp = os.path.join(data_dir, f"panel_{tag}.npy.tmp")
        with open(npy_tmp, "wb") as ftmp:
            np.save(ftmp, mat)
        os.replace(npy_tmp, os.path.join(data_dir, f"panel_{tag}.npy"))
        meta = {
            "times": minutes,
            "codes": codes,
            "names": names,
            "kinds": kinds,
            "source": "backfill",
            "anchor": f"daykline:push2his(lmt={LMT})",
            "profile": PROFILE,
            "synthesized_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _atomic_json(os.path.join(data_dir, f"panel_{tag}.json"), meta)
        # 历史日指数分钟行情不可得（接口限制）→ 空表，界面显示占位说明
        _atomic_json(os.path.join(data_dir, f"index_{tag}.json"), [])
        written.append(tag)
        say(f"  已写入 {tag}（{len(codes)} 板块 × {n_min} 分钟）")

    say(f"历史补全完成：新写入 {len(written)} 天。")
    return {"written": written, "skipped": skipped,
            "n_sectors": len(codes), "failed": len(codes) - len(dk)}
