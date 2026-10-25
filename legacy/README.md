# 历史源码快照

本目录保存原生桌面版的**历史源码**。这些文件**不是从 Git 历史还原的**，
而是从开发机/U 盘上实际存在的文件里取出的 —— 每一个都标注了原始路径与
其 `VERSION` 常量，未做任何修改。

## 内容

| 目录 | 文件 | `VERSION` 常量 | 文件时间 | 原始路径 |
|---|---|---|---|---|
| `v1.2.5/` | `_native_app_ref.py` | `1.2.5` | 2026-09-21 08:21 | `…\flowlive_v201\_native_app_ref.py` |
| `v2.0.1_to_v2.0.2_transition/` | `native_app.py` | `2.0.2` | 2026-09-21 13:05 | `…\flowlive_v201\build\native_app.py` |
| `v2.0.2/` | `app_main_v202.py` | `2.0.2` | 2026-09-22 00:54 | `…\flowlive_v201\build\app_main_v202.py` |
| `v2.0.2b/` | `app_main_v202b.py` | `2.0.2` | 2026-09-22 01:17 | `…\flowlive_v201\build\app_main_v202b.py` |
| `v2.0.2_core_reference/` | `_ref_core_*.py`（9 个） | — | 2026-09-21 08:23 | `…\flowlive_v201\_ref_core_*.py` |
| `snapshots/` | `native_app.py.bak_v203`、`native_app_patched.py` | `2.0.2` | 2026-09-21 13:05 / 2026-09-22 00:52 | `…\flowlive_v201\tmp\` |

## 关于 `v2.0.1_to_v2.0.2_transition/`（**归属待核实**）

`native_app.py` 的 `VERSION` 常量是 `2.0.2`，但文件时间是 `2026-09-21 13:05`，
**早于** v2.0.2 的正式构建（`app_main_v202.py`，09-22 00:54）。

结合更新日志里的这条记录：

> v2.1.0 时 `chk115.py` 改为加载**实际打包的主源文件** `app_main_v203.py`
> （此前长期加载同目录遗留副本 `native_app.py`，测试与交付物脱钩、虚假全绿）

可以判断 `native_app.py` 是**长期滞留在 `build/` 目录的遗留副本**，
其 `VERSION` 在 v2.0.2 开发早期被就地改过，因此它既不是干净的 v2.0.1，
也不能当作 v2.0.2 的正式源。

**结论：版本归属待核实。** 目录名已按实际情况命名为「过渡」，不做断言。

## 关于 `v2.0.2_core_reference/`

`_ref_core_*.py` 是 v2.0.2 时期 `core/` 各模块的**参考副本**
（`_ref_` 前缀即 reference），用于对照「打包产物里的内核」与「源码树里的内核」
是否一致。它们不是独立版本，只是同一时期内核的一份快照。

> 与 `flowlive-native/build/core/` 的区别：后者是 **v3.0.6** 的内核，
> 多出了 `source.py`、`tradingtime.py`，且 `flowdata.py` / `salience.py` /
> `openspace.py` 都有大幅演进。**不要混用。**

## 关于 `snapshots/`

两个文件都是 v2.0.2 时期的**开发中间产物**（`.bak_v203` 是改 v2.0.3 前的备份，
`_patched.py` 是打补丁后的中间态）。它们**不构成独立版本**，
放在这里只为完整性与可追溯。

## 未归档的历史源码

| 版本 | 状态 |
|---|---|
| v2.0.3 ~ v2.0.12 | **无源码**。主程序为单文件就地迭代，作者未为每版留副本 |
| v2.1.0 ~ v3.0.5 | **无独立源码**，由 `flowlive-native/build/app_main_v203.py`（v3.0.6 状态）代表 |
| v1.0.x ~ v1.2.4 | **无源码**。仅存在于 2.3 GB 全量归档 zip 内的 `work/native_build/` 早期状态 |

这是原始材料的客观限制，不是归档遗漏。详见
[../docs/version-history.md](../docs/version-history.md) 第五节。
