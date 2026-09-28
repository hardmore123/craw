"""価格.com（kakaku.com）日本站适配器：按型号抓用户评价。

用途聚焦「按型号 → 定位商品 → 读发售日 → 抓用户评价」，不做价格/规格入库。
两类用户内容：
  - レビュー（结构化评价）：review.kakaku.com/review/<item>/，有评分/标题/正文；
  - クチコミ（bbs 讨论板）：bbs.kakaku.com/bbs/<item>/，帖子形式。
策略：优先レビュー，レビュー 404/为空时回退クチコミ。

实测（2026-09，项目 Playwright msedge）：
  - 搜索 https://search.kakaku.com/<型号>/ → a[href*='/item/'] 取 item id（K000xxxx）
  - 商品页 https://kakaku.com/item/<item>/ 的 p.p-prdMakerInfo_txt 含「発売」，
    如「2026年5月下旬 発売」「2026年6月13日 発売」
  - レビュー每条：外层 .reviewBoxWt；日期 p.entryDate；总分 .revRateBox table.total td；
    标题 .reviewTitle a；正文 p.revEntryCont；详情链 a[href*='ReviewCD=']
  - クチコミ每帖：div.box06；作者 .title a.impact05；正文 .boxIn > p:first-child；
    书込番号/日期 p.date、顶部 p.writeDateTime
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import L2_MEDIUM
from .. import SiteAdapter


class KakakuJpAdapter(SiteAdapter):
    code = "kakaku_jp"
    name = "価格.com（日本）"
    base_url = "https://kakaku.com"
    country = "Japan"
    channel = "kakaku.com"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 7.0        # 有反爬，礼貌抓取

    # ---------- URL 规则 ----------
    def search_url(self, keyword: str, page: int = 1) -> str:
        """站内搜索。实测会 302 到 search.kakaku.com/<kw>/。"""
        kw = urllib.parse.quote(keyword.strip())
        return f"https://search.kakaku.com/{kw}/"

    def item_url(self, item_id: str) -> str:
        return f"{self.base_url}/item/{item_id}/"

    def product_url(self, sku: str) -> str:
        """支持 S3/S4 场景：sku 可以是 item_id(K000xxxx) 或完整URL。"""
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        # item_id 格式 K0001234567
        if re.match(r"K\d+", v):
            return self.item_url(v)
        return v

    def review_url(self, item_id: str, page: int = 1) -> str:
        url = f"https://review.kakaku.com/review/{item_id}/"
        if page and page > 1:
            url += f"Page={int(page)}/"
        return url

    def bbs_url(self, item_id: str, page: int = 1) -> str:
        url = f"https://bbs.kakaku.com/bbs/{item_id}/"
        if page and page > 1:
            url += f"?Page={int(page)}"
        return url

    # ---------- 搜索结果 → item id ----------
    _ITEM_RE = re.compile(r"/item/(K\d+)/")

    def first_item_id(self, dom, want_model: str = "") -> str:
        """从搜索结果页取最匹配的商品 item id。

        匹配优先级（避免误取相关配件/旧型号，如搜 50A6S 命中 2012 年的
        DCB-IP50A6SVP）：
          1) 链接文本「以型号开头」（如「50A6S [50インチ]」）——最可靠；
          2) 型号作为独立词出现（前后是空格/[ 等分隔，而非更长词的一部分）；
          3) 型号是文本子串（宽松兜底）；
          4) 全都不匹配时，才退回搜索结果第一条。
        """
        if dom is None:
            return ""
        want = self._norm(want_model)
        hrefs = dom.attr_list("a[href*='/item/']", "href", limit=60)
        texts = dom.texts("a[href*='/item/']")
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for href, text in zip(hrefs, texts):
            m = self._ITEM_RE.search(href or "")
            if not m:
                continue
            item_id = m.group(1)
            if item_id in seen:
                continue
            seen.add(item_id)
            pairs.append((item_id, text or ""))

        if want:
            # 1) 文本规范化后以型号开头
            for item_id, text in pairs:
                if self._norm(text).startswith(want):
                    return item_id
            # 2) 型号作为独立词（用原始文本按非字母数字切词）
            for item_id, text in pairs:
                tokens = re.split(r"[^A-Za-z0-9]+", (text or "").upper())
                if want in {self._norm(tok) for tok in tokens if tok}:
                    return item_id
            # 3) 宽松子串兜底
            for item_id, text in pairs:
                if want in self._norm(text):
                    return item_id
        # 4) 无型号线索时退回第一条
        return pairs[0][0] if pairs else ""

    @staticmethod
    def _norm(s: str) -> str:
        return "".join(ch for ch in (s or "").upper() if ch.isalnum())

    # ---------- 发售日 → 上市时间 / 年份 ----------
    _DATE_TXT_RE = re.compile(r"(20\d\d)\s*年\s*(\d{1,2})?\s*月?\s*([上中下]旬|\d{1,2}日)?\s*発売")

    def release_text(self, dom) -> str:
        """返回如「2026年5月下旬 発売」的原始上市时间文本，找不到返回空。"""
        if dom is None:
            return ""
        for t in dom.texts("p.p-prdMakerInfo_txt"):
            if "発売" in (t or ""):
                return " ".join((t or "").split())
        # 兜底：全页文本里找「20xx年..発売」
        body = dom.self_text()
        m = self._DATE_TXT_RE.search(body or "")
        if m:
            return m.group(0).strip()
        return ""

    def release_year(self, release_text: str) -> int | None:
        m = re.search(r"(20\d\d)\s*年", release_text or "")
        return int(m.group(1)) if m else None

    def release_display(self, release_text: str) -> str:
        """整理成模板「上市时间」列展示值，去掉尾部『 発売』。"""
        s = (release_text or "").replace(" 発売", "").replace("発売", "").strip()
        return s

    # ---------- 价格（最安価格）----------
    _PRICE_RE = re.compile(r"[¥￥]\s*([\d,]+)")

    def lowest_price(self, dom) -> str:
        """商品页「最安価格」。返回纯数字字符串（日元），找不到返回空。

        实测结构：
          <p class="p-prdInfoLowprice_ttl" data-jsread-lowprice="173967">最安価格</p>
          <a class="p-prdInfoLowprice_entity">173,967<span>円</span></a>
        """
        if dom is None:
            return ""
        # 1) 属性最稳：data-jsread-lowprice 已是纯数字
        v = dom.attr("p.p-prdInfoLowprice_ttl", "data-jsread-lowprice")
        if v and v.isdigit():
            return v
        # 2) 最安价格文本
        for sel in ["a.p-prdInfoLowprice_entity", ".p-prdInfoLowprice_entity",
                    "span.priceTxt", ".p-priceLeadPrice_price"]:
            for t in dom.texts(sel):
                m = re.search(r"([\d,]{3,})", t or "")
                if m:
                    return m.group(1).replace(",", "")
        return ""

    # ---------- 各店铺分列价格（价格监控模板用）----------
    # 模板店铺列 → 该列匹配的 kakaku 店铺名关键词（规范化后子串匹配）。
    # kakaku 商品页「価格比較」里，主要大型店铺一览的每行店铺名在
    # a.p-priceList_shopName h3；本店销售价在同一 li 的 p.p-priceList_price。
    # 楽天/Amazon 的最安店也会出现在带 h3 的一览里（店铺名如「Amazon」/「楽天市場」）。
    SHOP_COLUMNS: list[tuple[str, tuple[str, ...]]] = [
        ("エディオンネットショップ", ("エディオン",)),
        ("ヨドバシ.com", ("ヨドバシ",)),
        ("ヤマダウェブコム", ("ヤマダ",)),
        ("ビックカメラ.com", ("ビックカメラ", "ビック")),
        ("ケーズデンキ", ("ケーズ",)),
        ("Joshin", ("JOSHIN",)),
        ("ノジマオンライン", ("ノジマ",)),
        ("Amazon", ("AMAZON",)),
    ]

    @staticmethod
    def _to_int_price(text: str) -> int | None:
        """把「175,330 円」「175330」等价格文本转成整数日元，失败返回 None。"""
        m = re.search(r"([\d,]{3,})", (text or "").replace("\u3000", " "))
        if not m:
            return None
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None

    def shop_prices(self, dom) -> dict[str, int]:
        """解析商品页「主要店铺一览」，返回 {kakaku店铺名: 最低整数价}。

        kakaku 的价格一览含两组 <li.p-priceList_item>：
          1) 带 <h3> 的主要大型店铺 + 楽天/Amazon 最安店（模板需要的就是这组）；
          2) 无 <h3> 的全量在售明细（店铺名是 <a> 直接文本，含大量小店）。
        为对齐模板只取第 1 组（h3 有值）；同一店铺出现多次时取最低价。
        """
        out: dict[str, int] = {}
        if dom is None:
            return out
        for li in dom.sub("li.p-priceList_item", limit=200):
            shop = " ".join((li.text("a.p-priceList_shopName h3") or "").split())
            if not shop:                       # 无 h3 的是第 2 组明细，跳过
                continue
            price = self._to_int_price(li.text("p.p-priceList_price"))
            if price is None:
                continue
            prev = out.get(shop)
            if prev is None or price < prev:
                out[shop] = price
        return out

    def map_shop_columns(self, shop_prices: dict[str, int]) -> dict[str, int]:
        """把 {kakaku店铺名: 价} 映射到模板 8 个店铺列 {列名: 价}。

        用规范化子串匹配（去空格/大小写），同一列命中多个店铺取最低价。
        未命中的列不出现在结果里（由导出层填空）。
        """
        result: dict[str, int] = {}
        for col_name, keywords in self.SHOP_COLUMNS:
            best: int | None = None
            for shop_name, price in shop_prices.items():
                nshop = self._norm(shop_name)
                if any(self._norm(kw) in nshop for kw in keywords):
                    if best is None or price < best:
                        best = price
            if best is not None:
                result[col_name] = best
        return result

    # ---------- 尺寸（从型号/标题解析）----------
    _SIZE_RE = re.compile(r"(\d{2,3})\s*(?:インチ|型|V型|v)", re.IGNORECASE)

    def size_inch(self, model: str, page_text: str = "") -> str:
        """推断尺寸（英寸）。优先页面「NNインチ」，其次型号前缀数字。"""
        m = self._SIZE_RE.search(page_text or "")
        if m:
            return f'{m.group(1)}"'
        # 型号前缀数字，如 65U8S / 100U7S / 32A5S
        m = re.match(r"\s*(\d{2,3})", model or "")
        if m:
            return f'{m.group(1)}"'
        return ""

    # ---------- クチコミ 讨论主题（thread）列表 ----------
    _SORTID_RE = re.compile(r"/bbs/(K\d+)/SortID=(\d+)/")

    def bbs_threads(self, dom, limit: int = 60, with_meta: bool = False):
        """从讨论板列表页解析主题清单。

        默认返回 [(thread_url, thread_title), ...]（保持向后兼容）。
        with_meta=True 时返回 [(thread_url, thread_title, post_count, last_key), ...]，
        其中 post_count/last_key 为「尽力而为」的增量比较字段：解析不到时给
        0/""（表示未知），调用方遇到未知必须回退为「进入主题」，不能据此跳过。

        post_count 取主题链接所在行附近文本里的レス数/返信数（形如「レス:12」「12件」）；
        SortID 数字本身不随新帖变化，因此不作为更新判据，仅作 last_key 兜底占位。
        """
        if dom is None:
            return []
        hrefs = dom.attr_list("a[href*='SortID=']", "href", limit=limit * 3)
        texts = dom.texts("a[href*='SortID=']")
        out: list = []
        seen: set[str] = set()
        # 尝试按主题行容器解析每个主题的レス数（尽力而为，失败留 0=未知）。
        meta_by_sortid: dict[str, int] = {}
        if with_meta:
            try:
                meta_by_sortid = self._bbs_thread_meta(dom)
            except Exception:
                meta_by_sortid = {}
        for href, text in zip(hrefs, texts):
            if not href or "SortID=" not in href:
                continue
            base = href.split("#")[0].split("?")[0]
            if not base.endswith("/"):
                base += "/"
            m = self._SORTID_RE.search(base)
            if not m:
                continue
            sortid = m.group(2)
            # 相对链接补全为绝对地址（bbs 列表页链接常是 /bbs/K.../SortID=.../）
            if base.startswith("/"):
                base = "https://bbs.kakaku.com" + base
            elif not base.startswith("http"):
                base = "https://bbs.kakaku.com/" + base.lstrip("/")
            if base in seen:
                continue
            title = " ".join((text or "").split())
            # 过滤噪声：书込番号、纯数字、空
            if not title or "書込番号" in title or title.isdigit():
                continue
            seen.add(base)
            if with_meta:
                post_count = int(meta_by_sortid.get(sortid, 0))
                out.append((base, title, post_count, ""))
            else:
                out.append((base, title))
            if len(out) >= limit:
                break
        return out

    _RES_RE = re.compile(r"(?:レス|返信)\D{0,4}(\d+)|(\d+)\s*件")

    def _bbs_thread_meta(self, dom) -> dict[str, int]:
        """尽力从讨论板列表页解析 {SortID: レス数}。解析不到返回空 dict。

        不同版式差异较大，这里只做保守解析：遍历带 SortID 的链接元素，读取其
        自身/邻近文本里的レス数。取不到就不记，调用方按未知处理（进入主题）。
        """
        meta: dict[str, int] = {}
        try:
            blocks = dom.sub("a[href*='SortID=']", limit=180)
        except Exception:
            return meta
        for a in blocks:
            try:
                href = a.self_attr("href") or ""
            except Exception:
                href = ""
            m = self._SORTID_RE.search(href.split("#")[0].split("?")[0] + "/")
            if not m:
                continue
            sortid = m.group(2)
            if sortid in meta:
                continue
            try:
                text = a.self_text() or ""
            except Exception:
                text = ""
            rm = self._RES_RE.search(text)
            if rm:
                num = rm.group(1) or rm.group(2)
                if num and num.isdigit():
                    meta[sortid] = int(num)
        return meta
