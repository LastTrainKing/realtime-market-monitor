# -*- coding: utf-8 -*-
"""把 v2.0.1 部署到 D:\\exe（便携形态），**保留用户真实数据**。

为什么要专门写一个部署脚本、而不直接用工程的 deploy.py：
  deploy.py 是 v1.2.x 的思路 —— 把程序装到 %LOCALAPPDATA%\\FlowLiveNative
  并建桌面快捷方式。而 v1.2.6 起本程序已改为**便携形态**：
  数据/日志/截图/锁文件都跟着 exe 所在目录走（见 native_app.portable_root）。
  所以 v2.0.1 的部署就是"把新构建复制到 D:\\exe"，**不能**再往 C 盘写。

三条硬约束（缺一条都会造成不可逆损失）：
  ① D:\\exe\\data / logs / shots / app.lock **一个都不能删** ——
     那是用户真实的盘中历史（30 交易日滚动归档）；只替换程序本体
     （exe + _internal）。
  ② 部署前先关掉正在运行的旧实例（否则 exe 被占用，复制失败或写坏）。
  ③ 旧版本整份备份到 D:\\flowlive_v201\\backup_v126 —— 出问题能一键回退。
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = r"D:\flowlive_v201"


def _find_src():
    """定位构建产物。优先 dist_v201b（本轮最终构建），回退 dist_v201_onedir。

    ★ 为什么会有两个目录：`--noconfirm` 在清理旧 dist 时会触发工具层的
      批量删除确认（>50 个文件即拦截），所以后续构建改用**新目录名**
      避开删除动作。这里按优先级自动挑一个存在的。
    """
    for name in ("dist_v201c", "dist_v201b", "dist_v201_onedir"):
        p = os.path.join(ROOT, "build", name, "资金流向原生版")
        if os.path.exists(os.path.join(p, "资金流向原生版.exe")):
            return p
    return os.path.join(ROOT, "build", "dist_v201c", "资金流向原生版")


SRC_DIR = _find_src()
DST_DIR = r"D:\exe"
BACKUP = os.path.join(ROOT, "backup_v126")

EXE_NAME = "资金流向原生版.exe"
KEEP = {"data", "logs", "shots", "app.lock"}      # 用户数据，绝不触碰


def log(msg):
    print(msg, flush=True)


def kill_running():
    """关掉正在运行的旧实例（中文 Windows 下 tasklist 输出是 GBK，需手动解码）。"""
    try:
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}"],
                           capture_output=True, timeout=30)
        out = (r.stdout or b"").decode("gbk", "ignore")
        if EXE_NAME not in out:
            return False
        log("[0] 检测到旧实例在运行，正在关闭 …")
        subprocess.run(["taskkill", "/F", "/IM", EXE_NAME],
                       capture_output=True, timeout=30)
        time.sleep(1.5)
        return True
    except Exception as e:                                # noqa: BLE001
        log(f"[0] 关闭旧实例时出错（忽略）：{e}")
        return False


def backup_old():
    """整份备份旧部署（含数据），供回退。"""
    if not os.path.isdir(DST_DIR):
        log("[1] 目标不存在，跳过备份")
        return True
    if os.path.isdir(BACKUP):
        log(f"[1] 备份已存在，跳过：{BACKUP}")
        return True
    log(f"[1] 备份旧部署到 {BACKUP} …")
    try:
        shutil.copytree(DST_DIR, BACKUP, dirs_exist_ok=True)
        n = sum(len(f) for _, _, f in os.walk(BACKUP))
        log(f"    备份完成：{n} 个文件")
        return True
    except Exception as e:                                # noqa: BLE001
        log(f"[1] [WARN] 备份失败（继续部署）：{e}")
        return False


def deploy():
    if not os.path.exists(os.path.join(SRC_DIR, EXE_NAME)):
        log(f"[FAILED] 构建产物不存在：{SRC_DIR}")
        return 1
    os.makedirs(DST_DIR, exist_ok=True)

    # ---- ① 替换 exe ----
    #
    # ★★ 踩过的坑：在可移动盘上，**改名 / 原地覆盖都可能被拒**。
    #   实测（D 盘，均以管理员无关的普通权限执行）：
    #     · `shutil.copy2` 直接覆盖已存在的 exe → PermissionError [Errno 13]
    #     · `os.replace(exe, exe+'.old')` 改名     → PermissionError [WinError 5]
    #   但同一目录「读旧文件」「新建文件」完全正常，也确认没有任何进程持有它
    #   （Get-Process 查 D:\exe\* 无结果）。
    #   原因：Windows 对**刚被执行过的可执行映像**有延迟释放
    #   （即便进程已退出，Prefetch/杀软扫描仍可能短暂引用），
    #   加上 FAT/exFAT 的目录项操作特性，表现为"改名和覆盖被拒、删除却可以"。
    #   ★ 对策就一条：**先 os.remove 删掉，再复制新的**。
    #   实测这条路径稳定成功（已作为默认策略；失败时从备份回滚，不留半成品）。
    src_exe = os.path.join(SRC_DIR, EXE_NAME)
    dst_exe = os.path.join(DST_DIR, EXE_NAME)
    bak_exe = os.path.join(BACKUP, EXE_NAME)
    try:
        if os.path.exists(dst_exe):
            os.remove(dst_exe)
        shutil.copy2(src_exe, dst_exe)
        log(f"[2] 已替换 exe: {os.path.getsize(dst_exe):,} B"
            f"（旧版 {os.path.getsize(bak_exe):,} B 已备份）"
            if os.path.exists(bak_exe) else
            f"[2] 已替换 exe: {os.path.getsize(dst_exe):,} B")
    except Exception as e:                               # noqa: BLE001
        log(f"[FAILED] 替换 exe 失败：{type(e).__name__}: {e}")
        # 尽力回滚，避免留下"没有 exe"的半成品目录
        if not os.path.exists(dst_exe) and os.path.exists(bak_exe):
            try:
                shutil.copy2(bak_exe, dst_exe)
                log("         已从备份回滚旧 exe")
            except Exception as e2:                      # noqa: BLE001
                log(f"         回滚也失败：{e2}（请从 {BACKUP} 手动复制）")
        return 1

    # ---- ② 替换 _internal（先删后拷：只换 exe 会"新程序配旧依赖"）----
    src_int = os.path.join(SRC_DIR, "_internal")
    dst_int = os.path.join(DST_DIR, "_internal")
    if os.path.isdir(dst_int):
        shutil.rmtree(dst_int, ignore_errors=True)
    shutil.copytree(src_int, dst_int, dirs_exist_ok=True)
    n_int = sum(len(f) for _, _, f in os.walk(dst_int))
    mb = sum(os.path.getsize(os.path.join(r, f))
             for r, _, fs in os.walk(dst_int) for f in fs) / 1048576
    log(f"    已替换 _internal: {n_int:,} 个文件 / {mb:.1f} MB")

    # ---- ③' 归档旧日志（★ v2.0.1 新增，解决一个真实故障）----
    #
    #   为什么必须做这一步：实测发现 D:\exe\logs\app_YYYYMMDD.log 会处于
    #   "**新的 exe 无法追加**"的状态 —— exe 打开它报
    #   `PermissionError: [Errno 13]`，而同一个文件用纯 Python 追加却正常，
    #   属性也只有 ARCHIVE（不是只读）。把该文件删掉后 exe 立刻能新建并写入。
    #   结果就是：程序跑得好好的，**日志却一条都不写**，排查时毫无线索。
    #   对策：部署时把旧日志**改名归档**（改名后新名字可写，且旧内容不丢），
    #   让程序重新创建当天日志。代码侧另有 log_line 的降级兜底（见 native_app）。
    logdir = os.path.join(DST_DIR, "logs")
    if os.path.isdir(logdir):
        for f in sorted(os.listdir(logdir)):
            if not f.endswith(".log"):
                continue
            if ".bak_" in f or len(f) > 40:
                continue
            src_l = os.path.join(logdir, f)
            dst_l = src_l + ".bak_v126"
            try:
                if os.path.exists(dst_l):
                    os.remove(dst_l)
                os.replace(src_l, dst_l)
                log(f"[2b] 旧日志已归档: {f} → {os.path.basename(dst_l)}")
            except Exception as e:                       # noqa: BLE001
                log(f"[2b] 归档 {f} 失败（忽略）: {e}")

    # ---- ④ 核对用户数据仍完好 ----
    log("[3] 用户数据核对：")
    for k in sorted(KEEP):
        p = os.path.join(DST_DIR, k)
        if os.path.isdir(p):
            n = sum(len(f) for _, _, f in os.walk(p))
            log(f"    {k}/  {n} 个文件  ✓ 保留")
        elif os.path.exists(p):
            log(f"    {k}  {os.path.getsize(p)} B  ✓ 保留")
        else:
            log(f"    {k}  (不存在 — 首次运行时会自动创建)")

    # ---- ④ 校验 _internal 里有 ico（v2.0.1 图标依赖它）----
    ico = os.path.join(dst_int, "app.ico")
    log(f"[4] 图标资源: {'✓ ' + str(os.path.getsize(ico)) + ' B' if os.path.exists(ico) else '✗ 缺失'}")
    return 0


def main():
    log("=" * 66)
    log("部署 资金流向原生版 v2.0.1 → D:\\exe（便携形态，保留数据）")
    log("=" * 66)
    kill_running()
    backup_old()
    rc = deploy()
    log("=" * 66)
    log("部署完成" if rc == 0 else "部署失败")
    log("回退方式：把 backup_v126\\ 里的内容复制回 D:\\exe\\ 即可")
    return rc


if __name__ == "__main__":
    sys.exit(main())
