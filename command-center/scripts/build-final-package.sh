#!/usr/bin/env bash
# ============================================================
# 最终交付打包：把"恢复/运行/修改/重新构建本软件所必需的一切"
# 集中到一个 ZIP 里。
#
# 用法：bash scripts/build-final-package.sh
# 产物：交付/CapitalFlowCenter_Final_v<版本>.zip
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="C:/Users/user/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
VERSION=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' tauri/tauri.conf.json | head -1)
OUT="交付/CapitalFlowCenter_Final_v${VERSION}.zip"

echo "=============================================="
echo " 最终交付打包"
echo " 项目目录: $ROOT"
echo " 版本    : $VERSION"
echo " 产物    : $OUT"
echo "=============================================="

"$PY" - "$ROOT" "$OUT" "$VERSION" <<'PYEOF'
import hashlib
import os
import shutil
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
out_zip = root / sys.argv[2]
version = sys.argv[3]

stage = root / ".tmp" / "final-package" / "CapitalFlowCenter"
if stage.parent.exists():
    shutil.rmtree(stage.parent)
stage.mkdir(parents=True, exist_ok=True)

SKIP_DIRS = {
    "node_modules", "target", "dist", ".test-tmp", ".tmp", ".git",
    "__pycache__", "bundle", ".vitest-cache", ".vite",
}
SKIP_SUFFIX = (".log", ".tsbuildinfo", ".pdb", ".zip", ".map")
SKIP_SUBSTR = (".timestamp-",)
SKIP_NAMES = {"token", "port", "last_state.json", "runtime.json"}


def keep_file(rel, name):
    if name in SKIP_NAMES:
        return False
    if any(s in rel for s in SKIP_SUBSTR):
        return False
    if name.endswith(SKIP_SUFFIX):
        return False
    return True


def copy_tree(src, dst, counter):
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, src)
        target_dir = dst if rel_dir == "." else dst / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for fn in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, fn), src).replace("\\", "/")
            if not keep_file(rel, fn):
                continue
            shutil.copy2(os.path.join(dirpath, fn), target_dir / fn)
            counter["files"] += 1


def copy_file(src, dst, counter):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    counter["files"] += 1


c = {"files": 0}

copy_file(root / "tauri/target/release/capital_flow_center.exe",
          stage / "release/CapitalFlowCenter.exe", c)
copy_file(root / "tauri/target/release/backend/capital_backend.exe",
          stage / "release/backend/capital_backend.exe", c)
setup = root / "交付" / f"资金流向指挥中心_{version}_x64-setup.exe"
if not setup.exists():
    print(f"!! 缺少安装包：{setup}")
    sys.exit(1)
copy_file(setup, stage / "release/installer" / setup.name, c)
print("release/        主程序 + 后端 + 安装包")

src_root = stage / "source"
for item in ["README.md", "backend", "frontend", "tauri", "scripts", "docs"]:
    p = root / item
    if p.is_file():
        copy_file(p, src_root / item, c)
    else:
        copy_tree(p, src_root / item, c)
print("source/         完整源码树（backend / frontend / tauri / scripts / docs）")

appdata = Path(os.path.expandvars(r"%APPDATA%")) / "CapitalFlowCenter"
data_dst = stage / "data"
copy_file(appdata / "data/database.sqlite", data_dst / "database.sqlite", c)
copy_tree(appdata / "data/history", data_dst / "history", c)
copy_file(root / "docs/schema.sql", data_dst / "schema.sql", c)
print("data/           数据库 + schema.sql + 历史 Parquet")

cfg_dst = stage / "config"
copy_file(appdata / "config/config.json", cfg_dst / "config.json", c)
if (appdata / "config/window.json").exists():
    copy_file(appdata / "config/window.json", cfg_dst / "window.json", c)
print("config/         config.json (+ window.json)")

copy_tree(root / "tauri/icons", stage / "resources/icons", c)
print("resources/      应用图标")

tests_dst = stage / "tests"
for s in ["e2e-api-check.sh", "verify-homepage-modules.sh", "verify-history-system.sh",
          "verify-history-fix.sh", "check-dashboard.sh", "count-stats.sh"]:
    p = root / "scripts" / s
    if p.exists():
        copy_file(p, tests_dst / "scripts" / s, c)
fe_tests = [p for p in (root / "frontend/src").rglob("*.test.ts*")]
for p in fe_tests:
    rel = p.relative_to(root / "frontend/src")
    copy_file(p, tests_dst / "frontend" / rel, c)
print("tests/          验收脚本 + 前端测试文件 %d 个" % len(fe_tests))

copy_tree(root / "docs", stage / "docs", c)
copy_file(root / "交付/使用说明.md", stage / "docs/使用说明.md", c)
print("docs/           全部文档")

copy_tree(root / "scripts", stage / "scripts", c)
print("scripts/        构建与打包脚本")

out_zip.parent.mkdir(parents=True, exist_ok=True)
if out_zip.exists():
    out_zip.unlink()

count = 0
total_bytes = 0
with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for dirpath, dirnames, filenames in os.walk(stage):
        dirnames.sort()
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            rel = full.relative_to(stage.parent).as_posix()
            z.write(full, rel)
            count += 1
            total_bytes += full.stat().st_size

print()
print("复制文件数 : %d" % c["files"])
print("ZIP 条目数 : %d" % count)
print("原始体积   : %s 字节" % format(total_bytes, ","))
print("ZIP 体积   : %s 字节" % format(out_zip.stat().st_size, ","))
print("压缩率     : %.1f%%" % (out_zip.stat().st_size / max(total_bytes, 1) * 100))
print("SHA-256    : %s" % hashlib.sha256(out_zip.read_bytes()).hexdigest())
print("路径       : %s" % out_zip)
PYEOF

echo
echo "完成。"
