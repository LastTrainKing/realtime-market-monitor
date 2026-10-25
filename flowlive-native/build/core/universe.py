# -*- coding: utf-8 -*-
"""
universe.py —— 板块宇宙清洗

东财返回的 1000 个"板块"里，真正代表**可交易主题**的只是一部分，其余是统计桶：
  · 宽基/风格指数：HS300、上证380、中证500、MSCI中国、富时罗素、沪股通、融资融券 …
  · 财报事件标签：2026中报预增、2025三季报扭亏 …
  · 情绪/技术标签：昨日涨停、连板、破净股、百日新高、微盘股 …
  · 持股属性标签：机构重仓、QFII重仓、社保重仓、证金持股 …
  · 行业层级重复：汽车 → 汽车零部件 → 底盘与发动机系统（三级嵌套，重复计资金）

这些桶的问题是：成分股覆盖大半个市场，**结构性常年净流出/净流入**，会污染辨识度排序。
把它们剔掉，是"选手"看盘时第一步的领域常识 —— 这一步做成规则，就是可复现的能力。
"""
from __future__ import annotations

import re

# 统计桶 / 非主题标签（正则，任一命中即剔除）
DROP_PATTERNS = [
    # 宽基指数与互联互通
    r"融资融券", r"沪股通", r"深股通", r"MSCI", r"富时罗素", r"标准普尔",
    r"^AH股$", r"^GDR$", r"^AB股$", r"^B股$", r"HS300", r"^上证\d+", r"^深证\d+",
    r"^中证\d+", r"^深成\d+", r"创业板综", r"^央视\d+", r"^科创\d+", r"^北证\d+",
    # 财报事件
    r"^20\d\d(一季报|中报|三季报|年报)",
    # 情绪 / 技术形态
    r"昨日", r"连板", r"涨停", r"炸板", r"触板", r"首板", r"高换手", r"高振幅",
    r"新高", r"破净", r"破发", r"超跌", r"反转股", r"趋势股", r"最近多板",
    # 市值 / 风格桶
    r"微盘", r"^小盘", r"^大盘", r"^中盘", r"价值股", r"成长股", r"红利",
    r"权重股", r"低价股", r"百元股", r"微利股", r"高市净率", r"低市净率",
    r"风格$", r"茅指数", r"宁组合", r"行业龙头", r"专精特新", r"东方财富热股",
    r"超级品牌", r"稀缺资源", r"^中字头$",
    # 持股 / 资金属性
    r"机构重仓", r"基金重仓", r"QFII", r"社保重仓", r"证金持股", r"养老金",
    r"北向", r"举牌", r"股权集中", r"股权分散", r"股权激励", r"股权转让",
    r"密集调研", r"券商金股", r"转债标的", r"IPO受益", r"独角兽", r"^创投$",
    r"参股(银行|券商|保险|期货|新三板)",
    # 其他非主题
    r"^ST股$", r"近期摘帽", r"次新股",
]
_DROP_RE = [re.compile(p) for p in DROP_PATTERNS]

# ★ v1.2.1 性能：把 54 条规则**合并成一个交替式**，每个板块名只跑 1 次 search。
#
#   原实现 `any(r.search(name) for r in _DROP_RE)` 对每个名字最多试 54 个正则 ——
#   1000 个板块就是 **3.5 万次** re.search（cProfile 实测占 build_features
#   约 16ms / 50ms，是向量化 _pct_rank 之后的头号热点）。
#
#   合并是否安全：`search` 的语义是"任意位置命中即为真"，
#   而 `A|B|C` 的 search 正是"任一条分支在任意位置命中" —— 两者等价。
#   ★ 分支内的 `^` / `$` 锚点在交替式里仍然**各自生效**（不是整串锚定）：
#     例如 `^AH股$` 这一支依然只匹配"整个字符串恰好等于 AH股"。
#   等价性由 _verify_re.py 对全部 1000 个真实板块名逐条比对把关。
_DROP_ALT = re.compile("|".join(f"(?:{p})" for p in DROP_PATTERNS))

# 行业层只保留一级行业（白名单）。
# 原因：东财 t:2 把 一级/二级/三级 行业全塞进来了（496 个），
# 「汽车」=「汽车零部件」+「乘用车」+… 的父集，混在一起会重复计资金。
# 层级细分交由概念层承担（如"半导体概念""光通信模块"）。
PRIMARY_INDUSTRIES = {
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车", "家用电器",
    "食品饮料", "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输",
    "房地产", "商贸零售", "社会服务", "综合", "建筑材料", "建筑装饰",
    "电力设备", "国防军工", "计算机", "传媒", "通信", "银行", "非银金融",
    "煤炭", "石油石化", "环保", "美容护理", "机械设备",
}


def is_statistical_bucket(name: str) -> bool:
    return _DROP_ALT.search(name) is not None


