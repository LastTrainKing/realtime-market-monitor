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


def clean_universe(meta, keep_kinds=("industry", "concept")):
    """
    返回 (codes, names, kinds, dropped) —— 清洗后的板块宇宙。
    行业层：只保留 PRIMARY_INDUSTRIES 白名单。
    概念层：去掉统计桶。
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
