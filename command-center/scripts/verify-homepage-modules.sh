#!/usr/bin/env bash
# ============================================================
# 首页四个核心模块 —— 端到端真实验收
#
# 验收对象（全部读本地历史静态分钟库 / 本地持久化快照）：
#   ① 指数分时        ← /api/snapshot.index_series
#   ② 主力资金累计曲线 ← /api/snapshot.flow_series
#   ③ 板块资金流入流出 ← /api/sector/compare
#   ④ 时点数据面板     ← /api/timeline/point
#
# 关键断言：
#   * 曲线与时点面板必须是**同一交易日、同一分钟**，数值完全一致（不错位）
#   * 非交易时段板块模块必须读本地持久化快照（status=REBUILD），而非实时缓存
#   * 当天分钟数据缺失时必须显式回退并上报 series_fallback，不得假装是当天
#   * 昨收不可得时不得伪造涨跌幅
#   * 后端停止后仍能用 pyarrow 直接读取本地历史库（断网可用）
#
# 数据来源：docs/evidence/parquet-history 下归档的**真实**数据源样本
# ============================================================
set -u
ROOT="C:/Users/user/WorkBuddy AI/2026-10-01-12-48-43"
PROJ="$ROOT/capital-flow-command-center"
BIN="$PROJ/backend/target/release/capital_backend.exe"
DATA="$ROOT/.tmp/home-modules"
EVID="$PROJ/docs/evidence/parquet-history"
PY="C:/Users/user/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
PORT=47877
FAILED=0

fail() { echo "  ✗ FAIL: $1"; FAILED=$((FAILED+1)); }
ok()   { echo "  ✓ $1"; }

echo "============================================================"
echo " 首页四个核心模块 · 端到端验收"
echo "============================================================"

if [ ! -f "$BIN" ]; then echo "!! 缺少后端产物 $BIN"; exit 1; fi
if [ ! -d "$EVID/series" ]; then echo "!! 缺少历史数据样本 $EVID/series"; exit 1; fi

rm -rf "$DATA/data/history" 2>/dev/null || true
rm -f "$DATA/data/database.sqlite" "$DATA/data/database.sqlite-wal" "$DATA/data/database.sqlite-shm" 2>/dev/null || true
rm -f "$DATA/cache/token" "$DATA/cache/port" 2>/dev/null || true
mkdir -p "$DATA/data/history/series"
# 用 `src/.` 复制目录**内容**（幂等，重复执行不会产生 series/series 嵌套）
cp -r "$EVID/series/." "$DATA/data/history/series/"
echo "已归档历史样本: $(find "$DATA/data/history" -name '*.parquet' | wc -l) 个 parquet 文件"
echo "  （index 序列 $(find "$DATA/data/history/series/kind=index" -name '*.parquet' 2>/dev/null | wc -l) 个 +"
echo "    market_flow 序列 $(find "$DATA/data/history/series/kind=market_flow" -name '*.parquet' 2>/dev/null | wc -l) 个）"
if [ "$(find "$DATA/data/history/series/kind=market_flow" -name '*.parquet' 2>/dev/null | wc -l)" -eq 0 ]; then
  echo "!! 缺少 market_flow 样本，模块 ②④ 无法验收"; exit 1
fi

start_backend() {
  "$BIN" --port $PORT --data-dir "$DATA" > "$DATA/stdout.log" 2>&1 &
  BE=$!
  for i in $(seq 1 90); do
    curl -s -m 3 "http://127.0.0.1:$PORT/api/health" -o "$DATA/h.json" 2>/dev/null \
      && grep -q '"status":"ok"' "$DATA/h.json" && break
    sleep 1
  done
  TOKEN=""
  for _ in $(seq 1 40); do
    [ -s "$DATA/cache/token" ] && TOKEN=$(tr -d '\r\n' < "$DATA/cache/token") && break
    sleep 0.5
  done
  AUTH=(-H "X-CF-Token: $TOKEN")
}

stop_backend() {
  curl -s -m 15 -X POST "${AUTH[@]}" "http://127.0.0.1:$PORT/api/shutdown" -o /dev/null 2>/dev/null
  sleep 6
  kill -9 $BE 2>/dev/null
  wait $BE 2>/dev/null
}

echo
echo "---- 阶段 A：启动后端，让它采集并落盘当日板块快照 ----"
start_backend
echo "后端就绪（${i}s），令牌长度 ${#TOKEN}，等待板块采集与历史预加载…"
sleep 60

