# -*- mode: python ; coding: utf-8 -*-
"""onedir 打包配置（v1.2.3 启动提速）

为什么改 onedir：
  onefile 每次启动都要把 69MB PKG 解到 %TEMP%\_MEIxxxx（实测 **1.64 秒**），
  这段开销发生在 Python 代码执行**之前**，无法用任何代码优化绕开。
  onedir 把文件预先摊开，启动时直接内存映射，**不解包**。

代价：交付形态从「单文件 exe」变成「文件夹」。
  但这与用户偏好一致（明确说过偏好文件夹直传、免解压），
  且桌面快捷方式指向文件夹内的 exe，双击体验完全不变。

与 onefile 的其余配置完全一致（hiddenimports / excludes 一字未改），
确保功能不回退。
"""

a = Analysis(
    ['app_main_v202b.py'],
    pathex=[],
    binaries=[],
    # ★★ v2.0.1：把 app.ico 一并打进 bundle。
    #   为什么必须加这一条：EXE(icon=[...]) 只负责**文件图标**（资源管理器
    #   里看到的那一个），它不会让程序运行时能读到这个 ico。
    #   而 v2.0.1 新增了运行期 setWindowIcon / 任务栏图标，需要在
    #   _MEIPASS 下找到一个真实存在的 app.ico —— 靠这条 datas 提供。
    #   （onedir 下它落在 _internal/app.ico，正是 app_icon_path() 找的位置。）
    datas=[('core', 'core'), ('core2', 'core2'), ('app.ico', '.')],
    hiddenimports=[
        'numpy',
        'numpy.core._multiarray_umath',
        'numpy.random',
        'http', 'http.client', 'ssl',
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
        # ★ v1.2.3：显式拉黑打包环境可能混入的重库（不指望环境干净）
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
    [],
    exclude_binaries=True,
    name='资金流向原生版',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='资金流向原生版',
)
