# ============================================================
# Runtime screenshot capture (desktop-DC method)
#
# Usage:
#   powershell -File scripts/capture-screenshots.ps1 -OutDir "C:\path\to\shots"
#
# Why not PrintWindow:
#   WebView2 renders through DirectComposition (hardware composited).
#   PrintWindow(PW_RENDERFULLCONTENT) returns a FLAT BLANK FRAME in this
#   environment (measured: 1536x960 filled entirely with #05070F).
#   The previously shipped 1-dashboard.png ... 8-settings.png were produced
#   that way and were in fact blank images - a real defect.
#   This script uses Graphics.CopyFromScreen (desktop DC), which captures the
#   final GDI-composited frame and does get the real content.
#
# Preconditions: the app window must be visible and not occluded.
#
# NOTE: This file is intentionally ASCII-only. Windows PowerShell 5.1 reads
#       .ps1 files as ANSI unless a UTF-8 BOM is present, and a BOM that gets
#       duplicated by tooling breaks parsing. Keeping it ASCII avoids the
#       whole class of problems; the caller supplies any non-ASCII path.
# ============================================================
param(
  [Parameter(Mandatory = $true)][string]$OutDir,
  [int]$WarmupSeconds = 115,
  [switch]$KeepRunning
)

$ErrorActionPreference = 'Continue'

$proj = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$exe  = Join-Path $proj 'tauri\target\release\capital_flow_center.exe'

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Output "OutDir : $OutDir"
Write-Output "Exe    : $exe"

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$sig = @'
using System;
using System.Drawing;
using System.Runtime.InteropServices;
public class CfWin {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }

  public static Bitmap Grab(int l, int t, int w, int h) {
    if (w <= 0 || h <= 0) return null;
    Bitmap bmp = new Bitmap(w, h);
    using (Graphics g = Graphics.FromImage(bmp)) {
      g.CopyFromScreen(l, t, 0, 0, new Size(w, h), CopyPixelOperation.SourceCopy);
    }
    return bmp;
  }
}
'@
Add-Type -TypeDefinition $sig -ReferencedAssemblies System.Drawing, System.Runtime.InteropServices -ErrorAction SilentlyContinue

# Reject flat frames: count distinct sampled colours.
# Returns the distinct-colour count, or -1 when the frame is blank.
function Test-Frame {
  param($bmp)
  $colors = @{}
  for ($x = 0; $x -lt $bmp.Width; $x += 24) {
    for ($y = 0; $y -lt $bmp.Height; $y += 24) {
      $p = $bmp.GetPixel($x, $y)
      $colors["$($p.R),$($p.G),$($p.B)"] = 1
    }
  }
  return $colors.Count
}

if (-not (Test-Path $exe)) { Write-Output "FATAL: build artifact not found: $exe"; exit 1 }

Get-Process | Where-Object { $_.ProcessName -like 'capital_*' } | ForEach-Object {
  try { Stop-Process -Id $_.Id -Force } catch {}
}
Start-Sleep -Seconds 3

Write-Output "launching..."
Start-Process -FilePath $exe
Start-Sleep -Seconds $WarmupSeconds

$p = Get-Process -Name capital_flow_center -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $p) { Write-Output "FATAL: process not running"; exit 1 }
$h = $p.MainWindowHandle
Write-Output "pid=$($p.Id) hwnd=$h"
if ($h -eq 0) { Write-Output "FATAL: no main window handle"; exit 1 }

[void][CfWin]::ShowWindow($h, 9)
Start-Sleep -Milliseconds 800
[void][CfWin]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 2500

$r = New-Object CfWin+RECT
[void][CfWin]::GetWindowRect($h, [ref]$r)

# Clip to the virtual screen so we never grab off-screen black borders.
$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
$l  = [Math]::Max($r.Left, $vs.Left)
$t  = [Math]::Max($r.Top, $vs.Top)
$rr = [Math]::Min($r.Right, $vs.Right)
$bb = [Math]::Min($r.Bottom, $vs.Bottom)
$w  = $rr - $l
$ht = $bb - $t
Write-Output "window rect: ($($r.Left),$($r.Top))-($($r.Right),$($r.Bottom))  grabbing ${w}x${ht}"

$pages = @(
  @{ key = '';  name = '1-dashboard' },
  @{ key = '2'; name = '2-market' },
  @{ key = '3'; name = '3-capital' },
  @{ key = '4'; name = '4-sector' },
  @{ key = '5'; name = '5-stock' },
  @{ key = '6'; name = '6-anomaly' },
  @{ key = '7'; name = '7-review' },
  @{ key = '8'; name = '8-settings' }
)

$okCount = 0
foreach ($pg in $pages) {
  [void][CfWin]::SetForegroundWindow($h)
  Start-Sleep -Milliseconds 500
  if ($pg.key -ne '') {
    [System.Windows.Forms.SendKeys]::SendWait($pg.key)
    Start-Sleep -Milliseconds 3000
  }
  $file = Join-Path $OutDir ($pg.name + '.png')
  $saved = $false
  for ($try = 1; $try -le 6; $try++) {
    [void][CfWin]::SetForegroundWindow($h)
    Start-Sleep -Milliseconds 900
    $bmp = [CfWin]::Grab($l, $t, $w, $ht)
    if ($bmp) {
      $distinct = Test-Frame $bmp
      if ($distinct -ge 12) {
        $bmp.Save($file, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        Write-Output ("  OK   {0}  {1}x{2}  distinct={3}" -f $pg.name, $w, $ht, $distinct)
        $okCount++
        $saved = $true
        break
      }
      Write-Output ("  blank {0} (distinct={1}) retry={2}" -f $pg.name, $distinct, $try)
      $bmp.Dispose()
    }
    Start-Sleep -Seconds 2
  }
  if (-not $saved) { Write-Output ("  FAIL {0}" -f $pg.name) }
  Start-Sleep -Milliseconds 600
}

Write-Output "captured $okCount / $($pages.Count) into $OutDir"
Get-ChildItem -Path $OutDir -Filter '*.png' | ForEach-Object {
  Write-Output ("  {0}  {1} bytes" -f $_.Name, $_.Length)
}

if (-not $KeepRunning) {
  Get-Process | Where-Object { $_.ProcessName -like 'capital_*' } | ForEach-Object {
    try { Stop-Process -Id $_.Id -Force } catch {}
  }
  Write-Output "app closed"
}
