#!/usr/bin/env bash
set -u
ROOT="C:/Users/user/WorkBuddy AI/2026-10-01-12-48-43"
BIN="$ROOT/capital-flow-command-center/backend/target/release/capital_backend.exe"
DATA="$ROOT/.tmp/dash"
PY="C:/Users/user/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
PORT=47831
rm -rf "$DATA"; mkdir -p "$DATA"
"$BIN" --port $PORT --data-dir "$DATA" > "$DATA/stdout.log" 2>&1 &
BE=$!
for i in $(seq 1 60); do
  curl -s -m 3 "http://127.0.0.1:$PORT/api/health" -o "$DATA/h.json" 2>/dev/null && grep -q '"status":"ok"' "$DATA/h.json" && break
  sleep 1
done
echo "ready in ${i}s"

TOKEN=""
for _ in $(seq 1 20); do
  if [ -s "$DATA/cache/token" ]; then TOKEN=$(tr -d '\r\n' < "$DATA/cache/token"); break; fi
  sleep 0.5
done
AUTH=(-H "X-CF-Token: $TOKEN")

for t in 10 30 70 130; do
  sleep $t
  curl -s -m 20 "${AUTH[@]}" "http://127.0.0.1:$PORT/api/dashboard" -o "$DATA/d.json"
  echo "--- after ~${t}s accumulated ---"
  "$PY" - "$DATA/d.json" <<'PYEOF'
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
print("  indices:", len(d['indices']['data']), d['indices']['status'], d['indices'].get('message','')[:40])
print("  sectors:", len(d['sectors']['data']), d['sectors']['status'])
print("  boards :", len(d['boards']['data']))
print("  top    :", len(d['top_stocks']['data']))
cv = d['capital_curve']['data']
print("  curve  :", len(cv.get('points', [])), 'scope=' + str(cv.get('scope')), 'sample=' + str(cv.get('sample_size')), d['capital_curve']['status'])
print("  breadth:", d['breadth']['data'].get('sample_size'), d['breadth']['data'].get('scope'))
print("  cycle  :", d['cycle'], "| degraded:", d['degraded'])
PYEOF
done
curl -s -m 10 -X POST "${AUTH[@]}" "http://127.0.0.1:$PORT/api/shutdown" > /dev/null
sleep 4
kill $BE 2>/dev/null
