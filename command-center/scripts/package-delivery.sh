#!/usr/bin/env bash
# ============================================================
# 交付包打包脚本
#
# 产出（全部落在 交付/ 目录）：
#   资金流向指挥中心_v<版本>_源码包.zip   —— 纯源码，不含任何构建产物/依赖
#   资金流向指挥中心_v<版本>_交付包.zip   —— 使用说明 + 安装包 + 源码包 + 报告 + 截图
#
# 为什么要有这个脚本：
#   上一版源码包是手工压的，混进了 `tsconfig.tsbuildinfo`、
#   `vite.config.ts.timestamp-*.mjs` 等构建临时文件，也漏掉了新增的
#   `components/home/*` 与 `scripts/verify-*.sh`。
#   交付包的内容必须由脚本产出，禁止手工挑选。
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="C:/Users/user/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
VERSION="1.1.0"
OUT="交付"
SETUP="tauri/target/release/bundle/nsis/资金流向指挥中心_${VERSION}_x64-setup.exe"

echo "=============================================="
echo " 交付包打包"
echo " 项目目录: $ROOT"
echo "=============================================="

if [ ! -f "$SETUP" ]; then
  echo "!! 未找到安装包：$SETUP"
  echo "   请先执行 scripts/build-release.sh"
  exit 1
fi

mkdir -p "$OUT"

echo
echo "---- 1/4 生成源码包 ----"
"$PY" - "$ROOT" "$OUT/资金流向指挥中心_v${VERSION}_源码包.zip" <<'PYEOF'
import os, sys, zipfile

root, out = sys.argv[1], sys.argv[2]

# 只打包这些顶层路径（白名单），避免把构建产物压进去
INCLUDE_TOP = ["README.md", "backend", "frontend", "tauri", "scripts", "docs"]

# 任何一层出现下列目录名 → 整棵子树跳过
SKIP_DIRS = {
    "node_modules", "target", "dist", ".test-tmp", ".git",
    "__pycache__", "bundle",
}
# 跳过这些文件（构建临时产物 / 本机状态）
SKIP_SUFFIX = (".tsbuildinfo", ".log", ".pdb", ".exe", ".zip")
SKIP_PATTERNS = (".timestamp-",)

def keep(name: str) -> bool:
    if any(p in name for p in SKIP_PATTERNS):
        return False
    if name.endswith(SKIP_SUFFIX):
        return False
    return True

count = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for top in INCLUDE_TOP:
        p = os.path.join(root, top)
        if os.path.isfile(p):
            z.write(p, top); count += 1
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                if not keep(rel):
                    continue
                z.write(full, rel)
                count += 1
print(f"  源码包条目数: {count}")
PYEOF

echo
echo "---- 2/4 同步安装包与报告到交付目录 ----"
cp -f "$SETUP" "$OUT/资金流向指挥中心_${VERSION}_x64-setup.exe"
cp -f "docs/项目完成报告.md" "$OUT/项目完成报告.md"
ls -la "$OUT/资金流向指挥中心_${VERSION}_x64-setup.exe" "$OUT/项目完成报告.md"

echo
echo "---- 3/4 生成交付包 ----"
"$PY" - "$OUT" "$VERSION" <<'PYEOF'
import os, sys, zipfile
out, version = sys.argv[1], sys.argv[2]
src_zip = f"资金流向指挥中心_v{version}_源码包.zip"
members = [
    "使用说明.md",
    f"资金流向指挥中心_{version}_x64-setup.exe",
    src_zip,
    "项目完成报告.md",
]
shots_dir = os.path.join(out, "运行截图")
if os.path.isdir(shots_dir):
    for fn in sorted(os.listdir(shots_dir)):
        if fn.lower().endswith((".png", ".jpg", ".jpeg")):
            members.append(f"运行截图/{fn}")
    if not any(m.startswith("运行截图/") for m in members):
        print("  提示: 运行截图目录存在但没有图片，交付包不含截图")
else:
    print("  提示: 无运行截图目录，交付包不含截图")
    print("        如需截图请在有可交互桌面会话的机器上执行：")
    print("        powershell -NoProfile -File scripts/capture-screenshots.ps1 -OutDir <纯英文路径>")
    print("        然后把 png 放入 交付/运行截图/ 再重新打包")

missing = [m for m in members if not os.path.exists(os.path.join(out, m))]
if missing:
    print(f"  !! 交付包缺少文件: {missing}")
    sys.exit(1)

dst = os.path.join(out, f"资金流向指挥中心_v{version}_交付包.zip")
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for m in members:
        z.write(os.path.join(out, m), m)
print(f"  交付包条目数: {len(members)}")
for m in members:
    print(f"    {m}")
PYEOF

echo
echo "---- 4/4 产物清单 ----"
for f in "$OUT/资金流向指挥中心_${VERSION}_x64-setup.exe" \
         "$OUT/资金流向指挥中心_v${VERSION}_源码包.zip" \
         "$OUT/资金流向指挥中心_v${VERSION}_交付包.zip" \
         "$OUT/项目完成报告.md" \
         "$OUT/使用说明.md"; do
  if [ -f "$f" ]; then printf "  %-52s %s 字节\n" "$f" "$(wc -c < "$f" | tr -d ' ')"; fi
done
echo
echo "完成。"
