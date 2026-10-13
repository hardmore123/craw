"""CQC 数字开放平台授权 —— 弹出浏览器人工登录一次（参照 ul_login）。

和 UL 登录同样思路：本机运行时用 Playwright 打开一个有头浏览器（优先本机 Edge，
也支持部署时指定离线 Chrome）。流程：

  1. 打开 <服务地址>/api/cqc/authorize
  2. 你在 CQC 页面登录并授权
  3. CQC 回调 <服务地址>/api/cqc/callback，后端用 code 换取并保存 refresh_token
  4. 脚本检测到「授权成功」页即结束（令牌已存在服务器端）

前提：cert_verify 服务已在运行，且 .env 已配置 CQC_CLIENT_ID / CQC_CLIENT_SECRET /
CQC_REDIRECT_URI / CQC_ENABLED=true；运行本脚本的机器浏览器要能访问该服务地址与 CQC。

用法：
    python -m cert_verify.cqc_login
    python -m cert_verify.cqc_login http://10.18.87.51/craw
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 服务地址（Nginx /craw 入口）：命令行参数优先，其次环境变量，最后默认。
DEFAULT_BASE = os.environ.get("CQC_SERVICE_URL", "http://10.18.87.51/craw").rstrip("/")

# 持久化浏览器配置目录：记住 CQC 登录态，重复授权时免重复登录。
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".cqc_profile", "browser")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 优先本机 Edge；部署到服务器可用 CERT_BROWSER_EXECUTABLE 指定离线 Chrome。
BROWSER_CHANNELS = ["msedge", "chrome", "chromium"]
BROWSER_EXECUTABLE = os.environ.get("CERT_BROWSER_EXECUTABLE", "").strip()


def _launch_persistent(p, headless: bool, extra_args=None):
    """启动持久化浏览器；CQC 为境内平台，直连不走代理。"""
    from playwright.sync_api import Error as PWError
    args = ["--disable-blink-features=AutomationControlled"] + (extra_args or [])
    last = None
    channels = [None] if BROWSER_EXECUTABLE else BROWSER_CHANNELS
    for ch in channels:
        kw = {"headless": headless, "args": args, "user_agent": UA,
              "locale": "zh-CN", "viewport": None}
        if BROWSER_EXECUTABLE:
            kw["executable_path"] = BROWSER_EXECUTABLE
        elif ch != "chromium":
            kw["channel"] = ch
        try:
            ctx = p.chromium.launch_persistent_context(PROFILE_DIR, **kw)
            return ctx, ("offline" if BROWSER_EXECUTABLE else ch)
        except PWError as e:
            last = e
            continue
    raise SystemExit(f"没有可用浏览器（试过 {BROWSER_EXECUTABLE or BROWSER_CHANNELS}）：{last}")


def login(base_url: str = DEFAULT_BASE) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("需要 Playwright：pip install playwright")

    import time

    base_url = base_url.rstrip("/")
    authorize_url = base_url + "/api/cqc/authorize"
    os.makedirs(PROFILE_DIR, exist_ok=True)
    wait_minutes = 8
    print(f"服务地址: {base_url}")
    print(f"授权入口: {authorize_url}")
    print(f"配置目录: {PROFILE_DIR}")
    print(f"浏览器已打开。请在窗口里登录 CQC 并授权，最多等 {wait_minutes} 分钟，")
    print("脚本会自动检测授权结果（无需回终端操作）。\n", flush=True)

    result = None  # "ok" / "fail" / None(超时)
    with sync_playwright() as p:
        ctx, ch = _launch_persistent(p, headless=False,
                                     extra_args=["--start-maximized"])
        print(f"使用浏览器: {ch}", flush=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(authorize_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"打开授权入口出错：{type(e).__name__}: {e}", flush=True)
            print("请确认服务已运行、地址可达（可先在浏览器手动打开上面的授权入口）。",
                  flush=True)

        deadline = time.time() + wait_minutes * 60
        last_note = 0.0
        while time.time() < deadline:
            try:
                url = page.url
                body = page.content()
                # 回调成功/失败页由后端 _cqc_page 渲染，含明确中文标题
                if "授权成功" in body:
                    result = "ok"
                    print(f"检测到授权成功 ✅  URL={url}", flush=True)
                    break
                if "授权失败" in body:
                    result = "fail"
                    # 摘出提示文字帮助定位
                    print(f"检测到授权失败 ❌  URL={url}", flush=True)
                    print("页面提示：", _strip_text(body), flush=True)
                    break
            except Exception:
                pass
            if time.time() - last_note > 20:
                print(f"  等待授权中... 剩余 {int(deadline - time.time())}s", flush=True)
                last_note = time.time()
            time.sleep(2)

        # 关闭前尽量向服务确认一次授权状态
        try:
            authorized = _check_status(base_url)
        except Exception:
            authorized = None
        ctx.close()

    if result == "ok" or authorized:
        print("\n完成：refresh_token 已保存在服务器端，后续核对可自动续期。", flush=True)
    elif result == "fail":
        print("\n授权失败：请检查 .env 的 CQC_* 配置、client-secret 是否正确、"
              "回调地址是否与平台一致。", flush=True)
    else:
        print("\n超时未检测到授权结果：若你其实已完成授权，可用 "
              "curl <服务地址>/api/cqc/status 复核。", flush=True)


def _strip_text(html: str) -> str:
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200]


def _check_status(base_url: str):
    import json
    import urllib.request
    req = urllib.request.Request(base_url + "/api/cqc/status",
                                 headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return bool(data.get("authorized"))


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE
    login(base)
