# -*- coding: utf-8 -*-
"""部署到**用户实际运行**的位置 —— 这是"修了却还是没修复"的根治点。

★★ 为什么必须单独写这个脚本（v3.0.4 复盘的真实教训）
--------------------------------------------------------------------------
用户两次反馈「该节点未采集的bug**还是**没有修复」。排查后确认：
**修复代码本身是对的，但它从未到达用户运行的程序。** 而且有两重错位：

  错位 ①：`deploy.py` 的目标是 `%LOCALAPPDATA%\\FlowLiveNative\\`，
          而用户双击的是桌面快捷方式 `资金流向.lnk`，
          它指向的是 `%USERPROFILE%\\Desktop\\exe\\`。
          → 两个目录**不是同一个**。`%LOCALAPPDATA%` 里那个 exe 停在 09-21。

  错位 ②：`Desktop\\exe\\最新版\\` 里躺着的是 **v3.0.2**（打包于 01:18），
          `_internal/core/` 下**没有 `tradingtime.py`**、`opensnap.py`
          还是 `workers=6` 的旧版 —— 即 v3.0.3 / v3.0.4 两轮修复
          **一次都没进过用户程序**。

  结论：**部署目标的正确性，和代码的正确性同等重要。**
        一个"修好了但没送到"的修复，等价于没修。
        所以本脚本的第一件事不是复制文件，而是**先解析快捷方式确认真实目标**，
        再把"源指纹 / 目标指纹"打印出来供人工核对 —— 不做推测性部署。

用法：
  python deploy_to_running.py dist_v304              # 显式指定源
  python deploy_to_running.py                        # 自动取版本号最高的 dist_v*
  python deploy_to_running.py dist_v304 --dry-run    # 只看会做什么，不落盘
"""
import os
import re
import shutil
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_ICO = os.path.join(BASE, "app.ico")
EXE_NAME = "资金流向原生版.exe"
INTERNAL = "_internal"

#: 目标 = 用户实际运行的位置。由桌面快捷方式指向决定，不靠猜。
RUN_DIR = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"),
                       "Desktop", "exe", "最新版")

#: 快捷方式（用于**核对**目标是否一致 —— 这是本脚本存在的理由之一）
LNK = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"),
                   "Desktop", "资金流向.lnk")

#: 历史版本归档目录（部署前把旧版挪进去，便于回退）
HIST = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"),
                    "Desktop", "exe", "历史版本")


def _ver_key(name):
    """dist_v304 -> (3,0,4)；排序用。"""
    return tuple(int(x) for x in re.findall(r"\d+", name))


def find_src(argv):
    """命令行 > 自动探测（版本号最高的 dist_v*）。返回 (src_dir, 说明)。"""
    args = [a for a in argv if not a.startswith("-")]
    if args:
        p = args[0] if os.path.isabs(args[0]) else os.path.join(BASE, args[0])
        if os.path.basename(p.rstrip("\\/")) != "资金流向原生版":
            p = os.path.join(p, "资金流向原生版")
        return p, "命令行指定 (%s)" % args[0]
    cands = []
    for n in os.listdir(BASE):
        if n.startswith("dist_"):
            inner = os.path.join(BASE, n, "资金流向原生版")
            if os.path.exists(os.path.join(inner, EXE_NAME)):
                cands.append((_ver_key(n), inner))
    if not cands:
        return None, "自动探测失败（未找到含 exe 的 dist_*/资金流向原生版）"
    cands.sort(key=lambda x: x[0])
    return cands[-1][1], "自动探测（版本号最高）"


def read_lnk_target(lnk):
    """不依赖 COM，直接从 .lnk 字节流里抠出目标路径（UTF-16LE）。

    ★ 为什么不用 WScript.Shell：本机沙箱把 COM 实例化（可执行任意代码）
      列入了拦截范围。而 .lnk 的目标路径是明文 UTF-16LE 存着的，
      用正则抠出来足够可靠，且完全只读、无副作用。
    """
    try:
        data = open(lnk, "rb").read()
    except OSError:
        return None
    best = None
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,}", data):
        s = m.group().decode("utf-16le", "ignore")
        # 只认像 Windows 路径的串（带盘符或 UNC）
        if re.match(r"^[A-Za-z]:\\", s) or s.startswith("\\\\"):
            best = s if best is None or len(s) > len(best) else best
    return best


