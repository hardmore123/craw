"""UL Prospector 登录态获取 —— 首次运行，人工登录一次，存下会话。

UL Prospector（iq.ulprospector.com）挡在 Cloudflare 机器人管理后面，
脚本直连一律 403。只有真浏览器能过 Cloudflare 挑战。所以流程是：

  1. 开一个有头的 Playwright 浏览器（用持久化配置目录）
  2. 你在里面手动登录 UL（连带过 Cloudflare、登录验证码）
  3. 关掉后，登录态留在配置目录，之后抓取复用

用法：
    python -m cert_verify.ul_login
登录成功后，在终端按回车，脚本会把状态存好并自检一次。
"""
from __future__ import annotations

import os
import sys

from .http_client import resolve_proxy

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 持久化用户配置目录：cookie / localStorage / Cloudflare 令牌都存这
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ul_profile")

HOME = "https://iq.ulprospector.com/en"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 优先用本机已装的 Edge（省去下载 chromium）；顺序尝试
BROWSER_CHANNELS = ["msedge", "chrome", "chromium"]


def _launch_persistent(p, proxy, headless: bool, extra_args=None):
    """依次尝试 msedge / chrome / chromium，返回 (context, channel)。"""
    from playwright.sync_api import Error as PWError
    args = ["--disable-blink-features=AutomationControlled"] + (extra_args or [])
    last = None
    for ch in BROWSER_CHANNELS:
        kw = {"headless": headless, "args": args, "user_agent": UA,
              "locale": "en-US", "viewport": None}
        if proxy:
            kw["proxy"] = {"server": proxy}
        if ch != "chromium":
            kw["channel"] = ch
        try:
            ctx = p.chromium.launch_persistent_context(PROFILE_DIR, **kw)
            return ctx, ch
        except PWError as e:
            last = e
            continue
    raise SystemExit(f"没有可用浏览器（试过 {BROWSER_CHANNELS}）：{last}")


def login() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("需要 Playwright：pip install playwright")

    proxy = resolve_proxy()
    os.makedirs(PROFILE_DIR, exist_ok=True)
    print(f"配置目录: {PROFILE_DIR}")
    print(f"代理    : {proxy or '（无，直连）'}")
    import time
    wait_minutes = 8
    print(f"浏览器已打开。请在窗口里登录，最多等 {wait_minutes} 分钟，")
    print("脚本会自动检测登录成功并存档（无需回终端操作）。\n", flush=True)

    with sync_playwright() as p:
        ctx, ch = _launch_persistent(p, proxy, headless=False,
                                     extra_args=["--start-maximized"])
        print(f"使用浏览器: {ch}", flush=True)
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"打开首页出错（可忽略，继续登录）：{type(e).__name__}", flush=True)

        deadline = time.time() + wait_minutes * 60
        logged = False
        last_note = 0.0
        while time.time() < deadline:
            try:
                url = page.url
                body = page.content()[:8000].lower()
                logged = ("logout" in body or "sign out" in body or "sign-out" in body
                          or "signout" in body or "bplogin=success" in url
                          or "my account" in body)
                if logged:
                    print(f"检测到已登录 ✅  URL={url}", flush=True)
                    break
            except Exception:
                pass
            if time.time() - last_note > 20:
                print(f"  等待登录中... 剩余 {int(deadline - time.time())}s", flush=True)
                last_note = time.time()
            time.sleep(3)

        state_path = os.path.join(PROFILE_DIR, "storage_state.json")
        try:
            ctx.storage_state(path=state_path)
            print(f"已保存登录态: {state_path}", flush=True)
        except Exception as e:
            print(f"保存 storage_state 失败（不影响持久化目录）：{e}", flush=True)

        if not logged:
            print("⚠ 超时未检测到登录标志；若你其实已登录，状态也已存档，可继续 probe。", flush=True)
        ctx.close()
    print("\n完成。接下来跑： python -m cert_verify.ul_probe E502081", flush=True)


if __name__ == "__main__":
    login()
