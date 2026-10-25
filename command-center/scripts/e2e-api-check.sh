#!/usr/bin/env bash
# ============================================================
# 端到端接口验收脚本
#
# 覆盖：启动 → 就绪 → 鉴权边界 → 只读接口 → 写接口 → 数据真实性 → 优雅关闭
#
# 重要：写接口的失败**同样计入 FAIL**。
# 早期版本只打印写接口的 HTTP code 而不计入失败数，会出现
# "POST /api/watchlist 返回 500，最后仍显示失败接口数: 0" 的假通过。
# ============================================================
set -u
ROOT="C:/Users/user/WorkBuddy AI/2026-10-01-12-48-43"
BIN="$ROOT/capital-flow-command-center/backend/target/release/capital_backend.exe"
DATA="$ROOT/.tmp/e2e"
PY="C:/Users/user/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
# 使用独立端口，避免与已安装并正在运行的正式实例（默认 47821）冲突：
# 若端口被占用，本脚本启动的后端会判定"已有实例"直接退出，导致验收误判。
PORT=47841
FAIL=0
PASS=0

rm -rf "$DATA"
mkdir -p "$DATA"

"$BIN" --port $PORT --data-dir "$DATA" > "$DATA/stdout.log" 2>&1 &
BE_PID=$!

echo "后端 PID=$BE_PID，等待就绪…"
READY=0
for i in $(seq 1 90); do
  if curl -s -m 3 "http://127.0.0.1:$PORT/api/health" -o "$DATA/health.json" 2>/dev/null; then
    if grep -q '"status":"ok"' "$DATA/health.json" 2>/dev/null; then READY=1; break; fi
  fi
  sleep 1
done

if [ "$READY" != "1" ]; then
  echo "!! 后端未在 90 秒内就绪（端口 $PORT 可能被占用）"
  tail -20 "$DATA/stdout.log"
  kill $BE_PID 2>/dev/null
  exit 1
fi
echo "后端已就绪（耗时 ${i}s）"

# 读取本地 API 令牌
TOKEN_FILE="$DATA/cache/token"
TOKEN=""
for _ in $(seq 1 20); do
  if [ -s "$TOKEN_FILE" ]; then TOKEN=$(tr -d '\r\n' < "$TOKEN_FILE"); break; fi
  sleep 0.5
done
if [ -z "$TOKEN" ]; then
  echo "!! 未生成访问令牌，安全边界验证无法进行"
  kill $BE_PID 2>/dev/null
  exit 1
fi
echo "访问令牌已获取（长度 ${#TOKEN}）"

AUTH=(-H "X-CF-Token: $TOKEN")

check() { # $1=描述 $2=期望码 $3=实际码 $4=文件
  if [ "$3" = "$2" ]; then
    PASS=$((PASS+1))
    printf "  OK   %-34s HTTP %s\n" "$1" "$3"
  else
    FAIL=$((FAIL+1))
    printf "  FAIL %-34s HTTP %s (期望 %s)  %s\n" "$1" "$3" "$2" "$(head -c 120 "$4" 2>/dev/null)"
  fi
}