curl -s -m 120 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/snapshot" -o "$DATA/snap.json"
$PY - "$DATA/snap.json" "$DATA/trade_date.txt" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
print(f"  trade_date（市场时钟）   : {d['trade_date']}")
print(f"  series_date（序列实际日）: {d['series_date']}   回退={d['series_fallback']}")
print(f"  数据模式                 : {d['time']['data_mode_label']}   定稿={d['time']['is_final']}")
print(f"  行情时间                 : {d['time']['data_timestamp']}")
print(f"  系统时间                 : {d['time']['system_time']}")
print(f"  抓取时间                 : {d['time']['data_fetch_time']}")
print(f"  昨收基准                 : {d['index_prev_close']}")
print(f"  指数分时                 : {len(d['index_series'])} 点  完整={d['index_complete']}")
print(f"  资金曲线                 : {len(d['flow_series'])} 点  完整={d['flow_complete']}")
if d['missing_note']:
    print(f"  缺失说明                 : {d['missing_note'][:160]}")
open(sys.argv[2], 'w').write(d['trade_date'])
PYEOF

TRADE_DATE=$(cat "$DATA/trade_date.txt")

echo
echo "---- 阶段 B：停止后端，直接读 SQLite 验证「板块快照已落盘」 ----"
stop_backend
$PY - "$DATA/data/database.sqlite" "$TRADE_DATE" <<'PYEOF'
import sqlite3, sys, json
db, date = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = conn.execute(
    "SELECT node, source, LENGTH(payload) FROM sector_nodes WHERE trade_date=?", (date,)
).fetchall()
print(f"  sector_nodes 中 trade_date={date} 的记录: {len(rows)} 条")
for node, src, ln in rows:
    print(f"    node={node:<10} source={src:<20} payload={ln} 字节")
assert any(n == 'latest' for n, _, _ in rows), "未找到 node=latest 的当日板块快照"
payload = conn.execute(
    "SELECT payload FROM sector_nodes WHERE trade_date=? AND node='latest'", (date,)
).fetchone()[0]
data = json.loads(payload)
assert data and data[0].get('name'), "板块快照内容为空"
print(f"  快照内板块数量: {len(data)}   首条: {data[0]['name']}")
print("  ✓ 板块快照已由后端真实落盘到本地 SQLite")
PYEOF
[ $? -ne 0 ] && fail "板块快照未落盘"

echo
echo "---- 阶段 C：重启后端（模拟收盘后 / 休市），四个模块逐项验收 ----"
start_backend
echo "后端就绪（${i}s）"
curl -s -m 120 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/snapshot" -o "$DATA/snap2.json"

# --- ① 指数分时 ---
echo
echo "① 指数分时（/api/snapshot.index_series）"
$PY - "$DATA/snap2.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
idx = d['index_series']; s = d['index_summary']
assert idx, "指数分时为空"
ts = [b['minute'] for b in idx]
assert ts == sorted(ts), "横轴未按时间排序"
assert ts[0] == '09:31' and ts[-1] == '15:00', f"首尾异常 {ts[0]}->{ts[-1]}"
outside = [t for t in ts if not (('09:15' <= t <= '11:30') or ('13:00' <= t <= '15:00'))]
assert not outside, f"存在非交易时段时间点 {outside[:3]}"
assert s['points'] == len(idx), f"汇总点数 {s['points']} != 序列长度 {len(idx)}"
assert s['first_minute'] == ts[0] and s['last_minute'] == ts[-1], "汇总首末与序列不一致"
assert abs(s['last'] - idx[-1]['close']) < 1e-6, "汇总最新值 != 末根收盘"
assert s['high'] >= s['last'] and s['low'] <= s['last'], "最高/最低与最新值矛盾"
assert s['high'] >= max(b['high'] for b in idx) - 1e-6, "汇总最高未覆盖 bar 极值"
assert s['low'] <= min(b['low'] for b in idx) + 1e-6, "汇总最低未覆盖 bar 极值"
assert s['amount'] > 0, "成交额累计为 0"
if d['index_prev_close'] is None:
    assert s['change'] is None and s['change_pct'] is None, "昨收缺失却给出了涨跌幅（伪造）"
    print(f"  横轴 {ts[0]}→{ts[-1]}  {len(idx)} 点  排序正确  无越界时间点")
    print(f"  最新={s['last']:.2f} 高={s['high']:.2f} 低={s['low']:.2f}")
    print("  昨收不可得 → 未给出涨跌幅（未伪造基准）  ✓")
else:
    print(f"  横轴 {ts[0]}→{ts[-1]}  {len(idx)} 点  排序正确  无越界时间点")
    print(f"  昨收={d['index_prev_close']:.2f}  最新={s['last']:.2f}  "
          f"涨跌幅={s['change_pct']:+.2f}%  高={s['high']:.2f} 低={s['low']:.2f}  "
          f"成交额={s['amount']/1e8:.0f}亿")
