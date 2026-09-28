"""声明式抽取引擎。

抽取规则写在站点目录的 schema.json 里，不写进 Python —— 这是应对
Amazon 这类页面模板频繁变动的关键：改选择器只改配置，不动代码、不重新部署。

规则形态（字段级，按 selectors 顺序逐个尝试，第一个非空即采用）：
    {"selectors": ["#productTitle", "h1.title"], "transform": "clean_text"}
    {"selectors": ["#acrPopover"], "attr": "title", "transform": "rating"}
    {"self_attr": "id"}                                    # 容器元素自身属性
    {"kv": {"container": "#detailBullets li", "key_contains": "model number"}}
    {"regex": "\\"asin\\"\\s*:\\s*\\"([A-Z0-9]{10})\\""}     # 兜底：对整页 HTML 正则
    {"jsonld": "offers.price"}                             # 兜底：schema.org 结构化数据

多选择器兜底链（fallback chain）不是过度设计：实测中
#productDetails_techSpec_section_1 在部分模板不存在，型号实际在
#detailBullets_feature_div，必须逐个试。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .fetchers import Dom
from .models import Review, ReviewSummary, SpecItem

# ---------------------------------------------------------------- transforms

_WS_RE = re.compile(r"\s+")
_NUM_RE = re.compile(r"\d[\d,.\s\u00a0]*")
_CUR_PATTERNS = (
    ("CAD", ("cdn$", "c$", "cad", "ca$")),
    ("USD", ("us$", "usd")),
    ("EUR", ("€", "eur")),
    ("GBP", ("£", "gbp")),
)


def t_clean_text(v: str) -> str:
    return _WS_RE.sub(" ", (v or "").replace("\u200e", "").replace("\u200f", "")).strip()


def t_money(v: str) -> float | None:
    """从价格文本解析金额。兼容 'CDN$ 4,999.99' / '$1,299.00' / '1 299,00 €'。"""
    if not v:
        return None
    m = _NUM_RE.search(v.replace("\u00a0", " "))
    if not m:
        return None
    s = m.group(0).strip().replace(" ", "")
    # 判定小数分隔符：取最后出现的 . 或 , 且其后位数 <= 2
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    dec = max(last_dot, last_comma)
    if dec != -1 and len(s) - dec - 1 in (1, 2):
        int_part = re.sub(r"[.,]", "", s[:dec])
        frac = s[dec + 1:]
        s = f"{int_part}.{frac}"
    else:
        s = re.sub(r"[.,]", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def t_currency(v: str) -> str:
    low = (v or "").lower()
    for code, marks in _CUR_PATTERNS:
        if any(m in low for m in marks):
            return code
    if "$" in low:
        return "CAD"          # 本项目面向加拿大站点，裸 $ 视为 CAD
    return ""


def t_currency_mx(v: str) -> str:
    """墨西哥站专用货币归一：裸 $ / MXN / M.N. / MN$ 一律视为 MXN。

    墨西哥零售站价格常写「$12,999.00」「$12,999 MXN」「$12,999 M.N.」，与加拿大站
    的裸 $=CAD 冲突，因此单列一个 transform 供 *_mx 站点 schema 使用，不改动
    公共 t_currency（避免影响加拿大/美国线）。识别到明确的 USD/EUR 等仍按原样返回。"""
    low = (v or "").lower()
    if any(m in low for m in ("mxn", "m.n.", "mn$", "pesos")):
        return "MXN"
    for code, marks in _CUR_PATTERNS:
        if code == "CAD":
            continue          # 墨西哥站不把裸 $ 当 CAD
        if any(m in low for m in marks):
            return code
    if "$" in low:
        return "MXN"          # 墨西哥站裸 $ 视为 MXN
    return "MXN"              # 墨西哥站兜底 MXN


def t_int(v: str) -> int | None:
    if not v:
        return None
    m = _NUM_RE.search(v.replace("\u00a0", " "))
    if not m:
        return None
    s = re.sub(r"[^\d]", "", m.group(0))
    try:
        return int(s) if s else None
    except ValueError:
        return None


def t_float(v: str) -> float | None:
    return t_money(v)


def t_rating(v: str) -> float | None:
    """从 '4.5 out of 5 stars' / '4,5 sur 5' 取评分。"""
    if not v:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", v)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def t_bool_present(v: str) -> bool:
    return bool((v or "").strip())


_BRAND_NOISE = (
    (re.compile(r"^\s*visit\s+the\s+(.+?)\s+store\s*$", re.I), 1),
    (re.compile(r"^\s*visitez\s+la\s+boutique\s+(.+?)\s*$", re.I), 1),
    (re.compile(r"^\s*visita\s+la\s+tienda\s+de\s+(.+?)\s*$", re.I), 1),
    (re.compile(r"^\s*brand\s*[:：]\s*(.+?)\s*$", re.I), 1),
    (re.compile(r"^\s*marque\s*[:：]\s*(.+?)\s*$", re.I), 1),
    (re.compile(r"^\s*(.+?)\s+store\s*$", re.I), 1),
)


def t_brand(v: str) -> str:
    """清洗品牌噪音。Amazon 的 #bylineInfo 实测返回 'Visit the LG Store'。"""
    s = t_clean_text(v)
    if not s:
        return ""
    for rx, g in _BRAND_NOISE:
        m = rx.match(s)
        if m:
            return t_clean_text(m.group(g))
    return s


