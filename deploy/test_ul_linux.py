#!/usr/bin/env python3
"""Linux 服务器 UL 可行性验证脚本 —— 3 分钟知结果。

用法：
  1. scp 到服务器：
     scp deploy/test_ul_linux.py hisense@10.18.87.51:/home/hisense/pro/Gitlab/DataGather/

  2. SSH 登服务器：
     ssh hisense@10.18.87.51
     cd /home/hisense/pro/Gitlab/DataGather

  3. 安装 Playwright + Chromium（首次）：
     python3 -m pip install playwright
     python3 -m playwright install --with-deps chromium

  4. 跑测试：
     # 测试1：纯 headless
     python3 test_ul_linux.py headless

     # 测试2：xvfb + headed（需先 sudo apt install -y xvfb）
     xvfb-run -a python3 test_ul_linux.py headed

结果判读：
  - "UL 首页正常加载" → 此模式可用
  - "Cloudflare Challenge" → 此模式被拦，需换方案
  - "网络不通" → 服务器本身访问不了 UL（可能需代理）
"""
import sys
import time

def test_ul(mode: str = "headless"):
    headless = (mode != "headed")
    print(f"\n{'='*50}")
    print(f"  UL Prospector Linux 验证")
    print(f"  模式: {'headless（无头）' if headless else 'headed（有头，配合 xvfb）'}")
    print(f"{'='*50}\n")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("✗ 未安装 playwright，请先执行：")
        print("  python3 -m pip install playwright")
        print("  python3 -m playwright install --with-deps chromium")
        return

    print("[1/4] 启动 Chromium ...")
    t0 = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        print(f"  浏览器启动耗时: {time.time()-t0:.1f}s")

        print("[2/4] 访问 UL 首页 https://iq.ulprospector.com/en ...")
        try:
            page.goto("https://iq.ulprospector.com/en",
                      wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)  # 等 Cloudflare JS challenge 完成
        except Exception as e:
            print(f"  ✗ 页面加载失败: {type(e).__name__}: {e}")
            print("\n结论: 网络不通或超时，服务器可能需要代理才能访问 UL")
            browser.close()
            return

        print("[3/4] 分析页面内容 ...")
        content = page.content()
        title = page.title()
        url = page.url

        print(f"  标题: {title}")
        print(f"  URL:  {url[:80]}")
        print(f"  内容前200字: {content[:200]}")

        # 判断结果
        cf_signs = ["cf-error", "cf-challenge", "Checking your browser",
                    "Just a moment", "cf-turnstile", "challenge-platform"]
        is_cf = any(s.lower() in content.lower() for s in cf_signs)
        is_login = "ulprospector" in url.lower() and not is_cf
        is_blocked = "blocked" in content.lower()[:2000] or "403" in title

        print(f"\n[4/4] 结果判定:")
        if is_cf:
            print("  ⚠ Cloudflare Challenge 被触发！")
            print(f"  当前模式（{mode}）下 UL 不友好。")
            if headless:
                print("  → 建议试 xvfb + headed：xvfb-run -a python3 test_ul_linux.py headed")
            else:
                print("  → headed + xvfb 也被拦，需要方案 C（Windows Worker）")
        elif is_login or "UL Prospector" in content:
            print("  ✓ UL 首页正常加载！")
            print(f"  当前模式（{mode}）可用 → 可直接在 Linux 上跑 UL 查询（方案 B 可行）")
        elif is_blocked:
            print("  ✗ 被拦截（非 Cloudflare，可能是 IP 级封禁）")
            print("  → 需要代理或方案 C（Windows Worker）")
        else:
            print("  ? 无法确定（请人工看上面的内容判断）")
            print(f"  页面标题: {title}")

        # 额外：测一条 suggest 请求
        print("\n[附加] 测试 suggest API ...")
        try:
            resp = page.goto(
                "https://iq.ulprospector.com/en/suggest/_all?q=E67006",
                wait_until="domcontentloaded", timeout=15000)
            body = page.inner_text("body")[:500]
            if "items" in body or "qm" in body:
                print("  ✓ suggest API 返回正常数据！查询链路通")
            elif any(s.lower() in body.lower() for s in cf_signs):
                print("  ⚠ suggest API 也触发 Cloudflare")
            else:
                print(f"  ? suggest 返回: {body[:200]}")
        except Exception as e:
            print(f"  ✗ suggest 请求失败: {e}")

        browser.close()

    print(f"\n总耗时: {time.time()-t0:.1f}s")
    print("="*50)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "headless"
    if mode not in ("headless", "headed"):
        print(f"用法: python3 {sys.argv[0]} [headless|headed]")
        sys.exit(1)
    test_ul(mode)
