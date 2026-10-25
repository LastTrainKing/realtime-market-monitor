# -*- coding: utf-8 -*-
"""把今日工作记忆里的「待办」段替换为「已闭环」的最终结论。

为什么要用脚本而不是直接编辑：
  D 盘上**覆盖已存在文件**会被拒（PermissionError Errno 13，
  与部署 exe 时遇到的是同一个系统行为），
  但「删除 + 新建」正常 —— 所以统一走"读 → 改 → 删 → 写"。
"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
P = r"D:\.workbuddy\memory\2026-09-21.md"

OLD_TAIL = """### 待办 / 遗留"""

NEW_TAIL = """### 遗留问题：已全部闭环（最终状态）

**① exe 日志静默消失 —— 已定位并修复（根因明确）**
- 现象：exe 部署到 `D:\\exe` 后运行正常（11 页截图/表格全产出），但
  `D:\\exe\\logs\\app_20260921.log` 与 C 盘 logs 均无新记录，**整机零日志**。
- 定位：在 `preflight()` 加**按需开启**的 `[diag]` 通道（`--shot` 或
  `FLOWLIVE_DIAG=1` 时输出版径 + 真实写测试）。结果
  `portable_root='D:\\exe'`（判定正确）但
  `log write test=FAILED PermissionError [Errno 13]`。
- 根因：那个**已存在**的日志文件处于"exe 无法追加"的状态 ——
  同文件用普通 Python 追加正常、属性只有 ARCHIVE、重命名/删除都能做；
  **删掉后 exe 立刻能新建并正常写入**。
- 修法两层：① `log_line` 主路径失败即降级到 `app_YYYYMMDD.<n>.log`（最多 3 个），
  不再 `except: pass` 静默丢弃；② `deploy_v201.py` 部署时把旧日志改名归档为
  `*.log.bak_v126`。
- 复验：`[diag] log write test=OK`，日志正常写入 `D:\\exe\\logs\\`。

**② 可移动盘上覆盖 exe 被拒 —— 已修**
- `shutil.copy2` 覆盖报 Errno 13、`os.replace` 改名报 WinError 5，
  但同目录读/建/删都正常（无进程持有）。
- 修法：部署改为**先 `os.remove` 再复制**，失败自动从备份回滚。

**③ C 盘污染 —— 已修正脚本（未擅自删改历史文件）**
- `verify_v201.py` 早期版本未重定向 `LOCALAPPDATA`，使 q11 边界测试往
  `C:\\Users\\user\\AppData\\Local\\FlowLiveNative\\logs\\app_20260921.log`
  追加了 4 行（20:59:44）。该目录是 v1.2.5 时代部署位置、非本次新建，
  且含历史日志 → 未擅自删改，已在报告中如实说明。
- 已修脚本：运行前把 `LOCALAPPDATA` 指向 `D:\\flowlive_v201\\runtime`。

**④ 唯一未能在本会话取证的项（如实记录）**
- 「龙头选手」列：数据链路已实现且接口实测返回真实领涨股
  （白酒→绿地控股 +10.34%、银行Ⅱ→农业银行 +1.47%、光伏设备→海优新材 +7.26%），
  但本会话请求密集，**数据源对本机临时限流**（连续 33s 超时、0 条返回），
  未能取得当日全量快照，故截图中该列显示「—」。
  盘中正常使用时每 30 秒一次增量刷新会自动写入快照。

### 最终状态
- 交付：`D:\\exe\\`（便携，exe 8,352,295 B + `_internal` 437 文件/141.9MB），
  版本 v2.0.1；旧版整份备份在 `D:\\flowlive_v201\\backup_v126\\`。
- 验证：`tools/verify_v201.py` **92 项断言全绿**（2 项因面板无对应板块而跳过）。
- 报告：`D:\\flowlive_v201\\outputs\\资金流向原生版_v2.0.1_修复报告.html`。
- 屏幕层自检截图：`D:\\flowlive_v201\\shots\\v201final\\`（11 页 PNG + 各页表格实际内容）。
"""


def main():
    if not os.path.exists(P):
        print("记忆文件不存在:", P)
        return 1
    with open(P, encoding="utf-8") as f:
        t = f.read()
    if OLD_TAIL not in t:
        print("未找到待替换段落（可能已更新过），跳过")
        return 0
    head = t.split(OLD_TAIL)[0]
    new = head + NEW_TAIL
    os.remove(P)                       # ★ 先删后写（D 盘无法覆盖）
    with open(P, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"已更新: {P}  {len(new)} 字符")
    return 0


if __name__ == "__main__":
    sys.exit(main())
