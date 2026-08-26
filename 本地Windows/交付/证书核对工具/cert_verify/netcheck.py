"""网络可达性自检 —— 抓取前必跑。

本机实测（2026-08）：所在网络有 URL 白名单过滤设备（网关 10.18.0.250），
vde.com / iecee.org / productiq.ulprospector.com 均被拦或超时，
只有 certipedia.com、ul.com 可达。所以这个自检要放在流程最前面。
"""
from __future__ import annotations

import concurrent.futures as cf

from .http_client import HttpClient

TARGETS = [
    ("TUV-Rheinland Certipedia 首页", "https://www.certipedia.com/"),
    ("TUV-Rheinland 证书详情页", "https://www.certipedia.com/certificates/50679826?locale=en"),
    ("TUV-Rheinland 证书搜索", "https://www.certipedia.com/search/matching_product_certificates?locale=en&q=50679826"),
    ("VDE 证书查询接口(JSON)", "https://www.vde.com/www8/servlet/restSearch/2289904/"
                          "zertreg-certificates?wt=json&fq=id%3Azertreg%7Ccertificate%2340052271"
                          "&start=0&rows=50"),
    ("VDE 证书检索页", "https://www.vde.com/tic-en/marks-and-certificates/vde-approved-products/search"
                  "?search_t=name&page=1&certificateNumber=40052271"),
    ("VDE 入口(http)", "http://www.vde.com/certificate"),
    ("UL 官网", "https://www.ul.com/"),
    ("UL Product iQ", "https://productiq.ul.com/"),
    ("UL Prospector", "https://productiq.ulprospector.com/"),
    ("IECEE CB 证书库", "https://certificates.iecee.org/"),
    ("TUV SUD", "https://www.tuvsud.com/en"),
]


def _one(item):
    label, url = item
    c = HttpClient(min_interval=0, retries=1, timeout=20, use_cache=False)
    r = c.get(url)
    if r.blocked:
        return label, url, "被 URL 过滤拦截", "需要 IT 加白名单或配置代理"
    if r.status == 200:
        return label, url, "可达", ""
    if r.status == 403 or "HTTP 403" in r.text:
        return label, url, "403 反爬", "需 Playwright 真实浏览器（可能还要登录）"
    if r.status == -1:
        detail = r.text.replace("__FETCH_FAILED__", "").strip()[:70]
        note = detail
        if "CERTIFICATE_VERIFY_FAILED" in detail:
            note = "TLS 被网关中间人拦截（证书校验失败）→ 同样是白名单问题"
        elif "timed out" in detail or "10054" in detail:
            note = "连接被重置/超时 → 多为网关拦截"
        return label, url, "不可达", note
    return label, url, f"HTTP {r.status}", ""


def check(save: bool = True) -> str:
    import datetime
    import os

    rows = []
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        for label, url, verdict, note in ex.map(_one, TARGETS):
            rows.append(f"  {verdict:<14} {label:<24} {url[:95]}"
                        + (f"\n{'':>18}→ {note}" if note else ""))
    text = (f"网络可达性自检  {datetime.datetime.now():%Y-%m-%d %H:%M}\n" + "\n".join(rows))
    if save:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "netcheck_report.txt")
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            text += f"\n\n（已保存到 {p}）"
        except Exception:
            pass
    return text


if __name__ == "__main__":
    print(check())
