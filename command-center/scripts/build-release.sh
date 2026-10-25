#!/usr/bin/env bash
# ============================================================
# 资金流向指挥中心 AI Edition — 一键构建发布脚本
#
# 流程：后端 Release → 前端生产包 → Tauri 桌面外壳 → NSIS 安装包
# 产物：tauri/target/release/bundle/nsis/资金流向指挥中心_<版本>_x64-setup.exe
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=============================================="
echo " 资金流向指挥中心 · 发布构建"
echo " 项目目录: $ROOT"
echo "=============================================="

step() { echo; echo "---- $1 ----"; }

step "1/5 后端单元测试"
cargo test --manifest-path backend/Cargo.toml --lib --quiet

step "2/5 后端 Release 构建"
cargo build --manifest-path backend/Cargo.toml --release
BACKEND_BIN="backend/target/release/capital_backend.exe"
if [ ! -f "$BACKEND_BIN" ]; then
  echo "!! 后端产物缺失：$BACKEND_BIN"
  exit 1
fi
echo "后端产物: $BACKEND_BIN ($(wc -c < "$BACKEND_BIN") 字节)"

step "3/5 前端类型检查 + 单元测试"
cd frontend
npx tsc -b --pretty false
npx vitest run
cd ..

step "4/5 Tauri 桌面外壳 + 安装包"
cd tauri
npx tauri build
cd ..

step "5/5 产物清单"
# 版本号从 tauri.conf.json 读取，**按版本精确匹配安装包**。
# 早期用 `ls *.exe | head -1`，会按文件名排序挑到上一次构建留下的旧安装包，
# 产物清单里就会出现"版本号与刚构建的不一致"这种自相矛盾的输出。
VERSION=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' tauri/tauri.conf.json | head -1)
SETUP="tauri/target/release/bundle/nsis/资金流向指挥中心_${VERSION}_x64-setup.exe"
if [ ! -f "$SETUP" ]; then
  echo "!! 未生成安装包：$SETUP"
  echo "   bundle 目录现有："
  ls -1 tauri/target/release/bundle/nsis/*.exe 2>/dev/null | sed 's/^/     /'
  exit 1
fi
echo "主程序 : tauri/target/release/capital_flow_center.exe"
echo "后端   : $BACKEND_BIN"
echo "版本   : $VERSION"
echo "安装包 : $SETUP ($(wc -c < "$SETUP") 字节)"
# 列出历史遗留的其它版本安装包，避免误当成新产物分发
STALE=$(ls -1 tauri/target/release/bundle/nsis/*.exe 2>/dev/null | grep -v "$(basename "$SETUP")" || true)
if [ -n "$STALE" ]; then
  echo
  echo "注意：bundle 目录还有其它版本的安装包，请勿误分发："
  echo "$STALE" | sed 's/^/     /'
fi
echo
echo "完成。用户只需双击安装包即可完成安装，安装后桌面会出现「资金流向指挥中心」快捷方式。"
