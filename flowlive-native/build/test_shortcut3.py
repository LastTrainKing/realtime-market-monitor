# -*- coding: utf-8 -*-
"""验证桌面快捷方式双击可用

方法：记录启动前的进程 PID 集合 → 通过 WScript.Shell 模拟双击 .lnk →
      比较新增 PID（★ 中文进程名过滤不可靠，改用全量 PID 差分）
"""
import os, sys, time, subprocess, json

LNK = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"),
                   "Desktop", "资金流向原生版.lnk")
EXE_NAME = "资金流向原生版.exe"


def pids():
    """当前所有进程 PID（bytes 抓取再按 GBK 解码，避开中文编码坑）"""
    r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                       capture_output=True, timeout=60)
    out = (r.stdout or b"").decode("gbk", "ignore")
    s = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2:
            try:
                s.add(int(parts[1]))
            except ValueError:
                pass
    return s


def main():
    print("=" * 56)
    print("桌面快捷方式启动验证")
    print("=" * 56)

    if not os.path.exists(LNK):
        print("[FAILED] 快捷方式不存在:", LNK)
        return 1
    print(f"[1] 快捷方式存在  {os.path.getsize(LNK):,} B")
    print(f"    {LNK}")

    before = pids()
    print(f"[2] 启动前进程数: {len(before)}")

    # 模拟双击：WScript.Shell.Run 走 ShellExecute 路径，等价于资源管理器双击
    vbs = os.path.join(os.environ.get("TEMP", "."), "_run_lnk.vbs")
    with open(vbs, "w", encoding="gbk", errors="replace") as f:
        f.write('Set sh = CreateObject("WScript.Shell")\n')
        f.write(f'sh.Run """{LNK}""", 1, False\n')
    r = subprocess.run(["cscript", "//nologo", vbs], capture_output=True, timeout=60)
    print("[3] 已模拟双击（ShellExecute） rc =", r.returncode)

    # 等它起来（onefile 要解压 + 读缓存）
    new = []
    for _ in range(40):
        time.sleep(1.0)
        now = pids()
        added = now - before
        if added:
            new = sorted(added)
            time.sleep(4)                                   # 再等一会看是否稳定
            now2 = pids()
            if not (now2 - before):
                print("[WARN] 进程启动后又消失了（可能是启动失败）")
            break

    after = pids()
    added = sorted(after - before)
    print(f"[4] 启动后进程数: {len(after)}   新增: {added}")

    # 用 WMI 查新增 PID 的进程名
    if added:
        try:
            r = subprocess.run(
                ["wmic", "process", "where", "name like '%资金%'", "get",
                 "ProcessId,Name,WorkingSetSize", "/format:csv"],
                capture_output=True, timeout=60)
            txt = (r.stdout or b"").decode("gbk", "ignore").strip()
            print("[5] 匹配到的进程:")
            for line in txt.splitlines():
                if line.strip() and "Node" not in line:
                    print("   ", line.strip())
        except Exception as e:                              # noqa: BLE001
            print("[5] wmic 查询失败:", e)

    ok = len(added) > 0
    print()
    print(f"[{'SUCCESS' if ok else 'FAILED'}] 双击快捷方式{'成功启动' if ok else '未能启动'}")

    # 收尾：关掉测试进程，避免影响后续
    if ok:
        subprocess.run(["taskkill", "/F", "/IM", EXE_NAME],
                       capture_output=True, timeout=60)
        print("[6] 已关闭测试实例")

    result = {"lnk": LNK, "lnk_size": os.path.getsize(LNK),
              "added_pids": added, "ok": ok}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "shortcut_test.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
