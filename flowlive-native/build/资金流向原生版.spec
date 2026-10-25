# -*- mode: python ; coding: utf-8 -*-
"""资金流向 · 纯原生桌面版 —— PyInstaller 打包配置

关键点：core/ 是通过 datas= 送进 exe 的（不进 pyz），PyInstaller 不会去分析
它的 import 语句。因此 core/ 用到的第三方库必须显式写进 hiddenimports，
否则运行期会 ModuleNotFoundError —— 这正是 v1.0.0 崩溃的根因。

hiddenimports 里带上 requests 是**兜底**（现在 core 已无此依赖，带上是防回退）。
"""

a = Analysis(
    ['native_app.py'],
    pathex=[],
    binaries=[],
    # ★★ v2.0.1：图标随包分发（运行期 setWindowIcon 需要它真实存在）。
    #   详见 资金流向原生版_onedir.spec 里同一条的说明 —— 两个 spec 必须一致。
    datas=[('core', 'core'), ('app.ico', '.')],
    hiddenimports=[
        # ---- core/ 模块的真实依赖（datas= 不走静态分析，必须显式声明）----
        'numpy',
        'numpy.core._multiarray_umath',
        'numpy.random',
        # ★ v1.2.1：flowdata.py 为了做 keep-alive 长连接复用，新增了
        #   `import http.client, ssl`。这两个虽然是标准库，但**同样受
        #   "core/ 不走静态分析"这条规则约束** —— 只要主程序侧没有别的地方
        #   引用它们，就不会被自动收进 bundle，运行期一抓数据就 ModuleNotFoundError。
        #   实测 v1.2.1 的 exe 恰好能跑（被别的链路顺带带进来了），
        #   但那是巧合、不是保证，显式列出才是稳的。
        'http', 'http.client', 'ssl',
        # 兜底：若日后 core 里又出现 requests 用法，也不至于崩
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick', 'PySide6.QtWebChannel', 'PySide6.QtQml',
        'PySide6.QtQuick', 'PySide6.Qt3DCore', 'PySide6.QtMultimedia',
        'PySide6.QtCharts', 'PySide6.QtBluetooth', 'PySide6.QtNfc',
        'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtSql',
        'PySide6.QtTest', 'scipy', 'tkinter', 'PySide6.QtNetworkAuth',
        # ★ v1.2.3：显式拉黑打包环境可能混入的重库（不指望环境干净）。
        #   本应用自身零 pandas/lxml 依赖；一旦被 matplotlib 的可选依赖
        #   顺带收进来，exe 体积实测会从 69.6MB 涨到 92.5MB。
        'pandas', 'lxml', 'fsspec', 'IPython', 'jupyter', 'notebook',
        'pytest', 'setuptools', 'pip',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='资金流向原生版',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)
