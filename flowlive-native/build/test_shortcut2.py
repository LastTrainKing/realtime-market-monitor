# -*- coding: utf-8 -*-
"""验证桌面快捷方式能否双击启动（用全量 PID 差分，避开中文名过滤问题）"""
import os, sys, time, json, subprocess

DESK = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Desktop")
LNK = os.path.join(DESK, "资金流向原生版.lnk")
res = {}


def all_pids():
    r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=30)
    m = {}
    for line in (r.stdout or "").splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) >= 2:
            m[parts[1]] = parts[0]
    return m


print(f"快捷方式: {LNK}")
print(f"  存在: {os.path.exists(LNK)}")
res["lnk_exists"] = os.path.exists(LNK)

try:
    import win32com.client
    sh = win32com.client.Dispatch("WScript.Shell")
    sc = sh.CreateShortCut(LNK)
    res["target"] = sc.TargetPath
    res["workdir"] = sc.WorkingDirectory
    res["icon"] = sc.IconLocation
    print(f"  目标    : {sc.TargetPath}")
    print(f"  工作目录 : {sc.WorkingDirectory}")
    print(f"  图标    : {sc.IconLocation}")
    print(f"  目标存在 : {os.path.exists(sc.TargetPath)}")
except Exception as e:
    res["parse_err"] = str(e)
    print("  解析失败:", e)

before = all_pids()
print(f"\n双击前进程总数: {len(before)}")
print("模拟双击（os.startfile）…")
os.startfile(LNK)
time.sleep(38)
after = all_pids()
new = {pid: nm for pid, nm in after.items() if pid not in before}
res["new_count"] = len(new)
res["new_names"] = sorted({nm for nm in new.values()})
print(f"双击后进程总数: {len(after)}   新增 {len(new)} 个")
for nn in res["new_names"][:12]:
    print("   +", nn)
res["launched"] = len(new) > 0

print("\n清理 …")
for pid in new:
    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=20)

res["ok"] = res.get("lnk_exists") and res.get("launched")
print("\n" + json.dumps({k: v for k, v in res.items() if k != "new_names"},
                        ensure_ascii=False, indent=2))
