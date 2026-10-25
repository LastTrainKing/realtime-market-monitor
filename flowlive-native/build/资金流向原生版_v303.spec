# -*- mode: python ; coding: utf-8 -*-
"""onedir 打包配置（v3.0.3 延迟优化版）

与 v302 spec 的差异：**仅版本注释**。构建参数一字未改，原因：
  - v3.0.3 的改动全在 Python 源码内（采集并行 / 渲染增量 / 跨分钟守卫），
    **没有新增任何第三方依赖**（ThreadPoolExecutor 来自标准库 concurrent.futures）。
  - 因此 hiddenimports 不需要加 `concurrent.futures`——
    PyInstaller 的 stdlib 静态分析会自己收进去；手工加反而误导后人以为它是个"外部依赖"。
    （若打包后出现 `ModuleNotFoundError: concurrent.futures`，才需要补；1~2 分钟可验证。）

沿用 v302 的三条关键决策（勿回退）：
  1) **onedir 而非 onefile**：onefile 每次启动要把 69MB PKG 解到 %TEMP%\_MEIxxxx
     （实测 1.64 秒），这段开销在 Python 执行之前，代码无法绕开；onedir 预先摊开、不解包。
  2) **datas 必须含 app.ico**：EXE(icon=[]) 只管文件图标，运行期 setWindowIcon
     需要 _MEIPASS 下有真实 ico（onedir 落在 _internal/app.ico）。
  3) **excludes 必须显式拉黑 pandas/lxml/fsspec 等**：打包环境不干净，
     matplotlib 的可选依赖会把 pandas 拖进 exe（实测 69.6MB → 92.5MB，
     而应用自身零 pandas 依赖）。排除后体积应回到 ~70MB 级。
"""

a = Analysis(
    ['app_main_v203.py'],
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
        # ★ v3.0.2：显式拉黑打包环境可能混入的重库（不指望环境干净）
        # ★ v3.0.3 复核：本次改动未引入任何新依赖，故本段沿用不变。
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