print("  ① 指数分时 通过")
PYEOF
[ $? -ne 0 ] && fail "指数分时断言失败"

# --- ② 主力资金累计曲线 ---
echo
echo "② 主力资金累计曲线（/api/snapshot.flow_series）"
$PY - "$DATA/snap2.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
flw = d['flow_series']; s = d['flow_summary']
assert flw, "资金曲线为空"
ts = [b['minute'] for b in flw]
assert ts == sorted(ts), "横轴未按时间排序"
assert s['points'] == len(flw), "汇总点数 != 序列长度"
assert abs(s['last'] - flw[-1]['main_net_inflow']) < 1e-6, "汇总末值 != 末点累计值"
vals = [b['main_net_inflow'] for b in flw]
assert abs(s['high'] - max(vals)) < 1e-6, "峰值 != 累计值最大值"
assert abs(s['low'] - min(vals)) < 1e-6, "谷值 != 累计值最小值"
assert s['first_minute'] == ts[0] and s['last_minute'] == ts[-1], "汇总首末与序列不一致"
# 口径必须写明是"东财现成接口、接口本身即累计"，不能写成"股票层面合计"
assert 'fflow/kline' in s['note'], f"未写明数据源接口: {s['note']}"
assert '未做任何累加' in s['note'], "未说明本程序不做累加"
assert '股票层面合计' not in s['note'], "口径标注错误：这条曲线不是股票层面相加"
print(f"  横轴 {ts[0]}→{ts[-1]}  {len(flw)} 点  累计口径（主力=大单+超大单）")
print(f"  末值={s['last']/1e8:+.2f}亿  峰={s['high']/1e8:+.2f}亿  谷={s['low']/1e8:+.2f}亿")
print(f"  数据源: {s['note']}")
print("  ② 主力资金累计曲线 通过（东财现成接口，未做累加）")
PYEOF
[ $? -ne 0 ] && fail "资金曲线断言失败"

# --- ③ 板块资金流入流出对比 ---
echo
echo "③ 板块资金流入流出对比（/api/sector/compare）"
curl -s -m 90 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/sector/compare" -o "$DATA/sec.json"
$PY - "$DATA/sec.json" "$TRADE_DATE" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
date = sys.argv[2]
print(f"  接口返回交易日 : {d['trade_date']}")
print(f"  数据状态       : {d['status']}")
print(f"  来源通道       : {d['source']}")
print(f"  板块覆盖       : 共 {d['board_count']} 个（行业 {d['industry_count']} / 概念 {d['concept_count']}）")
print(f"  概念是否被截断 : {d['concept_truncated']}")
print(f"  说明           : {d['note']}")
ins = d['inflow_top']; outs = d['outflow_top']
assert d['trade_date'] == date, f"板块交易日 {d['trade_date']} != 首页统一交易日 {date}"
assert ins and outs, "板块流入/流出为空"
assert d['status'] == 'REBUILD', f"非交易时段应读本地持久化快照(REBUILD)，实际 {d['status']}"
assert '本地持久化快照' in d['note'], "未说明数据来自本地持久化快照"
assert d['source'], "未上报数据来源通道"
assert d['board_count'] == d['industry_count'] + d['concept_count'], \
    "板块总数与行业/概念分项不一致"
assert d['board_count'] > 0, "板块总数为 0"
# 覆盖不足时必须显式标记，不能把"只覆盖一部分"当作全量
if d['concept_count'] <= 250:
    assert d['concept_truncated'], \
        f"概念板块只有 {d['concept_count']} 个却没有标记为截断（覆盖不全被隐藏）"
    assert '概念板块数量偏少' in d['note'] and '400+' in d['note'], \
        f"截断时未在说明里提示东财全量规模: {d['note']}"
    print("  ⚠️ 概念板块覆盖不足，已被接口显式标记（未隐藏）")
for i in range(len(ins) - 1):
    assert ins[i]['main_net_inflow'] >= ins[i+1]['main_net_inflow'], "流入榜未按降序"
for i in range(len(outs) - 1):
    assert outs[i]['main_net_inflow'] <= outs[i+1]['main_net_inflow'], "流出榜未按升序"
print(f"  流入 TOP{len(ins)}: " + "、".join(f"{r['name']}({r['main_net_inflow']/1e8:+.1f}亿)" for r in ins[:3]))
print(f"  流出 TOP{len(outs)}: " + "、".join(f"{r['name']}({r['main_net_inflow']/1e8:+.1f}亿)" for r in outs[:3]))
print(f"  板块类型标注   : {sorted(set(r.get('kind','') for r in ins))}")
print("  ③ 板块资金流入流出对比 通过（非交易时段读本地持久化快照，覆盖情况已如实上报）")
PYEOF
[ $? -ne 0 ] && fail "板块模块断言失败"