_SIZE_INCH_RE = re.compile(
    r"(\d{2,3}(?:\.\d)?)\s*(?:\"|”|″|inch|inches|in\b|型|v型|v型|-?インチ|インチ)", re.I)
_SIZE_NUM_RE = re.compile(r"\b(\d{2,3}(?:\.\d)?)\b")


def t_size_inch(v: str) -> str:
    """把各种尺寸写法统一为 X" 格式。

    覆盖：65 Inch / 65" / 65 inches / 65-inch / 65 in / 65型 / 65V型 / 65インチ。
    优先按英寸标记提取；无标记但有 2~3 位纯数字（TV 尺寸区间）时也取。
    取不到返回空串。整数去掉小数点（65.0 → 65）。
    """
    s = t_clean_text(v)
    if not s:
        return ""
    m = _SIZE_INCH_RE.search(s)
    num = None
    if m:
        num = m.group(1)
    else:
        m2 = _SIZE_NUM_RE.search(s)
        # 仅当整串就是个尺寸数字（避免把 3840 之类误当尺寸），限定 20~120 区间
        if m2:
            try:
                val = float(m2.group(1))
                if 20 <= val <= 120:
                    num = m2.group(1)
            except ValueError:
                num = None
    if not num:
        return ""
    try:
        f = float(num)
        num = str(int(f)) if f.is_integer() else str(f)
    except ValueError:
        pass
    return f'{num}"'


_PALACIO_MODEL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9-]{4,}(?![A-Za-z0-9])")


def t_palacio_model(v: str) -> str:
    """从 Palacio 商品标题中提取含字母和数字的电视型号 token。"""
    s = t_clean_text(v)
    if not s:
        return ""
    for token in _PALACIO_MODEL_TOKEN_RE.findall(s):
        if any(ch.isalpha() for ch in token) and any(ch.isdigit() for ch in token):
            return token.upper()
    return s


def t_in_stock(v: str) -> bool | None:
    """库存判断，覆盖英语/法语以及墨西哥站常见的 Disponible/InStock。"""
    low = (v or "").lower()
    if not low:
        return None
    compact = re.sub(r"[^a-z]", "", low)
    if any(k in low for k in ("out of stock", "unavailable", "sold out",
                              "rupture de stock", "non disponible", "agotado")) \
            or any(k in compact for k in ("outofstock", "unavailable", "soldout", "nodisponible")):
        return False
    if any(k in low for k in ("in stock", "en stock", "add to cart",
                              "ajouter au panier", "available", "disponible")) \
            or any(k in compact for k in ("instock", "enstock", "addtocart", "disponible")):
        return True
    return None


_LIV_MONEY_RE = re.compile(r"\$\s*([\d,]+)(\d{2})(?=\s*\$|$)")


def t_liverpool_money(v: str) -> float | None:
    """解析 Liverpool 在线 DOM 的金额：小数点被 invisible span 隐藏后会变成 $11,49930。"""
    s = t_clean_text(v)
    if not s:
        return None
    m = _LIV_MONEY_RE.search(s)
    if m:
        try:
            return float(m.group(1).replace(",", "") + "." + m.group(2))
        except ValueError:
            pass
    return t_money(s)


TRANSFORMS = {
    "clean_text": t_clean_text,
    "money": t_money,
    "liverpool_money": t_liverpool_money,
    "currency": t_currency,
    "currency_mx": t_currency_mx,
    "int": t_int,
    "float": t_float,
    "rating": t_rating,
    "bool_present": t_bool_present,
    "in_stock": t_in_stock,
    "brand": t_brand,
    "palacio_model": t_palacio_model,
    "size_inch": t_size_inch,
}


def apply_transform(raw: str, name: str) -> Any:
    if not name:
        return t_clean_text(raw)
    fn = TRANSFORMS.get(name)
    if fn is None:
        raise ValueError(f"未知 transform: {name}（可用: {sorted(TRANSFORMS)}）")
    return fn(raw)


# ---------------------------------------------------------------- JSON-LD

_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I)


