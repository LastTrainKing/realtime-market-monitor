#!/usr/bin/env bash
# 统计代码规模（交付报告中的数字必须由本脚本产出，禁止手写估算）
set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

rust_files=$(find backend/src tauri/src -name '*.rs' -type f | wc -l | tr -d ' ')
rust_lines=$(find backend/src tauri/src -name '*.rs' -type f -exec cat {} + | wc -l | tr -d ' ')
rust_tests=$(grep -rho '#\[test\]\|#\[tokio::test\]' backend/src tauri/src | wc -l | tr -d ' ')
fe_files=$(find frontend/src -name '*.ts' -o -name '*.tsx' | wc -l | tr -d ' ')
fe_lines=$(find frontend/src \( -name '*.ts' -o -name '*.tsx' \) -exec cat {} + | wc -l | tr -d ' ')
css_lines=$(find frontend/src -name '*.css' -exec cat {} + | wc -l | tr -d ' ')
vitest=$(grep -rho 'it(' frontend/src --include='*.test.ts' --include='*.test.tsx' | wc -l | tr -d ' ')

echo "Rust 源文件数   : $rust_files"
echo "Rust 代码行数   : $rust_lines"
echo "Rust 测试用例数 : $rust_tests"
echo "前端 TS/TSX 文件: $fe_files"
echo "前端 TS/TSX 行数: $fe_lines"
echo "前端 CSS 行数   : $css_lines"
echo "Vitest 用例数   : $vitest"
