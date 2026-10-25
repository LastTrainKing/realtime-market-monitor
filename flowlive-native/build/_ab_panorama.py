# -*- coding: utf-8 -*-
"""v2.0.12 受控 A/B：市场全景田字格等分改动的回归验证（全离屏）。

做法：
  1. 把当前源码 app_main_v203.py 复制为 _ab_v2.py，并把列 stretch 改回旧值 4:5
     —— 这样除「那一行 stretch」外**字节完全相同**，A/B 隔离干净。
  2. 两份分别 --shot 到两个前缀（ab_before_ / ab_after_），同一份冻结数据、
     同一离屏平台、同一尺寸 → 消除环境噪声。
  3. 逐页 PNG 比对：非「市场全景」页必须逐字节一致（证明零回归）；
     「市场全景」页应不同（预期变更）。

用法：QT_QPA_PLATFORM=offscreen python _ab_panorama.py
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENV_DIR = os.path.join(ROOT, "_v2111_env")
SHOTS = os.path.join(ROOT, "_ab_shots")
PY = os.path.join(ROOT, "native_env", "Scripts", "python.exe")
SRC = os.path.join(HERE, "app_main_v203.py")
OLD = os.path.join(HERE, "_ab_v2.py")

PANO = "市场全景"


def sha(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def run_shot(script, prefix):
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["LOCALAPPDATA"] = ENV_DIR
    env["FLOWLIVE_SHOTS"] = SHOTS
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([PY, script, "--shot", prefix],
                       cwd=HERE, env=env, capture_output=True)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    print(f"  [{prefix}] exit={r.returncode} lines={len(out.splitlines())}")
    return out


def main():
    os.makedirs(SHOTS, exist_ok=True)
    src = open(SRC, encoding="utf-8").read()
    new_line = "        g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)\n        g.setRowStretch(0, 1); g.setRowStretch(1, 1)\n        self.tabs.addTab(w, \"市场全景\")"
    old_line = "        g.setColumnStretch(0, 4); g.setColumnStretch(1, 5)\n        g.setRowStretch(0, 1); g.setRowStretch(1, 1)\n        self.tabs.addTab(w, \"市场全景\")"
    if new_line not in src:
        print("!! 未在源码中找到新版 stretch 行，A/B 无法建立")
        return 1
    open(OLD, "w", encoding="utf-8").write(src.replace(new_line, old_line))
    print(f"已生成 before 副本：{os.path.basename(OLD)} "
          f"({os.path.getsize(OLD):,} B)  唯一差异 = 列 stretch 4:5")

    print("跑 before（4:5）…")
    run_shot(OLD, "ab_before")
    print("跑 after（1:1）…")
    run_shot("app_main_v203.py", "ab_after")

    def collect(prefix):
        out = {}
        for n in os.listdir(SHOTS):
            if n.startswith(prefix + "_"):
                m = re.match(rf"{prefix}_(\d+)_(.+?)\.(png|txt)$", n)
                if m:
                    out[(int(m.group(1)), m.group(2), m.group(3))] = n
        return out

    a = collect("ab_before")
    b = collect("ab_after")
    print(f"\n生成：before {len(a)} 个 / after {len(b)} 个")
    keys = sorted(set(a) | set(b))
    diff, same, missing = [], [], []
    for k in keys:
        if k not in a or k not in b:
            missing.append(k)
            continue
        pa, pb = os.path.join(SHOTS, a[k]), os.path.join(SHOTS, b[k])
        ha, hb = sha(pa), sha(pb)
        if ha == hb:
            same.append(k)
        else:
            diff.append((k, os.path.getsize(pa), os.path.getsize(pb)))
    print(f"\n逐字节一致 : {len(same)}")
    print(f"存在差异   : {len(diff)}")
    for k, sa, sb in diff:
        tag = "  ← 预期变更（市场全景）" if k[1] == PANO else "  ⚠ 非预期变更"
        print(f"   {k[1]}.{k[2]}  {sa:,} → {sb:,}{tag}")
    if missing:
        print(f"缺失配对   : {len(missing)}")
        for k in missing:
            print("   ", k)

    bad = [k for k, _, _ in diff if k[1] != PANO]
    print("\n结论：", "PASS —— 除市场全景页外零回归" if not bad
          else f"FAIL —— {len(bad)} 处非预期差异")
    try:
        os.remove(OLD)
        print("已清理 before 副本")
    except OSError:
        pass
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
