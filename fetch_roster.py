#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从米游社（原神接口）读取角色一览，同步到 data/roster.json

为什么需要它：角色「已有几命」直接决定每次出金的星辉返还，而抽卡记录只能覆盖
抽到过的角色（活动赠送、常驻自选、星辉兑换都不在记录里）。这个脚本直接拉官方
数据，一次同步全部角色。

────────────────────────────────────────────────────────
用法
────────────────────────────────────────────────────────
1) 浏览器登录 https://act.mihoyo.com/ ，F12 → Network → 任意请求的 Cookie，
   复制 `ltuid_v2=...; ltoken_v2=...;` 这一段（或整段 Cookie）。

2) 设置环境变量（推荐，避免命令历史留痕）：
       PowerShell:  $env:GACHA_COOKIE="ltuid_v2=xxx; ltoken_v2=yyy;"
       CMD:         set GACHA_COOKIE=ltuid_v2=xxx; ltoken_v2=yyy;
       Bash:        export GACHA_COOKIE="ltuid_v2=xxx; ltoken_v2=yyy;"

3) 预览变更（不写文件）：
       python fetch_roster.py
   确认无误后写入：
       python fetch_roster.py --yes

其他：
    python fetch_roster.py --uid 102322758 --server cn_gf01
    python fetch_roster.py --from-json resp.json --yes    # 用保存下来的响应离线导入

────────────────────────────────────────────────────────
安全提示
────────────────────────────────────────────────────────
Cookie 等同于账号登录凭证。只在本机使用，不要提交到仓库、不要发给任何人。
本脚本只发 GET 请求读取角色列表，不会做任何写操作。

接口风控：官方会按 x-rpc-app_version / DS 签名校验，版本号可能需随游戏更新。
若返回 1034（风控）或 10001（登录失效），换一个较新的 app_version 或重新登录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import string
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROSTER_PATH = os.path.join(HERE, "data", "roster.json")

API = "https://api-takumi-record.mihoyo.com/game_record/app/genshin/api/character"
SALT_CN = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"       # 国服 DS v2 salt
APP_VERSION = "2.71.1"

SERVER_BY_PREFIX = {
    "1": "cn_gf01", "2": "cn_gf01", "5": "cn_qd01",
    "6": "os_usa", "7": "os_euro", "8": "os_asia", "9": "os_cht",
}



# --------------------------------------------------------------------------
# 从本机浏览器读取 Cookie（Windows：DPAPI + AES-GCM 解密 Chromium 的 v10 记录）
# --------------------------------------------------------------------------
COOKIE_FILE = os.path.join(HERE, ".miyoushe_cookie")
WANT = ("ltuid_v2", "ltoken_v2", "cookie_token_v2", "account_id", "ltuid", "ltoken",
        "cookie_token", "account_id_v2", "login_ticket")


def _chromium_profiles():
    home = os.path.expanduser("~")
    cands = []
    for name in ("Edge", "Chrome", "Chromium", "BraveSoftware"):
        if name == "BraveSoftware":
            base = os.path.join(home, "AppData", "Local", "BraveSoftware", "Brave-Browser", "User Data")
        elif name == "Edge":
            base = os.path.join(home, "AppData", "Local", "Microsoft", "Edge", "User Data")
        elif name == "Chrome":
            base = os.path.join(home, "AppData", "Local", "Google", "Chrome", "User Data")
        else:
            base = os.path.join(home, "AppData", "Local", "Chromium", "User Data")
        if not os.path.isdir(base):
            continue
        for prof in ("Default", "Profile 1", "Profile 2", "Profile 3"):
            p = os.path.join(base, prof)
            if os.path.isdir(p):
                cands.append((name, base, p))
    return cands


def _dpapi_key(user_data_dir):
    import base64
    import json as _json
    import win32crypt
    ls = os.path.join(user_data_dir, "Local State")
    if not os.path.exists(ls):
        return None
    with open(ls, encoding="utf-8") as f:
        state = _json.load(f)
    enc = base64.b64decode(state["os_crypt"]["encrypted_key"])
    if enc[:5] == b"DPAPI":
        enc = enc[5:]
    try:
        return win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
    except Exception:
        return None


def _decrypt(blob, key):
    from Crypto.Cipher import AES
    if not blob:
        return ""
    if blob[:3] in (b"v10", b"v11") and key:
        nonce, payload = blob[3:15], blob[15:]
        try:
            c = AES.new(key, AES.MODE_GCM, nonce=nonce)
            return c.decrypt_and_verify(payload[:-16], payload[-16:]).decode("utf-8", "ignore")
        except Exception:
            return ""
    if blob[:3] == b"v20":
        return ""      # Chrome 127+ 的 App-Bound Encryption，离线解不开
    return blob.decode("utf-8", "ignore")


