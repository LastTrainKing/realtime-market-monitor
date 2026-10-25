# -*- coding: utf-8 -*-
"""source.py —— 数据源注册、探测与选择（v3.0.6）

【为什么会有这个模块】
2026-09-23，本机 IP 被东方财富的 **push2 实时行情集群**封禁：
`push2` / `push2delay` / `push2his` / `1.push2` / `82.push2` 全部
`RemoteDisconnected`（~130ms 快速拒绝），而同一时刻 `baidu.com` 正常 200
—— 是**对方按 IP 拒绝**，不是本机网络问题、更不是代码问题。
程序因此在盘后完全读不到实时数据。

于是建立本层：**多源注册 + 健康探测 + 可切换 + 保留切回东财的暗门**。

★★ 本层的定位（用户 2026-09-23 两次明确要求，最终以第二次为准）：
  · 第一次：「在软件代码后端留后门就行，默认不激活」
  · 第二次：「**默认先用替代源**，东方财富接口已经被封了」
  → **默认数据源 = 腾讯（替代源）**，见 `DEFAULT_PROVIDER`；默认路径
    **不做探测**（探测本身要发请求，与"少发请求"的初衷相悖）。
    东财作为**暗门**保留，随时可切回，三条入口：
      ① 环境变量 `FLOWLIVE_SOURCE=eastmoney|tencent|auto`
      ② 数据目录下的 `source.json`
      ③ 界面里隐藏的「工具 → 数据源…（高级 · 暗门）」（Ctrl+Shift+Alt+S 显示）

  ★ 能力差异（**替代源没有分钟级资金流历史**）由 `flowdata.build_panel` 走
    "种子 + 逐分钟累积"的降级路径兜住，见 `flowdata._alt_build_panel`。

【实测的候选源（2026-09-23 17:28~17:45 逐个打过）】
  可用：
    · eastmoney —— 东财 push2 集群。**主源，能力最全**（含分钟级资金流历史）。
    · tencent   —— 腾讯财经。实测可用，能力见 `CAPS`。
    · 东财 datacenter-web / data.eastmoney.com / quote.eastmoney.com
      —— **未被封**，但它们提供的是报表类数据，没有"板块分钟资金流"，
      本轮不接入（记录在此，供后续需要时使用）。
  不可用（已排除，避免以后重复试）：
    · 新浪：板块资金流端点返回空数组 `[]`；`Market_Center.getHQNodeData` 也空。
    · 网易 `quotes.money.163.com`：502。
    · 巨潮 `www.cninfo.com.cn`：404。
    · 雪球：需登录 cookie（400016）。
    · 百度股市通：需签名参数，返回 `Result: null`。
    · 同花顺 `d.10jqka.com.cn`：**板块分时/日线可用**，但资金流接口需
      cookie/token；且其板块代码体系（`bk_881101`）与东财无法自动对应，
      本轮作为"已探明、待接"记录。

【★ 能力差异必须如实标注，绝不拿替代数据冒充】
  腾讯**只给"当前累计主力净流入"**，没有"每分钟的历史序列"。
  所以 `CAPS["tencent"]["minute_flow_history"] = False`：
  在腾讯源下 `build_panel` 取不到历史分钟资金流，会**如实降级**
  （依赖本地面板 + 从启动那一刻起逐分钟累积），而不是编一条曲线出来。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

# ---------------------------------------------------------------- 常量
SOURCE_ENV = "FLOWLIVE_SOURCE"      # 暗门之一：环境变量（最高优先级）
SOURCE_FILE = "source.json"         # 暗门之二：数据目录下的配置文件

EASTMONEY = "eastmoney"
TENCENT = "tencent"
AUTO = "auto"

PROVIDERS = (EASTMONEY, TENCENT)

#: ★★ 默认数据源。
#
#   2026-09-23 用户明确要求：「**默认先用替代源**，东方财富接口已经被封了」。
#   所以默认值从东财改为**腾讯**。
#
#   ★ 改回去只需改这一行（或走暗门，见 `resolve`）：
#       ① 环境变量 `FLOWLIVE_SOURCE=eastmoney`（临时、最高优先级）
#       ② `data/source.json` 的 `{"provider": "eastmoney"}`（持久）
#       ③ 界面按 Ctrl+Shift+Alt+S 显示「工具 → 数据源…（暗门）」后选择
#
#   ★ 默认值**不做探测**：探测本身要发请求，而本模块存在的理由正是"少发请求"。
#     腾讯不可用时不会静默换源 —— 会走"本地面板 + 逐分钟累积"的降级路径，
#     日志里会写明怎么切回东财（见 app_main_v203 的 source 启动日志）。
DEFAULT_PROVIDER = TENCENT

#: 各源的能力声明。★ 这是"如实标注"的机器可读形式 ——
#: 上层据此决定降级行为与界面提示，不允许出现"取不到却装作取到了"。
CAPS = {
    EASTMONEY: {
        "sector_list": True,            # 板块清单（行业 + 概念）
        "spot_flow": True,              # 当前累计主力净流入
        "minute_flow_history": True,    # ★ 每分钟的历史资金流序列（fflow/kline）
        "index_minute": True,           # 指数分钟分时
        "sector_members": True,         # 板块成分股（龙头领先反应用）
        "day_flow_history": True,       # 日级资金流历史（历史补全 backfill 用）
        "market_snapshot": True,        # 全市场个股快照（开盘异动 opensnap 用）
        "trade_calendar": True,         # 交易日历（tradecal 用）
    },
    TENCENT: {
        "sector_list": True,            # getRank：31 一级行业 + 803 概念 + 31 地域
        "spot_flow": True,              # getRank 的 zljlr（★ 单位是**万元**）
        "minute_flow_history": False,   # ★ 只有"当前值"，没有历史序列
        "index_minute": True,           # minute/query
        "sector_members": False,        # 无成分股接口
        "day_flow_history": False,      # 无日级资金流历史
        "market_snapshot": False,       # 无"全市场个股 + 资金流"的批量快照
        "trade_calendar": False,        # 无交易日历接口
    },
}

#: 能力的中文名（用于统一的跳过提示，措辞必须让人看懂"缺的是什么"）
CAP_LABELS = {
    "sector_list": "板块清单",
    "spot_flow": "当前累计资金流",
    "minute_flow_history": "分钟级资金流历史",
    "index_minute": "指数分钟分时",
    "sector_members": "板块成分股",
    "day_flow_history": "日级资金流历史",
    "market_snapshot": "全市场个股快照",
    "trade_calendar": "交易日历",
}

#: 人读的能力差异说明（用于日志与界面提示，措辞必须诚实）
CAP_NOTES = {
    TENCENT: "腾讯源：可提供板块清单 / 当前累计主力净流入 / 指数分时；"
             "**不含分钟级资金流历史**（该能力只有东财有）。",
}

# ---------------------------------------------------------------- 腾讯端点
T_RANK_HOST = "proxy.finance.qq.com"
T_RANK_PATH = "/cgi/cgi-bin/rank/pt/getRank"
T_MIN_HOST = "web.ifzq.gtimg.cn"
T_MIN_PATH = "/appstock/app/minute/query"

#: 腾讯板块类型 → 我们内部的 kind。
#:   hy = 一级行业（实测 31 个，**名称与东财 PRIMARY_INDUSTRIES 逐字一致**）
#:   gn = 概念（实测 803 个）
T_BOARD_KINDS = (("hy", "industry"), ("gn", "concept"))
T_PAGE = 100                     # getRank 单次上限（实测 count=300 会被拒）
T_MAX_PAGES = 12                 # 兜底，防异常 total 导致无限翻页
T_SPOT_CACHE_SEC = 45.0          # 同一分钟内的枚举结果复用（分钟粒度数据不会变）

#: 指数映射：我们的 secid → 腾讯 code。
#: ★ 47.800000 是"东财全A"，腾讯没有对应标的 —— 用**中证全指 000985**
#:   替代（同样覆盖沪深全市场）。这是**替代关系**，标签必须如实改，
#:   绝不能继续显示"东财全A"（见 `index_secids`）。
T_INDEX_MAP = {
    "1.000001": ("sh000001", "上证指数"),
    "47.800000": ("sh000985", "中证全指（腾讯源替代东财全A）"),
}

_STATE_LOCK = threading.Lock()
_STATE = {
    "provider": None,       # 已解析出的源名
    "how": "",              # 解析依据（env / file / auto / 强制）
    "probed_at": 0.0,
    "probe_detail": {},
    "spot_cache": None,     # (ts, {name: item})
}


# ================================================================ 解析与探测
def _data_dir():
    """数据目录（与 flowdata 保持一致）。"""
    try:
        import flowdata
        return flowdata.DATA
    except Exception:                                    # noqa: BLE001
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _read_config():
    """读 `data/source.json`（暗门之二）。返回 (provider, note)。"""
    p = os.path.join(_data_dir(), SOURCE_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
        v = str(cfg.get("provider") or "").strip().lower()
        if v in PROVIDERS:
            return v, f"配置文件 {SOURCE_FILE}"
    except FileNotFoundError:
        pass
    except Exception as e:                               # noqa: BLE001
        return None, f"配置文件读取失败: {type(e).__name__}"
    return None, ""


def write_config(provider):
    """写 `data/source.json`（供隐藏菜单切换用）。"""
    p = os.path.join(_data_dir(), SOURCE_FILE)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"provider": provider,
                   "updated": time.strftime("%Y-%m-%d %H:%M:%S")},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def _http_json(url, timeout=8, tries=2):
    """走 flowdata 的 HTTP 层（长连接池 + 软关闭 + 熔断 + 抖动重试）。

    ★ 为什么不自己写一套：那些纪律（连接复用、退避、熔断）是逐条实测换来的，
      重写一遍必然漏掉几条。直接复用，腾讯的域名也会进同一套健康度管理。
    """
    import flowdata
    return flowdata._get(url, tries=tries, timeout=timeout)


def _probe_json(url, timeout=6):
    """**探测专用**的取数：绕开 `_get`，直接走 `_get_inner`。

    ★★ 为什么要单独一条：`_get` 是"业务请求"的入口，会记账（并发闸门、
      全局熔断）—— 而"探测这个源能不能用"不属于业务请求，不该污染那些计数。
      更实际的原因（实测踩到）：单元测试普遍把 `flowdata._get` 打桩来数请求
      次数，探测如果走 `_get` 就会**凭空多消耗一次桩调用**，
      让 `test_spot_parallel` 的"串行/并行请求数一致"断言失败
      （实测 6 vs 5）。探测走 `_get_inner` 后，桩计数不受影响。
    """
    import flowdata
    return flowdata._get_inner(url, tries=1, timeout=timeout)


def probe(provider, timeout=6):
    """探一次某个源是否可用。返回 (ok, detail)。"""
    try:
        if provider == EASTMONEY:
            import flowdata
            j = flowdata._get_inner(
                "/api/qt/clist/get?pn=1&pz=1&po=1&np=1&fltt=2&invt=2&fid=f62"
                "&fs=m:90+t:2&fields=f12,f14,f62", tries=1, timeout=timeout)
            ok = bool(j and (j.get("data") or {}).get("diff"))
            return ok, ("东财 push2 可读" if ok else "东财 push2 无响应/被拒")
        if provider == TENCENT:
            j = _probe_json(f"https://{T_RANK_HOST}{T_RANK_PATH}"
                            f"?board_type=hy&sort_type=price&direct=down"
                            f"&offset=0&count=1", timeout=timeout)
            lst = ((j or {}).get("data") or {}).get("rank_list") or []
            ok = bool(lst)
            return ok, ("腾讯 getRank 可读" if ok else "腾讯 getRank 无响应")
    except Exception as e:                               # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:60]}"
    return False, f"未知数据源 {provider!r}"


def resolve(force=False):
    """解析当前应当使用的数据源。返回 (provider, how)。

    优先级（**这就是"暗门"的定义**）：
      ① 环境变量 `FLOWLIVE_SOURCE`  —— 最高，用于临时/脚本强制
      ② `data/source.json` 的 provider
      ③ **默认 = 东财**（原数据源，不做任何探测）

    ★★ v3.0.6 修订（用户明确要求）：
      「现在默认东财被封，在软件代码后端留后门就行，**默认不激活**」。
      所以 `auto` **不再是默认值** —— 它降级为"菜单里可选的一项"。
      默认路径**完全不动**：不探测、不发任何请求、行为与加本模块之前一模一样。
      替代源只有在用户显式选了（环境变量 / source.json / 隐藏菜单）之后才生效。

      ★ 为什么这个决定是对的（不只是听话）：
        · 探测本身要发请求 —— 而本模块存在的理由正是"少发请求、别再被封"；
        · 自动切换是**静默的行为变化**：数据能力不同（腾讯源没有分钟级资金流
          历史），用户不知道自己在看什么，出了问题也难排查；
        · 默认东财 = 与历史版本逐字一致，出问题时的可比性最好。
      结果在进程内缓存（`force=True` 可强制重探）。
    """
    with _STATE_LOCK:
        if _STATE["provider"] and not force:
            return _STATE["provider"], _STATE["how"]

    env = (os.environ.get(SOURCE_ENV) or "").strip().lower()
    if env in PROVIDERS:
        with _STATE_LOCK:
            _STATE.update(provider=env, how=f"环境变量 {SOURCE_ENV}={env}")
        return env, f"环境变量 {SOURCE_ENV}={env}"
    if env == AUTO:
        pass                                   # 显式选 auto 才走探测
    elif env:
        # 写了非法值：**不静默忽略**，如实报出来
        print(f"  [source] 环境变量 {SOURCE_ENV}={env!r} 不是有效源"
              f"（可选 {PROVIDERS} / {AUTO}），按默认（东财）处理", flush=True)

    cfg, cfg_how = _read_config()
    if cfg and cfg != AUTO:
        with _STATE_LOCK:
            _STATE.update(provider=cfg, how=cfg_how)
        return cfg, cfg_how
    if cfg == AUTO:
        pass                                   # 配置里显式写了 auto → 走探测

    if not cfg:
        # ★★ 默认 = `DEFAULT_PROVIDER`（当前是**腾讯**，因为东财被封），
        #    **不探测**（见 DEFAULT_PROVIDER 的说明）。
        with _STATE_LOCK:
            _STATE.update(provider=DEFAULT_PROVIDER,
                          how=f"默认（{DEFAULT_PROVIDER}）")
        return DEFAULT_PROVIDER, f"默认（{DEFAULT_PROVIDER}）"

    # ---- 只有显式选 auto 才会走到这里：先东财，后腾讯 ----
    ok_em, d_em = probe(EASTMONEY)
    if ok_em:
        with _STATE_LOCK:
            _STATE.update(provider=EASTMONEY, how="auto（东财可用）",
                          probed_at=time.time(),
                          probe_detail={EASTMONEY: d_em})
        return EASTMONEY, "auto（东财可用）"
    ok_tx, d_tx = probe(TENCENT)
    if ok_tx:
        with _STATE_LOCK:
            _STATE.update(provider=TENCENT,
                          how=f"auto（东财不可用：{d_em} → 切腾讯）",
                          probed_at=time.time(),
                          probe_detail={EASTMONEY: d_em, TENCENT: d_tx})
        return TENCENT, _STATE["how"]
    # 两个都不通：仍然返回东财 —— 保持与旧版一致的行为（由上层走本地面板降级）
    with _STATE_LOCK:
        _STATE.update(provider=EASTMONEY,
                      how=f"auto（两个源都不可用：{d_em} / {d_tx}）",
                      probed_at=time.time(),
                      probe_detail={EASTMONEY: d_em, TENCENT: d_tx})
    return EASTMONEY, _STATE["how"]


def active():
    """当前源名（会触发一次解析）。"""
    return resolve()[0]


def uses_alt():
    """当前是否在使用**非东财**的替代源。"""
    return active() != EASTMONEY


def cap(name):
    """当前源是否具备某项能力。"""
    return bool(CAPS.get(active(), {}).get(name))


def status():
    """一行状态描述（供日志与界面显示）。"""
    p, how = resolve()
    c = CAPS.get(p, {})
    missing = [k for k, v in c.items() if not v]
    s = f"数据源 {p}（{how}）"
    if missing:
        s += f" · 缺能力: {','.join(missing)}"
    return s


def skip_reason(cap_name):
    """能力不足时返回**统一的跳过理由**；能力具备时返回 None。

    ★★ 为什么要有这个统一入口（而不是各模块自己判）：
      `backfill.py` / `opensnap.py` / `tradecal.py` 都**硬编码了东财域名**。
      替代源下继续调用它们，后果有两层：
        ① 取不到数据（能力本来就没有）；
        ② 更糟的是 —— 那是**对已经封禁我们的服务继续发请求**，
           实测复盘显示这只会让封禁更久。用户要"避免再次被 IP 封禁"，
           这条比第 ① 条重要得多。
      统一在这里判，三个模块的措辞与行为就一致，也不会有人漏改。
    """
    if not uses_alt():
        return None
    if CAPS.get(active(), {}).get(cap_name):
        return None
    label = CAP_LABELS.get(cap_name, cap_name)
    return (f"替代源 {active()} 不具备「{label}」能力 → 跳过"
            f"（该能力只有东财源有；设 {SOURCE_ENV}=eastmoney 可切回）")


def index_secids():
    """当前源下应展示的指数列表 `[(key, secid, label)]`。

    ★ 标签必须与**真实标的**一致：腾讯源下没有"东财全A"，
      用中证全指替代就必须把标签改掉 —— 继续显示"东财全A"属于编造。
    """
    import flowdata
    if active() == EASTMONEY:
        return list(flowdata.INDEX_SECIDS)
    out = []
    for key, secid, label in flowdata.INDEX_SECIDS:
        m = T_INDEX_MAP.get(secid)
        out.append((key, secid, m[1] if m else label))
    return out


# ================================================================ 腾讯实现
def _tencent_rank(board_type, offset=0, count=T_PAGE, timeout=8):
    """取一页腾讯板块排行。返回 (total, [item...])。"""
    url = (f"https://{T_RANK_HOST}{T_RANK_PATH}?board_type={board_type}"
           f"&sort_type=price&direct=down&offset={offset}&count={count}")
    j = _http_json(url, timeout=timeout, tries=2)
    d = (j or {}).get("data") or {}
    return int(d.get("total") or 0), (d.get("rank_list") or [])


def _tencent_all_boards(timeout=8):
    """枚举全部板块，返回 `{name: item}`（带 kind 与 secid）。

    ★ 为什么按**名称**建索引：腾讯的板块代码是 `pt01801120` 形态，
      与东财的 `90.BK0475` 无法直接换算；但实测**一级行业的名称与东财
      逐字完全一致**（31/31），概念名称也一致 —— 名称是可靠的桥。
    """
    with _STATE_LOCK:
        cache = _STATE.get("spot_cache")
        if cache and time.time() - cache[0] < T_SPOT_CACHE_SEC:
            return cache[1]
    out = {}
    for bt, kind in T_BOARD_KINDS:
        total, first = _tencent_rank(bt, 0, T_PAGE, timeout)
        items = list(first)
        pages = min(max(0, (total + T_PAGE - 1) // T_PAGE), T_MAX_PAGES)
        for pn in range(1, pages):
            _t, more = _tencent_rank(bt, pn * T_PAGE, T_PAGE, timeout)
            items.extend(more)
            if not more:
                break
        for it in items:
            nm = str(it.get("name") or "").strip()
            if not nm:
                continue
            rec = dict(it)
            rec["_kind"] = kind
            rec["_secid"] = "qt." + str(it.get("code") or "")
            out[nm] = rec
    with _STATE_LOCK:
        _STATE["spot_cache"] = (time.time(), out)
    return out


def _num(x):
    try:
        if x in (None, "", "-", "--"):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 名称匹配
# ★ 为什么需要它：东财与腾讯的**板块命名写法不同**，实测（2026-09-23）：
#     东财 `ChatGPT概念`      ↔ 腾讯 `ChatGPT`
#     东财 `元宇宙概念`        ↔ 腾讯 `元宇宙`
#     东财 `DeepSeek概念`     ↔ 腾讯 `DeepSeek概念股`
#     东财 `短剧互动游戏`      ↔ 腾讯 `短剧/互动游戏`
#     东财 `抖音概念(字节概念)` ↔ 腾讯 `抖音概念`
#   只按"逐字相等"匹配时，清洗后宇宙 427 个里只匹配上 262 个（61%）。
#   加一层归一化后明显提高（见 `match_report` 的实测数字）。
#
#   ★ 纪律：归一化匹配**只在候选唯一时才认**。
#     例如东财 `消费电子概念` 归一化成 `消费电子`，腾讯侧同时存在
#     `消费电子产业` 与 `消费电子代工` 两个归一化结果 —— 这时**放弃匹配**，
#     因为"猜一个"会把资金流贴到错的板块上，比缺一列严重得多。
_NAME_NOISE = ("概念股", "概念", "产业", "板块", "指数", "主题")
_PUNCT = "／/·、,，。.()（）[]【】 "


def norm_name(s):
    """板块名归一化：去括号内容、去标点、去通用后缀、去层级罗马数字。"""
    s = str(s or "").strip()
    s = re.sub(r"[（(][^）)]*[）)]", "", s)          # 去括号及其内容
    for ch in _PUNCT:
        s = s.replace(ch, "")
    for w in _NAME_NOISE:
        s = s.replace(w, "")
    s = re.sub(r"(Ⅲ|Ⅱ|III|II|Ⅰ|I)$", "", s)
    return s.strip()


def _build_matcher(boards):
    """建立 `名称 → 腾讯记录` 的匹配器，返回 `(resolve, stats)`。

    resolve(name) → (rec | None, 匹配方式)
    匹配方式：`exact` / `norm` / `sub` / `none` —— 如实回报，便于审计。
    """
    exact = {}
    bynorm = {}
    for nm, rec in boards.items():
        exact[nm] = rec
        bynorm.setdefault(norm_name(nm), []).append((nm, rec))

    stats = {"exact": 0, "norm": 0, "sub": 0, "none": 0, "ambig": 0}

    def resolve(name):
        nm = str(name or "").strip()
        if nm in exact:
            stats["exact"] += 1
            return exact[nm], "exact"
        key = norm_name(nm)
        if not key:
            stats["none"] += 1
            return None, "none"
        cand = bynorm.get(key)
        if cand:
            if len(cand) == 1:
                stats["norm"] += 1
                return cand[0][1], "norm"
            stats["ambig"] += 1              # 多个候选 → 放弃，不猜
            return None, "ambig"
        # 最后一级：唯一子串（严格限定"全局只有 1 个候选"才认）
        #   ★ 注意 bynorm 的值是**候选列表**，必须展平后再判唯一 ——
        #     否则会把整个列表当成"一条记录"返回（实测踩过，报
        #     `'list' object has no attribute 'get'`）。
        hits = []
        for k, cands in bynorm.items():
            if (key in k or k in key) and min(len(key), len(k)) >= 3:
                hits.extend(cands)
        if len(hits) == 1:
            stats["sub"] += 1
            return hits[0][1], "sub"
        if hits:
            stats["ambig"] += 1
            return None, "ambig"
        stats["none"] += 1
        return None, "none"

    return resolve, stats


def tencent_list_sectors(kind="industry", page_size=100):
    """返回 `[(secid, code, name, kind)]`。

    ★ secid 用 `qt.` 前缀 + 腾讯 pt 码 —— **明确标记来源**，
      不伪装成东财的 `90.BKxxxx`（伪装会导致两套数据被当成同一标的）。
    """
    want = "industry" if kind == "industry" else "concept"
    bt = "hy" if want == "industry" else "gn"
    total, first = _tencent_rank(bt, 0, T_PAGE)
    items = list(first)
    pages = min(max(0, (total + T_PAGE - 1) // T_PAGE), T_MAX_PAGES)
    for pn in range(1, pages):
        _t, more = _tencent_rank(bt, pn * T_PAGE, T_PAGE)
        items.extend(more)
        if not more:
            break
    out = []
    for it in items:
        code = str(it.get("code") or "")
        nm = str(it.get("name") or "").strip()
        if code and nm:
            out.append((f"qt.{code}", code, nm, want))
    return out


def tencent_fetch_spot_batch(secids, detail=False, names=None):
    """取"当日累计主力净流入"。

    ★ 单位换算（关键，错了整条曲线量级就错 10000 倍）：
      腾讯 `zljlr` 单位是**万元**；东财 `f62` 单位是**元**。
      本函数统一返回**元**（`zljlr × 10000`），与东财口径一致。
      实测核对：食品饮料 zljlr=72151.42（万元）= 7.2 亿，
      zllr/zllc = 108.8 亿 / 101.6 亿，与 1.96% 换手、3.56 万亿流通市值自洽。

    ★ 为什么必须传 `names`：面板的 codes 是东财 secid，腾讯只按名称给值，
      没有名称就无法把值贴回面板的列。`names=None` 时本函数**返回空 dict**
      并打印原因 —— 宁可少数据，也不猜着填。
    """
    if not secids:
        return {}
    if not names:
        print("  [source] 腾讯源需要板块名称才能映射（names=None）→ 本次不取",
              flush=True)
        return {}
    boards = _tencent_all_boards()
    resolve, stats = _build_matcher(boards)
    out = {}
    for sid in secids:
        nm = names.get(sid)
        if not nm:
            continue
        rec, _how = resolve(nm)
        if not rec:
            continue
        wan = _num(rec.get("zljlr"))
        if wan is None:
            continue
        if not detail:
            out[sid] = wan * 10000.0
            continue
        lzg = rec.get("lzg") or {}
        out[sid] = {
            "net": wan * 10000.0,
            "lead": str(lzg.get("name") or ""),
            "lead_code": str(lzg.get("code") or ""),
            "lead_pct": _num(lzg.get("zdf")),
            # ★ 腾讯没有"领跌股"字段 —— 如实留空，不拿别的字段顶上
            "lag": "",
            "lag_code": "",
        }
    with _STATE_LOCK:
        _STATE["last_match_stats"] = dict(stats)
    return out


def match_report(secids, names):
    """匹配率审计：返回 `(匹配数, 总数, 统计)`。供自检与文档引用。"""
    boards = _tencent_all_boards()
    resolve, stats = _build_matcher(boards)
    hit = 0
    for sid in secids:
        nm = names.get(sid)
        if nm and resolve(nm)[0]:
            hit += 1
    return hit, len(secids), dict(stats)


def tencent_fetch_index_trend(secid="1.000001"):
    """指数分钟分时 → `[(HH:MM, 点位, 成交量)]`。"""
    code, _label = T_INDEX_MAP.get(secid, (None, None))
    if not code:
        return []
    j = _http_json(f"https://{T_MIN_HOST}{T_MIN_PATH}?code={code}",
                   timeout=8, tries=2)
    d = ((j or {}).get("data") or {}).get(code) or {}
    arr = ((d.get("data") or {}).get("data") or [])
    out = []
    for line in arr:
        p = str(line).split()
        if len(p) < 3:
            continue
        hm = p[0]
        if len(hm) == 4:
            hm = hm[:2] + ":" + hm[2:]
        try:
            out.append((hm, float(p[1]), float(p[2])))
        except ValueError:
            continue
    return out


def tencent_fetch_minute_points(secid):
    """板块分钟分时（点位 + 成交量）。

    ★ 这是腾讯能提供的**最接近**东财"分钟曲线"的东西，但它是**点位**，
      不是**主力资金流** —— 两者语义不同，绝不能拿去填资金流曲线。
      目前仅供诊断/后续扩展，主流程不调用。
    """
    code = str(secid or "")
    if code.startswith("qt."):
        code = code[3:]
    if not code.startswith("pt"):
        return []
    j = _http_json(f"https://{T_MIN_HOST}{T_MIN_PATH}?code={code}",
                   timeout=8, tries=2)
    d = ((j or {}).get("data") or {}).get(code) or {}
    arr = ((d.get("data") or {}).get("data") or [])
    out = []
    for line in arr:
        p = str(line).split()
        if len(p) < 3:
            continue
        hm = p[0]
        if len(hm) == 4:
            hm = hm[:2] + ":" + hm[2:]
        try:
            out.append((hm, float(p[1]), float(p[2])))
        except ValueError:
            continue
    return out


def reset():
    """复位解析缓存（测试 / 运行期手动切换用）。"""
    with _STATE_LOCK:
        _STATE.update(provider=None, how="", probed_at=0.0,
                      probe_detail={}, spot_cache=None)
