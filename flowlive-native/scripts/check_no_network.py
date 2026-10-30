#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_no_network.py —— 无网络能力守卫

Shell 版的核心承诺：**程序不具备任何联网取数能力**。
本脚本做静态验证，CI 与本地提交前都应运行；发现违规即退出码 1。

检查三条：
  1. 运行时代码不得 import 任何网络库
  2. 运行时代码不得出现 http(s):// 字面量
  3. Cargo 依赖（若存在）不得包含 HTTP 客户端

用法：
    python scripts/check_no_network.py [仓库根目录]
"""
import os
import re
import sys

NET_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+("
    r"urllib|http\.client|http\.server|socket|ssl|requests|httpx|aiohttp|"
    r"urllib3|websocket|websockets|ftplib|smtplib|telnetlib|xmlrpc"
    r")\b",
    re.M,
)
URL_LITERAL = re.compile(r"https?://[^\s\"')]+")

# 运行时代码：真正会被打进发布产物的部分
RUNTIME_DIRS = ("core", "core2")
RUNTIME_FILES = ("app_main_v203.py",)
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "dist", "build_v3",
             "_internal", "target"}


def _base(root):
    """运行时代码的根目录：优先 build/（本仓库结构），否则用传入的根。"""
    for cand in (os.path.join(root, "build"), root):
        if os.path.isdir(os.path.join(cand, "core")):
            return cand
    return root


def iter_runtime_files(root):
    root = _base(root)
    for d in RUNTIME_DIRS:
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            continue
        for dp, dns, fns in os.walk(p):
            dns[:] = [x for x in dns if x not in SKIP_DIRS]
            for fn in fns:
                if fn.endswith(".py"):
                    yield os.path.join(dp, fn)
    for fn in RUNTIME_FILES:
        p = os.path.join(root, fn)
        if os.path.isfile(p):
            yield p


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    root = os.path.abspath(root)

    errors = []
    checked = 0
    for path in iter_runtime_files(root):
        checked += 1
        rel = os.path.relpath(path, root)
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError as e:
            errors.append("%s: 无法读取 (%s)" % (rel, e))
            continue

        for m in NET_IMPORT.finditer(src):
            line = src[:m.start()].count("\n") + 1
            errors.append("%s:%d 导入了网络库 %r" % (rel, line, m.group(1)))

        for m in URL_LITERAL.finditer(src):
            line = src[:m.start()].count("\n") + 1
            errors.append("%s:%d 出现 URL 字面量 %s" % (rel, line, m.group(0)[:60]))

    # Cargo 依赖检查（Rust 版）
    for dp, dns, fns in os.walk(root):
        dns[:] = [x for x in dns if x not in SKIP_DIRS]
        if "Cargo.toml" in fns:
            p = os.path.join(dp, "Cargo.toml")
            rel = os.path.relpath(p, root)
            txt = open(p, encoding="utf-8", errors="replace").read()
            for bad in ("reqwest", "hyper", "ureq", "isahc", "surf"):
                if re.search(r"^\s*%s\s*=" % bad, txt, re.M):
                    errors.append("%s 依赖了 HTTP 客户端 %s" % (rel, bad))

    print("检查文件数: %d" % checked)
    if errors:
        print("\n发现 %d 处违规：" % len(errors))
        for e in errors:
            print("  ✗ " + e)
        print("\nShell 版不允许存在联网取数能力。若为误报，请在此脚本中加白名单。")
        return 1

    print("✓ 通过：运行时代码无网络库导入、无 URL 字面量、无 HTTP 依赖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
