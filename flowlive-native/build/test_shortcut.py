# -*- coding: utf-8 -*-
"""验证桌面快捷方式：解析目标 + 模拟双击启动"""
import os, sys, time, json, subprocess

DESK = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Desktop")
LNK = os.path.join(DESK, "资金流向原生版.lnk")
res = {}


def procs(name):
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=25)
    return {p.strip('"').split('","')[1] for p in (r.stdout or "").splitlines()
            if p.startswith(f'"{name}"') and '","' in p}


print(f"快捷方式: {LNK}")
print(f"  存在: {os.path.exists(LNK)}  大小: "
      f"{os.path.getsize(LNK) if os.path.exists(LNK) else 0} B")
res["lnk_exists"] = os.path.exists(LNK)

# 解析 .lnk 指向
try:
    import win32com.client
    sh = win32com.client.Dispatch("WScript.Shell")
    sc = sh.CreateShortCut(LNK)
    print(f"  目标  : {sc.TargetPath}")
    print(f"  工作目录: {sc.WorkingDirectory}")
    print(f"  图标  : {sc.IconLocation}")
    res["target"] = sc.TargetPath
    res["target_exists"] = os.path.exists(sc.TargetPath)
except Exception as e:
    print("  解析失败:", e)
    res["parse_err"] = str(e)

# 模拟双击
before = procs("资金流向原生版.exe")
print(f"\n双击前：同名进程 {len(before)} 个")
print("模拟双击（os.startfile）…")
os.startfile(LNK)
time.sleep(35)
after = procs("资金流向原生版.exe")
new = after - before
res["launched"] = len(new) > 0
res["new_pids"] = sorted(new)
print(f"双击后：同名进程 {len(after)} 个，新增 {len(new)} 个")
print(f"  => {'快捷方式可用，双击成功拉起程序' if new else '未检测到新进程'}")

print("\n清理 …")
for pid in new:
    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=20)

res["ok"] = res.get("lnk_exists") and res.get("target_exists") and res.get("launched")
print("\n" + json.dumps(res, ensure_ascii=False, indent=2))
