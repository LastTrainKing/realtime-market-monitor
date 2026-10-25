# -*- coding: utf-8 -*-
"""deploy_transactional.py —— **事务式**部署（v3.0.6）

替代 `deploy_to_running.py`。旧脚本有四处不宜直接放行的设计，每一处都可能
把用户正在用的目录搞成"启动即退出"的状态：

  ① **非事务**：先覆盖 exe、再 `rmtree + copytree` 换 `_internal`。
     若在这两步之间失败（磁盘满 / 权限 / 文件占用），结果是
     「新 exe + 旧依赖」或「新 exe + 空依赖」—— 正是旧脚本自己注释里
     说的"启动即退出 / DLL 缺失"那种状态，而它对这种中间态**没有回滚**。
  ② **归档失败只告警**：`except: print("⚠ 归档失败（不阻断部署）")`。
     归档是唯一的回退手段，它失败就意味着**已经没有退路**，却继续部署。
  ③ **快捷方式不一致只提示**：打印一句"请确认到底该部署到哪"，然后照样往
     RUN_DIR 写 —— 于是又可能造出"改了没人用的那一份"（这正是 v3.0.4 复盘
     出来的那类错位）。
  ④ **按镜像名强杀**：`taskkill /F /IM 资金流向原生版.exe` 会杀掉**任何**
     同名进程（包括用户从别处启动的实例），而不是只关这个部署目录里的那个。

本脚本的做法（每一步都可回退）：

  [0] 只读预检：源包 / 目标 / 快捷方式一致性 / 磁盘余量 —— 任一不过就**中止**，
      全程不写盘。快捷方式指向与 RUN_DIR 不同源时**直接 FAILED**（不再只提示）。
  [1] 关停实例：先 `tasklist` 取 PID，再用 ctypes（纯标准库，不依赖 wmic/psutil）
      解析每个 PID 的可执行路径，**只关路径落在 RUN_DIR 里的那些**。
      路径解析不出来的 PID 一律**不杀** —— 宁可报错让用户手动关，也不误杀。
  [2] 暂存：把新包完整复制到 `RUN_DIR` 同级的 `.deploy_stage_<ts>/`
      （同一卷 → 第 4 步的改名是原子的），并在**暂存区**完成全部校验。
  [3] 校验暂存区：exe 的 sha256、`_internal` 文件数与字节数、v3.0.6 专属标志物。
  [4] 快照 + 提交（两阶段改名，同卷原子）：
        阶段 A：`exe` / `_internal` / `版本.txt` / `app.ico` → `*.old_<ts>`
        阶段 B：暂存区同名项 → `RUN_DIR`
      `data/` `logs/` `shots/` `app.lock` **原地不动**（用户数据）。
  [5] 部署后校验：同样的三项对账。
  [6] 任一步失败 → **自动回滚**：删掉刚提交的，把 `*.old_<ts>` 改回原名。
  [7] 启动自检（默认开，`--no-launch` 关闭）：启动 exe，等日志出现
      `preflight ok · v<VERSION>`，随即关闭。**启动失败或版本不符同样触发回滚。**
  [8] 成功后把 `*.old_<ts>` 归档进 `历史版本/`（归档失败只告警 —— 此时部署
      已经成功，旧包留在原地也只是占地方，不再是"没有退路"）。

用法：
  python deploy_transactional.py dist_v306                 # 完整事务部署
  python deploy_transactional.py dist_v306 --dry-run       # 只预检，不写盘
  python deploy_transactional.py dist_v306 --no-launch     # 跳过启动自检
  python deploy_transactional.py dist_v306 --stage-only    # 只做暂存+校验
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_ICO = os.path.join(BASE, "app.ico")
EXE_NAME = "资金流向原生版.exe"
INTERNAL = "_internal"
VER_TXT = "版本.txt"

USERPROFILE = os.environ.get("USERPROFILE") or os.path.expanduser("~")
RUN_DIR = os.path.join(USERPROFILE, "Desktop", "exe", "最新版")
LNK = os.path.join(USERPROFILE, "Desktop", "资金流向.lnk")
HIST = os.path.join(USERPROFILE, "Desktop", "exe", "历史版本")
DESK_EXE = os.path.join(USERPROFILE, "Desktop", "exe")

#: 需要事务化的顶层项（exe / 依赖树 / 版本标记 / 图标）
PAYLOAD = (EXE_NAME, INTERNAL, VER_TXT, "app.ico")

#: 部署后必须存在的 v3.0.6 专属标志物（缺失即说明装的是旧包）。
#  ★ 为什么挑这几个串：它们分别对应本次延迟修复的三个独立机制，
#    旧版本里**一个都不存在**，所以能可靠区分"新包 / 旧包"。
V306_MARKERS = (
    ("core/flowdata.py", "KA_RETRY_AFTER"),        # RC-1 快通道软关闭可恢复
    ("core/flowdata.py", "class _AdaptiveGate"),   # RC-5 AIMD 并发闸门
    ("core/flowdata.py", "BREAKER_WINDOW"),        # 全局熔断（轻）
    ("core/flowdata.py", "BREAKER_HARD_TRIPS"),    # 全局熔断（升级式硬熔断）
    ("core/flowdata.py", "BUILD_DEADLINE"),        # RC-6 全局时限
    ("core/flowdata.py", "OPENER_RULES"),          # 兼容通道健康度分档（2313ms→242ms）
    ("core/flowdata.py", "def _retry_pause"),      # RC-4 抖动停顿
    ("core/flowdata.py", "PANEL_KEEP_RATIO"),      # 面板不降级守卫
    ("core/flowdata.py", "def _alt_build_panel"),  # 替代源：种子 + 逐分钟累积
    ("core/flowdata.py", "def _alt_merge_panel"),  # 替代源：并入板块（老列保历史）
    ("core/source.py", "FLOWLIVE_SOURCE"),         # 数据源暗门（环境变量）
    ("core/source.py", "DEFAULT_PROVIDER"),        # 默认数据源（当前=腾讯）
    ("core/source.py", "minute_flow_history"),     # 能力声明（如实标注）
    ("core/source.py", "def skip_reason"),         # 能力守卫统一入口
    ("core/backfill.py", "day_flow_history"),      # 替代源下不发请求（历史补全）
    ("core/opensnap.py", "market_snapshot"),       # 替代源下不发请求（开盘异动）
    ("core/tradecal.py", "trade_calendar"),        # 替代源下不发请求（交易日历）
    ("core/universe.py", "UNIVERSE_MIN_KEEP"),     # 过滤前提失效时退化（防界面清空）
    ("core/universe.py", "HIST_MIN_POINTS"),       # 分层判据：按"有无历史"而非仅按数量
    ("core/universe.py", "行业层补回有历史"),        # 补回有历史的细分行业
    ("core/salience.py", "_hist"),                 # 特征层：把每列有效点数喂给宇宙
    ("core/flowdata.py", "def _label_of_idx"),     # 快速先行版：分钟序号→标签
    ("core/flowdata.py", "allow_gap=False"),       # 快速先行版：跨缺口先补当前
    ("core/flowdata.py", "partial_history"),       # 快速先行版：空洞标记
)
# ★★ 纪律：**不要**把 `app_main_v203.py` 加进上面这张表。
#   它是 PyInstaller 的 `ENTRY`，会被编译进 exe，`_internal/` 下**没有**这个文件 ——
#   写进来只会让部署永远 FAILED（实测踩到；守卫拒绝部署这个行为本身是对的，
#   说明"校验不到就不放行"这条纪律真的生效了）。
#   主程序的完整性由 `exe_sha256` 与 `构建信息.json` 对账覆盖（见 verify 的 exe 分支）。
# ★★ 纪律：**不要**把 `app_main_v203.py` 加进上面这张表。
#   它是 PyInstaller 的 `ENTRY`，会被编译进 exe，`_internal/` 下**没有**这个文件 ——
#   写进来只会让部署永远 FAILED（实测踩到；守卫拒绝部署这个行为本身是对的，
#   说明"校验不到就不放行"这条纪律真的生效了）。
#   主程序的完整性由 `exe_sha256` 与 `构建信息.json` 对账覆盖（见 verify 的 exe 分支）。


def log(msg=""):
    print(msg, flush=True)


def _ver_key(name):
    return tuple(int(x) for x in re.findall(r"\d+", name))


def find_src(argv):
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
    """不依赖 COM，从 .lnk 字节流里抠出目标路径（UTF-16LE）。"""
    try:
        data = open(lnk, "rb").read()
    except OSError:
        return None
    best = None
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,}", data):
        s = m.group().decode("utf-16le", "ignore")
        if re.match(r"^[A-Za-z]:\\", s) or s.startswith("\\\\"):
            best = s if best is None or len(s) > len(best) else best
    return best


def read_version(path):
    for cand in (os.path.join(path, VER_TXT),
                 os.path.join(os.path.dirname(path), VER_TXT)):
        try:
            with open(cand, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return "(无版本标记)"


#: `*.old_YYYYMMDD_HHMMSS` —— 本脚本自己的快照命名
_OLD_RE = re.compile(r"\.old_\d{8}_\d{6}$")


def find_leftovers(run_dir):
    """找出上一次**中断**留下的 `*.old_*` 快照。

    ★★ 为什么必须显式处理（这是实测踩出来的，不是假想）：
      2026-09-23 14:28 首次执行本脚本时，命令被沙箱拦下、经批准后**重跑了一次**。
      第一次运行在"快照+提交之后、归档之前"中断，于是 `最新版\\` 里留下了
      4 个 `*.old_20260923_142825`（v3.0.5 的 exe / _internal / 版本.txt / app.ico）
      —— 而第二次运行时脚本**完全不知道它们存在**，照常又做了一遍快照与提交。
      结果：目录里同时躺着两代快照，旧版被"孤悬"在那里没人归档，
      而且**没有任何提示**。这类"上一轮没做完"的残留如果不识别，
      久而久之就会把部署目录越堆越乱，且难以判断哪一份才是真基线。
    """
    out = []
    try:
        for n in os.listdir(run_dir):
            if _OLD_RE.search(n):
                out.append(n)
    except OSError:
        pass
    return sorted(out)


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def tree_stats(root):
    """返回 (文件数, 总字节数)。"""
    n = 0
    size = 0
    for r, _d, fs in os.walk(root):
        for f in fs:
            n += 1
            try:
                size += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return n, size


# ------------------------------------------------------------------ 进程
def _pids_of(image_name):
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/FI",
                            f"IMAGENAME eq {image_name}"],
                           capture_output=True, timeout=30)
    except Exception:                                          # noqa: BLE001
        return []
    out = (r.stdout or b"").decode("gbk", "ignore")
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith('"'):
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == image_name.lower():
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def _proc_path(pid):
    """用 ctypes 取进程完整可执行路径。

    ★ 为什么不用 wmic / psutil：本机没有 wmic（Win11 已移除），
      而本项目的硬约束是**零第三方依赖**（psutil 不是标准库）。
      `QueryFullProcessImageNameW` 是 kernel32 的公开 API，ctypes 直接调，
      不需要任何额外包，也不需要管理员权限（同用户进程即可查询）。
    """
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
            return None
        finally:
            k32.CloseHandle(h)
    except Exception:                                          # noqa: BLE001
        return None


def stop_own_instances(run_dir):
    """只关停**可执行路径落在 run_dir 内**的同名进程。

    返回 (已关停数, 无法判定路径的 PID 列表)。
    ★ 路径解析不出来的 PID 一律不杀 —— 误杀用户从别处启动的实例，
      比"让用户手动关一下"的代价大得多。
    """
    pids = _pids_of(EXE_NAME)
    if not pids:
        return 0, []
    tgt = os.path.normcase(os.path.abspath(run_dir))
    killed = 0
    unknown = []
    for pid in pids:
        p = _proc_path(pid)
        if not p:
            unknown.append(pid)
            continue
        if os.path.normcase(os.path.abspath(os.path.dirname(p))) == tgt:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=30)
                killed += 1
            except Exception:                                  # noqa: BLE001
                unknown.append(pid)
        else:
            log(f"     · PID {pid} 运行于 {p} —— 不属于本部署目录，**不关**")
    if killed:
        time.sleep(1.5)
    return killed, unknown


# ------------------------------------------------------------------ 事务
def _rm(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def snapshot(run_dir, ts):
    """阶段 A：把 RUN_DIR 里要替换的顶层项改名为 *.old_<ts>。返回已改名的名字。"""
    done = []
    for name in PAYLOAD:
        cur = os.path.join(run_dir, name)
        if os.path.exists(cur):
            os.rename(cur, cur + f".old_{ts}")
            done.append(name)
    return done


def commit(run_dir, stage, ts):
    """阶段 B：把暂存区同名项改名进 RUN_DIR。"""
    done = []
    for name in PAYLOAD:
        s = os.path.join(stage, name)
        if not os.path.exists(s):
            continue
        os.rename(s, os.path.join(run_dir, name))
        done.append(name)
    return done


def rollback(run_dir, ts):
    """把 *.old_<ts> 改回原名，并删掉本次提交进去的新文件。"""
    restored = []
    for name in PAYLOAD:
        cur = os.path.join(run_dir, name)
        old = cur + f".old_{ts}"
        if os.path.exists(old):
            _rm(cur)
            os.rename(old, cur)
            restored.append(name)
    return restored


def verify(run_dir, expect_sha, expect_n, expect_bytes, label):
    """对账：exe 哈希 + _internal 规模 + v3.0.6 标志物。返回 (ok, 明细列表)。"""
    detail = []
    ok = True
    exe = os.path.join(run_dir, EXE_NAME)
    if not os.path.exists(exe):
        return False, [f"{label}: exe 缺失"]
    got = sha256_file(exe)
    same = (got == expect_sha)
    ok = ok and same
    detail.append(f"{label}: exe sha256 {'一致' if same else '不一致!!'} "
                  f"({got[:12]} vs {expect_sha[:12]})")

    n, b = tree_stats(os.path.join(run_dir, INTERNAL))
    same_n = (n == expect_n)
    same_b = (b == expect_bytes)
    ok = ok and same_n and same_b
    detail.append(f"{label}: _internal {n:,} 文件/{b:,} B "
                  f"{'一致' if (same_n and same_b) else '不一致!!'} "
                  f"(期望 {expect_n:,}/{expect_bytes:,})")

    for rel, marker in V306_MARKERS:
        p = os.path.join(run_dir, INTERNAL, *rel.split("/"))
        try:
            txt = open(p, encoding="utf-8").read()
            has = marker in txt
        except OSError:
            has = False
        ok = ok and has
        detail.append(f"{label}: {rel} 含 v3.0.6 标志 {marker} "
                      f"{'✓' if has else '缺失!!'}")
    return ok, detail


def launch_selfcheck(run_dir, version, timeout=90):
    """启动 exe，等日志出现 `preflight ok · v<version>`，然后关闭。

    返回 (ok, 说明)。★ 这是"启动失败自动回滚"的判据 ——
    只有真的把程序跑起来、且它自己报出了正确版本号，才算部署成功。
    """
    exe = os.path.join(run_dir, EXE_NAME)
    logf = os.path.join(run_dir, "logs",
                        f"app_{time.strftime('%Y%m%d')}.log")
    try:
        os.remove(logf)
    except OSError:
        pass
    try:
        proc = subprocess.Popen([exe], cwd=run_dir,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except Exception as e:                                     # noqa: BLE001
        return False, f"启动失败: {type(e).__name__}: {e}"
    want = f"preflight ok · v{version}"
    deadline = time.time() + timeout
    found = False
    try:
        while time.time() < deadline:
            if proc.poll() is not None and not found:
                # 进程已退出 —— 给它 1 秒把日志刷完再判
                time.sleep(1.0)
                try:
                    if want in open(logf, encoding="utf-8", errors="ignore").read():
                        found = True
                except OSError:
                    pass
                if not found:
                    return False, (f"进程提前退出（exit={proc.returncode}），"
                                   f"日志未出现「{want}」")
            try:
                if want in open(logf, encoding="utf-8", errors="ignore").read():
                    found = True
                    break
            except OSError:
                pass
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)],
                               capture_output=True, timeout=20)
            except Exception:                                  # noqa: BLE001
                pass
        time.sleep(0.5)
    if not found:
        return False, f"超时 {timeout}s 仍未出现「{want}」"

    # ★★ v3.0.6：**只等 preflight 是不够的** —— 实测踩过。
    #   2026-09-23 18:38 那次部署：exe 正常启动、日志出现 `preflight ok · v3.0.6`，
    #   自检**判定通过**；可是紧接着数据加载就崩了：
    #       LOAD FAILED: UnicodeEncodeError: 'gbk' codec can't encode …
    #       LOAD FAILED reason: 数据加载过程中出现异常
    #   界面上是一片空白，而部署脚本说"成功"。
    #   → 所以：确认 preflight 之后再等一会儿（加载要几秒），
    #     扫一遍日志里有没有**失败标记**，有就判失败并触发回滚。
    #   ★ 判据只取"明确的失败词"，不用宽泛的 Error/Exception ——
    #     否则正常的重试日志会被误判成失败（那种误报会让人不再相信自检）。
    time.sleep(12.0)
    try:
        tail = open(logf, encoding="utf-8", errors="ignore").read()
    except OSError:
        tail = ""
    bad_marks = ("LOAD FAILED", "数据加载过程中出现异常", "Traceback (most recent call last)")
    hit = [m for m in bad_marks if m in tail]
    if hit:
        return False, f"启动成功但**数据加载失败**（日志出现 {hit}）"
    return True, f"日志出现「{want}」，且未发现加载失败标记"


# ------------------------------------------------------------------ 主流程
def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    stage_only = "--stage-only" in argv
    no_launch = "--no-launch" in argv

    log("=" * 74)
    log("事务式部署 → 用户实际运行目录")
    log("=" * 74)

    # ---------- [0] 只读预检 ----------
    src_dir, how = find_src(argv)
    log(f"[0] 预检")
    log(f"     源: {how}")
    if not src_dir:
        log(f"[FAILED] {how}")
        return 1
    src_exe = os.path.join(src_dir, EXE_NAME)
    src_int = os.path.join(src_dir, INTERNAL)
    if not os.path.exists(src_exe) or not os.path.isdir(src_int):
        log(f"[FAILED] 源包不完整: exe={os.path.exists(src_exe)} "
            f"_internal={os.path.isdir(src_int)}")
        return 1
    src_ver = read_version(src_dir)
    src_sha = sha256_file(src_exe)
    n_src, b_src = tree_stats(src_int)
    log(f"     {src_dir}")
    log(f"     源版本 {src_ver}  exe {os.path.getsize(src_exe):,} B  "
        f"sha256 {src_sha[:12]}  _internal {n_src:,} 文件/{b_src:,} B")

    # 源包本身必须先带 v3.0.6 标志物 —— 否则后面部署完才发现装错了
    src_ok, src_detail = verify(src_dir, src_sha, n_src, b_src, "源包")
    for d in src_detail:
        log("     " + d)
    if not src_ok:
        log("[FAILED] 源包未通过 v3.0.6 标志物校验 —— 拒绝部署。")
        return 1

    # 快捷方式一致性：**不一致就中止**（旧脚本只提示，于是又改了没人用的那份）
    #
    # ★ 本机实测：`资金流向.lnk` 的目标是**目录** `Desktop\exe\`（1701 B 的 .lnk，
    #   字节流里只有这一个路径候选），用户是从这个目录里再进 `最新版\` 双击 exe 的。
    #   所以判定规则是"**同源**"而不是"完全相等"：
    #     · lnk 指向 exe 文件  → 其所在目录必须是 RUN_DIR；
    #     · lnk 指向目录       → 必须是 RUN_DIR 本身，或 RUN_DIR 的**直接父目录**。
    #   再往外（例如指向 Desktop\）就算不同源 —— 那样无法确认用户到底启动哪一份。
    log(f"     快捷方式: {LNK}")
    if not os.path.exists(LNK):
        log("[FAILED] 快捷方式不存在 —— 无法确认用户实际启动的是哪一份，中止。")
        return 1
    tgt = read_lnk_target(LNK)
    if not tgt:
        log("[FAILED] 无法解析快捷方式目标，中止（不做推测性部署）。")
        return 1
    tdir = (os.path.dirname(tgt.rstrip("\\/"))
            if os.path.splitext(tgt.rstrip("\\/"))[1].lower() == ".exe"
            else tgt.rstrip("\\/"))
    log(f"     指向: {tgt}")
    rd = os.path.normcase(os.path.abspath(RUN_DIR))
    td = os.path.normcase(os.path.abspath(tdir))
    if td != rd and os.path.dirname(rd) != td:
        log(f"[FAILED] 快捷方式指向 {tdir}")
        log(f"         与本脚本的 RUN_DIR 既不相同、也不是它的父目录：")
        log(f"         RUN_DIR = {RUN_DIR}")
        log(f"         —— 无法确认用户启动的是这一份，已中止。")
        return 1
    log(f"     ✓ 与部署目标同源（{'直接指向' if td == rd else '指向其父目录'}）")

    if not os.path.isdir(RUN_DIR):
        log(f"[FAILED] 目标目录不存在: {RUN_DIR}")
        return 1
    old_ver = read_version(RUN_DIR)
    log(f"     目标 {RUN_DIR}")
    log(f"     当前版本 {old_ver}")
    # ★ 上一次中断的残留必须先报出来（见 find_leftovers 的实测说明）
    leftovers = find_leftovers(RUN_DIR)
    if leftovers:
        log(f"     ⚠ 发现 {len(leftovers)} 个**上一次中断部署**留下的快照：")
        for n in leftovers:
            log(f"       · {n}")
        log(f"       本次部署成功后会一并归档，不会丢；也不会被当成基线。")
    try:
        free = shutil.disk_usage(DESK_EXE).free
        need = b_src + os.path.getsize(src_exe) + (32 << 20)
        log(f"     磁盘余量 {free / 1048576:.0f} MB（暂存+快照需要约 "
            f"{need / 1048576:.0f} MB）")
        if free < need:
            log("[FAILED] 磁盘余量不足，中止。")
            return 1
    except OSError:
        pass

    if dry:
        log("\n[dry-run] 预检全部通过，未改动任何文件。")
        return 0

    # ---------- [1] 关停本目录实例 ----------
    log(f"\n[1] 关停本目录正在运行的实例")
    killed, unknown = stop_own_instances(RUN_DIR)
    log(f"     已关停 {killed} 个")
    if unknown:
        log(f"[FAILED] 有 {len(unknown)} 个同名进程无法确认路径（PID {unknown}）")
        log("         为避免误杀用户从别处启动的实例，已中止。请手动关闭后重试。")
        return 1

    ts = time.strftime("%Y%m%d_%H%M%S")
    stage = os.path.join(DESK_EXE, f".deploy_stage_{ts}")
    rollback_done = False

    try:
        # ---------- [2] 暂存 ----------
        log(f"\n[2] 暂存新包 → {stage}")
        if os.path.exists(stage):
            _rm(stage)
        os.makedirs(stage, exist_ok=True)
        shutil.copy2(src_exe, os.path.join(stage, EXE_NAME))
        shutil.copytree(src_int, os.path.join(stage, INTERNAL))
        shutil.copy2(os.path.join(src_dir, VER_TXT)
                     if os.path.exists(os.path.join(src_dir, VER_TXT))
                     else os.path.join(os.path.dirname(src_dir), VER_TXT),
                     os.path.join(stage, VER_TXT))
        if os.path.exists(SRC_ICO):
            shutil.copy2(SRC_ICO, os.path.join(stage, "app.ico"))

        # ---------- [3] 校验暂存区 ----------
        log(f"\n[3] 校验暂存区（**在动 RUN_DIR 之前**）")
        ok, detail = verify(stage, src_sha, n_src, b_src, "暂存")
        for d in detail:
            log("     " + d)
        if not ok:
            log("[FAILED] 暂存区校验未通过 —— RUN_DIR 尚未被改动，直接退出。")
            _rm(stage)
            return 1
        if stage_only:
            log(f"\n[stage-only] 暂存区已就绪并通过校验：{stage}")
            log("             （未改动 RUN_DIR）")
            return 0

        # ---------- [4] 快照 + 提交（两阶段改名，同卷原子） ----------
        log(f"\n[4] 快照旧版并提交新包（同卷改名，原子）")
        snap = snapshot(RUN_DIR, ts)
        log(f"     快照: {snap if snap else '(目标为空，无需快照)'}")
        try:
            com = commit(RUN_DIR, stage, ts)
        except Exception as e:                                 # noqa: BLE001
            log(f"     !! 提交失败: {type(e).__name__}: {e}")
            log(f"     → 自动回滚")
            rollback(RUN_DIR, ts)
            rollback_done = True
            raise
        log(f"     提交: {com}")

        # ---------- [5] 部署后校验 ----------
        log(f"\n[5] 部署后校验")
        ok, detail = verify(RUN_DIR, src_sha, n_src, b_src, "部署")
        for d in detail:
            log("     " + d)
        if not ok:
            log("[FAILED] 部署后校验未通过 → 自动回滚")
            rollback(RUN_DIR, ts)
            rollback_done = True
            return 1

        # ---------- [6] 启动自检 ----------
        if no_launch:
            log(f"\n[6] 跳过启动自检（--no-launch）")
        else:
            log(f"\n[6] 启动自检（等日志出现 preflight ok · v{src_ver}）")
            ok, why = launch_selfcheck(RUN_DIR, src_ver)
            log(f"     {'✓' if ok else '!!'} {why}")
            if not ok:
                log("[FAILED] 启动自检未通过 → 自动回滚")
                rollback(RUN_DIR, ts)
                rollback_done = True
                return 1

        # ---------- [7] 归档旧版 ----------
        log(f"\n[7] 归档旧版 → {HIST}")
        os.makedirs(HIST, exist_ok=True)
        stem = (f"资金流向原生版_v{old_ver}"
                if old_ver != "(无版本标记)" else "资金流向原生版_未标记")
        arch = os.path.join(HIST, f"{stem}_{ts}")
        try:
            os.makedirs(arch, exist_ok=True)
            moved = 0
            for name in PAYLOAD:
                old = os.path.join(RUN_DIR, name + f".old_{ts}")
                if os.path.exists(old):
                    os.rename(old, os.path.join(arch, name))
                    moved += 1
            log(f"     ✓ 已归档本次快照 {moved} 项 → {arch}")
            # ★ 上一次中断留下的快照也一并归档，放进子目录以便区分来源。
            #   子目录名带上**残留自己的版本号** —— 它可能和本次的 old_ver 不同
            #   （实测：残留其实是 v3.0.5，而 old_ver 已被上一次中断提交成了 3.0.6）。
            #   命名必须与内容一致，否则以后回退时会挑错包。
            if leftovers:
                lv = "(未标记)"
                for n in leftovers:
                    if n.startswith(VER_TXT):
                        try:
                            with open(os.path.join(RUN_DIR, n),
                                      encoding="utf-8") as f:
                                lv = f.read().strip()
                        except OSError:
                            pass
                        break
                sub = os.path.join(arch, f"_上次中断残留_v{lv}")
                os.makedirs(sub, exist_ok=True)
                n2 = 0
                for n in leftovers:
                    try:
                        os.rename(os.path.join(RUN_DIR, n),
                                  os.path.join(sub, n))
                        n2 += 1
                    except OSError:
                        pass
                log(f"     ✓ 已归档上次中断残留 {n2}/{len(leftovers)} 项"
                    f"（版本 {lv}）→ {sub}")
        except Exception as e:                                 # noqa: BLE001
            # 此时部署**已经成功**，旧包留在原地只是占地方，不再是"没有退路"
            log(f"     ⚠ 归档失败（部署本身已成功，不影响）: {e}")

        log("\n" + "=" * 74)
        log(f"[SUCCESS] v{src_ver} 已事务式部署到用户实际运行目录")
        log(f"          桌面「资金流向」双击即启动 v{src_ver}")
        log("=" * 74)
        return 0

    finally:
        _rm(stage)
        if rollback_done:
            log(f"     回滚完成，目标版本已恢复为 {read_version(RUN_DIR)}")


if __name__ == "__main__":
    sys.exit(main())