def jsonld_blocks(html: str) -> list[Any]:
    out = []
    for m in _LDJSON_RE.finditer(html or ""):
        try:
            out.append(json.loads(m.group(1).strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def jsonld_find_product(html: str) -> dict | None:
    """在 JSON-LD 中找 @type=Product 的节点（实测 Amazon 没有，仅作通用兜底）。"""
    def walk(node):
        if isinstance(node, dict):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t] if t else []
            if any(str(x).lower() == "product" for x in types):
                return node
            for v in node.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(node, list):
            for v in node:
                r = walk(v)
                if r:
                    return r
        return None
    for b in jsonld_blocks(html):
        r = walk(b)
        if r:
            return r
    return None


def dig(node: Any, path: str) -> Any:
    """按 'offers.price' 取值，遇 list 取第一个元素。"""
    cur = node
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    if isinstance(cur, list):
        cur = cur[0] if cur else None
    return cur


# ---------------------------------------------------------------- Schema


def load_schema(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Extractor:
    """按 schema 从 Dom（或 HTML 文本）抽取结构化数据。"""

    def __init__(self, schema: dict, html: str = "", base_url: str = ""):
        self.schema = schema or {}
        self.html = html
        self.base_url = base_url
        self._ld: dict | None | bool = False      # 懒加载

    # ---- 单字段 ----
    def raw(self, dom: Dom | None, rule: dict) -> str:
        if not isinstance(rule, dict):
            return ""
        # 1) 容器自身属性（评价条目 id 等）
        if rule.get("self_attr") and dom is not None:
            v = dom.self_attr(rule["self_attr"])
            if v:
                return v
        # 1b) 容器自身文本。零售搜索卡片常常就是 <a>…标题…</a>，卡片内没有
        # 稳定的标题子选择器；`{"self_text": true}` 直接取卡片文本，
        # 比猜类名稳（Costco 的卡片标题实测没有可用的 class）。
        if rule.get("self_text") and dom is not None:
            v = dom.self_text()
            if v:
                return v
        # 2) 选择器链
        if dom is not None:
            attr = rule.get("attr")
            for sel in rule.get("selectors") or []:
                v = dom.attr(sel, attr) if attr else dom.text(sel)
                if v:
                    return v
        # 3) 键值列表（label: value 结构）
        kv = rule.get("kv")
        if kv and dom is not None:
            v = self._kv_lookup(dom, kv)
            if v:
                return v
        # 4) 整页正则兜底
        rx = rule.get("regex")
        if rx and self.html:
            m = re.search(rx, self.html, re.S | re.I)
            if m:
                return (m.group(1) if m.groups() else m.group(0)).strip()
        # 5) JSON-LD 兜底
        ld_path = rule.get("jsonld")
        if ld_path and self.html:
            if self._ld is False:
                self._ld = jsonld_find_product(self.html)
            if self._ld:
                v = dig(self._ld, ld_path)
                if v is not None:
                    return str(v)
        return ""

    def value(self, dom: Dom | None, rule: dict) -> Any:
        return apply_transform(self.raw(dom, rule), (rule or {}).get("transform", ""))

    def _kv_lookup(self, dom: Dom, kv: dict) -> str:
        """在 container 列表里按 key 关键字找对应值。

        Amazon 的 #detailBullets_feature_div li 形如
        'Item model number ‏ : ‎ OLED77G6WUA'，按分隔符切开取右半。
        """
        container = kv.get("container") or ""
        want = (kv.get("key_contains") or "").lower()
        if not container or not want:
            return ""
        for item in dom.sub(container, limit=int(kv.get("limit", 120))):
            text = t_clean_text(item.self_text())
            if not text or want not in text.lower():
                continue
            # 优先用子选择器（表格 th/td 结构）
            k_sel, v_sel = kv.get("key"), kv.get("value")
            if v_sel:
                v = item.text(v_sel)
                if v:
                    return t_clean_text(v)
            for sep in (":", "：", "\n"):
                if sep in text:
                    left, _, right = text.partition(sep)
                    if want in left.lower() and right.strip():
                        return t_clean_text(right)
            return text
        return ""

    # ---- 分组 ----
    def fields(self, dom: Dom | None, group: str) -> dict:
        spec = self.schema.get(group) or {}
        out: dict[str, Any] = {}
        for name, rule in spec.items():
            try:
                out[name] = self.value(dom, rule)
            except ValueError:
                raise
            except Exception:
                out[name] = None
        return out

    def specs(self, dom: Dom | None) -> list[SpecItem]:
        """规格：支持 table（th/td）与 kvlist（label: value）两种形态，可配多组。"""
        rules = self.schema.get("specs") or []
        if isinstance(rules, dict):
            rules = [rules]
        out: list[SpecItem] = []
        seen: set[str] = set()
        if dom is None:
            return out
        for rule in rules:
            container = rule.get("container") or ""
            if not container:
                continue
            kind = rule.get("type") or "table"
            skip = [s.lower() for s in (rule.get("skip_keys") or [])]
            for row in dom.sub(container, limit=int(rule.get("limit", 200))):
                key = value = ""
                if kind == "table":
                    key = t_clean_text(row.text(rule.get("key") or "th"))
                    value = t_clean_text(row.text(rule.get("value") or "td"))
                else:                                  # kvlist
                    text = t_clean_text(row.self_text())
                    for sep in (":", "："):
                        if sep in text:
                            k, _, v = text.partition(sep)
                            key, value = t_clean_text(k), t_clean_text(v)
                            break
                if not key or not value:
                    continue
                kl = key.lower()
                if kl in seen or any(s in kl for s in skip) or len(key) > 120:
                    continue
                seen.add(kl)
                out.append(SpecItem(key=key[:120], value=value[:1000]))
        return out

    def reviews(self, dom: Dom | None) -> list[Review]:
        cfg = self.schema.get("reviews") or {}
        container = cfg.get("container") or ""
        fields = cfg.get("fields") or {}
        if dom is None or not container or not fields:
            return []
        media = cfg.get("media") or {}
        out: list[Review] = []
        for i, block in enumerate(dom.sub(container, limit=int(cfg.get("limit", 60)))):
            vals: dict[str, Any] = {}
            for name, rule in fields.items():
                try:
                    vals[name] = self.value(block, rule)
                except Exception:
                    vals[name] = None
            images = self._media_list(block, media.get("images"))
            videos = self._media_list(block, media.get("videos"))
            review_url = str(vals.get("review_url") or "").strip()
            key = str(vals.get("review_key") or "").strip()
            if not key:
                # 没有站内 id 时用内容哈希兜底，保证增量去重仍可用
                basis = f"{vals.get('author','')}|{vals.get('review_date','')}|{str(vals.get('body',''))[:120]}"
                if not basis.strip("|"):
                    continue
                import hashlib
                key = "h" + hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]
            out.append(Review(
                review_key=key[:120],
                rating=_as_float(vals.get("rating")),
                title=_as_text(vals.get("title"))[:300],
                body=_as_text(vals.get("body"))[:5000],
                author=_as_text(vals.get("author"))[:120],
                review_date=_as_text(vals.get("review_date"))[:80],
                verified=vals.get("verified") if isinstance(vals.get("verified"), bool) else None,
                helpful_count=_as_int(vals.get("helpful_count")),
                review_url=self._abs_url(review_url),
                image_urls=images,
                video_urls=videos,
            ))
        return out

    def _media_list(self, block: Dom, mrule: dict | None) -> list[str]:
        """抽取评价内的图片/视频 URL 列表，按规则过滤/替换。

        mrule 例：
          {"selector": "img[data-hook='review-image-tile']", "attr": "src",
           "include": ["media-amazon.com/images/I/"], "exclude": ["avatars-global"],
           "hires": ["._SY88_", "._SL1600_"]}
        """
        if not mrule:
            return []
        sel = mrule.get("selector") or ""
        attr = mrule.get("attr") or "src"
        if not sel:
            return []
        raw = block.attr_list(sel, attr, limit=int(mrule.get("limit", 40)))
        inc = mrule.get("include") or []
        exc = mrule.get("exclude") or []
        hires = mrule.get("hires")           # [pat_from, pat_to] 拿高清图
        out: list[str] = []
        seen: set[str] = set()
        for u in raw:
            u = self._abs_url(u.strip())
            if not u:
                continue
            if inc and not any(s in u for s in inc):
                continue
            if exc and any(s in u for s in exc):
                continue
            if hires and len(hires) == 2:
                u = re.sub(hires[0], hires[1], u)
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _abs_url(self, u: str) -> str:
        if not u:
            return ""
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("/") and self.base_url:
            return self.base_url.rstrip("/") + u
        return u

    def summary(self, dom: Dom | None) -> ReviewSummary:
        vals = self.fields(dom, "summary")
        stars: dict[int, int] = {}
        for n in range(1, 6):
            v = _as_int(vals.get(f"star{n}"))
            if v is not None:
                stars[n] = v
        return ReviewSummary(avg_rating=_as_float(vals.get("avg_rating")),
                             total_count=_as_int(vals.get("total_count")),
                             stars=stars)

    def anchor_selector(self) -> str:
        return self.schema.get("anchor") or ""


def _as_text(v: Any) -> str:
    return "" if v is None else (v if isinstance(v, str) else str(v))


def _as_float(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return t_money(_as_text(v))


def _as_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    return t_int(_as_text(v))