def _chromium_read(profile_dir):
    import shutil
    import sqlite3
    import tempfile
    db = os.path.join(profile_dir, "Network", "Cookies")
    if not os.path.exists(db):
        db = os.path.join(profile_dir, "Cookies")
    if not os.path.exists(db):
        return {}
    key = _dpapi_key(profile_dir)
    tmp = os.path.join(tempfile.gettempdir(), "_cookies_single.db")
    try:
        shutil.copy2(db, tmp)
    except Exception:
        return {}
    got = {}
    try:
        con = sqlite3.connect(tmp)
        for host, cname, plain, enc in con.execute(
                "select host_key, name, value, encrypted_value from cookies"):
            if not (host.endswith("miyoushe.com") or host.endswith("mihoyo.com")):
                continue
            if cname not in WANT:
                continue
            val = plain or _decrypt(enc, key)
            if val:
                got[cname] = val
        con.close()
    except Exception:
        pass
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    return got


def read_chromium_cookies():
    """返回 (dict, 说明)"""
    import shutil
    import sqlite3
    import tempfile
    found, notes = {}, []
    for name, user_data, prof in _chromium_profiles():
        db = os.path.join(prof, "Network", "Cookies")
        if not os.path.exists(db):
            db = os.path.join(prof, "Cookies")
        if not os.path.exists(db):
            continue
        key = _dpapi_key(user_data)
        tmp = os.path.join(tempfile.gettempdir(), f"_cookies_{name}.db")
        try:
            shutil.copy2(db, tmp)
        except Exception as e:
            notes.append(f"{name}: 数据库被占用（{e}）")
            continue
        try:
            con = sqlite3.connect(tmp)
            cur = con.execute("select host_key, name, value, encrypted_value from cookies")
            got = {}
            locked = 0
            for host, cname, plain, enc in cur:
                if not (host.endswith("miyoushe.com") or host.endswith("mihoyo.com")):
                    continue
                if cname not in WANT:
                    continue
                val = plain or _decrypt(enc, key)
                if not val:
                    locked += 1
                    continue
                got[cname] = val
            con.close()
            if got:
                found.update(got)
                notes.append(f"{name}/{os.path.basename(prof)}: 读到 {len(got)} 个")
            elif locked:
                notes.append(f"{name}/{os.path.basename(prof)}: {locked} 个 Cookie 是 v20（应用绑定加密），需手动复制")
            else:
                notes.append(f"{name}/{os.path.basename(prof)}: 未登录 miyoushe")
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass
    return found, notes


def read_firefox_cookies():
    import glob
    import shutil
    import sqlite3
    import tempfile
    pat = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Mozilla", "Firefox", "Profiles", "*", "cookies.sqlite")
    got = {}
    for db in glob.glob(pat):
        tmp = os.path.join(tempfile.gettempdir(), "_ff_cookies.db")
        try:
            shutil.copy2(db, tmp)
            con = sqlite3.connect(tmp)
            for host, cname, val in con.execute("select host, name, value from moz_cookies"):
                if not (host.endswith("miyoushe.com") or host.endswith("mihoyo.com")):
                    continue
                if cname in WANT and val:
                    got[cname] = val
            con.close()
        except Exception:
            pass
    return got


def cookie_from_browser():
    got, notes = read_chromium_cookies()
    if not got:
        ff = read_firefox_cookies()
        if ff:
            got = ff
            notes.append("Firefox: 读到 Cookie")
    return got, notes


def build_cookie(d):
    order = ["ltuid_v2", "ltoken_v2", "cookie_token_v2", "account_id", "account_id_v2",
             "ltuid", "ltoken", "cookie_token"]
    parts = [f"{k}={d[k]}" for k in order if d.get(k)]
    return "; ".join(parts)


def mask_cookie(d):
    out = []
    for k in ("ltuid_v2", "ltoken_v2", "cookie_token_v2", "account_id"):
        if d.get(k):
            v = d[k]
            out.append(f"{k}={v[:4]}…{v[-4:]}")
    return "、".join(out) or "（无）"


def load_saved_cookie():
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def save_cookie(s):
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(s)
    try:
        os.chmod(COOKIE_FILE, 0o600)
    except Exception:
        pass


