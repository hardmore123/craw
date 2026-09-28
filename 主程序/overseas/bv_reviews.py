"""BazaarVoice（BV）评价抓取：给用 BV 承载网评的零售站取评价明细。

背景（2026-09 实测，Costco CA）：
- Costco 商品页的评价区由 BazaarVoice 挂件渲染，**DOM 里没有评价明细**
  （`ol.bv-content-list li.bv-content-item` 恒为 0），但页面内嵌了 BV 的
  客户端配置，评价走的是一条可直连的 JSON 接口：
      https://apps.bazaarvoice.com/bfd/v1/clients/<client>/api-products/cv2/resources/data/reviews.json
- 该接口只要两个条件就能通（不需要登录、不需要 passkey）：
    1) 请求头 `Bv-Bfd-Token: <displayCode>,<site>,<locale>`
       —— 三个值都写在站点的 `*-config.js` 里，是**静态可推导**的；
    2) 请求头 `Origin`/`Referer` 指向该零售站（缺 Origin 会 401 Unauthorized）。
- 为什么不用浏览器渲染：浏览器里评价区也不出明细（懒加载条件苛刻），
  而这条 HTTP 路径稳定、快、可缓存。

★ 误抓风险（务必保留 ProductId/OriginalProductName 溯源）：
Costco 的 BV 是**按系列/商品族**聚合评价的。用 55 吋的商品号去查，
返回的 8 条里第一条的 ProductId 是 75 吋的商品号（OriginalProductName 写着
`Hisense 75" Class - U7SG Series`）。也就是说评价会串尺寸。
因此这里对每条评价都保留它真正的 ProductId/OriginalProductName，
并在 meta 里报告 `cross_product` 条数，导出/汇报时可据此剔除或标注。

配置形态（RetailSpec.reviews 段）：
    "reviews": {
      "source": "bazaarvoice_bfd",
      "bv": {
        "client": "Costco-EN_CA",
        "display_code": "20040_1_0",
        "site": "native_review_form",
        "locale": "en_CA",
        "origin": "https://www.costco.ca",
        "content_locale": "en_CA,en_US,fr_CA",
        "page_size": 100,
        "max_pages": 5
      }
    }
"""
from __future__ import annotations

import json
import urllib.parse

from .fetchers import HttpFetcher
from .models import Review, ReviewSummary

BV_SOURCE = "bazaarvoice_bfd"
_API_BASE = ("https://apps.bazaarvoice.com/bfd/v1/clients/{client}"
             "/api-products/cv2/resources/data")


def bv_config(spec: dict | None) -> dict:
    """取 spec.reviews.bv；未配置或源不是 BV 时返回 {}。"""
    reviews = (spec or {}).get("reviews") or {}
    if reviews.get("source") != BV_SOURCE:
        return {}
    cfg = reviews.get("bv") or {}
    return cfg if cfg.get("client") else {}


def is_bv_spec(spec: dict | None) -> bool:
    return bool(bv_config(spec))


def bfd_token(cfg: dict) -> str:
    """Bv-Bfd-Token：`displayCode,site,locale`（站点 config.js 里的静态值）。"""
    if cfg.get("token"):
        return str(cfg["token"])
    return ",".join(str(cfg.get(key) or "") for key in
                    ("display_code", "site", "locale")).strip(",")


def _headers(cfg: dict) -> dict:
    origin = str(cfg.get("origin") or "").rstrip("/")
    return {
        "Bv-Bfd-Token": bfd_token(cfg),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-CA,en;q=0.9",
        # Origin 是硬要求：缺了 BV 直接 401 Unauthorized（实测）。
        "Origin": origin,
        "Referer": origin + "/",
    }


def _photo_urls(raw) -> list[str]:
    """BV 照片结构随版本变化，尽力取原图 URL。"""
    out: list[str] = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        url = ""
        sizes = item.get("Sizes") or {}
        if isinstance(sizes, dict):
            for key in ("normal", "large", "original", "thumbnail"):
                node = sizes.get(key)
                if isinstance(node, dict) and node.get("Url"):
                    url = str(node["Url"])
                    break
        if not url:
            url = str(item.get("Url") or item.get("url") or "")
        if url:
            out.append(url)
    return out


