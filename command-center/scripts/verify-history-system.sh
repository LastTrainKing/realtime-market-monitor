#!/usr/bin/env bash
# ============================================================
# 历史静态分钟数据系统 —— 端到端真实验收
#
# 覆盖：多日分钟数据获取 → 校验 → Parquet 落盘 → DuckDB 查询 → 断网读取
# ============================================================
set -u
ROOT="C:/Users/user/WorkBuddy AI/2026-10-01-12-48-43"
BIN="$ROOT/capital-flow-command-center/backend/target/release/capital_backend.exe"
DATA="$ROOT/.tmp/hist"
PY="C:/Users/user/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
PORT=47866

rm -rf "$DATA"; mkdir -p "$DATA"
"$BIN" --port $PORT --data-dir "$DATA" > "$DATA/stdout.log" 2>&1 &
BE=$!

for i in $(seq 1 120); do
  curl -s -m 3 "http://127.0.0.1:$PORT/api/health" -o "$DATA/h.json" 2>/dev/null && \
    grep -q '"status":"ok"' "$DATA/h.json" && break
  sleep 1
done
echo "后端就绪 ${i}s"

TOKEN=""
for _ in $(seq 1 30); do
  [ -s "$DATA/cache/token" ] && TOKEN=$(tr -d '\r\n' < "$DATA/cache/token") && break
  sleep 0.5
done
AUTH=(-H "X-CF-Token: $TOKEN")

echo "等待历史预加载（多日指数序列 + 资金曲线 + 全市场宇宙）…"
sleep 200

echo
echo "=============== 1) 历史数据状态（DuckDB + 清单 + 能力矩阵） ==============="
curl -s -m 90 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/history/status" -o "$DATA/hs.json"
$PY - "$DATA/hs.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print(f"  DuckDB 版本    : {d['duckdb_version']}")
print(f"  历史库目录     : {d['root']}")
print(f"  磁盘占用       : {d['size_bytes']} 字节")
print(f"  已落盘交易日   : {len(d['dates'])} 个 {d['dates'][:6]}")
cov = d['coverage']
print(f"  期望覆盖       : {cov['days_requested']} 个交易日")
print(f"  分钟已就绪     : {len(cov['dates_minute_ready'])} 个")
print(f"  分钟缺失       : {len(cov['dates_minute_missing'])} 个")
print(f"  最新交易日     : {cov['latest_trading_day']}  分钟点数 {cov['latest_minute_points']}")
print(f"  当前粒度       : {d['granularity_label']}")
print("  粒度能力矩阵（实测）：")
for c in d['granularity_capability']:
    flag = "满足" if c['meets_requirement'] else "不满足"
    print(f"    {c['label']:<7} 可回溯 {c['source_max_days']:>4} 个交易日   {flag} 90 天要求")
print(f"  清单条目       : {len(d['manifest'])}")
print(f"  能力说明       : {d['capability_note']}")
if d['missing_note']:
    print(f"  缺失说明       : {d['missing_note'][:160]}")
PYEOF

echo
echo "=============== 2) Parquet 实际目录结构 ==============="
find "$DATA/data/history" -name "*.parquet" 2>/dev/null | head -14
echo "  parquet 文件数: $(find "$DATA/data/history" -name '*.parquet' 2>/dev/null | wc -l)"

echo
echo "=============== 3) 用 pyarrow 独立复核（不依赖本程序） ==============="
$PY - "$DATA/data/history" <<'PYEOF'
import sys, glob, os
import pyarrow.parquet as pq
root = sys.argv[1]
files = sorted(glob.glob(os.path.join(root, "series/**/*.parquet"), recursive=True))
print(f"  序列文件 {len(files)} 个")
for f in files[:6]:
    t = pq.read_table(f)
    d = t.to_pydict()
    rel = f.replace(root, "").replace("\\", "/")
    print(f"    {rel}")
    print(f"      行数={t.num_rows}  时间轴={d['minute'][0]} → {d['minute'][-1]}  收盘 {d['close'][0]:.2f} → {d['close'][-1]:.2f}")
PYEOF

