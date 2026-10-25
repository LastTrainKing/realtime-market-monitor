# -*- coding: utf-8 -*-
"""v2.2.0 面板（孙模块）普查。

口径：`make_card(` / `make_labeled_table(` 各算一个面板（Panel）。
归属：`addTab(w, "子页名")` 之前、上一个 addTab 之后的代码块，属于该子页
     （即"先建控件、后 addTab"的写法）。
循环展开：3 节点 × 2 指标 = 6（开盘检测）；3 节点 × 3 指标 = 9（个股异动）。
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "app_main_v203.py"), encoding="utf-8") as f:
    lines = f.readlines()

ADD = re.compile(r"\.addTab\(\s*w\s*,\s*\"([^\"]+)\"")
SUBTAB_ADD = re.compile(r'addTab\(\s*w\s*,\s*"(实时榜单|题材簇)"')
TOP_OVERVIEW = re.compile(r'self\.tabs\.addTab\(page,\s*"总览"\)')
CARD = re.compile(r"\bmake_card\(")
LABELED = re.compile(r"\bmake_labeled_table\(")

# 模块级工厂函数的定义体不是 UI 面板 —— 排除（L3943/3947/4002/4005）
SKIP_LINES = {3943, 3947, 4002, 4005}

# 手工确认的循环展开表（行号 -> 展开数）
EXPAND = {
    4238: 1,   # 总览 主力资金累计曲线
    4273: 1,   # 市场全景 指数分时
    4279: 1,   # 市场全景 流入/流出对比
    4294: 1,   # 市场全景 区间极值·吸筹
    4303: 1,   # 市场全景 区间极值·出货
    4346: 3,   # 开盘检测 成交量Top5 × 3 节点
    4356: 3,   # 开盘检测 主力净流入Top5 × 3 节点
    4408: 9,   # 个股异动 3 节点 × 3 指标
    4426: 1,   # 个股精选
    4481: 1,   # 异动雷达·流入
    4489: 1,   # 异动雷达·流出
    4498: 1,   # 辨识度榜·流入
    4503: 1,   # 辨识度榜·流出
    4575: 1,   # 行业概况·流入
    4580: 1,   # 行业概况·流出
    4560: 1,   # 题材簇 题材簇预览
    4569: 1,   # 题材簇 龙头领先反应
    4611: 1,   # 实时事件
    4621: 1,   # 主角更替
    4629: 1,   # 阶段对比
    4642: 1,   # 关键节点
    4679: 1,   # 实时榜单 累积池
    4708: 1, 4718: 1, 4728: 1, 4738: 1,   # 实时榜单 四个当下榜
}

# 归属分段：以 addTab 行号为界
bounds = []          # [(lineno, subname)]
for i, ln in enumerate(lines, 1):
    m = ADD.search(ln)
    if m:
        bounds.append((i, m.group(1)))
        continue
    if TOP_OVERVIEW.search(ln):
        bounds.append((i, "总览"))
        continue
    m = SUBTAB_ADD.search(ln)
    if m:
        bounds.append((i, m.group(1)))

# 手工纠正：4346/4356/4408 等位于 addTab 之前的块（先建后挂）
# 判定规则：取**下一个** addTab 作为归属（因为代码是"建完才 addTab"）
def owner(lineno):
    for i, nm in bounds:
        if lineno < i:
            return nm
    return None

panels = {}
unknown = []
for i, ln in enumerate(lines, 1):
    if i in SKIP_LINES:
        continue
    if CARD.search(ln) or LABELED.search(ln):
        nm = owner(i)
        n = EXPAND.get(i, 1)
        if nm is None:
            unknown.append((i, ln.rstrip()[:70]))
            continue
        panels.setdefault(nm, []).append((i, n))

ORDER = [
    ("总览", []),
    ("大盘", ["市场全景", "开盘检测"]),
    ("个股", ["个股异动", "个股精选"]),
    ("资金排行", ["异动雷达", "辨识度榜", "行业概况", "实时榜单"]),
    ("板块题材", ["题材簇"]),
    ("盘中复盘", ["实时事件", "主角更替", "阶段对比", "关键节点"]),
]

print("=" * 80)
print("v2.2.0 面板（孙模块）普查")
print("=" * 80)
total = 0
sub_n = 0
for g, subs in ORDER:
    if not subs:
        ps = panels.get(g, [])
        c = sum(n for _l, n in ps)
        total += c
        print("\n【%s】%d 个面板（无子页）" % (g, c))
        for l, n in ps:
            print("      L%-5d %s×%d" % (l, "面板", n))
        continue
    print("\n【%s】" % g)
    for s in subs:
        sub_n += 1
        ps = panels.get(s, [])
        c = sum(n for _l, n in ps)
        total += c
        print("   └─ %-6s %2d 个面板    %s" % (
            s, c, "  ".join("L%d×%d" % (l, n) for l, n in ps)))

if unknown:
    print("\n⚠ 未归属：")
    for l, t in unknown:
        print("   L%d  %s" % (l, t))

print("\n" + "=" * 80)
print("面板（孙模块）合计：%d" % total)
print("母模块 %d · 子模块 %d" % (len(ORDER), sub_n))
print("=" * 80)
