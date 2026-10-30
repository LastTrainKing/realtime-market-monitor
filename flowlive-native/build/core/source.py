# -*- coding: utf-8 -*-
"""source.py —— 数据源注册与选择【Shell 版：只有一个本地源】

【本版本为什么变成这样】
原实现有 eastmoney / tencent / auto 三个源，带健康探测、故障转移与"暗门"切换。
上游行情接口已对爬虫来源做风控标记，**任何联网取数都会导致运行机器的 IP 被封禁**，
因此三个网络源整体移除，只保留一个 `local` 源。

`local` 源不产生任何网络请求，它只做两件事：
  1. 读本地已落盘的面板/快照（历史缓存回放）；
  2. 读用户导入的文件（离线复盘终端形态）。

【为什么保留全部函数名】
flowdata / opensnap / tradecal / backfill / app_main 都按这些名字调用。
保留签名、改变行为，可以让上层一行不改地降级——这是"换实现而不是砍层"的具体做法。

【能力表】
`CAPS[LOCAL]` 里所有联网能力一律 False。`skip_reason()` 会据此告诉调用方
"这个模块为什么被跳过"，而不是让它去打一个不存在的接口。
"""

from __future__ import annotations

import json
import os

# ---------------------------------------------------------------- 源定义
LOCAL = "local"

PROVIDERS = (LOCAL,)
DEFAULT_PROVIDER = LOCAL

SOURCE_ENV = "FLOWLIVE_SOURCE"      # 保留常量：遗留代码/诊断脚本可能引用
SOURCE_FILE = "source.json"

CAPS = {
    LOCAL: {
        "sector_list": False,
        "spot_flow": False,
        "minute_flow_history": False,
        "index_minute": False,
        "sector_members": False,
        "day_flow_history": False,
        "market_snapshot": False,
        "trade_calendar": False,
        # —— 本地能力 ——
        "local_replay": True,
        "local_import": True,
    },
}

_SKIP = {
    "sector_list": "本版本不含数据读取能力：板块清单只读本地缓存",
    "spot_flow": "本版本不含数据读取能力：不再取实时资金流",
    "minute_flow_history": "本版本不含数据读取能力：无分钟级资金流历史",
    "index_minute": "本版本不含数据读取能力：无指数分时",
    "sector_members": "本版本不含数据读取能力：无板块成分股",
    "day_flow_history": "本版本不含数据读取能力：无日级资金流历史",
    "market_snapshot": "本版本不含数据读取能力：无全市场快照",
    "trade_calendar": "本版本不含数据读取能力：交易日历只读本地缓存",
}


def _data_dir():
    import flowdata
    return getattr(flowdata, "DATA", None) or "."


def _read_config():
    try:
        p = os.path.join(_data_dir(), SOURCE_FILE)
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                    # noqa: BLE001
        return {}


def write_config(provider):
    """写数据源配置。Shell 版只有 local，写任何值都落到 local。"""
    try:
        p = os.path.join(_data_dir(), SOURCE_FILE)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"provider": LOCAL, "requested": str(provider)}, f,
                      ensure_ascii=False, indent=1)
    except Exception:                    # noqa: BLE001
        pass
    return LOCAL


def probe(provider=None, timeout=6):
    """本地源无需探测，恒可用。"""
    return {"provider": LOCAL, "ok": True, "rtt_ms": 0, "note": "本地源，不发请求"}


def resolve(force=False):
    return LOCAL


def active():
    return LOCAL


def uses_alt():
    return False


def cap(name):
    return bool(CAPS[LOCAL].get(name, False))


def status():
    return {
        "provider": LOCAL,
        "label": "本地数据（无联网读取）",
        "alt": False,
        "caps": dict(CAPS[LOCAL]),
        "note": "本版本不含数据读取能力；如需数据请使用离线复盘终端导入本地文件。",
    }


def skip_reason(cap_name):
    if cap(cap_name):
        return None
    return _SKIP.get(cap_name, "本版本不含数据读取能力：%s" % cap_name)


def index_secids():
    import flowdata
    return list(getattr(flowdata, "INDEX_SECIDS", []) or [])


def reset():
    return None


# ---------------------------------------------------------------- 兼容桩
def tencent_list_sectors(kind="industry", page_size=100):
    return []


def tencent_fetch_spot_batch(secids, detail=False, names=None):
    return {}


def tencent_fetch_index_trend(secid="1.000001"):
    return []


def tencent_fetch_minute_points(secid):
    return []


def match_report(secids, names):
    return {"matched": 0, "total": len(secids or []),
            "note": "本地源不匹配远程清单"}
