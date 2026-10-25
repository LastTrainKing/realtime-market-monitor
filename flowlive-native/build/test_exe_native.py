# -*- coding: utf-8 -*-
"""实测打包后的 exe：能否启动、窗口是否出现、是否残留浏览器进程"""
import os, sys, time, json, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(BASE, "dist", "资金流向原生版.exe")
OUT = os.path.join(BASE, "exe_native_test.json")
res = {}


def procs(name):
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=25)
    out = set()
    for line in (r.stdout or "").splitlines():
        if line.startswith(f'"{name}"'):
            p = [x.strip('"') for x in line.split('","')]
            if len(p) > 1:
                out.add(p[1])
    return out


res["exe_exists"] = os.path.exists(EXE)
res["exe_MB"] = round(os.path.getsize(EXE) / 1048576, 1) if res["exe_exists"] else 0
print(f"exe: {EXE}")
print(f"  存在={res['exe_exists']}  大小={res['exe_MB']} MB")

before_edge = procs("msedge.exe")
before_webview = procs("msedgewebview2.exe")
print(f"\n启动前：msedge={len(before_edge)}  msedgewebview2={len(before_webview)}")

print("\n启动 exe（等待 40 秒，含解压 + 首次加载 + 读缓存）…")
t0 = time.time()
proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
time.sleep(40)

alive = proc.poll() is None
res["exe_alive"] = alive
print(f"  exe 进程存活: {alive}  (pid={proc.pid})")

after_edge = procs("msedge.exe")
after_webview = procs("msedgewebview2.exe")
new_edge = after_edge - before_edge
new_wv = after_webview - before_webview
res["new_msedge"] = len(new_edge)
res["new_webview2"] = len(new_wv)
print(f"  新增 msedge 进程: {len(new_edge)}   （网页版会拉起这个）")
print(f"  新增 msedgewebview2 进程: {len(new_wv)}   （WebView 方案会拉起这个）")

# 用 tasklist /V 看窗口标题，确认有可见窗口
r = subprocess.run(["tasklist", "/FI", f"PID eq {proc.pid}", "/V", "/FO", "CSV", "/NH"],
                   capture_output=True, encoding="utf-8", errors="replace", timeout=25)
line = (r.stdout or "").strip()
res["tasklist_line"] = line[:200]
print(f"  tasklist: {line[:160]}")

res["no_browser"] = (len(new_edge) == 0 and len(new_wv) == 0)
print(f"\n  浏览器进程：{'无（纯原生，符合预期）' if res['no_browser'] else '有残留'}")

print("\n清理 …")
subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
               capture_output=True, timeout=20)
res["ok"] = bool(res["exe_exists"] and res["no_browser"])
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False, indent=2))
