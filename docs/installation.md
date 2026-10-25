# 安装、运行与构建

> 本文的步骤基于源码与 spec 文件整理，**未在本次归档环境中重新执行验证**
> （环境无 PySide6 / PyInstaller / Rust 工具链）。请按实际报错调整。

## 〇、先决定你要跑哪一种

| 目标 | 走哪节 | 依赖 |
|---|---|---|
| 只想看看界面 | [一、网页版看板](#一网页版看板最轻量) | 浏览器 + Python 标准库 |
| 装到手机看 | [二、Android APK](#二android-apk) | 无 |
| **想直接拿去用** | **见下方说明** | 已有可运行的 exe |
| 跑桌面版（源码） | [三、从源码运行原生版](#三从源码运行原生版) | Python 3.13 + 3 个库 |
| 自己打包 exe | [四、构建 onedir 发布包](#四构建-onedir-发布包) | 上述 + PyInstaller |
| 跑指挥中心 | [五、指挥中心](#五指挥中心) | Rust + Node + Tauri CLI |

> **★ 想直接拿去用：优先 `v2.0.4` 或 `v2.0.1`**（仓库所有者标注）。
> 这两个版本只走东方财富单源、能拿到完整分钟历史，部署结构也最简单
> （整个文件夹拷走即可，数据跟着走）。
>
> **但本仓库不分发 exe** —— 需要从你自己的本地归档取用，并用 SHA256 核对：
> v2.0.1 = `8,352,295 B` / `c05d2c2fc82fb236…`；v2.0.4 = `8,365,174 B` / `363cbd9f7f71b4f5…`。
>
> 代价：v2.0.x 刷新是 30 秒 / 10 秒级（比 v3.0.6 激进），**更容易触发限流**，
> 且没有多源层与「面板不降级」守卫。完整取舍见
> [README §4.4](../README.md#44-版本选用建议仓库所有者标注)。

---

## 一、网页版看板（最轻量）

```bash
cd web-dashboard
python serve.py
```

或双击 `启动看板.bat`，浏览器会自动打开 `http://127.0.0.1:…`。

- **纯 Python 标准库**，无第三方依赖。
- 只监听 `127.0.0.1`，不对外网开放。
- 历史数据落在 `web-dashboard/data/*.json`（纯文本，可直接用记事本打开）；
  每次写入前自动留一份旧版本（保留最近 8 份）。

> ⚠️ **数据来源是你的浏览器直连东方财富接口** —— 请求以**你自己的 IP** 发出。
> 请先读 [data-source-risk.md](data-source-risk.md)。

**为什么需要 `serve.py` 而不是直接双击 HTML**：`localStorage` 的物理位置在浏览器
自己的 profile 目录（C 盘），既不跟着 HTML 走，也无法指定存到别处。`serve.py`
做两件事：用 `http://127.0.0.1` 发页面（而不是 `file://`），并提供 `/__store`
接口把 localStorage 的每次读写镜像成磁盘上的明文 JSON。

---

## 二、Android APK

`android/` 下有两个包：

| 文件 | 说明 |
|---|---|
| `资金流向.apk` | 标准版 |
| `资金流向_兼容版.apk` | 兼容版（两者字节相同，均为 111,462 B —— 内容一致，仅文件名不同） |

- 包名 `com.abu.flowlive`，minSdk 21。
- 是 **WebView 壳**，加载打包在 APK 内的 HTML 看板。
- 需要允许「安装未知来源应用」。
- 详细说明见 `android/安装与使用说明.txt`。

> 该 APK 为 2026-09-19 构建，**未签名发布、未上架任何应用商店**，
> 本次归档未在真机验证。

---

## 三、从源码运行原生版

### 3.1 环境

- Windows 10 / 11 x64
- Python **3.13.x**（打包产物内嵌 CPython 3.13；源码未在其他版本上验证）

### 3.2 安装依赖

```bash
cd flowlive-native/build
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
.venv/Scripts/pip install PySide6 matplotlib numpy
```

> 项目**没有** `requirements.txt`。上述三个库由源码 `import` 语句与
> `资金流向原生版_v306.spec` 的 `hiddenimports` 交叉核对得出。
> `requests`（含 urllib3 / certifi / charset_normalizer / idna）在 spec 的
> `hiddenimports` 中出现，作为间接依赖一并列出。

### 3.3 运行

```bash
.venv/Scripts/python app_main_v203.py
```

首次启动会在同级目录创建：

```
data/     面板缓存（panel_YYYYMMDD.npy / .json）、板块清单、交易日历
logs/     app_YYYYMMDD.log
shots/    --shot 自检截图
```

### 3.4 常用启动参数

| 参数 | 作用 |
|---|---|
| `--shot <name>` | 离屏自检：自动截图后退出，不长期占用窗口 |
| `--no-net` / 离线模式 | 只用本地面板（参数名以源码为准） |

> 源码中的参数解析集中在主程序顶部，改动前请先读
> `docs/archive-2026-09-21/技术交接文档/01_技术交接文档.md`。

---

## 四、构建 onedir 发布包

```bash
cd flowlive-native/build
.venv/Scripts/python -m PyInstaller 资金流向原生版_v306.spec --noconfirm
```

产物：`dist/资金流向原生版/资金流向原生版.exe` + `_internal/`。

### 4.1 五条不能改的构建约束

1. **onedir 而非 onefile**：onefile 每次启动要把约 69 MB 包解到
   `%TEMP%\_MEIxxxx`，实测多 1.64 s，且这段开销在 Python 执行之前，代码绕不开。
2. **`datas` 必须含 `app.ico`**：`EXE(icon=[...])` 只负责文件图标，
   运行期 `setWindowIcon` 需要 `_MEIPASS` 下有真实 ico（onedir 落在 `_internal/app.ico`）。
3. **`excludes` 必须显式拉黑 `pandas` / `lxml` / `fsspec`**：打包环境不干净时，
   matplotlib 的可选依赖会把 pandas 拖进来（实测 69.6 MB → 92.5 MB，
   而应用自身零 pandas 依赖）。
4. **打包前清 `core/__pycache__` 与 `core2/__pycache__`**：`app_main_v203.py` 把
   `core/` 插入 `sys.path`，源码与 `.pyc` 版本/mtime 不匹配时会**静默加载旧逻辑**。
   不清缓存时同一份源码连续构建，`_internal` 文件数会在 453 / 519 / 523 之间漂移。
5. **部署时 exe 与 `_internal/` 必须成对更新**：只换 exe 会得到「新程序 + 旧依赖」。

### 4.2 部署

`tools/deploy_desktop.py` 与 `build/deploy*.py` 系列脚本会：
归档旧版到「历史版本」→ 复制 exe + `_internal` → 写 `版本.txt` → 重建快捷方式。

`build/deploy_transactional.py`（v3.0.6 新增）是更严格的一版：
只读预检 → 只关路径落在本目录的实例 → 同卷暂存并校验 → 两阶段改名 →
部署后校验 → 启动自检（等日志出现 `preflight ok · vX.Y.Z`）→ 任一步失败自动回滚。

> ⚠️ 这些脚本含**原开发机的绝对路径**，换机器前先搜一遍 `C:\Users\user`。

---

## 五、指挥中心

### 5.1 环境

- Windows 10 / 11 x64
- Rust（stable）+ Cargo
- Node.js + npm
- Tauri CLI 2.x
- MSVC 构建工具（DuckDB bundled 编译需要；见下）

### 5.2 构建

```bash
cd command-center
npm --prefix frontend ci
cargo build --release --manifest-path backend/Cargo.toml
npm --prefix frontend run build
npx tauri build
```

产物：`tauri/target/release/bundle/nsis/资金流向指挥中心_<version>_x64-setup.exe`。

### 5.3 DuckDB 的两个坑（来自项目文档）

1. 必须 `DUCKDB_DISABLE_UNITY_BUILD=1` —— unity build 的
   `ub_src_planner_operator.cpp` 编译退出码 2。
2. 必须在**非受限环境**下构建 —— 受限沙箱会拦截 DuckDB 源码 tar 解压
   （`PermissionDenied`）。

### 5.4 运行期目录

| 路径 | 内容 |
|---|---|
| `%LOCALAPPDATA%\资金流向指挥中心\` | 安装目录（主程序 + `backend\capital_backend.exe` + `uninstall.exe`） |
| `%APPDATA%\CapitalFlowCenter\` | 用户数据：`data/database.sqlite`、`config/config.json`、`cache/{port,token}`、`logs/{app,backend,network,error}.log` |

程序与数据目录分离，升级不需要迁移。

---

## 六、首次运行会遇到的正常现象

| 现象 | 原因 | 是否正常 |
|---|---|---|
| 窗口先出、数据后到（1~2 s） | 两段式首屏：先用本地面板上屏 | 正常 |
| 状态栏显示 `源 tencent(替代)` | v3.0.6 默认数据源是腾讯 | 正常 |
| 概念板块当天没有曲线 | 替代源无分钟历史，需从 09:31 起逐分钟累积 | 正常（曲线如实断开） |
| 提示「该节点未采集（程序未在节点时刻运行）」 | 开盘检测三节点是定点快照，错过即无 | 正常（不拿收盘值冒充） |
| 首次启动较慢（指挥中心约 75 s 才出全市场统计） | 证券列表刷新约 28 s（新浪通道），之后缓存 3 天 | 正常 |
| 界面显示「全市场扫描进行中」 | 股票池未达全市场规模前不展示涨跌家数 | 正常（不展示失真数字） |
