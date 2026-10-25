# make_autostart.ps1 -- 创建/删除「开机自启」快捷方式（A 打底）
#
# 目的：让资金流向原生版在开机登录后自动启动，从而保证 09:36 / 09:41 / 09:46
#       三个开盘节点时刻程序正在运行 —— 这是「准点采集」的唯一可靠保障。
#
# 与桌面快捷方式的区别：
#   · 桌面快捷方式（资金流向.lnk）在 __DESKTOP__，需要用户双击；
#   · 本脚本创建的 lnk 放在「启动」文件夹，登录后**自动**执行，
#     不需要用户做任何事。
#
# 参数：
#   -Action install   创建自启动（默认）
#   -Action uninstall 删除自启动
#   -Action status    只查看当前状态
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File make_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File make_autostart.ps1 -Action uninstall

param(
    [ValidateSet("install", "uninstall", "status")]
    [string]$Action = "install"
)

$ErrorActionPreference = "Stop"

$desktop = [Environment]::GetFolderPath("Desktop")
$startup = [Environment]::GetFolderPath("Startup")

$target = Join-Path $desktop "exe\最新版\资金流向原生版.exe"
$workdir = Join-Path $desktop "exe\最新版"
$icon = Join-Path $desktop "exe\最新版\_internal\app.ico"
$lnkPath = Join-Path $startup "资金流向原生版.lnk"

Write-Output "Desktop      : $desktop"
Write-Output "Startup      : $startup"
Write-Output "Target       : $target"
Write-Output "ShortcutPath : $lnkPath"
Write-Output ""

if ($Action -eq "status") {
    if (Test-Path $lnkPath) {
        $sh = New-Object -ComObject WScript.Shell
        $l = $sh.CreateShortcut($lnkPath)
        Write-Output "STATUS: INSTALLED"
        Write-Output "  Target : $($l.TargetPath)"
        Write-Output "  WorkDir: $($l.WorkingDirectory)"
    } else {
        Write-Output "STATUS: NOT INSTALLED"
    }
    exit 0
}

if ($Action -eq "uninstall") {
    if (Test-Path $lnkPath) {
        Remove-Item $lnkPath -Force
        Write-Output "已删除自启动快捷方式：$lnkPath"
    } else {
        Write-Output "自启动快捷方式不存在，无需删除。"
    }
    exit 0
}

# ---- install ----
if (-not (Test-Path $target)) {
    Write-Output "ERROR: 找不到目标 exe：$target"
    Write-Output "       请先完成部署（最新版文件夹应存在且含 资金流向原生版.exe/）。"
    exit 1
}

$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut($lnkPath)
$lnk.TargetPath = $target
$lnk.WorkingDirectory = $workdir
if (Test-Path $icon) {
    $lnk.IconLocation = "$icon,0"
}
$lnk.Description = "资金流向 · 原生版（开机自启：保证开盘 5/10/15 分钟节点能准点采集）"
$lnk.Save()

# 回读校验（不靠"保存成功"就当成功）
$chk = $sh.CreateShortcut($lnkPath)
Write-Output "STATUS: INSTALLED"
Write-Output "  Target : $($chk.TargetPath)"
Write-Output "  WorkDir: $($chk.WorkingDirectory)"
Write-Output "  Icon   : $($chk.IconLocation)"
Write-Output ""
Write-Output "开机登录后将自动启动资金流向原生版。"
Write-Output "如需取消：powershell -ExecutionPolicy Bypass -File make_autostart.ps1 -Action uninstall"
exit 0