def _video_urls(raw) -> list[str]:
    out: list[str] = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("VideoUrl") or item.get("Url") or
                  item.get("HlsUrl") or "")
        if not url:
            sizes = item.get("Sizes") or {}
            if isinstance(sizes, dict):
                for node in sizes.values():
                    if isinstance(node, dict) and node.get("Url"):
                        url = str(node["Url"])
                        break
        if url:
            out.append(url)
    return out


def _to_review(node: dict, product_url: str = "",
               requested_id: str = "") -> Review | None:
    if not isinstance(node, dict):
        return None
    rid = str(node.get("Id") or "").strip()
    title = str(node.get("Title") or "").strip()
    body = str(node.get("ReviewText") or "").strip()
    if not (rid or title or body):
        return None
    badges = node.get("Badges") or {}
    verified = None
    if isinstance(badges, dict) and badges:
        verified = "verifiedPurchaser" in badges
    helpful = node.get("TotalPositiveFeedbackCount")
    try:
        helpful_count = int(helpful) if helpful is not None else None
    except (TypeError, ValueError):
        helpful_count = None
    rating = node.get("Rating")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None
    review = Review(
        review_key=rid or f"bv:{node.get('SubmissionId') or title[:40]}",
        rating=rating,
        title=title,
        body=body,
        author=str(node.get("UserNickname") or "").strip(),
        review_date=str(node.get("SubmissionTime") or "").strip(),
        verified=verified,
        helpful_count=helpful_count,
        review_url=product_url,
        image_urls=_photo_urls(node.get("Photos")),
        video_urls=_video_urls(node.get("Videos")),
    )
    # 溯源：BV 按系列聚合，评价可能来自同系列别的尺寸。挂到 review_key 之外
    # 的额外信息没法进 Review 模型，就放进 title/正文之外的地方不合适——
    # 这里用 review_url 的锚点保留来源商品号，供导出时人工核对。
    src_pid = str(node.get("ProductId") or "").strip()
    if src_pid and requested_id and src_pid != str(requested_id):
        review.review_url = (product_url or "") + (
            f"#bv-src-product={src_pid}")
    return review


def _summary_from_payload(data: dict) -> ReviewSummary | None:
    resp = (data or {}).get("response") or {}
    node = resp.get("reviewSummary") if isinstance(resp, dict) else None
    if not isinstance(node, dict):
        return None
    primary = node.get("primaryRating") or {}
    avg = primary.get("average")
    total = node.get("numReviews")
    stars: dict[int, int] = {}
    for row in (primary.get("distribution") or []):
        if not isinstance(row, dict):
            continue
        try:
            stars[int(row.get("key"))] = int(row.get("count") or 0)
        except (TypeError, ValueError):
            continue
    try:
        avg = float(avg) if avg is not None else None
    except (TypeError, ValueError):
        avg = None
    try:
        total = int(total) if total is not None else None
    except (TypeError, ValueError):
        total = None
    if avg is None and total is None and not stars:
        return None
    return ReviewSummary(avg_rating=avg, total_count=total, stars=stars)


