# -*- coding: utf-8 -*-
"""把原生版部署到稳定位置，并重建桌面快捷方式指向它

为什么不能把 exe 留在 work/native_build/dist/：
  那是构建目录，工作区清理或重装时可能被删，快捷方式就断了。
  改部署到 %LOCALAPPDATA%\\FlowLiveNative\\ —— 用户目录下，稳定且不会误删。

★★ v1.2.3：交付形态从「onefile 单文件」改为「onedir 文件夹」。
  原因（实测 A/B，各 5 次取中位）：
      onefile 单文件 ......... 3.67 s（最慢 4.88 s）  ← 每次都解包 69MB 到 %TEMP%
      onedir  文件夹 ......... 1.84 s（1.84~1.96 s）  ← 不解包，省 1.82 s
  解包发生在 Python 代码执行**之前**，任何代码优化都碰不到它，
  只能换形态。onedir 代价是 150MB 级目录（vs 69MB 单文件），
  但用户明确偏好文件夹直传免解压，且快捷方式指向文件夹内 exe，双击体验不变。

★★ v3.0.3：SRC_DIR 由**硬编码**改为**自动探测 + 命令行覆盖**。
  为什么改：旧代码写死 `dist_v125_onedir`，每次改版本都要手工改这一行；
  一旦忘改，就会把**旧构建当成新版本部署**（与"版本号只在标题栏"是同一类
  假成功陷阱 —— 部署看似成功，跑的却是老代码）。
  新逻辑：按版本号从高到低扫描同级的 `dist_v*` 目录，取第一个存在 exe 的；
  也可显式指定：  python deploy.py dist_v303           # 用目录名
                python deploy.py --src=dist_v303/资金流向原生版
  仍保持"打印实际使用的源目录"，便于核对。
"""
import os, sys, shutil, subprocess, tempfile, time, re

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_ICO = os.path.join(BASE, "app.ico")

APPDIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                      "FlowLiveNative")
DST_EXE = os.path.join(APPDIR, "资金流向原生版.exe")
DST_ICO = os.path.join(APPDIR, "app.ico")
# onedir 运行期需要的依赖目录（必须与 exe 同级，缺一个都起不来）
DST_INTERNAL = os.path.join(APPDIR, "_internal")

DESK = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Desktop")
LNK = os.path.join(DESK, "资金流向原生版.lnk")

EXE_NAME = "资金流向原生版.exe"


def _ver_key(name):
    """把 dist_v303 / dist_v304 / dist_v125_onedir 变成可比较的元组。

    规则：抓出所有数字段，主版本优先；带 "onedir" 后缀的降权（旧形态命名）。
    返回 (数字元组, 是否带前缀 v, 原名) —— 保证排序稳定且可复现。
    """
    nums = tuple(int(x) for x in re.findall(r"\d+", name))
    return (nums, 0 if "onedir" in name else 1, name)


def find_src_dir():
    """自动探测最新的 dist_v*/资金流向原生版 目录。"""
    cands = []
    for n in os.listdir(BASE):
        if not n.startswith("dist_"):
            continue
        inner = os.path.join(BASE, n, "资金流向原生版")
        if os.path.exists(os.path.join(inner, EXE_NAME)):
            cands.append((_ver_key(n), inner))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return cands[-1][1]


def resolve_src(argv):
    """命令行 > 自动探测。返回 (src_dir, src_exe, 来源说明)。"""
    args = [a for a in argv if not a.startswith("-")]
    if args:
        a0 = args[0]
        p = a0 if os.path.isabs(a0) else os.path.join(BASE, a0)
        if os.path.basename(p) != "资金流向原生版":
            p = os.path.join(p, "资金流向原生版")
        return p, os.path.join(p, EXE_NAME), f"命令行指定 ({args[0]})"
    auto = find_src_dir()
    if not auto:
        return None, None, "自动探测失败（未找到任何含 exe 的 dist_*/资金流向原生版）"
    return auto, os.path.join(auto, EXE_NAME), "自动探测（版本号最高）"


def kill_running():
    """部署前先关掉正在运行的旧实例，否则 exe 被占用会复制失败。

    ★ 注意：中文 Windows 上 tasklist/taskkill 输出是 GBK 编码，
      用 text=True 默认按 UTF-8 解码会抛 UnicodeDecodeError，所以统一
      抓 bytes 再手动解码。
    """
    try:
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}"],
                           capture_output=True, timeout=30)
        out = (r.stdout or b"").decode("gbk", "ignore")
        if EXE_NAME not in out:
            return False
        print(f"[0] 检测到旧实例在运行，正在关闭 …")
        subprocess.run(["taskkill", "/F", "/IM", EXE_NAME],
                       capture_output=True, timeout=30)
        time.sleep(1.2)
        return True
    except Exception as e:                                # noqa: BLE001
        print("[0] 关闭旧实例时出错（忽略）:", e)
        return False