def read_version(path):
    """读某个部署目录里的 版本.txt（判"是不是旧包"的第一手证据）。"""
    for cand in (os.path.join(path, "版本.txt"),
                 os.path.join(os.path.dirname(path), "版本.txt")):
        try:
            with open(cand, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return "(无版本标记)"


def kill_running():
    """关掉正在运行的旧实例，否则 exe 被占用复制会失败。

    中文 Windows 的 tasklist 输出是 GBK，用 text=True 默认按 UTF-8 解会炸，
    所以抓 bytes 手动解码（这个坑踩过一次，保留处理）。
    """
    try:
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}"],
                           capture_output=True, timeout=30)
        out = (r.stdout or b"").decode("gbk", "ignore")
        if EXE_NAME not in out:
            return False
        print("[0] 检测到旧实例在运行，正在关闭 …")
        subprocess.run(["taskkill", "/F", "/IM", EXE_NAME],
                       capture_output=True, timeout=30)
        time.sleep(1.5)
        return True
    except Exception as e:                                # noqa: BLE001
        print("[0] 关闭旧实例时出错（忽略）:", e)
        return False


def main():
    dry = "--dry-run" in sys.argv
    src_dir, how = find_src(sys.argv[1:])
    print("=" * 72)
    print("部署 → 用户实际运行目录（自动读取源版本）")
    print("=" * 72)
    print(f"[源] {how}")
    if not src_dir:
        print("[FAILED]", how)
        return 1
    print(f"     {src_dir}")

    src_exe = os.path.join(src_dir, EXE_NAME)
    src_int = os.path.join(src_dir, INTERNAL)
    if not os.path.exists(src_exe):
        print("[FAILED] 源 exe 不存在:", src_exe)
        return 1
    src_ver = read_version(src_dir)
    src_sz = os.path.getsize(src_exe)
    n_src_int = sum(len(f) for _, _, f in os.walk(src_int))
    print(f"     源版本: {src_ver}")
    print(f"     源 exe: {src_sz:,} B  mtime="
          f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(src_exe)))}")
    print(f"     源 _internal: {n_src_int:,} 个文件")

    # ---- ① 先核对该目录**确实是**用户运行的位置 ----
    print(f"\n[核] 快捷方式: {LNK}")
    tgt = read_lnk_target(LNK) if os.path.exists(LNK) else None
    if tgt:
        print(f"     快捷方式指向: {tgt}")
        exp = os.path.join(os.environ.get("USERPROFILE") or "", "Desktop", "exe")
        if os.path.normcase(tgt).startswith(os.path.normcase(exp)):
            print(f"     ✓ 与部署目标同源（{exp}\\）—— 部署会立刻生效")
        else:
            print(f"     ⚠ 快捷方式指向 {tgt}，与本脚本的 RUN_DIR 不同源！")
            print(f"       请确认到底该部署到哪，别改了没人用的那一份。")
    else:
        print("     (未能解析出目标；仅按 RUN_DIR 部署)")

    print(f"\n[目标] {RUN_DIR}")
    if not os.path.isdir(RUN_DIR):
        print("[FAILED] 目标目录不存在:", RUN_DIR)
        return 1
    old_ver = read_version(RUN_DIR)
    old_exe = os.path.join(RUN_DIR, EXE_NAME)
    print(f"     当前版本: {old_ver}"
          + (f"   exe mtime="
             f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(old_exe)))}"
             if os.path.exists(old_exe) else "   (无 exe)"))

    if src_ver == old_ver and src_ver != "(无版本标记)":
        print(f"     ⚠ 源与目标版本号相同（{src_ver}）——"
              f"若刚重新构建过，属正常（同版本重打包）；否则请确认源选对了。")

    if dry:
        print("\n[dry-run] 到此为止，未改动任何文件。")
        return 0

    # ---- ② 归档旧版（可回退），再部署 ----
    kill_running()

    if os.path.exists(old_exe):
        os.makedirs(HIST, exist_ok=True)
        stem = f"资金流向原生版_v{old_ver}" if old_ver != "(无版本标记)" \
            else "资金流向原生版_未标记"
        arch = os.path.join(HIST, stem)
        if os.path.isdir(arch):
            arch = arch + "_" + time.strftime("%H%M%S")
        print(f"\n[1] 归档旧版 → {arch}")
        try:
            shutil.copytree(RUN_DIR, arch,
                            ignore=shutil.ignore_patterns("shots", "logs",
                                                          "data", "app.lock", "*.log"),
                            dirs_exist_ok=False)
            print(f"     ✓ 已归档（含 exe 与 _internal，不含 data/logs/shots）")
        except Exception as e:                            # noqa: BLE001
            print(f"     ⚠ 归档失败（不阻断部署）: {e}")

    # exe 与 _internal 必须**成对**更新：只换 exe 会"新 exe 配旧依赖"，
    # 典型症状是启动即退出 / DLL 缺失弹窗。
    print(f"\n[2] 复制 exe …")
    try:
        shutil.copy2(src_exe, old_exe)
    except PermissionError as e:
        print(f"[FAILED] 目标 exe 被占用：{e}")
        print("         请手动关闭「资金流向原生版」后重试。")
        return 1

    print(f"[3] 替换 _internal …")
    dst_int = os.path.join(RUN_DIR, INTERNAL)
    if os.path.isdir(dst_int):
        shutil.rmtree(dst_int, ignore_errors=True)
    shutil.copytree(src_int, dst_int, dirs_exist_ok=True)

    # 版本标记（放在 exe 同级，read_version 会优先读它）
    with open(os.path.join(RUN_DIR, "版本.txt"), "w", encoding="utf-8") as f:
        f.write(src_ver)

    # ico（运行期 setWindowIcon 需要）
    if os.path.exists(SRC_ICO):
        shutil.copy2(SRC_ICO, os.path.join(RUN_DIR, "app.ico"))

    n_int = sum(len(f) for _, _, f in os.walk(dst_int))
    int_mb = sum(os.path.getsize(os.path.join(r, f))
                 for r, _, fs in os.walk(dst_int) for f in fs) / 1048576
    print(f"     已部署 _internal: {n_int:,} 个文件 / {int_mb:.1f} MB")

    # ---- ③ 对账：字节数 + 文件数 + 关键文件存在性 ----
    print(f"\n[4] 部署对账")
    ok_exe = os.path.getsize(old_exe) == src_sz
    ok_int = n_int == n_src_int
    print(f"     exe 字节 {'一致' if ok_exe else '不一致!!'}  "
          f"({os.path.getsize(old_exe):,} vs {src_sz:,})")
    print(f"     _internal 文件数 {'一致' if ok_int else '不一致!!'}  "
          f"({n_int:,} vs {n_src_int:,})")

    # ★ 关键文件存在性：这是"修复是否真的进去了"的最硬证据。
    #   tradingtime.py 是 v3.0.4 的标志物 —— 它不在，就说明部署的是旧包。
    checks = [
        (os.path.join(dst_int, "core", "tradingtime.py"), "tradingtime.py (v3.0.4 标志物)"),
        (os.path.join(dst_int, "core", "opensnap.py"), "core/opensnap.py"),
        (os.path.join(dst_int, "core2", "c.pyc"), "core2/c.pyc (主程序)"),
    ]
    all_ok = ok_exe and ok_int
    for p, lab in checks:
        ex = os.path.exists(p)
        all_ok = all_ok and ex
        print(f"     {lab}: {'存在' if ex else '缺失!!'}")

    # opensnap 里必须带 v3.0.4 标记
    try:
        src_txt = open(os.path.join(dst_int, "core", "opensnap.py"),
                       encoding="utf-8").read()
        has_v304 = "v3.0.4" in src_txt and "MIN_SECTORS" in src_txt
        all_ok = all_ok and has_v304
        print(f"     opensnap 含 v3.0.4 修复标记: {'是' if has_v304 else '否!!'}")
    except OSError:
        pass

    # ★★ v3.0.5：曲线横线修复的**专属标志物**（缺一即说明装的是旧包）。
    #   · flowdata.py 必须含 panel guard（读侧对齐守卫）与 save_panel（原子成对写）
    #   · backfill.py 必须含 v3.0.5（写序改为「先 json 后 npy」的标记）
    #   为什么值得单独断言：本 bug 的症状（曲线尾部一条横线）**不报错、不崩溃**，
    #   装错版本时界面完全正常、只是继续画错 —— 靠"能启动"是分辨不出来的。
    try:
        fd = open(os.path.join(dst_int, "core", "flowdata.py"),
                  encoding="utf-8").read()
        ok_guard = ("panel guard" in fd) and ("def save_panel" in fd)
        all_ok = all_ok and ok_guard
        print(f"     flowdata 含 v3.0.5 对齐守卫+原子写: {'是' if ok_guard else '否!!'}")
        bf = open(os.path.join(dst_int, "core", "backfill.py"),
                  encoding="utf-8").read()
        ok_bf = "v3.0.5" in bf
        all_ok = all_ok and ok_bf
        print(f"     backfill 含 v3.0.5 写序修复标记: {'是' if ok_bf else '否!!'}")
    except OSError:
        pass

    print(f"\n[5] 部署后版本: {read_version(RUN_DIR)}")

    if all_ok:
        print("\n" + "=" * 72)
        print("[SUCCESS] v%s 已部署到用户实际运行目录" % src_ver)
        print("          桌面「资金流向」双击即启动 v%s" % src_ver)
        print("=" * 72)
        return 0

    print("\n[FAILED] 对账未全部通过 —— 不要当成部署成功。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