#: 「过滤前提失效」的退化判据（见 clean_universe 的说明）。
#  两个条件必须**同时**满足才退化：
#    · 面板本身够大（否则"只有 5 个板块"就是它的正常样子）
#    · 清洗后既绝对少（< MIN_KEEP）又占比低（< MIN_RATIO）
UNIVERSE_MIN_TOTAL = 60         # 面板至少这么大才谈得上"被截断"
UNIVERSE_MIN_KEEP = 40          # 清洗后少于这个数 → 太少
UNIVERSE_MIN_RATIO = 0.25       # 且占比低于这个比例

#: **行业层白名单**自己的退化判据（与上面的"总数判据"并列，两者都要有）。
#
#  ★★ 为什么必须**分层**判 —— 实测（2026-09-23 18:30）踩到的坑：
#    老面板被截断成 122 个板块（全是二/三级行业）之后，程序按"并入替代源板块"
#    把概念补了进来，面板变成 956 列、清洗后剩 795 列 ——
#    于是上面那条**总数判据不再成立**（795 既不"绝对少"也不"占比低"）。
#    可是行业层里仍然只有 **5 个**一级行业（其余 148 个是二/三级行业，被白名单丢掉）。
#    后果：扇形图找不到"有历史的概念"（概念是刚并进来的，只有 1 个点），
#    退回到"有历史的行业板块"时**只有 5 条曲线** ——
#    界面上仍然是空的，而日志里看不出哪里不对。
#
#  ★ 结论：**总数够多会掩盖某一层的过滤失效**，所以判据必须分层。
#    这里只对"行业层"加判据；概念层用的是统计桶规则（与板块多少无关），
#    不存在同类问题。
INDUSTRY_MIN_KEEP = 15          # 一级行业白名单至少留下这么多
INDUSTRY_MIN_RATIO = 0.25       # 且不低于该层行业数的这个比例

#: 一列要被当作"有可用历史"，至少要有这么多有效分钟点。
#  ★ 为什么必须有这个门槛（实测 2026-09-23 18:35 的界面全空就是它引起的）：
#    `salience.build_features` 里有一步**市场中性化**：
#        resid = dV5 - dV5.mean(axis=1, keepdims=True)
#    只要某个时刻**任意一列**是 NaN，该时刻的均值就是 NaN ——
#    于是 NaN 会通过这一行**污染全部板块**。
#    实测：956 列的面板里 834 列只有"当前 1 个点"（替代源并入的），
#    结果 `F.V` 只剩 **1.0%** 有限值、`dV5.mean(axis=1)` 只剩 **2.1%**，
#    界面自然什么都画不出来 —— 而面板里其实躺着 122 列**完整的 240 分钟真实数据**。
#  ★ 所以"没有可用历史的列"必须在进入分析**之前**就剔除，
#    而不是留给下游用 NaN 自己消化。
HIST_MIN_POINTS = 10


