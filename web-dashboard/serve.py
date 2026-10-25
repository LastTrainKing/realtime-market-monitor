# -*- coding: utf-8 -*-
"""
资金流向实时看板 —— D 盘本地服务（纯 Python 标准库，零第三方依赖）

为什么需要它
============================================================================
原版是「单文件 HTML，双击即用」，历史数据存在浏览器的 localStorage 里。
localStorage 的物理位置在**浏览器自己的 profile 目录**（C 盘），
它既不跟着 HTML 文件走，也没法指定存到 D 盘 —— 把 HTML 拷到 D 盘，
历史数据仍然留在 C 盘，换台机器 / 清缓存就全没了。

本服务做两件事，把存储位置抢回来：
  1. 把页面用 http://127.0.0.1 发出去（而不是 file:// 直开）；
  2. 提供 /__store 接口，把 localStorage 的每一次读写**镜像成 D 盘上的明文 JSON**。
     页面侧通过一小段转接层（shim）把 localStorage 换成"内存 + 后台落盘"，
     业务代码一行没改 —— 所有读写照旧，只是数据躺到了 D:\\html\\data\\。

于是：
  · 历史数据完整跟着 D 盘走，不依赖浏览器缓存；
  · 未来新交易日的数据也写进 D:\\html\\data\\；
  · 数据是明文 JSON，随时能看、能备份、能拷走。

只监听 127.0.0.1，不对外开放；无登录、无上报。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "_backup")
PAGE = os.path.join(ROOT, "资金流向看板.html")
WRITE_LOG = os.path.join(DATA_DIR, "_writes.log")

# 归档页写档偏大（30 个交易日 × 完整分钟曲线），给足上限
MAX_BODY = 32 * 1024 * 1024
BACKUP_KEEP = 8                       # 每个键保留最近 N 份历史版本

_LOCK = threading.Lock()


# ------------------------------------------------------------------ 存储层
def key_path(key: str) -> str:
    """把 localStorage 的键名映射成一个安全的文件名。

    键名来自页面（flowlive_panel_v1 等），仍是不可信输入 ——
    做白名单式清洗，任何非 [A-Za-z0-9._-] 的字符都换成下划线，
    避免 ../ 之类的路径穿越把文件写到 data 目录外面。
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:120]
    return os.path.join(DATA_DIR, f"ls_{safe}.json")


def read_all() -> dict:
    """读出全部键值。返回值一定不含 None（页面把 null 当"没有"）。"""
    out = {}
    if not os.path.isdir(DATA_DIR):
        return out
    for fn in sorted(os.listdir(DATA_DIR)):
        if not (fn.startswith("ls_") and fn.endswith(".json")):
            continue
        p = os.path.join(DATA_DIR, fn)
        try:
            with open(p, encoding="utf-8") as f:
                v = f.read()
            if v:
                out[fn[3:-5]] = v
        except Exception:                                     # noqa: BLE001
            continue
    return out


DELETED_DIR = os.path.join(DATA_DIR, "_deleted")