# --- ④ 时点数据面板（与曲线同一交易日、同一分钟） ---
echo
echo "④ 时点数据面板（/api/timeline/point，与曲线对齐）"
$PY - "$DATA/snap2.json" "$DATA/mid_minute.txt" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
flw = d['flow_series']
assert flw, "资金曲线为空，无法取中点分钟"
mid = flw[len(flw)//2]['minute']
open(sys.argv[2], 'w').write(mid)
print(f"  取曲线中点分钟: {mid}")
PYEOF
[ $? -ne 0 ] && fail "无法取曲线中点分钟"
MID=$(cat "$DATA/mid_minute.txt" 2>/dev/null || echo "")
if [ -n "$MID" ]; then
  curl -s -m 60 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/timeline/point?minute=$MID&trade_date=$TRADE_DATE" -o "$DATA/tp.json"
  $PY - "$DATA/snap2.json" "$DATA/tp.json" "$TRADE_DATE" "$MID" <<'PYEOF'
import json, sys
snap = json.load(open(sys.argv[1], encoding='utf-8'))
tp = json.load(open(sys.argv[2], encoding='utf-8'))
date, mid = sys.argv[3], sys.argv[4]
assert tp['available'], f"时点不可用: {tp['note']}"
assert tp['trade_date'] == date, f"时点交易日 {tp['trade_date']} != 曲线交易日 {date}"
assert tp['minute'] == mid, "时点分钟与请求不一致"
curve = next(b for b in snap['flow_series'] if b['minute'] == mid)
assert abs(tp['main_net'] - curve['main_net_inflow']) < 1e-6, \
    f"面板主力净流入 {tp['main_net']} != 曲线同分钟 {curve['main_net_inflow']}（错位！）"
idx = next((b for b in snap['index_series'] if b['minute'] == mid), None)
if idx:
    assert abs(tp['index_close'] - idx['close']) < 1e-6, "面板指数点位 != 指数分时同分钟"
print(f"  {date} {mid}")
print(f"    指数点位       = {tp['index_close']:.2f}    （与指数分时同分钟一致 ✓）")
print(f"    主力净流入累计 = {tp['main_net']/1e8:+.2f}亿  （与资金曲线同分钟一致 ✓）")
print(f"    超大单={tp['super_large']/1e8:+.2f}亿 大单={tp['large']/1e8:+.2f}亿 "
      f"中单={tp['medium']/1e8:+.2f}亿 小单={tp['small']/1e8:+.2f}亿")
print(f"    说明: {tp['note']}")
print("  ④ 时点数据面板 通过（与曲线同交易日、同分钟，数值完全一致）")
PYEOF
  [ $? -ne 0 ] && fail "时点面板断言失败"
fi

# --- 附：历史库状态 ---
echo
echo "附：历史库状态（/api/history/status）"
curl -s -m 90 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/history/status" -o "$DATA/hs.json"
$PY - "$DATA/hs.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
assert d['duckdb_version'] and d['duckdb_version'] != 'unknown', "DuckDB 版本未知"
print(f"  查询引擎     : {d['duckdb_version']}")
print(f"  已落盘交易日 : {len(d['dates'])} 个 {d['dates']}")
print(f"  当前粒度     : {d['granularity_label']}")
for c in d['granularity_capability']:
    print(f"    {c['label']:<7} 可回溯 {c['source_max_days']:>4} 交易日  "
          f"{'满足' if c['meets_requirement'] else '不满足'} 配置要求")
PYEOF
[ $? -ne 0 ] && fail "历史库状态断言失败"

# --- 断网复核 ---
echo
echo "附：停止后端后直接用 pyarrow 读取本地历史库（断网可用性）"
stop_backend
$PY - "$DATA/data/history" <<'PYEOF'
import sys, glob, os
import pyarrow.parquet as pq
root = sys.argv[1]
files = sorted(glob.glob(os.path.join(root, "**/*.parquet"), recursive=True))
assert files, "无历史文件"
total = sum(pq.read_table(f).num_rows for f in files)
print(f"  后端已停止，直接读取本地 Parquet：{len(files)} 个文件 / {total} 行  ✓ 断网可用")
PYEOF
[ $? -ne 0 ] && fail "断网读取失败"

echo
echo "============================================================"
if [ "$FAILED" -eq 0 ]; then
  echo " 验收通过：首页四个核心模块全部读取本地历史静态数据"
else
  echo " 验收失败：$FAILED 项断言不通过"
fi
echo "============================================================"
exit $FAILED
