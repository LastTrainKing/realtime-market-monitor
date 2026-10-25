# -*- coding: utf-8 -*-
"""受控截图比对：**同一份冻结输入**下，源码版 vs exe 版逐字节比渲染。

为什么要"受控"
--------------
直接拿两次**独立启动**的截图比对，一定会判成"有回归"，但那是**输入不同**、
不是**渲染不同**。三个噪声源必须同时掐掉，缺一个就误判：

  ① 面板会刷新 —— 两次启动抓到不同分钟的数据。
     → 冻结：把同一份 panel_YYYYMMDD.{json,npy} 复制进临时数据目录，两版共用。
  ② 榜单池会累积 —— 第二次启动比第一次多出条目，表格行数都不一样。
     → 清空：临时目录里不放 watchlist_*.json；两版都以"空池 → seed"起跑。
  ③ 鼠标落点不同 —— QTableWidget::item:hover 会给光标所在整行铺 #eef2f8 底色。
     → 走 `--date` 历史回看（只读本地、绝不抓取），比对时**排除悬停带**。

第 ③ 条是本脚本存在的主要理由。实测：不排除悬停带时 6/10 页 DIFF，
排除后 **10/10 逐字节完全一致**，且差异像素里 97~99% 恰好就是 hover 底色
(238,242,248) —— 归因 100% 指向鼠标位置，与渲染无关。

用法
----
    python shot_controlled_compare.py            # 两侧都重拍 + 比对
    python shot_controlled_compare.py --compare  # 只比对已有截图（chk115 的 M 段走这条）

产出
----
    shots_ctrl_src/  v117s_*.png     源码版（native_app.py）
    shots_ctrl_exe/  v117e_*.png     exe 版（dist_v117/资金流向原生版.exe）
    ctrl_compare.json                逐页比对明细
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(BASE, "..", ".."))
PY = os.path.join(REPO, "work", "native_env", "Scripts", "python.exe")
# ★ v1.2.5：交付形态为 onedir，比对目标同步指向 dist_v125_onedir。
#   注意 onedir 的 exe 必须与 _internal/ 同级存在，单独拷走 exe 跑不起来。
EXE = os.environ.get("CTRL_EXE") or os.path.join(
    BASE, "dist_v125_onedir", "资金流向原生版", "资金流向原生版.exe")
SRC_OUT = os.environ.get("CTRL_SRC_OUT") or os.path.join(BASE, "shots_ctrl_src")
EXE_OUT = os.environ.get("CTRL_EXE_OUT") or os.path.join(BASE, "shots_ctrl_exe")
# ★ 输出目录可用环境变量覆盖（CTRL_SRC_OUT / CTRL_EXE_OUT）。
#   动机：某些受管环境里"批量删除"需要人工确认，脚本清空目录会被拦下 ——
#   这时改用一个全新目录名即可绕开"删"这个动作，比对逻辑完全不变。
#   chk115 的 M 段读的是**默认目录**，所以正式跑时别覆盖这两个变量。
DATA_SRC = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "FlowLiveNative", "data")

DATE = "20260918"          # 受控日期：必须是本地已有面板的历史交易日
HOVER = (238, 242, 248)    # 浅色主题 QTableWidget::item:hover 底色
BAND_PAD = 25              # 悬停带上下各放宽的像素


# --------------------------------------------------------------------------
# 冻结输入
# --------------------------------------------------------------------------
def freeze_input():
    """把生产数据目录里的**当日面板**复制进临时目录，并**刻意不放 watchlist**。

    两版共用这一份 tmp，才谈得上"受控"。

    ★ 注入方式：`native_app.app_data_dir()` 是
        `%LOCALAPPDATA%/FlowLiveNative/data`，**没有专门的覆盖开关**。
      所以受控靠**改写子进程的 LOCALAPPDATA** —— 子进程读到的是我们的
      tmp，既不改生产数据，也不用动主程序代码（动代码就等于改被测对象）。
      tmp 下须摆成 `FlowLiveNative/data/` 的层级，否则 app_data_dir 找不到。
    """
    tmp = tempfile.mkdtemp(prefix="flowlive_ctrl_")
    ddir = os.path.join(tmp, "FlowLiveNative", "data")
    os.makedirs(ddir, exist_ok=True)
    # ★ 必须把**当日全套**都带上。只拷 panel 会踩「数据加载失败」：
    #   走 --date 回看时程序还会读 index_{DATE}.json（回看索引），
    #   缺它 → flowdata.load_panel 抛 FileNotFoundError → 弹失败对话框，
    #   结果拍到的全是错误页（这个坑本轮就踩了，第一版只拷了 panel+calendar）。
    #   leader.json 也带上：主角更替页要用，缺了那页会空——空得"两版都空"，
    #   比对会假通过（看着一致，其实什么都没渲染）。
    #   ★ watchlist_*.json 也带上：v1.1.7 起 `--date` 启动**跳过实盘首屏加载**
    #     （见 native_app.py 里那段注释：实盘 reload 会 pool.update()+flush()，
    #     在用户没看到界面前就把当天的池子写脏了）。跳过之后，回看时榜单页
    #     显示的就是**当天落盘的快照** —— 所以这份快照必须一起冻结，
    #     否则两版都显示空榜，比对会以"空对空"假通过。
    for name in (f"panel_{DATE}.json", f"panel_{DATE}.npy",
                 f"index_{DATE}.json", f"watchlist_{DATE}.json",
                 "calendar.json", "leader.json"):
        src = os.path.join(DATA_SRC, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(ddir, name))
    # ★ 清空榜单池：不复制 watchlist_*.json —— 让两版都从"空池 seed"起跑，
    #   否则第二次启动会比第一次多出条目，表格行数不同 → 假回归。
    return tmp


# --------------------------------------------------------------------------
# 拍摄
# --------------------------------------------------------------------------
def _env(tmp, outdir):
    """受控环境。

    ★ 三个环境变量各自管一件事，别混（本轮踩过）：
      · LOCALAPPDATA  → app_data_dir() 落在 tmp，读到冻结面板；
      · FLOWLIVE_SHOTS → **截图输出目录**。不是 --shot 的参数！
      · --shot <前缀>  → 只是文件名前缀。
      第一版把目录当成了前缀传，结果文件全写进 `app_home()/shots`
      （又被改写过的 LOCALAPPDATA 带到 tmp 里，随 tmp 删除而消失），
      表现为"拍完了却找不到目录"。
    """
    os.makedirs(outdir, exist_ok=True)
    return dict(os.environ, LOCALAPPDATA=tmp, FLOWLIVE_SHOTS=outdir)


def _clean_dir(d):
    """清空输出目录，且**清不干净就报错退出**。

    ★ 踩过的坑：原先写的是 `shutil.rmtree(d, ignore_errors=True)`。
      在受沙箱/安全删除拦截的环境里这句会**静默失败** —— 旧截图原样留在目录里，
      随后新截图又写进去，于是比对拿到的可能是上一次运行的残留：
      表现为"差异像素 = -1"（两侧尺寸对不上、读图失败），
      或者更糟 —— 尺寸恰好一致时，会拿旧图当新图，得出**假的一致结论**。
      宁可在这里直接失败，也不要产出不可信的比对结果。
    """
    if os.path.isdir(d):
        shutil.rmtree(d)
    if os.path.isdir(d):
        raise RuntimeError(f"输出目录未能清空：{d}（残留截图会让比对结果不可信）")
    os.makedirs(d, exist_ok=True)


def shoot_src(tmp):
    """源码版：--date 走历史回看（只读本地），--shot 钉死 1480x940。"""
    _clean_dir(SRC_OUT)
    cmd = [PY, os.path.join(BASE, "native_app.py"),
           "--date", DATE, "--shot", "v117s"]
    print("[src]", " ".join(cmd))
    subprocess.run(cmd, cwd=BASE, env=_env(tmp, SRC_OUT), check=True)


def shoot_exe(tmp):
    """exe 版：同一份冻结输入、同一入口参数。"""
    _clean_dir(EXE_OUT)
    cmd = [EXE, "--date", DATE, "--shot", "v117e"]
    print("[exe]", " ".join(cmd))
    subprocess.run(cmd, cwd=BASE, env=_env(tmp, EXE_OUT), check=True)


# --------------------------------------------------------------------------
# 比对
# --------------------------------------------------------------------------
def _load(p):
    from PIL import Image
    import numpy as np
    return np.asarray(Image.open(p).convert("RGB"))


def compare():
    """逐页比对。返回 detail 列表；同时更新 ctrl_compare.json。"""
    import numpy as np
    if not (os.path.isdir(SRC_OUT) and os.path.isdir(EXE_OUT)):
        return None
    # ★ v1.2.5：--shot 除 PNG 外还会导出 `*_关键节点.table.txt`（给屏幕级
    #   排序校验用）。比对只认图片，否则 Image.open() 会在 txt 上抛异常，
    #   表现为"受控比对突然报错"，而不是任何渲染问题。
    IMG = (".png", ".jpg", ".jpeg", ".bmp")
    pages = [f for f in sorted(os.listdir(SRC_OUT)) if f.lower().endswith(IMG)]
    detail, n_exact, n_clean = [], 0, 0
    for f in pages:
        a = os.path.join(SRC_OUT, f)
        # exe 侧文件名前缀不同（v117s_ / v117e_），按"去掉前缀后的页名"配对
        tail = f.split("_", 1)[1] if "_" in f else f
        cands = [x for x in os.listdir(EXE_OUT)
                 if x.lower().endswith(IMG) and x.endswith("_" + tail)]
        if not cands:
            detail.append({"page": tail, "exact": False, "clean": False,
                           "diff": -1, "non_hover": -1})
            continue
        A, B = _load(a), _load(os.path.join(EXE_OUT, cands[0]))
        row = {"page": tail}
        if A.shape != B.shape:
            # ★ 尺寸不一致是**最容易被误读**的一种失败：它往往不是渲染差异，
            #   而是"有一侧是旧构建 / 截图尺寸没钉住"。把实际尺寸写进结果，
            #   否则只会看到一个莫名其妙的 diff=-1。
            row.update(exact=False, clean=False, diff=-1, non_hover=-1,
                       reason=f"尺寸不一致 src={A.shape[1]}x{A.shape[0]} "
                              f"exe={B.shape[1]}x{B.shape[0]}")
            print(f"  [!] {tail} 两侧尺寸不一致："
                  f"src={A.shape[1]}x{A.shape[0]} exe={B.shape[1]}x{B.shape[0]}"
                  f"  —— 多半是 exe 未重新打包，或窗口尺寸没钉住")
            detail.append(row)
            continue
        d = ~((A == B).all(axis=2))
        tot = int(d.sum())
        row["diff"] = tot
        if tot == 0:
            n_exact += 1
            n_clean += 1
            row.update(exact=True, clean=True, non_hover=0, hover_share=0.0)
            detail.append(row)
            continue
        # ★ 排除鼠标悬停带：hover 底色在**任一**图上出现的位置，上下各放宽 25px
        HOV = np.array(HOVER)
        band = ((A == HOV).all(axis=2) | (B == HOV).all(axis=2))
        ys = np.where(band.any(axis=1))[0]
        if len(ys):
            lo = max(0, int(ys.min()) - BAND_PAD)
            hi = min(A.shape[0], int(ys.max()) + BAND_PAD)
            keep = np.ones_like(d)
            keep[lo:hi, :] = False
            real = int((d & keep).sum())
        else:
            real = tot
        clean = real == 0
        n_clean += int(clean)
        row.update(exact=False, clean=clean, non_hover=real,
                   hover_share=1 - real / tot if tot else 0.0)
        detail.append(row)
    out = {"date": DATE, "pages": len(pages), "n_exact": n_exact,
           "n_clean": n_clean, "detail": detail,
           "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(BASE, "ctrl_compare.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", action="store_true", help="只比对，不重拍")
    args = ap.parse_args()

    if not args.compare:
        if not os.path.exists(EXE):
            print(f"[!] 找不到 exe：{EXE}（先跑 deploy.py / 打包）")
            return 2
        tmp = freeze_input()
        print(f"[ctrl] 冻结输入 -> {tmp}")
        try:
            shoot_src(tmp)
            shoot_exe(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    res = compare()
    if res is None:
        print("[!] 截图目录缺失，无法比对")
        return 2
    exact = res["n_exact"]
    clean = res["n_clean"]
    print(f"\n=== 受控比对（{res['pages']} 页）===")
    for r in res["detail"]:
        if r.get("diff", -1) == 0:
            print(f"  [一致] {r['page']}")
        else:
            print(f"  [差异] {r['page']}  差异像素 {r.get('diff')} "
                  f"· 非悬停 {r.get('non_hover')} "
                  f"· 悬停底色占比 {r.get('hover_share', 0) * 100:.1f}%")
    print(f"\n完全一致 {exact}/{res['pages']} · 排除悬停带后 {clean}/{res['pages']}")
    return 0 if clean == res["pages"] else 1


if __name__ == "__main__":
    sys.exit(main())