BROWSERS = {
    "edge": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"],
    "chrome": [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
               r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
}


def find_browser(name="edge"):
    for p in BROWSERS.get(name, []):
        if os.path.exists(p):
            return p
    return None


def read_profile_cookies(profile_dir):
    """从一个指定 user-data-dir 读 Cookie（浏览器关闭后才能读到）"""
    return _chromium_read(profile_dir)


def login_flow(uid, server, browser="edge"):
    """先试主浏览器；被占用就用独立配置目录开一个登录窗口"""
    import subprocess
    import time
    import webbrowser

    print("[1/3] 先尝试直接读取已登录的浏览器 Cookie …")
    got, notes = cookie_from_browser()
    for n in notes:
        print("   ·", n)
    if got:
        ck = build_cookie(got)
        print(f"   读到：{mask_cookie(got)}")
        save_cookie(ck)
        return ck

    exe = find_browser(browser) or find_browser("chrome")
    if not exe:
        print("   没找到 Edge/Chrome，改为手动方式。")
        webbrowser.open("https://www.miyoushe.com/ys/")
        manual = input("   登录后把 Cookie 粘到这里：").strip()
        if not manual:
            raise SystemExit("未提供 Cookie")
        save_cookie(manual)
        return manual

    profile = os.path.join(HERE, ".miyoushe_profile")
    os.makedirs(profile, exist_ok=True)
    print(f"[2/3] 用独立配置启动浏览器（不影响你正在用的浏览器）：")
    print(f"      {exe}")
    print("      请在弹窗里登录米游社（扫码最方便），登录成功后**关闭这个窗口**。")
    subprocess.Popen([exe, f"--user-data-dir={profile}", "--no-first-run",
                      "--no-default-browser-check", "--disable-background-mode",
                      "https://www.miyoushe.com/ys/"])

    print("[3/3] 等待窗口关闭后读取 Cookie（最多等 5 分钟）…")
    deadline = time.time() + 300
    while time.time() < deadline:
        got = read_profile_cookies(profile)
        if got:
            ck = build_cookie(got)
            if ck:
                print(f"   读到：{mask_cookie(got)}")
                save_cookie(ck)
                return ck
        time.sleep(2)
        if int(time.time()) % 10 < 2:
            print("   …还在等（关掉登录窗口后会自动继续）")
    raise SystemExit("超时。可手动复制 Cookie 后运行 --cookie \"ltuid_v2=...; ltoken_v2=...\"")


def guess_server(uid: str) -> str:
    return SERVER_BY_PREFIX.get(uid[0], "cn_gf01")


def make_ds(salt: str = SALT_CN):
    t = str(int(time.time()))
    r = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    c = hashlib.md5(f"salt={salt}&t={t}&r={r}".encode()).hexdigest()
    return f"{t},{r},{c}"


def fetch(uid: str, server: str, cookie: str) -> dict:
    qs = urllib.parse.urlencode({"role_id": uid, "server": server})
    req = urllib.request.Request(API + "?" + qs, headers={
        "Cookie": cookie,
        "DS": make_ds(),
        "x-rpc-app_version": APP_VERSION,
        "x-rpc-client_type": "5",
        "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) miHoYoBBS/{APP_VERSION}",
        "Referer": "https://act.mihoyo.com/",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


ERRORS = {
    -100: "登录失效：Cookie 过期或不完整（需要 ltuid_v2 + ltoken_v2）",
    10001: "登录失效：请重新登录米游社后复制新的 Cookie",
    10102: "参数错误：检查 --uid / --server",
    1034: "触发风控：请稍后再试，或更新脚本里的 APP_VERSION",
    -5003: "角色数据不可见：游戏内 设置 → 隐私 → 关闭「角色详情公开」限制",
}


def parse_characters(payload: dict) -> dict:
    """把接口响应转成 {名字: (命座, 星级)}"""
    if payload.get("retcode") not in (0, None):
        code = payload.get("retcode")
        raise SystemExit(f"接口返回 {code}：{ERRORS.get(code, payload.get('message'))}")
    data = payload.get("data") or {}
    out = {}
    for a in data.get("list") or []:
        name = a.get("name")
        if not name:
            continue
        out[name] = (int(a.get("actived_constellation_num", 0)), int(a.get("rarity", 5)))
    if not out:
        raise SystemExit("接口没有返回角色（隐私设置未公开？）")
    return out


def diff(roster: dict, remote: dict):
    chars = roster.setdefault("characters", {})
    added, changed, same = [], [], 0
    for name, (c, star) in remote.items():
        cur = chars.get(name)
        if cur is None:
            added.append((name, c, star))
        elif cur.get("c", 0) != c:
            changed.append((name, cur.get("c", 0), c))
        else:
            same += 1
    return added, changed, same


def serve(uid, server, cookie, port=8765, preset=None):
    """本地小服务：网页点「从米游社同步」时来取数据（只监听 127.0.0.1）"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    cache = {"chars": preset}

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()

        def do_GET(self):
            if self.path.startswith("/health"):
                return self._send({"ok": True, "cached": bool(cache["chars"])})
            if self.path.startswith("/roster"):
                try:
                    if not cache["chars"]:
                        cache["chars"] = parse_characters(fetch(uid, server, cookie))
                    chars = cache["chars"]
                    return self._send({"ok": True,
                                       "characters": {n: {"c": c, "star": st} for n, (c, st) in chars.items()}})
                except SystemExit as e:
                    return self._send({"ok": False, "error": str(e)}, 500)
                except Exception as e:  # noqa: BLE001
                    return self._send({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            return self._send({"ok": False, "error": "not found"}, 404)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"本地同步服务已启动： http://127.0.0.1:{port}/roster")
    print("回到网页点「从米游社同步」即可；Ctrl+C 结束。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="从米游社同步角色命座到 data/roster.json")
    ap.add_argument("--uid", default="102322758")
    ap.add_argument("--server", default=None, help="默认按 UID 首位推断")
    ap.add_argument("--cookie", default=os.environ.get("GACHA_COOKIE", ""),
                    help="默认读环境变量 GACHA_COOKIE")
    ap.add_argument("--from-json", default=None, help="用已保存的接口响应离线导入")
    ap.add_argument("--login", action="store_true", help="打开浏览器登录并自动读取本机 Cookie")
    ap.add_argument("--cookie-from-browser", action="store_true", help="只从浏览器读 Cookie（不打开网页）")
    ap.add_argument("--yes", action="store_true", help="实际写入（默认只预览）")
    ap.add_argument("--no-build", action="store_true", help="写完后不自动重建 roster.js")
    ap.add_argument("--serve", action="store_true", help="启动本地服务供网页同步（127.0.0.1）")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)

    if a.login or a.cookie_from_browser:
        if not a.cookie_from_browser:
            a.cookie = login_flow(a.uid, a.server or guess_server(a.uid))
        else:
            got, notes = cookie_from_browser()
            for n in notes:
                print("·", n)
            a.cookie = build_cookie(got)
            if not a.cookie:
                raise SystemExit("浏览器里没读到 miyoushe 的 Cookie，请用 --login 或手动粘贴")
            save_cookie(a.cookie)
            print(f"读到：{mask_cookie(got)}")

    if not a.cookie:
        a.cookie = load_saved_cookie()

    if a.serve:
        preset = None
        if a.from_json:
            preset = parse_characters(json.load(open(a.from_json, encoding="utf-8")))
        elif not a.cookie:
            raise SystemExit("--serve 需要 GACHA_COOKIE（或用 --from-json 提供离线数据）")
        return serve(a.uid, a.server or guess_server(a.uid), a.cookie, a.port, preset)

    if a.from_json:
        with open(a.from_json, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        if not a.cookie:
            raise SystemExit("缺少 Cookie：请设置环境变量 GACHA_COOKIE（见脚本顶部说明）")
        server = a.server or guess_server(a.uid)
        print(f"→ 请求 UID {a.uid} / {server} …")
        payload = fetch(a.uid, server, a.cookie)

    remote = parse_characters(payload)
    with open(ROSTER_PATH, encoding="utf-8") as f:
        roster = json.load(f)
    added, changed, same = diff(roster, remote)

    print(f"接口返回 {len(remote)} 个角色；本地 {len(roster['characters'])} 个")
    print(f"  新增 {len(added)} · 命座变化 {len(changed)} · 一致 {same}")
    for name, c, star in added:
        print(f"  + {name} {star}★ C{c}")
    for name, old, new in changed:
        print(f"  ~ {name} C{old} → C{new}")
    missing = [n for n in roster["characters"] if n not in remote]
    if missing:
        print(f"  ! 本地有、接口没有（{len(missing)}）：{'、'.join(missing[:12])}"
              f"{' …' if len(missing) > 12 else ''}")

    if not added and not changed:
        print("\n没有变化，无需写入。")
        return 0
    if not a.yes:
        print("\n预览模式：加 --yes 才会写入 data/roster.json")
        return 0

    for name, (c, star) in remote.items():
        roster["characters"][name] = {"c": c, "star": star}
    roster["updated"] = time.strftime("%Y-%m-%d")
    roster.setdefault("source", "")
    if "米游社接口" not in roster["source"]:
        roster["source"] = (roster["source"] + " + 米游社接口").strip(" +")
    with open(ROSTER_PATH, "w", encoding="utf-8") as f:
        json.dump(roster, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {ROSTER_PATH}")

    if not a.no_build:
        import subprocess
        subprocess.run([sys.executable, os.path.join(HERE, "build_data.py")], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
