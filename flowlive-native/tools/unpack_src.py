# -*- coding: utf-8 -*-
"""从归档 zip 解压「资金流向」原生成工程到 D 盘，用于 v2.0.1 改造。

只解压需要的部分，跳过所有 dist_*/build_* 历史产物与截图目录
（重新打包会生成新的），避免 3.25 GB 全量解压。
"""
import os
import sys
import zipfile

ZIP = r"D:\资金流向项目归档\资金流向项目_全量归档_20260921_1330.zip"
PKG = "资金流向项目_全量归档_20260921_1330/"
DST = r"D:\flowlive_v201"

SRC_NB = PKG + "work/native_build/"

# 排除的顶层前缀（历史构建产物、截图、缓存）
SKIP_TOP = ("dist", "build_", "shots", "__pycache__", "chart_test")

# 需要从 work/ 下其它位置额外拿的文件
EXTRA = [
    ("work/chk115.py", "build/chk115.py"),
]

# 归档文档（关键几份，放 docs/）
DOCS = [
    ("归档说明_从这里开始.md", "docs/归档说明_从这里开始.md"),
    ("work/_archive_docs/00_说明使用指南_先读我.md", "docs/00_说明使用指南_先读我.md"),
    ("work/_archive_docs/01_技术交接文档.md", "docs/01_技术交接文档.md"),
    ("work/_archive_docs/02_对话与任务摘要.md", "docs/02_对话与任务摘要.md"),
    ("work/_archive_docs/03_文件清单与索引.md", "docs/03_文件清单与索引.md"),
]


def main():
    if not os.path.exists(ZIP):
        print("[FAILED] 归档不存在:", ZIP)
        return 1

    z = zipfile.ZipFile(ZIP)
    infos = z.infolist()
    os.makedirs(DST, exist_ok=True)

    def dest(rel_under_pkg: str, sub: str) -> str:
        return os.path.join(DST, sub, rel_under_pkg.replace("/", os.sep))

    n_file = n_byte = 0

    # ---- 1. native_build 源码（排除产物） ----
    for i in infos:
        name = i.filename
        if not name.startswith(SRC_NB) or name.endswith("/"):
            continue
        rel = name[len(SRC_NB):]                     # 相对 native_build
        top = rel.split("/")[0]
        if top in SKIP_TOP or top.startswith(SKIP_TOP):
            continue
        # core/data 下的大 npy 可跳过（运行时会自行抓取）
        out = dest(rel, "build")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with z.open(i) as fi, open(out, "wb") as fo:
            while True:
                b = fi.read(1 << 20)
                if not b:
                    break
                fo.write(b)
                n_byte += len(b)
        n_file += 1

    # ---- 2. 额外文件 ----
    for src, dst in EXTRA:
        try:
            data = z.read(PKG + src)
        except KeyError:
            print("  [skip] 缺:", src)
            continue
        out = os.path.join(DST, dst.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as fo:
            fo.write(data)
        n_file += 1
        n_byte += len(data)

    # ---- 3. 文档 ----
    for src, dst in DOCS:
        try:
            data = z.read(PKG + src)
        except KeyError:
            print("  [skip] 缺:", src)
            continue
        out = os.path.join(DST, dst.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as fo:
            fo.write(data)
        n_file += 1
        n_byte += len(data)

    print("[1] 源码+文档: %d 个文件 / %.1f MB" % (n_file, n_byte / 1048576))

    # ---- 4. native_env（构建 venv） ----
    SRC_ENV = PKG + "work/native_env/"
    ne = nb = 0
    for i in infos:
        name = i.filename
        if not name.startswith(SRC_ENV) or name.endswith("/"):
            continue
        rel = name[len(SRC_ENV):]
        if rel.startswith("Lib/site-packages/PySide6/translations"):
            pass                                     # 保留（Qt 中文语言包）
        out = dest(rel, "native_env")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with z.open(i) as fi, open(out, "wb") as fo:
            while True:
                b = fi.read(1 << 20)
                if not b:
                    break
                fo.write(b)
                nb += len(b)
        ne += 1
        if ne % 2000 == 0:
            print("    venv ... %d 个 / %.0f MB" % (ne, nb / 1048576))
            sys.stdout.flush()

    print("[2] native_env: %d 个文件 / %.1f MB" % (ne, nb / 1048576))
    print("[DONE] 工程根:", DST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