# ---------------------------------------------------------------------------
# 预热等待（重要）
#
# 全市场扫描跑起来之前，依赖个股资金流的榜单接口会返回
# 503 + "榜单正在计算，请稍候" —— 这是**正常的启动过程**，不是接口故障。
# 早期版本只固定 sleep 20s，扫描还没跑（实测需 ~60s）就断言"只读接口必须 200"，
# 会把正常预热误判成 FAIL。
#
# 这里改为轮询 /api/system/status，等全市场扫描真正运行过一次再断言；
# 若等待窗口内始终没跑起来，仍按 FAIL 处理（不掩盖问题），并打印原因。
# ---------------------------------------------------------------------------
echo
echo "等待预热（全市场扫描完成，最长 300s）…"
WARMED=0
for w in $(seq 1 30); do
  st=$(curl -s -m 15 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/system/status" 2>/dev/null)
  runs=$(printf '%s' "$st" | $PY -c "
import json,sys
try:
    for c in json.load(sys.stdin)['status']['collectors']:
        if '全市场' in c.get('label','') or c.get('name') == 'market':
            print(c.get('runs', 0)); break
    else:
        print(0)
except Exception:
    print(0)
" 2>/dev/null)
  if [ "${runs:-0}" -gt 0 ] 2>/dev/null; then
    WARMED=1
    echo "  全市场扫描已运行 ${runs} 次（等待 $((w * 10))s）"
    break
  fi
  sleep 10
done
if [ "$WARMED" != "1" ]; then
  echo "  !! 全市场扫描在 300s 内未运行 —— 依赖它的榜单接口将返回 503"
  echo "     这通常意味着上游行情通道不可达（环境问题），请结合日志判断"
fi

echo
echo "================ 鉴权边界（无令牌必须 401） ================"
for ep in "/api/dashboard" "/api/boards" "/api/system/status"; do
  code=$(curl -s -m 15 -o "$DATA/r.json" -w "%{http_code}" "http://127.0.0.1:$PORT$ep")
  check "无令牌 GET $ep" "401" "$code" "$DATA/r.json"
done
for m in POST PUT DELETE; do
  code=$(curl -s -m 15 -o "$DATA/r.json" -w "%{http_code}" -X $m -H "Content-Type: application/json" \
    -d '{}' "http://127.0.0.1:$PORT/api/watchlist")
  check "无令牌 $m /api/watchlist" "401" "$code" "$DATA/r.json"
done
code=$(curl -s -m 15 -o "$DATA/r.json" -w "%{http_code}" -X POST "http://127.0.0.1:$PORT/api/shutdown")
check "无令牌 POST /api/shutdown" "401" "$code" "$DATA/r.json"
code=$(curl -s -m 15 -o "$DATA/r.json" -w "%{http_code}" "http://127.0.0.1:$PORT/api/health")
check "健康检查免鉴权" "200" "$code" "$DATA/r.json"

echo
echo "================ 只读接口（带令牌必须 200） ================"
for ep in \
  "/api/dashboard" \
  "/api/market/indices" \
  "/api/market/breadth" \
  "/api/market/sectors" \
  "/api/boards" \
  "/api/boards/main_net_flow" \
  "/api/stocks?limit=10" \
  "/api/stocks/600519" \
  "/api/capital/curve" \
  "/api/nodes" \
  "/api/opening" \
  "/api/anomaly" \
  "/api/selection" \
  "/api/review/days" \
  "/api/system/status" \
  "/api/config" \
  "/api/watchlist" \
  "/api/snapshot" \
  "/api/timeline/point?minute=09:35" \
  "/api/sector/compare" \
  "/api/history/status"
do
  code=$(curl -s -m 60 -o "$DATA/r.json" -w "%{http_code}" "${AUTH[@]}" "http://127.0.0.1:$PORT$ep")
  check "GET $ep" "200" "$code" "$DATA/r.json"
done

echo
echo "================ 写接口（失败同样计入 FAIL） ================"
code=$(curl -s -m 15 -o "$DATA/r.json" -w "%{http_code}" -X POST "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{"code":"600519","name":"贵州茅台"}' "http://127.0.0.1:$PORT/api/watchlist")
check "POST /api/watchlist" "200" "$code" "$DATA/r.json"

code=$(curl -s -m 15 -o "$DATA/r.json" -w "%{http_code}" "${AUTH[@]}" "http://127.0.0.1:$PORT/api/watchlist")
check "GET /api/watchlist（写入后可见）" "200" "$code" "$DATA/r.json"
if grep -q '600519' "$DATA/r.json"; then
  PASS=$((PASS+1)); echo "  OK   自选股已落库"
else
  FAIL=$((FAIL+1)); echo "  FAIL 自选股未落库"
fi

code=$(curl -s -m 15 -o "$DATA/r.json" -w "%{http_code}" -X DELETE "${AUTH[@]}" "http://127.0.0.1:$PORT/api/watchlist/600519")
check "DELETE /api/watchlist/600519" "200" "$code" "$DATA/r.json"

code=$(curl -s -m 15 -o "$DATA/r.json" -w "%{http_code}" -X POST "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{"code":"abc"}' "http://127.0.0.1:$PORT/api/watchlist")
check "POST 非法代码返回 200 且 ok=false" "200" "$code" "$DATA/r.json"
if grep -q '"ok":false' "$DATA/r.json"; then
  PASS=$((PASS+1)); echo "  OK   非法代码被正确拒绝"
else
  FAIL=$((FAIL+1)); echo "  FAIL 非法代码未被拒绝"
fi

code=$(curl -s -m 90 -o "$DATA/r.json" -w "%{http_code}" -X POST "${AUTH[@]}" "http://127.0.0.1:$PORT/api/nodes/09:35/rebuild")
check "POST /api/nodes/09:35/rebuild" "200" "$code" "$DATA/r.json"

code=$(curl -s -m 20 -o "$DATA/cfg.json" -w "%{http_code}" "${AUTH[@]}" "http://127.0.0.1:$PORT/api/config")
check "GET /api/config（保存前）" "200" "$code" "$DATA/cfg.json"
code=$(curl -s -m 20 -o "$DATA/r.json" -w "%{http_code}" -X PUT "${AUTH[@]}" -H "Content-Type: application/json" \
  --data-binary "@$DATA/cfg.json" "http://127.0.0.1:$PORT/api/config")
check "PUT /api/config（原样回写）" "200" "$code" "$DATA/r.json"

echo
echo "================ 数据真实性抽查 ================"
curl -s -m 25 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/boards" -o "$DATA/boards.json"
curl -s -m 25 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/system/status" -o "$DATA/sys.json"
curl -s -m 25 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/selection" -o "$DATA/sel.json"
curl -s -m 25 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/nodes" -o "$DATA/nodes.json"
curl -s -m 25 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/market/breadth" -o "$DATA/breadth.json"
curl -s -m 25 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/capital/curve" -o "$DATA/curve.json"

"$PY" - "$DATA" <<'PYEOF'
import json, sys, os
d = sys.argv[1]
def load(n):
    with open(os.path.join(d, n), encoding='utf-8') as f:
        return json.load(f)

boards = load('boards.json')
print("九榜:", boards['status'], '/', boards['source_label'], '/ 更新', boards['updated_at'])
for b in boards['data']:
    top = b['entries'][0] if b['entries'] else None
    label = f"{top['name']} {top['code']} {top['value_label']}" if top else ("不可用：" + (b.get('note') or '空'))
    print(f"   {b['name']:<10} {b['scope']:<7} 条目{len(b['entries']):>3}  榜首 {label}")

curve = load('curve.json')
c = curve['data']
print(f"\n两市资金曲线: 口径={c.get('scope')} 样本={c.get('sample_size')} 点数={len(c.get('points', []))} 来源={curve['source_label']}")
print(f"   说明: {c.get('note','')}")
if c.get('points'):
    last = c['points'][-1]
    print(f"   最新点 {last['time']} 主力净流入 {last['main_net']/1e8:.2f} 亿")

sel = load('sel.json')
print(f"\n个股精选: {len(sel['stocks'])} 只，累计上榜事件 {sel['total_events']}")
for s in sel['stocks'][:3]:
    print(f"   {s['name']} {s['code']} 上榜{s['appear_count']}次 命中{s['board_count']}榜 {s['board_labels']}")

nodes = load('nodes.json')
print("\n早盘节点:")
for n in nodes:
    print(f"   {n['node']}  {n['status']}  {n['source_label']}  {n['note'][:78]}")

br = load('breadth.json')
b = br['data']
print(f"\n市场概况: 状态 {br['status']} 口径 {b.get('scope')} 样本 {b.get('sample_size')} 只")
print(f"   上涨 {b['up_count']} / 下跌 {b['down_count']}  涨停 {b['limit_up_count']} 跌停 {b['limit_down_count']}")
print(f"   成交额 {b['total_amount']/1e8:.2f} 亿  主力净流入 {b['total_main_net']/1e8:.2f} 亿")

sysd = load('sys.json')['status']
print(f"\n运行状态: {sysd['market_status_label']} 交易日 {sysd['trade_date']} 运行 {sysd['uptime_secs']}s")
print(f"   网络: 请求 {sysd['net']['total_requests']} 成功 {sysd['net']['success']} 失败 {sysd['net']['failed']} 去重 {sysd['net']['deduped']}")
print(f"   数据库: 快照 {sysd['db']['snapshots']} 事件 {sysd['db']['ranking_events']} 分钟行 {sysd['db']['minute_rows']}")
print("   采集器:")
for c2 in sysd['collectors']:
    state = '正常' if c2['ok'] else '异常'
    print(f"      {c2['label']:<10} {state:<4} 数据 {c2['data_points']:>5}  成功{c2['runs']-c2['failures']:>3}/失败{c2['failures']:>3}")
if sysd.get('gated_sources'):
    print("   冷却通道:", [(g['label'], g['remaining_secs']) for g in sysd['gated_sources']])
PYEOF

echo
echo "================ 优雅关闭 ================"
code=$(curl -s -m 15 -o "$DATA/shutdown.json" -w "%{http_code}" -X POST "${AUTH[@]}" "http://127.0.0.1:$PORT/api/shutdown")
check "POST /api/shutdown" "200" "$code" "$DATA/shutdown.json"
sleep 10
# 注意：不能只看 curl 的退出码 —— 代理/网关返回 HTTP 502 时 curl 依然返回 0。
# 必须校验健康检查的响应体，才能判断后端是否真的退出。
HEALTH_BODY=$(curl -s -m 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null || true)
if printf '%s' "$HEALTH_BODY" | grep -q '"status":"ok"'; then
  FAIL=$((FAIL+1)); echo "  FAIL 后端仍在响应，进程未退出"
  kill -9 $BE_PID 2>/dev/null
else
  PASS=$((PASS+1)); echo "  OK   后端已优雅退出，端口已释放"
fi

# 进程是否真的消失（防止留下幽灵进程）
if kill -0 $BE_PID 2>/dev/null; then
  sleep 5
  if kill -0 $BE_PID 2>/dev/null; then
    FAIL=$((FAIL+1)); echo "  FAIL 后端进程 $BE_PID 仍然存活"
    kill -9 $BE_PID 2>/dev/null
  else
    PASS=$((PASS+1)); echo "  OK   后端进程已回收"
  fi
else
  PASS=$((PASS+1)); echo "  OK   后端进程已回收"
fi

echo
echo "================ 汇总 ================"
echo "通过: $PASS    失败: $FAIL"
exit $FAIL