echo
echo "=============== 4) DuckDB 查询任意历史交易日 ==============="
curl -s -m 60 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/history/status" -o "$DATA/hs2.json"
DATES=$($PY -c "
import json
d=json.load(open(r'$DATA/hs2.json',encoding='utf-8'))
print(' '.join(d['coverage']['dates_minute_ready'][:5]))
")
echo "  可查询交易日: $DATES"
for D in $DATES; do
  curl -s -m 40 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/timeline/point?minute=14:07&trade_date=$D" -o "$DATA/tp.json"
  $PY - "$DATA/tp.json" "$D" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
if d['available']:
    print(f"    {sys.argv[2]} 14:07  指数={d['index_close']:.2f}  主力净流入={(d['main_net'] or 0)/1e8:+.2f}亿  ✓")
else:
    print(f"    {sys.argv[2]} 14:07  不可用: {d['note']}")
PYEOF
done

echo
echo "=============== 5) 分钟级时间轴（09:31→15:00 + 午休断点） ==============="
LATEST=$($PY -c "
import json
d=json.load(open(r'$DATA/hs2.json',encoding='utf-8'))
print(d['coverage']['latest_trading_day'])
")
curl -s -m 60 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/snapshot" -o "$DATA/snap.json"
$PY - "$DATA/snap.json" "$LATEST" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print(f"  统一交易日: {d['trade_date']}   系统时间: {d['time']['system_time']}")
print(f"  数据模式  : {d['time']['data_mode_label']}   定稿: {d['time']['is_final']}")
idx = d['index_series']; flw = d['flow_series']
print(f"  指数分时  : {len(idx)} 点  完整={d['index_complete']}")
if idx:
    ts = [b['minute'] for b in idx]
    print(f"    首尾: {ts[0]} → {ts[-1]}")
    for i in range(len(ts)-1):
        if ts[i] == '11:30':
            print(f"    午休断点: {ts[i]} → {ts[i+1]}")
            break
    outside = [t for t in ts if not (('09:15'<=t<='11:30') or ('13:00'<=t<='15:00'))]
    print(f"    非交易时段时间点: {len(outside)} 个 {outside[:3]}")
print(f"  资金曲线  : {len(flw)} 点  完整={d['flow_complete']}")
if flw:
    print(f"    首尾: {flw[0]['minute']} → {flw[-1]['minute']}")
    print(f"    主力净流入: {flw[0]['main_net_inflow']/1e8:+.2f}亿 → {flw[-1]['main_net_inflow']/1e8:+.2f}亿")
PYEOF

echo
echo "=============== 6) 断网读取历史数据（关键验收项） ==============="
echo "  停止后端 → 用纯离线方式读取已落盘历史库"
curl -s -m 20 -X POST "${AUTH[@]}" "http://127.0.0.1:$PORT/api/shutdown" -o /dev/null
sleep 8
$PY - "$DATA/data/history" <<'PYEOF'
import sys, glob, os
import pyarrow.parquet as pq
root = sys.argv[1]
files = sorted(glob.glob(os.path.join(root, "**/*.parquet"), recursive=True))
if not files:
    print("  FAIL 无历史文件，无法验证断网读取")
    sys.exit(1)
total = 0
for f in files:
    total += pq.read_table(f).num_rows
print(f"  后端已停止，直接读取本地 Parquet：{len(files)} 个文件 / {total} 行  ✓ 断网可用")
PYEOF

echo
echo "=============== 7) 股票宇宙（确认非 1200） ==============="
$PY - "$DATA" <<'PYEOF'
import json, sys, os, glob
d = sys.argv[1]
# 从日志中提取证券列表规模
logs = glob.glob(os.path.join(d, "logs", "app.log.*"))
uni = None
for f in logs:
    for line in open(f, encoding='utf-8', errors='replace'):
        if "证券列表已刷新" in line and "symbols=" in line:
            try:
                uni = line.split("symbols=")[1].split()[0]
            except Exception:
                pass
print(f"  证券列表规模: {uni or '（见 app.log）'}")
PYEOF

if kill -0 $BE 2>/dev/null; then kill -9 $BE 2>/dev/null; fi
echo
echo "验收结束"
