#!/usr/bin/env bash
# 验证"历史静态数据 + 全市场宇宙 + 统一快照"修复是否真正生效
set -u
ROOT="C:/Users/user/WorkBuddy AI/2026-10-01-12-48-43"
BIN="$ROOT/capital-flow-command-center/backend/target-check/release/capital_backend.exe"
DATA="$ROOT/.tmp/verify2"
PY="C:/Users/user/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
PORT=47855

rm -rf "$DATA"; mkdir -p "$DATA"
"$BIN" --port $PORT --data-dir "$DATA" > "$DATA/stdout.log" 2>&1 &
BE=$!

for i in $(seq 1 120); do
  if curl -s -m 3 "http://127.0.0.1:$PORT/api/health" -o "$DATA/h.json" 2>/dev/null; then
    grep -q '"status":"ok"' "$DATA/h.json" && break
  fi
  sleep 1
done
echo "后端就绪耗时 ${i}s"

TOKEN=""
for _ in $(seq 1 30); do
  [ -s "$DATA/cache/token" ] && TOKEN=$(tr -d '\r\n' < "$DATA/cache/token") && break
  sleep 0.5
done
AUTH=(-H "X-CF-Token: $TOKEN")

# 等待全市场采集与历史预加载完成
echo "等待全市场采集与历史预加载…"
sleep 200

echo
echo "================= 1) 全市场股票宇宙 ================="
curl -s -m 60 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/market/breadth" -o "$DATA/b.json"
$PY -c "
import json,io
d=json.load(io.open(r'$DATA/b.json',encoding='utf-8'))
b=d['data']
print(f'  口径 scope      : {b.get(\"scope\")}')
print(f'  股票宇宙 total  : {b.get(\"total_symbols\")}')
print(f'  已加载 loaded   : {b.get(\"sample_size\")}')
print(f'  缺失 missing    : {b.get(\"missing_symbols\")}')
print(f'  是否完整        : {b.get(\"complete\")}')
print(f'  上涨/下跌/平盘  : {b[\"up_count\"]}/{b[\"down_count\"]}/{b[\"flat_count\"]}')
print(f'  涨停/跌停       : {b[\"limit_up_count\"]}/{b[\"limit_down_count\"]}')
print(f'  成交额          : {b[\"total_amount\"]/1e8:.2f} 亿')
print(f'  状态            : {d[\"status\"]} / {d[\"source_label\"]}')
"

echo
echo "================= 2) 统一数据快照 ================="
curl -s -m 120 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/snapshot" -o "$DATA/snap.json"
$PY -c "
import json,io
d=json.load(io.open(r'$DATA/snap.json',encoding='utf-8'))
t=d['time']
print(f'  系统时间        : {t[\"system_time\"]}')
print(f'  数据交易日      : {t[\"trade_date\"]}')
print(f'  行情时间        : {t[\"data_timestamp\"]}')
print(f'  数据模式        : {t[\"data_mode\"]} / {t[\"data_mode_label\"]}')
print(f'  是否定稿        : {t[\"is_final\"]}')
print(f'  数据源连接      : {t[\"source_link_label\"]}')
print(f'  说明            : {t[\"note\"]}')
print(f'  指数分时点数    : {len(d[\"index_series\"])}  完整={d[\"index_complete\"]}')
print(f'  资金曲线点数    : {len(d[\"flow_series\"])}  完整={d[\"flow_complete\"]}')
if d['index_series']:
    a=d['index_series']
    print(f'    指数时间轴    : {a[0][\"minute\"]} → {a[-1][\"minute\"]}  收 {a[0][\"close\"]} → {a[-1][\"close\"]}')
if d['flow_series']:
    f=d['flow_series']
    print(f'    资金时间轴    : {f[0][\"minute\"]} → {f[-1][\"minute\"]}  主力 {f[0][\"main_net_inflow\"]/1e8:.2f}亿 → {f[-1][\"main_net_inflow\"]/1e8:.2f}亿')
print(f'  历史库就绪      : {len(d[\"coverage\"][\"dates_minute_ready\"])} / {d[\"coverage\"][\"days_requested\"]}')
print(f'  能力说明        : {d[\"capability_note\"]}')
print(f'  缺失说明        : {d[\"missing_note\"] or \"（无缺失）\"}')
"

echo
echo "================= 3) 时点数据面板（与曲线同源） ================="
for M in 09:31 10:30 14:07 15:00; do
  curl -s -m 60 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/timeline/point?minute=$M" -o "$DATA/tp.json"
  $PY -c "
import json,io
d=json.load(io.open(r'$DATA/tp.json',encoding='utf-8'))
if d['available']:
    print(f'  {d[\"minute\"]}  指数={d[\"index_close\"]:.2f}  主力净流入={d[\"main_net\"]/1e8:+.2f}亿  交易日={d[\"trade_date\"]}')
else:
    print(f'  {d[\"minute\"]}  不可用: {d[\"note\"]}')
"
done

echo
echo "================= 4) 板块资金流入/流出对比 ================="
curl -s -m 60 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/sector/compare" -o "$DATA/sc.json"
$PY -c "
import json,io
d=json.load(io.open(r'$DATA/sc.json',encoding='utf-8'))
print(f'  交易日: {d[\"trade_date\"]}  状态: {d[\"status\"]}')
print('  流入 TOP5:', [(x['name'], x['value_label']) for x in d['inflow_top'][:5]])
print('  流出 TOP5:', [(x['name'], x['value_label']) for x in d['outflow_top'][:5]])
print('  说明:', d['note'])
"

echo
echo "================= 5) 榜单是否基于完整股票宇宙 ================="
curl -s -m 60 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/boards" -o "$DATA/bo.json"
$PY -c "
import json,io
d=json.load(io.open(r'$DATA/bo.json',encoding='utf-8'))
for b in d['data']:
    top = b['entries'][0]['name'] if b['entries'] else '（空）'
    print(f'  {b[\"name\"]:<10} 条目{len(b[\"entries\"]):>3}  榜首 {top}')
"

echo
echo "================= 6) 历史库 Parquet 落盘 ================="
find "$DATA/data/history" -name "*.parquet" 2>/dev/null | head -10
echo "  文件数: $(find "$DATA/data/history" -name '*.parquet' 2>/dev/null | wc -l)"
echo "  占用: $(du -sh "$DATA/data/history" 2>/dev/null | cut -f1)"

echo
echo "================= 7) 优雅关闭 ================="
curl -s -m 15 -X POST "${AUTH[@]}" "http://127.0.0.1:$PORT/api/shutdown" -o /dev/null
sleep 8
BODY=$(curl -s -m 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null || true)
if printf '%s' "$BODY" | grep -q '"status":"ok"'; then
  echo "  FAIL 后端仍在响应"; kill -9 $BE 2>/dev/null
else
  echo "  OK   后端已退出"
fi