def _stamped_name(key: str) -> str:
    """生成带时间戳的留档文件名（键名做白名单清洗，防路径穿越）。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(key))[:80]
    return f"{safe}.{stamp}.json"


def _rotate_backup(key: str, old: str) -> None:
    """写新值前把旧值留一份 —— 归档数据被写坏时还能回滚。

    ★ 超量时的清理也用「移走」而不是 os.remove：
      本机的文件安全策略会拦掉对 D 盘文件的真删（实测报 trash 操作失败），
      直接删会让整个写流程失败。挪进 _deleted/ 同样达到"不占主目录"的目的。
    """
    if not old:
        return
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(key))[:80]
        dst = os.path.join(BACKUP_DIR, _stamped_name(key))
        if not os.path.exists(dst):
            with open(dst, "w", encoding="utf-8") as f:
                f.write(old)
        olds = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(safe + "."))
        if len(olds) > BACKUP_KEEP:
            os.makedirs(DELETED_DIR, exist_ok=True)
            for f in olds[:-BACKUP_KEEP]:
                try:
                    os.replace(os.path.join(BACKUP_DIR, f),
                               os.path.join(DELETED_DIR, f.replace(".json", ".bak.json")))
                except OSError:
                    pass
    except Exception:                                         # noqa: BLE001
        pass


def _log_write(summary: str) -> None:
    try:
        with open(WRITE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {summary}\n")
    except Exception:                                         # noqa: BLE001
        pass


def write_keys(items: dict) -> dict:
    """写入若干键。value 为 None 表示删除该键。

    逐个键**原子替换**（临时文件 + os.replace），并且只在内容真的变了才落盘 ——
    页面每次 tick 都会重写状态键，内容一样时不该制造磁盘写。
    """
    changed, removed, skipped = [], [], []
    for k, v in items.items():
        p = key_path(k)
        try:
            old = ""
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    old = f.read()
            if v is None:
                if old:
                    _rotate_backup(k, old)
                    # ★ 只移动、不删除：把主目录里的文件原子改名进 _deleted/
                    #   既是留档，也顺便完成了"从主目录移除"。
                    moved = None
                    try:
                        os.makedirs(DELETED_DIR, exist_ok=True)
                        move_dst = os.path.join(DELETED_DIR, _stamped_name(k))
                        if not os.path.exists(move_dst):
                            os.replace(p, move_dst)
                            moved = move_dst
                    except Exception as e:                    # noqa: BLE001
                        _log_write(f"移档失败 {k}: {type(e).__name__} {e}")
                    if moved:
                        removed.append(k)
                    else:
                        # 移不走就退一步真删（可能被策略拦，那也如实记下来）
                        try:
                            os.remove(p)
                            removed.append(k)
                        except Exception as e:                # noqa: BLE001
                            _log_write(f"删除失败 {k}: {type(e).__name__} {e}")
                else:
                    skipped.append(k)
                continue
            v = str(v)
            if v == old:
                skipped.append(k)
                continue
            _rotate_backup(k, old)
            p_written = False
            tmp = p + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(v)
                os.replace(tmp, p)          # 原子替换：理想路径
                p_written = True
            except OSError:
                # ★ 兜底：某些环境会拦"替换已存在文件"（实测沙箱里 os.replace
                #   报 WinError 5 拒绝访问，但同目录直接覆盖写是允许的）。
                #   原子性换稳健性 —— 宁可非原子也要把数据写下去。
                try:
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(v)
                    p_written = True
                except Exception as e:                    # noqa: BLE001
                    _log_write(f"写入失败 {k}: {type(e).__name__} {e}")
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            if p_written:
                changed.append(k)
        except Exception as e:                                # noqa: BLE001
            _log_write(f"写入失败 {k}: {type(e).__name__} {e}")
    if changed or removed:
        parts = []
        if changed:
            parts.append("写入 " + ",".join(changed))
        if removed:
            parts.append("删除 " + ",".join(removed))
        _log_write("；".join(parts) + f"（{len(skipped)} 个未变化）")
    return {"changed": changed, "removed": removed, "skipped": skipped}


# ------------------------------------------------------------------ 页面注入
def build_page() -> bytes:
    """把磁盘上的历史数据内联进页面，让转接层能同步读到。

    ★ 为什么用"内联注入"而不是让页面启动时再异步拉一次：
      页面的业务代码是**同步**读 localStorage 的（saveCache/loadCache/archiveRead
      都在普通函数里直接 getItem）。若改成异步，就得把整条调用链改成 async，
      改动面极大且容易漏。内联注入让转接层在脚本执行前就拿到全量数据，
      业务代码零改动、时序零风险。
    ★ 必须转义 '<'：数据是用户可写内容，一旦出现 '</script>' 会提前闭合脚本块。
      JSON 里把 < 写成 \\u003c 语义完全等价，且不可能被解析成标签。
    """
    with open(PAGE, encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps({"ok": True, "data": read_all()}, ensure_ascii=False)
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028") \
                     .replace("\u2029", "\\u2029")
    seed = (f"<script>window.__FLOWLIVE_DISK_SEED__={payload};</script>\n")
    marker = "<script>\n\"use strict\";"
    if marker in html:
        html = html.replace(marker, seed + marker, 1)
    else:                        # 页面被改过时兜底插到第一个 <script> 前
        i = html.find("<script>")
        html = html[:i] + seed + html[i:] if i >= 0 else seed + html
    return html.encode("utf-8")


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "FlowLiveDisk/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):                        # noqa: A003
        pass                                                  # 静默，别刷屏

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):                                         # noqa: N802
        route = self.path.split("?", 1)[0]
        if route == "/__store":
            with _LOCK:
                self._json(200, {"ok": True, "data": read_all()})
            return
        if route == "/__ping":
            self._json(200, {"ok": True, "root": ROOT, "data": DATA_DIR,
                             "nkeys": len(read_all())})
            return
        if route in ("/", "/index.html", "/资金流向看板.html"):
            try:
                with _LOCK:
                    body = build_page()
            except FileNotFoundError:
                self._send(500, "找不到页面文件".encode("utf-8"),
                           "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def do_POST(self):                                        # noqa: N802
        route = self.path.split("?", 1)[0]
        if route != "/__store":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            self._json(413, {"ok": False, "err": "bad length"})
            return
        try:
            obj = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:                                # noqa: BLE001
            self._json(400, {"ok": False, "err": f"bad json: {e}"})
            return
        sets = obj.get("set") or {}
        if not isinstance(sets, dict):
            self._json(400, {"ok": False, "err": "set must be object"})
            return
        with _LOCK:
            res = write_keys(sets)
        self._json(200, {"ok": True, **res})


# ------------------------------------------------------------------ 启动
def free_port(preferred: int) -> int:
    for p in [preferred] + [preferred + i for i in range(1, 40)]:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    with socket.socket() as s:                                # 全占用就让系统给
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--port", type=int, default=8849)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(PAGE):
        print(f"[错误] 找不到页面文件：{PAGE}")
        return 1

    n = len(read_all())
    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 66)
    print("  资金流向实时看板（D 盘版）")
    print("=" * 66)
    print(f"  数据目录 : {DATA_DIR}")
    print(f"  已载入   : {n} 个存储键")
    print(f"  访问地址 : {url}")
    print()
    print("  浏览器会自动打开；关闭本窗口即停止服务。")
    print("  （页面用 http://127.0.0.1 打开是**必须**的：")
    print("    只有这样才能把数据写到 D 盘，file:// 直开写不了磁盘。）")
    print("=" * 66)

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
