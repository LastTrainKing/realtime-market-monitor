# -*- coding: utf-8 -*-
"""真机验证：启动部署版 exe，观察 live-lite 是否在亚秒级把当前数据送上屏。"""
import os, re, subprocess, time
RUN = r"C:\Users\user\Desktop\exe\最新版"
EXE = os.path.join(RUN, "资金流向原生版.exe")
LOG = os.path.join(RUN, "logs", time.strftime("app_%Y%m%d.log"))
try: os.remove(LOG)
except OSError: pass
print("启动:", EXE)
p = subprocess.Popen([EXE], cwd=RUN, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    t0 = time.time()
    while time.time() - t0 < 40:
        time.sleep(1)
        try:
            txt = open(LOG, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if "live-lite paint" in txt or "full fetch" in txt:
            # 再等几秒把后续行收全
            time.sleep(6)
            break
finally:
    subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True)
    time.sleep(1)
print("=" * 70)
try:
    for line in open(LOG, encoding="utf-8", errors="ignore"):
        if re.search(r"preflight|boot|early paint|live-lite|full fetch|loaded ok|spot|缺口|占位", line):
            print(line.rstrip())
except OSError as e:
    print("读日志失败:", e)