def main():
    src_dir, src_exe, how_src = resolve_src(sys.argv[1:])
    print(f"[源] {how_src}")
    if not src_dir:
        print("[FAILED]", how_src)
        return 1
    print(f"     {src_dir}")
    if not os.path.exists(src_exe):
        print("[FAILED] 源 exe 不存在:", src_exe)
        return 1

    # ★ 打印源 exe 指纹：部署后回读对比，避免"新 exe 配旧 _internal"
    src_sz = os.path.getsize(src_exe)
    print(f"     源 exe: {src_sz:,} B  mtime={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(src_exe)))}")
    n_src_int = sum(len(f) for _, _, f in os.walk(os.path.join(src_dir, "_internal")))
    print(f"     源 _internal: {n_src_int:,} 个文件")

    kill_running()
    os.makedirs(APPDIR, exist_ok=True)

    # ★ v1.2.3 onedir：exe 与 _internal/ 必须**同级**且成对更新。
    #   先删旧的 _internal 再整体复制：只换 exe 不换 _internal 会
    #   出现"新 exe 配旧依赖"，典型症状是启动即退出或 DLL 缺失弹窗。
    try:
        shutil.copy2(src_exe, DST_EXE)
    except PermissionError as e:
        print(f"[FAILED] 目标 exe 被占用，无法覆盖：{e}")
        print("         请手动关闭正在运行的「资金流向原生版」后重试。")
        return 1
    if os.path.isdir(DST_INTERNAL):
        shutil.rmtree(DST_INTERNAL, ignore_errors=True)
    shutil.copytree(os.path.join(src_dir, "_internal"), DST_INTERNAL,
                    dirs_exist_ok=True)
    n_int = sum(len(f) for _, _, f in os.walk(DST_INTERNAL))
    int_mb = sum(os.path.getsize(os.path.join(r, f))
                 for r, _, fs in os.walk(DST_INTERNAL) for f in fs) / 1048576
    shutil.copy2(SRC_ICO, DST_ICO)
    print(f"[1] 已部署 exe: {DST_EXE}  ({os.path.getsize(DST_EXE):,} B)")
    print(f"    已部署 _internal: {n_int:,} 个文件 / {int_mb:.1f} MB")
    print(f"    已部署 ico: {DST_ICO}")

    # ★★ 部署完整性对账：exe 字节数必须一致，_internal 文件数必须一致。
    #   少一个都说明复制中途失败 —— 直接判 FAILED，不要报"成功"。
    ok_exe = os.path.getsize(DST_EXE) == src_sz
    ok_int = n_int == n_src_int
    print(f"[1.1] 对账 exe 字节 {'一致' if ok_exe else '不一致!!'} · "
          f"_internal 文件数 {'一致' if ok_int else f'不一致!! ({n_int} vs {n_src_int})'}")
    if not (ok_exe and ok_int):
        print("[FAILED] 部署产物与源不一致，可能中途失败。")
        return 1

    # 重建快捷方式（先删旧的，避免残留）
    if os.path.exists(LNK):
        os.remove(LNK)
    try:
        import win32com.client
        sh = win32com.client.Dispatch("WScript.Shell")
        sc = sh.CreateShortCut(LNK)
        sc.TargetPath = DST_EXE
        sc.WorkingDirectory = APPDIR
        sc.IconLocation = DST_ICO + ",0"
        sc.Description = "资金流向 · 纯原生桌面版（无浏览器内核）"
        sc.Save()
        how = "pywin32 COM"
    except Exception:
        vbs = f'''
Set sh = CreateObject("WScript.Shell")
Set sc = sh.CreateShortCut("{LNK}")
sc.TargetPath = "{DST_EXE}"
sc.WorkingDirectory = "{APPDIR}"
sc.IconLocation = "{DST_ICO},0"
sc.Save
'''
        p = os.path.join(tempfile.gettempdir(), "_mklnk2.vbs")
        with open(p, "w", encoding="gbk", errors="replace") as f:
            f.write(vbs)
        subprocess.run(["cscript", "//nologo", p], capture_output=True, timeout=60)
        how = "VBScript"

    ok = os.path.exists(LNK)
    print(f"[2] 快捷方式({how}): {LNK}  {'OK' if ok else 'FAILED'}  "
          f"{os.path.getsize(LNK) if ok else 0} B")

    # 回读校验
    try:
        import win32com.client
        sc = win32com.client.Dispatch("WScript.Shell").CreateShortCut(LNK)
        print(f"[3] 校验 目标={sc.TargetPath}")
        print(f"         存在={os.path.exists(sc.TargetPath)}")
        print(f"         图标={sc.IconLocation}")
    except Exception as e:
        print("[3] 校验跳过:", e)

    print("\n[SUCCESS] 部署完成，桌面「资金流向原生版」双击即可启动"
          if ok else "\n[FAILED]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