def fetch_bv_reviews(spec: dict, product_id: str, *,
                     product_url: str = "",
                     fetcher=None, max_reviews: int | None = None,
                     max_pages: int | None = None,
                     verbose: bool = False) -> tuple[list[Review], ReviewSummary | None, dict]:
    """按 spec.reviews.bv 配置取评价。返回 (reviews, summary, meta)。

    meta 字段：
      ok / status / total_reviews / pages / stop_reason / cross_product / error
    """
    cfg = bv_config(spec)
    meta: dict = {"ok": False, "status": 0, "total_reviews": 0, "pages": 0,
                  "stop_reason": "", "cross_product": 0, "error": ""}
    pid = str(product_id or "").strip()
    if not cfg or not pid:
        meta["stop_reason"] = "not_configured"
        return [], None, meta

    own = fetcher is None
    client = HttpFetcher() if own else fetcher
    base = _API_BASE.format(client=urllib.parse.quote(str(cfg["client"])))
    page_size = int(cfg.get("page_size") or 100)
    pages_cap = int(max_pages or cfg.get("max_pages") or 5)
    cap = int(max_reviews if max_reviews is not None
              else (cfg.get("max_reviews") or 100))
    content_locale = str(cfg.get("content_locale") or cfg.get("locale") or "")
    headers = _headers(cfg)
    summary: ReviewSummary | None = None
    reviews: list[Review] = []
    seen: set[str] = set()
    offset = 0
    try:
        # 1) 评分汇总（顺带给出 TotalResults，供翻页判断）
        surl = (f"{base}/display/0.2alpha/product/summary?productid={urllib.parse.quote(pid)}"
                f"&contentType=reviews%2Cquestions"
                f"&reviewDistribution=primaryRating%2Crecommended&rev=0"
                f"&contentlocale={urllib.parse.quote(content_locale)}")
        sres = client.get(surl, headers=headers)
        meta["status"] = sres.status
        # 注意：不要用 res.ok 判据。BlockDetector 有"正文过短即可疑"这一路，
        # 汇总 JSON 只有几百字节会被误标 blocked（too_short），但它是合法的。
        # 这里以 HTTP 200 + 能解析出 JSON 为准。
        if sres.status == 200 and sres.html:
            try:
                summary = _summary_from_payload(json.loads(sres.html))
            except ValueError:
                summary = None
        total_expected: int | None = (
            summary.total_count if summary and summary.total_count is not None else None)

        # 2) 评价明细分页（offset 递增 + 多重安全阀，防越界页返回首页造成死循环）
        empty_streak = 0
        while meta["pages"] < pages_cap and len(reviews) < cap:
            q = urllib.parse.urlencode({
                "apiVersion": "5.4", "filter": f"productid:{pid}",
                "limit": page_size, "offset": offset,
                "include": "products", "contentlocale": content_locale})
            res = client.get(f"{base}/reviews.json?{q}", headers=headers)
            meta["pages"] += 1
            if res.status != 200 or not res.html:
                meta["stop_reason"] = f"http_{res.status}"
                break
            try:
                data = json.loads(res.html)
            except ValueError:
                meta["stop_reason"] = "bad_json"
                break
            body = data.get("response") or {}
            rows = body.get("Results") or []
            total = body.get("TotalResults")
            if total is not None:
                try:
                    total_expected = int(total)
                except (TypeError, ValueError):
                    pass
            new = 0
            for node in rows:
                review = _to_review(node, product_url=product_url,
                                    requested_id=pid)
                if review is None or review.review_key in seen:
                    continue
                seen.add(review.review_key)
                reviews.append(review)
                new += 1
                if len(reviews) >= cap:
                    break
            if verbose:
                print(f"    [BV] offset={offset} 本页 {len(rows)} 条，新增 {new}，"
                      f"累计 {len(reviews)}")
            if not rows:
                empty_streak += 1
                meta["stop_reason"] = "empty_page"
                if empty_streak >= 1:
                    break
            if new == 0:
                # 页内全是已见过的（站点对越界 offset 回首页）→ 立即停，别空转
                meta["stop_reason"] = "page_all_known"
                break
            offset += page_size
            if total_expected is not None and offset >= total_expected:
                meta["stop_reason"] = "exhausted"
                break
        else:
            meta["stop_reason"] = meta["stop_reason"] or (
                "max_reviews" if len(reviews) >= cap else "max_pages")
        meta["ok"] = True
        meta["total_reviews"] = len(reviews)
        meta["cross_product"] = sum(
            1 for r in reviews if "#bv-src-product=" in (r.review_url or ""))
    except Exception as e:                                   # 网络/解析异常都不致命
        meta["error"] = f"{type(e).__name__}: {e}"
        meta["stop_reason"] = meta["stop_reason"] or "exception"
    finally:
        if own:
            client.close()
    return reviews, summary, meta