def clean_universe(meta, keep_kinds=("industry", "concept"), hist=None):
    """
    返回 (codes, names, kinds, dropped) —— 清洗后的板块宇宙。
    行业层：只保留 PRIMARY_INDUSTRIES 白名单。
    概念层：去掉统计桶。

    hist: 可选 `{code: 有效分钟点数}`。★ v3.0.6 新增，见文件末尾两段说明 ——
          传了它才能识别"**列在、但历史不在**"这种状态（并入替代源板块之后
          就是这种状态：板块名齐全、可只有当前一个点）。

    ★★ v3.0.6：**过滤前提失效时必须退化，而不是把界面清空**。
      实测事故（2026-09-23）：本机 IP 被东财封禁，全量抓取只取到 **122** 个
      板块就写盘（抓取顺序是"行业在前"，被截断的这 122 个**几乎全是二/三级
      行业**）。而本函数的行业层只保留 31 个**一级**行业 →
      清洗后**只剩 5 个**（房地产/汽车/建筑装饰/美容护理/钢铁）。
      后果：`salience.build_features` 的 `F.codes` 只有 5 个，
      界面上"就绪 · 5 个板块"，辨识度榜/题材簇/主角更替/阶段对比**全部空掉**。
      ★ 而那份面板其实有 **240 分钟的真实数据** —— 数据是好的，
        坏的是"过滤前提"（假定面板含完整 1000 个板块）不成立了。

      【判据】清洗后同时满足"绝对数很少"与"占比很低"才退化 ——
        两个条件都要，避免把"本来就只关心少量板块"的正常场景误判。
      【退化行为】**不做过滤**（原样返回面板里的全部板块），
        并打印一条明确警告 + 在 `dropped` 里留 `退化_不过滤` 标记，
        让"为什么今天板块变少了/变多了"在日志里查得到。
    """
    names, kinds = meta["names"], meta["kinds"]
    codes = list(meta["codes"])
    kept, dropped = [], {"行业层级": 0, "统计桶": 0, "类型过滤": 0}
    for c in codes:
        n = names[c]
        k = kinds[c]
        if k not in keep_kinds:
            dropped["类型过滤"] += 1
            continue
        if k == "concept":
            if is_statistical_bucket(n):
                dropped["统计桶"] += 1
                continue
        elif k == "industry":
            if n not in PRIMARY_INDUSTRIES:
                dropped["行业层级"] += 1
                continue
        kept.append(c)

    # ---- 过滤前提失效 → 退化为不过滤（v3.0.6）----
    if (len(codes) >= UNIVERSE_MIN_TOTAL
            and len(kept) < UNIVERSE_MIN_KEEP
            and len(kept) < len(codes) * UNIVERSE_MIN_RATIO):
        print(f"  [universe] 注意： 清洗后仅剩 {len(kept)}/{len(codes)} 个板块"
              f"（占比 {len(kept) * 100 // len(codes)}%）—— 过滤前提不成立"
              f"（面板很可能被截断，只含板块清单的前一段）。"
              f"**退化为不做过滤**，如实使用面板里的全部板块。", flush=True)
        dropped["退化_不过滤"] = len(codes) - len(kept)
        kept = [c for c in codes if kinds.get(c) in keep_kinds]

    # ---- 行业层白名单失效 → **只把行业层**退化为不过滤（v3.0.6）----
    #   详见 INDUSTRY_MIN_KEEP 的说明：总数判据会被"概念补全"掩盖，
    #   必须单独看行业层。放在总数判据之后：若总数判据已退化，
    #   此时 kept 已含全部行业，本判据自然不会重复触发。
    ind_all = [c for c in codes if kinds.get(c) == "industry"]
    _kept_set = set(kept)
    ind_kept = [c for c in ind_all if c in _kept_set]
    if (len(ind_all) >= UNIVERSE_MIN_TOTAL
            and len(ind_kept) < INDUSTRY_MIN_KEEP
            and len(ind_kept) < len(ind_all) * INDUSTRY_MIN_RATIO):
        print(f"  [universe] 注意： 行业层白名单只留下 {len(ind_kept)}/{len(ind_all)} 个"
              f"（占比 {len(ind_kept) * 100 // max(1, len(ind_all))}%）—— "
              f"说明面板里的行业**不是**完整的一级行业集（多半被截断成"
              f"二/三级行业）。**行业层退化为不过滤**，补回全部行业板块；"
              f"★ 代价：细分行业与其父行业可能重复计入，"
              f"这是「看得见数据」与「口径最纯」之间的取舍，此处选前者。",
              flush=True)
        ind_extra = [c for c in ind_all if c not in _kept_set]
        dropped["行业层退化_不过滤"] = len(ind_extra)
        kept = kept + ind_extra

    # ---- 行业层：白名单留下的行业**没有可用历史** → 补回"有历史"的细分行业 ----
    #   详见 INDUSTRY_MIN_KEEP / HIST_MIN_POINTS 的说明。这是"分层判据"的第二半：
    #   前一段看**数量**，这一段看**历史**。实测（2026-09-23）面板并入替代源板块
    #   之后，一级行业有 36 个（数量看着正常），但**只有 5 个有分钟历史**，
    #   其余 31 个是刚并进来的单点列 —— 只看数量完全看不出来。
    if hist is not None and len(ind_all) >= UNIVERSE_MIN_TOTAL:
        _ind_hist = [c for c in ind_kept if hist.get(c, 0) >= HIST_MIN_POINTS]
        if len(_ind_hist) < INDUSTRY_MIN_KEEP:
            _extra = [c for c in ind_all
                      if c not in set(kept) and hist.get(c, 0) >= HIST_MIN_POINTS]
            if _extra:
                print(f"  [universe] 注意： 一级行业里只有 {len(_ind_hist)} 个有可用历史"
                      f"（≥{HIST_MIN_POINTS} 点）—— 白名单留下的行业大多是"
                      f"「有名字没历史」的并入列。**补回 {len(_extra)} 个有历史的"
                      f"细分行业**，让时间序列分析有东西可用。", flush=True)
                dropped["行业层补回有历史"] = len(_extra)
                kept = kept + _extra

    # ---- 丢掉"没有可用历史"的列（必须在进分析之前，见 HIST_MIN_POINTS）----
    if hist is not None:
        _before = len(kept)
        kept = [c for c in kept if hist.get(c, 0) >= HIST_MIN_POINTS]
        if _before - len(kept):
            dropped["无历史剔除"] = _before - len(kept)
    return kept, names, kinds, dropped


def describe_drop(meta):
    kept, names, kinds, dropped = clean_universe(meta)
    n_ind = sum(1 for c in kept if kinds[c] == "industry")
    n_con = sum(1 for c in kept if kinds[c] == "concept")
    return {
        "kept": len(kept), "industry": n_ind, "concept": n_con,
        "dropped": dropped, "total": len(meta["codes"]),
        "kept_codes": kept,
    }
