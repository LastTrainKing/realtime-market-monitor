# -*- coding: utf-8 -*-
"""验证 v3.0.4 采集并行化的效果与正确性。

两件事：
  A. 正确性 —— 并行结果必须与串行**逐项一致**（不能因为并发丢数据/串数据）
  B. 提速   —— 同等人造 RTT 下，并行 vs 串行的耗时对比
"""
import os
import sys
import time
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "core"))
# ★★ v3.0.6：本用例验证的是**东财通道**的并行分片行为，必须把数据源钉死在
#   东财 —— 否则在"东财被限流"的机器上 core/source.py 的 auto 会切到腾讯，
#   `fetch_spot_batch` 走腾讯分支（返回空），本用例会全线失败。
#   这不是测试将就实现，而是"被测对象必须被明确指定"这一基本要求。
os.environ.setdefault("FLOWLIVE_SOURCE", "eastmoney")
import flowdata                                                          # noqa: E402

PASS = 0
FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


RTT = 0.060
_orig = flowdata._get
seq_log = {"n": 0}


def _stub_factory(counter):
    def _stub(path_or_url, tries=3, timeout=15):
        time.sleep(RTT)
        # 从 URL 解析出本分片包含哪些 secid，逐条返回 —— 这样能校验"数据有没有串"
        import re
        m = re.search(r"secids=([^&]+)", path_or_url)
        ids = m.group(1).split(",") if m else []
        counter["n"] += 1
        diff = []
        for k, sid in enumerate(ids):
            code = sid.split(".")[-1]
            diff.append({"f12": code, "f14": "n" + code,
                         "f62": 1.0e6 * (len(code) + k),
                         "f128": "A" + code, "f140": code,
                         "f136": 1.5, "f207": "B" + code, "f208": code})
        return {"data": {"diff": diff}}
    return _stub


codes = ["90.BK%04d" % (1000 + i) for i in range(1000)]

print("=" * 70)
print("A. 正确性：并行结果 == 串行结果")
print("=" * 70)
c1 = {"n": 0}
flowdata._get = _stub_factory(c1)
serial = flowdata.fetch_spot_batch(codes, detail=True, workers=1)
c2 = {"n": 0}
flowdata._get = _stub_factory(c2)
parallel = flowdata.fetch_spot_batch(codes, detail=True, workers=6)
flowdata._get = _orig

check(len(serial) == len(parallel), "键数一致",
      "串行 %d / 并行 %d" % (len(serial), len(parallel)))
check(set(serial) == set(parallel), "键集合一致")
# 逐项比对（深比较）
mismatch = [k for k in serial if serial[k] != parallel.get(k)]
check(not mismatch, "逐项值一致", "不一致 %d 项" % len(mismatch))
if mismatch[:3]:
    print("     样例:", [(k, serial[k], parallel.get(k)) for k in mismatch[:3]])
check(c1["n"] == c2["n"], "请求数一致",
      "串行 %d / 并行 %d" % (c1["n"], c2["n"]))

print()
print("=" * 70)
print("B. 提速：多轮取样取中位")
print("=" * 70)


def timeit(workers, rounds=5):
    ts = []
    c = {"n": 0}
    flowdata._get = _stub_factory(c)
    for _ in range(rounds):
        t0 = time.time()
        flowdata.fetch_spot_batch(codes, detail=True, workers=workers)
        ts.append((time.time() - t0) * 1000)
    flowdata._get = _orig
    return statistics.median(ts), c["n"]


t1, n1 = timeit(1)
t3, n3 = timeit(3)
t6, n6 = timeit(6)
print("  workers=1 (串行/现状)   %8.1f ms   n=%d" % (t1, n1))
print("  workers=3               %8.1f ms   n=%d   %.2fx" % (t3, n3, t1 / t3))
print("  workers=6 (新默认)      %8.1f ms   n=%d   %.2fx" % (t6, n6, t1 / t6))
print()
print("  理论下限（1×RTT + 开销） %8.1f ms" % (RTT * 1000))

check(t6 < t1 * 0.5, "并行至少 2× 提速",
      "%.1fms -> %.1fms (%.2fx)" % (t1, t6, t1 / t6))
check(t6 < t1 * 0.3, "并行达到 3× 以上提速",
      "%.2fx" % (t1 / t6))

print()
print("=" * 70)
print("C. 边界：小规模不启线程池")
print("=" * 70)
c = {"n": 0}
flowdata._get = _stub_factory(c)
small = flowdata.fetch_spot_batch(codes[:50], detail=True)
flowdata._get = _orig
check(len(small) == 50, "50 个 secid 单分片正确", "返回 %d" % len(small))
check(c["n"] == 1, "只发 1 次请求", "实际 %d" % c["n"])

c = {"n": 0}
flowdata._get = _stub_factory(c)
empty = flowdata.fetch_spot_batch([], detail=True)
flowdata._get = _orig
check(empty == {} and c["n"] == 0, "空输入不发请求")

print()
print("=" * 70)
print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 70)
sys.exit(1 if FAIL else 0)
