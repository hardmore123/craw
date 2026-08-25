"""Playwright 封装：给需要 JS 渲染 / 过反爬 / 登录的站点用（VDE、UL Product iQ）。

Playwright 是可选依赖，未安装时抛出带安装提示的异常，不影响纯 HTTP 的 TUV 抓取。
    pip install playwright
    python -m playwright install chromium
"""
from __future__ import annotations

from contextlib import contextmanager

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class PlaywrightMissing(RuntimeError):
    pass


@contextmanager
def browser_page(headless: bool = True, proxy: str | None = None, state_file: str | None = None):
    """产出一个已配好反检测参数的 page。

    proxy: 形如 "http://127.0.0.1:7890"，公司网络需要代理时传入。
    state_file: 存 cookie/localStorage 的 json 路径，用于免重复登录。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise PlaywrightMissing(
            "需要 Playwright：pip install playwright && python -m playwright install chromium"
        ) from e

    with sync_playwright() as p:
        launch_kwargs = {"headless": headless, "args": ["--disable-blink-features=AutomationControlled"]}
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        browser = p.chromium.launch(**launch_kwargs)
        ctx_kwargs = {"user_agent": UA, "locale": "en-US", "viewport": {"width": 1440, "height": 900}}
        if state_file:
            import os
            if os.path.exists(state_file):
                ctx_kwargs["storage_state"] = state_file
        context = browser.new_context(**ctx_kwargs)
        context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = context.new_page()
        try:
            yield page
        finally:
            if state_file:
                try:
                    context.storage_state(path=state_file)
                except Exception:
                    pass
            context.close()
            browser.close()


def dump_page(page, out_path: str) -> str:
    """把渲染后的 DOM 存盘 —— 用于一次性确认选择器。"""
    html = page.content()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
