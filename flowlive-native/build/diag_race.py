# -*- coding: utf-8 -*-
"""证明 load_panel 的「先 json 后 npy」非原子读 + refresh 的「先 npy 后 json」
   非原子写，会造出 times=50 但 mat=51 行的错配 → 曲线尾部续假序号 → 横线。

   模拟真实时序（不加锁、不重试）：
     T0  UI 线程: json.load → times = 50
     T1  刷新线程: np.save(npy, 51行)      ← 写进去了
     T2  UI 线程: np.load(npy) → 51 行     ← 拿到新矩阵、旧 times
     T3  刷新线程: json.dump(times=51)
"""
import json, os, tempfile
import numpy as np

d = tempfile.mkdtemp()
p_json = os.path.join(d, "panel_t.json")
p_npy = os.path.join(d, "panel_t.npy")

times = [f"09:{31+i:02d}" for i in range(50)]     # 09:31..10:20
mat = np.random.rand(50, 5).cumsum(axis=0)
json.dump({"times": times, "codes": [f"BK{i}" for i in range(5)]},
          open(p_json, "w", encoding="utf-8"), ensure_ascii=False)
np.save(p_npy, mat)

# ---- 真实时序的「交错」执行 ----
# 步骤 1：UI 线程读 json
meta_read = json.load(open(p_json, encoding="utf-8"))
print("UI read json times =", len(meta_read["times"]))

# 步骤 2：刷新线程 np.save 了 51 行（此时 json 还没写）
mat51 = np.vstack([mat, mat[-1] + 1.0])
np.save(p_npy, mat51)

# 步骤 3：UI 线程读 npy
mat_read = np.load(p_npy)

# 步骤 4：刷新线程才写 json（UI 已经用不上了）
json.dump({"times": times + ["10:21"], "codes": meta_read["codes"]},
          open(p_json, "w", encoding="utf-8"), ensure_ascii=False)

print("UI read npy shape =", mat_read.shape)
T, N = mat_read.shape
print(">>> F.times 长度 =", len(meta_read["times"]), " F.V 行数 =", T)
assert T > len(meta_read["times"]), "本次没构造出错配，时序需调整"
print(">>> 错配成立：V 比 times 多", T - len(meta_read["times"]), "行")

# ---- 用真实 _x_positions 逻辑看结果 ----
def _minute_index(hm):
    try:
        h, m = str(hm).split(":")[:2]
        t = int(h) * 60 + int(m)
    except Exception:
        return None
    if 9 * 60 + 31 <= t <= 11 * 60 + 30:
        return t - (9 * 60 + 31)
    if 13 * 60 + 1 <= t <= 15 * 60:
        return 120 + (t - (13 * 60 + 1))
    if t == 9 * 60 + 30:
        return 0
    return None


def _minute_index_clamped(hm):
    v = _minute_index(hm)
    if v is not None:
        return v
    try:
        h, m = str(hm).split(":")[:2]
        t = int(h) * 60 + int(m)
    except Exception:
        return None
    if t < 9 * 60 + 31:
        return 0
    if t < 13 * 60 + 1:
        return 119
    return 239


def _x_positions(times, n):
    if n <= 0:
        return [], False
    if not times:
        return list(range(n)), False
    raw = [_minute_index_clamped(t) for t in times[:n]]
    if len(raw) < n:
        raw = raw + [None] * (n - len(raw))
    if not any(v is not None for v in raw):
        return list(range(n)), False
    k0 = next(i for i, v in enumerate(raw) if v is not None)
    base = raw[k0]
    for i in range(k0 - 1, -1, -1):
        base = max(0, base - 1)
        raw[i] = base
    out, prev = [], None
    for v in raw:
        if v is None:
            v = (prev + 1) if prev is not None else 0
        elif prev is not None and v <= prev:
            v = prev + 1
        out.append(int(v))
        prev = v
    return out, True


# 渲染层：times 用的是 F.times（50 个），n 取自 series 长度（51）
n = T
xpos, mapped = _x_positions(meta_read["times"], n)
print("n =", n, " len(xpos) =", len(xpos))
print("xpos[-5:] =", xpos[-5:], "  ← 尾部被续成 46,47,48,49,50")

# _fill_gaps 端值外推：末值重复
s = mat_read[:, 0]
print("series 末 5 值 =", [round(float(x), 3) for x in s[-5:]])

# 结果：曲线画到 x=50（约 10:21 之后继续外推），而真实数据只到 x=49
print(">>> 曲线终点 x =", xpos[-1], " y =", round(float(s[-1]), 3))
print(">>> 若错配更大（例如 240 vs 50）会一路续到 239(15:00) → 平直横线")
