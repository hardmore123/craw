"""加拿大官网 SPEC 入口页的通用链接抽取与电视范围过滤工具。"""
from __future__ import annotations

import html as html_lib
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from ..fetchers import Dom


_NON_TV_RE = re.compile(
    r"monitor|sound\s*bar|speaker|headphone|audio|projector|laser\s*cinema|"
    r"mobile|phone|tablet|appliance|refrigerator|fridge|washer|dryer|"
    r"air\s*condition|accessor(?:y|ies)?|camera|laptop|computer",
    re.I,
)


def clean_url(base_url: str, href: str) -> str:
    """转绝对 URL，并去掉查询串/片段，便于产品页去重。"""
    raw = str(href or "").strip()
    if not raw:
        return ""
    full = urljoin(base_url.rstrip("/") + "/", raw)
    parts = urlsplit(full)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/")


def anchor_records(dom: Dom | None, selector: str, html: str = "",
                   limit: int = 2000) -> list[dict[str, str]]:
    """读取入口链接及其可见文本/卡片文本。

    浏览器路径优先用页面 JS 找最近产品卡，给电视过滤提供第二层产品类型依据；
    静态或旧 Dom 实现退回 HTML href 抽取，仍由调用方做 URL 与型号过滤。
    """
    out: list[dict[str, str]] = []
    eval_js = getattr(dom, "eval_js", None) if dom is not None else None
    if callable(eval_js):
        script = f"""() => Array.from(document.querySelectorAll({json.dumps(selector)}))
          .slice(0, {int(limit)})
          .map(a => {{
            const text = e => (e?.innerText || e?.textContent || '')
              .replace(/\\s+/g, ' ').trim();
            const card = a.closest('[class*="product"], [class*="card"], article, li')
              || a.parentElement;
            return {{
              href: a.href || a.getAttribute('href') || '',
              text: text(a),
              card: text(card),
              aria: a.getAttribute('aria-label') || '',
              title: a.getAttribute('title') || ''
            }};
          }})"""
        try:
            values = eval_js(script) or []
            if isinstance(values, list):
                for value in values:
                    if not isinstance(value, dict):
                        continue
                    out.append({key: str(value.get(key) or "").strip()
                                for key in ("href", "text", "card", "aria", "title")})
        except Exception:
            out = []
    if not out:
        for href in re.findall(r"href\s*=\s*([\"'])(.*?)\1", html or "", re.I | re.S):
            out.append({"href": html_lib.unescape(href[1]), "text": "",
                        "card": "", "aria": "", "title": ""})
    return out[:limit]


def record_text(record: dict[str, str]) -> str:
    return " ".join(str(record.get(key) or "") for key in
                     ("href", "text", "card", "aria", "title"))


def clearly_non_tv(record: dict[str, str]) -> bool:
    """判断产品链接/标题是否明确属于非电视品类。"""
    return bool(_NON_TV_RE.search(record_text(record)))


def dedupe_records(records: list[dict[str, str]], base_url: str) -> list[dict[str, str]]:
    """按清洗后的 URL 去重，保留首次发现顺序。"""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in records:
        url = clean_url(base_url, record.get("href", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        item = dict(record)
        item["href"] = url
        out.append(item)
    return out


def unique_models(entries: list[tuple[str, str]]) -> int:
    return len({model.upper() for model, _ in entries if model})
